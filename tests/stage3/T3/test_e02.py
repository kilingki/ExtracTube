import unittest,copy
import run_t3 as t
import run_e02_plan as e
class E02(unittest.TestCase):
 def test_family_budget_includes_failed_prior(self):
  prior=[{'status':'failed','http_seconds':0.172584},{'status':'success','http_seconds':47.595146}]
  with self.assertRaises(ValueError):e.reserve(prior+[{'status':'success','http_seconds':1}]*13)
  self.assertAlmostEqual(e.reserve(prior+[{'status':'success','http_seconds':4100}]),52.232270000000085)
 def test_example_refs_rejected(self):
  ctx={'target':[15,17],'previous_source':[],'assets':[]}
  for ix in range(900,904):
   plan={'items':[{'source_indices':[15,16,ix],'instruction':'보존'}],'context_refs':[],'asset_plan':[],'omit_candidates':[]}
   with self.assertRaises(ValueError):t.validate(plan,ctx)
 def test_single_placeholder_no_target_answers(self):
  self.assertEqual(e.PROMPT.count('{frozen_request_context_json}'),1)
  self.assertNotIn('모모',e.EXAMPLES);self.assertNotIn('브랜드',e.EXAMPLES)
 def test_only_prompt_changes(self):
  jobs=t.read(e.PARENT/'plan_R1.json')
  for j in jobs:
   b=copy.deepcopy(j['body']);b['messages']=[{'role':'user','content':e.PROMPT.replace('{frozen_request_context_json}',t.wire(j['context']).decode(),1)}]
   self.assertNotEqual(b['messages'],j['body']['messages']);b['messages']=j['body']['messages'];self.assertEqual(b,j['body'])
if __name__=='__main__':unittest.main(verbosity=2)
