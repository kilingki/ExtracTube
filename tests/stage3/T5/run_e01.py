"""T5 E01: generic source-scope editing, normal controls, frozen cross-topic check.
Reuses E00 rendering/identity; one runner, one manifest/report, two raw responses.
"""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import argparse, copy, fcntl, hashlib, html, io, json, re, subprocess, sys, time, urllib.request
import run_e00 as b

ROOT, HERE = b.ROOT, b.HERE
BASE = HERE / "runs/20261105T050511Z_e00"
POLICY = "E01.source_relation_scope_local_edit.v1"
PRIMARY = ["p_u02", "p_u06", "p_u07", "p_u08", "p_u10", "p_u11", "p_u13", "p_u15"]
CONTROLS = ["p_u02", "p_u10"]
ENDPOINT = "http://127.0.0.1:8095/infer?model=qwen-vlm"
CONTAINER = "vllm-docker-config-vllm-runtime-1"
CODES = ["relation_scope", "condition_scope", "deictic_scope", "evidence_strength",
         "example_fidelity", "source_reference"]
PROMPT = """담당 원문과 이미 작성된 문단을 대조하여 독립 설명문의 의미 충실성을 보완하는 국소 편집자다.
이 입력의 원문은 외부 사실의 정답이 아니라 원본 영상의 발화 기록이다. 외부 지식이나 현재 정책으로 고치지 않는다.
핵심은 단어 일치가 아니라 대상·정의·값·조건·행동·주체와 근거 범위의 관계다.
1. 명칭이 무엇을 정의하는지, 수치가 어느 대상·집단·시점에 해당하는지 보존한다. 비율의 분모·비교 방향·정도나 인과를 임의 강화하지 않는다.
2. 한 항목의 조건·부정·삭제·예외를 옆 항목으로 옮기지 않는다. 단지 인접했다는 이유로 조건이나 의무를 합성하지 않는다.
3. 여기·그대로·이것 같은 지시 대상이 불명확하면 가까운 명사에 임의 결합하지 않는다. 확실한 절차는 보존하되 불명확한 지시를 새로운 규범으로 만들지 않는다.
4. 개인 경험·시연 중 화면 상태·평가·권고·전언과 일반 규칙을 구분한다. 주체와 확신 수준을 유지한다.
5. 원문 일부의 전사 모호성과 같은 문장의 명확한 예시를 구분한다. 명확한 예시·수치·분기·대안을 생략해 문장을 안전하게 만드는 식의 축약은 금지한다.
6. 별도로 등장한 입력 예시를 새 인용 문자열로 합성하지 않는다. 의도적 오타와 불명확한 전사는 상식으로 교정하지 않는다.
7. 영상의 위치 지시와 배포 문서의 실제 기능은 다르다. 제공되지 않은 링크·그림·화면을 지금 문서에서 클릭·참조하도록 만들지 않는다.
   원본의 안내 내용과 주체는 자연스럽게 보존하되 URL·자료·위치를 추정하지 않는다. 독자 본문에 누락/실패/검수 경고를 넣지 않는다.
8. approved_visual_amendments는 적힌 범위에서만 원시 발화보다 우선하는 승인 시연 관찰이다. 범위를 넘어 일반화하거나 그 구간의 깨진 발화를 다시 분해하지 않는다.
9. 문제가 없는 문단은 출력하지 않는다. 문체만 바꾸거나 귀속 표현을 일괄 삭제하기 위해 편집하지 않는다. 전체 문단 수·순서·문장 참조는 유지한다.
변경은 edits의 paragraph_ref·text로만 제안한다. 해당 문단의 source_indices 안에서 실제 근거 index를 제시하고 짧은 reason_code와 explanation을 쓴다.
수정한 문단 안의 기존 정보는 모두 보존한다. 해결할 수 없는 의미 문제는 unresolved에 남기고 원문 밖의 정답을 만들지 않는다.
마크다운·HTML·코드펜스 없이 스키마에 맞는 JSON만 반환한다."""
def wire(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
def digest(v):
    return hashlib.sha256(wire(v)).hexdigest()
def loads(raw):
    def obj(pairs):
        result = {}
        for k,v in pairs:
            b.check(k not in result, "duplicate_json_key")
            result[k] = v
        return result
    return json.loads(raw, object_pairs_hook=obj, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
def write(p, value):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(wire(value) + b"\n")
    tmp.replace(p)
def now():
    return datetime.now(timezone.utc).isoformat()
def schema(refs, indices):
    def obj(properties):
        return {"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}
    ref = {"type":"string","enum":refs}
    return obj({
        "edits":{"type":"array","maxItems":12,"items":obj({
            "paragraph_ref":ref,"text":{"type":"string","minLength":1,"maxLength":2600},
            "source_indices":{"type":"array","minItems":1,"maxItems":32,"items":{"type":"integer","enum":indices}},
            "reason_code":{"type":"string","enum":CODES},
            "explanation":{"type":"string","minLength":1,"maxLength":160}})},
        "unresolved":{"type":"array","maxItems":12,"items":obj({
            "paragraph_ref":ref,"reason":{"type":"string","minLength":1,"maxLength":200}})}})
def paragraph_map(units):
    return {u["unit_id"]+":"+str(p["paragraph_index"]):(u,p)
            for u in units for p in u["paragraphs"]}
def confirmation_units(jobs):
    draft = b.read(b.UPSTREAM / "confirmation/draft.json")
    result = []
    for u in draft["units"]:
        job = jobs[u["unit_id"]]["unit"]
        content = u["content"]
        result.append({"unit_id":u["unit_id"],"video_id":job["video_id"],
                       "source_range":job["source_range"],"section_hierarchy":job["section_hierarchy"],
                       "paragraphs":[{"paragraph_index":i, **p} for i,p in enumerate(content["paragraphs"])],
                       "unresolved":content["unresolved"]})
    return result
def make_job(phase, units, jobs, amendments):
    maps = paragraph_map(units)
    payload = {"task":"source_scope_local_edit","available_document_links":["original_video_only"],
               "reader_policy":"excluded","units":[]}
    for u in units:
        source = jobs[u["unit_id"]]["unit"]
        ids = {s["sentence_index"] for s in source["required_source_sentences"]}
        payload["units"].append({
            "unit_id":u["unit_id"],"title":source["section_hierarchy"]["leaf_title"],
            "assigned_source":[{"index":s["sentence_index"],"text":s["text"]} for s in source["required_source_sentences"]],
            "previous_context":[{"index":s["sentence_index"],"text":s["text"]} for s in source["previous_context"]["sentences"]],
            "source_cautions":source["resolved_cautions"],
            "existing_unresolved":u.get("unresolved",[]),
            "approved_visual_amendments":[a for a in amendments if set(a["source_indices"]) & ids],
            "paragraphs":[{"paragraph_ref":u["unit_id"]+":"+str(p["paragraph_index"]),
                           "source_indices":p["source_indices"],"text":p["text"]} for p in u["paragraphs"]]})
    indices = sorted({s["sentence_index"] for u in units for s in jobs[u["unit_id"]]["unit"]["required_source_sentences"]})
    body = {"model":"qwen3.8-27b","messages":[{"role":"system","content":PROMPT},
             {"role":"user","content":wire(payload).decode()}],"temperature":0,"max_tokens":4096,
            "stream":False,"chat_template_kwargs":{"enable_thinking":False},
            "response_format":{"type":"json_schema","json_schema":{"name":"stage3_t5_e01_edit","strict":True,"schema":schema(list(maps),indices)}}}
    return {"job_id":phase,"body":body,"request_sha256":digest(body),"unit_ids":[u["unit_id"] for u in units],
            "paragraph_refs":list(maps),"source_sentence_count":len(indices)}
def token_plan(jobs):
    source = (ROOT/"tests/stage3/T2/token_plan.py").read_text()
    code = "import sys,json,io,xgrammar\nv=json.load(sys.stdin)\nfor j in v['jobs']: xgrammar.Grammar.from_json_schema(j['body']['response_format']['json_schema']['schema'])\nsys.stdin=io.StringIO(json.dumps(v))\n"+source
    result = subprocess.run(["docker","exec","-i",CONTAINER,"python3","-c",code],
                            input=wire({"jobs":jobs}), capture_output=True, timeout=180)
    b.check(result.returncode==0,"cpu_token_plan:"+result.stderr.decode(errors="replace")[-1500:])
    plan = loads(result.stdout.decode())
    for job,row in zip(jobs,plan["jobs"]):
        b.check(job["job_id"]==row["job_id"] and row["input_tokens"]<=12000
                and row["input_tokens"]+4096+1024<=32768,"token_budget")
        job["token_plan"] = row
    return plan
def numbers(text):
    return Counter(x.replace(",","") for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text))
def validate_edits(value, units, jobs):
    b.check(set(value)=={"edits","unresolved"} and isinstance(value["edits"],list)
            and isinstance(value["unresolved"],list) and len(value["edits"])<=12, "patch_shape")
    maps, seen = paragraph_map(units), set()
    for edit in value["edits"]:
        b.check(set(edit)=={"paragraph_ref","text","source_indices","reason_code","explanation"},"patch_keys")
        ref = edit["paragraph_ref"]
        b.check(ref in maps and ref not in seen,"foreign_or_duplicate_ref")
        seen.add(ref); unit, old = maps[ref]; text = edit["text"]
        b.check(isinstance(text,str) and text.strip() and text!=old["text"] and len(text)<=2600,"empty_or_noop_patch")
        b.check(not re.search(r"<[/!a-zA-Z]|"+chr(96)*3,text),"non_plain_text")
        indices = edit["source_indices"]
        b.check(isinstance(indices,list) and bool(indices) and all(type(i) is int for i in indices)
                and indices==sorted(set(indices)) and set(indices).issubset(old["source_indices"]),"patch_source_scope")
        b.check(edit["reason_code"] in CODES and isinstance(edit["explanation"],str),"reason_code")
        source = jobs[unit["unit_id"]]["unit"]["required_source_sentences"]
        corpus = " ".join(s["text"] for s in source) + " " + old["text"]
        b.check(not (numbers(old["text"]) - numbers(text)), "lost_numeric_anchor")
        b.check(set(numbers(text)).issubset(numbers(corpus)),"new_numeric_anchor")
        for quote in re.findall(r"'([^'\n]{1,100})'|「([^」]{1,100})」",text):
            q = next(x for x in quote if x)
            b.check(re.sub(r"\s+","",q) in re.sub(r"\s+","",corpus),"unsupported_quote")
    for row in value["unresolved"]:
        b.check(set(row)=={"paragraph_ref","reason"} and row["paragraph_ref"] in maps
                and isinstance(row["reason"],str) and bool(row["reason"].strip()),"unresolved_scope")
    return value
def apply(units, value, jobs):
    validate_edits(value,units,jobs)
    result = copy.deepcopy(units); maps=paragraph_map(result)
    for edit in value["edits"]:
        maps[edit["paragraph_ref"]][1]["text"] = edit["text"]
    return result
def get_context():
    data,jobs=b.inputs()
    return data,jobs,b.read(BASE/"article.json"),confirmation_units(jobs)
def inspect_patch_report(units, value, jobs):
    result=apply(units,value,jobs)
    return {"unit_count":len(result),"paragraph_count":sum(len(u["paragraphs"]) for u in result),
            "edits":len(value["edits"]),"unresolved":len(value["unresolved"]),
            "metadata_and_indices_preserved":all(
                {k:v for k,v in p.items() if k!="text"}=={k:v for k,v in q.items() if k!="text"}
                for u,v in zip(units,result) for p,q in zip(u["paragraphs"],v["paragraphs"]))}
def identity(run):
    report,manifest=b.read(run/"report.json"),b.read(run/"manifest.json")
    b.check(b.sha(run/"manifest.json")==report["manifest_sha256"],"manifest_changed")
    for path,h in manifest["protected"].items():
        b.check(b.sha(ROOT/path)==h,"protected_changed:"+path)
    b.check(b.sha(Path(__file__))==manifest["runner_sha256"],"runner_changed")
    for path,h in manifest["runtime_identity"].items():
        b.check(b.sha(path)==h,"runtime_changed:"+path)
    data,jobs,base,confirm=get_context()
    for job in manifest["jobs"]:
        b.check(digest(job["body"])==job["request_sha256"],"request_changed")
    return manifest,report,data,jobs,base,confirm
def render(run):
    _,report,_,jobs,base,confirm=identity(run)
    article=copy.deepcopy(base); article.update(candidate="E01",policy=POLICY)
    dev=report["calls"].get("development",{})
    if dev.get("status")=="success":
        article["units"]=apply(article["units"],dev["parsed"],jobs)
    article["prose_modified"]=article["units"]!=base["units"]
    write(run/"article.json",article)
    (run/"article.html").write_text(b.article_html(article),encoding="utf-8")
    body="<h1>E01 원문·E00·편집안 대조</h1><p>독자용 HTML과 별도인 검수 화면. 기존 확인 구간은 blind가 아니며 대표 글에 섞지 않는다.</p>"
    for phase,units in [("development",base["units"]),("confirmation",confirm)]:
        call=report["calls"].get(phase,{})
        value=call.get("parsed",{"edits":[],"unresolved":[]}) if call.get("status")=="success" else {"edits":[],"unresolved":[]}
        patched=apply(units,value,jobs); changes={e["paragraph_ref"]:e for e in value["edits"]}
        body+="<h2>"+html.escape(phase)+"</h2>"
        for old,new in zip(units,patched):
            body+="<h3>"+html.escape(old["unit_id"]+" · "+old["section_hierarchy"]["leaf_title"])+"</h3><pre>"+html.escape(
                "\n".join(f'[{s["sentence_index"]}] {s["text"]}' for s in jobs[old["unit_id"]]["unit"]["required_source_sentences"]))+"</pre>"
            for p,q in zip(old["paragraphs"],new["paragraphs"]):
                ref=old["unit_id"]+":"+str(p["paragraph_index"])
                body+="<div class=review-item><strong>"+html.escape(ref)+"</strong>"
                if ref in changes: body+="<pre>"+html.escape(p["text"])+"</pre><p>"+html.escape(q["text"])+"</p><p class=review-meta>"+html.escape(changes[ref]["explanation"])+"</p>"
                else: body+="<p>"+html.escape(p["text"])+"</p>"
                body+="</div>"
    (run/"review.html").write_text(b.page("E01 원文·편집안 대조",body),encoding="utf-8")
def verify(run):
    manifest,report,data,jobs,base,confirm=identity(run)
    article=b.read(run/"article.json")
    expected=copy.deepcopy(base);expected.update(candidate="E01",policy=POLICY)
    dev=report["calls"].get("development",{})
    if dev.get("status")=="success":expected["units"]=apply(expected["units"],dev["parsed"],jobs)
    expected["prose_modified"]=expected["units"]!=base["units"]
    b.check(article==expected,"unlogged_article_change")
    normalized=copy.deepcopy(article);normalized.update(candidate="E00",policy=base["policy"],prose_modified=False)
    normalized["units"]=copy.deepcopy(base["units"])
    b.validate(normalized,data,jobs)
    result=b.inspect_html((run/"article.html").read_text(encoding="utf-8"),article)
    for phase,call in report["calls"].items():
        if call.get("response_sha256"):b.check(b.sha(run/(phase+".response.body"))==call["response_sha256"],"response_changed")
        if call.get("status")=="success":
            expected_units=[u for u in base["units"] if u["unit_id"] in PRIMARY] if phase=="development" else confirm
            validate_edits(call["parsed"],expected_units,jobs)
    result.update(protected_hashes_verified=len(manifest["protected"]),units=16,paragraphs=40,
                  source_sentences=217,metadata_preserved=True,quality_automatically_accepted=False)
    return result
def selftest():
    _,jobs,base,_=get_context()
    units=[u for u in base["units"] if u["unit_id"] in PRIMARY]
    ref=next(k for k,(u,p) in paragraph_map(units).items() if numbers(p["text"]))
    u,p=paragraph_map(units)[ref]
    def good():
        return {"edits":[{"paragraph_ref":ref,"text":p["text"]+" ","source_indices":[p["source_indices"][0]],
                          "reason_code":"relation_scope","explanation":"synthetic contract fixture"}],"unresolved":[]}
    # These are integrity fixtures, not semantic-quality tests.
    mutations=[
        ("foreign_paragraph",lambda x:x["edits"][0].update(paragraph_ref="other:0")),
        ("duplicate_paragraph",lambda x:x["edits"].append(copy.deepcopy(x["edits"][0]))),
        ("foreign_source",lambda x:x["edits"][0].update(source_indices=[9999])),
        ("bool_source",lambda x:x["edits"][0].update(source_indices=[True])),
        ("new_number",lambda x:x["edits"][0].update(text=p["text"]+" 99999999")),
        ("numeric_loss",lambda x:x["edits"][0].update(text=re.sub(r"\d[\d,]*(?:\.\d+)?","",p["text"]))),
        ("html_injection",lambda x:x["edits"][0].update(text=p["text"]+" <script>x</script>")),
        ("unsupported_quote",lambda x:x["edits"][0].update(text=p["text"]+" '합성미검증고유예시'")),
    ]
    validate_edits(good(),units,jobs)
    for label,mutate in mutations:
        value=good();mutate(value)
        try:validate_edits(value,units,jobs)
        except ValueError:pass
        else:raise AssertionError("fixture_accepted:"+label)
    return {"passed":len(mutations),"fixtures":[x[0] for x in mutations],"semantic_quality_evidence":False}
def prepare():
    b.verify(BASE);data,jobs,base,confirm=get_context()
    selected=[u for u in base["units"] if u["unit_id"] in PRIMARY]
    planned=[make_job("development",selected,jobs,data["source_amendments_applied"]),
             make_job("confirmation",confirm,jobs,[])]
    tests=selftest();tp=token_plan(planned)
    runtime={p:h for p,h in b.read(b.UPSTREAM/"config.json")["critical_files"].items()
             if "/vllm-docker-config/" in p or "/models/llm/" in p}
    for p,h in runtime.items():b.check(b.sha(p)==h,"t4_runtime_identity_changed")
    protected=b.protect()
    for p in [HERE/"run_e00.py",ROOT/"tests/stage3/T2/token_plan.py",ROOT/"src/extractube/infer.py"]+[
            p for p in BASE.rglob("*") if p.is_file()]:
        protected[str(p.relative_to(ROOT))]=b.sha(p)
    run=HERE/"runs"/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_e01")
    run.mkdir(parents=True,exist_ok=False)
    manifest={"candidate":"E01","policy":POLICY,"created_utc":now(),"baseline_run":str(BASE.relative_to(ROOT)),
              "runner_sha256":b.sha(Path(__file__)),"runner_source_snapshot":Path(__file__).read_text(),
              "spec_snapshot":b.SPEC.read_text(),"protected":protected,"runtime_identity":runtime,
              "limits":{"max_calls":2,"max_http_seconds":360,"request_timeout_seconds":180,"automatic_retries":0},
              "prompt_sha256":hashlib.sha256(PROMPT.encode()).hexdigest(),
              "jobs":planned,"token_plan":tp,"offline_contract_checks":tests,
              "normal_controls":CONTROLS,"confirmation_is_blind":False,"confirmation_frozen_after_development":True,
              "case_answers_or_findings_in_model_request":False,"image_policy":"unchanged_E00_pending_mobile_review"}
    write(run/"manifest.json",manifest)
    write(run/"report.json",{"candidate":"E01","policy":POLICY,"decision":"pending","created_utc":now(),
          "manifest_sha256":b.sha(run/"manifest.json"),"calls":{},"reviews":{},
          "T5_complete":False,"E01_complete":False,"user_accepted":False,"final_article_user_accepted":False,
          "E02_executed":False,"codex_account_usage":"not_observed"})
    render(run)
    print(json.dumps({"run":str(run.relative_to(ROOT)),"tokens":tp["jobs"],"tests":tests,
                      "protected":len(protected),"prompt_chars":len(PROMPT)},ensure_ascii=False))
def send(run,phase):
    manifest,report,_,jobs,base,confirm=identity(run)
    b.check(phase not in report["calls"],"already_reserved_no_resend")
    b.check(len(report["calls"])<2 and all(c["status"]=="success" for c in report["calls"].values()),"prior_failure_or_budget")
    if phase=="confirmation":
        b.check(report["reviews"].get("development",{}).get("decision")=="accepted" and "freeze" in report,"confirmation_gate")
        f=report["freeze"]
        b.check(f["manifest_sha256"]==b.sha(run/"manifest.json") and f["development_review_sha256"]==digest(report["reviews"]["development"])
                and f["primary_article_sha256"]==b.sha(run/"article.json"),"freeze_changed")
    spent=sum(c.get("http_seconds",180) for c in report["calls"].values())
    timeout=min(180,360-spent);b.check(timeout>0,"time_budget")
    job=next(j for j in manifest["jobs"] if j["job_id"]==phase)
    call={"status":"started","started_utc":now(),"timeout":timeout,"request_sha256":job["request_sha256"]}
    report["calls"][phase]=call;write(run/"report.json",report);start=time.monotonic();raw=b""
    try:
        status=json.load(urllib.request.urlopen("http://127.0.0.1:8095/v1/models",timeout=5))
        current=next(m for m in status["data"] if m["id"]=="qwen-vlm")
        call["runtime_status_before"]=current["status"]
        sys.path.insert(0,str(ROOT/"src"))
        from extractube.infer import HttpTransport
        def headers(code,values):
            call.update(http_status=code,response_headers=values);write(run/"report.json",report)
        response=HttpTransport().send(ENDPOINT,wire(job["body"]),timeout,4194304,headers)
        raw=response["body"];(run/(phase+".response.body")).write_bytes(raw)
        call.update(response_sha256=hashlib.sha256(raw).hexdigest(),response_complete=response["complete"])
        b.check(response["complete"],"incomplete_response")
        b.check(response["status"]==200,"http_error")
        outer=loads(raw.decode());choice=outer["choices"][0];message=choice["message"]
        call.update(finish_reason=choice.get("finish_reason"),usage=outer.get("usage"))
        b.check(choice["finish_reason"]=="stop","incomplete_generation")
        b.check(not message.get("reasoning") and not message.get("reasoning_content"),"unexpected_reasoning")
        b.check(outer["usage"]["prompt_tokens"]==job["token_plan"]["input_tokens"],"token_count_mismatch")
        units=[u for u in base["units"] if u["unit_id"] in PRIMARY] if phase=="development" else confirm
        value=loads(message["content"]);validate_edits(value,units,jobs)
        call.update(status="success",parsed=value,technical=inspect_patch_report(units,value,jobs))
    except Exception as exc:
        call.update(status="failed" if raw and call.get("response_complete") else "unknown",
                    error=type(exc).__name__+":"+str(exc))
    call.update(http_seconds=round(time.monotonic()-start,6),finished_utc=now())
    write(run/"report.json",report);render(run)
    print(json.dumps(call,ensure_ascii=False))
def review(run,phase):
    manifest,report,_,_,_,_=identity(run)
    b.check(report["calls"].get(phase,{}).get("status")=="success","review_requires_success")
    b.check(phase not in report["reviews"],"review_immutable")
    value=loads(sys.stdin.read())
    b.check(value.get("decision") in ("accepted","revise") and value.get("audio_or_full_video_verified") is False,"review_contract")
    b.check(value.get("response_sha256")==report["calls"][phase]["response_sha256"],"review_response")
    value["recorded_utc"]=now();report["reviews"][phase]=value
    if phase=="development" and value["decision"]=="accepted":
        report["freeze"]={"created_utc":now(),"manifest_sha256":b.sha(run/"manifest.json"),
                          "development_review_sha256":digest(value),"primary_article_sha256":b.sha(run/"article.json"),
                          "prompt_sha256":manifest["prompt_sha256"]}
    write(run/"report.json",report)
    print("review_saved:"+phase+":"+value["decision"])
def browser(run):
    verify(run)
    # Reuse the frozen E00 browser flow; only replace its verify hook in memory for this process.
    b.verify=lambda p:verify(p)
    b.browser(run)
def closeout(run):
    report=b.read(run/"report.json");report["technical"]=verify(run)
    reviews=report["reviews"];accepted=all(reviews.get(p,{}).get("decision")=="accepted" for p in ("development","confirmation"))
    report.update(decision="accepted_bounded_semantic_edit" if accepted else "revise",
                  E01_complete=True,T5_complete=False,E02_executed=False,closed_utc=now(),
                  new_model_calls=len(report["calls"]),
                  http_seconds=round(sum(c.get("http_seconds",180) for c in report["calls"].values()),6),
                  usage={k:sum((c.get("usage") or {}).get(k,0) for c in report["calls"].values())
                         for k in ("prompt_tokens","completion_tokens","total_tokens")},
                  automatic_retries=0,unknown_calls=sum(c["status"]=="unknown" for c in report["calls"].values()))
    report["artifact_hashes"]={str(p.relative_to(run)):b.sha(p) for p in run.rglob("*") if p.is_file() and p.name!="report.json"}
    write(run/"report.json",report)
    print(json.dumps({k:report[k] for k in ("decision","E01_complete","T5_complete","new_model_calls","http_seconds","usage","unknown_calls")},ensure_ascii=False))
def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["prepare","selftest","send","review","verify","browser","closeout"])
    parser.add_argument("--run");parser.add_argument("--phase",choices=["development","confirmation"]);a=parser.parse_args()
    if a.action=="selftest":print(json.dumps(selftest()));return
    if a.action=="prepare":prepare();return
    b.check(bool(a.run),"explicit_run_required");run=Path(a.run).resolve()
    b.check(run.is_relative_to((HERE/"runs").resolve()),"run_scope")
    with (run/"report.json").open("r+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=="send":b.check(bool(a.phase),"phase_required");send(run,a.phase)
        elif a.action=="review":b.check(bool(a.phase),"phase_required");review(run,a.phase)
        elif a.action=="verify":print(json.dumps(verify(run),ensure_ascii=False))
        elif a.action=="browser":browser(run)
        else:closeout(run)
if __name__=="__main__":main()
