"""T5 E05: evidence-bound review feedback; reuse frozen earlier experiment tools."""
from pathlib import Path
import copy, hashlib, html, io, json, re, sys
import run_e03 as t
q,e,b=t.q,t.e,t.b
ROOT,HERE,BASE=t.ROOT,t.HERE,t.BASE
POLICY="E05.bounded_relation_repair_and_finalization.v1"
original_tickets,original_materials=t.tickets_for,t.materials
original_review=t.review
PROMPT=t.PROMPT.replace(
    "각 ticket의 problem_kind와 draft_span은 검수 입력이며 정답 문장은 주어지지 않는다. 현재 문단·주변 근거를 대조하여 그 의미 문제만 해결하라.",
    "각 ticket에는 draft_span과 review_observation이 있다. 관측 또는 검수 질문을 담당 원문과 대조하고, 실제 불일치가 확인되면 그 의미 문제만 수정하라. 관측은 검토할 입력이며 무조건 따라야 할 정답이 아니다. 완성된 교정 문장은 제공하지 않는다."
).replace(
    "정답을 확보하지 못하면 unresolved, 검수 문제를 근거상 확인할 수 없으면 keep으로 판단하고 이유를 남긴다. 근거 없는 수정으로 문제를 숨기지 않는다.",
    "판단할 수 없으면 unresolved로 남긴다. 문제가 없으면 keep과 근거를 반환한다. keep은 관측의 쟁점인 대상·정의·조건·주체·예시·문서 기능의 연결 자체를 검토해야 한다. 같은 단어나 사건이 원문에 등장한다는 사실만으로 연결까지 지지되는 것은 아니다. 실제 불일치가 없으면 표기나 문체를 바꾸지 않는다. 유효한 정보를 삭제해 문제를 숨기지 않는다."
)
write,digest,loads=e.write,e.digest,e.loads


PREVIOUS=HERE/"runs/20261105T145737Z_e04"
CARRY_IDS=["D0","D2","D3","D5"]
PROMPT+="\n추가 편집 원칙: 근거가 명확한 사실과 근거가 불명확한 관계를 구분한다. 불명확한 지시 대상·분모·예시 간 관계를 가까운 다른 명사·집단·대안으로 다시 확정하지 않는다. 확인 가능한 사실과 절차·예시·수치는 유지하고 근거 없는 관계나 의무만 제거하거나 단정 수준을 낮춘다. 원본 영상의 행동 안내는 원본에서 이루어진 안내로 귀속한다. 원본에 링크·위치 안내가 있었다는 사실은 현재 배포 문서에 해당 자원이 존재한다는 근거가 아니다. 제공되지 않은 URL을 추측하지 않는다. 이전 실패 제안은 반례이며 정답이나 새 원문이 아니다. 각 수정의 실제 주장 전체가 근거로 지지되는지 확인하고, 단순 단어 존재나 철자 일치를 성공으로 보지 않는다."

def prior():
    m,r=b.read(PREVIOUS/"manifest.json"),b.read(PREVIOUS/"report.json")
    for rel,h in r["artifact_hashes"].items():b.check(b.sha(PREVIOUS/rel)==h,"carryover_artifact_changed")
    return m,r

def carryover(base):
    m,r=prior();packets=loads(m["jobs"][0]["body"]["messages"][1]["content"])["tickets"]
    selected=[z for z in r["calls"]["development"]["parsed"]["repairs"] if z["ticket_id"] in CARRY_IDS]
    reviewed={z["ticket_id"]:z for z in r["reviews"]["development"]["review_items"]}
    b.check(all(reviewed[z]["status"]=="resolved_in_unadopted_proposal" for z in CARRY_IDS),"carryover_review")
    changes=t.validate({"repairs":selected},[z for z in packets if z["ticket_id"] in CARRY_IDS])
    out=copy.deepcopy(base)
    for ref,(_,p) in e.paragraph_map(out["units"]).items():
        if ref in changes:p["text"]=changes[ref]
    return out

def tickets_for(units):
    if not any(u["unit_id"] in e.PRIMARY for u in units):return original_tickets(units)
    m,_=prior()
    return [copy.deepcopy(z) for z in m["jobs"][0]["review_tickets"] if z["ticket_id"] not in CARRY_IDS]

def observations():
    findings=b.read(BASE/"report.json")["source_review"]["findings"]
    result={f["id"]:f["observation"] for f in findings if f["id"] in t.KINDS}
    result.update({
        "D7":"현재 선택하는 배송 방식과 향후 입점 계획에 따른 선택의 시점·조건이 담당 원문과 같은지 확인한다.",
        "D8":"의도적 오타 예시의 유지와 무관 키워드의 제외가 담당 원문·승인 근거의 범위에 맞는지 확인한다. 원시 전사와 표기가 다르다는 이유만으로 표현을 되돌리지 않는다.",
        "T4_confirmation_claim_strength":"원문에는 파손된 전사와 담장 비유가 있다. 초안의 '진입 장벽이 높을수록 이익 수준도 높아진다'는 단조로운 정도 관계가 그 근거에서 확정되는지 확인한다.",
        "T4_confirmation_denominator_scope":"원문은 48건 중 70%의 특허와 이후 60%의 모방을 언급한다. 초안의 '이 중'이 모방 비율의 분모를 특허 보유 집단으로 좁혔는지 확인한다. 이 STT만으로 그 분모를 새로 확정하지 않는다."
    })
    return result

def materials(units,jobs):
    tickets,packets,readonly=original_materials(units,jobs)
    obs=observations();_,r=prior()
    for ticket,packet in zip(tickets,packets):
        packet["review_observation"]=obs[ticket["review_ref"]]
        if ticket["ticket_id"] in ["D1","D4","D6"]:
            failed=next(z for z in r["calls"]["development"]["parsed"]["repairs"] if z["ticket_id"]==ticket["ticket_id"])
            review=next(z for z in r["reviews"]["development"]["review_items"] if z["ticket_id"]==ticket["ticket_id"])
            packet["previous_attempt"]={"action":failed["action"],"replacement":failed["replacement"],"review_observation":review["observation"]}
    if any(u["unit_id"] in e.PRIMARY for u in units):
        working=carryover(b.read(BASE/"article.json"));mapping=e.paragraph_map(working["units"])
        for row in readonly:row["text"]=mapping[row["paragraph_ref"]][1]["text"]
    return tickets,packets,readonly

def make_job(phase,units,jobs):
    job=t.make_job(phase,units,jobs)
    job["body"]["response_format"]["json_schema"]["name"]="stage3_t5_e05_repair"
    payload=loads(job["body"]["messages"][1]["content"])
    payload["task"]="e05_bounded_relation_repair_and_finalization"
    payload["document_context"]={"delivery":"standalone_html","available_resources":["original_video_link"],"source_position_references":"belong_to_original_video","unprovided_resources_must_not_be_assumed":True}
    job["body"]["messages"][1]["content"]=e.wire(payload).decode()
    job["request_sha256"]=digest(job["body"])
    return job

def selftest():
    _,jobs,base,confirm=e.get_context();working=carryover(base)
    b.check(sum(p!=q for u,v in zip(base["units"],working["units"]) for p,q in zip(u["paragraphs"],v["paragraphs"]))==4,"carryover_count")
    tickets,packets,readonly=materials(base["units"],jobs)
    b.check([z["ticket_id"] for z in packets]==["D1","D4","D6","D7","D8"],"ticket_scope")
    b.check(sum("previous_attempt" in p for p in packets)==3,"failed_attempt_binding")
    mapping=e.paragraph_map(working["units"])
    b.check(all(p["text"]==mapping[p["paragraph_ref"]][1]["text"] for p in readonly),"carryover_context")
    keep={"repairs":[{"ticket_id":p["ticket_id"],"action":"keep","evidence_refs":[],"replacement":"","reason":"계약 검사"} for p in packets]}
    b.check(t.validate(keep,packets)=={},"all_keep_valid")
    forged=copy.deepcopy(keep);forged["repairs"][0]["ticket_id"]="D0"
    try:t.validate(forged,packets)
    except ValueError:pass
    else:raise AssertionError("frozen_carryover_edit_allowed")
    b.check(len(materials(confirm,jobs)[0])==2,"confirmation_count")
    return {"new_checks":7,"inherited_contract_checks":13,"model_calls":0,"semantic_quality_evidence":False}

def identity(run):
    m,r=b.read(run/"manifest.json"),b.read(run/"report.json")
    b.check(m["policy"]==POLICY and b.sha(run/"manifest.json")==r["manifest_sha256"],"manifest_identity")
    b.check(b.sha(Path(__file__))==m["runner_sha256"],"runner_identity")
    for path,h in m["protected"].items():b.check(b.sha(ROOT/path)==h,"protected_changed:"+path)
    for path,h in m["runtime_identity"].items():b.check(b.sha(path)==h,"runtime_changed:"+path)
    for job in m["jobs"]:b.check(digest(job["body"])==job["request_sha256"],"request_changed")
    return m,r,*e.get_context()

def article_for(r,base,jobs):
    article=carryover(base);article.update(candidate="E05",policy=POLICY)
    dev=r["calls"].get("development",{})
    if dev.get("status")=="success" and r["reviews"].get("development",{}).get("decision")!="revise":
        article["units"]=t.apply(article["units"],dev["parsed"],jobs)
    article["prose_modified"]=article["units"]!=base["units"]
    return article

def render(run):
    _,r,_,jobs,base,confirm=identity(run);article=article_for(r,base,jobs)
    write(run/"article.json",article);(run/"article.html").write_text(b.article_html(article),encoding="utf-8")
    body="<h1>E05 원문·검수 관측·수정 전후 대조</h1><p>검수 지원 국소 수정. 자동 발견·blind 확인·최종 사용자 채택과 구분한다.</p>"
    body+="<h2>E04 검수 통과 구절 재사용</h2><p>D0/D2/D3/D5: 원응답 그대로 새 E05 후보에 반영. E04 판정은 revise로 보존.</p>"
    previous_m,previous_r=prior()
    for proposal in previous_r["calls"]["development"]["parsed"]["repairs"]:
        if proposal["ticket_id"] in CARRY_IDS:body+="<pre>"+html.escape(json.dumps(proposal,ensure_ascii=False))+"</pre>"
    for phase,units in [("development",base["units"]),("confirmation",confirm)]:
        tickets,packets,_=materials(units,jobs);call=r["calls"].get(phase,{})
        repairs={x["ticket_id"]:x for x in call.get("parsed",{}).get("repairs",[])}
        body+="<h2>"+phase+"</h2>"
        if phase in r["reviews"]:body+="<pre>"+html.escape(json.dumps(r["reviews"][phase],ensure_ascii=False,indent=2))+"</pre>"
        for ticket,packet in zip(tickets,packets):
            body+="<h3>"+html.escape(ticket["ticket_id"]+" / "+ticket["paragraph_ref"])+"</h3><strong>검수 관측/질문</strong><p>"+html.escape(packet["review_observation"])+"</p><pre>"+html.escape(
                "\n".join(s["segment_ref"]+": "+s["text"] for s in packet["evidence"]))+"</pre><strong>기존 문단</strong><p>"+html.escape(packet["paragraph_text"])+"</p><strong>수정 대상</strong><pre>"+html.escape(packet["draft_span"])+"</pre>"
            if ticket["ticket_id"] in repairs:body+="<strong>모델 제안 (검수 전에는 미채택)</strong><pre>"+html.escape(json.dumps(repairs[ticket["ticket_id"]],ensure_ascii=False,indent=2))+"</pre>"
    (run/"review.html").write_text(b.page("E05 검수 피드백 수정 대조",body),encoding="utf-8")

def verify(run):
    m,r,data,jobs,base,confirm=identity(run);expected=article_for(r,base,jobs)
    b.check(b.read(run/"article.json")==expected,"unlogged_article_change");b.validate(base,data,jobs)
    b.check((run/"article.html").read_text()==b.article_html(expected),"unlogged_html_change")
    for phase,call in r["calls"].items():
        if call.get("response_sha256"):b.check(b.sha(run/(phase+".response.body"))==call["response_sha256"],"response_hash")
        if call.get("status")=="success":t.validate_repairs(call["parsed"],q.phase_scope(base["units"]) if phase=="development" else confirm,jobs)
    out=b.inspect_html((run/"article.html").read_text(),expected)
    out.update(protected_hashes_verified=len(m["protected"]),units=16,paragraphs=40,source_sentences=217,metadata_preserved=True,
               reviewer_assisted=True,autonomous_discovery_validated=False,quality_automatically_accepted=False)
    return out

def prepare():
    existing=list((HERE/"runs").glob("*_e05"))
    if existing:
        b.check(len(existing)==1,"multiple_runs");verify(existing[0]);b.check(not b.read(existing[0]/"report.json")["calls"],"already_executed")
        print("prepared_reused:"+str(existing[0].relative_to(ROOT)));return
    earlier=sorted(p for p in (HERE/"runs").glob("*_e0[0-4]") if p.is_dir())
    b.check(len(earlier)==5,"prior_runs")
    for old in earlier:
        for rel,h in b.read(old/"report.json")["artifact_hashes"].items():b.check(b.sha(old/rel)==h,"prior_artifact")
    data,jobs,base,confirm=e.get_context()
    planned=[make_job("development",q.phase_scope(base["units"]),jobs),make_job("confirmation",confirm,jobs)]
    tests=selftest();tp=q.token_plan(planned)
    runtime={p:h for p,h in b.read(b.UPSTREAM/"config.json")["critical_files"].items() if "/vllm-docker-config/" in p or "/models/llm/" in p}
    for p,h in runtime.items():b.check(b.sha(p)==h,"runtime_changed")
    protected=b.protect()
    for p in [HERE/("run_e0"+str(i)+".py") for i in range(5)]+[ROOT/"tests/stage3/T2/token_plan.py",ROOT/"src/extractube/infer.py"]+[p for old in earlier for p in old.rglob("*") if p.is_file()]:
        protected[str(p.relative_to(ROOT))]=b.sha(p)
    run=HERE/"runs"/(e.datetime.now(e.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_e05");run.mkdir(parents=True,exist_ok=False)
    m={"candidate":"E05","policy":POLICY,"created_utc":e.now(),"runner_sha256":b.sha(Path(__file__)),"runner_source_snapshot":Path(__file__).read_text(),
       "spec_snapshot":b.SPEC.read_text(),"protected":protected,"runtime_identity":runtime,"jobs":planned,"token_plan":tp,"offline_contract_checks":tests,
       "prompt_sha256":hashlib.sha256(PROMPT.encode()).hexdigest(),"limits":{"max_calls":2,"max_http_seconds":360,"request_timeout_seconds":180,"automatic_retries":0},
       "reviewer_assisted":True,"autonomous_discovery_validated":False,"confirmation_is_blind":False,
       "normal_controls_editable":["p_u02:0 first sentence","p_u10:2 last sentence"],"correct_replacement_answers_provided":False,
       "image_policy":"review location-overview utility and native-size access at finalization",
       "carryover":{"run":str(PREVIOUS.relative_to(ROOT)),"manifest_sha256":b.sha(PREVIOUS/"manifest.json"),"report_sha256":b.sha(PREVIOUS/"report.json"),"ticket_ids":CARRY_IDS,"s189_added_only_in_review_provenance":True}}
    write(run/"manifest.json",m);write(run/"report.json",{"candidate":"E05","policy":POLICY,"decision":"prepared_not_executed","manifest_sha256":b.sha(run/"manifest.json"),
        "created_utc":e.now(),"calls":{},"reviews":{},"new_model_calls":0,"http_seconds":0,"automatic_retries":0,"unknown_calls":0,"E05_complete":False,"E06_executed":False,
        "T5_complete":False,"user_accepted":False,"final_article_user_accepted":False,"codex_account_usage":"not_observed"})
    render(run)
    print(json.dumps({"run":str(run.relative_to(ROOT)),"tokens":tp["jobs"],"output_budget":tp["output_budget"],"tickets":[len(j["ticket_ids"]) for j in planned],
                      "owned_sources":[j["owned_source_count"] for j in planned],"tests":tests,"protected":len(protected)},ensure_ascii=False))

def review(run,phase):
    value=loads(sys.stdin.read())
    for key in ["major_unresolved","new_major_regressions","normal_control_regressions"]:
        b.check(type(value.get(key)) is int and value[key]>=0,"review_count:"+key)
    if value["decision"]=="accepted":b.check(value["normal_control_regressions"]==0,"normal_control_gate")
    sys.stdin=io.StringIO(json.dumps(value,ensure_ascii=False));original_review(run,phase)
    render(run)

def closeout(run):
    m,r,_,_,_,_=identity(run);b.check(bool(r["calls"]),"unexecuted_closeout")
    r["technical"]=verify(run);accepted=all(r["reviews"].get(p,{}).get("decision")=="accepted" for p in ["development","confirmation"])
    b.check(r.get("browser",{}).get("passed") and r["browser"]["portableFileSha256"]==b.sha(run/"article.html"),"browser_required")
    b.check("final_source_review" in r and "final_presentation_review" in r,"final_reviews_required")
    accepted=accepted and r["final_source_review"]["major_unresolved"]==0
    r["experiment_series_closed"]=True;r["automatic_next_candidate"]=False
    r.update(decision="accepted_bounded_reviewer_feedback_repair" if accepted else "revise",E05_complete=True,T5_complete=accepted,
             closed_utc=e.now(),new_model_calls=len(r["calls"]),http_seconds=round(sum(x.get("http_seconds",180) for x in r["calls"].values()),6),
             usage={k:sum((x.get("usage") or {}).get(k,0) for x in r["calls"].values()) for k in ["prompt_tokens","completion_tokens","total_tokens"]},
             unknown_calls=sum(x["status"]=="unknown" for x in r["calls"].values()))
    r["artifact_hashes"]={str(p.relative_to(run)):b.sha(p) for p in run.rglob("*") if p.is_file() and p.name!="report.json"}
    write(run/"report.json",r);print(json.dumps({k:r[k] for k in ["decision","new_model_calls","http_seconds","usage"]},ensure_ascii=False))

# Process-local adapters preserve earlier source files and frozen run identities.
t.tickets_for=tickets_for;t.materials=materials;t.POLICY=POLICY;t.PROMPT=PROMPT
t.identity=identity;t.render=render;t.verify=verify;t.prepare=prepare;t.selftest=selftest;t.review=review;t.closeout=closeout
q.output_probes=t.output_probes
e.identity=identity;e.render=render;e.verify=verify;e.validate_edits=t.validate_repairs;e.apply=t.apply
e.inspect_patch_report=lambda units,value,jobs:t.inspect(value,units,jobs)
if __name__=="__main__":t.main()
