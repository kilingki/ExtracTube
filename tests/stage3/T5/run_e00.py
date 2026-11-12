"""Stage 3 T5 E00: immutable T4 prose assembly, source review and portable HTML QA.
One experiment runner; no model client, service control or product-code imports.
"""
from pathlib import Path
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import argparse
import base64
import copy
import hashlib
import json
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
UPSTREAM = ROOT / "tests/stage3/T4/runs/20261103T041601Z_e05_f48a63"
INPUT = UPSTREAM / "t5_input.json"
INPUT_SHA = "20cf3a1ae3a0df8669d094648e8cd0503c69eeb5771a3700246863dee80e696f"
SPEC = ROOT / "local/stage3/t5_execution.md"
ASSET = ROOT / "tests/stage2/T5/runs/20261028T184105Z_visual_closeout/assets/V01.png"
POLICY = "E00.unchanged_prose_time_anchored_asset_single_html.v1"

def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def write(p, value):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def check(ok, message):
    if not ok:
        raise ValueError(message)

def refs(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("path"), str) and isinstance(obj.get("sha256"), str):
            yield obj
        for value in obj.values():
            yield from refs(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from refs(value)

def inputs():
    check(sha(INPUT) == INPUT_SHA, "t5_input_identity")
    data = read(INPUT)
    check(data["T4_complete"] and data["t5_ready"], "upstream_not_ready")
    check(data["reader_policy"] == "excluded", "reader_policy")
    for ref in refs(data["upstream_refs"]):
        check(sha(ROOT / ref["path"]) == ref["sha256"], "upstream_hash:" + ref["path"])
    asset = data["visual_and_asset_state"]["deferred_assets"][0]
    check(sha(ASSET) == asset["sha256"], "asset_identity")
    jobs = {j["job_id"]: j for j in read(UPSTREAM / "plan.json")}
    # Bind every paragraph and every source sentence to the selected T4 record.
    for unit in data["selected_units"]:
        job = jobs[unit["unit_id"]]
        parsed = UPSTREAM / "parsed" / (unit["unit_id"] + ".json")
        check(sha(parsed) == unit["parsed_sha256"], "parsed_identity")
        check(read(parsed)["content"]["paragraphs"] == [
            {k: v for k, v in p.items() if k != "paragraph_index"} for p in unit["paragraphs"]
        ], "selected_paragraphs")
        for sentence in job["unit"]["required_source_sentences"]:
            ref = sentence["source_ref"]
            check(sha(ROOT / ref["path"]) == ref["sha256"], "alignment_identity")
            original = read(ROOT / ref["path"])["chunks"][sentence["sentence_index"]]
            check(original["text"] == sentence["text"], "verbatim_source")
    return data, jobs

def protect():
    files = [ROOT / "README.md", ROOT / "pyproject.toml"]
    files += list((ROOT / "src").rglob("*.py"))
    files += list((ROOT / "local").rglob("*.md"))
    for stage in ["T1", "T2", "T3", "T4"]:
        files += [p for p in (HERE.parent / stage).rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".lock"]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(files))
            if p.is_file() and p != SPEC}

def identity(run):
    manifest = read(run / "manifest.json")
    check(sha(Path(__file__)) == manifest["runner_sha256"], "runner_changed")
    for path, expected in manifest["protected"].items():
        check(sha(ROOT / path) == expected, "protected_changed:" + path)
    data, jobs = inputs()
    return manifest, data, jobs

def build(data, jobs):
    units = copy.deepcopy(data["selected_units"])
    sentences = [s for u in units for s in jobs[u["unit_id"]]["unit"]["required_source_sentences"]]
    asset = copy.deepcopy(data["visual_and_asset_state"]["deferred_assets"][0])
    eligible = [s for s in sentences if asset["legacy_unit_range"][0] <= s["sentence_index"] < asset["legacy_unit_range"][1]]
    target = min(eligible, key=lambda s: (
        max(float(s["audio_start"]) - float(asset["video_seconds"]), 0,
            float(asset["video_seconds"]) - float(s["audio_end"])),
        abs(float(s["audio_start"]) - float(asset["video_seconds"])), s["sentence_index"]))
    unit = next(u for u in units if any(target["sentence_index"] in p["source_indices"] for p in u["paragraphs"]))
    paragraph = next(p for p in unit["paragraphs"] if target["sentence_index"] in p["source_indices"])
    asset.update(path=str(ASSET.relative_to(ROOT)), placement_unit=unit["unit_id"],
                 after_paragraph_index=paragraph["paragraph_index"], anchor_sentence=target["sentence_index"],
                 placement_policy="nearest_source_time_inside_upstream_association",
                 decision="provisional_use_pending_display_review",
                 caption=asset["caption"], ocr_or_new_visual_claims=False)
    return {
        "schema_version": "extractube.stage3.t5.article.experimental.v1",
        "candidate": "E00", "policy": POLICY, "video_id": data["primary_video_id"],
        "source_url": "https://www.youtube.com/watch?v=" + data["primary_video_id"],
        "title": "쿠팡 상품 등록 절차",
        "sentence_range": data["primary_sentence_range"], "outline": data["document_outline"],
        "units": units, "assets": [asset],
        "source_amendments": data["source_amendments_applied"],
        "visual_limitations": data["visual_and_asset_state"]["visual_limitations"],
        "unreviewed_visual_scope": read(UPSTREAM / "input.json")["unreviewed_visual_scope"],
        "confirmation_mixed_into_primary": False, "prose_modified": False,
    }

def validate(article, data, jobs):
    check(article["units"] == data["selected_units"], "e00_prose_or_metadata_changed")
    check(article["video_id"] == data["primary_video_id"] and not article["confirmation_mixed_into_primary"], "mixed_video")
    check(article["outline"] == data["document_outline"], "outline_changed")
    expected = list(range(*data["primary_sentence_range"]))
    covered = []
    paragraph_ids = set()
    for u in article["units"]:
        source = jobs[u["unit_id"]]["unit"]["required_source_sentences"]
        ids = [s["sentence_index"] for s in source]
        check(ids == list(range(u["source_range"]["sentence_start"], u["source_range"]["sentence_end"])), "unit_partition")
        seen = set()
        for p in u["paragraphs"]:
            check(bool(p["text"].strip()) and p["source_indices"] == sorted(set(p["source_indices"])), "paragraph_format")
            check(set(p["source_indices"]).issubset(ids), "foreign_source")
            seen.update(p["source_indices"])
            pid = (u["unit_id"], p["paragraph_index"])
            check(pid not in paragraph_ids, "paragraph_duplicate")
            paragraph_ids.add(pid)
        check(seen == set(ids), "paragraph_source_coverage")
        covered += ids
    check(covered == expected, "whole_source_partition")
    check(article["source_amendments"] == data["source_amendments_applied"], "amendment_changed")
    check(len(article["assets"]) == 1, "asset_count")
    a = article["assets"][0]
    check(a["sha256"] == sha(ROOT / a["path"]) == data["visual_and_asset_state"]["deferred_assets"][0]["sha256"], "asset_changed")
    check(a["placement_unit"] in a["referenced_by_units"] and not a["ocr_or_new_visual_claims"], "asset_scope")
    check(any(p["paragraph_index"] == a["after_paragraph_index"] and a["anchor_sentence"] in p["source_indices"]
              for u in article["units"] if u["unit_id"] == a["placement_unit"] for p in u["paragraphs"]), "asset_anchor")
    return {"structural_ok": True, "source_sentences": len(covered), "units": len(article["units"]),
            "paragraphs": len(paragraph_ids), "root_sections": len(article["outline"]["sections"]),
            "adjacent_boundaries": len(article["units"]) - 1, "prose_changed": False,
            "meaning_quality_automatically_accepted": False}

STYLE = """
:root{color-scheme:light;--ink:#232a2e;--muted:#56666f;--rule:#dce3e6}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#fafbf9;color:var(--ink);
font-family:system-ui,-apple-system,"Malgun Gothic",sans-serif;font-size:17px;line-height:1.9;word-break:keep-all;overflow-wrap:anywhere}
main{max-width:760px;margin:auto;padding:42px 24px 64px}h1{font-size:2rem;line-height:1.35;margin:0 0 24px}
h2{font-size:1.4rem;line-height:1.5;margin:48px 0 18px;padding-top:12px;border-top:1px solid var(--rule)}
h3{font-size:1.12rem;line-height:1.6;margin:28px 0 12px}p{margin:0 0 20px}a{color:#23566b;text-underline-offset:.2em}
nav{padding:18px 22px;background:#edf2f0;border-radius:10px;margin:28px 0 36px}nav summary{cursor:pointer;font-weight:650}
nav ol{padding-left:24px;margin:12px 0 0}nav a{font-size:.92rem}figure{margin:26px 0}figure img{display:block;width:100%;height:auto}
figcaption{font-size:.85rem;line-height:1.65;color:var(--muted);margin-top:10px}button{font:inherit;font-size:.9rem;border:1px solid #899da5;
background:white;color:var(--ink);border-radius:6px;padding:8px 13px;margin:10px 0;cursor:pointer;min-height:44px}
footer{border-top:1px solid var(--rule);padding-top:20px;margin-top:40px;font-size:.88rem;color:var(--muted)}
dialog{width:calc(100vw - 16px);max-width:calc(100vw - 16px);max-height:calc(100dvh - 16px);padding:10px;border:1px solid #607580;border-radius:8px}
dialog::backdrop{background:#0009}.original-scroll{overflow:auto;max-height:calc(100dvh - 115px);overscroll-behavior:contain}
.original-scroll img{display:block;max-width:none;height:auto}.review-item{padding:18px 0;border-bottom:1px solid var(--rule)}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.8;background:#eef1ef;padding:14px}
.review-meta{font-size:14px;color:var(--muted)}
@media(max-width:480px){main{padding:26px 18px 48px}h1{font-size:1.65rem}h2{font-size:1.25rem;margin-top:36px}nav{padding:15px 18px}body{font-size:17px}}
"""
SCRIPT = """
const viewer=document.querySelector('dialog');
document.querySelectorAll('[data-original]').forEach(button=>button.addEventListener('click',()=>{
const source=document.getElementById(button.dataset.original),image=viewer.querySelector('img');
image.src=source.src;image.alt=source.alt;image.style.width=source.naturalWidth+'px';viewer.showModal();
}));
viewer.querySelector('button').addEventListener('click',()=>viewer.close());
"""
def page(title, body, script=""):
    return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>' + STYLE + '</style></head><body><main>' + body + '</main>' + ('<script>' + script + '</script>' if script else '') + '</body></html>'

def article_html(article):
    sections = article["outline"]["sections"]
    body = '<header><h1>' + escape(article["title"]) + '</h1></header><nav aria-label="목차"><details><summary>목차</summary><ol>'
    body += ''.join(f'<li><a href="#section-{i}">{escape(s["title"])}</a></li>' for i, s in enumerate(sections))
    body += '</ol></details></nav><article>'
    for i, section in enumerate(sections):
        body += f'<section id="section-{i}"><h2>{escape(section["title"])}</h2>'
        for u in article["units"]:
            if u["section_hierarchy"]["root_index"] != i:
                continue
            if section["children"]:
                body += '<h3>' + escape(u["section_hierarchy"]["leaf_title"]) + '</h3>'
            for p in u["paragraphs"]:
                body += '<p>' + escape(p["text"]) + '</p>'
                for a in article["assets"]:
                    if a["placement_unit"] == u["unit_id"] and a["after_paragraph_index"] == p["paragraph_index"]:
                        url = "data:image/png;base64," + base64.b64encode((ROOT / a["path"]).read_bytes()).decode("ascii")
                        body += '<figure><img id="figure-original" width="1920" height="1080" src="' + url + '" alt="' + escape(a["caption"], quote=True) + '"><figcaption>' + escape(a["caption"]) + '</figcaption><button type="button" data-original="figure-original">원본 크기로 보기</button></figure>'
        body += '</section>'
    body += '</article><footer><a href="' + escape(article["source_url"], quote=True) + '">원본 영상</a></footer>'
    body += '<dialog aria-label="원본 이미지"><button type="button">닫기</button><div class="original-scroll"><img alt=""></div></dialog>'
    return page(article["title"], body, SCRIPT)

class Inspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.resources, self.ids, self.links, self.paragraphs = [], [], [], []
        self.in_article = False
        self.in_p = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "article": self.in_article = True
        if tag == "p" and self.in_article:
            self.in_p = True
            self.paragraphs.append("")
        if a.get("id"): self.ids.append(a["id"])
        if tag == "a": self.links.append(a.get("href", ""))
        if tag in ("img", "script", "link") and (a.get("src") or a.get("href")):
            self.resources.append(a.get("src") or a.get("href"))
    def handle_endtag(self, tag):
        if tag == "p": self.in_p = False
        if tag == "article": self.in_article = False
    def handle_data(self, value):
        if self.in_p: self.paragraphs[-1] += value

def inspect_html(text, article):
    parser = Inspector()
    parser.feed(text)
    check(parser.paragraphs == [p["text"] for u in article["units"] for p in u["paragraphs"]], "html_prose_changed")
    check(len(parser.ids) == len(set(parser.ids)), "html_duplicate_ids")
    check(all(x[1:] in parser.ids for x in parser.links if x.startswith("#")), "broken_anchor")
    check(len(parser.resources) == 1 and parser.resources[0].startswith("data:image/png;base64,"), "external_resource")
    check(hashlib.sha256(base64.b64decode(parser.resources[0].split(",", 1)[1], validate=True)).hexdigest() == article["assets"][0]["sha256"], "embedded_image_changed")
    return {"html_paragraphs_exact": True, "embedded_png_hash_verified": True,
            "external_resource_count": 0, "internal_links_valid": True}

def review_html(article, jobs, report):
    body = '<h1>E00 원문·본문 대조</h1><p>독자용 글과 별도의 검수 자료. 문장 참조의 포괄과 의미 보존을 구분한다.</p>'
    for f in report.get("source_review", {}).get("findings", []):
        body += '<div class="review-item"><strong>' + escape(f["id"] + " / " + f["severity"] + " / " + f["unit_id"]) + '</strong><p>' + escape(f["observation"]) + '</p><p>' + escape(f["generalized_cause"]) + '</p></div>'
    for u in article["units"]:
        body += '<h2>' + escape(u["unit_id"] + " · " + u["section_hierarchy"]["leaf_title"]) + '</h2>'
        body += '<pre>' + escape('\n'.join(f'[{s["sentence_index"]}] {s["text"]}' for s in jobs[u["unit_id"]]["unit"]["required_source_sentences"])) + '</pre>'
        body += ''.join('<p>' + escape(p["text"]) + '</p>' for p in u["paragraphs"])
        body += '<p class="review-meta">' + escape(json.dumps({"unresolved": u["unresolved"], "previous_context_links": u["previous_context_links"]}, ensure_ascii=False)) + '</p>'
    return page("E00 원문·본문 대조", body)

def prepare():
    started = time.monotonic()
    data, jobs = inputs()
    run = HERE / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_e00")
    run.mkdir(parents=True, exist_ok=False)
    article = build(data, jobs)
    validation = validate(article, data, jobs)
    text = article_html(article)
    validation.update(inspect_html(text, article))
    manifest = {"candidate": "E00", "policy": POLICY, "input": str(INPUT.relative_to(ROOT)),
                "input_sha256": INPUT_SHA, "runner_sha256": sha(Path(__file__)),
                "spec_snapshot": SPEC.read_text(encoding="utf-8"), "protected": protect(),
                "limits": {"max_calls": 0, "max_http_seconds": 0, "automatic_retries": 0},
                "confirmation_processed": False, "created_utc": datetime.now(timezone.utc).isoformat()}
    write(run / "manifest.json", manifest)
    write(run / "article.json", article)
    (run / "article.html").write_text(text, encoding="utf-8")
    report = {"candidate": "E00", "decision": "pending_review", "technical": validation,
              "new_model_calls": 0, "http_seconds": 0, "automatic_retries": 0, "unknown_calls": 0,
              "prepare_wall_seconds": round(time.monotonic() - started, 6),
              "user_accepted": False, "final_article_user_accepted": False,
              "T5_complete": False, "E01_executed": False, "E02_executed": False}
    write(run / "report.json", report)
    (run / "review.html").write_text(review_html(article, jobs, report), encoding="utf-8")
    print(json.dumps({"run": str(run.relative_to(ROOT)), "technical": validation, "protected": len(manifest["protected"])}, ensure_ascii=False))

def verify(run):
    manifest, data, jobs = identity(run)
    article = read(run / "article.json")
    result = validate(article, data, jobs)
    result.update(inspect_html((run / "article.html").read_text(encoding="utf-8"), article))
    result["protected_hashes_verified"] = len(manifest["protected"])
    result["runner_identity_verified"] = True
    return result

def selftest():
    data, jobs = inputs()
    good = build(data, jobs)
    validate(good, data, jobs)
    checks = []
    for label, change in [
        ("prose_rewrite", lambda a: a["units"][0]["paragraphs"][0].update(text="미검증 대체 주장")),
        ("missing_unit", lambda a: a["units"].pop()),
        ("duplicate_unit", lambda a: a["units"].append(copy.deepcopy(a["units"][0]))),
        ("source_index_drift", lambda a: a["units"][0]["paragraphs"][0]["source_indices"].append(999)),
        ("foreign_video", lambda a: a.update(video_id="other")),
        ("amendment_loss", lambda a: a.update(source_amendments=[])),
        ("fake_visual_claim", lambda a: a["assets"][0].update(ocr_or_new_visual_claims=True)),
    ]:
        bad = copy.deepcopy(good)
        change(bad)
        try: validate(bad, data, jobs)
        except ValueError: checks.append(label)
        else: raise AssertionError("accepted_mutation:" + label)
    text = article_html(good)
    inspect_html(text, good)
    try: inspect_html(text.replace("</head>", '<script src="https://invalid.example/x.js"></script></head>'), good)
    except ValueError: checks.append("external_dependency")
    else: raise AssertionError("accepted_external_dependency")
    print(json.dumps({"offline_contract_checks": len(checks), "passed": checks, "model_calls": 0}))

BROWSER_JS = r"""
const fs=require('fs'),path=require('path'),{pathToFileURL}=require('url');
const {chromium}=require('C:/Users/MaxedSet/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const cfg=JSON.parse(process.argv[1]), browser=await chromium.launch({channel:'chrome',headless:true});
 const results=[],out={engine:'bundled Playwright / installed Chrome',viewports:results,network_requests:[],page_errors:[]};
 try{
 const ctx=await browser.newContext({offline:true}),page=await ctx.newPage();
 page.on('request',req=>{if(/^https?:/.test(req.url()))out.network_requests.push(req.url())});
 page.on('pageerror',e=>out.page_errors.push(String(e)));
 for(const width of [320,390,768,1280]){
  await page.setViewportSize({width,height:width<500?844:900});
  await page.goto(pathToFileURL(cfg.portable).href,{waitUntil:'load'});
  await page.locator('article p').last().waitFor();
  const metrics=await page.evaluate(()=>({
   viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
   bodyWidth:document.body.scrollWidth,paragraphs:document.querySelectorAll('article p').length,
   roots:document.querySelectorAll('article h2').length,children:document.querySelectorAll('article h3').length,
   bodyFont:getComputedStyle(document.body).fontSize,
   image:{loaded:document.querySelector('figure img').complete,
    naturalWidth:document.querySelector('figure img').naturalWidth,
    naturalHeight:document.querySelector('figure img').naturalHeight,
    renderedWidth:document.querySelector('figure img').getBoundingClientRect().width,
    renderedHeight:document.querySelector('figure img').getBoundingClientRect().height}
  }));
  if(metrics.documentWidth>width||metrics.bodyWidth>width||metrics.paragraphs!==40||metrics.roots!==12||!metrics.image.loaded||metrics.image.naturalWidth!==1920)throw Error('viewport_contract:'+JSON.stringify(metrics));
  const summary=page.locator('nav summary');await summary.click();await page.locator('nav a').nth(7).click();
  metrics.tocNavigated=await page.evaluate(()=>location.hash==='#section-7');
  await page.evaluate(()=>scrollTo(0,0));
  if(width===390)await page.screenshot({path:path.join(cfg.output,'mobile_top.png')});
  if(width===1280)await page.screenshot({path:path.join(cfg.output,'desktop_top.png')});
  await page.getByRole('button',{name:'원본 크기로 보기',exact:true}).scrollIntoViewIfNeeded();
  if(width===390)await page.screenshot({path:path.join(cfg.output,'mobile_figure.png')});
  await page.getByRole('button',{name:'원본 크기로 보기',exact:true}).click();
  await page.locator('dialog[open] img').evaluate(img=>img.decode());
  metrics.original=await page.evaluate(()=>{
   const d=document.querySelector('dialog'),box=d.querySelector('.original-scroll'),img=d.querySelector('img');
   box.scrollLeft=300;
   return {open:d.open,width:img.getBoundingClientRect().width,naturalWidth:img.naturalWidth,
    naturalHeight:img.naturalHeight,localScrollWorks:innerWidth>=1920||box.scrollLeft>0,
    documentWidth:document.documentElement.scrollWidth};
  });
  if(!metrics.original.open||metrics.original.width!==1920||!metrics.original.localScrollWorks||metrics.original.documentWidth>width)throw Error('original_contract');
  if(width===390)await page.screenshot({path:path.join(cfg.output,'mobile_original.png')});
  await page.keyboard.press('Escape');
  metrics.escapeCloses=await page.locator('dialog').evaluate(d=>!d.open);
  if(!metrics.tocNavigated||!metrics.escapeCloses)throw Error('interaction_contract');
  results.push(metrics);
 }
 out.browserVersion=browser.version();out.portableFileSha256=require('crypto').createHash('sha256').update(fs.readFileSync(cfg.portable)).digest('hex');
 out.offlineRelocatedCopy=true;out.passed=out.page_errors.length===0&&out.network_requests.length===0;
 console.log(JSON.stringify(out));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack);process.exit(1)});
"""

def browser(run):
    verify(run)
    output = run / "output/playwright"
    output.mkdir(parents=True, exist_ok=True)
    scratch = Path("/mnt/c/Users/Public/Documents/ESTsoft/CreatorTemp")
    check(scratch.is_dir(), "windows_scratch_missing")
    with tempfile.TemporaryDirectory(prefix="extractube-t5-e00-", dir=scratch) as tmp:
        check(Path(tmp).resolve().is_relative_to(scratch.resolve()), "scratch_scope")
        portable = Path(tmp) / "article.html"
        portable.write_bytes((run / "article.html").read_bytes())
        cfg = {"portable": str(portable).replace("/mnt/c/", "C:/"),
               "output": "\\\\wsl$\\Ubuntu" + str(output).replace("/", "\\")}
        result = subprocess.run(["/mnt/c/Program Files/nodejs/node.exe", "-e", BROWSER_JS,
                                 json.dumps(cfg)], cwd=scratch, capture_output=True, text=True, timeout=90)
        check(result.returncode == 0, "browser_failed:" + result.stderr[-3000:])
        evidence = json.loads(result.stdout)
        check(evidence["portableFileSha256"] == sha(run / "article.html") and evidence["passed"], "portable_browser_qa")
    report = read(run / "report.json")
    report["browser"] = evidence
    write(run / "report.json", report)
    print(json.dumps(evidence, ensure_ascii=False))

def add_review(run):
    _, data, jobs = identity(run)
    report = read(run / "report.json")
    review = json.load(__import__("sys").stdin)
    check(review["primary_sentence_count"] == data["primary_sentence_count"], "review_scope")
    check(len(review["unit_reviews"]) == len(data["selected_units"]), "review_units")
    check(review["audio_or_full_video_independently_verified"] is False, "review_claim")
    report["source_review"] = review
    report["decision"] = "revise" if any(f["severity"] in ("major", "blocking") for f in review["findings"]) else "reviewed"
    report["technical"].update(verify(run))
    write(run / "report.json", report)
    (run / "review.html").write_text(review_html(read(run / "article.json"), jobs, report), encoding="utf-8")

def closeout(run):
    report = read(run / "report.json")
    check("source_review" in report and report.get("browser", {}).get("passed"), "review_or_browser_missing")
    report["technical"].update(verify(run))
    report["E00_complete"] = True
    report["T5_complete"] = False
    report["artifact_hashes"] = {str(p.relative_to(run)): sha(p) for p in run.rglob("*")
                                if p.is_file() and p.name != "report.json"}
    write(run / "report.json", report)
    print(json.dumps({k: report[k] for k in ("candidate", "decision", "E00_complete", "T5_complete", "new_model_calls", "http_seconds")}, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "verify", "selftest", "browser", "review", "closeout"])
    parser.add_argument("--run")
    args = parser.parse_args()
    if args.action == "prepare": prepare(); return
    if args.action == "selftest": selftest(); return
    check(bool(args.run), "explicit_run_required")
    run = Path(args.run).resolve()
    check(run.is_relative_to((HERE / "runs").resolve()), "run_scope")
    if args.action == "verify": print(json.dumps(verify(run), ensure_ascii=False))
    elif args.action == "browser": browser(run)
    elif args.action == "review": add_review(run)
    else: closeout(run)

if __name__ == "__main__":
    main()
