"""Bounded Stage 3 T1 experiment. No product, service, or model configuration mutations."""
from __future__ import annotations
import argparse,base64,hashlib,html,json,os,re,subprocess,sys,time,uuid,fcntl
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
from contracts import loads,parse_response,eligible
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"src"))
from extractube.infer import HttpTransport
HREL="tests/stage2/T5/runs/20261028T184105Z_visual_closeout/stage3_handoff.json"
HANDOFF_SHA="b380c419547c9f1d6c74a89e72e73a7e2d54b59d1d9b1734ccb0b5634a2a8cb5"
ASSETS=["V01","V02","V08","V10","V05","V07"]
AUDIO_SHA={"Fw-8hZhnH3Q":"7a7e476c57c93553a1da4b5b98eacd90e2d01dcf33920a24cb84759ad2fa8c4b","H2WN130OzLg":"6d937a19ac2a48ca21050553a2e4e83609648d17dc3451bb0d279c25c14cec93","7EJ-xotXiSE":"3d0b35dacf2454fb1a0263b0d77004a2827c6f93c63ad24f92a67f306edbdb4f","iVelddb1qQ4":"cda326b1cda9bd8c4bbdd083b66caa4f04f047f7a0011ede311cd333a814f6c8","wFikV0Y7NIk":"0e7be2cfc5d46bfbf7b5cb32837584ad1e26ff2d47e55708267cc339822c0dd6","dgGhsYSVzKc":"995a7e8ca0e9d5dc2977bfb09c5e658c92889dcba79a94bad219edd4f447604e"}
CONTAINER="vllm-docker-config-vllm-runtime-1"
def wire(obj):return json.dumps(obj,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
def digest(obj):return hashlib.sha256(wire(obj)).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return loads(Path(p).read_text(encoding="utf-8"))
def write(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+".tmp")
    with tmp.open("wb") as f:f.write(wire(obj));f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)
def bound_sha(p,expected):
    got=sha(p)
    if got!=expected:raise ValueError("source_hash_changed:"+str(p))
    return got
def current():
    pointer=HERE/"campaign.json"
    if not pointer.exists():raise ValueError("prepare_required")
    return HERE/"runs"/read(pointer)["run_id"]
def code_identity():
    return {p.name:sha(p) for p in [HERE/"run_t1.py",HERE/"contracts.py",HERE/"token_plan.py"]}
def check_identity(run):
    c=read(run/"config.json")
    state=read(run/"state.json")
    if state["config_sha256"]!=digest(c) or state["plan_sha256"]!=sha(run/"plan.json") or state["input_sha256"]!=sha(run/"input_manifest.json"):raise ValueError("campaign_identity_changed")
    for task,h in c["prompts"].items():
        bound_sha(run/c.get("prompt_files",{}).get(task,task+"_prompt.txt"),h)
    if c["code"]!=code_identity():raise ValueError("experiment_code_changed")
    for path,h in c["critical_files"].items():bound_sha(Path(path),h)
    return c
def reserve(records,timeout=300,max_calls=30,max_seconds=2400):
    started=[x for x in records if x["status"]!="not_sent"]
    seconds=sum(x.get("http_seconds",x.get("reserved_seconds",0)) for x in started)
    if any(x["status"] in ("started","unknown") for x in started):raise ValueError("blocked_unknown")
    if len(started)>=max_calls or seconds>=max_seconds:raise ValueError("paused_budget")
    return min(timeout,max_seconds-seconds)
def records(run,recover=True):
    result=[]
    for p in sorted((run/"journal").glob("*.json")):
        x=read(p)
        if recover and x["status"]=="started":
            x.update(status="unknown",error="interrupted_after_started")
            write(p,x)
        result.append(x)
    return result
def contexts_input():
    critical={};sources={};groups=[];assets=[];h=ROOT/HREL
    bound_sha(h,HANDOFF_SHA);critical[str(h)]=HANDOFF_SHA;j=read(h)
    sys.path.insert(0,str(ROOT/"tests/stage2"))
    from experiment_paths import resolve_path
    needed={next(a for a in j["assets"] if a["asset_id"]==aid)["unit_id"] for aid in ASSETS}
    units=[u for u in j["semantic_units"] if u["video_id"]=="Fw-8hZhnH3Q" or u["unit_id"] in needed]
    for vid,hsh in AUDIO_SHA.items():
        p=ROOT/f"data/{vid}/audio/alignment_sentences.json";bound_sha(p,hsh);critical[str(p)]=hsh;s=read(p)
        if s["time_reference"]!="audio_start" or s["overlap_deduplicated"] is not True or not s["sentence_policy_id"]:raise ValueError("audio_contract")
        meta=ROOT/f"data/{vid}/metadata.json";critical[str(meta)]=sha(meta);m=read(meta)
        parents={"video":[],"wav":["video"],"asr":["wav"],"fa":["wav","asr"],"sentences":["fa"]}
        ids={}
        for kind,up in parents.items():
            row=m["results"][kind]
            if row["kind"]!=kind or not row.get("result_id") or row["upstream"]!=[ids[k] for k in up]:raise ValueError("metadata_chain")
            ids[kind]={k:row[k] for k in ("kind","result_id","sha256")}
        if m["results"]["sentences"]["sha256"]!=hsh:raise ValueError("metadata_sentence_hash")
        ranges=sorted({(u["context"]["sentence_start"],u["context"]["sentence_end"]) for u in units if u["video_id"]==vid})
        chosen=range(len(s["chunks"])) if vid=="Fw-8hZhnH3Q" else sorted({i for a,z in ranges for i in range(a,z)})
        ss=[]
        for i in chosen:
            row=s["chunks"][i];items=row["items"]
            if type(row["index"]) is not int or not items or not row["text"]:raise ValueError("sentence_contract")
            a,z=Decimal(str(items[0]["start_time"])),Decimal(str(items[-1]["end_time"]))
            if not a.is_finite() or not z.is_finite() or a<0 or z<a:raise ValueError("sentence_time")
            ss.append({"video_id":vid,"sentence_index":i,"source_chunk_index":row["index"],"text":row["text"],"language":row["language"],"audio_start":str(a),"audio_end":str(z),"source_ref":{"path":str(p.relative_to(ROOT)),"sha256":hsh}})
        sources[vid]=ss;groups.append({"video_id":vid,"role":"primary_full" if vid=="Fw-8hZhnH3Q" else "auxiliary_context","ranges":[[0,len(s["chunks"])]] if vid=="Fw-8hZhnH3Q" else [list(x) for x in ranges],"omitted_audio_chunks":s["omitted_chunks"],"source_sha256":hsh})
    for u in units:
        p=resolve_path(ROOT/u["t1_source"]["canonical_path"]);bound_sha(p,u["t1_source"]["sha256"]);critical[str(p)]=sha(p)
        mapping={s["sentence_index"]:s for s in sources[u["video_id"]]}
        for old in u["context"]["sentences"]:
            new=mapping[old["index"]]
            if old["text"]!=new["text"] or old["source_chunk_index"]!=new["source_chunk_index"]:raise ValueError("legacy_text_binding")
            if any(abs(Decimal(str(old[k]))-Decimal(new[v]))>Decimal("0.000001") for k,v in [("start_time","audio_start"),("end_time","audio_end")]):raise ValueError("legacy_time_binding")
    fw=[u for u in units if u["video_id"]=="Fw-8hZhnH3Q"]
    if len(fw)!=20 or [x["index"] for u in fw for x in u["context"]["sentences"]]!=list(range(217)):raise ValueError("primary_coverage")
    for aid in ASSETS:
        a=next(x for x in j["assets"] if x["asset_id"]==aid);p=h.parent/a["path"]
        bound_sha(p,a["sha256"]);critical[str(p)]=a["sha256"]
        original=resolve_path(ROOT/a["source"]["full"]["path"]);bound_sha(original,a["sha256"]);critical[str(original)]=a["sha256"]
        u=next(u for u in units if u["unit_id"]==a["unit_id"])
        assets.append({**a,"video_id":u["video_id"],"path":str(p),"context":u["context"],"role":"confirm" if aid in ("V05","V07") else "dev"})
    return {"sources":sources,"groups":groups,"assets":assets,"critical_files":critical,"upstream_counts":j["counts"]}
def prompts():
    text=(ROOT/"local/stage3/t1_execution.md").read_text(encoding="utf-8");f=chr(96)*3
    return {k:re.search(r"### 9\.\d "+title+r"\n\n"+f+r"text\n(.*?)\n"+f,text,re.S)[1]+"\n" for k,title in [("audio","S3_T1_AUDIO_P0"),("visual","S3_T1_VISUAL_P0")]}
def substitute(template,values):
    pat=re.compile("|".join(re.escape("{"+k+"}") for k in values))
    return pat.sub(lambda m:wire(values[m[0][1:-1]]).decode(),template)
def build_jobs(inputs,pr):
    jobs=[]
    def public(s):return {"sentence_index":s["sentence_index"],"text":s["text"]}
    def audio(vid,a,z,phase):
        ss={s["sentence_index"]:s for s in inputs["sources"][vid]}
        before=[ss[i] for i in range(max(0,a-2),a) if i in ss];after=[ss[i] for i in range(z,z+2) if i in ss]
        target=[ss[i] for i in range(a,z)]
        rendered=substitute(pr["audio"],{"context_before_json":[public(s) for s in before],"target_json":[public(s) for s in target],"context_after_json":[public(s) for s in after]})
        jobs.append({"job_id":f"audio_{vid}_{a}_{z}","task":"audio","video_id":vid,"phase":phase,"gate":vid=="Fw-8hZhnH3Q" and a in (0,96),"targets":list(range(a,z)),"allowed":[s["sentence_index"] for s in before+target+after],"body":body([{"type":"text","text":rendered}])})
    for i in range(0,217,12):audio("Fw-8hZhnH3Q",i,min(217,i+12),"dev")
    audio("7EJ-xotXiSE",164,176,"confirm");audio("iVelddb1qQ4",279,291,"confirm")
    for a in inputs["assets"]:
        context=[{"sentence_index":s["index"],"text":s["text"]} for s in a["context"]["sentences"]]
        rendered=substitute(pr["visual"],{"asset_json":{"asset_id":a["asset_id"],"video_id":a["video_id"],"actual_video_seconds":a["source"]["video_seconds"]},"audio_context_json":context})
        content=[{"type":"text","text":rendered},{"type":"text","text":"asset_id="+a["asset_id"]},{"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(Path(a["path"]).read_bytes()).decode()}}]
        jobs.append({"job_id":"visual_"+a["asset_id"],"task":"visual","video_id":a["video_id"],"asset_id":a["asset_id"],"phase":a["role"],"gate":a["asset_id"] in ("V01","V02"),"allowed":[s["sentence_index"] for s in context],"targets":[],"body":body(content)})
    return jobs
def body(content):
    return {"model":"qwen3.8-27b","messages":[{"role":"user","content":content}],"temperature":0,"max_tokens":4096,"stream":False,"chat_template_kwargs":{"enable_thinking":False}}
def prepare():
    if (HERE/"campaign.json").exists():
        run=current();check_identity(run);print(json.dumps({"reused_prepare":str(run)}));return
    inputs=contexts_input();pr=prompts();jobs=build_jobs(inputs,pr)
    profile=ROOT.parent/"vllm-docker-config/configs/models/qwen3.8-27b.env";common=profile.parents[1]/"common.env"
    inputs["critical_files"].update({str(profile):sha(profile),str(common):sha(common),str(ROOT/"src/extractube/infer.py"):sha(ROOT/"src/extractube/infer.py"),str(ROOT/"tests/stage2/experiment_paths.py"):sha(ROOT/"tests/stage2/experiment_paths.py")})
    model=ROOT.parent/"models/llm/tf/qwen3.8-27b"
    for name in ("config.json","tokenizer_config.json","tokenizer.json","vocab.json","preprocessor_config.json","chat_template.jinja","chat_template_safe_v2.jinja"):
        p=model/name
        if p.exists():inputs["critical_files"][str(p)]=sha(p)
    helper=(HERE/"token_plan.py").read_text()
    result=subprocess.run(["docker","exec","-i",CONTAINER,"python3","-c",helper],input=wire({"jobs":jobs}),capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError("token_plan_failed:"+result.stderr.decode(errors="replace")[-3000:])
    token_plan=loads(result.stdout.decode());by={x["job_id"]:x for x in token_plan["jobs"]}
    for j in jobs:
        j["token_plan"]=by[j["job_id"]]
        if j["token_plan"]["input_tokens"]>12000 or j["token_plan"]["input_tokens"]+4096+1024>32768:raise ValueError("token_budget")
        if len(wire(j["body"]))>33554432:raise ValueError("request_bytes")
    config={"policy":"stage3.t1.p0.v1","code":code_identity(),"critical_files":inputs["critical_files"],"prompts":{k:hashlib.sha256(v.encode()).hexdigest() for k,v in pr.items()},"model":"qwen3.8-27b","endpoint":"http://127.0.0.1:8095/infer?model=qwen-vlm","limits":{"max_calls":30,"max_http_seconds":2400,"timeout":300},"reader_policy":"pending"}
    key=digest(config)
    for j in jobs:j["job_key"]=digest({"request":j["body"],"task":j["task"],"critical":inputs["critical_files"],"code":config["code"]})
    rid=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid.uuid4().hex[:8];run=HERE/"runs"/rid;run.mkdir(parents=True)
    write(run/"config.json",config);write(run/"input_manifest.json",inputs);write(run/"plan.json",jobs);write(run/"token_plan.json",token_plan)
    for k,v in pr.items():(run/(k+"_prompt.txt")).write_text(v,encoding="utf-8")
    for name in config["code"]:(run/"source_code").mkdir(exist_ok=True);(run/"source_code"/name).write_bytes((HERE/name).read_bytes())
    write(run/"state.json",{"run_id":rid,"config_sha256":key,"plan_sha256":sha(run/"plan.json"),"input_sha256":sha(run/"input_manifest.json"),"reader_policy":"pending","execution_status":"prepared","user_accepted":False})
    write(HERE/"campaign.json",{"run_id":rid});report(run)
    print(json.dumps({"prepared":rid,"jobs":len(jobs),"input_tokens":[min(x["input_tokens"] for x in token_plan["jobs"]),max(x["input_tokens"] for x in token_plan["jobs"])]}))
def send_job(run,config,job):
    p=run/"journal"/(job["job_id"]+".json")
    if p.exists():
        old=read(p)
        if old["job_key"]!=job["job_key"]:raise ValueError("job_key_changed")
        if old["status"]!="success":raise ValueError("terminal_job_not_resendable:"+old["status"])
        raw=(run/"responses"/(job["job_id"]+".body")).read_bytes()
        parsed,info=parse_response(raw,old["http_status"],job)
        if parsed!=old["parsed"] or hashlib.sha256(raw).hexdigest()!=old["response_sha256"]:raise ValueError("cache_reparse")
        return old
    check_identity(run);timeout=reserve(records(run))
    request=wire(job["body"]);req=run/"requests"/(job["job_id"]+".json");write(req,job["body"])
    rec={"job_id":job["job_id"],"job_key":job["job_key"],"task":job["task"],"video_id":job["video_id"],"asset_id":job.get("asset_id"),"phase":job["phase"],"status":"started","reserved_seconds":timeout,"request_sha256":hashlib.sha256(request).hexdigest(),"started_at":datetime.now(timezone.utc).isoformat()}
    write(p,rec);started=time.monotonic();response=None
    def headers(status,values):write(run/"responses"/(job["job_id"]+".headers.json"),{"status":status,"headers":values})
    try:
        response=HttpTransport().send(config["endpoint"],request,timeout,4194304,headers)
        rec["http_status"]=response["status"]
        raw=response["body"];dest=run/"responses"/(job["job_id"]+".body");dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw)
        rec["response_sha256"]=hashlib.sha256(raw).hexdigest();rec["response_complete"]=response["complete"]
        try:
            outer=loads(raw.decode());rec["usage"]=outer.get("usage");rec["finish_reason"]=outer.get("choices",[{}])[0].get("finish_reason")
        except (ValueError,UnicodeError,IndexError,TypeError,AttributeError):pass
        if not response["complete"]:rec.update(status="unknown",error=response["error"] or "incomplete_body")
        else:
            try:parsed,info=parse_response(raw,response["status"],job);rec.update(status="success",parsed=parsed,**info)
            except (ValueError,KeyError,TypeError) as e:rec.update(status="failed",error=str(e))
    except (OSError,TimeoutError,ValueError) as e:rec.update(status="unknown",error=type(e).__name__+":"+str(e))
    rec["http_seconds"]=round(time.monotonic()-started,6);write(p,rec)
    print(json.dumps({"job":job["job_id"],"status":rec["status"],"seconds":rec["http_seconds"],"tokens":rec.get("usage")},ensure_ascii=False),flush=True)
    return rec
def validate_gate(gate,config):
    if gate.get("config_sha256")!=digest(config) or gate.get("reader_policy") not in ("limited","excluded") or not gate.get("reviewer") or not gate.get("reason"):raise ValueError("unbound_gate_review")

def execute(phase):
    run=current();config=check_identity(run);rs=records(run)
    if any(x["status"]=="unknown" for x in rs):raise ValueError("blocked_unknown")
    plan=read(run/"plan.json");gate_path=run/"gate_review.json"
    gate=read(gate_path) if gate_path.exists() else None
    if gate:validate_gate(gate,config)
    if phase=="confirm":
        frozen=read(run/"freeze.json")
        if frozen!=freeze_identity(run):raise ValueError("freeze_changed")
    sequence=sorted([j for j in plan if j["phase"]==phase],key=lambda j:(not j["gate"],j["task"]=="visual",j["job_id"]))
    if phase=="dev":
        sequence=[j for j in plan if j["gate"]]+[j for j in plan if j["phase"]=="dev" and not j["gate"]]
    for job in sequence:
        if job["task"]=="visual" and gate and gate["reader_policy"]=="excluded":continue
        if phase=="dev" and not job["gate"] and not gate:
            report(run);print(json.dumps({"status":"gate_review_required","run":str(run)}));return
        result=send_job(run,config,job)
        if result["status"]!="success":report(run);print(json.dumps({"status":"source_review_required","job":job["job_id"]}));return
    report(run);print(json.dumps({"status":phase+"_execution_complete","run":str(run)}))
def freeze_identity(run):
    config=check_identity(run);gate=read(run/"gate_review.json");validate_gate(gate,config);review=read(run/"dev_review.json");plan=read(run/"plan.json");rs={x["job_id"]:x for x in records(run)}
    if not gate.get("reviewer") or not review.get("reviewer") or review.get("config_sha256")!=digest(config):raise ValueError("review_binding")
    for job in plan:
        if job["phase"]!="dev" or job["task"]=="visual" and gate["reader_policy"]=="excluded":continue
        if rs.get(job["job_id"],{}).get("status")!="success":raise ValueError("incomplete_dev")
    return {"config_sha256":digest(config),"gate_review_sha256":sha(run/"gate_review.json"),"dev_review_sha256":sha(run/"dev_review.json"),"code":code_identity(),"dev_responses":{j:rec.get("response_sha256") for j,rec in rs.items() if rec["phase"]=="dev"}}
def freeze():
    run=current();p=run/"freeze.json";identity=freeze_identity(run)
    if p.exists() and read(p)!=identity:raise ValueError("freeze_immutable")
    write(p,identity);print(json.dumps({"frozen":run.name}))
def report(run=None):
    run=run or current();config=check_identity(run);inp=read(run/"input_manifest.json");rs=records(run);gate=read(run/"gate_review.json") if (run/"gate_review.json").exists() else {}
    source=read(run/"source_review.json") if (run/"source_review.json").exists() else {"observations":{},"audio":{}}
    plan=read(run/"plan.json");active_ids={j["job_id"] for j in plan}
    active=[x for x in rs if x["job_id"] in active_ids]
    visual=[x for x in active if x["status"]=="success" and x["task"]=="visual"];audio=[x for x in active if x["status"]=="success" and x["task"]=="audio"]
    if (run/"source_review.json").exists() and source.get("config_sha256")!=digest(config):raise ValueError("source_review_config_binding")
    evidence=eligible(visual,source.get("observations",{}))
    for item in evidence:
        expected=next(a["sha256"] for a in inp["assets"] if a["asset_id"]==item["asset_id"])
        if item["review"]["image_sha256"]!=expected:raise ValueError("source_review_image_binding")
    sources=[s for rows in inp["sources"].values() for s in rows]
    reader=gate.get("reader_policy","pending")
    if reader=="excluded":evidence=[]
    materials={"schema_version":"extractube.stage3.t1.materials.experimental.v1","run_id":run.name,"config_sha256":digest(config),"input_mode":"experiment_source_binding","selected_method":None,"reader_policy":reader,"primary_video_id":"Fw-8hZhnH3Q","source_groups":inp["groups"],"source_sentences":sources,"annotations":audio,"editorial_review":source.get("audio",{}),"assets":inp["assets"],"visual_evidence":evidence,"visual_candidates_ref":"visual_observations.json","coverage":{"primary_source_sentences":len(inp["sources"]["Fw-8hZhnH3Q"]),"primary_annotated_sentences":sum(len(x["parsed"]["sentences"]) for x in audio if x["video_id"]=="Fw-8hZhnH3Q"),"visual_success_jobs":0 if reader=="excluded" else len(visual),"preserved_assets":len(inp["assets"]),"visual_added_accepted":sum(x["basis"] in ("image","joint") for x in evidence),"upstream":inp["upstream_counts"]},"warnings":["Visual input coverage is reviewed subset only; not_assessed is not absence.","Human final adoption pending."],"acceptance_ref":None,"t2_ready":False}
    baseline={**materials,"annotations":[],"editorial_review":{},"visual_evidence":[],"reader_policy":"excluded","warnings":materials["warnings"]+["E00 source-first baseline; no generated annotation or reading."]}
    baseline["coverage"]={**materials["coverage"],"primary_annotated_sentences":0,"visual_success_jobs":0,"visual_added_accepted":0}
    write(run/"materials_source_first.json",baseline)
    write(run/"source_sentences.json",sources);write(run/"audio_annotations.json",audio);write(run/"visual_observations.json",visual);write(run/"materials.json",materials)
    usage={k:sum((x.get("usage") or {}).get(k,0) or 0 for x in rs) for k in ("prompt_tokens","completion_tokens","total_tokens")}
    summary={"run_id":run.name,"execution_status":"blocked_unknown" if any(x["status"]=="unknown" for x in rs) else "awaiting_review","calls":len(rs),"http_seconds":round(sum(x.get("http_seconds",x.get("reserved_seconds",0)) for x in rs),6),"usage":usage,"missing_usage_calls":[x["job_id"] for x in rs if not x.get("usage")],"status_counts":{s:sum(x["status"]==s for x in rs) for s in ("success","failed","unknown")},"reader_policy":reader,"retired_job_ids":[x["job_id"] for x in rs if x["job_id"] not in active_ids],"not_sent_jobs":[{"job_id":j["job_id"],"phase":j["phase"],"status":"excluded_by_policy" if j["task"]=="visual" and reader=="excluded" else "not_sent"} for j in plan if j["job_id"] not in {x["job_id"] for x in rs}],"t2_ready":False,"user_accepted":False,"quality_accepted":False,"coverage":materials["coverage"]}
    write(run/"summary.json",summary);write(run/"acceptance.json",{"user_accepted":False,"selected_method":None,"t2_ready":False,"recommendation":source.get("recommendation"),"decision_owner":"user"})
    esc=lambda x:html.escape(str(x),quote=True)
    parts=['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>T1 원본 대조</title><style>body{max-width:1100px;margin:24px auto;padding:0 16px;font:16px/1.7 sans-serif}img{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #bbb;margin:24px 0}table{width:100%;border-collapse:collapse}td{padding:8px;border-bottom:1px solid #ddd;vertical-align:top}.tag{color:#745}</style><h1>Stage 3 T1 — 원문·관찰 대조</h1><p>실험 검토용이며 최종 블로그 글이 아닙니다. 사용자 최종 채택 전입니다.</p>',"<pre>"+esc(json.dumps(summary,ensure_ascii=False,indent=2))+"</pre>"]
    mapping={(x["video_id"],row["sentence_index"]):row for x in audio for row in x["parsed"]["sentences"]}
    for g in inp["groups"]:
        parts.append("<section><h2>"+esc(g["video_id"])+" — "+esc(g["role"])+"</h2><table>")
        for s in inp["sources"][g["video_id"]]:
            ann=mapping.get((g["video_id"],s["sentence_index"]))
            note=json.dumps({"model":ann,"source_review":source.get("audio",{}).get(g["video_id"]+":"+str(s["sentence_index"]))},ensure_ascii=False) if ann else "원문 보존 / 이 구간 주석 미실행"
            parts.append("<tr><td>"+esc(s["sentence_index"])+"</td><td>"+esc(s["text"])+"</td><td><pre>"+esc(note)+"</pre></td></tr>")
        parts.append("</table></section>")
    by={x.get("asset_id"):x for x in visual}
    for a in inp["assets"]:
        url=os.path.relpath(a["path"],run).replace(os.sep,"/");rec=by.get(a["asset_id"]);parts+=["<section><h2>"+esc(a["asset_id"])+" "+esc(a["video_id"])+"</h2>",'<a href="'+esc(url)+'"><img src="'+esc(url)+'"></a>']
        if rec:
            for i,o in enumerate(rec["parsed"]["observations"]):
                key=a["asset_id"]+":o"+str(i);parts.append("<pre>"+esc(json.dumps({"id":key,"candidate":o,"source_review":source.get("observations",{}).get(key)},ensure_ascii=False,indent=2))+"</pre>")
        else:parts.append("<p>미실행 또는 제외 — 원본 이미지는 보존합니다.</p>")
        parts.append("</section>")
    parts.append("</html>");(run/"review.html").write_text("\n".join(parts),encoding="utf-8")
    return summary
def revise(task,prompt_file):
    run=current();config=check_identity(run)
    if (run/"revision.json").exists() or (run/"freeze.json").exists():raise ValueError("revision_limit_or_frozen")
    oldplan=read(run/"plan.json");rs=records(run);gates={j["job_id"] for j in oldplan if j["gate"]}
    if any(x["job_id"] not in gates for x in rs):raise ValueError("revision_only_at_initial_gate")
    if any(x["status"]=="unknown" for x in rs):raise ValueError("blocked_unknown")
    replacement=Path(prompt_file).read_text(encoding="utf-8")
    if not replacement.strip():raise ValueError("empty_revision")
    pr={t:(run/config.get("prompt_files",{}).get(t,t+"_prompt.txt")).read_text(encoding="utf-8") for t in ("audio","visual")}
    if replacement==pr[task]:raise ValueError("identical_request_not_a_revision")
    oldconfig=dict(config);pr[task]=replacement;newplan=build_jobs(read(run/"input_manifest.json"),pr)
    affected=[j for j in newplan if j["task"]==task]
    result=subprocess.run(["docker","exec","-i",CONTAINER,"python3","-c",(HERE/"token_plan.py").read_text()],input=wire({"jobs":affected}),capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError("revision_token_plan:"+result.stderr.decode(errors="replace")[-1000:])
    token_plan=loads(result.stdout.decode());by={x["job_id"]:x for x in token_plan["jobs"]};oldby={j["job_id"]:j for j in oldplan}
    for j in newplan:
        if j["task"]!=task:j.update(oldby[j["job_id"]]);continue
        j["token_plan"]=by[j["job_id"]]
        if j["token_plan"]["input_tokens"]>12000 or j["token_plan"]["input_tokens"]+4096+1024>32768 or len(wire(j["body"]))>33554432:raise ValueError("revision_token_budget")
        j["job_id"]+="_r1"
        j["job_key"]=digest({"request":j["body"],"task":j["task"],"critical":config["critical_files"],"code":config["code"]})
    write(run/"config.p0.json",oldconfig);write(run/"plan.p0.json",oldplan)
    (run/(task+"_prompt_r1.txt")).write_text(replacement,encoding="utf-8")
    config["prompts"]={**config["prompts"],task:hashlib.sha256(replacement.encode()).hexdigest()}
    config["prompt_files"]={**config.get("prompt_files",{}),task:task+"_prompt_r1.txt"}
    write(run/"revision.json",{"task":task,"previous_config_sha256":digest(oldconfig),"new_config_sha256":digest(config),"reason":"Single gate hypothesis; previous results and budget retained."})
    write(run/"token_plan.r1.json",token_plan);write(run/"plan.json",newplan);write(run/"config.json",config)
    state=read(run/"state.json");state.update(config_sha256=digest(config),plan_sha256=sha(run/"plan.json"));write(run/"state.json",state)
    for name in ("gate_review.json","source_review.json"):
        p=run/name
        if p.exists():os.replace(p,run/(name+".p0"))
    print(json.dumps({"revision":task,"run":run.name,"calls_preserved":len(rs)}))

def main():
    a=argparse.ArgumentParser();a.add_argument("command",choices=["check","prepare","execute","freeze","report","revise"]);a.add_argument("--phase",choices=["dev","confirm"],default="dev");a.add_argument("--task",choices=["audio","visual"]);a.add_argument("--prompt-file");args=a.parse_args()
    if args.command=="check":
        import unittest
        suite=unittest.defaultTestLoader.discover(str(HERE),pattern="test_t1.py")
        if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():raise SystemExit(1)
    elif args.command=="prepare":prepare()
    elif args.command=="execute":execute(args.phase)
    elif args.command=="freeze":freeze()
    elif args.command=="revise":
        if not args.task or not args.prompt_file:raise ValueError("revision_arguments")
        revise(args.task,args.prompt_file)
    else:print(json.dumps(report(),ensure_ascii=False))
if __name__=="__main__":
    try:
        with (HERE/"execution.lock").open("a") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            main()
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as e:
        print(json.dumps({"status":"stopped","error":str(e)},ensure_ascii=False),flush=True);raise SystemExit(1)
