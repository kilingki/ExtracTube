"""Offline safeguards for the real T1 campaign; no model/network calls."""
import copy,json,tempfile,unittest
from pathlib import Path
import run_t1 as r
from contracts import parse_audio,parse_visual,parse_response,eligible,loads

class Contracts(unittest.TestCase):
 def setUp(self):
  self.audio={"targets":[2],"allowed":[0,1,2,3],"task":"audio"}
  self.row={"sentence_index":2,"handling":"retain","roles":["condition"],"repeat_of":[],"reason":"조건 보존"}
 def test_audio_target_and_reference_guards(self):
  parse_audio({"sentences":[self.row]},self.audio)
  for change in [{"sentence_index":True},{"sentence_index":1},{"roles":[]},{"handling":"repeat_candidate","repeat_of":[3]},{"handling":"retain","repeat_of":[1]},{"roles":["condition","condition"]},{"extra":0}]:
   with self.subTest(change=change),self.assertRaises(ValueError):
    parse_audio({"sentences":[{**self.row,**change}]},self.audio)
  with self.assertRaises(ValueError):parse_audio({"sentences":[]},self.audio)
 def test_strict_transport_envelope(self):
  good={"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"sentences":[self.row]})}}]}
  parse_response(json.dumps(good).encode(),200,self.audio)
  for finish,content in [("length",good["choices"][0]["message"]["content"]),("stop",chr(96)*3+"json\n{}\n"+chr(96)*3),("stop","{} trailing")]:
   obj=copy.deepcopy(good);obj["choices"][0].update(finish_reason=finish,message={"content":content})
   with self.assertRaises(ValueError):parse_response(json.dumps(obj).encode(),200,self.audio)
  with self.assertRaises(ValueError):loads('{"a":1,"a":2}')
  with self.assertRaises(ValueError):loads('{"a":NaN}')
 def test_visual_audio_image_separation(self):
  job={"asset_id":"V01","allowed":[2]}
  obs={"text":"화면의 관계","kind":"relationship","basis":"image","evidence_sentence_indices":[],"image_evidence":"화면 중앙","uncertain":False}
  parse_visual({"asset_id":"V01","observations":[obs],"unreadable_parts":[]},job)
  for patch in [{"basis":"image","evidence_sentence_indices":[2]},{"basis":"audio"},{"basis":"joint","evidence_sentence_indices":[99]},{"uncertain":1}]:
   with self.assertRaises(ValueError):parse_visual({"asset_id":"V01","observations":[{**obs,**patch}],"unreadable_parts":[]},job)
 def test_pending_and_wrong_response_not_promoted(self):
  item={"asset_id":"V01","job_id":"visual_V01","video_id":"v","response_sha256":"R","parsed":{"observations":[{"basis":"image"}]}}
  self.assertEqual(eligible([item],{}),[])
  review={"decision":"accepted","reviewer":"reviewer","reason":"source checked","image_sha256":"I","response_sha256":"different"}
  with self.assertRaises(ValueError):eligible([item],{"V01:o0":review})
  review["response_sha256"]="R"
  self.assertEqual(eligible([item],{"V01:o0":review})[0]["observation_id"],"V01:o0")

class Campaign(unittest.TestCase):
 def test_real_input_binding_and_chunk_coverage(self):
  inputs=r.contexts_input();jobs=r.build_jobs(inputs,r.prompts());dev=[j for j in jobs if j["task"]=="audio" and j["phase"]=="dev"]
  self.assertEqual([i for j in dev for i in j["targets"]],list(range(217)))
  self.assertEqual(len(jobs),27)
  self.assertEqual(len([j for j in jobs if j["gate"]]),4)
  self.assertEqual(len(inputs["assets"]),6)
  self.assertEqual(dev[0]["allowed"],list(range(14)))
  self.assertEqual(len(dev[-1]["targets"]),1)
  self.assertEqual([j["targets"] for j in jobs if j["task"]=="audio" and j["phase"]=="confirm"],[list(range(164,176)),list(range(279,291))])
 def test_budget_and_unknown_do_not_reset(self):
  self.assertEqual(r.reserve([{"status":"success","http_seconds":2350}]),50)
  for recs in [[{"status":"unknown","reserved_seconds":300}],[{"status":"success","http_seconds":2400}],[{"status":"failed","http_seconds":1}]*30]:
   with self.assertRaises(ValueError):r.reserve(recs)
 def test_crash_started_is_unknown(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);r.write(p/"journal/j.json",{"job_id":"j","status":"started","reserved_seconds":300})
   self.assertEqual(r.records(p)[0]["status"],"unknown")
   with self.assertRaises(ValueError):r.reserve(r.records(p))
 def test_plan_tamper_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);c={"code":r.code_identity(),"prompts":{},"critical_files":{}}
   r.write(p/"config.json",c);r.write(p/"plan.json",[]);r.write(p/"input_manifest.json",{})
   r.write(p/"state.json",{"config_sha256":r.digest(c),"plan_sha256":r.sha(p/"plan.json"),"input_sha256":r.sha(p/"input_manifest.json")})
   r.check_identity(p);r.write(p/"plan.json",[{"changed":True}])
   with self.assertRaises(ValueError):r.check_identity(p)
 def test_exact_success_reuse_and_failed_not_resent(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);job={"job_id":"j","job_key":"K","task":"audio","targets":[2],"allowed":[2]}
   parsed={"sentences":[{"sentence_index":2,"handling":"retain","roles":["condition"],"repeat_of":[],"reason":"조건"}]}
   raw=json.dumps({"choices":[{"finish_reason":"stop","message":{"content":json.dumps(parsed)}}]}).encode()
   (p/"responses").mkdir();(p/"responses/j.body").write_bytes(raw)
   r.write(p/"journal/j.json",{"job_key":"K","status":"success","http_status":200,"parsed":parsed,"response_sha256":r.sha(p/"responses/j.body")})
   self.assertEqual(r.send_job(p,{},job)["status"],"success")
   r.write(p/"journal/j.json",{"job_key":"K","status":"failed"})
   with self.assertRaises(ValueError):r.send_job(p,{},job)
 def test_gate_is_bound(self):
  r.validate_gate({"config_sha256":r.digest({}),"reader_policy":"limited","reviewer":"Codex","reason":"reviewed"},{})
  with self.assertRaises(ValueError):r.validate_gate({"config_sha256":"wrong","reader_policy":"limited","reviewer":"Codex","reason":"reviewed"},{})
if __name__=="__main__":unittest.main()
