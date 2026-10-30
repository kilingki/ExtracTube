#!/usr/bin/env python3
"""Audit completed work in a paused run; never publish a partial Stage 3 handoff."""

import argparse
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from extractube import infer as inference, screen_changes as changes, frames
from extractube.store import read, write, sha, wire, HashCache, ref, identity
from extractube.errors import VisionError

def audit(work,directory):
    hashes=HashCache();config=read(directory/'config.json');source=read(directory/'input.json');state=read(directory/'state.json')
    meta=read(work/'metadata.json')
    for kind,saved in source['stage1_upstream_records'].items():
        assert identity(meta['results'][kind])==source['stage1_identities'][kind]
        assert meta['results'][kind]['upstream']==saved['upstream']
        hashes.check(work,saved)
    for name,expected in config['code_sha256'].items():
        p=directory/'source_snapshot/extractube/stage2'/name
        assert sha(p)==expected,('saved code drift',name)
    docs=[read(p) for p in directory.glob('t3/*/screen_changes.json')]
    for d in docs:
        if d['execution_status']!='failed':assert changes.replay(d['payload'])==d['payload']['differences']
    fd=read(directory/'t4/frames.json')['payload'];td=read(directory/'t4/decisions.json')['payload']
    ctx=type('AuditContext',(),{'work':work,'hashes':hashes})()
    checked=set()
    for f in fd['frames']:
        assert f['source_video']==source['stage1_identities']['video']
        for key in ('full','vlm'):
            r=f[key]
            if r['path'] not in checked:frames.check_image(ctx,r);checked.add(r['path'])
    statuses=Counter();tasks=Counter();t5=Counter();t5_completed_batches=set();t5_completed_targets=0;failures=[];requests=[]
    for rp in sorted((directory/'calls').glob('*/record.json')):
        r=read(rp);q=read(rp.parent/'request.json');requests.append(q)
        assert hashlib.sha256(wire(q['body'])).hexdigest()==q['body_sha256']
        if not r['response_complete']:raise VisionError('미확정 응답이 있어 partial audit를 중단합니다.',kind='validation')
        body=(rp.parent/'response.body').read_bytes();assert hashlib.sha256(body).hexdigest()==r['body_sha']
        try:
            parsed,status,info=inference.parse_response(body,r['http_status'],q['spec'],q['thinking'])
            assert status==r['status'] and parsed==r['parsed']
        except (ValueError,UnicodeError):
            assert r['status']=='failed'
            failures.append({'task':q['task'],'call_id':r['call_id'],'error':r['error_code'],'record':ref(work,rp)})
        statuses[r['status']]+=1;tasks[q['task']]+=1
        if q['task']=='t5':
            cid=q['dependencies']['candidate']['candidate_id'];key=(cid,tuple(q['spec']['targets']))
            assert key not in t5_completed_batches,'unexpected duplicate T5 dispatch'
            t5_completed_batches.add(key);t5_completed_targets+=len(q['spec']['targets'])
            if r['status']=='succeeded':t5.update(d['decision'] for d in r['parsed']['frames'])
            else:t5['failed']+=len(q['spec']['targets'])
    calls,elapsed,reserved=inference.budget_from_journals(directory)
    assert calls==state['budget']['http_calls'] and abs(elapsed-state['budget']['http_seconds'])<1e-6 and reserved==0
    grouped=defaultdict(list)
    for a in fd['associations']:
        if a['t4_status']=='success' and a['t4_decision'] in ('keep','uncertain'):grouped[a['candidate_id']].append(a['frame_id'])
    expected={(cid,tuple(fids[i:i+4])) for cid,fids in grouped.items() for i in range(0,len(fids),4)}
    assert t5_completed_batches<=expected
    pending=sorted(expected-t5_completed_batches)
    assert state['execution_status']=='paused_budget' and state['budget']['max_http_seconds']-elapsed<config['settings']['timeout_seconds']
    return {'schema_version':'extractube.stage2.partial_audit.v1','run_id':directory.name,'execution_status':state['execution_status'],'ok_completed_work':True,'structure_complete':False,'stage3_usable':False,'quality_review_performed':False,'new_inference_calls':0,'stage1_identities_checked':len(source['stage1_identities']),'t1_units':len(read(directory/'t1/semantic_units.json')['payload']['units']),'t3_candidates':len(docs),'t3_samples':sum(len(d['payload']['samples']) for d in docs),'t4_batches':len(td['batches']),'t4_physical_frames':len(fd['frames']),'t4_associations':len(fd['associations']),'unique_pngs_decoded':len(checked),'task_calls':dict(tasks),'call_statuses':dict(statuses),'budget':state['budget'],'t5_expected_batches':len(expected),'t5_completed_batches':len(t5_completed_batches),'t5_completed_targets':t5_completed_targets,'t5_raw_decisions_uncommitted':dict(t5),'t5_pending_batches':len(pending),'t5_pending_targets':sum(len(ids) for cid,ids in pending),'pending':[{'candidate_id':cid,'targets':list(ids)} for cid,ids in pending],'failures':failures,'notes':['Parsed T5 use decisions are not exported assets.','No current pointer or handoff was created.','Fixed A quality acceptance remains revise.']}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('video_id');parser.add_argument('run_id');args=parser.parse_args()
    work=(Path('data')/args.video_id).resolve();directory=work/f'vision/runs/{args.run_id}'
    result=audit(work,directory)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');out=Path('tests/stage2/live')/('resume_'+stamp)/'partial_run_audit.json'
    write(out,result,immutable=True)
    print({k:result[k] for k in ('ok_completed_work','stage3_usable','unique_pngs_decoded','task_calls','t5_completed_batches','t5_pending_batches','t5_pending_targets','t5_raw_decisions_uncommitted')})
    print('AUDIT='+str(out))
if __name__=='__main__':main()
