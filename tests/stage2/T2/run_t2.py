"""T2 시각 분석 후보 선정. 현재 설정은 B00/P3_CTX2_B4096이며 sweep을 만들지 않는다."""

from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompts

ROOT = EXPERIMENT_ROOT
DATA_ROOT = resolve_experiment_path(ROOT / "data")
RUNS_ROOT = resolve_experiment_path(ROOT / "tests" / "T2" / "runs")
INPUT_RELATIVE = (
    "tests/T1/sweep/campaigns/20261017_181001_1c4a84/runs/confirm/E01/r01/attempt01"
)
INPUT_ROOT = resolve_experiment_path(ROOT / INPUT_RELATIVE)

SCHEMA_VERSION = "t2.visual_candidates.v1"
LOGIC_VERSION = "t2.decision.v1"
VALIDATION_RULE_VERSION = "t2.evidence.v2"
POLICY_VERSION = "t2.budget_guard.v1"
BUDGET_GUARD_REASON = (
    "thinking ON 호출이 HTTP 200, finish_reason=length, "
    "completion_tokens 4096과 reasoning_tokens 4096으로 최종 판정 JSON을 만들지 못했다. "
    "누락 방지 정책으로 대상 청크 전체를 후보로 유지한다. "
    "fallback의 not_needed는 후보 제외 근거가 아니다."
)
CONFIG_ID = "B00"
PROMPT_ID = "P3_CTX2_B4096"
ACTIVE_PROMPT = prompts.P3_CTX2
BASELINE_RUN_ID = "20261018T163714Z_27eb28"
P2_RUN_ID = "20261018T110743Z_96ad2c"
BASELINE_MANIFEST_SHA256 = "b6b62527b27e69f42a1669b1060ba01a3dbdd754fc356c7e75b23f646c470166"
B_REUSE_RUNS = {
    "20261020T175915Z_c239dd": (
        ("bjDkfjSmXVo", 7, 0),
        ("7EJ-xotXiSE", 18, 0),
    ),
    "20261020T182748Z_0fad2d": (
        ("iVelddb1qQ4", 5, 0),
        ("CvuE-jyg-N8", 59, 0),
        ("Fw-8hZhnH3Q", 17, 0),
        ("bjDkfjSmXVo", 0, 0),
        ("Fw-8hZhnH3Q", 19, 0),
        ("iVelddb1qQ4", 24, 0),
    ),
}
CHANGE_REASON = (
    "P3_CTX2 프롬프트와 앞뒤 최대 2문장을 유지한다. "
    "enable_thinking은 true이고 max_tokens는 4096이다. "
    "모델, temperature, timeout, pad 0, 재시도 0, t2.evidence.v2는 직전 B와 같다. "
    "요청·입력·응답 검증이 같은 B 8건만 재사용하고 나머지 241건을 호출한다. "
    "checkpoint는 unknown이다."
)
CONFIRM_ORDER = (
    ("bjDkfjSmXVo", 7),
    ("7EJ-xotXiSE", 18),
    ("iVelddb1qQ4", 5),
    ("CvuE-jyg-N8", 59),
    ("Fw-8hZhnH3Q", 17),
    ("bjDkfjSmXVo", 0),
    ("Fw-8hZhnH3Q", 19),
    ("iVelddb1qQ4", 24),
)
REVIEW_LABELS = ("문제 사례", "기존 선정", "기존 미선정", "기대 방향")
CHAR_BUDGET = 6000
CONTEXT_SENTENCES = 2
PAD_SECONDS = 0
TEMPERATURE = 0
MAX_TOKENS = 4096
ENABLE_THINKING = True
TIMEOUT_SECONDS = 300
CONCURRENCY = 1
RETRIES = 0
INFER_URL = "http://127.0.0.1:8095/infer?model=qwen-vlm"
INFER_MODEL_ID = "qwen-vlm"
BODY_MODEL = "qwen3.8-27b"
DECISIONS = {"needed", "not_needed", "uncertain"}
RESPONSE_KEYS = {"decision", "reason", "evidence_sentence_indices"}
EXPECTED_IDS = (
    "7EJ-xotXiSE",
    "CvuE-jyg-N8",
    "Fw-8hZhnH3Q",
    "H2WN130OzLg",
    "bjDkfjSmXVo",
    "hGVL9XRU9IQ",
    "iEMuJiVY9Oc",
    "iVelddb1qQ4",
)
EXPECTED_COUNTS = {
    "7EJ-xotXiSE": (368, 33),
    "CvuE-jyg-N8": (826, 65),
    "Fw-8hZhnH3Q": (217, 20),
    "H2WN130OzLg": (411, 26),
    "bjDkfjSmXVo": (328, 16),
    "hGVL9XRU9IQ": (362, 35),
    "iEMuJiVY9Oc": (445, 29),
    "iVelddb1qQ4": (398, 25),
}
MARKDOWN_ESCAPES = set("\\`*_{}[]<>()#+-.!|")
PLACEHOLDERS = (
    "unit_index",
    "unit_sentence_range",
    "chunk_index_1based",
    "chunk_count",
    "context_before_json",
    "sentences_json",
    "context_after_json",
)
OPTIONAL_PLACEHOLDERS = {"context_before_json", "context_after_json"}


class SchemaError(ValueError):
    """응답이 T2 판정 계약과 다르다."""


class RunBusy(RuntimeError):
    """같은 run을 다른 프로세스가 쓰는 중이다."""


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def time_literal(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def display_path(repo: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def md_escape(text: str) -> str:
    return "".join("\\" + char if char in MARKDOWN_ESCAPES else char for char in text)


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def settings_payload() -> dict:
    return {
        "config_id": CONFIG_ID,
        "prompt_id": PROMPT_ID,
        "char_budget": CHAR_BUDGET,
        "context_sentences": CONTEXT_SENTENCES,
        "pad_seconds": PAD_SECONDS,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "enable_thinking": ENABLE_THINKING,
        "timeout_seconds": TIMEOUT_SECONDS,
        "concurrency": CONCURRENCY,
        "retries": RETRIES,
        "infer_url": INFER_URL,
        "infer_model_id": INFER_MODEL_ID,
        "body_model": BODY_MODEL,
    }


def model_payload() -> dict:
    return {
        "url": INFER_URL,
        "infer_model_id": INFER_MODEL_ID,
        "body_model": BODY_MODEL,
        "checkpoint": "unknown",
        "server_version": "unknown",
        "context_limit": "unknown",
    }


def git_sha(repo: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def code_version(repo: Path) -> dict:
    base = Path(__file__).resolve().parent
    files = []
    for path in sorted(base.glob("*.py")):
        files.append({"path": display_path(repo, path), "sha256": sha256_file(path)})
    return {"files": files, "git_sha": git_sha(repo)}


def render_template(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for key in PLACEHOLDERS:
        token = "{" + key + "}"
        if token not in rendered:
            if key in OPTIONAL_PLACEHOLDERS:
                continue
            raise RuntimeError(f"프롬프트 자리표시자가 없습니다: {key}")
        rendered = rendered.replace(token, replacements[key], 1)
    return rendered


def sentences_json(sentences: list[dict]) -> str:
    rows = [{"sentence_index": sentence["index"], "text": sentence["text"]} for sentence in sentences]
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def char_count_of(sentences: list[dict]) -> int:
    return sum(len(sentence["text"]) for sentence in sentences)


def context_bounds(start: int, end: int, sentence_count: int, width: int = CONTEXT_SENTENCES) -> tuple[int, int, int, int]:
    return max(0, start - width), start, end, min(sentence_count, end + width)


def slice_sentences(sentences: list[dict], start: int, end: int) -> tuple[list[dict], str | None]:
    rows = [sentence for sentence in sentences if start <= sentence["index"] < end]
    if [sentence["index"] for sentence in rows] != list(range(start, end)):
        return [], f"문장 구간 [{start},{end})를 원문 순서대로 가져오지 못했습니다."
    return rows, None


def chunk_context(sentences: list[dict], start: int, end: int) -> tuple[dict | None, str | None]:
    if [item["index"] for item in sentences] != list(range(len(sentences))):
        return None, "영상 문장 index가 0부터 연속이 아닙니다."
    if start < 0 or end > len(sentences) or start >= end:
        return None, f"대상 문장 구간 [{start},{end})가 영상 밖입니다."
    before_start, target_start, target_end, after_end = context_bounds(start, end, len(sentences))
    before, before_error = slice_sentences(sentences, before_start, target_start)
    target, target_error = slice_sentences(sentences, target_start, target_end)
    after, after_error = slice_sentences(sentences, target_end, after_end)
    error = before_error or target_error or after_error
    if error:
        return None, error
    before_ids = [item["index"] for item in before]
    target_ids = [item["index"] for item in target]
    after_ids = [item["index"] for item in after]
    combined = before_ids + target_ids + after_ids
    if combined != list(range(before_start, after_end)) or len(combined) != len(set(combined)):
        return None, "문맥과 대상이 겹치거나 빠졌습니다."
    before_chars = char_count_of(before)
    after_chars = char_count_of(after)
    return {
        "before": before,
        "target": target,
        "after": after,
        "context_before_start": before_start,
        "context_before_end": target_start,
        "context_after_start": target_end,
        "context_after_end": after_end,
        "context_before_chars": before_chars,
        "context_after_chars": after_chars,
        "source_char_count": char_count_of(target) + before_chars + after_chars,
    }, None


def render_prompt(
    unit_index: int,
    sentence_start: int,
    sentence_end: int,
    chunk_index: int,
    chunk_count: int,
    sentences: list[dict],
    before: list[dict] | None = None,
    after: list[dict] | None = None,
    template: str | None = None,
) -> str:
    return render_template(
        ACTIVE_PROMPT if template is None else template,
        {
            "unit_index": str(unit_index),
            "unit_sentence_range": f"[{sentence_start},{sentence_end})",
            "chunk_index_1based": str(chunk_index + 1),
            "chunk_count": str(chunk_count),
            "context_before_json": sentences_json(before or []),
            "sentences_json": sentences_json(sentences),
            "context_after_json": sentences_json(after or []),
        },
    )


def request_body(prompt: str) -> dict:
    return {
        "model": BODY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": ENABLE_THINKING},
    }


def reuse_key(
    video_id: str,
    unit_index: int,
    chunk_index: int,
    sentence_start: int,
    sentence_end: int,
    sentences: list[dict],
    t1_sha256: str,
    alignment_sha256: str,
    metadata_sha256: str,
    effective_config_sha256: str,
    prompt: str,
    body: dict,
) -> str:
    payload = {
        "video_id": video_id,
        "unit_index": unit_index,
        "chunk_index": chunk_index,
        "sentence_start": sentence_start,
        "sentence_end": sentence_end,
        "sentences": [{"index": sentence["index"], "text": sentence["text"]} for sentence in sentences],
        "t1_sha256": t1_sha256,
        "alignment_sha256": alignment_sha256,
        "metadata_sha256": metadata_sha256,
        "effective_config_sha256": effective_config_sha256,
        "prompt": prompt,
        "request_body": body,
        "model": {
            "url": INFER_URL,
            "infer_model_id": INFER_MODEL_ID,
            "body_model": BODY_MODEL,
        },
        "logic_version": LOGIC_VERSION,
    }
    return sha256_text(canonical(payload))


def split_sentences(sentences: list[dict], budget: int = CHAR_BUDGET) -> tuple[list[list[dict]], str | None]:
    if budget < 1:
        return [], "글자 예산이 1보다 작습니다."
    groups: list[list[dict]] = []
    start = 0
    count = len(sentences)
    while start < count:
        total = 0
        end = start
        while end < count:
            length = len(sentences[end]["text"])
            if length > budget:
                return [], f"문장 {sentences[end]['index']}의 길이 {length}가 글자 예산 {budget}을 넘습니다."
            if total + length > budget:
                break
            total += length
            end += 1
        if end == start:
            return [], f"문장 {sentences[start]['index']}를 배치하지 못했습니다."
        groups.append(sentences[start:end])
        start = end
    return groups, None


def assess_video_range(
    audio_start: object,
    audio_end: object,
    offset: object,
    metadata_duration: object,
    actual_media_end: object = None,
    unit_index: int = 0,
    chunk_index: int = 0,
) -> tuple[dict | None, str | None]:
    if not is_finite_number(audio_start) or not is_finite_number(audio_end) or not is_finite_number(offset):
        return None, "청크 시각 또는 offset이 유한한 수가 아닙니다."
    if audio_end <= audio_start:
        return None, "청크 길이가 0이거나 역전되었습니다."
    video_start = audio_start + offset
    video_end = audio_end + offset
    if video_start < 0 or video_end < 0:
        return None, "영상 기준 시각이 음수입니다."
    if actual_media_end is not None:
        if not is_finite_number(actual_media_end):
            return None, "실제 미디어 끝이 유한한 수가 아닙니다."
        if video_end > actual_media_end:
            return None, "실제 미디어 범위를 초과했습니다."
    warnings: list[dict] = []
    if metadata_duration is not None:
        if not is_finite_number(metadata_duration):
            return None, "metadata 길이가 유한한 수가 아닙니다."
        if video_end > metadata_duration:
            warnings.append(
                {
                    "code": "metadata_duration_exceeded",
                    "unit_index": unit_index,
                    "chunk_index": chunk_index,
                    "metadata_duration_seconds": metadata_duration,
                    "video_end": video_end,
                    "excess_seconds": video_end - metadata_duration,
                }
            )
    return {"video_start": video_start, "video_end": video_end, "warnings": warnings}, None


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict:
    payload: dict = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"중복 키: {key}")
        payload[key] = value
    return payload


def _reject_constant(name: str) -> None:
    raise ValueError(f"허용되지 않은 값: {name}")


def strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.split("\n")
    if len(lines) < 2:
        raise SchemaError("코드 펜스가 올바르지 않습니다.")
    opening = lines[0].strip()
    closing = lines[-1].strip()
    if opening not in {"```", "```json"} or closing != "```":
        raise SchemaError("코드 펜스가 올바르지 않습니다.")
    inner = "\n".join(lines[1:-1])
    if "```" in inner:
        raise SchemaError("중첩 코드 펜스는 허용하지 않습니다.")
    return inner.strip()


def parse_object(text: str) -> dict:
    try:
        decoder = json.JSONDecoder(object_pairs_hook=_reject_pairs, parse_constant=_reject_constant)
        value, end = decoder.raw_decode(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SchemaError(str(exc)) from exc
    if text[end:].strip():
        raise SchemaError("JSON 객체가 하나가 아닙니다.")
    if not isinstance(value, dict):
        raise SchemaError("JSON 객체가 아닙니다.")
    return value


def parse_decision(text: str, allowed: set[int]) -> dict:
    payload = parse_object(strip_fence(text))
    if set(payload) != RESPONSE_KEYS:
        raise SchemaError("키가 계약을 벗어납니다.")
    decision = payload["decision"]
    if decision not in DECISIONS:
        raise SchemaError("decision이 허용 값이 아닙니다.")
    reason = payload["reason"]
    if not isinstance(reason, str) or reason.strip() == "":
        raise SchemaError("reason이 비어 있습니다.")
    evidence = payload["evidence_sentence_indices"]
    if not isinstance(evidence, list) or len(evidence) < 1:
        raise SchemaError("evidence가 비어 있습니다.")
    seen: set[int] = set()
    for item in evidence:
        if isinstance(item, bool) or not isinstance(item, int):
            raise SchemaError("evidence index가 정수가 아닙니다.")
        if item not in allowed or item in seen:
            raise SchemaError("evidence index가 청크 밖이거나 중복입니다.")
        seen.add(item)
    return {
        "decision": decision,
        "reason": reason,
        "evidence_sentence_indices": list(evidence),
    }


def extract_finish_reason(payload: dict) -> object:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    return choices[0].get("finish_reason")


def extract_reasoning(payload: dict) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning != "":
        return reasoning
    return None


def thinking_observed_from(payload: dict) -> bool | None:
    if extract_reasoning(payload) is not None:
        return True
    usage = payload.get("usage")
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
    if is_int(tokens) and tokens > 0:
        return True
    return None


def budget_cut(finish_reason: object, usage: object) -> bool:
    if finish_reason == "length":
        return True
    if not isinstance(usage, dict):
        return False
    tokens = usage.get("completion_tokens")
    return is_int(tokens) and tokens >= MAX_TOKENS and finish_reason != "stop"


def observe_response(raw_text: object) -> dict:
    empty = {
        "content": None,
        "reasoning": None,
        "thinking_observed": None,
        "finish_reason": None,
        "usage": None,
        "system_fingerprint": None,
    }
    if not isinstance(raw_text, str):
        return empty
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return empty
    if not isinstance(payload, dict):
        return empty
    content, _content_error = extract_content(payload)
    fingerprint = payload.get("system_fingerprint")
    return {
        "content": content if isinstance(content, str) else None,
        "reasoning": extract_reasoning(payload),
        "thinking_observed": thinking_observed_from(payload),
        "finish_reason": extract_finish_reason(payload),
        "usage": payload.get("usage") if "usage" in payload else None,
        "system_fingerprint": fingerprint if isinstance(fingerprint, str) else None,
    }


def extract_content(payload: dict) -> tuple[str | None, str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "choices가 없습니다."
    message = choices[0].get("message")
    if not isinstance(message, dict) or "content" not in message:
        return None, "message.content가 없습니다."
    content = message.get("content")
    if isinstance(content, str):
        return content, None
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            else:
                return None, "해석할 수 없는 content입니다."
        return "".join(parts), None
    return None, "해석할 수 없는 content입니다."


def interpret_response(status: int, raw_text: str) -> tuple[dict | None, str | None]:
    if status != 200:
        return None, f"HTTP {status}"
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return None, f"응답 JSON이 아닙니다: {exc}"
    if not isinstance(payload, dict):
        return None, "응답이 객체가 아닙니다."
    content, content_error = extract_content(payload)
    finish_reason = extract_finish_reason(payload)
    usage = payload.get("usage") if "usage" in payload else None
    if budget_cut(finish_reason, usage):
        return None, "예산 제약으로 비교 불완전"
    if content_error:
        return None, content_error
    if content is None or content == "":
        return None, "응답 텍스트가 없습니다."
    if finish_reason != "stop":
        return None, f"finish_reason={finish_reason}"
    return {"content": content, "finish_reason": finish_reason, "usage": usage}, None


def aggregate_unit(chunks: list[dict]) -> tuple[str, str]:
    if not chunks:
        return "not_selected", "ok"
    any_positive = any(chunk["status"] == "ok" and chunk["decision"] in {"needed", "uncertain"} for chunk in chunks)
    all_ok = all(chunk["status"] == "ok" for chunk in chunks)
    all_not_needed = all_ok and all(chunk["decision"] == "not_needed" for chunk in chunks)
    any_ok = any(chunk["status"] == "ok" for chunk in chunks)
    any_failed = any(chunk["status"] == "failed" for chunk in chunks)
    if any_positive:
        selection = "selected"
    elif all_not_needed:
        selection = "not_selected"
    else:
        selection = "unresolved"
    if all_ok:
        status = "ok"
    elif any_ok:
        status = "partial"
    elif any_failed:
        status = "failed"
    else:
        status = "pending"
    return selection, status


def rollup_status(statuses: list[str], force_failed: bool = False) -> str:
    if force_failed:
        return "failed"
    if not statuses:
        return "ok"
    if all(status == "ok" for status in statuses):
        return "ok"
    if any(status == "ok" for status in statuses):
        return "partial"
    if any(status == "failed" for status in statuses):
        return "failed"
    return "pending"


def iter_chunks(document: dict) -> Iterator[tuple[dict, dict]]:
    for unit in document["units"]:
        for chunk in unit["chunks"]:
            yield unit, chunk


def build_candidates(document: dict) -> list[dict]:
    candidates = []
    video_id = document["video_id"]
    for unit, chunk in iter_chunks(document):
        if chunk["status"] != "ok" or chunk["decision"] not in {"needed", "uncertain"}:
            continue
        candidates.append(
            {
                "candidate_id": f"{video_id}:u{unit['unit_index']}:c{chunk['chunk_index']}",
                "unit_index": unit["unit_index"],
                "chunk_index": chunk["chunk_index"],
                "sentence_start": chunk["sentence_start"],
                "sentence_end": chunk["sentence_end"],
                "audio_start": chunk["audio_start"],
                "audio_end": chunk["audio_end"],
                "video_start": chunk["video_start"],
                "video_end": chunk["video_end"],
                "audio_time_reference": "audio_start",
                "video_time_reference": "video_start",
                "decision": chunk["decision"],
                "reason": chunk["reason"],
                "call_ref": chunk["call_ref"],
                "warnings": copy.deepcopy(chunk["warnings"]),
            }
        )
    return candidates


def reasoning_budget_exhausted(call: object) -> bool:
    if not isinstance(call, dict):
        return False
    if call.get("http_status") != 200 or call.get("finish_reason") != "length":
        return False
    if call.get("thinking_requested") is not True or call.get("error") != "예산 제약으로 비교 불완전":
        return False
    if call.get("parsed") is not None:
        return False
    content = call.get("content")
    if isinstance(content, str) and content.strip() != "":
        return False
    usage = call.get("usage")
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    if not isinstance(details, dict):
        return False
    return usage.get("completion_tokens") == 4096 and details.get("reasoning_tokens") == 4096


def describe_budget_policy(chunk: dict, call: object, fallback: object, call_ref: object) -> dict:
    guard = chunk.get("decision") is None and chunk.get("status") != "ok" and reasoning_budget_exhausted(call)
    model_candidate = chunk.get("status") == "ok" and chunk.get("decision") in {"needed", "uncertain"}
    if model_candidate:
        source = "thinking_on"
        retention = None
    elif guard:
        source = "budget_guard"
        retention = BUDGET_GUARD_REASON
    else:
        source = None
        retention = None
    fallback_decision = None
    fallback_reason = None
    fallback_evidence = None
    fallback_call_ref = None
    if isinstance(fallback, dict):
        adopted = fallback.get("adopted_decision") if isinstance(fallback.get("adopted_decision"), dict) else fallback
        if isinstance(adopted, dict) and "decision" in adopted:
            fallback_decision = adopted.get("decision")
            fallback_reason = adopted.get("reason")
            fallback_evidence = adopted.get("evidence_sentence_indices")
        fallback_call_ref = fallback.get("fallback_call_ref")
    return {
        "policy_version": POLICY_VERSION,
        "thinking_on_status": chunk.get("status"),
        "thinking_on_error": list(chunk.get("problems") or []),
        "thinking_on_decision": chunk.get("decision"),
        "thinking_on_reason": chunk.get("reason"),
        "thinking_on_evidence": copy.deepcopy(chunk.get("evidence_sentence_indices")),
        "thinking_on_call_ref": call_ref,
        "fallback_decision": fallback_decision,
        "fallback_reason": fallback_reason,
        "fallback_evidence": copy.deepcopy(fallback_evidence),
        "fallback_call_ref": fallback_call_ref,
        "policy_candidate": source is not None,
        "candidate_source": source,
        "retention_reason": retention,
        "model_inference_success": source == "thinking_on",
    }


def policy_candidate(video_id: str, unit_index: int, chunk: dict, policy: dict) -> dict | None:
    source = policy.get("candidate_source")
    if source not in {"thinking_on", "budget_guard"}:
        return None
    candidate = {
        "candidate_id": f"{video_id}:u{unit_index}:c{chunk['chunk_index']}",
        "candidate_source": source,
        "policy_version": POLICY_VERSION,
        "model_inference_success": source == "thinking_on",
        "unit_index": unit_index,
        "chunk_index": chunk["chunk_index"],
        "sentence_start": chunk["sentence_start"],
        "sentence_end": chunk["sentence_end"],
        "audio_start": chunk["audio_start"],
        "audio_end": chunk["audio_end"],
        "video_start": chunk["video_start"],
        "video_end": chunk["video_end"],
        "audio_time_reference": "audio_start",
        "video_time_reference": "video_start",
        "warnings": copy.deepcopy(chunk.get("warnings") or []),
        "decision": None,
        "reason": None,
        "evidence_sentence_indices": None,
        "call_ref": None,
        "retention_reason": None,
        "source_call_ref": None,
    }
    if source == "thinking_on":
        candidate["decision"] = chunk.get("decision")
        candidate["reason"] = chunk.get("reason")
        candidate["evidence_sentence_indices"] = copy.deepcopy(chunk.get("evidence_sentence_indices"))
        candidate["call_ref"] = policy.get("thinking_on_call_ref")
    else:
        candidate["retention_reason"] = policy.get("retention_reason")
        candidate["source_call_ref"] = policy.get("thinking_on_call_ref")
    return candidate


def apply_budget_guard(
    documents: list[dict],
    calls_by_ident: dict[tuple[str, int, int], dict],
    fallback_by_ident: dict[tuple[str, int, int], dict],
    call_refs: dict[tuple[str, int, int], str],
) -> tuple[list[dict], dict]:
    derived: list[dict] = []
    summary = {
        "policy_version": POLICY_VERSION,
        "model_decisions": {"needed": 0, "not_needed": 0, "uncertain": 0, "none": 0},
        "inference_failures": 0,
        "pre_policy_candidates": 0,
        "policy_candidates": 0,
        "budget_guard": [],
    }
    for document in documents:
        cloned = copy.deepcopy(document)
        summary["pre_policy_candidates"] += len(document.get("candidates") or [])
        candidates = []
        seen: set[str] = set()
        for unit in cloned["units"]:
            for chunk in unit["chunks"]:
                ident = (cloned["video_id"], unit["unit_index"], chunk["chunk_index"])
                call = calls_by_ident.get(ident)
                policy = describe_budget_policy(
                    chunk,
                    call,
                    fallback_by_ident.get(ident),
                    call_refs.get(ident) or chunk.get("call_ref"),
                )
                chunk["policy"] = policy
                decision = chunk.get("decision")
                key = decision if decision in {"needed", "not_needed", "uncertain"} else "none"
                summary["model_decisions"][key] += 1
                if chunk.get("status") == "failed":
                    summary["inference_failures"] += 1
                candidate = policy_candidate(cloned["video_id"], unit["unit_index"], chunk, policy)
                if candidate is None:
                    continue
                if candidate["candidate_id"] in seen:
                    continue
                seen.add(candidate["candidate_id"])
                candidates.append(candidate)
                if candidate["candidate_source"] == "budget_guard":
                    summary["budget_guard"].append(
                        {
                            "video_id": cloned["video_id"],
                            "unit_index": unit["unit_index"],
                            "chunk_index": chunk["chunk_index"],
                        }
                    )
        cloned["candidates"] = candidates
        cloned["policy_version"] = POLICY_VERSION
        derived.append(cloned)
    summary["policy_candidates"] = sum(len(document["candidates"]) for document in derived)
    return derived, summary


def recompute_document(document: dict) -> None:
    force_video = bool(document["problems"])
    all_statuses: list[str] = []
    warnings: list[dict] = []
    validation_problems = list(document["problems"])
    for unit in document["units"]:
        force_unit = False
        for chunk in unit["chunks"]:
            all_statuses.append(chunk["status"])
            warnings.extend(chunk["warnings"])
            validation_problems.extend(chunk["problems"])
            if chunk["problems"]:
                force_unit = force_unit or any(problem.startswith("범위 오류:") for problem in chunk["problems"])
        selection, status = aggregate_unit(unit["chunks"])
        if force_unit:
            status = "failed"
            force_video = True
        unit["selection"] = selection
        unit["status"] = status
    document["warnings"] = warnings
    document["candidates"] = build_candidates(document)
    document["status"] = rollup_status(all_statuses, force_failed=force_video)
    pending = any(status == "pending" for status in all_statuses)
    failed = force_video or any(status == "failed" for status in all_statuses)
    if not document["units"] and not document["problems"]:
        document["status"] = "ok"
        document["validation"] = {"ok": True, "problems": []}
        return
    if failed:
        document["validation"] = {"ok": False, "problems": validation_problems}
    elif pending or not all_statuses:
        document["validation"] = {"ok": None, "problems": []}
    else:
        document["validation"] = {"ok": True, "problems": []}


def blank_chunk(group: list[dict], chunk_index: int, assessed: dict) -> dict:
    return {
        "chunk_index": chunk_index,
        "sentence_start": group[0]["index"],
        "sentence_end": group[-1]["index"] + 1,
        "audio_start": group[0]["start_time"],
        "audio_end": group[-1]["end_time"],
        "video_start": assessed["video_start"],
        "video_end": assessed["video_end"],
        "char_count": sum(len(sentence["text"]) for sentence in group),
        "status": "pending",
        "decision": None,
        "reason": None,
        "evidence_sentence_indices": None,
        "call_ref": None,
        "reused_from": None,
        "problems": [],
        "warnings": assessed["warnings"],
    }


def plan_units(video: dict) -> tuple[list[dict], list[dict], list[str]]:
    units = []
    flat_chunks = []
    problems: list[str] = []
    video_id = video["video_id"]
    for source in video["source_units"]:
        sentences = source["sentences"]
        groups, error = split_sentences(sentences)
        if error:
            problems.append(f"{video_id} 단위 {source['unit_index']}: {error}")
            continue
        chunks = []
        for chunk_index, group in enumerate(groups):
            audio_start = group[0]["start_time"]
            audio_end = group[-1]["end_time"]
            assessed, range_error = assess_video_range(
                audio_start,
                audio_end,
                video["offset"],
                video["duration_seconds"],
                None,
                source["unit_index"],
                chunk_index,
            )
            if range_error or assessed is None:
                problems.append(
                    f"{video_id} 단위 {source['unit_index']} 청크 {chunk_index}: {range_error}"
                )
                continue
            sentence_start = source["sentence_start"] + (group[0]["index"] - sentences[0]["index"])
            sentence_end = sentence_start + len(group)
            if sentence_start != group[0]["index"] or sentence_end != group[-1]["index"] + 1:
                problems.append(f"{video_id} 단위 {source['unit_index']} 청크 {chunk_index}: 문장 index가 연속이 아닙니다.")
                continue
            context, context_error = chunk_context(video["sentences"], sentence_start, sentence_end)
            if context_error or context is None:
                problems.append(f"{video_id} 단위 {source['unit_index']} 청크 {chunk_index}: {context_error}")
                continue
            if [item["index"] for item in context["target"]] != [item["index"] for item in group]:
                problems.append(f"{video_id} 단위 {source['unit_index']} 청크 {chunk_index}: 문맥 구성이 대상 청크와 다릅니다.")
                continue
            if [item["text"] for item in context["target"]] != [item["text"] for item in group]:
                problems.append(f"{video_id} 단위 {source['unit_index']} 청크 {chunk_index}: 대상 원문이 바뀌었습니다.")
                continue
            chunk = blank_chunk(group, chunk_index, assessed)
            chunk["sentence_start"] = sentence_start
            chunk["sentence_end"] = sentence_end
            prompt = render_prompt(
                source["unit_index"],
                source["sentence_start"],
                source["sentence_end"],
                chunk_index,
                len(groups),
                group,
                context["before"],
                context["after"],
            )
            body = request_body(prompt)
            key = reuse_key(
                video_id,
                source["unit_index"],
                chunk_index,
                sentence_start,
                sentence_end,
                group,
                video["t1_sha256"],
                video["alignment_sha256"],
                video["metadata_sha256"],
                video["effective_config_sha256"],
                ACTIVE_PROMPT,
                body,
            )
            record = {
                "video_id": video_id,
                "video_order": video["video_order"],
                "unit_index": source["unit_index"],
                "chunk_index": chunk_index,
                "sentence_start": sentence_start,
                "sentence_end": sentence_end,
                "audio_start": chunk["audio_start"],
                "audio_end": chunk["audio_end"],
                "video_start": chunk["video_start"],
                "video_end": chunk["video_end"],
                "char_count": chunk["char_count"],
                "context_before_start": context["context_before_start"],
                "context_before_end": context["context_before_end"],
                "context_after_start": context["context_after_start"],
                "context_after_end": context["context_after_end"],
                "context_before_chars": context["context_before_chars"],
                "context_after_chars": context["context_after_chars"],
                "source_char_count": context["source_char_count"],
                "rendered_chars": len(prompt),
                "rendered_sha256": sha256_text(prompt),
                "reuse_key": key,
                "warnings": chunk["warnings"],
                "sentences": group,
                "context_before": context["before"],
                "context_after": context["after"],
            }
            chunk["context_before_start"] = context["context_before_start"]
            chunk["context_before_end"] = context["context_before_end"]
            chunk["context_after_start"] = context["context_after_start"]
            chunk["context_after_end"] = context["context_after_end"]
            chunks.append(chunk)
            flat_chunks.append(record)
        units.append(
            {
                "unit_index": source["unit_index"],
                "sentence_start": source["sentence_start"],
                "sentence_end": source["sentence_end"],
                "audio_start": source["audio_start"],
                "audio_end": source["audio_end"],
                "sentences": copy.deepcopy(sentences),
                "status": "pending",
                "selection": "unresolved",
                "chunks": chunks,
            }
        )
    return units, flat_chunks, problems


def select_pilot(chunks: list[dict]) -> list[dict]:
    if not chunks:
        return []
    ordered = sorted(
        chunks,
        key=lambda chunk: (
            -chunk["rendered_chars"],
            chunk["video_order"],
            chunk["unit_index"],
            chunk["chunk_index"],
        ),
    )
    selected = [ordered[0]]
    seen = {(ordered[0]["video_id"], ordered[0]["unit_index"], ordered[0]["chunk_index"])}
    firsts: dict[int, dict] = {}
    for chunk in chunks:
        current = firsts.get(chunk["video_order"])
        ident = (chunk["unit_index"], chunk["chunk_index"])
        if current is None or ident < (current["unit_index"], current["chunk_index"]):
            firsts[chunk["video_order"]] = chunk
    for order in sorted(firsts):
        if len(selected) >= 9:
            break
        chunk = firsts[order]
        ident = (chunk["video_id"], chunk["unit_index"], chunk["chunk_index"])
        if ident in seen:
            continue
        selected.append(chunk)
        seen.add(ident)
    return selected


def select_confirm(chunks: list[dict], order: tuple[tuple[str, int], ...]) -> tuple[list[dict], str | None]:
    grouped: dict[tuple[str, int], list[dict]] = {}
    for chunk in chunks:
        grouped.setdefault((chunk["video_id"], chunk["unit_index"]), []).append(chunk)
    selected = []
    for video_id, unit_index in order:
        matches = grouped.get((video_id, unit_index), [])
        if len(matches) != 1:
            return [], f"{video_id} 단위 {unit_index}: 확인 청크가 {len(matches)}개입니다."
        selected.append(matches[0])
    return selected, None


def public_plan_chunk(chunk: dict) -> dict:
    return {
        "video_id": chunk["video_id"],
        "video_order": chunk["video_order"],
        "unit_index": chunk["unit_index"],
        "chunk_index": chunk["chunk_index"],
        "sentence_start": chunk["sentence_start"],
        "sentence_end": chunk["sentence_end"],
        "audio_start": chunk["audio_start"],
        "audio_end": chunk["audio_end"],
        "video_start": chunk["video_start"],
        "video_end": chunk["video_end"],
        "char_count": chunk["char_count"],
        "context_before_start": chunk["context_before_start"],
        "context_before_end": chunk["context_before_end"],
        "context_after_start": chunk["context_after_start"],
        "context_after_end": chunk["context_after_end"],
        "context_before_chars": chunk["context_before_chars"],
        "context_after_chars": chunk["context_after_chars"],
        "source_char_count": chunk["source_char_count"],
        "rendered_chars": chunk["rendered_chars"],
        "rendered_sha256": chunk["rendered_sha256"],
        "reuse_key": chunk["reuse_key"],
        "warnings": chunk["warnings"],
    }


def convert_chunk(video_id: str, position: int, chunk: object) -> tuple[dict | None, list[str]]:
    prefix = f"{video_id}: chunks[{position}]"
    if not isinstance(chunk, dict):
        return None, [f"{prefix}가 객체가 아닙니다."]
    text = chunk.get("text")
    language = chunk.get("language")
    source_index = chunk.get("index")
    items = chunk.get("items")
    problems: list[str] = []
    if not isinstance(text, str) or text == "":
        problems.append(f"{prefix}.text가 비어 있지 않은 문자열이 아닙니다.")
    if not isinstance(language, str) or language == "":
        problems.append(f"{prefix}.language가 비어 있지 않은 문자열이 아닙니다.")
    if not is_int(source_index):
        problems.append(f"{prefix}.index가 정수가 아닙니다.")
    if not isinstance(items, list) or not items:
        problems.append(f"{prefix}.items가 비어 있거나 배열이 아닙니다.")
        items = []
    for item_index, item in enumerate(items):
        item_prefix = f"{prefix}.items[{item_index}]"
        if not isinstance(item, dict):
            problems.append(f"{item_prefix}가 객체가 아닙니다.")
            continue
        start = item.get("start_time")
        end = item.get("end_time")
        if isinstance(start, bool) or isinstance(end, bool) or not is_finite_number(start) or not is_finite_number(end):
            problems.append(f"{item_prefix} 시각이 유한한 수가 아닙니다.")
            continue
        if start > end:
            problems.append(f"{item_prefix}의 start_time이 end_time보다 큽니다.")
    if problems:
        return None, problems
    return (
        {
            "index": position,
            "source_chunk_index": source_index,
            "start_time": items[0]["start_time"],
            "end_time": items[-1]["end_time"],
            "text": text,
            "language": language,
        },
        [],
    )


def stage1_sentences(video_id: str, payload: object) -> tuple[list[dict], list, str | None, list[str]]:
    if not isinstance(payload, dict):
        return [], [], None, [f"{video_id}: 문장 파일이 객체가 아닙니다."]
    problems: list[str] = []
    if payload.get("time_reference") != "audio_start":
        problems.append(f"{video_id}: time_reference가 audio_start가 아닙니다.")
    if payload.get("overlap_deduplicated") is not True:
        problems.append(f"{video_id}: overlap_deduplicated가 true가 아닙니다.")
    policy = payload.get("sentence_policy_id")
    if not isinstance(policy, str) or policy == "":
        problems.append(f"{video_id}: sentence_policy_id가 비어 있지 않은 문자열이 아닙니다.")
        policy = None
    chunks = payload.get("chunks")
    omitted = payload.get("omitted_chunks")
    if not isinstance(chunks, list):
        problems.append(f"{video_id}: chunks가 배열이 아닙니다.")
        chunks = []
    if not isinstance(omitted, list):
        problems.append(f"{video_id}: omitted_chunks가 배열이 아닙니다.")
        omitted = []
    sentences: list[dict] = []
    for position, chunk in enumerate(chunks):
        sentence, chunk_problems = convert_chunk(video_id, position, chunk)
        problems.extend(chunk_problems)
        if sentence is not None:
            sentences.append(sentence)
    if problems:
        return [], omitted, policy if isinstance(policy, str) else None, problems
    return sentences, omitted, policy, []


def inside_directory(base: Path, candidate: Path) -> bool:
    base_resolved = base.resolve()
    candidate_resolved = candidate.resolve()
    return candidate_resolved == base_resolved or base_resolved in candidate_resolved.parents


def load_campaign(repo: Path, input_root: Path, data_root: Path, enforce_expected: bool) -> tuple[list[dict], list[str]]:
    problems: list[str] = []
    config_path = input_root / "effective_config.json"
    if not config_path.is_file():
        return [], [f"effective_config.json이 없습니다: {config_path}"]
    try:
        config = read_json(config_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], [f"effective_config.json을 읽지 못했습니다: {exc}"]
    if not isinstance(config, dict) or not isinstance(config.get("input_ids"), list):
        return [], ["effective_config.input_ids가 배열이 아닙니다."]
    input_ids = config["input_ids"]
    if enforce_expected and input_ids != list(EXPECTED_IDS):
        problems.append(f"input_ids가 고정 순서와 다릅니다: {input_ids}")
    if any(not isinstance(video_id, str) or video_id == "" for video_id in input_ids):
        problems.append("input_ids에 빈 값이 있습니다.")
        return [], problems
    config_hash = sha256_file(config_path)
    videos: list[dict] = []
    for order, video_id in enumerate(input_ids):
        videos.append({})
        t1_path = input_root / video_id / "semantic_units.json"
        alignment_path = data_root / video_id / "audio" / "alignment_sentences.json"
        metadata_path = data_root / video_id / "metadata.json"
        if not t1_path.is_file():
            problems.append(f"{video_id}: semantic_units.json이 없습니다.")
            continue
        if not alignment_path.is_file():
            problems.append(f"{video_id}: alignment_sentences.json이 없습니다.")
            continue
        if not metadata_path.is_file():
            problems.append(f"{video_id}: metadata.json이 없습니다.")
            continue
        try:
            t1 = read_json(t1_path)
            alignment = read_json(alignment_path)
            metadata = read_json(metadata_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            problems.append(f"{video_id}: JSON을 읽지 못했습니다. {exc}")
            continue
        video_problems = validate_video(
            video_id,
            t1,
            alignment,
            metadata,
            data_root,
            enforce_expected,
        )
        if video_problems:
            problems.extend(video_problems)
            continue
        sentences, _omitted, _policy, sentence_problems = stage1_sentences(video_id, alignment)
        if sentence_problems:
            problems.extend(sentence_problems)
            continue
        source_units, unit_problems = validate_units(video_id, t1, sentences)
        if unit_problems:
            problems.extend(unit_problems)
            continue
        media = metadata["results"]["video"]
        duration = metadata.get("duration_seconds")
        duration_known = is_finite_number(duration)
        videos[order] = {
            "video_id": video_id,
            "video_order": order,
            "source_url": t1.get("source_url"),
            "title": t1.get("title"),
            "t1_path": display_path(repo, t1_path),
            "t1_sha256": sha256_file(t1_path),
            "alignment_path": display_path(repo, alignment_path),
            "alignment_sha256": sha256_file(alignment_path),
            "metadata_path": display_path(repo, metadata_path),
            "metadata_sha256": sha256_file(metadata_path),
            "effective_config_path": display_path(repo, config_path),
            "effective_config_sha256": config_hash,
            "sentence_policy_id": t1["input"]["sentence_policy_id"],
            "omitted_chunks": copy.deepcopy(t1["input"]["omitted_chunks"]),
            "time_reference": t1["input"]["time_reference"],
            "unit_time_reference": t1["time"]["unit_time_reference"],
            "offset": metadata["audio_to_video_offset_seconds"],
            "duration_seconds": duration if duration_known else None,
            "duration_known": duration_known,
            "media_path": display_path(repo, (data_root / video_id / media["path"]).resolve()),
            "result_id": media["result_id"],
            "media_sha256": media["sha256"],
            "source_units": source_units,
            "sentence_count": len(sentences),
            "sentences": sentences,
        }
    if problems:
        return [], problems
    if any(not video for video in videos):
        return [], ["입력 영상을 구성하지 못했습니다."]
    return videos, []


def validate_video(
    video_id: str,
    t1: object,
    alignment: object,
    metadata: object,
    data_root: Path,
    enforce_expected: bool,
) -> list[str]:
    problems: list[str] = []
    if not isinstance(t1, dict):
        return [f"{video_id}: semantic_units.json이 객체가 아닙니다."]
    if not isinstance(metadata, dict):
        return [f"{video_id}: metadata.json이 객체가 아닙니다."]
    if t1.get("video_id") != video_id:
        problems.append(f"{video_id}: T1 video_id가 다릅니다.")
    if t1.get("status") != "ok":
        problems.append(f"{video_id}: T1 status가 ok가 아닙니다.")
    validation = t1.get("validation")
    if not isinstance(validation, dict) or validation.get("ok") is not True:
        problems.append(f"{video_id}: T1 validation.ok가 true가 아닙니다.")
    t1_input = t1.get("input")
    t1_time = t1.get("time")
    if not isinstance(t1_input, dict) or not isinstance(t1_time, dict):
        return problems + [f"{video_id}: T1 input 또는 time이 객체가 아닙니다."]
    if t1_input.get("time_reference") != "audio_start":
        problems.append(f"{video_id}: input.time_reference가 audio_start가 아닙니다.")
    if t1_time.get("unit_time_reference") != "audio_start":
        problems.append(f"{video_id}: unit_time_reference가 audio_start가 아닙니다.")
    if not isinstance(t1_input.get("sentence_policy_id"), str) or t1_input.get("sentence_policy_id") == "":
        problems.append(f"{video_id}: sentence_policy_id가 없습니다.")
    if not isinstance(t1_input.get("omitted_chunks"), list):
        problems.append(f"{video_id}: omitted_chunks가 배열이 아닙니다.")
    if isinstance(alignment, dict):
        if alignment.get("sentence_policy_id") != t1_input.get("sentence_policy_id"):
            problems.append(f"{video_id}: sentence_policy_id가 Stage 1과 다릅니다.")
        if alignment.get("omitted_chunks") != t1_input.get("omitted_chunks"):
            problems.append(f"{video_id}: omitted_chunks가 Stage 1과 다릅니다.")
    if "audio_to_video_offset_seconds" not in metadata or "audio_to_video_offset_seconds" not in t1_time:
        problems.append(f"{video_id}: audio_to_video_offset_seconds가 누락되었습니다.")
    else:
        offset = metadata.get("audio_to_video_offset_seconds")
        stored = t1_time.get("audio_to_video_offset_seconds")
        if not is_finite_number(offset) or not is_finite_number(stored):
            problems.append(f"{video_id}: audio_to_video_offset_seconds가 유한한 수가 아닙니다.")
        elif offset != stored:
            problems.append(f"{video_id}: metadata offset과 T1 offset이 다릅니다.")
    if metadata.get("video_id") not in {None, video_id}:
        problems.append(f"{video_id}: metadata video_id가 다릅니다.")
    if isinstance(metadata.get("title"), str) and metadata.get("title") != t1.get("title"):
        problems.append(f"{video_id}: title이 metadata와 T1에서 다릅니다.")
    if isinstance(metadata.get("source_url"), str) and metadata.get("source_url") != t1.get("source_url"):
        problems.append(f"{video_id}: source_url이 metadata와 T1에서 다릅니다.")
    results = metadata.get("results")
    video = results.get("video") if isinstance(results, dict) else None
    if not isinstance(video, dict):
        problems.append(f"{video_id}: results.video가 없습니다.")
        return problems
    raw_path = video.get("path")
    result_id = video.get("result_id")
    recorded_hash = video.get("sha256")
    if not isinstance(raw_path, str) or raw_path == "":
        problems.append(f"{video_id}: 영상 경로가 없습니다.")
    elif not isinstance(result_id, str) or result_id == "" or not isinstance(recorded_hash, str) or recorded_hash == "":
        problems.append(f"{video_id}: 영상 result_id 또는 기록된 SHA-256이 없습니다.")
    else:
        candidate = data_root / video_id / raw_path
        if not inside_directory(data_root / video_id, candidate):
            problems.append(f"{video_id}: 영상 경로가 data 디렉터리 밖입니다.")
        elif not candidate.is_file():
            problems.append(f"{video_id}: 영상 파일이 없습니다: {candidate}")
    if enforce_expected:
        expected = EXPECTED_COUNTS.get(video_id)
        sentence_count = t1_input.get("sentence_count")
        unit_count = len(t1["units"]) if isinstance(t1.get("units"), list) else None
        if expected is None or sentence_count != expected[0] or unit_count != expected[1]:
            problems.append(
                f"{video_id}: 문장·단위 수가 고정 입력과 다릅니다. 문장 {sentence_count}, 단위 {unit_count}"
            )
    return problems


def validate_units(video_id: str, t1: dict, sentences: list[dict]) -> tuple[list[dict], list[str]]:
    problems: list[str] = []
    units = t1.get("units")
    if not isinstance(units, list):
        return [], [f"{video_id}: units가 배열이 아닙니다."]
    if t1["input"].get("sentence_count") != len(sentences):
        problems.append(f"{video_id}: sentence_count가 Stage 1 문장 수와 다릅니다.")
    cursor = 0
    source_units = []
    for position, unit in enumerate(units):
        prefix = f"{video_id} 단위 {position}"
        if not isinstance(unit, dict):
            problems.append(f"{prefix}가 객체가 아닙니다.")
            continue
        if unit.get("index") != position:
            problems.append(f"{prefix}: index가 연속이 아닙니다.")
        start = unit.get("sentence_start")
        end = unit.get("sentence_end")
        if not is_int(start) or not is_int(end) or end <= start or start != cursor:
            problems.append(f"{prefix}: 문장 구간이 전체를 순서대로 한 번 나누지 않습니다.")
            continue
        group = sentences[start:end]
        stored = unit.get("sentences")
        if stored != group:
            problems.append(f"{prefix}: 문장 index·원문·시각·language·source_chunk_index가 Stage 1과 다릅니다.")
            continue
        for sentence in group:
            if sentence["start_time"] > sentence["end_time"]:
                problems.append(f"{prefix}: 문장 시작이 종료보다 큽니다.")
        if unit.get("start_time") != group[0]["start_time"] or unit.get("end_time") != group[-1]["end_time"]:
            problems.append(f"{prefix}: 단위 시각이 첫·마지막 문장과 다릅니다.")
        source_units.append(
            {
                "unit_index": position,
                "sentence_start": start,
                "sentence_end": end,
                "audio_start": group[0]["start_time"],
                "audio_end": group[-1]["end_time"],
                "sentences": group,
            }
        )
        cursor = end
    if not problems and cursor != len(sentences):
        problems.append(f"{video_id}: 단위가 마지막 문장까지 덮지 않습니다.")
    if problems:
        return [], problems
    return source_units, []


def build_document(video: dict, units: list[dict]) -> dict:
    document = {
        "schema_version": SCHEMA_VERSION,
        "video_id": video["video_id"],
        "source_url": video["source_url"],
        "title": video["title"],
        "status": "pending",
        "problems": [],
        "warnings": [],
        "input": {
            "t1_semantic_units": video["t1_path"],
            "t1_sha256": video["t1_sha256"],
            "alignment_sentences": video["alignment_path"],
            "alignment_sha256": video["alignment_sha256"],
            "metadata": video["metadata_path"],
            "metadata_sha256": video["metadata_sha256"],
            "effective_config": video["effective_config_path"],
            "effective_config_sha256": video["effective_config_sha256"],
            "sentence_policy_id": video["sentence_policy_id"],
            "omitted_chunks": video["omitted_chunks"],
            "time_reference": video["time_reference"],
            "unit_time_reference": video["unit_time_reference"],
        },
        "media": {
            "path": video["media_path"],
            "result_id": video["result_id"],
            "sha256": video["media_sha256"],
            "duration_seconds": video["duration_seconds"],
            "duration_known": video["duration_known"],
            "audio_to_video_offset_seconds": video["offset"],
        },
        "units": units,
        "candidates": [],
        "validation": {"ok": None, "problems": []},
    }
    recompute_document(document)
    return document


def review_status_from_text(text: str) -> str:
    found = "pending_review"
    for line in text.splitlines():
        if not line.startswith("- 판단:"):
            continue
        value = line.split(":", 1)[1].strip()
        if value in {"pending_review", "accepted", "revise"}:
            found = value
    return found


def format_sentence_line(sentence: dict) -> str:
    return (
        f"- [{sentence['index']}] {time_literal(sentence['start_time'])}-{time_literal(sentence['end_time'])} "
        f"{md_escape(sentence['text'])}"
    )


def append_sentence_span(lines: list[str], rows: dict[int, dict], start: object, end: object) -> None:
    if not is_int(start) or not is_int(end) or start == end:
        lines.append("빈 배열 [].")
        return
    for index in range(start, end):
        row = rows.get(index)
        if row is None:
            lines.append(f"- [{index}] 문장을 찾지 못했습니다.")
        else:
            lines.append(format_sentence_line(row))


def render_review(document: dict) -> str:
    media = document["media"]
    duration = media.get("duration_seconds")
    selected = sum(1 for unit in document["units"] if unit["selection"] == "selected")
    not_selected = sum(1 for unit in document["units"] if unit["selection"] == "not_selected")
    unresolved = sum(1 for unit in document["units"] if unit["selection"] == "unresolved")
    lines = [
        f"# {md_escape(document.get('title') or '')}",
        "",
        f"- video id: {document['video_id']}",
        f"- 상태: {document['status']}",
        f"- 선정: selected {selected}, not_selected {not_selected}, unresolved {unresolved}",
        f"- URL: {md_escape(document.get('source_url') or '')}",
        f"- 영상 경로: {media.get('path')}",
        f"- result_id: {media.get('result_id')}",
        f"- 기록된 영상 SHA-256: {media.get('sha256')}",
        f"- offset: {time_literal(media.get('audio_to_video_offset_seconds'))}",
        f"- 제외 청크: {md_escape(json.dumps(document['input'].get('omitted_chunks'), ensure_ascii=False))}",
    ]
    if duration is None:
        lines.append("- 영상 길이: 미확인. metadata 길이 상한 검사를 하지 못했습니다.")
    else:
        lines.append(f"- 영상 길이: {time_literal(duration)}")
    if document["problems"]:
        lines.append("- problems:")
        lines.extend(f"  - {md_escape(problem)}" for problem in document["problems"])
    else:
        lines.append("- problems: 없음")
    if document["warnings"]:
        lines.append("- 경고: metadata 길이 초과이며 실패·미선정 개수에 넣지 않습니다.")
        for warning in document["warnings"]:
            lines.append(
                "  - "
                f"{warning['code']} 단위 {warning['unit_index']} 청크 {warning['chunk_index']} "
                f"video_end {time_literal(warning['video_end'])} "
                f"초과 {time_literal(warning['excess_seconds'])}초"
            )
    else:
        lines.append("- 경고: 없음")
    lines.append("")
    if not document["units"]:
        lines.append("단위 없음")
        lines.append("")
    sentence_rows = {
        sentence["index"]: sentence for unit in document["units"] for sentence in unit["sentences"]
    }
    for unit in document["units"]:
        lines.extend(
            [
                f"## 단위 {unit['unit_index']}",
                "",
                f"- 선택: {unit['selection']}",
                f"- 상태: {unit['status']}",
                f"- 문장 구간: [{unit['sentence_start']},{unit['sentence_end']})",
                f"- 오디오 시각: {time_literal(unit['audio_start'])}-{time_literal(unit['audio_end'])}",
                "",
                "### 원문",
                "",
            ]
        )
        for sentence in unit["sentences"]:
            lines.append(
                f"- [{sentence['index']}] {time_literal(sentence['start_time'])}-{time_literal(sentence['end_time'])} "
                f"{md_escape(sentence['text'])}"
            )
        lines.append("")
        for chunk in unit["chunks"]:
            lines.extend(
                [
                    f"### 청크 {chunk['chunk_index']}",
                    "",
                    f"- 상태: {chunk['status']}",
                    f"- 판정: {chunk['decision']}",
                    f"- 이유: {md_escape(chunk['reason'] or '')}",
                    f"- 근거 문장: {chunk['evidence_sentence_indices']}",
                    f"- 문장 구간: [{chunk['sentence_start']},{chunk['sentence_end']})",
                    f"- 오디오 시각: {time_literal(chunk['audio_start'])}-{time_literal(chunk['audio_end'])}",
                    f"- 영상 시각: {time_literal(chunk['video_start'])}-{time_literal(chunk['video_end'])}",
                    f"- 글자 수: {chunk['char_count']}",
                    f"- call_ref: {chunk['call_ref']}",
                    f"- reused_from: {md_escape(json.dumps(chunk['reused_from'], ensure_ascii=False))}",
                ]
            )
            if chunk["problems"]:
                lines.append("- 실패:")
                lines.extend(f"  - {md_escape(problem)}" for problem in chunk["problems"])
            else:
                lines.append("- 실패: 없음")
            if chunk["warnings"]:
                lines.append("- 경고:")
                for warning in chunk["warnings"]:
                    lines.append(
                        f"  - {warning['code']} 초과 {time_literal(warning['excess_seconds'])}초"
                    )
            else:
                lines.append("- 경고: 없음")
            lines.append("- 후보 범위는 대상 청크의 문장 구간과 오디오·영상 시각이다.")
            if "context_before_start" in chunk:
                lines.extend(["", "#### 앞 문맥", ""])
                append_sentence_span(
                    lines,
                    sentence_rows,
                    chunk.get("context_before_start"),
                    chunk.get("context_before_end"),
                )
            lines.extend(["", "#### 청크 원문", ""])
            for sentence in unit["sentences"]:
                if chunk["sentence_start"] <= sentence["index"] < chunk["sentence_end"]:
                    lines.append(format_sentence_line(sentence))
            if "context_after_start" in chunk:
                lines.extend(["", "#### 뒤 문맥", ""])
                append_sentence_span(
                    lines,
                    sentence_rows,
                    chunk.get("context_after_start"),
                    chunk.get("context_after_end"),
                )
            lines.append("")
    return "\n".join(lines)


def chunk_decision_counts(document: dict) -> dict[str, int]:
    counts = {"needed": 0, "not_needed": 0, "uncertain": 0, "failed": 0, "pending": 0, "reused": 0}
    for _unit, chunk in iter_chunks(document):
        if chunk["status"] == "ok" and chunk["decision"] in counts:
            counts[chunk["decision"]] += 1
        if chunk["status"] == "failed":
            counts["failed"] += 1
        if chunk["status"] == "pending":
            counts["pending"] += 1
        if chunk.get("reused_from"):
            counts["reused"] += 1
    return counts


def render_index(documents: list[dict], review_state: str) -> str:
    lines = [
        "# T2 시각 분석 후보",
        "",
        f"- 검수 상태: {review_state}",
        "- 경고는 실패·미선정 개수에 포함하지 않습니다.",
        "- 후보 시간 합은 겹친 구간을 포함한 관찰값이며 영상 커버리지가 아닙니다.",
        "",
        "| video | 상태 | selected | not_selected | unresolved | needed | not_needed | uncertain | failed | reused | warnings | candidates | 후보 시간 합 | review |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for document in documents:
        counts = chunk_decision_counts(document)
        selected = sum(1 for unit in document["units"] if unit["selection"] == "selected")
        not_selected = sum(1 for unit in document["units"] if unit["selection"] == "not_selected")
        unresolved = sum(1 for unit in document["units"] if unit["selection"] == "unresolved")
        duration_sum = 0.0
        for candidate in document["candidates"]:
            duration_sum += candidate["video_end"] - candidate["video_start"]
        title_note = md_escape(document.get("title") or "").replace("\n", " ")
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{document['video_id']}<br>{title_note}",
                    str(document["status"]),
                    str(selected),
                    str(not_selected),
                    str(unresolved),
                    str(counts["needed"]),
                    str(counts["not_needed"]),
                    str(counts["uncertain"]),
                    str(counts["failed"]),
                    str(counts["reused"]),
                    str(len(document["warnings"])),
                    str(len(document["candidates"])),
                    time_literal(duration_sum),
                    f"[review]({document['video_id']}/review.md)",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid.uuid4().hex[:6]}"


def review_header() -> str:
    return (
        "# T2 검토 기록\n"
        "\n"
        "pilot 검토, 재검토, 최종 판단은 UTC 시각과 검토자, 대상 manifest hash가 있는 항목으로 이어 씁니다.\n"
        "실행기는 품질을 채택하지 않습니다.\n"
    )


@contextlib.contextmanager
def run_lock(run_dir: Path) -> Iterator[None]:
    run_dir.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(run_dir / ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise RunBusy("같은 run을 다른 프로세스가 쓰고 있습니다.") from exc
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def write_views(repo: Path, run_dir: Path, documents: list[dict]) -> None:
    decision_path = run_dir / "review_decision.md"
    review_state = "pending_review"
    if decision_path.is_file():
        review_state = review_status_from_text(decision_path.read_text(encoding="utf-8"))
    for document in documents:
        video_dir = run_dir / document["video_id"]
        dump_json(video_dir / "visual_candidates.json", document)
        video_dir.joinpath("review.md").write_text(render_review(document), encoding="utf-8")
    (run_dir / "index.md").write_text(render_index(documents, review_state), encoding="utf-8")
    if not decision_path.exists():
        decision_path.write_text(review_header(), encoding="utf-8")


SENTENCE_MARKER = "대상 문장(JSON 배열; 각 항목은 sentence_index와 text):\n"


def json_array_after(prompt: str, marker: str) -> list:
    text = prompt.split(marker, 1)[1]
    rows, _end = json.JSONDecoder().raw_decode(text)
    if not isinstance(rows, list):
        raise SchemaError("문장 배열이 아닙니다.")
    return rows


def request_sentence_indices(prompt: str) -> list[int]:
    rows = json_array_after(prompt, SENTENCE_MARKER)
    return [row["sentence_index"] for row in rows]


def stored_decision(record: dict, allowed: set[int]) -> tuple[dict | None, list[str]]:
    issues: list[str] = []
    if record.get("http_status") != 200:
        issues.append(f"HTTP {record.get('http_status')}")
    if record.get("finish_reason") != "stop":
        issues.append(f"finish_reason={record.get('finish_reason')}")
    content = record.get("content")
    response_body = record.get("response_body")
    if not isinstance(content, str) or content == "":
        issues.append("저장된 content가 없습니다.")
    if not isinstance(response_body, str):
        issues.append("저장된 response_body가 없습니다.")
    elif isinstance(record.get("http_status"), int):
        interpreted, error = interpret_response(record["http_status"], response_body)
        if error:
            issues.append(error)
        elif not isinstance(content, str) or interpreted is None or interpreted["content"] != content:
            issues.append("저장 content와 response_body가 다릅니다.")
    parsed = None
    if isinstance(content, str) and content != "":
        try:
            parsed = parse_decision(content, allowed)
        except SchemaError as exc:
            issues.append(str(exc))
    if parsed is not None and parsed["evidence_sentence_indices"] != json.loads(strip_fence(content))["evidence_sentence_indices"]:
        issues.append("evidence를 잘라 통과시켰습니다.")
    return parsed, issues


def revalidate_stored(run_dir: Path, repo: Path) -> dict:
    manifest_path = run_dir / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = sha256_bytes(manifest_bytes)
    call_paths = sorted((run_dir / "calls").glob("*.json"))
    call_hashes = {display_path(repo, path): sha256_file(path) for path in call_paths}
    manifest = read_json(manifest_path)
    run = read_json(run_dir / "run.json")
    documents = load_documents(run_dir, manifest_video_ids(manifest))
    calls_by_ident: dict[tuple[str, int, int], list[dict]] = {}
    for path in call_paths:
        record = read_json(path)
        ident = (record["video_id"], record["unit_index"], record["chunk_index"])
        calls_by_ident.setdefault(ident, []).append(record)
    outcomes = []
    blockers = []
    for document in documents:
        for unit, chunk in iter_chunks(document):
            ident = (document["video_id"], unit["unit_index"], chunk["chunk_index"])
            records = calls_by_ident.get(ident, [])
            if len(records) != 1:
                blockers.append(f"{ident}: 저장 호출이 {len(records)}개입니다.")
                continue
            record = records[0]
            prompt = record["request_body"]["messages"][0]["content"]
            indices = request_sentence_indices(prompt)
            if indices != list(range(chunk["sentence_start"], chunk["sentence_end"])):
                blockers.append(f"{ident}: 요청 문장 index가 청크와 다릅니다.")
                continue
            parsed, issues = stored_decision(record, set(indices))
            prior_status = chunk["status"]
            outcome = {
                "video_id": document["video_id"],
                "unit_index": unit["unit_index"],
                "chunk_index": chunk["chunk_index"],
                "call_ref": record.get("call_ref"),
                "prior_status": prior_status,
                "prior_error": record.get("error"),
                "issues": issues,
            }
            if parsed is None or issues:
                blockers.append(f"{ident}: {issues}")
                outcomes.append(outcome)
                continue
            if prior_status == "ok":
                same = (
                    chunk["decision"] == parsed["decision"]
                    and chunk["reason"] == parsed["reason"]
                    and chunk["evidence_sentence_indices"] == parsed["evidence_sentence_indices"]
                )
                if not same:
                    blockers.append(f"{ident}: 기존 성공 판정과 재검증 결과가 다릅니다.")
                    outcome["issues"] = ["기존 성공 판정과 재검증 결과가 다릅니다."]
            outcome["decision"] = parsed["decision"]
            outcome["evidence_sentence_indices"] = parsed["evidence_sentence_indices"]
            outcome["evidence_count"] = len(parsed["evidence_sentence_indices"])
            outcome["parsed"] = parsed
            outcomes.append(outcome)
    changed = []
    if blockers:
        for document in documents:
            recompute_document(document)
    else:
        for document in documents:
            for unit, chunk in iter_chunks(document):
                outcome = next(
                    item
                    for item in outcomes
                    if item["video_id"] == document["video_id"]
                    and item["unit_index"] == unit["unit_index"]
                    and item["chunk_index"] == chunk["chunk_index"]
                )
                if outcome["prior_status"] == "ok":
                    continue
                apply_success(chunk, outcome["parsed"], chunk["call_ref"], chunk.get("reused_from"))
                changed.append({key: value for key, value in outcome.items() if key != "parsed"})
        for document in documents:
            recompute_document(document)
        for document in documents:
            dump_json(run_dir / document["video_id"] / "visual_candidates.json", document)
        write_views(repo, run_dir, documents)
        refresh_run_counts(run, documents)
        all_ok = all(document["validation"]["ok"] is True for document in documents) and all_chunks_ok(documents)
        run["status"] = "completed" if all_ok else "incomplete"
        run["revalidation"] = {
            "rule_version": VALIDATION_RULE_VERSION,
            "record": display_path(repo, run_dir / "revalidation.json"),
            "inference_calls": 0,
            "at": utc_now(),
        }
        save_run(run_dir, run)
    if manifest_path.read_bytes() != manifest_bytes:
        raise RuntimeError("manifest.json이 바뀌었습니다.")
    for path in call_paths:
        if sha256_file(path) != call_hashes[display_path(repo, path)]:
            raise RuntimeError(f"호출 기록이 바뀌었습니다: {path.name}")
    warnings = []
    for document in documents:
        for warning in document["warnings"]:
            warnings.append({"video_id": document["video_id"], **warning})
    counts = count_chunks(documents)
    report = {
        "schema_version": "t2.revalidation.v1",
        "run_id": manifest["run_id"],
        "created_at": utc_now(),
        "inference_calls": 0,
        "original_manifest_sha256": manifest_sha,
        "manifest_path": display_path(repo, manifest_path),
        "manifest_modified": False,
        "calls_preserved": True,
        "call_count": len(call_paths),
        "request_evidence_rule": {
            "logic_version": manifest["logic_version"],
            "prompt_requirement": "요청 프롬프트는 evidence_sentence_indices 1~3개를 요구했다.",
            "note": "요청 당시 프롬프트와 t2.decision.v1은 1~3개를 요구했다. 프롬프트 파일과 manifest는 바꾸지 않았다.",
        },
        "validation_rule_version": VALIDATION_RULE_VERSION,
        "validation_rule": {
            "minimum": 1,
            "maximum": None,
            "unique_integers": True,
            "in_chunk": True,
            "reject_bool": True,
            "reject_float": True,
            "truncate": False,
            "note": "사후 검증에서 개수 상한만 없앴다. 근거를 삭제하거나 앞 3개로 자르지 않는다.",
        },
        "source": code_version(repo),
        "applied": not blockers,
        "blockers": blockers,
        "counts": counts,
        "changed_chunks": changed,
        "warnings": warnings,
    }
    dump_json(run_dir / "revalidation.json", report)
    return report


def load_documents(run_dir: Path, video_ids: list[str]) -> list[dict]:
    documents = []
    for video_id in video_ids:
        documents.append(read_json(run_dir / video_id / "visual_candidates.json"))
    return documents


def manifest_video_ids(manifest: dict) -> list[str]:
    return [item["video_id"] for item in manifest["inputs"]]


def chunk_map(document: dict) -> dict[tuple[int, int], dict]:
    found = {}
    for unit, chunk in iter_chunks(document):
        found[(unit["unit_index"], chunk["chunk_index"])] = chunk
    return found


def count_chunks(documents: list[dict]) -> dict[str, int]:
    counts = {"new_calls": 0, "reused": 0, "failed": 0, "pending": 0}
    for _unit, chunk in (pair for document in documents for pair in iter_chunks(document)):
        if chunk["status"] == "failed":
            counts["failed"] += 1
        elif chunk["status"] == "pending":
            counts["pending"] += 1
        elif chunk.get("reused_from"):
            counts["reused"] += 1
        elif chunk["status"] == "ok":
            counts["new_calls"] += 1
    return counts


def save_run(run_dir: Path, run: dict) -> None:
    dump_json(run_dir / "run.json", run)


def refresh_run_counts(run: dict, documents: list[dict]) -> None:
    counts = count_chunks(documents)
    run["counts"] = {
        "new_calls": counts["new_calls"],
        "reused": counts["reused"],
        "failed": counts["failed"],
        "pending": counts["pending"],
        "http_attempts": run.get("counts", {}).get("http_attempts", 0),
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class InferClient:
    created = 0

    def __init__(self) -> None:
        InferClient.created += 1
        self._opener = urllib.request.build_opener(_NoRedirect())

    def post(self, body: dict) -> dict:
        started = time.perf_counter()
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            INFER_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read()
                status = response.status
            elapsed = time.perf_counter() - started
            return {
                "http_status": status,
                "response_body": raw.decode("utf-8"),
                "elapsed_seconds": elapsed,
                "error": None,
            }
        except urllib.error.HTTPError as exc:
            elapsed = time.perf_counter() - started
            raw = exc.read()
            return {
                "http_status": exc.code,
                "response_body": raw.decode("utf-8", errors="replace"),
                "elapsed_seconds": elapsed,
                "error": None,
            }
        except TimeoutError:
            elapsed = time.perf_counter() - started
            return {
                "http_status": None,
                "response_body": None,
                "elapsed_seconds": elapsed,
                "error": "요청이 시간 초과되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다.",
            }
        except urllib.error.URLError as exc:
            elapsed = time.perf_counter() - started
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                message = "요청이 시간 초과되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다."
            else:
                message = f"추론 요청에 실패했습니다: {reason}"
            return {
                "http_status": None,
                "response_body": None,
                "elapsed_seconds": elapsed,
                "error": message,
            }


def call_record(
    repo: Path,
    run_id: str,
    chunk: dict,
    body: dict,
    transport: dict,
    content: str | None,
    finish_reason: object,
    usage: object,
    parsed: dict | None,
    error: str | None,
    reasoning: str | None = None,
    thinking_observed: bool | None = None,
    system_fingerprint: str | None = None,
) -> dict:
    call_id = str(uuid.uuid4())
    response_body = transport.get("response_body")
    response_sha = sha256_text(response_body) if isinstance(response_body, str) else None
    kwargs = body.get("chat_template_kwargs")
    requested = isinstance(kwargs, dict) and kwargs.get("enable_thinking") is True
    return {
        "schema_version": SCHEMA_VERSION,
        "call_id": call_id,
        "video_id": chunk["video_id"],
        "unit_index": chunk["unit_index"],
        "chunk_index": chunk["chunk_index"],
        "reuse_key": chunk["reuse_key"],
        "url": INFER_URL,
        "request_body": body,
        "http_status": transport.get("http_status"),
        "response_body": response_body,
        "response_sha256": response_sha,
        "content": content,
        "reasoning": reasoning,
        "thinking_requested": requested,
        "thinking_observed": thinking_observed,
        "system_fingerprint": system_fingerprint,
        "finish_reason": finish_reason,
        "usage": usage,
        "parsed": parsed,
        "error": error,
        "elapsed_seconds": transport.get("elapsed_seconds"),
        "started_at": utc_now(),
        "call_ref": f"tests/T2/runs/{run_id}/calls/{call_id}.json" if repo == ROOT else display_path(
            repo, repo / "tests" / "T2" / "runs" / run_id / "calls" / f"{call_id}.json"
        ),
    }


def locate_reusable(runs_root: Path, run_id: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not runs_root.is_dir():
        return found
    for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir() and not path.name.startswith(".")):
        if run_dir.name == run_id:
            continue
        calls_dir = run_dir / "calls"
        if not calls_dir.is_dir():
            continue
        for path in sorted(calls_dir.glob("*.json")):
            try:
                record = read_json(path)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict) or record.get("error") is not None or not isinstance(record.get("parsed"), dict):
                continue
            key = record.get("reuse_key")
            response_body = record.get("response_body")
            if not isinstance(key, str) or not isinstance(response_body, str):
                continue
            digest = sha256_text(response_body)
            if record.get("response_sha256") != digest:
                continue
            found.setdefault(
                key,
                {
                    "record": record,
                    "run_id": run_dir.name,
                    "call_ref": record.get("call_ref"),
                    "response_sha256": digest,
                },
            )
    return found


def apply_success(chunk_state: dict, decision: dict, call_ref: str, reused_from: dict | None) -> None:
    chunk_state["status"] = "ok"
    chunk_state["decision"] = decision["decision"]
    chunk_state["reason"] = decision["reason"]
    chunk_state["evidence_sentence_indices"] = decision["evidence_sentence_indices"]
    chunk_state["call_ref"] = call_ref
    chunk_state["reused_from"] = reused_from
    chunk_state["problems"] = []


def apply_failure(chunk_state: dict, message: str, call_ref: str | None) -> None:
    chunk_state["status"] = "failed"
    chunk_state["decision"] = None
    chunk_state["reason"] = None
    chunk_state["evidence_sentence_indices"] = None
    chunk_state["call_ref"] = call_ref
    chunk_state["reused_from"] = None
    chunk_state["problems"] = [message]


def document_sentences(document: dict) -> list[dict]:
    rows: list[dict] = []
    for unit in document["units"]:
        rows.extend(unit["sentences"])
    return rows


def prompt_for_plan_chunk(manifest_chunk: dict, document: dict) -> tuple[str, set[int], list[dict], str | None]:
    unit = next(item for item in document["units"] if item["unit_index"] == manifest_chunk["unit_index"])
    group = [
        sentence
        for sentence in unit["sentences"]
        if manifest_chunk["sentence_start"] <= sentence["index"] < manifest_chunk["sentence_end"]
    ]
    context, error = chunk_context(document_sentences(document), manifest_chunk["sentence_start"], manifest_chunk["sentence_end"])
    if error or context is None:
        return "", set(), [], error or "문맥을 구성하지 못했습니다."
    for field in (
        "context_before_start",
        "context_before_end",
        "context_after_start",
        "context_after_end",
        "context_before_chars",
        "context_after_chars",
        "source_char_count",
    ):
        if context[field] != manifest_chunk[field]:
            return "", set(), [], f"{field}가 manifest와 다릅니다."
    if [item["text"] for item in context["target"]] != [item["text"] for item in group]:
        return "", set(), [], "대상 원문이 manifest 문장 구간과 다릅니다."
    chunk_count = len(unit["chunks"])
    prompt = render_prompt(
        unit["unit_index"],
        unit["sentence_start"],
        unit["sentence_end"],
        manifest_chunk["chunk_index"],
        chunk_count,
        group,
        context["before"],
        context["after"],
    )
    allowed = {sentence["index"] for sentence in group}
    return prompt, allowed, group, None


def execute_targets(
    repo: Path,
    runs_root: Path,
    run_dir: Path,
    manifest: dict,
    documents: list[dict],
    targets: list[dict],
    client: Any,
    run: dict,
    reuse: bool = True,
    explicit_reuse: dict[tuple[str, int, int], dict] | None = None,
    stop_on_failure: bool = True,
) -> str | None:
    reusable = {} if explicit_reuse is not None or not reuse else locate_reusable(runs_root, manifest["run_id"])
    if explicit_reuse:
        indexed = list(enumerate(targets))
        indexed.sort(
            key=lambda pair: (
                0
                if (pair[1]["video_id"], pair[1]["unit_index"], pair[1]["chunk_index"]) in explicit_reuse
                else 1,
                pair[0],
            )
        )
        targets = [item for _index, item in indexed]
    documents_by_id = {document["video_id"]: document for document in documents}
    states = {document["video_id"]: chunk_map(document) for document in documents}
    plan_by_ident = {
        (item["video_id"], item["unit_index"], item["chunk_index"]): item for item in manifest["plan"]
    }
    for target in targets:
        ident = (target["video_id"], target["unit_index"], target["chunk_index"])
        plan_chunk = plan_by_ident[ident]
        state = states[target["video_id"]][(target["unit_index"], target["chunk_index"])]
        if state["status"] == "ok":
            continue
        if state["status"] == "failed":
            continue
        document = documents_by_id[target["video_id"]]
        prompt, allowed, group, prompt_error = prompt_for_plan_chunk(plan_chunk, document)
        if prompt_error:
            return f"{ident}: {prompt_error}"
        if sha256_text(prompt) != plan_chunk["rendered_sha256"]:
            return f"{ident}: 렌더링 결과가 manifest와 다릅니다."
        body = request_body(prompt)
        forced = None if explicit_reuse is None else explicit_reuse.get(ident)
        if forced is not None:
            mismatch = request_body_difference(forced.get("request_body"), body)
            if mismatch:
                return f"{ident}: 재사용 요청이 다릅니다. 다시 호출하지 않습니다. {'; '.join(mismatch)}"
            try:
                parsed = parse_decision(forced.get("content") or "", allowed)
            except SchemaError as exc:
                return f"{ident}: 재사용 응답 검증 실패: {exc}. 다시 호출하지 않습니다."
            source = display_path(repo, forced["_path"])
            apply_success(
                state,
                parsed,
                source,
                {
                    "run_id": forced["_run_id"],
                    "video_id": forced.get("video_id"),
                    "unit_index": forced.get("unit_index"),
                    "chunk_index": forced.get("chunk_index"),
                    "call_ref": source,
                    "response_sha256": forced.get("response_sha256"),
                },
            )
            print(
                f"{target['video_id']} unit {target['unit_index']} chunk {target['chunk_index']} reused",
                flush=True,
            )
            recompute_document(document)
            dump_json(run_dir / document["video_id"] / "visual_candidates.json", document)
            refresh_run_counts(run, documents)
            save_run(run_dir, run)
            write_views(repo, run_dir, documents)
            continue
        key = reuse_key(
            target["video_id"],
            target["unit_index"],
            target["chunk_index"],
            plan_chunk["sentence_start"],
            plan_chunk["sentence_end"],
            group,
            document["input"]["t1_sha256"],
            document["input"]["alignment_sha256"],
            document["input"]["metadata_sha256"],
            document["input"]["effective_config_sha256"],
            ACTIVE_PROMPT,
            body,
        )
        if key != plan_chunk["reuse_key"]:
            return f"{ident}: 재사용 키가 manifest와 다릅니다."
        cached = reusable.get(key)
        if cached is not None:
            try:
                decision = parse_decision(cached["record"].get("content") or "", allowed)
            except SchemaError:
                cached = None
        if cached is not None:
            apply_success(
                state,
                decision,
                cached["call_ref"],
                {
                    "run_id": cached["run_id"],
                    "video_id": cached["record"].get("video_id"),
                    "unit_index": cached["record"].get("unit_index"),
                    "chunk_index": cached["record"].get("chunk_index"),
                    "call_ref": cached["call_ref"],
                    "response_sha256": cached["response_sha256"],
                },
            )
        else:
            transport = client.post(body)
            run["counts"]["http_attempts"] = run.get("counts", {}).get("http_attempts", 0) + 1
            elapsed = transport.get("elapsed_seconds")
            if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
                run["call_elapsed_seconds"] = run.get("call_elapsed_seconds", 0) + elapsed
            observed = observe_response(transport.get("response_body"))
            content = None
            finish_reason = observed["finish_reason"]
            usage = observed["usage"]
            parsed = None
            error = transport.get("error")
            if error is None and isinstance(transport.get("response_body"), str):
                interpreted, interpret_error = interpret_response(transport["http_status"], transport["response_body"])
                if interpret_error:
                    error = interpret_error
                    content = observed["content"]
                elif interpreted is not None:
                    content = interpreted["content"]
                    finish_reason = interpreted["finish_reason"]
                    usage = interpreted["usage"]
                    try:
                        parsed = parse_decision(content, allowed)
                    except SchemaError as exc:
                        error = str(exc)
            record = call_record(
                repo,
                manifest["run_id"],
                plan_chunk,
                body,
                transport,
                content,
                finish_reason,
                usage,
                parsed,
                error,
                reasoning=observed["reasoning"],
                thinking_observed=observed["thinking_observed"],
                system_fingerprint=observed["system_fingerprint"],
            )
            dump_json(run_dir / "calls" / f"{record['call_id']}.json", record)
            if error is None and parsed is not None:
                apply_success(state, parsed, record["call_ref"], None)
                print(
                    f"{target['video_id']} unit {target['unit_index']} chunk {target['chunk_index']} success",
                    flush=True,
                )
            else:
                apply_failure(state, error or "호출이 실패했습니다.", record["call_ref"])
                recompute_document(document)
                dump_json(run_dir / document["video_id"] / "visual_candidates.json", document)
                refresh_run_counts(run, documents)
                run["status"] = "failed"
                run["stage_error"] = error or "호출이 실패했습니다."
                run["ended_at"] = utc_now()
                save_run(run_dir, run)
                write_views(repo, run_dir, documents)
                print(
                    f"{target['video_id']} unit {target['unit_index']} chunk {target['chunk_index']} failure: {error}",
                    flush=True,
                )
                if stop_on_failure:
                    return error or "호출이 실패했습니다."
        recompute_document(document)
        dump_json(run_dir / document["video_id"] / "visual_candidates.json", document)
        refresh_run_counts(run, documents)
        save_run(run_dir, run)
        write_views(repo, run_dir, documents)
    return None


def verify_manifest_inputs(repo: Path, data_root: Path, input_root: Path, manifest: dict, enforce_expected: bool) -> str | None:
    videos, problems = load_campaign(repo, input_root, data_root, enforce_expected)
    if problems:
        return "\n".join(problems)
    current = {video["video_id"]: video for video in videos}
    if [video["video_id"] for video in videos] != manifest_video_ids(manifest):
        return "입력 순서가 manifest와 다릅니다."
    for stored in manifest["inputs"]:
        video = current[stored["video_id"]]
        for field in ("t1_sha256", "alignment_sha256", "metadata_sha256"):
            if video[field] != stored[field]:
                return f"{stored['video_id']}: {field}가 manifest와 다릅니다."
        if video["effective_config_sha256"] != manifest["effective_config"]["sha256"]:
            return "effective_config hash가 manifest와 다릅니다."
    rebuilt_units, rebuilt_chunks, plan_problems = [], [], []
    for video in videos:
        units, chunks, unit_problems = plan_units(video)
        plan_problems.extend(unit_problems)
        rebuilt_units.append(units)
        rebuilt_chunks.extend(chunks)
    if plan_problems:
        return "\n".join(plan_problems)
    current_plan = [public_plan_chunk(chunk) for chunk in rebuilt_chunks]
    comparable = []
    for item in manifest["plan"]:
        comparable.append({key: item[key] for key in current_plan[0]})
    if current_plan != comparable:
        return "청크 계획이 manifest와 다릅니다."
    return None


def load_run_bundle(run_dir: Path) -> tuple[dict, dict, list[dict]]:
    manifest = read_json(run_dir / "manifest.json")
    run = read_json(run_dir / "run.json")
    documents = load_documents(run_dir, manifest_video_ids(manifest))
    return manifest, run, documents


def all_chunks_ok(documents: list[dict]) -> bool:
    chunks = [chunk for document in documents for _unit, chunk in iter_chunks(document)]
    return bool(chunks) and all(chunk["status"] == "ok" for chunk in chunks) or (
        not chunks and all(document["validation"]["ok"] is True for document in documents)
    )


def different_condition(manifest: dict) -> bool:
    settings = manifest.get("settings")
    if not isinstance(settings, dict):
        return True
    return settings.get("prompt_id") != PROMPT_ID or manifest.get("prompt") != ACTIVE_PROMPT


def baseline_manifest_path() -> Path:
    return RUNS_ROOT / BASELINE_RUN_ID / "manifest.json"


def confirm_idents(chunks: list[dict]) -> list[tuple[str, int, int]]:
    return [(chunk["video_id"], chunk["unit_index"], chunk["chunk_index"]) for chunk in chunks]


def expected_confirm_idents() -> list[tuple[str, int, int]]:
    return [(video_id, unit_index, 0) for video_id, unit_index in CONFIRM_ORDER]


def format_confirm_chunk(index: int, chunk: dict) -> str:
    return (
        f"{index} {chunk['video_id']} unit {chunk['unit_index']} chunk {chunk['chunk_index']} "
        f"sentences [{chunk['sentence_start']},{chunk['sentence_end']}) "
        f"context_before [{chunk['context_before_start']},{chunk['context_before_end']}) "
        f"context_after [{chunk['context_after_start']},{chunk['context_after_end']}) "
        f"chars target {chunk['char_count']} before {chunk['context_before_chars']} "
        f"after {chunk['context_after_chars']} sum {chunk['source_char_count']} "
        f"rendered {chunk['rendered_chars']}"
    )


def print_confirm_http(chunks: list[dict]) -> None:
    print("confirm_http:", flush=True)
    for index, chunk in enumerate(chunks, 1):
        print(format_confirm_chunk(index, chunk), flush=True)
    print(f"http_targets: {len(chunks)}", flush=True)
    print("reuse: none", flush=True)


def baseline_hash_problems(inputs: list[dict]) -> list[str]:
    manifest_path = baseline_manifest_path()
    if not manifest_path.is_file():
        return [f"기준 manifest가 없습니다: {manifest_path}"]
    baseline = read_json(manifest_path)
    base_inputs = {item["video_id"]: item for item in baseline.get("inputs", []) if isinstance(item, dict)}
    problems = []
    present = {item.get("video_id") for item in inputs}
    for video_id in dict.fromkeys(video_id for video_id, _unit_index in CONFIRM_ORDER):
        if video_id not in present:
            problems.append(f"{video_id}: 입력 hash를 비교할 대상이 없습니다.")
    for item in inputs:
        video_id = item.get("video_id")
        if video_id not in {entry[0] for entry in CONFIRM_ORDER}:
            continue
        base = base_inputs.get(video_id)
        if not isinstance(base, dict):
            problems.append(f"{video_id}: 기준 입력 hash가 없습니다.")
            continue
        for field in ("t1_sha256", "alignment_sha256", "metadata_sha256"):
            if item.get(field) != base.get(field):
                problems.append(f"{video_id}: {field}가 기준과 다릅니다.")
    return problems


def prompt_label_problems(prompt: str) -> list[str]:
    problems = []
    for label in REVIEW_LABELS:
        if label in prompt:
            problems.append(f"프롬프트에 실험 구분이 있습니다: {label}")
    for instruction in (
        "앞뒤 문맥은 대상의 지시어와 작업 흐름을 이해하기 위한 보조 자료다.",
        "판정 대상은 대상 청크뿐이다.",
        "앞뒤 문맥에 시각 자료나 시연이 있다는 사실만으로 대상을 선정하지 않는다.",
        "evidence_sentence_indices는 대상 청크의 문장 index만 반환한다.",
    ):
        if instruction not in prompt:
            problems.append(f"문맥 지시가 없습니다: {instruction}")
    return problems


def official_confirm_problems(chunks: list[dict]) -> list[str]:
    problems: list[str] = []
    manifest_path = baseline_manifest_path()
    if not manifest_path.is_file():
        return [f"기준 manifest가 없습니다: {manifest_path}"]
    try:
        baseline = read_json(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"기준 manifest를 읽지 못했습니다: {exc}"]
    if baseline.get("prompt") != prompts.P3_CTX2 or baseline.get("prompt") != ACTIVE_PROMPT:
        problems.append("기준 run 프롬프트 전문이 P3_CTX2와 다릅니다.")
    if sha256_file(manifest_path) != BASELINE_MANIFEST_SHA256:
        problems.append("기준 manifest SHA-256이 P3_CTX2 run과 다릅니다.")
    base_settings = baseline.get("settings")
    if (
        not isinstance(base_settings, dict)
        or base_settings.get("prompt_id") != "P3_CTX2"
        or base_settings.get("context_sentences") != CONTEXT_SENTENCES
        or base_settings.get("enable_thinking") is not False
    ):
        problems.append("기준 run 설정이 P3_CTX2 thinking false가 아닙니다.")
    current = settings_payload()
    if isinstance(base_settings, dict):
        for key, value in base_settings.items():
            if key in {"prompt_id", "enable_thinking"}:
                continue
            if current.get(key) != value:
                problems.append(f"설정 {key}가 기준 P3_CTX2와 다릅니다.")
    if (
        current.get("prompt_id") != PROMPT_ID
        or current.get("enable_thinking") is not True
        or current.get("context_sentences") != CONTEXT_SENTENCES
    ):
        problems.append("P3_CTX2_THINK 설정이 thinking true와 문맥 2가 아닙니다.")
    selected, error = select_confirm(chunks, CONFIRM_ORDER)
    if error:
        return problems + [error]
    if confirm_idents(selected) != expected_confirm_idents():
        problems.append(f"확인 대상이 지정 8개와 다릅니다: {confirm_idents(selected)}")
    documents: dict[str, dict] = {}
    base_dir = RUNS_ROOT / BASELINE_RUN_ID
    for video_id in dict.fromkeys(video_id for video_id, _unit in CONFIRM_ORDER):
        path = base_dir / video_id / "visual_candidates.json"
        if not path.is_file():
            problems.append(f"기준 결과가 없습니다: {video_id}")
            continue
        try:
            documents[video_id] = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            problems.append(f"기준 결과를 읽지 못했습니다: {video_id} {exc}")
    if problems:
        return problems
    base_inputs = {item["video_id"]: item for item in baseline.get("inputs", []) if isinstance(item, dict)}
    for chunk in selected:
        video_id = chunk["video_id"]
        unit_index = chunk["unit_index"]
        prefix = f"{video_id} 단위 {unit_index}"
        document = documents[video_id]
        units = [unit for unit in document["units"] if unit["unit_index"] == unit_index]
        if len(units) != 1:
            problems.append(f"{prefix}: 기준 단위가 {len(units)}개입니다.")
            continue
        unit = units[0]
        matches = [item for item in unit["chunks"] if item["chunk_index"] == 0]
        if len(unit["chunks"]) != 1 or len(matches) != 1:
            problems.append(f"{prefix}: 기준 청크가 1개가 아닙니다.")
            continue
        base_chunk = matches[0]
        if base_chunk.get("status") != "ok" or base_chunk.get("decision") not in DECISIONS:
            problems.append(f"{prefix}: 기준 P3_CTX2 성공 결과가 없습니다.")
        for field in ("sentence_start", "sentence_end", "audio_start", "audio_end", "video_start", "video_end", "char_count"):
            if chunk.get(field) != base_chunk.get(field):
                problems.append(f"{prefix}: {field}가 기준과 다릅니다.")
        if chunk.get("sentence_start") != unit.get("sentence_start") or chunk.get("sentence_end") != unit.get("sentence_end"):
            problems.append(f"{prefix}: 단위 문장 범위가 기준과 다릅니다.")
        if chunk.get("audio_start") != unit.get("audio_start") or chunk.get("audio_end") != unit.get("audio_end"):
            problems.append(f"{prefix}: 단위 오디오 시각이 기준과 다릅니다.")
        rows = document_sentences(document)
        sentence_count = rows[-1]["index"] + 1 if rows else 0
        if [item["index"] for item in rows] != list(range(sentence_count)):
            problems.append(f"{video_id}: 기준 문장 index가 연속이 아닙니다.")
            continue
        context, context_error = chunk_context(rows, chunk["sentence_start"], chunk["sentence_end"])
        if context_error or context is None:
            problems.append(f"{prefix}: {context_error}")
            continue
        for field in (
            "context_before_start",
            "context_before_end",
            "context_after_start",
            "context_after_end",
            "context_before_chars",
            "context_after_chars",
            "source_char_count",
        ):
            if chunk.get(field) != context[field]:
                problems.append(f"{prefix}: {field}가 원문 문맥과 다릅니다.")
        target_ids = set(range(chunk["sentence_start"], chunk["sentence_end"]))
        context_ids = list(range(context["context_before_start"], context["context_before_end"]))
        context_ids.extend(range(context["context_after_start"], context["context_after_end"]))
        if any(index in target_ids for index in context_ids) or len(context_ids) != len(set(context_ids)):
            problems.append(f"{prefix}: 문맥과 대상이 겹칩니다.")
        if context["source_char_count"] > CHAR_BUDGET:
            problems.append(f"{prefix}: 원문+문맥 합계 {context['source_char_count']}자가 6000자를 넘습니다.")
        if chunk.get("audio_start") != context["target"][0]["start_time"] or chunk.get("audio_end") != context["target"][-1]["end_time"]:
            problems.append(f"{prefix}: 후보 오디오 시각이 대상 문장과 다릅니다.")
        if "sentences" in chunk and [item["text"] for item in chunk["sentences"]] != [item["text"] for item in context["target"]]:
            problems.append(f"{prefix}: 대상 원문이 기준과 다릅니다.")
        base_input = base_inputs.get(video_id)
        if not isinstance(base_input, dict):
            problems.append(f"{video_id}: 기준 입력 hash가 없습니다.")
    return problems


def confirm_prompt_problems(chunks: list[dict], documents: list[dict]) -> list[str]:
    problems: list[str] = []
    documents_by_id = {document["video_id"]: document for document in documents}
    baseline_reasons: dict[tuple[str, int], str] = {}
    for video_id in dict.fromkeys(chunk["video_id"] for chunk in chunks):
        path = RUNS_ROOT / BASELINE_RUN_ID / video_id / "visual_candidates.json"
        if not path.is_file():
            continue
        document = read_json(path)
        for unit in document["units"]:
            for base_chunk in unit["chunks"]:
                if isinstance(base_chunk.get("reason"), str):
                    baseline_reasons[(video_id, unit["unit_index"])] = base_chunk["reason"]
    for chunk in chunks:
        document = documents_by_id.get(chunk["video_id"])
        if document is None:
            problems.append(f"{chunk['video_id']}: 계획 문서가 없습니다.")
            continue
        prompt, _allowed, _group, error = prompt_for_plan_chunk(chunk, document)
        if error:
            problems.append(f"{chunk['video_id']} 단위 {chunk['unit_index']}: {error}")
            continue
        problems.extend(
            f"{chunk['video_id']} 단위 {chunk['unit_index']}: {problem}" for problem in prompt_label_problems(prompt)
        )
        reason = baseline_reasons.get((chunk["video_id"], chunk["unit_index"]))
        if reason and reason in prompt:
            problems.append(f"{chunk['video_id']} 단위 {chunk['unit_index']}: 기준 판정 근거가 요청에 있습니다.")
    return problems


def thinking_request_delta(base_body: object, current_body: object) -> list[str]:
    if not isinstance(base_body, dict) or not isinstance(current_body, dict):
        return ["요청 본문이 객체가 아닙니다."]
    problems: list[str] = []
    if list(base_body) != list(current_body):
        problems.append(f"요청 키가 다릅니다: {list(base_body)} / {list(current_body)}")
    for key, value in base_body.items():
        if key == "chat_template_kwargs":
            continue
        if current_body.get(key) != value:
            problems.append(f"요청 {key}가 기준과 다릅니다.")
    if set(current_body) - set(base_body):
        problems.append(f"요청에 기준에 없는 키가 있습니다: {sorted(set(current_body) - set(base_body))}")
    base_kwargs = base_body.get("chat_template_kwargs")
    current_kwargs = current_body.get("chat_template_kwargs")
    if base_kwargs != {"enable_thinking": False} or current_kwargs != {"enable_thinking": True}:
        problems.append(f"thinking 차이가 false와 true가 아닙니다: {base_kwargs} / {current_kwargs}")
    return problems


def baseline_call_records() -> tuple[dict[tuple[str, int, int], dict], list[str]]:
    calls_dir = RUNS_ROOT / BASELINE_RUN_ID / "calls"
    if not calls_dir.is_dir():
        return {}, ["기준 호출 기록이 없습니다."]
    found: dict[tuple[str, int, int], dict] = {}
    problems: list[str] = []
    for path in sorted(calls_dir.glob("*.json")):
        try:
            record = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            problems.append(f"기준 호출을 읽지 못했습니다: {path.name} {exc}")
            continue
        if not isinstance(record, dict):
            problems.append(f"기준 호출이 객체가 아닙니다: {path.name}")
            continue
        video_id = record.get("video_id")
        unit_index = record.get("unit_index")
        chunk_index = record.get("chunk_index")
        if not isinstance(video_id, str) or not is_int(unit_index) or not is_int(chunk_index):
            problems.append(f"기준 호출 식별자가 없습니다: {path.name}")
            continue
        ident = (video_id, unit_index, chunk_index)
        if ident in found:
            problems.append(f"{video_id} 단위 {unit_index}: 기준 호출이 둘 이상입니다.")
        found[ident] = record
    return found, problems


def thinking_request_problems(chunks: list[dict], documents: list[dict]) -> list[str]:
    if confirm_idents(chunks) != expected_confirm_idents():
        return [f"요청 비교 대상이 지정 8개가 아닙니다: {confirm_idents(chunks)}"]
    problems: list[str] = []
    manifest_path = baseline_manifest_path()
    if not manifest_path.is_file() or sha256_file(manifest_path) != BASELINE_MANIFEST_SHA256:
        problems.append("기준 manifest SHA-256이 P3_CTX2 run과 다릅니다.")
    records, record_problems = baseline_call_records()
    problems.extend(record_problems)
    expected = set(expected_confirm_idents())
    extra = sorted(set(records) - expected)
    if extra:
        problems.append(f"기준 호출에 지정 8개 밖 기록이 있습니다: {extra}")
    documents_by_id = {document["video_id"]: document for document in documents}
    for chunk in chunks:
        ident = (chunk["video_id"], chunk["unit_index"], chunk["chunk_index"])
        prefix = f"{chunk['video_id']} 단위 {chunk['unit_index']}"
        record = records.get(ident)
        if not isinstance(record, dict):
            problems.append(f"{prefix}: 기준 호출이 없습니다.")
            continue
        document = documents_by_id.get(chunk["video_id"])
        if document is None:
            problems.append(f"{prefix}: 요청을 만들 문서가 없습니다.")
            continue
        prompt, _allowed, _group, error = prompt_for_plan_chunk(chunk, document)
        if error:
            problems.append(f"{prefix}: {error}")
            continue
        for item in thinking_request_delta(record.get("request_body"), request_body(prompt)):
            problems.append(f"{prefix}: {item}")
    return problems


def confirm_order_ok(documents: list[dict], confirm_order: list[dict]) -> bool:
    states = {document["video_id"]: chunk_map(document) for document in documents}
    if not confirm_order:
        return False
    for item in confirm_order:
        state = states.get(item["video_id"], {}).get((item["unit_index"], item["chunk_index"]))
        if state is None or state["status"] != "ok":
            return False
    return True


def expected_b_idents() -> list[tuple[str, int, int]]:
    idents: list[tuple[str, int, int]] = []
    for rows in B_REUSE_RUNS.values():
        idents.extend(rows)
    return idents


def load_b_records() -> tuple[dict[tuple[str, int, int], dict], list[str]]:
    found: dict[tuple[str, int, int], dict] = {}
    problems: list[str] = []
    wanted = set(expected_b_idents())
    for run_id, rows in B_REUSE_RUNS.items():
        calls_dir = RUNS_ROOT / run_id / "calls"
        if not calls_dir.is_dir():
            problems.append(f"B 재사용 run이 없습니다: {run_id}")
            continue
        matched: dict[tuple[str, int, int], list[dict]] = {ident: [] for ident in rows}
        for path in sorted(calls_dir.glob("*.json")):
            try:
                record = read_json(path)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                problems.append(f"B 호출을 읽지 못했습니다: {path.name} {exc}")
                continue
            if not isinstance(record, dict) or record.get("condition") != "B":
                continue
            video_id = record.get("video_id")
            unit_index = record.get("unit_index")
            chunk_index = record.get("chunk_index")
            if not isinstance(video_id, str) or not is_int(unit_index) or not is_int(chunk_index):
                continue
            ident = (video_id, unit_index, chunk_index)
            if ident not in matched:
                if ident in wanted:
                    problems.append(f"{ident}: 지정 run이 아닌 곳에 B 호출이 있습니다.")
                continue
            record["_path"] = path
            record["_run_id"] = run_id
            matched[ident].append(record)
        for ident, items in matched.items():
            if len(items) != 1:
                problems.append(f"{run_id} {ident[0]} 단위 {ident[1]}: B 호출이 {len(items)}개입니다.")
                continue
            if ident in found:
                problems.append(f"{ident[0]} 단위 {ident[1]}: B 호출이 두 run에 있습니다.")
                continue
            found[ident] = items[0]
    if not problems and set(found) != wanted:
        problems.append(f"B 재사용이 8개가 아닙니다: {sorted(found)}")
    return found, problems


def request_body_difference(stored: object, built: dict) -> list[str]:
    if not isinstance(stored, dict):
        return ["저장된 요청 본문이 객체가 아닙니다."]
    problems: list[str] = []
    if list(stored) != list(built):
        problems.append(f"요청 키가 다릅니다: {list(stored)} / {list(built)}")
    for key, value in stored.items():
        if built.get(key) != value:
            if key == "messages":
                problems.append("messages가 저장된 B 요청과 다릅니다.")
            else:
                problems.append(f"{key}가 다릅니다.")
    extra = set(built) - set(stored)
    if extra:
        problems.append(f"새 요청에만 있는 키: {sorted(extra)}")
    return problems


def b_reuse_problems(videos: list[dict], documents: list[dict], chunks: list[dict]) -> list[str]:
    problems: list[str] = []
    if len(chunks) != 249:
        problems.append(f"청크가 249개가 아닙니다: {len(chunks)}")
    records, record_problems = load_b_records()
    problems.extend(record_problems)
    problems.extend(baseline_hash_problems(videos))
    documents_by_id = {document["video_id"]: document for document in documents}
    planned = {
        (chunk["video_id"], chunk["unit_index"], chunk["chunk_index"]): chunk for chunk in chunks
    }
    for ident in expected_b_idents():
        video_id, unit_index, chunk_index = ident
        prefix = f"{video_id} 단위 {unit_index}"
        chunk = planned.get(ident)
        record = records.get(ident)
        if chunk is None:
            problems.append(f"{prefix}: 계획 청크가 없습니다.")
            continue
        if chunk_index != 0 or chunk.get("chunk_index") != 0:
            problems.append(f"{prefix}: chunk 0이 아닙니다.")
        if record is None:
            continue
        document = documents_by_id.get(video_id)
        if document is None:
            problems.append(f"{prefix}: 계획 문서가 없습니다.")
            continue
        prompt, allowed, _group, error = prompt_for_plan_chunk(chunk, document)
        if error:
            problems.append(f"{prefix}: {error}")
            continue
        for item in request_body_difference(record.get("request_body"), request_body(prompt)):
            problems.append(f"{prefix}: {item}")
        if allowed != set(range(chunk["sentence_start"], chunk["sentence_end"])):
            problems.append(f"{prefix}: 대상 문장 구간이 요청과 다릅니다.")
        if record.get("http_status") != 200 or record.get("finish_reason") != "stop" or record.get("error") is not None:
            problems.append(f"{prefix}: B 응답이 성공 종료가 아닙니다.")
            continue
        response_body = record.get("response_body")
        if not isinstance(response_body, str) or sha256_text(response_body) != record.get("response_sha256"):
            problems.append(f"{prefix}: 응답 hash가 저장값과 다릅니다.")
            continue
        content = record.get("content")
        if not isinstance(content, str) or content == "":
            problems.append(f"{prefix}: content가 없습니다.")
            continue
        try:
            parsed = parse_decision(content, set(allowed))
        except SchemaError as exc:
            problems.append(f"{prefix}: {exc}")
            continue
        if parsed != record.get("parsed"):
            problems.append(f"{prefix}: 저장된 판정이 content 재검증과 다릅니다.")
    return problems


def controller_ready_problem() -> str | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8010/control/status", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        return f"vLLM 상태를 읽지 못했습니다: {exc}"
    if not isinstance(payload, dict) or payload.get("state") != "ready" or payload.get("residency") != "resident":
        return f"vLLM이 추론 가능 상태가 아닙니다: {payload}"
    return None


def cmd_plan(repo: Path, input_root: Path, data_root: Path, runs_root: Path, enforce_expected: bool) -> int:
    started = time.perf_counter()
    videos, problems = load_campaign(repo, input_root, data_root, enforce_expected)
    if problems:
        for problem in problems:
            print(problem, flush=True)
        return 1
    flat: list[dict] = []
    prepared = []
    for video in videos:
        units, chunks, plan_problems = plan_units(video)
        if plan_problems:
            for problem in plan_problems:
                print(problem, flush=True)
            return 1
        document = build_document(video, units)
        prepared.append((video, document, chunks))
        flat.extend(chunks)
    pilot = select_pilot(flat)
    if enforce_expected:
        prepared_documents = [document for _video, document, _chunks in prepared]
        preflight = b_reuse_problems(videos, prepared_documents, flat)
        if preflight:
            for problem in preflight:
                print(problem, flush=True)
            return 1
    run_id = new_run_id()
    temporary = runs_root / f".{run_id}.partial"
    if temporary.exists():
        print(f"임시 run 디렉터리가 이미 있습니다: {temporary}", flush=True)
        return 1
    temporary.mkdir(parents=True)
    try:
        code = code_version(repo)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": utc_now(),
            "input_root": display_path(repo, input_root),
            "effective_config": {
                "path": videos[0]["effective_config_path"],
                "sha256": videos[0]["effective_config_sha256"],
            },
            "input_ids": [video["video_id"] for video in videos],
            "inputs": [
                {
                    "video_id": video["video_id"],
                    "t1_path": video["t1_path"],
                    "t1_sha256": video["t1_sha256"],
                    "alignment_path": video["alignment_path"],
                    "alignment_sha256": video["alignment_sha256"],
                    "metadata_path": video["metadata_path"],
                    "metadata_sha256": video["metadata_sha256"],
                    "media_path": video["media_path"],
                    "result_id": video["result_id"],
                    "media_sha256": video["media_sha256"],
                    "sentence_count": video["sentence_count"],
                    "unit_count": len(document["units"]),
                    "chunk_count": sum(len(unit["chunks"]) for unit in document["units"]),
                    "duration_seconds": video["duration_seconds"],
                    "duration_known": video["duration_known"],
                    "audio_to_video_offset_seconds": video["offset"],
                }
                for video, document, _chunks in prepared
            ],
            "settings": settings_payload(),
            "prompt": ACTIVE_PROMPT,
            "code": code,
            "model": model_payload(),
            "logic_version": LOGIC_VERSION,
            "expected_call_count": len(flat),
            "pilot_order": [public_plan_chunk(chunk) for chunk in pilot],
            "plan": [public_plan_chunk(chunk) for chunk in flat],
        }
        if enforce_expected:
            manifest["change"] = {
                "message_baseline_run_id": BASELINE_RUN_ID,
                "message_baseline_manifest_sha256": sha256_file(baseline_manifest_path()),
                "from_prompt_id": "P3_CTX2",
                "to_prompt_id": PROMPT_ID,
                "enable_thinking": True,
                "max_tokens": MAX_TOKENS,
                "reused_b_runs": list(B_REUSE_RUNS),
                "p2_run_id": P2_RUN_ID,
                "reason": CHANGE_REASON,
            }
            manifest["reuse_policy"] = "b8"
        run = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "status": "planned",
            "stage": "plan",
            "started_at": utc_now(),
            "ended_at": None,
            "pilot_targets": [
                {
                    "video_id": chunk["video_id"],
                    "unit_index": chunk["unit_index"],
                    "chunk_index": chunk["chunk_index"],
                }
                for chunk in pilot
            ],
            "counts": {
                "new_calls": 0,
                "reused": 0,
                "failed": 0,
                "pending": len(flat),
                "http_attempts": 0,
            },
            "wall_clock_seconds": None,
            "call_elapsed_seconds": 0,
            "stage_error": None,
        }
        dump_json(temporary / "manifest.json", manifest)
        dump_json(temporary / "run.json", run)
        documents = [document for _video, document, _chunks in prepared]
        write_views(repo, temporary, documents)
        final = runs_root / run_id
        os.rename(temporary, final)
    except Exception:
        if temporary.exists():
            for path in sorted(temporary.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            if temporary.exists():
                temporary.rmdir()
        raise
    run["ended_at"] = utc_now()
    run["plan_wall_clock_seconds"] = time.perf_counter() - started
    dump_json(final / "run.json", run)
    print(f"run_id: {run_id}", flush=True)
    print(f"run_dir: {final.resolve()}", flush=True)
    print(f"expected_calls: {len(flat)}", flush=True)
    print(f"pilot_chunks: {len(pilot)}", flush=True)
    if enforce_expected:
        print("reused_b: 8", flush=True)
        print(f"http_targets: {len(flat) - len(expected_b_idents())}", flush=True)
    if pilot:
        longest = pilot[0]
        print(
            "longest: "
            f"{longest['video_id']} unit {longest['unit_index']} chunk {longest['chunk_index']} "
            f"rendered_chars {longest['rendered_chars']} char_count {longest['char_count']}",
            flush=True,
        )
    warning_count = 0
    for video, document, chunks in prepared:
        print(
            f"{document['video_id']} sentences {video['sentence_count']} "
            f"units {len(document['units'])} chunks {sum(len(unit['chunks']) for unit in document['units'])}",
            flush=True,
        )
        for chunk in chunks:
            for warning in chunk["warnings"]:
                warning_count += 1
                print(
                    "warning: "
                    f"{chunk['video_id']} unit {warning['unit_index']} chunk {warning['chunk_index']} "
                    f"excess {time_literal(warning['excess_seconds'])}",
                    flush=True,
                )
    print(f"warnings: {warning_count}", flush=True)
    return 0


def resolve_run_dir(runs_root: Path, run_id: str | None) -> Path | None:
    if not run_id or run_id.startswith(".") or "/" in run_id or "\\" in run_id:
        print("run_id가 올바르지 않습니다.", flush=True)
        return None
    run_dir = runs_root / run_id
    if not run_dir.is_dir():
        print(f"run_id가 없습니다: {run_id}", flush=True)
        return None
    return run_dir


def begin_stage(run: dict, stage: str) -> float:
    run["stage"] = stage
    if run.get("inference_started_at") is None and stage in {"pilot", "resume", "confirm"}:
        run["inference_started_at"] = utc_now()
    return time.perf_counter()


def finish_stage(run: dict, started: float, status: str, error: str | None = None) -> None:
    elapsed = time.perf_counter() - started
    previous = run.get("wall_clock_seconds")
    if not isinstance(previous, (int, float)) or isinstance(previous, bool):
        previous = 0
    if run["stage"] == "plan":
        run["plan_wall_clock_seconds"] = elapsed
    elif run["stage"] in {"pilot", "resume", "confirm"}:
        run["wall_clock_seconds"] = previous + elapsed
    run["status"] = status
    run["ended_at"] = utc_now()
    run["stage_error"] = error


def write_p2_comparison(repo: Path, run_dir: Path, documents: list[dict], run: dict) -> None:
    p2_dir = RUNS_ROOT / P2_RUN_ID
    lines = [
        "# P3_CTX2_B4096와 P2 대조",
        "",
        f"- 새 run: `{display_path(repo, run_dir)}/`",
        f"- P2 run: `tests/T2/runs/{P2_RUN_ID}/`",
        f"- 상태: {run.get('status')}. 실패 청크는 unresolved이며 미선정으로 세지 않는다.",
        f"- 재사용 {run.get('counts', {}).get('reused', 0)}, 신규 성공 {run.get('counts', {}).get('new_calls', 0)}, "
        f"실패 {run.get('counts', {}).get('failed', 0)}, pending {run.get('counts', {}).get('pending', 0)}, "
        f"HTTP {run.get('counts', {}).get('http_attempts', 0)}.",
        "- 바뀐 단위 수와 선정 수는 품질 점수가 아니다. T2 채택이 아니다.",
        "",
    ]
    changed: list[str] = []
    unfinished: list[str] = []
    uncertain: list[str] = []
    failed: list[str] = []
    warnings: list[str] = []
    for document in documents:
        video_id = document["video_id"]
        p2_path = p2_dir / video_id / "visual_candidates.json"
        p2_units: dict[int, dict] = {}
        if p2_path.is_file():
            p2_document = read_json(p2_path)
            p2_units = {unit["unit_index"]: unit for unit in p2_document.get("units", [])}
        else:
            changed.append(f"- {video_id}: P2 결과가 없습니다.")
        for warning in document.get("warnings", []):
            warnings.append(
                f"- {video_id} 단위 {warning.get('unit_index')} 청크 {warning.get('chunk_index')}: "
                f"{warning.get('code')} 초과 {time_literal(warning.get('excess_seconds'))}초"
            )
        for unit in document["units"]:
            prefix = f"{video_id} / {unit['unit_index']}"
            p2_unit = p2_units.get(unit["unit_index"])
            p2_chunks = {item["chunk_index"]: item for item in (p2_unit or {}).get("chunks", [])}
            for chunk in unit["chunks"]:
                previous = p2_chunks.get(chunk["chunk_index"], {})
                if chunk["status"] != "pending" and (
                    chunk.get("decision") == "uncertain" or previous.get("decision") == "uncertain"
                ):
                    uncertain.append(
                        f"- {prefix} 청크 {chunk['chunk_index']}: P2 {previous.get('decision')}, 새 판정 {chunk.get('decision')}"
                    )
                if chunk["status"] == "failed":
                    failed.append(f"- {prefix} 청크 {chunk['chunk_index']}: {chunk.get('problems')}")
            if p2_unit is None:
                continue
            if unit["status"] != "ok" or p2_unit.get("status") != "ok":
                unfinished.append(
                    f"- {prefix}: P2 {p2_unit.get('selection')}/{p2_unit.get('status')}, "
                    f"새 결과 {unit['selection']}/{unit['status']}"
                )
                continue
            if unit["selection"] == p2_unit.get("selection"):
                continue
            lines_for_unit = [
                f"### {prefix}",
                "",
                f"- P2 선택: {p2_unit.get('selection')}",
                f"- 새 선택: {unit['selection']}",
                "",
            ]
            p2_chunks = {item["chunk_index"]: item for item in p2_unit.get("chunks", [])}
            for chunk in unit["chunks"]:
                previous = p2_chunks.get(chunk["chunk_index"], {})
                lines_for_unit.extend(
                    [
                        f"- 청크 {chunk['chunk_index']} 문장 [{chunk['sentence_start']},{chunk['sentence_end']}) "
                        f"오디오 {time_literal(chunk['audio_start'])}-{time_literal(chunk['audio_end'])} "
                        f"영상 {time_literal(chunk['video_start'])}-{time_literal(chunk['video_end'])}",
                        f"  - P2: {previous.get('decision')} / {previous.get('reason')} / {previous.get('evidence_sentence_indices')}",
                        f"  - 새 결과: {chunk.get('decision')} / {chunk.get('reason')} / {chunk.get('evidence_sentence_indices')}",
                    ]
                )
            changed.append("\n".join(lines_for_unit))
    lines.extend(["## 선택이 달라진 단위", ""])
    lines.extend(changed or ["- 없음"])
    lines.extend(["", "## 미완료", ""])
    lines.extend(unfinished or ["- 없음"])
    lines.extend(["", "## uncertain", ""])
    lines.extend(uncertain or ["- 없음"])
    lines.extend(["", "## 실패", ""])
    lines.extend(failed or ["- 없음"])
    lines.extend(["", "## 경고", ""])
    lines.extend(warnings or ["- 없음"])
    prompt_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0
    elapsed = 0.0
    call_count = 0
    calls_dir = run_dir / "calls"
    if calls_dir.is_dir():
        for path in sorted(calls_dir.glob("*.json")):
            record = read_json(path)
            call_count += 1
            usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
            details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
            if is_int(usage.get("prompt_tokens")):
                prompt_tokens += usage["prompt_tokens"]
            if is_int(usage.get("completion_tokens")):
                completion_tokens += usage["completion_tokens"]
            if is_int(details.get("reasoning_tokens")):
                reasoning_tokens += details["reasoning_tokens"]
            if isinstance(record.get("elapsed_seconds"), (int, float)) and not isinstance(record.get("elapsed_seconds"), bool):
                elapsed += record["elapsed_seconds"]
    lines.extend(
        [
            "",
            "## 호출량",
            "",
            f"- 신규 호출 파일 {call_count}개. 경과 합 {elapsed:.3f}초.",
            f"- prompt_tokens {prompt_tokens}, completion_tokens {completion_tokens}, reasoning_tokens {reasoning_tokens}.",
            f"- 재사용 {run.get('counts', {}).get('reused', 0)}건. 출처는 각 청크의 reused_from이다.",
            "",
        ]
    )
    (run_dir / "p2_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_failure_notes(repo, run_dir, documents)


def failure_cause(call: dict | None, problems: object) -> str:
    if not isinstance(call, dict):
        return f"호출 기록이 없습니다. 청크 오류: {problems}"
    usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    finish = call.get("finish_reason")
    completion = usage.get("completion_tokens")
    reasoning = details.get("reasoning_tokens")
    content = call.get("content")
    if finish == "length" or (is_int(completion) and completion >= MAX_TOKENS and finish != "stop"):
        if is_int(reasoning) and reasoning == completion:
            return (
                f"HTTP {call.get('http_status')}, finish_reason={finish}. "
                f"completion_tokens {completion}이 모두 reasoning_tokens이고 content는 비어 있습니다. "
                f"max_tokens {MAX_TOKENS} 안에서 판정 JSON이 나오지 않아 예산 제약으로 비교 불완전입니다."
            )
        return (
            f"HTTP {call.get('http_status')}, finish_reason={finish}, "
            f"completion_tokens {completion}, reasoning_tokens {reasoning}. "
            "출력이 잘려 판정을 확정하지 못했습니다."
        )
    if call.get("error"):
        return f"HTTP {call.get('http_status')}, finish_reason={finish}. {call.get('error')}"
    return f"HTTP {call.get('http_status')}, finish_reason={finish}, content={content!r}, 청크 오류: {problems}"


def write_failure_notes(repo: Path, run_dir: Path, documents: list[dict]) -> None:
    lines = [
        "# 실패 청크",
        "",
        "실패 청크는 다시 호출하지 않았다. 판정은 만들지 않았고 선택은 unresolved이다.",
        "",
    ]
    found = False
    for document in documents:
        for unit in document["units"]:
            for chunk in unit["chunks"]:
                if chunk["status"] != "failed":
                    continue
                found = True
                call = None
                ref = chunk.get("call_ref")
                if isinstance(ref, str):
                    path = repo / ref
                    if path.is_file():
                        call = read_json(path)
                lines.extend(
                    [
                        f"## {document['video_id']} 단위 {unit['unit_index']} 청크 {chunk['chunk_index']}",
                        "",
                        f"- 단위 선택: {unit.get('selection')}",
                        f"- 문장 구간: [{chunk['sentence_start']},{chunk['sentence_end']})",
                        f"- 오디오: {time_literal(chunk['audio_start'])}-{time_literal(chunk['audio_end'])}",
                        f"- 영상: {time_literal(chunk['video_start'])}-{time_literal(chunk['video_end'])}",
                        f"- 오류: {chunk.get('problems')}",
                        f"- call_ref: {ref}",
                        f"- 원인: {failure_cause(call, chunk.get('problems'))}",
                        "",
                    ]
                )
    if not found:
        lines.extend(["- 없음", ""])
    (run_dir / "failures.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_execute(
    repo: Path,
    input_root: Path,
    data_root: Path,
    runs_root: Path,
    run_id: str,
    mode: str,
    client: Any,
    enforce_expected: bool,
    continue_on_failure: bool = False,
) -> int:
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return 1
    try:
        with run_lock(run_dir):
            manifest, run, documents = load_run_bundle(run_dir)
            if different_condition(manifest):
                print("조건이 다른 기존 run은 수정하지 않습니다.", flush=True)
                return 1
            mismatch = verify_manifest_inputs(repo, data_root, input_root, manifest, enforce_expected)
            if mismatch:
                print(mismatch, flush=True)
                run["status"] = "failed"
                run["stage"] = mode
                run["stage_error"] = mismatch
                run["ended_at"] = utc_now()
                save_run(run_dir, run)
                return 1
            if mode == "resume" and manifest.get("confirm_order"):
                print("confirm_order가 있는 run은 나머지 pending을 실행하지 않습니다.", flush=True)
                return 1
            if mode == "confirm":
                stored = manifest.get("confirm_order")
                if not isinstance(stored, list) or not stored:
                    print("confirm_order가 없습니다.", flush=True)
                    return 1
                targets = stored
                if enforce_expected:
                    preflight = official_confirm_problems(targets)
                    preflight.extend(baseline_hash_problems(manifest.get("inputs", [])))
                    preflight.extend(confirm_prompt_problems(targets, documents))
                    preflight.extend(thinking_request_problems(targets, documents))
                    if confirm_idents(targets) != expected_confirm_idents():
                        preflight.append(f"HTTP 발행 목록이 지정 8개와 다릅니다: {confirm_idents(targets)}")
                    if preflight:
                        for problem in preflight:
                            print(problem, flush=True)
                        return 1
                    print_confirm_http(targets)
            elif mode == "pilot":
                targets = manifest["pilot_order"]
            else:
                targets = [
                    chunk
                    for chunk in manifest["plan"]
                    if chunk_map(
                        next(document for document in documents if document["video_id"] == chunk["video_id"])
                    )[(chunk["unit_index"], chunk["chunk_index"])]["status"]
                    == "pending"
                ]
            explicit_reuse = None
            full_b = enforce_expected and mode == "resume" and not manifest.get("confirm_order")
            if full_b:
                videos, load_problems = load_campaign(repo, input_root, data_root, True)
                if load_problems:
                    for problem in load_problems:
                        print(problem, flush=True)
                    return 1
                gate = b_reuse_problems(videos, documents, manifest["plan"])
                if gate:
                    for problem in gate:
                        print(problem, flush=True)
                    return 1
                states = {document["video_id"]: chunk_map(document) for document in documents}
                b_idents = set(expected_b_idents())
                needs_http = False
                for target in targets:
                    ident = (target["video_id"], target["unit_index"], target["chunk_index"])
                    state = states[target["video_id"]][(target["unit_index"], target["chunk_index"])]
                    if state["status"] == "pending" and ident not in b_idents:
                        needs_http = True
                        break
                if needs_http:
                    ready = controller_ready_problem()
                    if ready:
                        print(ready, flush=True)
                        return 1
                records, record_problems = load_b_records()
                if record_problems:
                    for problem in record_problems:
                        print(problem, flush=True)
                    return 1
                explicit_reuse = records
            started = begin_stage(run, mode)
            error = execute_targets(
                repo,
                runs_root,
                run_dir,
                manifest,
                documents,
                targets,
                client,
                run,
                reuse=mode != "confirm" and explicit_reuse is None,
                explicit_reuse=explicit_reuse,
                stop_on_failure=not continue_on_failure,
            )
            if error:
                finish_stage(run, started, "failed", error)
                save_run(run_dir, run)
                write_views(repo, run_dir, documents)
                if full_b:
                    write_p2_comparison(repo, run_dir, documents, run)
                    print(
                        f"pending {run['counts']['pending']} failed {run['counts']['failed']} "
                        f"reused {run['counts']['reused']}",
                        flush=True,
                    )
                return 1
            if mode == "pilot":
                status = "pilot_complete"
            elif mode == "confirm":
                status = "confirm_complete" if confirm_order_ok(documents, manifest["confirm_order"]) else "incomplete"
            elif all_chunks_ok(documents) and all(document["validation"]["ok"] is True for document in documents):
                status = "completed"
            else:
                status = "incomplete"
            stage_error = None
            if status != "completed" and any(
                chunk["status"] == "failed" for document in documents for _unit, chunk in iter_chunks(document)
            ):
                stage_error = "실패 청크가 있습니다. failures.md"
            finish_stage(run, started, status, stage_error)
            refresh_run_counts(run, documents)
            save_run(run_dir, run)
            write_views(repo, run_dir, documents)
            if full_b:
                write_p2_comparison(repo, run_dir, documents, run)
            if mode == "confirm":
                if status != "confirm_complete":
                    print("확인 청크가 모두 성공하지 않았습니다.", flush=True)
                    return 1
                print(f"confirm_complete pending {run['counts']['pending']}", flush=True)
                return 0
            if mode == "resume" and status != "completed":
                print("완료되지 않은 청크가 있습니다.", flush=True)
                return 1
            return 0
    except RunBusy as exc:
        print(str(exc), flush=True)
        return 1


def cmd_render_only(
    repo: Path,
    input_root: Path,
    data_root: Path,
    runs_root: Path,
    run_id: str,
    enforce_expected: bool,
) -> int:
    before = InferClient.created
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return 1
    decision_path = run_dir / "review_decision.md"
    decision_bytes = decision_path.read_bytes() if decision_path.is_file() else None
    try:
        with run_lock(run_dir):
            manifest, run, documents = load_run_bundle(run_dir)
            if different_condition(manifest):
                print("조건이 다른 기존 run은 수정하지 않습니다.", flush=True)
                return 1
            mismatch = verify_manifest_inputs(repo, data_root, input_root, manifest, enforce_expected)
            if mismatch:
                print(mismatch, flush=True)
                return 1
            for document in documents:
                offset = document["media"]["audio_to_video_offset_seconds"]
                duration = document["media"]["duration_seconds"] if document["media"].get("duration_known") else None
                for unit, chunk in iter_chunks(document):
                    assessed, error = assess_video_range(
                        chunk["audio_start"],
                        chunk["audio_end"],
                        offset,
                        duration,
                        None,
                        unit["unit_index"],
                        chunk["chunk_index"],
                    )
                    if error or assessed is None:
                        chunk["problems"] = [f"범위 오류: {error}"]
                        continue
                    chunk["video_start"] = assessed["video_start"]
                    chunk["video_end"] = assessed["video_end"]
                    chunk["warnings"] = assessed["warnings"]
                    chunk["problems"] = [problem for problem in chunk["problems"] if problem.startswith("범위 오류:") is False]
                    if chunk["status"] == "failed":
                        pass
                recompute_document(document)
            started = begin_stage(run, "render")
            finish_stage(run, started, run.get("status") or "planned", run.get("stage_error"))
            run["stage"] = "render"
            refresh_run_counts(run, documents)
            save_run(run_dir, run)
            write_views(repo, run_dir, documents)
    except RunBusy as exc:
        print(str(exc), flush=True)
        return 1
    if decision_bytes is None:
        if decision_path.exists() and decision_path.read_text(encoding="utf-8") != review_header():
            print("render-only가 검토 판단을 만들었습니다.", flush=True)
            return 1
    elif decision_path.read_bytes() != decision_bytes:
        print("render-only가 review_decision.md를 바꿨습니다.", flush=True)
        return 1
    if InferClient.created != before:
        print("render-only에서 HTTP 클라이언트가 생성되었습니다.", flush=True)
        return 1
    print(f"run_dir: {run_dir.resolve()}", flush=True)
    return 0


def official_roots() -> tuple[Path, Path, Path, Path]:
    return ROOT, INPUT_ROOT, DATA_ROOT, RUNS_ROOT


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="T2 시각 분석 후보 선정")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--continue-on-failure", action="store_true")
    return parser.parse_args(argv)


def dispatch(args: argparse.Namespace) -> int:
    modes = [args.check, args.plan, args.pilot, args.resume, args.render_only, args.confirm]
    if sum(bool(mode) for mode in modes) != 1:
        print("실행 모드를 하나만 지정합니다.", flush=True)
        return 2
    if args.check:
        if args.run_id:
            print("--check에는 --run-id를 지정하지 않습니다.", flush=True)
            return 2
        return run_checks()
    if args.plan:
        if args.run_id:
            print("--plan에는 --run-id를 지정하지 않습니다.", flush=True)
            return 2
        repo, input_root, data_root, runs_root = official_roots()
        return cmd_plan(repo, input_root, data_root, runs_root, True)
    if not args.run_id:
        print("--run-id가 필요합니다.", flush=True)
        return 2
    repo, input_root, data_root, runs_root = official_roots()
    if args.render_only:
        return cmd_render_only(repo, input_root, data_root, runs_root, args.run_id, True)
    client = InferClient()
    if args.pilot:
        mode = "pilot"
    elif args.confirm:
        mode = "confirm"
    else:
        mode = "resume"
    return cmd_execute(
        repo,
        input_root,
        data_root,
        runs_root,
        args.run_id,
        mode,
        client,
        True,
        continue_on_failure=args.continue_on_failure,
    )


def expect(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def decision_text(decision: str, index: int) -> str:
    return json.dumps(
        {
            "decision": decision,
            "reason": "화면의 버튼 위치를 확인한다.",
            "evidence_sentence_indices": [index],
        },
        ensure_ascii=False,
    )


def response_body_for(decision: str, index: int, finish_reason: str = "stop") -> str:
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": decision_text(decision, index)},
                }
            ],
            "usage": {"completion_tokens": 12},
        },
        ensure_ascii=False,
    )


class ScriptedClient:
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.posts: list[dict] = []

    def post(self, body: dict) -> dict:
        self.posts.append(body)
        kind = self.script.pop(0)
        prompt = body["messages"][0]["content"]
        index = request_sentence_indices(prompt)[0]
        if kind == "ok":
            return {"http_status": 200, "response_body": response_body_for("needed", index), "elapsed_seconds": 0.2, "error": None}
        if kind == "not_needed":
            return {
                "http_status": 200,
                "response_body": response_body_for("not_needed", index),
                "elapsed_seconds": 0.2,
                "error": None,
            }
        if kind == "http":
            return {"http_status": 500, "response_body": "error", "elapsed_seconds": 0.1, "error": None}
        if kind == "length":
            return {
                "http_status": 200,
                "response_body": response_body_for("needed", index, "length"),
                "elapsed_seconds": 0.1,
                "error": None,
            }
        if kind == "timeout":
            return {
                "http_status": None,
                "response_body": None,
                "elapsed_seconds": 0.1,
                "error": "요청이 시간 초과되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다.",
            }
        raise AssertionError(kind)


def sentence(index: int, text: str, start: float = 0.0, end: float | None = None) -> dict:
    if end is None:
        end = start + 1
    return {
        "index": index,
        "source_chunk_index": index,
        "start_time": start,
        "end_time": end,
        "text": text,
        "language": "Korean",
    }


def write_fixture(root: Path, video_id: str, sentences: list[dict], offset: float = 0.0, duration: float | None = 10000) -> None:
    audio = root / "data" / video_id / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    chunks = []
    for item in sentences:
        chunks.append(
            {
                "index": item["source_chunk_index"],
                "text": item["text"],
                "language": item["language"],
                "items": [
                    {"text": item["text"], "start_time": item["start_time"], "end_time": item["end_time"]},
                ],
            }
        )
    alignment = {
        "time_reference": "audio_start",
        "overlap_deduplicated": True,
        "chunks": chunks,
        "omitted_chunks": [],
        "sentence_policy_id": "policy-test",
    }
    (audio / "alignment_sentences.json").write_text(json.dumps(alignment, ensure_ascii=False), encoding="utf-8")
    video_dir = root / "data" / video_id / "source"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "video.mp4").write_bytes(b"video")
    metadata = {
        "video_id": video_id,
        "source_url": f"https://example.invalid/{video_id}",
        "title": video_id,
        "duration_seconds": duration,
        "audio_to_video_offset_seconds": offset,
        "results": {
            "video": {
                "path": "source/video.mp4",
                "result_id": f"result-{video_id}",
                "sha256": "a" * 64,
            }
        },
    }
    (root / "data" / video_id / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    units = [
        {
            "index": 0,
            "sentence_start": 0,
            "sentence_end": len(sentences),
            "start_time": sentences[0]["start_time"],
            "end_time": sentences[-1]["end_time"],
            "sentences": sentences,
        }
    ]
    t1 = {
        "video_id": video_id,
        "source_url": metadata["source_url"],
        "title": video_id,
        "status": "ok",
        "input": {
            "sentence_policy_id": "policy-test",
            "omitted_chunks": [],
            "time_reference": "audio_start",
            "sentence_count": len(sentences),
        },
        "time": {"audio_to_video_offset_seconds": offset, "unit_time_reference": "audio_start"},
        "units": units,
        "validation": {"ok": True, "problems": []},
    }
    attempt = root / "input" / video_id
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "semantic_units.json").write_text(json.dumps(t1, ensure_ascii=False), encoding="utf-8")


def write_config(root: Path, video_ids: list[str]) -> None:
    payload = {"input_ids": video_ids}
    (root / "input").mkdir(parents=True, exist_ok=True)
    (root / "input" / "effective_config.json").write_text(json.dumps(payload), encoding="utf-8")


def check_prompt() -> None:
    expect("\r" not in ACTIVE_PROMPT and "\r" not in prompts.P3, "프롬프트에 CR이 있습니다.")
    expect(prompts.P3.endswith("{sentences_json}\n"), "P3 전문이 바뀌었습니다.")
    expect(ACTIVE_PROMPT.endswith("{context_after_json}\n"), "P3_CTX2 끝이 문맥 입력과 다릅니다.")
    expect(ACTIVE_PROMPT == prompts.P3_CTX2, "활성 프롬프트가 P3_CTX2가 아닙니다.")
    expect(prompts.P3_CTX2 != prompts.P3, "P3_CTX2 요청 본문이 P3와 같습니다.")
    head = prompts.P3.split("단위 식별:", 1)[0]
    expect(prompts.P3_CTX2.startswith(head), "P3 선정 기준 전문이 유지되지 않았습니다.")
    expect("앞뒤 문맥은 대상의 지시어와 작업 흐름을 이해하기 위한 보조 자료다." not in prompts.P3, "P3에 문맥 지시를 넣었습니다.")
    expect(prompts.P0 != prompts.P3 and prompts.P1 != prompts.P3 and prompts.P2 != prompts.P3, "P3가 이전 프롬프트와 같습니다.")
    for token in (*EXPECTED_IDS, "5%", "패딩", "제조사", "180만", "역시즌", *REVIEW_LABELS):
        expect(token not in prompts.P3, f"P3에 사례 표식이 있습니다: {token}")
        expect(token not in prompts.P3_CTX2, f"P3_CTX2에 사례 표식이 있습니다: {token}")
    rendered = render_prompt(3, 4, 9, 0, 1, [sentence(4, "가 나"), sentence(5, "다")])
    expect("단위 식별: 3" in rendered, "단위 식별이 렌더링되지 않았습니다.")
    expect("단위 문장 구간: [4,9)" in rendered, "문장 구간 형식이 다릅니다.")
    expect("분석 청크: 1/1" in rendered, "단일 청크 위치가 1/1이 아닙니다.")
    expect('[{"sentence_index":4,"text":"가 나"},{"sentence_index":5,"text":"다"}]' in rendered, "문장 JSON 직렬화가 다릅니다.")
    expect("앞 문맥(JSON 배열; 각 항목은 sentence_index와 text. 없으면 []):\n[]\n" in rendered, "빈 앞 문맥이 []가 아닙니다.")
    expect("뒤 문맥(JSON 배열; 각 항목은 sentence_index와 text. 없으면 []):\n[]\n" in rendered, "빈 뒤 문맥이 []가 아닙니다.")
    expect(prompt_label_problems(rendered) == [], "문맥 지시가 빠졌거나 실험 구분이 들어갔습니다.")
    body = request_body("안녕")
    expect(
        list(body) == ["model", "messages", "temperature", "max_tokens", "chat_template_kwargs"],
        "요청 키 순서가 다릅니다.",
    )
    expect(body["model"] == BODY_MODEL, "body model이 다릅니다.")
    expect(body["temperature"] == 0 and body["max_tokens"] == 4096, "temperature 또는 max_tokens가 다릅니다.")
    expect(body["chat_template_kwargs"] == {"enable_thinking": True}, "thinking 설정이 다릅니다.")
    expect(PROMPT_ID == "P3_CTX2_B4096" and ENABLE_THINKING is True, "활성 조건이 P3_CTX2_B4096가 아닙니다.")
    expect(ACTIVE_PROMPT == prompts.P3_CTX2, "전체 확인이 프롬프트 본문을 바꿨습니다.")
    expect(body["messages"] == [{"role": "user", "content": "안녕"}], "메시지가 user 하나가 아닙니다.")


def check_split() -> None:
    groups, error = split_sentences([sentence(0, "가" * 6000)])
    expect(error is None and len(groups) == 1, "6000자 문장이 한 청크가 아닙니다.")
    groups, error = split_sentences([sentence(0, "가" * 3000), sentence(1, "나" * 3000)])
    expect(error is None and len(groups) == 1 and [item["index"] for item in groups[0]] == [0, 1], "6000자 경계가 한 청크가 아닙니다.")
    groups, error = split_sentences(
        [sentence(0, "가" * 3000), sentence(1, "나" * 3001), sentence(2, "다" * 3000)]
    )
    expect(error is None, error or "")
    expect([[item["index"] for item in group] for group in groups] == [[0], [1], [2]], "초과 단위 분할 순서가 다릅니다.")
    flat = [item["index"] for group in groups for item in group]
    expect(flat == [0, 1, 2], "문장이 한 번씩 순서대로 들어가지 않았습니다.")
    _groups, error = split_sentences([sentence(7, "가" * 6001)])
    expect(error is not None and "7" in error, "단일 초과 문장을 통과시켰습니다.")


def check_aggregate() -> None:
    def chunk(status: str, decision: str | None) -> dict:
        return {"status": status, "decision": decision}

    expect(aggregate_unit([chunk("ok", "needed"), chunk("failed", None)]) == ("selected", "partial"), "needed+failed 집계가 다릅니다.")
    expect(aggregate_unit([chunk("ok", "uncertain"), chunk("pending", None)]) == ("selected", "partial"), "uncertain+pending 집계가 다릅니다.")
    expect(aggregate_unit([chunk("ok", "not_needed"), chunk("failed", None)]) == ("unresolved", "partial"), "not_needed+failed 집계가 다릅니다.")
    expect(aggregate_unit([chunk("ok", "not_needed"), chunk("pending", None)]) == ("unresolved", "partial"), "not_needed+pending 집계가 다릅니다.")
    expect(aggregate_unit([chunk("ok", "not_needed"), chunk("ok", "not_needed")]) == ("not_selected", "ok"), "전부 not_needed 집계가 다릅니다.")
    expect(aggregate_unit([chunk("failed", None)]) == ("unresolved", "failed"), "실패만 있는 집계가 다릅니다.")
    expect(aggregate_unit([chunk("pending", None), chunk("pending", None)]) == ("unresolved", "pending"), "미실행 집계가 다릅니다.")
    empty = {
        "video_id": "empty",
        "title": "empty",
        "source_url": "https://example.invalid/empty",
        "problems": [],
        "warnings": [],
        "media": {"duration_seconds": 1, "duration_known": True, "audio_to_video_offset_seconds": 0, "path": "p", "result_id": "r", "sha256": "h"},
        "input": {"omitted_chunks": []},
        "units": [],
        "candidates": [],
        "validation": {"ok": None, "problems": []},
    }
    recompute_document(empty)
    expect(empty["status"] == "ok" and empty["units"] == [] and empty["candidates"] == [], "빈 입력 결과가 다릅니다.")
    expect(empty["validation"]["ok"] is True, "빈 입력 구조 검증이 통과하지 않았습니다.")


def check_time() -> None:
    positive, error = assess_video_range(1.5, 2.25, 0.25, 10, None, 0, 0)
    expect(error is None and positive is not None, error or "")
    expect(positive["video_start"] == 1.75 and positive["video_end"] == 2.5, "양수 offset을 한 번만 더하지 않았습니다.")
    expect(positive["video_start"] != 1.5 + 0.25 + 0.25, "offset이 두 번 적용되었습니다.")
    negative, error = assess_video_range(10, 12, -0.5, 20, None, 0, 0)
    expect(error is None and negative is not None, error or "")
    expect(negative["video_start"] == 9.5 and negative["video_end"] == 11.5, "음수 offset 적용 결과가 다릅니다.")
    _zero, error = assess_video_range(3, 3, 0, 10, None, 0, 0)
    expect(error is not None and "0" in error, "0길이 구간을 통과시켰습니다.")
    _reverse, error = assess_video_range(4, 3, 0, 10, None, 0, 0)
    expect(error is not None, "역전 구간을 통과시켰습니다.")
    _below, error = assess_video_range(1, 2, -1.5, 10, None, 0, 0)
    expect(error is not None and "음수" in error, "음수 영상 시각을 통과시켰습니다.")
    raw_end = 10.2 + 0.1
    summed, error = assess_video_range(10.2, 10.2 + 1, 0.1, 100, None, 0, 0)
    expect(error is None and summed is not None and summed["video_end"] == raw_end + 1, "시각을 반올림했습니다.")
    for excess in (0.04, 0.16, 0.36):
        duration = 100
        assessed, error = assess_video_range(0, duration + excess, 0, duration, None, 1, 2)
        expect(error is None and assessed is not None, "metadata 초과를 범위 오류로 처리했습니다.")
        warning = assessed["warnings"][0]
        expect(warning["code"] == "metadata_duration_exceeded", "경고 코드가 다릅니다.")
        expect(warning["excess_seconds"] == assessed["video_end"] - duration, "초과 초 계산이 다릅니다.")
        expect(assessed["video_end"] == duration + excess, "원본 끝 시각을 바꾸었습니다.")
    _media, error = assess_video_range(0, 10, 0, 100, 9, 0, 0)
    expect(error is not None and "미디어" in error, "실제 미디어 범위 초과를 경고로 넘겼습니다.")
    warned, error = assess_video_range(0, 10.04, 0, 10, None, 0, 0)
    expect(error is None and warned is not None and warned["warnings"], "metadata 차이를 범위 오류로 처리했습니다.")


def check_duration_keeps_decision() -> None:
    for excess in (0.04, 0.16, 0.36):
        duration = 50
        document = {
            "video_id": "dur",
            "title": "dur",
            "source_url": "https://example.invalid/dur",
            "problems": [],
            "warnings": [],
            "media": {
                "duration_seconds": duration,
                "duration_known": True,
                "audio_to_video_offset_seconds": 0,
                "path": "p",
                "result_id": "r",
                "sha256": "h",
            },
            "input": {"omitted_chunks": []},
            "units": [
                {
                    "unit_index": 0,
                    "sentence_start": 0,
                    "sentence_end": 1,
                    "audio_start": 0,
                    "audio_end": duration + excess,
                    "sentences": [sentence(0, "버튼", 0, duration + excess)],
                    "status": "pending",
                    "selection": "unresolved",
                    "chunks": [
                        {
                            "chunk_index": 0,
                            "sentence_start": 0,
                            "sentence_end": 1,
                            "audio_start": 0,
                            "audio_end": duration + excess,
                            "video_start": 0,
                            "video_end": duration + excess,
                            "char_count": 2,
                            "status": "ok",
                            "decision": "needed",
                            "reason": "버튼 위치",
                            "evidence_sentence_indices": [0],
                            "call_ref": "calls/a.json",
                            "reused_from": None,
                            "problems": [],
                            "warnings": [
                                {
                                    "code": "metadata_duration_exceeded",
                                    "unit_index": 0,
                                    "chunk_index": 0,
                                    "metadata_duration_seconds": duration,
                                    "video_end": duration + excess,
                                    "excess_seconds": excess,
                                }
                            ],
                        }
                    ],
                }
            ],
            "candidates": [],
            "validation": {"ok": None, "problems": []},
        }
        recompute_document(document)
        chunk = document["units"][0]["chunks"][0]
        expect(document["status"] == "ok", "metadata 초과가 영상 status를 바꿨습니다.")
        expect(document["validation"]["ok"] is True, "metadata 초과가 validation을 실패로 만들었습니다.")
        expect(chunk["decision"] == "needed" and chunk["audio_end"] == duration + excess, "판정 또는 원본 시각을 바꾸었습니다.")
        expect(document["candidates"] and document["candidates"][0]["video_end"] == duration + excess, "후보를 제거하거나 시각을 바꿨습니다.")
        expect(document["warnings"] and document["warnings"][0]["excess_seconds"] == excess, "경고를 영상 최상위에 모으지 않았습니다.")


def check_parser() -> None:
    allowed = {1, 2, 3}
    good = '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[1,2]}'
    parsed = parse_decision(good, allowed)
    expect(parsed["evidence_sentence_indices"] == [1, 2], "정상 evidence를 거절했습니다.")
    fenced = "```json\n" + good + "\n```"
    expect(parse_decision(fenced, allowed)["decision"] == "needed", "json 펜스를 해석하지 못했습니다.")
    bare = "```\n" + good + "\n```"
    expect(parse_decision(bare, allowed)["decision"] == "needed", "빈 펜스를 해석하지 못했습니다.")
    four = '{"decision":"not_needed","reason":"의견","evidence_sentence_indices":[1,2,3,4]}'
    four_parsed = parse_decision(four, {1, 2, 3, 4})
    expect(four_parsed["evidence_sentence_indices"] == [1, 2, 3, 4], "4개 evidence를 자르거나 거절했습니다.")
    for text in (
        '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[1],"decision":"not_needed"}',
        '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[1],"note":"x"}',
        '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[true]}',
        '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[1.5]}',
        '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[9]}',
        '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[1,1]}',
        '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[]}',
        '{"decision":"needed","reason":"  ","evidence_sentence_indices":[1]}',
        good + "\n{}",
        "```python\n" + good + "\n```",
        "설명\n```json\n" + good + "\n```",
    ):
        try:
            parse_decision(text, allowed)
        except SchemaError:
            continue
        raise AssertionError(f"거절해야 하는 응답을 통과시켰습니다: {text}")
    _interpreted, error = interpret_response(200, response_body_for("needed", 1, "length"))
    expect(error == "예산 제약으로 비교 불완전", "잘린 응답을 예산 제약으로 적지 않았습니다.")
    exact = json.dumps(
        {
            "choices": [{"finish_reason": "stop", "message": {"content": good}}],
            "usage": {"completion_tokens": MAX_TOKENS},
        },
        ensure_ascii=False,
    )
    interpreted, error = interpret_response(200, exact)
    expect(error is None and interpreted is not None and interpreted["content"] == good, "1024토큰에서 끝난 stop을 잘림으로 보았습니다.")
    reasoned = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": good,
                        "reasoning": '{"decision":"not_needed","reason":"문맥의 시연","evidence_sentence_indices":[9]}',
                    },
                }
            ],
            "usage": {"completion_tokens": 30, "completion_tokens_details": {"reasoning_tokens": 12}},
        },
        ensure_ascii=False,
    )
    interpreted, error = interpret_response(200, reasoned)
    expect(error is None and interpreted is not None and interpreted["content"] == good, "reasoning을 판정 원문으로 읽었습니다.")
    expect(parse_decision(interpreted["content"], allowed)["decision"] == "needed", "reasoning의 판정을 사용했습니다.")
    payload = json.loads(reasoned)
    expect(extract_reasoning(payload) is not None and thinking_observed_from(payload) is True, "thinking 출력을 보존하지 않았습니다.")
    cut = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": None, "reasoning": good},
                }
            ],
            "usage": {"completion_tokens": MAX_TOKENS, "completion_tokens_details": {"reasoning_tokens": MAX_TOKENS}},
        },
        ensure_ascii=False,
    )
    _interpreted, error = interpret_response(200, cut)
    expect(error == "예산 제약으로 비교 불완전", "reasoning만 있는 length를 판정으로 통과시켰습니다.")
    observed = observe_response(cut)
    expect(observed["reasoning"] == good and observed["content"] is None, "length 응답의 reasoning과 content를 섞었습니다.")
    _interpreted, error = interpret_response(500, "no")
    expect(error is not None and error.startswith("HTTP"), "HTTP 오류를 통과시켰습니다.")


def check_pilot_order() -> None:
    chunks = [
        {"video_id": "b", "video_order": 1, "unit_index": 0, "chunk_index": 0, "rendered_chars": 10},
        {"video_id": "b", "video_order": 1, "unit_index": 1, "chunk_index": 0, "rendered_chars": 50},
        {"video_id": "a", "video_order": 0, "unit_index": 0, "chunk_index": 0, "rendered_chars": 50},
    ]
    order = select_pilot(chunks)
    expect(
        [(item["video_id"], item["unit_index"], item["chunk_index"]) for item in order] == [("a", 0, 0), ("b", 0, 0)],
        "동률 pilot 순서가 다릅니다.",
    )
    chunks[1]["rendered_chars"] = 51
    longer = select_pilot(chunks)
    expect(
        [(item["video_id"], item["unit_index"], item["chunk_index"]) for item in longer]
        == [("b", 1, 0), ("a", 0, 0), ("b", 0, 0)],
        "최장 청크 뒤 영상별 첫 청크 순서가 다릅니다.",
    )
    many = []
    for index in range(10):
        many.append(
            {
                "video_id": f"v{index}",
                "video_order": index,
                "unit_index": 0,
                "chunk_index": 0,
                "rendered_chars": 10,
            }
        )
        many.append(
            {
                "video_id": f"v{index}",
                "video_order": index,
                "unit_index": 1,
                "chunk_index": 0,
                "rendered_chars": 100 if index == 9 else 1,
            }
        )
    capped = select_pilot(many)
    expect(len(capped) == 9, "pilot 상한이 9가 아닙니다.")
    expect((capped[0]["video_id"], capped[0]["unit_index"]) == ("v9", 1), "최장 청크를 먼저 고르지 않았습니다.")


def check_plan_and_resume() -> None:
    import io
    import tempfile
    from contextlib import redirect_stdout

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runs = root / "tests" / "T2" / "runs"
        sentences = [sentence(0, "가" * 6000, 0, 1), sentence(1, "나" * 6000, 1, 2), sentence(2, "다" * 6000, 2, 3)]
        write_fixture(root, "vid", sentences, offset=0.0, duration=100)
        write_config(root, ["vid"])
        broken = root / "broken"
        write_fixture(broken, "vid", sentences, offset=0.0, duration=100)
        metadata_path = broken / "data" / "vid" / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        del metadata["audio_to_video_offset_seconds"]
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        t1_path = broken / "input" / "vid" / "semantic_units.json"
        t1 = json.loads(t1_path.read_text(encoding="utf-8"))
        del t1["time"]["audio_to_video_offset_seconds"]
        t1_path.write_text(json.dumps(t1), encoding="utf-8")
        write_config(broken, ["vid"])
        _videos, validation_problems = load_campaign(broken, broken / "input", broken / "data", False)
        expect(validation_problems, "offset 누락이 검증 함수에서 통과했습니다.")
        with redirect_stdout(io.StringIO()):
            code = cmd_plan(broken, broken / "input", broken / "data", broken / "runs", False)
        expect(code == 1, "잘못된 계획의 종료 코드가 1이 아닙니다.")
        expect(not (broken / "runs").exists(), "입력 검사 실패인데 run 디렉터리를 만들었습니다.")
        before_clients = InferClient.created
        with redirect_stdout(io.StringIO()) as buffer:
            code = cmd_plan(root, root / "input", root / "data", runs, False)
        expect(code == 0, "정상 fixture 계획을 만들지 못했습니다.")
        expect(InferClient.created == before_clients, "계획 단계에서 HTTP 클라이언트를 만들었습니다.")
        output = buffer.getvalue()
        run_id = next(line.split(": ", 1)[1] for line in output.splitlines() if line.startswith("run_id: "))
        run_dir = runs / run_id
        manifest = read_json(run_dir / "manifest.json")
        expect(manifest["expected_call_count"] == 3, "청크 수가 3이 아닙니다.")
        expect(manifest["settings"]["char_budget"] == 6000, "설정을 저장하지 않았습니다.")
        expect(manifest["prompt"] == prompts.P3_CTX2, "프롬프트 전문을 저장하지 않았습니다.")
        expect("confirm_order" not in manifest, "fixture 계획에 confirm_order를 넣었습니다.")
        client = ScriptedClient(["ok", "http", "ok"])
        with redirect_stdout(io.StringIO()):
            code = cmd_execute(root, root / "input", root / "data", runs, run_id, "resume", client, False)
        expect(code == 1, "중간 실패의 종료 코드가 1이 아닙니다.")
        expect(len(client.posts) == 2, "실패 뒤 호출을 중단하지 않았습니다.")
        document = read_json(run_dir / "vid" / "visual_candidates.json")
        statuses = [chunk["status"] for _unit, chunk in iter_chunks(document)]
        expect(statuses == ["ok", "failed", "pending"], f"실패 후 상태를 보존하지 않았습니다: {statuses}")
        resume_client = ScriptedClient(["ok"])
        with redirect_stdout(io.StringIO()):
            cmd_execute(root, root / "input", root / "data", runs, run_id, "resume", resume_client, False)
        expect(len(resume_client.posts) == 1, "resume가 성공 청크를 다시 호출했거나 pending을 건너뛰었습니다.")
        document = read_json(run_dir / "vid" / "visual_candidates.json")
        statuses = [chunk["status"] for _unit, chunk in iter_chunks(document)]
        expect(statuses[0] == "ok" and statuses[1] == "failed" and statuses[2] == "ok", "resume가 실패 청크를 자동 재시도했습니다.")
        decision_before = (run_dir / "review_decision.md").read_bytes()
        with redirect_stdout(io.StringIO()):
            code = cmd_render_only(root, root / "input", root / "data", runs, run_id, False)
        expect(code == 0, "render-only가 실패했습니다.")
        expect((run_dir / "review_decision.md").read_bytes() == decision_before, "render-only가 검토 기록을 바꿨습니다.")
        expect(InferClient.created == before_clients, "render-only가 HTTP 클라이언트를 만들었습니다.")
        with redirect_stdout(io.StringIO()) as second_buffer:
            code = cmd_plan(root, root / "input", root / "data", runs, False)
        expect(code == 0, "두 번째 계획을 만들지 못했습니다.")
        second_id = next(line.split(": ", 1)[1] for line in second_buffer.getvalue().splitlines() if line.startswith("run_id: "))
        filler = ScriptedClient(["ok"])
        with redirect_stdout(io.StringIO()):
            code = cmd_execute(root, root / "input", root / "data", runs, second_id, "resume", filler, False)
        expect(code == 0, "남은 청크를 성공으로 끝내지 못했습니다.")
        expect(len(filler.posts) == 1, "이미 성공한 청크를 새 run에서 다시 호출했습니다.")
        expect('"sentence_index":1' in filler.posts[0]["messages"][0]["content"], "재사용하지 않은 청크가 실패 청크가 아닙니다.")
        with redirect_stdout(io.StringIO()) as third_buffer:
            code = cmd_plan(root, root / "input", root / "data", runs, False)
        third_id = next(line.split(": ", 1)[1] for line in third_buffer.getvalue().splitlines() if line.startswith("run_id: "))
        idle = ScriptedClient([])
        with redirect_stdout(io.StringIO()):
            code = cmd_execute(root, root / "input", root / "data", runs, third_id, "resume", idle, False)
        expect(code == 0, "동일 키 재사용 실행이 실패했습니다.")
        expect(idle.posts == [], "재사용 가능한 청크를 다시 호출했습니다.")
        document = read_json(runs / third_id / "vid" / "visual_candidates.json")
        expect(all(chunk["status"] == "ok" and chunk["reused_from"] for _unit, chunk in iter_chunks(document)), "성공 재사용 표시가 없습니다.")
        changed = sentence(0, "가" * 5999, 0, 1)
        write_fixture(root, "vid", [changed, sentences[1], sentences[2]], offset=0.0, duration=100)
        with redirect_stdout(io.StringIO()) as fourth_buffer:
            code = cmd_plan(root, root / "input", root / "data", runs, False)
        fourth_id = next(line.split(": ", 1)[1] for line in fourth_buffer.getvalue().splitlines() if line.startswith("run_id: "))
        caller = ScriptedClient(["not_needed", "not_needed", "not_needed"])
        with redirect_stdout(io.StringIO()):
            cmd_execute(root, root / "input", root / "data", runs, fourth_id, "resume", caller, False)
        expect(len(caller.posts) == 3, "입력이 바뀌었는데 기존 성공을 재사용했습니다.")


def check_confirm_scope() -> None:
    import io
    import tempfile
    from contextlib import redirect_stdout

    expect(
        list(CONFIRM_ORDER)
        == [
            ("bjDkfjSmXVo", 7),
            ("7EJ-xotXiSE", 18),
            ("iVelddb1qQ4", 5),
            ("CvuE-jyg-N8", 59),
            ("Fw-8hZhnH3Q", 17),
            ("bjDkfjSmXVo", 0),
            ("Fw-8hZhnH3Q", 19),
            ("iVelddb1qQ4", 24),
        ],
        "확인 순서가 지정과 다릅니다.",
    )
    chunks = [
        {"video_id": "a", "unit_index": 1, "chunk_index": 0},
        {"video_id": "b", "unit_index": 0, "chunk_index": 0},
        {"video_id": "a", "unit_index": 0, "chunk_index": 0},
    ]
    selected, error = select_confirm(chunks, (("b", 0), ("a", 0)))
    expect(error is None and [(item["video_id"], item["unit_index"]) for item in selected] == [("b", 0), ("a", 0)], "확인 순서를 유지하지 않았습니다.")
    _missing, error = select_confirm(chunks, (("c", 0),))
    expect(error is not None and "0개" in error, "없는 단위를 통과시켰습니다.")
    chunks.append({"video_id": "a", "unit_index": 0, "chunk_index": 1})
    _multi, error = select_confirm(chunks, (("a", 0),))
    expect(error is not None and "2개" in error, "여러 청크 단위를 통과시켰습니다.")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runs = root / "tests" / "T2" / "runs"
        sentences = [sentence(0, "가" * 6000, 0, 1), sentence(1, "나" * 6000, 1, 2), sentence(2, "다" * 6000, 2, 3)]
        write_fixture(root, "vid", sentences, offset=0.0, duration=100)
        write_config(root, ["vid"])
        with redirect_stdout(io.StringIO()) as buffer:
            code = cmd_plan(root, root / "input", root / "data", runs, False)
        expect(code == 0, "확인 범위 fixture 계획을 만들지 못했습니다.")
        run_id = next(line.split(": ", 1)[1] for line in buffer.getvalue().splitlines() if line.startswith("run_id: "))
        run_dir = runs / run_id
        idle = ScriptedClient([])
        with redirect_stdout(io.StringIO()):
            code = cmd_execute(root, root / "input", root / "data", runs, run_id, "confirm", idle, False)
        expect(code == 1 and idle.posts == [], "confirm_order 없이 확인 호출을 시작했습니다.")
        manifest = read_json(run_dir / "manifest.json")
        manifest["confirm_order"] = [manifest["plan"][2], manifest["plan"][0]]
        dump_json(run_dir / "manifest.json", manifest)
        client = ScriptedClient(["ok", "ok"])
        with redirect_stdout(io.StringIO()):
            code = cmd_execute(root, root / "input", root / "data", runs, run_id, "confirm", client, False)
        expect(code == 0 and len(client.posts) == 2, "지정 청크만 호출하지 않았습니다.")
        indices = []
        for body in client.posts:
            prompt = body["messages"][0]["content"]
            expect("분석 후보로 남기는 판단은 구분한다" in prompt, "확인 호출이 P3 기준을 유지하지 않습니다.")
            expect("판정 대상은 대상 청크뿐이다." in prompt, "확인 호출에 문맥 지시가 없습니다.")
            indices.append(request_sentence_indices(prompt)[0])
        expect(indices == [2, 0], f"확인 호출 순서가 다릅니다: {indices}")
        document = read_json(run_dir / "vid" / "visual_candidates.json")
        statuses = [chunk["status"] for _unit, chunk in iter_chunks(document)]
        expect(statuses == ["ok", "pending", "ok"], f"지정 밖 청크 상태가 바뀌었습니다: {statuses}")
        expect(read_json(run_dir / "run.json")["status"] == "confirm_complete", "확인 완료 상태가 아닙니다.")
        resume = ScriptedClient(["ok"])
        with redirect_stdout(io.StringIO()):
            code = cmd_execute(root, root / "input", root / "data", runs, run_id, "resume", resume, False)
        expect(code == 1 and resume.posts == [], "confirm_order run의 resume가 pending을 호출했습니다.")
        document = read_json(run_dir / "vid" / "visual_candidates.json")
        statuses = [chunk["status"] for _unit, chunk in iter_chunks(document)]
        expect(statuses == ["ok", "pending", "ok"], "resume 거부가 청크 상태를 바꿨습니다.")
        expect(read_json(run_dir / "run.json")["status"] == "confirm_complete", "resume 거부가 run 상태를 바꿨습니다.")


def check_lock() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        run_dir = Path(temporary)
        with run_lock(run_dir):
            try:
                with run_lock(run_dir):
                    raise AssertionError("잠금이 걸린 run을 다시 열었습니다.")
            except RunBusy:
                return
    raise AssertionError("잠금 거절에 도달하지 않았습니다.")


def check_reuse_key_changes() -> None:
    base = sentence(0, "원문")
    body = request_body(render_prompt(0, 0, 1, 0, 1, [base]))
    first = reuse_key("v", 0, 0, 0, 1, [base], "a", "b", "c", "d", prompts.P3, body)
    changed = sentence(0, "다른 원문")
    changed_body = request_body(render_prompt(0, 0, 1, 0, 1, [changed]))
    second = reuse_key("v", 0, 0, 0, 1, [changed], "a", "b", "c", "d", prompts.P3, changed_body)
    third = reuse_key("v", 0, 0, 0, 1, [base], "a", "b", "c", "d", prompts.P3 + "\n", body)
    expect(first != second, "입력 변경이 재사용 키를 유지했습니다.")
    expect(first != third, "프롬프트 변경이 재사용 키를 유지했습니다.")
    p2_body = request_body(render_prompt(0, 0, 1, 0, 1, [base], template=prompts.P2))
    p2_key = reuse_key("v", 0, 0, 0, 1, [base], "a", "b", "c", "d", prompts.P2, p2_body)
    expect(first != p2_key, "P2 응답을 P3 키로 재사용할 수 있습니다.")


def minimal_video(groups: list[list[dict]]) -> dict:
    source_units = []
    sentences: list[dict] = []
    for unit_index, rows in enumerate(groups):
        source_units.append(
            {
                "unit_index": unit_index,
                "sentence_start": rows[0]["index"],
                "sentence_end": rows[-1]["index"] + 1,
                "audio_start": rows[0]["start_time"],
                "audio_end": rows[-1]["end_time"],
                "sentences": rows,
            }
        )
        sentences.extend(rows)
    return {
        "video_id": "v",
        "video_order": 0,
        "offset": 0.0,
        "duration_seconds": 100,
        "t1_sha256": "a",
        "alignment_sha256": "b",
        "metadata_sha256": "c",
        "effective_config_sha256": "d",
        "sentences": sentences,
        "source_units": source_units,
    }


def check_context_window() -> None:
    rows = [sentence(index, f"문장{index}", float(index), float(index) + 0.5) for index in range(6)]
    start, error = chunk_context(rows, 0, 1)
    expect(error is None and start is not None, error or "")
    expect(start["before"] == [] and [item["index"] for item in start["after"]] == [1, 2], "영상 시작 문맥이 다릅니다.")
    expect(sentences_json(start["before"]) == "[]", "빈 앞 문맥 직렬화가 []가 아닙니다.")
    end, error = chunk_context(rows, 5, 6)
    expect(error is None and end is not None, error or "")
    expect(end["after"] == [] and [item["index"] for item in end["before"]] == [3, 4], "영상 끝 문맥이 다릅니다.")
    expect(sentences_json(end["after"]) == "[]", "빈 뒤 문맥 직렬화가 []가 아닙니다.")
    middle, error = chunk_context(rows, 2, 4)
    expect(error is None and middle is not None, error or "")
    combined = [item["index"] for item in middle["before"] + middle["target"] + middle["after"]]
    expect(combined == [0, 1, 2, 3, 4, 5] and len(combined) == len(set(combined)), "문맥과 대상이 겹치거나 빠졌습니다.")
    units, flat, problems = plan_units(minimal_video([rows[:3], rows[3:]]))
    expect(problems == [], "문맥 계획에 오류가 있습니다.")
    expect([unit["sentence_start"] for unit in units] == [0, 3], "문맥 때문에 의미 단위가 병합되었습니다.")
    expect([[chunk["sentence_start"], chunk["sentence_end"]] for chunk in flat] == [[0, 3], [3, 6]], "대상 청크를 재분할했습니다.")
    first, second = flat
    expect(first["context_before_start"] == 0 and first["context_before_end"] == 0, "앞 문맥이 영상 밖으로 나갔습니다.")
    expect(first["context_after_start"] == 3 and first["context_after_end"] == 5, "단위 경계 너머의 뒤 문맥을 가져오지 않았습니다.")
    expect(first["audio_start"] == 0.0 and first["audio_end"] == 2.5, "후보 오디오 시각이 대상 문장과 다릅니다.")
    expect(first["video_start"] == 0.0 and first["video_end"] == 2.5, "후보 영상 시각에 문맥이 포함되었습니다.")
    expect(second["context_before_start"] == 1 and second["context_after_end"] == 6, "뒤 끝 문맥 범위가 다릅니다.")
    expect(second["audio_start"] == 3.0 and second["audio_end"] == 5.5, "뒤 단위 후보 시각이 대상과 다릅니다.")
    document = {
        "video_id": "v",
        "units": [
            {
                "unit_index": 0,
                "chunks": [
                    {
                        "status": "ok",
                        "decision": "needed",
                        "chunk_index": 0,
                        "sentence_start": first["sentence_start"],
                        "sentence_end": first["sentence_end"],
                        "audio_start": first["audio_start"],
                        "audio_end": first["audio_end"],
                        "video_start": first["video_start"],
                        "video_end": first["video_end"],
                        "reason": "버튼",
                        "call_ref": None,
                        "warnings": [],
                    }
                ],
            }
        ],
    }
    candidates = build_candidates(document)
    expect(candidates[0]["sentence_start"] == 0 and candidates[0]["sentence_end"] == 3, "후보 문장 범위에 문맥이 포함되었습니다.")
    expect(candidates[0]["audio_end"] == 2.5 and candidates[0]["video_end"] == 2.5, "후보 시간이 문맥 문장까지 확장되었습니다.")
    allowed = {2, 3}
    parsed = parse_decision(
        '{"decision":"needed","reason":"버튼","evidence_sentence_indices":[2]}',
        allowed,
    )
    expect(parsed["evidence_sentence_indices"] == [2], "대상 index evidence를 거절했습니다.")
    try:
        parse_decision('{"decision":"needed","reason":"버튼","evidence_sentence_indices":[1]}', allowed)
    except SchemaError:
        rejected = True
    else:
        rejected = False
    expect(rejected, "문맥 index evidence를 통과시켰습니다.")


def check_budget_guard() -> None:
    def chunk(status: str, decision: str | None) -> dict:
        return {
            "status": status,
            "decision": decision,
            "reason": "모델 근거" if decision else None,
            "evidence_sentence_indices": [2] if decision else None,
            "problems": ["예산 제약으로 비교 불완전"] if status == "failed" else [],
            "chunk_index": 0,
            "sentence_start": 8,
            "sentence_end": 36,
            "audio_start": 31.6,
            "audio_end": 139.76,
            "video_start": 31.6,
            "video_end": 139.76,
            "warnings": [],
            "call_ref": "calls/original.json",
            "context_before_start": 6,
            "context_after_end": 38,
        }

    def exhausted(**overrides: object) -> dict:
        call = {
            "http_status": 200,
            "finish_reason": "length",
            "thinking_requested": True,
            "error": "예산 제약으로 비교 불완전",
            "parsed": None,
            "content": None,
            "usage": {"completion_tokens": 4096, "completion_tokens_details": {"reasoning_tokens": 4096}},
        }
        call.update(overrides)
        return call

    fallback = {
        "adopted_decision": {"decision": "not_needed", "reason": "fallback 근거", "evidence_sentence_indices": [12, 33]},
        "fallback_call_ref": "fallback/calls/off.json",
    }
    failed = chunk("failed", None)
    policy = describe_budget_policy(failed, exhausted(), fallback, "calls/original.json")
    expect(policy["policy_candidate"] is True and policy["candidate_source"] == "budget_guard", "예산 소진을 후보로 유지하지 않았습니다.")
    expect(policy["fallback_decision"] == "not_needed", "fallback 판정을 정책 기록에서 빼었습니다.")
    expect(policy["thinking_on_decision"] is None and policy["model_inference_success"] is False, "예산 소진을 모델 성공으로 표시했습니다.")
    candidate = policy_candidate("H2WN130OzLg", 7, failed, policy)
    expect(candidate is not None and candidate["evidence_sentence_indices"] is None, "모델이 내지 않은 evidence를 만들었습니다.")
    expect(candidate["sentence_start"] == 8 and candidate["video_end"] == 139.76, "후보 시각을 대상 청크 밖으로 옮겼습니다.")
    expect(candidate["source_call_ref"] == "calls/original.json", "원래 실패 호출을 참조하지 않았습니다.")
    expect(failed["decision"] is None, "정책 함수가 모델 판정을 바꿨습니다.")
    documents = [
        {
            "video_id": "H2WN130OzLg",
            "candidates": [],
            "units": [
                {"unit_index": 7, "selection": "unresolved", "status": "failed", "chunks": [failed]},
                {"unit_index": 8, "selection": "not_selected", "status": "ok", "chunks": [chunk("ok", "not_needed")]},
            ],
        }
    ]
    derived, summary = apply_budget_guard(
        documents,
        {( "H2WN130OzLg", 7, 0): exhausted()},
        {( "H2WN130OzLg", 7, 0): fallback},
        {( "H2WN130OzLg", 7, 0): "calls/original.json"},
    )
    expect(summary["policy_candidates"] == 1 and len(summary["budget_guard"]) == 1, "예산 소진 후보가 하나가 아닙니다.")
    expect(derived[0]["units"][1]["chunks"][0]["policy"]["policy_candidate"] is False, "정상 not_needed를 후보로 바꿨습니다.")
    expect(derived[0]["units"][0]["chunks"][0]["decision"] is None, "파생 결과의 모델 판정을 바꿨습니다.")
    expect(documents[0]["units"][0]["chunks"][0]["decision"] is None and "policy" not in documents[0]["units"][0]["chunks"][0], "원본 청크를 수정했습니다.")
    needed = chunk("ok", "needed")
    both, both_summary = apply_budget_guard(
        [{"video_id": "v", "candidates": [{"candidate_id": "v:u0:c0"}], "units": [{"unit_index": 0, "chunks": [needed]}]}],
        {("v", 0, 0): exhausted()},
        {},
        {("v", 0, 0): "calls/needed.json"},
    )
    expect(both_summary["policy_candidates"] == 1 and both_summary["budget_guard"] == [], "이미 있는 모델 후보를 중복 생성했습니다.")
    expect(both[0]["candidates"][0]["candidate_source"] == "thinking_on", "정상 성공 후보의 출처가 다릅니다.")
    rejects = [
        exhausted(http_status=500, finish_reason="stop", error="HTTP 500"),
        exhausted(http_status=None, finish_reason=None, error="요청이 시간 초과되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다."),
        exhausted(finish_reason="stop", error="키가 계약을 벗어납니다.", content='{"decision":"needed"}'),
        exhausted(usage={"completion_tokens": 1024, "completion_tokens_details": {"reasoning_tokens": 1024}}),
        exhausted(thinking_requested=False),
        exhausted(parsed={"decision": "needed", "reason": "있음", "evidence_sentence_indices": [8]}),
    ]
    for call in rejects:
        expect(reasoning_budget_exhausted(call) is False, f"다른 실패를 예산 소진으로 적용했습니다: {call.get('error')}")
        other = describe_budget_policy(chunk("failed", None), call, fallback, "calls/other.json")
        expect(other["policy_candidate"] is False, "다른 실패를 후보로 유지했습니다.")


def check_official_confirm() -> None:
    videos, problems = load_campaign(ROOT, INPUT_ROOT, DATA_ROOT, True)
    expect(problems == [], "\n".join(problems))
    flat: list[dict] = []
    documents = []
    for video in videos:
        _units, chunks, plan_problems = plan_units(video)
        expect(plan_problems == [], "\n".join(plan_problems))
        flat.extend(chunks)
        documents.append(build_document(video, _units))
    expect(len(flat) == 249, f"청크가 249개가 아닙니다: {len(flat)}")
    preflight = b_reuse_problems(videos, documents, flat)
    expect(preflight == [], "\n".join(preflight))
    print("reused_b: 8", flush=True)
    print(f"http_targets: {len(flat) - len(expected_b_idents())}", flush=True)


def run_checks() -> int:
    try:
        check_prompt()
        check_split()
        check_aggregate()
        check_time()
        check_duration_keeps_decision()
        check_parser()
        check_budget_guard()
        check_pilot_order()
        check_reuse_key_changes()
        check_context_window()
        check_official_confirm()
        check_confirm_scope()
        check_lock()
        check_plan_and_resume()
    except Exception as exc:
        print(f"check 실패: {exc}", flush=True)
        traceback.print_exc()
        return 1
    print("check ok", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
