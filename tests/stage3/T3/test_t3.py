import unittest,copy,json
import run_t3 as t
class Contracts(unittest.TestCase):
 def setUp(self):
  self.ctx={'target':[10,12],'previous_source':[{'sentence_index':3}],'assets':[]}
  self.plan={'items':[{'source_indices':[10,11],'instruction':'조건과 예시 보존'}],'context_refs':[],'asset_plan':[],'omit_candidates':[]}
 def bad(self,p):
  with self.assertRaises((ValueError,TypeError)):t.validate(p,self.ctx)
 def test_valid_overlap_and_context(self):
  p=copy.deepcopy(self.plan);p['items'].append({'source_indices':[11],'instruction':'한 문장 내 예외'});p['context_refs']=[{'source_indices':[3],'purpose':'앞의 정의'}];t.validate(p,self.ctx)
 def test_bool(self):
  p=copy.deepcopy(self.plan);p['items'][0]['source_indices']=[True,11];self.bad(p)
 def test_missing(self):
  p=copy.deepcopy(self.plan);p['items'][0]['source_indices']=[10];self.bad(p)
 def test_foreign_and_future_context(self):
  for key,value in [('source_indices',[10,12])]:
   p=copy.deepcopy(self.plan);p['items'][0][key]=value;self.bad(p)
  p=copy.deepcopy(self.plan);p['context_refs']=[{'source_indices':[11],'purpose':'미래'}];self.bad(p)
 def test_order(self):
  p=copy.deepcopy(self.plan);p['items']=[{'source_indices':[11],'instruction':'후'},{'source_indices':[10],'instruction':'전'}];self.bad(p)
 def test_extra_empty_long(self):
  for field,value in [('extra',1),('instruction',''),('instruction','x'*1601)]:
   p=copy.deepcopy(self.plan);p['items'][0][field]=value;self.bad(p)
 def test_asset(self):
  p=copy.deepcopy(self.plan);p['asset_plan']=[{'asset_id':'V01','action':'use','source_indices':[10],'reason':'없는 그림'}];self.bad(p)
 def test_raw_errors(self):
  job={'context':self.ctx}
  for content,finish,status in [('```json\n{}\n```','stop',200),('{"items":[],"items":[]}','stop',200),(json.dumps(self.plan),'length',200),(json.dumps(self.plan),'stop',500)]:
   raw=json.dumps({'choices':[{'finish_reason':finish,'message':{'content':content}}]}).encode()
   with self.assertRaises(ValueError):t.parse_response(raw,status,job)
 def test_budget_unknown_started(self):
  for status in ['unknown','started']:
   with self.assertRaises(ValueError):t.old.reserve([{'status':status}],max_calls=14,max_seconds=4200)
  with self.assertRaises(ValueError):t.old.reserve([{'status':'failed','http_seconds':1}]*14,max_calls=14,max_seconds=4200)
  self.assertEqual(t.old.reserve([{'status':'failed','http_seconds':4190}],max_calls=14,max_seconds=4200),10)
 def test_primary_confirmation_separate(self):
  d=t.read(t.INPUT);confirm=t.read(t.PARENT/'input.json')['fresh_sentences'];ref=d['validation_only']['confirmation_structure_ref']
  jobs=t.make_jobs(d,confirm,t.read(t.ROOT/ref['path']),t.PROMPT)
  self.assertEqual(len(jobs),13)
  for j in jobs:
   self.assertEqual(j['target'],[j['context']['source'][0]['sentence_index'],j['context']['source'][-1]['sentence_index']+1])
   if j['phase']=='confirm':self.assertEqual(j['context']['assets'],[]);self.assertEqual(j['video_id'],'H2WN130OzLg')
   self.assertTrue(all(x['sentence_index']<j['target'][0] for x in j['context']['previous_source']))
if __name__=='__main__':unittest.main(verbosity=2)
