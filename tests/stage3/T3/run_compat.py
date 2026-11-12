"""Single gate compatibility correction; cumulative original journal remains authoritative."""
import copy,sys,json,time,hashlib,fcntl,argparse,subprocess
from datetime import datetime,timezone
import run_t3 as t

def strip_unique(v):
    if isinstance(v,dict):return {k:strip_unique(x) for k,x in v.items() if k!='uniqueItems'}
    if isinstance(v,list):return [strip_unique(x) for x in v]
    return v

def check(run):
    c=t.check_identity(run);reg=t.read(run/'compatibility_R1.json')
    for p,h in reg['frozen_files'].items():t.bound_sha(p,h)
    return c

def prepare():
    run=t.current();c=t.check_identity(run)
    if (run/'compatibility_R1.json').exists():check(run);print('R1_reused');return
    failed=t.read(run/'journal/p02.json')
    if failed['status']!='failed' or failed['http_status']!=400 or t.read(run/'responses/p02.body')['error']['message']!='Grammar error: Unimplemented keys: ["uniqueItems"]':raise ValueError('single_known_compatibility_cause_required')
    if len(t.old.records(run))!=1:raise ValueError('gate_only')
    jobs=copy.deepcopy(t.read(run/'plan.json'))
    for job in jobs:
        original=copy.deepcopy(job['body']);job['body']['response_format']['json_schema']['schema']=strip_unique(original['response_format']['json_schema']['schema'])
        reduced=copy.deepcopy(job['body']);reduced['response_format']=original['response_format']
        if reduced!=original:raise ValueError('unapproved_change')
        job['job_id']+='_R1';job['revision']='R1';job['job_key']=t.digest({'body':job['body'],'context':job['context'],'config':t.digest(c),'compatibility':'uniqueItems decoder only; strict local uniqueness retained'})
    # Same messages and template: CPU input count is unchanged. Confirm by recomputation.
    tp=t.old.token_plan(jobs)
    for k,v in c['token_identity'].items():
        if tp[k]!=v:raise ValueError('token_identity')
    schemas=[j['body']['response_format']['json_schema']['schema'] for j in jobs]
    script='''import json,sys,xgrammar,inspect
schemas=json.load(sys.stdin)
for s in schemas:xgrammar.Grammar.from_json_schema(json.dumps(s),strict_mode=True)
print(json.dumps({'schemas':len(schemas),'strict_mode':True,'passed':True,'weights_loaded':False}))'''
    r=subprocess.run(['docker','exec','-i',t.old.CONTAINER,'python3','-c',script],input=t.wire(schemas),capture_output=True)
    t.create(run/'schema_cpu_R1.json',{'returncode':r.returncode,'stdout':r.stdout.decode(),'stderr':r.stderr.decode()});r.check_returncode()
    t.create(run/'plan_R1.json',jobs);t.create(run/'token_plan_R1.json',tp)
    code=t.HERE/'run_compat.py';(run/'source_code/run_compat.py').write_bytes(code.read_bytes())
    files=[code,run/'source_code/run_compat.py',run/'plan_R1.json',run/'token_plan_R1.json',run/'schema_cpu_R1.json',run/'journal/p02.json',run/'responses/p02.body']
    t.create(run/'compatibility_R1.json',{'cause':'HTTP400 uniqueItems unsupported by live strict decoder; initial non-strict CPU check insufficient','changed':'remove uniqueItems from wire decoder schema only; local uniqueness remains mandatory','gate_corrections_used':1,'further_corrections_allowed':0,'prompt_changes':0,'family_max_calls':14,'family_max_http_seconds':4200,'original_failed_calls':1,'frozen_files':{str(p):t.sha(p) for p in files}})
    print('R1_prepared_strict_schema_passed')

def send(jobid):
    run=t.current();c=check(run);jobs=t.read(run/'plan_R1.json');job=next(j for j in jobs if j['job_id']==jobid);p=run/'journal'/(jobid+'.json')
    if p.exists():t.successful_cache(run,job,t.read(p));print('exact_cache:'+jobid);return
    if job['phase']=='confirm':
        t.accepted_review(run,'primary_review.json',[j for j in jobs if j['phase']=='primary'])
        f=t.read(run/'confirmation_freeze.json')
        for name,h in f['frozen_files'].items():t.bound_sha(run/name,h)
    elif jobid!='p02_R1':t.accepted_review(run,'gate_review.json',[j for j in jobs if j['job_id']=='p02_R1'])
    timeout=t.old.reserve(t.old.records(run),timeout=300,max_calls=14,max_seconds=4200)
    request=t.wire(job['body']);t.create(run/'requests'/(jobid+'.json'),job['body'])
    rec={k:job[k] for k in ['job_id','job_key','phase','video_id','revision']};rec.update(status='started',reserved_seconds=timeout,request_sha256=hashlib.sha256(request).hexdigest(),started_at=datetime.now(timezone.utc).isoformat())
    t.create(p,rec);start=time.monotonic()
    def headers(status,values):t.create(run/'responses'/(jobid+'.headers.json'),{'status':status,'headers':values})
    try:
        response=t.old.HttpTransport().send(c['endpoint'],request,timeout,4194304,headers);raw=response['body']
        dest=run/'responses'/(jobid+'.body');dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw)
        rec.update(http_status=response['status'],response_sha256=hashlib.sha256(raw).hexdigest(),response_complete=response['complete'])
        try:
            outer=t.loads(raw.decode());rec['usage']=outer.get('usage');rec['finish_reason']=outer.get('choices',[{}])[0].get('finish_reason')
        except (ValueError,UnicodeError,AttributeError,TypeError,IndexError):pass
        if not response['complete']:rec.update(status='unknown',error=response['error'] or 'incomplete_body')
        else:
            try:
                parsed,info=t.parse_response(raw,response['status'],job);rec.update(status='success',parsed=parsed,**info)
                if rec.get('usage',{}).get('prompt_tokens')!=job['token_plan']['input_tokens']:rec.update(status='failed',error='token_count_mismatch')
            except Exception as e:rec.update(status='failed',error=type(e).__name__+':'+str(e))
    except (OSError,TimeoutError,ValueError) as e:rec.update(status='unknown',error=type(e).__name__+':'+str(e))
    rec['http_seconds']=round(time.monotonic()-start,6);t.write(p,rec)
    print(json.dumps({k:rec.get(k) for k in ['job_id','status','http_seconds','error','usage']},ensure_ascii=False),flush=True)
    if rec['status']!='success':raise ValueError('stop_failed_or_unknown')

def freeze():
    run=t.current();check(run);jobs=[j for j in t.read(run/'plan_R1.json') if j['phase']=='primary'];t.accepted_review(run,'primary_review.json',jobs)
    names=['primary_review.json','compatibility_R1.json','plan_R1.json']
    t.create(run/'confirmation_freeze.json',{'frozen_files':{n:t.sha(run/n) for n in names},'corrections_used':1,'further_tuning':0});print('confirmation_frozen')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','send','freeze','verify']);ap.add_argument('--job');a=ap.parse_args()
    with (t.HERE/'campaign.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='prepare':prepare()
        elif a.action=='send':send(a.job)
        elif a.action=='freeze':freeze()
        else:
            run=t.current();check(run)
            for job in t.read(run/'plan_R1.json'):
                p=run/'journal'/(job['job_id']+'.json')
                if p.exists():t.successful_cache(run,job,t.read(p))
            print('all_identity_and_strict_raw_cache_verified; new_calls=0')
if __name__=='__main__':main()
