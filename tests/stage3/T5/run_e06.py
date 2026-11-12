"""T5 E06: de-specification and medium-deixis local repair; reuse frozen E00-E05 tools."""
from pathlib import Path
import copy, hashlib, html, io, json, re, sys
import run_e03 as t
q,e,b=t.q,t.e,t.b
ROOT,HERE,BASE=t.ROOT,t.HERE,t.BASE
POLICY="E06.de_specification_and_medium_deixis_repair.v1"
t.MAX_REASON=160
original_tickets,original_materials=t.tickets_for,t.materials
original_review=t.review
PROMPT=t.PROMPT.replace(
    "각 ticket의 problem_kind와 draft_span은 검수 입력이며 정답 문장은 주어지지 않는다. 현재 문단·주변 근거를 대조하여 그 의미 문제만 해결하라.",
    "각 ticket에는 draft_span과 review_observation이 있다. 관측 또는 검수 질문을 담당 원문 및 document_context와 대조하고, 실제 과잉 단정·불일치가 확인되면 반드시 action='replace'로 그 의미 문제를 수정하라. 완성된 교정 정답 문장은 제공하지 않는다."
).replace(
    "정답을 확보하지 못하면 unresolved, 검수 문제를 근거상 확인할 수 없으면 keep으로 판단하고 이유를 남긴다. 근거 없는 수정으로 문제를 숨기지 않는다.",
    "원문의 지시 대상·결합 의도·비율 분모·비례 관계가 STT만으로 특정되지 않는다는 사실은 unresolved(수정 포기) 사유가 아니라, 현재 draft_span에 들어간 과잉 단정·특정 구절을 제거·완화하는 action='replace'의 근거다. draft_span에 근거 없는 의무, 합성 인용, 축소된 분모, 단조 비례 단정, 또는 현재 문서에 없는 화면 위치 지시가 남아 있다면 unresolved나 keep으로 방치하지 말고 반드시 action='replace'로 확인된 사실과 앵커만 남기도록 수정한다. keep은 draft_span의 모든 관계·범위·조건·예시·매체 위치가 추가 단정 없이 원문 및 document_context와 완전히 부합할 때만 선택한다. 유효한 정보를 삭제해 문제를 숨기지 않는다."
)
PROMPT+=(
    "\n과잉 특정 절제 및 매체 직시어 탈고정 원칙:\n"
    "1. 지시 대상이나 비율 분모가 원문만으로 특정되지 않을 때는 다른 명사('현재 화면 영역' 등)로 재특정하지 말고, 원문에서 확인되는 선행 사실·사전 준비 조건·각각의 수치만 유지한 채 모호한 지시어에서 파생된 의무('그대로 유지해야 한다' 등)나 분모 한정어('이 중' 등)만 제거하라.\n"
    "2. 원본 영상 발화에 '아래 링크' 등 위치 안내가 있더라도 현재 독립 HTML 문서(document_context)에는 해당 초대 링크가 없고 원본 영상 링크만 있으므로 keep으로 유지할 수 없다. 내부 경고를 추가하지 말고 action='replace'를 사용하여, source_owned_anchors('소규모 단톡방', '위탁 판매', '온라인 셀링', '링크', '연락처' 등)와 화자의 운영·초대 안내 사실은 모두 보존하되 현재 문서 화면을 가리키는 위치 수식어('화면 하단의' 등)와 직접 클릭 지시를 덜어내어 화자가 안내한 내용으로만 서술하라.\n"
    "3. 원문에 따로 등장한 입력 예시들을 원문에 없는 단일 합성 인용 문자열로 묶지 말고, 동시에 '~만 입력하거나'처럼 상호 배타적 대안으로 단정하지도 마라. 원문 발화처럼 첫 예시를 입력해도 되고 여기에 두 번째 표현까지 넣어 입력할 수도 있다는 열린 형태로 두 개별 인용어를 보존하며, reason에서도 결합/대안 중 하나로 단정하지 마라.\n"
    "4. 전사가 파손된 비유 구간에서는 existing_unresolved 메모를 근거로 삼지 말고, 근거 없는 단조 비례 구절('~할수록 ~도 높아진다' 등)을 제거해 앞뒤 원문(source/context)에서 확인되는 기본 역할만 서술하라.\n"
    "5. replacement 안에서 작은따옴표('...')로 감싼 모든 표현은 반드시 선택한 evidence_refs 세그먼트 본문에 실제로 포함되어야 하므로, 문장 내 인용어들의 출처 세그먼트(최대 3개)를 빠짐없이 evidence_refs에 포함하고 원문에 없는 어구를 작은따옴표로 새로 감싸지 마라.\n"
    "6. previous_attempts가 있으면 이전 시도가 왜 실패했는지(다른 명사로 재특정, 대안 단정, 또는 unresolved/keep 선택으로 기존 오류 문장 방치)를 확인하고 같은 실패를 반복하지 마라."
)
write,digest,loads=e.write,e.digest,e.loads

PREVIOUS_E04=HERE/"runs/20261105T145737Z_e04"
PREVIOUS_E05=HERE/"runs/20261105T152605Z_e05"
CARRY_IDS=["D0","D2","D3","D5"]

def prior():
    m4,r4=b.read(PREVIOUS_E04/"manifest.json"),b.read(PREVIOUS_E04/"report.json")
    m5,r5=b.read(PREVIOUS_E05/"manifest.json"),b.read(PREVIOUS_E05/"report.json")
    for root_dir,rep in [(PREVIOUS_E04,r4),(PREVIOUS_E05,r5)]:
        for rel,h in rep["artifact_hashes"].items():
            b.check(b.sha(root_dir/rel)==h,"prior_artifact_changed:"+str(root_dir/rel))
    return m4,r4,m5,r5

def carryover(base):
    m4,r4,_,_=prior()
    packets=loads(m4["jobs"][0]["body"]["messages"][1]["content"])["tickets"]
    selected=[z for z in r4["calls"]["development"]["parsed"]["repairs"] if z["ticket_id"] in CARRY_IDS]
    reviewed={z["ticket_id"]:z for z in r4["reviews"]["development"]["review_items"]}
    b.check(all(reviewed[z]["status"]=="resolved_in_unadopted_proposal" for z in CARRY_IDS),"carryover_review")
    changes=t.validate({"repairs":selected},[z for z in packets if z["ticket_id"] in CARRY_IDS])
    out=copy.deepcopy(base)
    for ref,(_,p) in e.paragraph_map(out["units"]).items():
        if ref in changes:p["text"]=changes[ref]
    b.check(out["units"]==b.read(PREVIOUS_E05/"article.json")["units"],"e05_carryover_parity")
    return out

def tickets_for(units):
    if not any(u["unit_id"] in e.PRIMARY for u in units):return original_tickets(units)
    m4,_,_,_=prior()
    return [copy.deepcopy(z) for z in m4["jobs"][0]["review_tickets"] if z["ticket_id"] not in CARRY_IDS]

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
    obs=observations();_,r4,_,r5=prior()
    for ticket,packet in zip(tickets,packets):
        packet["review_observation"]=obs[ticket["review_ref"]]
        if ticket["ticket_id"] in ["D1","D4","D6"]:
            attempts=[]
            for cand,rep in [("E04",r4),("E05",r5)]:
                failed=next(z for z in rep["calls"]["development"]["parsed"]["repairs"] if z["ticket_id"]==ticket["ticket_id"])
                rev=next(z for z in rep["reviews"]["development"]["review_items"] if z["ticket_id"]==ticket["ticket_id"])
                attempts.append({"candidate":cand,"action":failed["action"],"replacement":failed["replacement"],"review_observation":rev["observation"]})
            packet["previous_attempts"]=attempts
    if any(u["unit_id"] in e.PRIMARY for u in units):
        working=carryover(b.read(BASE/"article.json"));mapping=e.paragraph_map(working["units"])
        for row in readonly:row["text"]=mapping[row["paragraph_ref"]][1]["text"]
    return tickets,packets,readonly

def make_job(phase,units,jobs):
    job=t.make_job(phase,units,jobs)
    job["body"]["response_format"]["json_schema"]["name"]="stage3_t5_e06_repair"
    payload=loads(job["body"]["messages"][1]["content"])
    payload["task"]="e06_de_specification_and_medium_deixis_repair"
    payload["document_context"]={"delivery":"standalone_html","available_resources":["original_video_link"],"source_position_references":"belong_to_original_video","unprovided_resources_must_not_be_assumed":True}
    job["body"]["messages"][1]["content"]=e.wire(payload).decode()
    job["request_sha256"]=digest(job["body"])
    return job

def selftest():
    _,jobs,base,confirm=e.get_context();working=carryover(base)
    b.check(sum(p!=q for u,v in zip(base["units"],working["units"]) for p,q in zip(u["paragraphs"],v["paragraphs"]))==4,"carryover_count")
    tickets,packets,readonly=materials(base["units"],jobs)
    b.check([z["ticket_id"] for z in packets]==["D1","D4","D6","D7","D8"],"ticket_scope")
    b.check(sum(len(p.get("previous_attempts",[]))==2 for p in packets)==3,"dual_failed_attempt_binding")
    mapping=e.paragraph_map(working["units"])
    b.check(all(p["text"]==mapping[p["paragraph_ref"]][1]["text"] for p in readonly),"carryover_context")
    b.check(t.schema(packets)["properties"]["repairs"]["items"]["properties"]["reason"]["maxLength"]==160,"expanded_reason_budget")
    keep={"repairs":[{"ticket_id":p["ticket_id"],"action":"keep","evidence_refs":[],"replacement":"","reason":"계약 검사"} for p in packets]}
    b.check(t.validate(keep,packets)=={},"all_keep_valid")
    forged=copy.deepcopy(keep);forged["repairs"][0]["ticket_id"]="D0"
    try:t.validate(forged,packets)
    except ValueError:pass
    else:raise AssertionError("frozen_carryover_edit_allowed")
    ct,cp,_=materials(confirm,jobs)
    b.check(len(ct)==len(cp)==2 and [z["ticket_id"] for z in cp]==["C0","C1"],"confirmation_count")
    for job in [make_job("development",q.phase_scope(base["units"]),jobs),make_job("confirmation",confirm,jobs)]:
        payload=loads(job["body"]["messages"][1]["content"])
        b.check(all("review_ref" not in p and "severity" not in p and "next_change" not in p and "replacement" not in p for p in payload["tickets"]),"no_answers_or_control_labels")
    return {"new_checks":9,"inherited_contract_checks":13,"model_calls":0,"semantic_quality_evidence":False}

def identity(run):
    m,r=b.read(run/"manifest.json"),b.read(run/"report.json")
    b.check(m["policy"]==POLICY and b.sha(run/"manifest.json")==r["manifest_sha256"],"manifest_identity")
    b.check(b.sha(Path(__file__))==m["runner_sha256"],"runner_identity")
    for path,h in m["protected"].items():b.check(b.sha(ROOT/path)==h,"protected_changed:"+path)
    for path,h in m["runtime_identity"].items():b.check(b.sha(path)==h,"runtime_changed:"+path)
    for job in m["jobs"]:b.check(digest(job["body"])==job["request_sha256"],"request_changed")
    return m,r,*e.get_context()

def article_for(r,base,jobs):
    article=carryover(base);article.update(candidate="E06",policy=POLICY)
    dev=r["calls"].get("development",{})
    if dev.get("status")=="success" and r["reviews"].get("development",{}).get("decision")!="revise":
        article["units"]=t.apply(article["units"],dev["parsed"],jobs)
    article["prose_modified"]=article["units"]!=base["units"]
    return article

def render(run):
    _,r,_,jobs,base,confirm=identity(run);article=article_for(r,base,jobs)
    write(run/"article.json",article);(run/"article.html").write_text(b.article_html(article),encoding="utf-8")
    body="<h1>E06 원문·검수 관측·수정 전후 대조</h1><p>검수 지원 과잉 특정·단정 절제 및 매체 직시어 탈고정 국소 수정. 자동 발견·blind 확인·최종 사용자 채택과 구분한다.</p>"
    body+="<h2>E04 검수 통과 구절 재사용 (동결 유지)</h2><p>D0/D2/D3/D5: 원응답 그대로 E06 후보에 반영.</p>"
    _,r4,_,_=prior()
    for proposal in r4["calls"]["development"]["parsed"]["repairs"]:
        if proposal["ticket_id"] in CARRY_IDS:body+="<pre>"+html.escape(json.dumps(proposal,ensure_ascii=False))+"</pre>"
    for phase,units in [("development",base["units"]),("confirmation",confirm)]:
        tickets,packets,_=materials(units,jobs);call=r["calls"].get(phase,{})
        repairs={x["ticket_id"]:x for x in call.get("parsed",{}).get("repairs",[])}
        body+="<h2>"+phase+"</h2>"
        if phase in r["reviews"]:body+="<pre>"+html.escape(json.dumps(r["reviews"][phase],ensure_ascii=False,indent=2))+"</pre>"
        for ticket,packet in zip(tickets,packets):
            body+="<h3>"+html.escape(ticket["ticket_id"]+" / "+ticket["paragraph_ref"])+"</h3><strong>검수 관측/질문</strong><p>"+html.escape(packet["review_observation"])+"</p><pre>"+html.escape(
                "\n".join(s["segment_ref"]+": "+s["text"] for s in packet["evidence"]))+"</pre><strong>기존 문단</strong><p>"+html.escape(packet["paragraph_text"])+"</p><strong>수정 대상</strong><pre>"+html.escape(packet["draft_span"])+"</pre>"
            if "previous_attempts" in packet:
                body+="<strong>이전 실패 이력 (E04/E05)</strong><pre>"+html.escape(json.dumps(packet["previous_attempts"],ensure_ascii=False,indent=2))+"</pre>"
            if ticket["ticket_id"] in repairs:
                rep=repairs[ticket["ticket_id"]]
                body+="<strong>E06 모델 제안</strong><pre>"+html.escape(json.dumps(rep,ensure_ascii=False,indent=2))+"</pre>"
                if rep.get("action")=="replace" and rep.get("replacement"):
                    patched_p=packet["paragraph_text"].replace(packet["draft_span"],rep["replacement"],1)
                    body+="<strong>교체 반영 문단</strong><p>"+html.escape(patched_p)+"</p>"
    (run/"review.html").write_text(b.page("E06 검수 피드백 수정 대조",body),encoding="utf-8")

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
    existing=list((HERE/"runs").glob("*_e06"))
    if existing:
        b.check(len(existing)==1,"multiple_runs");verify(existing[0]);b.check(not b.read(existing[0]/"report.json")["calls"],"already_executed")
        print("prepared_reused:"+str(existing[0].relative_to(ROOT)));return
    earlier=sorted(p for p in (HERE/"runs").glob("*_e0[0-5]") if p.is_dir())
    b.check(len(earlier)==6,"prior_runs")
    for old in earlier:
        for rel,h in b.read(old/"report.json")["artifact_hashes"].items():b.check(b.sha(old/rel)==h,"prior_artifact")
    data,jobs,base,confirm=e.get_context()
    planned=[make_job("development",q.phase_scope(base["units"]),jobs),make_job("confirmation",confirm,jobs)]
    tests=selftest();tp=q.token_plan(planned)
    runtime={p:h for p,h in b.read(b.UPSTREAM/"config.json")["critical_files"].items() if "/vllm-docker-config/" in p or "/models/llm/" in p}
    for p,h in runtime.items():b.check(b.sha(p)==h,"runtime_changed")
    protected=b.protect()
    for p in [HERE/("run_e0"+str(i)+".py") for i in range(6)]+[ROOT/"tests/stage3/T2/token_plan.py",ROOT/"src/extractube/infer.py"]+[p for old in earlier for p in old.rglob("*") if p.is_file()]:
        protected[str(p.relative_to(ROOT))]=b.sha(p)
    run=HERE/"runs"/(e.datetime.now(e.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_e06");run.mkdir(parents=True,exist_ok=False)
    m={"candidate":"E06","policy":POLICY,"created_utc":e.now(),"runner_sha256":b.sha(Path(__file__)),"runner_source_snapshot":Path(__file__).read_text(),
       "spec_snapshot":b.SPEC.read_text(),"protected":protected,"runtime_identity":runtime,"jobs":planned,"token_plan":tp,"offline_contract_checks":tests,
       "prompt_sha256":hashlib.sha256(PROMPT.encode()).hexdigest(),"limits":{"max_calls":2,"max_http_seconds":360,"request_timeout_seconds":180,"automatic_retries":0},
       "reviewer_assisted":True,"autonomous_discovery_validated":False,"confirmation_is_blind":False,
       "normal_controls_editable":["p_u02:0 first sentence","p_u10:2 last sentence"],"correct_replacement_answers_provided":False,
       "image_policy":"retain_as_location_overview_with_native_size_access",
       "carryover":{"run":str(PREVIOUS_E04.relative_to(ROOT)),"manifest_sha256":b.sha(PREVIOUS_E04/"manifest.json"),"report_sha256":b.sha(PREVIOUS_E04/"report.json"),
                    "e05_run":str(PREVIOUS_E05.relative_to(ROOT)),"e05_report_sha256":b.sha(PREVIOUS_E05/"report.json"),"ticket_ids":CARRY_IDS,"s189_added_only_in_review_provenance":True}}
    write(run/"manifest.json",m);write(run/"report.json",{"candidate":"E06","policy":POLICY,"decision":"prepared_not_executed","manifest_sha256":b.sha(run/"manifest.json"),
        "created_utc":e.now(),"calls":{},"reviews":{},"new_model_calls":0,"http_seconds":0,"automatic_retries":0,"unknown_calls":0,"E06_complete":False,"E07_executed":False,
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
    accepted=accepted and r["final_source_review"]["major_unresolved"]==0 and r["final_source_review"]["new_major_regressions"]==0
    r["experiment_series_closed"]=True;r["automatic_next_candidate"]=False
    r.update(decision="accepted_bounded_reviewer_feedback_repair" if accepted else "revise",E06_complete=True,T5_complete=accepted,
             closed_utc=e.now(),new_model_calls=len(r["calls"]),http_seconds=round(sum(x.get("http_seconds",180) for x in r["calls"].values()),6),
             usage={k:sum((x.get("usage") or {}).get(k,0) for x in r["calls"].values()) for k in ["prompt_tokens","completion_tokens","total_tokens"]},
             unknown_calls=sum(x["status"]=="unknown" for x in r["calls"].values()))
    r["artifact_hashes"]={str(p.relative_to(run)):b.sha(p) for p in run.rglob("*") if p.is_file() and p.name!="report.json"}
    write(run/"report.json",r);print(json.dumps({k:r[k] for k in ["decision","E06_complete","T5_complete","new_model_calls","http_seconds","usage"]},ensure_ascii=False))

# Process-local adapters preserve earlier source files and frozen run identities.
t.tickets_for=tickets_for;t.materials=materials;t.POLICY=POLICY;t.PROMPT=PROMPT
t.identity=identity;t.render=render;t.verify=verify;t.prepare=prepare;t.selftest=selftest;t.review=review;t.closeout=closeout
q.output_probes=t.output_probes
e.identity=identity;e.render=render;e.verify=verify;e.validate_edits=t.validate_repairs;e.apply=t.apply
e.inspect_patch_report=lambda units,value,jobs:t.inspect(value,units,jobs)
if __name__=="__main__":t.main()
