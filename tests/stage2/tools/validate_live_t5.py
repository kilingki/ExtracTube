#!/usr/bin/env python3
"""Spec 14.4 A: one fixed configuration; 12 images, <=12 HTTP calls, <=900 seconds."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid
from extractube import assets, frames, infer as inference
from extractube.store import read, write, sha, ref, digest, HashCache, identity
from extractube.settings import VisionSettings
from extractube.visual_policy import snapshot
from extractube.visual_input import flatten
from extractube.timestamps import span, seconds
from extractube.errors import InferencePaused

ROOT=Path(__file__).resolve().parents[3]
INDICES=(5,6,8,15,24,25,26,28,3,13,22,27)

def main(indices=INDICES, max_calls=12, max_http_seconds=900):
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:12]
    work=ROOT/'tests/stage2/live'/run_id/'t5_samples';directory=work/f'vision/runs/{run_id}';directory.mkdir(parents=True)
    settings=VisionSettings(max_inference_calls=max_calls,max_http_seconds=max_http_seconds);config=snapshot(settings)
    write(directory/'config.json',config,immutable=True)
    state={'execution_status':'running','current_stage':'t5','budget':{'max_calls':max_calls,'max_http_seconds':max_http_seconds,'http_calls':0,'http_seconds':0,'reserved_calls':0},'blocked_call_ids':[],'warnings':[]}
    ctx=SimpleNamespace(work=work,directory=directory,run_id=run_id,settings=settings,config=config,config_sha=sha(directory/'config.json'),state=state,hashes=HashCache(),critical_refs=[],invocation_calls=0,reused_jobs=0)
    ctx.emit=lambda message:print(message,flush=True)
    ctx.save_state=lambda:write(directory/'state.json',state)
    ctx.allow_reuse=lambda stage:False;ctx.require_reuse=lambda stage:False
    ctx.failure_ref=lambda exc:ref(work,exc.diagnostic/'record.json') if exc.diagnostic else None
    def check_inputs():
        for r in ctx.critical_refs:ctx.hashes.check(work,r)
        for filename,value in config['code_sha256'].items():
            if sha(ROOT/'src/extractube'/filename)!=value:raise ValueError('code changed during frozen validation')
    ctx.check_inputs=check_inputs;ctx.jobs=inference.Jobs(ctx)
    inventory=read(ROOT/'tests/stage2/T5/visual_review.json')['frames'];old=read(ROOT/'tests/stage2/T4/runs/20261025T154421Z_9076fc/t5_handoff.json')
    old_frames={f['frame_id']:f for f in old['frames']};sources={c['candidate_id']:c for c in old['candidate_sources']}
    for video in ('wFikV0Y7NIk','dgGhsYSVzKc'):
        doc=read(ROOT/f'tests/stage2/T4/runs/20261027T184333Z_21a346/{video}/selected_frames.json')
        for c in doc['candidates']:
            sources[c['candidate_id']]={**c,'media':doc['media']}
            for f in c['frames']:old_frames[f['frame_id']]={**f,'candidate_id':c['candidate_id']}
    manifest=[];groups=defaultdict(list)
    for index in indices:
        r=next(r for r in inventory if r['review_evidence']['review_index']==index);f=old_frames[r['frame_id']];source=sources[f['candidate_id']];p=ROOT/f['full']['path']
        if sha(p)!=r['sha256'] or sha(p)!=f['full']['sha256']:raise ValueError('fixed source image mismatch')
        video=f['frame_id'].split(':')[0];fid=f'{video}:v{f["stream_index"]}:pts{f["pts"]}'
        copied=work/f'inputs/{video}/pts{f["pts"]}.png';write(copied,p.read_bytes(),immutable=True,raw=True)
        row={'review_index':index,'source_frame_id':f['frame_id'],'source_png':f['full'],'product_frame_id':fid,'candidate_id':f['candidate_id'],'copied_png':ref(work,copied),'source_t2':{'path':source['source_t2_ref'],'sha256':source['source_t2_sha256']},'source_t3':{'path':source['source_t3_ref'],'sha256':source['source_t3_sha256']}}
        manifest.append(row);ctx.critical_refs.append(row['copied_png']);groups[f['candidate_id']].append((f,source,fid,copied))
    write(directory/'mapping.json',manifest,immutable=True);outputs=[]
    try:
        for cid,rows in groups.items():
            rows.sort(key=lambda r:r[0]['pts']);source=rows[0][1];video=cid.split(':')[0];meta=read(ROOT/f'data/{video}/metadata.json')
            raw=(ROOT/f'data/{video}'/meta['results']['sentences']['path']).read_bytes()
            if __import__('hashlib').sha256(raw).hexdigest()!=meta['results']['sentences']['sha256']:raise ValueError('sentence identity mismatch')
            text_path=work/f'inputs/{video}/sentences.json';write(text_path,raw,immutable=True,raw=True);ctx.critical_refs.append(ref(work,text_path))
            sentence_payload=read(text_path);sentences,_=flatten(sentence_payload);sc=source['source_candidate'];uid=cid.rsplit(':c',1)[0]
            ar=span(sc['audio_start'],sc['audio_end'],'audio_start');vr=span(sc['video_start'],sc['video_end'],'video_start')
            c={'candidate_id':cid,'unit_id':uid,'chunk_index':sc['chunk_index'],'sentence_start':sc['sentence_start'],'sentence_end':sc['sentence_end'],'requested_video_range':vr,'effective_video_range':vr,'range_status':'valid'}
            unit={'unit_id':uid,'video_range':vr};ctx.video_id=video;ctx.input={'stream':{'start_time':source['media']['start_time']},'stage1_identities':{'sentences':identity(meta['results']['sentences'])}}
            physical=[];associations=[]
            for f,source,fid,copied in rows:
                full=frames.image_ref(work,copied);vlm,filter_text=frames.vlm_image(ctx,full)
                physical.append({'frame_id':fid,'stream_index':f['stream_index'],'pts':f['pts'],'time_base':f['time_base'],'video_seconds':seconds(f['video_seconds']),'source_video':identity(meta['results']['video']),'full':full,'vlm':vlm,'display_transform':'existing original PNG','extraction_job_ref':None})
                associations.append({'association_id':cid+'@'+fid,'candidate_id':cid,'unit_id':uid,'segment_id':cid+':g'+str(f['segment_index']),'segment_index':f['segment_index'],'frame_id':fid,'extraction_status':'ok','t4_status':'success','t4_decision':'uncertain','original_t4_decision':f.get('model_decision',f['decision']),'t4_reason':None,'t4_job_ref':None})
            t2={'units':[{'unit_id':uid,'chunks':[{'chunk_id':cid,'inference_status':'success','decision':'needed'}]}]};t3={cid:{'execution_status':'ok','warnings':[]}}
            payload,warnings=assets.run(ctx,sentences,[unit],[c],{'frames':physical,'associations':associations},t2,t3)
            outputs.append({'candidate_id':cid,'frames':physical,'associations':associations,'payload':payload,'warnings':warnings});write(directory/'progress.json',outputs)
            print('candidate='+cid+' decisions='+','.join(d['decision'] for d in payload['decisions']),flush=True)
        state['execution_status']='complete'
    except InferencePaused as exc:state['execution_status']=exc.status
    finally:
        ctx.save_state();write(directory/'classification_results.json',{'run_id':run_id,'status':state['execution_status'],'mapping':manifest,'groups':outputs,'budget':state['budget'],'quality_review_performed':False},immutable=True)
        print('T5_FIXED_RESULT='+str(directory/'classification_results.json'),flush=True)
    return 0 if state['execution_status']=='complete' else 3
if __name__=='__main__':raise SystemExit(main())

