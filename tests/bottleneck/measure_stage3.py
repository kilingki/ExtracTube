"""Measure Stage3 against the exact Stage1 lineage of each selected handoff.

Use after the primary serial campaign; no concurrent model requests.
Initial mixed-lineage attempts are retained in the primary summary and files.
"""
import json
from pathlib import Path
import shutil
import os
import sys
from run import ROOT, read, write, seed, execute, now


def main(primary):
    from extractube import store
    if read(primary/'summary.json')['status'] != 'finished':
        raise ValueError('Wait for the serial primary campaign to finish first.')
    manifest = read(primary/'manifest.json')
    run = primary/'validated_stage3'
    run.mkdir(exist_ok=False)
    manifest = {**manifest, 'created_at': now(), 'purpose': 'Stage3 exact upstream lineage correction',
                'parent_campaign': str(primary), 'runner_sha256': store.sha(Path(__file__).with_name('run.py')),
                'preparer_sha256': store.sha(Path(__file__)), 'stage3_sources': {}}
    summary = {'status': 'running', 'videos': []}
    write(run/'summary.json', summary)
    for url in manifest['urls']:
        video = url.split('v=')[1]
        candidates = [primary/'data'/video]
        candidates += [p/'data'/video for p in sorted((ROOT/'tests/stage3/outputs').glob('e2e_*'), reverse=True)]
        source = next((p for p in candidates if (p/'vision/current.json').exists()), None)
        if source is None:
            row = {'video_id': video, 'stage': 'stage3', 'status': 'blocked_upstream',
                   'reason': 'No completed product Stage2 handoff.'}
        else:
            meta = read(source/'metadata.json')
            refs = {r['path']: r['sha256'] for r in meta['results'].values()}
            refs['metadata.json'] = store.sha(source/'metadata.json')
            for rel, expected in refs.items():
                if store.sha(source/rel) != expected:
                    raise ValueError('Upstream hash mismatch: '+str(source/rel))
            origin = {'kind': 'same_run' if source == candidates[0] else 'historical_handoff',
                      'work': str(source.relative_to(ROOT)), 'pointer': read(source/'vision/current.json'),
                      'stage1_refs': refs, 'stage1_matches_primary': refs == manifest['stage1'][video]}
            manifest['stage3_sources'][video] = origin
            write(run/'manifest.json', manifest)
            target = run/'stage3data'/video
            seed(source, target, refs)
            shutil.copytree(source/'vision', target/'vision', copy_function=os.link,
                            ignore=shutil.ignore_patterns('.tmp'))
            write(run/'measurements'/video/'stage3_input.json', origin)
            row = execute(run, video, 'stage3', manifest['limits']['stage3'])
            row['input_origin'] = origin
        summary['videos'].append({'video_id': video, 'stage3': row})
        write(run/'summary.json', summary)
    summary['upstreams_unchanged'] = all(store.sha(ROOT/origin['work']/rel) == expected
                                       for origin in manifest['stage3_sources'].values()
                                       for rel, expected in origin['stage1_refs'].items())
    summary['source_code_unchanged'] = manifest['source_code'] == {
        p.name: store.sha(p) for p in (ROOT/'src/extractube').glob('*.py')}
    summary.update(status='finished', finished_at=now())
    write(run/'summary.json', summary)
    print('FINISHED '+str(run), flush=True)


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
