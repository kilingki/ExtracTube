"""T1 문장 의미 단위 분할. 설정은 상수이며 본 실행은 인자로 바꾸지 않는다."""

from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


import hashlib
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompts

ROOT = EXPERIMENT_ROOT
DATA_ROOT = resolve_experiment_path(ROOT / "data")
OUTPUTS_DIR = resolve_experiment_path(ROOT / "tests" / "T1" / "outputs")
ARCHIVE_DIR = resolve_experiment_path(ROOT / "tests" / "T1" / "outputs_archive")
REQUIRED_VIDEO_IDS = ("Fw-8hZhnH3Q", "CvuE-jyg-N8")

MAX_SENTENCES = 40
MAX_CHARS = 6000
OVERLAP_SENTENCES = 8
NEAR_DISTANCE = 2
CONFLICT_CONTEXT_SENTENCES = 8
VERIFY_WINDOW_SENTENCES = 12
TEMPERATURE = 0
MAX_TOKENS = 4096
ENABLE_THINKING = False
TIMEOUT_SECONDS = 7200
INFER_BASE_URL = "http://127.0.0.1:8095"
INFER_MODEL_ID = "qwen-vlm"
BODY_MODEL = "qwen3.8-27b"
MARKDOWN_ESCAPES = set("\\`*_{}[]<>|#")


class SchemaError(ValueError):
    """응답이 6.4 계약과 다르다."""


class BudgetFailure(Exception):
    """글자 예산을 문장 단위로 지킬 수 없다."""


class StructureFailure(Exception):
    """내부 구조가 깨졌다. LLM을 다시 호출하지 않는다."""


class CallFailed(Exception):
    """같은 본문 재시도까지 실패했다."""


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def time_literal(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def settings_payload() -> dict:
    return {
        "max_sentences": MAX_SENTENCES,
        "max_chars": MAX_CHARS,
        "overlap_sentences": OVERLAP_SENTENCES,
        "near_distance": NEAR_DISTANCE,
        "conflict_context_sentences": CONFLICT_CONTEXT_SENTENCES,
        "verify_window_sentences": VERIFY_WINDOW_SENTENCES,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "enable_thinking": ENABLE_THINKING,
        "timeout_seconds": TIMEOUT_SECONDS,
        "infer_base_url": INFER_BASE_URL,
        "infer_model_id": INFER_MODEL_ID,
        "body_model": BODY_MODEL,
    }


def model_payload() -> dict:
    return {
        "url": INFER_BASE_URL,
        "infer_model_id": INFER_MODEL_ID,
        "body_model": BODY_MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "enable_thinking": ENABLE_THINKING,
    }


def prompts_payload() -> dict:
    return {
        "boundary": prompts.BOUNDARY,
        "conflict": prompts.CONFLICT,
        "verify": prompts.VERIFY,
    }


def render(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for key, value in replacements.items():
        token = "{" + key + "}"
        if token not in rendered:
            raise RuntimeError(f"프롬프트 자리표시자가 없습니다: {key}")
        rendered = rendered.replace(token, value, 1)
    return rendered


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def sentence_line(sentence: dict) -> str:
    start = time_literal(sentence["start_time"])
    end = time_literal(sentence["end_time"])
    return f"[{sentence['index']}] {start}-{end} {sentence['text']}"


def sentence_block(sentences: list[dict]) -> str:
    return "\n".join(sentence_line(sentence) for sentence in sentences)


def md_escape(text: str) -> str:
    return "".join("\\" + char if char in MARKDOWN_ESCAPES else char for char in text)


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict:
    payload: dict = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"중복 키: {key}")
        payload[key] = value
    return payload


def _reject_constant(name: str) -> None:
    raise ValueError(f"허용되지 않은 값: {name}")


def unwrap_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    newline = stripped.find("\n")
    if newline == -1 or not stripped.endswith("```"):
        return stripped
    body = stripped[newline + 1 :].rstrip()
    fence = body.rfind("```")
    if fence == -1 or body[fence:].strip() != "```":
        return stripped
    return body[:fence].strip()


def parse_object(text: str) -> dict:
    try:
        value = json.loads(
            unwrap_fence(text),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise SchemaError(str(exc)) from exc
    if not isinstance(value, dict):
        raise SchemaError("JSON 객체가 아닙니다.")
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise SchemaError("reason이 비어 있습니다.")
    return value


def parse_boundary_response(text: str, allowed: set[int]) -> dict:
    payload = parse_object(text)
    if set(payload) != {"boundaries"}:
        raise SchemaError("boundaries만 허용됩니다.")
    items = payload["boundaries"]
    if not isinstance(items, list):
        raise SchemaError("boundaries가 배열이 아닙니다.")
    parsed = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"sentence_index", "reason"}:
            raise SchemaError("경계 항목의 키가 계약과 다릅니다.")
        index = item["sentence_index"]
        if not is_int(index):
            raise SchemaError("sentence_index가 정수가 아닙니다.")
        if index not in allowed:
            raise SchemaError(f"허용되지 않은 sentence_index입니다: {index}")
        if index in seen:
            raise SchemaError(f"sentence_index가 중복됩니다: {index}")
        seen.add(index)
        parsed.append({"sentence_index": index, "reason": _reason(item["reason"])})
    parsed.sort(key=lambda item: item["sentence_index"])
    return {"boundaries": parsed}


def parse_conflict_response(
    text: str,
    candidate_indices: list[int],
    context_start: int,
    context_end: int,
    sentence_count: int,
) -> dict:
    payload = parse_object(text)
    if set(payload) != {"decisions"}:
        raise SchemaError("decisions만 허용됩니다.")
    items = payload["decisions"]
    if not isinstance(items, list):
        raise SchemaError("decisions가 배열이 아닙니다.")
    parsed = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            raise SchemaError("결정 항목이 객체가 아닙니다.")
        action = item.get("action")
        if action not in {"keep", "drop", "move"}:
            raise SchemaError("action이 올바르지 않습니다.")
        allowed_keys = {"sentence_index", "action", "reason"}
        if action == "move":
            allowed_keys.add("to_index")
        if set(item) != allowed_keys:
            raise SchemaError("결정 항목의 키가 계약과 다릅니다.")
        index = item["sentence_index"]
        if not is_int(index):
            raise SchemaError("sentence_index가 정수가 아닙니다.")
        if index in seen:
            raise SchemaError(f"결정 sentence_index가 중복됩니다: {index}")
        seen.add(index)
        decision = {
            "sentence_index": index,
            "action": action,
            "reason": _reason(item["reason"]),
        }
        if action == "move":
            to_index = item["to_index"]
            if not is_int(to_index):
                raise SchemaError("to_index가 정수가 아닙니다.")
            if not (0 < to_index < sentence_count) or to_index == index:
                raise SchemaError("to_index가 유효한 다른 문장 index가 아닙니다.")
            if to_index <= context_start or to_index >= context_end:
                raise SchemaError("to_index가 문맥 밖이거나 문맥의 첫 문장입니다.")
            decision["to_index"] = to_index
        parsed.append(decision)
    if seen != set(candidate_indices) or len(parsed) != len(candidate_indices):
        raise SchemaError("반환 후보 집합이 요청과 다릅니다.")
    parsed.sort(key=lambda item: item["sentence_index"])
    return {"decisions": parsed}


def parse_verify_response(text: str) -> dict:
    payload = parse_object(text)
    if set(payload) != {"action", "reason"}:
        raise SchemaError("action과 reason만 허용됩니다.")
    if payload["action"] not in {"keep", "merge"}:
        raise SchemaError("action이 keep 또는 merge가 아닙니다.")
    return {"action": payload["action"], "reason": _reason(payload["reason"])}


def convert_chunk(video_id: str, position: int, chunk: object) -> tuple[dict | None, list[str]]:
    prefix = f"{video_id}: chunks[{position}]"
    if not isinstance(chunk, dict):
        return None, [f"{prefix}가 객체가 아닙니다."]
    problems: list[str] = []
    text = chunk.get("text", None)
    language = chunk.get("language", None)
    source_index = chunk.get("index", None)
    items = chunk.get("items", None)
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
        if isinstance(start, bool) or isinstance(end, bool):
            problems.append(f"{item_prefix} 시각에 boolean이 있습니다.")
            continue
        if not is_finite_number(start) or not is_finite_number(end):
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


def validate_alignment(video_id: str, payload: object) -> tuple[list[str], list[dict], list, str | None]:
    if not isinstance(payload, dict):
        return [f"{video_id}: 문장 파일이 객체가 아닙니다."], [], [], None
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
        return problems, [], omitted, policy if isinstance(policy, str) else None
    return [], sentences, omitted, policy


def validate_metadata(video_id: str, payload: object) -> tuple[list[str], dict]:
    if not isinstance(payload, dict):
        return [f"{video_id}: metadata가 객체가 아닙니다."], {}
    problems: list[str] = []
    title = payload.get("title")
    source_url = payload.get("source_url")
    offset = payload.get("audio_to_video_offset_seconds")
    if not isinstance(title, str) or title == "":
        problems.append(f"{video_id}: title이 비어 있지 않은 문자열이 아닙니다.")
        title = None
    if not isinstance(source_url, str) or source_url == "":
        problems.append(f"{video_id}: source_url이 비어 있지 않은 문자열이 아닙니다.")
        source_url = None
    if isinstance(offset, bool) or not is_finite_number(offset):
        problems.append(f"{video_id}: audio_to_video_offset_seconds가 유한한 수가 아닙니다.")
        offset = None
    if "duration_seconds" not in payload or payload.get("duration_seconds") is None:
        duration = None
    elif not is_finite_number(payload.get("duration_seconds")):
        problems.append(f"{video_id}: duration_seconds가 유한한 수가 아닙니다.")
        duration = None
    else:
        duration = payload["duration_seconds"]
    return problems, {
        "title": title,
        "source_url": source_url,
        "audio_to_video_offset_seconds": offset,
        "duration_seconds": duration,
    }


def load_video(data_root: Path, video_id: str, alignment_path: Path) -> tuple[dict | None, list[str]]:
    try:
        payload = json.loads(alignment_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"{video_id}: 문장 파일을 읽지 못했습니다. {exc}"]
    metadata_path = data_root / video_id / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"{video_id}: metadata를 읽지 못했습니다. {exc}"]
    sentence_problems, sentences, omitted, policy = validate_alignment(video_id, payload)
    metadata_problems, meta_info = validate_metadata(video_id, metadata)
    problems = sentence_problems + metadata_problems
    if problems:
        return None, problems
    return (
        {
            "video_id": video_id,
            "source_url": meta_info["source_url"],
            "title": meta_info["title"],
            "duration_seconds": meta_info["duration_seconds"],
            "audio_to_video_offset_seconds": meta_info["audio_to_video_offset_seconds"],
            "sentence_policy_id": policy,
            "omitted_chunks": omitted,
            "sentences": sentences,
            "alignment_path": alignment_path,
            "metadata_path": metadata_path,
        },
        [],
    )


def load_all(data_root: Path) -> tuple[list[str], list[dict], list[str]]:
    paths = sorted(
        data_root.glob("*/audio/alignment_sentences.json"),
        key=lambda path: path.parent.parent.name,
    )
    seen: list[str] = []
    videos: list[dict] = []
    problems: list[str] = []
    for path in paths:
        video_id = path.parent.parent.name
        seen.append(video_id)
        video, video_problems = load_video(data_root, video_id, path)
        problems.extend(video_problems)
        if video is not None:
            videos.append(video)
    return seen, videos, problems


def build_chunks(texts: list[str], max_sentences: int, max_chars: int, overlap: int) -> list[dict]:
    sentence_count = len(texts)
    chunks: list[dict] = []
    start = 0
    while True:
        end = start
        chars = 0
        while end < sentence_count and (end - start) < max_sentences and chars + len(texts[end]) <= max_chars:
            chars += len(texts[end])
            end += 1
        if end == start:
            raise BudgetFailure(f"문장 {start}의 길이가 {max_chars}자를 넘습니다.")
        chunks.append(
            {
                "chunk_index": len(chunks),
                "sentence_start": start,
                "sentence_end": end,
                "char_count": chars,
                "call_id": None,
            }
        )
        if end == sentence_count:
            return chunks
        start = max(start + 1, end - overlap)


def comparable_chunk_indices(boundary: int, chunks: list[dict]) -> list[int]:
    return sorted(
        chunk["chunk_index"]
        for chunk in chunks
        if chunk["sentence_start"] < boundary < chunk["sentence_end"]
    )


def disagreement_indices(chunks: list[dict], candidates: list[dict]) -> set[int]:
    found: set[int] = set()
    for candidate in candidates:
        boundary = candidate["sentence_index"]
        comparable = comparable_chunk_indices(boundary, chunks)
        if len(comparable) < 2:
            continue
        proposed = {source["chunk_index"] for source in candidate["sources"]}
        proposed_here = [chunk_index for chunk_index in comparable if chunk_index in proposed]
        if proposed_here and len(proposed_here) != len(comparable):
            found.add(boundary)
    return found


def connected_groups(indices: list[int], disagreements: set[int], near_distance: int) -> list[list[int]]:
    ordered = sorted(set(indices))
    parent = {index: index for index in ordered}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for pos, left in enumerate(ordered):
        for right in ordered[pos + 1 :]:
            distance = right - left
            if distance > near_distance:
                break
            if 1 <= distance <= near_distance:
                union(left, right)
    groups: dict[int, list[int]] = {}
    for index in ordered:
        groups.setdefault(find(index), []).append(index)
    clusters: list[list[int]] = []
    for members in groups.values():
        members = sorted(members)
        if len(members) >= 2 or members[0] in disagreements:
            clusters.append(members)
    clusters.sort(key=lambda members: members[0])
    return clusters


def build_conflict_clusters(
    chunks: list[dict],
    candidates: list[dict],
    near_distance: int,
    context_pad: int,
    sentence_count: int,
) -> list[dict]:
    disagreements = disagreement_indices(chunks, candidates)
    groups = connected_groups(
        [candidate["sentence_index"] for candidate in candidates],
        disagreements,
        near_distance,
    )
    clusters = []
    for members in groups:
        clusters.append(
            {
                "cluster_index": len(clusters),
                "sentence_indices": members,
                "disagreement_indices": [index for index in members if index in disagreements],
                "context_start": max(0, members[0] - context_pad),
                "context_end": min(sentence_count, members[-1] + context_pad + 1),
                "decisions": None,
                "call_id": None,
            }
        )
    return clusters


def merge_candidates(proposals: list[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = {}
    for proposal in proposals:
        grouped.setdefault(proposal["sentence_index"], []).append(
            {
                "chunk_index": proposal["chunk_index"],
                "reason": proposal["reason"],
                "call_id": proposal["call_id"],
            }
        )
    candidates = []
    for index in sorted(grouped):
        sources = sorted(grouped[index], key=lambda source: (source["chunk_index"], source["call_id"] or ""))
        candidates.append({"sentence_index": index, "sources": sources})
    return candidates


def candidate_line(candidate: dict, chunks: list[dict]) -> str:
    proposed = sorted(source["chunk_index"] for source in candidate["sources"])
    comparable = comparable_chunk_indices(candidate["sentence_index"], chunks)
    payload = {
        "sentence_index": candidate["sentence_index"],
        "proposed_chunks": proposed,
        "not_proposed_comparable_chunks": [chunk_index for chunk_index in comparable if chunk_index not in proposed],
        "reasons": [
            {"chunk_index": source["chunk_index"], "reason": source["reason"]}
            for source in sorted(candidate["sources"], key=lambda source: source["chunk_index"])
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def apply_decisions(candidates: list[dict], clusters: list[dict], sentence_count: int) -> tuple[list[int], list[dict]]:
    by_index = {candidate["sentence_index"]: candidate for candidate in candidates}
    conflict_ids: set[int] = set()
    for cluster in clusters:
        conflict_ids.update(cluster["sentence_indices"])
    grouped: dict[int, list[dict]] = {}

    def add(final_index: int, contribution: dict) -> None:
        if not is_int(final_index) or not (0 < final_index < sentence_count):
            raise StructureFailure(f"적용 후 경계가 유효하지 않습니다: {final_index}")
        grouped.setdefault(final_index, []).append(contribution)

    for candidate in candidates:
        if candidate["sentence_index"] in conflict_ids:
            continue
        add(
            candidate["sentence_index"],
            {
                "source_sentence_index": candidate["sentence_index"],
                "sources": candidate["sources"],
                "decision": None,
                "call_id": None,
            },
        )
    for cluster in clusters:
        decisions = cluster["decisions"] or []
        decision_map = {decision["sentence_index"]: decision for decision in decisions}
        if set(decision_map) != set(cluster["sentence_indices"]) or len(decision_map) != len(decisions):
            raise StructureFailure("충돌 결정이 묶음 후보와 일치하지 않습니다.")
        for source_index in cluster["sentence_indices"]:
            decision = decision_map[source_index]
            if decision["action"] == "drop":
                continue
            if decision["action"] == "keep":
                final_index = source_index
            elif decision["action"] == "move":
                final_index = decision["to_index"]
            else:
                raise StructureFailure("알 수 없는 충돌 결정입니다.")
            add(
                final_index,
                {
                    "source_sentence_index": source_index,
                    "sources": by_index[source_index]["sources"],
                    "decision": decision,
                    "call_id": cluster["call_id"],
                },
            )
    boundaries = sorted(grouped)
    provenance = [
        {"sentence_index": index, "contributions": grouped[index]}
        for index in boundaries
    ]
    return boundaries, provenance


def choose_context(
    sentences: list[dict],
    left_start: int,
    boundary: int,
    right_end: int,
    max_sentences: int,
    max_chars: int,
    window: int,
) -> tuple[dict, list[dict], list[dict]]:
    left = sentences[left_start:boundary]
    right = sentences[boundary:right_end]
    if not left or not right:
        raise StructureFailure(f"경계 {boundary}의 검증 구간이 비어 있습니다.")

    def total_chars(left_part: list[dict], right_part: list[dict]) -> int:
        return sum(len(sentence["text"]) for sentence in left_part) + sum(len(sentence["text"]) for sentence in right_part)

    full_chars = total_chars(left, right)
    if len(left) + len(right) <= max_sentences and full_chars <= max_chars:
        context = {
            "kind": "full",
            "left_start": left_start,
            "left_end": boundary,
            "right_start": boundary,
            "right_end": right_end,
            "char_count": full_chars,
        }
        return context, left, right
    left_part = list(left[-window:])
    right_part = list(right[:window])
    while total_chars(left_part, right_part) > max_chars:
        can_trim_left = len(left_part) > 1
        can_trim_right = len(right_part) > 1
        if not can_trim_left and not can_trim_right:
            raise BudgetFailure(f"경계 {boundary}의 최소 문맥이 {max_chars}자를 넘습니다.")
        left_distance = boundary - 1 - left_part[0]["index"] if can_trim_left else None
        right_distance = right_part[-1]["index"] - boundary if can_trim_right else None
        if can_trim_right and (left_distance is None or right_distance >= left_distance):
            right_part = right_part[:-1]
        else:
            left_part = left_part[1:]
    context = {
        "kind": "window12",
        "left_start": left_part[0]["index"],
        "left_end": left_part[-1]["index"] + 1,
        "right_start": right_part[0]["index"],
        "right_end": right_part[-1]["index"] + 1,
        "char_count": total_chars(left_part, right_part),
    }
    return context, left_part, right_part


def build_units(sentences: list[dict], boundaries: list[int], verification_by_index: dict[int, dict]) -> list[dict]:
    sentence_count = len(sentences)
    if sentence_count == 0:
        return []
    points = [0, *boundaries, sentence_count]
    units = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        group = sentences[start:end]
        if index == 0:
            reasons = ["영상 시작"]
            verification = None
        else:
            verification = verification_by_index[start]
            reasons = [verification["reason"]]
        units.append(
            {
                "index": index,
                "sentence_start": start,
                "sentence_end": end,
                "start_time": group[0]["start_time"] if group else None,
                "end_time": group[-1]["end_time"] if group else None,
                "start_reasons": reasons,
                "verification": verification,
                "sentences": group,
            }
        )
    return units


def validate_structure(sentences: list[dict], units: list[dict], boundaries: list[int]) -> list[str]:
    problems: list[str] = []
    sentence_count = len(sentences)
    if sentence_count == 0:
        if units or boundaries:
            problems.append("빈 입력에 단위 또는 경계가 있습니다.")
        return problems
    seen: set[int] = set()
    for boundary in boundaries:
        if not is_int(boundary) or not (0 < boundary < sentence_count) or boundary in seen:
            problems.append(f"최종 경계가 유효한 고유 정수가 아닙니다: {boundary}")
        elif seen and boundary < max(seen):
            problems.append("최종 경계가 오름차순이 아닙니다.")
        if is_int(boundary):
            seen.add(boundary)
    if len(units) != len(boundaries) + 1:
        problems.append("단위 수가 최종 경계와 맞지 않습니다.")
    cursor = 0
    for unit_index, unit in enumerate(units):
        start = unit.get("sentence_start")
        end = unit.get("sentence_end")
        if start != cursor:
            problems.append(f"단위 {unit_index}가 이전 구간에 이어지지 않습니다.")
        if not is_int(start) or not is_int(end) or end <= start:
            problems.append(f"단위 {unit_index}가 빈 구간이거나 구간이 잘못되었습니다.")
            continue
        expected = sentences[start:end]
        if unit.get("sentences") != expected:
            problems.append(f"단위 {unit_index}의 문장 원문, 순서, 시각 또는 출처가 입력과 다릅니다.")
        elif expected and (
            unit.get("start_time") != expected[0]["start_time"] or unit.get("end_time") != expected[-1]["end_time"]
        ):
            problems.append(f"단위 {unit_index}의 시각이 문장 시각과 다릅니다.")
        if unit_index == 0:
            if start != 0:
                problems.append("첫 단위가 문장 0에서 시작하지 않습니다.")
        elif unit_index - 1 >= len(boundaries) or start != boundaries[unit_index - 1]:
            problems.append(f"단위 {unit_index}의 시작이 최종 경계와 다릅니다.")
        cursor = end
    if cursor != sentence_count:
        problems.append("단위가 마지막 문장까지 덮지 않습니다.")
    return problems


def empty_document(video: dict) -> dict:
    return {
        "video_id": video["video_id"],
        "source_url": video.get("source_url"),
        "title": video.get("title"),
        "status": "failed",
        "problems": [],
        "input": {
            "alignment_sentences": "audio/alignment_sentences.json",
            "sentence_policy_id": video.get("sentence_policy_id"),
            "time_reference": "audio_start",
            "sentence_count": len(video.get("sentences") or []),
            "omitted_chunks": video.get("omitted_chunks") or [],
        },
        "time": {
            "audio_to_video_offset_seconds": video.get("audio_to_video_offset_seconds"),
            "unit_time_reference": "audio_start",
        },
        "settings": settings_payload(),
        "model": model_payload(),
        "prompts": prompts_payload(),
        "chunks": [],
        "proposals": [],
        "candidates": [],
        "conflicts": [],
        "boundaries_after_conflict": [],
        "boundary_provenance": [],
        "verifications": [],
        "boundaries_final": [],
        "units": [],
        "validation": {"ok": None, "problems": []},
        "calls": [],
        "last_stage": None,
    }


def fail_document(document: dict, stage: str, message: str) -> dict:
    document["status"] = "failed"
    document["problems"].append(message)
    document["last_stage"] = stage
    print(f"{document['video_id']} {document['status']}", flush=True)
    return document


def finish_ok(document: dict, stage: str) -> dict:
    document["status"] = "ok"
    document["last_stage"] = stage
    print(f"{document['video_id']} {document['status']}", flush=True)
    return document


class InferClient:
    def __init__(self, base_url: str = INFER_BASE_URL, timeout: int = TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    def post(self, stage: str, attempt: int, body: dict) -> dict:
        call_id = str(uuid.uuid4())
        url = f"{self.base_url}/infer?{urllib.parse.urlencode({'model': INFER_MODEL_ID})}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        http_status = None
        response_body = None
        finish_reason = None
        usage = None
        response_content = None
        error = None
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                http_status = response.status
                response_body = raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            http_status = exc.code
            raw = exc.read()
            response_body = raw.decode("utf-8", errors="replace") if raw else ""
            error = f"HTTP {exc.code}"
        except TimeoutError:
            error = "timeout"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                error = "timeout"
            else:
                error = f"network: {exc.reason}"
        elapsed = time.perf_counter() - started
        if error is None and http_status != 200:
            error = f"HTTP {http_status}"
        if error is None:
            try:
                payload = json.loads(response_body)
            except json.JSONDecodeError:
                error = "응답 JSON 파싱 실패"
                payload = None
            if error is None:
                if not isinstance(payload, dict):
                    error = "응답이 객체가 아닙니다."
                else:
                    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
                    response_content, content_error = extract_content(payload)
                    finish_reason = extract_finish_reason(payload)
                    if content_error:
                        error = content_error
                    elif response_content is None or response_content == "":
                        error = "응답 텍스트가 없습니다."
                        response_content = None
                    elif finish_reason != "stop":
                        error = f"finish_reason={finish_reason}"
        return {
            "call_id": call_id,
            "stage": stage,
            "attempt": attempt,
            "http_status": http_status,
            "elapsed_seconds": elapsed,
            "finish_reason": finish_reason,
            "usage": usage,
            "request_body": body,
            "response_body": response_body,
            "response_content": response_content,
            "parsed": None,
            "error": error,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def extract_finish_reason(payload: dict) -> object:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    return choices[0].get("finish_reason")


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


def request_body(prompt: str) -> dict:
    return {
        "model": BODY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": ENABLE_THINKING},
    }


def request_stage(client, calls: list[dict], video_id: str, stage: str, number: int, body: dict, validate) -> dict:
    last_error = "호출이 실패했습니다."
    for attempt in (1, 2):
        record = client.post(stage, attempt, body)
        calls.append(record)
        if record["error"] is None:
            try:
                record["parsed"] = validate(record["response_content"])
            except SchemaError as exc:
                record["error"] = str(exc)
                record["parsed"] = None
        if record["error"] is None:
            print(f"{video_id} {stage} {number} success", flush=True)
            return record
        last_error = record["error"]
        print(f"{video_id} {stage} {number} failure", flush=True)
    detail = f"{stage} {number}: {last_error}"
    response_body = calls[-1].get("response_body") if calls else None
    if isinstance(response_body, str) and response_body:
        detail = f"{detail} body={response_body[:500]}"
    raise CallFailed(detail)


def process_video(video: dict, client) -> dict:
    document = empty_document(video)
    sentences = video["sentences"]
    sentence_count = len(sentences)
    if sentence_count == 0:
        document["validation"] = {"ok": True, "problems": []}
        return finish_ok(document, "validation")
    try:
        chunks = build_chunks(
            [sentence["text"] for sentence in sentences],
            MAX_SENTENCES,
            MAX_CHARS,
            OVERLAP_SENTENCES,
        )
    except BudgetFailure as exc:
        return fail_document(document, "chunk", str(exc))
    document["chunks"] = chunks
    document["last_stage"] = "boundary"
    proposals = document["proposals"]
    try:
        for chunk in chunks:
            if chunk["sentence_end"] - chunk["sentence_start"] <= 1:
                print(f"{video['video_id']} boundary {chunk['chunk_index']} skip", flush=True)
                continue
            allowed = set(range(chunk["sentence_start"] + 1, chunk["sentence_end"]))
            prompt = render(
                prompts.BOUNDARY,
                {"문장 줄": sentence_block(sentences[chunk["sentence_start"] : chunk["sentence_end"]])},
            )
            record = request_stage(
                client,
                document["calls"],
                video["video_id"],
                "boundary",
                chunk["chunk_index"],
                request_body(prompt),
                lambda text, allowed=allowed: parse_boundary_response(text, allowed),
            )
            chunk["call_id"] = record["call_id"]
            for item in record["parsed"]["boundaries"]:
                proposals.append(
                    {
                        "sentence_index": item["sentence_index"],
                        "chunk_index": chunk["chunk_index"],
                        "reason": item["reason"],
                        "call_id": record["call_id"],
                    }
                )
        candidates = merge_candidates(proposals)
        document["candidates"] = candidates
        document["last_stage"] = "conflict"
        clusters = build_conflict_clusters(
            chunks,
            candidates,
            NEAR_DISTANCE,
            CONFLICT_CONTEXT_SENTENCES,
            sentence_count,
        )
        document["conflicts"] = clusters
        candidate_map = {candidate["sentence_index"]: candidate for candidate in candidates}
        for cluster in clusters:
            selected = [candidate_map[index] for index in cluster["sentence_indices"]]
            prompt = render(
                prompts.CONFLICT,
                {
                    "후보 줄": "\n".join(candidate_line(candidate, chunks) for candidate in selected),
                    "문맥 문장 줄": sentence_block(sentences[cluster["context_start"] : cluster["context_end"]]),
                },
            )
            record = request_stage(
                client,
                document["calls"],
                video["video_id"],
                "conflict",
                cluster["cluster_index"],
                request_body(prompt),
                lambda text, cluster=cluster, sentence_count=sentence_count: parse_conflict_response(
                    text,
                    cluster["sentence_indices"],
                    cluster["context_start"],
                    cluster["context_end"],
                    sentence_count,
                ),
            )
            cluster["decisions"] = record["parsed"]["decisions"]
            cluster["call_id"] = record["call_id"]
        boundaries, provenance = apply_decisions(candidates, clusters, sentence_count)
    except CallFailed as exc:
        if proposals and not document["candidates"]:
            document["candidates"] = merge_candidates(proposals)
        return fail_document(document, document["last_stage"] or "boundary", str(exc))
    except (BudgetFailure, StructureFailure) as exc:
        return fail_document(document, "conflict", str(exc))
    document["boundaries_after_conflict"] = boundaries
    document["boundary_provenance"] = provenance
    document["last_stage"] = "verify"
    final: list[int] = []
    verifications: list[dict] = []
    kept = None
    try:
        for position, boundary in enumerate(boundaries):
            left_start = 0 if kept is None else kept
            right_end = boundaries[position + 1] if position + 1 < len(boundaries) else sentence_count
            context, left_sentences, right_sentences = choose_context(
                sentences,
                left_start,
                boundary,
                right_end,
                MAX_SENTENCES,
                MAX_CHARS,
                VERIFY_WINDOW_SENTENCES,
            )
            prompt = render(
                prompts.VERIFY,
                {
                    "왼쪽 문장 줄": sentence_block(left_sentences),
                    "오른쪽 문장 줄": sentence_block(right_sentences),
                },
            )
            record = request_stage(
                client,
                document["calls"],
                video["video_id"],
                "verify",
                boundary,
                request_body(prompt),
                parse_verify_response,
            )
            verification = {
                "sentence_index": boundary,
                "action": record["parsed"]["action"],
                "reason": record["parsed"]["reason"],
                "context": context,
                "call_id": record["call_id"],
            }
            verifications.append(verification)
            if verification["action"] == "keep":
                final.append(boundary)
                kept = boundary
    except CallFailed as exc:
        document["verifications"] = verifications
        return fail_document(document, "verify", str(exc))
    except BudgetFailure as exc:
        document["verifications"] = verifications
        return fail_document(document, "verify", str(exc))
    document["verifications"] = verifications
    document["boundaries_final"] = final
    verification_by_index = {
        verification["sentence_index"]: verification
        for verification in verifications
        if verification["action"] == "keep"
    }
    try:
        units = build_units(sentences, final, verification_by_index)
    except KeyError as exc:
        return fail_document(document, "units", f"유지 경계의 검증 기록이 없습니다: {exc}")
    document["units"] = units
    problems = validate_structure(sentences, units, final)
    document["validation"] = {"ok": not problems, "problems": problems}
    if problems:
        document["status"] = "failed"
        document["problems"].extend(problems)
        document["last_stage"] = "validation"
        print(f"{video['video_id']} failed", flush=True)
        return document
    return finish_ok(document, "validation")


def archive_outputs(outputs: Path, archive_root: Path) -> Path | None:
    if not outputs.exists():
        return None
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = archive_root / stamp
    suffix = 2
    while destination.exists():
        destination = archive_root / f"{stamp}_{suffix}"
        suffix += 1
    outputs.rename(destination)
    return destination


def conflict_decision_count(document: dict) -> int:
    total = 0
    for cluster in document["conflicts"]:
        decisions = cluster.get("decisions")
        if isinstance(decisions, list):
            total += len(decisions)
    return total


def merge_count(document: dict) -> int:
    return sum(1 for verification in document["verifications"] if verification.get("action") == "merge")


def format_context(context: dict | None) -> str:
    if context is None:
        return "없음"
    return (
        f"{context['kind']} "
        f"[{context['left_start']},{context['left_end']}) / "
        f"[{context['right_start']},{context['right_end']}) "
        f"글자 {context['char_count']}"
    )


def render_review(video: dict, document: dict) -> str:
    duration = video.get("duration_seconds")
    duration_text = "미확인" if duration is None else time_literal(duration)
    lines = [
        f"# {md_escape(document.get('title') or '')}",
        "",
        f"- 상태: {document['status']}",
        f"- 마지막 단계: {document.get('last_stage')}",
    ]
    if document["problems"]:
        lines.append("- problems:")
        lines.extend(f"  - {md_escape(problem)}" for problem in document["problems"])
    else:
        lines.append("- problems: 없음")
    lines.extend(
        [
            f"- URL: {md_escape(document.get('source_url') or '')}",
            f"- video id: {document['video_id']}",
            f"- 영상 길이: {duration_text}",
            f"- 문장 수: {document['input']['sentence_count']}",
            f"- policy id: {document['input'].get('sentence_policy_id')}",
            "",
            "## 경계 변경",
            "",
        ]
    )
    decisions = []
    for cluster in document["conflicts"]:
        cluster_decisions = cluster.get("decisions")
        if not cluster_decisions:
            continue
        for decision in cluster_decisions:
            suffix = f" to_index={decision['to_index']}" if decision["action"] == "move" else ""
            decisions.append(
                f"- {decision['sentence_index']} {decision['action']}{suffix} {md_escape(decision['reason'])}"
            )
    merges = [
        f"- merge {verification['sentence_index']} {md_escape(verification['reason'])}"
        for verification in document["verifications"]
        if verification.get("action") == "merge"
    ]
    keep_count = sum(1 for verification in document["verifications"] if verification.get("action") == "keep")
    if not decisions and not merges:
        lines.append("변경 없음")
    else:
        lines.extend(decisions)
        lines.extend(merges)
    if keep_count:
        lines.append(f"- 2차 keep: {keep_count}")
    lines.extend(["", "## 의미 단위", ""])
    if not document["units"]:
        lines.append("단위 없음")
    for unit in document["units"]:
        verification = unit["verification"]
        lines.extend(
            [
                f"### 단위 {unit['index']}",
                "",
                f"- 문장 구간: [{unit['sentence_start']},{unit['sentence_end']})",
                f"- 시각: {time_literal(unit['start_time'])}-{time_literal(unit['end_time'])}",
                f"- 시작 근거: {md_escape(json.dumps(unit['start_reasons'], ensure_ascii=False))}",
                f"- 검증 상태: {verification['action'] if verification else '없음'}",
                f"- 검증 문맥: {format_context(verification['context'] if verification else None)}",
                "",
            ]
        )
        for sentence in unit["sentences"]:
            lines.append(f"- {md_escape(sentence_line(sentence))}")
        lines.append("")
    lines.extend(
        [
            "## 사용자 입력",
            "",
            "- 판단:",
            "- 문제:",
            "- 수정 요청:",
            "",
        ]
    )
    return "\n".join(lines)


def render_index(documents: list[dict]) -> str:
    lines = [
        "# T1 의미 단위",
        "",
        "| 제목 | 상태 | 문장 수 | 단위 수 | 충돌 결정 수 | 2차 merge 수 | review |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for document in documents:
        title = md_escape(document.get("title") or "").replace("\n", " ")
        link = f"{document['video_id']}/review.md"
        lines.append(
            "| "
            + " | ".join(
                [
                    title,
                    str(document["status"]),
                    str(document["input"]["sentence_count"]),
                    str(len(document["units"])),
                    str(conflict_decision_count(document)),
                    str(merge_count(document)),
                    f"[review]({link})",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def call_seconds(calls: list[dict]) -> float:
    return sum(call["elapsed_seconds"] for call in calls if isinstance(call.get("elapsed_seconds"), (int, float)))


def write_run(path: Path, run: dict) -> None:
    dump_json(path, run)


def report_policies(videos: list[dict]) -> None:
    policies = {video["sentence_policy_id"] for video in videos}
    if len(policies) <= 1:
        return
    print("sentence_policy_id가 영상마다 다릅니다. 각 원값으로 진행합니다.", flush=True)
    for video in videos:
        print(f"{video['video_id']} {video['sentence_policy_id']}", flush=True)


def preflight_video(video: dict, client) -> dict:
    chunks = build_chunks(
        [sentence["text"] for sentence in video["sentences"]],
        MAX_SENTENCES,
        MAX_CHARS,
        OVERLAP_SENTENCES,
    )
    chunk = next((item for item in chunks if item["sentence_end"] - item["sentence_start"] > 1), None)
    if chunk is None:
        raise CallFailed("경계 탐색 호출을 만들 문장 청크가 없습니다.")
    allowed = set(range(chunk["sentence_start"] + 1, chunk["sentence_end"]))
    prompt = render(
        prompts.BOUNDARY,
        {"문장 줄": sentence_block(video["sentences"][chunk["sentence_start"] : chunk["sentence_end"]])},
    )
    calls: list[dict] = []
    record = request_stage(
        client,
        calls,
        video["video_id"],
        "boundary",
        chunk["chunk_index"],
        request_body(prompt),
        lambda text, allowed=allowed: parse_boundary_response(text, allowed),
    )
    return {
        "verdict": "pass",
        "video_id": video["video_id"],
        "chunk_index": chunk["chunk_index"],
        "sentence_start": chunk["sentence_start"],
        "sentence_end": chunk["sentence_end"],
        "call": record,
    }


def execute(data_root: Path, outputs_dir: Path, archive_dir: Path, client=None, require_eight: bool = True) -> int:
    started = time.perf_counter()
    started_at = utc_now()
    seen, videos, problems = load_all(data_root)
    if require_eight and len(seen) != 8:
        problems.append(f"입력이 8개가 아닙니다: {len(seen)}개 ({', '.join(seen)})")
    if require_eight:
        found = set(seen)
        for video_id in REQUIRED_VIDEO_IDS:
            if video_id not in found:
                problems.append(f"{video_id}가 입력에 없습니다.")
    if problems:
        for problem in problems:
            print(problem, flush=True)
        return 1
    report_policies(videos)
    if client is None:
        client = InferClient()
    try:
        preflight = preflight_video(videos[0], client)
    except (CallFailed, BudgetFailure, StructureFailure) as exc:
        print(f"preflight 실패: {exc}", flush=True)
        return 1
    moved = archive_outputs(outputs_dir, archive_dir)
    if moved is not None:
        print(f"outputs 이동: {display_path(moved)}", flush=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    inputs = [
        {
            "video_id": video["video_id"],
            "alignment_sentences": display_path(video["alignment_path"]),
            "metadata": display_path(video["metadata_path"]),
            "alignment_sha256": sha256_file(video["alignment_path"]),
            "metadata_sha256": sha256_file(video["metadata_path"]),
        }
        for video in videos
    ]
    documents = []
    video_rows = []

    def publish() -> None:
        run = {
            "started_at": started_at,
            "ended_at": utc_now(),
            "inputs": inputs,
            "settings": settings_payload(),
            "prompts": prompts_payload(),
            "preflight": preflight,
            "videos": video_rows,
            "wall_seconds": time.perf_counter() - started,
            "call_seconds_sum": call_seconds([preflight["call"]])
            + sum(row["call_seconds_sum"] for row in video_rows),
        }
        if documents:
            (outputs_dir / "index.md").write_text(render_index(documents), encoding="utf-8")
        write_run(outputs_dir / "run.json", run)

    publish()
    for video in videos:
        video_started = time.perf_counter()
        document = process_video(video, client)
        documents.append(document)
        video_dir = outputs_dir / video["video_id"]
        dump_json(video_dir / "semantic_units.json", document)
        (video_dir / "review.md").write_text(render_review(video, document), encoding="utf-8")
        video_rows.append(
            {
                "video_id": video["video_id"],
                "status": document["status"],
                "sentence_count": document["input"]["sentence_count"],
                "unit_count": len(document["units"]),
                "wall_seconds": time.perf_counter() - video_started,
                "call_seconds_sum": call_seconds(document["calls"]),
            }
        )
        publish()
    failed = [row["video_id"] for row in video_rows if row["status"] != "ok"]
    if failed:
        print("실패: " + ", ".join(failed), flush=True)
        return 1
    print(f"{len(video_rows)}개 처리 성공", flush=True)
    return 0


def make_sentence(index: int, text: str, start: float | None = None, end: float | None = None) -> dict:
    start_time = float(index) if start is None else start
    end_time = start_time + 0.5 if end is None else end
    return {
        "index": index,
        "source_chunk_index": index,
        "start_time": start_time,
        "end_time": end_time,
        "text": text,
        "language": "Korean",
    }


def make_sentences(count: int, text: str) -> list[dict]:
    return [make_sentence(index, text) for index in range(count)]


def check_chunks() -> None:
    ranges = [
        (chunk["sentence_start"], chunk["sentence_end"])
        for chunk in build_chunks(["a"] * 10, 4, 6000, 2)
    ]
    assert ranges == [(0, 4), (2, 6), (4, 8), (6, 10)]
    limited = [
        (chunk["sentence_start"], chunk["sentence_end"])
        for chunk in build_chunks(["aaaa", "bbbb", "cc", "d", "eeeee"], 10, 6, 1)
    ]
    assert limited == [(0, 1), (1, 3), (2, 4), (3, 5)]
    try:
        build_chunks(["가" * 6001], MAX_SENTENCES, MAX_CHARS, OVERLAP_SENTENCES)
    except BudgetFailure:
        return
    raise AssertionError("6000자를 넘는 문장을 자르지 않고 실패해야 합니다.")


def check_clusters() -> None:
    first_sentence_chunks = [
        {"chunk_index": 0, "sentence_start": 0, "sentence_end": 40},
        {"chunk_index": 1, "sentence_start": 32, "sentence_end": 72},
    ]
    assert comparable_chunk_indices(32, first_sentence_chunks) == [0]

    outside = [
        {"chunk_index": 0, "sentence_start": 0, "sentence_end": 5},
        {"chunk_index": 1, "sentence_start": 5, "sentence_end": 12},
    ]
    outside_candidates = [_candidate(4, [0]), _candidate(6, [1])]
    outside_clusters = build_conflict_clusters(outside, outside_candidates, 2, 8, 40)
    assert [cluster["sentence_indices"] for cluster in outside_clusters] == [[4, 6]]

    mixed_chunks = [
        {"chunk_index": 0, "sentence_start": 0, "sentence_end": 20},
        {"chunk_index": 1, "sentence_start": 8, "sentence_end": 30},
        {"chunk_index": 2, "sentence_start": 40, "sentence_end": 60},
        {"chunk_index": 3, "sentence_start": 45, "sentence_end": 70},
    ]
    mixed_candidates = [
        _candidate(3, [0]),
        _candidate(10, [0, 1]),
        _candidate(12, [0, 1]),
        _candidate(14, [0, 1]),
        _candidate(50, [2]),
    ]
    mixed = build_conflict_clusters(mixed_chunks, mixed_candidates, 2, 8, 80)
    assert [cluster["sentence_indices"] for cluster in mixed] == [[10, 12, 14], [50]]
    assert mixed[0]["disagreement_indices"] == []
    assert mixed[1]["disagreement_indices"] == [50]
    assert build_conflict_clusters(first_sentence_chunks, [_candidate(32, [0])], 2, 8, 80) == []

    adjacent = build_conflict_clusters(
        first_sentence_chunks,
        [_candidate(32, [0]), _candidate(34, [0, 1])],
        2,
        8,
        80,
    )
    assert [cluster["sentence_indices"] for cluster in adjacent] == [[32, 34]]


def _candidate(index: int, chunk_indexes: list[int], reason: str | None = None) -> dict:
    return {
        "sentence_index": index,
        "sources": [
            {
                "chunk_index": chunk_index,
                "reason": reason or f"r{chunk_index}",
                "call_id": f"c{chunk_index}",
            }
            for chunk_index in chunk_indexes
        ],
    }


def check_contract_and_empty() -> None:
    bool_problems, _, _, _ = validate_alignment(
        "bad",
        {
            "time_reference": "audio_start",
            "overlap_deduplicated": True,
            "sentence_policy_id": "policy",
            "chunks": [
                {
                    "index": 0,
                    "text": "문장",
                    "language": "Korean",
                    "items": [{"start_time": True, "end_time": 1}],
                }
            ],
            "omitted_chunks": [],
        },
    )
    assert any("boolean" in problem for problem in bool_problems)
    empty_item_problems, _, _, _ = validate_alignment(
        "bad",
        {
            "time_reference": "audio_start",
            "overlap_deduplicated": True,
            "sentence_policy_id": "policy",
            "chunks": [{"index": 0, "text": "문장", "language": "Korean", "items": []}],
            "omitted_chunks": [],
        },
    )
    assert empty_item_problems
    reversed_problems, _, _, _ = validate_alignment(
        "bad",
        {
            "time_reference": "audio_start",
            "overlap_deduplicated": True,
            "sentence_policy_id": "policy",
            "chunks": [
                {
                    "index": 0,
                    "text": "문장",
                    "language": "Korean",
                    "items": [{"start_time": 2, "end_time": 1}],
                }
            ],
            "omitted_chunks": [],
        },
    )
    assert reversed_problems
    empty_problems, empty_sentences, _, _ = validate_alignment(
        "empty",
        {
            "time_reference": "audio_start",
            "overlap_deduplicated": True,
            "sentence_policy_id": "policy",
            "chunks": [],
            "omitted_chunks": [{"index": 1}],
        },
    )
    assert empty_problems == []
    assert empty_sentences == []

    import io
    import tempfile
    from contextlib import redirect_stdout

    ids = [
        "7EJ-xotXiSE",
        "CvuE-jyg-N8",
        "Fw-8hZhnH3Q",
        "H2WN130OzLg",
        "bjDkfjSmXVo",
        "hGVL9XRU9IQ",
        "iEMuJiVY9Oc",
        "iVelddb1qQ4",
    ]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        data_root = root / "data"
        outputs = root / "outputs"
        archive = root / "archive"
        outputs.mkdir()
        (outputs / "marker.txt").write_text("keep", encoding="utf-8")
        for video_id in ids:
            _write_fixture(data_root, video_id, overlap=video_id != "7EJ-xotXiSE")
        client = _GuardClient()
        with redirect_stdout(io.StringIO()) as buffer:
            code = execute(data_root, outputs, archive, client)
        assert code == 1
        assert client.posts == 0
        assert (outputs / "marker.txt").read_text(encoding="utf-8") == "keep"
        assert not archive.exists()
        assert "overlap_deduplicated" in buffer.getvalue()

    video = _manual_video([])
    document = process_video(video, _GuardClient())
    assert document["status"] == "ok"
    assert document["validation"] == {"ok": True, "problems": []}
    assert document["chunks"] == []
    assert document["proposals"] == []
    assert document["candidates"] == []
    assert document["conflicts"] == []
    assert document["boundaries_final"] == []
    assert document["units"] == []
    assert document["calls"] == []
    assert document["input"]["omitted_chunks"] == [{"index": 3}]

    single = _manual_video([make_sentence(0, "안녕하세요.")])
    single_document = process_video(single, _GuardClient())
    assert single_document["status"] == "ok"
    assert single_document["calls"] == []
    assert single_document["chunks"][0]["call_id"] is None
    assert len(single_document["units"]) == 1
    assert single_document["validation"]["ok"] is True

    overlong = _manual_video([make_sentence(0, "가" * 6001)])
    overlong_document = process_video(overlong, _GuardClient())
    assert overlong_document["status"] == "failed"
    assert overlong_document["calls"] == []
    assert overlong_document["units"] == []
    assert overlong_document["validation"]["ok"] is None


def _write_fixture(data_root: Path, video_id: str, overlap: bool) -> None:
    audio = data_root / video_id / "audio"
    audio.mkdir(parents=True)
    payload = {
        "time_reference": "audio_start",
        "overlap_deduplicated": overlap,
        "sentence_policy_id": "policy-a",
        "chunks": [],
        "omitted_chunks": [],
    }
    (audio / "alignment_sentences.json").write_text(json.dumps(payload), encoding="utf-8")
    metadata = {
        "title": video_id,
        "source_url": f"https://example.invalid/{video_id}",
        "audio_to_video_offset_seconds": 0.0,
        "duration_seconds": 1.0,
    }
    (data_root / video_id / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def _manual_video(sentences: list[dict]) -> dict:
    return {
        "video_id": "manual",
        "source_url": "https://example.invalid/manual",
        "title": "manual",
        "duration_seconds": 1.0,
        "audio_to_video_offset_seconds": 0.0,
        "sentence_policy_id": "policy",
        "omitted_chunks": [{"index": 3}],
        "sentences": sentences,
    }


class _GuardClient:
    def __init__(self) -> None:
        self.posts = 0

    def post(self, stage: str, attempt: int, body: dict) -> dict:
        self.posts += 1
        raise AssertionError("추론이 호출되었습니다.")


def check_apply() -> None:
    candidates = [
        _candidate(5, [0], "오"),
        _candidate(6, [0], "육"),
        _candidate(10, [1], "십"),
        _candidate(11, [1], "십일"),
        _candidate(40, [2], "마흔"),
    ]
    clusters = [
        {
            "sentence_indices": [5, 6],
            "call_id": "ca",
            "decisions": [
                {"sentence_index": 5, "action": "drop", "reason": "제거"},
                {"sentence_index": 6, "action": "move", "to_index": 5, "reason": "여섯이동"},
            ],
        },
        {
            "sentence_indices": [10, 11],
            "call_id": "cb",
            "decisions": [
                {"sentence_index": 10, "action": "move", "to_index": 5, "reason": "열이동안"},
                {"sentence_index": 11, "action": "drop", "reason": "버림"},
            ],
        },
    ]
    boundaries, provenance = apply_decisions(candidates, clusters, 50)
    assert boundaries == [5, 40]
    by_index = {item["sentence_index"]: item["contributions"] for item in provenance}
    assert [item["source_sentence_index"] for item in by_index[5]] == [6, 10]
    assert [item["decision"]["reason"] for item in by_index[5]] == ["여섯이동", "열이동안"]
    assert [item["call_id"] for item in by_index[5]] == ["ca", "cb"]
    assert by_index[5][0]["sources"][0]["reason"] == "육"
    assert by_index[40][0]["decision"] is None
    assert by_index[40][0]["call_id"] is None
    assert by_index[40][0]["source_sentence_index"] == 40
    assert by_index[40][0]["sources"][0]["reason"] == "마흔"


def check_window() -> None:
    wide = make_sentences(80, "가")
    context, left, right = choose_context(wide, 0, 40, 80, 40, 6000, 12)
    assert context["kind"] == "window12"
    assert left[-1]["index"] == 39
    assert right[0]["index"] == 40
    assert context["char_count"] <= 6000
    assert context["char_count"] == sum(len(sentence["text"]) for sentence in left + right)

    tight = make_sentences(14, "가" * 2000)
    trimmed, left, right = choose_context(tight, 6, 10, 14, 40, 6000, 12)
    assert trimmed == {
        "kind": "window12",
        "left_start": 8,
        "left_end": 10,
        "right_start": 10,
        "right_end": 11,
        "char_count": 6000,
    }
    assert [sentence["index"] for sentence in left] == [8, 9]
    assert [sentence["index"] for sentence in right] == [10]
    assert left[-1]["text"] == "가" * 2000
    assert right[0]["text"] == "가" * 2000

    minimum = make_sentences(2, "가" * 4000)
    try:
        choose_context(minimum, 0, 1, 2, 40, 6000, 12)
    except BudgetFailure:
        return
    raise AssertionError("최소 문맥이 6000자를 넘으면 실패해야 합니다.")


def check_units() -> None:
    import copy

    sentences = [make_sentence(index, f"문장{index}") for index in range(4)]
    verification = {
        "sentence_index": 2,
        "action": "keep",
        "reason": "전환",
        "context": {
            "kind": "full",
            "left_start": 0,
            "left_end": 2,
            "right_start": 2,
            "right_end": 4,
            "char_count": 8,
        },
        "call_id": "v",
    }
    units = build_units(sentences, [2], {2: verification})
    assert validate_structure(sentences, units, [2]) == []
    assert units[0]["start_reasons"] == ["영상 시작"]
    assert units[0]["verification"] is None
    assert units[1]["start_reasons"] == ["전환"]
    assert units[0]["start_time"] == sentences[0]["start_time"]
    assert units[0]["end_time"] == sentences[1]["end_time"]

    missing = copy.deepcopy(units)
    missing[0]["sentences"] = missing[0]["sentences"][:1]
    assert validate_structure(sentences, missing, [2])
    duplicated = copy.deepcopy(units)
    duplicated[1]["sentences"] = [sentences[2], sentences[2]]
    assert validate_structure(sentences, duplicated, [2])
    reordered = copy.deepcopy(units)
    reordered[0]["sentences"] = [sentences[1], sentences[0]]
    assert validate_structure(sentences, reordered, [2])


def check_parser() -> None:
    rendered = render(prompts.BOUNDARY, {"문장 줄": "[1] 0.0-1.0 문장"})
    assert '{"boundaries":[]}' in rendered
    assert "{문장 줄}" not in rendered
    verified = render(prompts.VERIFY, {"왼쪽 문장 줄": "L", "오른쪽 문장 줄": "R"})
    assert '{"action":"keep","reason":"주제, 대상, 목적 중 무엇이 어떻게 바뀌거나 이어지는지 한 문장"}' in verified
    fenced = parse_boundary_response(
        '```json\n{"boundaries":[{"sentence_index":2,"reason":" 전환 "}]}\n```',
        {2},
    )
    assert fenced["boundaries"][0]["reason"] == " 전환 "
    _expect_schema(
        lambda: parse_conflict_response(
            '{"decisions":[{"sentence_index":1,"action":"keep","reason":"a"},{"sentence_index":2,"action":"drop","reason":"b"}]}',
            [1],
            0,
            10,
            10,
        )
    )
    _expect_schema(lambda: parse_boundary_response('{"boundaries":[{"sentence_index":true,"reason":"a"}]}', {1}))
    _expect_schema(lambda: parse_boundary_response('{"boundaries":[{"sentence_index":1.0,"reason":"a"}]}', {1}))
    _expect_schema(lambda: parse_boundary_response('{"boundaries":[]}\n{"boundaries":[]}', set()))
    _expect_schema(lambda: parse_boundary_response('{"boundaries":[],"boundaries":[]}', set()))


def _expect_schema(function) -> None:
    try:
        function()
    except SchemaError:
        return
    raise AssertionError("스키마 오류를 거절하지 않았습니다.")


def run_checks() -> int:
    checks = [
        ("chunks", check_chunks),
        ("clusters", check_clusters),
        ("contract", check_contract_and_empty),
        ("apply", check_apply),
        ("window", check_window),
        ("units", check_units),
        ("parser", check_parser),
    ]
    failed = False
    for name, function in checks:
        try:
            function()
        except Exception as exc:
            failed = True
            print(f"check {name} failure: {exc}", flush=True)
        else:
            print(f"check {name} ok", flush=True)
    if failed:
        return 1
    print("check ok", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--check"]:
        return run_checks()
    if args:
        print("본 실행 설정을 바꾸는 인자는 없습니다.", flush=True)
        return 2
    return execute(DATA_ROOT, OUTPUTS_DIR, ARCHIVE_DIR)


if __name__ == "__main__":
    sys.exit(main())
