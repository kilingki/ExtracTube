"""Offline regression tests for R1 E04 separated A -> B -> C selection and deterministic guards."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import jsonschema

import e04
import run as r1_run


def _obs(
    label: str = '수식',
    value: str = 'A = B + C',
    *,
    layer: str = 'content',
    readability: str = 'readable',
    kind: str = 'formula',
    unit: str | None = None,
    loc: str = '중앙',
    detail: str = '관찰 상세',
) -> dict:
    return {
        'layer': layer,
        'readability': readability,
        'kind': kind,
        'subject_or_label': label,
        'value_or_state': value,
        'unit_or_condition': unit,
        'location_cue': loc,
        'detail': detail,
    }


def _img_obs(idx: int, obs_list: list[dict], *, complete: bool = True, readability: str = 'readable', scene_type: str = 'formula_diagram') -> dict:
    return {
        'index': idx,
        'complete': complete,
        'incomplete_reason': None if complete else 'truncated',
        'readability': readability,
        'scene_type': scene_type,
        'screen_title': None,
        'bottom_subtitle_text': None,
        'observations': obs_list,
    }


def _c_img_obs(idx: int, obs_list: list[dict], *, complete: bool = True, readability: str = 'readable', scene_type: str = 'formula_diagram') -> dict:
    rec = _img_obs(idx, obs_list, complete=complete, readability=readability, scene_type=scene_type)
    rec['image_index'] = rec.pop('index')
    return rec


def _fake_figs(count: int, tmp_dir: Path) -> list[dict]:
    figs = []
    png_bytes = b'\x89PNG\r\n\x1a\nfake'
    import hashlib

    digest = hashlib.sha256(png_bytes).hexdigest()
    for i in range(count):
        fp = tmp_dir / f'f{i}.png'
        fp.write_bytes(png_bytes)
        figs.append({
            'asset_id': f'vid:v0:pts{i}',
            'actual_video_seconds': f'{10 + i}.0',
            'caption': f'SECRET_CAPTION_{i}',
            'image': {'path': f'f{i}.png', 'sha256': digest},
        })
    return figs


class E04IsolationAndValidationTests(unittest.TestCase):
    def test_no_leakage_across_stage_a_b_c_requests(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            figs = _fake_figs(2, tmp)
            entry = {'number': 1, 'video': 'vid', 'work': str(tmp), 'run': str(tmp)}
            p = {
                'paragraph_id': 'u0001-p000',
                'text': 'SECRET_ARTICLE_BODY_TEXT',
                'source_indices': [0],
                'figures': figs,
            }
            sources = [{'text': 'SECRET_SOURCE_TRANSCRIPT'}]
            prev_p = {'paragraph_id': 'u0000-p000', 'text': 'SECRET_PREV_PARAGRAPH'}
            next_p = {'paragraph_id': 'u0002-p000', 'text': 'SECRET_NEXT_PARAGRAPH'}

            # Stage A: must contain images and ONLY neutral image index, NO text/caption/asset_id.
            body_a = e04.make_body_a(entry, figs)
            a_texts = ' '.join(item['text'] for item in body_a['messages'][0]['content'] if item['type'] == 'text')
            a_imgs = [item for item in body_a['messages'][0]['content'] if item['type'] == 'image_url']
            self.assertEqual(len(a_imgs), 2)
            for secret in ('SECRET_ARTICLE_BODY_TEXT', 'SECRET_SOURCE_TRANSCRIPT', 'SECRET_CAPTION_0', 'vid:v0:pts0', 'SECRET_PREV_PARAGRAPH'):
                self.assertNotIn(secret, a_texts)

            # Stage B: must contain 0 images.
            obs_by_aid = {
                figs[0]['asset_id']: {**_img_obs(0, [_obs()]), 'observations': [{'obs_id': f'{figs[0]["asset_id"]}#o1', **_obs()}]},
                figs[1]['asset_id']: {**_img_obs(1, [_obs()]), 'observations': [{'obs_id': f'{figs[1]["asset_id"]}#o1', **_obs()}]},
            }
            body_b = e04.make_body_b('SECRET_SECTION', p, prev_p, next_p, sources, obs_by_aid)
            b_imgs = [item for item in body_b['messages'][0]['content'] if item['type'] == 'image_url']
            self.assertEqual(len(b_imgs), 0)

            # Stage C: must contain NO source, NO article, NO Stage A obs, NO B purpose/decision/reason.
            b_frames = [
                {'index': 0, 'decision': 'replace', 'replace_target': 1},
                {'index': 1, 'decision': 'keep', 'replace_target': None},
            ]
            tasks = e04.plan_verify_tasks('vid', 'u0001-p000', figs, b_frames)
            body_c, specs = e04.make_body_c(entry, tasks)
            c_texts = ' '.join(item['text'] for item in body_c['messages'][0]['content'] if item['type'] == 'text')
            for secret in (
                'SECRET_ARTICLE_BODY_TEXT',
                'SECRET_SOURCE_TRANSCRIPT',
                'SECRET_CAPTION_0',
                'SECRET_SECTION',
                'vid:v0:pts0',
                '관찰 상세',
                'replace',
                'omit_irrelevant',
            ):
                self.assertNotIn(secret, c_texts)
            self.assertEqual(len(specs), 2)

    def test_stage_b_source_span_and_target_rules(self):
        figs = [{'asset_id': f'vid:v0:pts{i}'} for i in range(3)]
        obs_by_aid = {
            f['asset_id']: {
                'observations': [{'obs_id': f'{f["asset_id"]}#o1', **_obs()}],
            }
            for f in figs
        }
        sources = [{'text': '묶음 배송은 불가능으로 체크해주세요.'}, {'text': '무료 배송을 추천합니다.'}]

        # Valid exact span (whitespace-normalized) and direct keep target passes.
        good = {
            'paragraph_purpose': '배송 설정 안내',
            'requirements': [
                {
                    'req_index': 0,
                    'source_index': 0,
                    'source_quote': '묶음 배송은   불가능으로 체크',
                    'target_concept': '묶음배송 불가능',
                    'metric_or_unit': None,
                    'why_visual_needed': 'UI 위치 확인',
                }
            ],
            'frames': [
                {
                    'index': 0,
                    'decision': 'replace',
                    'replace_target': 1,
                    'relevance': 'redundant_covered',
                    'reason': '1에 포함',
                    'bindings': [{'req_index': 0, 'origin_obs_id': 'vid:v0:pts0#o1', 'target_obs_id': 'vid:v0:pts1#o1', 'match_claim': 'exact_match'}],
                },
                {
                    'index': 1,
                    'decision': 'keep',
                    'replace_target': None,
                    'relevance': 'essential',
                    'reason': '최종 화면',
                    'bindings': [{'req_index': 0, 'origin_obs_id': 'vid:v0:pts1#o1', 'target_obs_id': None, 'match_claim': 'direct_fulfill'}],
                },
                {
                    'index': 2,
                    'decision': 'omit_irrelevant',
                    'replace_target': None,
                    'relevance': 'irrelevant',
                    'reason': '무관',
                    'bindings': [],
                },
            ],
        }
        res = e04.validate_stage_b(good, figs, [0, 1], sources, obs_by_aid)
        self.assertEqual(len(res['frames']), 3)

        # False source span rejected.
        bad_span = json.loads(json.dumps(good))
        bad_span['requirements'][0]['source_quote'] = '없는 인용문'
        with self.assertRaisesRegex(ValueError, 'false_source_span'):
            e04.validate_stage_b(bad_span, figs, [0, 1], sources, obs_by_aid)

        # Replacement chain (0 -> 2 where 2 is replace -> 1) rejected.
        bad_chain = json.loads(json.dumps(good))
        bad_chain['frames'][2] = {
            'index': 2,
            'decision': 'replace',
            'replace_target': 1,
            'relevance': 'redundant_covered',
            'reason': 'chain',
            'bindings': [{'req_index': 0, 'origin_obs_id': 'vid:v0:pts2#o1', 'target_obs_id': 'vid:v0:pts1#o1', 'match_claim': 'exact_match'}],
        }
        bad_chain['frames'][0]['replace_target'] = 2
        bad_chain['frames'][0]['bindings'][0]['target_obs_id'] = 'vid:v0:pts2#o1'
        with self.assertRaisesRegex(ValueError, 'must be keep'):
            e04.validate_stage_b(bad_chain, figs, [0, 1], sources, obs_by_aid)

        # Unobserved / wrong-frame obs_id rejected.
        bad_obs = json.loads(json.dumps(good))
        bad_obs['frames'][0]['bindings'][0]['origin_obs_id'] = 'vid:v0:pts1#o1'
        with self.assertRaisesRegex(ValueError, 'not in frame 0'):
            e04.validate_stage_b(bad_obs, figs, [0, 1], sources, obs_by_aid)


class E04GuardTests(unittest.TestCase):
    def _run_reconcile(
        self,
        a_obs_pairs: list[tuple[str, list[dict]]],
        b_selection: dict,
        c_packs: dict[str, dict],
    ) -> dict:
        figs = [{'asset_id': aid} for aid, _ in a_obs_pairs]
        a_by_aid = {}
        for idx, (aid, obs_list) in enumerate(a_obs_pairs):
            a_by_aid[aid] = {
                'asset_id': aid,
                'complete': True,
                'incomplete_reason': None,
                'readability': 'readable',
                'scene_type': obs_list[0].get('_scene', 'formula_diagram'),
                'screen_title': None,
                'bottom_subtitle_text': None,
                'observations': [{'obs_id': f'{aid}#o{j + 1}', **{k: v for k, v in o.items() if k != '_scene'}} for j, o in enumerate(obs_list)],
            }
        return e04.reconcile_paragraph_e04(figs, a_by_aid, b_selection, c_packs)

    def test_ui_polarity_conflict_rejected_even_if_model_claims_same(self):
        a0 = _obs('묶음배송', '가능 선택', kind='ui_control', unit='라디오 버튼')
        a1 = _obs('묶음배송', '불가능 선택', kind='ui_control', unit='라디오 버튼')
        b_sel = {
            'paragraph_purpose': '묶음배송 설정',
            'requirements': [{'req_index': 0, 'source_index': 0, 'source_quote': '묶음배송', 'target_concept': '묶음배송', 'metric_or_unit': None, 'why_visual_needed': 'UI'}],
            'frames': [
                {'index': 0, 'decision': 'replace', 'replace_target': 1, 'relevance': 'redundant_covered', 'reason': 'same UI', 'bindings': [{'req_index': 0, 'origin_obs_id': 'a0#o1', 'target_obs_id': 'a1#o1', 'match_claim': 'exact_match'}]},
                {'index': 1, 'decision': 'keep', 'replace_target': None, 'relevance': 'essential', 'reason': 'final', 'bindings': [{'req_index': 0, 'origin_obs_id': 'a1#o1', 'target_obs_id': None, 'match_claim': 'direct_fulfill'}]},
            ],
        }
        c_packs = {
            'a0': {
                'slot_roles': ['original', 'target'],
                'original_observation': _c_img_obs(0, [a0]),
                'target_observation': _c_img_obs(1, [a1]),
                'task_result': {
                    'overall_uncertainty': False,
                    # Even if C wrongly claims 'same'/'identical' while reporting 가능 vs 불가능, deterministic code must reject!
                    'comparison_items': [{'aspect': 'ui_control', 'subject_or_label': '묶음배송', 'first_value': '가능 선택', 'second_value': '불가능 선택', 'relation': 'same', 'containment': 'identical', 'note': 'wrongly equated'}],
                },
            },
            'a1': {
                'slot_roles': ['original'],
                'original_observation': _c_img_obs(0, [a1]),
                'target_observation': None,
                'task_result': {'overall_uncertainty': False, 'comparison_items': []},
            },
        }
        res = self._run_reconcile([('a0', [a0]), ('a1', [a1])], b_sel, c_packs)
        self.assertEqual(res['kept'], ['a0', 'a1'])
        self.assertTrue(res['decisions'][0]['guard_rejected'])
        self.assertIn('polarity_conflict', res['decisions'][0]['guard_reason'])

    def test_algebraic_equivalence_rejected_while_actual_subset_allowed(self):
        # a0: A - B = C (not in a2!), a1: A = B + C + z~N(0,I) (subset of a2!), a2: A = B + C + z~N(0,I) + (x-z)*1/16.
        o0 = _obs('수식', 'A - B = C', kind='formula')
        o1 = _obs('수식', 'A = B + C, z ~ N(0,I)', kind='formula')
        o2 = _obs('수식', 'A = B + C, z ~ N(0,I), (x - z) · 1/16', kind='formula')
        b_sel = {
            'paragraph_purpose': '수식 설명',
            'requirements': [{'req_index': 0, 'source_index': 0, 'source_quote': '수식', 'target_concept': '수식 관계', 'metric_or_unit': None, 'why_visual_needed': '식'}],
            'frames': [
                {'index': 0, 'decision': 'replace', 'replace_target': 2, 'relevance': 'redundant_covered', 'reason': '대수 동치 시도', 'bindings': [{'req_index': 0, 'origin_obs_id': 'a0#o1', 'target_obs_id': 'a2#o1', 'match_claim': 'exact_match'}]},
                {'index': 1, 'decision': 'replace', 'replace_target': 2, 'relevance': 'redundant_covered', 'reason': '부분집합 포함', 'bindings': [{'req_index': 0, 'origin_obs_id': 'a1#o1', 'target_obs_id': 'a2#o1', 'match_claim': 'superset_contains'}]},
                {'index': 2, 'decision': 'keep', 'replace_target': None, 'relevance': 'essential', 'reason': '완성 도식', 'bindings': [{'req_index': 0, 'origin_obs_id': 'a2#o1', 'target_obs_id': None, 'match_claim': 'direct_fulfill'}]},
            ],
        }
        c_packs = {
            'a0': {
                'slot_roles': ['original', 'target'],
                'original_observation': _c_img_obs(0, [o0]),
                'target_observation': _c_img_obs(1, [o2]),
                'task_result': {
                    'overall_uncertainty': False,
                    'comparison_items': [{'aspect': 'formula', 'subject_or_label': '수식', 'first_value': 'A - B = C', 'second_value': 'A = B + C, z ~ N(0,I), (x - z) · 1/16', 'relation': 'different', 'containment': 'incompatible_or_missing', 'note': 'sign/form differs'}],
                },
            },
            'a1': {
                'slot_roles': ['target', 'original'],
                'original_observation': _c_img_obs(1, [o1]),
                'target_observation': _c_img_obs(0, [o2]),
                'task_result': {
                    'overall_uncertainty': False,
                    'comparison_items': [{'aspect': 'formula', 'subject_or_label': '수식', 'first_value': 'A = B + C, z ~ N(0,I), (x - z) · 1/16', 'second_value': 'A = B + C, z ~ N(0,I)', 'relation': 'different', 'containment': 'second_in_first', 'note': 'target adds C'}],
                },
            },
            'a2': {
                'slot_roles': ['original'],
                'original_observation': _c_img_obs(0, [o2]),
                'target_observation': None,
                'task_result': {'overall_uncertainty': False, 'comparison_items': []},
            },
        }
        res = self._run_reconcile([('a0', [o0]), ('a1', [o1]), ('a2', [o2])], b_sel, c_packs)
        self.assertEqual(res['kept'], ['a0', 'a2'])
        self.assertTrue(res['decisions'][0]['guard_rejected'])
        self.assertFalse(res['decisions'][1]['guard_rejected'])
        self.assertEqual(res['decisions'][1]['final_covered_by'], 'a2')

    def test_single_irrelevant_omitted_and_unknown_or_ac_conflict_preserved(self):
        # Single irrelevant table (sales metric) omitted cleanly when A and C agree.
        o_irrel = _obs('연평균 매출 순위', '1위 투썸플레이스 5억 7100만 원', kind='table_metric', unit='억 원')
        b_irrel = {
            'paragraph_purpose': '매장 수 증가',
            'requirements': [],
            'frames': [{'index': 0, 'decision': 'omit_irrelevant', 'replace_target': None, 'relevance': 'irrelevant', 'reason': '지표 불일치(매출 억 원 vs 매장 수 개)', 'bindings': []}],
        }
        c_irrel = {
            'a0': {
                'slot_roles': ['original'],
                'original_observation': _c_img_obs(0, [o_irrel]),
                'target_observation': None,
                'task_result': {'overall_uncertainty': False, 'comparison_items': []},
            }
        }
        res1 = self._run_reconcile([('a0', [o_irrel])], b_irrel, c_irrel)
        self.assertEqual(res1['kept'], [])
        self.assertEqual(res1['decisions'][0]['applied_decision'], 'omit_irrelevant')

        # If Stage C flags overall_uncertainty=True or A/C disagree on polarity, omit_irrelevant is rejected!
        c_uncertain = {
            'a0': {
                'slot_roles': ['original'],
                'original_observation': _c_img_obs(0, [o_irrel]),
                'target_observation': None,
                'task_result': {'overall_uncertainty': True, 'comparison_items': []},
            }
        }
        res2 = self._run_reconcile([('a0', [o_irrel])], b_irrel, c_uncertain)
        self.assertEqual(res2['kept'], ['a0'])
        self.assertTrue(res2['decisions'][0]['guard_rejected'])
        self.assertTrue(res2['unresolved'])

    def test_seeded_slot_order_and_non_e04_reuse_blocked(self):
        figs = [{'asset_id': 'a0'}, {'asset_id': 'a1'}]
        b_frames = [{'index': 0, 'decision': 'replace', 'replace_target': 1}, {'index': 1, 'decision': 'keep', 'replace_target': None}]
        t1 = e04.plan_verify_tasks('vidX', 'u0001-p000', figs, b_frames)
        t2 = e04.plan_verify_tasks('vidX', 'u0001-p000', figs, b_frames)
        self.assertEqual(t1, t2)
        with tempfile.TemporaryDirectory() as td:
            bad_dir = Path(td)
            (bad_dir / 'manifest.json').write_text(json.dumps({'experiment': 'R1 E03'}), encoding='utf-8')
            with self.assertRaisesRegex(AssertionError, 'refusing non-E04 cache reuse'):
                e04._assert_reuse_dirs_are_e04((bad_dir,))

    def test_budget_exhaustion_aborts_without_retry(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / 'calls' / 'c1').mkdir(parents=True)
            (out / 'calls' / 'c1' / 'record.json').write_text(json.dumps({'status': 'complete', 'seconds': 10.0}), encoding='utf-8')

            class DummyCounter:
                identity = {'dummy': True}
                def count(self, body):
                    return {'tokens': 100}

            with self.assertRaisesRegex(RuntimeError, 'campaign budget exhausted'):
                r1_run.call(out, {'model': 'x'}, DummyCounter(), cap_calls=1, cap_http_seconds=100)

    def test_formula_extraction_avoids_matrix_bleed_and_preserves_real_conflicts(self):
        # Matrix decimal list before ∈ R^{d_k} must not attach "90" or fail against "∈ R^d_k".
        a_vec = '0.49, -0.84, 0.38, -1.25, 0.93, -0.90 ∈ R^{d_k}'
        c_vec = "행렬 우측에 '∈ R^d_k' 기호가 표시됨."
        self.assertIsNone(e04.has_formula_or_fraction_conflict(a_vec, c_vec))
        self.assertIsNone(e04.has_formula_or_fraction_conflict(c_vec, a_vec))
        # Different dimension space must still conflict.
        self.assertIsNotNone(e04.has_formula_or_fraction_conflict(a_vec, "∈ R^1"))

        # Adjacent equations in separated observations must not cross-bleed into "0/16A", while · vs = still conflicts.
        eq_ok = 'x · 16/16 + z · 0/16 | A = B + C'
        eq_bad_op = 'x = 16/16 + z · 0/16 | A = B + C'
        self.assertIsNone(e04.has_formula_or_fraction_conflict(eq_ok, eq_ok))
        self.assertIsNotNone(e04.has_formula_or_fraction_conflict(eq_bad_op, eq_ok))
        self.assertIsNotNone(e04.has_formula_or_fraction_conflict('A - B = C', 'A = B + C'))


if __name__ == '__main__':
    unittest.main()
