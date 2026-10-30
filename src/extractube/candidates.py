"""P3 CTX2 chunk selection and exact reasoning-budget provenance."""

from . import prompts
from .store import wire
from .visual_policy import warning
from .semantic import ranges
from .timestamps import decimal
from .errors import InferenceJobFailed

def split(sentences,start,end):
    result=[];a=start
    while a<end:
        z=a;chars=0
        while z<end and chars+len(sentences[z]['text'])<=6000:
            chars+=len(sentences[z]['text']);z+=1
        if z==a:z=a+1
        result.append((a,z));a=z
    return result

def run(ctx,sentences,units):
    unit_records=[];candidates=[];warnings=[]
    offset=decimal(ctx.input['audio_to_video_offset_seconds']);duration=decimal(ctx.input['stream']['duration'])
    for unit in units:
        uid=unit['unit_id'];chunks=[];parts=split(sentences,unit['sentence_start'],unit['sentence_end'])
        for k,(a,z) in enumerate(parts):
            cid=f'{uid}:c{k}';before=list(range(max(0,a-2),a));after=list(range(z,min(len(sentences),z+2)))
            ar,vr=ranges(sentences,a,z,offset);valid=decimal(vr['start'])>=0 and decimal(vr['start'])<decimal(vr['end'])<=duration
            row={'chunk_id':cid,'unit_id':uid,'chunk_index':k,'sentence_start':a,'sentence_end':z,'context_before_indices':before,'context_after_indices':after,'audio_range':ar,'video_range':vr,'range_status':'valid' if valid else 'invalid','decision':None,'reason':None,'evidence_sentence_indices':None,'inference_status':'failed','candidate_source':None,'model_inference_success':False,'job_ref':None,'warnings':[]}
            def projected(indices):return wire([{'sentence_index':i,'text':sentences[i]['text']} for i in indices]).decode()
            prompt=prompts.render(prompts.T2_P3_CTX2,{'unit_index':unit['unit_index'],'unit_sentence_range':f'[{unit["sentence_start"]},{unit["sentence_end"]})','chunk_index_1based':k+1,'chunk_count':len(parts),'context_before_json':projected(before),'sentences_json':projected(range(a,z)),'context_after_json':projected(after)})
            try:
                if len(sentences[a]['text'])>6000:raise InferenceJobFailed('한 문장 6000자 초과',kind='input_budget')
                parsed,jr,status=ctx.jobs.invoke('t2','t2',prompt,{'allowed':list(range(a,z))},{'sentences':ctx.input['stage1_identities']['sentences'],'unit':[unit['sentence_start'],unit['sentence_end']],'range':[a,z],'context':[before,after]})
                row['job_ref']=jr
                if status=='budget_exhausted':
                    row.update(inference_status='budget_exhausted',candidate_source='budget_guard')
                    row['warnings'].append(warning('t2_budget_guard','t2',cid,'reasoning 예산 소진으로 정책 후보를 보존합니다.'))
                else:row.update(**parsed,inference_status='success',candidate_source='thinking_on',model_inference_success=True)
            except InferenceJobFailed as exc:
                row['warnings'].append(warning('t2_failed','t2',cid,str(exc)))
                row['failure_ref']=ctx.failure_ref(exc)
            selected=row['decision'] in ('needed','uncertain') or row['candidate_source']=='budget_guard'
            if selected:
                candidates.append({'candidate_id':cid,'unit_id':uid,'chunk_index':k,'sentence_start':a,'sentence_end':z,'audio_range':ar,'requested_video_range':vr,'effective_video_range':vr if valid else None,'range_status':row['range_status'],'candidate_source':row['candidate_source'],'model_inference_success':row['model_inference_success'],'decision':row['decision'],'reason':row['reason'],'evidence_sentence_indices':row['evidence_sentence_indices'],'source_job_ref':row['job_ref'],'retention_reason':'reasoning_budget_exhausted' if row['candidate_source']=='budget_guard' else None})
                if not valid:row['warnings'].append(warning('invalid_video_range','t2',cid,'원래 범위를 보존하며 화면 처리를 생략합니다.'))
                if row['decision']=='uncertain':row['warnings'].append(warning('t2_uncertain','t2',cid,'시각 후보 판단의 불확실성을 보존합니다.'))
            warnings.extend(row['warnings']);chunks.append(row)
        has_candidate=any(c['unit_id']==uid for c in candidates)
        all_not_needed=all(c['decision']=='not_needed' and c['inference_status']=='success' for c in chunks)
        failed=any(c['inference_status']=='failed' for c in chunks)
        unit_records.append({'unit_id':uid,'chunk_ids':[c['chunk_id'] for c in chunks],'selection_state':'selected' if has_candidate else ('not_selected' if all_not_needed else 'unresolved'),'execution_status':'partial' if failed and has_candidate else ('failed' if failed else 'ok'),'chunks':chunks})
    if sentences:warnings.append(warning('t2_selected_scope','t2',detail='발화 단서로 선정한 후보만 화면 분석하며 후보 밖 시각 자료는 누락될 수 있습니다.'))
    return {'units':unit_records,'candidates':candidates},warnings
