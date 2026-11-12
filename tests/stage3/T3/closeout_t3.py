"""Archive source review and close out this bounded failed gate; never reclassify raw."""
import html,json
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import run_t3 as t
import run_compat as compat

def main():
 run=t.current();c=compat.check(run);jobs=t.read(run/'plan_R1.json');job=next(j for j in jobs if j['job_id']=='p02_R1');rec=t.read(run/'journal/p02_R1.json');t.successful_cache(run,job,rec)
 findings=[
 {'id':'F01','severity':'blocking','item_indices':[6,7],'source_indices':[24,25],'kind':'claim_strength_and_temporal_scope','evidence':'24 예전에는 ... 등록을 하면 됐었는데요 / 25 이제는 ... 보시면 돼요','observed':'과거 사실·현재 정책 사실로 명시하도록 지시하여 영상 화자의 설명을 현재 플랫폼 검증 사실로 강화한다. 25 instruction에는 현재 정책 사실을 명확히 서술한다고 직접 명시돼 있다.','required':'화자의 당시 설명과 현재 정책의 독립 검증을 구분할 것. 이 계획은 그대로 다음 단계에 사용하지 않음.'},
 {'id':'F02','severity':'blocking','item_indices':[16],'source_indices':[35,36],'kind':'wrong_source_assignment_and_uncertain_source_loss','evidence':'35 로고를 눅기를 딴다든지 해서 편집을 해서 신청을 해 보는 방법도 있고요. / 36 아니면 요새 AI 툴이 워낙 잘 나오니까 ...','observed':'35만 참조하는 item16에 AI 툴 성능·활용을 배정했다. AI는36의 내용이고35의 불확실한 편집 신청 설명은 구체적으로 보존되지 않았다. item17에서36을 다시 다뤄 AI 설명 중복도 발생한다.','required':'의심 전사35의 정보를 추정 교정하거나36으로 대체하지 말고 source별 의미 대응을 보존할 것.'},
 {'id':'F03','severity':'blocking','item_indices':[19],'source_indices':[31,32,38],'kind':'unsupported_alternative_interpretation','evidence':'31 직접 상품 ... 상품 로고 ... 라벨즈 ... / 32 그게 불가하다 하면 ... 브랜드 없음 / 38 이렇게 일단 두 부류로 구분해서 ...','observed':'두 부류를 브랜드 있음/없음 또는 직접 제작/편법 등으로 쓰도록 하여 원문에 없는 대체 분류를 열어두었다. 앞선 조건과 편법 제안을 동등한 등록 유형으로 합칠 위험이 있다.','required':'앞선 조건을 따라 두 부류의 연결을 보존하고 확인되지 않은 대안 분류를 추가하지 않을 것.'},
 {'id':'F04','severity':'minor','item_indices':[12],'source_indices':[31],'kind':'unsupported_evaluative_wording','evidence':'31 만약 내가 직접 상품을 제작해서 ... 그대로 브랜드 등록을 ...','observed':'원문에 없는 정당한이라는 평가를 붙인다. 단독으로는 국소 표현 수정 대상이나 차단 결함을 해소하지 못한다.','required':'평가어를 제외한 조건부 경로로 표현할 수 있음. 이번에는 미채택 원본을 그대로 보관.'},
 {'id':'F05','severity':'minor','item_indices':[8],'source_indices':[26,27],'kind':'mixed_language_wording','evidence':'26 모모 기업 예시 / 27 검색·브랜드 추가하기','observed':'instruction에 중국어 操作步骤가 섞였다. 단독 표현 문제이며 사실성 판단과 별도다.','required':'한국어 표현으로 다듬을 수 있으나 이번은 파생안 미채택.'},
 ]
 checks=[
 {'source_indices':[15,16,17],'result':'preserved','detail':'현재 판매자 배송과 향후 로켓그로스 입점 조건을 구별했다.'},
 {'source_indices':[18,19,20,21,22,23],'result':'mostly_preserved','detail':'화면 전환·브랜드 강조·타격 평가는 화자의 평가로 보존. 현재 정책 사실 강화는 F01로 별도 차단.'},
 {'source_indices':[26,27,28,29],'result':'preserved_with_minor_wording','detail':'모모 기업 예시, 검색·추가, 추가 서류의 인지 범위·로고/라벨 전언을 보존. 외국어 표현 F05.'},
 {'source_indices':[30,31,32,33,34,35,36],'result':'blocked','detail':'편법의 가능성과 신청 제안은 있으나35의 의미 매핑 실패 F02.31 평가어 F04.'},
 {'source_indices':[37,38,39],'result':'blocked','detail':'설명 길이·마무리 전환은 보존하지만 두 부류의 추정 분류 F03.'},
 {'source_indices':list(range(15,40)),'result':'mechanical_coverage_only','detail':'21 items가25개 source index를 모두 덮음. 인덱스 포괄이 세부 의미 보존을 보장하지 않는 실제 반례다.'},
 ]
 review={'decision':'rejected','reviewer':'Codex source comparison under user-delegated stage review','review_scope':'aligned STT text only, no new audio/video/image review','config_sha256':t.digest(c),'job_ids':['p02_R1'],'jobs':[{'job_id':'p02_R1','response_sha256':rec['response_sha256'],'reviewed_sentence_indices':list(range(15,40)),'reviewed_item_indices':list(range(len(rec['parsed']['items']))),'findings':findings,'checks':checks}], 'manual_derivative_selected':False,'reason':'multiple important meaning/source defects; not a few title/expression/boundary edits; single gate correction already consumed'}
 t.create(run/'gate_review.json',review)
 records=t.old.records(run);usage={k:sum((r.get('usage') or {}).get(k,0) for r in records) for k in ['prompt_tokens','completion_tokens','total_tokens']}
 pending=[j['job_id'] for j in jobs if j['job_id']!='p02_R1']
 closeout={'run_id':run.name,'closed_at_kst':datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul')).isoformat(),'candidate':'E01 / R1 decoder compatibility correction','execution_status':'stopped_at_development_quality_gate','decision':'stop_T3_do_not_prepare_or_execute_T4','technical_status':{'P0':'HTTP400 schema rejection, unchanged failed record','R1':'strict parsing, index coverage, order and CPU/server input token count passed'},'stage_quality_accepted':False,'t4_ready':False,'final_article_user_accepted':False,'decision_authority':'user-delegated intermediate source review','calls':len(records),'failed_calls':sum(r['status']=='failed' for r in records),'successful_transport_contract_calls':sum(r['status']=='success' for r in records),'unknown_calls':sum(r['status']=='unknown' for r in records),'max_calls':14,'http_seconds':round(sum(r['http_seconds'] for r in records),6),'max_http_seconds':4200,'usage':usage,'codex_account_usage':'not_observed','retry':0,'gate_corrections_used':1,'further_gate_corrections':0,'prompt_revisions':0,'wire_schema_compatibility_revisions':1,'manual_review_edits':0,'primary_meaning_reviewed_sentences':25,'primary_input_identity_checked_sentences':217,'confirmation_input_identity_checked_sentences':58,'primary_plans_complete':False,'confirmation_sent':False,'unexecuted_jobs':pending,'T4_inference_calls':0,'protected_file_count':len(c['critical_files']),'limitations':['T3 output is rejected and must not be consumed by T4','no whole-primary or different-video T3 quality result','same confirmation58 was used in T2; not blind','visual reader remains excluded, original V01 metadata only','initial non-strict CPU grammar check did not predict live strict decoder rejection'],'next_action_requires':'new bounded execution decision/specification; preserve cumulative journals and do not recycle unused budget as a reset'}
 t.create(run/'closeout.json',closeout)
 t.create(run/'acceptance.json',{'stage':'T3','accepted':False,'t4_ready':False,'user_accepted':False,'review_ref':'gate_review.json','review_sha256':t.sha(run/'gate_review.json'),'closeout_sha256':t.sha(run/'closeout.json'),'selected_plan':None,'no_t4_handoff':True})
 t.create(run/'archive_manifest.json',{'before_execution_document':{'path':'tests/stage3/T3/reviews/20261030_before_execution/t3_execution.md','sha256':t.sha(t.HERE/'reviews/20261030_before_execution/t3_execution.md')},'fixed_upstream_input':{'path':str(t.INPUT.relative_to(t.ROOT)),'sha256':t.INPUT_SHA},'upstream_acceptance_unchanged':True})
 esc=lambda x:html.escape(str(x),quote=True)
 css='body{font:16px/1.65 system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 18px;color:#20252a}h1,h2,h3{line-height:1.3}.note{background:#f1f5f9;padding:16px;border-radius:8px}.bad{border-left:5px solid #b91c1c;padding:10px 16px;background:#fff1f2;margin:14px 0}.minor{border-left:5px solid #b7791f;padding:10px 16px;background:#fffbeb;margin:14px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px;border-top:1px solid #cbd5e1;padding:18px 0}.idx{font-weight:700;color:#46556a}table{border-collapse:collapse;width:100%}th,td{padding:8px;text-align:left;border:1px solid #ccd3dd}code{overflow-wrap:anywhere}a{color:#075985}li{margin:8px 0}@media(max-width:700px){.pair{display:block}.pair>div{margin-bottom:16px}body{font-size:15px}}'
 def page(title,body):return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+css+'</style><body>'+body+'</body></html>'
 heading='<h1>T3 개발 게이트: 미통과</h1><p class="note">최종 블로그 글이나 채택된 계획이 아닙니다. T2 통과 인계는 유지하지만 T3는 원문 의미 검수에서 중단했습니다. T4 문서·추론은 미수행입니다.</p>'
 issues='<h2>검수 결과</h2>'
 for f in findings:issues+='<section class="'+('bad' if f['severity']=='blocking' else 'minor')+'"><h3>'+esc(f['id']+' · '+f['kind'])+'</h3><p>'+esc(f['observed'])+'</p><p>근거 index '+esc(f['source_indices'])+' · item '+esc(f['item_indices'])+'</p><p>'+esc(f['evidence'])+'</p></section>'
 compare=heading+issues+'<h2>담당 원문 → 생성 계획</h2><p>대표 영상 Fw-8hZhnH3Q의 [15,40), 25문장. 왼쪽은 보존 원문, 오른쪽은 원응답 계획의 해당 항목입니다. 모든 index 포괄은 의미 보존을 증명하지 않습니다.</p>'
 for s in job['context']['source']:
  ix=s['sentence_index'];matched=[(i,it) for i,it in enumerate(rec['parsed']['items']) if ix in it['source_indices']]
  compare+='<section class="pair"><div><span class="idx">원문 '+str(ix)+'</span><p>'+esc(s['text'])+'</p></div><div>'
  for i,it in matched:compare+='<span class="idx">item '+str(i)+' · '+esc(it['source_indices'])+'</span><p>'+esc(it['instruction'])+'</p>'
  compare+='</div></section>'
 compare+='<h2>자산·문맥·생략 후보</h2><p>이전 문맥과 생략 후보는 빈 배열입니다. V01은 defer이며 새 판독은 없습니다.</p><pre style="white-space:pre-wrap">'+esc(json.dumps(rec['parsed']['asset_plan'],ensure_ascii=False,indent=2))+'</pre><p><a href="result.html">결과 요약</a> · <a href="gate_review.json">검수 JSON</a> · <a href="responses/p02_R1.body">원응답</a> · <a href="requests/p02_R1.json">원요청</a></p>'
 (run/'review.html').write_text(page('ExtracTube T3 원문 대조 · 미통과',compare),encoding='utf-8')
 summary=heading+'<p>run <code>'+esc(run.name)+'</code></p><p>누적 '+str(closeout['calls'])+'/14 HTTP 요청 · '+str(closeout['http_seconds'])+'/4200초 · 로컬 모델 '+str(usage['total_tokens'])+'토큰. 최초 HTTP400 실패와 수정 응답의 형식 성공을 품질 판정과 분리했습니다.</p><p>오프라인 계약10개 통과, 13개 R1 strict CPU schema 통과, CPU/server 입력2080토큰 일치, 보호 파일195개 hash 유지. 검수 범위는 대표 원문25문장·21개 계획 항목입니다. 나머지11개 root와 확인58문장 계획은 전송하지 않았습니다.</p>'
 summary+='<p><a href="review.html">원문·계획 대조 보기</a> · <a href="gate_review.json">검수 근거</a> · <a href="closeout.json">마감·비용·남은 범위</a> · <a href="acceptance.json">미채택 판정</a></p>'+issues
 summary+='<h2>E00 원문 보존 기준안과 실행 범위</h2><p>E00는 신규 추론0회인 T2 구조·원문 매핑입니다. 다음 표는 T3 처리 성공이나 완료된 콘텐츠 계획이 아닙니다.</p><table><tr><th>root</th><th>담당 범위</th><th>제목</th><th>T3 상태</th></tr>'
 for i,n in enumerate(t.read(run/'input.json')['primary_structure']['sections']):summary+='<tr><td>'+str(i)+'</td><td>'+esc([n['sentence_start'],n['sentence_end']])+'</td><td>'+esc(n['title'])+'</td><td>'+('게이트 의미 검수 실패' if i==2 else '미전송')+'</td></tr>'
 summary+='</table><h2>후속 인계</h2><p>채택된 T3 계획과 T4 입력은 없습니다. 중요한 왜곡을 검수 파생안으로 덮어 통과시키지 않았습니다. 추가 실험 전에는 이번 실패와 누적 예산을 반영한 제한 실행 판단이 필요합니다. T4 실제 추론은0회이며 최종 글의 채택은 사용자에게 있습니다.</p>'
 (run/'result.html').write_text(page('ExtracTube T3 결과 · 개발 게이트 미통과',summary),encoding='utf-8')
 print(json.dumps(closeout,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
