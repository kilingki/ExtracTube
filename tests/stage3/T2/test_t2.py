import copy,json,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from contracts import loads,parse_structure,parse_response
import run_t2 as runner
class T2Tests(unittest.TestCase):
    def setUp(self):
        self.obj={'sections':[{'sentence_start':0,'sentence_end':3,'title':'주제','children':[{'sentence_start':0,'sentence_end':1,'title':'앞 설명'},{'sentence_start':1,'sentence_end':3,'title':'예시'}]}]}
    def test_valid_nested_coverage(self):
        self.assertEqual(parse_structure(self.obj,[0,3]),self.obj)
    def test_reject_gaps_overlap_and_empty_ranges(self):
        for key,value in [('sentence_start',1),('sentence_end',0),('sentence_end',4),('sentence_start',False)]:
            x=copy.deepcopy(self.obj);x['sections'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError): parse_structure(x,[0,3])
        x=copy.deepcopy(self.obj);x['sections'][0]['children'][1]['sentence_start']=0
        with self.assertRaises(ValueError): parse_structure(x,[0,3])
    def test_no_extra_keys_deeper_nodes_or_bad_arrays(self):
        for mutate in [lambda x:x.update(summary='x'),lambda x:x['sections'][0]['children'][0].update(children=[]),lambda x:x['sections'][0].update(children={}),lambda x:x['sections'][0].update(title=' '*3)]:
            x=copy.deepcopy(self.obj);mutate(x)
            with self.assertRaises(ValueError): parse_structure(x,[0,3])
    def test_nonzero_confirmation_indices(self):
        x={'sections':[{'sentence_start':152,'sentence_end':192,'title':'확인','children':[]}]}
        parse_structure(x,[152,192])
        with self.assertRaises(ValueError): parse_structure(x,[0,40])
    def test_duplicate_nonfinite_and_fenced_json(self):
        for raw in ['{"a":1,"a":2}','{"x":NaN}']:
            with self.assertRaises(ValueError): loads(raw)
        raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'```json\n{}\n```'}}]}).encode()
        with self.assertRaises(ValueError): parse_response(raw,200,{'target':[0,3]})
    def test_http_and_length_rejected(self):
        for status,finish in [(500,'stop'),(200,'length')]:
            raw=json.dumps({'choices':[{'finish_reason':finish,'message':{'content':json.dumps(self.obj)}}]}).encode()
            with self.assertRaises(ValueError): parse_response(raw,status,{'target':[0,3]})
    def test_budget_unknown_and_shared_limits(self):
        for rows in [[{'status':'unknown','reserved_seconds':300}],[{'status':'success','http_seconds':1}]*3,[{'status':'success','http_seconds':900}]]:
            with self.assertRaises(ValueError): runner.reserve(rows)
        self.assertEqual(runner.reserve([{'status':'success','http_seconds':899}]),1)
    def test_source_hash_and_text_mutation(self):
        d=runner.read(runner.INPUT);runner.verify_input(d)
        d['primary_sentences'][5]['text']+='수정'
        with self.assertRaises(ValueError): runner.verify_input(d)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x';p.write_text('raw')
            with self.assertRaises(ValueError): runner.bound_sha(p,'0'*64)
    def test_confirmation_isolation_and_single_substitution(self):
        d=runner.read(runner.INPUT);d['primary_sentences'][0]['text']='literal {video_id}'
        jobs=runner.build_jobs(d,'{video_id} {sentences_json} {existing_units_json} {cautions_json}')
        self.assertIn('literal {video_id}',jobs[0]['body']['messages'][0]['content'])
        text=jobs[1]['body']['messages'][0]['content']
        self.assertNotIn(d['primary_video_id'],text);self.assertTrue(text.endswith('[] []'))
    def test_success_cache_and_failed_unknown_block(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td);(run/'responses').mkdir();job={'job_id':'x','job_key':'k','target':[0,3]}
            raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(self.obj)}}]}).encode()
            (run/'responses/x.body').write_bytes(raw)
            rec={'job_key':'k','status':'success','parsed':self.obj,'response_sha256':__import__('hashlib').sha256(raw).hexdigest(),'http_status':200}
            self.assertEqual(runner.successful_cache(run,job,rec),rec)
            for status in ['failed','unknown','started']:
                with self.assertRaises(ValueError): runner.successful_cache(run,job,{**rec,'status':status})
            (run/'responses/x.body').write_bytes(raw+b' ')
            with self.assertRaises(ValueError): runner.successful_cache(run,job,rec)
    def test_frozen_gate_blocks_unreviewed_confirmation(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td,patch.object(runner,'current',return_value=Path(td)),patch.object(runner,'check_identity',return_value={}),patch.object(runner,'send_job') as send:
            with self.assertRaises(FileNotFoundError): runner.execute('confirm')
            send.assert_not_called()
if __name__=='__main__': unittest.main()
