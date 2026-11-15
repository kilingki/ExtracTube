import copy
import json
import unittest
from unittest.mock import patch

import jsonschema

from run import identity, metrics, validate_selection
import e03


def _fake_figs(count: int) -> list[dict]:
    return [
        {
            'asset_id': f'vid:v0:pts{i}',
            'actual_video_seconds': f'{10 + i}.0',
            'caption': f'cap {i}',
            'image': {'path': f'frames/{i}.png', 'sha256': f'sha{i}'},
        }
        for i in range(count)
    ]


def _fake_paragraph(count: int, source_indices=(10, 11)) -> dict:
    return {
        'paragraph_id': 'u0001-p000',
        'text': '본문 설명 텍스트',
        'source_indices': list(source_indices),
        'figures': _fake_figs(count),
    }


def _run_e03_with_mock(p: dict, scripted_responses: list[dict]) -> dict:
    """Run select_paragraph_e03 using scripted JSON model responses and a dummy counter."""
    sources = [{'text': f'src {i}'} for i in range(50)]
    entry = {'number': 1, 'video': 'vid', 'work': 'tests/revision/R1', 'run': 'tests/revision/R1'}
    it = iter(scripted_responses)

    class DummyCounter:
        identity = {'dummy': True}

    def fake_call(out, body, counter, **kwargs):
        resp = next(it)
        return {'choices': [{'message': {'content': json.dumps(resp)}, 'finish_reason': 'stop'}]}, f'call-{id(resp)}'

    with patch('e03.make_body_e03', return_value={'dummy': True}), patch('run.call', side_effect=fake_call):
        return e03.select_paragraph_e03(entry, '절 제목', p, None, None, sources, e03.ROOT / 'tmp_unused', DummyCounter())


class SelectionTests(unittest.TestCase):
    def test_later_complete_frame_can_replace_prior(self):
        rows = [
            dict(index=0, decision='omit', covered_by=1, reason='completed state'),
            dict(index=1, decision='keep', covered_by=None, reason='complete'),
        ]
        self.assertEqual(validate_selection({'frames': rows}, 2), rows)

    def test_transitive_substitution_flattened_to_keep(self):
        rows = [
            dict(index=0, decision='omit', covered_by=1, reason='step 0'),
            dict(index=1, decision='omit', covered_by=2, reason='step 1'),
            dict(index=2, decision='keep', covered_by=None, reason='step 2'),
        ]
        res = validate_selection({'frames': rows}, 3)
        self.assertEqual(res[0]['covered_by'], 2)
        self.assertEqual(res[1]['covered_by'], 2)
        self.assertEqual(res[2]['decision'], 'keep')

    def test_cycles_uncertain_targets_and_missing_indices_rejected(self):
        cases = [
            [dict(index=0, decision='omit', covered_by=1, reason='a'), dict(index=1, decision='omit', covered_by=0, reason='b')],
            [dict(index=0, decision='omit', covered_by=1, reason='a'), dict(index=1, decision='uncertain', covered_by=None, reason='b')],
            [dict(index=0, decision='keep', covered_by=None, reason='a'), dict(index=0, decision='keep', covered_by=None, reason='b')],
        ]
        for rows in cases:
            with self.assertRaises(ValueError):
                validate_selection({'frames': rows}, 2)

    def test_body_identity_excludes_only_visual_selection(self):
        a = {
            'sections': [
                {
                    'title': 'A',
                    'paragraphs': [
                        {'text': 'keep facts', 'source_indices': [1], 'paragraph_id': 'p', 'figures': [{'asset_id': 'a'}, {'asset_id': 'b'}]}
                    ],
                }
            ],
            'asset_placements': [],
        }
        b = copy.deepcopy(a)
        b['sections'][0]['paragraphs'][0]['figures'].pop()
        self.assertEqual(identity(a), identity(b))
        b['sections'][0]['paragraphs'][0]['text'] = 'altered'
        self.assertNotEqual(identity(a), identity(b))
        self.assertEqual(metrics(a)['zero_text_intervals'], 1)


class E03ObligationTests(unittest.TestCase):
    def _fact(self, text='수식 B 정의', src=(10,), nec='required', pres=True):
        return {
            'visual_fact': text,
            'source_indices': list(src),
            'necessity': nec,
            'preserve_obligation': pres,
            'reason': '설명 필수',
        }

    def test_a_to_b_to_c_rejected_when_c_misses_inherited_obligation(self):
        # 5 frames -> Pass 0 Batch 0: [1,2,3,4], Batch 1: [0, carry=1], Pass 1: [0 or 1...]
        # Construct 5 frames where Pass 0 reduces [1,2,3,4] -> keeps 1 (covering 2), keeps 4;
        # wait: easier with 5 frames: [1,2,3,4] -> 2,3,4 replaced onto 1? Reverse: let 1 keep, 2->1 (replace), 3->1, 4->1;
        # Batch 1: [0, 1] -> keep 0, keep 1 (no replacement); Pass 1: [0, 1] -> propose 1 replace-> 0, missing 2's inherited fact!
        p = _fake_paragraph(5)
        batch0 = {
            'paragraph_purpose': '수식 전개 설명',
            'frames': [
                {'index': 0, 'observed_facts': [self._fact('frame 1 own')], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'rep', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 1, 'observed_facts': [self._fact('frame 2 inherited')], 'decision': 'replace', 'replace_target': 0, 'decision_reason': 'covered by 1', 'coverage_checks': [{'fact_ref': 'own:0', 'status': 'covered', 'target_index': 0, 'visual_evidence': 'shown in 1'}], 'fact_conflicts': []},
                {'index': 2, 'observed_facts': [self._fact('irrel', nec='irrelevant', pres=False)], 'decision': 'omit_irrelevant', 'replace_target': None, 'decision_reason': 'irrel', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 3, 'observed_facts': [self._fact('irrel2', nec='irrelevant', pres=False)], 'decision': 'omit_irrelevant', 'replace_target': None, 'decision_reason': 'irrel', 'coverage_checks': [], 'fact_conflicts': []},
            ],
        }
        batch1 = {
            'paragraph_purpose': '수식 전개 설명',
            'frames': [
                {'index': 0, 'observed_facts': [self._fact('frame 0 final')], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'keep 0', 'coverage_checks': [], 'fact_conflicts': []},
                # Carried frame 1 stays keep in Pass 0
                {'index': 1, 'observed_facts': [], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'keep 1', 'coverage_checks': [], 'fact_conflicts': []},
            ],
        }
        # Pass 1 pool=[0, 1]: frame 1 proposes replace->0, checks own fact vid:v0:pts1#f1 as covered,
        # but marks inherited vid:v0:pts2#f1 as not_covered!
        pass1 = {
            'paragraph_purpose': '수식 전개 설명',
            'frames': [
                {'index': 0, 'observed_facts': [], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'keep 0', 'coverage_checks': [], 'fact_conflicts': []},
                {
                    'index': 1,
                    'observed_facts': [],
                    'decision': 'replace',
                    'replace_target': 0,
                    'decision_reason': 'try replace onto 0',
                    'coverage_checks': [
                        {'fact_ref': 'vid:v0:pts1#f1', 'status': 'covered', 'target_index': 0, 'visual_evidence': 'visible in 0'},
                        {'fact_ref': 'vid:v0:pts2#f1', 'status': 'not_covered', 'target_index': 0, 'visual_evidence': 'missing in 0'},
                    ],
                    'fact_conflicts': [],
                },
            ],
        }
        res = _run_e03_with_mock(p, [batch0, batch1, pass1])
        self.assertEqual(res['kept'], ['vid:v0:pts0', 'vid:v0:pts1'])
        d1 = next(d for d in res['decisions'] if d['asset_id'] == 'vid:v0:pts1')
        self.assertTrue(d1['guard_rejected'])
        self.assertIn('obligation_not_covered:vid:v0:pts2#f1', d1['guard_reason'])
        self.assertEqual(d1['applied_decision'], 'keep')
        # Inherited obligation from pts2 must remain mapped to pts1.
        self.assertEqual(res['final_obligation_mapping']['vid:v0:pts2#f1']['kept_asset_id'], 'vid:v0:pts1')

    def test_a_to_b_to_c_allowed_when_c_covers_all_inherited_obligations(self):
        p = _fake_paragraph(5)
        batch0 = {
            'paragraph_purpose': '수식 전개 설명',
            'frames': [
                {'index': 0, 'observed_facts': [self._fact('frame 1 own')], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'rep', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 1, 'observed_facts': [self._fact('frame 2 inherited')], 'decision': 'replace', 'replace_target': 0, 'decision_reason': 'covered by 1', 'coverage_checks': [{'fact_ref': 'own:0', 'status': 'covered', 'target_index': 0, 'visual_evidence': 'shown in 1'}], 'fact_conflicts': []},
                {'index': 2, 'observed_facts': [self._fact('irrel', nec='irrelevant', pres=False)], 'decision': 'omit_irrelevant', 'replace_target': None, 'decision_reason': 'irrel', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 3, 'observed_facts': [self._fact('irrel2', nec='irrelevant', pres=False)], 'decision': 'omit_irrelevant', 'replace_target': None, 'decision_reason': 'irrel', 'coverage_checks': [], 'fact_conflicts': []},
            ],
        }
        batch1 = {
            'paragraph_purpose': '수식 전개 설명',
            'frames': [
                {'index': 0, 'observed_facts': [self._fact('frame 0 final')], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'keep 0', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 1, 'observed_facts': [], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'keep 1', 'coverage_checks': [], 'fact_conflicts': []},
            ],
        }
        pass1 = {
            'paragraph_purpose': '수식 전개 설명',
            'frames': [
                {'index': 0, 'observed_facts': [], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'keep 0', 'coverage_checks': [], 'fact_conflicts': []},
                {
                    'index': 1,
                    'observed_facts': [],
                    'decision': 'replace',
                    'replace_target': 0,
                    'decision_reason': '0 covers both',
                    'coverage_checks': [
                        {'fact_ref': 'vid:v0:pts1#f1', 'status': 'covered', 'target_index': 0, 'visual_evidence': 'visible in 0'},
                        {'fact_ref': 'vid:v0:pts2#f1', 'status': 'covered', 'target_index': 0, 'visual_evidence': 'also visible in 0'},
                    ],
                    'fact_conflicts': [],
                },
            ],
        }
        res = _run_e03_with_mock(p, [batch0, batch1, pass1])
        self.assertEqual(res['kept'], ['vid:v0:pts0'])
        d2 = next(d for d in res['decisions'] if d['asset_id'] == 'vid:v0:pts2')
        self.assertEqual(d2['final_covered_by'], 'vid:v0:pts0')
        self.assertEqual(res['final_obligation_mapping']['vid:v0:pts2#f1']['kept_asset_id'], 'vid:v0:pts0')
        self.assertEqual(res['final_obligation_mapping']['vid:v0:pts1#f1']['kept_asset_id'], 'vid:v0:pts0')

    def test_final_result_alone_cannot_replace_missing_intermediate_fact(self):
        p = _fake_paragraph(2)
        resp = {
            'paragraph_purpose': 'A=B+C의 B 노이즈와 C 보정량 도식',
            'frames': [
                {'index': 0, 'observed_facts': [self._fact('최종 고양이 A')], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'final cat', 'coverage_checks': [], 'fact_conflicts': []},
                {
                    'index': 1,
                    'observed_facts': [self._fact('노이즈 B와 잔차 C')],
                    'decision': 'replace',
                    'replace_target': 0,
                    'decision_reason': '최종 결과가 있으므로 대체 시도',
                    'coverage_checks': [{'fact_ref': 'own:0', 'status': 'uncertain', 'target_index': 0, 'visual_evidence': '최종 A만 보임'}],
                    'fact_conflicts': [],
                },
            ],
        }
        res = _run_e03_with_mock(p, [resp])
        self.assertEqual(res['kept'], ['vid:v0:pts0', 'vid:v0:pts1'])
        d1 = next(d for d in res['decisions'] if d['asset_id'] == 'vid:v0:pts1')
        self.assertTrue(d1['guard_rejected'])
        self.assertEqual(d1['applied_decision'], 'keep')

    def test_ledger_entries_cannot_be_deleted_or_downgraded_in_later_pass(self):
        p = _fake_paragraph(5)
        batch0 = {
            'paragraph_purpose': '수식 설명',
            'frames': [
                {'index': 0, 'observed_facts': [self._fact('필수 관계')], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'k', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 1, 'observed_facts': [self._fact('irrel', nec='irrelevant', pres=False)], 'decision': 'omit_irrelevant', 'replace_target': None, 'decision_reason': 'o', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 2, 'observed_facts': [self._fact('irrel2', nec='irrelevant', pres=False)], 'decision': 'omit_irrelevant', 'replace_target': None, 'decision_reason': 'o', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 3, 'observed_facts': [self._fact('irrel3', nec='irrelevant', pres=False)], 'decision': 'omit_irrelevant', 'replace_target': None, 'decision_reason': 'o', 'coverage_checks': [], 'fact_conflicts': []},
            ],
        }
        batch1 = {
            'paragraph_purpose': '수식 설명',
            'frames': [
                {'index': 0, 'observed_facts': [self._fact('다른 필수 관계')], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'k', 'coverage_checks': [], 'fact_conflicts': []},
                {'index': 1, 'observed_facts': [], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'k', 'coverage_checks': [], 'fact_conflicts': []},
            ],
        }
        # Pass 1 tries to re-observe frame 1 as irrelevant AND omit_irrelevant AND flag conflict.
        pass1 = {
            'paragraph_purpose': '수식 설명',
            'frames': [
                {'index': 0, 'observed_facts': [], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'k', 'coverage_checks': [], 'fact_conflicts': []},
                {
                    'index': 1,
                    'observed_facts': [self._fact('downgrade attempt', nec='irrelevant', pres=False)],
                    'decision': 'omit_irrelevant',
                    'replace_target': None,
                    'decision_reason': 'try omit',
                    'coverage_checks': [],
                    'fact_conflicts': [{'fact_id': 'vid:v0:pts1#f1', 'reason': 'disputed'}],
                },
            ],
        }
        res = _run_e03_with_mock(p, [batch0, batch1, pass1])
        self.assertEqual(res['kept'], ['vid:v0:pts0', 'vid:v0:pts1'])
        self.assertEqual(res['fact_ledger']['vid:v0:pts1#f1']['necessity'], 'required')
        self.assertTrue(res['fact_ledger']['vid:v0:pts1#f1']['is_obligation'])
        d1 = next(d for d in res['decisions'] if d['asset_id'] == 'vid:v0:pts1')
        self.assertTrue(d1['guard_rejected'])
        self.assertIn('has_obligations', d1['guard_reason'])
        types = {u['type'] for u in res['unresolved']}
        self.assertIn('ignored_reobserved_facts_on_carried', types)
        self.assertIn('fact_conflict', types)

    def test_single_irrelevant_image_omitted_with_null_target(self):
        p = _fake_paragraph(1)
        resp = {
            'paragraph_purpose': '매장 수 증가 설명',
            'frames': [
                {
                    'index': 0,
                    'observed_facts': [self._fact('무관한 배너', src=(), nec='irrelevant', pres=False)],
                    'decision': 'omit_irrelevant',
                    'replace_target': None,
                    'decision_reason': '본문 설명에 시각 기여 없음',
                    'coverage_checks': [],
                    'fact_conflicts': [],
                }
            ],
        }
        res = _run_e03_with_mock(p, [resp])
        self.assertEqual(res['kept'], [])
        self.assertEqual(res['decisions'][0]['applied_decision'], 'omit_irrelevant')
        self.assertIsNone(res['decisions'][0]['final_covered_by'])

    def test_single_image_with_required_or_uncertain_cannot_be_omitted_irrelevant(self):
        for nec in ('required', 'uncertain'):
            p = _fake_paragraph(1)
            resp = {
                'paragraph_purpose': '매장 수 증가 설명',
                'frames': [
                    {
                        'index': 0,
                        'observed_facts': [self._fact('핵심 수치', nec=nec, pres=True)],
                        'decision': 'omit_irrelevant',
                        'replace_target': None,
                        'decision_reason': '제외 시도',
                        'coverage_checks': [],
                        'fact_conflicts': [],
                    }
                ],
            }
            res = _run_e03_with_mock(p, [resp])
            self.assertEqual(res['kept'], ['vid:v0:pts0'])
            self.assertTrue(res['decisions'][0]['guard_rejected'])
            self.assertEqual(res['decisions'][0]['applied_decision'], 'keep')

    def test_unobserved_id_cycle_uncertain_target_and_incomplete_rejected(self):
        specs = [
            {'asset_id': 'a0', 'is_new': True, 'known_obligation_ids': set()},
            {'asset_id': 'a1', 'is_new': True, 'known_obligation_ids': set()},
        ]
        # Cycle
        with self.assertRaises(ValueError):
            e03.validate_e03_response(
                {
                    'paragraph_purpose': 'p',
                    'frames': [
                        {'index': 0, 'observed_facts': [self._fact()], 'decision': 'replace', 'replace_target': 1, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                        {'index': 1, 'observed_facts': [self._fact()], 'decision': 'replace', 'replace_target': 0, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                    ],
                },
                2,
                [10, 11],
                specs,
            )
        # Replace targeting uncertain
        with self.assertRaises(ValueError):
            e03.validate_e03_response(
                {
                    'paragraph_purpose': 'p',
                    'frames': [
                        {'index': 0, 'observed_facts': [self._fact()], 'decision': 'replace', 'replace_target': 1, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                        {'index': 1, 'observed_facts': [self._fact()], 'decision': 'uncertain', 'replace_target': None, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                    ],
                },
                2,
                [10, 11],
                specs,
            )
        # New frame with empty observed_facts
        with self.assertRaises(ValueError):
            e03.validate_e03_response(
                {
                    'paragraph_purpose': 'p',
                    'frames': [
                        {'index': 0, 'observed_facts': [], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                        {'index': 1, 'observed_facts': [self._fact()], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                    ],
                },
                2,
                [10, 11],
                specs,
            )
        # Out-of-range source index rejected by schema
        with self.assertRaises(jsonschema.ValidationError):
            e03.validate_e03_response(
                {
                    'paragraph_purpose': 'p',
                    'frames': [
                        {'index': 0, 'observed_facts': [self._fact(src=(999,))], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                        {'index': 1, 'observed_facts': [self._fact()], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                    ],
                },
                2,
                [10, 11],
                specs,
            )
        # Unobserved own:5 ref when only 1 observed_fact
        with self.assertRaises(ValueError):
            e03.validate_e03_response(
                {
                    'paragraph_purpose': 'p',
                    'frames': [
                        {'index': 0, 'observed_facts': [self._fact()], 'decision': 'replace', 'replace_target': 1, 'decision_reason': 'r', 'coverage_checks': [{'fact_ref': 'own:5', 'status': 'covered', 'target_index': 1, 'visual_evidence': 'e'}], 'fact_conflicts': []},
                        {'index': 1, 'observed_facts': [self._fact()], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'r', 'coverage_checks': [], 'fact_conflicts': []},
                    ],
                },
                2,
                [10, 11],
                specs,
            )

    def test_same_batch_chain_never_blindly_flattens_unchecked_target(self):
        # 0 -> 1, 1 -> 2(keep). Since 1->2 succeeds, 1 is not kept, so 0->1 must be guard-rejected (not flattened to 2).
        p = _fake_paragraph(3)
        resp = {
            'paragraph_purpose': '연쇄 대체 안전성',
            'frames': [
                {'index': 0, 'observed_facts': [self._fact('f0')], 'decision': 'replace', 'replace_target': 1, 'decision_reason': '0->1', 'coverage_checks': [{'fact_ref': 'own:0', 'status': 'covered', 'target_index': 1, 'visual_evidence': 'in 1'}], 'fact_conflicts': []},
                {'index': 1, 'observed_facts': [self._fact('f1')], 'decision': 'replace', 'replace_target': 2, 'decision_reason': '1->2', 'coverage_checks': [{'fact_ref': 'own:0', 'status': 'covered', 'target_index': 2, 'visual_evidence': 'in 2'}], 'fact_conflicts': []},
                {'index': 2, 'observed_facts': [self._fact('f2')], 'decision': 'keep', 'replace_target': None, 'decision_reason': 'keep 2', 'coverage_checks': [], 'fact_conflicts': []},
            ],
        }
        res = _run_e03_with_mock(p, [resp])
        self.assertEqual(res['kept'], ['vid:v0:pts0', 'vid:v0:pts2'])
        d0 = next(d for d in res['decisions'] if d['asset_id'] == 'vid:v0:pts0')
        self.assertTrue(d0['guard_rejected'])
        self.assertIn('target_not_kept_in_batch', d0['guard_reason'])


if __name__ == '__main__':
    unittest.main()
