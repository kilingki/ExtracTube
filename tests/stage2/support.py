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
        if isinstance(content,str):
            if '영상 전사에서 의미' in prompt:parsed={'boundaries':[{'sentence_index':1,'reason':'입력 작업으로 전환'}]}
            elif '경계 앞:' in prompt:parsed={'action':'keep','reason':'도입에서 입력으로 전환'}
            elif '영상 발화 텍스트' in prompt:
                selected='단위 식별: 1' in prompt
                parsed={'decision':'needed' if selected else 'not_needed','reason':'입력 시연' if selected else '도입','evidence_sentence_indices':[1 if selected else 0]}
            else:raise AssertionError(prompt)
        else:
            import re
            ids=[re.search(r'frame_id: (.*)',content[i]['text']).group(1) for i in range(1,len(content),2)]
            if '후속 시각 정보 추출' in prompt:
                mapping={11250:'keep',11750:'drop',12250:'uncertain',12750:'keep',13200:'drop'}
                parsed={'frames':[{'frame_id':fid,'decision':mapping[int(fid.rsplit('pts',1)[1])],'reason':'실제 화면','duplicate_of':None} for fid in ids]}
            else:
                mapping={11250:'use',12250:'uncertain',12750:'omit'}
                rows=[]
                for fid in ids:
                    decision=mapping[int(fid.rsplit('pts',1)[1])]
                    rows.append({'frame_id':fid,'decision':decision,'relevance':'yes','readability':'sufficient' if decision=='use' else 'uncertain','completeness':'sufficient','visual_kind':'ui','caption':'입력 화면' if decision=='use' else None,'reason':'실제 화면의 판정'})
                parsed={'frames':rows}
        response=wire({'choices':[{'finish_reason':'stop','message':{'content':wire(parsed).decode()}}]})
        on_headers(200,{'Content-Length':str(len(response))})
        return {'status':200,'body':response,'complete':True}

def fake_detect(ctx,c):
    samples=[]
    for i,pts in enumerate([11250,11750,12250,12750,13200]):
        samples.append({'sample_index':i,'frame_id':f'{ctx.video_id}:v0:pts{pts}','stream_index':0,'pts':pts,'time_base':{'numerator':1,'denominator':1000},'video_seconds':f'{pts/1000-10:.9f}','request_indices':[i],'roles':['grid' if i<4 else 'tail'],'analysis_width':640,'analysis_height':360,'gray_sha256':__import__('hashlib').sha256(bytes(640*360)).hexdigest()})
    rows=[{'index':i,'before_sample':i-1,'after_sample':i,'elapsed_video_seconds':decimal(samples[i]['video_seconds'])-decimal(samples[i-1]['video_seconds']),'global_mean':decimal(0),'t':decimal(samples[i]['video_seconds']),'tile_values':[decimal(0)]*len(changes.b.tile_layout(640,360))} for i in range(1,5)]
    differences=changes.plain(changes.finish_rows(rows,samples,changes.b.tile_layout(640,360)))
    return {'candidate_id':c['candidate_id'],'requested_range':c['requested_video_range'],'effective_range':c['effective_video_range'],'requests':[{'request_index':i,'kind':'grid' if i<4 else 'tail','requested_video_seconds':f'{1.25+i*.5:.9f}','strategy':'first_at_or_after' if i<4 else 'last_before_end','status':'ok','sample_index':i,'extraction_error_seconds':'0.000000000'} for i in range(5)],'samples':samples,'differences':differences,'boundaries':[],'segments':[{'segment_id':c['candidate_id']+':g0','segment_index':0,'video_range':c['effective_video_range'],'left_boundary_id':None,'right_boundary_id':None,'sample_indices':list(range(5))}],'decode':{'fixture':True},'warnings':[]}

def fake_extract(ctx,samples):
    result={}
    for s in samples:
        p=ctx.work/f'vision/cache/fixture/{s["pts"]}.png';p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists():Image.new('RGB',(160,90),'white').save(p)
        r=frames.image_ref(ctx.work,p);log=ctx.directory/f't4/extractions/{s["pts"]}.json';write(log,{'pts':s['pts']},immutable=True)
        result[s['frame_id']]={'frame_id':s['frame_id'],'stream_index':0,'pts':s['pts'],'time_base':s['time_base'],'video_seconds':s['video_seconds'],'source_video':ctx.input['stage1_identities']['video'],'full':r,'vlm':r,'display_transform':'fixture','extraction_job_ref':{'job_key':digest(s),'source_run_id':ctx.run_id,'result':ref(ctx.work,log)}}
    return result

@contextmanager
def adapters(transport):
    with ExitStack() as stack:
        stack.enter_context(patch('extractube.infer.make_transport',return_value=transport))
        stack.enter_context(patch('extractube.visual_input.changes.probe_video',return_value=STREAM.copy()))
        stack.enter_context(patch('extractube.visual_input.read_audio_start',return_value=decimal('10.25')))
        stack.enter_context(patch('extractube.visual_input.changes.confirm_end',return_value={'last_pts':13900,'last_video_seconds':'3.900000000'}))
        stack.enter_context(patch('extractube.screen_changes.detect',side_effect=fake_detect))
        stack.enter_context(patch('extractube.frames.extract',side_effect=fake_extract))
        yield

def settings(**changes):
    return VisionSettings(timeout_seconds=1,max_inference_calls=30,max_http_seconds=100,**changes)
