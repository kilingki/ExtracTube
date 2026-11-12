import unittest,copy
import run_e01 as r
class E01Tests(unittest.TestCase):
 def setUp(self):
  self.u={'unit_id':'x','required_source_sentences':[{'sentence_index':i,'text':'원문'+str(i)} for i in range(5)],'resolved_cautions':[{'kind':'prior_plan_distortion_guard_no_classification','linked_constraint_id':'preserve_compound_scope_and_negation','sentence_indices':[2,3]}]}
  self.v={k:{'paragraphs':[{'text':'설명한다.','source_indices':ids}],'editorial_candidates':[],'unresolved':[]} for k,ids in [('r0',[0,1]),('r1',[4])]}
 def test_quote(self):
  p=r.validate(self.v,self.u);self.assertEqual(p['blocks'][1]['text'],'원문2\n원문3')
 def test_regions(self):self.assertEqual([x['region_id'] for x in r.partition(self.u)[0]],['r0','r1'])
 def test_hidden_text(self):
  regions,quotes=r.partition(self.u);self.assertNotIn('원문2',str(regions))
 def test_no_quote_policy(self):
  u=copy.deepcopy(self.u);u['resolved_cautions']=[];self.assertEqual(len(r.partition(u)[0]),1)
 def test_foreign_quote(self):
  u=copy.deepcopy(self.u);u['resolved_cautions'][0]['sentence_indices']=[99]
  with self.assertRaises(ValueError):r.partition(u)
 def test_cross_region(self):
  self.v['r1']['paragraphs'][0]['source_indices']=[0,4]
  with self.assertRaises(ValueError):r.validate(self.v,self.u)
 def test_missing(self):
  self.v.pop('r1')
  with self.assertRaises(ValueError):r.validate(self.v,self.u)
 def test_invent_quote(self):
  self.v['source_quote']='fake'
  with self.assertRaises(ValueError):r.validate(self.v,self.u)
 def test_duplicate_index(self):
  self.v['r0']['paragraphs'][0]['source_indices']=[0,0,1]
  with self.assertRaises(ValueError):r.validate(self.v,self.u)
 def test_bool(self):
  self.v['r0']['paragraphs'][0]['source_indices']=[False,1]
  with self.assertRaises(ValueError):r.validate(self.v,self.u)
 def test_plain(self):
  self.v['r0']['paragraphs'][0]['text']='<b>bad</b>'
  with self.assertRaises(ValueError):r.validate(self.v,self.u)
 def test_semantic_not_proved(self):
  self.v['r0']['paragraphs'][0]['text']='근거 없는 사실이다.'
  self.assertEqual(r.validate(self.v,self.u)['blocks'][0]['text'],'근거 없는 사실이다.')
if __name__=='__main__':unittest.main()
