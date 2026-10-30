import tempfile
import unittest
from pathlib import Path
from fractions import Fraction
from extractube.visual_input import flatten
from extractube.store import safe, write, read, HashCache, sha
from extractube.timestamps import actual, seconds, decimal
from extractube.settings import VisionSettings
from extractube.errors import VisionError

class InputTests(unittest.TestCase):
    def test_offsets_nonzero_start_negative_pts_and_nan(self):
        self.assertEqual(actual(11250,{'numerator':1,'denominator':1000},10),Fraction(5,4))
        self.assertEqual(actual(-1,{'numerator':1,'denominator':1000},0),Fraction(-1,1000))
        for x in (True,float('nan'),float('inf'),None):
            with self.assertRaises(VisionError):decimal(x)
        self.assertEqual(seconds(Fraction(1,3)),'0.333333333')
        for bad in ({'numerator':True,'denominator':1},{'numerator':1,'denominator':0}):
            with self.assertRaises(VisionError):actual(1,bad,0)
    def test_input_array_position_preserves_source_index_and_whitespace(self):
        payload={'time_reference':'audio_start','overlap_deduplicated':True,'sentence_policy_id':'new-policy','audio':{'sample_rate':16000,'channels':1,'num_samples':0},'omitted_chunks':[],'chunks':[{'index':99,'text':' x ','language':'ko','items':[{'start_time':0,'end_time':1}]}]}
        s,w=flatten(payload);self.assertEqual(s[0]['sentence_index'],0);self.assertEqual(s[0]['source_chunk_index'],99);self.assertEqual(s[0]['text'],' x ')
        payload['chunks'][0]['items'][0]['end_time']=-1
        with self.assertRaises(VisionError):flatten(payload)
    def test_paths_symlink_and_hash_changes(self):
        with tempfile.TemporaryDirectory() as d,tempfile.TemporaryDirectory() as other:
            work=Path(d);outside=Path(other);(outside/'x').write_text('x');(work/'link').symlink_to(outside,target_is_directory=True)
            for path in ('../x','/etc/passwd','C:/x','a\\x','link/x'):
                with self.assertRaises(VisionError):safe(work,path)
            p=work/'x';p.write_text('first');reference={'path':'x','sha256':sha(p)};cache=HashCache();cache.check(work,reference);p.write_text('changed')
            with self.assertRaises(VisionError):cache.check(work,reference)
    def test_settings_bounds(self):
        for args in ({'max_inference_calls':True},{'max_request_bytes':0},{'timeout_seconds':float('nan')},{'max_http_seconds':-1},{'infer_base_url':'file:///x'}):
            with self.assertRaises(VisionError):VisionSettings(**args)
