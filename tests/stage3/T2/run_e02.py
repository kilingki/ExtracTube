"""E02: one reviewed title patch plus one frozen confirmation. Old runs immutable."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,copy,fcntl,hashlib,json,time,uuid
import run_t2 as old
from contracts import parse_structure,parse_response,loads
wire,digest,sha,read,write,bound_sha=old.wire,old.digest,old.sha,old.read,old.write,old.bound_sha
HttpTransport=old.HttpTransport
ROOT,HERE=old.ROOT,old.HERE
PARENT=HERE/'runs/20261030T103557Z_5e794f3f'
OLD_TITLE='상품 정보 제공처 및 구비서류 처리'
NEW_TITLE='상품 정보 입력과 구비서류'
def patched_structure(original):
    result=copy.deepcopy(original)
    node=result['sections'][9]
    if (node['sentence_start'],node['sentence_end'],node['title'])!=(174,180,OLD_TITLE): raise ValueError('patch_target_changed')
    node['title']=NEW_TITLE
    parse_structure(result,[0,217])
    reverted=copy.deepcopy(result);reverted['sections'][9]['title']=OLD_TITLE
    if reverted!=original: raise ValueError('unexpected_patch')
    return result

def current(): return HERE/'runs'/read(HERE/'e02_campaign.json')['run_id']
def reserve(rows): return old.reserve(rows,timeout=300,max_calls=1,max_seconds=300)
def records(run): return old.records(run)
def check_identity(run):
    c=read(run/'config.json');state=read(run/'state.json')
    if digest(c)!=state['config_sha256']: raise ValueError('config_changed')
    for name,h in state['frozen_files'].items(): bound_sha(run/name,h)
    for p,h in c['critical_files'].items(): bound_sha(p,h)
    return c

def prepare():
    if (HERE/'e02_campaign.json').exists():check_identity(current());print('prepare_reused:'+str(current()));return
    previous=old.check_identity(PARENT)
    source=read(PARENT/'input.json');old.verify_input(source)
    original=read(PARENT/'journal/R1_dev.json')['parsed'];patched=patched_structure(original)
    basejob=next(j for j in read(PARENT/'revision_jobs.json') if j['job_id']=='R1_confirm')
    job={**basejob,'job_id':'E02_confirm','revision':'E02','phase':'confirm'}
    jobs=[job];tp=old.token_plan(jobs)
    if any(tp[k]!=previous['token_identity'][k] for k in previous['token_identity']): raise ValueError('runtime_tokenizer_changed')
    if job['body']!=basejob['body'] or job['video_id']!='7EJ-xotXiSE' or job['target']!=[152,192]: raise ValueError('confirmation_request_changed')
    method={'candidate':'E02','method':'R1 structure generation followed by source review and a neutral title patch when warranted','title_patch':{'node_id':'s9','old':OLD_TITLE,'new':NEW_TITLE,'range':[174,180],'evidence_indices':[175,178,179],'reviewer':'Codex','reason':'Broader topic grounded in information entry and conditional supporting documents without asserting suspect UI name.'},'review_rules':['no gap overlap or order changes','no source contradiction or loss of speaker conditions','uncertain terms may use source-supported broader titles; source is never corrected','critical missing content still blocks','style and final promotion editing are T3/T5 items'],'new_confirmation_prompt_revision':0,'authority':'user delegated the decision between further experiment and proceeding to T3','final_article_user_acceptance_delegated':False,'limits':{'max_calls':1,'max_http_seconds':300,'retry':0,'new_prompt_revisions':0},'generalization_limits':['human/Codex reviewed method, not standalone local model automation','one other-video40-sentence segment, not full-video or broad genre verification','no risk term case in confirmation means neutral-patch generalization remains untested']}
    critical=dict(previous['critical_files'])
    critical.update(read(PARENT/'protected_t1_files.json'))
    critical.update({str(p):sha(p) for p in PARENT.rglob('*') if p.is_file()})
    for name in ['run_e02.py','run_t2.py','contracts.py','token_plan.py']:critical[str(HERE/name)]=sha(HERE/name)
    c={'policy':'stage3.t2.e02.reviewed-title-patch.confirm.v1','parent_run':str(PARENT.relative_to(ROOT)),'parent_closeout_sha256':sha(PARENT/'closeout.json'),'endpoint':previous['endpoint'],'critical_files':critical,'token_identity':previous['token_identity'],'limits':method['limits'],'previous_t2_calls':2,'t2_family_max_calls':3,'method_sha256':digest(method),'reader_policy':'excluded'}
    job['job_key']=digest({'request':job['body'],'config':digest(c)})
    rid=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_e02_'+uuid.uuid4().hex[:6]
    run=HERE/'runs'/rid;run.mkdir(parents=True)
    (run/'input.json').write_bytes((PARENT/'input.json').read_bytes())
    write(run/'structure_E02.json',patched);write(run/'method.json',method);write(run/'plan.json',job);write(run/'token_plan.json',tp);write(run/'config.json',c)
    (run/'prompt_R1.txt').write_bytes((PARENT/'prompt_R1.txt').read_bytes())
    (run/'source_code').mkdir()
    for name in ['run_e02.py','run_t2.py','contracts.py','token_plan.py']:(run/'source_code'/name).write_bytes((HERE/name).read_bytes())
    write(run/'state.json',{'config_sha256':digest(c),'frozen_files':{name:sha(run/name) for name in ['input.json','structure_E02.json','method.json','plan.json','token_plan.json','prompt_R1.txt']}})
    write(HERE/'e02_campaign.json',{'run_id':rid})
    print(json.dumps({'prepared':rid,'input_tokens':job['token_plan']['input_tokens'],'protected_and_critical_files':len(critical),'title_only_patch':True},ensure_ascii=False))

def send_job(run,c,job):
    p=run/'journal'/(job['job_id']+'.json')
    if p.exists(): return successful_cache(run,job,read(p))
    check_identity(run);timeout=reserve(records(run))
    request=wire(job['body']);write(run/'requests'/(job['job_id']+'.json'),job['body'])
    rec={k:job[k] for k in ['job_id','job_key','phase','video_id','revision']}
    rec.update(status='started',reserved_seconds=timeout,request_sha256=hashlib.sha256(request).hexdigest(),started_at=datetime.now(timezone.utc).isoformat())
    write(p,rec);start=time.monotonic()
    def headers(status,values): write(run/'responses'/(job['job_id']+'.headers.json'),{'status':status,'headers':values})
    try:
        response=HttpTransport().send(c['endpoint'],request,timeout,4194304,headers)
        raw=response['body'];dest=run/'responses'/(job['job_id']+'.body');dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw)
        rec.update(http_status=response['status'],response_sha256=hashlib.sha256(raw).hexdigest(),response_complete=response['complete'])
        try:
            outer=loads(raw.decode());rec['usage']=outer.get('usage');rec['finish_reason']=outer.get('choices',[{}])[0].get('finish_reason')
        except (ValueError,UnicodeError,AttributeError,TypeError,IndexError): pass
        if not response['complete']: rec.update(status='unknown',error=response['error'] or 'incomplete_body')
        else:
            try:
                parsed,info=parse_response(raw,response['status'],job);rec.update(status='success',parsed=parsed,**info)
            except (ValueError,KeyError,TypeError,UnicodeError) as e: rec.update(status='failed',error=str(e))
    except (OSError,TimeoutError,ValueError) as e: rec.update(status='unknown',error=type(e).__name__+':'+str(e))
    rec['http_seconds']=round(time.monotonic()-start,6);write(p,rec)
    print(json.dumps({k:rec.get(k) for k in ['job_id','status','http_seconds','error','usage']},ensure_ascii=False),flush=True)
    return rec

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','execute']);a=ap.parse_args()
    with (HERE/'e02_campaign.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='prepare':prepare()
        else:
            run=current();c=check_identity(run)
            send_job(run,c,read(run/'plan.json'))
if __name__=='__main__':main()
