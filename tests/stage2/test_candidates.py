import tempfile
import unittest
from pathlib import Path
from support import seed, adapters, settings, FakeTransport, VIDEO_ID
from extractube.vision import run
from extractube.store import read, write, sha, wire, loads

class CandidateTests(unittest.TestCase):
    def test_batched_t2_disables_thinking_and_maps_per_chunk_decisions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);seed(root);fake=FakeTransport()
            with adapters(fake):result=run(VIDEO_ID,settings(),data_root=root,log=None)
            t2_calls=[c for c in fake.calls if c.get('response_format',{}).get('json_schema',{}).get('name')=='t2']
            self.assertEqual(len(t2_calls),1)
            self.assertFalse(t2_calls[0]['chat_template_kwargs']['enable_thinking'])
            h=read(result.handoff_path);doc=read(result.handoff_path.parent/'t2/visual_candidates.json')
            self.assertEqual(len(doc['payload']['candidates']),1)
            self.assertEqual(h['coverage']['t2_needed_chunks'],1)
            self.assertEqual(h['coverage']['t2_not_needed_chunks'],1)
    def test_nonempty_length_is_failed_unresolved_not_guard(self):
        class T2BadResponse(FakeTransport):
            def send(self,url,body,timeout,limit,on_headers):
                request=loads(body)
                if request.get('response_format',{}).get('json_schema',{}).get('name')=='t2':
                    self.calls.append(request);on_headers(200,{})
                    response={'choices':[{'finish_reason':'length','message':{'content':'{"chunks":'}}],'usage':{'completion_tokens':4096}}
                    return {'status':200,'body':wire(response),'complete':True}
                return super().send(url,body,timeout,limit,on_headers)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);seed(root);fake=T2BadResponse()
            with adapters(fake):result=run(VIDEO_ID,settings(),data_root=root,log=None)
            h=read(result.handoff_path);doc=read(result.handoff_path.parent/'t2/visual_candidates.json')
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
    def test_visual_probe_rescues_long_whiteboard_chunk_and_skips_talking_head(self):
        from unittest.mock import patch
        from extractube.handoff import verify
        from extractube.timestamps import decimal
        long_stream={'index':0,'width':160,'height':90,'codec_name':'h264','time_base':{'numerator':1,'denominator':1000},'time_base_fraction':__import__('fractions').Fraction(1,1000),'start_time':decimal(10),'duration':decimal(50),'args':[],'elapsed_seconds':0}
        class AllNotNeededThenProbe(FakeTransport):
            def send(self,url,body,timeout,limit,on_headers):
                request=loads(body)
                sname=request.get('response_format',{}).get('json_schema',{}).get('name','')
                if sname=='t2':
                    self.calls.append(request)
                    props=request['response_format']['json_schema']['schema']['properties']['chunks']['properties']
                    chunks_out={cid:{'decision':'not_needed','reason':'명시적 화면 지시어 없음','evidence_sentence_indices':[cs['properties']['evidence_sentence_indices']['items']['enum'][0]]} for cid,cs in props.items()}
                    on_headers(200,{});return {'status':200,'body':wire({'choices':[{'finish_reason':'stop','message':{'content':wire({'chunks':chunks_out}).decode()}}]}),'complete':True}
                if sname=='t2_probe':
                    self.calls.append(request)
                    props=request['response_format']['json_schema']['schema']['properties']['probes']['properties']
                    probes_out={cid:{'decision':'visual_info' if cid.endswith(':u0:c0') else 'talking_head','visual_kind':'whiteboard' if cid.endswith(':u0:c0') else 'talking_head','reason':'칠판 수식 판서 확인' if cid.endswith(':u0:c0') else '인물 단독 토킹헤드'} for cid in props}
                    on_headers(200,{});return {'status':200,'body':wire({'choices':[{'finish_reason':'stop','message':{'content':wire({'probes':probes_out}).decode()}}]}),'complete':True}
                return super().send(url,body,timeout,limit,on_headers)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=seed(root);p=work/'audio/alignment_sentences.json';s=read(p)
            s['chunks'][0]['items'][0]={'text':'칠판 없이도 들리는 수식 강의입니다.','start_time':0,'end_time':20}
            s['chunks'][1]['items'][0]={'text':'인물 인터뷰 구간입니다.','start_time':20,'end_time':30}
            s['chunks'][2]['items'][0]={'text':'마무리 인사입니다.','start_time':30,'end_time':42}
            write(p,s);m=read(work/'metadata.json');m['results']['sentences']['sha256']=sha(p);write(work/'metadata.json',m)
            fake=AllNotNeededThenProbe()
            with adapters(fake),patch('extractube.visual_input.changes.probe_video',return_value=long_stream),patch('extractube.visual_input.changes.confirm_end',return_value={'last_pts':59900,'last_video_seconds':'49.900000000'}):
                result=run(VIDEO_ID,settings(),data_root=root,log=None)
                self.assertTrue(result.stage3_usable)
                self.assertTrue(verify(VIDEO_ID,data_root=root)['ok'])
            h=read(result.handoff_path);doc=read(result.handoff_path.parent/'t2/visual_candidates.json')['payload']
            # Unit 0 (20s >= 15s, whiteboard) rescued by visual_probe; Unit 1 (22s >= 15s, talking_head) stays not_requested!
            self.assertEqual([c['candidate_id'] for c in doc['candidates']],[f'{VIDEO_ID}:u0:c0'])
            self.assertEqual(doc['candidates'][0]['candidate_source'],'visual_probe')
            self.assertEqual(h['coverage']['t2_visual_probe_chunks'],1)
            self.assertEqual(h['units'][0]['visual_status'],'available')
            self.assertEqual(h['units'][1]['visual_status'],'not_requested')
