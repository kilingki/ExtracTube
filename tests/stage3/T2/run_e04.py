"""Bounded E03 local repair, fresh confirmation, optional single repair."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
import argparse,copy,fcntl,hashlib,json,re,time,uuid
import run_t2 as old
import run_e02 as e02
import run_e03 as base
from contracts import loads,parse_structure,parse_response,nodes
ROOT,HERE=old.ROOT,old.HERE
PARENT=HERE/'runs/20261030T112021Z_e03_f3d42b'
SCHEMA=HERE/'reviews/20261030_e03_decision/e04_development_schema.json'
wire,digest,sha,read,write,bound_sha=old.wire,old.digest,old.sha,old.read,old.write,old.bound_sha
HttpTransport=old.HttpTransport
successful_cache=old.successful_cache
CONFIRM_SHA='6d937a19ac2a48ca21050553a2e4e83609648d17dc3451bb0d279c25c14cec93'
def current():return HERE/'runs'/read(HERE/'e04_campaign.json')['run_id']
def reserve(rows):return old.reserve(rows,timeout=300,max_calls=3,max_seconds=900)
def records(run):return old.records(run)
def merge_patch(original,partial,target,whole):
    parse_structure(original,whole);parse_structure(partial,target)
    prefix=[n for n in original['sections'] if n['sentence_end']<=target[0]]
    suffix=[n for n in original['sections'] if n['sentence_start']>=target[1]]
    covered=[n for n in original['sections'] if target[0]<=n['sentence_start'] and n['sentence_end']<=target[1]]
    if not covered or covered[0]['sentence_start']!=target[0] or covered[-1]['sentence_end']!=target[1] or len(prefix)+len(covered)+len(suffix)!=len(original['sections']):raise ValueError('repair_window_not_root_aligned')
    result={'sections':copy.deepcopy(prefix+partial['sections']+suffix)}
    parse_structure(result,whole)
    if result['sections'][:len(prefix)]!=prefix or suffix and result['sections'][-len(suffix):]!=suffix:raise ValueError('outside_window_changed')
    return result

def check_identity(run):
    c=read(run/'config.json');state=read(run/'state.json')
    if digest(c)!=state['config_sha256']:raise ValueError('config_changed')
    for name,h in state['frozen_files'].items():bound_sha(run/name,h)
    for p,h in c['critical_files'].items():bound_sha(p,h)
    if (run/'repair_registration.json').exists():
        reg=read(run/'repair_registration.json')
        if reg['config_sha256']!=digest(c):raise ValueError('repair_config')
        bound_sha(run/'confirm_repair_job.json',reg['job_sha256']);bound_sha(run/'confirm_initial_review.json',reg['review_sha256'])
    return c

def prompt_render(prompt,values):return re.sub(r'\{([a-z_]+)\}',lambda m:values[m[1]],prompt)
def with_format(original,target):
    result=copy.deepcopy(original);schema=read(SCHEMA)
    root=schema['properties']['sections']['items'];child=root['properties']['children']['items']
    for props in [root['properties'],child['properties']]:
        props['sentence_start'].update(minimum=target[0],maximum=target[1]-1)
        props['sentence_end'].update(minimum=target[0]+1,maximum=target[1])
    result['response_format']={'type':'json_schema','json_schema':{'name':'stage3_t2_structure','schema':schema}}
    return result
def body(text,target):
    return with_format(base.body(text),target)
def repair_job(prompt,rows,original,target,issues,phase):
    selected={'sections':[n for n in original['sections'] if target[0]<=n['sentence_start'] and n['sentence_end']<=target[1]]}
    parse_structure(selected,target)
    values={'video_id':rows[0]['video_id'],'repair_range':wire(target).decode(),'sentences_json':wire([{'sentence_index':s['sentence_index'],'text':s['text']} for s in rows if target[0]<=s['sentence_index']<target[1]]).decode(),'structure_json':wire(selected).decode(),'issues_json':wire(issues).decode()}
    return {'job_id':phase+'_repair','phase':phase,'revision':'E03','video_id':rows[0]['video_id'],'target':target,'body':body(prompt_render(prompt,values),target)}
def freeze_job_tokens(jobs,c=None):
    tp=old.token_plan(jobs)
    if c and any(tp[k]!=c['token_identity'][k] for k in c['token_identity']):raise ValueError('runtime_tokenizer_changed')
    return tp

def prepare():
    if (HERE/'e04_campaign.json').exists():check_identity(current());print('prepare_reused:'+str(current()));return
    previous=base.check_identity(PARENT);inp=read(PARENT/'input.json')
    old.verify_input(inp['primary_input'])
    jobs=copy.deepcopy(read(PARENT/'plan.json'))
    for job in jobs:
        original=copy.deepcopy(job['body']);job['revision']='E04';job['body']=with_format(original,job['target'])
        reduced=copy.deepcopy(job['body']);del reduced['response_format']
        if reduced!=original:raise ValueError('more_than_format_changed')
    tp=freeze_job_tokens(jobs,previous)
    critical=dict(previous['critical_files']);critical.update({str(p):sha(p) for p in PARENT.rglob('*') if p.is_file()})
    for name in ['run_e04.py','run_e03.py','run_t2.py','run_e02.py','contracts.py','token_plan.py']:critical[str(HERE/name)]=sha(HERE/name)
    critical[str(SCHEMA)]=sha(SCHEMA)
    spec=ROOT/'local/stage3/t2_e04_candidate.md'
    policy={'candidate':'E04','parent_run':PARENT.name,'limits':{'max_calls':3,'max_http_seconds':900,'retry':0,'prompt_revisions':0},'previous_t2_calls':4,'family_max_calls':7,'confirmation_repairs_max':1,'decision_authority':'user_delegated_stage_transition','acceptance_policy':'source coverage/order/meaning must hold; small title or boundary issues may use reviewed derived patches','manual_patch_scope':'at most two adjacent boundary groups or three titles; no source deletion or rewritten source','E05_allowed_only_for_one_small_unresolved_cause':True,'final_article_user_acceptance':False}
    c={'policy':policy,'endpoint':previous['endpoint'],'critical_files':critical,'token_identity':previous['token_identity'],'spec_sha256':sha(spec),'changed_request_field_only':'response_format'}
    for job in jobs:job['job_key']=digest({'body':job['body'],'config':digest(c)})
    rid=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_e04_'+uuid.uuid4().hex[:6];run=HERE/'runs'/rid;run.mkdir(parents=True)
    (run/'input.json').write_bytes((PARENT/'input.json').read_bytes());write(run/'config.json',c);write(run/'plan.json',jobs);write(run/'token_plan.json',tp)
    for name in ['repair_prompt.txt','generation_prompt_R1.txt']:(run/name).write_bytes((PARENT/name).read_bytes())
    (run/'spec_snapshot.md').write_bytes(spec.read_bytes());(run/'source_code').mkdir()
    for name in ['run_e04.py','run_e03.py','run_t2.py','run_e02.py','contracts.py','token_plan.py']:(run/'source_code'/name).write_bytes((HERE/name).read_bytes())
    write(run/'state.json',{'config_sha256':digest(c),'frozen_files':{n:sha(run/n) for n in ['input.json','plan.json','token_plan.json','repair_prompt.txt','generation_prompt_R1.txt','spec_snapshot.md']}})
    write(HERE/'e04_campaign.json',{'run_id':rid});print(json.dumps({'prepared':rid,'tokens':tp['jobs'],'critical_files':len(critical),'only_response_format_changed':True},ensure_ascii=False))


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

def accepted_dev(run,c):
    r=read(run/'dev_review.json');rec=read(run/'journal/dev_repair.json')
    if r.get('decision')!='accepted' or r.get('response_sha256')!=rec.get('response_sha256') or r.get('config_sha256')!=digest(c) or r.get('reviewed_sentence_indices')!=list(range(152,192)) or not r.get('reviewer'):raise ValueError('dev_review_required')
    if r.get('selected_structure_ref'):
        bound_sha(run/r['selected_structure_ref'],r['selected_structure_sha256'])
    if (run/'dev_freeze.json').exists():bound_sha(run/'dev_review.json',read(run/'dev_freeze.json')['review_sha256'])
    else:write(run/'dev_freeze.json',{'review_sha256':sha(run/'dev_review.json'),'response_sha256':rec['response_sha256']})

def register_repair(review_file):
    run=current();c=check_identity(run);accepted_dev(run,c)
    if (run/'repair_registration.json').exists():raise ValueError('repair_limit')
    rec=read(run/'journal/confirm_generate.json');r=read(review_file)
    if rec['status']!='success' or r.get('decision')!='needs_repair' or r.get('response_sha256')!=rec['response_sha256'] or not r.get('issues') or r.get('reviewed_sentence_indices')!=list(range(134,192)):raise ValueError('source_review_required')
    inp=read(run/'input.json');target=r['repair_range'];merge_patch(rec['parsed'],{'sections':[n for n in rec['parsed']['sections'] if target[0]<=n['sentence_start'] and n['sentence_end']<=target[1]]},target,[134,192])
    job=repair_job((run/'repair_prompt.txt').read_text(),inp['fresh_sentences'],rec['parsed'],target,r['issues'],'confirm');tp=freeze_job_tokens([job],c);job['job_key']=digest({'body':job['body'],'config':digest(c)})
    write(run/'confirm_initial_review.json',r);write(run/'confirm_repair_job.json',job);write(run/'confirm_repair_token_plan.json',tp)
    write(run/'repair_registration.json',{'config_sha256':digest(c),'job_sha256':sha(run/'confirm_repair_job.json'),'review_sha256':sha(run/'confirm_initial_review.json')});print('confirm_repair_registered')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','execute','register-repair']);ap.add_argument('--phase',choices=['dev','confirm','confirm-repair']);ap.add_argument('--review-file');a=ap.parse_args()
    with (HERE/'e04_campaign.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='prepare':prepare();return
        if a.action=='register-repair':register_repair(a.review_file);return
        run=current();c=check_identity(run)
        if a.phase in ['confirm','confirm-repair']:accepted_dev(run,c)
        if a.phase=='confirm-repair':job=read(run/'confirm_repair_job.json')
        else:job=next(j for j in read(run/'plan.json') if j['job_id']==('dev_repair' if a.phase=='dev' else 'confirm_generate'))
        send_job(run,c,job)
if __name__=='__main__':main()
