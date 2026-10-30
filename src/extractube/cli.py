from __future__ import annotations
import argparse
import sys
from pathlib import Path
from extractube.errors import PipelineError
from extractube.pipeline import run
from extractube.settings import Settings
from extractube.store import STAGES

def _run_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extractube", description="ExtracTube 영상 처리 파이프라인")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="단일 YouTube URL을 처리합니다.")
    sub.add_parser("stage2", help="완료한 Stage 1에서 시각 자료를 처리합니다.")
    sub.add_parser("stage2-verify", help="Stage 2 산출물을 로컬 검증합니다.")
    run_parser.add_argument("url")
    run_parser.add_argument("--through", choices=("stage1", "stage2"), default="stage1")
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
            through=args.through,
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
        return 3 if exc.kind in ("blocked_unknown", "paused_budget") else (2 if exc.kind == "usage" else 1)

    for stage, outcome in result.stages.items():
        state = "재사용" if outcome.reused else "생성"
        print(f"{stage}={state} id={outcome.record['result_id']} path={outcome.record['path']}")
    print(f"empty_sentences={str(result.empty_sentences).lower()}")
    print(f"asr_requests={result.asr_requests}")
    print(f"fa_requests={result.fa_requests}")
    print(f"work_dir={result.work_dir}")
    if result.audio_to_video_offset_seconds is not None:
        print(f"audio_to_video_offset_seconds={result.audio_to_video_offset_seconds}")
    if result.vision is not None:
        return _show_stage2(result.vision)
    return 0


_STAGE2_OPTIONS = {
    'infer_url': 'infer_base_url', 'qwen_model': 'qwen_model_id',
    'qwen_body_model': 'qwen_body_model', 'cache_epoch': 'cache_epoch',
    'timeout': 'timeout_seconds', 'max_request_bytes': 'max_request_bytes',
    'max_response_bytes': 'max_response_bytes', 'max_inference_calls': 'max_inference_calls',
    'max_http_seconds': 'max_http_seconds', 'ffmpeg': 'ffmpeg', 'ffprobe': 'ffprobe',
}


def _show_stage2(result):
    print(f'run_id={result.run_id} execution_status={result.execution_status}')
    print(f'inference_calls={result.inference_calls} reused_jobs={result.reused_jobs}')
    if result.handoff_path is not None:
        print(f'handoff={result.handoff_path}')
    if result.warnings:
        print(f'Stage 2 경고 {len(result.warnings)}건: handoff/review.html의 누락 범위를 확인하십시오.', file=sys.stderr)
    if result.execution_status in ('paused_budget', 'blocked_unknown'):
        return 3
    return 0 if result.stage3_usable else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ('stage2', 'stage2-verify'):
        return _run_main(argv)
    from dataclasses import replace
    from extractube.settings import VisionSettings
    from extractube.vision import run as stage2_run
    from extractube.handoff import verify
    from extractube.vision import load_settings
    parser = argparse.ArgumentParser(prog='extractube ' + argv[0])
    parser.add_argument('video_id')
    parser.add_argument('--data-root', type=Path, default=Path('data'))
    if argv[0] == 'stage2-verify':
        parser.add_argument('--run', dest='run_id')
    else:
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--force', action='store_true')
        group.add_argument('--from', dest='from_stage', choices=('t1','t2','t3','t4','t5'))
        group.add_argument('--resume', dest='resume_run_id')
        parser.add_argument('--retry-unknown', action='append', default=[])
        for option, field in _STAGE2_OPTIONS.items():
            kind = int if field in ('max_request_bytes','max_response_bytes','max_inference_calls') else (float if field in ('timeout_seconds','max_http_seconds') else str)
            parser.add_argument('--'+option.replace('_','-'), type=kind, default=None)
    args = parser.parse_args(argv[1:])
    try:
        if argv[0] == 'stage2-verify':
            result = verify(args.video_id, data_root=args.data_root, run_id=args.run_id)
            print('ok=' + str(result['ok']).lower() + ' quality_review_performed=false')
            return 0
        values = {field:getattr(args,option) for option,field in _STAGE2_OPTIONS.items() if getattr(args,option) is not None}
        limits = None
        if args.resume_run_id:
            settings = load_settings(args.video_id,args.data_root,args.resume_run_id)
            limits = {k:values.pop(k) for k in ('max_inference_calls','max_http_seconds') if k in values}
            settings = replace(settings,**values)
        else:
            settings = VisionSettings(**values)
        return _show_stage2(stage2_run(args.video_id,settings,data_root=args.data_root,force=args.force,from_stage=args.from_stage,resume_run_id=args.resume_run_id,retry_unknown_call_ids=tuple(args.retry_unknown),resume_budget_limits=limits))
    except KeyboardInterrupt:
        print('취소되었습니다. 전송 중인 호출은 재개 시 unknown으로 확인합니다.',file=sys.stderr)
        return 130
    except PipelineError as exc:
        print(str(exc),file=sys.stderr)
        if exc.diagnostic is not None:print(f'diagnostic={exc.diagnostic}',file=sys.stderr)
        return 3 if exc.kind in ('blocked_unknown','paused_budget') else (2 if exc.kind=='usage' else 1)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(f'Stage 2 입력/저장 오류: {exc}',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
