from __future__ import annotations

import argparse
import sys
from pathlib import Path

from extractube.errors import PipelineError
from extractube.pipeline import run
from extractube.settings import Settings
from extractube.store import STAGES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extractube", description="ExtracTube 1단계 오디오 파이프라인")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="단일 YouTube URL을 처리합니다.")
    run_parser.add_argument("url")
    run_parser.add_argument("--force", action="store_true", help="video부터 다시 처리합니다.")
    run_parser.add_argument(
        "--from",
        dest="from_stage",
        choices=STAGES,
        help="지정한 단계와 그 뒤를 다시 처리합니다.",
    )
    run_parser.add_argument("--infer-url", default="http://127.0.0.1:8095")
    run_parser.add_argument("--asr-model", default="qwen-asr")
    run_parser.add_argument("--fa-model", default="qwen-fa")
    run_parser.add_argument("--language", choices=("ko", "en"), default="ko")
    run_parser.add_argument("--data-root", type=Path, default=Path("data"))
    run_parser.add_argument("--timeout", type=float, default=7200)
    run_parser.add_argument("--format", dest="yt_dlp_format", default=Settings.yt_dlp_format)
    run_parser.add_argument("--merge-format", default="mp4")
    run_parser.add_argument("--max-body-bytes", type=int, default=1073741824)
    args = parser.parse_args(argv)

    if args.force and args.from_stage:
        print("--force와 --from은 함께 지정할 수 없습니다.", file=sys.stderr)
        return 2

    settings = Settings(
        infer_base_url=args.infer_url,
        asr_model_id=args.asr_model,
        fa_model_id=args.fa_model,
        language=args.language,
        data_root=args.data_root,
        timeout_seconds=args.timeout,
        yt_dlp_format=args.yt_dlp_format,
        merge_format=args.merge_format,
        max_body_bytes=args.max_body_bytes,
    )
    try:
        result = run(
            args.url,
            settings,
            force=args.force,
            from_stage=args.from_stage,
        )
    except KeyboardInterrupt:
        print(
            "취소되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다.",
            file=sys.stderr,
        )
        return 130
    except PipelineError as exc:
        print(exc, file=sys.stderr)
        if exc.diagnostic is not None:
            print(f"diagnostic={exc.diagnostic}", file=sys.stderr)
        return 2 if exc.kind == "usage" else 1

    for stage, outcome in result.stages.items():
        state = "재사용" if outcome.reused else "생성"
        print(f"{stage}={state} id={outcome.record['result_id']} path={outcome.record['path']}")
    print(f"empty_sentences={str(result.empty_sentences).lower()}")
    print(f"asr_requests={result.asr_requests}")
    print(f"fa_requests={result.fa_requests}")
    print(f"work_dir={result.work_dir}")
    if result.audio_to_video_offset_seconds is not None:
        print(f"audio_to_video_offset_seconds={result.audio_to_video_offset_seconds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
