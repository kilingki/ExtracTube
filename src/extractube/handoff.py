"""Stage 3 contract, independent coverage recomputation and offline provenance checks."""

import html
import os
import re
from pathlib import Path
from types import SimpleNamespace
from .store import read, write, ref, safe, HashCache, digest, RUN
from .visual_policy import POLICY, POLICIES, SUFFIXES, warning
from .timestamps import decimal, seconds, actual
from .errors import VisionError
from . import semantic, screen_changes as changes, frames, assets, infer as inference

USAGE_POLICY={'image_per_unit_required':False,'unverified_ocr_as_facts':False,'preserve_aspect_ratio':True,'full_size_access_required':True,'omit_if_unreadable_at_final_size':True,'infer_missing_content':False}

def coverage(sentences,t1,t2,t3,fd,t4,t5):
    chunks=[c for u in t2['units'] for c in u['chunks']];candidates=t2['candidates'];aa=fd['associations'];dd=t5['decisions'];vv=t5['unit_visuals']
    result={'total_units':len(t1['units']),'total_sentences':len(sentences['sentences'])}
    for decision in ('needed','not_needed','uncertain'):result['t2_'+decision+'_chunks']=sum(c['decision']==decision for c in chunks)
    result['t2_budget_guard_chunks']=sum(c['candidate_source']=='budget_guard' for c in chunks)
    result['t2_failed_chunks']=sum(c['inference_status']=='failed' for c in chunks)
    for k in ('valid','invalid'):result[k+'_candidates']=sum(c['range_status']==k for c in candidates)
    result['t3_success_candidates']=sum(d['execution_status']!='failed' for d in t3.values())
    result['t3_failed_candidates']=sum(d['execution_status']=='failed' for d in t3.values())
    result['t3_samples']=sum(len(d['payload']['samples']) for d in t3.values())
    for k in ('keep','drop','uncertain'):result['t4_'+k+'_associations']=sum(a['t4_decision']==k for a in aa)
    result['t4_failed_associations']=sum(a['t4_status']=='failed' for a in aa)
    for k in ('use','omit','uncertain','failed'):result['t5_'+k+'_associations']=sum(d['decision']==k for d in dd)
    for k in ('available','omitted','not_requested','unresolved'):result[k+'_units']=sum(v['visual_status']==k for v in vv)
    result['selected_assets']=len(t5['assets']);result['audio_incomplete']=bool(sentences['omitted_chunks'])
    return result

def build(ctx,docs,t3):
    s=docs['sentences']['payload'];t1=docs['t1']['payload'];t2=docs['t2']['payload'];fd=docs['frames']['payload'];t4=docs['t4']['payload'];t5=docs['t5']['payload']
    units=[];visuals={v['unit_id']:v for v in t5['unit_visuals']}
    for u in t1['units']:
        v=visuals[u['unit_id']]
        units.append({**{k:u[k] for k in ('unit_id','unit_index','sentence_start','sentence_end','audio_range','video_range')},'sentences':[{k:s['sentences'][i][k] for k in ('sentence_index','source_chunk_index','text','language','audio_start','audio_end')} for i in u['sentence_indices']],**{k:v[k] for k in ('candidate_ids','asset_refs','visual_status','coverage_status','omission_reasons','warnings')}})
    warnings=list(ctx.state['warnings']);status='empty' if not units else ('complete_with_warnings' if warnings else 'complete')
    return {'schema_version':'extractube.handoff.v1','policy_id':POLICY,'run_id':ctx.run_id,'video_id':ctx.video_id,'source_url':ctx.input['source_url'],'input_digest':ctx.input['input_digest'],'execution_status':status,'stage3_usable':True,'source':{'video':ctx.input['stage1_identities']['video'],'sentences':ctx.input['sentences_ref'],'audio_to_video_offset_seconds':ctx.input['audio_to_video_offset_seconds'],'video_stream_start_seconds':seconds(ctx.input['stream']['start_time']),'stream_index':ctx.input['stream']['index']},'omitted_audio_chunks':s['omitted_chunks'],'units':units,'assets':t5['assets'],'coverage':coverage(s,t1,t2,t3,fd,t4,t5),'warnings':warnings,'usage_policy':USAGE_POLICY,'provenance':{k:ref(ctx.work,ctx.directory/p) for k,p in {'input':'input.json','t1':'t1/semantic_units.json','t2':'t2/visual_candidates.json','t4':'t4/decisions.json','t5':'t5/visual_assets.json'}.items()}}

def html_document(ctx,handoff):
    esc=lambda value:html.escape(str(value),quote=True)
    def relative(path):return Path(os.path.relpath(safe(ctx.work,path),ctx.directory)).as_posix()
    rows=['<!doctype html><html lang="ko"><meta charset="utf-8"><title>Stage 2 검토</title><style>body{max-width:1100px;margin:2em auto;font:16px sans-serif}img{max-width:100%;height:auto}section{border-top:1px solid #ccc;padding:1em}pre{white-space:pre-wrap}</style>',f'<h1>{esc(ctx.video_id)} Stage 2</h1><p><a href="stage3_input.json">Stage 3 JSON</a></p>',f'<p><a href="{esc(handoff["source_url"])}">원본 영상</a></p>','<p>원문·원본 이미지 확인용입니다. 모델 caption과 reason은 검증된 전사가 아닙니다.</p>']
    by_asset={a['asset_id']:a for a in handoff['assets']}
    for u in handoff['units']:
        rows += [f'<section id="u{u["unit_index"]}"><h2>{esc(u["unit_id"])}</h2><p>{esc(u["visual_status"])} / {esc(u["coverage_status"])}</p>']
        rows += [f'<p>{esc(s["text"])}</p>' for s in u['sentences']]
        for ar in u['asset_refs']:
            a=by_asset[ar['asset_id']];url=esc(relative(a['image']['path']))
            rows += [f'<figure><a href="{url}"><img src="{url}" alt="{esc(ar["caption"])}"></a><figcaption>{esc(ar["caption"])} · 실제 영상 {esc(ar["actual_video_seconds"])}초 · <a href="{url}">원본 크기</a></figcaption></figure>']
        rows += [f'<pre>{esc(u["omission_reasons"])}</pre></section>']
    rows += ['<h2>경고</h2>',f'<pre>{esc(handoff["warnings"])}</pre>','</html>']
    return '\n'.join(rows).encode()

def all_refs(value):
    if isinstance(value,dict):
        if 'path' in value and 'sha256' in value:yield value
        for v in value.values():yield from all_refs(v)
    elif isinstance(value,list):
        for v in value:yield from all_refs(v)

def validate(ctx,docs,t3,handoff):
    def require(ok,message):
        if not ok:raise VisionError(message,kind='validation')
    s=docs['sentences']['payload'];t1=docs['t1']['payload'];t2=docs['t2']['payload'];fd=docs['frames']['payload'];t4=docs['t4']['payload'];t5=docs['t5']['payload']
    for key,d in docs.items():
        require(d['schema_version']==f'extractube.stage2.{SUFFIXES[key]}.v1' and d['policy_id']==POLICIES[key] and d['video_id']==ctx.video_id and d['run_id']==ctx.run_id and d['input_digest']==ctx.input['input_digest'],'단계 envelope identity 오류입니다.')
    for d in [ctx.input,ctx.config,*docs.values(),*t3.values(),handoff]:
        for reference in all_refs(d):ctx.hashes.check(ctx.work,reference)
    raw=read(ctx.hashes.check(ctx.work,ctx.input['sentences_ref']))
    from .visual_input import flatten
    from .visual_input import CHAIN
    canonical,_=flatten(raw)
    require(canonical==s['sentences'] and raw['omitted_chunks']==s['omitted_chunks'],'canonical 문장이 원본과 다릅니다.')
    meta=read(ctx.work/'metadata.json');records=meta.get('results',{})
    require(meta.get('video_id')==ctx.video_id,'현재 Stage 1 video ID가 다릅니다.')
    from extractube.store import identity
    for kind,parents in CHAIN.items():
        record=records.get(kind,{})
        require(record.get('kind')==kind and identity(record)==ctx.input['stage1_identities'][kind],'현재 Stage 1 identity가 다릅니다.')
        ctx.hashes.check(ctx.work,record)
        require(record.get('upstream')==[identity(records[p]) for p in parents],'현재 Stage 1 chain이 다릅니다.')
    semantic.validate_units(canonical,t1['units'])
    require(handoff.get('omitted_audio_chunks')==s['omitted_chunks'],'handoff 생략 음성 provenance가 다릅니다.')
    require(handoff['source'].get('video_stream_start_seconds')==seconds(ctx.input['stream']['start_time']) and handoff['source'].get('stream_index')==ctx.input['stream']['index'],'handoff stream 기준이 다릅니다.')
    require(len(handoff['units'])==len(t1['units']),'handoff 단위 수가 다릅니다.')
    offset=decimal(ctx.input['audio_to_video_offset_seconds'])
    for unit,exported in zip(t1['units'],handoff['units']):
        ar,vr=semantic.ranges(canonical,unit['sentence_start'],unit['sentence_end'],offset)
        require(unit['audio_range']==ar and unit['video_range']==vr and exported['audio_range']==ar and exported['video_range']==vr,'단위 시각이 다릅니다.')
        expected=[{k:canonical[i][k] for k in ('sentence_index','source_chunk_index','text','language','audio_start','audio_end')} for i in unit['sentence_indices']]
        require(exported['sentences']==expected and exported['unit_id']==unit['unit_id'],'handoff 원문/단위 identity가 다릅니다.')
    # Reparse every completed source response through the current task parser.
    entries={}
    for document in [*docs.values(),*t3.values()]:
        def visit(value):
            if isinstance(value,dict):
                if set(('job_key','source_run_id','result'))<=set(value):
                    entry_path=ctx.hashes.check(ctx.work,value['result']);entry=read(entry_path)
                    if 'call' in entry:
                        request=read(ctx.hashes.check(ctx.work,entry['request']));inference.validate_entry(ctx,entry,request['spec']);entries[value['job_key']]=entry
                for v in value.values():visit(v)
            elif isinstance(value,list):
                for v in value:visit(v)
        visit(document)
    for ur in t2['units']:
        unit=next(u for u in t1['units'] if u['unit_id']==ur['unit_id'])
        ranges=[]
        for k,c in enumerate(ur['chunks']):
            a,z=c['sentence_start'],c['sentence_end'];ranges.extend(range(a,z))
            require(c['context_before_indices']==list(range(max(0,a-2),a)) and c['context_after_indices']==list(range(z,min(len(canonical),z+2))),'T2 CTX2 범위 오류입니다.')
            ar,vr=semantic.ranges(canonical,a,z,offset)
            require(c['audio_range']==ar and c['video_range']==vr,'T2 시각이 다릅니다.')
            if c['job_ref']:
                e=entries[c['job_ref']['job_key']]
                if c['candidate_source']=='budget_guard':require(e['status']=='budget_exhausted' and c['decision'] is None and not c['model_inference_success'],'guard provenance 오류입니다.')
                else:require(e['parsed']=={q:c[q] for q in ('decision','reason','evidence_sentence_indices')},'T2 저장 결정이 raw와 다릅니다.')
        require(ranges==unit['sentence_indices'],'T2 단위 문장 범위 누락/중복입니다.')
    chunks_by_id={c['chunk_id']:c for u in t2['units'] for c in u['chunks']}
    expected_candidates={cid for cid,c in chunks_by_id.items() if c['decision'] in ('needed','uncertain') or c['candidate_source']=='budget_guard'}
    require({c['candidate_id'] for c in t2['candidates']}==expected_candidates,'T2 후보 누락/추가입니다.')
    for c in t2['candidates']:
        chunk=chunks_by_id[c['candidate_id']]
        require(c['unit_id']==chunk['unit_id'] and c['requested_video_range']==chunk['video_range'] and c['sentence_start']==chunk['sentence_start'] and c['sentence_end']==chunk['sentence_end'],'T2 후보 범위/단위 오류입니다.')
        r=c['requested_video_range'];valid=0<=decimal(r['start'])<decimal(r['end'])<=decimal(ctx.input['stream']['duration'])
        require(c['range_status']==('valid' if valid else 'invalid') and c['effective_video_range']==(r if valid else None),'T2 range clamp/상태 오류입니다.')
    require(set(t3)=={c['candidate_id'] for c in t2['candidates'] if c['range_status']=='valid'},'T3 처리 후보 누락/추가입니다.')
    for cid,document in t3.items():
        p=document['payload']
        if document['execution_status']=='failed':continue
        require(changes.replay(p)==p['differences'],'B05 재생 결과가 다릅니다.')
        candidate=next(c for c in t2['candidates'] if c['candidate_id']==cid);r=candidate['effective_video_range'];samples=p['samples']
        require([v['sample_index'] for v in samples]==list(range(len(samples))),'sample index 오류입니다.')
        times=[actual(v['pts'],v['time_base'],ctx.input['stream']['start_time']) for v in samples]
        require(all(decimal(r['start'])<=t<decimal(r['end']) for t in times) and all(a<z for a,z in zip(times,times[1:])),'T3 PTS 순서/후보 포함 오류입니다.')
        edges=[decimal(r['start'])]+[decimal(b['video_seconds']) for b in p['boundaries']]+[decimal(r['end'])]
        require(len(p['segments'])==len(edges)-1,'T3 segment 수 오류입니다.')
        native=[{**v,'video_seconds':decimal(v['video_seconds'])} for v in samples]
        expected_bounds=changes.plain(changes.b._boundaries(changes.replay(p),native,decimal(r['start']),decimal(r['end'])))
        for i,bound in enumerate(expected_bounds):bound['boundary_id']=f'{cid}:b{i}'
        require(expected_bounds==p['boundaries'],'T3 경계 재생 오류입니다.')
        grids=changes.b.grid_requests(decimal(r['start']),decimal(r['end']))
        require(len(p['requests'])==len(grids)+1,'T3 요청 수 오류입니다.')
        for i,request in enumerate(p['requests']):
            require(request['request_index']==i and request['kind']==('grid' if i<len(grids) else 'tail'),'T3 요청 index/kind 오류입니다.')
            if request['status']=='ok':
                si=request['sample_index'];require(type(si) is int and 0<=si<len(samples) and i in samples[si]['request_indices'],'T3 요청/sample 연결 오류입니다.')
                if i<len(grids):require(times[si]>=grids[i] and request['requested_video_seconds']==seconds(grids[i]),'T3 requested/actual 오류입니다.')
                else:require(si==len(samples)-1 and 'tail' in samples[si]['roles'],'T3 tail 오류입니다.')
        assigned=[]
        for segment,a,z in zip(p['segments'],edges,edges[1:]):
            require(a<z and decimal(segment['video_range']['start'])==a and decimal(segment['video_range']['end'])==z,'구간 분할 오류입니다.')
            assigned.extend(segment['sample_indices'])
        require(sorted(assigned)==list(range(len(samples))),'sample 단일 segment 소속 오류입니다.')
    fm={f['frame_id']:f for f in fd['frames']};am={a['association_id']:a for a in fd['associations']}
    require(len(am)==len(fd['associations']) and len(fm)==len(fd['frames']),'중복 frame/association입니다.')
    expected_associations={cid+'@'+s['frame_id']:(cid,s) for cid,d in t3.items() if d['execution_status']!='failed' for s in d['payload']['samples']}
    require(set(am)==set(expected_associations),'T4 실제 sample association 누락/추가입니다.')
    for aid,a in am.items():
        cid,sample=expected_associations[aid];candidate=next(c for c in t2['candidates'] if c['candidate_id']==cid)
        require(a['candidate_id']==cid and a['unit_id']==candidate['unit_id'] and a['frame_id']==sample['frame_id'] and a['source_sample_indices']==[sample['sample_index']] and a['source_request_indices']==sample['request_indices'],'T4 association 원본 연결 오류입니다.')
        if a['extraction_status']=='ok':require(a['frame_id'] in fm,'추출 성공 frame 누락입니다.')
    expected_t5={aid for aid,a in am.items() if a['t4_status']=='success' and a['t4_decision'] in ('keep','uncertain')}
    require({d['association_id'] for d in t5['decisions']}==expected_t5 and len(t5['decisions'])==len(expected_t5),'T5 대상 누락/추가입니다.')
    for f in fm.values():
        frames.check_image(ctx,f['full']);frames.check_image(ctx,f['vlm'])
        require(f['source_video']==ctx.input['stage1_identities']['video'] and f['video_seconds']==seconds(actual(f['pts'],f['time_base'],ctx.input['stream']['start_time'])),'physical frame 출처/시각 오류입니다.')
    for task,document in [('t4',t4),('t5',t5)]:
        for batch in document['batches']:
            require(1<=len(batch['target_ids'])<=4 and len(batch['association_ids'])==len(batch['target_ids']),'batch 크기 오류입니다.')
            require(all(am[a]['candidate_id']==batch['candidate_id'] and am[a]['frame_id']==fid for a,fid in zip(batch['association_ids'],batch['target_ids'])),'association batch 연결 오류입니다.')
            if batch['status']=='success':
                parsed=entries[batch['job_ref']['job_key']]['parsed']['frames']
                for aid,row in zip(batch['association_ids'],parsed):
                    if task=='t4':require(am[aid]['t4_decision']==row['decision'] and am[aid]['t4_reason']==row['reason'],'T4 raw 결정 불일치입니다.')
                    else:
                        stored=next(d for d in t5['decisions'] if d['association_id']==aid)
                        require(all(stored[k]==v for k,v in row.items()),'T5 raw 결정 불일치입니다.')
    expected_visuals=assets.visuals(t1['units'],t2['candidates'],fd['associations'],t5['decisions'],fm,t2,t3)
    require(t5['unit_visuals']==expected_visuals,'단위 coverage 상태가 다릅니다.')
    require(handoff['assets']==t5['assets'] and handoff['usage_policy']==USAGE_POLICY,'최종 자산/사용 정책이 다릅니다.')
    use={d['association_id']:d for d in t5['decisions'] if d['decision']=='use'}
    exported_associations=[]
    for asset in handoff['assets']:
        require(asset['kind']=='original_frame' and asset['transform']=='none' and asset['verified_text']==[] and asset['latex'] is None and asset['table_html'] is None and asset['regions']==[],'자산에 미검증 전사/변형이 있습니다.')
        require(asset['frame_id'] in fm and asset['image']==fm[asset['frame_id']]['full'] and asset['asset_id']==asset['frame_id'],'최종 자산이 원본 PNG가 아닙니다.')
        for aid in asset['association_ids']:
            require(aid in use and am[aid]['frame_id']==asset['frame_id'],'use 외 자산 승격입니다.');exported_associations.append(aid)
            a=am[aid];c=next(c for c in t2['candidates'] if c['candidate_id']==a['candidate_id']);u=next(u for u in t1['units'] if u['unit_id']==a['unit_id']);f=fm[a['frame_id']];t=actual(f['pts'],f['time_base'],ctx.input['stream']['start_time'])
            require(all(decimal(r['start'])<=t<decimal(r['end']) for r in (c['effective_video_range'],u['video_range'])),'단위 밖 자산입니다.')
    require(len(exported_associations)==len(set(exported_associations)) and set(exported_associations)==set(use),'use 자산 대응 누락/중복입니다.')
    for u,v in zip(handoff['units'],expected_visuals):require(all(u[k]==v[k] for k in ('candidate_ids','asset_refs','visual_status','coverage_status','omission_reasons','warnings')),'handoff 단위 시각 연결이 다릅니다.')
    require(handoff['coverage']==coverage(s,t1,t2,t3,fd,t4,t5),'coverage 집계가 다릅니다.')
    return {'schema_version':'extractube.stage2.validation.v1','ok':True,'quality_review_performed':False,'checks':['source_identity','source_text','unit_coverage','raw_response_reparse','b05_replay','png_decode','asset_use_only','coverage_recomputed'],'counts':handoff['coverage']}

FILES={'sentences':'sentences.json','t1':'t1/semantic_units.json','trace':'t1/trace.json','t2':'t2/visual_candidates.json','frames':'t4/frames.json','t4':'t4/decisions.json','t5':'t5/visual_assets.json'}

def verify(video_id,*,data_root=Path('data'),run_id=None):
    from extractube.media import validate_video_id
    from extractube.store import WorkLock
    work=Path(data_root)/validate_video_id(video_id)
    lock=WorkLock(work)
    try:return verify_locked(work,video_id,run_id)
    finally:lock.release()

def verify_locked(work,video_id,run_id=None):
    current=read(safe(work,'vision/current.json')) if (work/'vision/current.json').is_file() else None
    if run_id is None:
        if current is None:raise VisionError('성공한 Stage 2 current가 없습니다.',kind='validation')
        run_id=current['run_id']
    if not isinstance(run_id,str) or not RUN.fullmatch(run_id):raise VisionError('run ID 형식 오류입니다.',kind='usage')
    directory=safe(work,f'vision/runs/{run_id}');hashes=HashCache()
    if current and current['run_id']==run_id:hashes.check(work,current['result'])
    result=read(directory/'result.json')
    if not result.get('stage3_usable'):raise VisionError('미완료 run에는 유효한 handoff가 없습니다.',kind='validation')
    for reference in result['artifacts'].values():hashes.check(work,reference)
    for reference in result['t3_artifacts'].values():hashes.check(work,reference)
    docs={k:read(directory/p) for k,p in FILES.items()}
    t3={cid:read(hashes.check(work,r)) for cid,r in result['t3_artifacts'].items()}
    ctx=SimpleNamespace(work=work,directory=directory,run_id=run_id,video_id=video_id,input=read(directory/'input.json'),config=read(directory/'config.json'),hashes=hashes)
    handoff=read(hashes.check(work,result['handoff']));validation=validate(ctx,docs,t3,handoff)
    calls,elapsed,reserved=inference.budget_from_journals(directory)
    if calls!=result['budget']['http_calls'] or abs(elapsed-result['budget']['http_seconds'])>0.000001 or reserved:
        raise VisionError('누적 예산이 journal과 다릅니다.',kind='validation')
    if result['counts']!=handoff['coverage'] or result['execution_status']!=handoff['execution_status']:
        raise VisionError('result 집계/상태 불일치입니다.',kind='validation')
    # Internal static links must exist, external URLs are preserved as source attribution.
    text=(directory/'review.html').read_text()
    for url in re.findall(r'(?:src|href)="([^"]+)"',text):
        url=html.unescape(url)
        if '://' not in url and not url.startswith('#'):
            p=(directory/url).resolve()
            try:p.relative_to(work.resolve())
            except ValueError as exc:raise VisionError('HTML 경로 탈출입니다.',kind='validation') from exc
            if not p.is_file():raise VisionError('HTML 링크가 없습니다.',kind='validation')
    return validation
