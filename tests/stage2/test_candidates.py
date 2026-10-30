import tempfile
import unittest
from pathlib import Path
from support import seed, adapters, settings, FakeTransport, VIDEO_ID
from extractube.vision import run
from extractube.store import read, write, sha, wire, loads

class CandidateTests(unittest.TestCase):
    def run_case(self,mode):
        class T2Response(FakeTransport):
            def send(self,url,body,timeout,limit,on_headers):
                request=loads(body)
                if request['chat_template_kwargs']['enable_thinking'] and (mode!='guard' or '단위 식별: 1' in request['messages'][0]['content']):
                    self.calls.append(request);on_headers(200,{})
                    response={'choices':[{'finish_reason':'length','message':{'content':None if mode=='guard' else '{"decision":'}}],'usage':{'completion_tokens':4096,'completion_tokens_details':{'reasoning_tokens':4096}}}
                    return {'status':200,'body':wire(response),'complete':True}
                return super().send(url,body,timeout,limit,on_headers)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name);work=seed(root);fake=T2Response()
        with adapters(fake):result=run(VIDEO_ID,settings(),data_root=root,log=None)
        return read(result.handoff_path),read(result.handoff_path.parent/'t2/visual_candidates.json')
    def test_guard_keeps_failure_provenance_and_chunk_ranges(self):
        h,doc=self.run_case('guard')
        self.assertEqual(len(doc['payload']['candidates']),1)
        for c in doc['payload']['candidates']:
            self.assertFalse(c['model_inference_success']);self.assertIsNone(c['decision']);self.assertIsNone(c['reason']);self.assertEqual(c['candidate_source'],'budget_guard')
        self.assertEqual(h['coverage']['t2_budget_guard_chunks'],1)
    def test_nonempty_length_is_failed_unresolved_not_guard(self):
        h,doc=self.run_case('bad')
        self.assertEqual(doc['payload']['candidates'],[]);self.assertEqual(h['coverage']['t2_failed_chunks'],2)
        self.assertTrue(all(u['visual_status']=='unresolved' for u in h['units']))
    def test_invalid_candidate_range_not_clamped(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=seed(root);p=work/'audio/alignment_sentences.json';s=read(p);s['chunks'][2]['items'][0]['end_time']=6;write(p,s)
            m=read(work/'metadata.json');m['results']['sentences']['sha256']=sha(p);write(work/'metadata.json',m)
            fake=FakeTransport()
            with adapters(fake):result=run(VIDEO_ID,settings(),data_root=root,log=None)
            h=read(result.handoff_path);doc=read(result.handoff_path.parent/'t2/visual_candidates.json');c=doc['payload']['candidates'][0]
            self.assertEqual(c['range_status'],'invalid');self.assertIsNone(c['effective_video_range']);self.assertEqual(c['requested_video_range']['end'],'6.250000000')
            self.assertEqual(h['coverage']['t3_samples'],0);self.assertEqual(h['units'][1]['coverage_status'],'partial')
