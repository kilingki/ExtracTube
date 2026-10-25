"""T3 화면 변화 검출. 표시 코드와 분리하며 사전 조사 스크립트를 import하지 않는다."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from collections import deque
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterator

SCHEMA_VERSION = "t3.screen_changes.v1"
LOGIC_VERSION = "t3.screen_changes.v1"
CONFIG_ID = "B00"
SECOND_QUANT = Decimal("0.000000001")
DIFF_QUANT = Decimal("0.000000000001")
GRID_INTERVAL = Decimal("0.5")
NEIGHBOR_WINDOW = Decimal("5")
MIN_NEIGHBORS = 4
ABSOLUTE_THRESHOLD = Decimal("0.005")
MAD_SCALE = Decimal("1.4826")
MAD_MULTIPLIER = Decimal("3")
PEAK_RADIUS = Decimal("0.5")
SAMPLE_GAP = Decimal("1")
ANALYSIS_WIDTH = 640
FFMPEG_THREADS = "2"
SUBPROCESS_TIMEOUT = 300
TAIL_WINDOWS = (Decimal("1"), Decimal("2"), Decimal("4"))
DECODE_MARGIN = Decimal("0.15")

SETTINGS: dict[str, Any] = {
    "config_id": CONFIG_ID,
    "logic_version": LOGIC_VERSION,
    "scope": "t2_candidate_video_range",
    "padding_seconds": "0",
    "grid_interval_seconds": "0.5",
    "extra_sample": "last_frame_before_end",
    "analysis_width": ANALYSIS_WIDTH,
    "analysis_height": "even_aspect",
    "scale_flags": "area",
    "pixel_format": "gray",
    "difference": "mean_absolute_pixel",
    "neighbor_seconds": "5",
    "min_neighbor_count": MIN_NEIGHBORS,
    "absolute_threshold": "0.005",
    "mad_scale": "1.4826",
    "mad_multiplier": "3",
    "peak_radius_seconds": "0.5",
    "tie_break": "earlier_time",
    "min_segment_merge": False,
    "max_segment_split": False,
    "decode_concurrency": 1,
    "ffmpeg_threads": 2,
    "subprocess_timeout_seconds": SUBPROCESS_TIMEOUT,
    "retries": 0,
    "preview_max_width": 1280,
    "preview_qv": 2,
    "timeline_interval_seconds": "10",
}
SCHEMA_VERSION_B01 = "t3.screen_changes.v2"
LOGIC_VERSION_B01 = "t3.screen_changes.tile_or.v1"
CONFIG_ID_B01 = "B01"
TILE_WIDTH = 80
TILE_HEIGHT = 90
B00_IDENTITY = (CONFIG_ID, LOGIC_VERSION, SCHEMA_VERSION)
B01_IDENTITY = (CONFIG_ID_B01, LOGIC_VERSION_B01, SCHEMA_VERSION_B01)
SETTINGS_B01: dict[str, Any] = {
    **SETTINGS,
    "config_id": CONFIG_ID_B01,
    "logic_version": LOGIC_VERSION_B01,
    "difference": "tile_mean_absolute_or",
    "tile_width": TILE_WIDTH,
    "tile_height": TILE_HEIGHT,
    "tile_origin": "top_left",
    "tile_edge": "last_tile_absorbs_remainder",
    "tile_combine": "any_pass",
    "tile_peak": "passing_tiles_max",
}
SCHEMA_VERSION_B02 = "t3.screen_changes.v3"
LOGIC_VERSION_B02 = "t3.screen_changes.tile_or.temporal_confirm.v1"
CONFIG_ID_B02 = "B02"
TEMPORAL_OFFSETS = (Decimal("1.0"), Decimal("2.0"))
TEMPORAL_MATCH_TOLERANCE = GRID_INTERVAL
TEMPORAL_RULE = "all_passing_tiles_reverted_at_all_offsets"
B02_IDENTITY = (CONFIG_ID_B02, LOGIC_VERSION_B02, SCHEMA_VERSION_B02)
SETTINGS_B02: dict[str, Any] = {
    **SETTINGS_B01,
    "config_id": CONFIG_ID_B02,
    "logic_version": LOGIC_VERSION_B02,
    "difference": "tile_mean_absolute_or_temporal",
    "temporal_confirm": "after_peak_keep",
    "temporal_offsets_seconds": ["1.0", "2.0"],
    "temporal_match_tolerance_seconds": "0.5",
    "temporal_revert_absolute": "0.005",
    "temporal_rule": TEMPORAL_RULE,
    "temporal_on_insufficient": "keep",
}
SCHEMA_VERSION_B03 = "t3.screen_changes.v4"
LOGIC_VERSION_B03 = "t3.screen_changes.tile_or.spatial_shift.v1"
CONFIG_ID_B03 = "B03"
SPATIAL_PIXEL = 32
SPATIAL_FRINGE = 1
SPATIAL_NEIGHBORHOOD = 1
SPATIAL_RULE = "one_shift_explains_change_shape"
B03_IDENTITY = (CONFIG_ID_B03, LOGIC_VERSION_B03, SCHEMA_VERSION_B03)
SETTINGS_B03: dict[str, Any] = {
    **SETTINGS_B01,
    "config_id": CONFIG_ID_B03,
    "logic_version": LOGIC_VERSION_B03,
    "difference": "tile_mean_absolute_or_spatial",
    "spatial_confirm": "after_peak_keep",
    "spatial_pixel": SPATIAL_PIXEL,
    "spatial_fringe": SPATIAL_FRINGE,
    "spatial_centroid_neighborhood": SPATIAL_NEIGHBORHOOD,
    "spatial_shift_limit": "in_frame",
    "spatial_connectivity": 4,
    "spatial_rule": SPATIAL_RULE,
    "spatial_on_uncertain": "keep",
}
SCHEMA_VERSION_B04 = "t3.screen_changes.v5"
LOGIC_VERSION_B04 = "t3.screen_changes.tile_or.grid_neighbor.v1"
CONFIG_ID_B04 = "B04"
PEAK_NEIGHBOR_GRID_STEP = "one_grid_request_step"
B04_IDENTITY = (CONFIG_ID_B04, LOGIC_VERSION_B04, SCHEMA_VERSION_B04)
SETTINGS_B04: dict[str, Any] = {
    **SETTINGS_B01,
    "config_id": CONFIG_ID_B04,
    "logic_version": LOGIC_VERSION_B04,
    "difference": "tile_mean_absolute_or_grid_neighbor",
    "peak_neighbor": PEAK_NEIGHBOR_GRID_STEP,
}
SCHEMA_VERSION_B05 = "t3.screen_changes.v6"
LOGIC_VERSION_B05 = "t3.screen_changes.tile_or.grid_support.v1"
CONFIG_ID_B05 = "B05"
GRID_SUPPRESS_KEPT_TILE_SUBSET = "kept_neighbor_passing_tile_subset"
B05_IDENTITY = (CONFIG_ID_B05, LOGIC_VERSION_B05, SCHEMA_VERSION_B05)
SETTINGS_B05: dict[str, Any] = {
    **SETTINGS_B04,
    "config_id": CONFIG_ID_B05,
    "logic_version": LOGIC_VERSION_B05,
    "difference": "tile_mean_absolute_or_grid_support",
    "grid_suppress_rule": GRID_SUPPRESS_KEPT_TILE_SUBSET,
}
SCHEMA_VERSION_B06 = "t3.screen_changes.v7"
LOGIC_VERSION_B06 = "t3.screen_changes.tile_or.residual_latch.v1"
CONFIG_ID_B06 = "B06"
CONTENT_GATE = SPATIAL_PIXEL
CONTENT_EXPOSURE_MEDIAN = 8
CONTENT_EXPOSURE_COVER = 98
CONTENT_REGISTER_RADIUS = 2
CONTENT_CONFIRM_GAP = Decimal("1")
CONTENT_EPISODE_GAP = 1
CONTENT_ACTIONS = frozenset({
    "none",
    "birth",
    "extend",
    "short_unconfirmed",
    "unobservable",
    "possible_removal",
    "exposure",
    "unresolved_contact",
})
B06_IDENTITY = (CONFIG_ID_B06, LOGIC_VERSION_B06, SCHEMA_VERSION_B06)
SETTINGS_B06: dict[str, Any] = {
    **SETTINGS_B05,
    "config_id": CONFIG_ID_B06,
    "logic_version": LOGIC_VERSION_B06,
    "difference": "tile_mean_absolute_or_residual_latch",
    "content_channel": "residual_latch",
    "residual_gate": CONTENT_GATE,
    "exposure_median_min": CONTENT_EXPOSURE_MEDIAN,
    "exposure_cover_percent": CONTENT_EXPOSURE_COVER,
    "register_radius": CONTENT_REGISTER_RADIUS,
    "content_confirm_gap_seconds": "1",
    "content_connect": 4,
    "content_episode_gap": CONTENT_EPISODE_GAP,
}


def settings_for(config_id: str) -> dict[str, Any]:
    if config_id == CONFIG_ID:
        return SETTINGS
    if config_id == CONFIG_ID_B01:
        return SETTINGS_B01
    if config_id == CONFIG_ID_B02:
        return SETTINGS_B02
    if config_id == CONFIG_ID_B03:
        return SETTINGS_B03
    if config_id == CONFIG_ID_B04:
        return SETTINGS_B04
    if config_id == CONFIG_ID_B05:
        return SETTINGS_B05
    if config_id == CONFIG_ID_B06:
        return SETTINGS_B06
    raise T3Error("알 수 없는 config_id입니다.", stage="config", details={"config_id": config_id})


def schema_for(config_id: str) -> str:
    if config_id == CONFIG_ID:
        return SCHEMA_VERSION
    if config_id == CONFIG_ID_B01:
        return SCHEMA_VERSION_B01
    if config_id == CONFIG_ID_B02:
        return SCHEMA_VERSION_B02
    if config_id == CONFIG_ID_B03:
        return SCHEMA_VERSION_B03
    if config_id == CONFIG_ID_B04:
        return SCHEMA_VERSION_B04
    if config_id == CONFIG_ID_B05:
        return SCHEMA_VERSION_B05
    if config_id == CONFIG_ID_B06:
        return SCHEMA_VERSION_B06
    raise T3Error("알 수 없는 config_id입니다.", stage="config", details={"config_id": config_id})


class T3Error(Exception):
    def __init__(self, message: str, *, stage: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.stage = stage
        self.details = details or {}


def detector_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def loads(text: str) -> Any:
    def reject(name: str) -> Any:
        raise ValueError(name)

    return json.loads(text, parse_float=Decimal, parse_constant=reject)


def _dump(value: Any, level: int) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimal")
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError("float is not allowed in T3 JSON")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Fraction):
        return _dump(quantize_seconds(value), level)
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
            if not isinstance(key, str):
                raise TypeError("JSON object key must be a string")
            rows.append(" " * (level + 2) + json.dumps(key, ensure_ascii=False) + ": " + _dump(item, level + 2))
        return "{\n" + ",\n".join(rows) + "\n" + " " * level + "}"
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def dumps(value: Any) -> str:
    return _dump(value, 0) + "\n"


def canonical(value: Any) -> str:
    return json.dumps(_canonicalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Fraction):
        return format(quantize_seconds(value), "f")
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, float):
        raise TypeError("float is not allowed")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def analyze_frames(
    frames: list[dict[str, Any]],
    *,
    start: Decimal,
    end: Decimal,
    stream_start: Fraction,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = SETTINGS if settings is None else settings
    start_f = to_fraction(start)
    end_f = to_fraction(end)
    usable: list[tuple[Fraction, dict[str, Any]]] = []
    for frame in frames:
        pts = frame["pts"]
        if isinstance(pts, bool) or not isinstance(pts, int):
            raise T3Error("PTS가 정수가 아닙니다.", stage="time")
        seconds = video_seconds(
            int(pts),
            int(frame["time_base_numerator"]),
            int(frame["time_base_denominator"]),
            stream_start,
        )
        if seconds < start_f or seconds >= end_f:
            continue
        usable.append((seconds, frame))
    for previous, current in zip(usable, usable[1:]):
        if current[0] <= previous[0]:
            raise T3Error("PTS가 단조 증가하지 않습니다.", stage="time")
    requests = grid_requests(start, end)
    request_rows: list[dict[str, Any]] = []
    if not usable:
        for index, requested in enumerate(requests):
            request_rows.append(_request_row(
                index, "grid", requested, "first_at_or_after", None, None,
                "후보 안에 요청 시각 이후의 프레임이 없습니다.",
            ))
        request_rows.append(_request_row(
            len(requests), "tail", end, "last_before_end", None, None,
            "후보 안에 유효 프레임이 없습니다.",
        ))
        problem = "후보 안에 유효 프레임이 없습니다."
        return {
            "status": "failed",
            "problems": [problem],
            "warnings": [],
            "requests": request_rows,
            "samples": [],
            "differences": [],
            "boundaries": [],
            "segments": [],
            "validation": {"ok": False, "problems": [problem]},
        }

    assigned: dict[int, list[int]] = {}
    search_at = 0
    for request_index, requested in enumerate(requests):
        requested_f = to_fraction(requested)
        while search_at < len(usable) and usable[search_at][0] < requested_f:
            search_at += 1
        if search_at >= len(usable):
            request_rows.append(_request_row(
                request_index, "grid", requested, "first_at_or_after", None, None,
                "후보 안에 요청 시각 이후의 프레임이 없습니다.",
            ))
            continue
        assigned.setdefault(search_at, []).append(request_index)
        exact = usable[search_at][0]
        error = quantize_seconds(exact) - requested
        if exact >= to_fraction(requested) and error < 0:
            error = Decimal(0)
        request_rows.append(_request_row(
            request_index, "grid", requested, "first_at_or_after", -1,
            error, None,
        ))
    tail_position = len(usable) - 1
    sample_positions = sorted(set(assigned) | {tail_position})
    position_to_sample = {position: index for index, position in enumerate(sample_positions)}
    for row in request_rows:
        if row["status"] != "ok":
            continue
        source_position = next(
            position for position, indices in assigned.items() if row["request_index"] in indices
        )
        row["sample_index"] = position_to_sample[source_position]
    samples: list[dict[str, Any]] = []
    raws: list[bytes] = []
    for position in sample_positions:
        seconds, frame = usable[position]
        raw = bytes(frame["raw"])
        width = int(frame["width"])
        height = int(frame["height"])
        if len(raw) != width * height:
            raise T3Error("raw bytes와 이미지 크기가 일치하지 않습니다.", stage="decode")
        if height % 2 != 0:
            raise T3Error("분석 이미지 높이가 짝수가 아닙니다.", stage="decode")
        roles = []
        request_indices = list(assigned.get(position, []))
        if request_indices:
            roles.append("grid")
        if position == tail_position:
            roles.append("tail")
            request_indices.append(len(requests))
        samples.append({
            "sample_index": len(samples),
            "roles": roles,
            "request_indices": sorted(set(request_indices)),
            "stream_index": int(frame["stream_index"]),
            "pts": int(frame["pts"]),
            "time_base": _time_base_object(int(frame["time_base_numerator"]), int(frame["time_base_denominator"])),
            "video_seconds": _shown_seconds(seconds, start, end),
            "width": width,
            "height": height,
            "pixel_format": str(frame.get("pixel_format") or "gray"),
            "analysis_bytes_sha256": sha256_bytes(raw),
            "preview_ref": None,
        })
        raws.append(raw)
    tail_sample = position_to_sample[tail_position]
    request_rows.append(_request_row(
        len(requests), "tail", end, "last_before_end", tail_sample,
        samples[tail_sample]["video_seconds"] - end, None,
    ))
    request_rows.sort(key=lambda row: row["request_index"])
    warnings = []
    for before, after in zip(samples, samples[1:]):
        elapsed = after["video_seconds"] - before["video_seconds"]
        if elapsed > SAMPLE_GAP:
            warnings.append({
                "code": "sample_gap",
                "before_sample": before["sample_index"],
                "after_sample": after["sample_index"],
                "before_video_seconds": before["video_seconds"],
                "after_video_seconds": after["video_seconds"],
                "elapsed_video_seconds": elapsed,
            })
    mode = str(active.get("difference") or "mean_absolute_pixel")
    if mode == "mean_absolute_pixel":
        differences = _differences(samples, raws)
        boundaries = _boundaries(differences, samples, start, end)
    elif mode in {"tile_mean_absolute_or", "tile_mean_absolute_or_grid_neighbor", "tile_mean_absolute_or_grid_support"}:
        differences = _differences_tiles(samples, raws, active)
        boundaries = _boundaries(differences, samples, start, end)
    elif mode == "tile_mean_absolute_or_temporal":
        differences = _differences_tiles(samples, raws, active)
        _apply_temporal(differences, samples, raws, start, end, active)
        boundaries = _boundaries_temporal(differences, samples, start, end)
    elif mode == "tile_mean_absolute_or_spatial":
        differences = _differences_tiles(samples, raws, active)
        _apply_spatial(differences, samples, raws, active)
        boundaries = _boundaries_spatial(differences, samples, start, end)
    elif mode == "tile_mean_absolute_or_residual_latch":
        differences = _differences_tiles(samples, raws, active)
        _apply_residual_latch(differences, samples, raws, active)
        boundaries = _boundaries_content(differences, samples, start, end)
    else:
        raise T3Error("알 수 없는 변화량입니다.", stage="difference", details={"difference": mode})
    segments = _segments(start, end, boundaries, samples)
    problems = _coverage_problems(start, end, segments)
    return {
        "status": "failed" if problems else "ok",
        "problems": problems,
        "warnings": warnings,
        "requests": request_rows,
        "samples": samples,
        "differences": differences,
        "boundaries": boundaries,
        "segments": segments,
        "validation": {"ok": not problems, "problems": list(problems)},
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


def _apply_score_peak(
    rows: list[dict[str, Any]],
    passers: list[dict[str, Any]],
    samples: list[dict[str, Any]] | None,
    grid_step: bool,
) -> None:
    """B00 타일 이후의 점수 피크다. B04는 격자 한 단계를 같은 방식으로 더 비교한다."""
    for row in rows:
        if not row["passes_threshold"]:
            row["peak_action"] = "below_threshold"
            row["suppression_refs"] = []
            continue
        refs = []
        for other in passers:
            if other["index"] == row["index"]:
                continue
            if abs(other["t"] - row["t"]) <= PEAK_RADIUS:
                basis = "actual_time"
            elif grid_step and _one_grid_step(samples or [], row, other):
                basis = "grid_step"
            else:
                continue
            later_tie = other["peak_score"] == row["peak_score"] and (
                other["t"] < row["t"] or (other["t"] == row["t"] and other["index"] < row["index"])
            )
            if other["peak_score"] > row["peak_score"]:
                reason = (
                    "요청 격자 한 단계의 이웃이고 더 큰 차분이 있습니다."
                    if basis == "grid_step"
                    else "앞뒤 0.5초 안에 더 큰 차분이 있습니다."
                )
            elif later_tie:
                reason = (
                    "요청 격자 한 단계의 이웃이고 같은 차분에서 더 이른 시각이 있습니다."
                    if basis == "grid_step"
                    else "같은 차분이고 더 이른 시각이 있습니다."
                )
            else:
                continue
            ref = {
                "difference_index": other["index"],
                "sample_index": other["after_sample"],
                "reason": reason,
            }
            if grid_step:
                ref["neighbor_basis"] = basis
            refs.append(ref)
        row["suppression_refs"] = refs
        row["peak_action"] = "suppress" if refs else "keep"


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
    grid_step = bool(settings and settings.get("peak_neighbor") == PEAK_NEIGHBOR_GRID_STEP)
    support_rule = bool(settings and settings.get("grid_suppress_rule") == GRID_SUPPRESS_KEPT_TILE_SUBSET)
    if grid_step and samples is None:
        raise T3Error("격자 이웃 비교에 샘플이 없습니다.", stage="peak")
    if support_rule:
        if not grid_step or samples is None:
            raise T3Error("타일 지지 비교에 격자 이웃 설정이 없습니다.", stage="peak")
        _apply_kept_neighbor_tile_support(rows, passers, samples)
    else:
        _apply_score_peak(rows, passers, samples, grid_step)
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


def _differences_tiles(samples: list[dict[str, Any]], raws: list[bytes], settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not samples:
        return []
    width = int(samples[0]["width"])
    height = int(samples[0]["height"])
    for sample, raw in zip(samples, raws):
        if int(sample["width"]) != width or int(sample["height"]) != height or len(raw) != width * height:
            raise T3Error("샘플 이미지 크기가 서로 다릅니다.", stage="difference")
    tile_width = int(settings["tile_width"])
    tile_height = int(settings["tile_height"])
    layout = tile_layout(width, height, tile_width, tile_height)
    rows = []
    for index in range(1, len(samples)):
        before = samples[index - 1]
        after = samples[index]
        global_mean, tile_values = frame_tile_differences(
            raws[index - 1], raws[index], width, height, layout, tile_width, tile_height,
        )
        rows.append({
            "index": index,
            "before_sample": before["sample_index"],
            "after_sample": after["sample_index"],
            "elapsed_video_seconds": after["video_seconds"] - before["video_seconds"],
            "global_mean": global_mean,
            "t": after["video_seconds"],
            "tile_values": tile_values,
        })
    return _finish_tile_rows(rows, layout, samples, settings)


def _assert_temporal_settings(settings: dict[str, Any]) -> None:
    if settings.get("temporal_confirm") != "after_peak_keep":
        raise T3Error("B02 시간 확인 순서가 설정과 다릅니다.", stage="temporal")
    if settings.get("temporal_offsets_seconds") != ["1.0", "2.0"]:
        raise T3Error("B02 비교 시점이 설정과 다릅니다.", stage="temporal")
    if settings.get("temporal_match_tolerance_seconds") != "0.5":
        raise T3Error("B02 시각 허용이 설정과 다릅니다.", stage="temporal")
    if settings.get("temporal_revert_absolute") != "0.005":
        raise T3Error("B02 복귀 하한이 설정과 다릅니다.", stage="temporal")
    if settings.get("temporal_rule") != TEMPORAL_RULE:
        raise T3Error("B02 시간 판정 규칙이 설정과 다릅니다.", stage="temporal")
    if settings.get("temporal_on_insufficient") != "keep":
        raise T3Error("B02 증거 부족 처리가 설정과 다릅니다.", stage="temporal")


def _tile_reverted(d_reference: Decimal, d_boundary: Decimal) -> bool:
    return d_reference <= ABSOLUTE_THRESHOLD and d_boundary > d_reference


def _judge_temporal(
    passing: list[int],
    comparisons: list[dict[str, Any]],
    *,
    stage: str,
) -> tuple[str, str]:
    offsets = [as_decimal(item["offset_seconds"], "offset") for item in comparisons]
    if offsets != list(TEMPORAL_OFFSETS):
        raise T3Error("B02 비교 시점이 설정과 다릅니다.", stage=stage)
    if not passing:
        return "keep", "insufficient_evidence"
    ok_count = 0
    persisted = False
    for item in comparisons:
        status = item.get("status")
        if status not in {"ok", "unavailable", "too_late"}:
            raise T3Error("B02 비교 상태가 계약 밖입니다.", stage=stage)
        tiles = item.get("tiles")
        if not isinstance(tiles, list):
            raise T3Error("B02 비교 타일이 없습니다.", stage=stage)
        if status != "ok":
            if tiles:
                raise T3Error("B02 무효 비교에 타일 값이 있습니다.", stage=stage)
            continue
        ok_count += 1
        if [int(tile["tile_index"]) for tile in tiles] != list(passing):
            raise T3Error("B02 비교 타일이 통과 타일과 다릅니다.", stage=stage)
        for tile in tiles:
            d_reference = as_decimal(tile["d_reference"], "d_reference")
            d_boundary = as_decimal(tile["d_boundary"], "d_boundary")
            reverted = _tile_reverted(d_reference, d_boundary)
            if bool(tile["reverted"]) != reverted:
                raise T3Error("B02 타일 복귀 표시가 계산과 다릅니다.", stage=stage)
            if not reverted:
                persisted = True
    if ok_count < 2:
        return "keep", "insufficient_samples"
    if persisted:
        return "keep", "persists"
    return "suppress", "reverted"


def _temporal_comparison(
    samples: list[dict[str, Any]],
    raws: list[bytes],
    layout: list[dict[str, int]],
    width: int,
    height: int,
    tile_width: int,
    tile_height: int,
    boundary_time: Decimal,
    offset: Decimal,
    end: Decimal,
    before_index: int,
    after_index: int,
    passing: list[int],
) -> dict[str, Any]:
    target = quantize_seconds(boundary_time + offset)
    chosen = None
    for sample in samples:
        sample_time = as_decimal(sample["video_seconds"], "sample")
        if sample_time >= target and sample_time < end:
            chosen = sample
            break
    row: dict[str, Any] = {
        "offset_seconds": offset,
        "target_video_seconds": target,
        "status": "unavailable",
        "reason": "후보 끝 이전에 비교 샘플이 없습니다.",
        "sample_index": None,
        "video_seconds": None,
        "lateness_seconds": None,
        "tiles": [],
    }
    if chosen is None:
        return row
    chosen_time = as_decimal(chosen["video_seconds"], "sample")
    lateness = quantize_seconds(chosen_time - target)
    row["sample_index"] = int(chosen["sample_index"])
    row["video_seconds"] = chosen_time
    row["lateness_seconds"] = lateness
    if lateness > TEMPORAL_MATCH_TOLERANCE:
        row["status"] = "too_late"
        row["reason"] = "비교 샘플이 목표보다 0.5초를 넘겨 늦습니다."
        return row
    row["status"] = "ok"
    row["reason"] = None
    if not passing:
        return row
    sample_index = int(chosen["sample_index"])
    _, reference_values = frame_tile_differences(
        raws[before_index], raws[sample_index], width, height, layout, tile_width, tile_height,
    )
    _, boundary_values = frame_tile_differences(
        raws[after_index], raws[sample_index], width, height, layout, tile_width, tile_height,
    )
    tiles = []
    for tile_index in passing:
        d_reference = reference_values[tile_index]
        d_boundary = boundary_values[tile_index]
        tiles.append({
            "tile_index": tile_index,
            "d_reference": d_reference,
            "d_boundary": d_boundary,
            "reverted": _tile_reverted(d_reference, d_boundary),
        })
    row["tiles"] = tiles
    return row


def _apply_temporal(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    raws: list[bytes],
    start: Decimal,
    end: Decimal,
    settings: dict[str, Any],
) -> None:
    del start
    _assert_temporal_settings(settings)
    if not differences:
        return
    width = int(samples[0]["width"])
    height = int(samples[0]["height"])
    tile_width = int(settings["tile_width"])
    tile_height = int(settings["tile_height"])
    layout = tile_layout(width, height, tile_width, tile_height)
    for row in differences:
        if row.get("peak_action") != "keep":
            row["temporal_action"] = "not_applicable"
            row["temporal_reason"] = "not_a_b01_boundary"
            row["temporal"] = None
            continue
        before_index = int(row["before_sample"])
        after_index = int(row["after_sample"])
        boundary_time = as_decimal(samples[after_index]["video_seconds"], "boundary")
        passing = [int(item) for item in row["passing_tile_indices"]]
        comparisons = [
            _temporal_comparison(
                samples, raws, layout, width, height, tile_width, tile_height,
                boundary_time, offset, end, before_index, after_index, passing,
            )
            for offset in TEMPORAL_OFFSETS
        ]
        action, reason = _judge_temporal(passing, comparisons, stage="temporal")
        row["temporal_action"] = action
        row["temporal_reason"] = reason
        row["temporal"] = {
            "reference_sample": before_index,
            "reference_video_seconds": as_decimal(samples[before_index]["video_seconds"], "reference"),
            "required_offsets": list(TEMPORAL_OFFSETS),
            "match_tolerance_seconds": TEMPORAL_MATCH_TOLERANCE,
            "revert_absolute": ABSOLUTE_THRESHOLD,
            "rule": TEMPORAL_RULE,
            "comparisons": comparisons,
        }


def _boundaries_temporal(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
) -> list[dict[str, Any]]:
    kept = []
    for difference in differences:
        action = difference.get("temporal_action")
        if action is None:
            raise T3Error("B02 시간 판정 필드가 없습니다.", stage="temporal")
        if difference.get("peak_action") != "keep":
            if action != "not_applicable" or difference.get("temporal") is not None:
                raise T3Error("B02 비경계 차분의 시간 판정이 잘못되었습니다.", stage="temporal")
            if difference.get("temporal_reason") != "not_a_b01_boundary":
                raise T3Error("B02 비경계 이유가 잘못되었습니다.", stage="temporal")
            continue
        if action not in {"keep", "suppress"} or not isinstance(difference.get("temporal"), dict):
            raise T3Error("B02 시간 판정이 계약 밖입니다.", stage="temporal")
        if action != "keep":
            continue
        kept.append(difference)
    return _boundaries(kept, samples, start, end)


def _assert_spatial_settings(settings: dict[str, Any]) -> None:
    if settings.get("spatial_confirm") != "after_peak_keep":
        raise T3Error("B03 공간 확인 순서가 설정과 다릅니다.", stage="spatial")
    if settings.get("spatial_pixel") != SPATIAL_PIXEL:
        raise T3Error("B03 화소 임계가 설정과 다릅니다.", stage="spatial")
    if settings.get("spatial_fringe") != SPATIAL_FRINGE:
        raise T3Error("B03 가장자리가 설정과 다릅니다.", stage="spatial")
    if settings.get("spatial_centroid_neighborhood") != SPATIAL_NEIGHBORHOOD:
        raise T3Error("B03 중심 이웃이 설정과 다릅니다.", stage="spatial")
    if settings.get("spatial_shift_limit") != "in_frame":
        raise T3Error("B03 이동 한계가 설정과 다릅니다.", stage="spatial")
    if settings.get("spatial_connectivity") != 4:
        raise T3Error("B03 연결이 설정과 다릅니다.", stage="spatial")
    if settings.get("spatial_rule") != SPATIAL_RULE:
        raise T3Error("B03 공간 판정 규칙이 설정과 다릅니다.", stage="spatial")
    if settings.get("spatial_on_uncertain") != "keep":
        raise T3Error("B03 불확실 처리가 설정과 다릅니다.", stage="spatial")


def _spatial_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise T3Error(f"B03 {label}가 정수가 아닙니다.", stage="spatial")
    return value


def _nearest_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise T3Error("B03 중심 분모가 유효하지 않습니다.", stage="spatial")
    sign = 1 if numerator >= 0 else -1
    return sign * ((abs(numerator) + denominator // 2) // denominator)


def _shift_rows(rows: dict[int, int], dx: int, dy: int) -> dict[int, int]:
    shifted: dict[int, int] = {}
    for y, bits in rows.items():
        moved = bits << dx if dx >= 0 else bits >> (-dx)
        shifted[y + dy] = moved
    return shifted


def _dilate_rows(rows: dict[int, int], fringe: int) -> dict[int, int]:
    horizontal: dict[int, int] = {}
    for y, bits in rows.items():
        value = bits
        for step in range(1, fringe + 1):
            value |= bits << step
            value |= bits >> step
        horizontal[y] = value
    dilated: dict[int, int] = {}
    for y, bits in horizontal.items():
        for offset in range(-fringe, fringe + 1):
            dilated[y + offset] = dilated.get(y + offset, 0) | bits
    return dilated


def _rows_cover(rows: dict[int, int], dilated: dict[int, int]) -> bool:
    for y, bits in rows.items():
        if bits & ~dilated.get(y, 0):
            return False
    return True


def _component_record(pixels: list[tuple[int, int]]) -> dict[str, Any]:
    rows: dict[int, int] = {}
    sum_x = 0
    sum_y = 0
    min_x = pixels[0][0]
    max_x = pixels[0][0]
    min_y = pixels[0][1]
    max_y = pixels[0][1]
    for x, y in pixels:
        rows[y] = rows.get(y, 0) | (1 << x)
        sum_x += x
        sum_y += y
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y
    count = len(pixels)
    return {
        "pixels": pixels,
        "rows": rows,
        "dilated": _dilate_rows(rows, SPATIAL_FRINGE),
        "count": count,
        "sum_x": sum_x,
        "sum_y": sum_y,
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
        "width": max_x - min_x + 1,
        "height": max_y - min_y + 1,
    }


def _change_components(before: bytes, after: bytes, width: int, height: int) -> tuple[int, list[dict[str, Any]]]:
    total = width * height
    if len(before) != total or len(after) != total:
        raise T3Error("차분할 이미지 크기가 다릅니다.", stage="spatial")
    mask = bytearray(total)
    changed = 0
    before_view = memoryview(before)
    after_view = memoryview(after)
    for index in range(total):
        if abs(int(before_view[index]) - int(after_view[index])) >= SPATIAL_PIXEL:
            mask[index] = 1
            changed += 1
    labels = [-1] * total
    components: list[dict[str, Any]] = []
    for seed in range(total):
        if mask[seed] == 0 or labels[seed] != -1:
            continue
        component_id = len(components)
        labels[seed] = component_id
        stack = [seed]
        pixels: list[tuple[int, int]] = []
        while stack:
            current = stack.pop()
            x = current % width
            y = current // width
            pixels.append((x, y))
            if x > 0:
                nxt = current - 1
                if mask[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
            if x + 1 < width:
                nxt = current + 1
                if mask[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
            if y > 0:
                nxt = current - width
                if mask[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
            if y + 1 < height:
                nxt = current + width
                if mask[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
        components.append(_component_record(pixels))
    return changed, components


def _shape_translates(source: dict[str, Any], destination: dict[str, Any], dx: int, dy: int, width: int, height: int) -> bool:
    if source["min_x"] + dx < 0 or source["max_x"] + dx >= width:
        return False
    if source["min_y"] + dy < 0 or source["max_y"] + dy >= height:
        return False
    shifted = _shift_rows(source["rows"], dx, dy)
    if not _rows_cover(shifted, destination["dilated"]):
        return False
    return _rows_cover(destination["rows"], _dilate_rows(shifted, SPATIAL_FRINGE))


def _mask_rows(components: list[dict[str, Any]], height: int) -> list[int]:
    rows = [0] * height
    for component in components:
        for y, bits in component["rows"].items():
            rows[y] |= bits
    return rows


def _geom_has_residual(rows: list[int], dx: int, dy: int, height: int) -> bool:
    """Gray 이동의 상위 조건. 겹치는 변경 화소만으로도 설명되지 않으면 값 비교를 하지 않는다."""
    explained = [0] * height
    for y, bits in enumerate(rows):
        y2 = y + dy
        if y2 < 0 or y2 >= height or not bits:
            continue
        other = rows[y2]
        both = bits & (other >> dx) if dx >= 0 else bits & (other << (-dx))
        if not both:
            continue
        explained[y] |= both
        if dx >= 0:
            explained[y2] |= both << dx
        else:
            explained[y2] |= both >> (-dx)
    dilated = [0] * height
    for y, bits in enumerate(explained):
        if not bits:
            continue
        band = bits | (bits << SPATIAL_FRINGE) | (bits >> SPATIAL_FRINGE)
        for offset in range(-SPATIAL_FRINGE, SPATIAL_FRINGE + 1):
            yy = y + offset
            if 0 <= yy < height:
                dilated[yy] |= band
    for y, bits in enumerate(rows):
        if bits & ~dilated[y]:
            return True
    return False


def _shift_transport(
    changed: list[tuple[int, int]],
    mask: bytearray,
    before: bytes,
    after: bytes,
    dx: int,
    dy: int,
    width: int,
    height: int,
) -> tuple[int, int, int]:
    before_view = memoryview(before)
    after_view = memoryview(after)
    explained: set[int] = set()
    source_count = 0
    for x, y in changed:
        nx = x + dx
        ny = y + dy
        if nx < 0 or ny < 0 or nx >= width or ny >= height:
            continue
        destination_index = ny * width + nx
        if mask[destination_index] == 0:
            continue
        if abs(int(before_view[y * width + x]) - int(after_view[destination_index])) >= SPATIAL_PIXEL:
            continue
        source_count += 1
        explained.add(y * width + x)
        explained.add(destination_index)
    if source_count < 1:
        return 0, 0, 1
    residual = 0
    for x, y in changed:
        index = y * width + x
        if index in explained:
            continue
        near = False
        for oy in range(y - SPATIAL_FRINGE, y + SPATIAL_FRINGE + 1):
            if oy < 0 or oy >= height:
                continue
            base = oy * width
            for ox in range(x - SPATIAL_FRINGE, x + SPATIAL_FRINGE + 1):
                if 0 <= ox < width and (base + ox) in explained:
                    near = True
                    break
            if near:
                break
        if not near:
            return source_count, len(explained), 1
    return source_count, len(explained), residual


def _spatial_block(before: bytes, after: bytes, width: int, height: int, before_index: int, after_index: int, before_time: Decimal, after_time: Decimal) -> dict[str, Any]:
    changed_count, components = _change_components(before, after, width, height)
    block: dict[str, Any] = {
        "before_sample": before_index,
        "after_sample": after_index,
        "before_video_seconds": before_time,
        "after_video_seconds": after_time,
        "pixel_threshold": SPATIAL_PIXEL,
        "fringe": SPATIAL_FRINGE,
        "centroid_neighborhood": SPATIAL_NEIGHBORHOOD,
        "shift_limit": "in_frame",
        "connectivity": 4,
        "rule": SPATIAL_RULE,
        "change_pixel_count": changed_count,
        "component_count": len(components),
        "shape_shift_count": 0,
        "explained_shift_count": 0,
        "shift": None,
    }
    if len(components) < 2:
        return block
    limit = 2 * SPATIAL_FRINGE
    witnesses: dict[tuple[int, int], tuple[int, int]] = {}
    for source_index, source in enumerate(components):
        for destination_index, destination in enumerate(components):
            if source_index == destination_index:
                continue
            if abs(int(source["width"]) - int(destination["width"])) > limit:
                continue
            if abs(int(source["height"]) - int(destination["height"])) > limit:
                continue
            denominator = int(source["count"]) * int(destination["count"])
            dx0 = _nearest_div(
                int(destination["sum_x"]) * int(source["count"]) - int(source["sum_x"]) * int(destination["count"]),
                denominator,
            )
            dy0 = _nearest_div(
                int(destination["sum_y"]) * int(source["count"]) - int(source["sum_y"]) * int(destination["count"]),
                denominator,
            )
            for oy in range(-SPATIAL_NEIGHBORHOOD, SPATIAL_NEIGHBORHOOD + 1):
                for ox in range(-SPATIAL_NEIGHBORHOOD, SPATIAL_NEIGHBORHOOD + 1):
                    dx = dx0 + ox
                    dy = dy0 + oy
                    if dx == 0 and dy == 0:
                        continue
                    if not _shape_translates(source, destination, dx, dy, width, height):
                        continue
                    key = (dx, dy)
                    witness = (source_index, destination_index)
                    current = witnesses.get(key)
                    if current is None or witness < current:
                        witnesses[key] = witness
    block["shape_shift_count"] = len(witnesses)
    if not witnesses:
        return block
    mask = bytearray(width * height)
    changed_pixels: list[tuple[int, int]] = []
    for component in components:
        for pixel in component["pixels"]:
            changed_pixels.append(pixel)
            mask[pixel[1] * width + pixel[0]] = 1
    mask_rows = _mask_rows(components, height)
    passes: list[tuple[tuple[int, int, int, int, int, int], dict[str, Any]]] = []
    for (dx, dy), (source_index, destination_index) in witnesses.items():
        if _geom_has_residual(mask_rows, dx, dy, height):
            continue
        source_count, explained_count, residual = _shift_transport(
            changed_pixels, mask, before, after, dx, dy, width, height,
        )
        if source_count < 1 or residual != 0:
            continue
        shift = {
            "dx": dx,
            "dy": dy,
            "source_component": source_index,
            "destination_component": destination_index,
            "source_count": source_count,
            "explained_count": explained_count,
            "residual_count": residual,
        }
        rank = (-source_count, abs(dx) + abs(dy), dy, dx, source_index, destination_index)
        passes.append((rank, shift))
    block["explained_shift_count"] = len(passes)
    if passes:
        block["shift"] = min(passes, key=lambda item: item[0])[1]
    return block


def _judge_spatial(block: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(block, dict):
        raise T3Error("B03 공간 판정 입력이 없습니다.", stage="spatial")
    if _spatial_int(block.get("pixel_threshold"), "화소 임계") != SPATIAL_PIXEL:
        raise T3Error("B03 화소 임계가 저장값과 다릅니다.", stage="spatial")
    if _spatial_int(block.get("fringe"), "가장자리") != SPATIAL_FRINGE:
        raise T3Error("B03 가장자리가 저장값과 다릅니다.", stage="spatial")
    if _spatial_int(block.get("centroid_neighborhood"), "중심 이웃") != SPATIAL_NEIGHBORHOOD:
        raise T3Error("B03 중심 이웃이 저장값과 다릅니다.", stage="spatial")
    if block.get("shift_limit") != "in_frame":
        raise T3Error("B03 이동 한계가 저장값과 다릅니다.", stage="spatial")
    if _spatial_int(block.get("connectivity"), "연결") != 4:
        raise T3Error("B03 연결이 저장값과 다릅니다.", stage="spatial")
    if block.get("rule") != SPATIAL_RULE:
        raise T3Error("B03 공간 판정 규칙이 저장값과 다릅니다.", stage="spatial")
    change_count = _spatial_int(block.get("change_pixel_count"), "변경 화소 수")
    component_count = _spatial_int(block.get("component_count"), "성분 수")
    shape_count = _spatial_int(block.get("shape_shift_count"), "형태 이동 수")
    explained_count = _spatial_int(block.get("explained_shift_count"), "설명 이동 수")
    if min(change_count, component_count, shape_count, explained_count) < 0:
        raise T3Error("B03 공간 집계가 음수입니다.", stage="spatial")
    if (change_count == 0) != (component_count == 0):
        raise T3Error("B03 변경 화소와 성분 수가 맞지 않습니다.", stage="spatial")
    if shape_count < explained_count:
        raise T3Error("B03 설명된 이동이 형태 이동보다 많습니다.", stage="spatial")
    shift = block.get("shift")
    if component_count < 2 and (shape_count or explained_count or shift is not None):
        raise T3Error("B03 성분 둘 미만인데 이동이 있습니다.", stage="spatial")
    if change_count == 0:
        if shift is not None or shape_count or explained_count:
            raise T3Error("B03 변경 형태가 없는데 이동이 있습니다.", stage="spatial")
        return "keep", "no_change_shape"
    if shift is None:
        if explained_count != 0:
            raise T3Error("B03 유지 판정에 통과 이동이 남아 있습니다.", stage="spatial")
        return "keep", "not_explained"
    if not isinstance(shift, dict) or explained_count < 1:
        raise T3Error("B03 억제 이동이 없습니다.", stage="spatial")
    dx = _spatial_int(shift.get("dx"), "dx")
    dy = _spatial_int(shift.get("dy"), "dy")
    if dx == 0 and dy == 0:
        raise T3Error("B03 이동이 영입니다.", stage="spatial")
    source_component = _spatial_int(shift.get("source_component"), "출발 성분")
    destination_component = _spatial_int(shift.get("destination_component"), "도착 성분")
    if source_component == destination_component or min(source_component, destination_component) < 0:
        raise T3Error("B03 성분 쌍이 유효하지 않습니다.", stage="spatial")
    if source_component >= component_count or destination_component >= component_count:
        raise T3Error("B03 성분 번호가 범위를 벗어났습니다.", stage="spatial")
    source_count = _spatial_int(shift.get("source_count"), "source 수")
    covered_count = _spatial_int(shift.get("explained_count"), "explained 수")
    residual_count = _spatial_int(shift.get("residual_count"), "residual 수")
    if source_count < 1 or residual_count != 0:
        raise T3Error("B03 저장된 이동이 통과 조건과 다릅니다.", stage="spatial")
    if covered_count < source_count or covered_count > change_count:
        raise T3Error("B03 explained 수가 변경 화소와 맞지 않습니다.", stage="spatial")
    return "suppress", "translated"


def _apply_spatial(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    raws: list[bytes],
    settings: dict[str, Any],
) -> None:
    _assert_spatial_settings(settings)
    if not differences:
        return
    width = int(samples[0]["width"])
    height = int(samples[0]["height"])
    for row in differences:
        if row.get("peak_action") != "keep":
            row["spatial_action"] = "not_applicable"
            row["spatial_reason"] = "not_a_b01_boundary"
            row["spatial"] = None
            continue
        before_index = int(row["before_sample"])
        after_index = int(row["after_sample"])
        block = _spatial_block(
            raws[before_index],
            raws[after_index],
            width,
            height,
            before_index,
            after_index,
            as_decimal(samples[before_index]["video_seconds"], "before"),
            as_decimal(samples[after_index]["video_seconds"], "after"),
        )
        action, reason = _judge_spatial(block)
        row["spatial_action"] = action
        row["spatial_reason"] = reason
        row["spatial"] = block


def _boundaries_spatial(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
) -> list[dict[str, Any]]:
    kept = []
    for difference in differences:
        action = difference.get("spatial_action")
        if action is None:
            raise T3Error("B03 공간 판정 필드가 없습니다.", stage="spatial")
        if difference.get("peak_action") != "keep":
            if action != "not_applicable" or difference.get("spatial") is not None:
                raise T3Error("B03 비경계 차분의 공간 판정이 잘못되었습니다.", stage="spatial")
            if difference.get("spatial_reason") != "not_a_b01_boundary":
                raise T3Error("B03 비경계 이유가 잘못되었습니다.", stage="spatial")
            continue
        if action not in {"keep", "suppress"} or not isinstance(difference.get("spatial"), dict):
            raise T3Error("B03 공간 판정이 계약 밖입니다.", stage="spatial")
        if action != "keep":
            continue
        kept.append(difference)
    return _boundaries(kept, samples, start, end)


def _assert_latch_settings(settings: dict[str, Any]) -> None:
    if settings.get("content_channel") != "residual_latch":
        raise T3Error("B06 잔차 래치 설정이 없습니다.", stage="content")
    if int(settings.get("residual_gate") or 0) != CONTENT_GATE:
        raise T3Error("B06 잔차 게이트가 다릅니다.", stage="content")
    if int(settings.get("exposure_median_min") or 0) != CONTENT_EXPOSURE_MEDIAN:
        raise T3Error("B06 노출 중앙값 하한이 다릅니다.", stage="content")
    if int(settings.get("exposure_cover_percent") or 0) != CONTENT_EXPOSURE_COVER:
        raise T3Error("B06 노출 비율이 다릅니다.", stage="content")
    if int(settings.get("register_radius") or 0) != CONTENT_REGISTER_RADIUS:
        raise T3Error("B06 등록 반경이 다릅니다.", stage="content")
    if settings.get("content_confirm_gap_seconds") != "1":
        raise T3Error("B06 확인 간격이 다릅니다.", stage="content")
    if int(settings.get("content_connect") or 0) != 4 or int(settings.get("content_episode_gap") or 0) != CONTENT_EPISODE_GAP:
        raise T3Error("B06 연결 규칙이 다릅니다.", stage="content")
    if settings.get("peak_neighbor") != PEAK_NEIGHBOR_GRID_STEP or settings.get("grid_suppress_rule") != GRID_SUPPRESS_KEPT_TILE_SUBSET:
        raise T3Error("B06의 B05 타일 판정 설정이 다릅니다.", stage="content")


def _exposure_shift(current: bytes, reference: bytearray) -> tuple[bool, int | Decimal]:
    total = len(current)
    hist = [0] * 511
    cur = memoryview(current)
    ref = memoryview(reference)
    for index in range(total):
        hist[int(cur[index]) - int(ref[index]) + 255] += 1
    if total % 2 == 1:
        target = total // 2
        acc = 0
        median_twice = 0
        for value, count in enumerate(hist):
            acc += count
            if acc > target:
                median_twice = (value - 255) * 2
                break
    else:
        first_target = total // 2 - 1
        second_target = total // 2
        first = second = 0
        seen_first = False
        acc = 0
        for value, count in enumerate(hist):
            acc += count
            if not seen_first and acc > first_target:
                first = value - 255
                seen_first = True
            if acc > second_target:
                second = value - 255
                break
        median_twice = first + second
    if median_twice % 2 == 0:
        median: int | Decimal = median_twice // 2
    else:
        median = Decimal(median_twice) / Decimal(2)
    if abs(median_twice) < CONTENT_EXPOSURE_MEDIAN * 2:
        return False, median
    covered = 0
    limit = CONTENT_GATE * 2
    for index in range(total):
        delta = int(cur[index]) - int(ref[index])
        if abs(delta * 2 - median_twice) < limit:
            covered += 1
    return covered * 100 >= CONTENT_EXPOSURE_COVER * total, median


def _best_registration(current: bytes, reference: bytearray, width: int, height: int) -> tuple[int, int, int, bytearray, int]:
    total = width * height
    cur = memoryview(current)
    ref = memoryview(reference)
    best_rank: tuple[int, int, int, int] | None = None
    best: tuple[int, int, int, bytearray, int] | None = None
    for dy in range(-CONTENT_REGISTER_RADIUS, CONTENT_REGISTER_RADIUS + 1):
        for dx in range(-CONTENT_REGISTER_RADIUS, CONTENT_REGISTER_RADIUS + 1):
            explained = 0
            residual = bytearray(total)
            residual_count = 0
            for y in range(height):
                source_y = y - dy
                if source_y < 0 or source_y >= height:
                    continue
                row = y * width
                source_row = source_y * width
                for x in range(width):
                    source_x = x - dx
                    if source_x < 0 or source_x >= width:
                        continue
                    if abs(int(cur[row + x]) - int(ref[source_row + source_x])) < CONTENT_GATE:
                        explained += 1
                    else:
                        residual[row + x] = 1
                        residual_count += 1
            rank = (explained, -(abs(dx) + abs(dy)), -dy, -dx)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = (dx, dy, explained, residual, residual_count)
            if dx == 0 and dy == 0 and explained == total:
                return best
    if best is None:
        raise T3Error("B06 등록 결과가 없습니다.", stage="content")
    return best


def _stable_groups(stable: bytearray, width: int, height: int) -> list[list[int]]:
    total = width * height
    labels = [-1] * total
    components: list[list[int]] = []
    for seed in range(total):
        if not stable[seed] or labels[seed] != -1:
            continue
        component_id = len(components)
        labels[seed] = component_id
        stack = [seed]
        pixels: list[int] = []
        while stack:
            current = stack.pop()
            pixels.append(current)
            x = current % width
            y = current // width
            if x > 0:
                nxt = current - 1
                if stable[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
            if x + 1 < width:
                nxt = current + 1
                if stable[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
            if y > 0:
                nxt = current - width
                if stable[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
            if y + 1 < height:
                nxt = current + width
                if stable[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
        components.append(pixels)
    parent = list(range(len(components)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for index, flag in enumerate(stable):
        if not flag:
            continue
        x = index % width
        y = index // width
        for ox, oy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx = x + ox
            ny = y + oy
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            other = ny * width + nx
            if stable[other] and labels[other] != labels[index]:
                union(labels[index], labels[other])
    grouped: dict[int, list[int]] = {}
    for component_id, pixels in enumerate(components):
        grouped.setdefault(find(component_id), []).extend(pixels)
    return sorted(grouped.values(), key=lambda pixels: min(pixels))


def _episode_match(
    pixels: list[int],
    absorbed: bytearray,
    episode_at: list[int],
    width: int,
    height: int,
) -> int | None:
    counts: dict[int, int] = {}
    seen: set[int] = set()
    for pixel in pixels:
        x = pixel % width
        y = pixel // width
        for oy in range(-CONTENT_EPISODE_GAP, CONTENT_EPISODE_GAP + 1):
            ny = y + oy
            if ny < 0 or ny >= height:
                continue
            row = ny * width
            for ox in range(-CONTENT_EPISODE_GAP, CONTENT_EPISODE_GAP + 1):
                nx = x + ox
                if nx < 0 or nx >= width:
                    continue
                neighbor = row + nx
                if not absorbed[neighbor] or neighbor in seen:
                    continue
                seen.add(neighbor)
                episode_id = episode_at[neighbor]
                counts[episode_id] = counts.get(episode_id, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda episode_id: (counts[episode_id], -episode_id))


def _touches_moving(pixels: list[int], moving: bytearray, width: int, height: int) -> bool:
    for pixel in pixels:
        x = pixel % width
        y = pixel // width
        for oy in range(-1, 2):
            ny = y + oy
            if ny < 0 or ny >= height:
                continue
            row = ny * width
            for ox in range(-1, 2):
                nx = x + ox
                if nx < 0 or nx >= width:
                    continue
                if moving[row + nx]:
                    return True
    return False


def _content_geometry(pixels: list[int], width: int) -> tuple[dict[str, int] | None, dict[str, Decimal] | None]:
    if not pixels:
        return None, None
    min_x = min_y = 10**9
    max_x = max_y = -1
    sum_x = 0
    sum_y = 0
    for pixel in pixels:
        x = pixel % width
        y = pixel // width
        sum_x += x
        sum_y += y
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y
    count = len(pixels)
    return (
        {"x": min_x, "y": min_y, "width": max_x - min_x + 1, "height": max_y - min_y + 1},
        {
            "x": quantize_diff(Decimal(sum_x) / Decimal(count)),
            "y": quantize_diff(Decimal(sum_y) / Decimal(count)),
        },
    )


def _blank_content(
    row: dict[str, Any],
    *,
    action: str,
    sample_index: int,
    reference_sha256: str,
    exposure_median: int | Decimal | None,
    registration: dict[str, int] | None,
    stable_pixel_count: int,
    moving_pixel_count: int | None,
    protected: bool = False,
    confirm_sample_index: int | None = None,
    components: list[dict[str, Any]] | None = None,
    new_pixels: list[int] | None = None,
    width: int = 0,
) -> None:
    stored = components or []
    fresh = new_pixels or []
    episode_ids = [int(item["episode_id"]) for item in stored]
    contact = any(bool(item["contact_with_moving"]) for item in stored)
    bbox, centroid = _content_geometry(fresh, width)
    row["content_action"] = action
    row["content_episode_id"] = min(episode_ids) if episode_ids else None
    row["content_episode_ids"] = sorted(set(episode_ids))
    row["content_components"] = stored
    row["content_sample_index"] = sample_index
    row["confirm_sample_index"] = confirm_sample_index
    row["protected"] = protected
    row["registration"] = registration
    row["exposure_median"] = exposure_median
    row["stable_pixel_count"] = stable_pixel_count
    row["moving_pixel_count"] = moving_pixel_count
    row["bbox"] = bbox
    row["centroid"] = centroid
    row["contact_with_moving"] = contact
    row["reference_sha256"] = reference_sha256
    row["content_new_pixel_count"] = len(fresh)


def _apply_residual_latch(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    raws: list[bytes],
    settings: dict[str, Any],
) -> None:
    """B05 타일 판정은 바꾸지 않고, 기준 화면 대비 잔차만 내용 근거로 덧붙인다."""
    _assert_latch_settings(settings)
    if not differences:
        return
    width = int(samples[0]["width"])
    height = int(samples[0]["height"])
    total = width * height
    if len(raws) != len(samples):
        raise T3Error("B06 샘플과 프레임 수가 다릅니다.", stage="content")
    for sample, raw in zip(samples, raws):
        if int(sample["width"]) != width or int(sample["height"]) != height or len(raw) != total:
            raise T3Error("B06 분석 프레임 크기가 다릅니다.", stage="content")
    reference = bytearray(raws[0])
    absorbed = bytearray(total)
    episode_at = [-1] * total
    ink = bytearray(total)
    before_absorb = bytearray(total)
    next_episode = 0
    by_after = {int(row["after_sample"]): row for row in differences}
    for sample_index in range(1, len(samples)):
        row = by_after.get(sample_index)
        if row is None:
            raise T3Error("B06 차분과 샘플이 대응하지 않습니다.", stage="content", details={"sample_index": sample_index})
        current = raws[sample_index]
        reference_sha = sha256_bytes(reference)
        exposed, exposure_median = _exposure_shift(current, reference)
        if exposed:
            _blank_content(
                row,
                action="exposure",
                sample_index=sample_index,
                reference_sha256=reference_sha,
                exposure_median=exposure_median,
                registration=None,
                stable_pixel_count=0,
                moving_pixel_count=None,
            )
            continue
        dx, dy, explained, residual, residual_count = _best_registration(current, reference, width, height)
        registration = {
            "dx": dx,
            "dy": dy,
            "explained_pixel_count": explained,
            "residual_pixel_count": residual_count,
        }
        elapsed_ok = False
        moving_pixel_count: int | None = None
        stable = bytearray(total)
        moving = bytearray(total)
        stable_count = 0
        if sample_index + 1 < len(samples):
            elapsed = as_decimal(samples[sample_index + 1]["video_seconds"], "sample") - as_decimal(samples[sample_index]["video_seconds"], "sample")
            nxt = memoryview(raws[sample_index + 1])
            cur = memoryview(current)
            moving_count = 0
            for index in range(total):
                if abs(int(cur[index]) - int(nxt[index])) >= CONTENT_GATE:
                    moving[index] = 1
                    moving_count += 1
                elif residual[index]:
                    stable[index] = 1
                    stable_count += 1
            moving_pixel_count = moving_count
            elapsed_ok = elapsed <= CONTENT_CONFIRM_GAP
        components: list[dict[str, Any]] = []
        fresh: list[int] = []
        assignments: list[tuple[int, int]] = []
        if elapsed_ok and stable_count:
            for group in _stable_groups(stable, width, height):
                group_fresh: list[int] = []
                for pixel in group:
                    if not absorbed[pixel]:
                        group_fresh.append(pixel)
                        continue
                    if abs(int(current[pixel]) - int(before_absorb[pixel])) < CONTENT_GATE:
                        continue
                    group_fresh.append(pixel)
                if not group_fresh:
                    continue
                matched = _episode_match(group_fresh, absorbed, episode_at, width, height)
                if matched is None:
                    episode_id = next_episode
                    next_episode += 1
                    relation = "new"
                else:
                    episode_id = matched
                    relation = "existing"
                contact = _touches_moving(group, moving, width, height)
                components.append({
                    "episode_id": episode_id,
                    "relation": relation,
                    "new_pixel_count": len(group_fresh),
                    "contact_with_moving": contact,
                })
                fresh.extend(group_fresh)
                assignments.extend((pixel, episode_id) for pixel in group_fresh)
        fresh_mask = bytearray(total)
        for pixel, _episode_id in assignments:
            fresh_mask[pixel] = 1
        removal = 0
        unobservable = 0
        if any(absorbed):
            nxt_view = memoryview(raws[sample_index + 1]) if sample_index + 1 < len(samples) else None
            for pixel in range(total):
                if not absorbed[pixel] or fresh_mask[pixel]:
                    continue
                value = int(current[pixel])
                if abs(value - int(ink[pixel])) < CONTENT_GATE:
                    continue
                if abs(value - int(before_absorb[pixel])) < CONTENT_GATE:
                    if (
                        elapsed_ok
                        and nxt_view is not None
                        and abs(int(nxt_view[pixel]) - int(before_absorb[pixel])) < CONTENT_GATE
                    ):
                        removal += 1
                else:
                    unobservable += 1
        contact = any(bool(item["contact_with_moving"]) for item in components)
        has_new = any(item["relation"] == "new" for item in components)
        if fresh and elapsed_ok:
            if contact:
                action = "unresolved_contact"
            elif has_new:
                action = "birth"
            else:
                action = "extend"
            protected = True
            confirm_sample_index: int | None = sample_index + 1
        elif removal:
            action = "possible_removal"
            protected = False
            confirm_sample_index = None
            components = []
            fresh = []
        elif unobservable:
            action = "unobservable"
            protected = False
            confirm_sample_index = None
            components = []
            fresh = []
        elif residual_count:
            action = "short_unconfirmed"
            protected = False
            confirm_sample_index = None
            components = []
            fresh = []
        else:
            action = "none"
            protected = False
            confirm_sample_index = None
            components = []
            fresh = []
        _blank_content(
            row,
            action=action,
            sample_index=sample_index,
            reference_sha256=reference_sha,
            exposure_median=exposure_median,
            registration=registration,
            stable_pixel_count=stable_count if elapsed_ok else 0,
            moving_pixel_count=moving_pixel_count,
            protected=protected,
            confirm_sample_index=confirm_sample_index,
            components=components,
            new_pixels=fresh,
            width=width,
        )
        if protected:
            for pixel, episode_id in assignments:
                if not absorbed[pixel]:
                    before_absorb[pixel] = reference[pixel]
                ink[pixel] = current[pixel]
                reference[pixel] = current[pixel]
                absorbed[pixel] = 1
                episode_at[pixel] = episode_id


def _boundaries_content(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
) -> list[dict[str, Any]]:
    rows = []
    for difference in differences:
        keep = difference.get("peak_action") == "keep"
        protected = difference.get("protected") is True
        if not keep and not protected:
            continue
        after = samples[int(difference["after_sample"])]
        before = samples[int(difference["before_sample"])]
        boundary = as_decimal(after["video_seconds"], "boundary")
        if not (start < boundary < end):
            continue
        if keep and protected:
            origin = "b05_keep_with_content"
        elif keep:
            origin = "b05_keep"
        else:
            origin = "content"
        evidence = None
        if protected:
            evidence = {
                "content_action": difference.get("content_action"),
                "content_episode_id": difference.get("content_episode_id"),
                "content_episode_ids": list(difference.get("content_episode_ids") or []),
                "content_sample_index": difference.get("content_sample_index"),
                "confirm_sample_index": difference.get("confirm_sample_index"),
                "stable_pixel_count": difference.get("stable_pixel_count"),
                "content_new_pixel_count": difference.get("content_new_pixel_count"),
                "contact_with_moving": difference.get("contact_with_moving"),
            }
        rows.append({
            "difference_index": int(difference["index"]),
            "before_sample": int(before["sample_index"]),
            "after_sample": int(after["sample_index"]),
            "bracket_start": as_decimal(before["video_seconds"], "bracket"),
            "bracket_end": boundary,
            "video_seconds": boundary,
            "origin": origin,
            "content_evidence": evidence,
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


def _check_content_row(row: dict[str, Any], samples: list[dict[str, Any]], next_id: int, seen: set[int]) -> int:
    action = row.get("content_action")
    if action not in CONTENT_ACTIONS:
        raise T3Error("B06 내용 판정이 계약 밖입니다.", stage="replay", details={"difference_index": row.get("index")})
    protected = row.get("protected")
    if protected not in {True, False} or protected != (action in {"birth", "extend", "unresolved_contact"}):
        raise T3Error("B06 보호 플래그가 내용 판정과 다릅니다.", stage="replay", details={"difference_index": row.get("index")})
    after = int(row["after_sample"])
    if int(row.get("content_sample_index")) != after:
        raise T3Error("B06 내용 샘플이 차분과 다릅니다.", stage="replay", details={"difference_index": row.get("index")})
    reference = row.get("reference_sha256")
    if not isinstance(reference, str) or len(reference) != 64:
        raise T3Error("B06 기준 해시가 없습니다.", stage="replay", details={"difference_index": row.get("index")})
    components = row.get("content_components")
    if not isinstance(components, list):
        raise T3Error("B06 성분 목록이 없습니다.", stage="replay", details={"difference_index": row.get("index")})
    if action == "exposure":
        if row.get("registration") is not None:
            raise T3Error("B06 노출 프레임에 등록이 남아 있습니다.", stage="replay")
        if abs(as_decimal(row.get("exposure_median"), "exposure")) < CONTENT_EXPOSURE_MEDIAN:
            raise T3Error("B06 노출 중앙값이 하한보다 작습니다.", stage="replay")
    else:
        registration = row.get("registration")
        if not isinstance(registration, dict):
            raise T3Error("B06 등록 필드가 없습니다.", stage="replay", details={"difference_index": row.get("index")})
        dx = int(registration["dx"])
        dy = int(registration["dy"])
        if not (-CONTENT_REGISTER_RADIUS <= dx <= CONTENT_REGISTER_RADIUS and -CONTENT_REGISTER_RADIUS <= dy <= CONTENT_REGISTER_RADIUS):
            raise T3Error("B06 등록 이동이 반경 밖입니다.", stage="replay")
        if int(registration["explained_pixel_count"]) < 0 or int(registration["residual_pixel_count"]) < 0:
            raise T3Error("B06 등록 화소 수가 음수입니다.", stage="replay")
    if action in {"none", "exposure", "short_unconfirmed", "unobservable", "possible_removal"} and components:
        raise T3Error("B06 비보호 판정에 성분이 남아 있습니다.", stage="replay")
    if action == "short_unconfirmed" and int(row["registration"]["residual_pixel_count"]) < 1:
        raise T3Error("B06 미확인 기록에 잔차가 없습니다.", stage="replay")
    episode_ids: list[int] = []
    contact = False
    new_pixels = 0
    has_new = False
    for component in components:
        episode_id = int(component["episode_id"])
        relation = component.get("relation")
        count = int(component["new_pixel_count"])
        if count < 1 or relation not in {"new", "existing"}:
            raise T3Error("B06 성분이 유효하지 않습니다.", stage="replay")
        if component.get("contact_with_moving") not in {True, False}:
            raise T3Error("B06 접촉 플래그가 없습니다.", stage="replay")
        if relation == "new":
            if episode_id != next_id or episode_id in seen:
                raise T3Error("B06 새 에피소드 번호가 순서와 다릅니다.", stage="replay")
            next_id += 1
            has_new = True
        elif episode_id not in seen:
            raise T3Error("B06 기존 에피소드 번호가 없습니다.", stage="replay")
        seen.add(episode_id)
        episode_ids.append(episode_id)
        new_pixels += count
        contact = contact or bool(component["contact_with_moving"])
    if int(row.get("content_new_pixel_count") or 0) != new_pixels:
        raise T3Error("B06 새 화소 수가 성분과 다릅니다.", stage="replay")
    if bool(row.get("contact_with_moving")) != contact:
        raise T3Error("B06 접촉 요약이 성분과 다릅니다.", stage="replay")
    if episode_ids:
        if int(row.get("content_episode_id")) != min(episode_ids) or list(row.get("content_episode_ids") or []) != sorted(set(episode_ids)):
            raise T3Error("B06 에피소드 목록이 성분과 다릅니다.", stage="replay")
    elif row.get("content_episode_id") is not None or list(row.get("content_episode_ids") or []) != []:
        raise T3Error("B06 빈 에피소드 목록이 아닙니다.", stage="replay")
    if protected:
        if int(row.get("confirm_sample_index")) != after + 1 or after + 1 >= len(samples):
            raise T3Error("B06 확인 샘플이 없습니다.", stage="replay")
        gap = as_decimal(samples[after + 1]["video_seconds"], "sample") - as_decimal(samples[after]["video_seconds"], "sample")
        if gap > CONTENT_CONFIRM_GAP or new_pixels < 1:
            raise T3Error("B06 보호 경계의 확인 조건이 맞지 않습니다.", stage="replay")
        if contact and action != "unresolved_contact":
            raise T3Error("B06 접촉이 있는 경계를 다른 판정으로 적었습니다.", stage="replay")
        if not contact and has_new and action != "birth":
            raise T3Error("B06 새 에피소드를 birth로 적지 않았습니다.", stage="replay")
        if not contact and not has_new and action != "extend":
            raise T3Error("B06 기존 에피소드 확장을 extend로 적지 않았습니다.", stage="replay")
    elif row.get("confirm_sample_index") is not None:
        raise T3Error("B06 비보호 기록에 확인 샘플이 있습니다.", stage="replay")
    return next_id


def _replay_residual_latch(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _assert_latch_settings(settings)
    if settings.get("peak_neighbor") != PEAK_NEIGHBOR_GRID_STEP or settings.get("grid_suppress_rule") != GRID_SUPPRESS_KEPT_TILE_SUBSET:
        raise T3Error("B06의 B05 판정 설정이 없습니다.", stage="replay")
    decided = _decided_tile_rows(differences, samples, settings)
    stored = {int(row["index"]): row for row in differences}
    if len(stored) != len(differences):
        raise T3Error("B06 차분 index가 중복되었습니다.", stage="replay")
    for row in decided:
        previous = stored[int(row["index"])]
        if (
            previous.get("peak_action") != row["peak_action"]
            or previous.get("suppression_refs") != row["suppression_refs"]
            or previous.get("grid_support_reviews") != row.get("grid_support_reviews")
        ):
            raise T3Error(
                "B06의 B05 저장 판정과 재생 판정이 다릅니다.",
                stage="replay",
                details={"difference_index": row["index"]},
            )
    next_id = 0
    seen: set[int] = set()
    for row in sorted(differences, key=lambda item: int(item["index"])):
        next_id = _check_content_row(row, samples, next_id, seen)
    boundaries = _boundaries_content(differences, samples, start, end)
    return boundaries, _segments(start, end, boundaries, samples)


def replay_boundaries(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
    *,
    config_id: str,
    logic_version: str,
    schema_version: str,
    settings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """저장된 차분으로 임계값·피크·구간을 다시 계산한다. 버전 조합이 다르면 B00으로 넘기지 않는다."""
    identity = (config_id, logic_version, schema_version)
    if identity == B00_IDENTITY:
        return _replay_global(differences, samples, start, end)
    if identity == B01_IDENTITY:
        return _replay_tiles(differences, samples, start, end, settings or SETTINGS_B01)
    if identity == B02_IDENTITY:
        return _replay_temporal(differences, samples, start, end, settings or SETTINGS_B02)
    if identity == B03_IDENTITY:
        return _replay_spatial(differences, samples, start, end, settings or SETTINGS_B03)
    if identity == B04_IDENTITY:
        return _replay_grid_neighbor(differences, samples, start, end, settings or SETTINGS_B04)
    if identity == B05_IDENTITY:
        return _replay_grid_support(differences, samples, start, end, settings or SETTINGS_B05)
    if identity == B06_IDENTITY:
        return _replay_residual_latch(differences, samples, start, end, settings or SETTINGS_B06)
    raise T3Error(
        "알 수 없는 검출 버전 조합입니다.",
        stage="replay",
        details={"config_id": config_id, "logic_version": logic_version, "schema_version": schema_version},
    )


def _replay_global(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """B00 스칼라 차분을 재생한다."""
    rows = []
    for row in differences:
        rows.append({
            "index": int(row["index"]),
            "before_sample": int(row["before_sample"]),
            "after_sample": int(row["after_sample"]),
            "value": as_decimal(row["value"], "difference"),
            "t": as_decimal(samples[int(row["after_sample"])]["video_seconds"], "sample"),
        })
    for row in rows:
        others = [item for item in rows if item["index"] != row["index"]]
        nearby = [item for item in others if abs(item["t"] - row["t"]) <= NEIGHBOR_WINDOW]
        if len(nearby) >= MIN_NEIGHBORS:
            chosen = nearby
        elif len(others) >= MIN_NEIGHBORS:
            chosen = others
        else:
            chosen = []
        if not chosen:
            threshold = ABSOLUTE_THRESHOLD
        else:
            values = [item["value"] for item in chosen]
            middle_exact = median(values)
            mad_exact = median([abs(item - middle_exact) for item in values])
            threshold = quantize_diff(max(ABSOLUTE_THRESHOLD, middle_exact + MAD_MULTIPLIER * MAD_SCALE * mad_exact))
        row["passes_threshold"] = row["value"] > threshold
        row["threshold"] = threshold
    passers = [row for row in rows if row["passes_threshold"]]
    for row in rows:
        if not row["passes_threshold"]:
            row["peak_action"] = "below_threshold"
            continue
        suppressed = False
        for other in passers:
            if other["index"] == row["index"] or abs(other["t"] - row["t"]) > PEAK_RADIUS:
                continue
            later_tie = other["value"] == row["value"] and (
                other["t"] < row["t"] or (other["t"] == row["t"] and other["index"] < row["index"])
            )
            if other["value"] > row["value"] or later_tie:
                suppressed = True
                break
        row["peak_action"] = "suppress" if suppressed else "keep"
    kept = []
    for row in rows:
        if row["peak_action"] != "keep":
            continue
        kept.append({
            "index": row["index"],
            "before_sample": row["before_sample"],
            "after_sample": row["after_sample"],
            "peak_action": "keep",
        })
    boundaries = _boundaries(kept, samples, start, end)
    segments = _segments(start, end, boundaries, samples)
    return boundaries, segments


def _decided_tile_rows(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    if not samples:
        raise T3Error("타일 재생에 샘플이 없습니다.", stage="replay")
    width = int(samples[0]["width"])
    height = int(samples[0]["height"])
    for sample in samples:
        if int(sample["width"]) != width or int(sample["height"]) != height:
            raise T3Error("샘플 이미지 크기가 서로 다릅니다.", stage="replay")
    layout = tile_layout(width, height, int(settings["tile_width"]), int(settings["tile_height"]))
    if not differences:
        return []
    rows = []
    for row in differences:
        tiles = row.get("tiles")
        if not isinstance(tiles, list) or len(tiles) != len(layout):
            raise T3Error("B01 타일 값이 없습니다.", stage="replay")
        values = []
        for spec, tile in zip(layout, tiles):
            for key in ("tile_index", "row", "column", "x", "y", "width", "height", "pixel_count"):
                if int(tile[key]) != int(spec[key]):
                    raise T3Error("타일 배치가 설정과 다릅니다.", stage="replay", details={"field": key})
            values.append(as_decimal(tile["value"], "tile"))
        if "global_mean" not in row:
            raise T3Error("B01 global_mean이 없습니다.", stage="replay")
        rows.append({
            "index": int(row["index"]),
            "before_sample": int(row["before_sample"]),
            "after_sample": int(row["after_sample"]),
            "elapsed_video_seconds": as_decimal(row["elapsed_video_seconds"], "elapsed"),
            "global_mean": as_decimal(row["global_mean"], "global_mean"),
            "t": as_decimal(samples[int(row["after_sample"])]["video_seconds"], "sample"),
            "tile_values": values,
        })
    return _finish_tile_rows(rows, layout, samples, settings)


def _replay_tiles(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decided = _decided_tile_rows(differences, samples, settings)
    boundaries = _boundaries(decided, samples, start, end)
    return boundaries, _segments(start, end, boundaries, samples)


def _replay_grid_neighbor(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if settings.get("peak_neighbor") != PEAK_NEIGHBOR_GRID_STEP:
        raise T3Error("B04 격자 이웃 설정이 없습니다.", stage="replay")
    decided = _decided_tile_rows(differences, samples, settings)
    stored = {int(row["index"]): row for row in differences}
    for row in decided:
        previous = stored[int(row["index"])]
        if previous.get("peak_action") != row["peak_action"] or previous.get("suppression_refs") != row["suppression_refs"]:
            raise T3Error(
                "B04 저장 판정과 재생 판정이 다릅니다.",
                stage="replay",
                details={"difference_index": row["index"]},
            )
    boundaries = _boundaries(decided, samples, start, end)
    return boundaries, _segments(start, end, boundaries, samples)


def _replay_grid_support(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if settings.get("peak_neighbor") != PEAK_NEIGHBOR_GRID_STEP:
        raise T3Error("B05 격자 이웃 설정이 없습니다.", stage="replay")
    if settings.get("grid_suppress_rule") != GRID_SUPPRESS_KEPT_TILE_SUBSET:
        raise T3Error("B05 타일 지지 설정이 없습니다.", stage="replay")
    decided = _decided_tile_rows(differences, samples, settings)
    stored = {int(row["index"]): row for row in differences}
    for row in decided:
        previous = stored[int(row["index"])]
        if (
            previous.get("peak_action") != row["peak_action"]
            or previous.get("suppression_refs") != row["suppression_refs"]
            or previous.get("grid_support_reviews") != row.get("grid_support_reviews")
        ):
            raise T3Error(
                "B05 저장 판정과 재생 판정이 다릅니다.",
                stage="replay",
                details={"difference_index": row["index"]},
            )
    boundaries = _boundaries(decided, samples, start, end)
    return boundaries, _segments(start, end, boundaries, samples)


def _replay_temporal(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _assert_temporal_settings(settings)
    decided = _decided_tile_rows(differences, samples, settings)
    if len(decided) != len(differences):
        raise T3Error("B02 차분 수가 재생과 다릅니다.", stage="replay")
    stored_by_index: dict[int, dict[str, Any]] = {}
    for row in differences:
        if "temporal_action" not in row or "temporal_reason" not in row or "temporal" not in row:
            raise T3Error("B02 시간 판정 필드가 없습니다.", stage="replay")
        index = int(row["index"])
        if index in stored_by_index:
            raise T3Error("B02 차분 index가 중복되었습니다.", stage="replay")
        stored_by_index[index] = row
    for row in decided:
        stored = stored_by_index.get(int(row["index"]))
        if stored is None:
            raise T3Error("B02 차분 index가 없습니다.", stage="replay")
        if int(stored["before_sample"]) != int(row["before_sample"]) or int(stored["after_sample"]) != int(row["after_sample"]):
            raise T3Error("B02 차분 샘플이 재생과 다릅니다.", stage="replay")
        if row["peak_action"] != "keep":
            if stored.get("temporal_action") != "not_applicable" or stored.get("temporal") is not None:
                raise T3Error("B02 비경계 시간 판정이 잘못되었습니다.", stage="replay")
            if stored.get("temporal_reason") != "not_a_b01_boundary":
                raise T3Error("B02 비경계 이유가 잘못되었습니다.", stage="replay")
            row["temporal_action"] = "not_applicable"
            row["temporal_reason"] = "not_a_b01_boundary"
            row["temporal"] = None
            continue
        temporal = stored.get("temporal")
        if not isinstance(temporal, dict):
            raise T3Error("B02 시간 판정 입력이 없습니다.", stage="replay")
        if int(temporal["reference_sample"]) != int(row["before_sample"]):
            raise T3Error("B02 기준 샘플이 경계 앞 샘플이 아닙니다.", stage="replay")
        reference_time = as_decimal(samples[int(row["before_sample"])]["video_seconds"], "reference")
        if as_decimal(temporal["reference_video_seconds"], "reference") != reference_time:
            raise T3Error("B02 기준 시각이 샘플과 다릅니다.", stage="replay")
        stored_offsets = [as_decimal(item, "offset") for item in temporal.get("required_offsets") or []]
        if stored_offsets != list(TEMPORAL_OFFSETS):
            raise T3Error("B02 비교 시점이 설정과 다릅니다.", stage="replay")
        if as_decimal(temporal["match_tolerance_seconds"], "tolerance") != TEMPORAL_MATCH_TOLERANCE:
            raise T3Error("B02 시각 허용이 저장값과 다릅니다.", stage="replay")
        if as_decimal(temporal["revert_absolute"], "revert") != ABSOLUTE_THRESHOLD:
            raise T3Error("B02 복귀 하한이 저장값과 다릅니다.", stage="replay")
        if temporal.get("rule") != TEMPORAL_RULE:
            raise T3Error("B02 시간 판정 규칙이 저장값과 다릅니다.", stage="replay")
        comparisons = temporal.get("comparisons")
        if not isinstance(comparisons, list):
            raise T3Error("B02 비교 샘플이 없습니다.", stage="replay")
        passing = [int(item) for item in row["passing_tile_indices"]]
        action, reason = _judge_temporal(passing, comparisons, stage="replay")
        if stored.get("temporal_action") != action or stored.get("temporal_reason") != reason:
            raise T3Error(
                "B02 저장 판정과 재생 판정이 다릅니다.",
                stage="replay",
                details={"stored": stored.get("temporal_action"), "replay": action},
            )
        row["temporal_action"] = action
        row["temporal_reason"] = reason
        row["temporal"] = temporal
    boundaries = _boundaries_temporal(decided, samples, start, end)
    return boundaries, _segments(start, end, boundaries, samples)


def _replay_spatial(
    differences: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    start: Decimal,
    end: Decimal,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _assert_spatial_settings(settings)
    decided = _decided_tile_rows(differences, samples, settings)
    if len(decided) != len(differences):
        raise T3Error("B03 차분 수가 재생과 다릅니다.", stage="replay")
    stored_by_index: dict[int, dict[str, Any]] = {}
    for row in differences:
        if "spatial_action" not in row or "spatial_reason" not in row or "spatial" not in row:
            raise T3Error("B03 공간 판정 필드가 없습니다.", stage="replay")
        index = int(row["index"])
        if index in stored_by_index:
            raise T3Error("B03 차분 index가 중복되었습니다.", stage="replay")
        stored_by_index[index] = row
    for row in decided:
        stored = stored_by_index.get(int(row["index"]))
        if stored is None:
            raise T3Error("B03 차분 index가 없습니다.", stage="replay")
        if int(stored["before_sample"]) != int(row["before_sample"]) or int(stored["after_sample"]) != int(row["after_sample"]):
            raise T3Error("B03 차분 샘플이 재생과 다릅니다.", stage="replay")
        if row["peak_action"] != "keep":
            if stored.get("spatial_action") != "not_applicable" or stored.get("spatial") is not None:
                raise T3Error("B03 비경계 공간 판정이 잘못되었습니다.", stage="replay")
            if stored.get("spatial_reason") != "not_a_b01_boundary":
                raise T3Error("B03 비경계 이유가 잘못되었습니다.", stage="replay")
            row["spatial_action"] = "not_applicable"
            row["spatial_reason"] = "not_a_b01_boundary"
            row["spatial"] = None
            continue
        spatial = stored.get("spatial")
        if not isinstance(spatial, dict):
            raise T3Error("B03 공간 판정 입력이 없습니다.", stage="replay")
        if int(spatial["before_sample"]) != int(row["before_sample"]) or int(spatial["after_sample"]) != int(row["after_sample"]):
            raise T3Error("B03 비교 샘플이 경계 전후가 아닙니다.", stage="replay")
        before_time = as_decimal(samples[int(row["before_sample"])]["video_seconds"], "before")
        after_time = as_decimal(samples[int(row["after_sample"])]["video_seconds"], "after")
        if as_decimal(spatial["before_video_seconds"], "before") != before_time:
            raise T3Error("B03 앞 시각이 샘플과 다릅니다.", stage="replay")
        if as_decimal(spatial["after_video_seconds"], "after") != after_time:
            raise T3Error("B03 뒤 시각이 샘플과 다릅니다.", stage="replay")
        action, reason = _judge_spatial(spatial)
        if stored.get("spatial_action") != action or stored.get("spatial_reason") != reason:
            raise T3Error(
                "B03 저장 판정과 재생 판정이 다릅니다.",
                stage="replay",
                details={"stored": stored.get("spatial_action"), "replay": action},
            )
        row["spatial_action"] = action
        row["spatial_reason"] = reason
        row["spatial"] = spatial
    boundaries = _boundaries_spatial(decided, samples, start, end)
    return boundaries, _segments(start, end, boundaries, samples)


def _differences(samples: list[dict[str, Any]], raws: list[bytes]) -> list[dict[str, Any]]:
    rows = []
    for index in range(1, len(samples)):
        before = samples[index - 1]
        after = samples[index]
        rows.append({
            "index": index,
            "before_sample": before["sample_index"],
            "after_sample": after["sample_index"],
            "elapsed_video_seconds": after["video_seconds"] - before["video_seconds"],
            "value": mean_abs_difference(raws[index - 1], raws[index]),
            "t": after["video_seconds"],
        })
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
        if mode == "absolute":
            threshold = ABSOLUTE_THRESHOLD
            middle = None
            mad = None
        else:
            values = [item["value"] for item in chosen]
            middle_exact = median(values)
            mad_exact = median([abs(item - middle_exact) for item in values])
            threshold = quantize_diff(max(ABSOLUTE_THRESHOLD, middle_exact + MAD_MULTIPLIER * MAD_SCALE * mad_exact))
            middle = quantize_diff(middle_exact)
            mad = quantize_diff(mad_exact)
        row["statistics_mode"] = mode
        row["neighbor_indices"] = [item["index"] for item in chosen]
        row["median"] = middle
        row["mad"] = mad
        row["threshold"] = threshold
        row["passes_threshold"] = row["value"] > threshold
    passers = [row for row in rows if row["passes_threshold"]]
    for row in rows:
        if not row["passes_threshold"]:
            row["peak_action"] = "below_threshold"
            row["suppression_refs"] = []
            continue
        refs = []
        for other in passers:
            if other["index"] == row["index"] or abs(other["t"] - row["t"]) > PEAK_RADIUS:
                continue
            later_tie = other["value"] == row["value"] and (
                other["t"] < row["t"] or (other["t"] == row["t"] and other["index"] < row["index"])
            )
            if other["value"] > row["value"]:
                refs.append({
                    "difference_index": other["index"],
                    "sample_index": other["after_sample"],
                    "reason": "앞뒤 0.5초 안에 더 큰 차분이 있습니다.",
                })
            elif later_tie:
                refs.append({
                    "difference_index": other["index"],
                    "sample_index": other["after_sample"],
                    "reason": "같은 차분이고 더 이른 시각이 있습니다.",
                })
        row["suppression_refs"] = refs
        row["peak_action"] = "suppress" if refs else "keep"
    for row in rows:
        del row["t"]
    return rows


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


def pair_records(records: list[dict[str, Any]], raws: list[bytes]) -> None:
    if len(records) != len(raws):
        raise T3Error(
            f"raw bytes와 PTS 레코드 수가 다릅니다. records={len(records)} raws={len(raws)}",
            stage="decode",
        )
    for record, raw in zip(records, raws):
        expected = int(record["width"]) * int(record["height"])
        if len(raw) != expected:
            raise T3Error("raw bytes와 이미지 크기가 일치하지 않습니다.", stage="decode")


FRAME_RE = __import__("re").compile(
    r"\bn:\s*(-?\d+)\s+pts:\s*(-?\d+)\s+pts_time:\S+.*?s:(\d+)x(\d+)\b"
)
TIMEBASE_IN_RE = __import__("re").compile(r"config in time_base:\s*(\d+)/(\d+)")
TIMEBASE_OUT_RE = __import__("re").compile(r"config out time_base:\s*(\d+)/(\d+)")


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
    elapsed = Decimal(str(round(time.monotonic() - started, 6)))
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    details = {"args": args, "returncode": proc.returncode, "elapsed_seconds": elapsed, "stderr_tail": stderr_text[-4000:]}
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
        "stderr_tail": stderr_text[-4000:],
        "frame_count": counts["records"],
    }


def decode_gray(
    ffmpeg: str,
    path: Path,
    start: Decimal,
    end: Decimal | None,
    *,
    stream_time_base: Fraction,
    stream_start: Fraction,
    stream_index: int,
    timeout: int = SUBPROCESS_TIMEOUT,
    keep: str = "range",
) -> dict[str, Any]:
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info",
        "-threads", FFMPEG_THREADS, "-copyts", "-ss", format(start, "f"),
    ]
    if end is not None:
        args.extend(["-t", format((end - start) + DECODE_MARGIN, "f")])
    args.extend([
        "-i", str(path), "-map", "0:v:0", "-an",
        "-vf", f"scale={ANALYSIS_WIDTH}:-2:flags=area,format=gray,showinfo",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ])
    numerator = stream_time_base.numerator
    denominator = stream_time_base.denominator
    start_f = to_fraction(start)
    stop = None if end is None else to_fraction(end)
    requests = [] if end is None or keep != "grid" else grid_requests(start, end)
    request_at = 0
    kept: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    assigned: dict[int, list[int]] = {}

    def on_frame(record: dict[str, Any], raw: bytes) -> None:
        nonlocal request_at, last
        expected = int(record["width"]) * int(record["height"])
        if len(raw) != expected:
            raise T3Error("raw bytes와 이미지 크기가 일치하지 않습니다.", stage="decode")
        seconds = video_seconds(int(record["pts"]), numerator, denominator, stream_start)
        if seconds < start_f or (stop is not None and seconds >= stop):
            return
        frame = {
            "pts": int(record["pts"]),
            "time_base_numerator": numerator,
            "time_base_denominator": denominator,
            "stream_index": stream_index,
            "width": int(record["width"]),
            "height": int(record["height"]),
            "pixel_format": "gray",
            "raw": raw,
            "video_seconds": seconds,
        }
        if keep != "grid":
            kept.append(frame)
            return
        last = frame
        hits: list[int] = []
        while request_at < len(requests) and to_fraction(requests[request_at]) <= seconds:
            hits.append(request_at)
            request_at += 1
        if hits:
            assigned[len(kept)] = hits
            kept.append(frame)

    decoded = run_ffmpeg(args, timeout, on_frame)
    incoming = decoded["time_base_in"]
    if incoming is None:
        raise T3Error("필터 time_base를 확인하지 못했습니다.", stage="time", details={"args": args})
    if incoming != stream_time_base:
        raise T3Error(
            "필터 입력 time_base가 원본 스트림과 다릅니다.",
            stage="time",
            details={"filter_in": str(incoming), "stream": str(stream_time_base)},
        )
    outgoing = decoded["time_base_out"]
    if outgoing is not None and outgoing != incoming:
        raise T3Error(
            "필터 출력 time_base가 원본과 달라 대응을 확정할 수 없습니다.",
            stage="time",
            details={"filter_out": str(outgoing), "filter_in": str(incoming)},
        )
    if keep == "grid":
        if last is not None and (not kept or kept[-1]["pts"] != last["pts"]):
            kept.append(last)
        decoded["grid_requests"] = requests
        decoded["assigned_requests"] = assigned
    decoded["frames"] = kept
    return decoded

def decode_tail_frame(
    ffmpeg: str,
    path: Path,
    start: Decimal,
    end: Decimal,
    *,
    stream_time_base: Fraction,
    stream_start: Fraction,
    stream_index: int,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    attempts = []
    for window in TAIL_WINDOWS:
        window_start = end - window
        if window_start < start:
            window_start = start
        decoded = decode_gray(
            ffmpeg, path, window_start, end,
            stream_time_base=stream_time_base,
            stream_start=stream_start,
            stream_index=stream_index,
            timeout=timeout,
        )
        attempts.append({
            "window_seconds": format(window, "f"),
            "start": window_start,
            "frame_count": len(decoded["frames"]),
            "elapsed_seconds": decoded["elapsed_seconds"],
            "args": decoded["args"],
            "returncode": decoded["returncode"],
        })
        if decoded["frames"]:
            chosen = max(decoded["frames"], key=lambda item: item["pts"])
            return chosen, attempts
        if window_start == start:
            break
    return None, attempts


def confirm_stream_end(
    ffmpeg: str,
    path: Path,
    duration: Decimal,
    *,
    stream_time_base: Fraction,
    stream_start: Fraction,
    stream_index: int,
) -> dict[str, Any]:
    window_start = duration - Decimal(3) if duration > Decimal(3) else Decimal(0)
    decoded = decode_gray(
        ffmpeg, path, window_start, None,
        stream_time_base=stream_time_base,
        stream_start=stream_start,
        stream_index=stream_index,
    )
    if not decoded["frames"]:
        raise T3Error("영상 끝부분에서 프레임을 확인하지 못했습니다.", stage="probe", details={"path": str(path)})
    last = max(decoded["frames"], key=lambda item: item["pts"])
    return {
        "window_start": window_start,
        "frame_count": len(decoded["frames"]),
        "last_pts": last["pts"],
        "time_base": _time_base_object(stream_time_base.numerator, stream_time_base.denominator),
        "last_frame_video_seconds": quantize_seconds(last["video_seconds"]),
        "elapsed_seconds": decoded["elapsed_seconds"],
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


def detect_range(
    ffmpeg: str,
    path: Path,
    start: Decimal,
    end: Decimal,
    *,
    stream_time_base: Fraction,
    stream_start: Fraction,
    stream_index: int,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    decoded = decode_gray(
        ffmpeg, path, start, end,
        stream_time_base=stream_time_base,
        stream_start=stream_start,
        stream_index=stream_index,
        keep="grid",
    )
    decode_elapsed = time.monotonic() - started
    tail_started = time.monotonic()
    tail, attempts = decode_tail_frame(
        ffmpeg, path, start, end,
        stream_time_base=stream_time_base,
        stream_start=stream_start,
        stream_index=stream_index,
    )
    tail_elapsed = time.monotonic() - tail_started
    frames = decoded["frames"]
    if tail is None:
        if frames:
            raise T3Error("마지막 프레임을 확인하지 못했습니다.", stage="tail", details={"attempts": attempts})
    else:
        if not frames or int(frames[-1]["pts"]) != int(tail["pts"]):
            raise T3Error(
                "격자 디코드의 마지막 PTS와 끝부분 디코드의 PTS가 다릅니다.",
                stage="tail",
                details={
                    "grid_last_pts": None if not frames else frames[-1]["pts"],
                    "tail_pts": tail["pts"],
                    "attempts": attempts,
                },
            )
        if sha256_bytes(frames[-1]["raw"]) != sha256_bytes(tail["raw"]):
            raise T3Error("마지막 프레임 이미지가 두 디코드에서 다릅니다.", stage="tail")
    diff_started = time.monotonic()
    analysis = analyze_frames(frames, start=start, end=end, stream_start=stream_start, settings=settings)
    diff_elapsed = time.monotonic() - diff_started
    analysis["decode"] = {
        "grid_elapsed_seconds": Decimal(str(round(decode_elapsed, 6))),
        "tail_elapsed_seconds": Decimal(str(round(tail_elapsed, 6))),
        "difference_elapsed_seconds": Decimal(str(round(diff_elapsed, 6))),
        "grid_args": decoded["args"],
        "grid_returncode": decoded["returncode"],
        "grid_frame_count": decoded["frame_count"],
        "filter_time_base_in": str(decoded["time_base_in"]),
        "filter_time_base_out": None if decoded["time_base_out"] is None else str(decoded["time_base_out"]),
        "tail_attempts": attempts,
        "stderr_tail": decoded["stderr_tail"],
    }
    return analysis


def detection_key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def resume_action(status: str | None, stored_key: str | None, current_key: str) -> str:
    if status == "ok" and stored_key == current_key:
        return "reuse"
    if status == "failed":
        return "skip_failed"
    if status == "ok" and stored_key != current_key:
        return "reject_key"
    return "process"
