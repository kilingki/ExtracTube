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
        good={'frames':[{'frame_id':fid,'decision':'keep','reason':'x','duplicate_of':None} for fid in ('a','b')]}
        self.assertEqual(inference.task_parse(good,{'task':'t4','targets':['a','b']}),good)
        for mutate in (lambda x:x['frames'].reverse(),lambda x:x['frames'][0].update(duplicate_of='b'),lambda x:x['frames'][0].update(extra=1),lambda x:x['frames'].pop()):
            bad=copy.deepcopy(good);mutate(bad)
            with self.assertRaises(ValueError):inference.task_parse(bad,{'task':'t4','targets':['a','b']})
    def test_t5_use_cross_conditions_and_caption(self):
        base={'frame_id':'a','decision':'use','relevance':'yes','readability':'sufficient','completeness':'sufficient','visual_kind':'ui','caption':'화면','reason':'근거'}
        spec={'task':'t5','targets':['a']}
        inference.task_parse({'frames':[base]},spec)
        for field,value in [('relevance','no'),('readability','insufficient'),('completeness','uncertain'),('visual_kind','unknown'),('caption',None),('caption','x'*121),('reason','x'*301),('reason',' ')]:
            with self.subTest(field=field),self.assertRaises(ValueError):inference.task_parse({'frames':[{**base,field:value}]},spec)
        for decision in ('omit','uncertain'):
            with self.assertRaises(ValueError):inference.task_parse({'frames':[{**base,'decision':decision}]},spec)
            inference.task_parse({'frames':[{**base,'decision':decision,'caption':None}]},spec)
