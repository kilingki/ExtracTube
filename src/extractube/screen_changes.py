"""Streaming B05 sampling: raw images are discarded after adjacent scalar extraction."""

import hashlib
from decimal import Decimal
from fractions import Fraction
from . import _screen_change as b
from .store import write
from .timestamps import decimal, seconds, score, actual, span
from .errors import VisionError

B05 = {'config_id':'B05','logic_version':'t3.screen_changes.tile_or.grid_support.v1','grid_interval_seconds':'0.5','padding_seconds':'0','extra_sample':'last_frame_before_end','analysis_width':640,'analysis_height':'even_aspect','scale_flags':'area','pixel_format':'gray','tile_width':80,'tile_height':90,'tile_origin':'top_left','tile_combine':'any_pass','neighbor_seconds':'5','min_neighbor_count':4,'absolute_threshold':'0.005','mad_scale':'1.4826','mad_multiplier':'3','peak_radius_seconds':'0.5','peak_neighbor':'one_grid_request_step','grid_suppress_rule':'kept_neighbor_passing_tile_subset','tie_break':'earlier_time','min_segment_merge':False,'max_segment_split':False,'decode_concurrency':1,'ffmpeg_threads':2,'subprocess_timeout_seconds':300,'decode_margin_seconds':'0.15','tail_windows_seconds':['1','2','4'],'sample_gap_warning_seconds':'1'}
probe_video = b.probe_video
tool_identity = b.tool_identity
tile_layout = b.tile_layout

# Rendering decimals happens only at the artifact boundary.
def plain(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, Fraction):
        return seconds(value)
    if isinstance(value, dict):
        return {k:plain(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [plain(v) for v in value]
    return value

def decode(ffmpeg, path, start, end, stream, on_frame):
    args=[ffmpeg,'-hide_banner','-nostdin','-loglevel','info','-threads','2','-copyts','-ss',format(start,'f')]
    if end is not None:
        args += ['-t',format(end-start+Decimal('0.15'),'f')]
    args += ['-i',str(path),'-map','0:v:0','-an','-vf','scale=640:-2:flags=area,format=gray,showinfo','-fps_mode','passthrough','-f','rawvideo','-pix_fmt','gray','pipe:1']
    last_pts=None
    def accept(rec, raw):
        nonlocal last_pts
        if last_pts is not None and rec['pts'] <= last_pts:
            raise VisionError('디코드 PTS가 증가하지 않습니다.', kind='media')
        last_pts=rec['pts']
        on_frame(rec,raw)
    result=b.run_ffmpeg(args,300,accept)
    tb=Fraction(stream['time_base']['numerator'],stream['time_base']['denominator'])
    if result['time_base_in'] != tb or result['time_base_out'] not in (None,tb):
        raise VisionError('필터 time_base가 원본과 다릅니다.',kind='media')
    return result

def last_frame(ffmpeg,path,start,end,stream):
    last=None
    def accept(rec,raw):
        nonlocal last
        t=actual(rec['pts'],stream['time_base'],stream['start_time'])
        if t>=Fraction(start) and (end is None or t<Fraction(end)):
            last=(rec,raw,t)
    result=decode(ffmpeg,path,start,end,stream,accept)
    return last,result

def confirm_end(ffmpeg,path,stream):
    duration=decimal(stream['duration'])
    last, result=last_frame(ffmpeg,path,max(Decimal(0),duration-3),None,stream)
    if last is None or last[2]<0 or last[2]>=Fraction(duration):
        raise VisionError('스트림 마지막 프레임이 유효하지 않습니다.',kind='media')
    return {'last_pts':last[0]['pts'],'last_video_seconds':seconds(last[2])}

def finish_rows(rows, samples, layout):
    return b._finish_tile_rows(rows,layout,samples,B05)

def replay(payload):
    samples=payload['samples']
    if not samples:
        raise VisionError('T3 sample이 없습니다.',kind='validation')
    layout=b.tile_layout(samples[0]['analysis_width'],samples[0]['analysis_height'])
    rows=[]
    for row in payload['differences']:
        after=samples[row['after_sample']]
        rows.append({'index':row['index'],'before_sample':row['before_sample'],'after_sample':row['after_sample'],'elapsed_video_seconds':decimal(row['elapsed_video_seconds']),'global_mean':decimal(row['global_mean']),'t':decimal(after['video_seconds']),'tile_values':[decimal(t['value']) for t in row['tiles']]})
    return plain(finish_rows(rows,samples,layout))

def detect(ctx,candidate):
    r=candidate['effective_video_range']; start,end=decimal(r['start']),decimal(r['end'])
    stream=ctx.input['stream']; requests=b.grid_requests(start,end)
    samples=[]; request_rows=[]; rows=[]; previous=None; last=None; at=0; layout=None
    def add(rec,raw,hits,tail=False):
        nonlocal previous,layout
        t=actual(rec['pts'],stream['time_base'],stream['start_time'])
        if samples and samples[-1]['pts']==rec['pts']:
            samples[-1]['roles'].append('tail')
            samples[-1]['request_indices'].append(len(requests))
            return
        shown=max(start,min(decimal(seconds(t)),end-Decimal('0.000000001')))
        sample={'sample_index':len(samples),'frame_id':f'{ctx.video_id}:v{stream["index"]}:pts{rec["pts"]}','stream_index':stream['index'],'pts':rec['pts'],'time_base':stream['time_base'],'video_seconds':seconds(shown),'request_indices':hits+([len(requests)] if tail else []),'roles':(['grid'] if hits else [])+(['tail'] if tail else []),'analysis_width':rec['width'],'analysis_height':rec['height'],'gray_sha256':hashlib.sha256(raw).hexdigest()}
        if layout is None:
            layout=b.tile_layout(rec['width'],rec['height'])
        if previous is not None:
            if (rec['width'],rec['height'])!=(samples[-1]['analysis_width'],samples[-1]['analysis_height']):
                raise VisionError('분석 크기가 바뀌었습니다.',kind='media')
            mean,values=b.frame_tile_differences(previous,raw,rec['width'],rec['height'],layout,80,90)
            rows.append({'index':len(samples),'before_sample':len(samples)-1,'after_sample':len(samples),'elapsed_video_seconds':shown-decimal(samples[-1]['video_seconds']),'global_mean':mean,'t':shown,'tile_values':values})
        samples.append(sample); previous=raw
    def accept(rec,raw):
        nonlocal last,at
        t=actual(rec['pts'],stream['time_base'],stream['start_time'])
        if not Fraction(start)<=t<Fraction(end):
            return
        last=(rec,raw)
        hits=[]
        while at<len(requests) and Fraction(requests[at])<=t:
            hits.append(at);at+=1
        if hits:
            add(rec,raw,hits)
            for hit in hits:
                request_rows.append({'request_index':hit,'kind':'grid','requested_video_seconds':seconds(requests[hit]),'strategy':'first_at_or_after','status':'ok','sample_index':len(samples)-1,'extraction_error_seconds':seconds(t-Fraction(requests[hit]))})
    decoded=decode(ctx.settings.ffmpeg,ctx.video_path,start,end,stream,accept)
    stderr_path=ctx.directory/f't3/{ctx.folder(candidate)}/decode.stderr'
    write(stderr_path,decoded.pop('stderr').encode(),raw=True,immutable=True)
    if last is None:
        raise VisionError('후보 안에 실제 프레임이 없습니다.',kind='media')
    attempts=[]; tail=None
    for window in (1,2,4):
        seek=max(start,end-window)
        tail,info=last_frame(ctx.settings.ffmpeg,ctx.video_path,seek,end,stream)
        tail_log=ctx.directory/f't3/{ctx.folder(candidate)}/tail_{window}.stderr'
        write(tail_log,info.pop('stderr').encode(),raw=True,immutable=True)
        attempts.append({'window_seconds':str(window),'argv':info['args'],'frame_count':info['frame_count'],'stderr':tail_log.relative_to(ctx.work).as_posix()})
        if tail is not None or seek==start:
            break
    if tail is None or tail[0]['pts']!=last[0]['pts'] or tail[1]!=last[1]:
        raise VisionError('독립 tail 디코드가 원본 tail과 다릅니다.',kind='media')
    add(last[0],last[1],[],tail=True)
    for hit in range(at,len(requests)):
        request_rows.append({'request_index':hit,'kind':'grid','requested_video_seconds':seconds(requests[hit]),'strategy':'first_at_or_after','status':'missing','sample_index':None,'extraction_error_seconds':None})
    request_rows.append({'request_index':len(requests),'kind':'tail','requested_video_seconds':seconds(end),'strategy':'last_before_end','status':'ok','sample_index':len(samples)-1,'extraction_error_seconds':seconds(tail[2]-Fraction(end))})
    differences=finish_rows(rows,samples,layout)
    native=[{**s,'video_seconds':decimal(s['video_seconds'])} for s in samples]
    bounds=b._boundaries(differences,native,start,end)
    for i,bd in enumerate(bounds):
        bd['boundary_id']=f'{candidate["candidate_id"]}:b{i}'
    segments=[]
    edges=[start]+[v['video_seconds'] for v in bounds]+[end]
    for i,(left,right) in enumerate(zip(edges,edges[1:])):
        segments.append({'segment_id':f'{candidate["candidate_id"]}:g{i}','segment_index':i,'video_range':span(left,right,'video_start'),'left_boundary_id':None if i==0 else bounds[i-1]['boundary_id'],'right_boundary_id':None if i==len(bounds) else bounds[i]['boundary_id'],'sample_indices':[s['sample_index'] for s in samples if left<=decimal(s['video_seconds'])<right]})
    warnings=[]
    from .visual_policy import warning
    for req in request_rows:
        if req['status']=='missing': warnings.append(warning('sample_missing','t3',candidate['candidate_id']))
    for a,z in zip(samples,samples[1:]):
        if actual(z['pts'],z['time_base'],stream['start_time'])-actual(a['pts'],a['time_base'],stream['start_time'])>1:
            warnings.append(warning('sample_gap','t3',candidate['candidate_id']))
    return plain({'candidate_id':candidate['candidate_id'],'requested_range':candidate['requested_video_range'],'effective_range':r,'requests':request_rows,'samples':samples,'differences':differences,'boundaries':bounds,'segments':segments,'decode':{**decoded,'tail_attempts':attempts,'stderr':stderr_path.relative_to(ctx.work).as_posix()},'warnings':warnings})
