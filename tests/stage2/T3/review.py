"""T3 검수 표시. 검출 코드와 분리한다."""

from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


import os
import signal
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

from detector import (
    SUBPROCESS_TIMEOUT,
    T3Error,
    dumps,
    loads,
    parse_showinfo_line,
    sha256_bytes,
    sha256_file,
)

MARKDOWN_ESCAPES = set("\\`*_{}[]<>()#+-.!|")
KNOWN_LOSSES = {
    "hGVL9XRU9IQ": "T2에서 수용한 손실: 단위 0(0–2.72초) 판매 분석 대시보드는 후보에 없다. T3 미검출과 구분한다.",
    "iEMuJiVY9Oc": "T2에서 수용한 손실: 단위 0(0.32–15.28초) 판서와 단위 13 끝 판서는 후보에 충분히 남지 않았다. T3 미검출과 구분한다.",
    "7EJ-xotXiSE": "T2에서 수용한 손실: 단위 21의 펼친 검색필터 이름 목록은 후보에 없다. T3 미검출과 구분한다.",
}


def review_sha256() -> str:
    return sha256_file(Path(__file__))


def md_escape(text: str) -> str:
    return "".join("\\" + char if char in MARKDOWN_ESCAPES else char for char in text)


def _num(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def preview_plan(document: dict[str, Any]) -> dict[str, Any]:
    samples = document.get("samples") or []
    by_index = {int(sample["sample_index"]): sample for sample in samples}
    start = document["requested_range"]["video_start"]
    end = document["requested_range"]["video_end"]
    timeline: list[int] = []
    if samples:
        timeline.append(int(samples[0]["sample_index"]))
        step = 1
        while True:
            requested = start + Decimal(step) * Decimal(10)
            if requested >= end:
                break
            chosen = next((sample for sample in samples if sample["video_seconds"] >= requested), None)
            if chosen is not None:
                timeline.append(int(chosen["sample_index"]))
            step += 1
        timeline.append(int(samples[-1]["sample_index"]))
    deduped_timeline = []
    for index in timeline:
        if index not in deduped_timeline:
            deduped_timeline.append(index)
    boundaries = []
    for boundary in document.get("boundaries") or []:
        boundaries.append({
            "boundary_index": boundary["boundary_index"],
            "before": int(boundary["before_sample"]),
            "after": int(boundary["after_sample"]),
        })
    segments = []
    for segment in document.get("segments") or []:
        ids = [int(index) for index in segment.get("analysis_sample_indices") or []]
        if not ids:
            segments.append({"segment_index": segment["segment_index"], "samples": [], "empty": True})
            continue
        ranked = [(index, by_index[index]["video_seconds"]) for index in ids]
        first = min(ranked, key=lambda item: (item[1], item[0]))[0]
        last = max(ranked, key=lambda item: (item[1], -item[0]))[0]
        middle = (segment["video_start"] + segment["video_end"]) / Decimal(2)
        center = min(ranked, key=lambda item: (abs(item[1] - middle), item[1], item[0]))[0]
        picked = []
        for index in (first, center, last):
            if index not in picked:
                picked.append(index)
        segments.append({"segment_index": segment["segment_index"], "samples": picked, "empty": False})
    render_ids = []
    for index in [item["before"] for item in boundaries] + [item["after"] for item in boundaries] + deduped_timeline:
        if index not in render_ids:
            render_ids.append(index)
    for segment in segments:
        for index in segment["samples"]:
            if index not in render_ids:
                render_ids.append(index)
    return {"boundaries": boundaries, "timeline": deduped_timeline, "segments": segments, "render_samples": render_ids}


def extract_jpeg(
    ffmpeg: str,
    video: Path,
    pts: int,
    seek: Decimal,
    destination: Path,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.jpg")
    if temporary.exists():
        temporary.unlink()
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", "-threads", "2", "-copyts",
        "-ss", format(seek, "f"), "-i", str(video), "-map", "0:v:0", "-an",
        "-vf", f"select='eq(pts\\,{pts})',scale=w='min(1280,iw)':h=-2:flags=area,showinfo",
        "-frames:v", "1", "-q:v", "2", str(temporary),
    ]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        _, stderr = proc.communicate(timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            proc.kill()
        proc.wait(timeout=30)
        raise T3Error("검수 이미지 FFmpeg가 제한 시간을 초과했습니다.", stage="render", details={"args": args}) from exc
    text = stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0 or not temporary.exists():
        if temporary.exists():
            temporary.unlink()
        raise T3Error(
            "요청한 PTS의 검수 이미지를 만들지 못했습니다.",
            stage="render",
            details={"args": args, "returncode": proc.returncode, "stderr_tail": text[-2000:]},
        )
    seen = [parse_showinfo_line(line) for line in text.splitlines()]
    seen = [item for item in seen if item]
    if len(seen) != 1 or int(seen[0]["pts"]) != int(pts):
        temporary.unlink()
        raise T3Error(
            "검수 이미지의 PTS가 요청한 정수 PTS와 다릅니다.",
            stage="render",
            details={"expected_pts": pts, "seen": seen, "stderr_tail": text[-1500:]},
        )
    if int(seen[0]["width"]) > 1280:
        temporary.unlink()
        raise T3Error("검수 이미지 폭이 1280을 넘습니다.", stage="render")
    data = temporary.read_bytes()
    os.replace(temporary, destination)
    return {"sha256": sha256_bytes(data), "width": int(seen[0]["width"]), "height": int(seen[0]["height"]), "args": args}


def render_candidate_images(
    document: dict[str, Any],
    *,
    ffmpeg: str,
    video: Path,
    frames_dir: Path,
    repo_root: Path,
) -> tuple[dict[str, Any], list[str]]:
    plan = preview_plan(document)
    samples = {int(sample["sample_index"]): sample for sample in document.get("samples") or []}
    problems = []
    for sample_index in plan["render_samples"]:
        sample = samples[sample_index]
        filename = f"u{document['unit_index']}_c{document['chunk_index']}_pts{sample['pts']}.jpg"
        destination = frames_dir / filename
        seek = sample["video_seconds"] - Decimal(1)
        if seek < 0:
            seek = Decimal(0)
        try:
            saved = extract_jpeg(ffmpeg, video, int(sample["pts"]), seek, destination)
        except T3Error as exc:
            problems.append(f"{document['candidate_id']} sample {sample_index}: {exc}")
            continue
        relative = destination.resolve().relative_to(repo_root.resolve()).as_posix()
        sample["preview_ref"] = {"path": relative, "sha256": saved["sha256"], "width": saved["width"], "height": saved["height"]}
    return plan, problems


def _svg(document: dict[str, Any]) -> str:
    differences = document.get("differences") or []
    if differences and "tile_max" in differences[0]:
        return _svg_tiles(document)
    start = document["requested_range"]["video_start"]
    end = document["requested_range"]["video_end"]
    span = end - start
    if span <= 0:
        span = Decimal(1)
    width = 960
    height = 280
    left = 48
    right = 12
    top = 16
    bottom = 32
    plot_width = Decimal(width - left - right)
    plot_height = Decimal(height - top - bottom)
    values = [item["value"] for item in document.get("differences") or []]
    thresholds = [item["threshold"] for item in document.get("differences") or [] if item.get("threshold") is not None]
    peak = max(values + thresholds + [Decimal("0.01")])
    if peak <= 0:
        peak = Decimal(1)

    def x_of(seconds: Decimal) -> str:
        return format(left + (seconds - start) / span * plot_width, "f")

    def y_of(value: Decimal) -> str:
        return format(top + plot_height - (value / peak) * plot_height, "f")

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="14" font-size="12" fill="#222">변화량 / 임계값</text>',
    ]
    differences = document.get("differences") or []
    coords = []
    threshold_coords = []
    for item in differences:
        seconds = document["samples"][item["after_sample"]]["video_seconds"]
        coords.append(f'{x_of(seconds)},{y_of(item["value"])}')
        threshold_coords.append(f'{x_of(seconds)},{y_of(item["threshold"])}')
    if coords:
        parts.append(f'<polyline fill="none" stroke="#9aa0a6" stroke-width="1" points="{" ".join(threshold_coords)}"/>')
        parts.append(f'<polyline fill="none" stroke="#1a73e8" stroke-width="1.5" points="{" ".join(coords)}"/>')
    for item in differences:
        seconds = document["samples"][item["after_sample"]]["video_seconds"]
        action = item["peak_action"]
        color = {"keep": "#d93025", "suppress": "#f9ab00", "below_threshold": "#1a73e8"}[action]
        radius = "4" if action == "keep" else "2.5"
        parts.append(f'<circle cx="{x_of(seconds)}" cy="{y_of(item["value"])}" r="{radius}" fill="{color}"/>')
    for request in document.get("requests") or []:
        if request["status"] == "unavailable":
            parts.append(f'<circle cx="{x_of(request["requested_video_seconds"])}" cy="{y_of(Decimal(0))}" r="3" fill="none" stroke="#a142f4"/>')
    parts.append(f'<line x1="{x_of(end)}" y1="{top}" x2="{x_of(end)}" y2="{height - bottom}" stroke="#555" stroke-dasharray="3 3"/>')
    parts.append(f'<text x="{left}" y="{height - 8}" font-size="11" fill="#333">{md_escape(_num(start))} – {_num(end)} s</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _svg_tiles(document: dict[str, Any]) -> str:
    start = document["requested_range"]["video_start"]
    end = document["requested_range"]["video_end"]
    span = end - start
    if span <= 0:
        span = Decimal(1)
    width = 960
    height = 300
    left = 48
    right = 12
    top = 28
    bottom = 32
    plot_width = Decimal(width - left - right)
    plot_height = Decimal(height - top - bottom)
    differences = document.get("differences") or []
    scale_values = [item["tile_max"] for item in differences]
    scale_values.extend(item["peak_score"] for item in differences if item.get("peak_score") is not None)
    scale_values.extend(item["threshold"] for item in differences if item.get("threshold") is not None)
    peak = max(scale_values + [Decimal("0.01")])
    if peak <= 0:
        peak = Decimal(1)

    def x_of(seconds: Decimal) -> str:
        return format(left + (seconds - start) / span * plot_width, "f")

    def y_of(value: Decimal) -> str:
        return format(top + plot_height - (value / peak) * plot_height, "f")

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="48" y="16" font-size="12" fill="#222">타일 최대값(진단)과 통과 타일 점수. 전역 평균이나 하나의 공통 임계 곡선이 아니다.</text>',
    ]
    samples = document.get("samples") or []
    max_coords = []
    for item in differences:
        seconds = samples[item["after_sample"]]["video_seconds"]
        max_coords.append(f'{x_of(seconds)},{y_of(item["tile_max"])}')
    if max_coords:
        parts.append(f'<polyline fill="none" stroke="#9aa0a6" stroke-width="1" points="{" ".join(max_coords)}"/>')
    for item in differences:
        if item.get("peak_score") is None or item.get("threshold") is None:
            continue
        seconds = samples[item["after_sample"]]["video_seconds"]
        parts.append(f'<circle cx="{x_of(seconds)}" cy="{y_of(item["threshold"])}" r="3.5" fill="none" stroke="#555"/>')
    for item in differences:
        if item.get("peak_score") is None:
            continue
        seconds = samples[item["after_sample"]]["video_seconds"]
        action = item["peak_action"]
        if item.get("spatial_action") == "suppress":
            color = "#6a1b9a"
            radius = "4"
        else:
            color = {"keep": "#d93025", "suppress": "#f9ab00"}[action]
            radius = "4" if action == "keep" else "2.5"
        parts.append(f'<circle cx="{x_of(seconds)}" cy="{y_of(item["peak_score"])}" r="{radius}" fill="{color}"/>')
    for request in document.get("requests") or []:
        if request["status"] == "unavailable":
            parts.append(f'<circle cx="{x_of(request["requested_video_seconds"])}" cy="{y_of(Decimal(0))}" r="3" fill="none" stroke="#a142f4"/>')
    parts.append(f'<line x1="{x_of(end)}" y1="{top}" x2="{x_of(end)}" y2="{height - bottom}" stroke="#555" stroke-dasharray="3 3"/>')
    parts.append(f'<text x="{left}" y="{height - 8}" font-size="11" fill="#333">{md_escape(_num(start))} – {_num(end)} s</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _sample_table(document: dict[str, Any]) -> str:
    lines = ["| sample | roles | PTS | time_base | video_seconds | requests | error_seconds | sha256 |", "|---|---|---:|---|---:|---|---:|---|"]
    request_by_sample: dict[int, list[dict[str, Any]]] = {}
    for request in document.get("requests") or []:
        if request["sample_index"] is None:
            continue
        request_by_sample.setdefault(int(request["sample_index"]), []).append(request)
    for sample in document.get("samples") or []:
        base = sample["time_base"]
        errors = ", ".join(_num(item["error_seconds"]) for item in request_by_sample.get(int(sample["sample_index"]), []))
        requests = ",".join(str(item) for item in sample["request_indices"])
        lines.append(
            f"| {sample['sample_index']} | {','.join(sample['roles'])} | {sample['pts']} | {base['numerator']}/{base['denominator']} | {_num(sample['video_seconds'])} | {requests} | {errors} | `{sample['analysis_bytes_sha256'][:12]}` |"
        )
    unavailable = [request for request in document.get("requests") or [] if request["status"] == "unavailable"]
    if unavailable:
        lines.append("")
        lines.append("unavailable 요청:")
        for request in unavailable:
            lines.append(f"- {request['kind']} #{request['request_index']} requested {_num(request['requested_video_seconds'])}: {md_escape(request.get('reason') or '')}")
    return "\n".join(lines)


def _image_line(sample: dict[str, Any], repo_root: Path, markdown_path: Path) -> str:
    preview = sample.get("preview_ref")
    base = sample["time_base"]
    label = (
        f"PTS {sample['pts']} time_base {base['numerator']}/{base['denominator']} "
        f"video_seconds {_num(sample['video_seconds'])}"
    )
    if not preview:
        return f"- sample {sample['sample_index']}: 이미지 없음. {label}"
    target = (repo_root / preview["path"]).resolve()
    relative = os.path.relpath(target, markdown_path.parent.resolve())
    return f"![{md_escape(label)}]({Path(relative).as_posix()})\n\n{label}"


def write_candidate_page(document: dict[str, Any], path: Path, sentences: list[dict[str, Any]], repo_root: Path) -> None:
    lines = [f"# {document['candidate_id']}", "", _sample_table(document), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_svg(document: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_svg(document), encoding="utf-8")


def write_video_review(
    video_document: dict[str, Any],
    candidate_documents: dict[str, dict[str, Any]],
    sentences_by_id: dict[str, list[dict[str, Any]]],
    plans: dict[str, dict[str, Any]],
    *,
    path: Path,
    repo_root: Path,
    t2_review_link: str,
) -> None:
    lines = [
        f"# {video_document['video_id']} {video_document.get('title') or ''}",
        "",
        f"- T3 status: `{video_document['status']}`",
        f"- T2 status: `{video_document.get('source_t2_status')}`",
        f"- T2 validation.ok: `{video_document.get('source_t2_validation', {}).get('ok')}`",
        f"- [T2 검수]({t2_review_link})",
        "",
    ]
    note = KNOWN_LOSSES.get(video_document["video_id"])
    if note:
        lines.extend([note, ""])
    if video_document.get("source_t2_warnings"):
        lines.append("T2 metadata 경고는 후보를 자르지 않고 보존한다.")
        lines.append("")
        lines.append("```json")
        lines.append(dumps(video_document["source_t2_warnings"]).rstrip())
        lines.append("```")
        lines.append("")
    samples_root = repo_root
    for summary in video_document["candidates"]:
        cid = summary["candidate_id"]
        source = summary["source_candidate"]
        lines.append(f"## {cid}")
        lines.append("")
        lines.append(
            f"- unit {source['unit_index']} chunk {source['chunk_index']} "
            f"sentences {source['sentence_start']}–{source['sentence_end']}"
        )
        lines.append(
            f"- audio {_num(source['audio_start'])}–{_num(source['audio_end'])} "
            f"({source.get('audio_time_reference')})"
        )
        lines.append(
            f"- video {_num(source['video_start'])}–{_num(source['video_end'])} "
            f"({source.get('video_time_reference')})"
        )
        lines.append(
            f"- candidate_source `{source.get('candidate_source')}` "
            f"model_inference_success `{source.get('model_inference_success')}` "
            f"decision `{source.get('decision')}`"
        )
        if source.get("retention_reason"):
            lines.append(f"- retention_reason: {md_escape(str(source['retention_reason']))}")
        if source.get("source_call_ref"):
            lines.append(f"- source_call_ref: `{source['source_call_ref']}`")
        lines.append(f"- T3 status `{summary['status']}`")
        if summary["status"] == "out_of_scope":
            lines.append("")
            lines.append("이 subset 범위 밖이다. 검출하지 않았고 실패가 아니다.")
            lines.append("")
            continue
        if summary.get("problems"):
            lines.append(f"- problems: {md_escape(dumps(summary['problems']).rstrip())}")
        if summary.get("warnings"):
            lines.append(f"- warnings: {md_escape(dumps(summary['warnings']).rstrip())}")
        lines.append("")
        lines.append("원문:")
        lines.append("")
        for sentence in sentences_by_id.get(cid, []):
            lines.append(f"{sentence['index']}. {md_escape(sentence['text'])}")
        lines.append("")
        document = candidate_documents.get(cid)
        if document is None or summary["status"] == "pending":
            lines.append("아직 검출하지 않았다.")
            lines.append("")
            continue
        detail = f"candidates/u{source['unit_index']}_c{source['chunk_index']}.md"
        plot = f"plots/u{source['unit_index']}_c{source['chunk_index']}.svg"
        lines.append(f"[sample 표]({detail})")
        lines.append("")
        lines.append(f"![변화량]({plot})")
        lines.append("")
        plan = plans.get(cid) or preview_plan(document)
        sample_map = {int(sample["sample_index"]): sample for sample in document.get("samples") or []}
        lines.append("경계 전후:")
        lines.append("")
        if not plan["boundaries"]:
            lines.append("경계가 없다.")
            lines.append("")
        for boundary in document.get("boundaries") or []:
            lines.append(
                f"- boundary {boundary['boundary_index']}: "
                f"{_num(boundary['bracket_start'])}–{_num(boundary['bracket_end'])} "
                f"video_seconds {_num(boundary['video_seconds'])} "
                f"PTS before {sample_map[boundary['before_sample']]['pts']} / after {sample_map[boundary['after_sample']]['pts']}"
            )
            lines.append("")
            lines.append(_image_line(sample_map[boundary["before_sample"]], samples_root, path))
            lines.append("")
            lines.append(_image_line(sample_map[boundary["after_sample"]], samples_root, path))
            lines.append("")
        lines.append("시간열:")
        lines.append("")
        for index in plan["timeline"]:
            lines.append(_image_line(sample_map[index], samples_root, path))
            lines.append("")
        lines.append("구간:")
        lines.append("")
        for segment in document.get("segments") or []:
            lines.append(
                f"- segment {segment['segment_index']}: {_num(segment['video_start'])}–{_num(segment['video_end'])} "
                f"samples {segment['analysis_sample_indices']}"
            )
        for picked in plan["segments"]:
            if picked["empty"]:
                lines.append(f"- segment {picked['segment_index']}: 구간 안에 sample이 없다.")
                continue
            for index in picked["samples"]:
                lines.append(_image_line(sample_map[index], samples_root, path))
                lines.append("")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index(run_dir: Path, videos: list[dict[str, Any]], repo_root: Path) -> None:
    lines = ["# T3 screen changes", "", "품질 채택은 pending_review다. 구조 검증 통과는 품질 채택이 아니다.", ""]
    if any(summary["status"] == "out_of_scope" for video in videos for summary in video["candidates"]):
        lines.append("이 색인은 subset이다. out_of_scope는 실패가 아니고 139개 완료가 아니다.")
        lines.append("")
    lines.append("| video | candidates | ok | failed | pending | out_of_scope | segments | boundaries | no_boundary | over_100s | budget_guard | render |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    long_rows = []
    warnings = []
    for video in videos:
        documents = []
        ok = failed = pending = out_of_scope = segments = boundaries = quiet = long_count = guards = 0
        render_problems = []
        for summary in video["candidates"]:
            if summary["source_candidate"].get("candidate_source") == "budget_guard":
                guards += 1
            status = summary["status"]
            if status == "ok":
                ok += 1
            elif status == "failed":
                failed += 1
            elif status == "out_of_scope":
                out_of_scope += 1
            else:
                pending += 1
            if summary.get("result_ref"):
                path = repo_root / summary["result_ref"]
                if path.exists():
                    document = loads(path.read_text(encoding="utf-8"))
                    documents.append(document)
                    segments += len(document.get("segments") or [])
                    boundaries += len(document.get("boundaries") or [])
                    if status == "ok" and not document.get("boundaries"):
                        quiet += 1
                    for segment in document.get("segments") or []:
                        if segment["video_end"] - segment["video_start"] > Decimal(100):
                            long_count += 1
                            long_rows.append(
                                f"- {video['video_id']} {summary['candidate_id']} segment {segment['segment_index']} "
                                f"{_num(segment['video_start'])}–{_num(segment['video_end'])}"
                            )
                    for warning in document.get("warnings") or []:
                        warnings.append(f"- {summary['candidate_id']}: `{dumps(warning).rstrip()}`")
                    missing = [
                        sample["sample_index"]
                        for sample in document.get("samples") or []
                        if sample["sample_index"] in preview_plan(document)["render_samples"] and not sample.get("preview_ref")
                    ]
                    if status == "ok" and missing:
                        render_problems.append(str(missing))
        render = "failed" if render_problems else ("pending" if pending or failed else "ok")
        if out_of_scope and not ok and not failed and not pending and not render_problems:
            render = "out_of_scope"
        if pending and ok and not render_problems:
            render = "partial"
        link = f"{video['video_id']}/review.md"
        lines.append(
            f"| [{video['video_id']}]({link}) | {len(video['candidates'])} | {ok} | {failed} | {pending} | {out_of_scope} | {segments} | {boundaries} | {quiet} | {long_count} | {guards} | {render} |"
        )
    lines.extend(["", "## 100초 초과 구간", ""])
    lines.extend(long_rows or ["없다."])
    lines.extend(["", "## sample_gap·unavailable·경고", ""])
    lines.extend(warnings or ["없다."])
    lines.extend([
        "",
        "## T2에서 수용한 후보 밖 손실",
        "",
        "- hGVL9XRU9IQ 단위 0 대시보드",
        "- iEMuJiVY9Oc 단위 0 판서, 단위 13 끝 판서",
        "- 7EJ-xotXiSE 단위 21 검색필터 목록",
        "",
    ])
    (run_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_frame_hashes(video_dir: Path, documents: list[dict[str, Any]], repo_root: Path) -> None:
    rows = []
    for document in documents:
        for sample in document.get("samples") or []:
            preview = sample.get("preview_ref")
            if not preview:
                continue
            rows.append({
                "candidate_id": document["candidate_id"],
                "sample_index": sample["sample_index"],
                "pts": sample["pts"],
                "time_base": sample["time_base"],
                "video_seconds": sample["video_seconds"],
                "path": preview["path"],
                "sha256": preview["sha256"],
            })
    destination = video_dir / "frames" / "hashes.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(dumps(rows), encoding="utf-8")


OBSERVED_PTS = {
    "iEMuJiVY9Oc:u12:c0": [9538048, 9975808, 10436608, 10897408, 11273728, 11281408],
    "iEMuJiVY9Oc:u20:c0": [19125248, 19309568, 19432448, 19494400],
    "bjDkfjSmXVo:u12:c0": [26474448, 27014988, 29429400, 31514483],
}
H2_BOUNDARY_SECONDS = (Decimal("627.561"), Decimal("630.563"), Decimal("666.066"))


def _segment_at(document: dict[str, Any], seconds: Decimal) -> dict[str, Any] | None:
    for segment in document.get("segments") or []:
        if segment["video_start"] <= seconds < segment["video_end"]:
            return segment
    return None


def _sample_by_pts(document: dict[str, Any], pts: int) -> dict[str, Any] | None:
    for sample in document.get("samples") or []:
        if int(sample["pts"]) == int(pts):
            return sample
    return None


def _preview_path(document: dict[str, Any], pts: int) -> str | None:
    sample = _sample_by_pts(document, pts)
    preview = None if sample is None else sample.get("preview_ref")
    if not preview:
        return None
    return str(preview["path"])


def _link_from(markdown_path: Path, repo_root: Path, relative_path: str) -> str:
    target = (repo_root / relative_path).resolve()
    return Path(os.path.relpath(target, markdown_path.parent.resolve())).as_posix()


def _ensure_compare_frame(
    *,
    pts: int,
    document: dict[str, Any],
    baseline: dict[str, Any],
    markdown_path: Path,
    frames_dir: Path,
    repo_root: Path,
    ffmpeg: str,
    video: Path,
    cache: dict[int, str],
) -> str:
    if pts in cache:
        return cache[pts]
    for source in (document, baseline):
        found = _preview_path(source, pts)
        if found and (repo_root / found).exists():
            cache[pts] = found
            return found
    sample = _sample_by_pts(document, pts) or _sample_by_pts(baseline, pts)
    if sample is not None:
        seconds = sample["video_seconds"]
        time_base = sample["time_base"]
    else:
        time_base = (document.get("samples") or baseline.get("samples") or [{"time_base": {"numerator": 1, "denominator": 1}}])[0]["time_base"]
        seconds = Decimal(int(pts)) * Decimal(time_base["numerator"]) / Decimal(time_base["denominator"])
    seek = seconds - Decimal(1)
    if seek < 0:
        seek = Decimal(0)
    destination = frames_dir / f"{document['candidate_id'].split(':')[0]}_pts{pts}.jpg"
    saved = extract_jpeg(ffmpeg, video, int(pts), seek, destination)
    relative = destination.resolve().relative_to(repo_root.resolve()).as_posix()
    del saved
    cache[pts] = relative
    return relative


def _boundary_lines(document: dict[str, Any]) -> list[str]:
    samples = {int(sample["sample_index"]): sample for sample in document.get("samples") or []}
    if not document.get("boundaries"):
        return ["경계가 없다."]
    lines = []
    for boundary in document["boundaries"]:
        before = samples[int(boundary["before_sample"])]
        after = samples[int(boundary["after_sample"])]
        lines.append(
            f"- {boundary['boundary_index']}: {_num(boundary['video_seconds'])} "
            f"PTS {before['pts']} → {after['pts']}"
        )
    return lines


def write_b00_comparison(
    *,
    run_dir: Path,
    baseline_dir: Path,
    manifest: dict[str, Any],
    ffmpeg: str,
    repo_root: Path,
) -> None:
    """B00과 B01의 같은 PTS·경계 전후를 나란히 적는다. 경계 수를 점수로 만들지 않는다."""
    compare_dir = run_dir / "compare"
    frames_dir = compare_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = compare_dir / "b00.md"
    lines = [
        "# B00과 B01 비교",
        "",
        "경계 개수는 품질 점수가 아니다. 같은 시각의 이미지와 한쪽만의 경계 전후를 보고 판단한다.",
        "quality_status는 pending_review다.",
        "",
    ]
    subset = [item for item in (manifest.get("subset") or {}).get("candidate_ids") or []]
    plans = {
        plan["candidate_id"]: (video, plan)
        for video in manifest["videos"]
        for plan in video["candidates"]
    }
    for candidate_id in subset:
        video, plan = plans[candidate_id]
        current_path = run_dir / video["video_id"] / "candidates" / f"u{plan['unit_index']}_c{plan['chunk_index']}.json"
        baseline_path = baseline_dir / video["video_id"] / "candidates" / f"u{plan['unit_index']}_c{plan['chunk_index']}.json"
        document = loads(current_path.read_text(encoding="utf-8"))
        baseline = loads(baseline_path.read_text(encoding="utf-8"))
        lines.append(f"## {candidate_id}")
        lines.append("")
        lines.append(f"- 범위 {_num(document['requested_range']['video_start'])}–{_num(document['requested_range']['video_end'])}")
        lines.append(f"- B00 샘플 {len(baseline.get('samples') or [])} / B01 샘플 {len(document.get('samples') or [])}")
        same_samples = len(document.get("samples") or []) == len(baseline.get("samples") or [])
        if same_samples:
            for new, old in zip(document["samples"], baseline["samples"]):
                if (
                    int(new["pts"]) != int(old["pts"])
                    or new["time_base"] != old["time_base"]
                    or int(new["width"]) != int(old["width"])
                    or int(new["height"]) != int(old["height"])
                    or new.get("pixel_format") != old.get("pixel_format")
                    or new.get("analysis_bytes_sha256") != old.get("analysis_bytes_sha256")
                ):
                    same_samples = False
                    break
        lines.append(f"- 샘플 PTS·time_base·크기·pixel_format·analysis_bytes_sha256 일치: `{same_samples}`")
        lines.append("")
        lines.append("B00 경계:")
        lines.append("")
        lines.extend(_boundary_lines(baseline))
        lines.append("")
        lines.append("B01 경계:")
        lines.append("")
        lines.extend(_boundary_lines(document))
        lines.append("")
        lines.append("확인할 질문: 판서 추가·삭제가 나뉘는가. 자막·인물·커서 분할이 늘었는가. 그래프 등장·소멸이 남는가. 서로 다른 정보 상태가 한 구간으로 합쳐졌는가.")
        lines.append("")
        cache: dict[int, str] = {}
        media = repo_root / video["media"]["path"]
        wanted: list[tuple[str, int]] = []
        for pts in OBSERVED_PTS.get(candidate_id, []):
            wanted.append((f"관측 PTS {pts}", pts))
        if candidate_id == "H2WN130OzLg:u7:c0":
            for seconds in H2_BOUNDARY_SECONDS:
                boundary = next((item for item in baseline.get("boundaries") or [] if item["video_seconds"] == seconds), None)
                if boundary is None:
                    lines.append(f"B00에서 {_num(seconds)}초 경계를 찾지 못했다.")
                    lines.append("")
                    continue
                before = baseline["samples"][boundary["before_sample"]]
                after = baseline["samples"][boundary["after_sample"]]
                wanted.append((f"H2 {_num(seconds)} 전 PTS {before['pts']}", int(before["pts"])))
                wanted.append((f"H2 {_num(seconds)} 후 PTS {after['pts']}", int(after["pts"])))
        if candidate_id == "Fw-8hZhnH3Q:u0:c0":
            for index in preview_plan(document)["timeline"]:
                sample = document["samples"][index]
                wanted.append((f"Fw 시간열 sample {index}", int(sample["pts"])))
        b00_times = {item["video_seconds"] for item in baseline.get("boundaries") or []}
        b01_times = {item["video_seconds"] for item in document.get("boundaries") or []}
        for label, source in (("B00에만", baseline), ("B01에만", document)):
            other = b01_times if label == "B00에만" else b00_times
            for boundary in source.get("boundaries") or []:
                if boundary["video_seconds"] in other:
                    continue
                before = source["samples"][boundary["before_sample"]]
                after = source["samples"][boundary["after_sample"]]
                wanted.append((f"{label} {_num(boundary['video_seconds'])} 전", int(before["pts"])))
                wanted.append((f"{label} {_num(boundary['video_seconds'])} 후", int(after["pts"])))
        seen_labels = set()
        for label, pts in wanted:
            key = (label, pts)
            if key in seen_labels:
                continue
            seen_labels.add(key)
            lines.append(f"### {label}")
            lines.append("")
            try:
                relative = _ensure_compare_frame(
                    pts=pts,
                    document=document,
                    baseline=baseline,
                    markdown_path=markdown_path,
                    frames_dir=frames_dir,
                    repo_root=repo_root,
                    ffmpeg=ffmpeg,
                    video=media,
                    cache=cache,
                )
            except T3Error as exc:
                lines.append(f"PTS {pts} 이미지를 만들지 못했다: {md_escape(str(exc))}")
                lines.append("")
                continue
            sample = _sample_by_pts(document, pts) or _sample_by_pts(baseline, pts)
            if sample is not None:
                seconds = sample["video_seconds"]
                base = sample["time_base"]
                lines.append(
                    f"PTS {pts} time_base {base['numerator']}/{base['denominator']} video_seconds {_num(seconds)}"
                )
                for name, side in (("B00", baseline), ("B01", document)):
                    segment = _segment_at(side, seconds)
                    if segment is None:
                        lines.append(f"- {name}: 이 시각을 포함한 구간이 없다.")
                    else:
                        lines.append(
                            f"- {name} segment {segment['segment_index']}: "
                            f"{_num(segment['video_start'])}–{_num(segment['video_end'])}"
                        )
            else:
                lines.append(f"PTS {pts}는 분석 샘플이 아니다. 검수 이미지만 원본에서 뽑았다.")
            lines.append("")
            lines.append(f"![PTS {pts}]({_link_from(markdown_path, repo_root, relative)})")
            lines.append("")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b01_comparison(
    *,
    run_dir: Path,
    baseline_dir: Path,
    manifest: dict[str, Any],
    ffmpeg: str,
    repo_root: Path,
) -> None:
    """B01 경계마다 B02 유지·억제와 이유를 적는다. 경계 수는 점수가 아니다."""
    del ffmpeg
    compare_dir = run_dir / "compare"
    compare_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = compare_dir / "b01.md"
    b00_root = repo_root / str((manifest.get("comparison_baseline") or {}).get("b00", {}).get("path") or "")
    lines = [
        "# B01과 B02 비교",
        "",
        "경계 개수는 품질 점수가 아니다. B02는 B01이 남긴 경계만 1.0초와 2.0초 시점에서 되돌아왔는지 확인한다.",
        "quality_status는 pending_review다.",
        "",
    ]
    subset = [item for item in (manifest.get("subset") or {}).get("candidate_ids") or []]
    plans = {
        plan["candidate_id"]: (video, plan)
        for video in manifest["videos"]
        for plan in video["candidates"]
    }
    for candidate_id in subset:
        video, plan = plans[candidate_id]
        name = f"u{plan['unit_index']}_c{plan['chunk_index']}.json"
        document = loads((run_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        baseline = loads((baseline_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        b00_document = None
        b00_path = b00_root / video["video_id"] / "candidates" / name
        if b00_path.exists():
            b00_document = loads(b00_path.read_text(encoding="utf-8"))
        lines.append(f"## {candidate_id}")
        lines.append("")
        lines.append(f"- 범위 {_num(document['requested_range']['video_start'])}–{_num(document['requested_range']['video_end'])}")
        lines.append(f"- B01 샘플 {len(baseline.get('samples') or [])} / B02 샘플 {len(document.get('samples') or [])}")
        same_samples = len(document.get("samples") or []) == len(baseline.get("samples") or [])
        if same_samples:
            for new, old in zip(document["samples"], baseline["samples"]):
                if (
                    int(new["pts"]) != int(old["pts"])
                    or new["time_base"] != old["time_base"]
                    or int(new["width"]) != int(old["width"])
                    or int(new["height"]) != int(old["height"])
                    or new.get("pixel_format") != old.get("pixel_format")
                    or new.get("analysis_bytes_sha256") != old.get("analysis_bytes_sha256")
                ):
                    same_samples = False
                    break
        lines.append(f"- 샘플 PTS·time_base·크기·pixel_format·analysis_bytes_sha256 일치: `{same_samples}`")
        b01_times = [item["video_seconds"] for item in baseline.get("boundaries") or []]
        b02_times = [item["video_seconds"] for item in document.get("boundaries") or []]
        b00_times = {item["video_seconds"] for item in (b00_document or {}).get("boundaries") or []}
        lines.append(f"- B01 경계 {len(b01_times)} / B02 경계 {len(b02_times)}. 이 수는 점수가 아니다.")
        extra = [item for item in b02_times if item not in set(b01_times)]
        lines.append(f"- B02에만 있는 경계 시각: {', '.join(_num(item) for item in extra) if extra else '없음'}")
        lines.append("")
        by_index = {int(item["index"]): item for item in document.get("differences") or []}
        samples = {int(sample["sample_index"]): sample for sample in document.get("samples") or []}
        for boundary in baseline.get("boundaries") or []:
            difference = by_index.get(int(boundary["difference_index"]))
            before = samples.get(int(boundary["before_sample"]))
            after = samples.get(int(boundary["after_sample"]))
            side = "공통" if boundary["video_seconds"] in b00_times else "B01에만"
            lines.append(f"### {_num(boundary['video_seconds'])}초 / {side}")
            lines.append("")
            if before is None or after is None or difference is None:
                lines.append("- B02 차분 또는 샘플을 찾지 못했다.")
                lines.append("")
                continue
            lines.append(
                f"- 전후 PTS {before['pts']} → {after['pts']} "
                f"time_base {before['time_base']['numerator']}/{before['time_base']['denominator']}"
            )
            lines.append(f"- temporal_action `{difference.get('temporal_action')}` / temporal_reason `{difference.get('temporal_reason')}`")
            temporal = difference.get("temporal") or {}
            for comparison in temporal.get("comparisons") or []:
                sample_index = comparison.get("sample_index")
                compared = None if sample_index is None else samples.get(int(sample_index))
                pts = "없음" if compared is None else str(compared["pts"])
                lines.append(
                    f"- offset {_num(comparison['offset_seconds'])}초 status `{comparison.get('status')}` "
                    f"sample {sample_index} PTS {pts} lateness {_num(comparison.get('lateness_seconds')) if comparison.get('lateness_seconds') is not None else '없음'}"
                )
            kept = boundary["video_seconds"] in set(b02_times)
            lines.append(f"- B02 경계 유지: `{kept}`")
            lines.append("")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b03_comparison(
    *,
    run_dir: Path,
    baseline_dir: Path,
    manifest: dict[str, Any],
    ffmpeg: str,
    repo_root: Path,
) -> None:
    """B01 경계마다 B03 유지·억제와 공간 이동 근거를 적는다. 경계 수는 점수가 아니다."""
    del ffmpeg
    compare_dir = run_dir / "compare"
    compare_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = compare_dir / "b01.md"
    baseline_meta = manifest.get("comparison_baseline") or {}
    lines = [
        "# B01과 B03 비교",
        "",
        "경계 개수는 품질 점수가 아니다. B03은 B01이 남긴 경계의 앞뒤 분석 프레임에서 변화 성분의 평행이동만 확인한다.",
        "전역 평균이나 타일 평균이 작다는 이유만으로 억제하지 않는다.",
        "quality_status는 pending_review다.",
        "",
        f"- B01 `{baseline_meta.get('path')}` `{baseline_meta.get('manifest_sha256')}`",
        f"- B02 `{(baseline_meta.get('b02') or {}).get('path')}` `{(baseline_meta.get('b02') or {}).get('manifest_sha256')}`",
        f"- B00 `{(baseline_meta.get('b00') or {}).get('path')}` `{(baseline_meta.get('b00') or {}).get('manifest_sha256')}`",
        "",
    ]
    subset = [item for item in (manifest.get("subset") or {}).get("candidate_ids") or []]
    plans = {
        plan["candidate_id"]: (video, plan)
        for video in manifest["videos"]
        for plan in video["candidates"]
    }
    for candidate_id in subset:
        video, plan = plans[candidate_id]
        name = f"u{plan['unit_index']}_c{plan['chunk_index']}.json"
        document = loads((run_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        baseline = loads((baseline_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        lines.append(f"## {candidate_id}")
        lines.append("")
        lines.append(f"- 범위 {_num(document['requested_range']['video_start'])}–{_num(document['requested_range']['video_end'])}")
        lines.append(f"- B01 샘플 {len(baseline.get('samples') or [])} / B03 샘플 {len(document.get('samples') or [])}")
        same_samples = len(document.get("samples") or []) == len(baseline.get("samples") or [])
        if same_samples:
            for new, old in zip(document["samples"], baseline["samples"]):
                if (
                    int(new["pts"]) != int(old["pts"])
                    or new["time_base"] != old["time_base"]
                    or int(new["width"]) != int(old["width"])
                    or int(new["height"]) != int(old["height"])
                    or new.get("pixel_format") != old.get("pixel_format")
                    or new.get("analysis_bytes_sha256") != old.get("analysis_bytes_sha256")
                ):
                    same_samples = False
                    break
        lines.append(f"- 샘플 PTS·time_base·크기·pixel_format·analysis_bytes_sha256 일치: `{same_samples}`")
        b01_times = [item["video_seconds"] for item in baseline.get("boundaries") or []]
        b03_times = [item["video_seconds"] for item in document.get("boundaries") or []]
        lines.append(f"- B01 경계 {len(b01_times)} / B03 경계 {len(b03_times)}. 이 수는 점수가 아니다.")
        extra = [item for item in b03_times if item not in set(b01_times)]
        lines.append(f"- B03에만 있는 경계 시각: {', '.join(_num(item) for item in extra) if extra else '없음'}")
        suppressed = [item for item in b01_times if item not in set(b03_times)]
        lines.append(f"- B03이 억제한 B01 경계: {len(suppressed)}")
        lines.append("")
        by_index = {int(item["index"]): item for item in document.get("differences") or []}
        samples = {int(sample["sample_index"]): sample for sample in document.get("samples") or []}
        for boundary in baseline.get("boundaries") or []:
            difference = by_index.get(int(boundary["difference_index"]))
            before = samples.get(int(boundary["before_sample"]))
            after = samples.get(int(boundary["after_sample"]))
            lines.append(f"### {_num(boundary['video_seconds'])}초")
            lines.append("")
            if before is None or after is None or difference is None:
                lines.append("- B03 차분 또는 샘플을 찾지 못했다.")
                lines.append("")
                continue
            spatial = difference.get("spatial") or {}
            shift = spatial.get("shift")
            lines.append(
                f"- 전후 PTS {before['pts']} → {after['pts']} "
                f"time_base {before['time_base']['numerator']}/{before['time_base']['denominator']} "
                f"video_seconds {_num(before['video_seconds'])} → {_num(after['video_seconds'])}"
            )
            lines.append(f"- spatial_action `{difference.get('spatial_action')}` / spatial_reason `{difference.get('spatial_reason')}`")
            lines.append(
                f"- 변경 화소 {spatial.get('change_pixel_count')} / 성분 {spatial.get('component_count')} "
                f"/ 형태 이동 {spatial.get('shape_shift_count')} / 설명 이동 {spatial.get('explained_shift_count')}"
            )
            if shift is None:
                lines.append("- 통과한 이동 없음")
            else:
                lines.append(
                    f"- 이동 dx {shift.get('dx')} dy {shift.get('dy')} "
                    f"성분 {shift.get('source_component')}→{shift.get('destination_component')} "
                    f"source {shift.get('source_count')} explained {shift.get('explained_count')} residual {shift.get('residual_count')}"
                )
            lines.append(f"- B03 경계 유지: `{boundary['video_seconds'] in set(b03_times)}`")
            lines.append("")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b04_comparison(
    *,
    run_dir: Path,
    baseline_dir: Path,
    manifest: dict[str, Any],
    ffmpeg: str,
    repo_root: Path,
) -> None:
    """B01과 B04의 경계 차이와 억제 근거를 적는다. 경계 수는 점수가 아니다."""
    del ffmpeg, repo_root
    compare_dir = run_dir / "compare"
    compare_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = compare_dir / "b01.md"
    baseline_meta = manifest.get("comparison_baseline") or {}
    lines = [
        "# B01과 B04 비교",
        "",
        "경계 개수는 품질 점수가 아니다. B04는 B01 타일 점수와 실제 시각 0.5초 비교를 유지하고, 요청 격자가 정확히 한 단계인 쌍만 추가로 비교한다.",
        "quality_status는 pending_review다.",
        "",
        f"- B01 `{baseline_meta.get('path')}` `{baseline_meta.get('manifest_sha256')}`",
        f"- B00 `{(baseline_meta.get('b00') or {}).get('path')}` `{(baseline_meta.get('b00') or {}).get('manifest_sha256')}`",
        "",
    ]
    subset = [item for item in (manifest.get("subset") or {}).get("candidate_ids") or []]
    plans = {
        plan["candidate_id"]: (video, plan)
        for video in manifest["videos"]
        for plan in video["candidates"]
    }
    for candidate_id in subset:
        video, plan = plans[candidate_id]
        name = f"u{plan['unit_index']}_c{plan['chunk_index']}.json"
        document = loads((run_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        baseline = loads((baseline_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        lines.append(f"## {candidate_id}")
        lines.append("")
        lines.append(f"- 범위 {_num(document['requested_range']['video_start'])}–{_num(document['requested_range']['video_end'])}")
        lines.append(f"- B01 샘플 {len(baseline.get('samples') or [])} / B04 샘플 {len(document.get('samples') or [])}")
        same_samples = len(document.get("samples") or []) == len(baseline.get("samples") or [])
        if same_samples:
            for new, old in zip(document["samples"], baseline["samples"]):
                if (
                    int(new["pts"]) != int(old["pts"])
                    or new["time_base"] != old["time_base"]
                    or int(new["width"]) != int(old["width"])
                    or int(new["height"]) != int(old["height"])
                    or new.get("pixel_format") != old.get("pixel_format")
                    or new.get("analysis_bytes_sha256") != old.get("analysis_bytes_sha256")
                ):
                    same_samples = False
                    break
        lines.append(f"- 샘플 PTS·time_base·크기·pixel_format·analysis_bytes_sha256 일치: `{same_samples}`")
        b01_times = [item["video_seconds"] for item in baseline.get("boundaries") or []]
        b04_times = [item["video_seconds"] for item in document.get("boundaries") or []]
        lines.append(f"- B01 경계 {len(b01_times)} / B04 경계 {len(b04_times)}. 이 수는 점수가 아니다.")
        only_b01 = [item for item in b01_times if item not in set(b04_times)]
        only_b04 = [item for item in b04_times if item not in set(b01_times)]
        lines.append(f"- B01에만 있는 경계: {', '.join(_num(item) for item in only_b01) if only_b01 else '없음'}")
        lines.append(f"- B04에만 있는 경계: {', '.join(_num(item) for item in only_b04) if only_b04 else '없음'}")
        grid_step = 0
        actual_time = 0
        for difference in document.get("differences") or []:
            for ref in difference.get("suppression_refs") or []:
                if ref.get("neighbor_basis") == "grid_step":
                    grid_step += 1
                elif ref.get("neighbor_basis") == "actual_time":
                    actual_time += 1
        lines.append(f"- 억제 근거 grid_step {grid_step} / actual_time {actual_time}")
        samples = {int(sample["sample_index"]): sample for sample in document.get("samples") or []}
        if only_b01 or only_b04:
            lines.append("- 바뀐 경계의 앞뒤 샘플:")
            changed = set(only_b01) | set(only_b04)
            for boundary in list(baseline.get("boundaries") or []) + list(document.get("boundaries") or []):
                if boundary["video_seconds"] not in changed:
                    continue
                before = samples.get(int(boundary["before_sample"]))
                after = samples.get(int(boundary["after_sample"]))
                before_path = ((before or {}).get("preview_ref") or {}).get("path")
                after_path = ((after or {}).get("preview_ref") or {}).get("path")
                lines.append(
                    f"  - {_num(boundary['video_seconds'])}초 "
                    f"PTS {None if before is None else before.get('pts')} → {None if after is None else after.get('pts')} "
                    f"`{before_path}` → `{after_path}`"
                )
        lines.append("")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b05_comparison(
    *,
    run_dir: Path,
    b01_dir: Path,
    b04_dir: Path,
    manifest: dict[str, Any],
    ffmpeg: str,
    repo_root: Path,
) -> list[str]:
    """B05와 B04·B01의 경계 시각 집합을 적는다. 경계 수는 점수가 아니다."""
    del ffmpeg, repo_root
    compare_dir = run_dir / "compare"
    compare_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = compare_dir / "b04.md"
    baseline_meta = manifest.get("comparison_baseline") or {}
    b04_meta = baseline_meta.get("b04") or {}
    problems: list[str] = []
    lines = [
        "# B01·B04·B05 비교",
        "",
        "경계 개수는 품질 점수가 아니다. B05는 B04의 grid_step 억제만 바꾼다.",
        "최종 유지된 격자 이웃의 통과 타일이 후보의 통과 타일을 모두 포함할 때만 억제한다.",
        "actual_time 비교, 타일 크기, 임계값은 B04와 같다.",
        "quality_status는 pending_review다.",
        "",
        f"- B01 `{baseline_meta.get('path')}` `{baseline_meta.get('manifest_sha256')}`",
        f"- B04 `{b04_meta.get('path')}` `{b04_meta.get('manifest_sha256')}`",
        f"- B00 `{(baseline_meta.get('b00') or {}).get('path')}` `{(baseline_meta.get('b00') or {}).get('manifest_sha256')}`",
        "",
    ]
    unchanged_ids = {
        "Fw-8hZhnH3Q:u0:c0",
        "iEMuJiVY9Oc:u4:c0",
        "iEMuJiVY9Oc:u12:c0",
        "iEMuJiVY9Oc:u20:c0",
    }
    required_keep = {
        "7EJ-xotXiSE:u0:c0": Decimal("3.603600000"),
        "bjDkfjSmXVo:u12:c0": Decimal("989.488500000"),
    }
    absent_card = ("H2WN130OzLg:u25:c0", Decimal("1247.913000000"))
    prepaid_id = "iEMuJiVY9Oc:u4:c0"
    prepaid_start = Decimal("194.800000000")
    prepaid_end = Decimal("197.300000000")
    subset = [item for item in (manifest.get("subset") or {}).get("candidate_ids") or []]
    plans = {
        plan["candidate_id"]: (video, plan)
        for video in manifest["videos"]
        for plan in video["candidates"]
    }
    for candidate_id in subset:
        video, plan = plans[candidate_id]
        name = f"u{plan['unit_index']}_c{plan['chunk_index']}.json"
        document = loads((run_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        b01_document = loads((b01_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        b04_document = loads((b04_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        lines.append(f"## {candidate_id}")
        lines.append("")
        same_samples = len(document.get("samples") or []) == len(b01_document.get("samples") or [])
        if same_samples:
            for new, old in zip(document["samples"], b01_document["samples"]):
                if (
                    int(new["pts"]) != int(old["pts"])
                    or new["time_base"] != old["time_base"]
                    or int(new["width"]) != int(old["width"])
                    or int(new["height"]) != int(old["height"])
                    or new.get("pixel_format") != old.get("pixel_format")
                    or new.get("analysis_bytes_sha256") != old.get("analysis_bytes_sha256")
                ):
                    same_samples = False
                    break
        lines.append(f"- 샘플 PTS·time_base·크기·pixel_format·analysis_bytes_sha256 일치: `{same_samples}`")
        if not same_samples:
            problems.append(f"{candidate_id} 샘플 계약이 B01과 다릅니다.")
        b01_times = {item["video_seconds"] for item in b01_document.get("boundaries") or []}
        b04_times = {item["video_seconds"] for item in b04_document.get("boundaries") or []}
        b05_times = {item["video_seconds"] for item in document.get("boundaries") or []}
        lost_b04 = sorted(b04_times - b05_times)
        extra_b01 = sorted(b05_times - b01_times)
        restored = sorted((b01_times - b04_times) & b05_times)
        still_removed = sorted(b01_times - b05_times)
        lines.append(
            f"- B01 경계 {len(b01_times)} / B04 경계 {len(b04_times)} / B05 경계 {len(b05_times)}. 이 수는 점수가 아니다."
        )
        lines.append(f"- B04 경계가 B05에 포함: `{not lost_b04}`")
        lines.append(f"- B05 경계가 B01에 포함: `{not extra_b01}`")
        lines.append(f"- B04에서 복원 {len(restored)} / B01 대비 계속 없음 {len(still_removed)}. 이 수는 점수가 아니다.")
        if lost_b04:
            problems.append(f"{candidate_id}에서 B04 경계가 사라졌습니다.")
            lines.append(f"- 사라진 B04 경계: {', '.join(_num(item) for item in lost_b04)}")
        if extra_b01:
            problems.append(f"{candidate_id}에서 B01에 없는 경계가 생겼습니다.")
            lines.append(f"- B01에 없는 경계: {', '.join(_num(item) for item in extra_b01)}")
        if candidate_id in unchanged_ids and b05_times != b01_times:
            problems.append(f"{candidate_id} 경계 시각 집합이 B01과 다릅니다.")
        if candidate_id in required_keep and required_keep[candidate_id] not in b05_times:
            problems.append(f"{candidate_id} {_num(required_keep[candidate_id])}초 경계가 없습니다.")
        if candidate_id == absent_card[0] and absent_card[1] in b05_times:
            problems.append(f"{candidate_id} {_num(absent_card[1])}초가 경계가 되었습니다.")
        if candidate_id == prepaid_id:
            prepaid = [item for item in b05_times if prepaid_start <= item <= prepaid_end]
            lines.append(
                f"- {_num(prepaid_start)}–{_num(prepaid_end)}초 경계: "
                + (", ".join(_num(item) for item in sorted(prepaid)) if prepaid else "없음")
            )
            if prepaid:
                problems.append(f"{candidate_id} 선불 구간에 경계가 있습니다.")
        grid_step = 0
        actual_time = 0
        for difference in document.get("differences") or []:
            for ref in difference.get("suppression_refs") or []:
                if ref.get("neighbor_basis") == "grid_step":
                    grid_step += 1
                elif ref.get("neighbor_basis") == "actual_time":
                    actual_time += 1
        lines.append(f"- 억제 근거 grid_step {grid_step} / actual_time {actual_time}")
        if restored:
            lines.append(f"- 복원 경계: {', '.join(_num(item) for item in restored)}")
        lines.append("")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return problems


def write_b06_comparison(
    *,
    run_dir: Path,
    b01_dir: Path,
    b05_dir: Path,
    manifest: dict[str, Any],
    ffmpeg: str,
    repo_root: Path,
) -> list[str]:
    """B05 keep가 B06에 남는지와 샘플 계약을 적는다. 추가된 내용 경계는 실패가 아니다."""
    del ffmpeg, repo_root
    compare_dir = run_dir / "compare"
    compare_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = compare_dir / "b05.md"
    baseline_meta = manifest.get("comparison_baseline") or {}
    b05_meta = baseline_meta.get("b05") or {}
    problems: list[str] = []
    lines = [
        "# B01·B05·B06 비교",
        "",
        "경계 개수는 품질 점수가 아니다. B06은 B05 keep를 유지하고, 기준 잔차가 두 샘플 남으면 그 시각을 추가하거나 근거를 붙인다.",
        "추가된 시각을 B05에 없다는 이유로 실패로 세지 않는다. quality_status는 pending_review다.",
        "",
        f"- B01 `{baseline_meta.get('path')}` `{baseline_meta.get('manifest_sha256')}`",
        f"- B05 `{b05_meta.get('path')}` `{b05_meta.get('manifest_sha256')}`",
        "",
    ]
    subset = [item for item in (manifest.get("subset") or {}).get("candidate_ids") or []]
    plans = {
        plan["candidate_id"]: (video, plan)
        for video in manifest["videos"]
        for plan in video["candidates"]
    }
    for candidate_id in subset:
        video, plan = plans[candidate_id]
        name = f"u{plan['unit_index']}_c{plan['chunk_index']}.json"
        document = loads((run_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        b01_document = loads((b01_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        b05_document = loads((b05_dir / video["video_id"] / "candidates" / name).read_text(encoding="utf-8"))
        lines.append(f"## {candidate_id}")
        lines.append("")
        same_samples = len(document.get("samples") or []) == len(b01_document.get("samples") or [])
        if same_samples:
            for new, old in zip(document["samples"], b01_document["samples"]):
                if (
                    int(new["pts"]) != int(old["pts"])
                    or new["time_base"] != old["time_base"]
                    or int(new["width"]) != int(old["width"])
                    or int(new["height"]) != int(old["height"])
                    or new.get("pixel_format") != old.get("pixel_format")
                    or new.get("analysis_bytes_sha256") != old.get("analysis_bytes_sha256")
                ):
                    same_samples = False
                    break
        lines.append(f"- 샘플 PTS·time_base·크기·pixel_format·analysis_bytes_sha256 일치: `{same_samples}`")
        if not same_samples:
            problems.append(f"{candidate_id} 샘플 계약이 B01과 다릅니다.")
        b05_times = [item["video_seconds"] for item in b05_document.get("boundaries") or []]
        b06_rows = list(document.get("boundaries") or [])
        b06_times = [item["video_seconds"] for item in b06_rows]
        missing = [item for item in b05_times if item not in b06_times]
        lines.append(f"- B05 경계 {len(b05_times)} / B06 경계 {len(b06_times)}. 이 수는 점수가 아니다.")
        lines.append(f"- B05 경계가 B06에 포함: `{not missing}`")
        if missing:
            problems.append(f"{candidate_id}에서 B05 경계가 사라졌습니다.")
            lines.append(f"- 사라진 B05 경계: {', '.join(_num(item) for item in missing)}")
        by_time = {item["video_seconds"]: item for item in b06_rows}
        added = [item for item in b06_rows if item["video_seconds"] not in set(b05_times)]
        lines.append(f"- B05에 없던 경계 {len(added)}. 이 수는 점수가 아니다.")
        for item in added:
            evidence = item.get("content_evidence") or {}
            diff = next(
                (row for row in document.get("differences") or [] if int(row["index"]) == int(item["difference_index"])),
                {},
            )
            lines.append(
                f"- 추가 {_num(item['video_seconds'])}초 origin `{item.get('origin')}` "
                f"content `{evidence.get('content_action')}` peak `{diff.get('peak_action')}`"
            )
        attached = [
            item for item in b06_rows
            if item.get("origin") == "b05_keep_with_content"
        ]
        lines.append(f"- 기존 시각에 내용 근거를 붙인 경계 {len(attached)}")
        for item in attached:
            evidence = item.get("content_evidence") or {}
            lines.append(
                f"- 부착 {_num(item['video_seconds'])}초 content `{evidence.get('content_action')}`"
            )
        counts: dict[str, int] = {}
        for row in document.get("differences") or []:
            action = str(row.get("content_action") or "missing")
            counts[action] = counts.get(action, 0) + 1
        lines.append("- 내용 판정 수: " + ", ".join(f"{key} {counts[key]}" for key in sorted(counts)))
        lines.append("")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return problems
