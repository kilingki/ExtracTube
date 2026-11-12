"""Review-only HTML renderer; never invokes models or edits raw results."""
import html,json
import run_e02_plan as e
import run_t3 as t

def render():
 run=e.current();jobs=t.read(run/'plan.json');esc=lambda x:html.escape(str(x),quote=True)
 css='body{font:16px/1.65 system-ui,sans-serif;color:#20252a;max-width:1400px;margin:30px auto;padding:0 18px}h1,h2,h3{line-height:1.3}.note{background:#eef2f6;padding:16px}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;border-top:1px solid #cbd5e1;padding:18px 0}.three{grid-template-columns:repeat(3,minmax(0,1fr))}.idx{font-weight:bold;color:#46556a}.issue{border-left:5px solid #b91c1c;background:#fff1f2;padding:12px;margin:12px 0}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #cbd5e1;text-align:left}code{overflow-wrap:anywhere}a{color:#075985}@media(max-width:800px){.pair,.three{display:block}.pair>div{margin-bottom:18px}}'
 def page(title,body):return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+css+'</style><body>'+body+'</body></html>'
 close=t.read(run/'closeout.json') if (run/'closeout.json').exists() else {'decision':'검수 진행 중'}
 summary='<h1>T3 E02 실행·검수 결과</h1><p class="note">검토용 산출물입니다. 최종 블로그 글이 아닙니다. 판정: '+esc(close['decision'])+'</p><p>run <code>'+esc(run.name)+'</code></p>'
 summary+='<p>개발25문장은 이전 핵심 오류를 해결하고 표현2곳을 검수 수정해 통과했습니다. 새38문장은 복합 키워드 선택·제외 왜곡과 인접 원문 출처 대응 오류로 미통과했습니다. 남은10개 root와 확인58문장은 미전송이며 T4는 보류입니다.</p><p><a href="patches/p02.json">개발 검수 수정2곳의 before/after</a> · <a href="reviews/p07.json">새 구간 차단 근거</a></p>'
 summary+='<pre style="white-space:pre-wrap">'+esc(json.dumps(close,ensure_ascii=False,indent=2))+'</pre><p><a href="review.html">원문·E01·E02 비교</a></p><table><tr><th>요청</th><th>범위</th><th>형식</th><th>검수</th></tr>'
 detail='<h1>T3 E02 원문 대조</h1><p class="note">담당 alignment STT와 생성 계획을 대조합니다. 이미지·오디오를 새로 판독한 결과가 아닙니다. 개발 게이트에는 E01 원응답도 함께 표시합니다.</p>'
 for job in jobs:
  jid=job['job_id'];jp=run/'journal'/(jid+'.json');rp=run/'reviews'/(jid+'.json')
  rec=t.read(jp) if jp.exists() else {};r=t.read(rp) if rp.exists() else {}
  summary+='<tr><td>'+esc(jid)+'</td><td>'+esc(job['target'])+'</td><td>'+esc(rec.get('status','미전송'))+'</td><td>'+esc(r.get('decision','미검수'))+'</td></tr>'
  if not jp.exists():continue
  detail+='<h2>'+esc(jid+' '+str(job['target']))+'</h2><p>검수: '+esc(r.get('decision','미검수'))+'</p>'
  for f in r.get('findings',[]):
   detail+='<div class="issue"><b>'+esc(f.get('id','')+' '+f.get('severity',''))+'</b><p>'+esc(f.get('detail',''))+'</p><p>근거: '+esc(f.get('source_indices',[]))+'</p></div>'
  if not rec.get('parsed'):detail+='<p>'+esc(rec.get('error',''))+'</p>';continue
  selected=t.read(run/r['selected_plan_ref']) if r.get('selected_plan_ref') else rec['parsed']
  before=t.read(e.PARENT/'journal/p02_R1.json')['parsed'] if jid=='p02' else None
  for s in job['context']['source']:
   ix=s['sentence_index'];detail+='<section class="pair'+(' three' if before else '')+'"><div><span class="idx">원문 '+str(ix)+'</span><p>'+esc(s['text'])+'</p></div>'
   plans=[('E01 원응답',before),('E02 선택안' if r.get('selected_plan_ref') else 'E02 원응답',selected)] if before else [('E02',selected)]
   for label,plan in plans:
    detail+='<div><b>'+esc(label)+'</b>'
    for i,it in enumerate(plan['items']):
     if ix in it['source_indices']:detail+='<p class="idx">item '+str(i)+' '+esc(it['source_indices'])+'</p><p>'+esc(it['instruction'])+'</p>'
    for it in plan['omit_candidates']:
     if ix in it['source_indices']:detail+='<p>생략 후보: '+esc(it['reason'])+'</p>'
    detail+='</div>'
   detail+='</section>'
  detail+='<h3>문맥·자산·편집 후보</h3><pre style="white-space:pre-wrap">'+esc(json.dumps({k:v for k,v in selected.items() if k!='items'},ensure_ascii=False,indent=2))+'</pre><p><a href="responses/'+jid+'.body">원응답</a> · <a href="requests/'+jid+'.json">원요청</a>'+((' · <a href="reviews/'+jid+'.json">검수 기록</a>') if rp.exists() else '')+'</p>'
 summary+='</table><p><a href="closeout.json">마감 JSON</a> · <a href="acceptance.json">채택 판정</a></p>'
 (run/'result.html').write_text(page('ExtracTube T3 E02 결과',summary),encoding='utf-8');(run/'review.html').write_text(page('ExtracTube T3 E02 원문 대조',detail),encoding='utf-8')
 print('rendered:'+str(run))
if __name__=='__main__':render()
