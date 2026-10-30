from __future__ import annotations
from dataclasses import dataclass, replace, asdict
from pathlib import Path
import math
from urllib.parse import urlsplit
from extractube.errors import VisionError

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



@dataclass(frozen=True)
class VisionSettings:
    infer_base_url: str = 'http://127.0.0.1:8095'
    qwen_model_id: str = 'qwen-vlm'
    qwen_body_model: str = 'qwen3.8-27b'
    cache_epoch: str = 'stage2-v1'
    timeout_seconds: float = 300
    max_request_bytes: int = 67108864
    max_response_bytes: int = 16777216
    max_inference_calls: int = 10000
    max_http_seconds: float = 7200
    ffmpeg: str = 'ffmpeg'
    ffprobe: str = 'ffprobe'
    def __post_init__(self):
        for name in ('max_request_bytes','max_response_bytes','max_inference_calls'):
            if type(getattr(self,name)) is not int or getattr(self,name) <= 0:
                raise VisionError(f'{name}은 양의 정수여야 합니다.', kind='usage')
        for name in ('timeout_seconds','max_http_seconds'):
            value = getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise VisionError(f'{name}은 유한한 양수여야 합니다.', kind='usage')
        for name in ('qwen_model_id','qwen_body_model','cache_epoch','ffmpeg','ffprobe'):
            if not isinstance(getattr(self,name),str) or not getattr(self,name).strip():
                raise VisionError(f'{name}이 비어 있습니다.', kind='usage')
        if not isinstance(self.infer_base_url,str):
            raise VisionError('추론 URL은 문자열이어야 합니다.',kind='usage')
        url = urlsplit(self.infer_base_url)
        if url.scheme not in ('http','https') or not url.hostname or url.query or url.fragment:
            raise VisionError('추론 URL 형식 오류입니다.', kind='usage')
    def model_profile(self):
        return {k:asdict(self)[k] for k in ('infer_base_url','qwen_model_id','qwen_body_model','cache_epoch')}
