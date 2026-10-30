import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from support import seed, adapters, settings, FakeTransport, VIDEO_ID
from extractube.vision import run
from extractube.handoff import verify
from extractube import infer as inference
from extractube.store import read, write, wire
from extractube.errors import VisionError

class CrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.work=seed(self.root)
    def test_complete_body_crash_reparsed_without_resend(self):
        fake=FakeTransport();original=inference.write
        def crash(path,obj,**kwargs):
            if Path(path).name=='record.json':raise SystemExit('process crash before terminal record')
            return original(path,obj,**kwargs)
        with adapters(fake):
            with patch.object(inference,'write',side_effect=crash),self.assertRaises(SystemExit):run(VIDEO_ID,settings(),data_root=self.root,log=None)
            self.assertEqual(len(fake.calls),1)
            directory=next((self.work/'vision/runs').iterdir())
            result=run(VIDEO_ID,settings(),data_root=self.root,resume_run_id=directory.name,log=None)
            self.assertTrue(result.stage3_usable);self.assertEqual(len(fake.calls),7);self.assertEqual(result.inference_calls,6)
            self.assertTrue(verify(VIDEO_ID,data_root=self.root)['ok'])
    def test_sending_crash_without_complete_body_becomes_unknown(self):
        class CrashTransport(FakeTransport):
            def send(self,*args,**kwargs):self.calls.append('sending');raise SystemExit('killed')
        fake=CrashTransport()
        with adapters(fake):
            with self.assertRaises(SystemExit):run(VIDEO_ID,settings(),data_root=self.root,log=None)
            directory=next((self.work/'vision/runs').iterdir())
            with self.assertRaises(VisionError):run(VIDEO_ID,settings(),data_root=self.root,resume_run_id=directory.name,log=None)
            self.assertEqual(len(fake.calls),1)
            record=read(next((directory/'calls').glob('*/record.json')))
            self.assertEqual(record['status'],'unknown');self.assertEqual(record['elapsed_seconds'],settings().timeout_seconds)
    def test_reserved_crash_is_not_sent(self):
        original=inference.write
        def crash(path,obj,**kwargs):
            if Path(path).parent.name=='jobs' and isinstance(obj,dict) and any(a['status']=='sending' for a in obj.get('attempts',[])):raise SystemExit('before sending journal')
            return original(path,obj,**kwargs)
        fake=FakeTransport()
        with adapters(fake):
            with patch.object(inference,'write',side_effect=crash),self.assertRaises(SystemExit):run(VIDEO_ID,settings(),data_root=self.root,log=None)
            self.assertEqual(len(fake.calls),0)
            directory=next((self.work/'vision/runs').iterdir());result=run(VIDEO_ID,settings(),data_root=self.root,resume_run_id=directory.name,log=None)
            self.assertTrue(result.stage3_usable);self.assertEqual(result.inference_calls,7)
    def test_http_failure_not_retried_and_raw_preserved(self):
        class Bad(FakeTransport):
            def send(self,url,body,timeout,limit,on_headers):
                self.calls.append(body);on_headers(502,{})
                return {'status':502,'body':b'{"src":"inferswap","error":"fixture"}','complete':True}
        fake=Bad()
        with adapters(fake),self.assertRaises(VisionError):run(VIDEO_ID,settings(),data_root=self.root,log=None)
        self.assertEqual(len(fake.calls),1)
        directory=next((self.work/'vision/runs').iterdir());self.assertEqual(next((directory/'calls').glob('*/response.body')).read_bytes(),b'{"src":"inferswap","error":"fixture"}')
        self.assertFalse((self.work/'vision/current.json').exists())
    def test_t1_format_single_retry_only_and_metadata_preserved(self):
        class Bad(FakeTransport):
            def send(self,url,body,timeout,limit,on_headers):
                self.calls.append(body);on_headers(200,{})
                return {'status':200,'body':wire({'choices':[{'finish_reason':'length','message':{'content':'truncated','reasoning_content':'raw reasoning'}}],'usage':{'completion_tokens':4096}}),'complete':True}
        fake=Bad()
        with adapters(fake),self.assertRaises(VisionError):run(VIDEO_ID,settings(),data_root=self.root,log=None)
        self.assertEqual(len(fake.calls),2)
        for p in self.work.glob('vision/runs/*/calls/*/record.json'):
            r=read(p);self.assertEqual(r['finish_reason'],'length');self.assertEqual(r['usage']['completion_tokens'],4096);self.assertEqual(r['reasoning'],'raw reasoning')
    def test_response_truncated_unknown_preserves_partial(self):
        class Partial(FakeTransport):
            def send(self,url,body,timeout,limit,on_headers):
                self.calls.append(body);on_headers(200,{})
                return {'status':200,'body':b'incomplete','complete':False,'error':'incomplete_body'}
        fake=Partial()
        with adapters(fake):result=run(VIDEO_ID,settings(),data_root=self.root,log=None)
        self.assertEqual(result.execution_status,'blocked_unknown');self.assertEqual(len(fake.calls),1)
        self.assertEqual(len(list(self.work.glob('vision/runs/*/calls/*/response.partial'))),1);self.assertEqual(len(list(self.work.glob('vision/runs/*/calls/*/response.body'))),0)
