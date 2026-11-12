"""Run Stage 3 on recovery benchmark videos and verify end-to-end cryptographic integrity."""
import sys
import time
import shutil
from pathlib import Path
from extractube import content, store
from extractube.settings import ContentSettings

RUNS = Path(__file__).resolve().parent / 'runs'
DATA_ROOT = RUNS / 'batches/remaining-eight/stage3data'
MANIFEST = store.read(RUNS / 'recovery/rerun-two/manifest.json')
CFG = ContentSettings(**MANIFEST['settings']['content'])

VIDEOS = ['dgGhsYSVzKc', 'iVelddb1qQ4', 'hGVL9XRU9IQ', 'CvuE-jyg-N8']

def main():
    print(f"Starting Stage 3 recovery run on: {VIDEOS}")
    results = {}
    for vid in VIDEOS:
        vid_dir = DATA_ROOT / vid
        content_runs_dir = vid_dir / 'content/runs'
        if content_runs_dir.is_dir():
            for p in list(content_runs_dir.glob('*')):
                if p.is_dir():
                    shutil.rmtree(p)
        current_file = vid_dir / 'content/current.json'
        if current_file.exists():
            current_file.unlink()

        print(f"\n==========================================")
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] RUNNING Stage 3 for {vid}...")
        t0 = time.monotonic()
        try:
            res = content.run(vid, CFG, data_root=DATA_ROOT, log=print)
            elapsed = time.monotonic() - t0
            print(f"Completed {vid} in {elapsed:.1f}s: execution_status={res.execution_status}")
            verify_res = content.verify(vid, data_root=DATA_ROOT)
            print(f"Verification {vid}: {verify_res}")
            results[vid] = {
                'ok': True,
                'execution_status': res.execution_status,
                'elapsed_seconds': elapsed,
                'calls': res.inference_calls,
                'reused': res.reused_jobs,
                'unresolved': res.unresolved,
                'verify': verify_res,
            }
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"FAILED {vid} in {elapsed:.1f}s: {exc}")
            import traceback
            traceback.print_exc()
            results[vid] = {
                'ok': False,
                'error': str(exc),
                'elapsed_seconds': elapsed,
            }
            # Stop immediately on failure so we can inspect
            break

    print("\n==========================================")
    print("RECOVERY RESULTS SUMMARY:")
    for vid, r in results.items():
        print(f"  {vid}: {r}")

if __name__ == '__main__':
    main()
