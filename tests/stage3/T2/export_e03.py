"""Offline E03 failure closeout. No fence stripping or inference calls."""
from pathlib import Path
import html,json,re
import run_e03 as e
read,write,sha=e.read,e.write,e.sha
run=e.current();c=e.check_identity(run);inp=read(run/'input.json');job=read(run/'plan.json')[0];rec=read(run/'journal/dev_repair.json')
assert rec['status']=='failed' and rec['error']=='content' and rec['http_status']==200 and rec['finish_reason']=='stop'
raw=(run/'responses/dev_repair.body').read_bytes()
assert sha(run/'responses/dev_repair.body')==rec['response_sha256']
assert sha(run/'requests/dev_repair.json')==rec['request_sha256'] and read(run/'requests/dev_repair.json')==job['body']
assert rec['usage']['prompt_tokens']==job['token_plan']['input_tokens']==1807
try:e.parse_response(raw,200,job)
except ValueError as err:assert str(err)=='content'
else:raise AssertionError('failure_reclassified')
outer=e.loads(raw.decode());content=outer['choices'][0]['message']['content']
assert content.startswith('```json') and content.rstrip().endswith('```')
assert len(e.records(run))==1 and not (run/'dev_freeze.json').exists() and not (run/'dev_review.json').exists()
# Do not parse, strip, repair, merge, or promote content inside the fence.
write(run/'dev_diagnostic.json',{'decision':'rejected_format','reviewer':'Codex','raw_response_sha256':rec['response_sha256'],'fenced_content_observed':True,'inner_JSON_extracted':False,'semantic_adoption':False,'manual_observations':['First generated section title no longer claims saving; shown range stays158-168.','Replacement section in raw text extends to185 and next section starts185.'],'observations_limit':'Diagnostic reading only; no valid parsed candidate or complete semantic acceptance.','source_context_reviewed_indices':list(range(152,192)),'failed_response_resend':False})
previous=read(e.PARENT/'closeout.json');seconds=round(previous['t2_family_http_seconds']+rec['http_seconds'],6);usage={k:previous['t2_family_usage'][k]+rec['usage'][k] for k in ['prompt_tokens','completion_tokens','total_tokens']}
close={'run_id':run.name,'candidate':'E03','execution_status':'stopped_development_schema_failure','reason':'complete stop response wrapped JSON in markdown fence; strict parser rejected content','new_calls':1,'max_calls':3,'http_seconds':rec['http_seconds'],'max_http_seconds':900,'usage':rec['usage'],'prompt_revisions':0,'retries':0,'unknown_calls':0,'confirm_generation_status':'not_sent_development_prerequisite_failed','confirm_repair_status':'not_sent','fresh_confirmation_sentences':58,'raw_inner_content_promoted':False,'t2_family_calls':4,'t2_family_http_seconds':seconds,'t2_family_usage':usage,'quality_accepted':False,'user_accepted':False,'t3_ready':False,'decision':'continue_T2_with_E04','decision_authority':'user_delegated_stage_transition','next_candidate':'E04 same local repair with JSON Schema constrained output','next_candidate_status':'web_researched_and_CPU_schema_checked_not_executed'}
write(run/'closeout.json',close);write(run/'acceptance.json',{'user_accepted':False,'quality_accepted':False,'selected_for_T3':None,'t3_ready':False,'decision':'hold_T3_continue_T2_E04','decision_authority':'user_delegated','failed_output_not_salvaged':True})
write(run/'t3_handoff_draft.json',{'t3_ready':False,'selected_candidate':None,'primary_input_ref':{'path':'input.json','pointer':'/primary_input','sha256':sha(run/'input.json')},'primary_structure_ref':{'path':'input.json','pointer':'/primary_structure','sha256':sha(run/'input.json')},'source_of_primary':'E02 reviewed title patch; unchanged in E03','reason':'E03 development strict format failure; fresh confirmation not run'})
css=re.search(r'<style>(.*?)</style>',(e.PARENT/'result.html').read_text(encoding='utf-8'),re.S).group(1);esc=lambda x:html.escape(str(x))
def doc(title,body):return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+css+'</style><main>'+body+'</main></html>'
body='<h1>E03 실행 결과</h1><p class="badge">개발 형식 실패 · 확인 미실행 · T3 보류</p><section><h2>판단</h2><p>원문34문장과 기존 구조·검수 근거로 국소 보정을 한 번 요청했습니다. 응답은 정상 종료했지만 JSON을 코드 펜스로 감싸 엄격한 파서가 거부했습니다. 실패 응답에서 JSON을 추출하거나 일부를 채택하지 않았습니다.</p><p>원시 텍스트에는 저장을 첫 절 제목에서 제외하고 교체 완료 문장의 경계를185로 옮긴 흔적이 있습니다. 이는 진단상 관찰이며 내용 검수 통과나 채택을 뜻하지 않습니다.</p><p>개발 통과가 전제인 새 영상58문장 확인은 전송하지 않았습니다.</p></section><section><h2>비용</h2><p>신규1/3호출 · HTTP9.96051/900초 · 입력1807·출력350·총2157 로컬 모델 토큰 · 재시도0·프롬프트 수정0·unknown0.</p><p>T2 계열 누적4호출·HTTP104.710281초·20330토큰. Codex 계정 사용량은 미관측입니다.</p></section><section><h2>다음 후보</h2><p>E04는 같은 국소 보정 요청에 JSON Schema 출력 제약을 추가합니다. 공식 문서 조사와 설치된 XGrammar0.2.7의 CPU schema 변환을 확인했습니다. E04 추론은 아직 하지 않았으며 실제 API·의미 품질은 미검증입니다.</p><p><a href="review.html">실패 원응답과 원문 비교</a> · <a href="closeout.json">마감 기록</a> · <a href="integrity.json">무결성</a> · <a href="acceptance.json">단계 상태</a></p></section>'
(run/'result.html').write_text(doc('E03 결과',body),encoding='utf-8')
source=''.join('<div class="sentence" id="s'+str(s['sentence_index'])+'"><b>'+str(s['sentence_index'])+'</b> '+esc(s['text'])+'</div>' for s in inp['dev_sentences'])
review='<h1>E03 원문과 실패 응답</h1><p><a href="result.html">결과 요약</a></p><section><h2>원시 content 그대로</h2><p>코드 펜스를 포함한 실패 응답입니다. 아래 내용을 통과 구조로 변환하지 않았습니다.</p><pre>'+esc(content)+'</pre></section><section><h2>개발 원문40문장</h2><p>수정 허용 창은158~191이며152~157은 고정입니다.</p>'+source+'</section>'
(run/'review.html').write_text(doc('E03 원문 대조',review),encoding='utf-8')
write(run/'integrity.json',{'critical_files_unchanged':len(c['critical_files']),'frozen_input_plan_prompt_verified':True,'request_response_hash_verified':True,'strict_reparse_error':'content','CPU_and_server_input_tokens':1807,'new_tests_passed':3,'inner_JSON_extracted':False,'failed_output_promoted':False,'extra_inference_calls':0,'fresh_confirmation_not_sent':True,'old_runs_unchanged':True,'export_script_sha256':sha(Path(__file__))})
links=0
for p in [run/'result.html',run/'review.html']:
 for href in re.findall(r'href="([^"]+)"',p.read_text(encoding='utf-8')):assert (p.parent/href).exists();links+=1
integrity=read(run/'integrity.json');integrity['HTML_links_validated']=links;write(run/'integrity.json',integrity)
print(json.dumps({'run':run.name,'decision':close['decision'],'calls':1,'HTTP_seconds':rec['http_seconds'],'protected_and_critical_files_unchanged':len(c['critical_files']),'fresh_confirm_not_sent':True,'HTML_links':links},ensure_ascii=False))
