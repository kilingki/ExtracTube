"""Visual processing after aligned sentences, with immutable runs and explicit resumption."""

import subprocess
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .settings import VisionSettings
from .visual_policy import STAGES, snapshot, envelope, warning
from .pipeline import VisionResult
from .errors import VisionError, InferencePaused, InferenceJobFailed
from .store import read, write, ref, safe, digest, HashCache, RUN, sha
from . import visual_input as inputs, semantic, candidates, screen_changes as changes, selection, assets, handoff, infer as inference
from extractube.media import validate_video_id
from extractube.store import WorkLock

COMPLETE=('complete','complete_with_warnings','empty')
RESUMABLE=('pending','running','paused_budget','blocked_unknown')

def now():return datetime.now(timezone.utc).isoformat()

class PolicyJobs(inference.Jobs):
    """Resume guard; the wire and parser stay owned by inference.Jobs."""
    def invoke(self,stage,task,content,spec,dependencies,images=(),retry=0):
        ctx=self.ctx
        body=inference.body_for(ctx.settings,content,task=='t2')
        key=digest({'task':task,'body_sha256':hashlib.sha256(inference.wire(body)).hexdigest(),'dependencies':dependencies,'projection':ctx.config['projections'][stage],'parser_policy':inference.PARSER_POLICY,'model':ctx.settings.model_profile()})
        journal_path=ctx.directory/f'jobs/{key}.json'
        if journal_path.is_file():
            journal=read(journal_path);failed=[]
            for attempt in journal['attempts']:
                cp=safe(ctx.work,attempt['path']);rp=cp/'record.json'
                if rp.is_file():
                    record=read(rp)
                    if record['status']=='failed':failed.append((cp,record))
                    elif record['status'] in ('succeeded','budget_exhausted'):
                        failed=[]
            if failed:
                cp,record=failed[-1]
                if not task.startswith('t1.') or record['http_status']!=200 or len(failed)>retry:
                    raise InferenceJobFailed(f'{task}의 확정 실패를 같은 run에서 보존합니다.',kind='inference',diagnostic=cp)
                retry-=len(failed)
        return super().invoke(stage,task,content,spec,dependencies,images=images,retry=retry)


class Context:
    def __init__(self,work,video_id,directory,settings,config,input_doc,state,plan,hashes,force,from_stage,log):
        self.work=work;self.video_id=video_id;self.directory=directory;self.run_id=directory.name;self.settings=settings;self.config=config;self.config_sha=sha(directory/'config.json');self.input=input_doc;self.state=state;self.plan=plan;self.hashes=hashes;self.force=force;self.from_stage=from_stage;self.log=log
        self.video_path=safe(work,input_doc['video_ref']['path']);self.critical_refs=[{k:r[k] for k in ('path','sha256')} for r in input_doc['stage1_upstream_records'].values()]

        self.invocation_calls=0;self.reused_jobs=0;self.trace=None;self.jobs=PolicyJobs(self)
        self.artifact_job_statuses={}
        for relative in handoff.FILES.values():
            existing=self.directory/relative
            if existing.is_file():self.collect_job_refs(read(existing))
    def collect_job_refs(self,value):
        if isinstance(value,dict):
            if set(('job_key','source_run_id','result'))<=set(value):
                key=value['job_key']
                entry=read(safe(self.work,value['result']['path']))
                self.artifact_job_statuses[key]=entry.get('status','succeeded')
                if key not in self.plan['job_ids']:self.plan['job_ids'].append(key)
            for child in value.values():self.collect_job_refs(child)
        elif isinstance(value,list):
            for child in value:self.collect_job_refs(child)
    def emit(self,message):
        if self.log:self.log(message)
    def save_state(self):
        statuses=dict(self.artifact_job_statuses)
        keys=set(self.plan['job_ids'])
        for journal_path in (self.directory/'jobs').glob('*.json') if (self.directory/'jobs').exists() else []:
            journal=read(journal_path);keys.add(journal['job_key'])
            if journal['attempts']:
                attempt=journal['attempts'][-1];record_path=safe(self.work,attempt['path'])/'record.json'
                status=read(record_path)['status'] if record_path.exists() else attempt['status']
                if status in ('succeeded','budget_exhausted','failed','unknown'):
                    statuses[journal['job_key']]=status
        counts={}
        for status in statuses.values():
            counts[status]=counts.get(status,0)+1
        self.state['terminal_job_counts']=counts
        self.plan['job_ids']=sorted(keys)
        write(self.directory/'plan.json',self.plan)
        self.state['updated_at']=now();write(self.directory/'state.json',self.state)
    def check_inputs(self):
        for r in self.critical_refs:self.hashes.check(self.work,r)
        from extractube.store import identity
        meta=read(self.work/'metadata.json')
        if any(identity(meta['results'][kind])!=saved for kind,saved in self.input['stage1_identities'].items()):
            raise VisionError('실행 중 Stage 1 identity가 변경되었습니다.',kind='input')
        for filename,expected in self.config['code_sha256'].items():
            if sha(Path(__file__).parent/filename)!=expected:raise VisionError('실행 중 제품 코드가 변경되었습니다.',kind='input')
    def allow_reuse(self,stage):return not self.force and (self.from_stage is None or STAGES.index(stage)<STAGES.index(self.from_stage))
    def require_reuse(self,stage):return self.from_stage is not None and STAGES.index(stage)<STAGES.index(self.from_stage)
    def folder(self,c):return f'u{int(c["unit_id"].rsplit(":u",1)[1]):06d}_c{c["chunk_index"]:06d}'
    def failure_ref(self,exc):
        return ref(self.work,exc.diagnostic/'record.json') if exc.diagnostic and (exc.diagnostic/'record.json').is_file() else None
    def stage_doc(self,key,payload,warnings=None,status=None,path=None):
        path=path or handoff.FILES[key]
        doc=envelope(self,key,payload,warnings,status)
        write(self.directory/path,doc,immutable=True)
        self.collect_job_refs(doc)
        return doc
    def update_plan(self,stage,count):
        self.plan['stage_counts'][stage]=count;write(self.directory/'plan.json',self.plan)
    def add_warnings(self,values):
        known={digest(v) for v in self.state['warnings']}
        for w in values:
            if digest(w) not in known:self.state['warnings'].append(w);known.add(digest(w))
        self.save_state()

def recover_and_authorize(work,retry_ids,requested_resume):
    unknown=[];states=[]
    runs=work/'vision/runs'
    if not runs.exists():return []
    for directory in sorted(runs.iterdir()):
        if not directory.is_dir() or not RUN.fullmatch(directory.name) or not (directory/'state.json').is_file():continue
        state=read(directory/'state.json')
        inference.recover_calls(work,directory,state)
        for jp in sorted((directory/'jobs').glob('*.json')) if (directory/'jobs').exists() else []:
            journal=read(jp)
            for attempt in journal.get('attempts',[]):
                record_path=safe(work,attempt['path'])/'record.json'
                if record_path.is_file() and read(record_path)['status']=='unknown' and not attempt.get('retry_authorization'):
                    unknown.append((directory,jp,journal,attempt))
        states.append((directory,state))
    available={attempt['call_id'] for _,_,_,attempt in unknown}
    if set(retry_ids)-available:raise VisionError('--retry-unknown에 현재 미확정 call ID만 지정할 수 있습니다.',kind='usage')
    if available-set(retry_ids):raise VisionError('미확정 호출이 남아 있습니다. --resume 및 명시 --retry-unknown이 필요합니다: '+','.join(sorted(available-set(retry_ids))),kind='blocked_unknown')
    authorizations=[]
    for directory,jp,journal,attempt in unknown:
        authorization={'call_id':attempt['call_id'],'source_run_id':directory.name,'source_call_path':attempt['path'],'authorized_at':now(),'action':'retry_same_job' if directory.name==requested_resume else 'supersede_for_new_run'}
        attempt['retry_authorization']=authorization
        # Another unknown in the same job must not be dropped by a stale local journal copy.
        current=read(jp)
        next(a for a in current['attempts'] if a['call_id']==attempt['call_id'])['retry_authorization']=authorization
        write(jp,current);authorizations.append(authorization)
    return authorizations

def _result(work,directory,invocation_calls=0,reused_jobs=0):
    r=read(directory/'result.json') if (directory/'result.json').is_file() else None
    s=read(directory/'state.json')
    usable=bool(r and r['stage3_usable'])
    return VisionResult(work.name,work,directory.name,s['execution_status'],safe(work,r['handoff']['path']) if usable else None,usable,s['warnings'],invocation_calls,reused_jobs)

def load_settings(video_id,data_root,run_id):
    work=Path(data_root)/validate_video_id(video_id)
    if not RUN.fullmatch(run_id):raise VisionError('run ID 형식 오류입니다.',kind='usage')
    return VisionSettings(**read(safe(work,f'vision/runs/{run_id}/config.json'))['settings'])

def run(video_id,settings,*,data_root=Path('data'),from_stage=None,force=False,resume_run_id=None,retry_unknown_call_ids=(),resume_budget_limits=None,expected_inputs=None,log=print):
    video_id=validate_video_id(video_id)
    if (force and from_stage) or (resume_run_id and (force or from_stage)) or from_stage not in (None,*STAGES) or (resume_budget_limits is not None and not resume_run_id):
        raise VisionError('force/from/resume/예산 인자의 조합이 잘못되었습니다.',kind='usage')
    if resume_run_id and not RUN.fullmatch(resume_run_id):raise VisionError('run ID 형식 오류입니다.',kind='usage')
    if len(set(retry_unknown_call_ids))!=len(retry_unknown_call_ids):raise VisionError('중복 retry call ID입니다.',kind='usage')
    work=Path(data_root).resolve()/video_id
    if not work.is_dir():raise VisionError('완료한 Stage 1 작업 폴더가 없습니다.',kind='input')
    lock=WorkLock(work)
    try:
        hashes=HashCache()
        authorization=recover_and_authorize(work,retry_unknown_call_ids,resume_run_id)
        input_doc,sentence_payload,input_warnings=inputs.load(work,video_id,settings,hashes,expected_inputs)
        config=snapshot(settings)
        directory=None
        if resume_run_id:
            directory=safe(work,f'vision/runs/{resume_run_id}')
            saved_input=read(directory/'input.json');saved_config=read(directory/'config.json');state=read(directory/'state.json');plan=read(directory/'plan.json')
            if input_doc['input_digest']!=saved_input['input_digest'] or config!=saved_config:
                raise VisionError('재개 입력/설정/코드가 바뀌었습니다. 새 run이 필요합니다.',kind='input')
            if state['execution_status'] in COMPLETE:
                handoff.verify_locked(work,video_id,resume_run_id);return _result(work,directory)
            if state['execution_status'] not in RESUMABLE:raise VisionError('실패한 run은 --force/--from으로 새로 실행하십시오.',kind='usage')
            if resume_budget_limits:
                for key,value in resume_budget_limits.items():
                    target={'max_inference_calls':'max_calls','max_http_seconds':'max_http_seconds'}.get(key)
                    if target is None:raise VisionError('알 수 없는 재개 예산 필드입니다.',kind='usage')
                    from dataclasses import replace
                    replace(settings,**{key:value})
                    if value<state['budget'][target]:raise VisionError('재개 예산은 기존 한도 이상이어야 합니다.',kind='usage')
                    state['limit_changes'].append({'field':key,'before':state['budget'][target],'after':value,'at':now()});state['budget'][target]=value
            input_doc=saved_input
        else:
            runs=work/'vision/runs'
            for d in sorted(runs.iterdir()) if runs.exists() else []:
                if (d/'state.json').is_file():
                    st=read(d/'state.json')
                    if not force and from_stage is None and st['execution_status'] in RESUMABLE and (d/'input.json').is_file() and read(d/'input.json')['input_digest']==input_doc['input_digest'] and not authorization:
                        raise VisionError(f'미완결 run을 --resume {d.name}으로 재개하십시오.',kind='paused_budget')
            current_path=work/'vision/current.json'
            if not force and not from_stage and current_path.is_file():
                current=read(current_path);previous=safe(work,f'vision/runs/{current["run_id"]}')
                if current['input_digest']==input_doc['input_digest'] and read(previous/'config.json')==config:
                    handoff.verify_locked(work,video_id,current['run_id']);return _result(work,previous)
            run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:12]
            directory=safe(work,f'vision/runs/{run_id}');directory.mkdir(parents=True,exist_ok=False)
            input_doc={**input_doc,'run_id':run_id}
            write(directory/'input.json',input_doc,immutable=True);write(directory/'config.json',config,immutable=True)
            state={'execution_status':'pending','current_stage':'input','terminal_job_counts':{},'budget':{'max_calls':settings.max_inference_calls,'max_http_seconds':settings.max_http_seconds,'reserved_calls':0,'http_calls':0,'http_seconds':0},'blocked_call_ids':[],'unknown_authorizations':[],'limit_changes':[],'warnings':input_warnings,'updated_at':now()}
            plan={'input_digest':input_doc['input_digest'],'stage_counts':{s:None for s in STAGES},'job_ids':[],'budget_limits':{'max_inference_calls':settings.max_inference_calls,'max_http_seconds':settings.max_http_seconds},'execution_scope':'whole_video_selected_candidates','from_stage':from_stage,'force':force,'reused_source_run':None}
            write(directory/'state.json',state);write(directory/'plan.json',plan)
        state['unknown_authorizations'].extend(authorization)
        if resume_run_id:
            state['blocked_call_ids']=[v for v in state['blocked_call_ids'] if v not in retry_unknown_call_ids]
        ctx=Context(work,video_id,directory,settings,config,input_doc,state,plan,hashes,plan.get('force',force),plan.get('from_stage',from_stage),log)
        calls,elapsed,reserved=inference.budget_from_journals(directory);state['budget'].update(http_calls=calls,http_seconds=elapsed,reserved_calls=reserved)
        state['execution_status']='running';ctx.save_state()
        try:
            docs={};t3={}
            def existing(key):
                path=directory/handoff.FILES[key]
                return read(path) if path.is_file() else None
            docs['sentences']=existing('sentences') or ctx.stage_doc('sentences',sentence_payload,input_warnings)
            sentences=docs['sentences']['payload']['sentences']
            state['current_stage']='t1';ctx.save_state();ctx.emit('[t1] 실행/정확 일치 재사용')
            if existing('t1'):
                docs['t1']=existing('t1');docs['trace']=existing('trace')
            else:
                t1,trace=semantic.run(ctx,sentences);docs['trace']=ctx.stage_doc('trace',trace)
                t1['trace']=ref(work,directory/'t1/trace.json');docs['t1']=ctx.stage_doc('t1',t1)
            units=docs['t1']['payload']['units'];ctx.update_plan('t1',len(units))
            state['current_stage']='t2';ctx.save_state();ctx.emit('[t2] 실행/정확 일치 재사용')
            if existing('t2'):docs['t2']=existing('t2')
            else:
                t2,warnings=candidates.run(ctx,sentences,units);docs['t2']=ctx.stage_doc('t2',t2,warnings)
            ctx.add_warnings(docs['t2']['warnings']);cs=docs['t2']['payload']['candidates'];ctx.update_plan('t2',len(cs))
            state['current_stage']='t3';ctx.save_state();ctx.emit('[t3] B05 전체 후보 분석')
            for c in cs:
                if c['range_status']!='valid':continue
                path=f't3/{ctx.folder(c)}/screen_changes.json';target=directory/path
                key=digest({'video':input_doc['video_ref']['sha256'],'stream':input_doc['stream'],'range':c['effective_video_range'],'b05':changes.B05,'projection':config['projections']['t3']})
                if key not in ctx.plan['job_ids']:ctx.plan['job_ids'].append(key)
                pointer=work/f'vision/cache/jobs/{key}/result.json';payload=None;ww=[];status=None
                if target.is_file():doc=read(target)
                else:
                    if ctx.allow_reuse('t3') and pointer.is_file():
                        try:
                            entry=read(hashes.check(work,read(pointer)['entry']));source=read(hashes.check(work,entry['artifact']))
                            if source['execution_status']=='failed' or source['payload']['candidate_id']!=c['candidate_id'] or source['payload']['differences']!=changes.replay(source['payload']):raise ValueError()
                            payload=source['payload'];ww=source['warnings'];ctx.reused_jobs+=1
                        except (VisionError,ValueError,KeyError,TypeError):payload=None
                    if payload is None:
                        if ctx.require_reuse('t3'):raise VisionError('--from에 필요한 T3 캐시가 없습니다.',kind='upstream')
                        try:payload=changes.detect(ctx,c);ww=payload.pop('warnings')
                        except (changes.b.T3Error,subprocess.SubprocessError) as exc:
                            payload={'candidate_id':c['candidate_id'],'requested_range':c['requested_video_range'],'effective_range':c['effective_video_range'],'requests':[],'samples':[],'differences':[],'boundaries':[],'segments':[],'decode':{'error':str(exc)}};ww=[warning('t3_failed','t3',c['candidate_id'],str(exc))];status='failed'
                        except VisionError as exc:
                            if exc.kind not in ('media','image'):raise
                            payload={'candidate_id':c['candidate_id'],'requested_range':c['requested_video_range'],'effective_range':c['effective_video_range'],'requests':[],'samples':[],'differences':[],'boundaries':[],'segments':[],'decode':{'error':str(exc)}};ww=[warning('t3_failed','t3',c['candidate_id'],str(exc))];status='failed'
                    doc=ctx.stage_doc('t3',payload,ww,status,path)
                    if doc['execution_status']!='failed':
                        entry={'job_key':key,'source_run_id':ctx.run_id,'artifact':ref(work,target)};ep=work/f'vision/cache/jobs/{key}/entries/{digest(entry)[:32]}.json';write(ep,entry,immutable=True);write(pointer,{'entry':ref(work,ep)})
                ctx.artifact_job_statuses[key]='failed' if doc['execution_status']=='failed' else 'succeeded'
                t3[c['candidate_id']]=doc;ctx.add_warnings(doc['warnings'])
            ctx.update_plan('t3',sum(len(d['payload']['samples']) for d in t3.values()))
            ctx.update_plan('t4',sum((len(d['payload']['samples'])+3)//4 for d in t3.values() if d['execution_status']!='failed'))
            state['current_stage']='t4';ctx.save_state()
            if existing('frames') and existing('t4'):docs['frames']=existing('frames');docs['t4']=existing('t4')
            else:
                fd,t4,ww=selection.run(ctx,sentences,cs,t3);docs['frames']=ctx.stage_doc('frames',fd,ww);docs['t4']=ctx.stage_doc('t4',t4,ww)
            ctx.add_warnings(docs['t4']['warnings']);ctx.update_plan('t4',len(docs['t4']['payload']['batches']))
            ctx.update_plan('t5',sum((sum(a['candidate_id']==c['candidate_id'] and a['t4_status']=='success' and a['t4_decision'] in ('keep','uncertain') for a in docs['frames']['payload']['associations'])+3)//4 for c in cs))
            state['current_stage']='t5';ctx.save_state()
            if existing('t5'):docs['t5']=existing('t5')
            else:
                t5,ww=assets.run(ctx,sentences,units,cs,docs['frames']['payload'],docs['t2']['payload'],t3);docs['t5']=ctx.stage_doc('t5',t5,ww)
            ctx.add_warnings(docs['t5']['warnings']);ctx.update_plan('t5',len(docs['t5']['payload']['batches']))
            state['current_stage']='handoff';ctx.save_state();ctx.check_inputs()
            export=handoff.build(ctx,docs,t3);validation=handoff.validate(ctx,docs,t3,export)
            write(directory/'stage3_input.json',export,immutable=True);write(directory/'review.html',handoff.html_document(ctx,export),raw=True,immutable=True)
            write(directory/'validation.json',validation,immutable=True)
            state['execution_status']=export['execution_status'];state['current_stage']='complete'
            calls,elapsed,reserved=inference.budget_from_journals(directory);state['budget'].update(http_calls=calls,http_seconds=elapsed,reserved_calls=reserved)
            artifacts={key:ref(work,directory/p) for key,p in {**handoff.FILES,'input':'input.json','config':'config.json','html':'review.html','validation':'validation.json'}.items()}
            result={'execution_status':state['execution_status'],'stage3_usable':True,'handoff':ref(work,directory/'stage3_input.json'),'validation':ref(work,directory/'validation.json'),'input_digest':input_doc['input_digest'],'config_sha256':ctx.config_sha,'counts':export['coverage'],'budget':state['budget'],'warnings':state['warnings'],'artifacts':artifacts,'t3_artifacts':{cid:ref(work,directory/f't3/{ctx.folder(next(c for c in cs if c["candidate_id"]==cid))}/screen_changes.json') for cid in t3}}
            write(directory/'result.json',result,immutable=True)
            handoff.verify_locked(work,video_id,ctx.run_id)
            write(work/'vision/current.json',{'schema_version':'extractube.stage2.current.v1','run_id':ctx.run_id,'input_digest':input_doc['input_digest'],'result':ref(work,directory/'result.json')})
            ctx.save_state();ctx.emit(f'handoff={directory/"stage3_input.json"} current={work/"vision/current.json"}')
        except InferencePaused as exc:
            state['execution_status']=exc.status;ctx.save_state()
        except KeyboardInterrupt:
            state['execution_status']='running';ctx.save_state();raise
        except Exception:
            state['execution_status']='failed'
            if ctx.trace is not None and not (directory/'t1/trace.json').exists():write(directory/'t1/partial_trace.json',envelope(ctx,'trace',ctx.trace,status='failed'),immutable=True)
            ctx.save_state();raise
        ctx.emit(f'inference_calls={ctx.invocation_calls} reused_jobs={ctx.reused_jobs} http_seconds={state["budget"]["http_seconds"]:.3f}')
        return _result(work,directory,ctx.invocation_calls,ctx.reused_jobs)
    finally:lock.release()
