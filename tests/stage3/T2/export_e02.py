"""Offline E02 review closeout; no inference calls and no historical mutations."""
from pathlib import Path
import copy,html,json,re
import run_e02 as e
from contracts import nodes,parse_response,parse_structure
read,write,sha=e.read,e.write,e.sha
run=e.current();c=e.check_identity(run);job=read(run/'plan.json');rec=read(run/'journal/E02_confirm.json');d=read(run/'input.json')
parsed,info=parse_response((run/'responses/E02_confirm.body').read_bytes(),rec['http_status'],job)
assert parsed==rec['parsed'] and rec['status']=='success'
assert sha(run/'responses/E02_confirm.body')==rec['response_sha256']
assert sha(run/'requests/E02_confirm.json')==rec['request_sha256']
assert read(run/'requests/E02_confirm.json')==job['body']
assert rec['usage']['prompt_tokens']==job['token_plan']['input_tokens']==1461
assert read(run/'input.json')==read(e.PARENT/'input.json')
original=read(e.PARENT/'journal/R1_dev.json')['parsed'];patched=read(run/'structure_E02.json')
assert e.patched_structure(original)==patched
reverted=copy.deepcopy(patched);reverted['sections'][9]['title']=e.OLD_TITLE;assert reverted==original
# Validate an exact cached response using the unchanged shared parser; this sends nothing.
assert e.old.successful_cache(run,job,rec)==rec
notes={
's0':('accepted',[152,153,154,155,156,157],[],'既存 image limitations and the speaker program introduction are grounded in this range.'),
's1':('rejected',[158,159,160,161,162,164,167],[168,170,171],'Title claims saving; actual saving instruction/example is outside [158,168).'),
's1.0':('accepted',[158,159,160,161,162],[],'Program introduction, representative-image control, upload and generation occur within the range.'),
's1.1':('rejected',[163,164,165,166,167],[168,170,171],'Image confirmation is supported but saving in title is only supported after the interval ends.'),
's2':('rejected',[168,170,171,174,177,178,179,183],[184],'Replacement procedure is split before its explicit completion instruction184.'),
's2.0':('accepted',[168,169,170,171,174,175],[176],'Saving and additional upload are supported;176 is a continuation assigned to the next child, noted as a boundary weakness.'),
's2.1':('rejected',[177,178,179,180,183],[184],'Beige deletion and save are present; explicit replacement184 is outside child and parent.'),
's3':('uncertain',[185,187,188,189,190,191],[184],'First sentence184 finishes previous replacement procedure while title discusses flexibility/free availability. Rest is supported.')}
notes['s0']=(notes['s0'][0],notes['s0'][1],notes['s0'][2],notes['s0'][3].replace('既存','Existing'))
reviews=[]
for nid,n in nodes(parsed):
 status,inside,outside,reason=notes[nid]
 assert all(n['sentence_start']<=i<n['sentence_end'] for i in inside)
 reviews.append({'node_id':nid,'title':n['title'],'range':[n['sentence_start'],n['sentence_end']],'decision':status,'within_range_evidence_indices':inside,'neighbor_evidence_indices':outside,'reason':reason})
write(run/'primary_review.json',{'reviewer':'Codex','decision':'accepted_for_primary_reviewed_patch','source_response_sha256':read(e.PARENT/'journal/R1_dev.json')['response_sha256'],'inherited_review_ref':str((e.PARENT/'r1_review.json').relative_to(e.ROOT)),'inherited_accepted_nodes':19,'fresh_reviewed_node':'s9','new_title':e.NEW_TITLE,'evidence_indices':[175,178,179],'only_one_title_changed':True,'source_and_ranges_unchanged':True,'caution_preserved':True,'scope':'primary217 only; confirmation decides continuation'})
write(run/'confirmation_review.json',{'reviewer':'Codex','response_sha256':rec['response_sha256'],'reviewed_sentence_indices':list(range(152,192)),'decision':'rejected','nodes':reviews,'root_count':4,'child_count':4,'source_coverage':40,'missing_or_duplicate_indices':[],'independent_defect_groups':[{'boundary':168,'detail':'saving claimed before saving-source range'},{'boundary':184,'detail':'replacement completion assigned to next root'}],'review_scope':'fixed STT40 sentences, not new video replay or image reading','post_confirmation_edit':False})
assert [i for n in parsed['sections'] for leaf in (n['children'] or [n]) for i in range(leaf['sentence_start'],leaf['sentence_end'])]==list(range(152,192))
oldclose=read(e.PARENT/'closeout.json');seconds=round(oldclose['http_seconds']+rec['http_seconds'],6)
usage={k:oldclose['local_model_usage'][k]+rec['usage'][k] for k in ['prompt_tokens','completion_tokens','total_tokens']}
closeout={'run_id':run.name,'candidate':'E02','parent_run':e.PARENT.name,'execution_status':'confirmation_quality_failed','primary_patch_status':'accepted_for_reviewed_primary','confirmation_transport_status':'success','confirmation_structure_status':'passed','confirmation_quality_status':'failed_boundary_title_alignment','new_calls':1,'max_new_calls':1,'http_seconds':rec['http_seconds'],'max_http_seconds':300,'usage':rec['usage'],'t2_family_calls':3,'t2_family_http_seconds':seconds,'t2_family_usage':usage,'retry':0,'prompt_revisions':0,'unknown_calls':0,'decision':'continue_T2_with_E03','decision_authority':'user_delegated_stage_transition_judgement','quality_accepted':False,'user_accepted':False,'t3_ready':False,'reason':'two boundary groups at168 and184 split the supporting saving/replacement content from titles; original confirmation preserved without edits','next_candidate':'E03 source-grounded local boundary/title repair','next_candidate_status':'researched_and_selected_not_executed'}
write(run/'closeout.json',closeout)
write(run/'acceptance.json',{'quality_accepted':False,'user_accepted':False,'t3_ready':False,'stage_transition_decision':'hold_T3_continue_T2','decision_authority':'user_delegated','selected_for_T3':None,'primary_review_candidate':'E02','confirmation_passed':False})
write(run/'t3_handoff_draft.json',{'t3_ready':False,'selected_candidate':None,'candidate_structure_ref':{'path':'structure_E02.json','sha256':sha(run/'structure_E02.json')},'primary_video_id':d['primary_video_id'],'primary_sentences':d['primary_sentences'],'primary_units':d['primary_units'],'primary_assets':d['primary_assets'],'reader_policy':'excluded','model_annotations_selected':False,'visual_evidence':[],'source_cautions':d['source_cautions'],'unreviewed_visual_scope':d['unreviewed_visual_scope'],'reason':'confirmation boundary/title mismatch; no T3 execution'})
css=re.search(r'<style>(.*?)</style>',(e.PARENT/'result.html').read_text(encoding='utf-8'),re.S).group(1)
esc=lambda x:html.escape(str(x))
def doc(title,body):return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+css+'</style><main>'+body+'</main></html>'
body='<h1>E02 추가 실험 결과</h1><p class="badge">원문 제목 수정 확인 · 다른 영상 확인 품질 실패 · T3 보류</p><section><h2>무엇을 했나</h2><p>대표 영상의 제목 한 곳만 “상품 정보 입력과 구비서류”로 바꿨고 원문217문장·모든 범위·그 밖의 제목이 동일한지 검증했습니다. 다른 영상40문장 확인을1회 수행했습니다.</p></section><section><h2>확인에서 발견한 두 경계 문제</h2><p>[158,168)의 제목은 이미지 생성과 저장을 담당하지만 실제 저장 설명은168~171에 있습니다. [168,184)의 이미지 교체 절은 실제 교체를 마무리하는184번 직전에 끝납니다. 원문40문장이 모두 배정됐어도 단계의 연결은 어긋났습니다.</p><p>확인 결과는 수정하지 않았습니다. 판단은 T3 보류, 후속 후보는 E03 원문 근거를 사용한 국소 경계·제목 보정입니다.</p><p><a href="review.html">전체 제목·경계·원문 대조</a> · <a href="confirmation_review.json">확인 검수</a></p></section><section><h2>비용과 상태</h2><p>추가1호출 · HTTP11.474503초 · 입력1461/출력402토큰 · 재시도0 · 프롬프트 수정0. T2 누적3호출·HTTP94.749771초·18173토큰입니다. 로컬 모델 토큰이며 Codex 계정 사용량은 미관측입니다.</p><p><a href="closeout.json">마감 기록</a> · <a href="integrity.json">무결성 검사</a> · <a href="acceptance.json">채택 상태</a></p></section>'
(run/'result.html').write_text(doc('E02 추가 실험 결과',body),encoding='utf-8')
rows=''
for r in reviews:
 rows+='<article><b>'+esc(r['node_id']+' · '+r['title'])+'</b><p>'+esc(str(r['range'])+' · '+r['decision'])+'</p><p>'+esc(r['reason'])+'</p><p>내부 근거: '+', '.join('<a href="#c'+str(i)+'">'+str(i)+'</a>' for i in r['within_range_evidence_indices'])+' / 인접 근거: '+', '.join('<a href="#c'+str(i)+'">'+str(i)+'</a>' for i in r['neighbor_evidence_indices'])+'</p></article>'
source=''.join('<div class="sentence" id="c'+str(s['sentence_index'])+'"><span class="muted">'+str(s['sentence_index'])+' · '+esc(s['audio_start']+'–'+s['audio_end'])+'초</span><br>'+esc(s['text'])+'</div>' for s in d['confirmation']['sentences'])
(run/'review.html').write_text(doc('E02 확인 원문 대조','<h1>E02 확인 — 40문장 전체 대조</h1><p><a href="result.html">결과 요약</a> · 확인 후 제목이나 범위를 바꾸지 않았습니다.</p>'+rows+'<section><h2>고정 원문 전체</h2>'+source+'</section>'),encoding='utf-8')
links=0
for p in [run/'result.html',run/'review.html']:
 text=p.read_text(encoding='utf-8');ids=set(re.findall(r'id="([^"]+)"',text))
 for href in re.findall(r'href="([^"]+)"',text):
  if href.startswith('#'):assert href[1:] in ids
  elif href!='integrity.json':assert (p.parent/href).exists()
  links+=1
write(run/'integrity.json',{'config_and_frozen_files_verified':True,'protected_and_critical_files_unchanged':len(c['critical_files']),'one_title_only_patch':True,'primary217_and_all_ranges_unchanged':True,'confirmation_request_identical_to_frozen_R1':True,'cpu_and_server_input_tokens':1461,'response_and_request_sha256_verified':True,'strict_response_reparse':'passed','exact_cache_reparse_via_shared_helper':True,'cache_new_calls':0,'confirmation_source_indices':list(range(152,192)),'confirmation_source_coverage':40,'post_confirmation_edit':False,'html_links_validated':links,'new_tests_passed':2,'old_acceptance_untouched':True,'export_code_sha256':sha(Path(__file__))})
print(json.dumps({'run':run.name,'status':closeout['execution_status'],'decision':closeout['decision'],'new_calls':1,'http_seconds':rec['http_seconds'],'critical_files_unchanged':len(c['critical_files']),'html_links':links},ensure_ascii=False))
