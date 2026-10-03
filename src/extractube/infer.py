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
