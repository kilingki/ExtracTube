"""Serial product profiling (unbounded by default, optional cutoffs), using immutable upstream hardlinks."""
import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import functools
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temp.replace(path)


class Cutoff(BaseException):
    pass


class Meter:
    def __init__(self, directory, total=None, phase=None):
        self.start = time.monotonic()
        self.total_limit, self.phase_limit = total, phase
        self.phase, self.phase_start = 'input', self.start
        self.phase_times = defaultdict(float)
        self.stack = []
        self.stats = defaultdict(lambda: {'calls': 0, 'wall_seconds': 0., 'self_seconds': 0., 'max_seconds': 0.})
        self.events = (directory / 'events.jsonl').open('w', buffering=1)

    def event(self, kind, **fields):
        self.events.write(json.dumps({'at': now(), 'elapsed': time.monotonic()-self.start,
                                     'event': kind, 'phase': self.phase, **fields}, default=str) + '\n')

    def check(self):
        elapsed = time.monotonic()-self.start
        phase_elapsed = self.phase_times[self.phase] + time.monotonic()-self.phase_start
        if (self.total_limit is not None and elapsed >= self.total_limit) or (
            self.phase_limit is not None and phase_elapsed >= self.phase_limit
        ):
            reason = f'cutoff: phase={self.phase}, phase_seconds={phase_elapsed:.3f}, total_seconds={elapsed:.3f}'
            self.event('cutoff', reason=reason)
            raise Cutoff(reason)

    def change(self, phase):
        if phase != self.phase:
            self.check()
            self.phase_times[self.phase] += time.monotonic()-self.phase_start
            self.phase, self.phase_start = phase, time.monotonic()
            self.event('phase')

    def wrap(self, obj, name, label=None):
        original = getattr(obj, name)
        label = label or obj.__name__ + '.' + name
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            self.check()
            begin = time.monotonic()
            frame = [begin, 0.]
            self.stack.append(frame)
            self.event('begin', function=label)
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = time.monotonic()-begin
                self.stack.pop()
                if self.stack:
                    self.stack[-1][1] += elapsed
                stat = self.stats[label]
                stat['calls'] += 1
                stat['wall_seconds'] += elapsed
                stat['self_seconds'] += max(0., elapsed-frame[1])
                stat['max_seconds'] = max(stat['max_seconds'], elapsed)
                self.event('end', function=label, seconds=elapsed)
        setattr(obj, name, wrapped)

    def install(self):
        from extractube import (vision, content, semantic, candidates, screen_changes,
                                frames, selection, assets, handoff, infer, content_plan, article)
        original = vision.Context.save_state
        def save(ctx):
            self.change(ctx.state['current_stage'])
            return original(ctx)
        vision.Context.save_state = save
        invoke = content.Context.invoke
        def content_invoke(ctx, stage, *args, **kwargs):
            self.change(stage)
            return invoke(ctx, stage, *args, **kwargs)
        content.Context.invoke = content_invoke
        for module, names in [
            (semantic, ['run']), (candidates, ['run']), (screen_changes, ['detect_many', 'detect', 'decode']),
            (frames, ['extract', 'vlm_image', 'check_image']),
            (selection, ['run', 'classify']), (assets, ['run']),
            (handoff, ['build', 'validate', 'verify_locked', 'html_document']),
            (content, ['load_input', '_draft', 'verify_locked']),
            (content_plan, ['generation_plan', 'check_budget']),
            (article, ['assemble', 'render', 'validate']),
            (vision.Context, ['check_inputs']), (content.Context, ['check_inputs']),
            (infer, ['budget_from_journals']), (infer.Jobs, ['invoke']),
            (infer.HttpTransport, ['send'])]:
            for name in names:
                self.wrap(module, name)

    def finish(self):
        self.phase_times[self.phase] += time.monotonic()-self.phase_start
        return {'wall_seconds': time.monotonic()-self.start, 'last_phase': self.phase,
                'phase_seconds': dict(self.phase_times), 'functions': dict(self.stats)}


def calls(work, namespace):
    rows = []
    for path in sorted((work/namespace/'runs').glob('*/calls/*/request.json')):
        req = read(path)
        record = read(path.with_name('record.json')) if path.with_name('record.json').exists() else {}
        rows.append({'path': str(path.parent.relative_to(ROOT)), 'task': req['task'],
                     'request_bytes': path.stat().st_size, 'images': len(req.get('image_refs', [])),
                     **{k: record.get(k) for k in ('status', 'http_status', 'elapsed_seconds', 'error_code', 'usage')}})
    return rows


def worker(args):
    from extractube import vision, content
    from extractube.settings import VisionSettings, ContentSettings
    campaign = read(args.run/'manifest.json')
    directory = args.run/'measurements'/args.video/args.stage
    directory.mkdir(parents=True, exist_ok=True)
    meter = Meter(directory, campaign['limits'][args.stage], campaign['limits']['phase'])
    meter.install()
    work_root = args.run/('data' if args.stage == 'stage2' else 'stage3data')
    row = {'video_id': args.video, 'stage': args.stage, 'started_at': now()}
    try:
        v_cfg = VisionSettings(**campaign['settings']['vision'])
        c_cfg = ContentSettings(**campaign['settings']['content'])
        if campaign.get('adaptive_ceiling'):
            meta_p = work_root/args.video/'metadata.json'
            dur = read(meta_p).get('duration_seconds') if meta_p.is_file() else None
            if dur:
                v_cfg = v_cfg.with_duration_ceiling(dur)
                c_cfg = c_cfg.with_duration_ceiling(dur)
        if args.stage == 'stage2':
            result = vision.run(args.video, v_cfg, data_root=work_root,
                                log=lambda message: meter.event('log', message=message))
        else:
            result = content.run(args.video, c_cfg, data_root=work_root,
                                 log=lambda message: meter.event('log', message=message))
        row.update(status=result.execution_status, result=asdict(result))
    except (Cutoff, KeyboardInterrupt) as exc:
        row.update(status='stopped_slow', reason=str(exc) or 'hard_watchdog')
    except Exception as exc:
        row.update(status='failed', error={'type': type(exc).__name__, 'kind': getattr(exc, 'kind', None), 'message': str(exc)})
        traceback.print_exc()
    finally:
        row.update(meter.finish())
        row['finished_at'] = now()
        ns = 'vision' if args.stage == 'stage2' else 'content'
        row['calls'] = calls(work_root/args.video, ns)
        row['runs'] = [{'run_id': p.parent.name, 'state': read(p),
                        'plan': read(p.with_name('plan.json')) if p.with_name('plan.json').exists() else None}
                       for p in (work_root/args.video/ns/'runs').glob('*/state.json')]
        write(directory/'result.json', row)
        print(json.dumps({k: row[k] for k in ('video_id', 'stage', 'status', 'wall_seconds', 'last_phase') if k in row}), flush=True)


def seed(source, target, refs):
    target.mkdir(parents=True, exist_ok=True)
    for rel in refs:
        dest = target/rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel == 'metadata.json':
            shutil.copy2(source/rel, dest)
        else:
            os.link(source/rel, dest)


def emit_line(run, message):
    print(message, flush=True)
    with (run/'runner.log').open('a', encoding='utf-8') as f:
        f.write(message + '\n')


def execute(run, video, stage, limit):
    directory = run/'measurements'/video/stage
    directory.mkdir(parents=True, exist_ok=True)
    watchdog = None if limit is None else limit + 330
    with (directory/'worker.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, __file__, '--run', str(run), '--video', video, '--stage', stage],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            proc.wait(timeout=watchdog)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    path = directory/'result.json'
    row = read(path) if path.exists() else {'video_id': video, 'stage': stage, 'status': 'worker_failed', 'returncode': proc.returncode}
    emit_line(run, f'{now()} {video} {stage} {row["status"]} {row.get("wall_seconds", 0):.1f}s {row.get("last_phase", "")}')
    return row


def main(args):
    from extractube import store
    from extractube.settings import VisionSettings, ContentSettings
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import analyze
    all_urls = [s.strip() for s in (ROOT/'tests/stage1/test_inputs.txt').read_text().splitlines() if s.strip()]
    assert len(all_urls) == len(set(all_urls)) == 10
    urls = [u for u in all_urls if u.split('v=')[1] in set(args.videos)] if args.videos else all_urls
    if not urls:
        raise ValueError('No matching videos for --videos')
    limits = {'phase': args.phase_limit, 'stage2': args.stage2_limit, 'stage3': args.stage3_limit}
    unbounded = all(v is None for v in limits.values())
    category = 'batches' if unbounded else 'diagnostics'
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run = (args.run or ROOT/'tests/bottleneck/runs'/category/timestamp).resolve()
    run.mkdir(parents=True, exist_ok=False)
    tokenizer = '/home/kjh/workspace/models/llm/tf/qwen3.8-27b'
    vision_cfg = VisionSettings(infer_base_url=args.infer_url,
                                max_http_seconds=args.vision_max_http_seconds,
                                max_inference_calls=args.vision_max_calls)
    content_cfg = ContentSettings(infer_base_url=args.infer_url,
                                  tokenizer_path=tokenizer,
                                  max_http_seconds=args.content_max_http_seconds,
                                  max_inference_calls=args.content_max_calls)
    stop_policy = (
        'unbounded wall-clock (no phase/stage cutoff); cumulative HTTP/call budget raised; single-request HTTP timeout 300s preserved'
        if unbounded else
        'cooperative function/request boundaries; finish in-flight call (300s timeout); hard watchdog stage limit+330s'
    )
    manifest = {'created_at': now(), 'urls': urls, 'limits': limits, 'adaptive_ceiling': bool(args.adaptive_ceiling),
                'settings': {'vision': asdict(vision_cfg), 'content': asdict(content_cfg)},
                'source_code': {p.name: store.sha(p) for p in (ROOT/'src/extractube').glob('*.py')},
                'runner_sha256': store.sha(Path(__file__)), 'stage1': {}, 'stage3_sources': {}, 'model_concurrency': 1,
                'stop_policy': stop_policy,
                'cache': 'fresh Stage2/Stage3 model cache, warm server; immutable upstream hardlinks, metadata copies',
                'retries': 'no orchestration retries; existing product T1 format retry and Stage3 bounded repair unchanged'}
    for url in urls:
        video = url.split('v=')[1]
        source = ROOT/'data'/video
        meta = read(source/'metadata.json')
        refs = {r['path']: r['sha256'] for r in meta['results'].values()}
        refs['metadata.json'] = store.sha(source/'metadata.json')
        for rel, expected in refs.items():
            if store.sha(source/rel) != expected:
                raise ValueError('Stage1 hash mismatch: '+video+'/'+rel)
        manifest['stage1'][video] = refs
        seed(source, run/'data'/video, refs)
    write(run/'manifest.json', manifest)
    summary = {'status': 'running', 'run': str(run), 'videos': []}
    write(run/'summary.json', summary)
    emit_line(run, 'campaign='+str(run))
    for url in urls:
        video = url.split('v=')[1]
        emit_line(run, f'{now()} START {video} stage2')
        s2 = execute(run, video, 'stage2', manifest['limits']['stage2'])
        target = run/'stage3data'/video
        candidates = [run/'data'/video]
        candidates += [p/'data'/video for p in sorted((ROOT/'tests/stage3/outputs').glob('e2e_*'), reverse=True)]
        source = next((p for p in candidates if (p/'vision/current.json').exists()), None)
        if source:
            meta = read(source/'metadata.json')
            s3_refs = {r['path']: r['sha256'] for r in meta['results'].values()}
            s3_refs['metadata.json'] = store.sha(source/'metadata.json')
            for rel, expected in s3_refs.items():
                if store.sha(source/rel) != expected:
                    raise ValueError('Stage3 upstream hash mismatch: '+str(source/rel))
            seed(source, target, s3_refs)
            shutil.copytree(source/'vision', target/'vision', copy_function=os.link,
                            ignore=shutil.ignore_patterns('.tmp'))
            origin = {'kind': 'same_run' if source == candidates[0] else 'historical_handoff',
                      'work': str(source.relative_to(ROOT)), 'pointer': read(source/'vision/current.json'),
                      'stage1_refs': s3_refs, 'stage1_matches_primary': s3_refs == manifest['stage1'][video]}
            manifest['stage3_sources'][video] = origin
            write(run/'manifest.json', manifest)
            write(run/'measurements'/video/'stage3_input.json', origin)
            emit_line(run, f'{now()} START {video} stage3 ({origin["kind"]})')
            s3 = execute(run, video, 'stage3', manifest['limits']['stage3'])
            s3['input_origin'] = origin
        else:
            s3 = {'video_id': video, 'stage': 'stage3', 'status': 'blocked_upstream',
                  'reason': 'No completed product Stage2 handoff. Partial Stage2 is not a valid Stage3 input.'}
            write(run/'measurements'/video/'stage3'/'result.json', s3)
            emit_line(run, f'{now()} {video} stage3 blocked_upstream')
        summary['videos'].append({'video_id': video, 'url': url, 'stage2': s2, 'stage3': s3})
        write(run/'summary.json', summary)
        analyze.main(run, quiet=True)
    summary['stage1_unchanged'] = all(store.sha(ROOT/'data'/v/rel) == sha
                                    for v, refs in manifest['stage1'].items() for rel, sha in refs.items())
    summary['stage3_upstreams_unchanged'] = all(
        store.sha(ROOT/origin['work']/rel) == expected
        for origin in manifest['stage3_sources'].values()
        for rel, expected in origin['stage1_refs'].items()
    )
    summary['source_code_unchanged'] = manifest['source_code'] == {p.name: store.sha(p) for p in (ROOT/'src/extractube').glob('*.py')}
    summary.update(status='finished', finished_at=now())
    write(run/'summary.json', summary)
    analyze.main(run, quiet=True)
    emit_line(run, 'FINISHED '+str(run))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, help='Output path for this campaign (default: runs/<category>/<UTC timestamp>)')
    parser.add_argument('--video')
    parser.add_argument('--stage', choices=['stage2', 'stage3'])
    parser.add_argument('--phase-limit', type=float, default=None)
    parser.add_argument('--stage2-limit', type=float, default=None)
    parser.add_argument('--stage3-limit', type=float, default=None)
    parser.add_argument('--vision-max-http-seconds', type=float, default=1_000_000.0)
    parser.add_argument('--vision-max-calls', type=int, default=1_000_000)
    parser.add_argument('--content-max-http-seconds', type=float, default=1_000_000.0)
    parser.add_argument('--content-max-calls', type=int, default=100_000)
    parser.add_argument('--infer-url', default='http://127.0.0.1:8095')
    parser.add_argument('--adaptive-ceiling', action='store_true')
    parser.add_argument('--videos', nargs='*', default=None)
    args = parser.parse_args()
    worker(args) if args.video else main(args)
