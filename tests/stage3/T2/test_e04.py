import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_e04 as e
class Tests(unittest.TestCase):
 def test_only_format_changes_and_target_bounds(self):
  original=e.read(e.PARENT/'plan.json')[0]['body'];result=e.with_format(original,[158,192]);schema=result.pop('response_format')['json_schema']['schema']
  self.assertEqual(result,original);self.assertNotIn('response_format',original)
  for n in [schema['properties']['sections']['items'],schema['properties']['sections']['items']['properties']['children']['items']]:
   self.assertFalse(n['additionalProperties']);self.assertEqual(n['properties']['sentence_start']['minimum'],158);self.assertEqual(n['properties']['sentence_end']['maximum'],192)
 def test_full_repair_request_contains_format(self):
  d=e.read(e.PARENT/'input.json');j=e.repair_job((e.PARENT/'repair_prompt.txt').read_text(),d['dev_sentences'],d['dev_original'],[158,192],[],'dev')
  self.assertEqual(j['body']['response_format']['type'],'json_schema');self.assertEqual(j['target'],[158,192])
if __name__=='__main__':unittest.main()
