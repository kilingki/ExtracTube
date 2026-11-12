"""Review renderer for E03, including rejected candidates without promoting them."""
import json,html
import run_e04_plan as e
import run_t3 as t

def render():
 run=e.current();esc=lambda x:html.escape(str(x),quote=True)
 css='body{font:16px/1.65 system-ui,sans-serif;color:#20252a;max-width:1400px;margin:30px auto;padding:0 18px}h1,h2,h3{line-height:1.3}.note{background:#eef2f6;padding:16px}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;border-top:1px solid #cbd5e1;padding:18px 0}.three{grid-template-columns:repeat(3,minmax(0,1fr))}.idx{font-weight:bold;color:#46556a}.issue{border-left:5px solid #b91c1c;background:#fff1f2;padding:12px;margin:12px 0}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #cbd5e1;text-align:left}code{overflow-wrap:anywhere}a{color:#075985}@media(max-width:800px){.pair,.three{display:block}.pair>div{margin-bottom:18px}}'
 def page(title,body):return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+css+'</style><body>'+body+'</body></html>'
 close=t.read(run/'closeout.json') if (run/'closeout.json').exists() else {'decision':'검수 진행 중'}
 summary='<h1>T3 E04 실행·검수</h1><p>원문 인용·가림·수정 범위 검사는 통과했지만,인용 뒤에 같은 잘못된 키워드 분류를 다시 생성해 미통과했습니다.새30문장은 미전송이고 T3 성공 마감과 T4 진입은 보류입니다.후보 조사 후 E05 원문 보존형 CPU 계획 인계를1위로 설계했습니다.</p><p class="note">검토용 산출물이며 최종 글이 아닙니다. 판정: '+esc(close['decision'])+'</p><p><a href="review.html">원문·보정 전후 대조</a> · <a href="closeout.json">비용·마감</a> · <a href="acceptance.json">단계 판정</a></p><pre style="white-space:pre-wrap">'+esc(json.dumps(close,ensure_ascii=False,indent=2))+'</pre><table><tr><th>요청</th><th>범위</th><th>처리</th><th>검수</th></tr>'
 detail='<h1>T3 E04 원문 대조</h1><p class="note">고정 alignment STT를 대조한 기록입니다. 새 음성·화면 판독은 없습니다. 실패 후보를 표시하더라도 채택이나 원응답 복구를 의미하지 않습니다.</p>'
 for job in e.jobs(run):
  jid=job['job_id'];jp=run/'journal'/(jid+'.json');rp=run/'reviews'/(jid+'.json');rec=t.read(jp) if jp.exists() else {};r=t.read(rp) if rp.exists() else {}
  summary+='<tr><td>'+esc(jid)+'</td><td>'+esc(job['target'])+'</td><td>'+esc(rec.get('status','미전송'))+'</td><td>'+esc(r.get('decision','미검수'))+'</td></tr>'
  if not rec:continue
  detail+='<h2>'+esc(jid+' '+str(job['target']))+'</h2><p>처리: '+esc(rec['status'])+' · 검수: '+esc(r.get('decision','미검수'))+' · '+esc(rec.get('error',''))+'</p>'
  for f in r.get('findings',[]):detail+='<div class="issue"><b>'+esc(f.get('id','')+' '+f.get('severity',''))+'</b><p>'+esc(f.get('detail',''))+'</p><p>근거: '+esc(f.get('source_indices',[]))+'</p></div>'
  candidate=rec.get('parsed')
  if candidate is None:
   try:candidate,_=t.parse_response((run/'responses'/(jid+'.body')).read_bytes(),rec['http_status'],job)
   except Exception:candidate=None
  if r.get('selected_plan_ref'):candidate=t.read(run/r['selected_plan_ref'])
  old=job.get('original_plan')
  for s in job['context']['source']:
   ix=s['sentence_index'];detail+='<section class="pair'+(' three' if old else '')+'"><div><span class="idx">원문 '+str(ix)+'</span><p>'+esc(s['text'])+'</p></div>'
   variants=[('기존 계획',old),('보정 응답 후보',candidate)] if old else [('생성 응답 후보',candidate)]
   for label,plan in variants:
    detail+='<div><b>'+esc(label)+'</b>'
    if plan:
     for i,it in enumerate(plan['items']):
      if ix in it['source_indices']:detail+='<p class="idx">item '+str(i)+' '+esc(it['source_indices'])+'</p><p>'+esc(it['instruction'])+'</p>'
     for it in plan['omit_candidates']:
      if ix in it['source_indices']:detail+='<p>생략 후보: '+esc(it['reason'])+'</p>'
    else:detail+='<p>엄격한 계획 형식으로 읽을 수 없음. 원응답 참조.</p>'
    detail+='</div>'
   detail+='</section>'
  detail+='<p><a href="responses/'+jid+'.body">원응답</a> · <a href="requests/'+jid+'.json">원요청</a>'+(' · <a href="reviews/'+jid+'.json">검수 기록</a>' if rp.exists() else '')+'</p>'
 summary+='</table><p><a href="next_candidate_design.json">다음1위 후보 설계</a></p>'
 (run/'result.html').write_text(page('ExtracTube T3 E04 결과',summary),encoding='utf-8');(run/'review.html').write_text(page('ExtracTube T3 E04 원문 대조',detail),encoding='utf-8');print('rendered:'+str(run))
if __name__=='__main__':render()
