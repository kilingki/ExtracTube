from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp

from extractube.errors import PipelineError
from extractube.settings import Settings

PCM_SAMPLE_RATE = 16000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2


@dataclass
class DownloadedVideo:
    path: Path
    video_id: str
    title: str | None
    webpage_url: str | None
    duration: float | None
    format_id: str | None
    ext: str
    vcodec: str | None
    acodec: str | None
    height: int | None


@dataclass
class Probe:
    video_start_time_seconds: float
    source_audio_start_time_seconds: float
    audio_to_video_offset_seconds: float
    height: int | None
    vcodec: str | None
    acodec: str | None


@dataclass
class WavInfo:
    sample_rate: int
    channels: int
    sample_width: int
    num_samples: int


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise PipelineError(f"필요한 실행 파일이 없습니다: {name}", kind="media")


def validate_video_id(video_id: object) -> str:
    if not isinstance(video_id, str) or video_id == "" or video_id.strip() != video_id:
        raise PipelineError("video ID가 없습니다.", kind="media")
    if "/" in video_id or "\\" in video_id or ".." in video_id:
        raise PipelineError(f"video ID를 작업 경로로 쓸 수 없습니다: {video_id}", kind="media")
    return video_id


def _ydl_options(settings: Settings, outtmpl: str | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "format": settings.yt_dlp_format,
        "merge_output_format": settings.merge_format,
        # No IPv6 default route here, so AAAA lookups fail with ENETUNREACH.
        # The Python API also treats an omitted retry count as zero.
        "source_address": "0.0.0.0",
        "retries": 10,
        "fragment_retries": 10,
    }
    if outtmpl is not None:
        options["outtmpl"] = outtmpl
    return options


def resolve_video_id(url: str, settings: Settings) -> str:
    options = _ydl_options(settings)
    options["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise PipelineError(f"video ID를 확인하지 못했습니다: {exc}", kind="media") from exc
    if not isinstance(info, dict) or info.get("_type") == "playlist":
        raise PipelineError("Playlist URL은 처리하지 않습니다.", kind="media")
    return validate_video_id(info.get("id"))


def _optional_str(info: dict[str, Any], key: str) -> str | None:
    value = info.get(key)
    if isinstance(value, str) and value != "":
        return value
    return None


def _optional_float(info: dict[str, Any], key: str) -> float | None:
    value = info.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _optional_int(info: dict[str, Any], key: str) -> int | None:
    value = info.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def download_video(url: str, settings: Settings, dest_dir: Path) -> DownloadedVideo:
    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "download.%(ext)s")
    try:
        with yt_dlp.YoutubeDL(_ydl_options(settings, outtmpl)) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        raise PipelineError(f"영상 다운로드에 실패했습니다: {exc}", kind="media") from exc
    if not isinstance(info, dict) or info.get("_type") == "playlist":
        raise PipelineError("Playlist URL은 처리하지 않습니다.", kind="media")
    video_id = validate_video_id(info.get("id"))
    path = _downloaded_path(dest_dir, info)
    ext = path.suffix[1:] if path.suffix else str(info.get("ext") or "bin")
    return DownloadedVideo(
        path=path,
        video_id=video_id,
        title=_optional_str(info, "title"),
        webpage_url=_optional_str(info, "webpage_url"),
        duration=_optional_float(info, "duration"),
        format_id=_optional_str(info, "format_id"),
        ext=ext,
        vcodec=_optional_str(info, "vcodec"),
        acodec=_optional_str(info, "acodec"),
        height=_optional_int(info, "height"),
    )


def _downloaded_path(dest_dir: Path, info: dict[str, Any]) -> Path:
    downloads = info.get("requested_downloads")
    if isinstance(downloads, list):
        for item in downloads:
            if isinstance(item, dict) and item.get("filepath"):
                path = Path(item["filepath"])
                if path.is_file():
                    return path
    candidates = [path for path in dest_dir.iterdir() if path.is_file() and path.name.startswith("download.")]
    if len(candidates) == 1:
        return candidates[0]
    raise PipelineError("다운로드한 영상 파일을 찾지 못했습니다.", kind="media")


def probe(path: Path) -> Probe:
    _require_tool("ffprobe")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise PipelineError(f"ffprobe에 실패했습니다: {detail}", kind="media")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineError("ffprobe 결과가 JSON이 아닙니다.", kind="media") from exc
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise PipelineError("ffprobe 스트림이 없습니다.", kind="media")
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if not isinstance(video, dict) or not isinstance(audio, dict):
        raise PipelineError("영상에 영상 스트림과 오디오 스트림이 모두 있어야 합니다.", kind="media")
    video_start = _start_time(video, "영상")
    audio_start = _start_time(audio, "오디오")
    height = video.get("height")
    return Probe(
        video_start_time_seconds=video_start,
        source_audio_start_time_seconds=audio_start,
        audio_to_video_offset_seconds=audio_start - video_start,
        height=height if isinstance(height, int) and not isinstance(height, bool) else None,
        vcodec=video.get("codec_name") if isinstance(video.get("codec_name"), str) else None,
        acodec=audio.get("codec_name") if isinstance(audio.get("codec_name"), str) else None,
    )


def _start_time(stream: dict[str, Any], label: str) -> float:
    if "start_time" not in stream:
        raise PipelineError(f"{label} start_time이 없습니다.", kind="media")
    raw = stream["start_time"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise PipelineError(f"{label} start_time이 유효하지 않습니다.", kind="media")
    try:
        value = float(raw)
    except ValueError as exc:
        raise PipelineError(f"{label} start_time이 유효하지 않습니다.", kind="media") from exc
    if not math.isfinite(value):
        raise PipelineError(f"{label} start_time이 유효하지 않습니다.", kind="media")
    return value


def extract_wav(video: Path, dest: Path) -> None:
    _require_tool("ffmpeg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            "-vn",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise PipelineError(f"WAV 추출에 실패했습니다: {detail}", kind="media")


def inspect_wav(path: Path) -> WavInfo:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            num_samples = handle.getnframes()
            comptype = handle.getcomptype()
    except (wave.Error, EOFError) as exc:
        raise PipelineError(f"PCM WAV가 아닙니다: {path}", kind="media") from exc
    if (
        channels != PCM_CHANNELS
        or sample_width != PCM_SAMPLE_WIDTH
        or sample_rate != PCM_SAMPLE_RATE
        or comptype != "NONE"
        or num_samples <= 0
    ):
        raise PipelineError(
            "WAV는 16 kHz mono PCM S16LE이어야 합니다. "
            f"channels={channels} sample_width={sample_width} "
            f"sample_rate={sample_rate} comptype={comptype} num_samples={num_samples}",
            kind="media",
        )
    return WavInfo(
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        num_samples=num_samples,
    )
