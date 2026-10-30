"""Load the fixed 25-frame manifest from the accepted T4 handoff."""

from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


from pathlib import Path
from typing import Any

from common import ROOT, HashCache, decimal_text, read_json, rel, video_seconds, load_png_info

png_info = load_png_info()
INPUT_RUN = resolve_experiment_path(ROOT / "tests" / "T4" / "runs" / "20261025T154421Z_9076fc")
EXPECTED_SHA256 = {
    "acceptance.json": "d1a9c34f50ca5e1b9b558f0b891fc3cb072188e0fb2e128a8babb23fc05d5e4f",
    "t5_handoff.json": "f50689ec11871480ab3c1fa8ac92b997161488d5999c7ca2122743e09b5cb4ed",
    "quality_review.json": "e2f2570654770bf0618f21537d7e6ed87b241e5834e36031c51d67e5d4ffc796",
    "review_retained_uncertain.json": "9a68e51109332008ce6a505241acbdbcb265b6e20d10c3fa41a7c5cd40931842",
}
DEV_TABLE = [
    ("7EJ-xotXiSE:u0:c0", 138138),
    ("7EJ-xotXiSE:u15:c0", 14856842),
    ("CvuE-jyg-N8:u43:c0", 56512456),
    ("Fw-8hZhnH3Q:u0:c0", 798720),
    ("H2WN130OzLg:u25:c0", 19966608),
    ("H2WN130OzLg:u7:c0", 10040976),
    ("bjDkfjSmXVo:u12:c0", 28694666),
    ("hGVL9XRU9IQ:u26:c0", 33276243),
    ("iEMuJiVY9Oc:u12:c0", 10582528),
    ("iEMuJiVY9Oc:u20:c0", 19125248),
    ("iEMuJiVY9Oc:u4:c0", 3030528),
    ("iVelddb1qQ4:u17:c0", 35076041),
]
FIRST_TWO = [
    ("H2WN130OzLg:u25:c0", 19966608),
    ("iEMuJiVY9Oc:u4:c0", 3030528),
]
UNCERTAIN = [
    ("7EJ-xotXiSE:u15:c0", 14811797),
    ("iVelddb1qQ4:u17:c0", 33276243),
]
CONFIRM = [
    ("7EJ-xotXiSE:u0:c0", 437437),
    ("7EJ-xotXiSE:u15:c0", 15726711),
    ("CvuE-jyg-N8:u43:c0", 56721665),
    ("Fw-8hZhnH3Q:u0:c0", 3223040),
    ("H2WN130OzLg:u7:c0", 10064992),
    ("bjDkfjSmXVo:u12:c0", 29444415),
    ("hGVL9XRU9IQ:u26:c0", 31010980),
    ("iEMuJiVY9Oc:u12:c0", 10475008),
    ("iEMuJiVY9Oc:u20:c0", 19363328),
    ("iEMuJiVY9Oc:u4:c0", 3007488),
    ("iVelddb1qQ4:u17:c0", 36876840),
]


def ordered_samples() -> list[dict[str, Any]]:
    dev = FIRST_TWO + [item for item in DEV_TABLE if item not in FIRST_TWO] + UNCERTAIN
    rows = []
    for order, (candidate_id, pts) in enumerate(dev):
        group = "initial_two" if (candidate_id, pts) in FIRST_TWO else "dev_remainder"
        rows.append({"split": "dev", "request_group": group, "order": order, "candidate_id": candidate_id, "pts": pts})
    for offset, (candidate_id, pts) in enumerate(CONFIRM):
        rows.append({
            "split": "confirm",
            "request_group": "confirm",
            "order": len(dev) + offset,
            "candidate_id": candidate_id,
            "pts": pts,
        })
    return rows


def _frame_id(candidate_id: str, pts: int) -> str:
    return f"{candidate_id.split(':', 1)[0]}:s0:pts{pts}"


def _check_image(cache: HashCache, image: dict[str, Any], label: str, frame_id: str) -> list[str]:
    problems = []
    path = resolve_experiment_path(ROOT / str(image.get("path") or ""))
    if not path.is_file():
        return [f"{frame_id} {label} 파일이 없습니다."]
    data = path.read_bytes()
    if cache.file(path) != image.get("sha256"):
        problems.append(f"{frame_id} {label} 해시가 기록과 다릅니다.")
    if len(data) != image.get("bytes"):
        problems.append(f"{frame_id} {label} 크기가 기록과 다릅니다.")
    try:
        info = png_info(data)
    except Exception as exc:
        return problems + [f"{frame_id} {label} PNG가 아닙니다: {exc}"]
    if info["width"] != image.get("width") or info["height"] != image.get("height"):
        problems.append(f"{frame_id} {label} IHDR이 기록과 다릅니다.")
    return problems


def t1_source_path(ref: str) -> Path:
    """Read historical T1 references from the current location without a symlink."""
    legacy = "tests/T1_sweep/"
    if ref.startswith(legacy):
        ref = "tests/T1/sweep/" + ref[len(legacy):]
    return resolve_experiment_path(ROOT / ref)


def load_manifest(cache: HashCache | None = None) -> tuple[list[str], dict[str, Any] | None]:
    cache = cache or HashCache()
    problems: list[str] = []
    hashes = {}
    for name, expected in EXPECTED_SHA256.items():
        path = INPUT_RUN / name
        actual = cache.file(path)
        hashes[name] = actual
        if actual != expected:
            problems.append(f"{name} 해시가 명세와 다릅니다.")
    if problems:
        return problems, None
    acceptance = read_json(INPUT_RUN / "acceptance.json")
    handoff = read_json(INPUT_RUN / "t5_handoff.json")
    quality = read_json(INPUT_RUN / "quality_review.json")
    retained = read_json(INPUT_RUN / "review_retained_uncertain.json")
    if acceptance.get("accepted") is not True:
        problems.append("acceptance.accepted가 true가 아닙니다.")
    retained_ids = ["7EJ-xotXiSE:s0:pts14811797", "iVelddb1qQ4:s0:pts33276243"]
    if acceptance.get("review_retained_uncertain_ids") != retained_ids or quality.get("review_retained_uncertain_ids") != retained_ids:
        problems.append("미확정 2장의 ID가 채택 기록과 다릅니다.")
    if handoff.get("retained_total") != 2130 or quality.get("handoff_total") != 2130:
        problems.append("인계 장수가 2130이 아닙니다.")
    if handoff.get("t5_inference_executed") is not False:
        problems.append("T4 인계의 T5 추론 플래그가 false가 아닙니다.")
    frames = handoff.get("frames")
    sources = {item.get("candidate_id"): item for item in handoff.get("candidate_sources") or []}
    if not isinstance(frames, list) or len(frames) != 2130 or len({item.get("frame_id") for item in frames}) != 2130:
        problems.append("인계 프레임 2130개의 ID가 고유하지 않습니다.")
        return problems, None
    by_id = {item["frame_id"]: item for item in frames}
    h2_count = sum(1 for item in frames if item.get("candidate_id") == "H2WN130OzLg:u25:c0")
    if h2_count != 1:
        problems.append("H2 u25 인계가 1장이 아닙니다.")
    rows = ordered_samples()
    if len(rows) != 25 or len({_frame_id(row["candidate_id"], row["pts"]) for row in rows}) != 25:
        problems.append("대표 25장 목록이 고유하지 않습니다.")
    loaded = []
    verified_refs: set[str] = set()
    for row in rows:
        frame_id = _frame_id(row["candidate_id"], row["pts"])
        frame = by_id.get(frame_id)
        source = sources.get(row["candidate_id"])
        if frame is None or source is None:
            problems.append(f"{frame_id}이 인계에 없습니다.")
            continue
        if frame.get("candidate_id") != row["candidate_id"] or frame.get("stream_index") != 0 or frame.get("pts") != row["pts"]:
            problems.append(f"{frame_id}의 후보나 PTS가 다릅니다.")
        time_base = frame.get("time_base") or {}
        numerator = time_base.get("numerator")
        denominator = time_base.get("denominator")
        if isinstance(numerator, bool) or isinstance(denominator, bool) or not isinstance(numerator, int) or not isinstance(denominator, int):
            problems.append(f"{frame_id} time_base가 정수가 아닙니다.")
            continue
        try:
            computed = video_seconds(row["pts"], numerator, denominator)
        except (ValueError, ArithmeticError):
            problems.append(f"{frame_id} 시각을 계산하지 못했습니다.")
            continue
        if decimal_text(frame.get("video_seconds")) != computed and format(__import__("decimal").Decimal(str(frame.get("video_seconds"))).quantize(__import__("decimal").Decimal("0.000000001")), "f") != computed:
            problems.append(f"{frame_id} 기록 시각이 PTS와 다릅니다.")
        full = frame.get("full") or {}
        vlm = frame.get("vlm") or {}
        if (full.get("width"), full.get("height"), vlm.get("width"), vlm.get("height")) != (1920, 1080, 1280, 720):
            problems.append(f"{frame_id} 이미지 크기가 1920x1080과 1280x720이 아닙니다.")
        problems.extend(_check_image(cache, full, "원본", frame_id))
        problems.extend(_check_image(cache, vlm, "VLM", frame_id))
        expected_retention = "review_retained_uncertain" if (row["candidate_id"], row["pts"]) in UNCERTAIN else "model_keep"
        expected_decision = "drop" if expected_retention == "review_retained_uncertain" else "keep"
        if frame.get("retention_category") != expected_retention or frame.get("model_decision") != expected_decision:
            problems.append(f"{frame_id}의 원 판정 구분이 명세와 다릅니다.")
        if frame_id not in {item.get("frame_id") for item in retained.get("items") or []} and expected_retention == "review_retained_uncertain":
            problems.append(f"{frame_id}이 미확정 보존 파일에 없습니다.")
        candidate = source.get("source_candidate") or {}
        if row["candidate_id"] == "H2WN130OzLg:u25:c0" and (
            candidate.get("model_inference_success") is not False or candidate.get("candidate_source") != "budget_guard"
        ):
            problems.append("H2 u25의 budget_guard 실패 출처가 유지되지 않았습니다.")
        media = source.get("media") or {}
        media_path = resolve_experiment_path(ROOT / str(media.get("path") or ""))
        if not media_path.is_file() or media_path.stat().st_size != media.get("size"):
            problems.append(f"{row['candidate_id']} 원본 영상 참조 크기가 다릅니다.")
        sentences = source.get("sentences") or []
        if not sentences or any(not isinstance(item.get("text"), str) or item.get("text") == "" for item in sentences):
            problems.append(f"{row['candidate_id']} 원문이 없습니다.")
        for label, key in (
            ("selected_frames", "selected_frames_ref"),
            ("t3", "source_t3_ref"),
            ("t2", "source_t2_ref"),
        ):
            ref = source.get(key)
            sha_key = {
                "selected_frames": "selected_frames_sha256",
                "t3": "source_t3_sha256",
                "t2": "source_t2_sha256",
            }[label]
            if not isinstance(ref, str) or ref in verified_refs:
                continue
            ref_path = resolve_experiment_path(ROOT / ref)
            if not ref_path.is_file() or cache.file(ref_path) != source.get(sha_key):
                problems.append(f"{row['candidate_id']} {label} 참조 해시가 다릅니다.")
            verified_refs.add(ref)
        text_ref = source.get("text_ref") or {}
        t1_ref = str(text_ref.get("t1_path") or "")
        t1_path = t1_source_path(t1_ref)
        if not t1_ref.startswith(("tests/T1_sweep/", "tests/T1/sweep/")) or not t1_path.is_file() or cache.file(t1_path) != text_ref.get("t1_sha256"):
            problems.append(f"{row['candidate_id']} T1 참조 경로나 해시가 다릅니다.")
        t3_path = resolve_experiment_path(ROOT / str(source.get("source_t3_ref") or ""))
        segment = None
        if t3_path.is_file():
            document = read_json(t3_path)
            matches = [item for item in document.get("segments") or [] if item.get("segment_index") == frame.get("segment_index")]
            if len(matches) != 1:
                problems.append(f"{frame_id} T3 구간이 하나가 아닙니다.")
            else:
                segment = {
                    "segment_index": frame.get("segment_index"),
                    "video_start": decimal_text(matches[0].get("video_start")),
                    "video_end": decimal_text(matches[0].get("video_end")),
                    "source_t3_ref": source.get("source_t3_ref"),
                }
        loaded.append({
            "frame_id": frame_id,
            "split": row["split"],
            "request_group": row["request_group"],
            "order": row["order"],
            "candidate_id": row["candidate_id"],
            "stream_index": 0,
            "pts": row["pts"],
            "time_base": {"numerator": numerator, "denominator": denominator},
            "video_seconds": computed,
            "segment": segment,
            "retention_category": frame.get("retention_category"),
            "model_decision": frame.get("model_decision"),
            "t4_reason": frame.get("reason"),
            "full": {"path": full.get("path"), "width": full.get("width"), "height": full.get("height"), "bytes": full.get("bytes"), "sha256": full.get("sha256")},
            "vlm": {"path": vlm.get("path"), "width": vlm.get("width"), "height": vlm.get("height"), "bytes": vlm.get("bytes"), "sha256": vlm.get("sha256")},
            "sentences": [
                {"sentence_index": item.get("sentence_index"), "text": item.get("text"), "audio_start": decimal_text(item.get("audio_start")), "audio_end": decimal_text(item.get("audio_end"))}
                for item in sentences
            ],
            "text_ref": text_ref,
            "media": {"path": media.get("path"), "recorded_sha256": media.get("recorded_sha256"), "size": media.get("size"), "bytes_rehashed": False},
            "source_candidate": {
                "candidate_source": candidate.get("candidate_source"),
                "policy_version": candidate.get("policy_version"),
                "model_inference_success": candidate.get("model_inference_success"),
            },
            "raw_call_ref": frame.get("raw_call_ref"),
            "technical_status": "pending",
            "review_status": "unreviewed",
            "adoption": None,
            "regions": [],
            "visual_status": None,
        })
    if len(loaded) != 25:
        problems.append("대표 25장을 모두 읽지 못했습니다.")
    dev_count = sum(1 for item in loaded if item["split"] == "dev")
    confirm_count = sum(1 for item in loaded if item["split"] == "confirm")
    if dev_count != 14 or confirm_count != 11:
        problems.append("개발 14장과 확인 11장으로 나뉘지 않았습니다.")
    document = {
        "input_sha256": hashes,
        "frames": loaded,
        "paths": {name: rel(INPUT_RUN / name) for name in EXPECTED_SHA256},
    }
    return problems, document if not problems else None
