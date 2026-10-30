"""A paused invocation must not turn a closed batch failure into an implicit retry."""

import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from support import seed, adapters, settings, FakeTransport, VIDEO_ID
from extractube.vision import run
from extractube.store import loads, wire, read

class ClosedFailureResumeTests(unittest.TestCase):
    def test_failed_t4_batch_is_preserved_when_next_batch_pauses(self):
        class OneClosedFailure(FakeTransport):
            def send(self,url,body,timeout,limit,on_headers):
                request=loads(body);content=request['messages'][0]['content']
                if isinstance(content,list) and '후속 시각 정보 추출' in content[0]['text'] and len(content)==9:
                    self.calls.append(request);on_headers(200,{})
                    return {'status':200,'body':wire({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}),'complete':True}
                return super().send(url,body,timeout,limit,on_headers)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=seed(root);fake=OneClosedFailure();small=replace(settings(),max_inference_calls=5)
            with adapters(fake):
                paused=run(VIDEO_ID,small,data_root=root,log=None)
                self.assertEqual(paused.execution_status,'paused_budget');self.assertEqual(len(fake.calls),5)
                result=run(VIDEO_ID,small,data_root=root,resume_run_id=paused.run_id,resume_budget_limits={'max_inference_calls':30},log=None)
                self.assertTrue(result.stage3_usable);self.assertEqual(result.inference_calls,1);self.assertEqual(len(fake.calls),6)
                h=read(result.handoff_path);self.assertEqual(h['coverage']['t4_failed_associations'],4);self.assertEqual(h['coverage']['selected_assets'],0)
                failed=[read(p) for p in (result.handoff_path.parent/'calls').glob('*/record.json') if read(p)['status']=='failed']
                self.assertEqual(len(failed),1)
