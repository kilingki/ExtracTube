"""Adopted B05 arithmetic and bounded FFmpeg pipe adapter, from T3 detector.
Only the selected tile/grid-support policy is part of the product.
"""

from __future__ import annotations
import hashlib
import json
import os
import signal
import subprocess
import threading
import time
import re
from ._frame_extract import FRAME_RE, TIMEBASE_IN_RE, TIMEBASE_OUT_RE
from collections import deque
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

SECOND_QUANT=Decimal('0.000000001')
DIFF_QUANT=Decimal('0.000000000001')
GRID_INTERVAL=Decimal('0.5')
NEIGHBOR_WINDOW=Decimal('5')
MIN_NEIGHBORS=4
ABSOLUTE_THRESHOLD=Decimal('0.005')
MAD_SCALE=Decimal('1.4826')
MAD_MULTIPLIER=Decimal('3')
PEAK_RADIUS=Decimal('0.5')
TILE_WIDTH=80
TILE_HEIGHT=90
SUBPROCESS_TIMEOUT=300
PEAK_NEIGHBOR_GRID_STEP='one_grid_request_step'
GRID_SUPPRESS_KEPT_TILE_SUBSET='kept_neighbor_passing_tile_subset'


class T3Error(Exception):
    def __init__(self, message: str, *, stage: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.stage = stage
        self.details = details or {}

def as_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise T3Error(f"{label}가 유한한 수치가 아닙니다.", stage="input")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    else:
        raise T3Error(f"{label}가 유한한 수치가 아닙니다.", stage="input")
    if not number.is_finite():
        raise T3Error(f"{label}가 유한한 수치가 아닙니다.", stage="input")
    return number

def quantize_seconds(value: Fraction | Decimal) -> Decimal:
    number = value if isinstance(value, Decimal) else Decimal(value.numerator) / Decimal(value.denominator)
    return number.quantize(SECOND_QUANT)

def _shown_seconds(seconds: Fraction, start: Decimal, end: Decimal) -> Decimal:
    shown = quantize_seconds(seconds)
    if shown < start:
        shown = start
    if shown >= end:
        shown = end - SECOND_QUANT
    return shown

def quantize_diff(value: Decimal) -> Decimal:
    return value.quantize(DIFF_QUANT)

def to_fraction(value: Decimal) -> Fraction:
    return Fraction(value)

def video_seconds(pts: int, numerator: int, denominator: int, stream_start: Fraction) -> Fraction:
    if denominator <= 0 or numerator < 0:
        raise T3Error("time_base가 유효하지 않습니다.", stage="time")
    return Fraction(pts) * Fraction(numerator, denominator) - stream_start

def median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise ValueError("empty median")
    if count % 2 == 1:
        return ordered[count // 2]
    return (ordered[count // 2 - 1] + ordered[count // 2]) / Decimal(2)

def grid_requests(start: Decimal, end: Decimal) -> list[Decimal]:
    if start >= end:
        return []
    requests = []
    index = 0
    while True:
        requested = start + Decimal(index) * GRID_INTERVAL
        if requested >= end:
            break
        requests.append(requested)
        index += 1
        if index > 1_000_000:
            raise T3Error("격자 요청이 비정상적으로 많습니다.", stage="grid")
    return requests

def mean_abs_difference(before: bytes, after: bytes) -> Decimal:
    if len(before) != len(after) or not before:
        raise T3Error("차분할 이미지 크기가 다릅니다.", stage="difference")
    total = 0
    view_before = memoryview(before)
    view_after = memoryview(after)
    for offset in range(0, len(before), 4096):
        left = view_before[offset : offset + 4096]
        right = view_after[offset : offset + 4096]
        total += sum(abs(int(item) - int(other)) for item, other in zip(left, right))
    return quantize_diff(Decimal(total) / Decimal(len(before) * 255))

def _axis_edges(total: int, nominal: int) -> list[int]:
    if total <= nominal:
        return [0, total]
    edges = list(range(0, total, nominal))
    if edges[-1] != total:
        edges.append(total)
    return edges

def tile_layout(width: int, height: int, tile_width: int = TILE_WIDTH, tile_height: int = TILE_HEIGHT) -> list[dict[str, int]]:
    if width <= 0 or height <= 0 or tile_width <= 0 or tile_height <= 0:
        raise T3Error("타일 크기가 유효하지 않습니다.", stage="tile")
    xs = _axis_edges(width, tile_width)
    ys = _axis_edges(height, tile_height)
    tiles: list[dict[str, int]] = []
    for row in range(len(ys) - 1):
        for column in range(len(xs) - 1):
            x0, x1 = xs[column], xs[column + 1]
            y0, y1 = ys[row], ys[row + 1]
            tiles.append({
                "tile_index": len(tiles),
                "row": row,
                "column": column,
                "x": x0,
                "y": y0,
                "width": x1 - x0,
                "height": y1 - y0,
                "pixel_count": (x1 - x0) * (y1 - y0),
            })
    assert_tiles_cover(tiles, width, height)
    return tiles

def assert_tiles_cover(tiles: list[dict[str, int]], width: int, height: int) -> None:
    if not tiles:
        raise T3Error("타일이 없습니다.", stage="tile")
    covered = bytearray(width * height)
    total = 0
    for tile in tiles:
        if tile["width"] <= 0 or tile["height"] <= 0:
            raise T3Error("타일 크기가 유효하지 않습니다.", stage="tile")
        if tile["x"] < 0 or tile["y"] < 0 or tile["x"] + tile["width"] > width or tile["y"] + tile["height"] > height:
            raise T3Error("타일이 프레임 밖에 있습니다.", stage="tile")
        if tile["pixel_count"] != tile["width"] * tile["height"]:
            raise T3Error("타일 픽셀 수가 크기와 다릅니다.", stage="tile")
        total += tile["pixel_count"]
        for y in range(tile["y"], tile["y"] + tile["height"]):
            start = y * width + tile["x"]
            for offset in range(tile["width"]):
                index = start + offset
                if covered[index]:
                    raise T3Error("타일이 겹칩니다.", stage="tile")
                covered[index] = 1
    if total != width * height or any(pixel == 0 for pixel in covered):
        raise T3Error("타일이 프레임을 모두 덮지 않습니다.", stage="tile")

def frame_tile_differences(
    before: bytes,
    after: bytes,
    width: int,
    height: int,
    tiles: list[dict[str, int]],
    tile_width: int,
    tile_height: int,
) -> tuple[Decimal, list[Decimal]]:
    if len(before) != width * height or len(after) != width * height or not before:
        raise T3Error("차분할 이미지 크기가 다릅니다.", stage="difference")
    columns = max(int(tile["column"]) for tile in tiles) + 1
    rows = max(int(tile["row"]) for tile in tiles) + 1
    totals = [0] * len(tiles)
    global_total = 0
    before_view = memoryview(before)
    after_view = memoryview(after)
    for y in range(height):
        row_start = y * width
        tile_row = y // tile_height
        if tile_row >= rows:
            tile_row = rows - 1
        base = tile_row * columns
        for x in range(width):
            diff = abs(int(before_view[row_start + x]) - int(after_view[row_start + x]))
            global_total += diff
            tile_column = x // tile_width
            if tile_column >= columns:
                tile_column = columns - 1
            totals[base + tile_column] += diff
    global_mean = quantize_diff(Decimal(global_total) / Decimal(width * height * 255))
    values = [
        quantize_diff(Decimal(total) / Decimal(int(tile["pixel_count"]) * 255))
        for tile, total in zip(tiles, totals)
    ]
    return global_mean, values

def _time_base_object(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}

def _request_row(
    index: int,
    kind: str,
    requested: Decimal,
    policy: str,
    sample_index: int | None,
    error: Decimal | None,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "request_index": index,
        "kind": kind,
        "requested_video_seconds": requested,
        "policy": policy,
        "status": "unavailable" if sample_index is None else "ok",
        "sample_index": sample_index,
        "error_seconds": None if error is None else quantize_seconds(error),
        "reason": reason,
    }

def _neighbor_selection(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        others = [item for item in rows if item["index"] != row["index"]]
        nearby = [item for item in others if abs(item["t"] - row["t"]) <= NEIGHBOR_WINDOW]
        if len(nearby) >= MIN_NEIGHBORS:
            chosen = nearby
            mode = "neighbor_5s"
        elif len(others) >= MIN_NEIGHBORS:
            chosen = others
            mode = "candidate_except_self"
        else:
            chosen = []
            mode = "absolute"
        row["statistics_mode"] = mode
        row["neighbor_indices"] = [item["index"] for item in chosen]
        row["_chosen"] = chosen

def _grid_request_indices(sample: dict[str, Any]) -> list[int]:
    indices = [int(item) for item in sample.get("request_indices") or []]
    if "tail" in (sample.get("roles") or []) and indices:
        tail_index = max(indices)
        return [item for item in indices if item != tail_index]
    return indices

def _one_grid_step(samples: list[dict[str, Any]], left: dict[str, Any], right: dict[str, Any]) -> bool:
    """요청 격자에서 바로 한 단계인지 본다. 실제 시각 epsilon이나 단순 index 인접으로 판정하지 않는다."""
    earlier, later = (left, right) if int(left["index"]) < int(right["index"]) else (right, left)
    if int(later["index"]) != int(earlier["index"]) + 1:
        return False
    previous = _grid_request_indices(samples[int(later["before_sample"])])
    current = _grid_request_indices(samples[int(later["after_sample"])])
    if len(current) != 1 or not previous:
        return False
    return current[0] == max(previous) + 1

def _peak_outranks(other: dict[str, Any], row: dict[str, Any]) -> bool:
    """점수가 더 크거나, 같은 점수에서 더 이른 시각이면 other가 row보다 높다."""
    if other["peak_score"] > row["peak_score"]:
        return True
    if other["peak_score"] < row["peak_score"]:
        return False
    return other["t"] < row["t"] or (other["t"] == row["t"] and other["index"] < row["index"])

def _passing_tile_support(candidate: set[int], neighbor: set[int]) -> str:
    if candidate <= neighbor:
        return "subset"
    if candidate.isdisjoint(neighbor):
        return "disjoint"
    return "partial"

def _actual_time_reason(other: dict[str, Any], row: dict[str, Any]) -> str:
    if other["peak_score"] > row["peak_score"]:
        return "앞뒤 0.5초 안에 더 큰 차분이 있습니다."
    return "같은 차분이고 더 이른 시각이 있습니다."

def _apply_kept_neighbor_tile_support(
    rows: list[dict[str, Any]],
    passers: list[dict[str, Any]],
    samples: list[dict[str, Any]],
) -> None:
    """actual_time은 그대로 두고, grid_step은 유지 이웃의 통과 타일이 포함할 때만 억제한다."""
    decision: dict[int, str] = {}
    actual_refs: dict[int, list[dict[str, Any]]] = {}
    for row in passers:
        refs = []
        for other in passers:
            if other["index"] == row["index"] or abs(other["t"] - row["t"]) > PEAK_RADIUS:
                continue
            if not _peak_outranks(other, row):
                continue
            refs.append({
                "difference_index": other["index"],
                "sample_index": other["after_sample"],
                "reason": _actual_time_reason(other, row),
                "neighbor_basis": "actual_time",
            })
        if refs:
            decision[row["index"]] = "suppress"
            actual_refs[row["index"]] = refs
    ordered = sorted(
        (row for row in passers if row["index"] not in decision),
        key=lambda item: (-item["peak_score"], item["t"], item["index"]),
    )
    grid_refs: dict[int, list[dict[str, Any]]] = {}
    reviews: dict[int, list[dict[str, Any]]] = {}
    for row in ordered:
        evidence = []
        review_rows = []
        own_tiles = {int(tile) for tile in row["passing_tile_indices"]}
        for other in passers:
            if other["index"] == row["index"] or abs(other["t"] - row["t"]) <= PEAK_RADIUS:
                continue
            if not _one_grid_step(samples, row, other) or not _peak_outranks(other, row):
                continue
            other_decision = decision.get(int(other["index"]))
            if other_decision is None:
                raise T3Error(
                    "격자 지지 순서가 결정되지 않았습니다.",
                    stage="peak",
                    details={"difference_index": row["index"], "neighbor": other["index"]},
                )
            relation = _passing_tile_support(own_tiles, {int(tile) for tile in other["passing_tile_indices"]})
            neighbor_kept = other_decision == "keep"
            used = neighbor_kept and relation == "subset"
            item: dict[str, Any] = {
                "difference_index": other["index"],
                "neighbor_basis": "grid_step",
                "neighbor_kept": neighbor_kept,
                "support": relation,
                "used": used,
            }
            if not used:
                item["unused_reason"] = "neighbor_not_kept" if not neighbor_kept else "support_not_covered"
            review_rows.append(item)
            if used:
                evidence.append({
                    "difference_index": other["index"],
                    "sample_index": other["after_sample"],
                    "reason": "유지된 격자 이웃의 통과 타일이 이 차분의 통과 타일을 포함합니다.",
                    "neighbor_basis": "grid_step",
                    "support": "subset",
                })
        decision[row["index"]] = "suppress" if evidence else "keep"
        grid_refs[row["index"]] = evidence
        reviews[row["index"]] = review_rows
    for row in rows:
        if not row["passes_threshold"]:
            row["peak_action"] = "below_threshold"
            row["suppression_refs"] = []
            row["grid_support_reviews"] = []
            continue
        if row["index"] in actual_refs:
            row["peak_action"] = "suppress"
            row["suppression_refs"] = actual_refs[row["index"]]
            row["grid_support_reviews"] = []
            continue
        row["suppression_refs"] = grid_refs[row["index"]]
        row["grid_support_reviews"] = reviews[row["index"]]
        row["peak_action"] = decision[row["index"]]

def _tile_threshold(values: list[Decimal]) -> tuple[Decimal | None, Decimal | None, Decimal]:
    if not values:
        return None, None, ABSOLUTE_THRESHOLD
    middle_exact = median(values)
    mad_exact = median([abs(item - middle_exact) for item in values])
    threshold = quantize_diff(max(ABSOLUTE_THRESHOLD, middle_exact + MAD_MULTIPLIER * MAD_SCALE * mad_exact))
    return quantize_diff(middle_exact), quantize_diff(mad_exact), threshold

def _finish_tile_rows(
    rows: list[dict[str, Any]],
    layout: list[dict[str, int]],
    samples: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    _neighbor_selection(rows)
    for row in rows:
        chosen = row["_chosen"]
        tiles_out = []
        for spec_index, spec in enumerate(layout):
            if row["statistics_mode"] == "absolute":
                middle: Decimal | None = None
                mad: Decimal | None = None
                threshold = ABSOLUTE_THRESHOLD
            else:
                series = [item["tile_values"][spec_index] for item in chosen]
                middle, mad, threshold = _tile_threshold(series)
            value = row["tile_values"][spec_index]
            tiles_out.append({
                "tile_index": spec["tile_index"],
                "row": spec["row"],
                "column": spec["column"],
                "x": spec["x"],
                "y": spec["y"],
                "width": spec["width"],
                "height": spec["height"],
                "pixel_count": spec["pixel_count"],
                "value": value,
                "median": middle,
                "mad": mad,
                "threshold": threshold,
                "passes_threshold": value > threshold,
            })
        passing = [tile for tile in tiles_out if tile["passes_threshold"]]
        tile_max = max(tile["value"] for tile in tiles_out)
        max_tile = min((tile for tile in tiles_out if tile["value"] == tile_max), key=lambda tile: tile["tile_index"])
        row["tiles"] = tiles_out
        row["tile_max"] = tile_max
        row["max_tile_index"] = max_tile["tile_index"]
        row["passing_tile_indices"] = [tile["tile_index"] for tile in passing]
        row["passes_threshold"] = bool(passing)
        if not passing:
            row["peak_score"] = None
            row["peak_tile_index"] = None
            row["threshold"] = None
        else:
            peak_tile = min(passing, key=lambda tile: (-tile["value"], tile["tile_index"]))
            row["peak_score"] = peak_tile["value"]
            row["peak_tile_index"] = peak_tile["tile_index"]
            row["threshold"] = peak_tile["threshold"]
    passers = [row for row in rows if row["passes_threshold"]]
    _apply_kept_neighbor_tile_support(rows, passers, samples)
    public = []
    for row in rows:
        public.append({
            "index": row["index"],
            "before_sample": row["before_sample"],
            "after_sample": row["after_sample"],
            "elapsed_video_seconds": row["elapsed_video_seconds"],
            "global_mean": row["global_mean"],
            "tile_max": row["tile_max"],
            "peak_score": row["peak_score"],
            "threshold": row["threshold"],
            "passes_threshold": row["passes_threshold"],
            "statistics_mode": row["statistics_mode"],
            "neighbor_indices": row["neighbor_indices"],
            "tiles": row["tiles"],
            "passing_tile_indices": row["passing_tile_indices"],
            "max_tile_index": row["max_tile_index"],
            "peak_tile_index": row["peak_tile_index"],
            "peak_action": row["peak_action"],
            "suppression_refs": row["suppression_refs"],
        })
        if "grid_support_reviews" in row:
            public[-1]["grid_support_reviews"] = row["grid_support_reviews"]
    return public

def _boundaries(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
) -> list[dict[str, Any]]:
    rows = []
    for difference in differences:
        if difference.get("peak_action") != "keep":
            continue
        after = samples[int(difference["after_sample"])]
        before = samples[int(difference["before_sample"])]
        boundary = as_decimal(after["video_seconds"], "boundary")
        if not (start < boundary < end):
            continue
        rows.append({
            "difference_index": int(difference["index"]),
            "before_sample": int(before["sample_index"]),
            "after_sample": int(after["sample_index"]),
            "bracket_start": as_decimal(before["video_seconds"], "bracket"),
            "bracket_end": boundary,
            "video_seconds": boundary,
        })
    rows.sort(key=lambda item: (item["video_seconds"], item["difference_index"]))
    unique = []
    seen: set[Decimal] = set()
    for row in rows:
        if row["video_seconds"] in seen:
            continue
        seen.add(row["video_seconds"])
        row["boundary_index"] = len(unique)
        unique.append(row)
    return unique

def _segments(
    start: Decimal,
    end: Decimal,
    boundaries: list[dict[str, Any]],
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    edges = [start] + [item["video_seconds"] for item in boundaries] + [end]
    segments = []
    for index in range(len(edges) - 1):
        left = edges[index]
        right = edges[index + 1]
        inside = [
            int(sample["sample_index"])
            for sample in samples
            if left <= as_decimal(sample["video_seconds"], "sample") < right
        ]
        segments.append({
            "segment_index": index,
            "video_start": left,
            "video_end": right,
            "left_boundary_index": None if index == 0 else index - 1,
            "right_boundary_index": None if index == len(boundaries) else index,
            "analysis_sample_indices": inside,
        })
    return segments

def _coverage_problems(start: Decimal, end: Decimal, segments: list[dict[str, Any]]) -> list[str]:
    problems = []
    if not segments:
        return ["구간이 없습니다."]
    if segments[0]["video_start"] != start or segments[-1]["video_end"] != end:
        problems.append("구간이 원래 후보 범위와 같지 않습니다.")
    for previous, current in zip(segments, segments[1:]):
        if previous["video_end"] != current["video_start"]:
            problems.append("구간 사이에 빈틈이나 겹침이 있습니다.")
    for segment in segments:
        if segment["video_end"] <= segment["video_start"]:
            problems.append("길이가 0인 구간이 있습니다.")
    return problems

def parse_showinfo_line(line: str) -> dict[str, Any] | None:
    matched = FRAME_RE.search(line)
    if not matched:
        return None
    return {
        "n": int(matched.group(1)),
        "pts": int(matched.group(2)),
        "width": int(matched.group(3)),
        "height": int(matched.group(4)),
    }

def parse_time_base_line(line: str) -> tuple[str, Fraction | None] | None:
    incoming = TIMEBASE_IN_RE.search(line)
    if incoming:
        return "in", Fraction(int(incoming.group(1)), int(incoming.group(2)))
    outgoing = TIMEBASE_OUT_RE.search(line)
    if outgoing:
        numerator = int(outgoing.group(1))
        denominator = int(outgoing.group(2))
        if numerator <= 0 or denominator <= 0:
            return "out", None
        return "out", Fraction(numerator, denominator)
    return None

def _kill_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)

def run_ffmpeg(args: list[str], timeout: int, on_frame: Callable[[dict[str, Any], bytes], None]) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    records: deque[dict[str, Any] | None] = deque()
    raws: deque[bytes | None] = deque()
    ready = threading.Condition()
    state = {"stderr_done": False, "stdout_done": False, "error": None, "time_base_in": None, "time_base_out": None}
    stderr_chunks: list[bytes] = []
    counts = {"records": 0, "raws": 0}

    def read_stderr() -> None:
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_chunks.append(line)
                text = line.decode("utf-8", errors="replace")
                found = parse_time_base_line(text)
                if found:
                    kind, value = found
                    if kind == "in" and state["time_base_in"] is None:
                        state["time_base_in"] = value
                    elif kind == "out" and "time_base_out_seen" not in state:
                        state["time_base_out"] = value
                        state["time_base_out_seen"] = True
                parsed = parse_showinfo_line(text)
                if parsed:
                    with ready:
                        if state.get("frame_size") is None:
                            state["frame_size"] = int(parsed["width"]) * int(parsed["height"])
                        records.append(parsed)
                        counts["records"] += 1
                        ready.notify_all()
        except BaseException as exc:
            state["error"] = exc
        finally:
            with ready:
                state["stderr_done"] = True
                records.append(None)
                ready.notify_all()

    def read_stdout() -> None:
        try:
            assert proc.stdout is not None
            pending = bytearray()

            def push_ready() -> None:
                size = state.get("frame_size")
                if not size:
                    return
                while len(pending) >= size:
                    raw = bytes(pending[:size])
                    del pending[:size]
                    with ready:
                        while len(raws) >= 8 and state["error"] is None:
                            ready.wait(timeout=0.05)
                        raws.append(raw)
                        counts["raws"] += 1
                        ready.notify_all()

            while True:
                chunk = proc.stdout.read(1 << 20)
                if not chunk:
                    break
                pending.extend(chunk)
                push_ready()
            with ready:
                while state.get("frame_size") is None and not state["stderr_done"] and state["error"] is None:
                    ready.wait(timeout=0.05)
            push_ready()
            if pending:
                raise T3Error("raw bytes가 프레임 크기로 나누어 떨어지지 않습니다.", stage="decode", details={"extra": len(pending)})
        except BaseException as exc:
            state["error"] = exc
        finally:
            with ready:
                state["stdout_done"] = True
                raws.append(None)
                ready.notify_all()

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread.start()
    stdout_thread.start()
    timed_out = False
    try:
        while True:
            if time.monotonic() - started > timeout:
                timed_out = True
                _kill_process(proc)
                break
            with ready:
                while (not records or not raws) and state["error"] is None and not timed_out:
                    if records and records[0] is None and raws and raws[0] is None:
                        break
                    if state["stderr_done"] and state["stdout_done"] and (not records or not raws):
                        break
                    ready.wait(timeout=0.2)
                    if time.monotonic() - started > timeout:
                        timed_out = True
                        break
                if timed_out or state["error"] is not None:
                    break
                if not records or not raws:
                    if state["stderr_done"] and state["stdout_done"]:
                        break
                    continue
                record = records[0]
                raw = raws[0]
                if record is None and raw is None:
                    records.popleft()
                    raws.popleft()
                    break
                if record is None or raw is None:
                    raise T3Error("raw bytes와 PTS 레코드 수가 다릅니다.", stage="decode")
                records.popleft()
                raws.popleft()
                ready.notify_all()
            on_frame(record, raw)
        returncode = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            _kill_process(proc)
            returncode = proc.wait(timeout=30)
        stderr_thread.join(timeout=30)
        stdout_thread.join(timeout=30)
        proc.stdout.close()
        proc.stderr.close()
    elapsed = Decimal(str(round(time.monotonic() - started, 6)))
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    details = {"args": args, "returncode": proc.returncode, "elapsed_seconds": elapsed, "stderr_tail": stderr_text[-4000:], "stderr": stderr_text}
    if timed_out:
        raise T3Error("FFmpeg 제한 시간 300초를 초과했습니다.", stage="decode", details=details)
    if state["error"] is not None:
        if isinstance(state["error"], T3Error):
            raise state["error"]
        raise T3Error(str(state["error"]), stage="decode", details=details)
    if returncode != 0:
        raise T3Error(f"FFmpeg가 종료 코드 {returncode}로 끝났습니다.", stage="decode", details=details)
    if counts["records"] != counts["raws"]:
        raise T3Error(
            f"raw bytes와 PTS 레코드 수가 다릅니다. records={counts['records']} raws={counts['raws']}",
            stage="decode",
            details=details,
        )
    return {
        "time_base_in": state["time_base_in"],
        "time_base_out": state["time_base_out"],
        "args": args,
        "returncode": returncode,
        "elapsed_seconds": elapsed,
        "stderr_tail": stderr_text[-4000:], "stderr": stderr_text,
        "frame_count": counts["records"],
    }

def probe_video(ffprobe: str, path: Path) -> dict[str, Any]:
    args = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=index,codec_name,width,height,time_base,start_time,duration",
        "-of", "json", str(path),
    ]
    started = time.monotonic()
    completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=SUBPROCESS_TIMEOUT, check=False)
    elapsed = Decimal(str(round(time.monotonic() - started, 6)))
    if completed.returncode != 0:
        raise T3Error(
            "ffprobe가 실패했습니다.",
            stage="probe",
            details={"args": args, "stderr": completed.stderr.decode("utf-8", errors="replace")[-2000:]},
        )
    payload = json.loads(completed.stdout.decode("utf-8"))
    streams = payload.get("streams") or []
    if len(streams) != 1:
        raise T3Error("비디오 스트림을 하나만 확정하지 못했습니다.", stage="probe")
    stream = streams[0]
    for field in ("index", "codec_name", "width", "height", "time_base", "start_time", "duration"):
        if stream.get(field) in (None, "", "N/A"):
            raise T3Error(f"비디오 스트림의 {field}가 불명확합니다.", stage="probe")
    time_base_text = str(stream["time_base"])
    if "/" not in time_base_text:
        raise T3Error("time_base 형식이 아닙니다.", stage="probe")
    numerator_text, denominator_text = time_base_text.split("/", 1)
    numerator = int(numerator_text)
    denominator = int(denominator_text)
    if numerator <= 0 or denominator <= 0:
        raise T3Error("time_base가 유효하지 않습니다.", stage="probe")
    return {
        "index": int(stream["index"]),
        "codec_name": str(stream["codec_name"]),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "time_base": _time_base_object(numerator, denominator),
        "time_base_fraction": Fraction(numerator, denominator),
        "start_time": Decimal(str(stream["start_time"])),
        "duration": Decimal(str(stream["duration"])),
        "args": args,
        "elapsed_seconds": elapsed,
    }

def tool_identity(path: str) -> dict[str, str]:
    completed = subprocess.run([path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    text = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode != 0 or not text.strip():
        raise T3Error(f"{path} 버전을 확인하지 못했습니다.", stage="runtime")
    return {"path": path, "version_text": text, "version_line": text.splitlines()[0]}
