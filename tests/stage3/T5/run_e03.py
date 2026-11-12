"""T5 E03: reviewer-localized semantic repairs, not autonomous defect discovery.
Reuse E01 transport and E02 provenance/token tools; preserve all previous runs.
"""
from pathlib import Path
import argparse, contextlib, copy, fcntl, hashlib, html, io, json, os, re, sys, urllib.request
import run_e02 as q
e,b=q.e,q.b
ROOT,HERE,BASE=q.ROOT,q.HERE,q.BASE
POLICY="E03.review_localized_semantic_repair.v1"
MAX_REPLACEMENT,MAX_REASON=240,80
PROMPT="""검수로 위치가 지정된 문제 구절을 담당 원문에 근거해 수정한다. 자동 오류 발견 실험이 아니다.
각 ticket의 problem_kind와 draft_span은 검수 입력이며 정답 문장은 주어지지 않는다. 현재 문단·주변 근거를 대조하여 그 의미 문제만 해결하라.
원문은 실제 발화의 전사 기록이며 외부 사실의 절대 정답이 아니다. 파손된 표기와 의도적으로 보존해야 할 표현, 승인된 관찰을 구분한다.
수치의 대상·관계, 조건의 적용 범위, 주체와 확신 수준, 예시와 자료 지시를 보존한다. 모호한 근거에서 새 규칙이나 단정을 만들지 않는다.
현재 문단의 숫자 표기와 문제 밖의 어휘를 유지하라. 숫자/한글 표기나 문어체/전사체를 서로 바꾸는 것만으로는 의미 문제를 고친 것이 아니다.
source/context/visual은 각각 출처와 범위가 연결된 근거다. inherited_numeric_forms는 기존 표시 형식으로, 그 값의 외부 사실 정확성을 새로 검증했다는 뜻은 아니다.
source_owned_anchors는 증거가 연결된 표현이다. 이를 유지하고 다른 올바른 설명·예시·조건을 삭제하지 않는다.
원시 전사에 같은 철자가 없다는 이유만으로 기존 표현을 되돌리지 않는다. 동시에 기존 초안에 있다는 이유만으로 그 의미가 옳다고 가정하지 않는다.
ticket에 지정된 draft_span만 교체할 수 있다. replacement는 앞뒤 문장에 문법적으로 이어지는 독립 설명문 구절로 쓴다.
정답을 확보하지 못하면 unresolved, 검수 문제를 근거상 확인할 수 없으면 keep으로 판단하고 이유를 남긴다. 근거 없는 수정으로 문제를 숨기지 않는다.
각 ticket_id를 입력 순서대로 한 번씩 반환한다. replace에는 실제 대체 구절과 해당 ticket의 evidence_refs를, keep/unresolved에는 빈 replacement를 반환한다.
같은 근거 ID를 반복하지 말고, 근거가 허용하는 범위를 넓히지 않는다. 정답 문장·새 URL·보충 사실·내부 검수 경고를 본문에 추가하지 않는다.
readonly_context는 문맥용으로 수정 권한이 없다. 지정 JSON만 출력한다."""
KINDS={"F01":"relation","F02":"scope","F03":"scope","F04":"attribution","F05":"resource","F06":"example","F07":"example"}
write,digest,loads=e.write,e.digest,e.loads

def enclosing_sentence(text,evidence):
    candidates=[s for s in re.split(r"(?<=[.!?])\s+",text) if evidence in s]
    b.check(len(candidates)==1,"unbound_review_span")
    return candidates[0]

def tickets_for(units):
    maps=e.paragraph_map(units)
    if any(u["unit_id"] in e.PRIMARY for u in units):
        findings=b.read(BASE/"report.json")["source_review"]["findings"];result=[]
        for f in findings:
            if f["id"] not in KINDS:continue
            ref=f["unit_id"]+":"+str(f["paragraph_index"]);old=maps[ref][1]["text"]
            result.append({"ticket_id":"D"+str(len(result)),"paragraph_ref":ref,"problem_kind":KINDS[f["id"]],
                           "draft_span":enclosing_sentence(old,f["result_evidence"]),"review_ref":f["id"],"severity":f["severity"]})
        return result
    # Previously used T4 confirmation material. These are reviewer questions, not blind labels.
    first=maps["c_u00:1"][1]["text"];second=maps["c_u02:0"][1]["text"]
    return [
        {"ticket_id":"C0","paragraph_ref":"c_u00:1","problem_kind":"certainty",
         "draft_span":enclosing_sentence(first,"진입 장벽이 높을수록"),"review_ref":"T4_confirmation_claim_strength","severity":"major"},
        {"ticket_id":"C1","paragraph_ref":"c_u02:0","problem_kind":"reference_scope",
         "draft_span":enclosing_sentence(second,"이 중 60%"),"review_ref":"T4_confirmation_denominator_scope","severity":"review_question"}]

def materials(units,jobs):
    units=q.phase_scope(units);tickets=tickets_for(units);mapping=e.paragraph_map(units)
    rows={p["paragraph_ref"]:p for p in q.rows_for(units,jobs)};packets=[]
    for ticket in tickets:
        unit,paragraph=mapping[ticket["paragraph_ref"]];row=rows[ticket["paragraph_ref"]]
        segments=copy.deepcopy(row["segments"]);present={s["source_indices"][0] for s in segments if s["kind"] in ["source","context"]}
        # Earlier sentences in the same generation unit are interpretation context, never new edit targets.
        for s in jobs[unit["unit_id"]]["unit"]["required_source_sentences"]:
            i=s["sentence_index"]
            if i<min(paragraph["source_indices"]) and i not in present:
                segments.append({"segment_ref":"u:"+str(i),"kind":"context","source_indices":[i],"text":s["text"]})
        packets.append({"ticket_id":ticket["ticket_id"],"paragraph_ref":ticket["paragraph_ref"],
            "problem_kind":ticket["problem_kind"],"draft_span":ticket["draft_span"],
            "paragraph_text":paragraph["text"],"evidence":segments,
            "source_owned_anchors":row["protected_anchors"],"inherited_numeric_forms":list(e.numbers(paragraph["text"])),
            "existing_unresolved":row["existing_unresolved"],"cautions":row["cautions"]})
    selected={x["paragraph_ref"] for x in tickets}
    readonly=[{"paragraph_ref":ref,"text":p["text"]} for ref,(_,p) in mapping.items() if ref not in selected]
    return tickets,packets,readonly

def schema(packets):
    def obj(properties):
        return {"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}
    return obj({"repairs":{"type":"array","minItems":len(packets),"maxItems":len(packets),"items":obj({
        "ticket_id":{"type":"string","enum":[p["ticket_id"] for p in packets]},
        "action":{"type":"string","enum":["replace","keep","unresolved"]},
        "evidence_refs":{"type":"array","maxItems":3,"items":{"type":"string","enum":sorted({s["segment_ref"] for p in packets for s in p["evidence"]})}},
        "replacement":{"type":"string","maxLength":MAX_REPLACEMENT},
        "reason":{"type":"string","minLength":1,"maxLength":MAX_REASON}})}})

def validate(value,packets):
    b.check(isinstance(value,dict) and set(value)=={"repairs"} and isinstance(value["repairs"],list),"response_shape")
    repairs=value["repairs"];b.check([r.get("ticket_id") for r in repairs]==[p["ticket_id"] for p in packets],"ticket_completeness")
    result={};positions={}
    for repair,packet in zip(repairs,packets):
        b.check(set(repair)=={"ticket_id","action","evidence_refs","replacement","reason"},"repair_keys")
        action=repair["action"];text=repair["replacement"];refs=repair["evidence_refs"]
        b.check(action in ["replace","keep","unresolved"] and isinstance(text,str) and len(text)<=MAX_REPLACEMENT,"repair_action")
        b.check(isinstance(repair["reason"],str) and 0<len(repair["reason"])<=MAX_REASON,"repair_reason")
        evidence={s["segment_ref"]:s for s in packet["evidence"]}
        b.check(isinstance(refs,list) and len(refs)<=3 and len(refs)==len(set(refs)) and all(x in evidence for x in refs),"evidence_ownership")
        if action!="replace":b.check(text=="","nonreplacement_text");continue
        b.check(bool(refs),"replacement_requires_evidence")
        span,old=packet["draft_span"],packet["paragraph_text"]
        b.check(bool(span) and old.count(span)==1,"review_span_identity")
        b.check(q.norm(text)!=q.norm(span),"noop_replacement")
        b.check(not re.search(r"<[/!a-zA-Z]|"+chr(96)*3,text),"non_plain_text")
        b.check(not re.search(r"https?://",text) or text in old,"invented_url")
        corpus=[evidence[x]["text"] for x in refs]
        for quoted in re.findall(r"'([^'\n]{1,100})'|「([^」]{1,100})」",text):
            quote=next(x for x in quoted if x)
            numeric=bool(re.fullmatch(r"\d[\d,.]*",quote)) and quote in old
            b.check(numeric or any(q.norm(quote) in q.norm(s) for s in corpus),"new_ungrounded_quote")
        for term in re.findall(r"\b(?:p_u\d+|c_u\d+|[AV]\d+)\b",text):
            b.check(term in old or any(term in s for s in corpus),"internal_id_leak")
        for term in ["원문에서","전사본에서"]:
            b.check(term not in text or term in old or any(term in s for s in corpus),"meta_leak")
        start=old.index(span);end=start+len(span);ref=packet["paragraph_ref"]
        b.check(all(end<=a or start>=z for a,z,_ in positions.get(ref,[])),"overlapping_review_spans")
        positions.setdefault(ref,[]).append((start,end,text))
    for ref,edits in positions.items():
        packet=next(p for p in packets if p["paragraph_ref"]==ref);text=packet["paragraph_text"]
        for a,z,replacement in sorted(edits,reverse=True):text=text[:a]+replacement+text[z:]
        b.check(e.numbers(text)==e.numbers(packet["paragraph_text"]),"numeric_surface_drift")
        for anchor in packet["source_owned_anchors"]:
            b.check(q.norm(anchor["text"]) in q.norm(text),"lost_source_owned_anchor")
        result[ref]=text
    return result

def validate_repairs(value,units,jobs):
    _,packets,_=materials(units,jobs);validate(value,packets);return value
def apply(units,value,jobs):
    _,packets,_=materials(units,jobs);changes=validate(value,packets);out=copy.deepcopy(units)
    for ref,(_,p) in e.paragraph_map(out).items():
        if ref in changes:p["text"]=changes[ref]
    return out
def empty_response(units):
    return {"repairs":[{"ticket_id":t["ticket_id"],"action":"keep","evidence_refs":[],"replacement":"","reason":"not_generated"} for t in tickets_for(q.phase_scope(units))]}
def inspect(value,units,jobs):
    out=apply(units,value,jobs)
    return {"tickets":len(value["repairs"]),"replacements":sum(r["action"]=="replace" for r in value["repairs"]),
            "unresolved":sum(r["action"]=="unresolved" for r in value["repairs"]),
            "metadata_preserved":all({k:x for k,x in p.items() if k!="text"}=={k:x for k,x in s.items() if k!="text"}
                                     for u,v in zip(units,out) for p,s in zip(u["paragraphs"],v["paragraphs"]))}

def make_job(phase,units,jobs):
    tickets,packets,readonly=materials(units,jobs)
    payload={"task":"reviewer_localized_semantic_repair","available_document_links":["original_video_only"],
             "tickets":packets,"readonly_context":readonly}
    body={"model":"qwen3.8-27b","messages":[{"role":"system","content":PROMPT},{"role":"user","content":e.wire(payload).decode()}],
          "temperature":0,"max_tokens":4096,"stream":False,"chat_template_kwargs":{"enable_thinking":False},
          "response_format":{"type":"json_schema","json_schema":{"name":"stage3_t5_e03_repair","strict":True,"schema":schema(packets)}}}
    return {"job_id":phase,"body":body,"request_sha256":digest(body),"unit_ids":[u["unit_id"] for u in units],
            "ticket_ids":[t["ticket_id"] for t in tickets],"review_tickets":tickets,
            "owned_source_count":len({i for p in packets for s in p["evidence"] for i in s["source_indices"]})}
def output_probes(job):
    packets=loads(job["body"]["messages"][1]["content"])["tickets"]
    kept={"repairs":[{"ticket_id":p["ticket_id"],"action":"keep","evidence_refs":[],"replacement":"","reason":"표기 변경은 필요 없음"} for p in packets]}
    stress={"repairs":[{"ticket_id":p["ticket_id"],"action":"replace","evidence_refs":[p["evidence"][0]["segment_ref"]],
                       "replacement":("근거의 대상과 조건 및 주체를 보존하는 출력 길이 검사. "*12)[:MAX_REPLACEMENT],
                       "reason":("관계와 적용 범위를 확인하는 길이 검사. "*6)[:MAX_REASON]} for p in packets]}
    return {"all_keep":e.wire(kept).decode(),"stress":e.wire(stress).decode()}

def identity(run):
    m,r=b.read(run/"manifest.json"),b.read(run/"report.json")
    b.check(m["policy"]==POLICY and b.sha(run/"manifest.json")==r["manifest_sha256"],"manifest_identity")
    b.check(b.sha(Path(__file__))==m["runner_sha256"],"runner_identity")
    for path,h in m["protected"].items():b.check(b.sha(ROOT/path)==h,"protected_changed:"+path)
    for path,h in m["runtime_identity"].items():b.check(b.sha(path)==h,"runtime_changed:"+path)
    for job in m["jobs"]:b.check(digest(job["body"])==job["request_sha256"],"request_changed")
    return m,r,*e.get_context()
def proposal(run,phase,units,r):
    call=r["calls"].get(phase,{})
    return apply(units,call["parsed"],identity(run)[3]) if call.get("status")=="success" else copy.deepcopy(units)
def render(run):
    _,r,_,jobs,base,confirm=identity(run);article=copy.deepcopy(base);article.update(candidate="E03",policy=POLICY)
    dev=r["calls"].get("development",{})
    if dev.get("status")=="success" and r["reviews"].get("development",{}).get("decision")!="revise":
        article["units"]=apply(base["units"],dev["parsed"],jobs)
    article["prose_modified"]=article["units"]!=base["units"]
    write(run/"article.json",article);(run/"article.html").write_text(b.article_html(article),encoding="utf-8")
    body="<h1>E03 검수 위치 지정 국소 수정 대조</h1><p>자동 발견 실험이 아니며 독자용 글의 사용자 채택과 구분한다.</p>"
    for phase,units in [("development",base["units"]),("confirmation",confirm)]:
        tickets,packets,_=materials(units,jobs);call=r["calls"].get(phase,{})
        repairs={x["ticket_id"]:x for x in call.get("parsed",{}).get("repairs",[])}
        body+="<h2>"+phase+"</h2>"
        for ticket,packet in zip(tickets,packets):
            result=repairs.get(ticket["ticket_id"])
            body+="<h3>"+html.escape(ticket["ticket_id"]+" / "+ticket["paragraph_ref"]+" / "+ticket["problem_kind"])+"</h3><pre>"+html.escape(
                "\n".join(x["segment_ref"]+": "+x["text"] for x in packet["evidence"]))+"</pre><strong>기존 문단</strong><p>"+html.escape(packet["paragraph_text"])+"</p><strong>수정 대상</strong><pre>"+html.escape(ticket["draft_span"])+"</pre>"
            if result:
                body+="<strong>모델 제안</strong><pre>"+html.escape(json.dumps(result,ensure_ascii=False,indent=2))+"</pre>"
    (run/"review.html").write_text(b.page("E03 원문·국소 수정 검수",body),encoding="utf-8")
def verify(run):
    m,r,data,jobs,base,confirm=identity(run);expected=copy.deepcopy(base);expected.update(candidate="E03",policy=POLICY)
    dev=r["calls"].get("development",{})
    if dev.get("status")=="success" and r["reviews"].get("development",{}).get("decision")!="revise":
        expected["units"]=apply(base["units"],dev["parsed"],jobs)
    expected["prose_modified"]=expected["units"]!=base["units"]
    b.check(b.read(run/"article.json")==expected,"unlogged_article_change");b.validate(base,data,jobs)
    for phase,call in r["calls"].items():
        if call.get("response_sha256"):b.check(b.sha(run/(phase+".response.body"))==call["response_sha256"],"response_hash")
        if call.get("status")=="success":validate_repairs(call["parsed"],q.phase_scope(base["units"]) if phase=="development" else confirm,jobs)
    out=b.inspect_html((run/"article.html").read_text(),expected)
    out.update(protected_hashes_verified=len(m["protected"]),units=16,paragraphs=40,source_sentences=217,metadata_preserved=True,
               reviewer_assisted=True,autonomous_discovery_validated=False,quality_automatically_accepted=False)
    return out

def selftest():
    packet={"ticket_id":"test","paragraph_ref":"unit:0","draft_span":"항상 열려 있다","paragraph_text":"'다솔' 문은 항상 열려 있다. 값은 12다.",
            "evidence":[{"segment_ref":"s:0","kind":"source","source_indices":[0],"text":"'다솔' 문은 조건에 따라 열린다."}],
            "source_owned_anchors":[{"text":"다솔","evidence_refs":["s:0"]}]}
    def good():return {"repairs":[{"ticket_id":"test","action":"replace","evidence_refs":["s:0"],"replacement":"조건에 따라 열린다","reason":"조건 범위 보존"}]}
    validate(good(),[packet])
    changes=[
        ("missing_ticket",lambda v:v["repairs"].clear()),
        ("foreign_ticket",lambda v:v["repairs"][0].update(ticket_id="foreign")),
        ("foreign_evidence",lambda v:v["repairs"][0].update(evidence_refs=["s:9"])),
        ("duplicate_evidence",lambda v:v["repairs"][0].update(evidence_refs=["s:0","s:0"])),
        ("noop",lambda v:v["repairs"][0].update(replacement="항상 열려 있다")),
        ("numeric_surface_change",lambda v:v["repairs"][0].update(replacement="값은 13으로 열린다")),
        ("unprovided_quote",lambda v:v["repairs"][0].update(replacement="'미검증합성'으로 열린다")),
        ("html",lambda v:v["repairs"][0].update(replacement="<b>열린다</b>")),
        ("url",lambda v:v["repairs"][0].update(replacement="https://invalid.example로 열린다")),
        ("keep_with_text",lambda v:v["repairs"][0].update(action="keep")),
    ]
    for name,change in changes:
        v=good();change(v)
        try:validate(v,[packet])
        except ValueError:pass
        else:raise AssertionError("fixture_accepted:"+name)
    v=good();v["repairs"][0].update(action="unresolved",replacement="",evidence_refs=[]);b.check(validate(v,[packet])=={},"unresolved_fixture")
    renamed=copy.deepcopy(packet);renamed["ticket_id"]="renamed";renamed["paragraph_text"]=renamed["paragraph_text"].replace("다솔","누리");renamed["evidence"][0]["text"]=renamed["evidence"][0]["text"].replace("다솔","누리");renamed["source_owned_anchors"][0]["text"]="누리";v=good();v["repairs"][0]["ticket_id"]="renamed";validate(v,[renamed])
    return {"rejection_checks":len(changes),"positive_checks":3,"cases":[x[0] for x in changes],"semantic_quality_evidence":False,"model_calls":0}

def can_send(run,phase):
    m,r,_,_,_,_=identity(run)
    b.check(not r.get("closed_utc"),"closed_run");b.check(phase not in r["calls"],"already_reserved_no_resend")
    b.check(len(r["calls"])<2 and all(x["status"]=="success" for x in r["calls"].values()),"prior_failure_or_budget")
    if phase=="confirmation":
        b.check(r["reviews"].get("development",{}).get("decision")=="accepted" and "freeze" in r,"confirmation_gate")
        f=r["freeze"];b.check(f["manifest_sha256"]==b.sha(run/"manifest.json") and f["primary_article_sha256"]==b.sha(run/"article.json")
                            and f["development_review_sha256"]==digest(r["reviews"]["development"]),"freeze_changed")
    return {"phase":phase,"model_calls":0}
def prepare():
    existing=list((HERE/"runs").glob("*_e03"))
    if existing:
        b.check(len(existing)==1,"multiple_runs");verify(existing[0]);b.check(not b.read(existing[0]/"report.json")["calls"],"already_executed")
        print("prepared_reused:"+str(existing[0].relative_to(ROOT)));return
    earlier=[BASE,HERE/"runs/20261105T061526Z_e01",HERE/"runs/20261105T085850Z_e02"]
    for old in earlier:
        for rel,h in b.read(old/"report.json")["artifact_hashes"].items():b.check(b.sha(old/rel)==h,"prior_artifact")
    data,jobs,base,confirm=e.get_context();planned=[make_job("development",q.phase_scope(base["units"]),jobs),make_job("confirmation",confirm,jobs)]
    tests=selftest();tp=q.token_plan(planned)
    runtime={p:h for p,h in b.read(b.UPSTREAM/"config.json")["critical_files"].items() if "/vllm-docker-config/" in p or "/models/llm/" in p}
    for p,h in runtime.items():b.check(b.sha(p)==h,"runtime_changed")
    protected=b.protect()
    for p in [HERE/"run_e00.py",HERE/"run_e01.py",HERE/"run_e02.py"]+[p for old in earlier for p in old.rglob("*") if p.is_file()]:
        protected[str(p.relative_to(ROOT))]=b.sha(p)
    run=HERE/"runs"/(e.datetime.now(e.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_e03");run.mkdir(parents=True,exist_ok=False)
    m={"candidate":"E03","policy":POLICY,"created_utc":e.now(),"runner_sha256":b.sha(Path(__file__)),"runner_source_snapshot":Path(__file__).read_text(),
       "spec_snapshot":b.SPEC.read_text(),"protected":protected,"runtime_identity":runtime,"jobs":planned,"token_plan":tp,"offline_contract_checks":tests,
       "prompt_sha256":hashlib.sha256(PROMPT.encode()).hexdigest(),"limits":{"max_calls":2,"max_http_seconds":360,"request_timeout_seconds":180,"automatic_retries":0},
       "reviewer_assisted":True,"autonomous_discovery_validated":False,"confirmation_is_blind":False,"normal_controls_readonly":e.CONTROLS,
       "correct_replacement_answers_provided":False,"image_policy":"unchanged; mobile utility pending"}
    write(run/"manifest.json",m);write(run/"report.json",{"candidate":"E03","policy":POLICY,"decision":"prepared_not_executed","manifest_sha256":b.sha(run/"manifest.json"),
        "created_utc":e.now(),"calls":{},"reviews":{},"new_model_calls":0,"http_seconds":0,"automatic_retries":0,"unknown_calls":0,"E03_complete":False,"E04_executed":False,
        "T5_complete":False,"user_accepted":False,"final_article_user_accepted":False,"codex_account_usage":"not_observed"})
    render(run)
    print(json.dumps({"run":str(run.relative_to(ROOT)),"tokens":tp["jobs"],"output_budget":tp["output_budget"],"tickets":[len(j["ticket_ids"]) for j in planned],
                      "owned_sources":[j["owned_source_count"] for j in planned],"tests":tests,"protected":len(protected)},ensure_ascii=False))
def review(run,phase):
    m,r,_,_,_,_=identity(run);value=loads(sys.stdin.read());job=next(j for j in m["jobs"] if j["job_id"]==phase)
    b.check(value.get("reviewed_ticket_ids")==job["ticket_ids"],"review_ticket_coverage")
    b.check(type(value.get("major_unresolved")) is int and type(value.get("new_major_regressions")) is int,"review_counts")
    if value["decision"]=="accepted":b.check(value["major_unresolved"]==0 and value["new_major_regressions"]==0,"quality_gate")
    sys.stdin=io.StringIO(json.dumps(value,ensure_ascii=False));e.review(run,phase)
    if value["decision"]=="revise":render(run)
def closeout(run):
    m,r,_,_,_,_=identity(run);b.check(bool(r["calls"]),"unexecuted_closeout")
    r["technical"]=verify(run);accepted=all(r["reviews"].get(p,{}).get("decision")=="accepted" for p in ["development","confirmation"])
    if accepted:b.check(r.get("browser",{}).get("passed") and r["browser"]["portableFileSha256"]==b.sha(run/"article.html"),"browser_required")
    r.update(decision="accepted_bounded_reviewer_assisted_repair" if accepted else "revise",E03_complete=True,T5_complete=False,
             closed_utc=e.now(),new_model_calls=len(r["calls"]),http_seconds=round(sum(x.get("http_seconds",180) for x in r["calls"].values()),6),
             usage={k:sum((x.get("usage") or {}).get(k,0) for x in r["calls"].values()) for k in ["prompt_tokens","completion_tokens","total_tokens"]},
             unknown_calls=sum(x["status"]=="unknown" for x in r["calls"].values()))
    r["artifact_hashes"]={str(p.relative_to(run)):b.sha(p) for p in run.rglob("*") if p.is_file() and p.name!="report.json"}
    write(run/"report.json",r);print(json.dumps({k:r[k] for k in ["decision","new_model_calls","http_seconds","usage"]},ensure_ascii=False))

# Local process adapters only; earlier source files and runs remain immutable.
q.output_probes=output_probes;e.identity=identity;e.render=render;e.verify=verify;e.validate_edits=validate_repairs;e.apply=apply;e.inspect_patch_report=lambda units,value,jobs:inspect(value,units,jobs)
def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["prepare","selftest","verify","check-send","send","review","browser","closeout"])
    parser.add_argument("--run");parser.add_argument("--phase",choices=["development","confirmation"]);a=parser.parse_args()
    if a.action=="selftest":print(json.dumps(selftest()));return
    if a.action=="prepare":prepare();return
    b.check(bool(a.run),"explicit_run_required");run=Path(a.run).resolve();b.check(run.is_relative_to((HERE/"runs").resolve()),"run_scope")
    fd=os.open(run,os.O_RDONLY)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action in ["check-send","send"]:
            b.check(bool(a.phase),"phase_required");out=can_send(run,a.phase)
            if a.action=="check-send":print(json.dumps(out))
            else:
                with contextlib.redirect_stdout(io.StringIO()):e.send(run,a.phase)
                r=b.read(run/"report.json");r.update(new_model_calls=len(r["calls"]),http_seconds=round(sum(x.get("http_seconds",180) for x in r["calls"].values()),6))
                write(run/"report.json",r);call=r["calls"][a.phase]
                print(json.dumps({k:call.get(k) for k in ["status","error","http_seconds","finish_reason","usage"]},ensure_ascii=False))
        elif a.action=="verify":print(json.dumps(verify(run),ensure_ascii=False))
        elif a.action=="review":b.check(bool(a.phase),"phase_required");review(run,a.phase)
        elif a.action=="browser":e.browser(run)
        else:closeout(run)
    finally:os.close(fd)
if __name__=="__main__":main()
