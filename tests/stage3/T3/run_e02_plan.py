"""T3 E02: one fixed example-guided prompt; cumulative E01+E02 budgets."""
import copy,json,time,hashlib,argparse,fcntl,subprocess,uuid,sys
from datetime import datetime,timezone
from pathlib import Path
import run_t3 as t
import run_compat as compat
HERE=t.HERE
PARENT=HERE/'runs/20261030T132835Z_e01_d3871b'
EXAMPLES='''
작성 방식: instruction은 해당 원문을 T4가 어떻게 보존할지 구체적으로 지시한다. 원문 내용을 새 정책 사실·평가·분류로 확장하는 설명을 만들지 마라. 영상 당시 화자의 설명·권고·전언과 현재 외부 검증 사실은 구분한다. 매 문장마다 화자는 말한다를 반복하라는 최종 문체 지시는 아니다.
불확실한 어휘·지시대상은 필요한 원문 표현과 불확실성을 유지하라. 근거가 없으면 가능한 해석들을 나열하여 채우지 마라. 특히 다음 문장에 나온 구체 방법을 앞 문장의 source index에 배정하지 마라. 조건의 일부만 골라 불가/가능 조건을 좁히지 마라.
아래는 실제 입력과 무관한 짧은 합성 예시다. items 작성법만 보여준다. 예시900~903은 실제 응답의 source_indices로 사용하지 마라.
예시 원문900: 이 설정을 바꾸면 더 빨라진다고 들었어요.
좋은 items 예시: [{"source_indices":[900],"instruction":"설정 변경의 효과를 화자가 들은 내용으로 소개한다. 검증된 성능 사실이나 개인 측정 결과로 바꾸지 않는다."}]
예시 원문901: 여기는 '가...나'라고 적힌 것 같네요.
예시 원문902: 다음에는 자동 도구를 사용해 보겠습니다.
좋은 items 예시: [{"source_indices":[901],"instruction":"불확실한 명칭은 원문 표현과 불확실성을 보존하고 임의 확정하지 않는다."},{"source_indices":[902],"instruction":"다음에 자동 도구를 사용해 보겠다는 계획을902에 배정한다.901에서 이 방법을 설명한 것으로 바꾸지 않는다."}]
예시 원문903: 이렇게 두 가지로 나눕니다.
좋은 instruction 예시: 앞 원문에 확인되는 구분만 연결하고 확인되지 않은 분류명이나 대안은 추가하지 않는다. 지시대상을 확정할 근거가 부족하면 원문의 두 가지라는 표현을 유지한다.
예시는 편집 방식을 보여주며 실제 원문의 세부 설명·조건·수치·예시를 줄이라는 지시가 아니다. 한 문장에 여러 정보를 분리·연결할 수 있으나 한 문장 한 item을 강제하지 않는다.
이제 다음 고정 실제 입력만 분석하고 실제 schema의 네 키로 응답하라.
'''
PROMPT=t.PROMPT.replace('{frozen_request_context_json}',EXAMPLES+'{frozen_request_context_json}')

def current():return HERE/'runs'/t.read(HERE/'e02_campaign.json')['run_id']
def reserve(rows):return t.old.reserve(rows,timeout=300,max_calls=15,max_seconds=4200)
def family_records(run):return t.old.records(PARENT)+t.old.records(run)
def check(run):
 compat.check(PARENT)
 state=t.read(run/'state.json')
 for p,h in state['frozen_files'].items():t.bound_sha(p,h)
 return t.read(run/'config.json')

def prepare():
 if (HERE/'e02_campaign.json').exists():check(current());print('E02_prepare_reused');return
 parent=compat.check(PARENT);oldjobs=t.read(PARENT/'plan_R1.json');jobs=copy.deepcopy(oldjobs)
 for j,old in zip(jobs,oldjobs):
  j['body']['messages']=[{'role':'user','content':PROMPT.replace('{frozen_request_context_json}',t.wire(j['context']).decode(),1)}]
  b=copy.deepcopy(j['body']);b['messages']=old['body']['messages']
  if b!=old['body']:raise ValueError('only_prompt_may_change')
  j['job_id']=j['job_id'].replace('_R1','');j['revision']='E02'
 tp=t.old.token_plan(jobs)
 for k,v in parent['token_identity'].items():
  if tp[k]!=v:raise ValueError('runtime_token_identity_changed')
 rid=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_e02_'+uuid.uuid4().hex[:6];run=HERE/'runs'/rid;run.mkdir(parents=True)
 critical={str(p):t.sha(p) for p in PARENT.rglob('*') if p.is_file() and p.suffix!='.lock'}
 for p in [HERE/'run_e02_plan.py',HERE/'test_e02.py']:critical[str(p)]=t.sha(p)
 c={'candidate':'E02','endpoint':parent['endpoint'],'parent_run':str(PARENT),'critical_files':critical,'parent_calls':2,'parent_http_seconds':47.767730,'family_max_calls':15,'family_max_http_seconds':4200,'new_max_calls':13,'request_timeout':300,'prompt_revisions_after_prepare':0,'schema_revisions':0,'retry':0,'manual_edits_total':6,'manual_edits_per_root':2,'authorization':'2026-10-31 user: E02 proceed and verify; if insufficient analyze and plan next','prompt_sha256':hashlib.sha256(PROMPT.encode()).hexdigest(),'token_identity':parent['token_identity'],'input_sha256':t.INPUT_SHA}
 for j in jobs:j['job_key']=t.digest({'body':j['body'],'context':j['context'],'config':t.digest(c)})
 for name,obj in [('config.json',c),('plan.json',jobs),('token_plan.json',tp)]:t.create(run/name,obj)
 for name in ['input.json','confirmation_sentences.json']:(run/name).write_bytes((PARENT/name).read_bytes())
 (run/'prompt_E02.txt').write_text(PROMPT,encoding='utf-8');(run/'spec_snapshot.md').write_bytes(t.SPEC.read_bytes());(run/'source_code').mkdir()
 for p in [HERE/'run_e02_plan.py',HERE/'test_e02.py',HERE/'run_t3.py',HERE/'run_compat.py']:(run/'source_code'/p.name).write_bytes(p.read_bytes())
 code='''import json,sys,xgrammar
schemas=json.load(sys.stdin)
for s in schemas:xgrammar.Grammar.from_json_schema(json.dumps(s),strict_mode=True)
print(json.dumps({'schemas':len(schemas),'strict_mode':True,'passed':True,'weights_loaded':False}))'''
 r=subprocess.run(['docker','exec','-i',t.old.CONTAINER,'python3','-c',code],input=t.wire([j['body']['response_format']['json_schema']['schema'] for j in jobs]),capture_output=True)
 t.create(run/'schema_cpu_check.json',{'returncode':r.returncode,'stdout':r.stdout.decode(),'stderr':r.stderr.decode()});r.check_returncode()
 r=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(HERE),'-p','test_*.py'],capture_output=True)
 t.create(run/'offline_check.json',{'returncode':r.returncode,'output':r.stdout.decode()+r.stderr.decode(),'inference_calls':0});r.check_returncode()
 files={str(p):t.sha(p) for p in run.rglob('*') if p.is_file()};files.update(critical)
 t.create(run/'state.json',{'frozen_files':files})
 t.create(HERE/'e02_campaign.json',{'run_id':rid,'family_parent':PARENT.name,'family_max_calls':15,'family_max_http_seconds':4200})
 print(json.dumps({'run_id':rid,'tokens':tp['jobs'],'protected_parent_files':len(critical),'offline':'passed'},ensure_ascii=False))

def accepted(run,jobid):
 c=check(run);j=next(j for j in t.read(run/'plan.json') if j['job_id']==jobid);r=t.read(run/'reviews'/(jobid+'.json'));rec=t.read(run/'journal'/(jobid+'.json'));t.successful_cache(run,j,rec)
 if r['decision']!='accepted' or r['response_sha256']!=rec['response_sha256'] or r['reviewed_sentence_indices']!=list(range(*j['target'])) or r['config_sha256']!=t.digest(c) or not r['findings']:raise ValueError('source_review_required')
 if r.get('selected_plan_ref'):t.bound_sha(run/r['selected_plan_ref'],r['selected_plan_sha256'])
 return r

def send(jobid):
 run=current();c=check(run);job=next(j for j in t.read(run/'plan.json') if j['job_id']==jobid);p=run/'journal'/(jobid+'.json')
 if (run/'closeout.json').exists():raise ValueError('closed_run')
 if p.exists():t.successful_cache(run,job,t.read(p));print('exact_cache:'+jobid);return
 if jobid!='p02':
  accepted(run,'p02')
  if jobid!='p07':accepted(run,'p07')
  if job['phase']=='confirm':
   for j in t.read(run/'plan.json'):
    if j['phase']=='primary':accepted(run,j['job_id'])
   freeze=t.read(run/'confirmation_freeze.json')
   for name,h in freeze['frozen_files'].items():t.bound_sha(run/name,h)
 timeout=reserve(family_records(run));request=t.wire(job['body']);t.create(run/'requests'/(jobid+'.json'),job['body'])
 rec={k:job[k] for k in ['job_id','job_key','phase','video_id','revision']};rec.update(status='started',reserved_seconds=timeout,request_sha256=hashlib.sha256(request).hexdigest(),started_at=datetime.now(timezone.utc).isoformat())
 t.create(p,rec);start=time.monotonic()
 def headers(status,values):t.create(run/'responses'/(jobid+'.headers.json'),{'status':status,'headers':values})
 try:
  response=t.old.HttpTransport().send(c['endpoint'],request,timeout,4194304,headers);raw=response['body'];dest=run/'responses'/(jobid+'.body');dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw)
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

def main():
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','send','verify']);ap.add_argument('--job');a=ap.parse_args()
 with (HERE/'campaign.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if a.action=='prepare':prepare()
  elif a.action=='send':send(a.job)
  else:
   run=current();check(run)
   for j in t.read(run/'plan.json'):
    p=run/'journal'/(j['job_id']+'.json')
    if p.exists():t.successful_cache(run,j,t.read(p))
   print('E01/E02 immutable identities and raw/cache verified; new_calls=0')
if __name__=='__main__':main()
