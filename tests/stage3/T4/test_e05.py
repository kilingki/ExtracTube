"""Offline contract, Hangul Jamo near-miss quote guard, and repair ledger tests for T4 E05 (zero network or GPU calls)."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_e05 as r
if sys.path[0] != str(Path(__file__).resolve().parent):
    sys.path.insert(0, str(Path(__file__).resolve().parent))


class E05Tests(unittest.TestCase):
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
                    'text': "사이트 검색창에서 엔터를 치면 '연관검색어'가 표시된다. 이 설명에서는 '변기 뚜렷빵' 같은 오타도 포함하도록 권하며 '배수구'는 제외하고 하나하나 입력한다.",
                },
            ],
            'editorial_candidates': [],
            'unresolved': [
                {'source_indices': [134, 135, 137], 'reason': '원시 발화 전사 모호성은 시각 노트로만 한정 서술'},
            ],
        }

    def assert_rejected(self, mutator, unit=None):
        v = copy.deepcopy(self.valid_output)
        mutator(v)
        with self.assertRaises((ValueError, TypeError)):
            r.validate(v, unit or self.unit, self.amends)

    def test_valid_and_attribute_first_field_order(self):
        s = r.schema(self.unit, self.amends)
        para_keys = list(s['properties']['paragraphs']['items']['properties'].keys())
        self.assertEqual(para_keys, ['source_indices', 'claim_mode', 'amendment_refs', 'key_anchors', 'text'])
        out = r.validate(self.valid_output, self.unit, self.amends)
        self.assertEqual(len(out['blocks']), 2)

    def test_jamo_near_miss_blocks_bpe_hybrid_typos_and_stt_normalizations(self):
        # E04-R1-F01 exact BPE hybrid '뚫어빵' & '변기 뚫어빵' MUST be rejected
        self.assert_rejected(
            lambda v: v['paragraphs'][1].update(
                text="검색창에서 엔터를 치면 '연관검색어'가 표시된다. 화자는 '뚫어빵' 같은 오타도 넣으라고 권하며 '배수구'는 제외한다."
            )
        )
        self.assert_rejected(
            lambda v: v['paragraphs'][1].update(
                text="검색창에서 엔터를 치면 '연관검색어'가 표시된다. 화자는 '변기 뚫어빵' 같은 오타도 넣으라고 권하며 '배수구'는 제외한다."
            )
        )
        # Primary [174] '상품 정보 제공 곳' -> silent normalization '상품정보 제공고시' MUST be rejected
        u174 = {
            'unit_id': 'p_u12',
            'video_id': 'Fw-8hZhnH3Q',
            'section_hierarchy': {'unit_position': '13/16', 'root_title': '상세 설정', 'leaf_title': '정보 설정'},
            'required_source_sentences': [{'sentence_index': 174, 'text': '이제 상품 정보 제공 곳이라는 게 있는데 상세페이지 참조로 일괄 체크합니다.'}],
            'resolved_cautions': [{'sentence_indices': [174], 'guidance': '임의 교정 금지'}],
            'internal_source_links': [],
            'previous_context': {'sentences': [], 'links_from_current_unit': []},
        }
        bad174 = {
            'unit_id': 'p_u12',
            'paragraphs': [{'source_indices': [174], 'claim_mode': 'procedure_or_fact', 'key_anchors': ['상세페이지 참조'], 'text': "'상품정보 제공고시' 항목은 상세페이지 참조로 체크한다."}],
            'editorial_candidates': [],
            'unresolved': [],
        }
        with self.assertRaises(ValueError) as ctx174:
            r.validate(bad174, u174, self.amends)
        self.assertIn('near_miss_unverified_quote', str(ctx174.exception))

        # Confirmation [188] '통신판매분 어때요?' -> silent quote '통신판매업' MUST be rejected
        u188 = {
            'unit_id': 'c_u06',
            'video_id': 'H2WN130OzLg',
            'section_hierarchy': {'unit_position': '7/7', 'root_title': '진입 장벽과 유통 기한', 'leaf_title': '복제 시간과 사업 수명'},
            'required_source_sentences': [{'sentence_index': 188, 'text': '통신판매분 어때요? 컴퓨터만 있으면 오늘 등록되는 거 아니야?'}],
            'resolved_cautions': [],
            'internal_source_links': [],
            'previous_context': {'sentences': [], 'links_from_current_unit': []},
        }
        bad188 = {
            'unit_id': 'c_u06',
            'paragraphs': [{'source_indices': [188], 'claim_mode': 'procedure_or_fact', 'key_anchors': ['컴퓨터'], 'text': "'통신판매업'은 컴퓨터만 있으면 당일 등록할 수 있다."}],
            'editorial_candidates': [],
            'unresolved': [],
        }
        with self.assertRaises(ValueError) as ctx188:
            r.validate(bad188, u188, self.amends)
        self.assertIn('near_miss_unverified_quote', str(ctx188.exception))

    def test_jamo_guard_allows_legitimate_inflections_and_ui_suffixes(self):
        u_c01 = {
            'unit_id': 'c_u01',
            'video_id': 'H2WN130OzLg',
            'section_hierarchy': {'unit_position': '2/7', 'root_title': '낮은 진입 장벽', 'leaf_title': '네 가지 유행어와 허들 부재'},
            'required_source_sentences': [{'sentence_index': 146, 'text': '무자본, 뭐 노스킬, 뭐 누구나 할 수 있고 오늘 시작할 수 있고 이런 거.'}],
            'resolved_cautions': [],
            'internal_source_links': [],
            'previous_context': {'sentences': [], 'links_from_current_unit': []},
        }
        ok_c01 = {
            'unit_id': 'c_u01',
            'paragraphs': [{
                'source_indices': [146],
                'claim_mode': 'speaker_view',
                'key_anchors': ['무자본', '노스킬'],
                'text': "화자는 '무자본', '노스킬', '누구나 할 수 있다', '오늘 시작할 수 있다' 같은 표현이 진입 장벽 부재를 뜻한다고 설명한다.",
            }],
            'editorial_candidates': [],
            'unresolved': [],
        }
        self.assertEqual(len(r.validate(ok_c01, u_c01, self.amends)['blocks']), 1)

    def test_simultaneous_missing_noun_anchor_and_near_miss_quote_collected(self):
        v = copy.deepcopy(self.valid_output)
        v['paragraphs'][1]['key_anchors'] = ['연관검색어', '변기 뚫어빵', '배수구']
        v['paragraphs'][1]['text'] = "사이트 검색창에서 엔터를 치면 '연관검색어'가 표시된다. 이 설명에서는 '변기 뚫어빵' 같은 오타도 포함하도록 권하며 하나하나 입력한다."
        with self.assertRaises(ValueError) as ctx:
            r.validate(v, self.unit, self.amends)
        msg = str(ctx.exception)
        self.assertIn('missing_noun_anchor:배수구', msg)
        self.assertIn('near_miss_unverified_quote', msg)

    def test_visual_note_a1_forbids_raw_stt_redecomposition(self):
        self.assertIn('다시 분해해', r.VISUAL_NOTE_PROJECTIONS['A1'])
        self.assertNotIn('626초', r.VISUAL_NOTE_PROJECTIONS['A1'])
        self.assertIn('cautions 표시 유무와 무관', r.SYSTEM_PROMPT)
        u_with_141_caution = copy.deepcopy(self.unit)
        u_with_141_caution['resolved_cautions'].append({'sentence_indices': [141], 'guidance': '오타 키워드 맞춤법 교정 금지'})
        payload = r.project_unit_payload(u_with_141_caution, [u_with_141_caution], {'roots': [{'title': '태그'}]}, self.amends)
        s134 = next(x for x in payload['assigned_sentences'] if x['index'] == 134)
        s141 = next(x for x in payload['assigned_sentences'] if x['index'] == 141)
        self.assertIn('verified_visual_notes', s134)
        self.assertNotIn('cautions', s134)
        self.assertIn('cautions', s141)

    def test_native_korean_numeral_equivalence_and_intrinsic_stt_cautions(self):
        # Verify '1일' in key_anchors matches '하루' in text without missing_numeric_anchor
        self.assertFalse(r._check_anchor('1일', '화자는 하루 만에 미용실을 차릴 수 없다고 설명한다.'))
        self.assertFalse(r._check_anchor('1명', '실력과 평판이 없으면 원생이 한 명도 오지 않는다.'))
        # Verify multi-digit statistics still require exact digits
        with self.assertRaises(ValueError):
            r._check_anchor('45.7%', '통신판매업의 생존률은 사십오 점 칠 퍼센트이다.')

        # Verify intrinsic STT cautions detect interleaved clause restart and single-syllable variant
        u_a = {
            'unit_id': 'c_u00',
            'video_id': 'H2WN130OzLg',
            'section_hierarchy': {'unit_position': '1/2', 'root_title': '진입 장벽', 'leaf_title': '포터 이론'},
            'required_source_sentences': [
                {'sentence_index': 140, 'text': '담장이라고 보면 이익이 빠른데 담장이 났는데 이익은 높아.'},
                {'sentence_index': 166, 'text': '통신판매업이 사십오 점 칠 퍼센트야.'},
                {'sentence_index': 170, 'text': '상위 미용실과 하위 통신판매업 차이가 이십칠 점 칠 포인트다.'},
            ],
            'resolved_cautions': [],
            'internal_source_links': [],
            'previous_context': {'sentences': [], 'links_from_current_unit': []},
        }
        u_b = {
            'unit_id': 'c_u06',
            'video_id': 'H2WN130OzLg',
            'section_hierarchy': {'unit_position': '2/2', 'root_title': '진입 장벽', 'leaf_title': '유통 기한'},
            'required_source_sentences': [
                {'sentence_index': 188, 'text': '통신판매분 어때요?'},
            ],
            'resolved_cautions': [],
            'internal_source_links': [],
            'previous_context': {'sentences': [], 'links_from_current_unit': []},
        }
        c_a = r._detect_intrinsic_stt_cautions(u_a, [u_a, u_b])
        c_b = r._detect_intrinsic_stt_cautions(u_b, [u_a, u_b])
        self.assertIn(140, c_a)
        self.assertIn(188, c_b)

    def test_archive_attempt1_preserves_full_ledger_and_t5_export(self):
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
            r.b.write(run / 'config.json', {'policy': r.POLICY_ID})
            r.b.write(run / 'source_amendments.json', self.amends)
            r.b.write(run / 'confirmation_freeze.json', {'primary_review_sha256': '2' * 64})

            # Simulate attempt 1 archived via _archive_attempt1 and attempt 2 active
            r.b.write(run / 'journal/p_u10.json', {'job_id': 'p_u10', 'status': 'failed', 'http_seconds': 21.0, 'response_sha256': 'a' * 64, 'usage': {'prompt_tokens': 2000, 'completion_tokens': 800, 'total_tokens': 2800}})
            r.b.write(run / 'requests/p_u10.json', {'attempt': 1})
            (run / 'responses').mkdir(parents=True, exist_ok=True)
            (run / 'responses/p_u10.body').write_bytes(b'attempt1')
            r._archive_attempt1(run, 'p_u10', 'repaired_validation_failure', 'auto_validator_repair', "near_miss_unverified_quote:'뚫어빵'")
            # Verify max 1 repair per unit enforced
            r.b.write(run / 'journal/p_u10.json', {'job_id': 'p_u10', 'attempt': 2, 'status': 'success', 'http_seconds': 22.0, 'response_sha256': 'b' * 64, 'usage': {'prompt_tokens': 2150, 'completion_tokens': 850, 'total_tokens': 3000}})
            with self.assertRaises(ValueError):
                r._archive_attempt1(run, 'p_u10', 'repaired_validation_failure', 'auto_validator_repair', 'second_repair')

            r.b.write(run / 'journal/c_u00.json', {'job_id': 'c_u00', 'attempt': 1, 'status': 'success', 'http_seconds': 15.0, 'response_sha256': 'c' * 64, 'usage': {'prompt_tokens': 1400, 'completion_tokens': 500, 'total_tokens': 1900}})
            own = r.b.old.records(run)
            self.assertEqual(len(own), 3)
            self.assertEqual(r._repair_counts(own), (1, 1, 0))

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
                'document_outline': {'roots': [{'title': '태그'}]},
                'generation_units': [u_p],
            }
            t5 = r.build_t5_input(run, d, {'policy': r.POLICY_ID}, jobs, own, own)
            self.assertEqual(t5['selected_t4_candidate'], 'E05')
            self.assertEqual(t5['selected_units'][0]['response_sha256'], 'b' * 64)
            self.assertEqual(t5['selected_units'][0]['attempt'], 2)
            self.assertEqual(t5['ledger_and_cost']['candidate_calls'], 3)
            self.assertEqual(t5['ledger_and_cost']['auto_validator_repairs'], 1)
            self.assertFalse(t5['user_accepted'])
            self.assertFalse(t5['final_article_user_accepted'])


if __name__ == '__main__':
    unittest.main()
