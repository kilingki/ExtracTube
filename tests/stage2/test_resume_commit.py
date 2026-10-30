import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from extractube.vision import run
from extractube.handoff import verify
from extractube.store import read, write, sha
from extractube.errors import VisionError
from support import seed, adapters, settings, FakeTransport, VIDEO_ID

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.work=seed(self.root)
    def test_spec_14_3_and_exact_reuse(self):
        fake=FakeTransport()
        with adapters(fake):
            result=run(VIDEO_ID,settings(),data_root=self.root,log=None)
            self.assertEqual(result.execution_status,'complete_with_warnings')
            self.assertEqual(len(fake.calls),7)
            h=read(result.handoff_path)
            self.assertEqual(h['source']['video_stream_start_seconds'],'10.000000000')
            self.assertEqual(h['source']['stream_index'],0)
            self.assertEqual(h['omitted_audio_chunks'],[])
            self.assertTrue(read(result.handoff_path.parent/'plan.json')['job_ids'])
            self.assertTrue(read(result.handoff_path.parent/'state.json')['terminal_job_counts'])
            self.assertEqual(len(h['units']),2);self.assertEqual(h['coverage']['total_sentences'],3)
            self.assertEqual(len(h['assets']),1);self.assertTrue(h['assets'][0]['asset_id'].endswith('pts11250'))
            self.assertEqual([(u['visual_status'],u['coverage_status']) for u in h['units']],[('not_requested','not_requested'),('available','partial')])
            pointer=(self.work/'vision/current.json').read_bytes()
            self.assertTrue(verify(VIDEO_ID,data_root=self.root)['ok'])
            again=run(VIDEO_ID,settings(),data_root=self.root,log=None)
            self.assertEqual(again.inference_calls,0);self.assertEqual(again.run_id,result.run_id);self.assertEqual(len(fake.calls),7)
            self.assertEqual(pointer,(self.work/'vision/current.json').read_bytes())
            image=self.work/h['assets'][0]['image']['path'];image.write_bytes(b'broken')
            with self.assertRaises(VisionError):verify(VIDEO_ID,data_root=self.root)
    def test_source_corruption_zero_calls(self):
        (self.work/'source/video.mp4').write_bytes(b'corrupt');fake=FakeTransport()
        with adapters(fake),self.assertRaises(VisionError):run(VIDEO_ID,settings(),data_root=self.root,log=None)
        self.assertEqual(fake.calls,[])
    def test_unknown_cannot_be_bypassed_by_force(self):
        fake=FakeTransport('timeout')
        with adapters(fake):
            result=run(VIDEO_ID,settings(),data_root=self.root,log=None)
            self.assertEqual(result.execution_status,'blocked_unknown');self.assertEqual(len(fake.calls),1)
            for kwargs in ({'force':True},{'resume_run_id':result.run_id},{}):
                with self.assertRaises(VisionError):run(VIDEO_ID,settings(),data_root=self.root,log=None,**kwargs)
            self.assertEqual(len(fake.calls),1)
            state=read(self.work/f'vision/runs/{result.run_id}/state.json');call=state['blocked_call_ids'][0]
            fake.mode=None
            resumed=run(VIDEO_ID,settings(),data_root=self.root,resume_run_id=result.run_id,retry_unknown_call_ids=(call,),log=None)
            self.assertTrue(resumed.stage3_usable);self.assertEqual(len(fake.calls),8)
            self.assertEqual(read(self.work/f'vision/runs/{result.run_id}/result.json')['budget']['http_calls'],8)
    def test_pause_resume_budget_and_omitted_defaults(self):
        fake=FakeTransport();small=replace(settings(),max_inference_calls=2)
        with adapters(fake):
            result=run(VIDEO_ID,small,data_root=self.root,log=None)
            self.assertEqual(result.execution_status,'paused_budget');self.assertEqual(len(fake.calls),2)
            resumed=run(VIDEO_ID,small,data_root=self.root,resume_run_id=result.run_id,resume_budget_limits={'max_inference_calls':30},log=None)
            self.assertTrue(resumed.stage3_usable);self.assertEqual(len(fake.calls),7)
            self.assertEqual(resumed.inference_calls,5)
    def test_force_and_from(self):
        fake=FakeTransport()
        with adapters(fake):
            first=run(VIDEO_ID,settings(),data_root=self.root,log=None)
            second=run(VIDEO_ID,settings(),data_root=self.root,from_stage='t5',log=None)
            self.assertNotEqual(first.run_id,second.run_id);self.assertEqual(second.inference_calls,1)
            third=run(VIDEO_ID,settings(),data_root=self.root,force=True,log=None)
            self.assertEqual(third.inference_calls,7)
    def test_atomic_failure_keeps_previous_current(self):
        fake=FakeTransport()
        with adapters(fake):
            first=run(VIDEO_ID,settings(),data_root=self.root,log=None);before=(self.work/'vision/current.json').read_bytes()
            from extractube import vision as runner
            original=runner.write
            def fail(path,obj,**kwargs):
                if Path(path).name=='current.json':raise OSError('fault before current replace')
                return original(path,obj,**kwargs)
            with patch.object(runner,'write',side_effect=fail),self.assertRaises(OSError):run(VIDEO_ID,settings(),data_root=self.root,force=True,log=None)
            self.assertEqual(before,(self.work/'vision/current.json').read_bytes())
            self.assertTrue(verify(VIDEO_ID,data_root=self.root)['ok'])

class EmptyTests(unittest.TestCase):
    def test_empty_omitted_and_html_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);seed(root,empty=True,omitted=True);fake=FakeTransport()
            with adapters(fake):
                result=run(VIDEO_ID,settings(),data_root=root,log=None)
                self.assertEqual(result.execution_status,'empty');self.assertEqual(fake.calls,[])
                h=read(result.handoff_path);self.assertEqual(h['units'],[]);self.assertTrue(h['coverage']['audio_incomplete'])
                self.assertIn('&lt;script&gt;',result.handoff_path.with_name('review.html').read_text())
                self.assertTrue(verify(VIDEO_ID,data_root=root)['ok'])
