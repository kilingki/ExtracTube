from __future__ import annotations
import argparse
import sys
from pathlib import Path
from extractube.errors import PipelineError
from extractube.pipeline import run
from extractube.settings import Settings
from extractube.store import STAGES


def _run_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="extractube", description="ExtracTube 영상 처리 파이프라인"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="단일 YouTube URL을 처리합니다.")
    sub.add_parser("batch", help="여러 YouTube URL을 단계별로 일괄 처리합니다.")
    sub.add_parser("stage2", help="완료한 Stage 1에서 시각 자료를 처리합니다.")
    sub.add_parser("stage2-verify", help="Stage 2 산출물을 로컬 검증합니다.")
    run_parser.add_argument("url")
    run_parser.add_argument("--through", choices=("stage1", "stage2", "stage3"), default="stage1")
    run_parser.add_argument('--tokenizer-path', default='')
    run_parser.add_argument('--content-max-calls', type=int, default=100)
    run_parser.add_argument('--content-max-http-seconds', type=float, default=7200)
    run_parser.add_argument(
        '--adaptive-ceiling',
        action='store_true',
        help='영상 길이에 비례한 추론 시간 상한을 적용합니다.',
    )
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
        extra_kwargs = {"adaptive_ceiling": True} if args.adaptive_ceiling else {}
        result = run(
            args.url,
            settings,
            force=args.force,
            from_stage=args.from_stage,
            through=args.through,
            content_settings=_content_settings(args) if args.through == 'stage3' else None,
            **extra_kwargs,
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
        return (
            3
            if exc.kind in ("blocked_unknown", "paused_budget")
            else (2 if exc.kind == "usage" else 1)
        )

    for stage, outcome in result.stages.items():
        state = "재사용" if outcome.reused else "생성"
        print(f"{stage}={state} id={outcome.record['result_id']} path={outcome.record['path']}")
    print(f"empty_sentences={str(result.empty_sentences).lower()}")
    print(f"asr_requests={result.asr_requests}")
    print(f"fa_requests={result.fa_requests}")
    print(f"work_dir={result.work_dir}")
    if result.audio_to_video_offset_seconds is not None:
        print(f"audio_to_video_offset_seconds={result.audio_to_video_offset_seconds}")
    if getattr(result, 'content', None) is not None:
        _show_stage2(result.vision)
        return _show_content(result.content)
    if result.vision is not None:
        return _show_stage2(result.vision)
    return 0


_STAGE2_OPTIONS = {
    'infer_url': 'infer_base_url',
    'qwen_model': 'qwen_model_id',
    'qwen_body_model': 'qwen_body_model',
    'cache_epoch': 'cache_epoch',
    'timeout': 'timeout_seconds',
    'max_request_bytes': 'max_request_bytes',
    'max_response_bytes': 'max_response_bytes',
    'max_inference_calls': 'max_inference_calls',
    'max_http_seconds': 'max_http_seconds',
    'ffmpeg': 'ffmpeg',
    'ffprobe': 'ffprobe',
}


def _content_settings(args):
    from .settings import ContentSettings

    return ContentSettings(
        infer_base_url=args.infer_url,
        tokenizer_path=args.tokenizer_path,
        max_inference_calls=args.content_max_calls,
        max_http_seconds=args.content_max_http_seconds,
    )


def _show_content(result):
    print(
        f'content_run={result.run_id} execution_status={result.execution_status} quality_status={result.quality_status}'
    )
    print(f'html={result.html_path} report={result.report_path}')
    print(
        f'inference_calls={result.inference_calls} reused_jobs={result.reused_jobs} unresolved={result.unresolved}'
    )
    return (
        3
        if result.execution_status in ('paused_budget', 'blocked_unknown')
        else (0 if result.html_path else 1)
    )


def _content_main(argv):
    from . import content
    from .settings import ContentSettings

    parser = argparse.ArgumentParser(prog='extractube ' + argv[0])
    parser.add_argument('video_id')
    parser.add_argument('--data-root', type=Path, default=Path('data'))
    parser.add_argument('--run', dest='run_id')
    if argv[0] == 'stage3':
        parser.add_argument('--vision-run-id')
        parser.add_argument('--resume', dest='resume_run_id')
        parser.add_argument('--review', type=Path)
        parser.add_argument('--render-only', action='store_true')
        parser.add_argument('--tokenizer-path')
        parser.add_argument('--infer-url')
        parser.add_argument('--max-inference-calls', type=int)
        parser.add_argument('--max-http-seconds', type=float)
    args = parser.parse_args(argv[1:])
    try:
        if argv[0] == 'stage3-verify':
            print(content.verify(args.video_id, data_root=args.data_root, run_id=args.run_id))
            return 0
        if args.render_only:
            if args.resume_run_id or args.review:
                parser.error('--render-only와 --resume/--review는 함께 사용할 수 없습니다.')
            return _show_content(
                content.render_only(args.video_id, data_root=args.data_root, run_id=args.run_id)
            )
        if args.run_id:
            parser.error('--run은 --render-only 또는 stage3-verify에서 사용합니다.')
        values = {
            k: v
            for k, v in {
                'tokenizer_path': args.tokenizer_path,
                'infer_base_url': args.infer_url,
            }.items()
            if v is not None
        }
        limits = {
            k: getattr(args, k)
            for k in ('max_inference_calls', 'max_http_seconds')
            if getattr(args, k) is not None
        }
        if args.resume_run_id:
            from dataclasses import replace

            settings = replace(
                content.load_settings(args.video_id, args.data_root, args.resume_run_id), **values
            )
        else:
            settings = ContentSettings(**values, **limits)
        return _show_content(
            content.run(
                args.video_id,
                settings,
                data_root=args.data_root,
                vision_run_id=args.vision_run_id,
                resume_run_id=args.resume_run_id,
                review_path=args.review,
                resume_budget_limits=limits if args.resume_run_id else None,
            )
        )
    except KeyboardInterrupt:
        return 130
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        return (
            3
            if exc.kind in ('blocked_unknown', 'paused_budget')
            else (2 if exc.kind == 'usage' else 1)
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Stage 3 입력/저장 오류: {exc}', file=sys.stderr)
        return 1


def _show_stage2(result):
    print(f'run_id={result.run_id} execution_status={result.execution_status}')
    print(f'inference_calls={result.inference_calls} reused_jobs={result.reused_jobs}')
    if result.handoff_path is not None:
        print(f'handoff={result.handoff_path}')
    if result.warnings:
        print(
            f'Stage 2 경고 {len(result.warnings)}건: handoff/review.html의 누락 범위를 확인하십시오.',
            file=sys.stderr,
        )
    if result.execution_status in ('paused_budget', 'blocked_unknown'):
        return 3
    return 0 if result.stage3_usable else 1


def _batch_main(argv: list[str]) -> int:
    from .pipeline import run_batch

    parser = argparse.ArgumentParser(
        prog="extractube batch", description="여러 YouTube URL을 단계별로 일괄 처리합니다."
    )
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--urls-file", type=Path, default=None)
    parser.add_argument("--through", choices=("stage1", "stage2", "stage3"), default="stage3")
    parser.add_argument("--tokenizer-path", default="")
    parser.add_argument("--content-max-calls", type=int, default=100)
    parser.add_argument("--content-max-http-seconds", type=float, default=7200)
    parser.add_argument("--adaptive-ceiling", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--infer-url", default="http://127.0.0.1:8095")
    parser.add_argument("--asr-model", default="qwen-asr")
    parser.add_argument("--fa-model", default="qwen-fa")
    parser.add_argument("--language", choices=("ko", "en"), default="ko")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--timeout", type=float, default=7200)
    parser.add_argument("--format", dest="yt_dlp_format", default=Settings.yt_dlp_format)
    parser.add_argument("--merge-format", default="mp4")
    parser.add_argument("--max-body-bytes", type=int, default=1073741824)
    args = parser.parse_args(argv[1:])

    urls = list(args.urls)
    if args.urls_file is not None:
        if not args.urls_file.is_file():
            print(f"URL 목록 파일이 없습니다: {args.urls_file}", file=sys.stderr)
            return 2
        for line in args.urls_file.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                urls.append(s)
    if not urls:
        print("처리할 URL을 인자 또는 --urls-file로 지정하십시오.", file=sys.stderr)
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
        batch = run_batch(
            urls,
            settings,
            through=args.through,
            content_settings=_content_settings(args) if args.through == "stage3" else None,
            force=args.force,
            adaptive_ceiling=args.adaptive_ceiling,
            continue_on_error=not args.fail_fast,
        )
    except KeyboardInterrupt:
        print("취소되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다.", file=sys.stderr)
        return 130
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        return (
            3
            if exc.kind in ("blocked_unknown", "paused_budget")
            else (2 if exc.kind == "usage" else 1)
        )

    for item in batch.items:
        vid = item.video_id or "-"
        if item.error is not None:
            print(
                f"item={vid} url={item.url} status=failed kind={item.error_kind} error={item.error}"
            )
        elif item.result is not None and item.result.content is not None:
            print(
                f"item={vid} status={item.result.content.execution_status} html={item.result.content.html_path}"
            )
        elif item.result is not None and item.result.vision is not None:
            print(
                f"item={vid} status={item.result.vision.execution_status} handoff={item.result.vision.handoff_path}"
            )
        else:
            print(
                f"item={vid} status=complete work_dir={item.result.work_dir if item.result else '-'}"
            )
    print(f"batch_succeeded={batch.succeeded} batch_failed={batch.failed}")
    return 0 if batch.failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "batch":
        return _batch_main(argv)
    if argv and argv[0] in ('stage3', 'stage3-verify'):
        return _content_main(argv)
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
        group.add_argument('--from', dest='from_stage', choices=('t1', 't2', 't3', 't4', 't5'))
        group.add_argument('--resume', dest='resume_run_id')
        parser.add_argument('--retry-unknown', action='append', default=[])
        for option, field in _STAGE2_OPTIONS.items():
            kind = (
                int
                if field in ('max_request_bytes', 'max_response_bytes', 'max_inference_calls')
                else (float if field in ('timeout_seconds', 'max_http_seconds') else str)
            )
            parser.add_argument('--' + option.replace('_', '-'), type=kind, default=None)
    args = parser.parse_args(argv[1:])
    try:
        if argv[0] == 'stage2-verify':
            result = verify(args.video_id, data_root=args.data_root, run_id=args.run_id)
            print('ok=' + str(result['ok']).lower() + ' quality_review_performed=false')
            return 0
        values = {
            field: getattr(args, option)
            for option, field in _STAGE2_OPTIONS.items()
            if getattr(args, option) is not None
        }
        limits = None
        if args.resume_run_id:
            settings = load_settings(args.video_id, args.data_root, args.resume_run_id)
            limits = {
                k: values.pop(k) for k in ('max_inference_calls', 'max_http_seconds') if k in values
            }
            settings = replace(settings, **values)
        else:
            settings = VisionSettings(**values)
        return _show_stage2(
            stage2_run(
                args.video_id,
                settings,
                data_root=args.data_root,
                force=args.force,
                from_stage=args.from_stage,
                resume_run_id=args.resume_run_id,
                retry_unknown_call_ids=tuple(args.retry_unknown),
                resume_budget_limits=limits,
            )
        )
    except KeyboardInterrupt:
        print('취소되었습니다. 전송 중인 호출은 재개 시 unknown으로 확인합니다.', file=sys.stderr)
        return 130
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        if exc.diagnostic is not None:
            print(f'diagnostic={exc.diagnostic}', file=sys.stderr)
        return (
            3
            if exc.kind in ('blocked_unknown', 'paused_budget')
            else (2 if exc.kind == 'usage' else 1)
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Stage 2 입력/저장 오류: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
