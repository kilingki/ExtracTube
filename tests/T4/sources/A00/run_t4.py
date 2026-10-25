"""T4 frame selection runner. A00/P0 is the first experiment, not an acceptance."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import extract
import prompts
import review

ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "tests" / "T4" / "runs"
B05_ROOT = "tests/T3/runs/20261024T175204Z_bdfca4"
B05_MANIFEST_SHA = "33dfff0fd9b83283dd0975328178f1d20191b5e6ae239fd9c88fa6f618f6be95"
CONFIG_ID = "A00"
LOGIC_VERSION = "t4.frame_selection.observed_pts.v1"
SCHEMA_VERSION = "t4.selected_frames.v1"
INFER_URL = "http://127.0.0.1:8095/infer?model=qwen-vlm"
INFER_MODEL_ID = "qwen-vlm"
BODY_MODEL = "qwen3.8-27b"
MAX_TOKENS = 4096
TIMEOUT_SECONDS = 300
SECOND = Decimal("0.000000001")
CODE_FILES = [
    "tests/T4/prompts.py",
    "tests/T4/extract.py",
    "tests/T4/review.py",
    "tests/T4/run_t4.py",
]
CANDIDATES = [
    ("7EJ-xotXiSE:u0:c0", "7EJ-xotXiSE/candidates/u0_c0.json", "4f4da733caa8d146a292691fa599e11065768e1ee501ce8dea62f15e18f315c1"),
    ("7EJ-xotXiSE:u15:c0", "7EJ-xotXiSE/candidates/u15_c0.json", "26e22ebe6caee13f4777f91faa563d34c68b87429a7a885f99fc6bd4b9e20b6c"),
    ("bjDkfjSmXVo:u12:c0", "bjDkfjSmXVo/candidates/u12_c0.json", "363e4f0950c43e34162413ac1d7f22be954aa3d61b015436287ab36e1c56e9af"),
    ("CvuE-jyg-N8:u43:c0", "CvuE-jyg-N8/candidates/u43_c0.json", "f06f44578d1d9305ca61b8a352f7cd7d7c920256384457a25b5a2971701b02c5"),
    ("Fw-8hZhnH3Q:u0:c0", "Fw-8hZhnH3Q/candidates/u0_c0.json", "7d7f6d3382beb970fea5af6b61c2c4b6825b0f9ce4647232f25af13c660258e2"),
    ("H2WN130OzLg:u25:c0", "H2WN130OzLg/candidates/u25_c0.json", "1609f8bf27758c44830c055b0699cff8d571e3fc5b094de8948f0ae43ed60de1"),
    ("H2WN130OzLg:u7:c0", "H2WN130OzLg/candidates/u7_c0.json", "77a45b3e393822e9f9db64aaeed545308d17df9af58934a506db4a7660ccdc0d"),
    ("hGVL9XRU9IQ:u26:c0", "hGVL9XRU9IQ/candidates/u26_c0.json", "b304dd08f8b7c67e5f6c884f75be9514becb963df1398ded0bf95f2bf103d42e"),
    ("iEMuJiVY9Oc:u12:c0", "iEMuJiVY9Oc/candidates/u12_c0.json", "3d3a88917904210a86c0fc1e27aa0ff9e24087a8c098516c49fdee992c2869ff"),
    ("iEMuJiVY9Oc:u20:c0", "iEMuJiVY9Oc/candidates/u20_c0.json", "3b114a24a3f4adc7b7ed67285947bfddc425fc4c084fff4caa2958b72a8039bd"),
    ("iEMuJiVY9Oc:u4:c0", "iEMuJiVY9Oc/candidates/u4_c0.json", "9056d9b9ca1061fbcc23b762ef467f10937ffea2f22b6766a089958108568285"),
    ("iVelddb1qQ4:u17:c0", "iVelddb1qQ4/candidates/u17_c0.json", "cd3d9549234e8979fddf64bfbef558f2aaa0fef0c7ad192d02618064503099fd"),
]
PILOT = [
    "CvuE-jyg-N8:u43:c0",
    "iEMuJiVY9Oc:u4:c0",
    "H2WN130OzLg:u25:c0",
    "7EJ-xotXiSE:u0:c0",
    "iEMuJiVY9Oc:u12:c0",
    "iVelddb1qQ4:u17:c0",
]
SMOKE_CANDIDATE = "H2WN130OzLg:u25:c0"
SMOKE_SAMPLE_INDEX = 1
SMOKE_PTS = 19966608
DECISION_KEYS = {"frame_id", "decision", "reason", "duplicate_of"}
SELECTION = {"keep": "selected_confirmed", "uncertain": "retained_uncertain", "drop": "not_selected"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def freeze(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("유한하지 않은 수치가 있습니다.")
        return Decimal(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("유한하지 않은 수치가 있습니다.")
        return value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, list):
        return [freeze(item) for item in value]
    if isinstance(value, dict):
        return {str(key): freeze(item) for key, item in value.items()}
    raise TypeError(f"JSON으로 보관할 수 없는 값입니다: {type(value).__name__}")


def _dump(value: Any, level: int) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("유한하지 않은 수치입니다.")
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError("float는 T4 JSON에 직접 쓰지 않습니다.")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        if not value:
            return "[]"
        inner = ",\n".join(" " * (level + 2) + _dump(item, level + 2) for item in value)
        return "[\n" + inner + "\n" + " " * level + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        rows = []
        for key, item in value.items():
            rows.append(" " * (level + 2) + json.dumps(str(key), ensure_ascii=False) + ": " + _dump(item, level + 2))
        return "{\n" + ",\n".join(rows) + "\n" + " " * level + "}"
    raise TypeError(f"지원하지 않는 JSON 값입니다: {type(value).__name__}")


def dump_text(value: Any) -> str:
    return _dump(value, 0) + "\n"


def json_time_base(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return {"numerator": int(value["numerator"]), "denominator": int(value["denominator"])}
    numerator, denominator = value
    return {"numerator": int(numerator), "denominator": int(denominator)}


def compact(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("유한하지 않은 수치입니다.")
        return format(value, "f")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(compact(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(str(key), ensure_ascii=False) + ":" + compact(item) for key, item in value.items()) + "}"
    raise TypeError(f"지원하지 않는 JSON 값입니다: {type(value).__name__}")


def canonical(value: Any) -> str:
    return compact(_sort(value))


def _sort(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort(item) for item in value]
    return value


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write(path, dump_text(payload))


def print_problem(message: str, file: str, target: str) -> None:
    print(f"원인: {message}\n파일: {file}\n대상: {target}", flush=True)


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


@contextmanager
def run_lock(run_dir: Path) -> Iterator[None]:
    handle = (run_dir / ".lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("같은 run이 이미 기록 중입니다.") from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def video_seconds_of(pts: int, numerator: int, denominator: int) -> Decimal:
    if numerator <= 0 or denominator <= 0:
        raise ValueError("time_base가 유효하지 않습니다.")
    return (Decimal(pts) * Decimal(numerator) / Decimal(denominator)).quantize(SECOND)


def frame_id_of(video_id: str, stream_index: int, pts: int) -> str:
    return f"{video_id}:s{stream_index}:pts{pts}"


def planned_request_count(count: int) -> int:
    if count <= 0:
        return 0
    if count <= 6:
        return 1
    return 1 + math.ceil((count - 6) / 5)


def build_batches(frame_ids: list[str]) -> list[dict[str, Any]]:
    if not frame_ids:
        return []
    batches = [{"context_ids": [], "target_ids": list(frame_ids[:6])}]
    position = 6
    while position < len(frame_ids):
        targets = list(frame_ids[position:position + 5])
        if not targets:
            break
        batches.append({"context_ids": [frame_ids[position - 1]], "target_ids": targets})
        position += len(targets)
    return batches


def eligible_context_ids(context_ids: list[str], decisions: dict[str, str]) -> list[str]:
    chosen = []
    for frame_id in context_ids:
        if decisions.get(frame_id) == "keep":
            chosen.append(frame_id)
    return chosen


def rollup_status(statuses: list[str]) -> str:
    if not statuses:
        return "ok"
    if all(status == "ok" for status in statuses):
        return "ok"
    if all(status == "pending" for status in statuses):
        return "pending"
    if any(status == "ok" for status in statuses):
        return "partial"
    if any(status == "failed" for status in statuses):
        return "failed"
    return "pending"


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"중복 키 {key}")
        payload[key] = value
    return payload


def _bad_constant(name: str) -> None:
    raise ValueError(f"유한하지 않은 수치 {name}")


def parse_strict_object(text: str) -> tuple[dict[str, Any] | None, str | None]:
    if "```" in text:
        return None, "코드 펜스나 설명 펜스가 있습니다."
    try:
        payload = json.loads(text, object_pairs_hook=_pairs, parse_constant=_bad_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"JSON 객체를 확인하지 못했습니다: {exc}"
    if not isinstance(payload, dict):
        return None, "JSON 객체가 아닙니다."
    return payload, None


def extract_content(payload: dict[str, Any]) -> tuple[str | None, str | None]:
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
            elif isinstance(part, dict) and set(part).issubset({"type", "text"}) and isinstance(part.get("text"), str):
                if "type" in part and part["type"] != "text":
                    return None, "content 조각 종류를 해석하지 못합니다."
                parts.append(part["text"])
            else:
                return None, "content 조각을 해석하지 못합니다."
        return "".join(parts), None
    return None, "content를 해석하지 못합니다."


def finish_reason_of(payload: dict[str, Any]) -> Any:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    return choices[0].get("finish_reason")


def reasoning_of(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    reasoning = message.get("reasoning")
    if reasoning is None:
        reasoning = message.get("reasoning_content")
    return reasoning if isinstance(reasoning, str) else None


def validate_decisions(payload: dict[str, Any], target_ids: list[str], eligible_ids: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    if set(payload) != {"frames"}:
        return None, "최상위 키가 frames 하나가 아닙니다."
    frames = payload["frames"]
    if not isinstance(frames, list) or len(frames) != len(target_ids):
        return None, "frames 수가 targets와 다릅니다."
    allowed_keeps: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item, expected in zip(frames, target_ids):
        if not isinstance(item, dict) or set(item) != DECISION_KEYS:
            return None, "frame 객체의 키가 계약과 다릅니다."
        frame_id = item["frame_id"]
        decision = item["decision"]
        reason = item["reason"]
        duplicate = item["duplicate_of"]
        if not isinstance(frame_id, str) or frame_id != expected:
            return None, "frame_id가 target 순서와 다릅니다."
        if decision not in SELECTION:
            return None, "decision이 keep, drop, uncertain이 아닙니다."
        if not isinstance(reason, str) or reason == "":
            return None, "reason이 비어 있지 않은 문자열이 아닙니다."
        if decision in {"keep", "uncertain"}:
            if duplicate is not None:
                return None, "keep 또는 uncertain의 duplicate_of가 null이 아닙니다."
        elif duplicate is not None and not isinstance(duplicate, str):
            return None, "duplicate_of 자료형이 계약과 다릅니다."
        if decision == "keep":
            allowed_keeps.add(frame_id)
        normalized.append({
            "frame_id": frame_id,
            "decision": decision,
            "reason": reason,
            "duplicate_of": duplicate,
        })
    allowed = allowed_keeps | set(eligible_ids)
    references: dict[str, str] = {}
    for item in normalized:
        duplicate = item["duplicate_of"]
        if duplicate is None:
            continue
        if duplicate == item["frame_id"] or duplicate not in allowed:
            return None, "duplicate_of가 허용된 keep target 또는 context가 아닙니다."
        references[item["frame_id"]] = duplicate
    for frame_id, target in references.items():
        if target in references:
            return None, "duplicate_of 체인 또는 순환이 있습니다."
        seen = {frame_id}
        current = target
        while current in references:
            if current in seen:
                return None, "duplicate_of 순환이 있습니다."
            seen.add(current)
            current = references[current]
    return normalized, None


def interpret_transport(transport: dict[str, Any], target_ids: list[str], eligible_ids: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "error": transport.get("error"),
        "content": None,
        "reasoning": None,
        "finish_reason": None,
        "usage": None,
        "parsed": None,
        "outer": None,
    }
    if transport.get("error"):
        return result
    if transport.get("http_status") != 200:
        result["error"] = f"HTTP {transport.get('http_status')}"
        return result
    raw = transport.get("response_body")
    if not isinstance(raw, str):
        result["error"] = "응답 본문이 없습니다."
        return result
    outer, error = parse_strict_object(raw)
    if error or outer is None:
        result["error"] = error or "응답 JSON이 아닙니다."
        return result
    result["outer"] = outer
    result["finish_reason"] = finish_reason_of(outer)
    result["reasoning"] = reasoning_of(outer)
    usage = outer.get("usage") if isinstance(outer.get("usage"), dict) else None
    result["usage"] = usage
    content, content_error = extract_content(outer)
    result["content"] = content
    if content_error:
        result["error"] = content_error
        return result
    if result["finish_reason"] != "stop":
        result["error"] = f"finish_reason={result['finish_reason']}"
        return result
    if not isinstance(content, str) or content == "":
        result["error"] = "판정 JSON이 없습니다."
        return result
    decision_payload, decision_error = parse_strict_object(content)
    if decision_error or decision_payload is None:
        result["error"] = decision_error or "판정 JSON이 아닙니다."
        return result
    parsed, parsed_error = validate_decisions(decision_payload, target_ids, eligible_ids)
    if parsed_error or parsed is None:
        result["error"] = parsed_error or "판정 구조가 계약과 다릅니다."
        return result
    result["parsed"] = parsed
    result["ok"] = True
    result["error"] = None
    return result


def tool_version(path: str) -> str | None:
    try:
        completed = subprocess.run([path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = completed.stdout.decode("utf-8", errors="replace").splitlines()
    return line[0] if line else None


def probe_video(ffprobe: str, path: Path) -> dict[str, Any]:
    args = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=index,codec_name,width,height,time_base,start_time,duration",
        "-of", "json", str(path),
    ]
    completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace")[-500:])
    payload = json.loads(completed.stdout.decode("utf-8"))
    streams = payload.get("streams") or []
    if len(streams) != 1:
        raise RuntimeError("비디오 스트림을 하나만 확인하지 못했습니다.")
    stream = streams[0]
    numerator, denominator = str(stream["time_base"]).split("/", 1)
    return {
        "index": int(stream["index"]),
        "codec_name": str(stream["codec_name"]),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "numerator": int(numerator),
        "denominator": int(denominator),
        "start_time": Decimal(str(stream["start_time"])),
        "duration": Decimal(str(stream["duration"])),
    }


def same_decimal(left: Any, right: Any) -> bool:
    return Decimal(str(left)) == Decimal(str(right))


def prompt_sha() -> str:
    return sha256_text(prompts.P0)


def code_records() -> list[dict[str, str]]:
    return [{"path": path, "sha256": sha256_file(ROOT / path)} for path in CODE_FILES]


def load_inputs(ffmpeg: str, ffprobe: str) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    root = ROOT / B05_ROOT
    manifest_path = root / "manifest.json"
    acceptance_path = root / "acceptance.md"
    if not manifest_path.is_file() or not acceptance_path.is_file():
        return ["B05 manifest 또는 acceptance가 없습니다."], {}
    manifest_hash = sha256_file(manifest_path)
    if manifest_hash != B05_MANIFEST_SHA:
        problems.append("B05 manifest SHA-256이 고정값과 다릅니다.")
    acceptance_text = acceptance_path.read_text(encoding="utf-8")
    if B05_MANIFEST_SHA not in acceptance_text:
        problems.append("acceptance에 manifest SHA-256이 없습니다.")
    manifest = read_json(manifest_path)
    if manifest.get("config_id") != "B05" or manifest.get("logic_version") != "t3.screen_changes.tile_or.grid_support.v1":
        problems.append("B05 config 또는 logic이 고정값과 다릅니다.")
    if manifest.get("schema_version") != "t3.screen_changes.v6":
        problems.append("B05 schema가 t3.screen_changes.v6이 아닙니다.")
    videos = {item["video_id"]: item for item in manifest.get("videos") or []}
    t2_ids: list[str] = []
    t2_cache: dict[str, dict[str, Any]] = {}
    documents: dict[str, dict[str, Any]] = {}
    for candidate_id, relative, expected in CANDIDATES:
        path = root / relative
        digest = sha256_file(path) if path.is_file() else ""
        if digest != expected or expected not in acceptance_text:
            problems.append(f"{candidate_id} 해시가 고정값 또는 acceptance와 다릅니다.")
            continue
        raw = read_json(path)
        if raw.get("candidate_id") != candidate_id or raw.get("status") != "ok" or not (raw.get("validation") or {}).get("ok"):
            problems.append(f"{candidate_id} 식별자, status, validation이 계약과 다릅니다.")
        if raw.get("schema_version") != "t3.screen_changes.v6":
            problems.append(f"{candidate_id} schema가 다릅니다.")
        video_id = candidate_id.split(":", 1)[0]
        video = videos.get(video_id)
        if video is None:
            problems.append(f"{candidate_id}의 영상이 manifest에 없습니다.")
            continue
        problems.extend(_audit_candidate(raw, video, path, t2_cache))
        documents[candidate_id] = raw
    for video_id, video in videos.items():
        source = ROOT / video["source_path"]
        if source.is_file():
            payload = read_json(source)
            t2_ids.extend(item["candidate_id"] for item in payload.get("candidates") or [])
    expected_ids = [item[0] for item in CANDIDATES]
    out_of_scope = [item for item in t2_ids if item not in expected_ids]
    if len(documents) == 12 and (len(t2_ids) != 139 or len(out_of_scope) != 127):
        problems.append(f"T2 후보 수 {len(t2_ids)}, 범위 밖 {len(out_of_scope)}이 139/127과 다릅니다.")
    probed: dict[str, dict[str, Any]] = {}
    if not problems:
        for video_id, video in videos.items():
            if video_id not in {item.split(':', 1)[0] for item in expected_ids}:
                continue
            media = video["media"]
            media_path = ROOT / media["path"]
            if not media_path.is_file() or not str(media["path"]).startswith(f"data/{video_id}/"):
                problems.append(f"{video_id} 원본 경로가 없습니다.")
                continue
            stat = media_path.stat()
            if stat.st_size != media["size"] or stat.st_mtime_ns != media["mtime_ns"]:
                problems.append(f"{video_id} 원본 size 또는 mtime이 manifest와 다릅니다.")
            try:
                probed[video_id] = probe_video(ffprobe, media_path)
            except (OSError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
                problems.append(f"{video_id} ffprobe 실패: {exc}")
                continue
            got = probed[video_id]
            time_base = media["time_base"]
            if got["index"] != media["stream_index"] or got["codec_name"] != media["codec_name"]:
                problems.append(f"{video_id} stream index 또는 codec이 manifest와 다릅니다.")
            if got["width"] != media["width"] or got["height"] != media["height"]:
                problems.append(f"{video_id} 해상도가 manifest와 다릅니다.")
            if got["numerator"] != time_base["numerator"] or got["denominator"] != time_base["denominator"]:
                problems.append(f"{video_id} time_base가 manifest와 다릅니다.")
            if got["start_time"] != Decimal(str(media["start_time"])):
                problems.append(f"{video_id} start_time이 manifest와 다릅니다.")
            if not same_decimal(media.get("audio_to_video_offset_seconds"), 0):
                problems.append(f"{video_id} audio offset이 0이 아닙니다. 재적용하지 않고 중단합니다.")
    library = {
        "manifest": manifest,
        "manifest_sha256": manifest_hash,
        "acceptance_sha256": sha256_file(acceptance_path),
        "documents": documents,
        "probed": probed,
        "videos": videos,
        "out_of_scope": out_of_scope,
        "t2_count": len(t2_ids),
        "ffmpeg": {"path": ffmpeg, "version_line": tool_version(ffmpeg)},
        "ffprobe": {"path": ffprobe, "version_line": tool_version(ffprobe)},
    }
    return problems, library


def _audit_candidate(raw: dict[str, Any], video: dict[str, Any], path: Path, t2_cache: dict[str, dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    candidate_id = raw["candidate_id"]
    video_id = candidate_id.split(":", 1)[0]
    text_ref = raw.get("text_ref") or {}
    t2_path = ROOT / str(text_ref.get("t2_path") or "")
    t1_path = ROOT / str(text_ref.get("t1_path") or "")
    if raw.get("source_t2_ref") != text_ref.get("t2_path") or raw.get("source_t2_sha256") != text_ref.get("t2_sha256"):
        problems.append(f"{candidate_id} T2 참조가 text_ref와 다릅니다.")
    if not t2_path.is_file() or sha256_file(t2_path) != text_ref.get("t2_sha256"):
        problems.append(f"{candidate_id} T2 해시가 다릅니다.")
        return problems
    if not t1_path.is_file() or sha256_file(t1_path) != text_ref.get("t1_sha256"):
        problems.append(f"{candidate_id} T1 해시가 다릅니다.")
        return problems
    cache_key = str(text_ref["t2_path"])
    if cache_key not in t2_cache:
        t2_cache[cache_key] = read_json(t2_path)
    t2 = t2_cache[cache_key]
    if t2.get("video_id") != video_id:
        problems.append(f"{candidate_id} T2 video_id가 다릅니다.")
    matches = [item for item in t2.get("candidates") or [] if item.get("candidate_id") == candidate_id]
    if len(matches) != 1:
        problems.append(f"{candidate_id} T2 후보가 하나가 아닙니다.")
        return problems
    source = raw.get("source_candidate") or {}
    match = matches[0]
    for key in ("candidate_source", "model_inference_success", "unit_index", "chunk_index", "sentence_start", "sentence_end"):
        if source.get(key) != match.get(key):
            problems.append(f"{candidate_id} T2 {key}가 다릅니다.")
    if video_id == "H2WN130OzLg" and candidate_id in {SMOKE_CANDIDATE, "H2WN130OzLg:u7:c0"}:
        if source.get("candidate_source") != "budget_guard" or source.get("model_inference_success") is not False:
            problems.append(f"{candidate_id} budget_guard 출처가 유지되지 않았습니다.")
    t1 = read_json(t1_path)
    t1_units = [unit for unit in t1.get("units") or [] if int(unit.get("index")) == int(text_ref["unit_index"])]
    t2_units = [unit for unit in t2.get("units") or [] if int(unit.get("unit_index")) == int(text_ref["unit_index"])]
    if len(t1_units) != 1 or len(t2_units) != 1:
        problems.append(f"{candidate_id} 의미 단위가 하나가 아닙니다.")
        return problems
    start = int(text_ref["sentence_start"])
    end = int(text_ref["sentence_end"])
    t1_sentences = [item for item in t1_units[0].get("sentences") or [] if start <= int(item["index"]) < end]
    t2_sentences = [item for item in t2_units[0].get("sentences") or [] if start <= int(item["index"]) < end]
    if [item["text"] for item in t1_sentences] != [item["text"] for item in t2_sentences]:
        problems.append(f"{candidate_id} T1과 T2 문장 텍스트가 다릅니다.")
    alignment_path = ROOT / t2["input"]["alignment_sentences"]
    if sha256_file(alignment_path) != t2["input"]["alignment_sha256"]:
        problems.append(f"{candidate_id} alignment 해시가 다릅니다.")
        return problems
    chunks = read_json(alignment_path)["chunks"]
    for sentence in t1_sentences:
        chunk = chunks[int(sentence["index"])]
        if chunk["text"] != sentence["text"]:
            problems.append(f"{candidate_id} 문장 {sentence['index']} 원문이 다릅니다.")
            break
        if not same_decimal(chunk["items"][0]["start_time"], sentence["start_time"]) or not same_decimal(chunk["items"][-1]["end_time"], sentence["end_time"]):
            problems.append(f"{candidate_id} 문장 {sentence['index']} audio 시각이 다릅니다.")
            break
    offset = Decimal(str(video["media"]["audio_to_video_offset_seconds"]))
    if not same_decimal(source.get("video_start"), Decimal(str(source.get("audio_start"))) + offset):
        problems.append(f"{candidate_id} video 시작이 audio와 offset의 합과 다릅니다.")
    if not same_decimal(source.get("video_end"), Decimal(str(source.get("audio_end"))) + offset):
        problems.append(f"{candidate_id} video 끝이 audio와 offset의 합과 다릅니다.")
    media = video["media"]
    problems.extend(_audit_samples(raw, media))
    return problems


def _audit_samples(raw: dict[str, Any], media: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    candidate_id = raw["candidate_id"]
    samples = raw.get("samples") or []
    segments = raw.get("segments") or []
    owned: dict[int, int] = {}
    for segment in segments:
        for index in segment.get("analysis_sample_indices") or []:
            owned[int(index)] = owned.get(int(index), 0) + 1
    if sorted(owned) != list(range(len(samples))) or any(value != 1 for value in owned.values()):
        problems.append(f"{candidate_id} 구간 소속이 누락되었거나 중복입니다.")
    seen_pts: set[int] = set()
    duration = Decimal(str(media["duration"]))
    for sample in samples:
        index = int(sample["sample_index"])
        pts = sample.get("pts")
        time_base = sample.get("time_base") or {}
        if not isinstance(pts, int) or pts in seen_pts:
            problems.append(f"{candidate_id} sample {index} PTS가 정수가 아니거나 후보 안에서 중복입니다.")
            continue
        seen_pts.add(pts)
        if time_base.get("numerator") != media["time_base"]["numerator"] or time_base.get("denominator") != media["time_base"]["denominator"]:
            problems.append(f"{candidate_id} sample {index} time_base가 스트림과 다릅니다.")
            continue
        actual = video_seconds_of(pts, int(time_base["numerator"]), int(time_base["denominator"]))
        if actual != Decimal(str(sample["video_seconds"])):
            problems.append(f"{candidate_id} sample {index} 저장 시각이 PTS rational과 다릅니다.")
        if actual < 0 or actual >= duration:
            problems.append(f"{candidate_id} sample {index}가 스트림 범위 밖입니다.")
        if int(sample.get("stream_index")) != int(media["stream_index"]):
            problems.append(f"{candidate_id} sample {index} stream index가 다릅니다.")
    request_links: dict[int, list[int]] = {}
    for request in raw.get("requests") or []:
        sample_index = request.get("sample_index")
        if request.get("status") != "ok" or not isinstance(sample_index, int) or not 0 <= sample_index < len(samples):
            problems.append(f"{candidate_id} request {request.get('request_index')} 연결이 없습니다.")
            continue
        request_links.setdefault(sample_index, []).append(int(request["request_index"]))
        if request_links[sample_index] != list(samples[sample_index].get("request_indices") or [])[:len(request_links[sample_index])]:
            pass
    for sample in samples:
        if list(sample.get("request_indices") or []) != request_links.get(int(sample["sample_index"]), []):
            problems.append(f"{candidate_id} sample {sample['sample_index']} request 연결이 다릅니다.")
            break
    if candidate_id == SMOKE_CANDIDATE:
        anchor = next((sample for sample in samples if int(sample["sample_index"]) == SMOKE_SAMPLE_INDEX), None)
        if anchor is None or int(anchor["pts"]) != SMOKE_PTS or Decimal(str(anchor["video_seconds"])) != Decimal("1247.913000000"):
            problems.append("H2 u25 sample_index 1의 실제 PTS가 1247.913초가 아닙니다.")
    return problems


def sentences_of(raw: dict[str, Any]) -> list[dict[str, Any]]:
    text_ref = raw["text_ref"]
    t2 = read_json(ROOT / text_ref["t2_path"])
    unit = next(unit for unit in t2["units"] if int(unit["unit_index"]) == int(text_ref["unit_index"]))
    start = int(text_ref["sentence_start"])
    end = int(text_ref["sentence_end"])
    sentences = []
    for sentence in unit["sentences"]:
        if start <= int(sentence["index"]) < end:
            sentences.append({
                "sentence_index": int(sentence["index"]),
                "text": str(sentence["text"]),
                "audio_start": Decimal(str(sentence["start_time"])),
                "audio_end": Decimal(str(sentence["end_time"])),
            })
    return sentences


def build_frames(raw: dict[str, Any]) -> list[dict[str, Any]]:
    video_id = raw["candidate_id"].split(":", 1)[0]
    segment_of: dict[int, dict[str, Any]] = {}
    for segment in raw["segments"]:
        for index in segment["analysis_sample_indices"]:
            segment_of[int(index)] = segment
    requests = {int(item["request_index"]): item for item in raw["requests"]}
    frames: list[dict[str, Any]] = []
    by_pts: dict[int, dict[str, Any]] = {}
    for sample in sorted(raw["samples"], key=lambda item: (int(item["pts"]), int(item["sample_index"]))):
        pts = int(sample["pts"])
        request_rows = []
        for request_index in sample["request_indices"]:
            request = requests[int(request_index)]
            request_rows.append({
                "request_index": int(request_index),
                "requested_video_seconds": Decimal(str(request["requested_video_seconds"])),
                "kind": request.get("kind"),
                "sample_index": int(sample["sample_index"]),
            })
        if pts in by_pts:
            frame = by_pts[pts]
            frame["sample_indices"].append(int(sample["sample_index"]))
            frame["requests"].extend(request_rows)
            continue
        time_base = sample["time_base"]
        frame = {
            "frame_id": frame_id_of(video_id, int(sample["stream_index"]), pts),
            "sample_indices": [int(sample["sample_index"])],
            "requests": request_rows,
            "stream_index": int(sample["stream_index"]),
            "pts": pts,
            "time_base": {"numerator": int(time_base["numerator"]), "denominator": int(time_base["denominator"])},
            "video_seconds": video_seconds_of(pts, int(time_base["numerator"]), int(time_base["denominator"])),
            "segment_index": int(segment_of[int(sample["sample_index"])]["segment_index"]),
            "full": None,
            "vlm": None,
            "decision": None,
            "reason": None,
            "duplicate_of": None,
            "selection": None,
            "execution_state": "pending",
            "batch_id": None,
        }
        by_pts[pts] = frame
        frames.append(frame)
    return frames


def build_candidate(raw: dict[str, Any], video: dict[str, Any]) -> dict[str, Any]:
    frames = build_frames(raw)
    batches = []
    for index, batch in enumerate(build_batches([frame["frame_id"] for frame in frames])):
        batch_id = f"{raw['candidate_id']}#b{index}"
        for frame_id in batch["target_ids"]:
            frame = next(item for item in frames if item["frame_id"] == frame_id)
            frame["batch_id"] = batch_id
        batches.append({
            "batch_id": batch_id,
            "batch_index": index,
            "context_ids": batch["context_ids"],
            "target_ids": batch["target_ids"],
            "eligible_context_ids": [],
            "status": "pending",
            "call_ref": None,
            "reused_from": None,
            "http_attempts": 0,
            "decisions": None,
            "error": None,
            "request_bytes": None,
            "text_chars": None,
            "target_image_bytes": None,
            "context_image_bytes": None,
        })
    segments = []
    for segment in raw["segments"]:
        sample_ids = [int(item) for item in segment["analysis_sample_indices"]]
        frame_ids = []
        for frame in frames:
            if int(segment["segment_index"]) == frame["segment_index"] and any(index in sample_ids for index in frame["sample_indices"]):
                frame_ids.append(frame["frame_id"])
        segments.append({
            "segment_index": int(segment["segment_index"]),
            "video_start": Decimal(str(segment["video_start"])),
            "video_end": Decimal(str(segment["video_end"])),
            "left_boundary_index": segment.get("left_boundary_index"),
            "right_boundary_index": segment.get("right_boundary_index"),
            "analysis_sample_indices": sample_ids,
            "frame_ids": frame_ids,
            "selected_confirmed": [],
            "retained_uncertain": [],
            "not_selected": [],
            "pending": list(frame_ids),
            "failed": [],
        })
    text_ref = raw["text_ref"]
    return {
        "candidate_id": raw["candidate_id"],
        "unit_index": int(raw["unit_index"]),
        "chunk_index": int(raw["chunk_index"]),
        "source_candidate": freeze(raw["source_candidate"]),
        "source_t3_ref": rel(ROOT / B05_ROOT / f"{raw['candidate_id'].split(':', 1)[0]}/candidates/u{raw['unit_index']}_c{raw['chunk_index']}.json"),
        "source_t3_sha256": sha256_file(ROOT / B05_ROOT / f"{raw['candidate_id'].split(':', 1)[0]}/candidates/u{raw['unit_index']}_c{raw['chunk_index']}.json"),
        "source_t2_ref": raw["source_t2_ref"],
        "source_t2_sha256": raw["source_t2_sha256"],
        "text_ref": freeze(text_ref),
        "sentences": sentences_of(raw),
        "requested_range": freeze(raw["requested_range"]),
        "effective_range": freeze(raw["effective_range"]),
        "t3_warnings": freeze(raw.get("warnings") or []),
        "boundaries": freeze(raw.get("boundaries") or []),
        "frames": frames,
        "segments": segments,
        "batches": batches,
        "selected_confirmed": [],
        "retained_uncertain": [],
        "not_selected": [],
        "status": rollup_status([batch["status"] for batch in batches]),
        "problems": [],
        "warnings": [],
        "validation": {"ok": True, "problems": []},
    }


def build_video_document(video_id: str, candidates: list[dict[str, Any]], video: dict[str, Any], probed: dict[str, Any]) -> dict[str, Any]:
    media = video["media"]
    metadata = read_json(ROOT / video["references"]["metadata"]["path"])
    return {
        "schema_version": SCHEMA_VERSION,
        "config_id": CONFIG_ID,
        "logic_version": LOGIC_VERSION,
        "prompt_id": prompts.PROMPT_ID,
        "prompt_sha256": prompt_sha(),
        "path_reference": "repository_root",
        "video_id": video_id,
        "source_url": metadata.get("source_url"),
        "media": {
            "path": media["path"],
            "recorded_sha256": media["recorded_sha256"],
            "bytes_rehashed": False,
            "size": media["size"],
            "mtime_ns": media["mtime_ns"],
            "stream_index": media["stream_index"],
            "codec_name": media["codec_name"],
            "width": media["width"],
            "height": media["height"],
            "time_base": freeze(media["time_base"]),
            "start_time": Decimal(str(media["start_time"])),
            "duration": Decimal(str(probed["duration"])),
            "audio_to_video_offset_seconds": Decimal(str(media["audio_to_video_offset_seconds"])),
            "offset_reapplied": False,
        },
        "candidates": candidates,
        "status": rollup_status([item["status"] for item in candidates]),
        "problems": [],
        "warnings": [],
        "validation": {"ok": True, "problems": []},
    }


def refresh_candidate(candidate: dict[str, Any]) -> None:
    frames = {frame["frame_id"]: frame for frame in candidate["frames"]}
    decisions: dict[str, dict[str, Any]] = {}
    batch_of = {frame["frame_id"]: frame["batch_id"] for frame in candidate["frames"]}
    status_of = {batch["batch_id"]: batch["status"] for batch in candidate["batches"]}
    for batch in candidate["batches"]:
        if batch["status"] == "ok" and isinstance(batch.get("decisions"), list):
            for item in batch["decisions"]:
                decisions[item["frame_id"]] = item
    for frame in candidate["frames"]:
        item = decisions.get(frame["frame_id"])
        state = status_of.get(frame["batch_id"], "pending")
        if item and state == "ok":
            frame["decision"] = item["decision"]
            frame["reason"] = item["reason"]
            frame["duplicate_of"] = item["duplicate_of"]
            frame["selection"] = SELECTION[item["decision"]]
            frame["execution_state"] = "ok"
        else:
            frame["decision"] = None
            frame["reason"] = None
            frame["duplicate_of"] = None
            frame["selection"] = None
            frame["execution_state"] = "failed" if state == "failed" else "pending"
    candidate["selected_confirmed"] = [frame["frame_id"] for frame in candidate["frames"] if frame["selection"] == "selected_confirmed"]
    candidate["retained_uncertain"] = [frame["frame_id"] for frame in candidate["frames"] if frame["selection"] == "retained_uncertain"]
    candidate["not_selected"] = [frame["frame_id"] for frame in candidate["frames"] if frame["selection"] == "not_selected"]
    confirmed = set(candidate["selected_confirmed"])
    problems = []
    for frame in candidate["frames"]:
        duplicate = frame.get("duplicate_of")
        if isinstance(duplicate, str) and duplicate not in confirmed:
            problems.append(f"{frame['frame_id']}의 duplicate_of가 confirmed에 없습니다.")
    for segment in candidate["segments"]:
        ids = segment["frame_ids"]
        segment["selected_confirmed"] = [frame_id for frame_id in ids if frames[frame_id]["selection"] == "selected_confirmed"]
        segment["retained_uncertain"] = [frame_id for frame_id in ids if frames[frame_id]["selection"] == "retained_uncertain"]
        segment["not_selected"] = [frame_id for frame_id in ids if frames[frame_id]["selection"] == "not_selected"]
        segment["pending"] = [frame_id for frame_id in ids if frames[frame_id]["execution_state"] == "pending"]
        segment["failed"] = [frame_id for frame_id in ids if frames[frame_id]["execution_state"] == "failed"]
    candidate["problems"] = problems
    candidate["status"] = rollup_status([batch["status"] for batch in candidate["batches"]])
    candidate["validation"] = {"ok": not problems, "problems": problems}
    if batch_of is None:
        return


def refresh_document(document: dict[str, Any]) -> None:
    for candidate in document["candidates"]:
        refresh_candidate(candidate)
    document["status"] = rollup_status([candidate["status"] for candidate in document["candidates"]])
    problems = [problem for candidate in document["candidates"] for problem in candidate["problems"]]
    document["problems"] = problems
    document["validation"] = {"ok": not problems, "problems": problems}


def settings_payload() -> dict[str, Any]:
    return {
        "config_id": CONFIG_ID,
        "logic_version": LOGIC_VERSION,
        "schema_version": SCHEMA_VERSION,
        "prompt_id": prompts.PROMPT_ID,
        "frame_source": "all_b05_samples_time_order",
        "extra_time_samples": False,
        "padding_seconds": "0",
        "image": {
            "color": "rgb24",
            "container": "png",
            "vlm_long_side_max": 1280,
            "upscale": False,
            "keep_aspect": True,
            "scale_flags": "area",
            "scale_filter_landscape": "scale=w='min(1280,iw)':h=-2:flags=area,format=rgb24",
            "scale_filter_portrait": "scale=w=-2:h='min(1280,ih)':flags=area,format=rgb24",
        },
        "images_per_request": 6,
        "context_images": 1,
        "new_targets_after_first": 5,
        "temperature": 0,
        "enable_thinking": False,
        "max_tokens": MAX_TOKENS,
        "timeout_seconds": TIMEOUT_SECONDS,
        "concurrency": 1,
        "retries": 0,
        "infer_url": INFER_URL,
        "body_model": BODY_MODEL,
        "infer_model_id": INFER_MODEL_ID,
        "separate_duplicate_call": False,
    }


def model_payload() -> dict[str, Any]:
    return {
        "url": INFER_URL,
        "infer_model_id": INFER_MODEL_ID,
        "body_model": BODY_MODEL,
        "checkpoint": None,
        "server_version": None,
        "context_limit": None,
        "visual_token_limit": None,
        "max_images": None,
        "vram": None,
        "image_preprocessor": None,
        "note": "body model은 기존 T1/T2 요청 식별자이며 현재 체크포인트 확인은 아니다.",
    }


def empty_run(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "started_at": utc_now(),
        "finished_at": None,
        "phase": "planned",
        "run_status": "pending",
        "quality_status": "pending_review",
        "counts": {
            "new": 0,
            "reused": 0,
            "failed": 0,
            "pending": 0,
            "http_attempts": 0,
            "context_reinputs": 0,
        },
        "timing": {
            "decode_seconds": Decimal(0),
            "resize_seconds": Decimal(0),
            "inference_seconds": Decimal(0),
            "render_seconds": Decimal(0),
            "wall_seconds": Decimal(0),
        },
        "usage": None,
        "vram": None,
        "model_calls_observed": {
            "multi_image_http_accepted": None,
            "image_use_confirmed": None,
        },
        "smoke": None,
        "problems": [],
    }


def video_order(candidate_ids: list[str]) -> list[str]:
    order = []
    for candidate_id in candidate_ids:
        video_id = candidate_id.split(":", 1)[0]
        if video_id not in order:
            order.append(video_id)
    return order


def command_plan() -> int:
    ffmpeg = shutil.which("ffmpeg") or ""
    ffprobe = shutil.which("ffprobe") or ""
    if not ffmpeg or not ffprobe:
        print_problem("ffmpeg 또는 ffprobe가 없습니다.", "tests/T4/run_t4.py", "plan")
        return 1
    problems, library = load_inputs(ffmpeg, ffprobe)
    if problems or not library:
        for problem in problems:
            print_problem(problem, B05_ROOT, "plan")
        return 1
    documents = []
    total_requests = 0
    total_frames = 0
    total_segments = 0
    for candidate_id, _relative, _expected in CANDIDATES:
        raw = library["documents"][candidate_id]
        video_id = candidate_id.split(":", 1)[0]
        candidate = build_candidate(raw, library["videos"][video_id])
        total_requests += len(candidate["batches"])
        total_frames += len(candidate["frames"])
        total_segments += len(candidate["segments"])
        if len(candidate["batches"]) != planned_request_count(len(raw["samples"])):
            print_problem("묶음 수가 샘플 수 공식과 다릅니다.", candidate_id, "plan")
            return 1
        documents.append(candidate)
    if total_frames != 2698 or total_segments != 975 or total_requests != 543:
        print_problem(
            f"고정 입력의 프레임 {total_frames}, 구간 {total_segments}, 요청 {total_requests}이 2698/975/543과 다릅니다.",
            B05_ROOT,
            "plan",
        )
        return 1
    pilot_requests = sum(len(item["batches"]) for item in documents if item["candidate_id"] in PILOT)
    if pilot_requests != 205:
        print_problem(f"pilot 요청 수 {pilot_requests}이 205와 다릅니다.", B05_ROOT, "plan")
        return 1
    anchor = next(item for item in documents if item["candidate_id"] == SMOKE_CANDIDATE)
    anchor_frames = anchor["frames"]
    first_targets = anchor["batches"][0]["target_ids"]
    if anchor["batches"][0]["context_ids"] or not any(SMOKE_SAMPLE_INDEX in frame["sample_indices"] and frame["frame_id"] in first_targets for frame in anchor_frames):
        print_problem("H2 u25 첫 묶음에 sample_index 1이 없습니다.", SMOKE_CANDIDATE, "plan")
        return 1
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(3)
    run_dir = RUNS_ROOT / run_id
    if run_dir.exists():
        print_problem("run 폴더가 이미 있습니다.", str(run_dir), run_id)
        return 1
    run_dir.mkdir(parents=True)
    by_video: dict[str, list[dict[str, Any]]] = {}
    for candidate in documents:
        by_video.setdefault(candidate["candidate_id"].split(":", 1)[0], []).append(candidate)
    video_documents = []
    for video_id in video_order([item[0] for item in CANDIDATES]):
        video_documents.append(build_video_document(video_id, by_video[video_id], library["videos"][video_id], library["probed"][video_id]))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "config_id": CONFIG_ID,
        "logic_version": LOGIC_VERSION,
        "prompt_id": prompts.PROMPT_ID,
        "prompt_sha256": prompt_sha(),
        "prompt_text": prompts.P0,
        "path_reference": "repository_root",
        "settings": settings_payload(),
        "model": model_payload(),
        "code": code_records(),
        "runtime": {
            "python": sys.version,
            "ffmpeg": library["ffmpeg"],
            "ffprobe": library["ffprobe"],
        },
        "input": {
            "b05_root": B05_ROOT,
            "manifest_sha256": library["manifest_sha256"],
            "acceptance_sha256": library["acceptance_sha256"],
            "t3_config_id": library["manifest"].get("config_id"),
            "t3_logic_version": library["manifest"].get("logic_version"),
            "t3_schema_version": library["manifest"].get("schema_version"),
            "t3_settings": freeze(library["manifest"].get("settings")),
            "candidates": [
                {"candidate_id": candidate_id, "path": f"{B05_ROOT}/{relative}", "sha256": expected}
                for candidate_id, relative, expected in CANDIDATES
            ],
            "video_bytes_rehashed": False,
            "out_of_scope": library["out_of_scope"],
        },
        "plan": {
            "frames": total_frames,
            "segments": total_segments,
            "requests": total_requests,
            "pilot_requests": pilot_requests,
            "smoke": {
                "first": {"candidate_id": SMOKE_CANDIDATE, "batch_index": 0, "must_include_sample_index": SMOKE_SAMPLE_INDEX},
                "second": {
                    "rule": "largest_serialized_first_batch_among_pilot_excluding_H2WN130OzLg:u25:c0",
                    "tie_break": [item for item in PILOT if item != SMOKE_CANDIDATE],
                },
                "max_http_requests": 2,
            },
            "batches": [
                {
                    "batch_id": batch["batch_id"],
                    "candidate_id": candidate["candidate_id"],
                    "batch_index": batch["batch_index"],
                    "context_ids": batch["context_ids"],
                    "target_ids": batch["target_ids"],
                    "request_bytes": None,
                }
                for candidate in documents
                for batch in candidate["batches"]
            ],
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    for document in video_documents:
        refresh_document(document)
        write_json(run_dir / document["video_id"] / "selected_frames.json", document)
    run = empty_run(run_id)
    run["counts"]["pending"] = total_requests
    write_json(run_dir / "run.json", run)
    _write_views(run_dir, video_documents, run)
    print(f"run: tests/T4/runs/{run_id}", flush=True)
    print(f"frames {total_frames}, segments {total_segments}, requests {total_requests}, pilot {pilot_requests}", flush=True)
    return 0


def load_documents(run_dir: Path) -> list[dict[str, Any]]:
    manifest = read_json(run_dir / "manifest.json")
    documents = []
    seen = []
    for item in manifest["plan"]["batches"]:
        video_id = item["candidate_id"].split(":", 1)[0]
        if video_id not in seen:
            seen.append(video_id)
            documents.append(freeze(read_json(run_dir / video_id / "selected_frames.json")))
    return documents


def save_documents(run_dir: Path, documents: list[dict[str, Any]]) -> None:
    for document in documents:
        refresh_document(document)
        write_json(run_dir / document["video_id"] / "selected_frames.json", document)


def find_batch(documents: list[dict[str, Any]], candidate_id: str, batch_index: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for document in documents:
        for candidate in document["candidates"]:
            if candidate["candidate_id"] == candidate_id:
                return document, candidate, candidate["batches"][batch_index]
    raise KeyError(candidate_id)


def iter_frames(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {frame["frame_id"]: frame for frame in candidate["frames"]}


def image_label(frame: dict[str, Any], role: str) -> str:
    return (
        f"frame_id: {frame['frame_id']}\n"
        f"role: {role}\n"
        f"video_seconds: {format(frame['video_seconds'], 'f')}\n"
        f"segment_index: {frame['segment_index']}\n"
    )


def build_request(candidate: dict[str, Any], batch: dict[str, Any], decisions: dict[str, str]) -> dict[str, Any]:
    frames = iter_frames(candidate)
    eligible = eligible_context_ids(batch["context_ids"], decisions)
    sentence_payload = [
        {
            "sentence_index": sentence["sentence_index"],
            "text": sentence["text"],
            "audio_start": sentence["audio_start"],
            "audio_end": sentence["audio_end"],
        }
        for sentence in candidate["sentences"]
    ]
    rendered = prompts.render_prompt(
        candidate["candidate_id"],
        compact(sentence_payload),
        compact(batch["target_ids"]),
        compact(eligible),
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": rendered}]
    image_meta = []
    target_bytes = 0
    context_bytes = 0
    ordered = [("context", frame_id) for frame_id in batch["context_ids"]] + [("target", frame_id) for frame_id in batch["target_ids"]]
    for role, frame_id in ordered:
        frame = frames[frame_id]
        vlm = frame.get("vlm") or {}
        path = ROOT / str(vlm.get("path") or "")
        data = path.read_bytes()
        if sha256_bytes(data) != vlm.get("sha256"):
            raise RuntimeError(f"{frame_id} VLM 파일 해시가 기록과 다릅니다.")
        content.append({"type": "text", "text": image_label(frame, role)})
        encoded = base64.standard_b64encode(data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}})
        image_meta.append({"frame_id": frame_id, "role": role, "sha256": vlm["sha256"], "path": vlm["path"], "bytes": len(data)})
        if role == "target":
            target_bytes += len(data)
        else:
            context_bytes += len(data)
    body = {
        "model": BODY_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    wire = compact(body).encode("utf-8")
    return {
        "body": body,
        "wire": wire,
        "eligible": eligible,
        "rendered": rendered,
        "images": image_meta,
        "target_image_bytes": target_bytes,
        "context_image_bytes": context_bytes,
        "text_chars": len(rendered),
        "request_bytes": len(wire),
    }


def decisions_so_far(candidate: dict[str, Any]) -> dict[str, str]:
    found = {}
    for batch in candidate["batches"]:
        if batch["status"] == "ok" and isinstance(batch.get("decisions"), list):
            for item in batch["decisions"]:
                found[item["frame_id"]] = item["decision"]
    return found


def reuse_key(document: dict[str, Any], candidate: dict[str, Any], batch: dict[str, Any], built: dict[str, Any]) -> str:
    payload = {
        "logic_version": LOGIC_VERSION,
        "prompt_sha256": prompt_sha(),
        "rendered_prompt_sha256": sha256_text(built["rendered"]),
        "url": INFER_URL,
        "body_model": BODY_MODEL,
        "infer_model_id": INFER_MODEL_ID,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "enable_thinking": False,
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["source_t3_sha256"],
        "media_path": document["media"]["path"],
        "media_recorded_sha256": document["media"]["recorded_sha256"],
        "media_size": document["media"]["size"],
        "media_mtime_ns": document["media"]["mtime_ns"],
        "sentences": candidate["sentences"],
        "target_ids": batch["target_ids"],
        "context_ids": batch["context_ids"],
        "eligible_context_ids": built["eligible"],
        "image_sha256": [item["sha256"] for item in built["images"]],
        "request_sha256": sha256_bytes(built["wire"]),
    }
    return sha256_text(canonical(payload))


class InferClient:
    def post(self, wire: bytes) -> dict[str, Any]:
        started = time.perf_counter()
        request = urllib.request.Request(
            INFER_URL,
            data=wire,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read()
                status = response.status
            return {
                "http_status": status,
                "response_body": raw.decode("utf-8"),
                "elapsed_seconds": Decimal(str(round(time.perf_counter() - started, 6))),
                "error": None,
            }
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return {
                "http_status": exc.code,
                "response_body": raw.decode("utf-8", errors="replace"),
                "elapsed_seconds": Decimal(str(round(time.perf_counter() - started, 6))),
                "error": None,
            }
        except TimeoutError:
            return _timeout(started)
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                return _timeout(started)
            return {
                "http_status": None,
                "response_body": None,
                "elapsed_seconds": Decimal(str(round(time.perf_counter() - started, 6))),
                "error": f"추론 요청에 실패했습니다: {reason}",
            }


def _timeout(started: float) -> dict[str, Any]:
    return {
        "http_status": None,
        "response_body": None,
        "elapsed_seconds": Decimal(str(round(time.perf_counter() - started, 6))),
        "error": "요청이 시간 초과되었습니다. 서버 작업이 끝났는지는 확인하지 않았습니다.",
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def locate_reuse(run_dir: Path, key: str) -> dict[str, Any] | None:
    calls = run_dir / "calls"
    if not calls.is_dir():
        return None
    for path in sorted(calls.glob("*.json")):
        try:
            record = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("reuse_key") != key or record.get("error") is not None or not isinstance(record.get("parsed"), list):
            continue
        response_body = record.get("response_body")
        if not isinstance(response_body, str) or record.get("response_sha256") != sha256_text(response_body):
            continue
        return record
    return None


def apply_success(batch: dict[str, Any], parsed: list[dict[str, Any]], call_ref: str, reused_from: str | None) -> None:
    batch["status"] = "ok"
    batch["decisions"] = parsed
    batch["error"] = None
    batch["call_ref"] = call_ref
    batch["reused_from"] = reused_from


def store_call(run_dir: Path, run_id: str, record: dict[str, Any]) -> str:
    calls = run_dir / "calls"
    calls.mkdir(parents=True, exist_ok=True)
    path = calls / f"{record['call_id']}.json"
    record["call_ref"] = f"tests/T4/runs/{run_id}/calls/{record['call_id']}.json"
    write_json(path, record)
    return record["call_ref"]


def execute_batch(
    run_dir: Path,
    run: dict[str, Any],
    document: dict[str, Any],
    candidate: dict[str, Any],
    batch: dict[str, Any],
    client: InferClient,
    *,
    allow_http: bool,
) -> str:
    if batch["status"] == "ok":
        return "skipped"
    if batch["status"] == "failed":
        return "skipped_failed"
    built = build_request(candidate, batch, decisions_so_far(candidate))
    batch["eligible_context_ids"] = built["eligible"]
    batch["request_bytes"] = built["request_bytes"]
    batch["text_chars"] = built["text_chars"]
    batch["target_image_bytes"] = built["target_image_bytes"]
    batch["context_image_bytes"] = built["context_image_bytes"]
    key = reuse_key(document, candidate, batch, built)
    existing = locate_reuse(run_dir, key)
    if existing:
        checked, error = validate_decisions(
            {"frames": existing["parsed"]},
            batch["target_ids"],
            built["eligible"],
        )
        if error or checked is None:
            batch["status"] = "failed"
            batch["error"] = error or "저장 응답이 현재 구조 검사를 통과하지 못했습니다."
            return "failed"
        apply_success(batch, checked, existing["call_ref"], existing["call_ref"])
        return "reused"
    if not allow_http:
        return "pending"
    transport = client.post(built["wire"])
    run["counts"]["http_attempts"] = int(run["counts"]["http_attempts"]) + 1
    batch["http_attempts"] = int(batch["http_attempts"]) + 1
    run["timing"]["inference_seconds"] = run["timing"]["inference_seconds"] + transport["elapsed_seconds"]
    if batch["context_ids"]:
        run["counts"]["context_reinputs"] = int(run["counts"]["context_reinputs"]) + len(batch["context_ids"])
    interpreted = interpret_transport(transport, batch["target_ids"], built["eligible"])
    response_body = transport.get("response_body")
    record = {
        "schema_version": SCHEMA_VERSION,
        "call_id": str(uuid.uuid4()),
        "batch_id": batch["batch_id"],
        "candidate_id": candidate["candidate_id"],
        "video_id": document["video_id"],
        "reuse_key": key,
        "url": INFER_URL,
        "request_sha256": sha256_bytes(built["wire"]),
        "request_bytes": built["request_bytes"],
        "request_body": built["body"],
        "transmitted_images": built["images"],
        "http_status": transport.get("http_status"),
        "response_body": response_body,
        "response_sha256": sha256_text(response_body) if isinstance(response_body, str) else None,
        "content": interpreted["content"],
        "reasoning": interpreted["reasoning"],
        "finish_reason": interpreted["finish_reason"],
        "usage": interpreted["usage"],
        "parsed": interpreted["parsed"],
        "error": interpreted["error"],
        "elapsed_seconds": transport["elapsed_seconds"],
        "started_at": utc_now(),
        "reused_from": None,
    }
    call_ref = store_call(run_dir, run["run_id"], record)
    if interpreted["ok"]:
        apply_success(batch, interpreted["parsed"], call_ref, None)
        if len(built["images"]) > 1 and transport.get("http_status") == 200:
            run["model_calls_observed"]["multi_image_http_accepted"] = True
        return "ok"
    batch["status"] = "failed"
    batch["error"] = interpreted["error"]
    batch["call_ref"] = call_ref
    return "failed"


def execute_selected(run_dir: Path, documents: list[dict[str, Any]], run: dict[str, Any], refs: list[tuple[str, int]], client: InferClient) -> str:
    for candidate_id, batch_index in refs:
        document, candidate, batch = find_batch(documents, candidate_id, batch_index)
        outcome = execute_batch(run_dir, run, document, candidate, batch, client, allow_http=True)
        save_documents(run_dir, documents)
        write_json(run_dir / "run.json", run)
        if outcome == "failed":
            return "failed"
    return "done"


def smoke_refs(documents: list[dict[str, Any]], sizes: dict[str, int]) -> list[tuple[str, int]]:
    _document, candidate, batch = find_batch(documents, SMOKE_CANDIDATE, 0)
    frames = iter_frames(candidate)
    if batch["context_ids"] or not any(SMOKE_SAMPLE_INDEX in frames[frame_id]["sample_indices"] for frame_id in batch["target_ids"]):
        raise RuntimeError("smoke 첫 묶음이 H2 u25의 sample_index 1을 포함하지 않습니다.")
    ranked = []
    for index, candidate_id in enumerate(item for item in PILOT if item != SMOKE_CANDIDATE):
        other = find_batch(documents, candidate_id, 0)[2]
        if other["context_ids"]:
            raise RuntimeError(f"{candidate_id} 첫 묶음에 context가 있습니다.")
        if other["batch_id"] not in sizes:
            raise RuntimeError(f"{other['batch_id']} 직렬화 크기가 없습니다.")
        ranked.append((sizes[other["batch_id"]], index, candidate_id))
    _size, _index, second = min(ranked, key=lambda item: (-item[0], item[1]))
    return [(SMOKE_CANDIDATE, 0), (second, 0)]


def mode_refs(documents: list[dict[str, Any]], mode: str, sizes: dict[str, int] | None = None) -> list[tuple[str, int]]:
    if mode == "smoke":
        return smoke_refs(documents, sizes or {})
    present = [candidate["candidate_id"] for document in documents for candidate in document["candidates"]]
    if mode == "pilot":
        ids = [item for item in PILOT if item in present]
    else:
        ids = [item for item in (entry[0] for entry in CANDIDATES) if item in present] or present
    refs = []
    for candidate_id in ids:
        document, candidate, _batch = find_batch(documents, candidate_id, 0)
        del document
        for batch in candidate["batches"]:
            if mode == "resume" and batch["status"] != "pending":
                continue
            if mode == "pilot" and batch["status"] == "failed":
                continue
            if mode == "pilot" and batch["status"] == "ok":
                continue
            refs.append((candidate_id, batch["batch_index"]))
    return refs


def approvals_of(text: str) -> dict[str, bool]:
    pilot = False
    full = False
    for line in text.splitlines():
        if line.strip() == "pilot_approved: true":
            pilot = True
        if line.strip() == "full_resume_approved: true":
            full = True
    return {"pilot": pilot, "full": full}


def identity_problems(manifest: dict[str, Any]) -> list[str]:
    problems = []
    if manifest.get("prompt_sha256") != prompt_sha() or manifest.get("prompt_text") != prompts.P0:
        problems.append("프롬프트가 manifest와 다릅니다.")
    if manifest.get("logic_version") != LOGIC_VERSION or manifest.get("config_id") != CONFIG_ID:
        problems.append("설정 식별자가 manifest와 다릅니다.")
    recorded = {item["path"]: item["sha256"] for item in manifest.get("code") or []}
    for item in code_records():
        if recorded.get(item["path"]) != item["sha256"]:
            problems.append(f"코드 해시가 manifest와 다릅니다: {item['path']}")
    for item in manifest["input"]["candidates"]:
        if sha256_file(ROOT / item["path"]) != item["sha256"]:
            problems.append(f"입력 해시가 manifest와 다릅니다: {item['path']}")
    return problems


def command_extract(run_dir: Path) -> int:
    manifest = read_json(run_dir / "manifest.json")
    problems = identity_problems(manifest)
    if problems:
        for problem in problems:
            print_problem(problem, rel(run_dir / "manifest.json"), "extract")
        return 1
    documents = load_documents(run_dir)
    run = read_json(run_dir / "run.json")
    for key in ("decode_seconds", "resize_seconds", "inference_seconds", "render_seconds", "wall_seconds"):
        run["timing"][key] = Decimal(str(run["timing"][key]))
    existing = {}
    index_path = run_dir / "frames_index.json"
    if index_path.exists():
        existing = {item["frame_id"]: item for item in read_json(index_path).get("frames") or []}
    jobs = []
    seen: set[str] = set()
    for document in documents:
        for candidate in document["candidates"]:
            for frame in candidate["frames"]:
                if frame["frame_id"] in seen:
                    continue
                seen.add(frame["frame_id"])
                prior = existing.get(frame["frame_id"])
                if prior and prior.get("ok") and _files_match(prior):
                    _attach_frame(documents, frame["frame_id"], prior)
                    continue
                full_rel = f"tests/T4/runs/{run['run_id']}/frames/{document['video_id']}/s{frame['stream_index']}_pts{frame['pts']}_full.png"
                vlm_rel = f"tests/T4/runs/{run['run_id']}/frames/{document['video_id']}/s{frame['stream_index']}_pts{frame['pts']}_vlm.png"
                jobs.append({
                    "frame_id": frame["frame_id"],
                    "media_path": str(ROOT / document["media"]["path"]),
                    "stream_index": frame["stream_index"],
                    "pts": frame["pts"],
                    "video_seconds": frame["video_seconds"],
                    "time_base": frame["time_base"],
                    "source_width": document["media"]["width"],
                    "source_height": document["media"]["height"],
                    "full_path": str(ROOT / full_rel),
                    "vlm_path": str(ROOT / vlm_rel),
                    "full_rel": full_rel,
                    "vlm_rel": vlm_rel,
                })
    ffmpeg = manifest["runtime"]["ffmpeg"]["path"]

    def progress(done: int, total: int, label: str) -> None:
        print(f"extract {done}/{total} {label}", flush=True)

    started = time.monotonic()
    extracted = extract.extract_jobs(ffmpeg, jobs, on_progress=progress) if jobs else {"records": [], "decode_seconds": Decimal(0), "resize_seconds": Decimal(0)}
    by_id = {frame_id: item for frame_id, item in existing.items() if item.get("ok") and _files_match(item)}
    failures = []
    for record in extracted["records"]:
        if record["ok"]:
            stored = {
                "frame_id": record["frame_id"],
                "ok": True,
                "pts": record["pts"],
                "showinfo_pts": record["showinfo_pts"],
                "seek": record["seek"],
                "duration": record["duration"],
                "time_base_in": json_time_base(record["time_base_in"]),
                "time_base_out": json_time_base(record["time_base_out"]),
                "scale_filter": record["scale_filter"],
                "full": record["full"],
                "vlm": record["vlm"],
            }
            by_id[record["frame_id"]] = stored
            _attach_frame(documents, record["frame_id"], stored)
        else:
            failures.append({"frame_id": record["frame_id"], "ok": False, "error": record["error"], "pts": record["pts"]})
            by_id[record["frame_id"]] = failures[-1]
    run["timing"]["decode_seconds"] = run["timing"]["decode_seconds"] + extracted["decode_seconds"]
    run["timing"]["resize_seconds"] = run["timing"]["resize_seconds"] + extracted["resize_seconds"]
    run["timing"]["wall_seconds"] = run["timing"]["wall_seconds"] + Decimal(str(round(time.monotonic() - started, 6)))
    write_json(index_path, {"frames": list(by_id.values()), "problems": [item["error"] for item in failures]})
    sizes = {}
    if not failures:
        for document in documents:
            for candidate in document["candidates"]:
                for batch in candidate["batches"]:
                    built = build_request(candidate, batch, {})
                    batch["request_bytes"] = built["request_bytes"]
                    batch["text_chars"] = built["text_chars"]
                    batch["target_image_bytes"] = built["target_image_bytes"]
                    batch["context_image_bytes"] = built["context_image_bytes"]
                    batch["eligible_context_ids"] = []
                    sizes[batch["batch_id"]] = built["request_bytes"]
        write_json(run_dir / "request_plan.json", {"eligible_context_assumed": [], "batches": {
            batch["batch_id"]: {
                "request_bytes": batch["request_bytes"],
                "text_chars": batch["text_chars"],
                "target_image_bytes": batch["target_image_bytes"],
                "context_image_bytes": batch["context_image_bytes"],
            }
            for document in documents
            for candidate in document["candidates"]
            for batch in candidate["batches"]
        }})
    run["phase"] = "extracted" if not failures else "frame_error"
    run["run_status"] = "pending" if not failures else "failed"
    run["problems"] = [item["error"] for item in failures]
    run["counts"]["pending"] = sum(1 for document in documents for candidate in document["candidates"] for batch in candidate["batches"] if batch["status"] == "pending")
    save_documents(run_dir, documents)
    write_json(run_dir / "run.json", run)
    _write_views(run_dir, documents, run)
    print(f"extract frames {len(by_id)} failures {len(failures)} http 0", flush=True)
    return 1 if failures else 0


def _files_match(record: dict[str, Any]) -> bool:
    for key in ("full", "vlm"):
        info = record.get(key) or {}
        path = ROOT / str(info.get("path") or "")
        if not path.is_file() or sha256_file(path) != info.get("sha256"):
            return False
    return True


def _attach_frame(documents: list[dict[str, Any]], frame_id: str, record: dict[str, Any]) -> None:
    image = {"full": record["full"], "vlm": record["vlm"]}
    for document in documents:
        for candidate in document["candidates"]:
            for frame in candidate["frames"]:
                if frame["frame_id"] == frame_id:
                    frame["full"] = image["full"]
                    frame["vlm"] = image["vlm"]


def load_sizes(run_dir: Path) -> dict[str, int]:
    path = run_dir / "request_plan.json"
    if not path.is_file():
        return {}
    payload = read_json(path)
    return {key: int(value["request_bytes"]) for key, value in payload["batches"].items()}


def prepare_run(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]] | None:
    if not (run_dir / "manifest.json").is_file():
        print_problem("run이 없습니다.", str(run_dir), run_dir.name)
        return None
    manifest = read_json(run_dir / "manifest.json")
    problems = identity_problems(manifest)
    if problems:
        for problem in problems:
            print_problem(problem, rel(run_dir / "manifest.json"), run_dir.name)
        return None
    documents = load_documents(run_dir)
    run = read_json(run_dir / "run.json")
    for key in ("decode_seconds", "resize_seconds", "inference_seconds", "render_seconds", "wall_seconds"):
        run["timing"][key] = Decimal(str(run["timing"][key]))
    return manifest, documents, run


def frames_ready(run_dir: Path, documents: list[dict[str, Any]]) -> str | None:
    index_path = run_dir / "frames_index.json"
    if not index_path.is_file():
        return "프레임 추출 결과가 없습니다."
    payload = read_json(index_path)
    if payload.get("problems"):
        return "frame_error가 있어 추론을 시작하지 않습니다."
    needed = {frame["frame_id"] for document in documents for candidate in document["candidates"] for frame in candidate["frames"]}
    have = {item["frame_id"] for item in payload.get("frames") or [] if item.get("ok")}
    if needed != have:
        return "추출된 프레임이 계획과 다릅니다."
    for frame in (frame for document in documents for candidate in document["candidates"] for frame in candidate["frames"]):
        if not frame.get("vlm") or not _files_match({"full": frame["full"], "vlm": frame["vlm"]}):
            return f"{frame['frame_id']} 이미지 연결이 없습니다."
    return None


def command_execute(run_dir: Path, mode: str) -> int:
    prepared = prepare_run(run_dir)
    if prepared is None:
        return 1
    _manifest, documents, run = prepared
    if mode in {"smoke", "pilot", "resume"}:
        problem = frames_ready(run_dir, documents)
        if problem:
            print_problem(problem, rel(run_dir / "frames_index.json"), mode)
            return 1
    decision_path = run_dir / "review_decision.md"
    approved = approvals_of(decision_path.read_text(encoding="utf-8")) if decision_path.exists() else {"pilot": False, "full": False}
    if mode == "pilot" and not approved["pilot"]:
        print_problem("pilot 확대 승인이 review_decision.md에 없습니다.", rel(decision_path), "pilot")
        return 2
    if mode == "resume" and not approved["full"]:
        print_problem("12개 범위 재개 승인이 review_decision.md에 없습니다.", rel(decision_path), "resume")
        return 2
    try:
        refs = mode_refs(documents, mode, load_sizes(run_dir) if mode == "smoke" else None)
    except RuntimeError as exc:
        print_problem(str(exc), rel(run_dir), mode)
        return 1
    if mode == "smoke":
        run["smoke"] = {"refs": [{"candidate_id": candidate_id, "batch_index": index} for candidate_id, index in refs], "max_http_requests": 2}
    started = time.monotonic()
    outcome = execute_selected(run_dir, documents, run, refs, InferClient())
    run["timing"]["wall_seconds"] = run["timing"]["wall_seconds"] + Decimal(str(round(time.monotonic() - started, 6)))
    run["phase"] = mode
    refresh_counts(run, documents)
    run["usage"] = sum_usage(run_dir)
    run["run_status"] = rollup_status([batch["status"] for document in documents for candidate in document["candidates"] for batch in candidate["batches"]])
    run["finished_at"] = utc_now()
    save_documents(run_dir, documents)
    write_json(run_dir / "run.json", run)
    _write_views(run_dir, documents, run)
    if mode == "smoke":
        _write_decision(run_dir, documents, run, outcome)
    print(f"{mode} {outcome} http {run['counts']['http_attempts']} status {run['run_status']}", flush=True)
    return 0 if outcome == "done" else 1


def refresh_counts(run: dict[str, Any], documents: list[dict[str, Any]]) -> None:
    counts = {"new": 0, "reused": 0, "failed": 0, "pending": 0}
    for document in documents:
        for candidate in document["candidates"]:
            for batch in candidate["batches"]:
                if batch["status"] == "failed":
                    counts["failed"] += 1
                elif batch["status"] == "pending":
                    counts["pending"] += 1
                elif batch.get("reused_from"):
                    counts["reused"] += 1
                elif batch["status"] == "ok":
                    counts["new"] += 1
    counts["http_attempts"] = run["counts"]["http_attempts"]
    counts["context_reinputs"] = run["counts"]["context_reinputs"]
    run["counts"] = counts


def sum_usage(run_dir: Path) -> dict[str, Any] | None:
    calls = run_dir / "calls"
    if not calls.is_dir():
        return None
    totals: dict[str, int] = {}
    seen = False
    for path in sorted(calls.glob("*.json")):
        record = read_json(path)
        usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        seen = True
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals or ({"incomplete": True} if seen else None)


def command_render(run_dir: Path) -> int:
    prepared = prepare_run(run_dir)
    if prepared is None:
        return 1
    _manifest, documents, run = prepared
    started = time.monotonic()
    save_documents(run_dir, documents)
    run["timing"]["render_seconds"] = run["timing"]["render_seconds"] + Decimal(str(round(time.monotonic() - started, 6)))
    write_json(run_dir / "run.json", run)
    _write_views(run_dir, documents, run)
    print(f"render {rel(run_dir)}", flush=True)
    return 0


def _write_views(run_dir: Path, documents: list[dict[str, Any]], run: dict[str, Any]) -> None:
    started = time.monotonic()
    manifest = read_json(run_dir / "manifest.json")
    rows = []
    for document in documents:
        atomic_write(run_dir / document["video_id"] / "review.md", review.render_video(document))
        for candidate in document["candidates"]:
            counts = {"ok": 0, "failed": 0, "pending": 0}
            for batch in candidate["batches"]:
                counts[batch["status"]] = counts.get(batch["status"], 0) + 1
            rows.append({
                "candidate_id": candidate["candidate_id"],
                "status": candidate["status"],
                "ok": counts.get("ok", 0),
                "failed": counts.get("failed", 0),
                "pending": counts.get("pending", 0),
                "confirmed": len(candidate["selected_confirmed"]),
                "uncertain": len(candidate["retained_uncertain"]),
                "review": f"{document['video_id']}/review.md",
            })
    summary = {
        "run_id": run["run_id"],
        "phase": run["phase"],
        "run_status": run["run_status"],
        "quality_status": run["quality_status"],
        "config_id": CONFIG_ID,
        "logic_version": LOGIC_VERSION,
        "prompt_id": prompts.PROMPT_ID,
        "video_count": len(documents),
        "candidate_count": len(rows),
        "out_of_scope_count": len(manifest["input"]["out_of_scope"]),
        "out_of_scope": manifest["input"]["out_of_scope"],
        "planned_requests": manifest["plan"]["requests"],
        "candidates": rows,
        "counts": run["counts"],
        "timing": {key: format(Decimal(str(value)), "f") for key, value in run["timing"].items()},
        "usage": run.get("usage"),
    }
    atomic_write(run_dir / "index.md", review.render_index(summary))
    structural = structural_validation(documents)
    write_json(run_dir / "validation.json", structural)
    run["timing"]["render_seconds"] = Decimal(str(run["timing"]["render_seconds"])) + Decimal(str(round(time.monotonic() - started, 6)))


def structural_validation(documents: list[dict[str, Any]]) -> dict[str, Any]:
    problems = []
    for document in documents:
        for candidate in document["candidates"]:
            frames = candidate["frames"]
            pts = [frame["pts"] for frame in frames]
            if len(pts) != len(set(pts)):
                problems.append(f"{candidate['candidate_id']} 동일 PTS를 두 번 판정합니다.")
            covered = []
            for batch_index, batch in enumerate(candidate["batches"]):
                covered.extend(batch["target_ids"])
                if batch_index == 0 and batch["context_ids"]:
                    problems.append(f"{batch['batch_id']} 첫 묶음에 context가 있습니다.")
                if batch_index > 0:
                    previous = candidate["batches"][batch_index - 1]["target_ids"][-1]
                    if batch["context_ids"] != [previous]:
                        problems.append(f"{batch['batch_id']} context가 직전 target이 아닙니다.")
                if set(batch["context_ids"]) & set(batch["target_ids"]):
                    problems.append(f"{batch['batch_id']} context가 target에 포함됩니다.")
                if batch["status"] == "ok":
                    decisions = batch.get("decisions") or []
                    checked, error = validate_decisions({"frames": decisions}, batch["target_ids"], batch.get("eligible_context_ids") or [])
                    if error or checked is None:
                        problems.append(f"{batch['batch_id']} {error}")
            if covered != [frame["frame_id"] for frame in frames]:
                problems.append(f"{candidate['candidate_id']} target 순서가 프레임과 다릅니다.")
            sample_ids = [index for frame in frames for index in frame["sample_indices"]]
            segment_ids = [index for segment in candidate["segments"] for index in segment["analysis_sample_indices"]]
            if sorted(sample_ids) != sorted(segment_ids):
                problems.append(f"{candidate['candidate_id']} 구간 소속이 프레임과 다릅니다.")
            source = candidate["source_candidate"]
            if source.get("candidate_source") == "budget_guard" and source.get("model_inference_success") is not False:
                problems.append(f"{candidate['candidate_id']} 상위 실패 출처가 바뀌었습니다.")
    counts = {"ok": 0, "failed": 0, "pending": 0}
    for document in documents:
        for candidate in document["candidates"]:
            for batch in candidate["batches"]:
                counts[batch["status"]] = counts.get(batch["status"], 0) + 1
    return {
        "ok": not problems,
        "quality_score": None,
        "problems": problems,
        "counts": counts,
        "note": "구조 검사이며 선정 품질 점수가 아니다.",
    }


def _write_decision(run_dir: Path, documents: list[dict[str, Any]], run: dict[str, Any], outcome: str) -> None:
    manifest_hash = sha256_file(run_dir / "manifest.json")
    lines = [
        "# T4 smoke 관찰",
        "",
        f"- 시각: {utc_now()}",
        f"- manifest_sha256: `{manifest_hash}`",
        "- 검토자: 구현 실행의 smoke 기록. 품질 채택 권한으로 기록하지 않음",
        "- quality_status: pending_review",
        "- 채택: 아님",
        "- pilot 시작: 아님",
        "pilot_approved: false",
        "full_resume_approved: false",
        f"- smoke 결과: `{outcome}`",
        f"- HTTP 시도: {run['counts']['http_attempts']}",
        f"- 신규/재사용/실패/pending: {run['counts']['new']}/{run['counts']['reused']}/{run['counts']['failed']}/{run['counts']['pending']}",
        f"- usage: `{run.get('usage')}`",
        "- VRAM: null",
        "- 체크포인트, 컨텍스트 한도, 시각 토큰, 서버 이미지 한도: unknown",
        "- image_use_confirmed: 전송 성공만으로 확정하지 않음. 아래 호출을 원본 이미지와 대조해야 함",
        "",
        "## 호출",
        "",
    ]
    calls = run_dir / "calls"
    if calls.is_dir():
        for path in sorted(calls.glob("*.json")):
            record = read_json(path)
            lines.append(f"- `{record['call_ref']}` HTTP {record.get('http_status')} finish `{record.get('finish_reason')}` error `{record.get('error')}`")
            for image in record.get("transmitted_images") or []:
                lines.append(f"  - {image['role']} `{image['frame_id']}` `{image['path']}`")
            if isinstance(record.get("parsed"), list):
                for item in record["parsed"]:
                    lines.append(f"  - {item['frame_id']} {item['decision']} duplicate_of={item['duplicate_of']} reason={item['reason']}")
    lines.extend([
        "",
        "이 파일은 smoke에서 저장한 범위만 다룬다. exit 0이어도 pilot이나 전체 실행을 승인하지 않는다.",
        "",
    ])
    atomic_write(run_dir / "review_decision.md", "\n".join(lines))


def command_check() -> int:
    failures: list[str] = []

    def expect(condition: bool, label: str) -> None:
        if not condition:
            failures.append(label)

    expect(planned_request_count(0) == 0 and planned_request_count(1) == 1 and planned_request_count(6) == 1, "small batches")
    expect(planned_request_count(7) == 2 and planned_request_count(11) == 2 and planned_request_count(34) == 7, "larger batches")
    ids = [f"v:s0:pts{index}" for index in range(7)]
    batches = build_batches(ids)
    expect(batches[0]["context_ids"] == [] and batches[0]["target_ids"] == ids[:6], "first batch")
    expect(batches[1]["context_ids"] == [ids[5]] and batches[1]["target_ids"] == [ids[6]], "second context")
    expect(build_batches([f"other:s0:pts{index}" for index in range(3)])[0]["context_ids"] == [], "candidate context isolated")
    expect(frame_id_of("H2", 0, 10) == "H2:s0:pts10", "frame id")
    expect(json_time_base((1, 16000)) == {"numerator": 1, "denominator": 16000} and json_time_base(None) is None, "time base json")
    expect('"numerator": 1' in dump_text({"time_base_in": json_time_base((1, 16000))}), "time base dump")
    expect(video_seconds_of(SMOKE_PTS, 1, 16000) == Decimal("1247.913000000"), "anchor seconds")
    expect(video_seconds_of(SMOKE_PTS, 1, 16000) + Decimal(5) != video_seconds_of(SMOKE_PTS, 1, 16000), "offset not included")
    frames = build_frames(_synthetic_candidate())
    expect(len(frames) == 1 and frames[0]["sample_indices"] == [0, 1], "same pts once")
    expect(frames[0]["video_seconds"] != frames[0]["requests"][0]["requested_video_seconds"], "request time separate")
    targets = ["a", "b"]
    good, error = validate_decisions({"frames": [
        {"frame_id": "a", "decision": "keep", "reason": "표가 보인다.", "duplicate_of": None},
        {"frame_id": "b", "decision": "drop", "reason": "같은 표다.", "duplicate_of": "a"},
    ]}, targets, [])
    expect(error is None and good is not None and SELECTION["keep"] == "selected_confirmed", "duplicate keep")
    _bad, error = validate_decisions({"frames": [
        {"frame_id": "a", "decision": "keep", "reason": "표", "duplicate_of": "b"},
        {"frame_id": "b", "decision": "drop", "reason": "같음", "duplicate_of": None},
    ]}, targets, [])
    expect(error is not None, "keep duplicate rejected")
    _bad, error = validate_decisions({"frames": [
        {"frame_id": "a", "decision": "drop", "reason": "같음", "duplicate_of": "a"},
        {"frame_id": "b", "decision": "keep", "reason": "표", "duplicate_of": None},
    ]}, targets, [])
    expect(error is not None, "self reference")
    _bad, error = validate_decisions({"frames": [
        {"frame_id": "a", "decision": "uncertain", "reason": "흐림", "duplicate_of": None},
        {"frame_id": "b", "decision": "drop", "reason": "같음", "duplicate_of": "a"},
    ]}, targets, [])
    expect(error is not None, "uncertain reference")
    _bad, error = validate_decisions({"frames": [
        {"frame_id": "a", "decision": "drop", "reason": "같음", "duplicate_of": "b"},
        {"frame_id": "b", "decision": "drop", "reason": "같음", "duplicate_of": "c"},
    ]}, targets, ["c"])
    expect(error is not None, "chain")
    _good, error = validate_decisions({"frames": [
        {"frame_id": "a", "decision": "drop", "reason": "이전 화면과 같다.", "duplicate_of": "c"},
        {"frame_id": "b", "decision": "keep", "reason": "새 수식", "duplicate_of": None},
    ]}, targets, ["c"])
    expect(error is None, "eligible context")
    expect(eligible_context_ids([], {}) == [] and eligible_context_ids(["c"], {"c": "drop"}) == [] and eligible_context_ids(["c"], {"c": "keep"}) == ["c"], "eligible rule")
    _bad, error = parse_strict_object('{"frames": [], "frames": []}')
    expect(error is not None, "duplicate key")
    _bad, error = parse_strict_object('{"frames": NaN}')
    expect(error is not None, "nan")
    _bad, error = parse_strict_object('```json\n{"frames": []}\n```')
    expect(error is not None, "fence")
    transport = {
        "http_status": 200,
        "response_body": json.dumps({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "", "reasoning": json.dumps({"frames": []})},
            }],
        }),
        "error": None,
    }
    interpreted = interpret_transport(transport, [], [])
    expect(not interpreted["ok"], "reasoning is not content")
    expect(rollup_status(["ok", "pending"]) == "partial" and rollup_status(["failed", "pending"]) == "failed", "rollup")
    expect(rollup_status(["pending", "pending"]) == "pending" and rollup_status(["ok", "ok"]) == "ok", "rollup ends")
    expect(approvals_of("pilot_approved: false\nfull_resume_approved: false\n") == {"pilot": False, "full": False}, "approval false")
    expect(approvals_of("pilot_approved: true\n")["pilot"] and not approvals_of("note pilot_approved: true inside")["pilot"], "approval exact")
    groups = extract.clusters_for([
        {"pts": 1, "video_seconds": Decimal(0)},
        {"pts": 2, "video_seconds": Decimal("0.5")},
        {"pts": 3, "video_seconds": Decimal("4")},
    ])
    expect(len(groups) == 2 and [item["pts"] for item in groups[1]] == [3], "cluster gap")
    expect("min(1280,iw)" in extract.scale_filter(1920, 1080) and "min(1280,ih)" in extract.scale_filter(720, 1280), "scale filter")
    with __import__("tempfile").TemporaryDirectory() as temporary:
        path = Path(temporary) / "sample.json"
        atomic_write(path, dump_text({"value": Decimal("1.25"), "ok": True}))
        expect(read_json(path) == {"value": 1.25, "ok": True}, "atomic json")
        lock_dir = Path(temporary) / "run"
        lock_dir.mkdir()
        with run_lock(lock_dir):
            try:
                with run_lock(lock_dir):
                    expect(False, "lock overlap")
            except RuntimeError:
                expect(True, "lock overlap")
        _check_execution(Path(temporary), expect)
    if failures:
        for label in failures:
            print_problem("합성 검사를 통과하지 못했습니다.", "tests/T4/run_t4.py", label)
        return 1
    print("check ok", flush=True)
    return 0


def _synthetic_candidate() -> dict[str, Any]:
    return {
        "candidate_id": "vid:u0:c0",
        "segments": [
            {"segment_index": 0, "analysis_sample_indices": [0, 1]},
        ],
        "requests": [
            {"request_index": 0, "requested_video_seconds": 1, "kind": "grid"},
            {"request_index": 1, "requested_video_seconds": 2, "kind": "grid"},
        ],
        "samples": [
            {"sample_index": 0, "pts": 10, "stream_index": 0, "time_base": {"numerator": 1, "denominator": 1}, "request_indices": [0], "video_seconds": 10},
            {"sample_index": 1, "pts": 10, "stream_index": 0, "time_base": {"numerator": 1, "denominator": 1}, "request_indices": [1], "video_seconds": 10},
        ],
    }


def _check_execution(root: Path, expect) -> None:
    run_dir = root / "mini"
    run_dir.mkdir()
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    image = root / "tiny.png"
    image.write_bytes(png)
    digest = sha256_bytes(png)
    relative = str(image)
    frames = []
    for index in range(12):
        frames.append({
            "frame_id": f"vid:s0:pts{index}",
            "sample_indices": [index],
            "requests": [{"request_index": index, "requested_video_seconds": Decimal(index), "kind": "grid", "sample_index": index}],
            "stream_index": 0,
            "pts": index,
            "time_base": {"numerator": 1, "denominator": 1},
            "video_seconds": Decimal(index),
            "segment_index": 0,
            "full": {"path": relative, "sha256": digest, "width": 1, "height": 1, "bytes": len(png)},
            "vlm": {"path": relative, "sha256": digest, "width": 1, "height": 1, "bytes": len(png)},
            "decision": None,
            "reason": None,
            "duplicate_of": None,
            "selection": None,
            "execution_state": "pending",
            "batch_id": None,
        })
    batches = []
    for index, built in enumerate(build_batches([frame["frame_id"] for frame in frames])):
        batch_id = f"vid:u0:c0#b{index}"
        for frame_id in built["target_ids"]:
            next(frame for frame in frames if frame["frame_id"] == frame_id)["batch_id"] = batch_id
        batches.append({
            "batch_id": batch_id,
            "batch_index": index,
            "context_ids": built["context_ids"],
            "target_ids": built["target_ids"],
            "eligible_context_ids": [],
            "status": "pending",
            "call_ref": None,
            "reused_from": None,
            "http_attempts": 0,
            "decisions": None,
            "error": None,
            "request_bytes": None,
            "text_chars": None,
            "target_image_bytes": None,
            "context_image_bytes": None,
        })
    candidate = {
        "candidate_id": "vid:u0:c0",
        "source_t3_sha256": "abc",
        "source_candidate": {"candidate_source": "thinking_on", "model_inference_success": True},
        "sentences": [{"sentence_index": 0, "text": "원문", "audio_start": Decimal("0.1"), "audio_end": Decimal("0.2")}],
        "frames": frames,
        "batches": batches,
        "segments": [{"segment_index": 0, "video_start": Decimal(0), "video_end": Decimal(12), "analysis_sample_indices": list(range(12)), "frame_ids": [frame["frame_id"] for frame in frames], "selected_confirmed": [], "retained_uncertain": [], "not_selected": [], "pending": [], "failed": []}],
        "selected_confirmed": [],
        "retained_uncertain": [],
        "not_selected": [],
        "status": "pending",
        "problems": [],
        "warnings": [],
        "validation": {"ok": True, "problems": []},
        "text_ref": {},
        "requested_range": {},
        "effective_range": {},
    }
    document = {
        "video_id": "vid",
        "media": {"path": "data/vid/source/video.mp4", "recorded_sha256": "hash", "size": 1, "mtime_ns": 1, "bytes_rehashed": False, "offset_reapplied": False},
        "source_url": "https://example.invalid/vid",
        "candidates": [candidate],
        "status": "pending",
        "problems": [],
        "warnings": [],
        "validation": {"ok": True, "problems": []},
        "schema_version": SCHEMA_VERSION,
        "config_id": CONFIG_ID,
        "logic_version": LOGIC_VERSION,
        "prompt_id": prompts.PROMPT_ID,
        "prompt_sha256": prompt_sha(),
        "path_reference": "repository_root",
    }
    run = empty_run("mini")
    client = _FakeClient(fail_on=2)
    outcome = execute_selected(run_dir, [document], run, [("vid:u0:c0", 0), ("vid:u0:c0", 1), ("vid:u0:c0", 2)], client)
    expect(outcome == "failed" and client.posts == 2 and candidate["batches"][2]["status"] == "pending", "stop on first failure")
    expect(candidate["batches"][0]["status"] == "ok" and candidate["batches"][1]["status"] == "failed", "saved failure")
    posts_before = client.posts
    again = execute_batch(run_dir, run, document, candidate, candidate["batches"][0], client, allow_http=True)
    expect(again == "skipped" and client.posts == posts_before, "ok batch not recalled")
    candidate["batches"][0]["status"] = "pending"
    reused = execute_batch(run_dir, run, document, candidate, candidate["batches"][0], client, allow_http=True)
    expect(reused == "reused" and client.posts == posts_before and candidate["batches"][0]["reused_from"], "reuse without http")
    resume_refs = mode_refs([document], "resume")
    expect(resume_refs == [("vid:u0:c0", 2)], "resume pending only")


class _FakeClient:
    def __init__(self, fail_on: int) -> None:
        self.fail_on = fail_on
        self.posts = 0

    def post(self, wire: bytes) -> dict[str, Any]:
        self.posts += 1
        if self.posts == self.fail_on:
            return {"http_status": 500, "response_body": "no", "elapsed_seconds": Decimal("0.01"), "error": None}
        body = json.loads(wire.decode("utf-8"))
        text = body["messages"][0]["content"][0]["text"]
        marker = "응답할 targets: "
        line = next(item for item in text.splitlines() if item.startswith(marker))
        targets = json.loads(line[len(marker):])
        parsed = {"frames": [
            {"frame_id": frame_id, "decision": "keep", "reason": "합성 검사 화면", "duplicate_of": None}
            for frame_id in targets
        ]}
        outer = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(parsed, ensure_ascii=False)}}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}}
        return {"http_status": 200, "response_body": json.dumps(outer, ensure_ascii=False), "elapsed_seconds": Decimal("0.02"), "error": None}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="T4 분석 대상 프레임 선정")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    modes = [args.check, args.plan, args.extract, args.smoke, args.pilot, args.resume, args.render_only]
    if sum(bool(mode) for mode in modes) != 1:
        print_problem("실행 모드를 하나만 지정해야 합니다.", "tests/T4/run_t4.py", "cli")
        return 2
    if args.plan and args.run_id:
        print_problem("--plan과 --run-id는 함께 쓰지 않습니다.", "tests/T4/run_t4.py", "cli")
        return 2
    if args.check:
        return command_check()
    if args.plan:
        return command_plan()
    if not args.run_id:
        print_problem("run_id가 필요합니다.", "tests/T4/run_t4.py", "cli")
        return 2
    run_dir = RUNS_ROOT / args.run_id
    try:
        with run_lock(run_dir):
            if args.extract:
                return command_extract(run_dir)
            if args.render_only:
                return command_render(run_dir)
            mode = "smoke" if args.smoke else "pilot" if args.pilot else "resume"
            return command_execute(run_dir, mode)
    except RuntimeError as exc:
        print_problem(str(exc), str(run_dir), args.run_id)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
