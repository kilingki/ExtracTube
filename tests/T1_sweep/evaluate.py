"""슬롯 검증, 경계 비교, 검수 대기열, 후보 선정, 요약."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from contextlib import redirect_stdout
from pathlib import Path

import engine

SEED = 20261017
ALGORITHM = "sha256_rank_v1"


def canonical_hash(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = math.ceil(0.95 * len(ordered)) - 1
    return ordered[index]


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def jaccard(left: set[int], right: set[int]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def sample_boundaries(video_id: str, boundaries_final: list[int], sentence_count: int, seed: int = SEED) -> tuple[list[int], list[int]]:
    pool = sorted({index for index in boundaries_final if 0 < index < sentence_count})
    ranked = sorted(
        pool,
        key=lambda index: (hashlib.sha256(f"{seed}:{video_id}:{index}".encode("utf-8")).hexdigest(), index),
    )
    return pool, sorted(ranked[:5])


def choose_selected(eligible: list[dict]) -> tuple[str | None, str]:
    if not eligible:
        return None, "none"
    if any(row["experiment_id"] == "E01" for row in eligible):
        return "E01", "default"
    ranked = sorted(eligible, key=lambda row: (-row["jaccard_mean"], row["call_seconds_mean"], row["experiment_id"]))
    return ranked[0]["experiment_id"], "technical_fallback"


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def attempt_dir(campaign: Path, phase: str, experiment_id: str, repeat: str, attempt: int) -> Path:
    return campaign / "runs" / phase / experiment_id / repeat / f"attempt{attempt:02d}"


def _token_sum(calls: list[dict], field: str) -> int | str:
    total = 0
    for call in calls:
        usage = call.get("usage")
        if not isinstance(usage, dict) or field not in usage or usage[field] is None:
            return "unknown"
        value = usage[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "unknown"
        total += value
    return total


def _reasoning_sum(calls: list[dict]) -> int | str:
    total = 0
    for call in calls:
        usage = call.get("usage")
        if not isinstance(usage, dict):
            return "unknown"
        details = usage.get("completion_tokens_details")
        if not isinstance(details, dict) or "reasoning_tokens" not in details or details["reasoning_tokens"] is None:
            return "unknown"
        value = details["reasoning_tokens"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "unknown"
        total += value
    return total


def _unit_lengths(document: dict) -> tuple[list[float], list[float] | str]:
    sentences: list[float] = []
    seconds: list[float] = []
    missing_time = False
    for unit in document["units"]:
        sentences.append(float(unit["sentence_end"] - unit["sentence_start"]))
        start = unit.get("start_time")
        end = unit.get("end_time")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            missing_time = True
            continue
        seconds.append(float(end) - float(start))
    return sentences, "unknown" if missing_time else seconds


def _calls_of(run: dict, documents: list[dict]) -> tuple[list[dict], list[dict]]:
    preflight = []
    if isinstance(run.get("preflight"), dict):
        calls = run["preflight"].get("calls")
        if isinstance(calls, list):
            preflight = calls
    processing: list[dict] = []
    for document in documents:
        processing.extend(document.get("calls") or [])
    return preflight, processing


def document_metrics(document: dict) -> dict:
    verifications = document.get("verifications") or []
    keeps = sum(1 for item in verifications if item.get("action") == "keep")
    merges = sum(1 for item in verifications if item.get("action") == "merge")
    decisions = keeps + merges
    sentence_lengths, durations = _unit_lengths(document)
    duration_values = [] if durations == "unknown" else durations
    full = [item for item in verifications if isinstance(item.get("context"), dict) and item["context"].get("kind") == "full"]
    excerpt = [item for item in verifications if isinstance(item.get("context"), dict) and str(item["context"].get("kind", "")).startswith("window")]
    verify_sentences = 0
    for item in verifications:
        context = item.get("context") if isinstance(item.get("context"), dict) else {}
        left = int(context.get("left_end", 0)) - int(context.get("left_start", 0))
        right = int(context.get("right_end", 0)) - int(context.get("right_start", 0))
        verify_sentences += left + right
    chunks = document.get("chunks") or []
    sentence_count = document["input"]["sentence_count"]
    max_sentences = document["settings"]["chunk_max_sentences"]
    char_limited = 0
    overlaps = []
    for previous, current in zip(chunks, chunks[1:]):
        overlaps.append(previous["sentence_end"] - current["sentence_start"])
    for chunk in chunks:
        length = chunk["sentence_end"] - chunk["sentence_start"]
        if chunk["sentence_end"] < sentence_count and length < max_sentences:
            char_limited += 1
    calls = document.get("calls") or []
    finish = {}
    for call in calls:
        reason = call.get("finish_reason")
        key = "none" if reason is None else str(reason)
        finish[key] = finish.get(key, 0) + 1
    return {
        "proposal_count": len(document.get("proposals") or []),
        "boundaries_after_conflict": len(document.get("boundaries_after_conflict") or []),
        "final_units": len(document.get("units") or []),
        "final_boundaries": list(document.get("boundaries_final") or []),
        "after_conflict": list(document.get("boundaries_after_conflict") or []),
        "proposal_indexes": sorted({item["sentence_index"] for item in document.get("proposals") or []}),
        "keep_count": keeps,
        "merge_count": merges,
        "merge_ratio": None if decisions == 0 else merges / decisions,
        "unit_sentences_median": median(sentence_lengths),
        "unit_sentences_p95": percentile_95(sentence_lengths),
        "unit_sentences_max": max(sentence_lengths) if sentence_lengths else None,
        "unit_seconds_median": "unknown" if durations == "unknown" else median(duration_values),
        "unit_seconds_p95": "unknown" if durations == "unknown" else percentile_95(duration_values),
        "unit_seconds_max": "unknown" if durations == "unknown" else (max(duration_values) if duration_values else None),
        "single_sentence_units": sum(1 for value in sentence_lengths if value == 1),
        "units_over_40_sentences": sum(1 for value in sentence_lengths if value > 40),
        "verify_full_count": len(full),
        "verify_excerpt_count": len(excerpt),
        "verify_sentences": verify_sentences,
        "chunk_count": len(chunks),
        "overlap_values": overlaps,
        "overlap_mean": None if not overlaps else sum(overlaps) / len(overlaps),
        "char_limited_chunks": char_limited,
        "boundary_calls": sum(1 for call in calls if call.get("stage") == "boundary"),
        "conflict_calls": sum(1 for call in calls if call.get("stage") == "conflict"),
        "verify_calls": sum(1 for call in calls if call.get("stage") == "verify"),
        "error_calls": sum(1 for call in calls if call.get("error")),
        "retry_calls": sum(1 for call in calls if call.get("attempt", 1) > 1),
        "finish_reasons": finish,
        "prompt_tokens": _token_sum(calls, "prompt_tokens"),
        "completion_tokens": _token_sum(calls, "completion_tokens"),
        "reasoning_tokens": _reasoning_sum(calls),
        "call_seconds": engine.call_seconds(calls),
        "sentence_lengths": sentence_lengths,
        "durations": durations,
    }


def validate_attempt(attempt: Path, config: engine.ExperimentConfig, input_ids: list[str]) -> dict:
    problems: list[str] = []
    run = _load_json(attempt / "run.json")
    if run is None:
        return {"ok": False, "problems": ["run.json이 없습니다."], "replay_ok": False, "config_ok": False, "structure_ok": False}
    if run.get("settings") != config.public_dict():
        problems.append("저장 설정이 실효 설정과 다릅니다.")
    if run.get("input_ids") != input_ids:
        problems.append("입력 순서가 슬롯과 다릅니다.")
    videos, load_problems = engine.load_selected(engine.DATA_ROOT, input_ids)
    problems.extend(load_problems)
    documents = []
    structure_ok = True
    replay_ok = True
    if len(videos) == len(input_ids):
        with redirect_stdout(io.StringIO()):
            for video in videos:
                path = attempt / video["video_id"] / "semantic_units.json"
                document = _load_json(path)
                if document is None:
                    problems.append(f"{video['video_id']} 결과가 없습니다.")
                    structure_ok = False
                    replay_ok = False
                    continue
                documents.append(document)
                if document.get("status") != "ok" or not (document.get("validation") or {}).get("ok"):
                    problems.append(f"{video['video_id']} 상태가 ok가 아닙니다.")
                    structure_ok = False
                structure_problems = engine.validate_structure(video["sentences"], document.get("units") or [], document.get("boundaries_final") or [])
                if structure_problems:
                    structure_ok = False
                    problems.extend(structure_problems)
                review_path = attempt / video["video_id"] / "review.md"
                if not review_path.is_file() or review_path.read_text(encoding="utf-8") != engine.render_review(video, document):
                    problems.append(f"{video['video_id']} review.md가 결과와 다릅니다.")
                try:
                    produced = engine.replay_video(video, document, config)
                except engine.ReplayMismatch as exc:
                    replay_ok = False
                    problems.append(f"{video['video_id']} 재생 실패: {exc}")
                    continue
                difference = engine.first_diff(engine.semantic_view(produced), engine.semantic_view(document))
                if difference:
                    replay_ok = False
                    problems.append(f"{video['video_id']} 재생 차이: {difference}")
    else:
        structure_ok = False
        replay_ok = False
    if documents and (attempt / "index.md").read_text(encoding="utf-8") != engine.render_index(documents):
        problems.append("index.md가 결과와 다릅니다.")
    if not (attempt / "index.md").is_file():
        problems.append("index.md가 없습니다.")
    config_ok = run.get("settings") == config.public_dict() and run.get("input_ids") == input_ids
    ok = not problems and structure_ok and replay_ok and config_ok and len(documents) == len(input_ids)
    return {"ok": ok, "problems": problems, "replay_ok": replay_ok, "config_ok": config_ok, "structure_ok": structure_ok}


def _boundaries(document: dict, key: str) -> set[int]:
    return {index for index in document.get(key) or [] if isinstance(index, int)}


def _proposal_set(document: dict) -> set[int]:
    return {item["sentence_index"] for item in document.get("proposals") or []}


def compare_attempt(attempt: Path, baseline: Path | None, input_ids: list[str]) -> str:
    lines = ["# 기준 실험 비교", ""]
    if baseline is None or not (baseline / "run.json").is_file():
        lines.append("비교: unavailable")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"기준: {baseline}")
    lines.append("비교: available")
    lines.append("")
    for video_id in input_ids:
        current = _load_json(attempt / video_id / "semantic_units.json")
        base = _load_json(baseline / video_id / "semantic_units.json")
        if current is None or base is None:
            lines.append(f"## {video_id}")
            lines.append("")
            lines.append("비교: unavailable")
            lines.append("")
            continue
        final_added = sorted(_boundaries(current, "boundaries_final") - _boundaries(base, "boundaries_final"))
        final_removed = sorted(_boundaries(base, "boundaries_final") - _boundaries(current, "boundaries_final"))
        conflict_added = sorted(_boundaries(current, "boundaries_after_conflict") - _boundaries(base, "boundaries_after_conflict"))
        conflict_removed = sorted(_boundaries(base, "boundaries_after_conflict") - _boundaries(current, "boundaries_after_conflict"))
        proposal_added = sorted(_proposal_set(current) - _proposal_set(base))
        proposal_removed = sorted(_proposal_set(base) - _proposal_set(current))
        lines.append(f"## {video_id}")
        lines.append("")
        lines.append(f"- 최종 추가: {final_added}")
        lines.append(f"- 최종 제거: {final_removed}")
        lines.append(f"- 충돌 후 추가: {conflict_added}")
        lines.append(f"- 충돌 후 제거: {conflict_removed}")
        lines.append(f"- 초기 후보 추가: {proposal_added}")
        lines.append(f"- 초기 후보 제거: {proposal_removed}")
        if conflict_added or conflict_removed or proposal_added or proposal_removed:
            lines.append("- 상류 경계가 다르므로 이 차이를 VERIFY 단계만의 효과로 보지 않는다.")
        lines.append("")
    return "\n".join(lines)


def _group_for(video_id: str, plan: dict) -> str:
    if video_id in plan["inputs"]["search_ids"]:
        return "search"
    return "final_only"


def _blank_row() -> dict:
    return {
        "phase": "",
        "input_group": "",
        "experiment_id": "",
        "repeat": "",
        "attempt": "",
        "status": "",
        "config_hash": "",
        "engine_hash": "",
        "prompt_hash": "",
        "model_identity": "",
        "input_ids": "",
        "expected_sentences": "",
        "actual_sentences": "",
        "input_hash_set": "",
        "video_id": "",
        "proposal_count": "",
        "boundaries_after_conflict": "",
        "final_units": "",
        "keep_count": "",
        "merge_count": "",
        "merge_ratio": "",
        "unit_sentences_median": "",
        "unit_sentences_p95": "",
        "unit_sentences_max": "",
        "unit_seconds_median": "",
        "unit_seconds_p95": "",
        "unit_seconds_max": "",
        "single_sentence_units": "",
        "units_over_40_sentences": "",
        "verify_full_count": "",
        "verify_excerpt_count": "",
        "verify_sentences": "",
        "verify_tokens": "",
        "chunk_count": "",
        "overlap_mean": "",
        "char_limited_chunks": "",
        "boundary_calls": "",
        "conflict_calls": "",
        "verify_calls": "",
        "error_calls": "",
        "retry_calls": "",
        "finish_reasons": "",
        "prompt_tokens": "",
        "completion_tokens": "",
        "reasoning_tokens": "",
        "call_seconds": "",
        "monotonic_seconds": "",
        "utc_span_seconds": "",
        "preflight_calls": "",
        "processing_calls": "",
        "preflight_seconds": "",
        "cost_scope": "",
    }


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _sum_unknown(values: list[object]) -> int | float | str:
    total = 0
    for value in values:
        if value == "unknown" or isinstance(value, bool) or not isinstance(value, (int, float)):
            return "unknown"
        total += value
    return total


def _aggregate_metrics(documents: list[dict]) -> dict:
    metrics_list = [document_metrics(document) for document in documents]
    sentence_lengths: list[float] = []
    durations: list[float] = []
    duration_unknown = False
    overlaps: list[int] = []
    finish: dict[str, int] = {}
    for metrics in metrics_list:
        sentence_lengths.extend(metrics["sentence_lengths"])
        overlaps.extend(metrics["overlap_values"])
        if metrics["durations"] == "unknown":
            duration_unknown = True
        else:
            durations.extend(metrics["durations"])
        for key, value in metrics["finish_reasons"].items():
            finish[key] = finish.get(key, 0) + value

    def add(key: str) -> int:
        return sum(metrics[key] for metrics in metrics_list)

    keeps = add("keep_count")
    merges = add("merge_count")
    decisions = keeps + merges
    calls: list[dict] = []
    for document in documents:
        calls.extend(document.get("calls") or [])
    return {
        "proposal_count": add("proposal_count"),
        "boundaries_after_conflict": add("boundaries_after_conflict"),
        "final_units": add("final_units"),
        "keep_count": keeps,
        "merge_count": merges,
        "merge_ratio": None if decisions == 0 else merges / decisions,
        "unit_sentences_median": median(sentence_lengths),
        "unit_sentences_p95": percentile_95(sentence_lengths),
        "unit_sentences_max": max(sentence_lengths) if sentence_lengths else None,
        "unit_seconds_median": "unknown" if duration_unknown else median(durations),
        "unit_seconds_p95": "unknown" if duration_unknown else percentile_95(durations),
        "unit_seconds_max": "unknown" if duration_unknown else (max(durations) if durations else None),
        "single_sentence_units": add("single_sentence_units"),
        "units_over_40_sentences": add("units_over_40_sentences"),
        "verify_full_count": add("verify_full_count"),
        "verify_excerpt_count": add("verify_excerpt_count"),
        "verify_sentences": add("verify_sentences"),
        "verify_tokens": _token_sum([call for call in calls if call.get("stage") == "verify"], "prompt_tokens"),
        "chunk_count": add("chunk_count"),
        "overlap_mean": None if not overlaps else sum(overlaps) / len(overlaps),
        "char_limited_chunks": add("char_limited_chunks"),
        "boundary_calls": add("boundary_calls"),
        "conflict_calls": add("conflict_calls"),
        "verify_calls": add("verify_calls"),
        "error_calls": add("error_calls"),
        "retry_calls": add("retry_calls"),
        "finish_reasons": finish,
        "prompt_tokens": _sum_unknown([metrics["prompt_tokens"] for metrics in metrics_list]),
        "completion_tokens": _sum_unknown([metrics["completion_tokens"] for metrics in metrics_list]),
        "reasoning_tokens": _sum_unknown([metrics["reasoning_tokens"] for metrics in metrics_list]),
        "call_seconds": sum(metrics["call_seconds"] for metrics in metrics_list),
        "processing_calls": len(calls),
    }


def _row_from_documents(
    common: dict,
    documents: list[dict],
    run: dict,
    video_id: str,
    input_group: str,
    preflight_calls: list[dict],
    cost_scope: str,
) -> dict:
    if video_id == "*":
        metrics = _aggregate_metrics(documents)
        verify_tokens = metrics["verify_tokens"]
        processing_calls = metrics["processing_calls"]
    else:
        document = next(item for item in documents if item["video_id"] == video_id)
        metrics = document_metrics(document)
        verify_tokens = _token_sum(
            [call for call in document.get("calls") or [] if call.get("stage") == "verify"],
            "prompt_tokens",
        )
        processing_calls = len(document.get("calls") or [])
    row = _blank_row()
    row.update(common)
    row.update(
        {
            "input_group": input_group,
            "video_id": video_id,
            "cost_scope": cost_scope,
            "proposal_count": metrics["proposal_count"],
            "boundaries_after_conflict": metrics["boundaries_after_conflict"],
            "final_units": metrics["final_units"],
            "keep_count": metrics["keep_count"],
            "merge_count": metrics["merge_count"],
            "merge_ratio": _fmt(metrics["merge_ratio"]),
            "unit_sentences_median": _fmt(metrics["unit_sentences_median"]),
            "unit_sentences_p95": _fmt(metrics["unit_sentences_p95"]),
            "unit_sentences_max": _fmt(metrics["unit_sentences_max"]),
            "unit_seconds_median": _fmt(metrics["unit_seconds_median"]),
            "unit_seconds_p95": _fmt(metrics["unit_seconds_p95"]),
            "unit_seconds_max": _fmt(metrics["unit_seconds_max"]),
            "single_sentence_units": metrics["single_sentence_units"],
            "units_over_40_sentences": metrics["units_over_40_sentences"],
            "verify_full_count": metrics["verify_full_count"],
            "verify_excerpt_count": metrics["verify_excerpt_count"],
            "verify_sentences": metrics["verify_sentences"],
            "verify_tokens": verify_tokens,
            "chunk_count": metrics["chunk_count"],
            "overlap_mean": _fmt(metrics["overlap_mean"]),
            "char_limited_chunks": metrics["char_limited_chunks"],
            "boundary_calls": metrics["boundary_calls"],
            "conflict_calls": metrics["conflict_calls"],
            "verify_calls": metrics["verify_calls"],
            "error_calls": metrics["error_calls"],
            "retry_calls": metrics["retry_calls"],
            "finish_reasons": json.dumps(metrics["finish_reasons"], ensure_ascii=False, sort_keys=True),
            "prompt_tokens": metrics["prompt_tokens"],
            "completion_tokens": metrics["completion_tokens"],
            "reasoning_tokens": metrics["reasoning_tokens"],
            "call_seconds": _fmt(metrics["call_seconds"]),
            "monotonic_seconds": _fmt(run.get("monotonic_seconds")),
            "utc_span_seconds": _fmt(run.get("utc_span_seconds")),
            "preflight_calls": len(preflight_calls) if video_id == "*" else "",
            "processing_calls": processing_calls,
            "preflight_seconds": _fmt(engine.call_seconds(preflight_calls)) if video_id == "*" else "",
        }
    )
    return row


def _attempt_rows(campaign: Path, slot: dict, plan: dict, manifest: dict) -> list[dict]:
    rows = []
    phase = slot["phase"]
    experiment_id = slot.get("experiment_id")
    repeat = slot["repeat"]
    if slot["status"] == "skipped" or not experiment_id:
        row = _blank_row()
        row.update(
            {
                "phase": phase,
                "experiment_id": experiment_id or "",
                "repeat": repeat,
                "status": slot["status"],
                "input_group": "all" if phase == "confirm" else "search",
                "video_id": "*",
                "cost_scope": slot.get("reason") or slot["status"],
            }
        )
        return [row]
    input_ids = plan["inputs"]["search_ids"] if phase == "search" else plan["inputs"]["all_ids"]
    expected = plan["inputs"]["expected_sentence_count"]["search" if phase == "search" else "all"]
    attempts = slot.get("attempts_started") or 0
    for number in range(1, attempts + 1):
        path = attempt_dir(campaign, phase, experiment_id, repeat, number)
        run = _load_json(path / "run.json")
        result = _load_json(path / "attempt_result.json")
        accepted = slot.get("accepted_attempt") == number and slot["status"] == "validated"
        if run is None:
            row = _blank_row()
            row.update(
                {
                    "phase": phase,
                    "experiment_id": experiment_id,
                    "repeat": repeat,
                    "attempt": number,
                    "status": "missing" if result is None else result.get("failure_class") or "failed",
                    "input_group": "all" if phase == "confirm" else "search",
                    "input_ids": "|".join(input_ids),
                    "expected_sentences": expected,
                    "video_id": "*",
                    "cost_scope": "failed_attempt",
                    "monotonic_seconds": _fmt((result or {}).get("monotonic_seconds")),
                }
            )
            rows.append(row)
            continue
        documents = []
        for video_id in input_ids:
            document = _load_json(path / video_id / "semantic_units.json")
            if document is not None:
                documents.append(document)
        preflight, _processing = _calls_of(run, documents)
        identity = manifest.get("response_fingerprint") or "unknown"
        common = {
            "phase": phase,
            "experiment_id": experiment_id,
            "repeat": repeat,
            "attempt": number,
            "status": "validated" if accepted else (result or {}).get("failure_class") or "failed",
            "config_hash": canonical_hash(run.get("settings")),
            "engine_hash": manifest.get("engine_sha256", ""),
            "prompt_hash": canonical_hash(run.get("prompts")),
            "model_identity": identity,
            "input_ids": "|".join(input_ids),
            "expected_sentences": run.get("expected_sentence_count", expected),
            "actual_sentences": run.get("actual_sentence_count", ""),
            "input_hash_set": "|".join(
                f"{item['video_id']}:{item['alignment_sha256']}:{item['metadata_sha256']}" for item in run.get("inputs") or []
            ),
        }
        if not documents:
            row = _blank_row()
            row.update(common)
            row.update(
                {
                    "input_group": "all" if phase == "confirm" else "search",
                    "video_id": "*",
                    "cost_scope": "accepted" if accepted else "failed_attempt",
                    "preflight_calls": len(preflight),
                    "preflight_seconds": _fmt(engine.call_seconds(preflight)),
                    "monotonic_seconds": _fmt(run.get("monotonic_seconds")),
                    "utc_span_seconds": _fmt(run.get("utc_span_seconds")),
                }
            )
            rows.append(row)
            continue
        groups = ["search"] if phase == "search" else ["search", "final_only", "all"]
        present_ids = [document["video_id"] for document in documents]
        for video_id in present_ids:
            rows.append(
                _row_from_documents(
                    common, documents, run, video_id, _group_for(video_id, plan), preflight,
                    "accepted" if accepted else "failed_attempt",
                )
            )
        for group in groups:
            if group == "all":
                chosen = documents
            else:
                chosen = [document for document in documents if _group_for(document["video_id"], plan) == group]
            if not chosen:
                continue
            rows.append(
                _row_from_documents(
                    common, chosen, run, "*", group, preflight,
                    "accepted" if accepted else "failed_attempt",
                )
            )
    return rows


def write_metrics(campaign: Path, state: dict, plan: dict, manifest: dict) -> None:
    rows: list[dict] = []
    for slot in state["slots"].values():
        rows.extend(_attempt_rows(campaign, slot, plan, manifest))
    fieldnames = list(_blank_row())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    target = campaign / "metrics.csv"
    temporary = target.with_suffix(".csv.tmp")
    temporary.write_text(buffer.getvalue(), encoding="utf-8")
    temporary.replace(target)


def _accepted_documents(campaign: Path, slot: dict) -> tuple[Path | None, dict[str, dict]]:
    attempt = slot.get("accepted_attempt")
    if slot.get("status") != "validated" or not attempt or not slot.get("experiment_id"):
        return None, {}
    path = attempt_dir(campaign, slot["phase"], slot["experiment_id"], slot["repeat"], attempt)
    documents = {}
    for file_path in sorted(path.glob("*/semantic_units.json")):
        document = _load_json(file_path)
        if document is not None:
            documents[document["video_id"]] = document
    return path, documents


def write_boundary_diffs(campaign: Path, state: dict, plan: dict) -> None:
    payload: dict = {}
    slots = state["slots"]
    for key, slot in slots.items():
        path, documents = _accepted_documents(campaign, slot)
        if path is None or slot.get("experiment_id") in {None, "E00"}:
            continue
        baseline_key = f"{slot['phase']}/E00/{slot['repeat']}"
        baseline = slots.get(baseline_key)
        entry = {"baseline": baseline_key, "comparison": "unavailable", "videos": {}}
        if baseline and baseline.get("status") == "validated":
            _base_path, base_documents = _accepted_documents(campaign, baseline)
            entry["comparison"] = "available"
            for video_id, document in documents.items():
                base = base_documents.get(video_id)
                if base is None:
                    entry["videos"][video_id] = {"comparison": "unavailable"}
                    continue
                final_current = _boundaries(document, "boundaries_final")
                final_base = _boundaries(base, "boundaries_final")
                entry["videos"][video_id] = {
                    "added_final": sorted(final_current - final_base),
                    "removed_final": sorted(final_base - final_current),
                    "added_after_conflict": sorted(_boundaries(document, "boundaries_after_conflict") - _boundaries(base, "boundaries_after_conflict")),
                    "removed_after_conflict": sorted(_boundaries(base, "boundaries_after_conflict") - _boundaries(document, "boundaries_after_conflict")),
                    "added_proposals": sorted(_proposal_set(document) - _proposal_set(base)),
                    "removed_proposals": sorted(_proposal_set(base) - _proposal_set(document)),
                }
        payload[key] = entry
    for phase in ("search", "confirm"):
        for experiment_id in plan["experiment_ids"]:
            left = slots.get(f"{phase}/{experiment_id}/r01")
            right = slots.get(f"{phase}/{experiment_id}/r02")
            if not left or not right:
                continue
            record = payload.setdefault(f"{phase}/{experiment_id}/repeats", {"videos": {}})
            if left.get("status") != "validated" or right.get("status") != "validated":
                record["jaccard"] = "unavailable"
                continue
            _left_path, left_docs = _accepted_documents(campaign, left)
            _right_path, right_docs = _accepted_documents(campaign, right)
            scores = {}
            for video_id in left_docs:
                if video_id not in right_docs:
                    continue
                a = _boundaries(left_docs[video_id], "boundaries_final")
                b = _boundaries(right_docs[video_id], "boundaries_final")
                scores[video_id] = {
                    "jaccard": jaccard(a, b),
                    "symmetric_difference": len(a ^ b),
                }
            record["jaccard"] = scores
    engine.dump_json(campaign / "boundary_diffs.json", payload)


def _context_lines(video: dict, index: int) -> dict:
    sentences = video["sentences"]
    count = len(sentences)
    left = sentences[max(0, index - 12) : index]
    right = sentences[index : min(count, index + 12)]
    return {
        "left_span": [max(0, index - 12), index],
        "right_span": [index, min(count, index + 12)],
        "left": [engine.sentence_line(sentence) for sentence in left],
        "right": [engine.sentence_line(sentence) for sentence in right],
        "extra_context": False,
    }


def _observation(slot_key: str, document: dict, index: int) -> dict:
    verification = next((item for item in document.get("verifications") or [] if item.get("sentence_index") == index), None)
    return {
        "slot": slot_key,
        "action": None if verification is None else verification.get("action"),
        "reason": None if verification is None else verification.get("reason"),
        "in_final": index in set(document.get("boundaries_final") or []),
        "in_after_conflict": index in set(document.get("boundaries_after_conflict") or []),
        "in_proposals": index in _proposal_set(document),
    }


def write_review_queue(campaign: Path, state: dict, plan: dict) -> None:
    videos, problems = engine.load_selected(engine.DATA_ROOT, plan["inputs"]["all_ids"])
    if problems:
        engine.dump_json(campaign / "review_queue.json", {"status": "input_error", "problems": problems, "items": []})
        return
    by_id = {video["video_id"]: video for video in videos}
    samples = []
    items: dict[tuple[str, int], dict] = {}

    def add_item(video_id: str, index: int, tag: str) -> None:
        key = (video_id, index)
        item = items.get(key)
        if item is None:
            item = {
                "video_id": video_id,
                "sentence_index": index,
                "tags": [],
                "context": _context_lines(by_id[video_id], index),
                "observations": [],
                "status": "pending_human",
                "reviewer_type": None,
            }
            items[key] = item
        if tag not in item["tags"]:
            item["tags"].append(tag)

    def sample_from(slot: dict | None, video_ids: list[str]) -> None:
        for video_id in video_ids:
            record = {
                "video_id": video_id,
                "source_run": None,
                "source_artifact_sha256": None,
                "pool": [],
                "seed": plan["review_seed"],
                "algorithm": ALGORITHM,
                "selected": [],
                "status": "unavailable",
            }
            if slot and slot.get("status") == "validated":
                path, documents = _accepted_documents(campaign, slot)
                document = documents.get(video_id)
                if path is not None and document is not None:
                    artifact = path / video_id / "semantic_units.json"
                    pool, selected = sample_boundaries(
                        video_id,
                        document.get("boundaries_final") or [],
                        document["input"]["sentence_count"],
                        plan["review_seed"],
                    )
                    record.update(
                        {
                            "source_run": f"{slot['phase']}/{slot['experiment_id']}/{slot['repeat']}",
                            "source_artifact_sha256": engine.sha256_file(artifact),
                            "pool": pool,
                            "selected": selected,
                            "status": "ok",
                        }
                    )
                    for index in selected:
                        add_item(video_id, index, "sample")
            samples.append(record)

    search_e00 = state["slots"].get("search/E00/r01")
    sample_from(search_e00, plan["inputs"]["search_ids"])
    confirm_started = any(slot["phase"] == "confirm" and slot["status"] != "pending" for slot in state["slots"].values())
    if confirm_started or (state["slots"].get("confirm/E00/r01") or {}).get("status") == "validated":
        sample_from(state["slots"].get("confirm/E00/r01"), plan["inputs"]["final_only_ids"])
    for spec in plan["fixed_review"]:
        add_item(spec["video_id"], spec["sentence_index"], spec["tag"])
    if confirm_started:
        for spec in plan["confirm_uncertain_review"]:
            add_item(spec["video_id"], spec["sentence_index"], spec["tag"])
    diffs = _load_json(campaign / "boundary_diffs.json") or {}
    for slot_key, entry in diffs.items():
        if not isinstance(entry, dict):
            continue
        for video_id, video_entry in (entry.get("videos") or {}).items():
            if not isinstance(video_entry, dict):
                continue
            for index in video_entry.get("added_final") or []:
                add_item(video_id, index, "added")
            for index in video_entry.get("removed_final") or []:
                add_item(video_id, index, "removed")
    for slot_key, slot in state["slots"].items():
        _path, documents = _accepted_documents(campaign, slot)
        for video_id, document in documents.items():
            for item in items.values():
                if item["video_id"] == video_id:
                    item["observations"].append(_observation(slot_key, document, item["sentence_index"]))
    engine.dump_json(
        campaign / "review_queue.json",
        {
            "seed": plan["review_seed"],
            "algorithm": ALGORITHM,
            "samples": samples,
            "items": [items[key] for key in sorted(items)],
            "pending_human": True,
        },
    )


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def eligible_stats(campaign: Path, state: dict, plan: dict) -> list[dict]:
    stats = []
    search_ids = plan["inputs"]["search_ids"]
    for experiment_id in plan["experiment_ids"]:
        if experiment_id == "E00":
            continue
        repeats = []
        ok = True
        for repeat in plan["repeats"]:
            slot = state["slots"].get(f"search/{experiment_id}/{repeat}")
            if slot is None or slot.get("status") != "validated":
                ok = False
                break
            repeats.append(slot)
        if not ok:
            continue
        scores = []
        times = []
        left_path, left_docs = _accepted_documents(campaign, repeats[0])
        right_path, right_docs = _accepted_documents(campaign, repeats[1])
        del left_path, right_path
        for video_id in search_ids:
            if video_id not in left_docs or video_id not in right_docs:
                ok = False
                break
            scores.append(
                jaccard(
                    _boundaries(left_docs[video_id], "boundaries_final"),
                    _boundaries(right_docs[video_id], "boundaries_final"),
                )
            )
            times.append(engine.call_seconds(left_docs[video_id].get("calls") or []))
            times.append(engine.call_seconds(right_docs[video_id].get("calls") or []))
        if not ok or not scores:
            continue
        stats.append(
            {
                "experiment_id": experiment_id,
                "jaccard_mean": _mean(scores),
                "call_seconds_mean": _mean(times),
            }
        )
    return stats


def build_selection(campaign: Path, state: dict, plan: dict) -> dict:
    stats = eligible_stats(campaign, state, plan)
    selected_id, method = choose_selected(stats)
    config_hash = None
    if selected_id is not None:
        config_hash = canonical_hash(engine.experiment_config(selected_id, plan).public_dict())
    if method == "default":
        rationale = (
            "자동 실행기는 의미 품질의 우열을 점수화하지 않는다. "
            "search 두 반복이 모두 검증된 E01을 사전 정의된 기본 후보로 골랐다. "
            "이 선택은 품질 우승이 아니다."
        )
    elif method == "technical_fallback":
        rationale = (
            "E01이 후보 자격을 갖지 못해, 두 반복의 영상별 최종 경계 Jaccard 평균이 높은 순, "
            "평균 본 처리 호출 시간이 짧은 순, 실험 ID가 작은 순으로 기술 대체 후보를 골랐다. "
            "품질 우승이 아니다."
        )
    else:
        rationale = "search 두 반복이 모두 검증된 E01~E12 후보가 없다."
    parts = []
    for experiment_id in plan["experiment_ids"]:
        for repeat in plan["repeats"]:
            slot = state["slots"].get(f"search/{experiment_id}/{repeat}")
            if slot and slot.get("status") == "validated":
                path = attempt_dir(campaign, "search", experiment_id, repeat, slot["accepted_attempt"]) / "run.json"
                parts.append(engine.sha256_file(path))
            else:
                parts.append(f"missing:{experiment_id}:{repeat}")
    return {
        "selected_id": selected_id,
        "method": method,
        "rationale": rationale,
        "unreviewed": ["pending_human"],
        "eligible": [row["experiment_id"] for row in stats],
        "eligible_stats": stats,
        "search_result_hash": hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest(),
        "config_hash": config_hash,
        "input_group": "search",
        "confirmed_at": engine.utc_now(),
        "quality_winner": False,
    }


def _slot_table(state: dict) -> list[str]:
    lines = ["| 슬롯 | 상태 | attempt | 사유 |", "| --- | --- | ---: | --- |"]
    for key, slot in state["slots"].items():
        lines.append(
            f"| {key} | {slot.get('status')} | {slot.get('accepted_attempt') or slot.get('attempts_started') or ''} | {slot.get('reason') or ''} |"
        )
    return lines


def write_summary(campaign: Path, state: dict, plan: dict) -> None:
    selection = _load_json(campaign / "selection.json") or {}
    diffs = _load_json(campaign / "boundary_diffs.json") or {}
    lines = [
        "# Sweep 요약",
        "",
        "2회 반복으로 통계적 유의성이나 일반화를 주장하지 않는다. 사람 검수가 없는 항목은 pending_human이다.",
        "",
        "## 진행",
        "",
    ]
    lines.extend(_slot_table(state))
    counts: dict[str, int] = {}
    for slot in state["slots"].values():
        counts[slot.get("status") or "unknown"] = counts.get(slot.get("status") or "unknown", 0) + 1
    lines.extend(["", "## 상태 개수", ""])
    for status in ("validated", "failed", "skipped", "pending", "running", "validating"):
        lines.append(f"- {status}: {counts.get(status, 0)}")
    if state.get("stopped_reason"):
        lines.append(f"- 중단 사유: {state['stopped_reason']}")
    lines.extend(["", f"- 누적 monotonic 초: {state.get('elapsed_seconds')}", ""])
    lines.extend(["## 후보", ""])
    if selection:
        lines.append(f"- selected_id: {selection.get('selected_id')}")
        lines.append(f"- method: {selection.get('method')}")
        lines.append(f"- quality_winner: {selection.get('quality_winner')}")
        lines.append(f"- 근거: {selection.get('rationale')}")
        lines.append(f"- 미검수: {selection.get('unreviewed')}")
    else:
        lines.append("후보 선정이 아직 없다.")
    lines.extend(["", "## 탐색 관측", ""])
    lines.extend(_experiment_lines(campaign, state, plan, "search"))
    lines.extend(["", "## 최종 확인", ""])
    lines.append("confirm 지표는 metrics.csv에서 input_group이 search, final_only, all인 행으로 나뉜다. 단계 총합을 입력 크기를 무시하고 비교하지 않는다.")
    lines.extend(_experiment_lines(campaign, state, plan, "confirm"))
    lines.extend(["", "## 기준 대비 최종 경계", ""])
    for key, entry in diffs.items():
        if not isinstance(entry, dict) or "videos" not in entry:
            continue
        lines.append(f"### {key}")
        lines.append("")
        lines.append(f"- 비교: {entry.get('comparison')}")
        for video_id, video_entry in (entry.get("videos") or {}).items():
            if not isinstance(video_entry, dict):
                continue
            lines.append(
                f"- {video_id} 추가 {video_entry.get('added_final')} 제거 {video_entry.get('removed_final')}"
            )
        lines.append("")
    lines.extend(_cost_lines(campaign, state))
    lines.extend(
        [
            "",
            "## 장편과 과분할",
            "",
            "CvuE-jyg-N8은 confirm에만 있고 후보 선정에 쓰지 않는다. 1문장 단위 수는 품질 점수가 아니며 metrics.csv의 single_sentence_units로 E00과 나란히 본다.",
            "",
            "## 검수",
            "",
            "review_queue.json의 고정 위치, SHA-256 표본, 추가·제거 경계는 pending_human이다. 에이전트 평가는 human gold가 아니다.",
            "",
        ]
    )
    target = campaign / "summary.md"
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(target)


def _experiment_lines(campaign: Path, state: dict, plan: dict, phase: str) -> list[str]:
    lines = []
    for experiment_id in plan["experiment_ids"]:
        values = []
        ratios = []
        seconds = []
        for repeat in plan["repeats"]:
            slot = state["slots"].get(f"{phase}/{experiment_id}/{repeat}")
            if slot is None:
                continue
            _path, documents = _accepted_documents(campaign, slot)
            if not documents:
                lines.append(f"- {phase}/{experiment_id}/{repeat}: {slot.get('status')} {slot.get('reason') or ''}")
                continue
            units = sum(len(document.get("units") or []) for document in documents.values())
            merges = 0
            decisions = 0
            call_seconds = 0.0
            for document in documents.values():
                for item in document.get("verifications") or []:
                    decisions += 1
                    if item.get("action") == "merge":
                        merges += 1
                call_seconds += engine.call_seconds(document.get("calls") or [])
            values.append(units)
            ratios.append(None if decisions == 0 else merges / decisions)
            seconds.append(call_seconds)
            lines.append(f"- {phase}/{experiment_id}/{repeat}: 단위 {units}, merge 비율 {ratios[-1]}, 본처리 초 {call_seconds}")
        if values:
            ratio_text = "n/a" if any(value is None for value in ratios) else f"{min(ratios)}..{max(ratios)} 평균 {sum(ratios) / len(ratios)}"
            lines.append(
                f"- {phase}/{experiment_id} 범위: 단위 {min(values)}..{max(values)} 평균 {sum(values) / len(values)}, "
                f"merge 비율 {ratio_text}, 본처리 초 {min(seconds)}..{max(seconds)} 평균 {sum(seconds) / len(seconds)}"
            )
    return lines


def _cost_lines(campaign: Path, state: dict) -> list[str]:
    accepted_preflight = 0
    accepted_processing = 0
    accepted_seconds = 0.0
    failed_preflight = 0
    failed_processing = 0
    failed_seconds = 0.0
    for slot in state["slots"].values():
        if not slot.get("experiment_id"):
            continue
        attempts = slot.get("attempts_started") or 0
        for number in range(1, attempts + 1):
            path = attempt_dir(campaign, slot["phase"], slot["experiment_id"], slot["repeat"], number)
            run = _load_json(path / "run.json") or {}
            documents = []
            for file_path in path.glob("*/semantic_units.json"):
                document = _load_json(file_path)
                if document is not None:
                    documents.append(document)
            preflight, processing = _calls_of(run, documents)
            seconds = engine.call_seconds(preflight) + engine.call_seconds(processing)
            if slot.get("status") == "validated" and slot.get("accepted_attempt") == number:
                accepted_preflight += len(preflight)
                accepted_processing += len(processing)
                accepted_seconds += seconds
            else:
                failed_preflight += len(preflight)
                failed_processing += len(processing)
                failed_seconds += seconds
    return [
        "## 비용",
        "",
        f"- 성공 attempt 사전 점검 호출: {accepted_preflight}",
        f"- 성공 attempt 본 처리 호출: {accepted_processing}",
        f"- 성공 attempt 호출 시간 합: {accepted_seconds}",
        f"- 실패 attempt 사전 점검 호출: {failed_preflight}",
        f"- 실패 attempt 본 처리 호출: {failed_processing}",
        f"- 실패 attempt 호출 시간 합: {failed_seconds}",
        f"- campaign 호출 시간 합: {accepted_seconds + failed_seconds}",
        f"- 상태 파일 monotonic 초: {state.get('elapsed_seconds')}",
        "",
        "monotonic 초와 UTC 시각 차이는 서로 다른 값이다. 호출 시간 합과도 섞지 않는다.",
    ]


def rebuild_reports(campaign: Path, state: dict, plan: dict, manifest: dict) -> None:
    write_metrics(campaign, state, plan, manifest)
    write_boundary_diffs(campaign, state, plan)
    write_review_queue(campaign, state, plan)
    for slot in state["slots"].values():
        if slot.get("status") != "validated" or not slot.get("experiment_id"):
            continue
        path, _documents = _accepted_documents(campaign, slot)
        if path is None:
            continue
        baseline = None
        if slot["experiment_id"] != "E00":
            baseline_slot = state["slots"].get(f"{slot['phase']}/E00/{slot['repeat']}")
            if baseline_slot and baseline_slot.get("status") == "validated":
                baseline, _base_documents = _accepted_documents(campaign, baseline_slot)
        input_ids = plan["inputs"]["search_ids"] if slot["phase"] == "search" else plan["inputs"]["all_ids"]
        text = compare_attempt(path, baseline, input_ids)
        target = path / "comparison_report.md"
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
    write_summary(campaign, state, plan)


def check_helpers() -> None:
    assert percentile_95([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 10
    assert percentile_95([5]) == 5
    assert jaccard(set(), set()) == 1
    assert jaccard({1, 2}, {2, 3}) == 1 / 3
    pool, selected = sample_boundaries("video", [0, 2, 5, 9], 10, SEED)
    assert pool == [2, 5, 9]
    assert selected == sorted(selected)
    assert len(selected) == 3
    chosen, method = choose_selected(
        [
            {"experiment_id": "E02", "jaccard_mean": 0.9, "call_seconds_mean": 10},
            {"experiment_id": "E01", "jaccard_mean": 0.1, "call_seconds_mean": 100},
        ]
    )
    assert chosen == "E01" and method == "default"
    fallback, fallback_method = choose_selected(
        [
            {"experiment_id": "E05", "jaccard_mean": 0.5, "call_seconds_mean": 3},
            {"experiment_id": "E03", "jaccard_mean": 0.5, "call_seconds_mean": 9},
            {"experiment_id": "E04", "jaccard_mean": 0.8, "call_seconds_mean": 100},
        ]
    )
    assert fallback == "E04" and fallback_method == "technical_fallback"
    tied, tied_method = choose_selected(
        [
            {"experiment_id": "E06", "jaccard_mean": 0.4, "call_seconds_mean": 2},
            {"experiment_id": "E02", "jaccard_mean": 0.4, "call_seconds_mean": 2},
        ]
    )
    assert tied == "E02" and tied_method == "technical_fallback"
    assert choose_selected([]) == (None, "none")
