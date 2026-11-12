"""T4 E00 baseline: prepare, one-request send, reviewed gates, render and closeout."""
from pathlib import Path
from datetime import datetime, timezone
import sys, json, re, hashlib, time, uuid, argparse, fcntl, html, copy
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(HERE.parent/'T3'))
import run_t3 as prior
old=prior.old
wire,digest,sha,read,write,bound_sha=old.wire,old.digest,old.sha,old.read,old.write,old.bound_sha
INPUT=HERE.parent/'T3/runs/20261030T194941Z_e05_eb6a0f/t4_input.json'
INPUT_SHA='164af43c9ea09dcdd673a8a4e931a4f13a55cb8808bd15dcda2007dc1b0aa6f3'
SPEC=ROOT/'local/stage3/t4_execution.md'
ORDER=['p_u09','p_u10','p_u05','p_u07','p_u14']
GATES={'G1':ORDER[:2],'G2':ORDER[2:]}
def current():return HERE/'runs'/read(HERE/'campaign.json')['run_id']
def create(p,x):
    if Path(p).exists():raise ValueError('immutable_exists:'+str(p))
    write(p,x)
def schema(unit):
    ids=[s['sentence_index'] for s in unit['required_source_sentences']]
    def obj(p):return {'type':'object','properties':p,'required':list(p),'additionalProperties':False}
    def arr(s,n,minimum=0):return {'type':'array','items':s,'maxItems':n,'minItems':minimum}
    idx={**arr({'type':'integer','enum':ids},len(ids),1),'uniqueItems':True}
    text={'type':'string','minLength':1,'maxLength':6000}
    note=obj({'source_indices':idx,'reason':{'type':'string','minLength':1,'maxLength':1000}})
    return obj({'unit_id':{'type':'string','enum':[unit['unit_id']]},
        'paragraphs':arr(obj({'text':text,'source_indices':idx}),24,1),
        'editorial_candidates':arr(note,32),'unresolved':arr(note,32)})
def validate(value,unit):
    prior.validate_schema(value,schema(unit))
    ids={s['sentence_index'] for s in unit['required_source_sentences']}
    seen=set()
    for key in ['paragraphs','editorial_candidates','unresolved']:
        for p in value[key]:
            if p['source_indices']!=sorted(set(p['source_indices'])):raise ValueError('index_order')
            seen.update(p['source_indices'])
            text=p.get('text',p.get('reason',''))
            if not text.strip() or re.search(r'<[/!a-zA-Z]|'+chr(96)*3,text):raise ValueError('non_plain_text')
    if seen!=ids:raise ValueError('source_coverage')
    return value
def parse(raw,status,unit):
    if status!=200:raise ValueError('http_error:'+str(status))
    o=prior.loads(raw.decode())
    if len(o.get('choices',[]))!=1:raise ValueError('choices')
    c=o['choices'][0]
    if c.get('finish_reason')!='stop':raise ValueError('finish_reason:'+str(c.get('finish_reason')))
    m=c['message']
    if m.get('reasoning') or m.get('reasoning_content'):raise ValueError('unexpected_reasoning')
    return validate(prior.loads(m['content']),unit),o
def verify_units(units,video,target):
    if [s['sentence_index'] for u in units for s in u['required_source_sentences']]!=list(range(*target)):raise ValueError('partition')
    for u in units:
        if u['video_id']!=video or u['assets']['asset_action'] not in ('skip','defer') or u['model_fact_restatement'] or u['new_visual_evidence']:raise ValueError('unit_policy')
        ids=[s['sentence_index'] for s in u['required_source_sentences']]
        if ids!=list(range(u['source_range']['sentence_start'],u['source_range']['sentence_end'])):raise ValueError('unit_range')
        for s in u['required_source_sentences']+u['previous_context']['sentences']:
            ref=s.get('source_ref',u['required_source_sentences'][0]['source_ref']);bound_sha(ROOT/ref['path'],ref['sha256'])
            orig=read(ROOT/ref['path'])['chunks'][s['sentence_index']]
            if s['text']!=orig['text']:raise ValueError('source_text')
        if any(i>=min(ids) for i in u['previous_context']['previous_context_indices']):raise ValueError('future_context')
def runtime_schema(s):
    s=copy.deepcopy(s)
    def walk(v):
        if isinstance(v,dict):
            v.pop('uniqueItems',None)
            for x in v.values():walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(s)
    return s
def prepare():
    prior_records=[];prior_run=None
    if (HERE/'campaign.json').exists():
        prior_run=current()
        close=read(prior_run/'closeout.json')
        rr=old.records(prior_run)
        raw=(prior_run/'responses/p_u09.body').read_text()
        if close['calls']!=1 or len(rr)!=1 or rr[0].get('http_status')!=400 or 'Unimplemented keys' not in raw or 'uniqueItems' not in raw:
            raise ValueError('existing_campaign_do_not_reset')
        prior_records=rr
    bound_sha(INPUT,INPUT_SHA);d=read(INPUT)
    if not all(d[k] is True for k in ['stage_gate_accepted','T3_complete','t4_ready']) or d['reader_policy']!='excluded':raise ValueError('input_gate')
    confref=d['validation_only']['resolved_confirmation_dossiers_ref'];bound_sha(ROOT/confref['path'],confref['sha256'])
    conf=read(ROOT/confref['path'])
    primary=d['generation_units'];confirm=conf['units']
    verify_units(primary,d['primary_video_id'],[0,217]);verify_units(confirm,conf['video_id'],[134,192])
    critical=old.critical_files(d)
    # Preserve all prior evidence plus product code, without hashing large working media.
    for base in [HERE.parent/'T1',HERE.parent/'T2',HERE.parent/'T3',ROOT/'src/extractube']:
        for p in base.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:critical[str(p)]=sha(p)
    for p in [HERE/'run_e00.py',HERE/'test_e00.py']:critical[str(p)]=sha(p)
    prompt=re.search(r'```text\n(.*?)\n```',SPEC.read_text(),re.S).group(1)
    jobs=[]
    for phase,units,outline in [('primary',primary,d['document_outline']),('confirmation',confirm,conf['document_outline'])]:
        for unit in units:
            # Preserve dossier fields; full storage hashes remain outside the model request.
            ctx=copy.deepcopy(unit)
            ctx['required_source_sentences']=[{'sentence_index':s['sentence_index'],'text':s['text']} for s in unit['required_source_sentences']]
            body={'model':'qwen3.8-27b','messages':[{'role':'system','content':prompt},{'role':'user','content':wire({'outline':outline,'dossier':ctx}).decode()}],
                  'temperature':0,'max_tokens':4096,'stream':False,'chat_template_kwargs':{'enable_thinking':False},
                  'response_format':{'type':'json_schema','json_schema':{'name':'stage3_t4_unit','strict':True,'schema':runtime_schema(schema(unit))}}}
            if len(wire(body))>4194304:raise ValueError('request_bytes')
            jobs.append({'job_id':unit['unit_id'],'phase':phase,'unit':unit,'body':body})
    import subprocess
    grammar_code="import sys,json,xgrammar; jobs=json.load(sys.stdin); [xgrammar.Grammar.from_json_schema(j['body']['response_format']['json_schema']['schema']) for j in jobs]; print('all_runtime_grammars_valid')"
    check=subprocess.run(['docker','exec','-i',old.CONTAINER,'python3','-c',grammar_code],input=wire(jobs),capture_output=True,timeout=180)
    if check.returncode:raise ValueError('runtime_grammar_failed:'+check.stderr.decode()[-2000:])
    tp=old.token_plan(jobs)
    config={'candidate':'E00','policy':'T4.source_grounded_leaf_baseline.v1','endpoint':'http://127.0.0.1:8095/infer?model=qwen-vlm',
            'prior_records':prior_records,'prior_run':str(prior_run) if prior_run else None,'runtime_grammar_check':check.stdout.decode().strip(),'critical_files':critical,'limits':{'max_calls':27,'max_http_seconds':7200,'timeout':300,'baseline_calls':23,'retry':0,'e00_revisions':0},
            'token_identity':{k:tp[k] for k in ['transformers','processor','template_sha256','runtime_environment']},
            'authorization':'2026-11-02 user: E00 implementation, execution and intermediate review only',
            'future_candidates':'approximately five total intended by user; no future calls authorized in this run'}
    rid=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_e00_'+uuid.uuid4().hex[:6]
    run=HERE/'runs'/rid;run.mkdir(parents=True)
    for name,obj in [('input.json',d),('config.json',config),('plan.json',jobs),('token_plan.json',tp)]:create(run/name,obj)
    (run/'spec_snapshot.md').write_bytes(SPEC.read_bytes())
    (run/'source_code').mkdir()
    for name in ['run_e00.py','test_e00.py']:(run/'source_code'/name).write_bytes((HERE/name).read_bytes())
    create(run/'state.json',{'frozen':{n:sha(run/n) for n in ['config.json','plan.json','token_plan.json','input.json','spec_snapshot.md']}})
    write(HERE/'campaign.json',{'run_id':rid})
    print(json.dumps({'run':str(run),'jobs':len(jobs),'protected_files':len(critical),'tokens':tp['jobs']},ensure_ascii=False))
def identity(run):
    for n,h in read(run/'state.json')['frozen'].items():bound_sha(run/n,h)
    c=read(run/'config.json')
    for p,h in c['critical_files'].items():bound_sha(p,h)
    return c
def review_ok(run,gate,ids):
    r=read(run/'reviews'/(gate+'.json'))
    if r['decision']!='accepted' or r['reviewer']!='Codex source comparison' or r['job_ids']!=ids or r['config_sha256']!=sha(run/'config.json'):raise ValueError('gate_review')
    for jid in ids:
        j=next(j for j in read(run/'plan.json') if j['job_id']==jid)
        rec=read(run/'journal'/(jid+'.json'))
        bound_sha(run/'responses'/(jid+'.body'),rec['response_sha256'])
        row=next(x for x in r['jobs'] if x['job_id']==jid)
        if row['response_sha256']!=rec['response_sha256'] or row['reviewed_sentence_indices']!=[s['sentence_index'] for s in j['unit']['required_source_sentences']] or not row['findings']:raise ValueError('incomplete_review')
        if rec['status']!='success':raise ValueError('failed_job_review')
    return r
def send(jid):
    run=current();c=identity(run);jobs=read(run/'plan.json');job=next(j for j in jobs if j['job_id']==jid)
    p=run/'journal'/(jid+'.json')
    if p.exists():raise ValueError('already_sent_no_resend')
    records=old.records(run)
    if any(r['status']!='success' for r in records):raise ValueError('prior_technical_failure')
    primary=[j['job_id'] for j in jobs if j['phase']=='primary']
    if jid not in GATES['G1']:review_ok(run,'G1',GATES['G1'])
    if jid not in ORDER:review_ok(run,'G2',GATES['G2'])
    if job['phase']=='confirmation':
        review_ok(run,'primary',primary)
        bound_sha(run/'reviews/primary.json',read(run/'confirmation_freeze.json')['primary_review_sha256'])
    timeout=old.reserve(c['prior_records']+records,300,27,7200)
    request=wire(job['body']);create(run/'requests'/(jid+'.json'),job['body'])
    rec={'job_id':jid,'status':'started','reserved_seconds':timeout,'request_sha256':hashlib.sha256(request).hexdigest(),'started_at':datetime.now(timezone.utc).isoformat()}
    create(p,rec);start=time.monotonic()
    try:
        def headers(status,values):create(run/'responses'/(jid+'.headers.json'),{'status':status,'headers':values})
        resp=old.HttpTransport().send(c['endpoint'],request,timeout,4194304,headers)
        raw=resp['body'];(run/'responses'/(jid+'.body')).write_bytes(raw)
        rec.update(http_status=resp['status'],response_sha256=hashlib.sha256(raw).hexdigest(),response_complete=resp['complete'])
        try:
            outer=prior.loads(raw.decode());rec.update(usage=outer.get('usage'),finish_reason=outer.get('choices',[{}])[0].get('finish_reason'))
        except Exception:pass
        if not resp['complete']:rec.update(status='unknown',error=resp['error'])
        else:
            try:
                parsed,outer=parse(raw,resp['status'],job['unit'])
                create(run/'parsed'/(jid+'.json'),parsed)
                observed=outer['usage']['prompt_tokens'];expected=job['token_plan']['input_tokens']
                if observed>12000 or observed+4096+1024>32768 or observed!=expected:raise ValueError('token_count_mismatch')
                rec.update(status='success')
            except Exception as exc:rec.update(status='failed',error=str(exc))
    except Exception as exc:rec.update(status='unknown',error=type(exc).__name__+':'+str(exc))
    rec['http_seconds']=round(time.monotonic()-start,6);write(p,rec)
    render(run)
    print(json.dumps(rec,ensure_ascii=False),flush=True)
def render(run):
    jobs=read(run/'plan.json');body=[];comparison=[]
    for job in jobs:
        jid=job['job_id'];path=run/'parsed'/(jid+'.json')
        if not path.exists():continue
        v=read(path);title=job['unit']['section_hierarchy']['leaf_title']
        body.append('<h2>'+html.escape(title)+'</h2>'+''.join('<p>'+html.escape(p['text'])+'</p>' for p in v['paragraphs']))
        sources='\n'.join(str(s['sentence_index'])+': '+s['text'] for s in job['unit']['required_source_sentences'])
        comparison.append('<h2>'+jid+' '+html.escape(title)+'</h2><pre>'+html.escape(sources)+'</pre><pre>'+html.escape(json.dumps(v,ensure_ascii=False,indent=2))+'</pre>')
    style='<meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>body{max-width:900px;margin:30px auto;padding:16px;line-height:1.8}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
    (run/'result.html').write_text(style+''.join(body))
    (run/'review.html').write_text(style+''.join(comparison))
def closeout():
    run=current();identity(run);records=old.records(run);reviews=[read(p) for p in (run/'reviews').glob('*.json')]
    complete=len(records)==23 and all(r['status']=='success' for r in records) and all(any(r.get('gate')==g and r['decision']=='accepted' for r in reviews) for g in ['G1','G2','primary','confirmation'])
    prior_records=read(run/'config.json').get('prior_records',[])
    records=prior_records+records
    result={'preflight_calls':len(prior_records),'candidate':'E00','decision':'accepted' if complete else 'revise_or_stop','T4_complete':complete,'t5_ready':complete,'user_accepted':False,
        'calls':len(records),'http_seconds':round(sum(r.get('http_seconds',0) for r in records),6),
        'usage':{k:sum((r.get('usage') or {}).get(k,0) for r in records) for k in ['prompt_tokens','completion_tokens','total_tokens']},
        'technical_failures':[{'job_id':r['job_id'],'error':r.get('error')} for r in records if r['status']!='success'],
        'review_files':[str(p.relative_to(run)) for p in (run/'reviews').glob('*.json')],
        'unexecuted_jobs':[j['job_id'] for j in read(run/'plan.json') if j['job_id'] not in {r['job_id'] for r in records}],
        'future_candidates_executed':False}
    create(run/'closeout.json',result);create(run/'acceptance.json',result)
    create(run/'result_manifest.json',{str(p.relative_to(run)):sha(p) for p in run.rglob('*') if p.is_file()})
    print(json.dumps(result,ensure_ascii=False))
def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','send','verify','render','closeout']);ap.add_argument('--job');a=ap.parse_args()
    with (HERE/'campaign.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='prepare':prepare()
        elif a.action=='send':send(a.job)
        elif a.action=='closeout':closeout()
        elif a.action=='render':render(current())
        else:identity(current());print('identity_verified_no_inference')
if __name__=='__main__':main()
