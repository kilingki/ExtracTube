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


def adaptive_ceiling_seconds(
    duration_seconds: float,
    *,
    base_seconds: float = 180.0,
    ratio: float = 0.75,
    cap_seconds: float = 2700.0,
) -> float:
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
    ):
        return float(cap_seconds)
    return round(
        min(float(cap_seconds), float(base_seconds) + float(ratio) * float(duration_seconds)), 3
    )


def _adaptive_per_call_timeout(current_timeout: float, ceiling: float) -> float:
    return round(min(float(current_timeout), max(60.0, min(120.0, ceiling * 0.45))), 3)


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
        for name in ('max_request_bytes', 'max_response_bytes', 'max_inference_calls'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise VisionError(f'{name}은 양의 정수여야 합니다.', kind='usage')
        for name in ('timeout_seconds', 'max_http_seconds'):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise VisionError(f'{name}은 유한한 양수여야 합니다.', kind='usage')
        for name in ('qwen_model_id', 'qwen_body_model', 'cache_epoch', 'ffmpeg', 'ffprobe'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise VisionError(f'{name}이 비어 있습니다.', kind='usage')
        if not isinstance(self.infer_base_url, str):
            raise VisionError('추론 URL은 문자열이어야 합니다.', kind='usage')
        url = urlsplit(self.infer_base_url)
        if url.scheme not in ('http', 'https') or not url.hostname or url.query or url.fragment:
            raise VisionError('추론 URL 형식 오류입니다.', kind='usage')

    def model_profile(self):
        return {
            k: asdict(self)[k]
            for k in ('infer_base_url', 'qwen_model_id', 'qwen_body_model', 'cache_epoch')
        }

    def with_duration_ceiling(self, duration_seconds: float) -> VisionSettings:
        ceiling = adaptive_ceiling_seconds(duration_seconds)
        return replace(
            self,
            max_http_seconds=ceiling,
            timeout_seconds=_adaptive_per_call_timeout(self.timeout_seconds, ceiling),
        )


@dataclass(frozen=True)
class ContentSettings:
    infer_base_url: str = 'http://127.0.0.1:8095'
    qwen_model_id: str = 'qwen-vlm'
    qwen_body_model: str = 'qwen3.8-27b'
    cache_epoch: str = 'content-v1'
    tokenizer_path: str = ''
    timeout_seconds: float = 300
    max_request_bytes: int = 4194304
    max_response_bytes: int = 16777216
    max_inference_calls: int = 100
    max_http_seconds: float = 7200
    context_limit: int = 32768
    input_token_limit: int = 12000
    output_tokens: int = 4096
    safety_tokens: int = 1024
    max_auto_repairs: int = 2
    max_review_repairs: int = 2

    def __post_init__(self):
        from .errors import ContentError

        try:
            VisionSettings(
                **{
                    k: getattr(self, k)
                    for k in (
                        'infer_base_url',
                        'qwen_model_id',
                        'qwen_body_model',
                        'cache_epoch',
                        'timeout_seconds',
                        'max_request_bytes',
                        'max_response_bytes',
                        'max_inference_calls',
                        'max_http_seconds',
                    )
                }
            )
        except VisionError as exc:
            raise ContentError(str(exc), kind='usage') from exc
        for name in ('context_limit', 'input_token_limit', 'output_tokens', 'safety_tokens'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ContentError(f'{name}은 양의 정수여야 합니다.', kind='usage')
        if self.input_token_limit + self.output_tokens + self.safety_tokens > self.context_limit:
            raise ContentError('입력/출력/여유 토큰이 context_limit을 초과합니다.', kind='usage')
        if type(self.max_auto_repairs) is not int or not 0 <= self.max_auto_repairs <= 64:
            raise ContentError('max_auto_repairs는 0~64여야 합니다.', kind='usage')
        if type(self.max_review_repairs) is not int or not 0 <= self.max_review_repairs <= 2:
            raise ContentError('max_review_repairs는 0~2여야 합니다.', kind='usage')
        if not isinstance(self.tokenizer_path, str):
            raise ContentError('tokenizer_path는 문자열이어야 합니다.', kind='usage')

    def model_profile(self):
        return {
            k: asdict(self)[k]
            for k in ('infer_base_url', 'qwen_model_id', 'qwen_body_model', 'cache_epoch')
        }

    def with_duration_ceiling(self, duration_seconds: float) -> ContentSettings:
        ceiling = adaptive_ceiling_seconds(duration_seconds)
        return replace(
            self,
            max_http_seconds=ceiling,
            timeout_seconds=_adaptive_per_call_timeout(self.timeout_seconds, ceiling),
        )
