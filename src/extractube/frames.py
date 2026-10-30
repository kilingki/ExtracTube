"""Exact-PTS original PNG extraction, immutable image sharing and VLM resizing."""

import subprocess
import uuid
from decimal import Decimal
from pathlib import Path
from PIL import Image
from . import _frame_extract as _rgb
from .store import safe, sha, ref, write, read, digest
from .timestamps import decimal, seconds, actual
from .errors import VisionError

EXTRACT_POLICY={'policy':'rgb_exact_pts.v1','pixel_format':'rgb24','rotation':'ffmpeg_default_display','clusters':{'gap':'2','limit':40},'prerolls':['1','2','4','8','actual']}

def image_info(path):
    data=Path(path).read_bytes();info=_rgb.png_info(data)
    with Image.open(path) as im:im.verify()
    with Image.open(path) as im:
        im.load()
        if im.mode!='RGB' or im.size!=(info['width'],info['height']):raise VisionError('PNG RGB decode 오류입니다.',kind='image')
    return {'width':info['width'],'height':info['height'],'bytes':len(data),'format':'png','color':'rgb24'}

def image_ref(work,path):return {**ref(work,path),**image_info(path)}

def check_image(ctx,reference):
    p=ctx.hashes.check(ctx.work,reference);info=image_info(p)
    if any(reference.get(k)!=v for k,v in info.items()):raise VisionError('PNG 이미지 크기/형식 참조가 다릅니다.',kind='validation')
    return p

def frame_filename(stream,pts):return f'v{stream:03d}_pts{pts if pts>=0 else "m"+str(-pts)}.png'

def vlm_image(ctx,full):
    source=check_image(ctx,full);filter_text=_rgb.scale_filter(full['width'],full['height'])
    policy=digest({'filter':filter_text,'version':ctx.config['versions']['ffmpeg']})
    dest=safe(ctx.work,f'vision/cache/vlm/{full["sha256"]}/{policy}.png')
    side=dest.with_suffix('.json')
    if side.is_file():
        try:
            reference=read(side);check_image(ctx,reference)
            return reference,filter_text
        except (VisionError,ValueError,OSError):
            dest=dest.with_name(policy+'_'+uuid.uuid4().hex+'.png');side=dest.with_suffix('.json')
    tmp=ctx.directory/f'.tmp/vlm_{uuid.uuid4().hex}.png';tmp.parent.mkdir(parents=True,exist_ok=True)
    args=[ctx.settings.ffmpeg,'-hide_banner','-nostdin','-loglevel','error','-i',str(source),'-vf',filter_text,'-frames:v','1','-update','1',str(tmp)]
    result=subprocess.run(args,capture_output=True,timeout=300,check=False)
    if result.returncode or not tmp.is_file():raise VisionError('VLM 축소본 생성 실패입니다.',kind='image')
    info=image_info(tmp)
    if max(info['width'],info['height'])>1280 or info['width']>full['width'] or info['height']>full['height']:raise VisionError('VLM 크기/확대 금지 조건 위반입니다.',kind='image')
    write(dest,tmp.read_bytes(),raw=True,immutable=True);reference=image_ref(ctx.work,dest);write(side,reference,immutable=True)
    return reference,filter_text

def extract(ctx,samples):
    unique={s['frame_id']:s for s in samples};stream=ctx.input['stream'];tb=stream['time_base'];expected=(tb['numerator'],tb['denominator']);version=ctx.config['versions']['ffmpeg']
    profile=digest({'policy':EXTRACT_POLICY,'version':version});base=f'vision/cache/frames/{ctx.input["video_ref"]["sha256"]}/{profile}'
    frames={};missing=[];paths={}
    for fid,s in sorted(unique.items(),key=lambda pair:pair[1]['pts']):
        dest=safe(ctx.work,base+'/'+frame_filename(stream['index'],s['pts']));side=dest.with_suffix('.json')
        if side.is_file():
            try:
                saved=read(side);check_image(ctx,saved['full'])
                if saved['pts']!=s['pts'] or saved['source_video']!=ctx.input['stage1_identities']['video'] or saved['time_base']!=tb:raise VisionError('원본 프레임 identity 오류',kind='image')
                frames[fid]=saved;continue
            except (VisionError,ValueError,OSError):
                dest=safe(ctx.work,base+'_recovery_'+uuid.uuid4().hex+'/'+frame_filename(stream['index'],s['pts']))
        paths[fid]=dest;missing.append(s)
    clusters=[]
    for s in missing:
        t=actual(s['pts'],tb,stream['start_time'])
        if not clusters or len(clusters[-1])>=40 or t-actual(clusters[-1][-1]['pts'],tb,stream['start_time'])>2:clusters.append([])
        clusters[-1].append(s)
    for group in clusters:
        start=decimal(seconds(actual(group[0]['pts'],tb,stream['start_time'])));end=decimal(seconds(actual(group[-1]['pts'],tb,stream['start_time'])))
        seek=max(Decimal(0),start-1);duration=end-seek+Decimal('1.5');temp=ctx.directory/f'.tmp/rgb_{uuid.uuid4().hex}'
        result=_rgb.extract_window(ctx.settings.ffmpeg,ctx.video_path,[s['pts'] for s in group],seek=seek,duration=duration,destination=temp,expected_time_base=expected)
        matches={}
        if result['problem'] is None:
            if any(rec['pts'] not in {s['pts'] for s in group} for rec in result['frames']):raise VisionError('요청 외 PTS가 추출되었습니다.',kind='image')
            for rec,path in zip(result['frames'],result['files']):
                if rec['pts'] in matches:raise VisionError('중복 PTS 추출입니다.',kind='image')
                matches[rec['pts']]=(path,result)
        for s in group:
            fid=s['frame_id'];t=decimal(seconds(actual(s['pts'],tb,stream['start_time'])));tried=set()
            if s['pts'] not in matches:
                for pre in [Decimal(1),Decimal(2),Decimal(4),Decimal(8),t]:
                    seek=max(Decimal(0),t-pre)
                    if seek in tried:continue
                    tried.add(seek);tmp=ctx.directory/f'.tmp/one_{uuid.uuid4().hex}'
                    one=_rgb.extract_one(ctx.settings.ffmpeg,ctx.video_path,s['pts'],seek=seek,duration=t-seek+Decimal('1.5'),destination=tmp,expected_time_base=expected)
                    if one['problem'] is None:matches[s['pts']]=(one['files'][0],one);break
            if s['pts'] not in matches:continue
            path,info=matches[s['pts']]
            image_info(path);dest=paths[fid];write(dest,path.read_bytes(),raw=True,immutable=True)
            logpath=ctx.directory/f't4/extractions/{uuid.uuid4().hex}.json'
            write(logpath,{'pts':s['pts'],'time_base':tb,'argv':info['args'],'returncode':info['returncode'],'frames':info['frames'],'stderr':info['stderr_tail']},immutable=True)
            full=image_ref(ctx.work,dest)
            frame={'frame_id':fid,'stream_index':stream['index'],'pts':s['pts'],'time_base':tb,'video_seconds':seconds(actual(s['pts'],tb,stream['start_time'])),'source_video':ctx.input['stage1_identities']['video'],'full':full,'display_transform':'ffmpeg_default_display','extraction_job_ref':{'job_key':digest({'video':ctx.input['video_ref']['sha256'],'stream':stream['index'],'pts':s['pts'],'time_base':tb,'policy':profile}),'source_run_id':ctx.run_id,'result':ref(ctx.work,logpath)}}
            write(dest.with_suffix('.json'),frame,immutable=True);frames[fid]=frame
    output={}
    for fid,frame in frames.items():
        try:
            vlm,filter_text=vlm_image(ctx,frame['full']);output[fid]={**frame,'vlm':vlm,'vlm_filter':filter_text}
        except (VisionError,OSError,ValueError,subprocess.SubprocessError):continue
    return output
