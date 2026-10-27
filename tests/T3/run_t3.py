"""T3 화면 변화 탐색 실행. 검출은 detector, 표시는 review를 사용한다."""

from __future__ import annotations

import argparse
import fcntl
import os
import secrets
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator

import detector
import review

ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "tests" / "T3" / "runs"
INPUT_RELATIVE = "tests/T2/runs/20261020T184601Z_b6d662/budget_guard"
T2_VALIDATION_RELATIVE = "tests/T2/runs/20261021T_delegated_review/validation.json"
T2_REVIEW_RELATIVE = "tests/T2/runs/20261021T_delegated_review/review.md"
T2_CLOSURE_RELATIVE = "local/t2_execution.md"
VIDEO_IDS = (
    "7EJ-xotXiSE",
    "CvuE-jyg-N8",
    "Fw-8hZhnH3Q",
    "H2WN130OzLg",
    "bjDkfjSmXVo",
    "hGVL9XRU9IQ",
    "iEMuJiVY9Oc",
    "iVelddb1qQ4",
)
EXPECTED_SHA256 = {
    "7EJ-xotXiSE": "48da938db390781d9a951cd60bece7885a3779098f5679df3821de9b270b8c58",
    "CvuE-jyg-N8": "33731bbe8eeae3b31e249d2eb228f30404e421d632d08235c125e2c2bdee65a6",
    "Fw-8hZhnH3Q": "e6f8e14ae64621b321a8e38db3d830bc9d4984a5599888588fb3dd88e2329b65",
    "H2WN130OzLg": "f165fba2840e887c888849751d7450d613f5bf103653482460a0074b215e0606",
    "bjDkfjSmXVo": "ce4d0d8ffc042cb0338a958188f050ec697988d1d41baa01a535ec1f0adfbe09",
    "hGVL9XRU9IQ": "16e9efe294e4a415f2b7bae1b4f5d12320048207a31decf2579c5e06a29b6c03",
    "iEMuJiVY9Oc": "b0d18e794acaaf3ec545a710a470955cc23df7d1e7ad6b1c72f3e1b4a73b3f3d",
    "iVelddb1qQ4": "64461c763ec0d255d7eb2bb2d4653dc31426d7436da582f6df1f5b1cdf03aa20",
}
EXPECTED_COUNTS = {
    "7EJ-xotXiSE": (26, 0),
    "CvuE-jyg-N8": (28, 0),
    "Fw-8hZhnH3Q": (18, 0),
    "H2WN130OzLg": (10, 4),
    "bjDkfjSmXVo": (13, 0),
    "hGVL9XRU9IQ": (16, 0),
    "iEMuJiVY9Oc": (14, 0),
    "iVelddb1qQ4": (14, 1),
}
BUDGET_GUARD_IDS = {
    "H2WN130OzLg:u1:c0",
    "H2WN130OzLg:u3:c0",
    "H2WN130OzLg:u7:c0",
    "H2WN130OzLg:u25:c0",
    "iVelddb1qQ4:u21:c0",
}
PILOT_KEYS = {
    ("7EJ-xotXiSE", 0, 0),
    ("7EJ-xotXiSE", 11, 0),
    ("CvuE-jyg-N8", 7, 0),
    ("CvuE-jyg-N8", 59, 0),
    ("Fw-8hZhnH3Q", 0, 0),
    ("Fw-8hZhnH3Q", 1, 0),
    ("Fw-8hZhnH3Q", 2, 0),
    ("H2WN130OzLg", 1, 0),
    ("H2WN130OzLg", 3, 0),
    ("H2WN130OzLg", 7, 0),
    ("H2WN130OzLg", 25, 0),
    ("bjDkfjSmXVo", 1, 0),
    ("hGVL9XRU9IQ", 5, 0),
    ("iEMuJiVY9Oc", 4, 0),
    ("iEMuJiVY9Oc", 14, 0),
    ("iVelddb1qQ4", 1, 0),
    ("iVelddb1qQ4", 21, 0),
}
B00_BASELINE_RELATIVE = "tests/T3/runs/20261021T200213Z_cae64b"
B00_BASELINE_MANIFEST_SHA256 = "66b2548a74b7c403e2b09a8b42d9e00ec87b82951a0f32a316a43bc733f9f51f"
B00_SOURCE_SNAPSHOT = "tests/T3/source_snapshots/b00_pre_b01_20261022T055034Z"
B01_BASELINE_RELATIVE = "tests/T3/runs/20261022T055753Z_b11df4"
B01_BASELINE_MANIFEST_SHA256 = "978aa541d075b4aa875e1cf522dc04ce508b91f9f4eeec4e677a3ebbc58c8040"
B01_SOURCE_SNAPSHOT = "tests/T3/source_snapshots/b01_pre_b02_20261022T080800Z"
B02_BASELINE_RELATIVE = "tests/T3/runs/20261022T081315Z_d568e1"
B02_BASELINE_MANIFEST_SHA256 = "786532d8946b1a7278ef8bdea871d239a69eceab717d2d05ef4ddc62f29ebb3d"
B02_SOURCE_SNAPSHOT = "tests/T3/source_snapshots/b02_pre_b03_20261022T090422Z"
B01_FULL_BASELINE_RELATIVE = "tests/T3/runs/20261022T111916Z_bf47f5"
B01_FULL_BASELINE_MANIFEST_SHA256 = "0b0a12cdc3be484b115e20c8016a3467b73f065c4103a9bc6854e165914172ff"
B04_SOURCE_SNAPSHOT = "tests/T3/source_snapshots/b01full_pre_b04_20261024T163258Z"
B04_RUN_RELATIVE = "tests/T3/runs/20261024T163822Z_63a911"
B04_RUN_MANIFEST_SHA256 = "dfd7218c86da550f43e2a2b1fb9f78be92a52a6b6a0100b03649ac04790a0b0a"
B05_SOURCE_SNAPSHOT = "tests/T3/source_snapshots/b04_pre_b05_20261024T174905Z"
B05_RUN_RELATIVE = "tests/T3/runs/20261024T175204Z_bdfca4"
B05_RUN_MANIFEST_SHA256 = "33dfff0fd9b83283dd0975328178f1d20191b5e6ae239fd9c88fa6f618f6be95"
B06_SOURCE_SNAPSHOT = "tests/T3/source_snapshots/b05_pre_b06_20261025T033900Z"
CODE_SOURCES = (
    "tests/T3/run_t3.py",
    "tests/T3/detector.py",
    "tests/T3/review.py",
)


def source_inventory() -> dict[str, Any]:
    rows = [{"path": relative, "sha256": detector.sha256_file(ROOT / relative)} for relative in CODE_SOURCES]
    by_path = {row["path"]: row["sha256"] for row in rows}
    return {
        "sources": rows,
        "detector_sha256": by_path["tests/T3/detector.py"],
        "review_sha256": by_path["tests/T3/review.py"],
        "runner_sha256": by_path["tests/T3/run_t3.py"],
        "git": None,
    }


def comparison_baseline() -> dict[str, Any]:
    manifest_path = ROOT / B00_BASELINE_RELATIVE / "manifest.json"
    digest = detector.sha256_file(manifest_path)
    if digest != B00_BASELINE_MANIFEST_SHA256:
        raise detector.T3Error(
            "B00 manifest 해시가 기준과 다릅니다.",
            stage="baseline",
            details={"expected": B00_BASELINE_MANIFEST_SHA256, "actual": digest},
        )
    snapshot = detector.loads((ROOT / B00_SOURCE_SNAPSHOT / "snapshot.json").read_text(encoding="utf-8"))
    return {
        "run_id": "20261021T200213Z_cae64b",
        "path": B00_BASELINE_RELATIVE,
        "manifest_sha256": digest,
        "source_snapshot": {
            "path": B00_SOURCE_SNAPSHOT,
            "confirmed_at": snapshot["confirmed_at"],
            "files": snapshot["files"],
        },
    }


def comparison_baseline_b02() -> dict[str, Any]:
    b00 = comparison_baseline()
    manifest_path = ROOT / B01_BASELINE_RELATIVE / "manifest.json"
    digest = detector.sha256_file(manifest_path)
    if digest != B01_BASELINE_MANIFEST_SHA256:
        raise detector.T3Error(
            "B01 manifest 해시가 기준과 다릅니다.",
            stage="baseline",
            details={"expected": B01_BASELINE_MANIFEST_SHA256, "actual": digest},
        )
    snapshot = detector.loads((ROOT / B01_SOURCE_SNAPSHOT / "snapshot.json").read_text(encoding="utf-8"))
    return {
        "run_id": "20261022T055753Z_b11df4",
        "path": B01_BASELINE_RELATIVE,
        "manifest_sha256": digest,
        "b00": {
            "run_id": b00["run_id"],
            "path": b00["path"],
            "manifest_sha256": b00["manifest_sha256"],
        },
        "source_snapshot": {
            "path": B01_SOURCE_SNAPSHOT,
            "confirmed_at": snapshot["confirmed_at"],
            "files": snapshot["files"],
        },
    }


def comparison_baseline_b03() -> dict[str, Any]:
    b01 = comparison_baseline_b02()
    manifest_path = ROOT / B02_BASELINE_RELATIVE / "manifest.json"
    digest = detector.sha256_file(manifest_path)
    if digest != B02_BASELINE_MANIFEST_SHA256:
        raise detector.T3Error(
            "B02 manifest 해시가 기준과 다릅니다.",
            stage="baseline",
            details={"expected": B02_BASELINE_MANIFEST_SHA256, "actual": digest},
        )
    snapshot = detector.loads((ROOT / B02_SOURCE_SNAPSHOT / "snapshot.json").read_text(encoding="utf-8"))
    return {
        "run_id": b01["run_id"],
        "path": b01["path"],
        "manifest_sha256": b01["manifest_sha256"],
        "b00": b01["b00"],
        "b02": {
            "run_id": "20261022T081315Z_d568e1",
            "path": B02_BASELINE_RELATIVE,
            "manifest_sha256": digest,
        },
        "source_snapshot": {
            "path": B02_SOURCE_SNAPSHOT,
            "confirmed_at": snapshot["confirmed_at"],
            "files": snapshot["files"],
        },
    }


def comparison_baseline_b04() -> dict[str, Any]:
    b00 = comparison_baseline()
    manifest_path = ROOT / B01_FULL_BASELINE_RELATIVE / "manifest.json"
    digest = detector.sha256_file(manifest_path)
    if digest != B01_FULL_BASELINE_MANIFEST_SHA256:
        raise detector.T3Error(
            "B01 전체 manifest 해시가 기준과 다릅니다.",
            stage="baseline",
            details={"expected": B01_FULL_BASELINE_MANIFEST_SHA256, "actual": digest},
        )
    snapshot = detector.loads((ROOT / B04_SOURCE_SNAPSHOT / "snapshot.json").read_text(encoding="utf-8"))
    return {
        "run_id": "20261022T111916Z_bf47f5",
        "path": B01_FULL_BASELINE_RELATIVE,
        "manifest_sha256": digest,
        "b00": {
            "run_id": b00["run_id"],
            "path": b00["path"],
            "manifest_sha256": b00["manifest_sha256"],
        },
        "source_snapshot": {
            "path": B04_SOURCE_SNAPSHOT,
            "confirmed_at": snapshot["confirmed_at"],
            "files": snapshot["files"],
        },
    }


def comparison_baseline_b05() -> dict[str, Any]:
    b04_view = comparison_baseline_b04()
    manifest_path = ROOT / B04_RUN_RELATIVE / "manifest.json"
    digest = detector.sha256_file(manifest_path)
    if digest != B04_RUN_MANIFEST_SHA256:
        raise detector.T3Error(
            "B04 manifest 해시가 기준과 다릅니다.",
            stage="baseline",
            details={"expected": B04_RUN_MANIFEST_SHA256, "actual": digest},
        )
    snapshot = detector.loads((ROOT / B05_SOURCE_SNAPSHOT / "snapshot.json").read_text(encoding="utf-8"))
    return {
        "run_id": b04_view["run_id"],
        "path": b04_view["path"],
        "manifest_sha256": b04_view["manifest_sha256"],
        "b00": b04_view["b00"],
        "b04": {
            "run_id": "20261024T163822Z_63a911",
            "path": B04_RUN_RELATIVE,
            "manifest_sha256": digest,
        },
        "source_snapshot": {
            "path": B05_SOURCE_SNAPSHOT,
            "confirmed_at": snapshot["confirmed_at"],
            "files": snapshot["files"],
        },
    }


def comparison_baseline_b06() -> dict[str, Any]:
    b05_view = comparison_baseline_b05()
    manifest_path = ROOT / B05_RUN_RELATIVE / "manifest.json"
    digest = detector.sha256_file(manifest_path)
    if digest != B05_RUN_MANIFEST_SHA256:
        raise detector.T3Error(
            "B05 manifest 해시가 기준과 다릅니다.",
            stage="baseline",
            details={"expected": B05_RUN_MANIFEST_SHA256, "actual": digest},
        )
    snapshot = detector.loads((ROOT / B06_SOURCE_SNAPSHOT / "snapshot.json").read_text(encoding="utf-8"))
    return {
        "run_id": b05_view["run_id"],
        "path": b05_view["path"],
        "manifest_sha256": b05_view["manifest_sha256"],
        "b00": b05_view["b00"],
        "b04": b05_view["b04"],
        "b05": {
            "run_id": "20261024T175204Z_bdfca4",
            "path": B05_RUN_RELATIVE,
            "manifest_sha256": digest,
        },
        "source_snapshot": {
            "path": B06_SOURCE_SNAPSHOT,
            "confirmed_at": snapshot["confirmed_at"],
            "files": snapshot["files"],
        },
    }


def sample_contract_problem(new_samples: list[dict[str, Any]], old_samples: list[dict[str, Any]]) -> str | None:
    if len(new_samples) != len(old_samples):
        return f"sample 수가 다릅니다. new={len(new_samples)} baseline={len(old_samples)}"
    for index, (new, old) in enumerate(zip(new_samples, old_samples)):
        if int(new["pts"]) != int(old["pts"]):
            return f"sample {index} PTS가 다릅니다."
        if new["time_base"] != old["time_base"]:
            return f"sample {index} time_base가 다릅니다."
        if int(new["width"]) != int(old["width"]) or int(new["height"]) != int(old["height"]):
            return f"sample {index} 분석 이미지 크기가 다릅니다."
        if new.get("pixel_format") != old.get("pixel_format"):
            return f"sample {index} pixel_format이 다릅니다."
        if new.get("analysis_bytes_sha256") != old.get("analysis_bytes_sha256"):
            return f"sample {index} analysis_bytes_sha256이 다릅니다."
    return None


def scope_counts(statuses: list[str]) -> dict[str, int]:
    return {
        "success": sum(status == "ok" for status in statuses),
        "failed": sum(status == "failed" for status in statuses),
        "pending": sum(status == "pending" for status in statuses),
        "out_of_scope": sum(status == "out_of_scope" for status in statuses),
    }


def phase_for(*, subset: bool, pending: int, failed: int, problems: bool) -> str:
    if subset:
        return "subset"
    if pending == 0 and failed == 0 and not problems:
        return "done"
    return "full"


def scope_problem(status: str, *, subset_run: bool, in_scope: bool) -> str | None:
    if status == "out_of_scope" and (not subset_run or in_scope):
        return "out_of_scope는 subset의 범위 밖에만 허용됩니다."
    if subset_run and not in_scope and status != "out_of_scope":
        return "범위 밖 후보 status가 out_of_scope가 아닙니다."
    return None


def subset_ids_of(manifest: dict[str, Any]) -> set[str] | None:
    subset = manifest.get("subset")
    if not subset:
        return None
    return {str(item) for item in subset.get("candidate_ids") or []}


def candidate_in_scope(plan: dict[str, Any], manifest: dict[str, Any]) -> bool:
    subset = subset_ids_of(manifest)
    if subset is None:
        return True
    return plan["candidate_id"] in subset


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def print_problem(message: str, file: str, target: str) -> None:
    print(f"원인: {message}\n파일: {file}\n대상: {target}", flush=True)


def create_run_directory(path: Path, problems: list[str]) -> bool:
    if problems:
        for problem in problems:
            print(problem, flush=True)
        return False
    if path.exists():
        print_problem("run 폴더가 이미 있습니다.", str(path), path.name)
        return False
    path.mkdir(parents=True)
    return True


@contextmanager
def run_lock(run_dir: Path) -> Iterator[None]:
    handle = (run_dir / ".lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise detector.T3Error("같은 run이 이미 기록 중입니다.", stage="lock") from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def audit_candidate(candidate: dict[str, Any], *, file_hash: str, expected_hash: str, video_id: str) -> list[str]:
    problems = []
    cid = str(candidate.get("candidate_id"))
    if file_hash != expected_hash:
        problems.append(f"{cid} 입력 hash가 고정값과 다릅니다.")
    source = candidate.get("candidate_source")
    if cid in BUDGET_GUARD_IDS:
        if source != "budget_guard":
            problems.append(f"{cid} candidate_source가 budget_guard가 아닙니다.")
        if candidate.get("policy_version") != "t2.budget_guard.v1":
            problems.append(f"{cid} policy_version이 t2.budget_guard.v1이 아닙니다.")
        if candidate.get("model_inference_success") is not False:
            problems.append(f"{cid} model_inference_success가 false가 아닙니다.")
        if candidate.get("decision") is not None:
            problems.append(f"{cid} decision이 비어 있지 않습니다.")
    elif source == "thinking_on":
        if candidate.get("model_inference_success") is not True:
            problems.append(f"{cid} thinking_on의 model_inference_success가 true가 아닙니다.")
        if candidate.get("decision") not in {"needed", "uncertain"}:
            problems.append(f"{cid} thinking_on decision이 needed/uncertain이 아닙니다.")
    else:
        problems.append(f"{cid} 출처 {source}는 이번 입력이 아닙니다.")
    if not cid.startswith(video_id + ":"):
        problems.append(f"{cid}가 영상 {video_id}의 후보가 아닙니다.")
    return problems


def _same_number(left: Any, right: Any, label: str) -> str | None:
    try:
        if detector.as_decimal(left, label) != detector.as_decimal(right, label):
            return f"{label}가 대응 기록과 다릅니다."
    except detector.T3Error as exc:
        return str(exc)
    return None


def t1_source_path(ref: str) -> Path:
    """Read historical T1 references from the current location without a symlink."""
    legacy = "tests/T1_sweep/"
    if ref.startswith(legacy):
        ref = "tests/T1/sweep/" + ref[len(legacy):]
    return ROOT / ref


def inspect_inputs(ffmpeg: str, ffprobe: str) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    validation_path = ROOT / T2_VALIDATION_RELATIVE
    if not validation_path.exists():
        problems.append(f"원인: T2 검증 기록이 없습니다.\n파일: {T2_VALIDATION_RELATIVE}\n대상: validation")
        return problems, {}
    validation = detector.loads(validation_path.read_text(encoding="utf-8"))
    recorded_hashes = validation.get("file_hashes") or {}
    videos = []
    seen_ids: set[str] = set()
    for video_id in VIDEO_IDS:
        relative = f"{INPUT_RELATIVE}/{video_id}/visual_candidates.json"
        path = ROOT / relative
        if not path.exists():
            problems.append(f"원인: 입력 파일이 없습니다.\n파일: {relative}\n대상: {video_id}")
            continue
        file_hash = detector.sha256_file(path)
        expected = EXPECTED_SHA256[video_id]
        recorded = recorded_hashes.get(relative)
        if file_hash != expected or recorded != expected:
            problems.append(f"원인: visual_candidates.json hash가 고정값과 다릅니다.\n파일: {relative}\n대상: {video_id}")
            continue
        try:
            source = detector.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"원인: JSON을 읽을 수 없습니다. {exc}\n파일: {relative}\n대상: {video_id}")
            continue
        if source.get("schema_version") != "t2.visual_candidates.v1" or source.get("video_id") != video_id:
            problems.append(f"원인: schema_version 또는 video_id가 다릅니다.\n파일: {relative}\n대상: {video_id}")
            continue
        candidates = list(source.get("candidates") or [])
        guards = sum(1 for candidate in candidates if candidate.get("candidate_source") == "budget_guard")
        if (len(candidates), guards) != EXPECTED_COUNTS[video_id]:
            problems.append(f"원인: 후보 수 또는 budget_guard 수가 다릅니다.\n파일: {relative}\n대상: {video_id}")
        units = {int(unit["unit_index"]): unit for unit in source.get("units") or []}
        local_ids = set()
        plans = []
        for candidate in sorted(candidates, key=lambda item: (item["unit_index"], item["chunk_index"], item["candidate_id"])):
            cid = candidate["candidate_id"]
            if cid in local_ids or cid in seen_ids:
                problems.append(f"원인: 후보 ID가 중복입니다.\n파일: {relative}\n대상: {cid}")
            local_ids.add(cid)
            seen_ids.add(cid)
            problems.extend(
                f"원인: {problem}\n파일: {relative}\n대상: {cid}"
                for problem in audit_candidate(candidate, file_hash=file_hash, expected_hash=expected, video_id=video_id)
            )
            unit = units.get(int(candidate["unit_index"]))
            chunk = None
            if unit is None:
                problems.append(f"원인: 의미 단위가 없습니다.\n파일: {relative}\n대상: {cid}")
            else:
                chunk = next((item for item in unit.get("chunks") or [] if int(item["chunk_index"]) == int(candidate["chunk_index"])), None)
                if chunk is None:
                    problems.append(f"원인: 청크가 없습니다.\n파일: {relative}\n대상: {cid}")
            if chunk is not None:
                for label, left, right in (
                    ("video_start", candidate.get("video_start"), chunk.get("video_start")),
                    ("video_end", candidate.get("video_end"), chunk.get("video_end")),
                    ("audio_start", candidate.get("audio_start"), chunk.get("audio_start")),
                    ("audio_end", candidate.get("audio_end"), chunk.get("audio_end")),
                ):
                    mismatch = _same_number(left, right, label)
                    if mismatch:
                        problems.append(f"원인: {mismatch}\n파일: {relative}\n대상: {cid}")
                if candidate.get("sentence_start") != chunk.get("sentence_start") or candidate.get("sentence_end") != chunk.get("sentence_end"):
                    problems.append(f"원인: 문장 구간이 청크와 다릅니다.\n파일: {relative}\n대상: {cid}")
                policy = chunk.get("policy") or {}
                if policy.get("candidate_source") not in (None, candidate.get("candidate_source")):
                    problems.append(f"원인: 청크 선정 출처가 후보와 다릅니다.\n파일: {relative}\n대상: {cid}")
            if candidate.get("audio_time_reference") != "audio_start" or candidate.get("video_time_reference") != "video_start":
                problems.append(f"원인: time_reference가 원래 계약과 다릅니다.\n파일: {relative}\n대상: {cid}")
            try:
                video_start = detector.as_decimal(candidate.get("video_start"), "video_start")
                video_end = detector.as_decimal(candidate.get("video_end"), "video_end")
                audio_start = detector.as_decimal(candidate.get("audio_start"), "audio_start")
                audio_end = detector.as_decimal(candidate.get("audio_end"), "audio_end")
                offset = detector.as_decimal(source["media"].get("audio_to_video_offset_seconds"), "offset")
            except detector.T3Error as exc:
                problems.append(f"원인: {exc}\n파일: {relative}\n대상: {cid}")
                continue
            if video_start >= video_end:
                problems.append(f"원인: 영상 범위가 비거나 역전입니다.\n파일: {relative}\n대상: {cid}")
            if video_start != audio_start + offset or video_end != audio_end + offset:
                problems.append(f"원인: video 시각이 audio+offset과 다릅니다.\n파일: {relative}\n대상: {cid}")
            sentence_ids = {int(sentence["index"]) for sentence in (unit or {}).get("sentences") or []}
            if unit is not None:
                expected_ids = range(int(candidate["sentence_start"]), int(candidate["sentence_end"]))
                if any(index not in sentence_ids for index in expected_ids):
                    problems.append(f"원인: 문장 참조가 단위 안에 없습니다.\n파일: {relative}\n대상: {cid}")
            plans.append({
                "candidate_id": cid,
                "unit_index": int(candidate["unit_index"]),
                "chunk_index": int(candidate["chunk_index"]),
                "video_start": video_start,
                "video_end": video_end,
                "grid_request_count": len(detector.grid_requests(video_start, video_end)),
                "pilot": (video_id, int(candidate["unit_index"]), int(candidate["chunk_index"])) in PILOT_KEYS,
                "source_candidate": candidate,
            })
        input_block = source.get("input") or {}
        if input_block.get("time_reference") != "audio_start" or input_block.get("unit_time_reference") != "audio_start":
            problems.append(f"원인: 입력 time_reference가 audio_start가 아닙니다.\n파일: {relative}\n대상: {video_id}")
        references = {}
        for key, hash_key in (
            ("t1_semantic_units", "t1_sha256"),
            ("alignment_sentences", "alignment_sha256"),
            ("metadata", "metadata_sha256"),
            ("effective_config", "effective_config_sha256"),
        ):
            ref = input_block.get(key)
            if not isinstance(ref, str):
                problems.append(f"원인: 참조 경로가 없습니다.\n파일: {relative}\n대상: {key}")
                continue
            ref_path = t1_source_path(ref)
            if not ref_path.exists():
                problems.append(f"원인: 참조 파일이 없습니다.\n파일: {ref}\n대상: {video_id}")
                continue
            actual_hash = detector.sha256_file(ref_path)
            if actual_hash != input_block.get(hash_key):
                problems.append(f"원인: 참조 hash가 T2 기록과 다릅니다.\n파일: {ref}\n대상: {video_id}")
            references[key] = {"path": ref, "sha256": actual_hash}
        media = source.get("media") or {}
        media_path = ROOT / str(media.get("path") or "")
        metadata_path = ROOT / str(input_block.get("metadata") or "")
        metadata = detector.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        video_result = (metadata.get("results") or {}).get("video") or {}
        meta_video = ROOT / "data" / video_id / str(video_result.get("path") or "")
        try:
            inside = media_path.resolve().is_relative_to((ROOT / "data" / video_id).resolve())
        except AttributeError:
            inside = str(media_path.resolve()).startswith(str((ROOT / "data" / video_id).resolve()) + os.sep)
        if not media_path.exists() or not inside or media_path.resolve() != meta_video.resolve():
            problems.append(f"원인: 원본 영상 경로가 data/{video_id}의 같은 파일이 아닙니다.\n파일: {media.get('path')}\n대상: {video_id}")
        if media.get("result_id") != video_result.get("result_id") or media.get("sha256") != video_result.get("sha256"):
            problems.append(f"원인: 원본 result_id 또는 기록된 SHA-256이 metadata와 다릅니다.\n파일: {relative}\n대상: {video_id}")
        stat = media_path.stat() if media_path.exists() else None
        try:
            probed = detector.probe_video(ffprobe, media_path) if media_path.exists() else None
        except detector.T3Error as exc:
            problems.append(f"원인: {exc}\n파일: {media.get('path')}\n대상: {video_id}")
            probed = None
        end_info = None
        if probed is not None:
            try:
                end_info = detector.confirm_stream_end(
                    ffmpeg, media_path, probed["duration"],
                    stream_time_base=probed["time_base_fraction"],
                    stream_start=Fraction(probed["start_time"]),
                    stream_index=probed["index"],
                )
            except detector.T3Error as exc:
                problems.append(f"원인: {exc}\n파일: {media.get('path')}\n대상: {video_id}")
            for plan in plans:
                if plan["video_start"] < 0 or plan["video_end"] > probed["duration"]:
                    problems.append(
                        f"원인: 후보가 확인한 영상 범위 밖입니다.\n파일: {relative}\n대상: {plan['candidate_id']}"
                    )
        videos.append({
            "video_id": video_id,
            "source_path": relative,
            "source_sha256": file_hash,
            "source": source,
            "references": references,
            "plans": plans,
            "media_stat": None if stat is None else {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
            "probe": None if probed is None else {key: value for key, value in probed.items() if key != "time_base_fraction"},
            "time_base_fraction": None if probed is None else probed["time_base_fraction"],
            "end_confirmation": end_info,
        })
    found_pilot = {
        (video["video_id"], plan["unit_index"], plan["chunk_index"])
        for video in videos
        for plan in video["plans"]
        if plan["pilot"]
    }
    if found_pilot != PILOT_KEYS:
        missing = sorted(PILOT_KEYS - found_pilot)
        problems.append(f"원인: pilot 후보가 입력에 없습니다.\n파일: {INPUT_RELATIVE}\n대상: {missing}")
    payload = {"videos": videos, "validation_path": T2_VALIDATION_RELATIVE}
    return problems, payload


def _public_probe(probe: dict[str, Any] | None, end_info: dict[str, Any] | None, stat: dict[str, Any] | None, media: dict[str, Any], offset: Any) -> dict[str, Any]:
    return {
        "path": media.get("path"),
        "result_id": media.get("result_id"),
        "recorded_sha256": media.get("sha256"),
        "bytes_rehashed": False,
        "size": None if stat is None else stat["size"],
        "mtime_ns": None if stat is None else stat["mtime_ns"],
        "stream_index": None if probe is None else probe["index"],
        "codec_name": None if probe is None else probe["codec_name"],
        "width": None if probe is None else probe["width"],
        "height": None if probe is None else probe["height"],
        "start_time": None if probe is None else probe["start_time"],
        "time_base": None if probe is None else probe["time_base"],
        "duration": None if probe is None else probe["duration"],
        "end_confirmation": end_info,
        "audio_to_video_offset_seconds": offset,
    }


def build_manifest(
    payload: dict[str, Any],
    ffmpeg_info: dict[str, str],
    ffprobe_info: dict[str, str],
    *,
    config_id: str,
    subset_ids: list[str] | None,
) -> dict[str, Any]:
    settings = detector.settings_for(config_id)
    videos = []
    for video in payload["videos"]:
        source = video["source"]
        videos.append({
            "video_id": video["video_id"],
            "source_path": video["source_path"],
            "source_sha256": video["source_sha256"],
            "source_url": source.get("source_url"),
            "title": source.get("title"),
            "source_t2_status": source.get("status"),
            "source_t2_validation": source.get("validation"),
            "source_t2_warnings": source.get("warnings") or [],
            "references": video["references"],
            "media": _public_probe(video["probe"], video["end_confirmation"], video["media_stat"], source["media"], source["media"].get("audio_to_video_offset_seconds")),
            "candidates": video["plans"],
        })
    return {
        "schema_version": detector.schema_for(config_id),
        "config_id": config_id,
        "logic_version": settings["logic_version"],
        "input_root": INPUT_RELATIVE,
        "video_order": list(VIDEO_IDS),
        "t2_validation": T2_VALIDATION_RELATIVE,
        "t2_closure": T2_CLOSURE_RELATIVE,
        "settings": settings,
        "code": source_inventory(),
        "runtime": {
            "python": sys.version,
            "ffmpeg": ffmpeg_info,
            "ffprobe": ffprobe_info,
            "model_calls": 0,
        },
        "videos": videos,
        "pilot_candidate_ids": [
            plan["candidate_id"]
            for video in videos
            for plan in video["candidates"]
            if plan["pilot"]
        ],
        "subset": None if not subset_ids else {
            "candidate_ids": list(subset_ids),
            "target_count": len(subset_ids),
        },
        "comparison_baseline": (
            comparison_baseline_b06() if config_id == detector.CONFIG_ID_B06
            else comparison_baseline_b05() if config_id == detector.CONFIG_ID_B05
            else comparison_baseline_b04() if config_id == detector.CONFIG_ID_B04
            else comparison_baseline_b03() if config_id == detector.CONFIG_ID_B03
            else comparison_baseline_b02() if config_id == detector.CONFIG_ID_B02
            else comparison_baseline() if config_id == detector.CONFIG_ID_B01
            else None
        ),
    }


def empty_run_state(*, pending: int = 139, out_of_scope: int = 0, subset_target: int | None = None) -> dict[str, Any]:
    counts: dict[str, Any] = {"new": 0, "reused": 0, "success": 0, "failed": 0, "pending": pending, "reused_external": 0, "out_of_scope": out_of_scope}
    if subset_target is not None:
        counts["subset_target"] = subset_target
    return {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at": None,
        "phase": "planned",
        "detection_status": "pending",
        "render_status": "pending",
        "quality_status": "pending_review",
        "counts": counts,
        "timing": {
            "decode_seconds": Decimal(0),
            "difference_seconds": Decimal(0),
            "render_seconds": Decimal(0),
            "wall_seconds": Decimal(0),
        },
        "model_calls": 0,
        "model_tokens": None,
        "vram": None,
    }


def video_shell(video: dict[str, Any], settings: dict[str, Any], *, schema_version: str, subset_ids: set[str] | None) -> dict[str, Any]:
    references = video["references"]
    shell = {
        "schema_version": schema_version,
        "video_id": video["video_id"],
        "source_url": video["source_url"],
        "title": video["title"],
        "status": "pending",
        "problems": [],
        "warnings": [],
        "input": {
            "visual_candidates": {"path": video["source_path"], "sha256": video["source_sha256"]},
            "t1_semantic_units": references.get("t1_semantic_units"),
            "alignment_sentences": references.get("alignment_sentences"),
            "metadata": references.get("metadata"),
            "effective_config": references.get("effective_config"),
            "t2_acceptance": T2_VALIDATION_RELATIVE,
            "t2_closure": T2_CLOSURE_RELATIVE,
            "source_policy": "t2.budget_guard.v1",
        },
        "source_t2_status": video["source_t2_status"],
        "source_t2_validation": video["source_t2_validation"],
        "source_t2_warnings": video["source_t2_warnings"],
        "media": video["media"],
        "settings": settings,
        "candidates": [
            {
                "candidate_id": plan["candidate_id"],
                "unit_index": plan["unit_index"],
                "chunk_index": plan["chunk_index"],
                "source_candidate": plan["source_candidate"],
                "status": "pending" if subset_ids is None or plan["candidate_id"] in subset_ids else "out_of_scope",
                "result_ref": None,
                "result_sha256": None,
                "segment_count": None,
                "problems": [],
                "warnings": [],
            }
            for plan in video["candidates"]
        ],
        "validation": {"ok": None, "problems": []},
    }
    shell["status"] = aggregate_status([item["status"] for item in shell["candidates"]], [])
    return shell


def aggregate_status(statuses: list[str], structural: list[str]) -> str:
    if structural:
        return "failed"
    relevant = [status for status in statuses if status != "out_of_scope"]
    if not statuses:
        return "ok"
    if not relevant:
        return "out_of_scope"
    if all(status == "ok" for status in relevant):
        return "ok"
    if any(status == "ok" for status in relevant):
        return "partial"
    if any(status == "failed" for status in relevant):
        return "failed"
    return "pending"


def detection_key_for(video: dict[str, Any], plan: dict[str, Any], manifest: dict[str, Any]) -> str:
    media = video["media"]
    return detector.detection_key({
        "t2_path": video["source_path"],
        "t2_sha256": video["source_sha256"],
        "source_candidate": plan["source_candidate"],
        "reference_hashes": {key: value["sha256"] for key, value in video["references"].items()},
        "media": {
            "result_id": media.get("result_id"),
            "recorded_sha256": media.get("recorded_sha256"),
            "size": media.get("size"),
            "mtime_ns": media.get("mtime_ns"),
        },
        "stream": {
            "index": media.get("stream_index"),
            "codec_name": media.get("codec_name"),
            "width": media.get("width"),
            "height": media.get("height"),
            "time_base": media.get("time_base"),
            "start_time": format(media.get("start_time"), "f") if isinstance(media.get("start_time"), Decimal) else media.get("start_time"),
            "duration": format(media.get("duration"), "f") if isinstance(media.get("duration"), Decimal) else media.get("duration"),
            "last_pts": (media.get("end_confirmation") or {}).get("last_pts"),
        },
        "settings": manifest["settings"],
        "detector_sha256": manifest["code"]["detector_sha256"],
        "runner_sha256": manifest["code"]["runner_sha256"],
        "ffmpeg": {"path": manifest["runtime"]["ffmpeg"]["path"], "version_line": manifest["runtime"]["ffmpeg"]["version_line"]},
        "ffprobe": {"path": manifest["runtime"]["ffprobe"]["path"], "version_line": manifest["runtime"]["ffprobe"]["version_line"]},
    })


def candidate_path(run_dir: Path, video_id: str, plan: dict[str, Any]) -> Path:
    return run_dir / video_id / "candidates" / f"u{plan['unit_index']}_c{plan['chunk_index']}.json"


def sentences_for(source: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    unit = next(unit for unit in source["units"] if int(unit["unit_index"]) == int(candidate["unit_index"]))
    start = int(candidate["sentence_start"])
    end = int(candidate["sentence_end"])
    return [
        {"index": int(sentence["index"]), "text": str(sentence.get("text") or "")}
        for sentence in unit.get("sentences") or []
        if start <= int(sentence["index"]) < end
    ]


def build_document(video: dict[str, Any], plan: dict[str, Any], analysis: dict[str, Any], key: str, manifest: dict[str, Any]) -> dict[str, Any]:
    source = plan["source_candidate"]
    requested = {
        "video_start": plan["video_start"],
        "video_end": plan["video_end"],
        "time_reference": "video_start",
    }
    segments = []
    for segment in analysis.get("segments") or []:
        copied = dict(segment)
        copied["candidate_id"] = plan["candidate_id"]
        segments.append(copied)
    references = video["references"]
    decode = analysis.get("decode") or {}
    return {
        "schema_version": manifest["schema_version"],
        "candidate_id": plan["candidate_id"],
        "unit_index": plan["unit_index"],
        "chunk_index": plan["chunk_index"],
        "source_candidate": source,
        "source_t2_ref": video["source_path"],
        "source_t2_sha256": video["source_sha256"],
        "text_ref": {
            "t2_path": video["source_path"],
            "t2_sha256": video["source_sha256"],
            "t1_path": references["t1_semantic_units"]["path"],
            "t1_sha256": references["t1_semantic_units"]["sha256"],
            "unit_index": plan["unit_index"],
            "chunk_index": plan["chunk_index"],
            "sentence_start": source["sentence_start"],
            "sentence_end": source["sentence_end"],
        },
        "requested_range": requested,
        "effective_range": dict(requested),
        "status": analysis.get("status") or "failed",
        "problems": list(analysis.get("problems") or []),
        "warnings": list(analysis.get("warnings") or []),
        "detection_key": key,
        "reused_from": None,
        "requests": analysis.get("requests") or [],
        "samples": analysis.get("samples") or [],
        "differences": analysis.get("differences") or [],
        "boundaries": analysis.get("boundaries") or [],
        "segments": segments,
        "settings": manifest["settings"],
        "timing": {
            "decode_seconds": decode.get("grid_elapsed_seconds", Decimal(0)) + decode.get("tail_elapsed_seconds", Decimal(0)),
            "difference_seconds": decode.get("difference_elapsed_seconds", Decimal(0)),
            "render_seconds": Decimal(0),
            "total_seconds": Decimal(0),
        },
        "decode": {key: value for key, value in decode.items() if key != "stderr_tail"},
        "validation": analysis.get("validation") or {"ok": False, "problems": list(analysis.get("problems") or [])},
    }


def failed_document(video: dict[str, Any], plan: dict[str, Any], key: str, manifest: dict[str, Any], message: str, stage: str, details: dict[str, Any]) -> dict[str, Any]:
    analysis = {
        "status": "failed",
        "problems": [message],
        "warnings": [],
        "requests": [],
        "samples": [],
        "differences": [],
        "boundaries": [],
        "segments": [],
        "validation": {"ok": False, "problems": [message]},
        "decode": {},
    }
    document = build_document(video, plan, analysis, key, manifest)
    document["failure"] = {"stage": stage, "message": message, "details": details}
    return document


def refresh_video(run_dir: Path, video: dict[str, Any]) -> dict[str, Any]:
    shell = detector.loads((run_dir / video["video_id"] / "screen_changes.json").read_text(encoding="utf-8"))
    for summary, plan in zip(shell["candidates"], video["candidates"]):
        path = candidate_path(run_dir, video["video_id"], plan)
        if not path.exists():
            continue
        document = detector.loads(path.read_text(encoding="utf-8"))
        summary["status"] = document["status"]
        summary["result_ref"] = rel(path)
        summary["result_sha256"] = detector.sha256_file(path)
        summary["segment_count"] = len(document.get("segments") or []) if document["status"] == "ok" else None
        summary["problems"] = document.get("problems") or []
        summary["warnings"] = document.get("warnings") or []
        summary["source_candidate"] = document["source_candidate"]
    shell["status"] = aggregate_status([item["status"] for item in shell["candidates"]], [])
    atomic_write(run_dir / video["video_id"] / "screen_changes.json", detector.dumps(shell))
    return shell


def render_document(run_dir: Path, video: dict[str, Any], plan: dict[str, Any], document: dict[str, Any], source: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    started = time.monotonic()
    video_dir = run_dir / video["video_id"]
    problems: list[str] = []
    if document["status"] == "ok":
        try:
            _, image_problems = review.render_candidate_images(
                document,
                ffmpeg=video["media"] and "",
                video=ROOT / video["media"]["path"],
                frames_dir=video_dir / "frames",
                repo_root=ROOT,
            )
        except detector.T3Error as exc:
            image_problems = [str(exc)]
        problems.extend(image_problems)
    # The empty ffmpeg string above is wrong. Caller passes identity through the manifest.
    return document, problems


def render_document_with_ffmpeg(
    run_dir: Path,
    video: dict[str, Any],
    document: dict[str, Any],
    ffmpeg: str,
) -> list[str]:
    if document["status"] != "ok":
        return []
    _, problems = review.render_candidate_images(
        document,
        ffmpeg=ffmpeg,
        video=ROOT / video["media"]["path"],
        frames_dir=run_dir / video["video_id"] / "frames",
        repo_root=ROOT,
    )
    return problems


def write_logs(run_dir: Path, candidate_id: str, payload: dict[str, Any]) -> None:
    safe = candidate_id.replace(":", "_")
    atomic_write(run_dir / "logs" / f"{safe}.json", detector.dumps(payload))


def manifest_matches(manifest: dict[str, Any], ffmpeg_info: dict[str, str], ffprobe_info: dict[str, str]) -> str | None:
    try:
        expected = detector.settings_for(str(manifest.get("config_id")))
    except detector.T3Error as exc:
        return str(exc)
    if manifest.get("settings") != expected:
        return "검출 설정이 manifest와 다릅니다."
    code = manifest.get("code") or {}
    current = source_inventory()
    if code.get("sources"):
        for row in code["sources"]:
            actual = detector.sha256_file(ROOT / row["path"])
            if actual != row.get("sha256"):
                return f"소스 해시가 manifest와 다릅니다: {row.get('path')}"
        if code.get("runner_sha256") != current["runner_sha256"]:
            return "runner hash가 manifest와 다릅니다."
        if code.get("review_sha256") != current["review_sha256"]:
            return "표시 코드 hash가 manifest와 다릅니다."
    if code.get("detector_sha256") != current["detector_sha256"]:
        return "검출 코드 hash가 manifest와 다릅니다."
    if not code.get("sources") and code.get("review_sha256") != current["review_sha256"]:
        return "표시 코드 hash가 manifest와 다릅니다."
    runtime = manifest.get("runtime") or {}
    if (runtime.get("ffmpeg") or {}).get("path") != ffmpeg_info["path"] or (runtime.get("ffmpeg") or {}).get("version_line") != ffmpeg_info["version_line"]:
        return "FFmpeg 식별이 manifest와 다릅니다."
    if (runtime.get("ffprobe") or {}).get("path") != ffprobe_info["path"] or (runtime.get("ffprobe") or {}).get("version_line") != ffprobe_info["version_line"]:
        return "ffprobe 식별이 manifest와 다릅니다."
    for video in manifest["videos"]:
        media_path = ROOT / video["media"]["path"]
        if not media_path.exists():
            return f"원본 영상이 없습니다: {video['video_id']}"
        stat = media_path.stat()
        if stat.st_size != video["media"]["size"] or stat.st_mtime_ns != video["media"]["mtime_ns"]:
            return f"원본 size 또는 mtime_ns가 바뀌었습니다: {video['video_id']}"
        current = detector.sha256_file(ROOT / video["source_path"])
        if current != video["source_sha256"]:
            return f"입력 hash가 바뀌었습니다: {video['video_id']}"
    return None


def process_run(run_dir: Path, *, pilot_only: bool, subset_only: bool = False) -> int:
    manifest = detector.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    state = detector.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    ffmpeg = manifest["runtime"]["ffmpeg"]["path"]
    ffprobe = manifest["runtime"]["ffprobe"]["path"]
    ffmpeg_info = detector.tool_identity(ffmpeg)
    ffprobe_info = detector.tool_identity(ffprobe)
    mismatch = manifest_matches(manifest, ffmpeg_info, ffprobe_info)
    if mismatch:
        print_problem(mismatch, rel(run_dir / "manifest.json"), run_dir.name)
        return 1
    if subset_only and not manifest.get("subset"):
        print_problem("subset manifest가 없습니다.", rel(run_dir / "manifest.json"), run_dir.name)
        return 1
    if manifest.get("subset") and not subset_only:
        print_problem("subset run은 --subset으로만 실행합니다.", rel(run_dir / "manifest.json"), run_dir.name)
        return 1
    wall_started = time.monotonic()
    new = reused = success = failed = 0
    sources = {
        video["video_id"]: detector.loads((ROOT / video["source_path"]).read_text(encoding="utf-8"))
        for video in manifest["videos"]
    }
    abort = False
    with run_lock(run_dir):
        for video in manifest["videos"]:
            if abort:
                break
            for plan in video["candidates"]:
                if abort:
                    break
                if not candidate_in_scope(plan, manifest):
                    continue
                if pilot_only and not plan["pilot"]:
                    continue
                path = candidate_path(run_dir, video["video_id"], plan)
                key = detection_key_for(video, plan, manifest)
                existing = detector.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                action = detector.resume_action(
                    None if existing is None else existing.get("status"),
                    None if existing is None else existing.get("detection_key"),
                    key,
                )
                if action == "reject_key":
                    print_problem("저장된 검출 key가 현재 조건과 다릅니다.", rel(path), plan["candidate_id"])
                    return 1
                if action == "reuse":
                    reused += 1
                    print(f"reuse {plan['candidate_id']}", flush=True)
                    continue
                if action == "skip_failed":
                    failed += 1
                    print(f"skip_failed {plan['candidate_id']}: {existing.get('problems')}", flush=True)
                    continue
                started = time.monotonic()
                try:
                    analysis = detector.detect_range(
                        ffmpeg,
                        ROOT / video["media"]["path"],
                        plan["video_start"],
                        plan["video_end"],
                        stream_time_base=Fraction(video["media"]["time_base"]["numerator"], video["media"]["time_base"]["denominator"]),
                        stream_start=Fraction(video["media"]["start_time"]),
                        stream_index=int(video["media"]["stream_index"]),
                        settings=manifest["settings"],
                    )
                    baseline_relative = None
                    if manifest.get("config_id") == detector.CONFIG_ID_B01:
                        baseline_relative = B00_BASELINE_RELATIVE
                    elif manifest.get("config_id") in {detector.CONFIG_ID_B04, detector.CONFIG_ID_B05, detector.CONFIG_ID_B06}:
                        baseline_relative = B01_FULL_BASELINE_RELATIVE
                    elif manifest.get("config_id") in {detector.CONFIG_ID_B02, detector.CONFIG_ID_B03}:
                        baseline_relative = B01_BASELINE_RELATIVE
                    if baseline_relative is not None:
                        baseline_path = (
                            ROOT / baseline_relative / video["video_id"] / "candidates"
                            / f"u{plan['unit_index']}_c{plan['chunk_index']}.json"
                        )
                        baseline = detector.loads(baseline_path.read_text(encoding="utf-8"))
                        contract = sample_contract_problem(analysis.get("samples") or [], baseline.get("samples") or [])
                        if contract:
                            document = failed_document(
                                video, plan, key, manifest, contract, "sampling_contract",
                                {"baseline": rel(baseline_path)},
                            )
                            atomic_write(path, detector.dumps(document))
                            write_logs(run_dir, plan["candidate_id"], {"stage": "sampling_contract", "message": contract})
                            failed += 1
                            new += 1
                            abort = True
                            print(f"abort {plan['candidate_id']} {contract}", flush=True)
                            break
                    document = build_document(video, plan, analysis, key, manifest)
                    write_logs(run_dir, plan["candidate_id"], {"stage": "detect", "decode": document.get("decode"), "stderr_tail": analysis.get("decode", {}).get("stderr_tail")})
                    render_started = time.monotonic()
                    render_problems = render_document_with_ffmpeg(run_dir, video, document, ffmpeg)
                    render_elapsed = Decimal(str(round(time.monotonic() - render_started, 6)))
                    document["timing"]["render_seconds"] = render_elapsed
                    document["timing"]["total_seconds"] = Decimal(str(round(time.monotonic() - started, 6)))
                    if render_problems:
                        document["warnings"] = list(document.get("warnings") or []) + [{"code": "render_failed", "problems": render_problems}]
                    atomic_write(path, detector.dumps(document))
                    detail = run_dir / video["video_id"] / "candidates" / f"u{plan['unit_index']}_c{plan['chunk_index']}.md"
                    review.write_candidate_page(document, detail, sentences_for(sources[video["video_id"]], plan["source_candidate"]), ROOT)
                    review.write_svg(document, run_dir / video["video_id"] / "plots" / f"u{plan['unit_index']}_c{plan['chunk_index']}.svg")
                    state["timing"]["decode_seconds"] += document["timing"]["decode_seconds"]
                    state["timing"]["difference_seconds"] += document["timing"]["difference_seconds"]
                    state["timing"]["render_seconds"] += render_elapsed
                    new += 1
                    if document["status"] == "ok" and not render_problems:
                        success += 1
                        print(
                            f"ok {plan['candidate_id']} samples={len(document['samples'])} "
                            f"boundaries={len(document['boundaries'])} segments={len(document['segments'])} "
                            f"{document['timing']['total_seconds']}s",
                            flush=True,
                        )
                    else:
                        failed += 1
                        print(f"failed {plan['candidate_id']} status={document['status']} render={render_problems} problems={document['problems']}", flush=True)
                except detector.T3Error as exc:
                    document = failed_document(video, plan, key, manifest, str(exc), exc.stage, exc.details)
                    atomic_write(path, detector.dumps(document))
                    write_logs(run_dir, plan["candidate_id"], {"stage": exc.stage, "message": str(exc), "details": exc.details})
                    failed += 1
                    new += 1
                    print(f"failed {plan['candidate_id']} stage={exc.stage} {exc}", flush=True)
                    continue
                except Exception as exc:
                    document = failed_document(video, plan, key, manifest, str(exc), "unexpected", {"traceback": traceback.format_exc()})
                    atomic_write(path, detector.dumps(document))
                    failed += 1
                    new += 1
                    print(f"failed {plan['candidate_id']} unexpected {exc}", flush=True)
                    continue
            refresh_video(run_dir, video)
        publish_reviews(run_dir, manifest, sources)
        validation = validate_saved(run_dir, manifest, pilot_only=pilot_only)
        atomic_write(run_dir / "validation.json", detector.dumps(validation))
        pending = 0
        ok_count = 0
        fail_count = 0
        out_of_scope = 0
        for video in manifest["videos"]:
            shell = detector.loads((run_dir / video["video_id"] / "screen_changes.json").read_text(encoding="utf-8"))
            for summary in shell["candidates"]:
                if summary["status"] == "ok":
                    ok_count += 1
                elif summary["status"] == "failed":
                    fail_count += 1
                elif summary["status"] == "out_of_scope":
                    out_of_scope += 1
                else:
                    pending += 1
        counts = {
            "new": int(state["counts"].get("new") or 0) + new,
            "reused": int(state["counts"].get("reused") or 0) + reused,
            "success": ok_count,
            "failed": fail_count,
            "pending": pending,
            "reused_external": 0,
            "out_of_scope": out_of_scope,
        }
        if manifest.get("subset"):
            counts["subset_target"] = int(manifest["subset"]["target_count"])
        state["counts"] = counts
        state["timing"]["wall_seconds"] = Decimal(str(state["timing"].get("wall_seconds") or 0)) + Decimal(str(round(time.monotonic() - wall_started, 6)))
        state["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["detection_status"] = aggregate_status(["ok"] * ok_count + ["failed"] * fail_count + ["pending"] * pending, validation["problems"])
        state["render_status"] = "failed" if validation.get("render_problems") else ("ok" if pending == 0 and fail_count == 0 else "partial")
        state["quality_status"] = "pending_review"
        if manifest.get("subset"):
            state["phase"] = "subset"
        elif pilot_only:
            state["phase"] = "pilot"
        else:
            state["phase"] = phase_for(
                subset=False,
                pending=pending,
                failed=fail_count,
                problems=bool(validation["problems"] or validation.get("render_problems")),
            )
        atomic_write(run_dir / "run.json", detector.dumps(state))
        if (
            manifest.get("subset")
            and not abort
            and fail_count == 0
            and pending == 0
            and validation["ok"]
        ):
            if manifest.get("config_id") == detector.CONFIG_ID_B06:
                relation_problems = review.write_b06_comparison(
                    run_dir=run_dir,
                    b01_dir=ROOT / B01_FULL_BASELINE_RELATIVE,
                    b05_dir=ROOT / B05_RUN_RELATIVE,
                    manifest=manifest,
                    ffmpeg=ffmpeg,
                    repo_root=ROOT,
                )
                if relation_problems:
                    abort = True
                    for item in relation_problems:
                        print(item, flush=True)
            elif manifest.get("config_id") == detector.CONFIG_ID_B05:
                relation_problems = review.write_b05_comparison(
                    run_dir=run_dir,
                    b01_dir=ROOT / B01_FULL_BASELINE_RELATIVE,
                    b04_dir=ROOT / B04_RUN_RELATIVE,
                    manifest=manifest,
                    ffmpeg=ffmpeg,
                    repo_root=ROOT,
                )
                if relation_problems:
                    abort = True
                    for item in relation_problems:
                        print(item, flush=True)
            elif manifest.get("config_id") == detector.CONFIG_ID_B04:
                review.write_b04_comparison(
                    run_dir=run_dir,
                    baseline_dir=ROOT / B01_FULL_BASELINE_RELATIVE,
                    manifest=manifest,
                    ffmpeg=ffmpeg,
                    repo_root=ROOT,
                )
            elif manifest.get("config_id") == detector.CONFIG_ID_B03:
                review.write_b03_comparison(
                    run_dir=run_dir,
                    baseline_dir=ROOT / B01_BASELINE_RELATIVE,
                    manifest=manifest,
                    ffmpeg=ffmpeg,
                    repo_root=ROOT,
                )
            elif manifest.get("config_id") == detector.CONFIG_ID_B02:
                review.write_b01_comparison(
                    run_dir=run_dir,
                    baseline_dir=ROOT / B01_BASELINE_RELATIVE,
                    manifest=manifest,
                    ffmpeg=ffmpeg,
                    repo_root=ROOT,
                )
            else:
                review.write_b00_comparison(
                    run_dir=run_dir,
                    baseline_dir=ROOT / B00_BASELINE_RELATIVE,
                    manifest=manifest,
                    ffmpeg=ffmpeg,
                    repo_root=ROOT,
                )
    target = int(manifest["subset"]["target_count"]) if manifest.get("subset") else (17 if pilot_only else 139)
    print(
        f"phase={state['phase']} new={new} reused={reused} success={ok_count} failed={fail_count} pending={pending} "
        f"out_of_scope={state['counts'].get('out_of_scope')} validation_ok={validation['ok']} wall={state['timing']['wall_seconds']}",
        flush=True,
    )
    if manifest.get("subset"):
        return 0 if ok_count == target and fail_count == 0 and pending == 0 and validation["ok"] and not abort else 1
    if pilot_only:
        return 0 if ok_count >= target and fail_count == 0 and validation["ok"] else 1
    return 0 if pending == 0 and fail_count == 0 and validation["ok"] else 1


def publish_reviews(run_dir: Path, manifest: dict[str, Any], sources: dict[str, Any]) -> None:
    videos = []
    t2_link = os.path.relpath((ROOT / T2_REVIEW_RELATIVE).resolve(), (run_dir / "placeholder").resolve())
    for video in manifest["videos"]:
        shell = refresh_video(run_dir, video)
        videos.append(shell)
        documents = {}
        plans = {}
        sentences = {}
        loaded = []
        for plan in video["candidates"]:
            path = candidate_path(run_dir, video["video_id"], plan)
            sentences[plan["candidate_id"]] = sentences_for(sources[video["video_id"]], plan["source_candidate"])
            if not path.exists():
                continue
            document = detector.loads(path.read_text(encoding="utf-8"))
            documents[plan["candidate_id"]] = document
            plans[plan["candidate_id"]] = review.preview_plan(document)
            loaded.append(document)
        review_path = run_dir / video["video_id"] / "review.md"
        link = os.path.relpath((ROOT / T2_REVIEW_RELATIVE).resolve(), review_path.parent.resolve())
        review.write_video_review(
            shell, documents, sentences, plans,
            path=review_path, repo_root=ROOT, t2_review_link=Path(link).as_posix(),
        )
        review.write_frame_hashes(run_dir / video["video_id"], loaded, ROOT)
    review.write_index(run_dir, videos, ROOT)
    del t2_link


def validate_saved(run_dir: Path, manifest: dict[str, Any], *, pilot_only: bool) -> dict[str, Any]:
    problems = []
    render_problems = []
    checked = 0
    subset_run = subset_ids_of(manifest) is not None
    for video in manifest["videos"]:
        shell = detector.loads((run_dir / video["video_id"] / "screen_changes.json").read_text(encoding="utf-8"))
        summaries = {item["candidate_id"]: item for item in shell["candidates"]}
        for plan in video["candidates"]:
            path = candidate_path(run_dir, video["video_id"], plan)
            in_scope = candidate_in_scope(plan, manifest)
            relevant = in_scope and ((not pilot_only) or plan["pilot"] or subset_run)
            summary = summaries[plan["candidate_id"]]
            scoped = scope_problem(str(summary["status"]), subset_run=subset_run, in_scope=in_scope)
            if scoped:
                problems.append(f"{plan['candidate_id']} {scoped}")
            if not path.exists():
                if relevant:
                    problems.append(f"{plan['candidate_id']} 결과 파일이 없습니다.")
                continue
            if not relevant:
                document = detector.loads(path.read_text(encoding="utf-8"))
                if subset_run and not in_scope:
                    problems.append(f"{plan['candidate_id']} 범위 밖 후보에 결과 파일이 있습니다.")
                elif document.get("status") not in {"pending", "ok", "failed"}:
                    problems.append(f"{plan['candidate_id']} status가 계약 밖입니다.")
                continue
            document = detector.loads(path.read_text(encoding="utf-8"))
            checked += 1
            problems.extend(_validate_document(video, plan, document, manifest))
            if document.get("status") == "ok":
                render_problems.extend(_validate_previews(document, ROOT))
            source = document.get("source_candidate") or {}
            if source.get("model_inference_success") != plan["source_candidate"].get("model_inference_success"):
                problems.append(f"{plan['candidate_id']} model_inference_success가 바뀌었습니다.")
            if source.get("decision") != plan["source_candidate"].get("decision"):
                problems.append(f"{plan['candidate_id']} decision이 바뀌었습니다.")
    expected_budget = sorted(BUDGET_GUARD_IDS)
    found_budget = sorted(
        plan["candidate_id"]
        for video in manifest["videos"]
        for plan in video["candidates"]
        if plan["source_candidate"].get("candidate_source") == "budget_guard"
    )
    if found_budget != expected_budget:
        problems.append(f"budget_guard 구성이 다릅니다: {found_budget}")
    return {
        "ok": not problems and not render_problems,
        "problems": problems,
        "render_problems": render_problems,
        "checked_candidates": checked,
        "quality_score": None,
    }


def _validate_document(video: dict[str, Any], plan: dict[str, Any], document: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    problems = []
    cid = plan["candidate_id"]
    if document.get("schema_version") != manifest.get("schema_version"):
        problems.append(f"{cid} schema_version이 다릅니다.")
    if document.get("requested_range", {}).get("video_start") != plan["video_start"] or document.get("effective_range", {}).get("video_end") != plan["video_end"]:
        problems.append(f"{cid} 범위가 입력과 다릅니다.")
    if document.get("detection_key") != detection_key_for(video, plan, manifest):
        problems.append(f"{cid} detection_key가 다시 계산한 값과 다릅니다.")
    samples = document.get("samples") or []
    for previous, current in zip(samples, samples[1:]):
        if current["pts"] <= previous["pts"]:
            problems.append(f"{cid} PTS가 단조 증가하지 않습니다.")
    if document.get("status") == "ok":
        try:
            boundaries, segments = detector.replay_boundaries(
                document.get("differences") or [],
                samples,
                plan["video_start"],
                plan["video_end"],
                config_id=str(manifest.get("config_id")),
                logic_version=str(manifest.get("logic_version")),
                schema_version=str(document.get("schema_version")),
                settings=manifest.get("settings"),
            )
        except detector.T3Error as exc:
            problems.append(f"{cid} 차분 재생에 실패했습니다: {exc}")
            return problems
        stored_bounds = [item["video_seconds"] for item in document.get("boundaries") or []]
        replay_bounds = [item["video_seconds"] for item in boundaries]
        if stored_bounds != replay_bounds:
            problems.append(f"{cid} 저장 경계와 재생 경계가 다릅니다.")
        stored_edges = [(item["video_start"], item["video_end"]) for item in document.get("segments") or []]
        replay_edges = [(item["video_start"], item["video_end"]) for item in segments]
        if stored_edges != replay_edges:
            problems.append(f"{cid} 저장 구간과 재생 구간이 다릅니다.")
        if not segments or segments[0]["video_start"] != plan["video_start"] or segments[-1]["video_end"] != plan["video_end"]:
            problems.append(f"{cid} 구간이 원래 후보를 덮지 않습니다.")
    return problems


def _validate_previews(document: dict[str, Any], repo_root: Path) -> list[str]:
    problems = []
    wanted = set(review.preview_plan(document)["render_samples"])
    samples = {int(sample["sample_index"]): sample for sample in document.get("samples") or []}
    for index in wanted:
        preview = samples[index].get("preview_ref")
        if not preview:
            problems.append(f"{document['candidate_id']} sample {index} 검수 이미지가 없습니다.")
            continue
        path = repo_root / preview["path"]
        if not path.exists() or detector.sha256_file(path) != preview.get("sha256"):
            problems.append(f"{document['candidate_id']} sample {index} 이미지 hash가 다릅니다.")
    return problems


def command_plan(*, config_id: str = "B00", subset_ids: list[str] | None = None) -> int:
    try:
        detector.settings_for(config_id)
        if config_id == detector.CONFIG_ID_B01:
            comparison_baseline()
        elif config_id == detector.CONFIG_ID_B02:
            comparison_baseline_b02()
        elif config_id == detector.CONFIG_ID_B03:
            comparison_baseline_b03()
        elif config_id == detector.CONFIG_ID_B04:
            comparison_baseline_b04()
        elif config_id == detector.CONFIG_ID_B05:
            comparison_baseline_b05()
        elif config_id == detector.CONFIG_ID_B06:
            comparison_baseline_b06()
    except detector.T3Error as exc:
        print(f"원인: {exc}\n파일: tests/T3/run_t3.py\n대상: {config_id}", flush=True)
        return 1
    ffmpeg = detector.tool_identity("/usr/bin/ffmpeg")
    ffprobe = detector.tool_identity("/usr/bin/ffprobe")
    problems, payload = inspect_inputs(ffmpeg["path"], ffprobe["path"])
    if subset_ids:
        found = {
            plan["candidate_id"]
            for video in (payload or {}).get("videos", [])
            for plan in video["plans"]
        }
        missing = [candidate_id for candidate_id in subset_ids if candidate_id not in found]
        if missing:
            problems.append(f"원인: subset 후보가 입력에 없습니다.\n파일: {INPUT_RELATIVE}\n대상: {missing}")
    if problems or not payload:
        for problem in problems:
            print(problem, flush=True)
        return 1
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(3)
    run_dir = RUNS_ROOT / run_id
    if not create_run_directory(run_dir, []):
        return 1
    manifest = build_manifest(payload, ffmpeg, ffprobe, config_id=config_id, subset_ids=subset_ids)
    atomic_write(run_dir / "manifest.json", detector.dumps(manifest))
    total = sum(len(video["plans"]) for video in payload["videos"])
    pending = len(subset_ids) if subset_ids else total
    out_of_scope = 0 if not subset_ids else total - len(subset_ids)
    state = empty_run_state(pending=pending, out_of_scope=out_of_scope, subset_target=None if not subset_ids else len(subset_ids))
    state["run_id"] = run_id
    atomic_write(run_dir / "run.json", detector.dumps(state))
    chosen = None if not subset_ids else set(subset_ids)
    for video in manifest["videos"]:
        atomic_write(
            run_dir / video["video_id"] / "screen_changes.json",
            detector.dumps(video_shell(video, manifest["settings"], schema_version=manifest["schema_version"], subset_ids=chosen)),
        )
    print(run_id, flush=True)
    print(str(run_dir.resolve()), flush=True)
    return 0


def command_render_only(run_dir: Path) -> int:
    manifest = detector.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    ffmpeg = manifest["runtime"]["ffmpeg"]["path"]
    sources = {video["video_id"]: detector.loads((ROOT / video["source_path"]).read_text(encoding="utf-8")) for video in manifest["videos"]}
    with run_lock(run_dir):
        for video in manifest["videos"]:
            for plan in video["candidates"]:
                path = candidate_path(run_dir, video["video_id"], plan)
                if not path.exists():
                    continue
                document = detector.loads(path.read_text(encoding="utf-8"))
                if document.get("status") != "ok":
                    continue
                before = document.get("detection_key")
                problems = render_document_with_ffmpeg(run_dir, video, document, ffmpeg)
                if document.get("detection_key") != before:
                    print_problem("표시 재생성이 검출 key를 바꿨습니다.", rel(path), plan["candidate_id"])
                    return 1
                if problems:
                    document.setdefault("warnings", []).append({"code": "render_failed", "problems": problems})
                atomic_write(path, detector.dumps(document))
                review.write_svg(document, run_dir / video["video_id"] / "plots" / f"u{plan['unit_index']}_c{plan['chunk_index']}.svg")
        publish_reviews(run_dir, manifest, sources)
    print(str(run_dir.resolve()), flush=True)
    return 0


def _frame(pts: int, raw: bytes, numerator: int = 1, denominator: int = 1, width: int | None = None, height: int = 2) -> dict[str, Any]:
    if width is None:
        width = len(raw) // height
    return {
        "pts": pts,
        "time_base_numerator": numerator,
        "time_base_denominator": denominator,
        "stream_index": 0,
        "width": width,
        "height": height,
        "pixel_format": "gray",
        "raw": raw,
    }


def command_check() -> int:
    failures = []

    def expect(condition: bool, label: str) -> None:
        if not condition:
            failures.append(label)

    gray = bytes([8, 8, 8, 8])
    same = detector.analyze_frames([_frame(index, gray) for index in range(5)], start=Decimal(0), end=Decimal("2.5"), stream_start=Fraction(0))
    expect(same["status"] == "ok" and same["boundaries"] == [] and len(same["segments"]) == 1, "identical")
    expect(all(item["value"] == 0 for item in same["differences"]), "zero diff")
    one = detector.analyze_frames([_frame(0, gray)], start=Decimal(0), end=Decimal(1), stream_start=Fraction(0))
    expect(one["status"] == "ok" and one["differences"] == [] and len(one["segments"]) == 1, "one frame")
    zero = detector.analyze_frames([], start=Decimal(0), end=Decimal(1), stream_start=Fraction(0))
    expect(zero["status"] == "failed" and zero["segments"] == [], "zero frames")

    def solid(value: int) -> bytes:
        return bytes([value, value, value, value])

    changed = detector.analyze_frames(
        [_frame(index, solid(0 if index < 2 else 255)) for index in range(4)],
        start=Decimal(0), end=Decimal("3.5"), stream_start=Fraction(0),
    )
    expect([item["video_seconds"] for item in changed["boundaries"]] == [Decimal("2")], "one change")
    two = detector.analyze_frames(
        [_frame(0, solid(0)), _frame(2, solid(255)), _frame(4, solid(0))],
        start=Decimal(0), end=Decimal("4.5"), stream_start=Fraction(0),
    )
    expect(len(two["boundaries"]) == 2 and two["segments"][0]["video_end"] - two["segments"][0]["video_start"] < Decimal(3), "two changes keep short")
    constant = detector.analyze_frames(
        [_frame(index, solid(index * 40)) for index in range(6)],
        start=Decimal(0), end=Decimal("5.5"), stream_start=Fraction(0),
    )
    expect(constant["boundaries"] == [] and constant["differences"][0]["mad"] == 0 and constant["differences"][0]["statistics_mode"] == "neighbor_5s", "mad zero")
    small = bytes(400)
    large = bytearray(400)
    large[0] = 255
    large[1] = 255
    equal = detector.analyze_frames(
        [_frame(0, bytes(small), width=20, height=20), _frame(1, bytes(large), width=20, height=20)],
        start=Decimal(0), end=Decimal(2), stream_start=Fraction(0),
    )
    expect(equal["differences"][0]["value"] == Decimal("0.005") and equal["differences"][0]["passes_threshold"] is False, "threshold tie")
    expect(equal["differences"][0]["statistics_mode"] == "absolute" and equal["differences"][0]["median"] is None, "few neighbors")
    tie = detector.analyze_frames(
        [_frame(0, solid(0), denominator=2), _frame(1, solid(255), denominator=2), _frame(2, solid(0), denominator=2)],
        start=Decimal(0), end=Decimal("1.5"), stream_start=Fraction(0),
    )
    expect([item["peak_action"] for item in tie["differences"]] == ["keep", "suppress"], "peak tie")
    expect(tie["differences"][1]["suppression_refs"][0]["reason"].startswith("같은 차분"), "suppression reason")
    edges = [item["video_end"] for item in two["segments"]]
    expect(edges[-1] == Decimal("4.5") and len({(item["video_start"], item["video_end"]) for item in two["segments"]}) == len(two["segments"]), "coverage")
    expect(all(item["video_end"] > item["video_start"] for item in two["segments"]), "nonzero length")
    shifted = detector.video_seconds(150, 1, 100, Fraction(1))
    expect(shifted == Fraction(1, 2), "nonzero stream start")
    sparse = detector.analyze_frames(
        [_frame(0, solid(0), denominator=10), _frame(12, solid(3), denominator=10)],
        start=Decimal(0), end=Decimal(2), stream_start=Fraction(0),
    )
    expect(len(sparse["samples"]) == 2 and sparse["samples"][1]["request_indices"].count(1) == 1, "shared pts")
    expect(sparse["warnings"] and sparse["warnings"][0]["code"] == "sample_gap", "sample gap")
    expect(any(item["status"] == "unavailable" for item in sparse["requests"]), "unavailable")
    closed = detector.analyze_frames([_frame(0, gray), _frame(1, gray)], start=Decimal(0), end=Decimal(1), stream_start=Fraction(0))
    expect([item["pts"] for item in closed["samples"]] == [0] and closed["segments"][0]["video_end"] == Decimal(1), "end pts excluded")
    tail = detector.analyze_frames([_frame(0, gray), _frame(1, gray, denominator=2)], start=Decimal(0), end=Decimal("0.8"), stream_start=Fraction(0))
    expect(tail["segments"][0]["video_end"] == Decimal("0.8") and "tail" in tail["samples"][-1]["roles"], "end after last pts")
    expect(detector.grid_requests(Decimal("0.1"), Decimal("1.2")) == [Decimal("0.1"), Decimal("0.6"), Decimal("1.1")], "fixed grid")
    first = detector.analyze_frames([_frame(0, gray)], start=Decimal(0), end=Decimal(1), stream_start=Fraction(0))
    second = detector.analyze_frames([_frame(10, solid(9))], start=Decimal(10), end=Decimal(11), stream_start=Fraction(0))
    expect(first["segments"][0]["video_end"] == Decimal(1) and second["segments"][0]["video_start"] == Decimal(10), "independent ranges")
    try:
        detector.analyze_frames([_frame(2, gray), _frame(1, gray)], start=Decimal(0), end=Decimal(3), stream_start=Fraction(0))
        expect(False, "non-monotonic")
    except detector.T3Error:
        pass
    try:
        detector.as_decimal(True, "video_start")
        expect(False, "bool time")
    except detector.T3Error:
        pass
    try:
        detector.pair_records([{"width": 1, "height": 1}], [b"a", b"b"])
        expect(False, "raw mismatch")
    except detector.T3Error:
        pass
    good = {"candidate_id": "H2WN130OzLg:u1:c0", "candidate_source": "budget_guard", "policy_version": "t2.budget_guard.v1", "model_inference_success": False, "decision": None}
    expect(audit_candidate(good, file_hash="abc", expected_hash="abc", video_id="H2WN130OzLg") == [], "budget accept")
    bad = dict(good)
    bad["model_inference_success"] = True
    expect(audit_candidate(bad, file_hash="abc", expected_hash="abc", video_id="H2WN130OzLg"), "budget reject success flip")
    expect(audit_candidate(good, file_hash="nope", expected_hash="abc", video_id="H2WN130OzLg"), "hash reject")
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary) / "run"
        expect(create_run_directory(destination, ["원인: 입력 오류\n파일: x\n대상: y"]) is False and not destination.exists(), "no run on error")
        kept = Path(temporary) / "kept.json"
        kept.write_text("KEEP", encoding="utf-8")
        rows = {
            "a": {"status": "ok", "key": "k"},
            "b": {"status": "failed", "key": "k"},
            "c": {"status": "pending", "key": None},
        }
        processed = []
        for name, row in rows.items():
            action = detector.resume_action(row["status"], row["key"], "k")
            if action in {"reuse", "skip_failed"}:
                continue
            processed.append(name)
        expect(processed == ["c"] and kept.read_text(encoding="utf-8") == "KEEP", "no retry failed")
        expect(detector.resume_action("ok", "old", "new") == "reject_key", "reject other key")
    layout = detector.tile_layout(640, 360)
    expect(len(layout) == 32 and sum(item["pixel_count"] for item in layout) == 640 * 360, "640x360 tiles")
    remainder = detector.tile_layout(100, 50)
    expect(
        len(remainder) == 2 and remainder[0]["width"] == 80 and remainder[1]["width"] == 20 and remainder[0]["height"] == 50,
        "remainder tile",
    )
    expect(sum(item["pixel_count"] for item in remainder) == 100 * 50, "remainder cover")

    def paint(left_n: int, right_n: int, right_origin: int = 80) -> bytes:
        raw = bytearray(160 * 90)

        def fill(origin: int, start: int, count: int) -> None:
            seen = 0
            for y in range(90):
                for x in range(80):
                    if start <= seen < start + count:
                        raw[y * 160 + origin + x] = 255
                    seen += 1

        fill(0, 0, left_n)
        fill(right_origin, 0, right_n)
        return bytes(raw)

    quiet = paint(0, 0)
    local = paint(40, 0)
    b01_local = detector.analyze_frames(
        [_frame(0, quiet, width=160, height=90), _frame(1, local, width=160, height=90)],
        start=Decimal(0), end=Decimal(2), stream_start=Fraction(0), settings=detector.SETTINGS_B01,
    )
    b00_local = detector.analyze_frames(
        [_frame(0, quiet, width=160, height=90), _frame(1, local, width=160, height=90)],
        start=Decimal(0), end=Decimal(2), stream_start=Fraction(0),
    )
    expect(len(b01_local["boundaries"]) == 1 and b01_local["differences"][0]["passes_threshold"] is True, "local tile boundary")
    expect(b00_local["boundaries"] == [] and b00_local["differences"][0]["value"] < Decimal("0.005"), "global misses local")
    expect("tiles" not in b00_local["differences"][0] and "value" in b00_local["differences"][0], "b00 keys stay")
    expect("value" not in b01_local["differences"][0] and b01_local["differences"][0]["global_mean"] == b00_local["differences"][0]["value"], "global mean diagnostic")
    replayed, _segments = detector.replay_boundaries(
        b01_local["differences"], b01_local["samples"], Decimal(0), Decimal(2),
        config_id="B01", logic_version=detector.LOGIC_VERSION_B01, schema_version=detector.SCHEMA_VERSION_B01,
        settings=detector.SETTINGS_B01,
    )
    expect([item["video_seconds"] for item in replayed] == [item["video_seconds"] for item in b01_local["boundaries"]], "tile replay")
    broad = detector.analyze_frames(
        [_frame(0, quiet, width=160, height=90), _frame(1, bytes([255]) * (160 * 90), width=160, height=90)],
        start=Decimal(0), end=Decimal(2), stream_start=Fraction(0), settings=detector.SETTINGS_B01,
    )
    expect(len(broad["boundaries"]) == 1 and len(broad["differences"][0]["passing_tile_indices"]) == 2, "full change one boundary")
    steady_frames = []
    for index in range(6):
        steady_frames.append(_frame(index, paint(0, 0) if index % 2 == 0 else paint(400, 0), denominator=2, width=160, height=90))
    steady = detector.analyze_frames(
        steady_frames, start=Decimal(0), end=Decimal(3), stream_start=Fraction(0), settings=detector.SETTINGS_B01,
    )
    expect(steady["boundaries"] == [] and steady["differences"][0]["tiles"][0]["mad"] == 0, "steady tile mad")
    paired = detector.analyze_frames(
        [_frame(0, quiet, width=160, height=90), _frame(1, paint(100, 100), width=160, height=90)],
        start=Decimal(0), end=Decimal(2), stream_start=Fraction(0), settings=detector.SETTINGS_B01,
    )
    expect(len(paired["boundaries"]) == 1 and len(paired["differences"][0]["passing_tile_indices"]) == 2, "two tiles one boundary")

    def peak_frames(right_count: int) -> list[dict[str, Any]]:
        frames = []
        for index in range(8):
            raw = bytearray(160 * 90)
            if index % 2:
                seen = 0
                for y in range(90):
                    for x in range(80):
                        if seen < right_count:
                            raw[y * 160 + 80 + x] = 255
                        seen += 1
            if index == 4:
                for x in range(80):
                    raw[x] = 255
            if index >= 5:
                seen = 0
                for y in range(90):
                    for x in range(80):
                        if 80 <= seen < 280:
                            raw[y * 160 + x] = 255
                        seen += 1
            frames.append(_frame(index, bytes(raw), denominator=2, width=160, height=90))
        return frames

    peak_a = detector.analyze_frames(
        peak_frames(720), start=Decimal(0), end=Decimal(4), stream_start=Fraction(0), settings=detector.SETTINGS_B01,
    )
    peak_b = detector.analyze_frames(
        peak_frames(1440), start=Decimal(0), end=Decimal(4), stream_start=Fraction(0), settings=detector.SETTINGS_B01,
    )
    by_index = {item["index"]: item for item in peak_a["differences"]}
    by_index_b = {item["index"]: item for item in peak_b["differences"]}
    expect(by_index[4]["peak_action"] == "suppress" and by_index[5]["peak_action"] == "keep", "passing score suppresses")
    expect(by_index[4]["tile_max"] == by_index[5]["tile_max"], "tile max would tie")
    expect(by_index[5]["peak_tile_index"] == 0 and by_index[5]["max_tile_index"] == 1, "peak tile is passing")
    expect(
        [item["peak_action"] for item in peak_b["differences"]] == [item["peak_action"] for item in peak_a["differences"]]
        and [item["peak_score"] for item in by_index_b.values()] == [item["peak_score"] for item in by_index.values()],
        "nonpassing tile does not change peak",
    )
    expect(all(item["peak_score"] is None for item in peak_a["differences"] if not item["passes_threshold"]), "nonpass excluded")
    try:
        detector.replay_boundaries(
            changed["differences"], changed["samples"], Decimal(0), Decimal("3.5"),
            config_id="B01", logic_version="t3.screen_changes.v1", schema_version="t3.screen_changes.v1",
        )
        expect(False, "unknown identity")
    except detector.T3Error as exc:
        expect(exc.stage == "replay", "unknown identity stage")
    try:
        detector.replay_boundaries(
            [{"index": 1, "before_sample": 0, "after_sample": 1, "elapsed_video_seconds": Decimal(1), "global_mean": Decimal(0), "tiles": []}],
            b01_local["samples"], Decimal(0), Decimal(2),
            config_id="B01", logic_version=detector.LOGIC_VERSION_B01, schema_version=detector.SCHEMA_VERSION_B01,
            settings=detector.SETTINGS_B01,
        )
        expect(False, "missing tiles")
    except detector.T3Error as exc:
        expect("타일" in str(exc), "missing tiles message")
    damaged = [{**b01_local["differences"][0], "tiles": [dict(tile) for tile in b01_local["differences"][0]["tiles"]]}]
    damaged[0]["tiles"][0] = dict(damaged[0]["tiles"][0])
    damaged[0]["tiles"][0]["x"] = 7
    try:
        detector.replay_boundaries(
            damaged, b01_local["samples"], Decimal(0), Decimal(2),
            config_id="B01", logic_version=detector.LOGIC_VERSION_B01, schema_version=detector.SCHEMA_VERSION_B01,
            settings=detector.SETTINGS_B01,
        )
        expect(False, "damaged layout")
    except detector.T3Error as exc:
        expect("배치" in str(exc), "damaged layout message")
    b00_replay, _ = detector.replay_boundaries(
        changed["differences"], changed["samples"], Decimal(0), Decimal("3.5"),
        config_id="B00", logic_version="t3.screen_changes.v1", schema_version="t3.screen_changes.v1",
    )
    expect([item["video_seconds"] for item in b00_replay] == [Decimal("2")], "b00 replay")

    def half_frame(index: int, raw: bytes) -> dict[str, Any]:
        return _frame(index, raw, denominator=2, width=160, height=90)

    left = paint(400, 0)
    both = paint(400, 400)

    def run_pair(frames: list[dict[str, Any]], end: str) -> tuple[dict[str, Any], dict[str, Any]]:
        common = {"start": Decimal(0), "end": Decimal(end), "stream_start": Fraction(0)}
        return (
            detector.analyze_frames(frames, settings=detector.SETTINGS_B01, **common),
            detector.analyze_frames(frames, settings=detector.SETTINGS_B02, **common),
        )

    flash_b01, flash_b02 = run_pair([half_frame(index, left if index == 1 else quiet) for index in range(6)], "3")
    expect(len(flash_b01["boundaries"]) == 1 and flash_b02["boundaries"] == [], "flash suppress")
    flash_row = next(item for item in flash_b02["differences"] if item["peak_action"] == "keep" and int(item["after_sample"]) == 1)
    expect(flash_row["temporal_action"] == "suppress" and flash_row["temporal_reason"] == "reverted", "flash reason")
    expect(all("temporal_action" not in item for item in flash_b01["differences"]), "b01 omits temporal")
    expect(detector.SETTINGS_B01["config_id"] == "B01" and detector.SETTINGS_B01["difference"] == "tile_mean_absolute_or", "b01 settings unchanged")
    flash_replay, _flash_segments = detector.replay_boundaries(
        flash_b02["differences"], flash_b02["samples"], Decimal(0), Decimal(3),
        config_id="B02", logic_version=detector.LOGIC_VERSION_B02, schema_version=detector.SCHEMA_VERSION_B02,
        settings=detector.SETTINGS_B02,
    )
    expect(flash_replay == [], "flash replay")
    b01_flash_replay, _ = detector.replay_boundaries(
        flash_b01["differences"], flash_b01["samples"], Decimal(0), Decimal(3),
        config_id="B01", logic_version=detector.LOGIC_VERSION_B01, schema_version=detector.SCHEMA_VERSION_B01,
        settings=detector.SETTINGS_B01,
    )
    expect([item["video_seconds"] for item in b01_flash_replay] == [item["video_seconds"] for item in flash_b01["boundaries"]], "b01 flash replay")

    _held_b01, held_b02 = run_pair([half_frame(index, left if index else quiet) for index in range(6)], "3")
    expect([item["video_seconds"] for item in held_b02["boundaries"]] == [Decimal("0.5")], "persistent boundary")
    held_row = next(item for item in held_b02["differences"] if int(item["after_sample"]) == 1)
    expect(held_row["temporal_action"] == "keep" and held_row["temporal_reason"] == "persists", "persistent reason")

    _partial_b01, partial_b02 = run_pair(
        [half_frame(index, left if 1 <= index <= 3 else quiet) for index in range(6)], "3",
    )
    partial_row = next(item for item in partial_b02["differences"] if int(item["after_sample"]) == 1)
    expect(
        partial_row["temporal_action"] == "keep" and partial_row["temporal_reason"] == "persists"
        and Decimal("0.5") in [item["video_seconds"] for item in partial_b02["boundaries"]],
        "one offset remains",
    )

    _tiles_b01, tiles_b02 = run_pair(
        [half_frame(0, quiet), half_frame(1, both)] + [half_frame(index, left) for index in range(2, 6)], "3",
    )
    tiles_row = next(item for item in tiles_b02["differences"] if int(item["after_sample"]) == 1)
    expect(
        len(tiles_row["passing_tile_indices"]) == 2 and tiles_row["temporal_reason"] == "persists"
        and Decimal("0.5") in [item["video_seconds"] for item in tiles_b02["boundaries"]],
        "one tile remains",
    )

    _short_b01, short_b02 = run_pair(
        [half_frame(0, quiet), half_frame(1, left), half_frame(2, quiet)], "1.2",
    )
    short_row = next(item for item in short_b02["differences"] if int(item["after_sample"]) == 1)
    expect(
        short_row["temporal_reason"] == "insufficient_samples"
        and [item["status"] for item in short_row["temporal"]["comparisons"]] == ["unavailable", "unavailable"]
        and [item["video_seconds"] for item in short_b02["boundaries"]] == [Decimal("0.5")],
        "short tail kept",
    )

    _late_b01, late_b02 = run_pair([half_frame(0, quiet), half_frame(1, left), half_frame(6, quiet)], "3.5")
    late_row = next(item for item in late_b02["differences"] if int(item["after_sample"]) == 1)
    expect(
        late_row["temporal_reason"] == "insufficient_samples"
        and [item["status"] for item in late_row["temporal"]["comparisons"]] == ["too_late", "ok"]
        and Decimal("0.5") in [item["video_seconds"] for item in late_b02["boundaries"]],
        "late sample kept",
    )
    expect(
        {item["video_seconds"] for item in late_b02["boundaries"]} <= {item["video_seconds"] for item in _late_b01["boundaries"]},
        "b02 subset of b01",
    )
    damaged_temporal = [dict(item) for item in flash_b02["differences"]]
    damaged_temporal[0] = dict(damaged_temporal[0])
    del damaged_temporal[0]["temporal_action"]
    try:
        detector.replay_boundaries(
            damaged_temporal, flash_b02["samples"], Decimal(0), Decimal(3),
            config_id="B02", logic_version=detector.LOGIC_VERSION_B02, schema_version=detector.SCHEMA_VERSION_B02,
            settings=detector.SETTINGS_B02,
        )
        expect(False, "missing temporal")
    except detector.T3Error as exc:
        expect("시간 판정" in str(exc), "missing temporal message")
    try:
        detector.replay_boundaries(
            flash_b02["differences"], flash_b02["samples"], Decimal(0), Decimal(3),
            config_id="B02", logic_version=detector.LOGIC_VERSION_B01, schema_version=detector.SCHEMA_VERSION_B02,
        )
        expect(False, "b02 wrong logic")
    except detector.T3Error as exc:
        expect(exc.stage == "replay", "b02 wrong logic stage")
    sample_row = {
        "pts": 3, "time_base": {"numerator": 1, "denominator": 2}, "width": 4, "height": 2,
        "pixel_format": "gray", "analysis_bytes_sha256": "abc",
    }
    expect(sample_contract_problem([sample_row], [dict(sample_row)]) is None, "sample contract match")
    changed_hash = dict(sample_row)
    changed_hash["analysis_bytes_sha256"] = "def"
    expect(sample_contract_problem([sample_row], [changed_hash]) is not None, "sample contract hash")
    base_key = {"settings": detector.SETTINGS, "runner_sha256": "runner-a"}
    expect(detector.detection_key(base_key) != detector.detection_key({**base_key, "runner_sha256": "runner-b"}), "runner key")
    expect(detector.detection_key(base_key) != detector.detection_key({**base_key, "settings": detector.SETTINGS_B01}), "settings key")
    tally = scope_counts(["ok"] * 5 + ["out_of_scope"] * 134)
    expect(tally == {"success": 5, "failed": 0, "pending": 0, "out_of_scope": 134}, "subset tally")
    expect(phase_for(subset=True, pending=0, failed=0, problems=False) == "subset", "subset phase")
    expect(aggregate_status(["ok"] * 5 + ["out_of_scope"] * 134, []) == "ok", "subset video ok")
    expect(scope_problem("out_of_scope", subset_run=False, in_scope=False) is not None, "b00 rejects out of scope")
    expect(scope_problem("out_of_scope", subset_run=True, in_scope=True) is not None, "in scope rejects out of scope")
    expect(scope_problem("out_of_scope", subset_run=True, in_scope=False) is None, "subset allows out of scope")

    def paint(rects: list[tuple[int, int, int, int, int]], fill: int = 0) -> bytes:
        raw = bytearray([fill]) * (160 * 90)
        for x0, y0, rect_width, rect_height, value in rects:
            for y in range(y0, y0 + rect_height):
                start = y * 160 + x0
                raw[start : start + rect_width] = bytes([value]) * rect_width
        return bytes(raw)

    def run_spatial(before: bytes, after: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
        frames = [
            _frame(0, before, width=160, height=90),
            _frame(1, after, width=160, height=90),
        ]
        common = {"start": Decimal(0), "end": Decimal(2), "stream_start": Fraction(0)}
        return (
            detector.analyze_frames(frames, settings=detector.SETTINGS_B01, **common),
            detector.analyze_frames(frames, settings=detector.SETTINGS_B03, **common),
        )

    moved_before = paint([(10, 10, 8, 8, 255)])
    moved_after = paint([(20, 10, 8, 8, 255)])
    moved_b01, moved_b03 = run_spatial(moved_before, moved_after)
    moved_row = moved_b03["differences"][0]
    expect(
        len(moved_b01["boundaries"]) == 1 and moved_b03["boundaries"] == []
        and moved_row["spatial_action"] == "suppress" and moved_row["spatial_reason"] == "translated"
        and abs(int(moved_row["spatial"]["shift"]["dx"])) == 10 and int(moved_row["spatial"]["shift"]["dy"]) == 0
        and int(moved_row["spatial"]["shift"]["source_count"]) == 64
        and int(moved_row["spatial"]["shift"]["residual_count"]) == 0,
        "pure translation suppress",
    )
    expect("spatial_action" not in moved_b01["differences"][0] and "temporal_action" not in moved_b03["differences"][0], "b03 omits temporal")
    expect(detector.SETTINGS_B02["difference"] == "tile_mean_absolute_or_temporal" and detector.SETTINGS_B03["spatial_pixel"] == 32, "b02 settings unchanged")
    moved_replay, _moved_segments = detector.replay_boundaries(
        moved_b03["differences"], moved_b03["samples"], Decimal(0), Decimal(2),
        config_id="B03", logic_version=detector.LOGIC_VERSION_B03, schema_version=detector.SCHEMA_VERSION_B03,
        settings=detector.SETTINGS_B03,
    )
    expect(moved_replay == [], "pure translation replay")

    stroke_after = paint([(20, 10, 8, 8, 255), (140, 70, 2, 2, 255)])
    _stroke_b01, stroke_b03 = run_spatial(moved_before, stroke_after)
    stroke_row = stroke_b03["differences"][0]
    expect(
        stroke_row["spatial_action"] == "keep" and stroke_row["spatial_reason"] == "not_explained"
        and stroke_row["spatial"]["shift"] is None and int(stroke_row["spatial"]["explained_shift_count"]) == 0
        and [item["video_seconds"] for item in stroke_b03["boundaries"]] == [Decimal(1)],
        "translation plus stroke kept",
    )
    stroke_replay, _stroke_segments = detector.replay_boundaries(
        stroke_b03["differences"], stroke_b03["samples"], Decimal(0), Decimal(2),
        config_id="B03", logic_version=detector.LOGIC_VERSION_B03, schema_version=detector.SCHEMA_VERSION_B03,
        settings=detector.SETTINGS_B03,
    )
    expect([item["video_seconds"] for item in stroke_replay] == [Decimal(1)], "stroke replay")

    glyph_after = paint([(10, 10, 10, 5, 255)])
    glyph_b01, glyph_b03 = run_spatial(paint([]), glyph_after)
    glyph_row = glyph_b03["differences"][0]
    expect(
        glyph_row["global_mean"] < Decimal("0.005") and glyph_row["passes_threshold"] is True
        and glyph_b01["boundaries"] and glyph_row["spatial_reason"] == "not_explained"
        and [item["video_seconds"] for item in glyph_b03["boundaries"]] == [Decimal(1)],
        "small mean new mark kept",
    )

    covered_before = bytes([255]) * (160 * 90)
    covered_raw = bytearray(covered_before)
    for y in range(10, 30):
        covered_raw[y * 160 + 10 : y * 160 + 30] = bytes([100]) * 20
    _cover_b01, cover_b03 = run_spatial(covered_before, bytes(covered_raw))
    cover_row = cover_b03["differences"][0]
    expect(
        cover_row["spatial_reason"] == "not_explained" and int(cover_row["spatial"]["component_count"]) == 1
        and [item["video_seconds"] for item in cover_b03["boundaries"]] == [Decimal(1)],
        "occlusion kept",
    )

    faint_b01, faint_b03 = run_spatial(paint([]), bytes([20]) * (160 * 90))
    faint_row = faint_b03["differences"][0]
    expect(
        faint_b01["boundaries"] and faint_row["spatial_reason"] == "no_change_shape"
        and int(faint_row["spatial"]["change_pixel_count"]) == 0
        and [item["video_seconds"] for item in faint_b03["boundaries"]] == [Decimal(1)],
        "subthreshold change kept",
    )
    expect(
        {item["video_seconds"] for item in stroke_b03["boundaries"]} <= {item["video_seconds"] for item in _stroke_b01["boundaries"]},
        "b03 subset of b01",
    )
    damaged_spatial = detector.loads(detector.dumps(moved_b03["differences"]))
    del damaged_spatial[0]["spatial_action"]
    try:
        detector.replay_boundaries(
            damaged_spatial, moved_b03["samples"], Decimal(0), Decimal(2),
            config_id="B03", logic_version=detector.LOGIC_VERSION_B03, schema_version=detector.SCHEMA_VERSION_B03,
            settings=detector.SETTINGS_B03,
        )
        expect(False, "missing spatial")
    except detector.T3Error as exc:
        expect("공간 판정" in str(exc), "missing spatial message")
    mismatched = detector.loads(detector.dumps(moved_b03["differences"]))
    mismatched[0]["spatial_action"] = "keep"
    mismatched[0]["spatial_reason"] = "not_explained"
    try:
        detector.replay_boundaries(
            mismatched, moved_b03["samples"], Decimal(0), Decimal(2),
            config_id="B03", logic_version=detector.LOGIC_VERSION_B03, schema_version=detector.SCHEMA_VERSION_B03,
            settings=detector.SETTINGS_B03,
        )
        expect(False, "spatial replay mismatch")
    except detector.T3Error as exc:
        expect("재생 판정" in str(exc), "spatial replay mismatch message")
    try:
        detector.replay_boundaries(
            moved_b03["differences"], moved_b03["samples"], Decimal(0), Decimal(2),
            config_id="B03", logic_version=detector.LOGIC_VERSION_B01, schema_version=detector.SCHEMA_VERSION_B03,
        )
        expect(False, "b03 wrong logic")
    except detector.T3Error as exc:
        expect(exc.stage == "replay", "b03 wrong logic stage")

    expect("peak_neighbor" not in detector.SETTINGS_B01, "b01 settings stay")
    expect(detector.SETTINGS_B04["peak_neighbor"] == "one_grid_request_step", "b04 neighbor setting")
    expect(detector.SCHEMA_VERSION_B04 == "t3.screen_changes.v5", "b04 schema")

    def counted_frame(pts: int, count: int, denominator: int) -> dict[str, Any]:
        raw = bytearray(160 * 90)
        seen = 0
        for y in range(90):
            for x in range(80):
                if seen < count:
                    raw[y * 160 + x] = 255
                seen += 1
        return _frame(pts, bytes(raw), denominator=denominator, width=160, height=90)

    def spacing_actions(points: list[tuple[int, int]], denominator: int, end: str, settings: dict[str, Any]) -> dict[int, dict[str, Any]]:
        analyzed = detector.analyze_frames(
            [counted_frame(pts, count, denominator) for pts, count in points],
            start=Decimal(0), end=Decimal(end), stream_start=Fraction(0), settings=settings,
        )
        return {int(item["index"]): item for item in analyzed["differences"]}, analyzed

    for label, points, denominator, end in (
        ("0.5", [(0, 0), (1, 80), (2, 800)], 2, "1.5"),
        ("0.5005", [(0, 0), (5005, 80), (10010, 800)], 10000, "1.6"),
        ("0.501", [(0, 0), (501, 80), (1002, 800)], 1000, "1.6"),
        ("0.502", [(0, 0), (502, 80), (1004, 800)], 1000, "1.7"),
    ):
        b01_by, _b01_doc = spacing_actions(points, denominator, end, detector.SETTINGS_B01)
        b04_by, b04_doc = spacing_actions(points, denominator, end, detector.SETTINGS_B04)
        b05_by, b05_doc = spacing_actions(points, denominator, end, detector.SETTINGS_B05)
        expect(b01_by[1]["passes_threshold"] and b01_by[2]["passes_threshold"], f"{label} both pass")
        expect("neighbor_basis" not in b01_by[1]["suppression_refs"][0] if b01_by[1]["suppression_refs"] else True, f"{label} b01 basis absent")
        if label == "0.5":
            expect(b01_by[1]["peak_action"] == "suppress" and b01_by[2]["peak_action"] == "keep", "0.5 b01 suppresses")
            expect(b04_by[1]["peak_action"] == "suppress" and b04_by[1]["suppression_refs"][0]["neighbor_basis"] == "actual_time", "0.5 stays actual time")
            expect([item["peak_action"] for item in b04_doc["differences"]] == [item["peak_action"] for item in _b01_doc["differences"]], "0.5 actions agree")
            expect(b05_by[1]["peak_action"] == "suppress" and b05_by[1]["suppression_refs"][0]["neighbor_basis"] == "actual_time", "b05 0.5 stays actual time")
            expect(b05_by[1]["grid_support_reviews"] == [], "b05 0.5 skips grid review")
            expect([item["peak_action"] for item in b05_doc["differences"]] == [item["peak_action"] for item in b04_doc["differences"]], "b05 0.5 matches b04")
        else:
            expect(b01_by[1]["peak_action"] == "keep" and b01_by[2]["peak_action"] == "keep", f"{label} b01 keeps both")
            expect(b04_by[1]["peak_action"] == "suppress" and b04_by[1]["suppression_refs"][0]["neighbor_basis"] == "grid_step", f"{label} grid step")
            expect(b04_by[2]["peak_action"] == "keep", f"{label} later kept")
            expect(b05_by[1]["peak_action"] == "suppress" and b05_by[1]["suppression_refs"][0]["neighbor_basis"] == "grid_step", f"b05 {label} grid support")
            expect(b05_by[1]["suppression_refs"][0].get("support") == "subset", f"b05 {label} subset")
            expect(b05_by[2]["peak_action"] == "keep", f"b05 {label} later kept")
            replayed_support, _support_segments = detector.replay_boundaries(
                detector.loads(detector.dumps(b05_doc["differences"])),
                detector.loads(detector.dumps(b05_doc["samples"])),
                Decimal(0), Decimal(end),
                config_id="B05", logic_version=detector.LOGIC_VERSION_B05, schema_version=detector.SCHEMA_VERSION_B05,
                settings=detector.SETTINGS_B05,
            )
            expect(
                [item["video_seconds"] for item in replayed_support] == [item["video_seconds"] for item in b05_doc["boundaries"]],
                f"b05 {label} replay",
            )
        replayed_b04, _replay_segments = detector.replay_boundaries(
            detector.loads(detector.dumps(b04_doc["differences"])),
            detector.loads(detector.dumps(b04_doc["samples"])),
            Decimal(0), Decimal(end),
            config_id="B04", logic_version=detector.LOGIC_VERSION_B04, schema_version=detector.SCHEMA_VERSION_B04,
            settings=detector.SETTINGS_B04,
        )
        expect(
            [item["video_seconds"] for item in replayed_b04] == [item["video_seconds"] for item in b04_doc["boundaries"]],
            f"{label} replay",
        )

    two_step_b04, _two_doc = spacing_actions(
        [(0, 0), (5005, 200), (10010, 80), (15015, 800)], 10000, "2.1", detector.SETTINGS_B04,
    )
    expect(two_step_b04[1]["peak_action"] == "keep" and two_step_b04[3]["peak_action"] == "keep", "two steps stay apart")
    expect(two_step_b04[2]["peak_action"] == "suppress", "middle loses to neighbors")

    collapsed_b01, _collapsed_b01 = spacing_actions(
        [(0, 0), (5005, 80), (68730, 400), (70060, 2000)], 10000, "8", detector.SETTINGS_B01,
    )
    collapsed_b04, _collapsed_b04 = spacing_actions(
        [(0, 0), (5005, 80), (68730, 400), (70060, 2000)], 10000, "8", detector.SETTINGS_B04,
    )
    expect(collapsed_b01[1]["peak_action"] == "keep" and collapsed_b04[1]["peak_action"] == "keep", "collapsed gap is not a neighbor")
    expect(collapsed_b01[2]["peak_action"] == "suppress" and collapsed_b04[2]["peak_action"] == "suppress", "0.133 appearance stays suppressed")
    expect(collapsed_b04[2]["suppression_refs"][0]["neighbor_basis"] == "actual_time", "0.133 uses actual time")
    expect(len(detector._grid_request_indices(_collapsed_b04["samples"][2])) > 1, "collapsed sample holds many requests")

    def direct_peak(specs: list[dict[str, Any]], scores: list[str], settings: dict[str, Any]) -> list[dict[str, Any]]:
        samples = []
        for index, spec in enumerate(specs):
            samples.append({
                "sample_index": index,
                "video_seconds": Decimal(spec["t"]),
                "request_indices": spec["requests"],
                "roles": spec.get("roles", ["grid"]),
                "width": 160,
                "height": 90,
            })
        layout = detector.tile_layout(160, 90, 80, 90)
        rows = []
        for index, score in enumerate(scores, start=1):
            rows.append({
                "index": index,
                "before_sample": index - 1,
                "after_sample": index,
                "elapsed_video_seconds": samples[index]["video_seconds"] - samples[index - 1]["video_seconds"],
                "global_mean": Decimal(score),
                "t": samples[index]["video_seconds"],
                "tile_values": [Decimal(score), Decimal(0)],
            })
        return detector._finish_tile_rows(rows, layout, samples, settings)

    hole = direct_peak(
        [{"t": "0", "requests": [0]}, {"t": "0.5005", "requests": [1]}, {"t": "1.001", "requests": [3]}],
        ["0.02", "0.08"],
        detector.SETTINGS_B04,
    )
    expect(hole[0]["peak_action"] == "keep" and hole[1]["peak_action"] == "keep", "unavailable hole is not a neighbor")
    tail_close = direct_peak(
        [
            {"t": "0", "requests": [0]},
            {"t": "0.5005", "requests": [1]},
            {"t": "0.9005", "requests": [2], "roles": ["tail"]},
        ],
        ["0.02", "0.08"],
        detector.SETTINGS_B04,
    )
    expect(tail_close[0]["peak_action"] == "suppress" and tail_close[0]["suppression_refs"][0]["neighbor_basis"] == "actual_time", "tail within 0.5 uses time")
    tail_far = direct_peak(
        [
            {"t": "0", "requests": [0]},
            {"t": "0.5005", "requests": [1]},
            {"t": "1.1005", "requests": [2], "roles": ["tail"]},
        ],
        ["0.02", "0.08"],
        detector.SETTINGS_B04,
    )
    expect(tail_far[0]["peak_action"] == "keep" and tail_far[1]["peak_action"] == "keep", "tail beyond 0.5 is not a neighbor")
    tied = direct_peak(
        [{"t": "0", "requests": [0]}, {"t": "0.5005", "requests": [1]}, {"t": "1.001", "requests": [2]}],
        ["0.05", "0.05"],
        detector.SETTINGS_B04,
    )
    expect(tied[0]["peak_action"] == "keep" and tied[1]["peak_action"] == "suppress", "grid tie keeps earlier")
    expect(tied[1]["suppression_refs"][0]["neighbor_basis"] == "grid_step", "tie basis is grid step")
    tied_b01 = direct_peak(
        [{"t": "0", "requests": [0]}, {"t": "0.5005", "requests": [1]}, {"t": "1.001", "requests": [2]}],
        ["0.05", "0.05"],
        detector.SETTINGS_B01,
    )
    expect(tied_b01[0]["peak_action"] == "keep" and tied_b01[1]["peak_action"] == "keep", "b01 tie stays outside 0.5")
    flash = direct_peak(
        [
            {"t": "1241.040", "requests": [0]},
            {"t": "1247.913", "requests": list(range(1, 14))},
            {"t": "1248.046", "requests": [14]},
        ],
        ["0.02", "0.08"],
        detector.SETTINGS_B04,
    )
    flash_b01 = direct_peak(
        [
            {"t": "1241.040", "requests": [0]},
            {"t": "1247.913", "requests": list(range(1, 14))},
            {"t": "1248.046", "requests": [14]},
        ],
        ["0.02", "0.08"],
        detector.SETTINGS_B01,
    )
    expect(flash[0]["peak_action"] == "suppress" and flash_b01[0]["peak_action"] == "suppress", "short card stays suppressed")
    expect(flash[0]["suppression_refs"][0]["neighbor_basis"] == "actual_time", "short card basis")
    b00_far = detector.analyze_frames(
        [
            _frame(0, bytes([0]) * 4, denominator=10000),
            _frame(5005, bytes([255]) * 4, denominator=10000),
            _frame(10010, bytes([0]) * 4, denominator=10000),
        ],
        start=Decimal(0), end=Decimal("1.6"), stream_start=Fraction(0),
    )
    expect([item["peak_action"] for item in b00_far["differences"]] == ["keep", "keep"], "b00 0.5005 does not suppress")
    damaged_b04 = detector.loads(detector.dumps(_collapsed_b04["differences"]))
    damaged_b04[1]["peak_action"] = "keep"
    try:
        detector.replay_boundaries(
            damaged_b04, detector.loads(detector.dumps(_collapsed_b04["samples"])), Decimal(0), Decimal(8),
            config_id="B04", logic_version=detector.LOGIC_VERSION_B04, schema_version=detector.SCHEMA_VERSION_B04,
            settings=detector.SETTINGS_B04,
        )
        expect(False, "b04 replay mismatch")
    except detector.T3Error as exc:
        expect("재생 판정" in str(exc), "b04 replay mismatch message")

    expect("grid_suppress_rule" not in detector.SETTINGS_B04, "b04 support rule absent")
    expect(detector.SETTINGS_B05["grid_suppress_rule"] == "kept_neighbor_passing_tile_subset", "b05 support rule")
    expect(detector.SETTINGS_B05["peak_neighbor"] == "one_grid_request_step", "b05 neighbor stays")
    expect(detector.SCHEMA_VERSION_B05 == "t3.screen_changes.v6", "b05 schema")

    def valued_peak(specs: list[dict[str, Any]], tile_rows: list[list[str]], settings: dict[str, Any]) -> list[dict[str, Any]]:
        width = 80 * len(tile_rows[0])
        samples = []
        for index, spec in enumerate(specs):
            samples.append({
                "sample_index": index,
                "video_seconds": Decimal(spec["t"]),
                "request_indices": spec["requests"],
                "roles": spec.get("roles", ["grid"]),
                "width": width,
                "height": 90,
            })
        layout = detector.tile_layout(width, 90, 80, 90)
        rows = []
        for index, values in enumerate(tile_rows, start=1):
            decimals = [Decimal(value) for value in values]
            rows.append({
                "index": index,
                "before_sample": index - 1,
                "after_sample": index,
                "elapsed_video_seconds": samples[index]["video_seconds"] - samples[index - 1]["video_seconds"],
                "global_mean": max(decimals),
                "t": samples[index]["video_seconds"],
                "tile_values": decimals,
            })
        return detector._finish_tile_rows(rows, layout, samples, settings)

    step_specs = [
        {"t": "0", "requests": [0]},
        {"t": "0.5005", "requests": [1]},
        {"t": "1.001", "requests": [2]},
    ]
    covered = valued_peak(step_specs, [["0.04", "0"], ["0.20", "0.01"]], detector.SETTINGS_B05)
    expect(covered[0]["peak_action"] == "suppress" and covered[0]["suppression_refs"][0]["support"] == "subset", "covered tile suppresses")
    expect(covered[1]["peak_action"] == "keep" and covered[1]["grid_support_reviews"] == [], "covering neighbor stays")
    exclusive = valued_peak(step_specs, [["0.04", "0.09"], ["0.20", "0"]], detector.SETTINGS_B05)
    expect(exclusive[0]["peak_action"] == "keep", "exclusive tile stays")
    expect(exclusive[0]["grid_support_reviews"][0]["unused_reason"] == "support_not_covered", "exclusive tile reason")
    expect(exclusive[0]["suppression_refs"] == [], "exclusive tile has no suppressor")
    chain_specs = step_specs + [{"t": "1.5015", "requests": [3]}]
    chain = valued_peak(
        chain_specs,
        [["0", "0.05", "0.04"], ["0", "0.08", "0"], ["0.30", "0.10", "0"]],
        detector.SETTINGS_B05,
    )
    expect(chain[0]["peak_action"] == "keep" and chain[1]["peak_action"] == "suppress" and chain[2]["peak_action"] == "keep", "exclusive chain keeps the first")
    expect(chain[0]["grid_support_reviews"][0]["unused_reason"] == "neighbor_not_kept", "suppressed middle is not evidence")
    expect(chain[1]["suppression_refs"][0]["support"] == "subset", "middle is covered by the last")
    transitive = valued_peak(
        chain_specs,
        [["0", "0.04", "0"], ["0", "0.08", "0"], ["0.30", "0.10", "0"]],
        detector.SETTINGS_B05,
    )
    expect(transitive[0]["peak_action"] == "keep" and transitive[1]["peak_action"] == "suppress", "subset does not pass through a removed neighbor")
    expect(transitive[0]["grid_support_reviews"][0]["support"] == "subset" and transitive[0]["grid_support_reviews"][0]["neighbor_kept"] is False, "covered removed neighbor stays unused")
    tie_cover = valued_peak(step_specs, [["0.05", "0.02"], ["0.05", "0"]], detector.SETTINGS_B05)
    expect(tie_cover[0]["peak_action"] == "keep" and tie_cover[1]["peak_action"] == "suppress", "tie suppresses the later covered diff")
    expect(tie_cover[1]["suppression_refs"][0]["neighbor_basis"] == "grid_step", "tie basis stays grid step")
    tie_open = valued_peak(step_specs, [["0.05", "0"], ["0.05", "0.04"]], detector.SETTINGS_B05)
    expect(tie_open[0]["peak_action"] == "keep" and tie_open[1]["peak_action"] == "keep", "tie keeps an uncovered later diff")
    b05_hole = valued_peak(
        [{"t": "0", "requests": [0]}, {"t": "0.5005", "requests": [1]}, {"t": "1.001", "requests": [3]}],
        [["0.02", "0"], ["0.08", "0"]],
        detector.SETTINGS_B05,
    )
    expect(b05_hole[0]["peak_action"] == "keep" and b05_hole[1]["peak_action"] == "keep", "b05 unavailable hole is not a neighbor")
    b05_tail_close = valued_peak(
        [
            {"t": "0", "requests": [0]},
            {"t": "0.5005", "requests": [1]},
            {"t": "0.9005", "requests": [2], "roles": ["tail"]},
        ],
        [["0.02", "0"], ["0.08", "0"]],
        detector.SETTINGS_B05,
    )
    expect(
        b05_tail_close[0]["peak_action"] == "suppress" and b05_tail_close[0]["suppression_refs"][0]["neighbor_basis"] == "actual_time",
        "b05 tail within 0.5 uses time",
    )
    expect(b05_tail_close[0]["grid_support_reviews"] == [], "b05 tail time has no grid review")
    b05_tail_far = valued_peak(
        [
            {"t": "0", "requests": [0]},
            {"t": "0.5005", "requests": [1]},
            {"t": "1.1005", "requests": [2], "roles": ["tail"]},
        ],
        [["0.02", "0"], ["0.08", "0"]],
        detector.SETTINGS_B05,
    )
    expect(b05_tail_far[0]["peak_action"] == "keep" and b05_tail_far[1]["peak_action"] == "keep", "b05 tail beyond 0.5 is not a neighbor")
    b05_two = valued_peak(
        chain_specs,
        [["0.20", "0"], ["0.04", "0"], ["0.80", "0"]],
        detector.SETTINGS_B05,
    )
    expect(b05_two[0]["peak_action"] == "keep" and b05_two[2]["peak_action"] == "keep" and b05_two[1]["peak_action"] == "suppress", "b05 two steps stay apart")
    b05_flash = valued_peak(
        [
            {"t": "1241.040", "requests": [0]},
            {"t": "1247.913", "requests": list(range(1, 14))},
            {"t": "1248.046", "requests": [14]},
        ],
        [["0.02", "0.04"], ["0.08", "0"]],
        detector.SETTINGS_B05,
    )
    expect(b05_flash[0]["peak_action"] == "suppress" and b05_flash[0]["suppression_refs"][0]["neighbor_basis"] == "actual_time", "b05 short card stays actual time")
    expect(b05_flash[0]["grid_support_reviews"] == [], "b05 short card ignores the extra tile")
    support_doc = detector.analyze_frames(
        [counted_frame(0, 0, 10000), counted_frame(5005, 80, 10000), counted_frame(10010, 800, 10000)],
        start=Decimal(0), end=Decimal("1.6"), stream_start=Fraction(0), settings=detector.SETTINGS_B05,
    )
    replayed_doc, _replayed_segments = detector.replay_boundaries(
        detector.loads(detector.dumps(support_doc["differences"])),
        detector.loads(detector.dumps(support_doc["samples"])),
        Decimal(0), Decimal("1.6"),
        config_id="B05", logic_version=detector.LOGIC_VERSION_B05, schema_version=detector.SCHEMA_VERSION_B05,
        settings=detector.SETTINGS_B05,
    )
    expect(
        [item["video_seconds"] for item in replayed_doc] == [item["video_seconds"] for item in support_doc["boundaries"]],
        "b05 counted replay",
    )
    damaged_support = detector.loads(detector.dumps(support_doc["differences"]))
    damaged_support[0]["grid_support_reviews"] = [{"difference_index": 99, "used": False}]
    try:
        detector.replay_boundaries(
            damaged_support, detector.loads(detector.dumps(support_doc["samples"])), Decimal(0), Decimal("1.6"),
            config_id="B05", logic_version=detector.LOGIC_VERSION_B05, schema_version=detector.SCHEMA_VERSION_B05,
            settings=detector.SETTINGS_B05,
        )
        expect(False, "b05 replay mismatch")
    except detector.T3Error as exc:
        expect("재생 판정" in str(exc), "b05 replay mismatch message")

    expect(detector.SCHEMA_VERSION_B06 == "t3.screen_changes.v7", "b06 schema")
    expect(detector.SETTINGS_B06["grid_suppress_rule"] == detector.SETTINGS_B05["grid_suppress_rule"], "b06 keeps the b05 rule")
    expect(detector.SETTINGS_B06["residual_gate"] == 32, "b06 gate")

    def paint_frames(
        paints: list[Any],
        width: int,
        height: int,
        denominator: int = 2,
        background: int = 100,
        pts: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        frames = []
        for index, paint in enumerate(paints):
            raw = bytearray([background]) * (width * height)
            paint(raw, width)
            frames.append(_frame(index if pts is None else pts[index], bytes(raw), denominator=denominator, width=width, height=height))
        return frames

    def run_b06(frames: list[dict[str, Any]], end: str) -> dict[str, Any]:
        return detector.analyze_frames(
            frames,
            start=Decimal(0),
            end=Decimal(end),
            stream_start=Fraction(0),
            settings=detector.SETTINGS_B06,
        )

    def diff_at(document: dict[str, Any], seconds: str) -> dict[str, Any]:
        sample_index = next(
            int(item["sample_index"])
            for item in document["samples"]
            if item["video_seconds"] == Decimal(seconds)
        )
        return next(item for item in document["differences"] if int(item["after_sample"]) == sample_index)

    def boundary_at(document: dict[str, Any], seconds: str) -> dict[str, Any]:
        return next(item for item in document["boundaries"] if item["video_seconds"] == Decimal(seconds))

    def blank(raw: bytearray, width: int) -> None:
        del raw, width

    board_w, board_h = 160, 90

    def stroke_column(raw: bytearray, width: int) -> None:
        for y in range(board_h):
            raw[y * width + 10] = 140

    def stroke_and_other(raw: bytearray, width: int) -> None:
        stroke_column(raw, width)
        for y in range(board_h):
            row = y * width
            for x in range(80, board_w):
                raw[row + x] = 0

    thin = run_b06(paint_frames([blank, stroke_column, stroke_and_other, stroke_and_other], board_w, board_h), "2")
    thin_first = diff_at(thin, "0.5")
    expect(thin_first["peak_action"] == "below_threshold", "1px stroke stays below the tile floor")
    expect(thin_first["content_action"] == "birth" and thin_first["protected"] is True, "1px stroke births at its first sample")
    expect(boundary_at(thin, "0.5")["origin"] == "content", "1px content boundary remains beside a later peak")
    expect(thin_first["confirm_sample_index"] == 2, "content time is the first sample")
    expect(boundary_at(thin, "1")["origin"] == "b05_keep_with_content", "later peak keeps its own content boundary")

    def passing_block(raw: bytearray, width: int) -> None:
        for y in range(60):
            row = y * width
            for x in range(4):
                raw[row + x] = 140

    def passing_block_covered(raw: bytearray, width: int) -> None:
        passing_block(raw, width)
        for y in range(board_h):
            row = y * width
            for x in range(10, board_w):
                raw[row + x] = 200

    covered = run_b06(
        paint_frames(
            [blank, passing_block, passing_block_covered, passing_block_covered],
            board_w,
            board_h,
            denominator=10000,
            pts=[0, 5005, 10010, 15015],
        ),
        "2",
    )
    covered_first = diff_at(covered, "0.5005")
    covered_ref = (covered_first["suppression_refs"] or [None])[0] or {}
    expect(covered_first["peak_action"] == "suppress", "passing stroke is subset-suppressed")
    expect(covered_ref.get("support") == "subset" and covered_ref.get("neighbor_basis") == "grid_step", "suppression record stays subset")
    expect(covered_first["protected"] is True and boundary_at(covered, "0.5005")["origin"] == "content", "subset suppression does not remove content")

    def put_points(raw: bytearray, width: int, points: list[tuple[int, int]]) -> None:
        for x, y in points:
            raw[y * width + x] = 140

    growth_points = [
        [],
        [(2, 2)],
        [(2, 2), (3, 2)],
        [(2, 2), (3, 2), (4, 2)],
        [(2, 2), (3, 2), (4, 2), (2, 7)],
        [(2, 2), (3, 2), (4, 2), (2, 7)],
    ]
    grown = run_b06(
        paint_frames(
            [lambda raw, width, points=item: put_points(raw, width, points) for item in growth_points],
            32,
            8,
        ),
        "3",
    )
    expect(
        diff_at(grown, "0.5")["content_action"] == "unresolved_contact" and diff_at(grown, "0.5")["content_episode_ids"] == [0],
        "first stroke stays one episode while the next pixel is still moving",
    )
    expect(
        diff_at(grown, "1")["content_action"] == "unresolved_contact" and diff_at(grown, "1.5")["content_action"] == "extend",
        "connected growth extends the episode",
    )
    expect(diff_at(grown, "1")["content_episode_ids"] == [0] and diff_at(grown, "1.5")["content_episode_ids"] == [0], "connected growth keeps the episode")
    expect(diff_at(grown, "2")["content_action"] == "birth" and diff_at(grown, "2")["content_episode_ids"] == [1], "a stroke four pixels away is another episode")
    expect(
        [item["video_seconds"] for item in grown["boundaries"]] == [Decimal("0.5"), Decimal("1"), Decimal("1.5"), Decimal("2")],
        "each newly stable sample is a boundary",
    )

    def blob_at(raw: bytearray, width: int, x0: int) -> None:
        for y in range(11):
            row = y * width
            for x in range(10):
                raw[row + x0 + x] = 140

    moved = run_b06(
        paint_frames(
            [blank, *[lambda raw, width, x0=x0: blob_at(raw, width, x0) for x0 in (0, 20, 40, 60)]],
            board_w,
            board_h,
        ),
        "2.5",
    )
    expect(moved["boundaries"] == [], "a blob that moves every sample has no segment boundary")
    expect(all(row["content_action"] == "short_unconfirmed" for row in moved["differences"]), "moving blob is only stored as unconfirmed")

    def spec_paint(spec: list[tuple[int, int]]) -> Any:
        def paint(raw: bytearray, width: int, spec: list[tuple[int, int]] = spec) -> None:
            for x, value in spec:
                raw[2 * width + x] = value
        return paint

    occluded = run_b06(
        paint_frames(
            [
                spec_paint([]),
                spec_paint([(5, 180)]),
                spec_paint([(5, 180)]),
                spec_paint([(5, 40), (6, 40), (7, 40)]),
                spec_paint([(5, 180)]),
                spec_paint([(5, 180), (12, 180)]),
                spec_paint([(5, 180), (12, 180)]),
            ],
            32,
            8,
        ),
        "3.5",
    )
    expect(diff_at(occluded, "0.5")["content_action"] == "birth", "stroke births before occlusion")
    expect(diff_at(occluded, "1.5")["content_action"] == "unobservable", "a cover is unobservable")
    expect(diff_at(occluded, "2")["content_action"] == "none", "the same ink after the cover is not a new boundary")
    expect(diff_at(occluded, "2.5")["content_action"] == "birth" and diff_at(occluded, "2.5")["content_new_pixel_count"] == 1, "only the new stable pixel is a boundary")
    expect(
        [item["video_seconds"] for item in occluded["boundaries"]] == [Decimal("0.5"), Decimal("2.5")],
        "occlusion does not add a boundary",
    )

    def fill_value(value: int) -> Any:
        def paint(raw: bytearray, width: int, value: int = value) -> None:
            del width
            for index in range(len(raw)):
                raw[index] = value
        return paint

    bright = run_b06(paint_frames([fill_value(100), fill_value(120), fill_value(120), fill_value(100)], 16, 2), "2")
    expect(all(row["protected"] is False for row in bright["differences"]), "exposure adds no content boundary")
    expect(all(item["origin"] == "b05_keep" for item in bright["boundaries"]), "brightness can remain a b05 boundary")
    expect(diff_at(bright, "0.5")["content_action"] == "exposure" and diff_at(bright, "0.5")["registration"] is None, "plus 20 is exposure")
    expect(diff_at(bright, "1")["reference_sha256"] == bright["samples"][0]["analysis_bytes_sha256"], "exposure does not replace the reference")

    def edge(raw: bytearray, width: int, shift: int) -> None:
        for y in range(8):
            row = y * width
            for x in range(width):
                source = x - shift
                raw[row + x] = 200 if 8 <= source <= 9 else 40

    shifted = run_b06(
        paint_frames(
            [
                lambda raw, width: edge(raw, width, 0),
                lambda raw, width: edge(raw, width, 2),
                lambda raw, width: edge(raw, width, 2),
            ],
            32,
            8,
        ),
        "1.5",
    )
    shift_reg = diff_at(shifted, "0.5")["registration"]
    expect(shift_reg["dx"] == 2 and shift_reg["dy"] == 0 and shift_reg["residual_pixel_count"] == 0, "2px registration explains the edge")
    expect(all(row["protected"] is False for row in shifted["differences"]), "explained shift adds no content boundary")

    brief = run_b06(
        paint_frames(
            [spec_paint([]), spec_paint([(4, 140)]), spec_paint([]), spec_paint([(8, 140)])],
            32,
            8,
        ),
        "2",
    )
    expect(brief["boundaries"] == [], "one-sample and tail strokes are not segment boundaries")
    expect(
        [row["content_action"] for row in brief["differences"]] == ["short_unconfirmed", "none", "short_unconfirmed"],
        "one-sample and tail strokes stay in the record",
    )

    shared_raw = bytearray([100]) * 64
    shared_raw[0] = 140
    shared = detector.analyze_frames(
        [
            _frame(0, bytes([100]) * 64, denominator=2, width=32, height=2),
            _frame(2, bytes(shared_raw), denominator=2, width=32, height=2),
        ],
        start=Decimal(0),
        end=Decimal("1.6"),
        stream_start=Fraction(0),
        settings=detector.SETTINGS_B06,
    )
    expect(len(shared["samples"]) == 2 and len(shared["differences"]) == 1, "shared PTS updates content once")
    expect(len(shared["samples"][1]["request_indices"]) >= 2, "two grid requests share one sample")
    expect(shared["differences"][0]["content_action"] == "short_unconfirmed" and shared["boundaries"] == [], "tail-only shared sample is not a boundary")

    gap_raw = bytearray([100]) * 64
    gap_raw[0] = 140
    gap = detector.analyze_frames(
        [
            _frame(0, bytes([100]) * 64, denominator=10, width=32, height=2),
            _frame(12, bytes(gap_raw), denominator=10, width=32, height=2),
        ],
        start=Decimal(0),
        end=Decimal(2),
        stream_start=Fraction(0),
        settings=detector.SETTINGS_B06,
    )
    expect(gap["differences"][0]["content_action"] == "short_unconfirmed" and gap["boundaries"] == [], "a gap over 1s does not confirm")

    def neighbor_moves(raw: bytearray, width: int, neighbor: int) -> None:
        raw[2 * width + 4] = 140
        raw[2 * width + 5] = neighbor

    touching = run_b06(
        paint_frames(
            [
                blank,
                lambda raw, width: neighbor_moves(raw, width, 200),
                lambda raw, width: neighbor_moves(raw, width, 80),
                lambda raw, width: neighbor_moves(raw, width, 80),
            ],
            32,
            8,
        ),
        "2",
    )
    expect(diff_at(touching, "0.5")["content_action"] == "unresolved_contact" and diff_at(touching, "0.5")["protected"] is True, "a stroke touching motion stays protected")
    expect(boundary_at(touching, "0.5")["origin"] == "content", "contact does not drop the content boundary")

    replayed, _replay_segments = detector.replay_boundaries(
        detector.loads(detector.dumps(thin["differences"])),
        detector.loads(detector.dumps(thin["samples"])),
        Decimal(0),
        Decimal(2),
        config_id="B06",
        logic_version=detector.LOGIC_VERSION_B06,
        schema_version=detector.SCHEMA_VERSION_B06,
        settings=detector.SETTINGS_B06,
    )
    expect(
        [(item["video_seconds"], item["origin"]) for item in replayed]
        == [(item["video_seconds"], item["origin"]) for item in thin["boundaries"]],
        "b06 replay matches stored boundaries",
    )
    damaged_peak = detector.loads(detector.dumps(thin["differences"]))
    damaged_peak[0]["peak_action"] = "keep"
    try:
        detector.replay_boundaries(
            damaged_peak,
            detector.loads(detector.dumps(thin["samples"])),
            Decimal(0),
            Decimal(2),
            config_id="B06",
            logic_version=detector.LOGIC_VERSION_B06,
            schema_version=detector.SCHEMA_VERSION_B06,
            settings=detector.SETTINGS_B06,
        )
        expect(False, "b06 peak replay mismatch")
    except detector.T3Error as exc:
        expect("B05" in str(exc), "b06 recomputes the b05 decision")
    damaged_content = detector.loads(detector.dumps(thin["differences"]))
    damaged_content[0]["protected"] = False
    try:
        detector.replay_boundaries(
            damaged_content,
            detector.loads(detector.dumps(thin["samples"])),
            Decimal(0),
            Decimal(2),
            config_id="B06",
            logic_version=detector.LOGIC_VERSION_B06,
            schema_version=detector.SCHEMA_VERSION_B06,
            settings=detector.SETTINGS_B06,
        )
        expect(False, "b06 content replay mismatch")
    except detector.T3Error as exc:
        expect("보호" in str(exc), "b06 checks stored content fields")

    if failures:
        for failure in failures:
            print(f"check failed: {failure}", flush=True)
        return 1
    print("check ok", flush=True)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="T3 화면 변화 탐색")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--subset", nargs="?", const=True, default=None)
    parser.add_argument("--config", default="B00")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    subset_mode = args.subset is True
    subset_text = args.subset if isinstance(args.subset, str) else None
    if subset_text is not None and not args.plan:
        print("원인: subset 후보 목록은 --plan과 함께 씁니다.\n파일: tests/T3/run_t3.py\n대상: cli", flush=True)
        return 2
    modes = [args.check, args.plan, args.pilot, args.resume, args.render_only, subset_mode]
    if sum(bool(mode) for mode in modes) != 1:
        print("원인: 실행 모드를 하나만 지정해야 합니다.\n파일: tests/T3/run_t3.py\n대상: cli", flush=True)
        return 2
    if args.plan and args.run_id:
        print("원인: --plan과 --run-id는 함께 쓰지 않습니다.\n파일: tests/T3/run_t3.py\n대상: cli", flush=True)
        return 2
    subset_configs = {
        detector.CONFIG_ID_B01,
        detector.CONFIG_ID_B02,
        detector.CONFIG_ID_B03,
        detector.CONFIG_ID_B04,
        detector.CONFIG_ID_B05,
        detector.CONFIG_ID_B06,
    }
    if args.config in subset_configs and args.plan and not subset_text:
        print("원인: B01·B02·B03·B04·B05·B06 plan은 subset 후보 목록이 필요합니다.\n파일: tests/T3/run_t3.py\n대상: cli", flush=True)
        return 2
    if args.config in subset_configs and (args.resume or args.pilot):
        print("원인: B01·B02·B03·B04·B05·B06는 --subset으로만 실행합니다.\n파일: tests/T3/run_t3.py\n대상: cli", flush=True)
        return 2
    if args.check:
        return command_check()
    if args.plan:
        subset_ids = None
        if subset_text:
            subset_ids = [item.strip() for item in subset_text.split(",") if item.strip()]
            if len(subset_ids) != len(set(subset_ids)):
                print("원인: subset 후보가 중복되었습니다.\n파일: tests/T3/run_t3.py\n대상: cli", flush=True)
                return 2
        return command_plan(config_id=args.config, subset_ids=subset_ids)
    if not args.run_id:
        print("원인: run_id가 필요합니다.\n파일: tests/T3/run_t3.py\n대상: cli", flush=True)
        return 2
    run_dir = RUNS_ROOT / args.run_id
    if not (run_dir / "manifest.json").exists():
        print_problem("run이 없습니다.", str(run_dir), args.run_id)
        return 1
    if args.render_only:
        return command_render_only(run_dir)
    return process_run(run_dir, pilot_only=args.pilot, subset_only=subset_mode)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
