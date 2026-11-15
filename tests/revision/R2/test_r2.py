import copy
import unittest

# Import after run.py is defined
from run import validate_explanation, explanation_schema, assemble_slice, metrics, merge_explanations

class ExplanationSchemaTests(unittest.TestCase):
    def test_valid_explanation_structure(self):
        sample = {
            'explanation_units': [
                {
                    'explanation_purpose': '색상 옵션 설정',
                    'source_indices': [99, 100],
                    'paragraphs': [
                        {'text': '옵션 메뉴에서 색상을 선택합니다.', 'asset_ids': ['aid-01']},
                        {'text': '원하는 색상을 추가 완료합니다.', 'asset_ids': ['aid-02']}
                    ]
                }
            ],
            'visual_evidence': [
                {'asset_id': 'aid-01', 'observation': '드롭다운 메뉴 열림', 'unit_index': 0},
                {'asset_id': 'aid-02', 'observation': '색상 목록 완료 화면', 'unit_index': 0}
            ],
            'selection_review_needed': [],
            'unresolved': []
        }
        res = validate_explanation(sample, {'aid-01', 'aid-02'})
        self.assertEqual(len(res['explanation_units']), 1)
        self.assertEqual(len(res['explanation_units'][0]['paragraphs']), 2)

    def test_duplicate_asset_placement_rejected(self):
        sample = {
            'explanation_units': [
                {
                    'explanation_purpose': '중복 테스트',
                    'source_indices': [1],
                    'paragraphs': [
                        {'text': '첫 번째 문단', 'asset_ids': ['aid-01']},
                        {'text': '두 번째 문단', 'asset_ids': ['aid-01']}
                    ]
                }
            ],
            'visual_evidence': [],
            'selection_review_needed': [],
            'unresolved': []
        }
        with self.assertRaises(ValueError):
            validate_explanation(sample, {'aid-01'})

    def test_unknown_asset_id_rejected(self):
        sample = {
            'explanation_units': [
                {
                    'explanation_purpose': '알 수 없는 자산 테스트',
                    'source_indices': [1],
                    'paragraphs': [
                        {'text': '문단', 'asset_ids': ['aid-999']}
                    ]
                }
            ],
            'visual_evidence': [],
            'selection_review_needed': [],
            'unresolved': []
        }
        with self.assertRaises(ValueError):
            validate_explanation(sample, {'aid-01'})

    def test_assemble_slice_replaces_paragraph_and_distributes_figures(self):
        article = {
            'sections': [
                {
                    'title': '섹션 1',
                    'root_index': 0,
                    'root_title': '루트 1',
                    'paragraphs': [
                        {
                            'paragraph_id': 'p0',
                            'text': '기존 단일 문단에 2개 그림이 몰려있음',
                            'source_indices': [1, 2],
                            'figures': [
                                {'asset_id': 'aid-01', 'caption': '그림 1'},
                                {'asset_id': 'aid-02', 'caption': '그림 2'}
                            ]
                        }
                    ]
                }
            ],
            'asset_placements': []
        }
        explanation = {
            'explanation_units': [
                {
                    'explanation_purpose': '그림 1 설명',
                    'source_indices': [1],
                    'paragraphs': [{'text': '그림 1에 대한 상세 설명', 'asset_ids': ['aid-01']}]
                },
                {
                    'explanation_purpose': '그림 2 설명',
                    'source_indices': [2],
                    'paragraphs': [{'text': '그림 2에 대한 상세 설명', 'asset_ids': ['aid-02']}]
                }
            ],
            'visual_evidence': [],
            'selection_review_needed': [],
            'unresolved': []
        }
        # Before metrics: 2 figures after 1 paragraph -> 1 zero-text interval between figure 1 and figure 2
        before_m = metrics(article)
        self.assertEqual(before_m['figures'], 2)
        self.assertEqual(before_m['zero_text_intervals'], 1)

        # Assemble
        new_art = assemble_slice(article, 'p0', explanation)
        ps = [p for s in new_art['sections'] for p in s['paragraphs']]
        self.assertEqual(len(ps), 2)
        self.assertEqual(len(ps[0]['figures']), 1)
        self.assertEqual(len(ps[1]['figures']), 1)
        self.assertEqual(ps[0]['figures'][0]['asset_id'], 'aid-01')
        self.assertEqual(ps[1]['figures'][0]['asset_id'], 'aid-02')

        # After metrics: Each paragraph has text followed by 1 figure -> 0 zero-text intervals!
        after_m = metrics(new_art)
        self.assertEqual(after_m['figures'], 2)
        self.assertEqual(after_m['zero_text_intervals'], 0)

class ProvenanceRegressionTests(unittest.TestCase):
    def part(self, aid='a', source=66):
        return {
            'explanation_units': [{'explanation_purpose': '설명', 'source_indices': [source],
                                   'paragraphs': [{'text': '본문', 'asset_ids': [aid]}]}],
            'visual_evidence': [{'asset_id': aid, 'observation': '관찰', 'unit_index': 0}],
            'selection_review_needed': [], 'unresolved': [],
        }

    def test_batch_local_source_number_cannot_replace_global_source_index(self):
        with self.assertRaisesRegex(ValueError, 'source_indices'):
            validate_explanation(self.part(source=0), {'a'}, {66, 67})

    def test_unplaced_image_is_rejected_before_assembly(self):
        with self.assertRaisesRegex(ValueError, 'Missing asset'):
            validate_explanation(self.part(), {'a', 'b'}, {66})
        article = {'sections': [{'paragraphs': [{'paragraph_id': 'p', 'source_indices': [66],
                    'figures': [{'asset_id': 'a'}, {'asset_id': 'b'}]}]}]}
        before = copy.deepcopy(article)
        with self.assertRaisesRegex(ValueError, 'Missing asset'):
            assemble_slice(article, 'p', self.part())
        self.assertEqual(article, before)

    def test_evidence_must_point_to_unit_that_uses_image(self):
        combined = merge_explanations([self.part('a'), self.part('b', 67)])
        combined['visual_evidence'][1]['unit_index'] = 0
        with self.assertRaisesRegex(ValueError, 'assigned unit'):
            validate_explanation(combined, {'a', 'b'}, {66, 67})

    def test_merge_rebases_evidence_without_mutating_raw_parts(self):
        parts = [self.part('a'), self.part('b', 67)]
        original = copy.deepcopy(parts)
        merged = merge_explanations(parts)
        self.assertEqual([r['unit_index'] for r in merged['visual_evidence']], [0, 1])
        validate_explanation(merged, {'a', 'b'}, {66, 67})
        self.assertEqual(parts, original)

    def test_review_and_unresolved_cannot_reference_unassigned_inputs(self):
        bad = self.part()
        bad['selection_review_needed'] = [{'asset_id': 'outside', 'reason': '사유'}]
        with self.assertRaisesRegex(ValueError, 'unknown asset'):
            validate_explanation(bad, {'a'}, {66})
        bad = self.part()
        bad['unresolved'] = [{'description': '확인 필요', 'source_indices': [0]}]
        with self.assertRaisesRegex(ValueError, 'unresolved source_indices'):
            validate_explanation(bad, {'a'}, {66})


if __name__ == '__main__':
    unittest.main()
