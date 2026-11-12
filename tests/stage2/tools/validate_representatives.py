"""Bounded live comparison on saved images; never rewrites historical runs."""
import argparse
import copy
from datetime import datetime,timezone
from pathlib import Path
import shutil
import uuid
from PIL import Image,ImageDraw
from extractube import store,vision,selection,assets,visual_policy,infer
from extractube.settings import VisionSettings
from extractube.errors import InferenceJobFailed,VisionError

ROOT=Path(__file__).resolve().parents[3]
SOURCES=(('bjDkfjSmXVo','20261106T105517Z_bd7504c6e903'),
         ('Fw-8hZhnH3Q','20261106T042147Z_be81c6aa1179'))
INDICES=(0,1,2,3,20,21,22,23,100,101,102,103)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:12]
    output=ROOT/'tests/stage2/outputs'/('representatives_'+run_id)
    settings=VisionSettings(max_inference_calls=24,max_http_seconds=3600)
    config=visual_policy.snapshot(settings)
    manifest={'source':str(args.source.resolve()),'indices':INDICES,'settings':config,
              'max_calls_total':24,'max_http_seconds_total':3600,'retry_policy':'none',
              'scope':'24 saved frames, two candidate ranges; not a full-video quality gate',
              'stop_condition':'first failed/unknown call or no reduction; manual source comparison required',
              'sources':[],'runner_sha256':store.sha(Path(__file__))}
    store.write(output/'manifest.json',manifest)
    reports=[];total_calls=0;total_seconds=0
    for video,source_run in SOURCES:
        old=args.source/'data'/video;source=old/'vision/runs'/source_run
        work=output/'data'/video;directory=work/'vision/runs'/run_id
        inp=store.read(source/'input.json');meta=store.read(old/'metadata.json')
        for record in meta['results'].values():
            src=store.safe(old,record['path']);dest=store.safe(work,record['path'])
            if store.sha(src)!=record['sha256']:raise ValueError('source hash')
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
        store.write(work/'metadata.json',meta)
        t2=store.read(source/'t2/visual_candidates.json')['payload']
        candidate=next(c for c in t2['candidates'] if c['candidate_id']==video+':u1:c0')
        tp=source/'t3/u000001_c000000/screen_changes.json'
        original=store.read(tp);selected=copy.deepcopy(original)
        selected['payload']['samples']=[original['payload']['samples'][i] for i in INDICES]
        oldframes=store.read(source/'t4/frames.json')['payload'];wanted={s['frame_id'] for s in selected['payload']['samples']}
        for frame in oldframes['frames']:
            if frame['frame_id'] not in wanted:continue
            for kind in ('full','vlm'):
                record=frame[kind];src=store.safe(old,record['path']);dest=store.safe(work,record['path'])
                if store.sha(src)!=record['sha256']:raise ValueError('image source hash')
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
        # Reuse verified physical PNGs through their existing frame records.
        from unittest.mock import patch
        frame_map={f['frame_id']:f for f in oldframes['frames'] if f['frame_id'] in wanted}
        for name,value in [('config',config),('input',inp)]:store.write(directory/f'{name}.json',value,immutable=True)
        state={'execution_status':'running','current_stage':'pilot','budget':{'max_calls':24-total_calls,'max_http_seconds':3600-total_seconds,'http_calls':0,'http_seconds':0,'reserved_calls':0},'blocked_call_ids':[],'warnings':[]}
        ctx=vision.Context(work,video,directory,settings,config,inp,state,{'stage_counts':{},'job_ids':[]},store.HashCache(),False,None,lambda m:print(m,flush=True))
        class PilotJobs(vision.PolicyJobs):
            def invoke(self,*args,**kwargs):
                try:return super().invoke(*args,**kwargs)
                except InferenceJobFailed as exc:
                    raise VisionError('대표 검증 첫 확정 실패: '+str(exc),kind='pilot_failed') from exc
        ctx.jobs=PilotJobs(ctx)
        sentences=store.read(source/'sentences.json')['payload']['sentences']
        units=store.read(source/'t1/semantic_units.json')['payload']['units']
        with patch.object(selection.frames,'extract',return_value=frame_map):
            fd,t4,warnings=selection.run(ctx,sentences,[candidate],{candidate['candidate_id']:selected})
        if any(b['status']!='success' for b in t4['batches']):raise RuntimeError('T4 failed; inspect preserved call')
        t5,ww=assets.run(ctx,sentences,units,[candidate],fd,t2,{candidate['candidate_id']:selected})
        if any(b['status']!='success' for b in t5['batches']):raise RuntimeError('T5 failed; inspect preserved call')
        total_calls+=state['budget']['http_calls'];total_seconds+=state['budget']['http_seconds']
        removed=sum(bool(a.get('t4_duplicate_of')) for a in fd['associations'])
        old_keep=sum(a['frame_id'] in wanted and a['t4_decision']=='keep' for a in oldframes['associations'])
        report={'video_id':video,'source_t3':store.ref(old,tp),'selected_indices':INDICES,'old_keep':old_keep,
                'sample_count':len(wanted),'duplicates':removed,'t5_targets':len(t5['decisions']),
                't4':t4,'frames':fd,'t5':t5,'calls':state['budget'],'manual_quality_status':'unreviewed'}
        store.write(directory/'comparison.json',report,immutable=True)
        sheet=Image.new('RGB',(1000,12*165),'white');draw=ImageDraw.Draw(sheet)
        for i,a in enumerate(fd['associations']):
            f=frame_map[a['frame_id']];im=Image.open(store.safe(work,f['full']['path'])).convert('RGB');im.thumbnail((720,145))
            sheet.paste(im,(0,i*165));draw.text((725,i*165+5),f'{i}: {f["video_seconds"]}s\n{a["t4_decision"]}\nrep: {a.get("t4_duplicate_of")}',fill='black')
        sheet.save(directory/'comparison.png');reports.append({k:report[k] for k in ('video_id','sample_count','old_keep','duplicates','t5_targets','calls')})
        manifest['sources'].append({'video_id':video,'source_t3':report['source_t3']});store.write(output/'manifest.json',manifest)
        if not removed:raise RuntimeError('No reduction; do not expand')
    store.write(output/'summary.json',{'transport_and_structure':'passed','quality':'manual_review_required','reports':reports,'calls':total_calls,'http_seconds':total_seconds})
    print('PILOT='+str(output),flush=True)

if __name__=='__main__':main()
