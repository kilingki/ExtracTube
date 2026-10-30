"""Validated Stage 1 snapshot. Never repairs or reruns Stage 1."""

from fractions import Fraction
from . import screen_changes as changes
from .store import read, digest, ref, safe
from .timestamps import decimal, seconds
from .errors import VisionError
from .visual_policy import warning
from extractube.store import identity

CHAIN={'video':(), 'wav':('video',), 'asr':('wav',), 'fa':('wav','asr'), 'sentences':('fa',)}

def flatten(payload):
    if not isinstance(payload,dict) or payload.get('time_reference')!='audio_start' or payload.get('overlap_deduplicated') is not True or not isinstance(payload.get('sentence_policy_id'),str) or not payload['sentence_policy_id'].strip():
        raise VisionError('Stage 1 문장 정책/시각 기준이 유효하지 않습니다.',kind='input')
    audio=payload.get('audio'); chunks=payload.get('chunks'); omitted=payload.get('omitted_chunks')
    if not isinstance(audio,dict) or not isinstance(chunks,list) or not isinstance(omitted,list):
        raise VisionError('문장 audio/chunks/omitted_chunks 형식 오류입니다.',kind='input')
    for key in ('sample_rate','channels','num_samples'):
        if type(audio.get(key)) is not int or audio[key] < (0 if key=='num_samples' else 1):
            raise VisionError('문장 audio 수치가 유효하지 않습니다.',kind='input')
    result=[]; warnings=[]; prev=None
    for i,c in enumerate(chunks):
        if not isinstance(c,dict) or type(c.get('index')) is not int or any(not isinstance(c.get(k),str) or not c[k].strip() for k in ('text','language')) or not isinstance(c.get('items'),list) or not c['items']:
            raise VisionError(f'문장 {i} 구조가 유효하지 않습니다.',kind='input')
        previous_start=None
        for item in c['items']:
            if not isinstance(item,dict): raise VisionError('문장 item 형식 오류입니다.',kind='input')
            a,z=decimal(item.get('start_time')),decimal(item.get('end_time'))
            if a<0 or z<a or (previous_start is not None and a<previous_start):
                raise VisionError('문장 item 시각이 유효하지 않습니다.',kind='input')
            previous_start=a
        a=decimal(c['items'][0]['start_time']);z=decimal(c['items'][-1]['end_time'])
        if z<a or (prev is not None and a<prev[0]):
            raise VisionError('문장 시각 순서 오류입니다.',kind='input')
        if prev is not None and a<prev[1]: warnings.append(warning('alignment_overlap','input',str(i),'기존 정렬 중첩을 보존합니다.'))
        result.append({'sentence_index':i,'source_chunk_index':c['index'],'audio_start':seconds(a),'audio_end':seconds(z),'text':c['text'],'language':c['language'],'source_chunk':c})
        prev=(a,z)
    if omitted: warnings.append(warning('audio_incomplete','input',detail='생략된 음성 구간이 있습니다.'))
    return result,warnings

def load(work,video_id,settings,hashes,expected_inputs=None):
    meta=read(safe(work,'metadata.json'))
    if not isinstance(meta,dict) or meta.get('video_id')!=video_id or not isinstance(meta.get('results'),dict):
        raise VisionError('Stage 1 metadata가 유효하지 않습니다.',kind='input')
    records=meta['results']; identities={}
    for kind,parents in CHAIN.items():
        r=records.get(kind)
        if not isinstance(r,dict) or r.get('kind')!=kind or not isinstance(r.get('result_id'),str) or not r['result_id']:
            raise VisionError(f'Stage 1 {kind} record 오류입니다.',kind='input')
        hashes.check(work,r)
        identities[kind]=identity(r)
        if r.get('upstream') != [identities[p] for p in parents]:
            raise VisionError(f'Stage 1 {kind} upstream identity가 다릅니다.',kind='input')
    if expected_inputs and any(expected_inputs.get(k)!=identities[k] for k in ('video','sentences')):
        raise VisionError('Stage 1 반환 후 입력이 변경되었습니다.',kind='input')
    video_path=safe(work,records['video']['path']);sentence_path=safe(work,records['sentences']['path'])
    payload=read(sentence_path); sentences,warnings=flatten(payload)
    stream=dict(changes.probe_video(settings.ffprobe,video_path))
    stream.pop('time_base_fraction');stream.pop('args');stream.pop('elapsed_seconds')
    stream=changes.plain(stream)
    media=records['video'].get('media',{})
    values=[]
    for container in (meta,media):
        if 'audio_to_video_offset_seconds' in container: values.append(decimal(container['audio_to_video_offset_seconds']))
    if not values or len(set(values))!=1:
        raise VisionError('음성/영상 오프셋이 없거나 일치하지 않습니다.',kind='input')
    offset=values[0]; tick=Fraction(stream['time_base']['numerator'],stream['time_base']['denominator'])
    starts=[]
    for key in ('video_start_time_seconds','source_audio_start_time_seconds'):
        found=[decimal(c[key]) for c in (meta,media) if key in c]
        if not found or Fraction(max(found)-min(found))>tick:
            raise VisionError('Stage 1 stream 시작 시각 오류입니다.',kind='input')
        starts.append(found[0])
    if abs(Fraction(starts[0])-Fraction(decimal(stream['start_time'])))>tick or abs(Fraction(starts[1]-starts[0]-offset))>tick:
        raise VisionError('Stage 1/probe 시작 시각 또는 offset이 다릅니다.',kind='input')
    # Audio stream start is independently checked; never infer a missing offset.
    import subprocess
    probed=read_audio_start(settings.ffprobe,video_path)
    if abs(Fraction(probed)-Fraction(starts[1]))>tick:
        raise VisionError('원본 오디오 시작 시각이 Stage 1과 다릅니다.',kind='input')
    stream.update(changes.confirm_end(settings.ffmpeg,video_path,stream))
    core={'video':identities['video'],'sentences':identities['sentences'],'sentence_policy_id':payload['sentence_policy_id'],'stream':{k:stream[k] for k in ('index','start_time','duration','time_base')},'offset':seconds(offset)}
    snapshot={'schema_version':'extractube.stage2.input.v1','video_id':video_id,'source_url':meta.get('source_url'),'metadata_snapshot_sha256':hashes.hash(work/'metadata.json'),'stage1_identities':identities,'video_ref':ref(work,video_path),'sentences_ref':ref(work,sentence_path),'stage1_upstream_records':{k:records[k] for k in CHAIN},'stream':stream,'audio_to_video_offset_seconds':seconds(offset),'input_digest':digest(core)}
    return snapshot,{'sentences':sentences,'omitted_chunks':payload['omitted_chunks'],'source':ref(work,sentence_path),'sentence_policy_id':payload['sentence_policy_id'],'audio':payload['audio']},warnings

def read_audio_start(ffprobe,path):
    import subprocess
    from .store import loads
    proc=subprocess.run([ffprobe,'-v','error','-select_streams','a:0','-show_entries','stream=start_time','-of','json',str(path)],capture_output=True,timeout=300,check=False)
    try:
        rows=loads(proc.stdout)['streams']
        if proc.returncode or len(rows)!=1: raise ValueError()
        return decimal(rows[0]['start_time'])
    except (KeyError,ValueError,IndexError) as exc:
        raise VisionError('원본 오디오 시작 시각이 불명확합니다.',kind='media') from exc
