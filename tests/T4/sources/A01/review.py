"""Markdown reviews for T4 frame selection. Image bytes stay on disk."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None:
        return "null"
    return str(value)


def _selection_note() -> str:
    return (
        "`selected_confirmed`는 모델이 keep으로 정상 판정했다는 뜻이다. "
        "사람의 검수 완료나 화면 내용의 사실 검증이 아니다."
    )


def render_video(document: dict[str, Any]) -> str:
    lines = [
        f"# {document['video_id']}",
        "",
        _selection_note(),
        "",
        f"- 상태: `{document.get('status')}`",
        f"- 원본: `{document.get('source_url')}`",
        f"- 미디어: `{document['media']['path']}`",
        f"- 기록된 SHA-256: `{document['media']['recorded_sha256']}`",
        f"- bytes 재해시: `{document['media']['bytes_rehashed']}`",
        f"- audio offset 재적용: `{document['media']['offset_reapplied']}`",
        "",
    ]
    problems = document.get("problems") or []
    if problems:
        lines.append("## 문제")
        lines.append("")
        for problem in problems:
            lines.append(f"- {problem}")
        lines.append("")
    for candidate in document["candidates"]:
        lines.extend(_candidate(candidate))
    return "\n".join(lines).rstrip() + "\n"


def _candidate(candidate: dict[str, Any]) -> list[str]:
    source = candidate["source_candidate"]
    lines = [
        f"## {candidate['candidate_id']}",
        "",
        f"- 상태: `{candidate.get('status')}`",
        f"- 출처: `{source.get('candidate_source')}`",
        f"- 모델 추론 성공: `{source.get('model_inference_success')}`",
        f"- 정책: `{source.get('policy_version')}`",
        f"- T3: `{candidate['source_t3_ref']}`",
        f"- T2: `{candidate['source_t2_ref']}`",
        f"- T1: `{candidate['text_ref']['t1_path']}`",
        f"- 문장 범위: [{candidate['text_ref']['sentence_start']}, {candidate['text_ref']['sentence_end']})",
        f"- 요청 범위: {_text(candidate['requested_range']['video_start'])}–{_text(candidate['requested_range']['video_end'])}",
        f"- 유효 범위: {_text(candidate['effective_range']['video_start'])}–{_text(candidate['effective_range']['video_end'])}",
        "",
        "문장 원문:",
        "",
    ]
    for sentence in candidate["sentences"]:
        lines.append(
            f"- {sentence['sentence_index']} "
            f"[{_text(sentence['audio_start'])}, {_text(sentence['audio_end'])}] "
            f"{sentence['text']}"
        )
    lines.append("")
    lines.append("### 묶음")
    lines.append("")
    for batch in candidate["batches"]:
        lines.append(
            f"- {batch['batch_id']}: `{batch['status']}` "
            f"context={batch['context_ids']} targets={batch['target_ids']} "
            f"eligible={batch.get('eligible_context_ids') or []} "
            f"call={batch.get('call_ref') or 'null'} "
            f"reused={batch.get('reused_from') or 'null'} "
            f"error={batch.get('error') or 'null'}"
        )
    lines.append("")
    lines.append("### 구간과 프레임")
    lines.append("")
    frames = {frame["frame_id"]: frame for frame in candidate["frames"]}
    for segment in candidate["segments"]:
        lines.append(
            f"#### segment {segment['segment_index']} "
            f"[{_text(segment['video_start'])}, {_text(segment['video_end'])})"
        )
        lines.append("")
        lines.append(f"- 분석 샘플: {segment['analysis_sample_indices']}")
        if not segment["frame_ids"]:
            lines.append("- 분석 샘플이 없는 구간이다.")
            lines.append("")
            continue
        for frame_id in segment["frame_ids"]:
            lines.extend(_frame(frames[frame_id], frames))
        lines.append("")
    confirmed = candidate.get("selected_confirmed") or []
    uncertain = candidate.get("retained_uncertain") or []
    dropped = candidate.get("not_selected") or []
    lines.extend([
        "### 집계",
        "",
        f"- selected_confirmed ({len(confirmed)}): {confirmed or '없음'}",
        f"- retained_uncertain ({len(uncertain)}): {uncertain or '없음'}",
        f"- not_selected ({len(dropped)}): {dropped or '없음'}",
        "",
    ])
    return lines


def _frame(frame: dict[str, Any], frames: dict[str, dict[str, Any]]) -> list[str]:
    full = frame.get("full") or {}
    vlm = frame.get("vlm") or {}
    requests = ", ".join(
        f"{item['request_index']}:{_text(item['requested_video_seconds'])}"
        for item in frame["requests"]
    ) or "없음"
    lines = [
        f"##### {frame['frame_id']}",
        "",
        f"- sample_index: {frame['sample_indices']}",
        f"- 실제 시각: {_text(frame['video_seconds'])}",
        f"- PTS: {frame['pts']} time_base {frame['time_base']['numerator']}/{frame['time_base']['denominator']}",
        f"- 요청 시각: {requests}",
        f"- 판정: `{frame.get('decision') or 'pending'}` / `{frame.get('selection') or 'pending'}`",
        f"- 이유: {frame.get('reason') or '없음'}",
        f"- duplicate_of: `{frame.get('duplicate_of') or 'null'}`",
    ]
    source = frame.get("reused_from")
    if isinstance(source, dict) and source.get("run_id"):
        lines.append(f"- 이미지 출처: `{source.get('config_id')}` `{source.get('run_id')}`")
    if full.get("path"):
        lines.append(f"- 원본 색상: ![{frame['frame_id']}]({_relative(full['path'])})")
    else:
        lines.append("- 원본 색상: 없음")
    if vlm.get("path"):
        lines.append(f"- VLM 입력: `{_relative(vlm['path'])}` {vlm.get('width')}×{vlm.get('height')} `{vlm.get('sha256')}`")
    duplicate = frame.get("duplicate_of")
    if isinstance(duplicate, str):
        other = frames.get(duplicate)
        other_path = ((other or {}).get("full") or {}).get("path")
        if other_path:
            lines.append(f"- 대체 화면: ![{duplicate}]({_relative(other_path)})")
        else:
            lines.append(f"- 대체 화면 파일을 이 후보에서 찾지 못함: `{duplicate}`")
    lines.append("")
    return lines


def _relative(path: str) -> str:
    marker = "tests/T4/runs/"
    if path.startswith(marker):
        return "../../" + path[len(marker):]
    nested = "/frames/"
    if nested in path:
        return "../frames/" + path.split(nested, 1)[1]
    return path


def render_index(summary: dict[str, Any]) -> str:
    lines = [
        "# T4 프레임 선정",
        "",
        _selection_note(),
        "",
        f"- run: `{summary['run_id']}`",
        f"- 단계: `{summary['phase']}`",
        f"- 실행 상태: `{summary['run_status']}`",
        f"- quality_status: `{summary['quality_status']}`",
        f"- 설정: `{summary['config_id']}` / `{summary['logic_version']}` / `{summary['prompt_id']}`",
        "",
        "## 범위",
        "",
        f"- 영상 {summary['video_count']}개, 이번 입력 후보 {summary['candidate_count']}개, 범위 밖 {summary['out_of_scope_count']}개",
        f"- 계획 요청 {summary['planned_requests']}건. smoke·pilot·전체를 합친 완료가 아니다.",
        "",
        "## 후보",
        "",
        "| 후보 | 상태 | 요청 ok/fail/pending | confirmed | uncertain | review |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in summary["candidates"]:
        lines.append(
            f"| {row['candidate_id']} | {row['status']} | "
            f"{row['ok']}/{row['failed']}/{row['pending']} | "
            f"{row['confirmed']} | {row['uncertain']} | "
            f"[review]({row['review']}) |"
        )
    lines.extend(["", "## 범위 밖", "", "T2 후보 가운데 이번 B05 입력이 아닌 127개다. B05 결과를 만들지 않았다.", ""])
    for candidate_id in summary["out_of_scope"]:
        lines.append(f"- {candidate_id}")
    lines.extend([
        "",
        "## 비용",
        "",
        f"- 신규 {summary['counts']['new']} / 재사용 {summary['counts']['reused']} / "
        f"실패 {summary['counts']['failed']} / pending {summary['counts']['pending']}",
        f"- HTTP 시도 {summary['counts']['http_attempts']}",
        f"- 프레임 파일 재사용 {(summary.get('frame_files') or {}).get('reused', 0)} / "
        f"신규 {(summary.get('frame_files') or {}).get('created', 0)}",
        f"- context 재입력 이미지 {summary['counts']['context_reinputs']}",
        f"- decode {summary['timing']['decode_seconds']}초, resize {summary['timing']['resize_seconds']}초, "
        f"inference {summary['timing']['inference_seconds']}초, render {summary['timing']['render_seconds']}초, "
        f"wall {summary['timing']['wall_seconds']}초",
        f"- usage: `{summary['usage']}`",
        "- VRAM: null",
        "",
    ])
    return "\n".join(lines)
