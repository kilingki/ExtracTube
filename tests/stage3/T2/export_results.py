"""Offline closeout and derived review rendering. Never sends inference requests."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,html,json,re
import run_t2 as runner
from contracts import parse_response,nodes
H=runner.HERE;run=runner.current();c=runner.check_identity(run);d=runner.read(run/'input.json')
read=runner.read;write=runner.write;sha=runner.sha
rs=runner.records(run)
assert len(rs)==2 and all(x['phase']=='dev' for x in rs)
checks=[]
for rec in rs:
    job=next(j for j in runner.all_jobs(run) if j['job_id']==rec['job_id'])
    request=run/'requests'/(rec['job_id']+'.json');raw=(run/'responses'/(rec['job_id']+'.body')).read_bytes()
    assert sha(request)==rec['request_sha256'] and read(request)==job['body']
    assert hashlib.sha256(raw).hexdigest()==rec['response_sha256']
    try:
        parsed,info=parse_response(raw,rec['http_status'],job)
        assert rec['status']=='success' and parsed==rec['parsed']
        strict='success'
    except ValueError as error:
        assert rec['status']=='failed' and str(error)==rec['error']
        strict=str(error)
    planned=job['token_plan']['input_tokens'];actual=rec['usage']['prompt_tokens']
    assert planned==actual
    checks.append({'job_id':rec['job_id'],'strict_reparse':strict,'request_sha256':sha(request),'response_sha256':rec['response_sha256'],'cpu_input_tokens':planned,'server_input_tokens':actual})
protected=read(run/'protected_t1_files.json')
assert all(sha(p)==h for p,h in protected.items())
job=next(j for j in runner.all_jobs(run) if j['job_id']=='R1_dev')
rec=next(x for x in rs if x['job_id']=='R1_dev')
assert runner.successful_cache(run,job,rec)==rec
bad=next(x for x in rs if x['job_id']=='P0_dev');badjob=next(j for j in runner.all_jobs(run) if j['job_id']=='P0_dev')
try:
    runner.successful_cache(run,badjob,bad)
except ValueError: blocked=True
else: raise AssertionError('failed_resend_not_blocked')
try:
    runner.freeze(run/'r1_review.json')
except ValueError as error:
    assert str(error)=='incomplete_source_review'
else: raise AssertionError('uncertain_review_advanced')
assert not (run/'freeze.json').exists()
review=read(run/'r1_review.json')
assert review['reviewed_sentence_indices']==list(range(217))
assert [x['node_id'] for x in review['nodes']]==[name for name,n in nodes(rec['parsed'])]
leaves=[ch for n in rec['parsed']['sections'] for ch in (n['children'] or [n])]
assert [i for n in leaves for i in range(n['sentence_start'],n['sentence_end'])]==list(range(217))
usage={k:sum(x['usage'][k] for x in rs) for k in ['prompt_tokens','completion_tokens','total_tokens']}
seconds=round(sum(x['http_seconds'] for x in rs),6)
now=datetime.now(timezone.utc)
closeout={'execution_status':'stopped_development_quality_gate','implementation_status':'implemented_offline_checked','finished_at':now.isoformat(),'primary_video_id':d['primary_video_id'],'new_model_calls':len(rs),'max_calls':3,'http_seconds':seconds,'max_http_seconds':900,'prompt_revisions_used':1,'prompt_revisions_max':1,'automatic_retries':0,'unknown_calls':0,'confirmation_status':'not_sent_development_quality_gate','confirmation_sentences':40,'dev_source_reviewed_sentences':217,'reviewed_nodes':20,'accepted_nodes':19,'uncertain_nodes':['s9'],'r1_root_sections':12,'r1_children':8,'r1_leaf_sections':len(leaves),'quality_accepted':False,'user_accepted':False,'selected_candidate':None,'t3_ready':False,'reason':'Uncertain title 상품 정보 제공처 at [174,180); no verified screen evidence to determine whether suspect STT was normalized into a technical label. No additional prompt revision allowed.','local_model_usage':usage,'codex_account_usage':'not_observed','recommendation':'hold_adoption_preserve_R1_for_review','input_sha256':sha(run/'input.json'),'wall_seconds_from_first_request_to_closeout':round((now-datetime.fromisoformat(rs[0]['started_at'])).total_seconds(),6),'scope_limits':['STT source comparison; no new whole-video watching','Only one supplied primary image V01; no late174 screen verification','No confirmation inference, final blog generation, over-context hierarchy or T3 execution']}
write(run/'closeout.json',closeout)
if not (run/'acceptance.json').exists(): write(run/'acceptance.json',{'user_accepted':False,'quality_accepted':False,'selected_candidate':None,'t3_ready':False,'execution_recommendation':'hold_adoption','reviewed_candidate':'R1','unresolved_nodes':['s9']})
write(run/'structure_R1_candidate.json',{'candidate':'R1','quality_status':'uncertain_title','structure':rec['parsed'],'node_reviews_ref':'r1_review.json','source_sha256':sha(run/'input.json'),'response_sha256':rec['response_sha256']})
write(run/'t3_handoff_draft.json',{'schema_version':'extractube.stage3.t2.handoff.experimental.v1','t3_ready':False,'user_accepted':False,'selected_candidate':None,'review_candidate_ref':{'path':'structure_R1_candidate.json','sha256':sha(run/'structure_R1_candidate.json')},'source_input_ref':{'path':'input.json','sha256':sha(run/'input.json')},'primary_video_id':d['primary_video_id'],'primary_sentences':d['primary_sentences'],'primary_units':d['primary_units'],'primary_assets':d['primary_assets'],'source_cautions':d['source_cautions'],'unreviewed_visual_scope':d['unreviewed_visual_scope'],'reader_policy':'excluded','model_annotations_selected':False,'visual_evidence':[],'connections':read(run/'connections.json')['connections'],'limitations':closeout['scope_limits']+['Unresolved title s9; not ready for T3.'],'confirmation_included_in_primary':False})
write(run/'integrity.json',{'request_response_checks':checks,'protected_t1_files_unchanged':len(protected),'critical_files_unchanged':len(c['critical_files']),'success_cache_reparsed':True,'cache_new_calls':0,'failed_resend_blocked':blocked,'uncertain_review_cannot_freeze':True,'primary_leaf_coverage':217,'duplicate_or_missing_sentence_indices':[],'offline_tests_passed':11,'offline_test_command':'.venv/bin/python tests/stage3/T2/run_t2.py check','export_script_sha256':sha(Path(__file__))})
e=lambda x:html.escape(str(x),quote=True)
css="""body{margin:0;background:#f5f6f8;color:#172134;font:16px/1.75 system-ui,sans-serif}main{max-width:1150px;margin:auto;padding:24px}h1{font-size:1.7rem;line-height:1.4}h2{font-size:1.25rem}a{color:#1556ad}section,article{background:white;border:1px solid #dfe3eb;border-radius:12px;padding:18px;margin:14px 0}.columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}.node{border-left:3px solid #7296ba;padding:6px 12px;margin:12px 0}.uncertain{border-color:#bd7200;background:#fff5df}.badge{background:#fff0cf;padding:3px 9px;border-radius:20px}.muted{color:#5b6474}.sentence{scroll-margin-top:20px;border-top:1px solid #edf0f3;padding:10px 0}.sentence:target{background:#fff2ad}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}th,td{border:1px solid #dfe3eb;padding:8px;text-align:left}@media(max-width:720px){main{padding:14px}.columns{grid-template-columns:1fr}h1{font-size:1.4rem}}"""
def doc(title,body): return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(title)+'</title><style>'+css+'</style><main>'+body+'</main></html>'
def range_link(a,z,label=None): return '<a href="#p'+str(a)+'">'+e(label or f'원문 [{a},{z})')+'</a>'
base=''.join('<div class="node"><b>'+e(u['unit_id'])+'</b><br>'+range_link(u['sentence_start'],u['sentence_end'])+'</div>' for u in d['primary_units'])
rmap={x['node_id']:x for x in review['nodes']}
def card(nid,n):
    r=rmap[nid];a,z=n['sentence_start'],n['sentence_end'];cl=' uncertain' if r['decision']!='accepted' else ''
    return '<div class="node'+cl+'"><b>'+e(nid+' · '+n['title'])+'</b><br>'+range_link(a,z)+' · '+e(r['decision'])+'<details><summary>대조 근거</summary><p>'+e(r['reason'])+'</p><p>'+', '.join(range_link(i,i+1,str(i)) for i in r['evidence_indices'])+'</p></details></div>'
new=''
for i,n in enumerate(rec['parsed']['sections']):
    nid='s'+str(i);new+=card(nid,n)
    new+=''.join(card(nid+'.'+str(j),ch) for j,ch in enumerate(n['children']))
source=''
for s in d['primary_sentences']:
    cautions=[x for x in d['source_cautions'] if x['sentence_index']==s['sentence_index'] and x['video_id']==s['video_id']]
    source+='<div class="sentence" id="p'+str(s['sentence_index'])+'"><span class="muted">'+e(str(s['sentence_index'])+' · '+s['audio_start']+'–'+s['audio_end']+'초')+'</span><br>'+e(s['text'])+(''.join('<details><summary>원문 주의 표시</summary>'+e(x['detail'])+'</details>' for x in cautions))+'</div>'
p0=read(run/'p0_review.json')
raw=read(run/'responses/P0_dev.body')['choices'][0]['message']['content']
body='<h1>T2 목차 구조와 원문 대조</h1><p class="badge">수정안 형식 통과 · 제목 1개 불확실 · 채택 보류</p><p>대표 STT 217문장 전체를 대조했습니다. R1은 12개 상위 섹션·8개 하위 주제·16개 leaf로 원문 전체를 보존합니다. [174,180)의 “상품 정보 제공처” 제목은 화면 근거 없이 의심 전사를 고친 표현인지 확정하지 못했습니다.</p><p><a href="result.html">실행 결과 요약</a> · <a href="r1_review.json">전체 노드 검수</a> · <a href="closeout.json">마감 기록</a> · <a href="input.json">고정 원문과 출처</a></p><div class="columns"><section><h2>T2 E00 · 기존 20개 단위</h2><p>단위 ID·범위 기준안. 새 제목 생성 없음.</p>'+base+'</section><section><h2>T2 E01 R1 · 생성된 목차</h2><p>19개 노드 accepted, 1개 uncertain. 부모와 자식은 같은 원문을 계층적으로 담당합니다.</p>'+new+'</section></div><section><details><summary>P0 원응답 진단 — 형식 실패, 채택 불가</summary><p>상위12개·하위29개로 하위 상한24개를 초과했습니다. 원응답 일부를 복구·채택하지 않았습니다.</p><pre>'+e(raw)+'</pre><pre>'+e(json.dumps(p0['issues'],ensure_ascii=False,indent=2))+'</pre></details></section><section><h2>대표 원문 전체 · 시간은 기존 audio 기준</h2><p>전체 영상 재시청·새 전사·확보하지 못한 화면 검수는 포함하지 않습니다. 아래 전사는 변경하지 않았습니다.</p>'+source+'</section>'
(run/'review.html').write_text(doc('T2 목차·원문 대조',body),encoding='utf-8')
summary='<h1>T2 실행 결과</h1><p class="badge">구현·제한 실험 완료 / 품질 게이트 중단 / 사용자 채택 전</p><section><h2>판단할 내용</h2><p>R1은 구조 형식을 통과했고 주요 설명의 묶음이 개선됐습니다. 다만 원문174의 의심 표현을 “상품 정보 제공처”로 바꾼 제목 하나는 근거가 불확실합니다. 이 제목의 실제 오답을 확정한 것은 아니며, 현재 자료로 승인하기 어렵다는 판단입니다.</p><p>프롬프트 수정 1회를 사용했으므로 확인용40문장은 전송하지 않고 종료했습니다. 선택 후보는 비워 두고 T3 준비 상태는 false로 유지했습니다.</p><p><a href="review.html">기존 단위 ↔ 새 목차 ↔ 원문 비교 열기</a></p></section><section><h2>개선된 부분</h2><ul>'+''.join('<li>'+e(x)+'</li>' for x in review['baseline_comparison']['improvements'])+'</ul></section><section><h2>호출과 검수</h2><table><tr><th>항목</th><th>결과</th></tr><tr><td>P0</td><td>하위 주제29개: 상한24개 초과로 형식 실패</td></tr><tr><td>R1</td><td>상위12개·하위8개: 형식 통과, 제목1개 uncertain</td></tr><tr><td>원문 검수</td><td>대표217문장·20개 제목/범위 전체; leaf16개가 전체 원문 담당</td></tr><tr><td>확인40문장</td><td>개발 품질 게이트 중단으로 미전송</td></tr><tr><td>실제 호출 / 상한</td><td>2 / 3회, HTTP '+str(seconds)+' / 900초</td></tr><tr><td>수정·재시도·unknown</td><td>프롬프트 수정1회, 자동 재시도0회, unknown0회</td></tr><tr><td>로컬 모델 토큰</td><td>입력 '+str(usage['prompt_tokens'])+' + 출력 '+str(usage['completion_tokens'])+' = '+str(usage['total_tokens'])+'</td></tr><tr><td>오프라인 검사</td><td>11개 통과; 요청별 CPU 토큰과 서버 입력 토큰 일치</td></tr></table></section><section><h2>검수 범위와 보존</h2><p>T1 과거 파일 '+str(len(protected))+'개와 출처·runtime 관련 파일 '+str(len(c['critical_files']))+'개의 hash가 유지됐습니다. 본문 작성·새 VLM 판독·장문 한도 초과 처리·전체 영상 검증은 수행하지 않았습니다. 모델 토큰은 Codex 계정 사용량과 별개이며 후자는 관측하지 않았습니다.</p><p><a href="closeout.json">마감</a> · <a href="integrity.json">무결성</a> · <a href="acceptance.json">채택 상태</a> · <a href="t3_handoff_draft.json">T3 인계 초안(진입 불가)</a></p></section>'
(run/'result.html').write_text(doc('T2 실행 결과',summary),encoding='utf-8')
links=0
for p in [run/'review.html',run/'result.html']:
    text=p.read_text(encoding='utf-8');ids=set(re.findall(r'id="([^"]+)"',text))
    for href in re.findall(r'href="([^"]+)"',text):
        if href.startswith('#'): assert href[1:] in ids,href
        else: assert (p.parent/href).exists(),href
        links+=1
integrity=read(run/'integrity.json');integrity['html_links_validated']=links;write(run/'integrity.json',integrity)
print(json.dumps({'run':run.name,'calls':len(rs),'http_seconds':seconds,'tokens':usage,'protected_t1_files':len(protected),'html_links':links,'status':closeout['execution_status']},ensure_ascii=False))
