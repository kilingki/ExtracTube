"""Offline contract and guardrail tests for T4 E04 (zero network or GPU calls)."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_e04 as r
if sys.path[0] != str(Path(__file__).resolve().parent):
    sys.path.insert(0, str(Path(__file__).resolve().parent))


class E04Tests(unittest.TestCase):
    def setUp(self):
        self.amends = [
            {'id': 'A1', 'source_indices': [134, 135], 'constraints': '시연 한정'},
            {'id': 'A2', 'source_indices': [137], 'constraints': '라벨 한정'},
        ]
        self.unit = {
            'unit_id': 'p_u10',
            'video_id': 'Fw-8hZhnH3Q',
            'section_hierarchy': {
                'unit_position': '11/16',
                'root_title': '검색 태그 수집',
                'leaf_title': '연관어 및 오타 수집',
            },
            'required_source_sentences': [
                {'sentence_index': 133, 'text': '관계없는 키워드는 지워주시면 돼요. ' * 8},
                {'sentence_index': 134, 'text': '기구.'},
                {'sentence_index': 135, 'text': '뚜렷봉 액체 아니죠, 욕실 뚜렷봉 액체 아니니까 지워주세요.'},
                {'sentence_index': 136, 'text': '뚜렷봉에서 엔터를 한번 치겠습니다.'},
                {'sentence_index': 137, 'text': '치면은 이렇게 영광 검색기가 여기에도 붙어요.'},
                {'sentence_index': 141, 'text': '변기 뚜렷빵 철자 틀린 것도 넣어주시고 배수구는 지워주세요. 하나하나 입력합니다.'},
            ],
            'resolved_cautions': [
                {'sentence_indices': [134, 135], 'guidance': '복합 부정 임의 분해 금지'},
                {'sentence_indices': [137], 'guidance': '의심 어휘 임의 교정 금지'},
            ],
            'internal_source_links': [],
            'previous_context': {'sentences': [], 'links_from_current_unit': []},
        }
        self.valid_output = {
            'unit_id': 'p_u10',
            'paragraphs': [
                {
                    'source_indices': [133, 134, 135],
                    'claim_mode': 'scoped_demonstration_or_uncertain_stt',
                    'amendment_refs': ['A1'],
                    'key_anchors': ['욕실 뚫어뻥 액체', '변기 뚫어뻥 기구'],
                    'text': "무관한 키워드는 지운다. 시연에서는 '욕실 뚫어뻥 액체'를 제외하고 '변기 뚫어뻥 기구' 등을 남긴다.",
                },
                {
                    'source_indices': [136, 137, 141],
                    'claim_mode': 'mixed_procedure_and_view',
                    'amendment_refs': ['A2'],
                    'key_anchors': ['연관검색어', '변기 뚜렷빵', '배수구'],
                    'text': "검색창에서 엔터를 치면 '연관검색어'가 표시된다. 이 설명에서는 '변기 뚜렷빵' 같은 오타도 포함하도록 권하며 '배수구'는 제외하고 하나하나 입력한다.",
                },
            ],
            'editorial_candidates': [],
            'unresolved': [],
        }

    def assert_rejected(self, mutator):
        v = copy.deepcopy(self.valid_output)
        mutator(v)
        with self.assertRaises((ValueError, TypeError)):
            r.validate(v, self.unit, self.amends)

    def test_valid_and_attribute_first_field_order(self):
        s = r.schema(self.unit, self.amends)
        para_keys = list(s['properties']['paragraphs']['items']['properties'].keys())
        self.assertEqual(para_keys, ['source_indices', 'claim_mode', 'amendment_refs', 'key_anchors', 'text'])
        out = r.validate(self.valid_output, self.unit, self.amends)
        self.assertEqual(len(out['blocks']), 2)

    def test_adaptive_paragraph_bounds(self):
        short_u = {'required_source_sentences': [{'sentence_index': i, 'text': '짧은 문장.'} for i in range(3)]}
        self.assertEqual(r.paragraph_bounds(short_u)['min_paragraphs'], 1)
        med_u = {'required_source_sentences': [{'sentence_index': i, 'text': '가' * 30} for i in range(11)]}
        self.assertEqual(r.paragraph_bounds(med_u)['min_paragraphs'], 2)
        long_u = {'required_source_sentences': [{'sentence_index': i, 'text': '가' * 33} for i in range(27)]}
        b_long = r.paragraph_bounds(long_u)
        self.assertEqual(b_long['min_paragraphs'], 4)
        self.assertEqual(b_long['max_span'], 12)

    def test_wall_of_text_blocked(self):
        def merge_all(v):
            v['paragraphs'] = [{
                'source_indices': [133, 134, 135, 136, 137, 141],
                'claim_mode': 'procedure_or_fact',
                'amendment_refs': ['A1', 'A2'],
                'key_anchors': ['연관검색어'],
                'text': "연관검색어를 수집한다.",
            }]
        self.assert_rejected(merge_all)

    def test_anchor_omission_blocked_and_comma_norm_allowed(self):
        self.assert_rejected(lambda v: (v['paragraphs'][0]['key_anchors'].append('73.4%'), v['paragraphs'][0].update(text=v['paragraphs'][0]['text'] + ' 생존율은 73%다.')))
        self.assert_rejected(lambda v: v['paragraphs'][0]['key_anchors'].append('   '))
        v2 = copy.deepcopy(self.valid_output)
        v2['paragraphs'][0]['key_anchors'].extend(['10000원', '지우기'])
        v2['paragraphs'][0]['text'] += ' 가격은 10,000원이며 관계없는 단어를 지우는 과정이다.'
        res = r.validate(v2, self.unit, self.amends)
        self.assertEqual(len(res['blocks']), 2)
        self.assertEqual(res['paraphrased_anchors'], [{'paragraph_index': 0, 'anchor': '지우기'}])

    def test_stutter_corruption_blocked(self):
        self.assert_rejected(
            lambda v: v['paragraphs'][0].update(
                text="시연에서는 '욕실 뚫어뻥 액체'를 제외하고 '변기 뚫어뚫어뻥 기구'와 '변기 뚫어뻥 기구'를 남긴다."
            )
        )

    def test_forensic_timestamp_leak_blocked(self):
        self.assert_rejected(
            lambda v: v['paragraphs'][0].update(
                text="626초와 629초 메모장을 비교하면 '욕실 뚫어뻥 액체'를 제외하고 '변기 뚫어뻥 기구'를 남긴다."
            )
        )
        v_prefix_ok = copy.deepcopy(self.valid_output)
        v_prefix_ok['paragraphs'][0]['text'] += ' 기준치 20 초과 시 제외한다.'
        self.assertEqual(len(r.validate(v_prefix_ok, self.unit, self.amends)['blocks']), 2)
        u_sec = copy.deepcopy(self.unit)
        u_sec['required_source_sentences'][0]['text'] += ' 10초 안에 처리한다.'
        v_sec = copy.deepcopy(self.valid_output)
        v_sec['paragraphs'][0]['text'] += ' 10초 안에 처리한다.'
        self.assertEqual(len(r.validate(v_sec, u_sec, self.amends)['blocks']), 2)

    def test_polite_ending_and_meta_relay_blocked(self):
        self.assert_rejected(
            lambda v: v['paragraphs'][0].update(
                text="시연에서는 '욕실 뚫어뻥 액체'를 제외하고 '변기 뚫어뻥 기구'를 남깁니다."
            )
        )
        self.assert_rejected(
            lambda v: v['paragraphs'][0].update(
                text="원문에서는 '욕실 뚫어뻥 액체'를 제외하고 '변기 뚫어뻥 기구'를 남긴다."
            )
        )
        v_ok = copy.deepcopy(self.valid_output)
        v_ok['paragraphs'][0]['text'] += ' 일반 규칙이 아니다.'
        self.assertEqual(len(r.validate(v_ok, self.unit, self.amends)['blocks']), 2)

    def test_internal_id_leak_with_korean_particle_blocked(self):
        self.assert_rejected(
            lambda v: v['paragraphs'][0].update(
                text="A1에서는 '욕실 뚫어뻥 액체'를 제외하고 '변기 뚫어뻥 기구'를 남긴다."
            )
        )
        self.assert_rejected(
            lambda v: v['paragraphs'][0].update(
                text="p_u10의 '욕실 뚫어뻥 액체'를 제외하고 '변기 뚫어뻥 기구'를 남긴다."
            )
        )

    def test_caution_hidden_in_editorial_blocked(self):
        def hide_caution(v):
            v['paragraphs'][0]['source_indices'] = [133, 135]
            v['editorial_candidates'] = [{'source_indices': [134], 'reason': '단독 어절 생략'}]
        self.assert_rejected(hide_caution)

    def test_monotonic_order_and_internal_backlink(self):
        self.assert_rejected(lambda v: v['paragraphs'].reverse())
        u_link = {
            'unit_id': 'p_u03',
            'video_id': 'Fw-8hZhnH3Q',
            'required_source_sentences': [
                {'sentence_index': 29, 'text': '로고와 라벨지가 확인되어야 한다. ' * 9},
                {'sentence_index': 30, 'text': '편법이 있을 수 있다.'},
                {'sentence_index': 31, 'text': '직접 제작 시 그대로 등록한다.'},
                {'sentence_index': 33, 'text': '앞서 말한 편법을 설명한다.'},
                {'sentence_index': 34, 'text': '샘플 사진이나 로고 편집을 활용한다.'},
            ],
            'resolved_cautions': [],
            'internal_source_links': [{'from_sentence_index': 33, 'to_sentence_indices': [30], 'relation': '편법 재참조'}],
            'previous_context': {'sentences': [], 'links_from_current_unit': []},
        }
        v_link = {
            'unit_id': 'p_u03',
            'paragraphs': [
                {'source_indices': [29, 30, 31], 'claim_mode': 'procedure_or_fact', 'key_anchors': ['라벨지'], 'text': '로고와 라벨지가 있으면 등록한다.'},
                {'source_indices': [30, 33, 34], 'claim_mode': 'procedure_or_fact', 'key_anchors': ['샘플 사진'], 'text': '앞서 언급한 편법으로 샘플 사진 등을 활용할 수 있다.'},
            ],
            'editorial_candidates': [],
            'unresolved': [],
        }
        self.assertEqual(len(r.validate(v_link, u_link, self.amends)['blocks']), 2)

    def test_amendment_scope_and_other_video_isolation(self):
        self.assert_rejected(lambda v: v['paragraphs'][1].update(amendment_refs=['A1']))
        other_u = copy.deepcopy(self.unit)
        other_u['video_id'] = 'H2WN130OzLg'
        s_other = r.schema(other_u, self.amends)
        self.assertNotIn('amendment_refs', s_other['properties']['paragraphs']['items']['properties'])

    def test_inline_caution_and_clean_projection(self):
        payload = r.project_unit_payload(self.unit, [self.unit], {'roots': [{'title': '태그'}]}, self.amends)
        dumped = r.b.wire(payload).decode()
        self.assertNotIn('626초', dumped)
        self.assertNotIn('sha256', dumped)
        s134 = next(x for x in payload['assigned_sentences'] if x['index'] == 134)
        self.assertIn('cautions', s134)
        self.assertIn('verified_visual_notes', s134)

    def test_semantic_counterexample_still_requires_review(self):
        v = copy.deepcopy(self.valid_output)
        v['paragraphs'][1]['text'] = "검색창에서 엔터를 치면 '연관검색어'가 표시된다. '변기 뚜렷빵'은 제외하고 '배수구'는 반드시 포함한다."
        self.assertEqual(len(r.validate(v, self.unit, self.amends)['blocks']), 2)

    def test_t5_input_builder_isolates_confirmation_and_preserves_flags(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            u_p = copy.deepcopy(self.unit)
            u_p['source_range'] = {'sentence_start': 133, 'sentence_end': 142}
            u_p['required_source_sentences'][0]['source_ref'] = {'path': 'data/Fw-8hZhnH3Q/audio/alignment_sentences.json', 'sha256': '0' * 64}
            u_p['assets'] = {
                'asset_ids': ['V01'],
                'asset_action': 'defer',
                'resolved_assets': [{'asset_id': 'V01', 'sha256': '1' * 64, 'caption': '화면 영역'}],
            }
            u_c = copy.deepcopy(u_p)
            u_c['unit_id'] = 'c_u00'
            u_c['video_id'] = 'H2WN130OzLg'
            u_c['source_range'] = {'sentence_start': 134, 'sentence_end': 140}
            u_c['assets'] = {'asset_ids': [], 'asset_action': 'skip', 'resolved_assets': []}
            jobs = [
                {'job_id': 'p_u10', 'phase': 'primary', 'unit': u_p},
                {'job_id': 'c_u00', 'phase': 'confirmation', 'unit': u_c},
            ]
            r.b.write(run / 'plan.json', jobs)
            r.b.write(run / 'config.json', {'policy': 'E04.attribute_first_adaptive_window_and_cpu_guard.v1'})
            r.b.write(run / 'source_amendments.json', self.amends)
            r.b.write(run / 'confirmation_freeze.json', {'primary_review_sha256': '2' * 64})
            for g in ['G1', 'G2', 'primary', 'confirmation']:
                r.b.write(run / 'reviews' / f'{g}.json', {'gate': g, 'decision': 'accepted'})
            r.b.write(run / 'parsed/p_u10.json', r.validate(self.valid_output, u_p, self.amends))
            c_out = {
                'unit_id': 'c_u00',
                'paragraphs': [{'source_indices': [133, 134, 135, 136, 137, 141], 'claim_mode': 'procedure_or_fact', 'key_anchors': ['키워드'], 'text': '키워드를 정리한다.'}],
                'editorial_candidates': [],
                'unresolved': [],
            }
            r.b.write(run / 'parsed/c_u00.json', {'unit_id': 'c_u00', 'content': c_out, 'blocks': [{'kind': 'paragraph', **c_out['paragraphs'][0]}], 'quote_unresolved': []})
            r.render(run)
            d = {
                'primary_video_id': 'Fw-8hZhnH3Q',
                'primary_sentence_range': [0, 217],
                'primary_sentence_count': 217,
                'reader_policy': 'excluded',
                'document_outline': {'sections': []},
                'generation_units': [u_p],
            }
            own = [
                {'job_id': 'p_u10', 'response_sha256': 'a' * 64, 'http_seconds': 10.0, 'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}},
                {'job_id': 'c_u00', 'response_sha256': 'b' * 64, 'http_seconds': 8.0, 'usage': {'prompt_tokens': 90, 'completion_tokens': 40, 'total_tokens': 130}},
            ]
            t5 = r.build_t5_input(run, d, {'policy': 'E04.attribute_first_adaptive_window_and_cpu_guard.v1'}, jobs, own, own)
            self.assertTrue(t5['T4_complete'])
            self.assertTrue(t5['t5_ready'])
            self.assertFalse(t5['user_accepted'])
            self.assertFalse(t5['final_article_user_accepted'])
            self.assertEqual([u['unit_id'] for u in t5['selected_units']], ['p_u10'])
            self.assertFalse(t5['confirmation_validation_summary']['mixed_into_primary_article'])
            self.assertEqual(t5['visual_and_asset_state']['deferred_assets'][0]['asset_id'], 'V01')


if __name__ == '__main__':
    unittest.main()
