"""Offline, frozen, stratified review of E02. Never calls inference or edits E02."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'tests/revision/R1/runs/20261111_e02'
OUT = ROOT / 'tests/revision/R1/runs/20261114_e03_audit'
SEED = 'R1-E03-20261114-frozen-e02-v1'
EXTRA_EXCLUSIONS = {1: ['u0037-p000'],
                    2: ['u0001-p000', 'u0013-p000', 'u0047-p000'],
                    10: ['u0005-p000']}


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def repo_path(name):
    """Resolve recorded repo-relative paths on both Windows and Linux."""
    relative = Path(name.replace('\\', '/'))
    assert not relative.anchor and not PureWindowsPath(relative).drive and '..' not in relative.parts
    return ROOT / relative


def write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def paragraphs(a):
    return [p for s in a['sections'] for p in s['paragraphs']]


def without_figures(a):
    b = copy.deepcopy(a)
    b.pop('asset_placements', None)
    for p in paragraphs(b):
        p.pop('figures')
    return b


def frame_path(entry, frame):
    # All evidence is read-only and hash-bound. Avoid thousands of slow UNC
    # reparse-point resolutions; reject absolute/traversal paths lexically.
    relative = Path(frame['image']['path'])
    assert not relative.is_absolute() and '..' not in relative.parts
    return ROOT / entry['work'] / relative


def choose(video, pool, excluded):
    available = [p for p in pool if p['paragraph_id'] not in excluded and p['figures']]
    rank = lambda p: hashlib.sha256(
        f"{SEED}:{video}:{p['paragraph_id']}".encode()).hexdigest()
    multi = sorted([p for p in available if len(p['figures']) >= 2], key=rank)
    single = sorted([p for p in available if len(p['figures']) == 1], key=rank)
    if not multi:
        raise ValueError('No untouched multi-image paragraph; do not silently alter strata')
    picked = [(multi[0], 'multi')]
    if single:
        picked.append((single[0], 'single'))
    elif len(multi) >= 2:
        picked.append((multi[1], 'multi_fallback_no_single'))
    else:
        raise ValueError('Insufficient untouched paragraphs; no silent resampling')
    return picked, {'multi': len(multi), 'single': len(single)}


def plan():
    target = OUT / 'plan.json'
    if target.exists():
        raise FileExistsError('Frozen plan exists; inspect/reuse, never reroll')
    manifest = read(BASE / 'manifest.json')
    samples, population, files = [], [], {}
    for e in manifest['entries']:
        number = e['number']
        oldpath = ROOT / e['run'] / 'article.json'
        old = read(oldpath)
        afterpath = BASE / f'comparison/{number:02}/article.json'
        after = read(afterpath)
        amap = {p['paragraph_id']: p for p in paragraphs(after)}
        excluded = set(EXTRA_EXCLUSIONS.get(number, []))
        for s in manifest['samples'].values():
            if s['number'] == number:
                excluded.update(s['paragraphs'])
        picked, counts = choose(e['video'], paragraphs(old), excluded)
        population.append(dict(number=number, paragraphs=len(paragraphs(old)),
                               excluded=sorted(excluded), eligible=counts))
        for p, stratum in picked:
            pid = p['paragraph_id']
            selected_path = BASE / 'selected' / e['video'] / f'{pid}.json'
            samples.append(dict(id=f'{number:02}-{pid}', number=number, video=e['video'],
                                paragraph_id=pid, stratum=stratum,
                                before=len(p['figures']), after=len(amap[pid]['figures']),
                                asset_ids=[f['asset_id'] for f in p['figures']],
                                selection_sha256=sha(selected_path)))
        for p in [oldpath, afterpath, ROOT / e['run'] / 'source_bundle.json']:
            files[str(p.relative_to(ROOT))] = sha(p)
    total = sum(s['before'] for s in samples)
    pages = sum((s['before'] + 3) // 4 for s in samples)
    assert len(samples) == 20
    assert total <= 120 and pages <= 32, 'Budget exceeded; stop without rerolling'
    write(target, dict(seed=SEED, policy='frozen E02; no selection changes',
                       sampling='one multi + one single per video; absent single => second multi; SHA256 rank',
                       budget=dict(samples=20, original_frames=120, sheets=32, zooms=24,
                                   inference_calls=0, model_judge_calls=0),
                       actual=dict(samples=len(samples), original_frames=total, sheets=pages),
                       exclusions='R1 dev/confirm + 20261112 reviewed targets + R2 dev/confirm targets',
                       population=population, input_files=files, samples=samples,
                       manifest_sha256=sha(BASE / 'manifest.json'), runner_sha256=sha(Path(__file__))))
    print(json.dumps(dict(actual=dict(samples=20, frames=total, sheets=pages),
                          samples=[{k:s[k] for k in ('id','stratum','before','after')} for s in samples])))


def prepare(verify_only=False):
    planpath = OUT / 'plan.json'
    frozen = read(planpath)
    assert frozen['manifest_sha256'] == sha(BASE / 'manifest.json')
    for name, expected in frozen['input_files'].items():
        assert sha(repo_path(name)) == expected, name
    manifest = read(BASE / 'manifest.json')
    structural, packets, jobs, texts = [], [], [], []
    checked_images = {}
    for e in manifest['entries']:
        print(f"Verifying video {e['number']:02}", flush=True)
        oldpath = ROOT / e['run'] / 'article.json'
        sourcepath = ROOT / e['run'] / 'source_bundle.json'
        assert sha(oldpath) == e['article_sha256']
        assert sha(sourcepath) == e['source_sha256']
        assert sha(ROOT / e['run'] / 'article.html') == e['html_sha256']
        old = read(oldpath)
        after = read(BASE / f"comparison/{e['number']:02}/article.json")
        assert without_figures(old) == without_figures(after)
        op, ap = paragraphs(old), paragraphs(after)
        if verify_only:
            population = next(p for p in frozen['population'] if p['number'] == e['number'])
            picked, counts = choose(e['video'], op, set(population['excluded']))
            saved = [s for s in frozen['samples'] if s['number'] == e['number']]
            assert counts == population['eligible']
            assert [(p['paragraph_id'], stratum) for p, stratum in picked] == [
                (s['paragraph_id'], s['stratum']) for s in saved], 'Frozen sample drift'
        assert len(op) == len(ap)
        omitted_count = 0
        for p, q in zip(op, ap):
            assert p['paragraph_id'] == q['paragraph_id']
            pid = p['paragraph_id']
            selection = read(BASE / 'selected' / e['video'] / f'{pid}.json')
            kept = selection['kept']
            assert kept == [f['asset_id'] for f in q['figures']]
            original_map = {f['asset_id']: f for f in p['figures']}
            assert len(original_map) == len(p['figures'])
            assert len(set(kept)) == len(kept) and set(kept) <= set(original_map)
            for f in q['figures']:
                assert f == original_map[f['asset_id']]
            decisions = {d['asset_id']: d for d in selection['decisions']}
            if len(p['figures']) >= 2:
                assert set(decisions) == set(original_map)
            for aid, d in decisions.items():
                assert (aid in kept) == (d['decision'] != 'omit')
                if d['decision'] == 'omit':
                    assert d['final_covered_by'] in kept
                    omitted_count += 1
            for f in p['figures']:
                path = frame_path(e, f)
                if str(path) not in checked_images:
                    checked_images[str(path)] = sha(path)
                assert checked_images[str(path)] == f['image']['sha256']
        structural.append(dict(number=e['number'], paragraphs=len(op),
                               before=sum(len(p['figures']) for p in op),
                               after=sum(len(p['figures']) for p in ap),
                               omitted=omitted_count, status='pass_structure_not_semantics'))
        sources = read(sourcepath)['sentences']
        for sample in [s for s in frozen['samples'] if s['number'] == e['number']]:
            pid = sample['paragraph_id']
            index = next(i for i,p in enumerate(op) if p['paragraph_id'] == pid)
            p, q = op[index], ap[index]
            sp = BASE / 'selected' / e['video'] / f'{pid}.json'
            assert sha(sp) == sample['selection_sha256']
            packet = dict(**sample, paragraph=p, after_paragraph=q,
                          source=[dict(index=i,text=sources[i]['text']) for i in p['source_indices']],
                          context=[dict(paragraph_id=x['paragraph_id'],text=x['text'])
                                   for i,x in enumerate(op) if abs(i-index)==1],
                          decisions=read(sp)['decisions'])
            packets.append(packet)
            framejobs=[]
            for i,f in enumerate(p['figures']):
                path=frame_path(e, f)
                framejobs.append(dict(index=i, path=str(path), sha256=f['image']['sha256'],
                                      asset_id=f['asset_id'],
                                      label=f"{sample['id']} [{i}] {'KEEP' if f['asset_id'] in sample_kept(q) else 'OMIT'} {f['asset_id'].split(':')[-1]}"))
            for start in range(0,len(framejobs),4):
                jobs.append(dict(output=str(OUT/'sheets'/f"{sample['id']}-{start//4:02}.jpg"),
                                 frames=framejobs[start:start+4]))
            texts.append(dict(id=sample['id'],before=sample['before'],after=sample['after'],
                              text=p['text'],source=packet['source'],context=packet['context']))
    if verify_only:
        saved = read(OUT/'structural.json')
        assert saved['plan_sha256'] == sha(planpath)
        assert structural == saved['videos']
        assert len(checked_images) == saved['unique_images_hash_verified']
        assert packets == read(OUT/'packets.json'), 'Prepared evidence drift'
        assert texts == read(OUT/'reading.json')
        assert frozen['seed'] == SEED
        assert frozen['runner_sha256'] == sha(OUT/'runner_at_plan.py')
        summary = read(OUT/'summary.json')
        assert summary['plan_sha256'] == sha(planpath)
        assert summary['review_results_sha256'] == sha(OUT/'review_results.json')
        assert summary['report_sha256'] == sha(OUT/'review.html')
        assert sha(repo_path(summary['output'])) == summary['report_sha256']
        for job in jobs:
            assert Path(job['output']).is_file(), 'Missing contact sheet'
        assert len(jobs) == frozen['actual']['sheets']
        print(json.dumps(dict(mode='read_only_verify', status='pass_structure_not_semantics',
                              videos=len(structural), samples=len(packets),
                              frames=sum(s['before'] for s in packets),
                              images_hash_verified=len(checked_images),
                              frozen_sample_reproduced=True, evidence_unchanged=True,
                              inference_calls=0, writes=0)))
        return
    write(OUT/'structural.json', dict(plan_sha256=sha(planpath), runner_sha256=sha(Path(__file__)), videos=structural,
                                     unique_images_hash_verified=len(checked_images)))
    write(OUT/'packets.json', packets)
    write(OUT/'sheet_jobs.json',jobs)
    write(OUT/'reading.json',texts)
    # The review page contains original images, not resized evidence substitutes.
    page=['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>R1 E03 — E02 층화 무작위 검수</title><style>body{font:16px/1.65 system-ui;max-width:1200px;margin:auto;padding:24px}section{border-top:2px solid #888;padding:16px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}img{width:100%}figure{margin:0;padding:8px;border:2px solid #999}.omit{border-color:#b85}summary{cursor:pointer}a{overflow-wrap:anywhere}</style><h1>R1 E03 — 고정 E02 선정 결과 검수</h1><p>10개 영상 × 2구간. 전체 글 통독·새 영상 검증이 아님. 원문·본문·이미지 원본과 삭제/대체 근거를 대조한다. 추가 추론 0회. 판정은 아래 검수 기록 참조.</p><div id="findings"></div>']
    for packet in packets:
        esc=html.escape
        page.append(f"<section id='{packet['id']}'><h2>{packet['id']} — {packet['before']} → {packet['after']}장</h2><p>{esc(packet['paragraph']['text'])}</p><details><summary>인접 본문 — 읽기 전용</summary>" + ''.join(f"<p>{esc(c['paragraph_id'])}: {esc(c['text'])}</p>" for c in packet['context'])+'</details><h3>대응 원문</h3>'+''.join(f"<p>[{s['index']}] {esc(s['text'])}</p>" for s in packet['source'])+'<div class="grid">')
        kept=sample_kept(packet['after_paragraph'])
        for i,f in enumerate(packet['paragraph']['figures']):
            path=ROOT/manifest['entries'][packet['number']-1]['work']/f['image']['path']
            data=base64.b64encode(path.read_bytes()).decode()
            status='유지' if f['asset_id'] in kept else '제외'
            page.append(f"<figure class='{'keep' if status=='유지' else 'omit'}'><figcaption>[{i}] {status} {esc(f['asset_id'])}</figcaption><a href='data:image/png;base64,{data}' target='_blank'><img src='data:image/png;base64,{data}'></a><p>{esc(f['caption'])}</p></figure>")
        page.append('</div><details><summary>모델 선정 사유 — 검증된 사실 아님</summary><pre>'+esc(json.dumps(packet['decisions'],ensure_ascii=False,indent=2))+'</pre></details></section>')
    page.append('</html>')
    (OUT/'review.html').write_text('\n'.join(page),encoding='utf-8')
    print(json.dumps(dict(videos=len(structural),samples=len(packets),sheets=len(jobs),unique_images=len(checked_images))))


def sample_kept(p):
    return {f['asset_id'] for f in p['figures']}


def finalize():
    from collections import Counter
    from html.parser import HTMLParser
    results = read(OUT/'review_results.json')
    frozen = read(OUT/'plan.json')
    structural = read(OUT/'structural.json')
    packets = read(OUT/'packets.json')
    inventory = read(OUT/'sheet_inventory.json')
    assert structural['plan_sha256'] == sha(OUT/'plan.json')
    assert read(OUT/'plan.json')['runner_sha256'] == sha(OUT/'runner_at_plan.py')
    assert {s['id'] for s in results['samples']} == {s['id'] for s in frozen['samples']}
    assert len(results['samples']) == len(frozen['samples']) == 20
    assert len(inventory) == results['usage']['contact_sheets_viewed'] == 27
    assert len(read(OUT/'zoom_plan.json')) == results['usage']['original_zoom_views'] == 12
    for name, expected in frozen['input_files'].items():
        assert sha(repo_path(name)) == expected, name
    # A compact embedded sheet view avoids duplicating 60 full PNG data URLs.
    esc=html.escape
    labels=dict(no_selection_issue_observed='선정 문제 미발견',relevance_boundary='관련성 경계',
                revise_relevance='불필요 그림 잔존',uncertain_visual_relation='관계 보존 불확실',
                revise_information_loss='필수 시각 정보 손실')
    counts=dict(Counter(s['status'] for s in results['samples']))
    header='<p>10개 영상 · 20구간 · 원래 그림 60장 → 26장 · contact sheet 27장 및 원본 확대 12회 검수 · 추가 추론 0회</p><p><strong>E02 채택 보류(revise).</strong> 전체 구조 검사는 통과했지만, 표본에서 필수 시각 정보 손실 1구간과 불필요 그림 잔존 1구간이 확인됐다. 관계 보존 불확실 1구간·관련성 경계 1구간은 통과에 포함하지 않았다. 나머지 16구간은 선정상 명백한 문제 미발견이며 전체 품질 승인이 아니다.</p>'
    page=['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>R1 E03 — E02 일반화 표본 검수</title><style>body{font:16px/1.7 system-ui;max-width:1100px;margin:auto;padding:24px;color:#222;background:#fff}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #bbb;padding:8px;text-align:left}section{border-top:2px solid #999;padding:24px 0}img{width:100%;height:auto}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}.finding{padding:12px;background:#eef2f6}.issue{background:#fff0df}a{color:#1460a0}h2{scroll-margin-top:16px}</style><h1>R1 E03 — 고정 E02의 일반화 표본 검수</h1>',header,
          '<p>우선 볼 근거: <a href="#09-u0028-p000">09번 B/C 정보 손실</a> · <a href="#05-u0016-p000">05번 다른 지표의 표 잔존</a> · <a href="#06-u0008-p000">06번 관계 보존 불확실</a></p>',
          '<h2>개선 방향</h2><ol>'+''.join('<li>'+esc(t)+'</li>' for t in results['recommendation'])+'</ol>',
          '<h2>검수 범위와 한계</h2><ul>'+''.join('<li>'+esc(t)+'</li>' for t in results['limitations'])+'</ul>',
          '<p>추출 seed: '+esc(frozen['seed'])+' · 다중/단일 그림 층화 추출, 06은 단일 문단이 없어 다중 2개. 기존 개발·확인·검수 대상 제외. 결과를 보고 재추출하지 않았다.</p>',
          '<table><tr><th>구간</th><th>그림</th><th>선정 검수</th></tr>']
    smap={s['id']:s for s in results['samples']}
    for p in packets:
        s=smap[p['id']]
        page.append(f"<tr><td><a href='#{p['id']}'>{p['id']}</a></td><td>{p['before']} → {p['after']}</td><td>{labels[s['status']]}</td></tr>")
    page.append('</table>')
    for p in packets:
        s=smap[p['id']]
        css='finding' if s['status']=='no_selection_issue_observed' else 'finding issue'
        page.append(f"<section id='{p['id']}'><h2>{p['id']} — {labels[s['status']]}</h2><p><strong>보존 대상:</strong> {esc(s['information'])}</p><div class='{css}'><p>{esc(s['finding'])}</p><p>R2 경계: {esc(s['r2'])}</p>")
        for key in ['mechanism','existing_issue']:
            if key in s:
                page.append('<p>'+esc(s[key])+'</p>')
        page.append('</div><h3>고정 본문</h3><p>'+esc(p['paragraph']['text'])+'</p><details><summary>인접 본문 — 읽기 전용</summary>'+''.join('<p>'+esc(c['paragraph_id'])+': '+esc(c['text'])+'</p>' for c in p['context'])+'</details><h3>대응 원문</h3>'+''.join(f"<p>[{x['index']}] {esc(x['text'])}</p>" for x in p['source'])+'<h3>선정 전 전체 그림 — KEEP/OMIT은 E02 결과</h3><p>그림 번호는 이 구간의 원래 순서(0부터)다. 브라우저 확대 또는 이미지 새 탭 열기로 확인할 수 있다.</p>')
        for job in [j for j in inventory if j['sample']==p['id']]:
            imagepath=Path(job['sheet'])
            data=base64.b64encode(imagepath.read_bytes()).decode()
            page.append(f"<img alt='{p['id']} original frames {job['start']} to {job['start']+job['count']-1}' src='data:image/jpeg;base64,{data}'>")
        page.append('<details><summary>선정 전 캡션·자산 ID — 검증된 사실로 가정하지 않음</summary><ol start="0">'+''.join(f"<li>{esc(f['asset_id'])}: {esc(f['caption'])}</li>" for f in p['paragraph']['figures'])+'</ol></details><details><summary>모델 선정 사유 — 이미지/원문 확인 후 참고</summary><pre>'+esc(json.dumps(p['decisions'],ensure_ascii=False,indent=2))+'</pre></details></section>')
    page.append('</html>')
    content='\n'.join(page)
    class Check(HTMLParser):
        def __init__(self):
            super().__init__(); self.ids=set(); self.links=[]; self.images=0
        def handle_starttag(self, tag, attrs):
            attrs=dict(attrs)
            if 'id' in attrs:
                assert attrs['id'] not in self.ids
                self.ids.add(attrs['id'])
            if tag=='a': self.links.append(attrs['href'])
            if tag=='img':
                assert base64.b64decode(attrs['src'].split(',',1)[1]).startswith(b'\xff\xd8')
                self.images+=1
    check=Check(); check.feed(content)
    assert check.images==27 and len(check.ids)==20
    assert all(link.startswith('#') and link[1:] in check.ids for link in check.links)
    review=OUT/'review.html'; review.write_text(content,encoding='utf-8')
    output=ROOT/'tests/revision/output/r1-e03-review-20261114.html'
    output.write_bytes(review.read_bytes())
    assert sha(output)==sha(review)
    write(OUT/'summary.json',dict(status='revise',status_counts=counts,
          plan_sha256=sha(OUT/'plan.json'),review_results_sha256=sha(OUT/'review_results.json'),
          runner_sha256=sha(Path(__file__)),usage=results['usage'],
          structural_videos=len(structural['videos']),
          structural_paragraphs=sum(v['paragraphs'] for v in structural['videos']),
          hash_verified_images=structural['unique_images_hash_verified'],
          report_sha256=sha(review),output=str(output.relative_to(ROOT)),
          artifact_checks=dict(internal_links=True,embedded_images=27,copy_hash_match=True,
                               browser_render_verified=False),
          preparation_note='Initial prepare interrupted due slow UNC resolve; optimized read-only path verification. Frozen sampling and E02 unchanged; runner_at_plan.py preserves initial runner.'))
    print(json.dumps(dict(status='revise',counts=counts,html_bytes=output.stat().st_size)))


def selftest():
    assert repo_path('tests\\revision\\R1') == ROOT/'tests/revision/R1'
    for bad in ['../outside', '/outside', 'C:/outside', 'tests/../../outside']:
        try:
            repo_path(bad)
        except AssertionError:
            pass
        else:
            raise AssertionError('Path traversal accepted')
    pool=[dict(paragraph_id=str(i),figures=[{}]*(1 if i<3 else 3)) for i in range(7)]
    picked,_=choose('video',pool,{'0','3'})
    reverse,_=choose('video',list(reversed(pool)),{'0','3'})
    assert [p['paragraph_id'] for p,_ in picked] == [p['paragraph_id'] for p,_ in reverse]
    assert all(p['paragraph_id'] not in {'0','3'} for p,_ in picked)
    assert [s for _,s in picked] == ['multi','single']
    fallback,_=choose('video',pool,{'0','1','2'})
    assert len({p['paragraph_id'] for p,_ in fallback})==2
    assert fallback[1][1]=='multi_fallback_no_single'
    a={'sections':[{'paragraphs':[dict(text='fact',figures=[{'x':1}])]}]}
    b=copy.deepcopy(a); b['sections'][0]['paragraphs'][0]['figures']=[]
    assert without_figures(a)==without_figures(b)
    b['sections'][0]['paragraphs'][0]['text']='changed'
    assert without_figures(a)!=without_figures(b)
    print('PASS: portable relative paths, deterministic selection, exclusions, strata/fallback, immutable body')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action', choices=['plan','prepare','verify','finalize','selftest'])
    args=parser.parse_args()
    {'plan':plan,'prepare':prepare,'verify':lambda:prepare(verify_only=True),
     'finalize':finalize,'selftest':selftest}[args.action]()
