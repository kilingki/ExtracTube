from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


@dataclass
class Settings:
    infer_base_url: str = "http://127.0.0.1:8095"
    asr_model_id: str = "qwen-asr"
    fa_model_id: str = "qwen-fa"
    language: str = "ko"
    data_root: Path = Path("data")
    timeout_seconds: float = 7200
    # HTTPS only. The bare best-video selector prefers an HLS premium format that
    # fails on this network, while the 1080p DASH formats are reachable.
    yt_dlp_format: str = "bv*[height<=1080][protocol=https]+ba[protocol=https]/b"
    merge_format: str = "mp4"
    max_body_bytes: int = 1073741824

    def copy(self, **changes: object) -> Settings:
        return replace(self, **changes)
