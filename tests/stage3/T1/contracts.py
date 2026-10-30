"""Strict experiment contracts. Preserve source text and every failed response."""
import json
HANDLING={"retain","noncontent_candidate","repeat_candidate","uncertain"}
ROLES={"claim","explanation","reason","example","condition","exception","experience","opinion","question","visual_reference","other"}
KINDS={"scene","visible_text","number","relationship","formula","table","code"}
def loads(text):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: raise ValueError("duplicate_json_key")
            out[k]=v
        return out
    return json.loads(text,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError("nonfinite_json")))
def exact(obj,keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys): raise ValueError("object_keys")
def text(value,limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit: raise ValueError("text")
def integers(values,allowed):
    if not isinstance(values,list) or any(type(i) is not int or i not in allowed for i in values) or len(values)!=len(set(values)): raise ValueError("indices")
def parse_audio(obj,job):
    exact(obj,["sentences"]);rows=obj["sentences"]
    if not isinstance(rows,list) or len(rows)!=len(job["targets"]):raise ValueError("target_count")
    available=set(job["allowed"])
    for row,i in zip(rows,job["targets"]):
        exact(row,["sentence_index","handling","roles","repeat_of","reason"])
        if type(row["sentence_index"]) is not int or row["sentence_index"]!=i:raise ValueError("target_order")
        if not isinstance(row["handling"],str) or row["handling"] not in HANDLING:raise ValueError("handling")
        roles=row["roles"]
        if not isinstance(roles,list) or any(not isinstance(x,str) or x not in ROLES for x in roles) or len(roles)!=len(set(roles)):raise ValueError("roles")
        if row["handling"] in ("retain","uncertain") and not roles:raise ValueError("empty_roles")
        integers(row["repeat_of"],{k for k in available if k<i});text(row["reason"],160)
        if bool(row["repeat_of"])!=(row["handling"]=="repeat_candidate"):raise ValueError("repeat_condition")
    return obj
def parse_visual(obj,job):
    exact(obj,["asset_id","observations","unreadable_parts"])
    if obj["asset_id"]!=job["asset_id"]:raise ValueError("asset")
    obs=obj["observations"];unread=obj["unreadable_parts"]
    if not isinstance(obs,list) or len(obs)>8 or not isinstance(unread,list) or len(unread)>6:raise ValueError("visual_count")
    for x in unread:text(x,160)
    for row in obs:
        exact(row,["text","kind","basis","evidence_sentence_indices","image_evidence","uncertain"])
        text(row["text"],240)
        if not isinstance(row["kind"],str) or row["kind"] not in KINDS or row["basis"] not in ("image","audio","joint") or type(row["uncertain"]) is not bool:raise ValueError("visual_enum")
        integers(row["evidence_sentence_indices"],set(job["allowed"]))
        if row["basis"]=="image":
            if row["evidence_sentence_indices"]:raise ValueError("image_audio_refs")
        elif not row["evidence_sentence_indices"]:raise ValueError("missing_audio_refs")
        if row["basis"]=="audio":
            if row["image_evidence"] is not None:raise ValueError("audio_image_evidence")
        else:text(row["image_evidence"],160)
    return obj
def parse_response(raw,status,job):
    outer=loads(raw.decode("utf-8"))
    if status!=200:raise ValueError("http_error")
    if not isinstance(outer,dict):raise ValueError("outer_object")
    choices=outer.get("choices")
    if not isinstance(choices,list) or len(choices)!=1:raise ValueError("choices")
    c=choices[0]
    if not isinstance(c,dict) or not isinstance(c.get("message"),dict):raise ValueError("message")
    if c.get("finish_reason")!="stop":raise ValueError("finish_reason")
    content=c.get("message",{}).get("content")
    if not isinstance(content,str) or not content.strip() or content.lstrip().startswith(chr(96)*3):raise ValueError("content")
    obj=loads(content)
    parsed=parse_audio(obj,job) if job["task"]=="audio" else parse_visual(obj,job)
    return parsed,{"usage":outer.get("usage"),"finish_reason":c["finish_reason"]}
def eligible(observations,reviews):
    result=[]
    for item in observations:
        for i,obs in enumerate(item["parsed"]["observations"]):
            key=item["asset_id"]+":o"+str(i)
            review=reviews.get(key,{})
            if review.get("decision")=="accepted":
                if not review.get("reason") or not review.get("reviewer") or not review.get("image_sha256") or review.get("response_sha256")!=item.get("response_sha256"):raise ValueError("review_provenance")
                result.append({**obs,"observation_id":key,"video_id":item["video_id"],"asset_id":item["asset_id"],"review":review})
    return result
