"""Ten-video product validation. Preserves existing data and records each stage separately."""
import argparse
from contextlib import redirect_stdout,redirect_stderr
from dataclasses import asdict
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import time
import traceback
import uuid
from extractube import pipeline,store,content
from extractube.settings import Settings,VisionSettings,ContentSettings
from extractube.content_plan import TokenCounter

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path)
    parser.add_argument('--tokenizer-path',required=True);parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--vision-max-http-seconds',type=float,default=36000.0)
    parser.add_argument('--content-max-http-seconds',type=float,default=7200.0)
    parser.add_argument('--retry-incomplete',action='store_true')
    parser.add_argument('--retry-unknown',action='append',default=[],help='Explicitly authorized interrupted call ID; never retries newly unknown calls.')
    args=parser.parse_args()
    directory=args.run or ROOT/'tests/stage3/outputs'/('e2e_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:6])
    directory=directory.resolve();directory.mkdir(parents=True,exist_ok=True)
    manifest_path=directory/'manifest.json';counter=TokenCounter(args.tokenizer_path)
    vision_cfg=VisionSettings(max_http_seconds=args.vision_max_http_seconds)
    content_cfg=ContentSettings(tokenizer_path=args.tokenizer_path,max_http_seconds=args.content_max_http_seconds)
    if not manifest_path.exists():
        urls=[s.strip() for s in (ROOT/'tests/stage1/test_inputs.txt').read_text().splitlines() if s.strip()]
        if len(urls)!=10 or len(set(urls))!=10:raise ValueError('expected_ten_unique_inputs')
        manifest={'schema_version':'extractube.e2e.v1','created_at':datetime.now(timezone.utc).isoformat(),
                  'urls':urls,'tokenizer':counter.identity,'settings':{'content':asdict(content_cfg),
                  'vision':asdict(vision_cfg)},'source_code':{p.name:store.sha(p) for p in (ROOT/'src/extractube').glob('*.py')},
                  'protected_stage1':{},'stage1_reuse':'validated copies of previous artifacts; pipeline verifies every stage',
                  'model_concurrency':1,'automatic_retries':False}
        for url in urls:
            video=url.split('v=')[1];original=ROOT/'data'/video;target=directory/'data'/video
            meta=store.read(original/'metadata.json')
            refs={r['path']:r['sha256'] for r in meta['results'].values()}
            refs['metadata.json']=store.sha(original/'metadata.json')
            manifest['protected_stage1'][video]=refs
            for relative,expected in refs.items():
                source=store.safe(original,relative);dest=store.safe(target,relative)
                if store.sha(source)!=expected:raise ValueError('seed_hash:'+video+':'+relative)
                dest.parent.mkdir(parents=True,exist_ok=True)
                if relative=='metadata.json':shutil.copy2(source,dest)
                else:
                    try:os.link(source,dest)
                    except OSError:shutil.copy2(source,dest)
        store.write(manifest_path,manifest,immutable=True)
        store.write(directory/'summary.json',{'status':'prepared','videos':[]})
    manifest=store.read(manifest_path)
    current_code={p.name:store.sha(p) for p in (ROOT/'src/extractube').glob('*.py')}
    if manifest['source_code']!=current_code:
        raise ValueError('code_changed_create_a_new_campaign')
    print('campaign='+str(directory),flush=True)
    if args.prepare_only:return
    import socket
    with socket.create_connection(('127.0.0.1',8095),timeout=5):pass
    summary=store.read(directory/'summary.json');summary['status']='running';store.write(directory/'summary.json',summary)
    if args.retry_incomplete:
        summary['videos']=[r for r in summary['videos'] if r.get('status') in ('generated','failed')]
        store.write(directory/'summary.json',summary)
    done={r['video_id'] for r in summary['videos']}
    for url in manifest['urls']:
        video=url.split('v=')[1]
        if video in done:continue
        print('START '+video,flush=True);started=time.monotonic()
        work=directory/'data'/video;meta=store.read(work/'metadata.json')
        # Retain the accepted download selection when validating an existing source.
        settings=Settings(data_root=directory/'data',yt_dlp_format=meta['results']['video']['settings']['format'])
        row={'video_id':video,'url':url,'started_at':datetime.now(timezone.utc).isoformat()}
        with (directory/f'{video}.log').open('a',encoding='utf-8',buffering=1) as log:
            try:
                with redirect_stdout(log),redirect_stderr(log):
                    from extractube import vision,infer
                    resumable_vision=None
                    for p in sorted((work/'vision/runs').glob('*/state.json')):
                        st=store.read(p)
                        infer.recover_calls(work,p.parent,st)
                        if st.get('execution_status') in ('pending','running','paused_budget','blocked_unknown'):
                            resumable_vision=p.parent.name
                    if resumable_vision:
                        unknown_ids=[]
                        for jp in sorted((work/'vision/runs').glob('*/jobs/*.json')):
                            for att in store.read(jp).get('attempts',[]):
                                rp=store.safe(work,att['path'])/'record.json'
                                if rp.is_file() and store.read(rp).get('status')=='unknown' and not att.get('retry_authorization'):
                                    unknown_ids.append(att['call_id'])
                        unapproved=set(unknown_ids)-set(args.retry_unknown)
                        if unapproved:
                            from extractube.errors import ContentError
                            raise ContentError('미확정 호출의 명시 재개가 필요합니다: '+','.join(sorted(unapproved)),kind='blocked_unknown')
                        saved_vcfg=vision.load_settings(video,settings.data_root,resumable_vision)
                        vision.run(video,saved_vcfg,data_root=settings.data_root,resume_run_id=resumable_vision,
                                   retry_unknown_call_ids=tuple(unknown_ids),
                                   resume_budget_limits={'max_http_seconds':max(saved_vcfg.max_http_seconds,args.vision_max_http_seconds)},
                                   log=lambda message:print(message,flush=True))
                        active_vision_cfg=saved_vcfg
                    else:
                        active_vision_cfg=vision_cfg
                    result=pipeline.run(url,settings,video_id=video,through='stage3',vision_settings=active_vision_cfg,
                        content_settings=content_cfg,log=lambda message:print(message,flush=True))
                    row['stage1']={k:{'reused':v.reused,'record':v.record} for k,v in result.stages.items()}
                    row['stage2']=asdict(result.vision) if result.vision else None
                    row['stage3']=asdict(result.content) if result.content else None
                    if result.content and result.content.html_path:
                        row['verification']=content.verify(video,data_root=settings.data_root,run_id=result.content.run_id)
                        again=content.run(video,content_cfg,data_root=settings.data_root,
                                          vision_run_id=result.vision.run_id,counter=counter,log=None)
                        row['reuse_check']={'same_run':again.run_id==result.content.run_id,'new_calls':again.inference_calls}
                    row['status']='generated' if result.content and result.content.html_path else 'incomplete'
            except Exception as exc:
                traceback.print_exc(file=log);row.update(status='failed',error={'type':type(exc).__name__,'kind':getattr(exc,'kind',None),'message':str(exc)})
            for stage in ('vision','content'):
                runs=work/stage/'runs'
                row[stage+'_runs']=[{'run_id':p.parent.name,**store.read(p)} for p in sorted(runs.glob('*/state.json'))]
            row['wall_seconds']=time.monotonic()-started
        row=json.loads(json.dumps(row,default=str,ensure_ascii=False))
        summary['videos'].append(row);store.write(directory/'summary.json',summary)
        print('END '+video+' '+row['status']+' '+str(round(row['wall_seconds'],1))+'s',flush=True)
    summary['protected_stage1_unchanged']=all(store.sha(ROOT/'data'/video/relative)==expected
        for video,refs in manifest['protected_stage1'].items() for relative,expected in refs.items())
    summary['status']='finished';summary['finished_at']=datetime.now(timezone.utc).isoformat()
    store.write(directory/'summary.json',summary)
    print('FINISHED '+str(directory),flush=True)


if __name__=='__main__':main()
