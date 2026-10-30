"""Candidate-local A03/P1 batches; model decisions belong to associations."""

import base64
from . import prompts, frames
from .store import wire, digest
from .visual_policy import warning
from .errors import InferenceJobFailed

def batch_items(items,size=4):return [items[i:i+size] for i in range(0,len(items),size)]

def content(ctx,task,candidate,associations,frame_map,sentences):
    targets=[a['frame_id'] for a in associations]
    projected=[{k:s[k] for k in ('sentence_index','text','audio_start','audio_end')} for s in sentences[candidate['sentence_start']:candidate['sentence_end']]]
    template=prompts.T4_P1 if task=='t4' else prompts.T5_IMAGE_USE_V1
    prompt=prompts.render(template,{'candidate_id':candidate['candidate_id'],'sentences_json':wire(projected).decode(),'target_ids_json':wire(targets).decode()})
    result=[{'type':'text','text':prompt}];images=[]
    for a in associations:
        f=frame_map[a['frame_id']];path=frames.check_image(ctx,f['vlm']);frames.check_image(ctx,f['full'])
        label=f'frame_id: {f["frame_id"]}\nrole: target\nvideo_seconds: {f["video_seconds"]}\nsegment_index: {a["segment_index"]}\n'
        result.extend([{'type':'text','text':label},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode('ascii')}}]);images.extend([f['vlm'],f['full']])
    return result,images

def classify(ctx,task,candidate,associations,frame_map,sentences,batch_index):
    request,images=content(ctx,task,candidate,associations,frame_map,sentences)
    parsed,jr,status=ctx.jobs.invoke(task,task,request,{'targets':[a['frame_id'] for a in associations]},{'candidate':candidate,'association_ids':[a['association_id'] for a in associations],'sentences':ctx.input['stage1_identities']['sentences'],'images':images},images=images)
    batch={'candidate_id':candidate['candidate_id'],'target_ids':[a['frame_id'] for a in associations],'association_ids':[a['association_id'] for a in associations],'batch_index':batch_index,'status':'success','job_ref':jr}
    return parsed['frames'],batch

def run(ctx,sentences,candidates,t3):
    samples=[s for doc in t3.values() if doc['execution_status']!='failed' for s in doc['payload']['samples']]
    frame_map=frames.extract(ctx,samples);associations=[];decisions=[];batches=[];warnings=[]
    for candidate in candidates:
        cid=candidate['candidate_id'];doc=t3.get(cid)
        if doc is None or doc['execution_status']=='failed':continue
        p=doc['payload'];local=[]
        for sample in p['samples']:
            segment=next(g for g in p['segments'] if sample['sample_index'] in g['sample_indices']);fid=sample['frame_id'];ok=fid in frame_map
            a={'association_id':cid+'@'+fid,'candidate_id':cid,'unit_id':candidate['unit_id'],'segment_id':segment['segment_id'],'segment_index':segment['segment_index'],'frame_id':fid,'source_sample_indices':[sample['sample_index']],'source_request_indices':sample['request_indices'],'extraction_status':'ok' if ok else 'failed','t4_status':'not_run' if ok else 'failed','t4_decision':None,'t4_reason':None,'t4_job_ref':None}
            associations.append(a)
            if ok:local.append(a)
            else:warnings.append(warning('frame_decode_failed','t4',a['association_id']))
        for bi,group in enumerate(batch_items(local)):
            ctx.emit(f'[t4] batch {bi+1}/{(len(local)+3)//4}')
            try:
                parsed,batch=classify(ctx,'t4',candidate,group,frame_map,sentences,bi)
                for a,row in zip(group,parsed):a.update(t4_status='success',t4_decision=row['decision'],t4_reason=row['reason'],t4_job_ref=batch['job_ref'])
            except InferenceJobFailed as exc:
                batch={'candidate_id':cid,'target_ids':[a['frame_id'] for a in group],'association_ids':[a['association_id'] for a in group],'batch_index':bi,'status':'failed','job_ref':None,'failure_ref':ctx.failure_ref(exc)}
                for a in group:a['t4_status']='failed';warnings.append(warning('t4_failed','t4',a['association_id'],str(exc)))
            batches.append(batch)
        for a in associations:
            if a['candidate_id']==cid:
                decisions.append({k:a[k] for k in ('association_id','frame_id','t4_status','t4_decision','t4_reason','t4_job_ref')})
                if a['t4_decision']=='uncertain':warnings.append(warning('t4_uncertain','t4',a['association_id']))
    return {'frames':list(frame_map.values()),'associations':associations},{'batches':batches,'decisions':decisions},warnings
