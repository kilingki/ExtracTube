"""Exact integer-PTS color frames from the original video."""

from __future__ import annotations

import hashlib
import os
import signal
import struct
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

FRAME_RE = __import__("re").compile(
    r"\bn:\s*(-?\d+)\s+pts:\s*(-?\d+)\s+pts_time:\S+.*?s:(\d+)x(\d+)\b"
)
TIMEBASE_IN_RE = __import__("re").compile(r"config in time_base:\s*(\d+)/(\d+)")
TIMEBASE_OUT_RE = __import__("re").compile(r"config out time_base:\s*(\d+)/(\d+)")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CLUSTER_GAP = Decimal("2")
CLUSTER_LIMIT = 40
SUBPROCESS_TIMEOUT = 300
FFMPEG_THREADS = "2"


class ExtractError(Exception):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def parse_showinfo_line(line: str) -> dict[str, int] | None:
    matched = FRAME_RE.search(line)
    if not matched:
        return None
    return {
        "n": int(matched.group(1)),
        "pts": int(matched.group(2)),
        "width": int(matched.group(3)),
        "height": int(matched.group(4)),
    }


def parse_time_base(line: str) -> tuple[str, tuple[int, int] | None] | None:
    incoming = TIMEBASE_IN_RE.search(line)
    if incoming:
        return "in", (int(incoming.group(1)), int(incoming.group(2)))
    outgoing = TIMEBASE_OUT_RE.search(line)
    if outgoing:
        numerator = int(outgoing.group(1))
        denominator = int(outgoing.group(2))
        if numerator <= 0 or denominator <= 0:
            return "out", None
        return "out", (numerator, denominator)
    return None


def png_info(data: bytes) -> dict[str, int]:
    if len(data) < 26 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise ExtractError("PNG IHDR을 읽지 못했습니다.")
    width, height = struct.unpack(">II", data[16:24])
    depth = data[24]
    color = data[25]
    if width < 1 or height < 1 or depth != 8 or color != 2:
        raise ExtractError(
            "PNG가 8비트 RGB가 아닙니다.",
            details={"width": width, "height": height, "depth": depth, "color": color},
        )
    return {"width": width, "height": height, "depth": depth, "color": color}


def scale_filter(width: int, height: int) -> str:
    if width >= height:
        return "scale=w='min(1280,iw)':h=-2:flags=area,format=rgb24"
    return "scale=w=-2:h='min(1280,ih)':flags=area,format=rgb24"


def _kill(proc: subprocess.Popen[bytes]) -> None:
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


def run_ffmpeg(args: list[str], timeout: int = SUBPROCESS_TIMEOUT) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill(proc)
        raise ExtractError("FFmpeg가 제한 시간을 초과했습니다.", details={"args": args}) from exc
    elapsed = Decimal(str(round(time.monotonic() - started, 6)))
    text = stderr.decode("utf-8", errors="replace")
    frames = []
    time_in: tuple[int, int] | None = None
    time_out: tuple[int, int] | None = None
    for line in text.splitlines():
        found = parse_time_base(line)
        if found and found[0] == "in" and time_in is None:
            time_in = found[1]
        elif found and found[0] == "out" and time_out is None:
            time_out = found[1]
        info = parse_showinfo_line(line)
        if info:
            frames.append(info)
    return {
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "stderr_tail": text[-2000:],
        "frames": frames,
        "time_base_in": time_in,
        "time_base_out": time_out,
        "args": args,
    }


def _decimal_arg(value: Decimal) -> str:
    return format(value, "f")


def _select_expr(pts_values: list[int]) -> str:
    return "+".join(f"eq(pts\\,{pts})" for pts in pts_values)


def _sequence_paths(directory: Path, count_hint: int) -> list[Path]:
    paths = sorted(path for path in directory.iterdir() if path.suffix == ".png" and path.is_file())
    if count_hint >= 0 and len(paths) != count_hint:
        return paths
    return paths


def _accept_time_base(result: dict[str, Any], expected: tuple[int, int]) -> str | None:
    incoming = result["time_base_in"]
    if incoming != expected:
        return f"필터 입력 time_base가 스트림과 다릅니다: {incoming}"
    outgoing = result["time_base_out"]
    if outgoing is not None and outgoing != expected:
        return f"필터 출력 time_base가 원본과 다릅니다: {outgoing}"
    return None


def extract_window(
    ffmpeg: str,
    media: Path,
    pts_values: list[int],
    *,
    seek: Decimal,
    duration: Decimal,
    destination: Path,
    expected_time_base: tuple[int, int],
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    pattern = destination / "seq_%06d.png"
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", "-threads", FFMPEG_THREADS,
        "-copyts", "-ss", _decimal_arg(seek), "-t", _decimal_arg(duration),
        "-i", str(media), "-map", "0:v:0", "-an",
        "-vf", f"select='{_select_expr(pts_values)}',format=rgb24,showinfo",
        "-fps_mode", "passthrough", "-start_number", "0", str(pattern),
    ]
    result = run_ffmpeg(args)
    files = _sequence_paths(destination, len(result["frames"]))
    result["files"] = files
    problem = _accept_time_base(result, expected_time_base)
    if result["returncode"] != 0:
        problem = problem or f"FFmpeg 종료 코드 {result['returncode']}"
    if problem:
        result["problem"] = problem
        return result
    if len(files) != len(result["frames"]):
        result["problem"] = "showinfo 수와 PNG 수가 다릅니다."
        return result
    if [item["n"] for item in result["frames"]] != list(range(len(result["frames"]))):
        result["problem"] = "showinfo 프레임 번호가 연속이 아닙니다."
        return result
    result["problem"] = None
    return result


def extract_one(
    ffmpeg: str,
    media: Path,
    pts: int,
    *,
    seek: Decimal,
    duration: Decimal,
    destination: Path,
    expected_time_base: tuple[int, int],
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "one.png"
    if output.exists():
        output.unlink()
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", "-threads", FFMPEG_THREADS,
        "-copyts", "-ss", _decimal_arg(seek), "-t", _decimal_arg(duration),
        "-i", str(media), "-map", "0:v:0", "-an",
        "-vf", f"select='eq(pts\\,{pts})',format=rgb24,showinfo",
        "-frames:v", "1", "-update", "1", str(output),
    ]
    result = run_ffmpeg(args)
    result["files"] = [output] if output.exists() else []
    problem = _accept_time_base(result, expected_time_base)
    if result["returncode"] != 0:
        problem = problem or f"FFmpeg 종료 코드 {result['returncode']}"
    frames = result["frames"]
    if not problem and (len(frames) != 1 or frames[0]["pts"] != pts or len(result["files"]) != 1):
        problem = "요청한 정수 PTS 프레임을 확인하지 못했습니다."
    result["problem"] = problem
    return result


def write_vlm(ffmpeg: str, source: Path, destination: Path, *, width: int, height: int) -> dict[str, Any]:
    temporary = destination.with_suffix(".tmp.png")
    if temporary.exists():
        temporary.unlink()
    chosen = scale_filter(width, height)
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(source), "-vf", chosen, "-frames:v", "1", "-update", "1", str(temporary),
    ]
    started = time.monotonic()
    completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=SUBPROCESS_TIMEOUT, check=False)
    elapsed = Decimal(str(round(time.monotonic() - started, 6)))
    if completed.returncode != 0 or not temporary.exists():
        if temporary.exists():
            temporary.unlink()
        raise ExtractError(
            "VLM PNG를 만들지 못했습니다.",
            details={"args": args, "stderr": completed.stderr.decode("utf-8", errors="replace")[-1500:]},
        )
    data = temporary.read_bytes()
    info = png_info(data)
    if info["width"] > 1280 or info["height"] > 1280:
        temporary.unlink()
        raise ExtractError("VLM 이미지의 긴 변이 1280을 넘습니다.", details=info)
    if info["width"] > width or info["height"] > height:
        temporary.unlink()
        raise ExtractError("VLM 이미지를 원본보다 확대했습니다.", details=info)
    os.replace(temporary, destination)
    return {"args": args, "elapsed_seconds": elapsed, "filter": chosen, "width": info["width"], "height": info["height"], "bytes": data}


def _move_png(source: Path, destination: Path) -> bytes:
    data = source.read_bytes()
    info = png_info(data)
    temporary = destination.with_suffix(".tmp.png")
    temporary.write_bytes(data)
    os.replace(temporary, destination)
    source.unlink()
    if info["width"] < 1:
        raise ExtractError("원본 PNG 크기가 없습니다.")
    return data


def clusters_for(jobs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(jobs, key=lambda item: (item["video_seconds"], item["pts"]))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for job in ordered:
        if not current:
            current = [job]
            continue
        gap = job["video_seconds"] - current[-1]["video_seconds"]
        if gap > CLUSTER_GAP or len(current) >= CLUSTER_LIMIT:
            groups.append(current)
            current = [job]
        else:
            current.append(job)
    if current:
        groups.append(current)
    return groups


def _window_for(group: list[dict[str, Any]], preroll: Decimal) -> tuple[Decimal, Decimal]:
    first = group[0]["video_seconds"]
    last = group[-1]["video_seconds"]
    seek = first - preroll
    if seek < 0:
        seek = Decimal(0)
    duration = (last - seek) + Decimal("1.5")
    if duration <= 0:
        duration = Decimal("1.5")
    return seek, duration


def _take_matches(result: dict[str, Any], wanted: set[int]) -> dict[int, Path]:
    if result.get("problem"):
        return {}
    found: dict[int, Path] = {}
    for info, path in zip(result["frames"], result["files"]):
        pts = int(info["pts"])
        if pts not in wanted or pts in found:
            continue
        if int(info["width"]) < 1 or int(info["height"]) < 1:
            continue
        found[pts] = path
    return found


def extract_jobs(
    ffmpeg: str,
    jobs: list[dict[str, Any]],
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    if not jobs:
        return {"records": [], "decode_seconds": Decimal(0), "resize_seconds": Decimal(0)}
    by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    order = []
    for job in jobs:
        key = (str(job["media_path"]), int(job["stream_index"]))
        if key not in by_key:
            order.append(key)
            by_key[key] = []
        by_key[key].append(job)
    records: dict[int, dict[str, Any]] = {}
    decode_total = Decimal(0)
    resize_total = Decimal(0)
    total = len(jobs)
    done = 0
    for media_text, stream_index in order:
        grouped = by_key[(media_text, stream_index)]
        media = Path(media_text)
        expected = (int(grouped[0]["time_base"]["numerator"]), int(grouped[0]["time_base"]["denominator"]))
        for group in clusters_for(grouped):
            wanted = [int(item["pts"]) for item in group]
            temp_root = Path(group[0]["full_path"]).parent / f".tmp_{stream_index}_{wanted[0]}_{wanted[-1]}"
            if temp_root.exists():
                _remove_tree(temp_root)
            temp_root.mkdir(parents=True)
            seek, duration = _window_for(group, Decimal(1))
            located: dict[int, dict[str, Any]] = {}
            try:
                try:
                    window = extract_window(
                        ffmpeg, media, wanted,
                        seek=seek, duration=duration, destination=temp_root, expected_time_base=expected,
                    )
                    decode_total += window["elapsed_seconds"]
                except ExtractError as exc:
                    window = {"problem": str(exc), "frames": [], "files": [], "elapsed_seconds": Decimal(0), "args": None}
                if not window.get("problem"):
                    for info, path in zip(window["frames"], window["files"]):
                        pts = int(info["pts"])
                        if pts in set(wanted) and pts not in located:
                            located[pts] = _located(path, info, window, seek, duration)
                for item in group:
                    pts = int(item["pts"])
                    if pts in located:
                        continue
                    last_problem = window.get("problem") or "묶음에서 PTS가 빠졌습니다."
                    for preroll in (Decimal(1), Decimal(2), Decimal(4), Decimal(8), item["video_seconds"]):
                        single_dir = temp_root / f"one_{pts}_{preroll}"
                        one_seek, one_duration = _window_for([item], preroll)
                        try:
                            one = extract_one(
                                ffmpeg, media, pts,
                                seek=one_seek, duration=one_duration, destination=single_dir, expected_time_base=expected,
                            )
                        except ExtractError as exc:
                            one = {"problem": str(exc), "frames": [], "files": [], "elapsed_seconds": Decimal(0), "args": None}
                        decode_total += one["elapsed_seconds"]
                        if not one.get("problem") and one["frames"] and int(one["frames"][0]["pts"]) == pts and one["files"]:
                            located[pts] = _located(one["files"][0], one["frames"][0], one, one_seek, one_duration)
                            break
                        last_problem = one.get("problem") or last_problem
                    if pts not in located:
                        records[id(item)] = _failure(item, last_problem, seek=seek, duration=duration, args=window.get("args"))
                for item in group:
                    if id(item) in records:
                        continue
                    pts = int(item["pts"])
                    try:
                        saved = _finalize_frame(ffmpeg, item, located[pts])
                        resize_total += saved["resize_seconds"]
                        records[id(item)] = saved
                    except ExtractError as exc:
                        records[id(item)] = _failure(item, str(exc), seek=seek, duration=duration, args=window.get("args"), details=exc.details)
            finally:
                if temp_root.exists():
                    _remove_tree(temp_root)
            for item in group:
                done += 1
                if on_progress and (done == total or done % 25 == 0 or not records[id(item)]["ok"]):
                    on_progress(done, total, str(records[id(item)].get("error") or item["frame_id"]))
    return {
        "records": [records[id(job)] for job in jobs],
        "decode_seconds": decode_total,
        "resize_seconds": resize_total,
    }


def _located(path: Path, info: dict[str, int], result: dict[str, Any], seek: Decimal, duration: Decimal) -> dict[str, Any]:
    return {
        "path": path,
        "info": info,
        "seek": seek,
        "duration": duration,
        "args": result.get("args"),
        "time_base_in": result.get("time_base_in"),
        "time_base_out": result.get("time_base_out"),
    }


def _remove_tree(directory: Path) -> None:
    for child in sorted(directory.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    directory.rmdir()


def _finalize_frame(ffmpeg: str, job: dict[str, Any], located: dict[str, Any]) -> dict[str, Any]:
    info = located["info"]
    if int(info["pts"]) != int(job["pts"]):
        raise ExtractError("showinfo PTS가 요청 PTS와 다릅니다.", details={"showinfo_pts": info["pts"], "pts": job["pts"]})
    if int(info["width"]) != int(job["source_width"]) or int(info["height"]) != int(job["source_height"]):
        raise ExtractError(
            "원본 프레임 해상도가 스트림과 다릅니다.",
            details={"showinfo": [info["width"], info["height"]], "source": [job["source_width"], job["source_height"]]},
        )
    full_path = Path(job["full_path"])
    vlm_path = Path(job["vlm_path"])
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_bytes = _move_png(Path(located["path"]), full_path)
    full_info = png_info(full_bytes)
    if full_info["width"] != int(job["source_width"]) or full_info["height"] != int(job["source_height"]):
        raise ExtractError("저장한 원본 PNG 해상도가 스트림과 다릅니다.", details=full_info)
    scaled = write_vlm(ffmpeg, full_path, vlm_path, width=full_info["width"], height=full_info["height"])
    return {
        "ok": True,
        "error": None,
        "frame_id": job["frame_id"],
        "pts": int(job["pts"]),
        "showinfo_pts": int(info["pts"]),
        "seek": format(located["seek"], "f"),
        "duration": format(located["duration"], "f"),
        "resize_seconds": scaled["elapsed_seconds"],
        "time_base_in": located["time_base_in"],
        "time_base_out": located["time_base_out"],
        "decode_args": located["args"],
        "scale_filter": scaled["filter"],
        "scale_args": scaled["args"],
        "full": {
            "path": job["full_rel"],
            "width": full_info["width"],
            "height": full_info["height"],
            "bytes": len(full_bytes),
            "sha256": hashlib.sha256(full_bytes).hexdigest(),
        },
        "vlm": {
            "path": job["vlm_rel"],
            "width": scaled["width"],
            "height": scaled["height"],
            "bytes": len(scaled["bytes"]),
            "sha256": hashlib.sha256(scaled["bytes"]).hexdigest(),
        },
    }


def _failure(job: dict[str, Any], message: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": message,
        "frame_id": job["frame_id"],
        "pts": int(job["pts"]),
        "details": {key: value for key, value in details.items() if key != "args"},
        "decode_args": details.get("args"),
    }
