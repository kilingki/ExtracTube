"""Synthetic adapter fixture from spec 14.3; no original video answers enter product code."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from extractube import screen_changes as changes, frames
from extractube.store import write, sha, wire, loads, ref, digest, result_record, identity
from extractube.settings import VisionSettings
from extractube.timestamps import span, decimal

VIDEO_ID='Fixture1234'
STREAM={'index':0,'width':160,'height':90,'codec_name':'h264','time_base':{'numerator':1,'denominator':1000},'time_base_fraction':__import__('fractions').Fraction(1,1000),'start_time':decimal(10),'duration':decimal(4),'args':[],'elapsed_seconds':0}

def seed(root,empty=False,omitted=False):
    work=root/VIDEO_ID;work.mkdir(parents=True)
    (work/'source').mkdir();(work/'audio').mkdir()
    (work/'source/video.mp4').write_bytes(b'synthetic-adapter-video')
    chunks=[]
    for i,text in enumerate(['도입.','입력 칸을 보세요.','저장합니다.'] if not empty else []):
        chunks.append({'index':i*3,'text':text,'language':'Korean','items':[{'text':text,'start_time':i,'end_time':i+1}]})
    sentence={'time_reference':'audio_start','overlap_deduplicated':True,'sentence_policy_id':'fixture-v1','audio':{'sample_rate':16000,'channels':1,'num_samples':64000},'chunks':chunks,'omitted_chunks':[{'reason':'fixture_loss'}] if omitted else []}
    write(work/'audio/alignment_sentences.json',sentence)
    records={}
    chain={'video':(),'wav':('video',),'asr':('wav',),'fa':('wav','asr'),'sentences':('fa',)}
    for kind,parents in chain.items():
        relative='source/video.mp4' if kind=='video' else ('audio/alignment_sentences.json' if kind=='sentences' else 'audio/'+kind+'.json')
        p=work/relative
        if not p.exists():write(p,{'fixture':kind})
        records[kind]=result_record(kind=kind,relative_path=relative,file_hash=sha(p),upstream=[identity(records[k]) for k in parents],settings={},media={'video_start_time_seconds':10,'source_audio_start_time_seconds':10.25,'audio_to_video_offset_seconds':.25} if kind=='video' else None)
    write(work/'metadata.json',{'video_id':VIDEO_ID,'source_url':'https://example.test/<script>','results':records,'video_start_time_seconds':10,'source_audio_start_time_seconds':10.25,'audio_to_video_offset_seconds':.25})
    return work

class FakeTransport:
    def __init__(self,mode=None):self.calls=[];self.mode=mode
    def send(self,url,body,timeout,limit,on_headers):
        request=loads(body);self.calls.append(request)
        if self.mode=='timeout':raise TimeoutError('fixture')
        content=request['messages'][0]['content'];prompt=content if isinstance(content,str) else content[0]['text']
        schema_name=request.get('response_format',{}).get('json_schema',{}).get('name','')
        if isinstance(content,str):
            if schema_name=='t1_boundary' or '영상 전사에서 의미' in prompt:
                parsed={'boundaries':[{'sentence_index':1,'reason':'입력 작업으로 전환'}]}
            elif schema_name=='t1_verify' or '검증할 후보 경계 목록' in prompt:
                targets=request['response_format']['json_schema']['schema']['properties']['verifications']['items']['properties']['sentence_index']['enum']
                parsed={'verifications':[{'sentence_index':t,'action':'keep','reason':'도입에서 입력으로 전환'} for t in targets]}
            elif '경계 앞:' in prompt:
                parsed={'action':'keep','reason':'도입에서 입력으로 전환'}
            elif schema_name=='t2':
                props=request['response_format']['json_schema']['schema']['properties']['chunks']['properties']
                chunks_out={}
                for cid,cschema in props.items():
                    allowed=cschema['properties']['evidence_sentence_indices']['items']['enum']
                    selected=not cid.endswith(':u0:c0')
                    chunks_out[cid]={'decision':'needed' if selected else 'not_needed','reason':'입력 시연' if selected else '도입','evidence_sentence_indices':[allowed[0]]}
                parsed={'chunks':chunks_out}
            elif '영상 발화 텍스트' in prompt:
                selected='단위 식별: 1' in prompt
                parsed={'decision':'needed' if selected else 'not_needed','reason':'입력 시연' if selected else '도입','evidence_sentence_indices':[1 if selected else 0]}
            else:raise AssertionError(prompt)
        else:
            if schema_name=='t2_probe':
                props=request['response_format']['json_schema']['schema']['properties']['probes']['properties']
                probes_out={}
                for cid in props:
                    is_board='판서' in str(content) or cid.endswith(':u0:c0')
                    probes_out[cid]={'decision':'visual_info' if is_board else 'talking_head','visual_kind':'whiteboard' if is_board else 'talking_head','reason':'화이트보드 판서 확인' if is_board else '토킹헤드 화면'}
                parsed={'probes':probes_out}
            else:
                import re
                ids=[re.search(r'frame_id: (.*)',content[i]['text']).group(1) for i in range(1,len(content),2) if 'role: target\n' in content[i]['text']]
                ctx_ids=[re.search(r'frame_id: (.*)',content[i]['text']).group(1) for i in range(1,len(content),2) if 'role: context_confirmed_use\n' in content[i]['text']]
                rows=[]
                rep=ctx_ids[0] if ctx_ids else None
                for idx,fid in enumerate(ids):
                    if idx==0 and rep is None:
                        rows.append({'frame_id':fid,'decision':'use','duplicate_of':None,'relevance':'yes','readability':'sufficient','completeness':'sufficient','visual_kind':'ui','caption':'입력 화면','reason':'실제 화면'})
                        rep=fid
                    elif idx==1 and not ctx_ids:
                        rows.append({'frame_id':fid,'decision':'omit','duplicate_of':None,'relevance':'no','readability':'sufficient','completeness':'sufficient','visual_kind':'ui','caption':None,'reason':'불필요'})
                    elif idx==2 and not ctx_ids:
                        rows.append({'frame_id':fid,'decision':'uncertain','duplicate_of':None,'relevance':'yes','readability':'uncertain','completeness':'sufficient','visual_kind':'ui','caption':None,'reason':'판독 불확실'})
                    else:
                        rows.append({'frame_id':fid,'decision':'omit','duplicate_of':rep,'relevance':'yes','readability':'sufficient','completeness':'sufficient','visual_kind':'ui','caption':None,'reason':'중복 화면'})
                parsed={'frames':rows}
        response=wire({'choices':[{'finish_reason':'stop','message':{'content':wire(parsed).decode()}}]})
        on_headers(200,{'Content-Length':str(len(response))})
        return {'status':200,'body':response,'complete':True}

def fake_detect(ctx,c):
    r=c['effective_video_range'];start=decimal(r['start']);end=decimal(r['end'])
    grids=changes.b.grid_requests(start,end)
    layout=changes.b.tile_layout(640,360)
    samples=[];requests=[]
    for i,g in enumerate(grids):
        shown=g+decimal('0.01')*i
        if shown>=end:shown=end-decimal('0.001')
        pts=int((decimal('10')+shown)*1000)
        samples.append({'sample_index':i,'frame_id':f'{ctx.video_id}:v0:pts{pts}','stream_index':0,'pts':pts,'time_base':{'numerator':1,'denominator':1000},'video_seconds':f'{shown:.9f}','request_indices':[i],'roles':['grid'],'analysis_width':640,'analysis_height':360,'gray_sha256':__import__('hashlib').sha256(bytes([i])*640*360).hexdigest()})
        requests.append({'request_index':i,'kind':'grid','requested_video_seconds':f'{g:.9f}','strategy':'first_at_or_after','status':'ok','sample_index':i,'extraction_error_seconds':f'{shown-g:.9f}'})
    tail_t=max(decimal(samples[-1]['video_seconds'])+decimal('0.02') if samples else start,end-decimal('0.05'))
    if tail_t>=end:tail_t=end-decimal('0.001')
    tail_pts=int((decimal('10')+tail_t)*1000)
    tail_idx=len(samples)
    samples.append({'sample_index':tail_idx,'frame_id':f'{ctx.video_id}:v0:pts{tail_pts}','stream_index':0,'pts':tail_pts,'time_base':{'numerator':1,'denominator':1000},'video_seconds':f'{tail_t:.9f}','request_indices':[len(grids)],'roles':['tail'],'analysis_width':640,'analysis_height':360,'gray_sha256':__import__('hashlib').sha256(bytes([tail_idx])*640*360).hexdigest()})
    requests.append({'request_index':len(grids),'kind':'tail','requested_video_seconds':f'{end:.9f}','strategy':'last_before_end','status':'ok','sample_index':tail_idx,'extraction_error_seconds':f'{tail_t-end:.9f}'})
    rows=[]
    for i in range(1,len(samples)):
        vals=[decimal(0)]*len(layout)
        gm=decimal(0)
        if i<len(samples)-1:
            vals[(i-1)%8]=decimal('0.2')
            gm=decimal('0.01')
        rows.append({'index':i,'before_sample':i-1,'after_sample':i,'elapsed_video_seconds':decimal(samples[i]['video_seconds'])-decimal(samples[i-1]['video_seconds']),'global_mean':gm,'t':decimal(samples[i]['video_seconds']),'tile_values':vals})
    raw_diffs=changes.finish_rows(rows,samples,layout)
    native=[{**s,'video_seconds':decimal(s['video_seconds'])} for s in samples]
    bounds=changes.b._boundaries(raw_diffs,native,start,end)
    cid=c['candidate_id']
    for i,bd in enumerate(bounds):bd['boundary_id']=f'{cid}:b{i}'
    edges=[start]+[bd['video_seconds'] for bd in bounds]+[end]
    segments=[]
    for i,(left,right) in enumerate(zip(edges,edges[1:])):
        segments.append({'segment_id':f'{cid}:g{i}','segment_index':i,'video_range':span(left,right,'video_start'),'left_boundary_id':None if i==0 else bounds[i-1]['boundary_id'],'right_boundary_id':None if i==len(bounds) else bounds[i]['boundary_id'],'sample_indices':[s['sample_index'] for s in samples if left<=decimal(s['video_seconds'])<right]})
    vsegs=changes.consolidate_segments(cid,samples,raw_diffs,bounds,segments)
    return changes.plain({'candidate_id':cid,'requested_range':c['requested_video_range'],'effective_range':r,'requests':requests,'samples':samples,'differences':raw_diffs,'boundaries':bounds,'segments':segments,'visual_segments':vsegs,'decode':{'fixture':True},'warnings':[]})

def fake_extract(ctx,samples):
    result={}
    for s in samples:
        p=ctx.work/f'vision/cache/fixture/{s["pts"]}.png';p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists():Image.new('RGB',(160,90),'white').save(p)
        r=frames.image_ref(ctx.work,p);log=ctx.directory/f't4/extractions/{s["pts"]}.json';write(log,{'pts':s['pts']},immutable=True)
        result[s['frame_id']]={'frame_id':s['frame_id'],'stream_index':0,'pts':s['pts'],'time_base':s['time_base'],'video_seconds':s['video_seconds'],'source_video':ctx.input['stage1_identities']['video'],'full':r,'vlm':r,'display_transform':'fixture','extraction_job_ref':{'job_key':digest(s),'source_run_id':ctx.run_id,'result':ref(ctx.work,log)}}
    return result

def fake_extract_probe(ctx,chunk_id,start_seconds,end_seconds):
    safe_id=chunk_id.replace(':','_')
    p=ctx.work/f'vision/cache/fixture_probes/{safe_id}.png';p.parent.mkdir(parents=True,exist_ok=True)
    if not p.exists():Image.new('RGB',(160,90),'white').save(p)
    return frames.image_ref(ctx.work,p)

@contextmanager
def adapters(transport):
    with ExitStack() as stack:
        stack.enter_context(patch('extractube.infer.make_transport',return_value=transport))
        stack.enter_context(patch('extractube.visual_input.changes.probe_video',return_value=STREAM.copy()))
        stack.enter_context(patch('extractube.visual_input.read_audio_start',return_value=decimal('10.25')))
        stack.enter_context(patch('extractube.visual_input.changes.confirm_end',return_value={'last_pts':13900,'last_video_seconds':'3.900000000'}))
        stack.enter_context(patch('extractube.screen_changes.detect',side_effect=fake_detect))
        stack.enter_context(patch('extractube.frames.extract',side_effect=fake_extract))
        stack.enter_context(patch('extractube.frames.extract_probe',side_effect=fake_extract_probe))
        yield

def settings(**changes):
    return VisionSettings(timeout_seconds=1,max_inference_calls=30,max_http_seconds=100,**changes)
