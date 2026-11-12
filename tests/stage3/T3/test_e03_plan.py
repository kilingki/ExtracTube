import unittest,copy
import run_t3 as t
import run_e03_plan as e
class E03(unittest.TestCase):
 def setUp(self):self.original={'items':[{'source_indices':[10],'instruction':'원래A'},{'source_indices':[11],'instruction':'원래B'}],'context_refs':[],'asset_plan':[],'omit_candidates':[]}
 def test_allowed_instruction_only(self):
  p=copy.deepcopy(self.original);p['items'][1]['instruction']='보정';self.assertEqual(e.validate_unchanged(p,self.original,[1]),p)
 def test_outside_changes_blocked(self):
  variants=[]
  p=copy.deepcopy(self.original);p['items'][0]['instruction']='딴수정';variants.append(p)
  p=copy.deepcopy(self.original);p['items'][1]['source_indices']=[10,11];variants.append(p)
  p=copy.deepcopy(self.original);p['omit_candidates']=[{'source_indices':[10],'reason':'삭제'}];variants.append(p)
  p=copy.deepcopy(self.original);p['items'].reverse();variants.append(p)
  for p in variants:
   with self.assertRaises(ValueError):e.validate_unchanged(p,self.original,[1])
 def test_allowlist_invalid(self):
  for a in [[True],[2],[1,1],[-1]]:
   with self.assertRaises(ValueError):e.validate_unchanged(self.original,self.original,a)
 def test_three_call_and_time_caps(self):
  prior=[{'status':'success','http_seconds':40}]*4
  with self.assertRaises(ValueError):e.reserve([{'status':'success','http_seconds':1}]*3,prior)
  with self.assertRaises(ValueError):e.reserve([{'status':'success','http_seconds':900}],prior)
  with self.assertRaises(ValueError):e.reserve([],prior+[{'status':'success','http_seconds':1}]*11)
  with self.assertRaises(ValueError):e.reserve([{'status':'unknown','reserved_seconds':300}],prior)
  self.assertEqual(e.reserve([{'status':'success','http_seconds':890}],prior),10)
 def test_repair_prompt_single_placeholder(self):self.assertEqual(e.PROMPT.count('{frozen_source_plan_allowed_edits_and_review_issues_json}'),1)
if __name__=='__main__':unittest.main(verbosity=2)
