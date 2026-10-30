"""Stage 3 T2 bounded experiment; no product/service configuration changes."""
from __future__ import annotations
import argparse,hashlib,html,json,os,re,subprocess,sys,time,uuid,fcntl
from pathlib import Path
from datetime import datetime,timezone
from contracts import loads,exact,parse_structure,parse_response,nodes
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from extractube.infer import HttpTransport
INPUT=ROOT/'tests/stage3/T1/reviews/20261030_e00_readiness/t2_input.json'
INPUT_SHA='61c4d885d02c090841a07f4746a21f77f32bc0df85ef2442e4c8b26c1e950019'
CONTAINER='vllm-docker-config-vllm-runtime-1'
def wire(obj):return json.dumps(obj,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
def digest(obj):return hashlib.sha256(wire(obj)).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return loads(Path(p).read_text(encoding="utf-8"))
def write(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+".tmp")
    with tmp.open("wb") as f:f.write(wire(obj));f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)
def bound_sha(p,expected):
    got=sha(p)
    if got!=expected:raise ValueError("source_hash_changed:"+str(p))
    return got
def reserve(records,timeout=300,max_calls=3,max_seconds=900):
    started=[x for x in records if x["status"]!="not_sent"]
    seconds=sum(x.get("http_seconds",x.get("reserved_seconds",0)) for x in started)
    if any(x["status"] in ("started","unknown") for x in started):raise ValueError("blocked_unknown")
    if len(started)>=max_calls or seconds>=max_seconds:raise ValueError("paused_budget")
    return min(timeout,max_seconds-seconds)
def records(run,recover=True):
    result=[]
    for p in sorted((run/"journal").glob("*.json")):
        x=read(p)
        if recover and x["status"]=="started":
            x.update(status="unknown",error="interrupted_after_started")
            write(p,x)
        result.append(x)
    return result

def current():
    return HERE/'runs'/read(HERE/'campaign.json')['run_id']
def identity():
    return {p.name:sha(p) for p in [HERE/'run_t2.py',HERE/'contracts.py',HERE/'token_plan.py']}
def check_identity(run):
    c=read(run/'config.json');state=read(run/'state.json')
    if digest(c)!=state['config_sha256'] or identity()!=c['code']: raise ValueError('code_config_changed')
    for name,key in [('plan.json','plan_sha256'),('input.json','input_sha256')]: bound_sha(run/name,state[key])
    for p,h in c['critical_files'].items(): bound_sha(p,h)
    bound_sha(run/'prompt_P0.txt',c['prompt_sha256'])
    if (run/'revision.json').exists():
        rev=read(run/'revision.json')
        bound_sha(run/'prompt_R1.txt',rev['prompt_sha256'])
        if rev['config_sha256']!=digest(c) or rev['jobs_sha256']!=sha(run/'revision_jobs.json'): raise ValueError('revision_identity')
    return c

def verify_input(d):
    if d['schema_version']!='extractube.stage3.t2.input.experimental.v1' or d['reader_policy']!='excluded' or d['model_annotations_selected'] is not False or d['visual_evidence']!=[]: raise ValueError('input_policy')
    primary=d['primary_sentences'];confirm=d['confirmation']['sentences']
    for rows,vid,target in [(primary,d['primary_video_id'],[0,217]),(confirm,d['confirmation']['video_id'],[152,192])]:
        if [s['sentence_index'] for s in rows]!=list(range(*target)) or any(s['video_id']!=vid for s in rows): raise ValueError('source_indices')
        for s in rows:
            ref=s['source_ref'];bound_sha(ROOT/ref['path'],ref['sha256'])
            orig=read(ROOT/ref['path'])['chunks'][s['sentence_index']]
            if s['text']!=orig['text'] or s['source_chunk_index']!=orig['index']: raise ValueError('source_text')
    if len(primary)!=217 or sum(len(s['text']) for s in primary)!=7363 or len(d['primary_units'])!=20 or len(d['primary_assets'])!=1: raise ValueError('input_counts')
    cursor=0
    for u in d['primary_units']:
        if u['sentence_start']!=cursor or not cursor<u['sentence_end']<=217: raise ValueError('unit_partition')
        cursor=u['sentence_end']
    if cursor!=217: raise ValueError('unit_end')

def critical_files(d):
    paths={str(INPUT):INPUT_SHA}
    sys.path.insert(0,str(ROOT/'tests/stage2'))
    from experiment_paths import resolve_path
    def visit(v):
        if isinstance(v,dict):
            path=v.get('canonical_path') or v.get('path')
            h=v.get('sha256')
            if path and h:
                p=Path(path)
                if not p.is_absolute(): p=ROOT/p
                if not p.exists(): p=resolve_path(p)
                bound_sha(p,h);paths[str(p)]=h
            for x in v.values(): visit(x)
        elif isinstance(v,list):
            for x in v: visit(x)
    visit(d)
    profile=ROOT.parent/'vllm-docker-config/configs/models/qwen3.8-27b.env'
    for p in [profile,profile.parents[1]/'common.env',ROOT/'src/extractube/infer.py',ROOT/'tests/stage2/experiment_paths.py']:
        paths[str(p)]=sha(p)
    model=ROOT.parent/'models/llm/tf/qwen3.8-27b'
    for name in ['config.json','tokenizer_config.json','tokenizer.json','vocab.json','preprocessor_config.json','chat_template.jinja','chat_template_safe_v2.jinja']:
        p=model/name
        if p.exists(): paths[str(p)]=sha(p)
    return paths

def build_jobs(d,prompt,revision='P0'):
    result=[]
    for phase,vid,rows,target,units in [('dev',d['primary_video_id'],d['primary_sentences'],[0,217],d['primary_units']),('confirm',d['confirmation']['video_id'],d['confirmation']['sentences'],[152,192],[])]:
        values={'video_id':vid,'target_range_json':wire(target).decode(),'sentences_json':wire([{'sentence_index':s['sentence_index'],'text':s['text']} for s in rows]).decode(),'existing_units_json':wire([{k:u[k] for k in ['sentence_start','sentence_end']} for u in units]).decode(),'cautions_json':wire([{k:x[k] for k in ['sentence_index','kind']} for x in d['source_cautions'] if x['video_id']==vid and target[0]<=x['sentence_index']<target[1]]).decode()}
        rendered=re.sub(r'\{([a-z_]+)\}',lambda m:values[m[1]],prompt)
        body={'model':'qwen3.8-27b','messages':[{'role':'user','content':rendered}],'temperature':0,'max_tokens':4096,'stream':False,'chat_template_kwargs':{'enable_thinking':False}}
        result.append({'job_id':revision+'_'+phase,'revision':revision,'phase':phase,'video_id':vid,'target':target,'body':body})
    return result

def token_plan(jobs):
    r=subprocess.run(['docker','exec','-i',CONTAINER,'python3','-c',(HERE/'token_plan.py').read_text()],input=wire({'jobs':jobs}),capture_output=True,timeout=180)
    if r.returncode: raise RuntimeError('token_plan_failed:'+r.stderr.decode(errors='replace')[-2000:])
    plan=loads(r.stdout.decode())
    for j,t in zip(jobs,plan['jobs']):
        if t['job_id']!=j['job_id'] or t['input_tokens']>12000 or t['input_tokens']+4096+1024>32768 or len(wire(j['body']))>33554432: raise ValueError('token_budget')
        j['token_plan']=t
    return plan

def prepare():
    if (HERE/'campaign.json').exists(): check_identity(current());print('prepare_reused:'+str(current()));return
    bound_sha(INPUT,INPUT_SHA);d=read(INPUT);verify_input(d);critical=critical_files(d)
    spec=ROOT/'local/stage3/t2_execution.md'
    prompt=re.search(r'## 8\..*?```text\n(.*?)\n```',spec.read_text(),re.S).group(1)
    jobs=build_jobs(d,prompt);tp=token_plan(jobs)
    c={'policy':'stage3.t2.structure.v1','code':identity(),'critical_files':critical,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'endpoint':'http://127.0.0.1:8095/infer?model=qwen-vlm','limits':{'max_calls':3,'max_http_seconds':900,'timeout':300},'retry':0,'max_prompt_revisions':1,'spec_sha256':sha(spec),'token_identity':{k:tp[k] for k in ['transformers','processor','template_sha256','runtime_environment']}}
    for j in jobs: j['job_key']=digest({'body':j['body'],'config':digest(c)})
    rid=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    run=HERE/'runs'/rid;run.mkdir(parents=True)
    write(run/'input.json',d);write(run/'config.json',c);write(run/'plan.json',jobs);write(run/'token_plan.json',tp)
    (run/'prompt_P0.txt').write_text(prompt,encoding='utf-8')
    (run/'source_code').mkdir()
    for name in c['code']: (run/'source_code'/name).write_bytes((HERE/name).read_bytes())
    old=ROOT/'tests/stage3/T1/runs/20261030T070419Z_95b43c03'
    write(run/'protected_t1_files.json',{str(p):sha(p) for p in old.rglob('*') if p.is_file()})
    write(run/'state.json',{'config_sha256':digest(c),'plan_sha256':sha(run/'plan.json'),'input_sha256':sha(run/'input.json')})
    write(HERE/'campaign.json',{'run_id':rid})
    print(json.dumps({'prepared':rid,'tokens':tp['jobs'],'critical_files':len(critical)},ensure_ascii=False))

def successful_cache(run,job,rec):
    if rec['job_key']!=job['job_key'] or rec['status']!='success': raise ValueError('terminal_job_not_resendable')
    raw=(run/'responses'/(job['job_id']+'.body')).read_bytes()
    parsed,info=parse_response(raw,rec['http_status'],job)
    if hashlib.sha256(raw).hexdigest()!=rec['response_sha256'] or parsed!=rec['parsed']: raise ValueError('cache_corrupted')
    return rec

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

def all_jobs(run):
    return read(run/'plan.json')+(read(run/'revision_jobs.json') if (run/'revision_jobs.json').exists() else [])

def execute(phase):
    run=current();c=check_identity(run)
    if any(x['status']=='unknown' for x in records(run)): raise ValueError('blocked_unknown')
    if phase=='confirm':
        freeze=read(run/'freeze.json')
        if freeze['config_sha256']!=digest(c): raise ValueError('freeze_config')
        bound_sha(run/'dev_review.json',freeze['review_sha256'])
        bound_sha(run/'journal'/(freeze['dev_job_id']+'.json'),freeze['journal_sha256'])
        jobid=freeze['revision']+'_confirm'
    else:
        if (run/'freeze.json').exists(): raise ValueError('development_frozen')
        jobid=('R1' if (run/'revision.json').exists() else 'P0')+'_dev'
    job=next(j for j in all_jobs(run) if j['job_id']==jobid)
    send_job(run,c,job)

def freeze(review_file):
    run=current();c=check_identity(run);r=read(review_file)
    job=next(j for j in all_jobs(run) if j['job_id']==r['job_id'] and j['phase']=='dev')
    rec=read(run/'journal'/(job['job_id']+'.json'))
    successful_cache(run,job,rec)
    expected=[name for name,n in nodes(rec['parsed'])]
    if r.get('config_sha256')!=digest(c) or r.get('response_sha256')!=rec['response_sha256'] or r.get('reviewed_sentence_indices')!=list(range(217)) or r.get('decision')!='accepted' or not r.get('reviewer') or [x['node_id'] for x in r.get('nodes',[])]!=expected or any(x.get('decision')!='accepted' or not x.get('reason') or not x.get('evidence_indices') for x in r['nodes']): raise ValueError('incomplete_source_review')
    if (run/'freeze.json').exists(): raise ValueError('already_frozen')
    write(run/'dev_review.json',r)
    write(run/'freeze.json',{'config_sha256':digest(c),'review_sha256':sha(run/'dev_review.json'),'journal_sha256':sha(run/'journal'/(job['job_id']+'.json')),'dev_job_id':job['job_id'],'revision':job['revision']})
    print('frozen:'+job['job_id'])

def revise(prompt_file,reason):
    run=current();c=check_identity(run)
    if (run/'revision.json').exists() or (run/'freeze.json').exists(): raise ValueError('revision_limit')
    rec=read(run/'journal/P0_dev.json')
    if rec['status']=='unknown' or rec.get('http_status')!=200: raise ValueError('infrastructure_not_revisable')
    if not reason.strip(): raise ValueError('revision_reason')
    if rec['status']=='success':
        review=read(run/'p0_review.json')
        if review.get('response_sha256')!=rec['response_sha256'] or review.get('decision')!='rejected': raise ValueError('review_required')
    prompt=Path(prompt_file).read_text(encoding='utf-8')
    if hashlib.sha256(prompt.encode()).hexdigest()==c['prompt_sha256']: raise ValueError('same_prompt')
    jobs=build_jobs(read(run/'input.json'),prompt,'R1');tp=token_plan(jobs)
    if any(tp[k]!=c['token_identity'][k] for k in c['token_identity']): raise ValueError('tokenizer_changed')
    for j in jobs: j['job_key']=digest({'body':j['body'],'config':digest(c),'revision':1})
    (run/'prompt_R1.txt').write_text(prompt,encoding='utf-8');write(run/'revision_jobs.json',jobs);write(run/'revision_token_plan.json',tp)
    write(run/'revision.json',{'config_sha256':digest(c),'prompt_sha256':sha(run/'prompt_R1.txt'),'jobs_sha256':sha(run/'revision_jobs.json'),'reason':reason,'revision':1})
    print('revision_prepared')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['check','prepare','execute','freeze','revise']);ap.add_argument('--phase',choices=['dev','confirm']);ap.add_argument('--review-file');ap.add_argument('--prompt-file');ap.add_argument('--reason');a=ap.parse_args()
    if a.action=='check':
        import unittest
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(HERE),pattern='test_t2.py'));sys.exit(not result.wasSuccessful())
    HERE.mkdir(exist_ok=True)
    with (HERE/'campaign.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='prepare': prepare()
        elif a.action=='execute':
            if not a.phase: raise ValueError('phase_required')
            execute(a.phase)
        elif a.action=='freeze': freeze(a.review_file)
        elif a.action=='revise': revise(a.prompt_file,a.reason or '')
if __name__=='__main__': main()
