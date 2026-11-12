"""Product Stage 3 contracts, using an actual verified Stage 2 fixture."""
import copy
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'stage2'))
from support import seed,adapters,FakeTransport,VIDEO_ID,settings as visual_settings
from extractube import content,content_plan,article,writing,infer,vision,store,pipeline
from extractube.settings import ContentSettings,Settings
from extractube.errors import ContentError,InferenceJobFailed


class Counter:
    identity={'fixture_tokenizer':'1'}
    def count(self,body):return 100


class Transport:
    def __init__(self,mode='normal'):self.calls=[];self.mode=mode
    def send(self,url,body,timeout,limit,on_headers):
        request=store.loads(body);self.calls.append(request)
        if self.mode=='unknown':raise OSError('simulated loss')
        payload=store.loads(request['messages'][1]['content']);name=request['response_format']['json_schema']['name']
        if name=='content_outline':
            start=payload.get('target_start',0)
            value={'sections':[{'start_sentence':start,'title':'입력과 저장','children':[]}]}
        elif name=='content_repair':
            value={'action':'keep','evidence_indices':[s['sentence_index'] for s in payload['source']],'replacement':'','reason':'원문을 유지한다.'}
        else:
            indices=[s['index'] for s in payload['assigned_sentences']]
            value={'unit_id':payload['unit_id'],'paragraphs':[{'source_indices':indices,'claim_mode':'procedure',
                'key_anchors':[],'text':'입력 칸을 확인하고 저장한다.'}], 'editorial_candidates':[],'unresolved':[]}
        if self.mode=='bad_once' and len(self.calls)==2:value['paragraphs'][0]['source_indices']=[0]
        raw=store.wire({'choices':[{'finish_reason':'stop','message':{'content':store.wire(value).decode()}}],
                        'usage':{'prompt_tokens':100,'completion_tokens':50}})
        on_headers(200,{});return {'status':200,'body':raw,'complete':True}


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.work=seed(self.root)
        self.adapters=adapters(FakeTransport());self.adapters.__enter__()
        self.visual=vision.run(VIDEO_ID,visual_settings(),data_root=self.root,log=None)
        self.fake=Transport();self.transport_patch=patch.object(infer,'make_transport',return_value=self.fake);self.transport_patch.start()
        self.settings=ContentSettings(max_inference_calls=10,max_http_seconds=3000)
    def tearDown(self):
        self.transport_patch.stop();self.adapters.__exit__(None,None,None);self.temp.cleanup()
    def run_content(self,**kwargs):
        return content.run(VIDEO_ID,kwargs.pop('settings',self.settings),data_root=self.root,counter=kwargs.pop('counter',Counter()),log=None,**kwargs)
    def test_generation_verify_exact_reuse_and_html(self):
        result=self.run_content();self.assertEqual(result.inference_calls,2)
        self.assertEqual(result.quality_status,'unreviewed')
        self.assertTrue(content.verify(VIDEO_ID,data_root=self.root)['ok'])
        again=self.run_content();self.assertEqual(result.run_id,again.run_id);self.assertEqual(len(self.fake.calls),2)
        text=result.html_path.read_text();self.assertIn('data:image/webp;base64,',text);self.assertNotIn('src="http',text)
    def test_upstream_change_rejected(self):
        self.run_content();meta=store.read(self.work/'metadata.json');meta['results']['sentences']['result_id']='changed';store.write(self.work/'metadata.json',meta)
        with self.assertRaises(Exception):content.verify(VIDEO_ID,data_root=self.root)
        self.assertEqual(len(self.fake.calls),2)
    def test_result_tampering_rejected(self):
        r=self.run_content();doc=store.read(r.article_path);doc['sections'][0]['paragraphs'][0]['text']='조작';store.write(r.article_path,doc)
        with self.assertRaises(Exception):content.verify(VIDEO_ID,data_root=self.root)
    def test_budget_pause_and_resume_without_resending_outline(self):
        low=replace(self.settings,max_inference_calls=1)
        first=self.run_content(settings=low);self.assertEqual(first.execution_status,'paused_budget')
        second=self.run_content(settings=low,resume_run_id=first.run_id,resume_budget_limits={'max_inference_calls':3})
        self.assertIn(second.execution_status,content.COMPLETE);self.assertEqual(len(self.fake.calls),2)
    def test_unknown_blocks_resume_and_new_run(self):
        self.fake.mode='unknown';first=self.run_content();self.assertEqual(first.execution_status,'blocked_unknown')
        for kwargs in ({},{'resume_run_id':first.run_id}):
            with self.assertRaises(ContentError) as caught:self.run_content(**kwargs)
            self.assertEqual(caught.exception.kind,'blocked_unknown')
        self.assertEqual(len(self.fake.calls),1)
    def test_input_limit_sends_nothing(self):
        with self.assertRaises(ContentError):self.run_content(settings=replace(self.settings,input_token_limit=50))
        self.assertEqual(self.fake.calls,[])
    def test_bounded_contract_repair(self):
        self.fake.mode='bad_once';r=self.run_content();self.assertEqual(r.inference_calls,3)
        self.assertEqual(len(list((r.article_path.parent/'drafts').glob('*.repair.json'))),1)
    def test_render_only_no_inference(self):
        first=self.run_content();rendered=content.render_only(VIDEO_ID,data_root=self.root)
        self.assertNotEqual(first.run_id,rendered.run_id);self.assertEqual(len(self.fake.calls),2)
        self.assertTrue(content.verify(VIDEO_ID,data_root=self.root,run_id=rendered.run_id)['ok'])
    def test_recovery_uses_content_namespace(self):
        r=self.run_content();directory=r.article_path.parent
        records=list((directory/'calls').glob('*/record.json'));record=store.read(records[0]);records[0].unlink()
        for path in (directory/'jobs').glob('*.json'):
            journal=store.read(path)
            for attempt in journal['attempts']:
                if attempt['call_id']==record['call_id']:attempt['status']='sending'
            store.write(path,journal)
        content.recover(self.work)
        recovered=store.read(records[0]);self.assertEqual(recovered['status'],'succeeded')
        self.assertTrue((self.work/'content/cache/jobs'/record['job_key']/'result.json').exists())
        self.assertFalse((self.work/'vision/cache/jobs'/record['job_key']).exists())
    def test_pipeline_releases_locks_and_connects_same_visual_run(self):
        with patch.object(pipeline,'_reusable',return_value=True),patch.object(content_plan,'TokenCounter',return_value=Counter()):
            r=pipeline.run('https://example.test',Settings(data_root=self.root),video_id=VIDEO_ID,
                           through='stage3',vision_settings=visual_settings(),content_settings=self.settings,log=None)
        self.assertEqual(r.vision.run_id,self.visual.run_id);self.assertIsNotNone(r.content)
    def test_schema_partition_rejects_gap_and_derives_ends(self):
        spec={'schema':content_plan.outline_schema(0,10),'target':[0,10]}
        with self.assertRaises(Exception):
            content_plan.validate_outline({'sections':[{'start_sentence':1,'title':'절','children':[]}]},spec)
        with self.assertRaises(Exception):
            content_plan.validate_outline({'sections':[{'start_sentence':0,'title':'절1','children':[]},{'start_sentence':0,'title':'절2','children':[]}]},spec)
        normalized=content_plan.validate_outline({
            'sections':[
                {'start_sentence':0,'title':'대주제','children':[{'start_sentence':3,'title':'세부주제'}]},
                {'start_sentence':7,'title':'마무리','children':[]},
            ]
        },spec)
        self.assertEqual(normalized['sections'][0]['sentence_start'],0)
        self.assertEqual(normalized['sections'][0]['sentence_end'],7)
        # Intro gap [0,3) is deterministically preserved as a child leaf under root title.
        self.assertEqual([(c['sentence_start'],c['sentence_end'],c['title']) for c in normalized['sections'][0]['children']],
                         [(0,3,'대주제'),(3,7,'세부주제')])
        self.assertEqual((normalized['sections'][1]['sentence_start'],normalized['sections'][1]['sentence_end']),(7,10))

    def test_oversized_leaf_auto_splits_without_duplicate_h3(self):
        bundle={'schema_version':'extractube.content.source.v1','video_id':VIDEO_ID,
                'sentences':[{'sentence_index':i,'text':f'문장 {i}.','audio_start':str(i),'audio_end':str(i+1)} for i in range(40)],
                'units':[],'assets':[],'coverage':{},'omitted_audio_chunks':[],'warnings':[],'usage_policy':{},
                'source':{'audio_to_video_offset_seconds':'0'},'source_url':'https://example.test'}
        outline={'sections':[{'sentence_start':0,'sentence_end':40,'title':'전체','children':[{'sentence_start':0,'sentence_end':40,'title':'하위절'}]}]}
        plan=content_plan.generation_plan(bundle,outline)
        self.assertEqual(len(plan['units']),2)
        self.assertEqual([s['sentence_index'] for u in plan['units'] for s in u['sentences']],list(range(40)))
        drafts=[{'unit_id':u['unit_id'],'paragraphs':[{'source_indices':[s['sentence_index'] for s in u['sentences']],'claim_mode':'procedure','key_anchors':[],'text':'본문 단락.'}],'editorial_candidates':[],'unresolved':[]} for u in plan['units']]
        built=article.assemble(bundle,outline,plan,drafts,'제목')
        rendered=article.render(built,self.work).decode('utf-8')
        self.assertEqual(rendered.count('<h3>하위절</h3>'),1)

    def test_multi_slice_outline_and_verify(self):
        class SizeCounter:
            identity={'fixture_tokenizer':'size'}
            def count(self,body):
                payload=store.loads(body['messages'][1]['content'])
                if 'sentences' in payload:
                    return 60 + 40 * len(payload['sentences'])
                return 100
        class DynamicPromptUsageTransport(Transport):
            def send(self,url,body,timeout,limit,on_headers):
                res=super().send(url,body,timeout,limit,on_headers)
                req=store.loads(body)
                toks=SizeCounter().count(req)
                doc=store.loads(res['body'])
                doc['usage']['prompt_tokens']=toks
                res['body']=store.wire(doc)
                return res
        self.fake=DynamicPromptUsageTransport()
        self.transport_patch.stop()
        self.transport_patch=patch.object(infer,'make_transport',return_value=self.fake)
        self.transport_patch.start()
        # 3 sentences => full outline = 60 + 120 = 180 > 150, while 2-sentence slice = 140 <= 150!
        sliced_settings=replace(self.settings,input_token_limit=150)
        res=self.run_content(settings=sliced_settings,counter=SizeCounter())
        self.assertIn(res.execution_status,content.COMPLETE)
        outline_doc=store.read(res.article_path.parent/'outline.json')
        self.assertIn('parts',outline_doc)
        self.assertEqual(len(outline_doc['parts']),2)
        self.assertTrue(content.verify(VIDEO_ID,data_root=self.root,run_id=res.run_id)['ok'])
    def test_unreviewed_patch_rejected(self):
        r=self.run_content();doc=store.read(r.article_path);p=doc['sections'][0]['paragraphs'][0]
        ticket={'paragraph_id':p['paragraph_id'],'span':[0,2],'original':p['text'][:2]}
        proposal={'action':'replace','replacement':'변경'}
        with self.assertRaises(ValueError):writing.apply_patch(doc,ticket,proposal,{'decision':'accepted'})
    def test_multiword_near_miss_quote_and_anchor_guards(self):
        unit={'unit_id':'u0000','root_title':'태그 수집','title':'검색어 정리',
              'sentences':[{'sentence_index':0,'text':'변기 뚫어뻥을 검색하고 샤워커튼은 해지해 주시면 됩니다.'},
                           {'sentence_index':1,'text':'출고 소요일은 1일 이내로 설정한다.'}],
              'previous_context':[]}
        spec={'schema':writing.schema(unit),'unit':unit}
        bad_quote={'unit_id':'u0000','paragraphs':[{'source_indices':[0,1],'claim_mode':'procedure',
            'key_anchors':['변기 뚫어뻥','하루'],'text':"검색창에 '변기 뚫어빵'을 입력하고 당일 출고로 설정한다."}],
            'editorial_candidates':[],'unresolved':[]}
        with self.assertRaisesRegex(ValueError,'near_miss_quote'):
            writing.validate(bad_quote,spec)
        bad_noun={'unit_id':'u0000','paragraphs':[{'source_indices':[0,1],'claim_mode':'procedure',
            'key_anchors':['변기 뚫어뻥','해지','1일'],'text':"검색창에 '변기 뚫어뻥'을 입력하고 체크를 푼 뒤 하루 이내로 설정한다."}],
            'editorial_candidates':[],'unresolved':[]}
        with self.assertRaisesRegex(ValueError,'missing_noun_anchor:해지'):
            writing.validate(bad_noun,spec)
        good={'unit_id':'u0000','paragraphs':[{'source_indices':[0,1],'claim_mode':'procedure',
            'key_anchors':['변기 뚫어뻥','해지','1일'],'text':"검색창에 '변기 뚫어뻥'을 입력하고 무관 항목은 해지한 뒤 당일 출고로 설정한다."}],
            'editorial_candidates':[],'unresolved':[]}
        self.assertEqual(writing.validate(good,spec),good)
    def test_two_review_tickets_against_base_article(self):
        r=self.run_content();base=store.read(r.article_path);base_sha=store.digest(base)
        p=base['sections'][0]['paragraphs'][0]
        proposal={'action':'keep','evidence_indices':[0,1],'replacement':'','reason':'원문을 유지한다.'}
        acceptance={'decision':'accepted','reviewer':'tester','proposal_sha256':store.digest(proposal),
                    'article_sha256':base_sha,'paragraph_and_neighbors_reviewed':True}
        review={'tickets':[
            {'video_id':VIDEO_ID,'article_sha256':base_sha,'paragraph_id':p['paragraph_id'],
             'span':[0,2],'original':p['text'][:2],'evidence_indices':[0,1],'observation':'첫 구절 점검','acceptance':acceptance},
            {'video_id':VIDEO_ID,'article_sha256':base_sha,'paragraph_id':p['paragraph_id'],
             'span':[3,5],'original':p['text'][3:5],'evidence_indices':[0,1],'observation':'둘째 구절 점검','acceptance':acceptance},
        ]}
        review_path=self.root/'review.json';store.write(review_path,review)
        reviewed=self.run_content(review_path=review_path)
        self.assertEqual(reviewed.quality_status,'revise')
        self.assertTrue(content.verify(VIDEO_ID,data_root=self.root,run_id=reviewed.run_id)['ok'])
    def test_schema_blocks_paragraph_span_and_validates_korean_numerals_and_indirect_quotes(self):
        import jsonschema
        unit={'unit_id':'u0000','root_title':'수익과 회귀 설명','title':'판매량과 확률 예시',
              'sentences':[{'sentence_index':i,'text':f'문장 {i}번은 강의 흐름을 설명하는 충분히 긴 원문 발화입니다.'} for i in range(20)],
              'previous_context':[]}
        unit['sentences'][0]['text']='10,000개 팔면 100만 원이고 구십구퍼센트 성공하며 매출이 안 죽네요.'
        unit['sentences'][1]['text']='입출력 차원에서 16분의 1 고양이이고 이게 하면 된다라는 확신이 생깁니다.'
        sch=writing.schema(unit)
        b=writing.bounds(unit)
        self.assertLess(b['max_sentences_per_paragraph'],20)
        over_span={'unit_id':'u0000','paragraphs':[
            {'source_indices':list(range(b['max_sentences_per_paragraph']+1)),'claim_mode':'source_fact','key_anchors':[],'text':'초과 단락이다.'},
            {'source_indices':list(range(b['max_sentences_per_paragraph']+1,20)),'claim_mode':'source_fact','key_anchors':[],'text':'나머지 단락이다.'},
        ],'editorial_candidates':[],'unresolved':[]}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(over_span,sch)
        valid_doc={'unit_id':'u0000','paragraphs':[
            {'source_indices':list(range(7)),'claim_mode':'speaker_view',
             'key_anchors':['10,000개 판매 시 100만 원','구십구퍼센트','오래','왜냐면'],
             'text':"1만 개를 판매하면 100만 원 수익이 발생하며 99% 확률로 '매출이 안 죽는다'고 언급한다."},
            {'source_indices':list(range(7,14)),'claim_mode':'speaker_view',
             'key_anchors':['입출력','16분의 1 고양이','카우님','끝났습니다'],
             'text':"입력과 출력 구조상 십육분의 일 고양이가 되며 화자는 '이렇게 하면 된다'는 확신이 중요하다고 설명한다."},
            {'source_indices':list(range(14,20)),'claim_mode':'source_fact',
             'key_anchors':[],'text':'이후 강의 흐름에 대한 설명이 이어진다.'},
        ],'editorial_candidates':[],'unresolved':[]}
        self.assertEqual(writing.validate(valid_doc,{'schema':sch,'unit':unit}),valid_doc)
    def test_proportional_repair_cap_and_soft_anchor_fallback_verify(self):
        self.assertEqual(content._effective_max_repairs(self.settings,1),2)
        self.assertEqual(content._effective_max_repairs(self.settings,30),8)
        self.assertEqual(content._effective_max_repairs(self.settings,72),18)
        self.assertEqual(content._effective_max_repairs(replace(self.settings,max_auto_repairs=0),72),0)
        class SoftAnchorOnRepairTransport(Transport):
            def send(self,url,body,timeout,limit,on_headers):
                request=store.loads(body);self.calls.append(request)
                payload=store.loads(request['messages'][1]['content']);name=request['response_format']['json_schema']['name']
                if name=='content_outline':
                    value={'sections':[{'start_sentence':0,'title':'입력과 저장','children':[]}]}
                else:
                    indices=[s['index'] for s in payload['assigned_sentences']]
                    # Trigger missing_noun_anchor:'입력칸' on both initial and repair attempts while keeping structural coverage 100% intact.
                    value={'unit_id':payload['unit_id'],'paragraphs':[{'source_indices':indices,'claim_mode':'procedure',
                        'key_anchors':['입력칸'],'text':'빈칸을 확인하고 저장한다.'}],'editorial_candidates':[],'unresolved':[]}
                raw=store.wire({'choices':[{'finish_reason':'stop','message':{'content':store.wire(value).decode()}}],
                                'usage':{'prompt_tokens':100,'completion_tokens':50}})
                on_headers(200,{});return {'status':200,'body':raw,'complete':True}
        self.fake=SoftAnchorOnRepairTransport()
        self.transport_patch.stop()
        self.transport_patch=patch.object(infer,'make_transport',return_value=self.fake)
        self.transport_patch.start()
        res=self.run_content()
        self.assertEqual(res.execution_status,'complete_with_warnings')
        self.assertEqual(res.quality_status,'revise')
        self.assertEqual(res.unresolved,1)
        self.assertEqual(res.inference_calls,3)
        self.assertTrue(content.verify(VIDEO_ID,data_root=self.root,run_id=res.run_id)['ok'])


if __name__=='__main__':unittest.main()
