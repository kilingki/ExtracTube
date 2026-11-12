import copy
import unittest
from extractube import infer as inference, prompts
from extractube.store import wire, loads
from extractube.visual_policy import digest_text

class ParserTests(unittest.TestCase):
    def response(self,content,finish='stop',usage=None):return wire({'choices':[{'finish_reason':finish,'message':{'content':content},'index':0}],'usage':usage})
    def test_prompt_bytes_and_single_pass(self):
        for name in prompts.NAMES:self.assertEqual(digest_text(getattr(prompts,name)),prompts.EXPECTED_SHA256[name])
        self.assertEqual(prompts.render('{a} {b}',{'a':'{b}','b':'safe'}),'{b} safe')
    def test_outer_json_rejects_duplicate_nan_utf8_and_extra_choices(self):
        for body in (b'{"choices":[],"choices":[]}',b'{"choices":NaN}',b'\xff',wire({'choices':[]}),wire({'choices':[{},{}]})):
            with self.subTest(body=body),self.assertRaises((ValueError,UnicodeError)):inference.parse_response(body,200,{'task':'t1.verify'},False)
    def test_single_fence_and_text_fragments(self):
        for content in ('```json\n{"action":"keep","reason":"근거"}\n```',[{'type':'text','text':'{"action":"keep",'},'"reason":"근거"}']):
            parsed,status,info=inference.parse_response(self.response(content),200,{'task':'t1.verify'},False)
            self.assertEqual(parsed['action'],'keep')
        for content in ('{} trailing','{}{}','prefix {}','```python\n{}\n```'):
            with self.assertRaises(ValueError):inference.parse_response(self.response(content),200,{'task':'t1.verify'},False)
    def test_guard_exact_and_counterexamples(self):
        usage={'completion_tokens':4096,'completion_tokens_details':{'reasoning_tokens':4096}}
        for content in (None,'','  '):
            self.assertEqual(inference.parse_response(self.response(content,'length',usage),200,{'task':'t2'},True)[1],'budget_exhausted')
        counter=[(None,'length',None,200,True),('{}','length',usage,200,True),(None,'length',usage,502,True),(None,'length',usage,200,False),(None,'stop',usage,200,True)]
        for field in ('completion_tokens','reasoning_tokens'):
            u=copy.deepcopy(usage)
            if field=='reasoning_tokens':u['completion_tokens_details'][field]=4095
            else:u[field]=4095
            counter.append((None,'length',u,200,True))
        for content,finish,u,status,thinking in counter:
            with self.subTest(content=content,usage=u),self.assertRaises(ValueError):inference.parse_response(self.response(content,finish,u),status,{'task':'t2'},thinking)
    def test_t1_index_bool_duplicate_and_conflict_move(self):
        spec={'task':'t1.boundary','allowed':[1,2]}
        for rows in ([{'sentence_index':True,'reason':'x'}],[{'sentence_index':0,'reason':'x'}],[{'sentence_index':1,'reason':' '}],[{'sentence_index':1,'reason':'x'}]*2):
            with self.assertRaises(ValueError):inference.task_parse({'boundaries':rows},spec)
        spec={'task':'t1.conflict','allowed':[1,2],'move_allowed':[2,3]}
        good={'decisions':[{'sentence_index':1,'action':'move','to_index':3,'reason':'x'},{'sentence_index':2,'action':'move','to_index':3,'reason':'x'}]}
        self.assertEqual(inference.task_parse(good,spec),good)
        with self.assertRaises(ValueError):inference.task_parse({'decisions':good['decisions'][:1]},spec)
    def test_t2_evidence_target_only(self):
        for evidence in ([0],[True],[1,1],[],[1,2,3,4]):
            with self.assertRaises(ValueError):inference.task_parse({'decision':'needed','reason':'x','evidence_sentence_indices':evidence},{'task':'t2','allowed':[1,2,3,4]})
    def test_t4_target_order_duplicates_extra_key(self):
        base={'decision':'use','relevance':'yes','readability':'sufficient','completeness':'sufficient','visual_kind':'ui','caption':'화면','reason':'x','duplicate_of':None}
        good={'frames':[{'frame_id':fid,**base} for fid in ('a','b')]}
        self.assertEqual(inference.task_parse(good,{'task':'t4','targets':['a','b']}),good)
        for mutate in (lambda x:x['frames'].reverse(),lambda x:x['frames'][0].update(decision='omit',caption=None,duplicate_of='b'),lambda x:x['frames'][0].update(extra=1),lambda x:x['frames'].pop()):
            bad=copy.deepcopy(good);mutate(bad)
            with self.assertRaises(ValueError):inference.task_parse(bad,{'task':'t4','targets':['a','b']})
    def test_t4_and_t5_use_cross_conditions_and_caption(self):
        base={'frame_id':'a','decision':'use','relevance':'yes','readability':'sufficient','completeness':'sufficient','visual_kind':'ui','caption':'화면','reason':'근거','duplicate_of':None}
        spec={'task':'t4','targets':['a']}
        inference.task_parse({'frames':[base]},spec)
        for field,value in [('relevance','no'),('readability','insufficient'),('completeness','uncertain'),('visual_kind','unknown'),('caption',None),('caption','x'*121),('reason','x'*301),('reason',' '),('duplicate_of','other')]:
            with self.subTest(field=field),self.assertRaises(ValueError):inference.task_parse({'frames':[{**base,field:value}]},spec)
        for decision in ('omit','uncertain'):
            with self.assertRaises(ValueError):inference.task_parse({'frames':[{**base,'decision':decision}]},spec)
            inference.task_parse({'frames':[{**base,'decision':decision,'caption':None}]},spec)

    def test_representatives_cannot_point_forward_or_to_uncertain_and_normalize_omit_chain(self):
        import jsonschema
        spec={'task':'t4','targets':['a','b','c'],'context_ids':['prior']}
        rows=[{'frame_id':fid,'decision':'omit','relevance':'yes','readability':'sufficient','completeness':'sufficient','visual_kind':'ui','caption':None,'reason':'same information','duplicate_of':'prior'} for fid in spec['targets']]
        inference.task_parse({'frames':rows},spec)
        for target in ('a','b','c','other'):
            bad=copy.deepcopy(rows);bad[0]['duplicate_of']=target
            with self.assertRaises(ValueError):inference.task_parse({'frames':bad},spec)
        # Per-slot prefixItems schema also blocks forward/self references and relevance='no' duplicate_of at decode time!
        schema=inference.visual_schema('t4',spec)
        for bad_dup in ('a','b','c'):
            bad_s=copy.deepcopy(rows);bad_s[0]['duplicate_of']=bad_dup
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({'frames':bad_s},schema)
        bad_rel=copy.deepcopy(rows);bad_rel[0]['relevance']='no'
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({'frames':bad_rel},schema)
        rows[0].update(decision='uncertain',duplicate_of=None);rows[1]['duplicate_of']='a'
        with self.assertRaises(ValueError):inference.task_parse({'frames':rows},spec)
        rows[0].update(decision='use',caption='대표 화면',duplicate_of=None);rows[1]['duplicate_of']='prior'
        inference.task_parse({'frames':rows},spec)
        # Intra-batch omit -> omit(prior) deterministically normalizes to root 'prior'; omit -> omit(None) normalizes to None!
        rows[2]['duplicate_of']='b'
        normalized=inference.task_parse({'frames':copy.deepcopy(rows)},spec)
        self.assertEqual(normalized['frames'][2]['duplicate_of'],'prior')
        rows[1]['duplicate_of']=None;rows[2]['duplicate_of']='b'
        normalized_null=inference.task_parse({'frames':copy.deepcopy(rows)},spec)
        self.assertIsNone(normalized_null['frames'][2]['duplicate_of'])

    def test_schema_rejects_array_root_and_invalid_use(self):
        import jsonschema
        for task in ('t4','t5'):
            schema=inference.visual_schema(task,{'targets':['a']})
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate([],schema)
        row={'frame_id':'a','decision':'use','duplicate_of':None,'relevance':'yes','readability':'sufficient','completeness':'sufficient','visual_kind':'ui','caption':'입력 화면','reason':'식별 가능'}
        schema=inference.visual_schema('t4',{'targets':['a']})
        jsonschema.validate({'frames':[row]},schema)
        for change in ({'caption':None},{'relevance':'no'},{'decision':'omit'}):
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({'frames':[{**row,**change}]},schema)

    def test_cross_batch_representative_deduplicates_and_reuses_without_calls(self):
        import tempfile
        from pathlib import Path
        from support import seed,adapters,settings,FakeTransport,VIDEO_ID
        from extractube import vision,handoff
        from extractube.store import read
        class AllUnitsNeeded(FakeTransport):
            def send(self,url,body,timeout,limit,on_headers):
                request=loads(body)
                if request.get('response_format',{}).get('json_schema',{}).get('name')=='t2':
                    self.calls.append(request)
                    props=request['response_format']['json_schema']['schema']['properties']['chunks']['properties']
                    chunks_out={cid:{'decision':'needed','reason':'시각 확인','evidence_sentence_indices':[cschema['properties']['evidence_sentence_indices']['items']['enum'][0]]} for cid,cschema in props.items()}
                    response=wire({'choices':[{'finish_reason':'stop','message':{'content':wire({'chunks':chunks_out}).decode()}}]})
                    on_headers(200,{});return {'status':200,'body':response,'complete':True}
                return super().send(url,body,timeout,limit,on_headers)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=seed(root);fake=AllUnitsNeeded()
            with adapters(fake):
                result=vision.run(VIDEO_ID,settings(),data_root=root,log=None)
                self.assertTrue(result.stage3_usable)
                self.assertTrue(handoff.verify(VIDEO_ID,data_root=root)['ok'])
                run=work/'vision/runs'/result.run_id
                t4=read(run/'t4/decisions.json')['payload']
                self.assertGreaterEqual(len(t4['batches']),2)
                # Batch 1 receives confirmed 'use' representative from Batch 0 as cross-candidate context!
                self.assertEqual(t4['batches'][1]['context_ids'],[f'{VIDEO_ID}:v0:pts10250'])
                frames=read(run/'t4/frames.json')['payload']['associations']
                self.assertGreaterEqual(sum(bool(a.get('t4_duplicate_of')) for a in frames),2)
                t5=read(run/'t5/visual_assets.json')['payload']
                self.assertEqual(t5['batches'],[])
                self.assertEqual(len(t5['assets']),1)
                calls=len(fake.calls)
                self.assertEqual(vision.run(VIDEO_ID,settings(),data_root=root,log=None).inference_calls,0)
                self.assertEqual(len(fake.calls),calls)
