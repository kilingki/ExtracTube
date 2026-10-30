"""Selected exact-PTS RGB adapter, ported from T4 extract primitives."""

from __future__ import annotations
import os
import signal
import struct
import subprocess
import time
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

FRAME_RE=re.compile(r'\bn:\s*(-?\d+)\s+pts:\s*(-?\d+)\s+pts_time:\S+.*?s:(\d+)x(\d+)\b')
TIMEBASE_IN_RE=re.compile(r'config in time_base:\s*(\d+)/(\d+)')
TIMEBASE_OUT_RE=re.compile(r'config out time_base:\s*(\d+)/(\d+)')
PNG_SIGNATURE=b'\x89PNG\r\n\x1a\n'
SUBPROCESS_TIMEOUT=300
FFMPEG_THREADS='2'


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
