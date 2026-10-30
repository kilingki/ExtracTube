import copy,tempfile,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_e03 as e
class Tests(unittest.TestCase):
 def test_patch_scope_and_partition(self):
  original=e.read(e.PARENT/'journal/E02_confirm.json')['parsed']
  patch={'sections':copy.deepcopy(original['sections'][1:])};patch['sections'][0]['title']='원문 기반 제목'
  merged=e.merge_patch(original,patch,[158,192],[152,192])
  self.assertEqual(merged['sections'][0],original['sections'][0]);self.assertNotEqual(merged,original)
  with self.assertRaises(ValueError):e.merge_patch(original,patch,[159,192],[152,192])
 def test_budget_and_terminal_cache(self):
  with self.assertRaises(ValueError):e.reserve([{'status':'success','http_seconds':1}]*3)
  with self.assertRaises(ValueError):e.reserve([{'status':'unknown','reserved_seconds':300}])
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):e.successful_cache(Path(d),{'job_key':'x'},{'job_key':'x','status':'failed'})
 def test_freeze_tamper(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);e.write(p/'f.json',{'value':1});c={'critical_files':{}};e.write(p/'config.json',c);e.write(p/'state.json',{'config_sha256':e.digest(c),'frozen_files':{'f.json':e.sha(p/'f.json')}})
   e.check_identity(p);e.write(p/'f.json',{'value':2})
   with self.assertRaises(ValueError):e.check_identity(p)
if __name__=='__main__':unittest.main()
