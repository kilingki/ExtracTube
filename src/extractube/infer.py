from __future__ import annotations
import json
import math
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from extractube.errors import PipelineError
from extractube.media import PCM_CHANNELS, PCM_SAMPLE_RATE
from extractube.settings import Settings
import base64
import hashlib
import http.client
import re
import time
from .store import wire, digest, write, read, loads, ref, safe
from .errors import VisionError, InferenceJobFailed, InferencePaused

FA_CHUNK_LIMIT_SECONDS = 180
TIME_TOLERANCE_SECONDS = 0.001
AUDIO_IDENTITY_FIELDS = ("sample_rate", "channels", "num_samples")
ASR_FORM = {
    "response_format": "verbose_json",
    "include_chunks": "true",
}
SERVER_DEFAULTS = {
    "ASR_MODEL": "qwen3-asr",
    "DEFAULT_LANGUAGE": "ko",
}


@dataclass
class InferResponse:
    status: int
    body: bytes


class HttpInferClient:
    """POST /infer without following redirects or retrying."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.asr_requests = 0
        self.fa_requests = 0
        self._opener = urllib.request.build_opener(_NoRedirect())

    def transcribe(self, wav_path: Path) -> InferResponse:
        boundary = _boundary()
        body = build_multipart(
            [
                ("file", "file", "source.wav", "audio/wav", wav_path.read_bytes()),
                ("field", "response_format", ASR_FORM["response_format"]),
                ("field", "include_chunks", ASR_FORM["include_chunks"]),
                ("field", "language", self.settings.language),
            ],
            boundary,
        )
        self._reject_if_too_large(body)
        self.asr_requests += 1
        return self._post(self.settings.asr_model_id, body, boundary)

    def align(self, wav_path: Path, asr_bytes: bytes) -> InferResponse:
        boundary = _boundary()
        body = build_multipart(
            [
                ("file", "file", "source.wav", "audio/wav", wav_path.read_bytes()),
                ("field", "payload", asr_bytes.decode("utf-8")),
            ],
            boundary,
        )
        self._reject_if_too_large(body)
        self.fa_requests += 1
        return self._post(self.settings.fa_model_id, body, boundary)

    def _reject_if_too_large(self, body: bytes) -> None:
        if len(body) > self.settings.max_body_bytes:
            raise PipelineError(
                "요청 본문이 InferSwap maxBodyBytes를 넘습니다. 요청을 보내지 않았습니다.",
                kind="body",
            )

    def _post(self, model_id: str, body: bytes, boundary: str) -> InferResponse:
        query = urlencode({"model": model_id})
        url = f"{self.settings.infer_base_url.rstrip('/')}/infer?{query}"
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with self._opener.open(request, timeout=self.settings.timeout_seconds) as response:
                return InferResponse(status=response.status, body=response.read())
        except urllib.error.HTTPError as exc:
            return InferResponse(status=exc.code, body=exc.read())
        except TimeoutError as exc:
            raise PipelineError(
                "요청이 시간 초과되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다.",
                kind="timeout",
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise PipelineError(
                    "요청이 시간 초과되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다.",
                    kind="timeout",
                ) from exc
            raise PipelineError(f"추론 요청에 실패했습니다: {reason}", kind="network") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _boundary() -> str:
    return f"ExtracTube{uuid.uuid4().hex}"


def build_multipart(parts: list[tuple[Any, ...]], boundary: str) -> bytes:
    chunks: list[bytes] = []
    for part in parts:
        kind = part[0]
        header = f"--{boundary}\r\n"
        if kind == "file":
            _, name, filename, content_type, payload = part
            header += (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            )
            chunks.append(header.encode("utf-8"))
            chunks.append(payload)
            chunks.append(b"\r\n")
        elif kind == "field":
            _, name, value = part
            header += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            chunks.append(header.encode("utf-8"))
            if isinstance(value, bytes):
                chunks.append(value)
            else:
                chunks.append(str(value).encode("utf-8"))
            chunks.append(b"\r\n")
        else:
            raise PipelineError(f"알 수 없는 multipart 파트입니다: {kind}", kind="infer")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)


def new_attempt_id() -> str:
    return str(uuid.uuid4())


def parse_json_object(body: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"{label} 응답이 JSON 객체가 아닙니다.", kind="validation") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"{label} 응답이 JSON 객체가 아닙니다.", kind="validation")
    return payload


def error_source(body: bytes) -> tuple[str, str]:
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return "unknown", text[:2000]
    if not isinstance(payload, dict):
        return "unknown", text[:2000]
    if payload.get("src") == "inferswap":
        return "inferswap", str(payload.get("error", ""))
    if "detail" in payload:
        detail = payload["detail"]
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        return "runtime", detail
    return "unknown", text[:2000]


def as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PipelineError(f"{field}은 정수여야 합니다.", kind="validation")
    return value


def validate_asr(payload: dict[str, Any], num_samples: int) -> None:
    audio = payload.get("audio")
    chunks = payload.get("chunks")
    if not isinstance(audio, dict) or not isinstance(chunks, list) or not chunks:
        raise PipelineError("ASR audio 또는 chunks가 계약과 다릅니다.", kind="validation")
    if (
        audio.get("sample_rate") != PCM_SAMPLE_RATE
        or audio.get("channels") != PCM_CHANNELS
        or audio.get("num_samples") != num_samples
    ):
        raise PipelineError("ASR audio 메타데이터가 WAV와 다릅니다.", kind="validation")
    for position, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise PipelineError(f"chunks[{position}]가 객체가 아닙니다.", kind="validation")
        index = as_int(chunk.get("index"), f"chunks[{position}].index")
        start = as_int(chunk.get("start_sample"), f"chunks[{index}].start_sample")
        end = as_int(chunk.get("end_sample"), f"chunks[{index}].end_sample")
        if not (0 <= start < end <= num_samples):
            raise PipelineError(
                f"chunks[{index}] sample 구간이 WAV 밖입니다.",
                kind="validation",
            )
        text = chunk.get("text")
        if not isinstance(text, str):
            raise PipelineError(f"chunks[{index}].text가 문자열이 아닙니다.", kind="validation")
        if text.strip() != "":
            language = chunk.get("language")
            if not isinstance(language, str) or language.strip() == "":
                raise PipelineError(
                    f"chunks[{index}]에 텍스트가 있지만 language가 없습니다.",
                    kind="validation",
                )


def guard_fa_duration(payload: dict[str, Any]) -> None:
    audio = payload.get("audio")
    chunks = payload.get("chunks")
    if not isinstance(audio, dict) or not isinstance(chunks, list):
        raise PipelineError("FA 입력 ASR에 audio 또는 chunks가 없습니다.", kind="validation")
    sample_rate = audio.get("sample_rate")
    if sample_rate != PCM_SAMPLE_RATE:
        raise PipelineError("FA 입력 sample rate가 16000이 아닙니다.", kind="validation")
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        start = chunk.get("start_sample")
        end = chunk.get("end_sample")
        if isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if (end - start) / PCM_SAMPLE_RATE > FA_CHUNK_LIMIT_SECONDS:
            raise PipelineError(
                "ASR 청크가 FA 상한 180초를 넘습니다. 청크를 다시 나누지 않습니다.",
                kind="config",
            )


def _audio_object(payload: dict[str, Any], label: str) -> dict[str, Any]:
    audio = payload.get("audio")
    if not isinstance(audio, dict):
        raise PipelineError(f"{label} audio가 객체가 아닙니다.", kind="validation")
    return audio


def _validate_audio_identity(asr_payload: dict[str, Any], fa_payload: dict[str, Any]) -> None:
    asr_audio = _audio_object(asr_payload, "ASR")
    fa_audio = _audio_object(fa_payload, "FA")
    for field in AUDIO_IDENTITY_FIELDS:
        if field not in asr_audio or field not in fa_audio or fa_audio[field] != asr_audio[field]:
            raise PipelineError(f"FA audio.{field}가 ASR과 다릅니다.", kind="validation")


def validate_alignment(asr_payload: dict[str, Any], fa_payload: dict[str, Any]) -> None:
    if fa_payload.get("time_reference") != "audio_start":
        raise PipelineError("time_reference가 audio_start가 아닙니다.", kind="validation")
    if fa_payload.get("overlap_deduplicated") is not False:
        raise PipelineError("overlap_deduplicated가 false가 아닙니다.", kind="validation")
    _validate_audio_identity(asr_payload, fa_payload)
    asr_chunks = asr_payload.get("chunks")
    fa_chunks = fa_payload.get("chunks")
    if not isinstance(asr_chunks, list) or not isinstance(fa_chunks, list):
        raise PipelineError("FA chunks가 없습니다.", kind="validation")
    if len(fa_chunks) != len(asr_chunks):
        raise PipelineError("ASR과 FA의 청크 수가 다릅니다.", kind="validation")
    for src, dst in zip(asr_chunks, fa_chunks):
        if not isinstance(src, dict) or not isinstance(dst, dict):
            raise PipelineError("FA 청크가 객체가 아닙니다.", kind="validation")
        index = as_int(src.get("index"), "chunks.index")
        text = src.get("text")
        if not isinstance(text, str):
            raise PipelineError(f"chunks[{index}].text가 문자열이 아닙니다.", kind="validation")
        start_sample = as_int(src.get("start_sample"), f"chunks[{index}].start_sample")
        end_sample = as_int(src.get("end_sample"), f"chunks[{index}].end_sample")
        for field, expected in (
            ("index", index),
            ("start_sample", start_sample),
            ("end_sample", end_sample),
            ("text", text),
        ):
            if dst.get(field) != expected:
                raise PipelineError(
                    f"chunks[{index}].{field}가 ASR과 다릅니다.",
                    kind="validation",
                )
        items = dst.get("items")
        if not isinstance(items, list):
            raise PipelineError(f"chunks[{index}].items가 배열이 아닙니다.", kind="validation")
        if text.strip() == "":
            if dst.get("status") != "skipped_empty_text" or items != []:
                raise PipelineError(
                    f"chunks[{index}] 빈 텍스트가 skipped_empty_text가 아닙니다.",
                    kind="validation",
                )
            continue
        if dst.get("status") != "aligned":
            raise PipelineError(f"chunks[{index}] status가 aligned가 아닙니다.", kind="validation")
        language = dst.get("language")
        if not isinstance(language, str) or language.strip() == "":
            raise PipelineError(f"chunks[{index}] language가 없습니다.", kind="validation")
        start = start_sample / PCM_SAMPLE_RATE
        end = end_sample / PCM_SAMPLE_RATE
        previous: tuple[float, float] | None = None
        for item in items:
            current = _validate_item(index, item, start, end)
            if previous is not None and (current[0] < previous[0] or current[1] < previous[1]):
                raise PipelineError(
                    f"chunks[{index}] item 시간 순서가 뒤집혔습니다.",
                    kind="validation",
                )
            previous = current


def _validate_item(index: int, item: Any, start: float, end: float) -> tuple[float, float]:
    if not isinstance(item, dict) or not isinstance(item.get("text"), str) or item["text"] == "":
        raise PipelineError(f"chunks[{index}] item text가 비어 있습니다.", kind="validation")
    start_time = _finite_time(item.get("start_time"), f"chunks[{index}].start_time")
    end_time = _finite_time(item.get("end_time"), f"chunks[{index}].end_time")
    if start_time > end_time:
        raise PipelineError(f"chunks[{index}] item 시각 순서가 잘못되었습니다.", kind="validation")
    if start_time < start - TIME_TOLERANCE_SECONDS or end_time > end + TIME_TOLERANCE_SECONDS:
        raise PipelineError(
            f"chunks[{index}] item 시각이 청크 구간 밖입니다.",
            kind="validation",
        )
    return start_time, end_time


def _finite_time(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PipelineError(f"{field}이 수치가 아닙니다.", kind="validation")
    number = float(value)
    if not math.isfinite(number):
        raise PipelineError(f"{field}이 유한한 수치가 아닙니다.", kind="validation")
    return number


# Journaled JSON inference for semantic and visual decisions.
"""Strict task parsers and durable, serial InferSwap calls without implicit retries."""

PARSER_POLICY='strict_json_tasks.v1'

def exact(obj, keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys):
        raise ValueError('응답 키가 계약과 다릅니다.')

def reason(value, limit=None):
    if not isinstance(value,str) or not value.strip() or (limit and len(value)>limit):
        raise ValueError('판정 사유가 유효하지 않습니다.')

def index(value, allowed):
    if type(value) is not int or value not in allowed:
        raise ValueError('허용되지 않은 문장 index입니다.')

def task_parse(obj, spec):
    task=spec['task']; allowed=set(spec.get('allowed',[]))
    if task=='t1.boundary':
        exact(obj,['boundaries']); seen=set()
        if not isinstance(obj['boundaries'],list): raise ValueError('boundaries 배열 오류')
        for row in obj['boundaries']:
            exact(row,['sentence_index','reason']);index(row['sentence_index'],allowed);reason(row['reason'])
            if row['sentence_index'] in seen: raise ValueError('중복 경계')
            seen.add(row['sentence_index'])
    elif task=='t1.conflict':
        exact(obj,['decisions']);seen=set()
        if not isinstance(obj['decisions'],list): raise ValueError('decisions 배열 오류')
        for row in obj['decisions']:
            if not isinstance(row,dict): raise ValueError('결정 객체 오류')
            action=row.get('action');exact(row,['sentence_index','action','reason']+(['to_index'] if action=='move' else []))
            index(row['sentence_index'],allowed);reason(row['reason'])
            if action not in ('keep','drop','move') or row['sentence_index'] in seen: raise ValueError('충돌 결정 오류')
            if action=='move':
                index(row['to_index'],set(spec['move_allowed']))
                if row['to_index']==row['sentence_index']: raise ValueError('동일 위치 이동')
            seen.add(row['sentence_index'])
        if seen!=allowed: raise ValueError('누락된 후보')
    elif task=='t1.verify':
        exact(obj,['action','reason']);reason(obj['reason'])
        if obj['action'] not in ('keep','merge'): raise ValueError('VERIFY action 오류')
    elif task=='t2':
        exact(obj,['decision','reason','evidence_sentence_indices']);reason(obj['reason'])
        if obj['decision'] not in ('needed','not_needed','uncertain'): raise ValueError('T2 decision 오류')
        ev=obj['evidence_sentence_indices']
        if not isinstance(ev,list) or not 1<=len(ev)<=3 or any(type(i) is not int for i in ev) or len(set(ev))!=len(ev): raise ValueError('evidence 배열 오류')
        for i in ev:index(i,allowed)
    elif task in ('t4','t5'):
        exact(obj,['frames'])
        if not isinstance(obj['frames'],list) or len(obj['frames'])!=len(spec['targets']): raise ValueError('target 수 오류')
        for row,target in zip(obj['frames'],spec['targets']):
            if task=='t4':
                exact(row,['frame_id','decision','reason','duplicate_of']);reason(row['reason'])
                if row['decision'] not in ('keep','drop','uncertain') or row['duplicate_of'] is not None: raise ValueError('T4 decision 오류')
            else:
                exact(row,['frame_id','decision','relevance','readability','completeness','visual_kind','caption','reason']);reason(row['reason'],300)
                if row['decision'] not in ('use','omit','uncertain') or row['relevance'] not in ('yes','no','uncertain') or any(row[k] not in ('sufficient','insufficient','uncertain') for k in ('readability','completeness')) or row['visual_kind'] not in ('ui','slide','chart','formula','table','diagram','object','place','demonstration','other','unknown'): raise ValueError('T5 enum 오류')
                if row['decision']=='use':
                    if row['relevance']!='yes' or row['readability']!='sufficient' or row['completeness']!='sufficient' or row['visual_kind']=='unknown': raise ValueError('위험한 use 교차 조건')
                    reason(row['caption'],120)
                elif row['caption'] is not None: raise ValueError('use 외 caption 금지')
            if row['frame_id']!=target: raise ValueError('target 순서/집합 오류')
    else:
        raise ValueError('알 수 없는 task')
    return obj

def content_text(content):
    if content is None: return None
    if isinstance(content,str): return content
    if isinstance(content,list):
        parts=[]
        for piece in content:
            if isinstance(piece,str):parts.append(piece)
            elif isinstance(piece,dict) and isinstance(piece.get('text'),str) and piece.get('type','text')=='text':parts.append(piece['text'])
            else: raise ValueError('content 조각 오류')
        return ''.join(parts)
    raise ValueError('content 형식 오류')

def parse_response(body,status,spec,thinking):
    if status!=200: raise ValueError('http_error')
    outer=loads(body)
    if not isinstance(outer,dict) or not isinstance(outer.get('choices'),list) or len(outer['choices'])!=1: raise ValueError('choices 오류')
    choice=outer['choices'][0]
    if not isinstance(choice,dict) or not isinstance(choice.get('message'),dict): raise ValueError('message 오류')
    content=content_text(choice['message'].get('content')); finish=choice.get('finish_reason');usage=outer.get('usage')
    info={'finish_reason':finish,'usage':usage,'reasoning':choice['message'].get('reasoning_content',choice['message'].get('reasoning'))}
    if spec['task']=='t2' and thinking and finish=='length' and (content is None or not content.strip()) and isinstance(usage,dict) and type(usage.get('completion_tokens')) is int and usage['completion_tokens']==4096 and isinstance(usage.get('completion_tokens_details'),dict) and type(usage['completion_tokens_details'].get('reasoning_tokens')) is int and usage['completion_tokens_details']['reasoning_tokens']==4096:
        return None,'budget_exhausted',info
    if finish!='stop' or not content or not content.strip(): raise ValueError('finish_reason/content 오류')
    text=content.strip()
    if text.startswith('```'):
        match=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```',text,re.S)
        if not match: raise ValueError('코드 펜스 오류')
        text=match.group(1)
    parsed=task_parse(loads(text),spec)
    return parsed,'succeeded',info

def response_metadata(body):
    try:
        outer=loads(body);choice=outer['choices'][0];message=choice['message']
        return {'finish_reason':choice.get('finish_reason'),'usage':outer.get('usage'),'reasoning':message.get('reasoning_content',message.get('reasoning'))}
    except (ValueError,UnicodeError,KeyError,IndexError,TypeError):
        return {}


class HttpTransport:
    def send(self,url,body,timeout,limit,on_headers):
        request=urllib.request.Request(url,data=body,method='POST',headers={'Content-Type':'application/json'})
        opener=urllib.request.build_opener(_NoRedirect())
        try:
            response=opener.open(request,timeout=timeout)
        except urllib.error.HTTPError as exc:
            response=exc
        with response:
            status=response.code; headers=dict(response.headers)
            on_headers(status,headers)
            chunks=[];size=0
            try:
                while size<=limit:
                    block=response.read(min(65536,limit+1-size))
                    if not block: break
                    chunks.append(block);size+=len(block)
                data=b''.join(chunks)
            except http.client.IncompleteRead as exc:
                data=b''.join(chunks)+exc.partial
                return {'status':status,'body':data[:limit+1],'complete':False,'error':'incomplete_body'}
            except (OSError,http.client.HTTPException) as exc:
                return {'status':status,'body':b''.join(chunks),'complete':False,'error':type(exc).__name__}
            length=response.headers.get('Content-Length')
            complete=size<=limit and (length is None or size==int(length))
            return {'status':status,'body':data,'complete':complete,'error':None if complete else ('body_too_large' if size>limit else 'incomplete_body')}

def make_transport(settings):
    return HttpTransport()

def body_for(settings,content,thinking):
    return {'model':settings.qwen_body_model,'messages':[{'role':'user','content':content}],'temperature':0,'max_tokens':4096,'stream':False,'chat_template_kwargs':{'enable_thinking':thinking}}

def validate_entry(ctx,entry,spec):
    if entry['status'] not in ('succeeded','budget_exhausted'):raise ValueError('캐시 terminal 오류')
    request=read(ctx.hashes.check(ctx.work,entry['request']))
    record=read(ctx.hashes.check(ctx.work,entry['call']))
    data=ctx.hashes.check(ctx.work,entry['response']).read_bytes()
    if hashlib.sha256(wire(request['body'])).hexdigest()!=request['body_sha256'] or record['job_key']!=entry['job_key'] or not record['response_complete']: raise ValueError('캐시 출처 오류')
    for reference in request['critical_refs']+request['image_refs']:ctx.hashes.check(ctx.work,reference)
    parsed,status,info=parse_response(data,record['http_status'],spec,request['thinking'])
    if parsed!=entry['parsed'] or status!=entry['status']:raise ValueError('캐시 재파싱 오류')
    return parsed,status

def recover_calls(work,run_dir,state):
    """Finish local parsing if a complete body exists; otherwise freeze sending as unknown."""
    for journal_path in sorted((run_dir/'jobs').glob('*.json')) if (run_dir/'jobs').exists() else []:
        journal=read(journal_path)
        for attempt in journal.get('attempts',[]):
            call_dir=safe(work,attempt['path']);record_path=call_dir/'record.json'
            if record_path.exists():continue
            request=read(call_dir/'request.json')
            if attempt['status']=='reserved':
                attempt['status']='not_sent';continue
            if attempt['status']!='sending':continue
            status='unknown';parsed=None;info={};error='crash_sending';http_status=None;complete=False
            if (call_dir/'response.body').is_file() and (call_dir/'transport.json').is_file():
                transport=read(call_dir/'transport.json');http_status=transport['http_status'];complete=True
                try:parsed,status,info=parse_response((call_dir/'response.body').read_bytes(),http_status,request['spec'],request['thinking'])
                except (ValueError,UnicodeError,TypeError,KeyError) as exc:status='failed';error=str(exc);info=response_metadata((call_dir/'response.body').read_bytes())
            record={'call_id':call_dir.name,'job_key':journal['job_key'],'status':status,'http_status':http_status,'body_sha':hashlib.sha256((call_dir/'response.body').read_bytes()).hexdigest() if complete else None,'parsed':parsed,'error_code':error,'finish_reason':info.get('finish_reason'),'usage':info.get('usage'),'reasoning':info.get('reasoning'),'elapsed_seconds':request['timeout'],'response_complete':complete}
            write(record_path,record,immutable=True);attempt['status']=status
            if status in ('succeeded','budget_exhausted'):
                recovered=SimpleRecoveryContext(work,run_dir.name)
                save_entry(recovered,journal['job_key'],make_entry(recovered,journal['job_key'],call_dir,record))
        write(journal_path,journal)

def budget_from_journals(run_dir):
    calls=0;elapsed=0;reserved=0
    if (run_dir/'jobs').exists():
        for jp in (run_dir/'jobs').glob('*.json'):
            for attempt in read(jp).get('attempts',[]):
                if attempt['status'] in ('not_sent','reserved'): continue
                calls+=1
                cp=run_dir/'calls'/PathName(attempt['path'])/'record.json'
                if cp.exists():elapsed+=read(cp)['elapsed_seconds']
                else:reserved+=1
    return calls,elapsed,reserved

def PathName(path):return path.rsplit('/',1)[-1]

class Jobs:
    def __init__(self,ctx):
        self.ctx=ctx;self.transport=make_transport(ctx.settings)
    def invoke(self,stage,task,content,spec,dependencies,images=(),retry=0):
        ctx=self.ctx;thinking=task=='t2';spec={'task':task,**spec};body=body_for(ctx.settings,content,thinking)
        data=wire(body)
        if len(data)>ctx.settings.max_request_bytes:raise VisionError('요청 본문 byte 한도 초과. 미전송입니다.',kind='body')
        key=digest({'task':task,'body_sha256':hashlib.sha256(data).hexdigest(),'dependencies':dependencies,'projection':ctx.config['projections'][stage],'parser_policy':PARSER_POLICY,'model':ctx.settings.model_profile()})
        jp=ctx.directory/f'jobs/{key}.json'
        journal=read(jp) if jp.exists() else {'job_key':key,'task':task,'attempts':[]}
        def accept(entry,entry_ref):
            parsed,status=validate_entry(ctx,entry,spec);ctx.reused_jobs+=1
            return parsed,{'job_key':key,'source_run_id':entry['source_run_id'],'result':entry_ref},status
        # Reuse the same run's exact completed call even when force/from bypasses older cache.
        for attempt in reversed(journal['attempts']):
            cp=safe(ctx.work,attempt['path']);rp=cp/'record.json'
            if rp.exists():
                record=read(rp)
                if record['status'] in ('succeeded','budget_exhausted'):
                    entry=make_entry(ctx,key,cp,record)
                    entry_ref=save_entry(ctx,key,entry)
                    return accept(entry,entry_ref)
                if record['status']=='unknown' and not attempt.get('retry_authorization'):
                    raise InferencePaused('blocked_unknown')
        pointer=safe(ctx.work,f'vision/cache/jobs/{key}/result.json')
        if ctx.allow_reuse(stage) and pointer.is_file():
            try:
                entry_ref=read(pointer)['entry'];entry=read(ctx.hashes.check(ctx.work,entry_ref))
                if entry['job_key']!=key:raise ValueError('key 오류')
                return accept(entry,entry_ref)
            except (VisionError,ValueError,KeyError,TypeError):pass
        if ctx.require_reuse(stage):
            raise VisionError(f'--from에 필요한 {stage} 작업 캐시가 없습니다.',kind='upstream')
        for attempt_index in range(retry+1):
            ctx.check_inputs()
            calls,elapsed,reserved=budget_from_journals(ctx.directory)
            budget=ctx.state['budget'];budget.update(http_calls=calls,http_seconds=elapsed,reserved_calls=reserved)
            if calls+1>budget['max_calls'] or elapsed+(reserved+1)*ctx.settings.timeout_seconds>budget['max_http_seconds']:
                ctx.save_state();raise InferencePaused('paused_budget')
            call_id=uuid.uuid4().hex;cp=ctx.directory/f'calls/{call_id}'
            url=ctx.settings.infer_base_url.rstrip('/')+'/infer?'+urlencode({'model':ctx.settings.qwen_model_id})
            request={'url':url,'task':task,'job_key':key,'body_sha256':hashlib.sha256(data).hexdigest(),'body':body,'image_refs':list(images),'critical_refs':ctx.critical_refs,'timeout':ctx.settings.timeout_seconds,'max_response_bytes':ctx.settings.max_response_bytes,'attempt_index':len(journal['attempts']),'thinking':thinking,'spec':spec,'dependencies':dependencies}
            write(cp/'request.json',request,immutable=True)
            attempt={'call_id':call_id,'path':cp.relative_to(ctx.work).as_posix(),'status':'reserved'};journal['attempts'].append(attempt);write(jp,journal)
            attempt['status']='sending';write(jp,journal);budget['reserved_calls']=reserved+1;ctx.save_state()
            started=time.monotonic();response=None;error=None
            ctx.invocation_calls+=1
            def headers(status,values):
                try:write(cp/'transport.json',{'http_status':status,'headers':values,'receive_started':True},immutable=True)
                except OSError as exc:raise VisionError('transport 기록 저장 실패입니다.',kind='commit') from exc
            try:
                response=self.transport.send(url,data,ctx.settings.timeout_seconds,ctx.settings.max_response_bytes,headers)
                if len(response['body'])>ctx.settings.max_response_bytes: response['complete']=False;response['error']='body_too_large'
            except KeyboardInterrupt:
                raise
            except (OSError,urllib.error.URLError,http.client.HTTPException) as exc:
                error=type(exc).__name__
            elapsed=time.monotonic()-started
            status='unknown';parsed=None;info={};http_status=response['status'] if response else None;complete=bool(response and response['complete'])
            if response is not None:
                write(cp/('response.body' if complete else 'response.partial'),response['body'],immutable=True,raw=True)
            if complete:
                try:parsed,status,info=parse_response(response['body'],http_status,spec,thinking)
                except (ValueError,UnicodeError,TypeError,KeyError) as exc:status='failed';error=str(exc);info=response_metadata(response['body'])
            else:error=error or response.get('error') if response else error
            record={'call_id':call_id,'job_key':key,'status':status,'http_status':http_status,'body_sha':hashlib.sha256(response['body']).hexdigest() if response else None,'parsed':parsed,'error_code':error,'finish_reason':info.get('finish_reason'),'usage':info.get('usage'),'reasoning':info.get('reasoning'),'elapsed_seconds':elapsed,'response_complete':complete}
            write(cp/'record.json',record,immutable=True);attempt['status']=status;write(jp,journal)
            calls,total,reserved=budget_from_journals(ctx.directory);budget.update(http_calls=calls,http_seconds=total,reserved_calls=reserved);ctx.save_state()
            if status=='unknown':
                ctx.state['blocked_call_ids'].append(call_id);ctx.save_state();raise InferencePaused('blocked_unknown')
            if status in ('succeeded','budget_exhausted'):
                entry=make_entry(ctx,key,cp,record);entry_ref=save_entry(ctx,key,entry)
                return parsed,{'job_key':key,'source_run_id':ctx.run_id,'result':entry_ref},status
            # Only a completed format/finish failure in T1 permits the single retry.
            if attempt_index<retry and http_status==200:continue
            raise InferenceJobFailed(f'{task} 호출 확정 실패: {error}',kind='inference',diagnostic=cp)

def make_entry(ctx,key,cp,record):
    return {'job_key':key,'status':record['status'],'parsed':record['parsed'],'source_run_id':ctx.run_id,'call':ref(ctx.work,cp/'record.json'),'request':ref(ctx.work,cp/'request.json'),'response':ref(ctx.work,cp/'response.body')}

def save_entry(ctx,key,entry):
    # Content-addressed immutable entries make recovery idempotent.
    folder=ctx.work/f'vision/cache/jobs/{key}'
    name=digest(entry)
    path=folder/f'entries/{name[:32]}.json'
    write(path,entry,immutable=True);entry_ref=ref(ctx.work,path)
    write(folder/'result.json',{'entry':entry_ref})
    return entry_ref


class SimpleRecoveryContext:
    def __init__(self,work,run_id):
        self.work=work;self.run_id=run_id
