"""T5 E02 preparation/execution: source-owned span editing with bounded audit output.
One runner; immutable E00/E01 evidence, inherited transport/tokenizer/HTML.
"""
from pathlib import Path
from collections import Counter
import argparse, copy, fcntl, hashlib, html, io, json, os, re, subprocess, sys, unicodedata, urllib.request
import run_e01 as e
b = e.b
ROOT, HERE, BASE = e.ROOT, e.HERE, e.BASE
POLICY = "E02.source_owned_span_audit.v2"
MAX_EDITS, MAX_SUPPORTS = 8, 2
MAX_DRAFT, MAX_REPLACEMENT, MAX_QUOTE, MAX_DIAGNOSIS = 144, 192, 64, 32
CODES = ["relation", "scope", "attribution", "example", "resource", "literal"]
PROMPT = """확보된 원문과 현재 초안을 대조하는 국소 편집자다. 외부 지식으로 원문을 정정하지 않는다.
각 문단에 인접 제공된 근거와 draft_text를 대조한다. 단어·수치뿐 아니라 대상과 관계, 적용 범위, 주체와 확신 수준, 예시와 전개의 보존을 확인한다.
기존 초안에 이미 반영된 표현 변환은 새 수정이 아니다. 모호한 근거에서 새로운 의무·예외·인과·분류를 만들지 않는다.
명확한 내용을 지워 불확실성을 감추지 않는다. 의도와 역할을 가진 표현을 표준화하지 않는다. 문체만 다듬기 위해 정상 문단을 바꾸지 않는다.
문서가 실제 제공하는 기능·자료와 원본의 안내를 구분하고, 제공되지 않은 내용을 추정하지 않는다. 내부 검수 설명을 독자 본문에 쓰지 않는다.
paragraph_audits에서 모든 paragraph_ref를 입력 순서대로 keep/patch/unresolved로 판정한다. keep에는 설명을 쓰지 않는다.
patch인 문단만 edits를 반환한다. support에서 해당 문단에 제공된 segment_ref와 그 세그먼트의 정확한 인용 구절 quote를 먼저 선택한다.
draft_span은 현재 문단 안에서 정확히 한 번 나오는 비어 있지 않은 짧은 구절이어야 한다. diagnosis는 실제 원문-초안 차이를 짧게 기술한다.
그 뒤 replacement_span으로 해당 구절만 교체한다. 다른 위치의 근거를 빌리거나 근거 범위를 확장하지 않는다.
문단별 protected_anchors는 명시된 evidence_refs로 출처가 연결된 표현이다. 이 표현과 기존 수치·예시·조건·귀속을 보존한다.
unverified_anchors는 초안의 후보 표현일 뿐 검증된 근거가 아니다. 새 인용어는 support의 개별 근거로 지지되어야 한다.
승인된 visual 세그먼트는 기록된 source_indices 범위에서만 유효하다. 참조 문자열로 그 범위를 변경할 수 없다.
한 문단에 독립 문제가 여러 개면 겹치지 않는 짧은 패치를 각각 낸다. 전체 문단을 다시 쓰지 않는다.
해결 근거가 부족하면 unresolved에 남긴다. 수정 구절·인용·진단은 스키마 길이 제한 안에서 간결하게 쓰되 중요한 내용을 축약하지 않는다.
원문에 없는 정답, 인용어, URL, 보충 예시를 만들지 않는다. 코드펜스나 HTML 없이 지정 JSON만 반환한다."""
write, digest, loads = e.write, e.digest, e.loads

def norm(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))

def paragraph_rows(units, jobs, amendments):
    result = []
    for u in units:
        dossier = jobs[u["unit_id"]]["unit"]
        source = {s["sentence_index"]:s for s in dossier["required_source_sentences"]}
        previous = {s["sentence_index"]:s for s in dossier["previous_context"]["sentences"]}
        for p in u["paragraphs"]:
            ids = set(p["source_indices"])
            segments = [{"segment_ref":"s:"+str(i),"kind":"source","source_indices":[i],"text":source[i]["text"]} for i in p["source_indices"]]
            context_ids = set()
            for link in u.get("previous_context_links",[]) + u.get("internal_source_links",[]):
                if link["from_sentence_index"] in ids:
                    context_ids.update(link["to_sentence_indices"])
            for i in sorted(context_ids):
                s = previous.get(i) or source.get(i)
                b.check(s is not None, "unbound_context")
                segments.append({"segment_ref":"c:"+str(i),"kind":"context","source_indices":[i],"text":s["text"]})
            for amendment in amendments:
                approved = set(amendment["source_indices"])
                if amendment["id"] in p.get("amendment_refs",[]) and approved.issubset(ids):
                    segments.append({"segment_ref":"v:"+amendment["id"],"kind":"visual",
                                     "source_indices":sorted(approved),"evidence_id":amendment["id"],
                                     "text":amendment.get("writer_projection",amendment["proposal"])})
            anchors, unverified = [], []
            for anchor in p.get("key_anchors",[]):
                if norm(anchor) not in norm(p["text"]): continue
                owners = [s["segment_ref"] for s in segments if norm(anchor) in norm(s["text"])]
                if owners: anchors.append({"text":anchor,"evidence_refs":owners})
                else: unverified.append(anchor)
            result.append({"paragraph_ref":u["unit_id"]+":"+str(p["paragraph_index"]),
                           "claim_mode":p.get("claim_mode","unspecified"),"source_indices":p["source_indices"],
                           "segments":segments,"protected_anchors":anchors,"unverified_anchors":unverified,
                           "cautions":[{"source_indices":c["sentence_indices"],"guidance":c["guidance"]}
                                       for c in dossier["resolved_cautions"] if ids.intersection(c["sentence_indices"])],
                           "existing_unresolved":[x for x in u.get("unresolved",[]) if ids.intersection(x["source_indices"])],
                           "draft_text":p["text"]})
    return result

def rows_for(units,jobs):
    data = b.read(b.INPUT)
    return paragraph_rows(units,jobs,data["source_amendments_applied"])

def schema(rows):
    def obj(properties):
        return {"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}
    ref = {"type":"string","enum":[p["paragraph_ref"] for p in rows]}
    segment = {"type":"string","enum":sorted({s["segment_ref"] for p in rows for s in p["segments"]})}
    return obj({
        "paragraph_audits":{"type":"array","minItems":len(rows),"maxItems":len(rows),"items":obj({
            "paragraph_ref":ref,"verdict":{"type":"string","enum":["keep","patch","unresolved"]}})},
        "edits":{"type":"array","maxItems":MAX_EDITS,"items":obj({
            "paragraph_ref":ref,
            "support":{"type":"array","minItems":1,"maxItems":MAX_SUPPORTS,"items":obj({
                "segment_ref":segment,"quote":{"type":"string","minLength":1,"maxLength":MAX_QUOTE}})},
            "draft_span":{"type":"string","minLength":1,"maxLength":MAX_DRAFT},
            "reason_code":{"type":"string","enum":CODES},
            "diagnosis":{"type":"string","minLength":1,"maxLength":MAX_DIAGNOSIS},
            "replacement_span":{"type":"string","minLength":1,"maxLength":MAX_REPLACEMENT}})},
        "unresolved":{"type":"array","maxItems":len(rows),"items":obj({
            "paragraph_ref":ref,"reason":{"type":"string","minLength":1,"maxLength":48}})}})

def validate(value, rows):
    b.check(isinstance(value,dict) and set(value)=={"paragraph_audits","edits","unresolved"},"response_keys")
    b.check(all(isinstance(value[k],list) for k in value),"response_arrays")
    maps = {p["paragraph_ref"]:p for p in rows}
    audits = value["paragraph_audits"]
    b.check(all(isinstance(a,dict) and set(a)=={"paragraph_ref","verdict"} for a in audits),"audit_keys")
    b.check([a["paragraph_ref"] for a in audits]==list(maps),"audit_completeness")
    b.check(all(a["verdict"] in ["keep","patch","unresolved"] for a in audits),"audit_verdict")
    b.check(len(value["edits"])<=MAX_EDITS,"edit_budget")
    targets, intervals = [], {}
    for edit in value["edits"]:
        b.check(isinstance(edit,dict) and set(edit)=={"paragraph_ref","support","draft_span","reason_code","diagnosis","replacement_span"},"edit_keys")
        ref = edit["paragraph_ref"]; b.check(ref in maps,"foreign_paragraph")
        p = maps[ref]; old = p["draft_text"]
        draft, replacement = edit["draft_span"], edit["replacement_span"]
        b.check(isinstance(draft,str) and draft.strip() and len(draft)<=MAX_DRAFT and old.count(draft)==1,"unique_draft_span")
        b.check(isinstance(replacement,str) and replacement.strip() and len(replacement)<=MAX_REPLACEMENT and norm(draft)!=norm(replacement),"noop_span")
        b.check(edit["reason_code"] in CODES and isinstance(edit["diagnosis"],str) and 0<len(edit["diagnosis"])<=MAX_DIAGNOSIS,"diagnosis")
        segments = {s["segment_ref"]:s for s in p["segments"]}
        support = edit["support"]; b.check(isinstance(support,list) and 1<=len(support)<=MAX_SUPPORTS,"support_count")
        used, texts = set(), []
        for item in support:
            b.check(isinstance(item,dict) and set(item)=={"segment_ref","quote"},"evidence_scope_shape")
            name, quote = item["segment_ref"], item["quote"]
            b.check(name in segments and name not in used,"evidence_ownership")
            used.add(name); s = segments[name]
            b.check(isinstance(quote,str) and quote.strip() and len(quote)<=MAX_QUOTE and norm(quote) in norm(s["text"]),"source_span_grounding")
            if s["kind"]=="visual": b.check(set(s["source_indices"]).issubset(p["source_indices"]),"visual_scope_subset")
            texts.append(s["text"])
        b.check(not re.search(r"<[/!a-zA-Z]|"+chr(96)*3,replacement),"non_plain_text")
        for name in re.findall(r"\b(?:p_u\d+|c_u\d+|[AV]\d+)\b",replacement):
            b.check(any(name in text for text in texts) or name in old,"internal_id_leak")
        for phrase in ["원문에서","전사본에서"]:
            b.check(phrase not in replacement or any(phrase in t for t in texts) or phrase in old,"meta_leak")
        for match in re.findall(r"'([^'\n]{1,100})'|「([^」]{1,100})」",replacement):
            quote = next(x for x in match if x)
            inherited_number = bool(re.fullmatch(r"\d[\d,.]*",quote)) and quote in old
            b.check(inherited_number or any(norm(quote) in norm(t) for t in texts),"ungrounded_replacement_quote")
        b.check(set(e.numbers(replacement)).issubset(set(e.numbers(old)) | set(e.numbers(" ".join(texts)))),"new_numeric_anchor")
        start = old.index(draft); end = start+len(draft)
        b.check(all(end<=a or start>=z for a,z,_ in intervals.get(ref,[])),"overlapping_spans")
        intervals.setdefault(ref,[]).append((start,end,replacement));targets.append(ref)
    patched = {}
    for ref, edits in intervals.items():
        text = maps[ref]["draft_text"]
        for start,end,replacement in sorted(edits,reverse=True):
            text = text[:start]+replacement+text[end:]
        b.check(text!=maps[ref]["draft_text"],"noop_paragraph")
        b.check(not(e.numbers(maps[ref]["draft_text"])-e.numbers(text)),"lost_numeric_anchor")
        for anchor in maps[ref]["protected_anchors"]:
            b.check(norm(anchor["text"]) in norm(text),"lost_source_owned_anchor")
        patched[ref] = text
    b.check({a["paragraph_ref"] for a in audits if a["verdict"]=="patch"}==set(targets),"audit_edit_mismatch")
    unknown = value["unresolved"]
    b.check(all(isinstance(u,dict) and set(u)=={"paragraph_ref","reason"} and u["paragraph_ref"] in maps
                and isinstance(u["reason"],str) and 0<len(u["reason"])<=48 for u in unknown),"unresolved_shape")
    b.check(len({u["paragraph_ref"] for u in unknown})==len(unknown),"duplicate_unresolved")
    b.check({u["paragraph_ref"] for u in unknown}=={a["paragraph_ref"] for a in audits if a["verdict"]=="unresolved"},"audit_unresolved_mismatch")
    return patched

def phase_scope(units):
    return [u for u in units if u["unit_id"] in e.PRIMARY] if any(u["unit_id"] in e.PRIMARY for u in units) else units

def validate_edits(value,units,jobs):
    validate(value,rows_for(units,jobs))
    return value

def apply(units,value,jobs):
    # E01's display path uses this local sentinel before any response exists.
    scope = phase_scope(units); rows = rows_for(scope,jobs)
    if value=={"edits":[],"unresolved":[]}:
        value={"paragraph_audits":[{"paragraph_ref":p["paragraph_ref"],"verdict":"keep"} for p in rows],"edits":[],"unresolved":[]}
    changes=validate(value,rows);result=copy.deepcopy(units)
    for ref,(_,p) in e.paragraph_map(result).items():
        if ref in changes:p["text"]=changes[ref]
    return result

def make_job(phase,units,jobs,amendments):
    rows=paragraph_rows(units,jobs,amendments)
    payload={"task":"compare_current_draft_to_owned_evidence","available_document_links":["original_video_only"],
             "reader_policy":"excluded","paragraphs":rows}
    body={"model":"qwen3.8-27b","messages":[{"role":"system","content":PROMPT},{"role":"user","content":e.wire(payload).decode()}],
          "temperature":0,"max_tokens":4096,"stream":False,"chat_template_kwargs":{"enable_thinking":False},
          "response_format":{"type":"json_schema","json_schema":{"name":"stage3_t5_e02_span","strict":True,"schema":schema(rows)}}}
    return {"job_id":phase,"body":body,"request_sha256":digest(body),"unit_ids":[u["unit_id"] for u in units],
            "paragraph_refs":[p["paragraph_ref"] for p in rows],"source_sentence_count":len({i for p in rows for i in p["source_indices"]})}

def output_probes(job):
    rows=loads(job["body"]["messages"][1]["content"])["paragraphs"]
    floor={"paragraph_audits":[{"paragraph_ref":p["paragraph_ref"],"verdict":"keep"} for p in rows],"edits":[],"unresolved":[]}
    probe=copy.deepcopy(floor)
    # Format/budget stress only: never sent to the model and not a correction answer.
    for i,p in enumerate(rows[:MAX_EDITS]):
        probe["paragraph_audits"][i]["verdict"]="patch"
        supports=[{"segment_ref":s["segment_ref"],"quote":s["text"][:MAX_QUOTE]} for s in p["segments"][:MAX_SUPPORTS]]
        probe["edits"].append({"paragraph_ref":p["paragraph_ref"],"support":supports,
            "draft_span":p["draft_text"][:MAX_DRAFT],"reason_code":"relation","diagnosis":"관계와 근거 범위를 대조하는 출력 길이 검사",
            "replacement_span":("대상과 관계 및 조건을 근거 범위에서 보존한다. "*8)[:MAX_REPLACEMENT]})
    return {"all_keep":e.wire(floor).decode(),"stress":e.wire(probe).decode()}

def token_plan(planned):
    source=(ROOT/"tests/stage3/T2/token_plan.py").read_text()
    code="import sys,json,io,contextlib,xgrammar\nv=json.load(sys.stdin)\nfor j in v['jobs']:xgrammar.Grammar.from_json_schema(j['body']['response_format']['json_schema']['schema'])\nsys.stdin=io.StringIO(json.dumps(v));capture=io.StringIO();ns={}\nwith contextlib.redirect_stdout(capture):exec(v['token_script'],ns)\nout=json.loads(capture.getvalue());out['output_budget']=[]\nfor j in v['jobs']:\n out['output_budget'].append({'job_id':j['job_id'],**{k:len(ns['processor'].tokenizer.encode(t,add_special_tokens=False)) for k,t in j['probes'].items()}})\nprint(json.dumps(out))"
    enriched=[{**j,"probes":output_probes(j)} for j in planned]
    r=subprocess.run(["docker","exec","-i",e.CONTAINER,"python3","-c",code],
                     input=e.wire({"jobs":enriched,"token_script":source}),capture_output=True,timeout=180)
    b.check(r.returncode==0,"cpu_token_plan:"+r.stderr.decode(errors="replace")[-1800:])
    plan=loads(r.stdout.decode())
    for job,row,budget in zip(planned,plan["jobs"],plan["output_budget"]):
        b.check(row["job_id"]==job["job_id"] and row["input_tokens"]<=12000 and row["input_tokens"]+4096+1024<=32768,"input_token_budget")
        b.check(budget["stress"]+256<=4096,"output_stress_budget:"+str(budget))
        job["token_plan"]=row
    plan["output_budget_note"]="Stress fixture measurement, not a mathematical bound on all Unicode outputs; stop/complete checks remain mandatory."
    return plan

def identity(run):
    m,r=b.read(run/"manifest.json"),b.read(run/"report.json")
    b.check(b.sha(run/"manifest.json")==r["manifest_sha256"],"manifest_changed")
    b.check(b.sha(Path(__file__))==m["runner_sha256"],"runner_changed")
    for path,h in m["protected"].items():b.check(b.sha(ROOT/path)==h,"protected_changed:"+path)
    for path,h in m["runtime_identity"].items():b.check(b.sha(path)==h,"runtime_changed:"+path)
    context=e.get_context()
    for j in m["jobs"]:b.check(digest(j["body"])==j["request_sha256"],"request_changed")
    return m,r,*context

def render(run):
    _,r,_,jobs,base,confirm=identity(run)
    article=copy.deepcopy(base);article.update(candidate="E02",policy=POLICY)
    dev=r["calls"].get("development",{})
    if dev.get("status")=="success":article["units"]=apply(base["units"],dev["parsed"],jobs)
    article["prose_modified"]=article["units"]!=base["units"]
    write(run/"article.json",article)
    (run/"article.html").write_text(b.article_html(article),encoding="utf-8")
    body="<h1>E02 원문·초안·구절 편집 대조</h1><p>준비 상태의 글은 E00와 같다. 확인 자료는 대표 글에 혼합하지 않는다.</p>"
    for phase,units in [("development",base["units"]),("confirmation",confirm)]:
        call=r["calls"].get(phase,{})
        value=call.get("parsed",{"edits":[],"unresolved":[]}) if call.get("status")=="success" else {"edits":[],"unresolved":[]}
        patched=apply(units,value,jobs)
        body+="<h2>"+phase+"</h2>"
        for old,new in zip(units,patched):
            body+="<h3>"+html.escape(old["unit_id"]+" · "+old["section_hierarchy"]["leaf_title"])+"</h3><pre>"+html.escape(
                "\n".join(f'[{s["sentence_index"]}] {s["text"]}' for s in jobs[old["unit_id"]]["unit"]["required_source_sentences"]))+"</pre>"
            for p,q in zip(old["paragraphs"],new["paragraphs"]):
                body+="<div class=review-item>"
                if p["text"]!=q["text"]:body+="<pre>"+html.escape(p["text"])+"</pre>"
                body+="<p>"+html.escape(q["text"])+"</p></div>"
    (run/"review.html").write_text(b.page("E02 검수",body),encoding="utf-8")

def verify(run):
    m,r,data,jobs,base,confirm=identity(run)
    expected=copy.deepcopy(base);expected.update(candidate="E02",policy=POLICY)
    dev=r["calls"].get("development",{})
    if dev.get("status")=="success":expected["units"]=apply(base["units"],dev["parsed"],jobs)
    expected["prose_modified"]=expected["units"]!=base["units"]
    b.check(b.read(run/"article.json")==expected,"unlogged_article_change")
    normalized=copy.deepcopy(base);b.validate(normalized,data,jobs)
    for phase,call in r["calls"].items():
        if call.get("response_sha256"):b.check(b.sha(run/(phase+".response.body"))==call["response_sha256"],"response_changed")
        if call.get("status")=="success":
            units=phase_scope(base["units"]) if phase=="development" else confirm
            validate_edits(call["parsed"],units,jobs)
    result=b.inspect_html((run/"article.html").read_text(),expected)
    result.update(protected_hashes_verified=len(m["protected"]),units=16,paragraphs=40,source_sentences=217,
                  metadata_preserved=True,quality_automatically_accepted=False)
    return result

def synthetic_rows():
    return [{"paragraph_ref":"unit:0","source_indices":[0,1],"draft_text":"'카노' 값은 12다. 절차는 그대로 둔다.",
             "segments":[{"segment_ref":"s:0","kind":"source","source_indices":[0],"text":"'카노' 값은 12다. 절차는 그대로 둔다."},
                         {"segment_ref":"v:note","kind":"visual","source_indices":[1],"text":"별도 시각 설명"}],
             "protected_anchors":[{"text":"카노","evidence_refs":["s:0"]}]}]
def selftest():
    rows=synthetic_rows();ref=rows[0]["paragraph_ref"]
    def good():
        return {"paragraph_audits":[{"paragraph_ref":ref,"verdict":"patch"}],"edits":[{
            "paragraph_ref":ref,"support":[{"segment_ref":"s:0","quote":"절차는 그대로 둔다."}],
            "draft_span":"그대로 둔다","reason_code":"relation","diagnosis":"동일 절차 표현 보존","replacement_span":"유지한다"}],"unresolved":[]}
    validate(good(),rows)
    mutations=[
        ("missing_audit",lambda v:v["paragraph_audits"].clear()),
        ("audit_edit_mismatch",lambda v:v["paragraph_audits"][0].update(verdict="keep")),
        ("noop_span",lambda v:v["edits"][0].update(replacement_span=" 그대로 둔다 ")),
        ("foreign_paragraph",lambda v:v["edits"][0].update(paragraph_ref="other:0")),
        ("foreign_evidence",lambda v:v["edits"][0]["support"][0].update(segment_ref="s:999")),
        ("scope_widening_not_overlap",lambda v:v["edits"][0]["support"][0].update(source_indices=[0,1])),
        ("invented_source_span",lambda v:v["edits"][0]["support"][0].update(quote="근거에 없는 문장")),
        ("lost_source_owned_anchor",lambda v:v["edits"][0].update(draft_span="'카노'",replacement_span="대상")),
        ("new_numeric_anchor",lambda v:v["edits"][0].update(replacement_span="999999로 유지한다")),
        ("lost_numeric_anchor",lambda v:v["edits"][0].update(draft_span="12",replacement_span="수량")),
        ("ungrounded_quote",lambda v:v["edits"][0].update(replacement_span="'카노새합성'을 유지한다")),
        ("html_injection",lambda v:v["edits"][0].update(replacement_span="<b>유지한다</b>")),
        ("overlapping_spans",lambda v:v["edits"].append(copy.deepcopy(v["edits"][0]))),
        ("unresolved_mismatch",lambda v:v["unresolved"].append({"paragraph_ref":ref,"reason":"불명확"})),
    ]
    for name,mutate in mutations:
        value=good();mutate(value)
        try:validate(value,rows)
        except (ValueError,TypeError,KeyError):pass
        else:raise AssertionError("fixture_accepted:"+name)
    # Two separate replacements use offsets in the original, not offsets after the first change.
    v=good();x=copy.deepcopy(v["edits"][0]);x.update(draft_span="절차는",replacement_span="절차 자체는");v["edits"].append(x)
    b.check("절차 자체는 유지한다" in validate(v,rows)[ref],"multiple_nonoverlapping_patches")
    renamed=copy.deepcopy(rows);renamed[0]["paragraph_ref"]="다른자료:8";renamed[0]["draft_text"]=renamed[0]["draft_text"].replace("카노","제타")
    renamed[0]["segments"][0]["text"]=renamed[0]["segments"][0]["text"].replace("카노","제타")
    renamed[0]["protected_anchors"][0]["text"]="제타";v=good();v["paragraph_audits"][0]["paragraph_ref"]=v["edits"][0]["paragraph_ref"]="다른자료:8";validate(v,renamed)
    forged=copy.deepcopy(rows);forged[0]["segments"][1]["source_indices"]=[1,3]
    v=good();v["edits"][0]["support"]=[{"segment_ref":"v:note","quote":"별도"}]
    try:validate(v,forged)
    except ValueError as exc:b.check(str(exc)=="visual_scope_subset","wrong_scope_rejection")
    else:raise AssertionError("visual_scope_subset_accepted")
    units=[{"unit_id":"unit","paragraphs":[{"paragraph_index":0,"source_indices":[0],
             "text":"카노 가짜","key_anchors":["카노","가짜"]}]}]
    jobs={"unit":{"unit":{"required_source_sentences":[{"sentence_index":0,"text":"카노"}],
                           "previous_context":{"sentences":[]},"resolved_cautions":[]}}}
    row=paragraph_rows(units,jobs,[])[0]
    b.check([a["text"] for a in row["protected_anchors"]]==["카노"] and row["unverified_anchors"]==["가짜"],"draft_is_not_anchor_proof")
    return {"rejection_checks":len(mutations)+1,"positive_checks":4,"cases":[x[0] for x in mutations]+["visual_scope_subset"],
            "semantic_quality_evidence":False,"model_calls":0}

def can_send(run,phase):
    m,r,_,_,_,_=identity(run)
    b.check(r["preflight"]["ready_to_send"] and not r.get("closed_utc"),"not_open_prepared_run")
    b.check(phase not in r["calls"],"already_reserved_no_resend")
    b.check(len(r["calls"])<2 and all(c["status"]=="success" for c in r["calls"].values()),"prior_failure_or_budget")
    if phase=="confirmation":
        b.check(r["reviews"].get("development",{}).get("decision")=="accepted" and "freeze" in r,"confirmation_gate")
        freeze=r["freeze"]
        b.check(freeze["manifest_sha256"]==b.sha(run/"manifest.json") and freeze["primary_article_sha256"]==b.sha(run/"article.json")
                and freeze["development_review_sha256"]==digest(r["reviews"]["development"]),"freeze_changed")
    return {"phase":phase,"request_sha256":next(j["request_sha256"] for j in m["jobs"] if j["job_id"]==phase),"model_calls":0}

def prepare():
    existing=list((HERE/"runs").glob("*_e02"))
    if existing:
        b.check(len(existing)==1,"multiple_e02_runs_no_budget_reset")
        run=existing[0];verify(run);b.check(not b.read(run/"report.json")["calls"],"e02_already_started")
        print(json.dumps({"prepared_reused":str(run.relative_to(ROOT)),"model_calls":0}));return
    b.verify(BASE);e01=HERE/"runs/20261105T061526Z_e01"
    for path in [BASE,e01]:
        report=b.read(path/"report.json")
        for rel,h in report["artifact_hashes"].items():b.check(b.sha(path/rel)==h,"prior_run_artifact")
    data,jobs,base,confirm=e.get_context();selected=phase_scope(base["units"])
    planned=[make_job("development",selected,jobs,data["source_amendments_applied"]),make_job("confirmation",confirm,jobs,[])]
    tests=selftest();tp=token_plan(planned)
    runtime={p:h for p,h in b.read(b.UPSTREAM/"config.json")["critical_files"].items() if "/vllm-docker-config/" in p or "/models/llm/" in p}
    for p,h in runtime.items():b.check(b.sha(p)==h,"runtime_identity_changed")
    status=json.load(urllib.request.urlopen("http://127.0.0.1:8095/v1/models",timeout=5))
    backend=next(x for x in status["data"] if x["id"]=="qwen-vlm")
    b.check(backend["status"]["ready"],"backend_not_ready_no_service_change")
    protected=b.protect()
    for p in [HERE/"run_e00.py",HERE/"run_e01.py"]+[p for d in [BASE,e01] for p in d.rglob("*") if p.is_file()]:
        protected[str(p.relative_to(ROOT))]=b.sha(p)
    run=HERE/"runs"/(e.datetime.now(e.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_e02")
    run.mkdir(parents=True,exist_ok=False)
    m={"candidate":"E02","policy":POLICY,"created_utc":e.now(),"baseline_run":str(BASE.relative_to(ROOT)),
       "runner_sha256":b.sha(Path(__file__)),"runner_source_snapshot":Path(__file__).read_text(),
       "spec_snapshot":b.SPEC.read_text(),"protected":protected,"runtime_identity":runtime,
       "limits":{"max_calls":2,"max_http_seconds":360,"request_timeout_seconds":180,"automatic_retries":0},
       "prompt_sha256":hashlib.sha256(PROMPT.encode()).hexdigest(),"jobs":planned,"token_plan":tp,
       "offline_contract_checks":tests,"normal_controls":e.CONTROLS,"confirmation_is_blind":False,
       "case_answers_or_findings_in_model_request":False,"image_policy":"E00 display retained; adoption pending actual utility",
       "prior_T5_cost":{"new_calls":b.read(e01/"report.json")["new_model_calls"],"http_seconds":b.read(e01/"report.json")["http_seconds"]}}
    write(run/"manifest.json",m)
    write(run/"report.json",{"candidate":"E02","policy":POLICY,"decision":"prepared_not_executed","created_utc":e.now(),
        "manifest_sha256":b.sha(run/"manifest.json"),"calls":{},"reviews":{},
        "preflight":{"ready_to_send":True,"backend_status":backend["status"],"offline_contract_checks":tests,
                     "generation_requests":0,"code_identity_frozen":True},
        "new_model_calls":0,"http_seconds":0,"automatic_retries":0,"unknown_calls":0,
        "E02_complete":False,"T5_complete":False,"E03_executed":False,"user_accepted":False,"final_article_user_accepted":False,
        "codex_account_usage":"not_observed"})
    render(run);v=verify(run)
    r=b.read(run/"report.json");r["technical"]=v;write(run/"report.json",r)
    print(json.dumps({"run":str(run.relative_to(ROOT)),"tokens":tp["jobs"],"output_budget":tp["output_budget"],
                      "tests":tests,"protected":len(protected),"model_calls":0},ensure_ascii=False))

def review(run,phase):
    value=loads(sys.stdin.read());m,r,_,jobs,base,confirm=identity(run)
    job=next(j for j in m["jobs"] if j["job_id"]==phase)
    expected=[u for u in base["units"] if u["unit_id"] in e.PRIMARY] if phase=="development" else confirm
    ids=[s["sentence_index"] for u in expected for s in jobs[u["unit_id"]]["unit"]["required_source_sentences"]]
    b.check(value.get("reviewed_source_indices")==ids,"review_coverage")
    b.check(type(value.get("major_unresolved")) is int and type(value.get("new_major_regressions")) is int,"review_findings_required")
    if value.get("decision")=="accepted":
        b.check(value["major_unresolved"]==0 and value["new_major_regressions"]==0,"quality_gate")
        if phase=="development":b.check(value.get("normal_controls_reviewed")==e.CONTROLS,"controls_review_required")
    sys.stdin=io.StringIO(json.dumps(value,ensure_ascii=False));e.review(run,phase)

def closeout(run):
    m,r,_,_,_,_=identity(run);r["technical"]=verify(run)
    b.check(bool(r["calls"]),"cannot_close_unexecuted_run_as_complete")
    accepted=all(r["reviews"].get(p,{}).get("decision")=="accepted" for p in ["development","confirmation"])
    if accepted:b.check(r.get("browser",{}).get("passed"),"browser_verification_required")
    r.update(decision="accepted_bounded_span_edit" if accepted else "revise",E02_complete=True,T5_complete=False,
             closed_utc=e.now(),new_model_calls=len(r["calls"]),http_seconds=round(sum(x.get("http_seconds",180) for x in r["calls"].values()),6),
             usage={k:sum((c.get("usage") or {}).get(k,0) for c in r["calls"].values()) for k in ["prompt_tokens","completion_tokens","total_tokens"]},
             unknown_calls=sum(c["status"]=="unknown" for c in r["calls"].values()))
    r["artifact_hashes"]={str(p.relative_to(run)):b.sha(p) for p in run.rglob("*") if p.is_file() and p.name!="report.json"}
    write(run/"report.json",r);print(json.dumps({"decision":r["decision"],"new_calls":r["new_model_calls"]}))

# Only this process receives the new policy. E00/E01 files and their run identities remain unchanged.
e.POLICY=POLICY;e.validate_edits=validate_edits;e.apply=apply;e.identity=identity;e.render=render;e.verify=verify

def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["selftest","prepare","verify","check-send","send","review","browser","closeout"])
    parser.add_argument("--run");parser.add_argument("--phase",choices=["development","confirmation"]);a=parser.parse_args()
    if a.action=="selftest":print(json.dumps(selftest(),ensure_ascii=False));return
    if a.action=="prepare":prepare();return
    b.check(bool(a.run),"explicit_run_required");run=Path(a.run).resolve()
    b.check(run.is_relative_to((HERE/"runs").resolve()),"run_scope")
    # Lock the stable directory inode: report.json is atomically replaced during sends.
    fd=os.open(run,os.O_RDONLY)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action in ["check-send","send"]:
            b.check(bool(a.phase),"phase_required");info=can_send(run,a.phase)
            if a.action=="check-send":print(json.dumps(info))
            else:e.send(run,a.phase)
        elif a.action=="verify":print(json.dumps(verify(run),ensure_ascii=False))
        elif a.action=="review":b.check(bool(a.phase),"phase_required");review(run,a.phase)
        elif a.action=="browser":e.browser(run)
        else:closeout(run)
    finally:os.close(fd)
if __name__=="__main__":main()
