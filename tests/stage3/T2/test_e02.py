import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_e02 as e02
class E02Tests(unittest.TestCase):
    def test_patch_preserves_every_other_field_and_ranges(self):
        original=e02.read(e02.PARENT/'journal/R1_dev.json')['parsed']
        patched=e02.patched_structure(original)
        self.assertEqual(original['sections'][9]['title'],e02.OLD_TITLE)
        patched['sections'][9]['title']=e02.OLD_TITLE
        self.assertEqual(patched,original)
        wrong=copy.deepcopy(original);wrong['sections'][9]['sentence_start']=173
        with self.assertRaises(ValueError):e02.patched_structure(wrong)
    def test_one_call_budget_and_unknown_no_retry(self):
        self.assertEqual(e02.reserve([]),300)
        for status in ['success','failed','unknown','started']:
            with self.assertRaises(ValueError):e02.reserve([{'status':status,'http_seconds':1,'reserved_seconds':300}])
if __name__=='__main__':unittest.main()
