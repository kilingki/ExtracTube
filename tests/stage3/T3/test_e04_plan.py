import unittest,copy
import run_t3 as t
import run_e04_plan as e
class E04(unittest.TestCase):
 def setUp(self):
  self.original={'items':[{'source_indices':[10],'instruction':'가릴 오류 문구'},{'source_indices':[11],'instruction':'고정값'}],'context_refs':[],'asset_plan':[],'omit_candidates':[]}
  self.source={'context':{'source':[{'sentence_index':10,'text':'기구.'},{'sentence_index':11,'text':'액체는 아니죠.'}]},'body':{'messages':[]},'video_id':'test','target':[10,12]}
  self.job=e.rewrite_job(self.source,self.original,[0],'test')
 def test_faulty_instruction_not_in_wire(self):
  wire=t.wire(self.job['body']).decode();self.assertNotIn('가릴 오류 문구',wire);self.assertIn(e.MASK,wire);self.assertIn('고정값',wire)
 def test_quotes_and_directive(self):
  p=copy.deepcopy(self.original);p['items'][0]['instruction']='[10] 기구. 작성 지시: 독립 발화의 대상을 확정하지 않는다.';e.validate_quotes(p,self.job)
 def test_missing_quote_or_mask_or_directive(self):
  for text in ['다른 말 작성 지시: 추정하지 않는다.',e.MASK,'[10] 기구.']:
   p=copy.deepcopy(self.original);p['items'][0]['instruction']=text
   with self.assertRaises(ValueError):e.validate_quotes(p,self.job)
 def test_outside_mutation(self):
  p=copy.deepcopy(self.original);p['items'][0]['instruction']='기구. 작성 지시: 의미를 보존한다.';p['items'][1]['instruction']='임의수정'
  with self.assertRaises(ValueError):e.validate_quotes(p,self.job)
 def test_quotes_do_not_prove_meaning(self):
  p=copy.deepcopy(self.original);p['items'][0]['instruction']='기구. 작성 지시: 원문에 없는 확정 분류를 새로 주장한다.'
  e.validate_quotes(p,self.job) # Deliberately passes mechanically; source review must reject meaning.
if __name__=='__main__':unittest.main(verbosity=2)
