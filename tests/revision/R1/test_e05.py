"""Offline verification tests for R1 E05 requirement-witness minimum-set selection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import e05
import run as r1_run


def _fake_figs(count: int, tmp_dir: Path) -> list[dict]:
    figs = []
    png_bytes = b'\x89PNG\r\n\x1a\nfake'
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


def _pred(
    ptype: str,
    *,
    label: str | None = None,
    value: str | None = None,
    mode: str | None = None,
    items: list[str] | None = None,
    roles: list[dict] | None = None,
    links: list[dict] | None = None,
) -> dict:
    return {
        'predicate_type': ptype,
        'target_label': label,
        'expected_value': value,
        'unit_or_check_mode': mode,
        'expected_items': items or [],
        'expected_roles': roles or [],
        'expected_links': links or [],
    }


def _reading(
    *,
    label: str | None = None,
    value: str | None = None,
    unit: str | None = None,
    items: list[str] | None = None,
    roles: list[dict] | None = None,
    links: list[dict] | None = None,
) -> dict:
    return {
        'label_or_subject': label,
        'value_or_state': value,
        'unit_or_role': unit,
        'items_or_expressions': items or [],
        'diagram_roles': roles or [],
        'diagram_links': links or [],
    }


def _img_obs(
    idx: int,
    answers: list[dict],
    *,
    complete: bool = True,
    scene: str = 'ui',
    suspicions: list[dict] | None = None,
    with_suspicions: bool = True,
) -> dict:
    doc = {
        'image_index': idx,
        'complete': complete,
        'incomplete_reason': None if complete else 'truncated',
        'scene_type': scene,
        'screen_title': '테스트 화면',
        'topic_or_subject': '테스트 주제',
        'metric_or_unit': None,
        'bottom_subtitle_text': None,
        'answers': answers,
    }
    if with_suspicions:
        doc['missing_suspicions'] = suspicions or []
    return doc


def _ans(qid: str, status: str, reading: dict | None, location: str | None = '중앙') -> dict:
    return {
        'question_id': qid,
        'status': status,
        'location': location if status != 'absent' else None,
        'reading': reading if status != 'absent' else None,
    }


class E05IsolationAndValidationTests(unittest.TestCase):
    def test_no_leakage_across_p_o_v_and_neutral_questions(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            figs = _fake_figs(2, tmp)
            entry = {'number': 1, 'video': 'vid', 'work': str(tmp), 'run': str(tmp)}
            p = {
                'paragraph_id': 'u0001-p000',
                'text': 'SECRET_BODY_TEXT',
                'source_indices': [0],
                'figures': figs,
            }
            sources = [{'text': '묶음 배송은 불가능으로 체크해주세요. SECRET_SOURCE_EXTRA'}]
            prev_p = {'paragraph_id': 'u0000-p000', 'text': 'SECRET_PREV'}
            next_p = {'paragraph_id': 'u0002-p000', 'text': 'SECRET_NEXT'}

            body_p = e05.make_body_p('SECRET_SECTION', p, prev_p, next_p, sources)
            p_imgs = [c for c in body_p['messages'][0]['content'] if c['type'] == 'image_url']
            self.assertEqual(len(p_imgs), 0)
            p_txt = body_p['messages'][0]['content'][0]['text']
            self.assertNotIn('SECRET_CAPTION_0', p_txt)
            self.assertNotIn('vid:v0:pts0', p_txt)

            p_doc = {
                'paragraph_purpose': 'SECRET_PURPOSE_묶음배송',
                'requirements': [{
                    'req_id': 'r1',
                    'question_id': 'q1',
                    'source_index': 0,
                    'source_quote': '묶음 배송은 불가능으로 체크',
                    'why_visual_needed': 'SECRET_WHY_VISUAL',
                    'reading_type': 'ui_state',
                    'read_question': '묶음배송 필드의 현재 선택된 옵션은 무엇인가?',
                    'diagram_slots': [],
                    'expected_predicates': [
                        _pred('ui_state', label='묶음배송', value='불가능', mode='selected_state')
                    ],
                }],
                'temporal_constraints': [],
            }
            e05.validate_stage_p(p_doc, [0], sources)

            body_o = e05.make_body_o(entry, figs, p, sources, p_doc)
            o_txts = ' '.join(c['text'] for c in body_o['messages'][0]['content'] if c['type'] == 'text')
            o_imgs = [c for c in body_o['messages'][0]['content'] if c['type'] == 'image_url']
            self.assertEqual(len(o_imgs), 2)
            for secret in ('SECRET_BODY_TEXT', 'SECRET_CAPTION_0', 'vid:v0:pts0', 'SECRET_PREV'):
                self.assertNotIn(secret, o_txts)

            neutral_qs = e05.extract_neutral_questions_for_v(p_doc)
            body_v = e05.make_body_v(entry, figs, neutral_qs)
            v_txts = ' '.join(c['text'] for c in body_v['messages'][0]['content'] if c['type'] == 'text')
            for secret in (
                'SECRET_BODY_TEXT',
                'SECRET_SOURCE_EXTRA',
                'SECRET_CAPTION_0',
                'SECRET_SECTION',
                'SECRET_PURPOSE',
                'SECRET_WHY_VISUAL',
                'vid:v0:pts0',
                '불가능',
                'selected_state',
                'expected_predicates',
            ):
                self.assertNotIn(secret, v_txts)

    def test_p_validator_rejects_false_span_unsupported_and_answer_leakage(self):
        sources = [{'text': '묶음 배송은 불가능으로 체크하고, 수식 A - B = C와 매장 수 51,551개를 확인한다.'}]
        base = {
            'paragraph_purpose': '설정 및 수식 확인',
            'requirements': [{
                'req_id': 'r1',
                'question_id': 'q1',
                'source_index': 0,
                'source_quote': '묶음 배송은 불가능으로 체크',
                'why_visual_needed': 'UI 위치 확인',
                'reading_type': 'ui_state',
                'read_question': '묶음배송 필드의 선택된 옵션 텍스트를 읽어라.',
                'diagram_slots': [],
                'expected_predicates': [_pred('ui_state', label='묶음배송', value='불가능', mode='selected_state')],
            }],
            'temporal_constraints': [],
        }
        e05.validate_stage_p(base, [0], sources)

        bad_span = json.loads(json.dumps(base))
        bad_span['requirements'][0]['source_quote'] = '존재하지 않는 인용'
        with self.assertRaisesRegex(ValueError, 'false_source_span'):
            e05.validate_stage_p(bad_span, [0], sources)

        bad_unsup = json.loads(json.dumps(base))
        bad_unsup['requirements'][0]['reading_type'] = 'unsupported'
        with self.assertRaisesRegex(ValueError, 'unsupported_requirement'):
            e05.validate_stage_p(bad_unsup, [0], sources)

        bad_leak_val = json.loads(json.dumps(base))
        bad_leak_val['requirements'][0]['read_question'] = '묶음배송이 불가능인지 선택 상태를 확인하라.'
        with self.assertRaisesRegex(ValueError, 'answer_leakage'):
            e05.validate_stage_p(bad_leak_val, [0], sources)

        bad_leak_math = json.loads(json.dumps(base))
        bad_leak_math['requirements'][0].update({
            'source_quote': '수식 A - B = C',
            'reading_type': 'formula_notation',
            'read_question': '화면에 A - B = C 수식이 보이는지 읽어라.',
            'expected_predicates': [_pred('formula_notation', value='A - B = C', mode='equation')],
        })
        with self.assertRaisesRegex(ValueError, 'answer_leakage_formula'):
            e05.validate_stage_p(bad_leak_math, [0], sources)


class E05ComparatorAndSelectionTests(unittest.TestCase):
    def test_strict_comparators_block_polarity_unit_algebraic_and_partial_matches(self):
        # UI polarity conflict rejected even with whitespace normalization.
        ui_pred = _pred('ui_state', label='묶음배송', value='불가능', mode='selected_state')
        self.assertTrue(e05.match_predicate(ui_pred, _reading(label='묶음 배송', value='불가능 (선택됨)'))[0])
        self.assertFalse(e05.match_predicate(ui_pred, _reading(label='묶음배송', value='가능'))[0])

        # Formula syntax normalization allowed, algebraic equivalence & fraction change rejected.
        f_pred = _pred('formula_notation', value='A - B = C', mode='equation')
        self.assertTrue(e05.match_predicate(f_pred, _reading(items=['a - b = c']))[0])
        self.assertFalse(e05.match_predicate(f_pred, _reading(items=['A = B + C']))[0])

        frac_pred = _pred('formula_notation', value='(x - z) · 1/16', mode='expression')
        self.assertTrue(e05.match_predicate(frac_pred, _reading(items=[r'(x - z) \cdot \frac{1}{16}']))[0])
        self.assertFalse(e05.match_predicate(frac_pred, _reading(items=['16/16 + z · 0/16']))[0])

        dim_pred = _pred('formula_notation', value='R^{d_k}', mode='dimension')
        self.assertTrue(e05.match_predicate(dim_pred, _reading(items=['∈ ℝ^{d_k}']))[0])
        self.assertFalse(e05.match_predicate(dim_pred, _reading(items=['∈ R^{L × d_k}']))[0])

        # Metric/unit mismatch rejected (가맹점 수 vs 연평균 매출).
        t_pred = _pred('table_metric', label='프랜차이즈', value='가맹점 수', mode='개', items=['29101'])
        self.assertTrue(e05.match_predicate(t_pred, _reading(label='프랜차이즈', value='가맹점 수', unit='개', items=['29,101개']))[0])
        self.assertFalse(e05.match_predicate(t_pred, _reading(label='프랜차이즈', value='연평균 매출', unit='억 원', items=['29101']))[0])

        # Diagram transpose vs horizontal layout distinguished strictly.
        d_pred = _pred(
            'diagram_relation',
            roles=[{'slot': 's1', 'role_or_layout': 'row_or_horizontal'}, {'slot': 's2', 'role_or_layout': 'column_or_vertical_transpose'}],
            links=[{'source_slot': 's1', 'relation_type': 'multiplies_with', 'target_slot': 's2'}],
        )
        self.assertTrue(e05.match_predicate(d_pred, _reading(
            roles=[{'slot': 's1', 'role_or_layout': 'row_or_horizontal', 'color_mode': 'multi_color'},
                   {'slot': 's2', 'role_or_layout': 'column_or_vertical_transpose', 'color_mode': 'multi_color'}],
            links=[{'source_slot': 's1', 'relation_type': 'multiplies_with', 'target_slot': 's2'}],
        ))[0])
        self.assertFalse(e05.match_predicate(d_pred, _reading(
            roles=[{'slot': 's1', 'role_or_layout': 'row_or_horizontal', 'color_mode': 'multi_color'},
                   {'slot': 's2', 'role_or_layout': 'row_or_horizontal', 'color_mode': 'multi_color'}],
            links=[{'source_slot': 's1', 'relation_type': 'multiplies_with', 'target_slot': 's2'}],
        ))[0])

        # Uncertain status never satisfies a requirement, and partial predicates of 1 req must hold in same image.
        req = {
            'req_id': 'r1', 'question_id': 'q1', 'reading_type': 'ui_state',
            'expected_predicates': [
                _pred('ui_state', label='색상', value=None, mode='items_present', items=['네이비', '블랙', '민트']),
            ],
        }
        self.assertFalse(e05.match_requirement(req, _ans('q1', 'uncertain', _reading(label='색상', items=['네이비', '블랙', '민트'])))[0])
        self.assertFalse(e05.match_requirement(req, _ans('q1', 'present', _reading(label='색상', items=['네이비', '블랙'])))[0])

    def test_minimum_set_selection_tie_break_temporal_and_direct_references(self):
        figs = [
            {'asset_id': 'v:v0:pts0', 'actual_video_seconds': '1.0'},
            {'asset_id': 'v:v0:pts1', 'actual_video_seconds': '2.0'},
            {'asset_id': 'v:v0:pts2', 'actual_video_seconds': '3.0'},
            {'asset_id': 'v:v0:pts3', 'actual_video_seconds': '4.0'},
        ]
        p_doc = {
            'paragraph_purpose': '두 수식 설명',
            'requirements': [
                {
                    'req_id': 'r1', 'question_id': 'q1', 'source_index': 0, 'source_quote': 'a - b = c',
                    'why_visual_needed': '식1', 'reading_type': 'formula_notation',
                    'read_question': '수식1 읽기', 'diagram_slots': [],
                    'expected_predicates': [_pred('formula_notation', value='A - B = C', mode='equation')],
                },
                {
                    'req_id': 'r2', 'question_id': 'q2', 'source_index': 0, 'source_quote': 'a = b + c',
                    'why_visual_needed': '식2', 'reading_type': 'formula_notation',
                    'read_question': '수식2 읽기', 'diagram_slots': [],
                    'expected_predicates': [_pred('formula_notation', value='A = B + C', mode='equation')],
                },
            ],
            'temporal_constraints': [],
        }
        # pts0: r1 only; pts1: r1 & r2 (if E02 kept pts0+pts2, min cardinality 1 still beats E02!)
        # Wait: make pts1 and pts2 both satisfy r1+r2 so cardinality=1 ties, and E02 tie-break picks pts2!
        o_by_aid = {
            'v:v0:pts0': _img_obs(0, [_ans('q1', 'present', _reading(items=['A - B = C'])), _ans('q2', 'absent', None)]),
            'v:v0:pts1': _img_obs(1, [_ans('q1', 'present', _reading(items=['A - B = C'])), _ans('q2', 'present', _reading(items=['A = B + C']))]),
            'v:v0:pts2': _img_obs(2, [_ans('q1', 'present', _reading(items=['A - B = C'])), _ans('q2', 'present', _reading(items=['A = B + C']))]),
            'v:v0:pts3': _img_obs(3, [_ans('q1', 'absent', None), _ans('q2', 'absent', None)]),
        }
        sel = e05.select_minimum_set(figs, p_doc, o_by_aid, e02_kept_set={'v:v0:pts2'})
        self.assertEqual(sel['status'], 'selected')
        self.assertEqual(sel['kept'], ['v:v0:pts2'])
        decs = {d['asset_id']: d for d in sel['decisions']}
        self.assertEqual(decs['v:v0:pts0']['decision'], 'omit_covered')
        self.assertEqual(decs['v:v0:pts0']['final_witnesses'], ['v:v0:pts2'])
        self.assertEqual(decs['v:v0:pts3']['decision'], 'omit_irrelevant')
        self.assertEqual(decs['v:v0:pts3']['final_witnesses'], [])

        # With strict temporal constraint r1 before r2, single frame pts1 cannot satisfy strict earlier<later!
        p_temp = json.loads(json.dumps(p_doc))
        p_temp['temporal_constraints'] = [{'earlier_req_id': 'r1', 'later_req_id': 'r2', 'strict': True, 'reason': '순서'}]
        sel_t = e05.select_minimum_set(figs, p_temp, o_by_aid, e02_kept_set={'v:v0:pts2'})
        self.assertEqual(sel_t['kept'], ['v:v0:pts0', 'v:v0:pts2'])

    def test_empty_requirements_respects_missing_suspicions_and_incomplete_o(self):
        figs = [{'asset_id': 'v:v0:pts0', 'actual_video_seconds': '1.0'}]
        p_empty = {'paragraph_purpose': '단순 설명', 'requirements': [], 'temporal_constraints': []}
        o_clean = {'v:v0:pts0': _img_obs(0, [])}
        sel = e05.select_minimum_set(figs, p_empty, o_clean, set())
        self.assertEqual(sel['kept'], [])
        self.assertEqual(sel['decisions'][0]['decision'], 'omit_irrelevant')

        o_susp = {'v:v0:pts0': _img_obs(0, [], suspicions=[{
            'source_index': 0, 'source_quote': '핵심 표', 'missing_visual_fact': '지표 누락', 'why_essential': '필수'
        }])}
        sel_bad = e05.select_minimum_set(figs, p_empty, o_susp, set())
        self.assertEqual(sel_bad['status'], 'incomplete')
        self.assertEqual(sel_bad['error'], 'missing_requirement_suspicion')


class E05VerificationAndExecutionContractTests(unittest.TestCase):
    def test_v_verification_rejects_false_absence_and_ov_conflicts_without_reselection(self):
        figs = [
            {'asset_id': 'v:v0:pts0', 'actual_video_seconds': '1.0'},
            {'asset_id': 'v:v0:pts1', 'actual_video_seconds': '2.0'},
        ]
        p_doc = {
            'paragraph_purpose': '배송 설정',
            'requirements': [{
                'req_id': 'r1', 'question_id': 'q1', 'source_index': 0, 'source_quote': '묶음배송 불가능',
                'why_visual_needed': 'UI', 'reading_type': 'ui_state',
                'read_question': '묶음배송 선택 상태', 'diagram_slots': [],
                'expected_predicates': [_pred('ui_state', label='묶음배송', value='불가능', mode='selected_state')],
            }],
            'temporal_constraints': [],
        }
        # O claims pts0 is absent, pts1 is present -> selects pts1.
        o_by_aid = {
            'v:v0:pts0': _img_obs(0, [_ans('q1', 'absent', None)]),
            'v:v0:pts1': _img_obs(1, [_ans('q1', 'present', _reading(label='묶음배송', value='불가능'))]),
        }
        sel = e05.select_minimum_set(figs, p_doc, o_by_aid, set())
        self.assertEqual(sel['kept'], ['v:v0:pts1'])

        # V sees pts0 actually HAS 묶음배송=불가능! Must fail on false_absence_claim_by_o!
        v_false_abs = {
            'v:v0:pts0': _img_obs(0, [_ans('q1', 'present', _reading(label='묶음배송', value='불가능'))], with_suspicions=False),
            'v:v0:pts1': _img_obs(1, [_ans('q1', 'present', _reading(label='묶음배송', value='불가능'))], with_suspicions=False),
        }
        res_abs = e05.verify_selection_with_v(figs, p_doc, o_by_aid, sel, v_false_abs)
        self.assertEqual(res_abs['status'], 'incomplete')
        self.assertIn('false_absence_claim_by_o', res_abs['error'])

        # V sees kept pts1 actually has 가능 instead of 불가능 -> must fail witness_failed_in_v (never silently reselect)!
        v_bad_wit = {
            'v:v0:pts0': _img_obs(0, [_ans('q1', 'absent', None)], with_suspicions=False),
            'v:v0:pts1': _img_obs(1, [_ans('q1', 'present', _reading(label='묶음배송', value='가능'))], with_suspicions=False),
        }
        res_wit = e05.verify_selection_with_v(figs, p_doc, o_by_aid, sel, v_bad_wit)
        self.assertEqual(res_wit['status'], 'incomplete')
        self.assertIn('witness_failed_in_v', res_wit['error'])

    def test_seeded_order_non_e05_reuse_blocked_and_budget_aborts_immediately(self):
        figs = [{'asset_id': f'vid:v0:pts{i}', 'actual_video_seconds': str(i)} for i in range(6)]
        order1 = [f['asset_id'] for f in e05.seeded_verify_order('vid', 'u0001-p000', figs)]
        order2 = [f['asset_id'] for f in e05.seeded_verify_order('vid', 'u0001-p000', list(reversed(figs)))]
        self.assertEqual(order1, order2)
        self.assertEqual(sorted(order1), sorted(f['asset_id'] for f in figs))

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            e04_dir = tmp / 'e04_run'
            e04_dir.mkdir()
            (e04_dir / 'manifest.json').write_text(json.dumps({'experiment': 'R1 E04'}))
            with self.assertRaisesRegex(AssertionError, 'non-E05'):
                e05._assert_reuse_dirs_are_e05((e04_dir,))

            # Budget exhaustion aborts without making HTTP call.
            out_dir = tmp / 'e05_run'
            (out_dir / 'calls' / 'c1').mkdir(parents=True)
            (out_dir / 'calls' / 'c1' / 'record.json').write_text(json.dumps({'status': 'complete', 'seconds': 10.0}))

            class DummyCounter:
                identity = {'tokenizer': 'test'}

                def count(self, body):
                    return {'tokens': 100}

            with patch.object(r1_run.urllib.request, 'urlopen') as mock_open:
                with self.assertRaisesRegex(RuntimeError, 'campaign budget exhausted'):
                    e05._call_stage(out_dir, {'model': 'qwen3.8-27b'}, DummyCounter(), 'P', cap_calls=1, extra_run_dirs=())
                mock_open.assert_not_called()

    def test_verify_paragraph_e05_and_schema_validity(self):
        import jsonschema
        for n in (1, 2):
            jsonschema.Draft7Validator.check_schema(e05.schema_p([0, 1]))
            jsonschema.Draft7Validator.check_schema(e05.schema_o(n, ['q1', 'q2'], [0, 1]))
            jsonschema.Draft7Validator.check_schema(e05.schema_o(n, [], [0, 1]))
            jsonschema.Draft7Validator.check_schema(e05.schema_v(n, ['q1', 'q2']))
            jsonschema.Draft7Validator.check_schema(e05.schema_v(n, []))

        figs = [
            {'asset_id': 'v:v0:pts0', 'actual_video_seconds': '1.0'},
            {'asset_id': 'v:v0:pts1', 'actual_video_seconds': '2.0'},
        ]
        p_doc = {
            'paragraph_purpose': '설정 확인',
            'requirements': [{
                'req_id': 'r1', 'question_id': 'q1', 'source_index': 0, 'source_quote': '묶음배송 불가능',
                'why_visual_needed': 'UI', 'reading_type': 'ui_state',
                'read_question': '묶음배송 선택 상태', 'diagram_slots': [],
                'expected_predicates': [_pred('ui_state', label='묶음배송', value='불가능', mode='selected_state')],
            }],
            'temporal_constraints': [],
        }
        o_by_aid = {
            'v:v0:pts0': _img_obs(0, [_ans('q1', 'absent', None)]),
            'v:v0:pts1': _img_obs(1, [_ans('q1', 'present', _reading(label='묶음배송', value='불가능'))]),
        }
        v_by_aid = {
            'v:v0:pts0': _img_obs(0, [_ans('q1', 'absent', None)], with_suspicions=False),
            'v:v0:pts1': _img_obs(1, [_ans('q1', 'present', _reading(label='묶음배송', value='불가능'))], with_suspicions=False),
        }
        sel = e05.select_minimum_set(figs, p_doc, o_by_aid, set())
        ver = e05.verify_selection_with_v(figs, p_doc, o_by_aid, sel, v_by_aid)
        e05.verify_paragraph_e05(figs, p_doc, o_by_aid, v_by_aid, ver, set())
        hashes = e05.e05_code_hashes()
        self.assertEqual(len(hashes), 14)
        self.assertTrue(all(len(v) == 64 for v in hashes.values()))


class E05V2RegressionAndIsolationTests(unittest.TestCase):
    def test_v1_raw_response_still_strictly_rejected_and_v2_dedup_conflict_work(self):
        import jsonschema

        raw_p_path = (
            Path(__file__).resolve().parent
            / 'runs/20261114_e05_dev/calls/aae9d25433c9ab750dd5a3e8635d11ac165bf03f8d87e66ac8224a706748d34a/response.json'
        )
        raw_doc = json.loads(raw_p_path.read_text(encoding='utf-8'))
        content_json = json.loads(raw_doc['choices'][0]['message']['content'])
        ss_path = Path(__file__).resolve().parent / 'runs/20261114_0145_baseline/work/7EJ-xotXiSE/source_bundle.json'
        if ss_path.exists():
            sources = json.loads(ss_path.read_text(encoding='utf-8'))['sentences']
            allowed = list(range(99, 105))
        else:
            sources = [{'text': ''} for _ in range(105)]
            sources[99] = {'text': '예를 들어 컬러를 네이비 추가하고 베이지 추가하고 블랙을 넣었고 민트를 넣었습니다.'}
            sources[102] = {'text': '원하는 색깔을 추가하고 확인을 눌러주시면 됩니다.'}
            allowed = [99, 102]

        # V2 must NEVER relax validation to accept broken v1 selected_and_items with expected_value=None.
        with self.assertRaises(jsonschema.ValidationError):
            e05.validate_stage_p(json.loads(json.dumps(content_json)), allowed, sources)

        # Clean v2 plan with duplicate identical predicates in r1 should deterministically dedup, not fail.
        v2_plan = {
            'paragraph_purpose': '추가된 색상 옵션 목록 확인',
            'requirements': [{
                'req_id': 'r1',
                'question_id': 'q1',
                'source_index': 99,
                'source_quote': '네이비 추가하고 베이지 추가하고 블랙을 넣었고 민트',
                'why_visual_needed': 'UI 목록 확인',
                'reading_type': 'ui_state',
                'read_question': '추가된 색상 항목 이름들을 모두 읽어라.',
                'diagram_slots': [],
                'expected_predicates': [
                    _pred('ui_state', label='색상', value=None, mode='items_present', items=['네이비', '베이지', '블랙', '민트']),
                    _pred('ui_state', label='색상', value=None, mode='items_present', items=['네이비', '베이지', '블랙', '민트']),
                ],
            }],
            'temporal_constraints': [],
        }
        validated = e05.validate_stage_p(v2_plan, allowed, sources)
        self.assertEqual(len(validated['requirements'][0]['expected_predicates']), 1)

        # Contradictory expected_value on same label within one requirement must fail with p:conflicting_predicates.
        conflict_plan = json.loads(json.dumps(v2_plan))
        conflict_plan['requirements'][0]['expected_predicates'] = [
            _pred('ui_state', label='색상', value='네이비', mode='selected_state'),
            _pred('ui_state', label='색상', value='베이지', mode='selected_state'),
        ]
        with self.assertRaises(e05.FallbackParagraphError) as ctx:
            e05.validate_stage_p(conflict_plan, allowed, sources)
        self.assertEqual(ctx.exception.category, 'p:conflicting_predicates')

        # Unsupported reading_type validates against schema_p (model can report honestly) and triggers fallback category.
        unsup_plan = {
            'paragraph_purpose': '지원 불가 테스트',
            'requirements': [{
                'req_id': 'r1',
                'question_id': 'q1',
                'source_index': 99,
                'source_quote': sources[99]['text'][:10],
                'why_visual_needed': '판정 불가',
                'reading_type': 'unsupported',
                'read_question': '화면을 읽어라.',
                'diagram_slots': [],
                'expected_predicates': [_pred('unsupported')],
            }],
            'temporal_constraints': [],
        }
        jsonschema.validate(unsup_plan, e05.schema_p(allowed))
        with self.assertRaises(e05.FallbackParagraphError) as uctx:
            e05.validate_stage_p(unsup_plan, allowed, sources)
        self.assertEqual(uctx.exception.category, 'p:unsupported_requirement')

    def test_schema_p_mode_parity_across_all_8_variants(self):
        import jsonschema

        schema = e05.schema_p([0])
        sources = [{'text': '묶음배송 불가능 색상 네이비 베이지 수식 A = B 매장 10개 흐름도 예시1'}]

        def _wrap(rtype, pred, slots=None):
            return {
                'paragraph_purpose': '테스트',
                'requirements': [{
                    'req_id': 'r1', 'question_id': 'q1', 'source_index': 0,
                    'source_quote': '묶음배송', 'why_visual_needed': '이유',
                    'reading_type': rtype, 'read_question': '질문을 읽어라.',
                    'diagram_slots': slots or [], 'expected_predicates': [pred],
                }],
                'temporal_constraints': [],
            }

        valid_cases = [
            ('ui_state', _pred('ui_state', label='옵션', value='활성', mode='selected_state')),
            ('ui_state', _pred('ui_state', label='색상', value=None, mode='items_present', items=['A', 'B'])),
            ('ui_state', _pred('ui_state', label='배송', value='활성', mode='selected_and_items', items=['택배'])),
            ('formula_notation', _pred('formula_notation', label='식', value='A = B', mode='equation')),
            ('table_metric', _pred('table_metric', label='기업', value='매장 수', mode='개', items=['10'])),
            ('diagram_relation', _pred('diagram_relation', roles=[{'slot': 's1', 'role_or_layout': 'row_or_horizontal'}]), [{'slot': 's1', 'name': 'S1'}]),
            ('visual_example', _pred('visual_example', mode='items_present', items=['예시1'])),
            ('unsupported', _pred('unsupported')),
        ]
        for case in valid_cases:
            rtype, pred = case[0], case[1]
            slots = case[2] if len(case) > 2 else None
            doc = _wrap(rtype, pred, slots)
            jsonschema.validate(doc, schema)
            if rtype != 'unsupported':
                e05.validate_stage_p(doc, [0], sources)

        invalid_schema_cases = [
            ('ui_state', _pred('ui_state', label='색상', value='잘못된값', mode='items_present', items=['A'])),
            ('ui_state', _pred('ui_state', label='색상', value=None, mode='items_present', items=[])),
            ('ui_state', _pred('ui_state', label='옵션', value=None, mode='selected_state')),
            ('ui_state', _pred('ui_state', label='옵션', value='활성', mode='selected_state', items=['A'])),
            ('ui_state', _pred('ui_state', label='옵션', value=None, mode='selected_and_items', items=['A'])),
            ('visual_example', _pred('visual_example', value='있음', mode='items_present', items=['예시1'])),
        ]
        for rtype, pred in invalid_schema_cases:
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(_wrap(rtype, pred), schema)

    def test_diagram_multi_role_per_slot_not_overwritten_in_ov_check(self):
        p_doc = {
            'paragraph_purpose': '행렬 구조',
            'requirements': [{
                'req_id': 'r1',
                'question_id': 'q1',
                'source_index': 0,
                'source_quote': '행 방향 배치',
                'why_visual_needed': '배치 확인',
                'reading_type': 'diagram_relation',
                'read_question': '각 블록의 배치 방향과 색상 묶음을 읽어라.',
                'diagram_slots': [{'slot': 's1', 'description': 'K 행렬'}],
                'expected_predicates': [_pred(
                    'diagram_relation',
                    roles=[
                        {'slot': 's1', 'role_or_layout': 'row_or_horizontal'},
                        {'slot': 's1', 'role_or_layout': 'multi_color_grouped'},
                    ],
                )],
            }],
            'temporal_constraints': [],
        }
        r_good = _ans('q1', 'present', _reading(roles=[
            {'slot': 's1', 'role_or_layout': 'row_or_horizontal', 'color_mode': 'multi_color'},
            {'slot': 's1', 'role_or_layout': 'multi_color_grouped', 'color_mode': 'multi_color'},
        ]))
        self.assertIsNone(e05._check_ov_reading_conflict(p_doc['requirements'][0], r_good, r_good))

        r_bad_v = _ans('q1', 'present', _reading(roles=[
            {'slot': 's1', 'role_or_layout': 'row_or_horizontal', 'color_mode': 'multi_color'},
            {'slot': 's1', 'role_or_layout': 'single_color_uniform', 'color_mode': 'single_color'},
        ]))
        conflict = e05._check_ov_reading_conflict(p_doc['requirements'][0], r_good, r_bad_v)
        self.assertIsNotNone(conflict)
        self.assertIn('ov_diagram', conflict)

    def test_paragraph_fallback_skips_downstream_and_halts_on_3_consecutive_same_errors(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            figs = _fake_figs(3, tmp)
            entry = {'number': 1, 'video': 'vid1', 'work': str(tmp), 'run': str(tmp)}
            p = {'paragraph_id': 'u0001-p000', 'text': '본문', 'source_indices': [0], 'figures': figs}
            sources = [{'text': '묶음배송은 불가능입니다.'}]
            out_dir = tmp / 'run_out'

            class DummyCounter:
                identity = {'tokenizer': 'test'}

            # Stage P failure (unsupported) preserves all 3 originals and NEVER calls Stage O or V.
            p_resp = {
                'choices': [{'message': {'content': json.dumps({
                    'paragraph_purpose': '테스트',
                    'requirements': [{
                        'req_id': 'r1', 'question_id': 'q1', 'source_index': 0,
                        'source_quote': '묶음배송은 불가능', 'why_visual_needed': 'UI',
                        'reading_type': 'unsupported', 'read_question': '상태를 읽어라.',
                        'diagram_slots': [], 'expected_predicates': [_pred('unsupported')],
                    }],
                    'temporal_constraints': [],
                })}}]
            }
            calls_seen = []

            def fake_call(out, body, counter, stage, cap_calls, extra_run_dirs):
                calls_seen.append(stage)
                return p_resp, f'k_{stage}_{len(calls_seen)}', 'mock'

            with patch.object(e05, '_call_stage', side_effect=fake_call):
                res = e05.run_paragraph_e05(
                    entry, 'sec', p, None, None, sources, set(),
                    out_dir, DummyCounter(), e05.e05_code_hashes(), cap_calls=64, extra_run_dirs=(),
                )
            self.assertEqual(calls_seen, ['P'])
            self.assertEqual(res['status'], 'fallback_preserved')
            self.assertFalse(res['e05_selection_success'])
            self.assertTrue(res['fallback_used'])
            self.assertEqual(res['failure_stage'], 'P')
            self.assertEqual(res['error_category'], 'p:unsupported_requirement')
            self.assertEqual(res['kept'], [f['asset_id'] for f in figs])
            self.assertTrue((out_dir / 'failures' / 'vid1' / 'u0001-p000.json').exists())

            # Test infer(): 1st fallback + 2nd success continues; 3 consecutive same errors halt.
            out_inf = tmp / 'inf_run'
            out_inf.mkdir()
            (out_inf / 'policies').mkdir()
            (tmp / 'source_bundle.json').write_text(json.dumps({'sentences': sources}))
            art_paras = [
                {'paragraph_id': f'u000{i}-p000', 'text': f't{i}', 'source_indices': [0], 'figures': figs}
                for i in range(5)
            ]
            fake_art = {'sections': [{'title': 'S', 'paragraphs': art_paras}]}
            (out_inf / 'manifest.json').write_text(json.dumps({
                'experiment': 'R1 E05',
                'code_hashes': e05.e05_code_hashes(),
                'development_samples': {f'd{i}': {'number': 1, 'paragraph_id': f'u000{i}-p000'} for i in range(5)},
                'entries': [{'number': 1, 'video': 'vid1', 'run': str(tmp)}],
            }))

            outcomes = [
                e05._make_fallback_result(figs, 'P', 'p:invalid_plan', 'err1'),
                {'status': 'complete', 'outcome': 'e05_selected', 'e05_selection_success': True,
                 'fallback_used': False, 'kept': [figs[0]['asset_id']], 'decisions': []},
                e05._make_fallback_result(figs, 'P', 'p:invalid_plan', 'err2'),
                e05._make_fallback_result(figs, 'P', 'p:invalid_plan', 'err3'),
                e05._make_fallback_result(figs, 'P', 'p:invalid_plan', 'err4'),
            ]
            seen_pids = []

            def fake_run_p(e, sec, par, *args, **kwargs):
                seen_pids.append(par['paragraph_id'])
                return outcomes[len(seen_pids) - 1]

            class MockTS:
                identity = {'tokenizer': 'test'}

                def close(self):
                    pass

            with (
                patch.object(r1_run, 'TokenService', return_value=MockTS()),
                patch.object(r1_run, 'verify', return_value=fake_art),
                patch.object(e05, 'run_paragraph_e05', side_effect=fake_run_p),
            ):
                with self.assertRaisesRegex(RuntimeError, '3_consecutive_identical_errors:p:invalid_plan'):
                    e05.infer(out_inf, 'dev')
            # Stopped right after 5th paragraph (u0004-p000 was 3rd consecutive failure after 1 ok + 2,3,4 fail).
            self.assertEqual(seen_pids, ['u0000-p000', 'u0001-p000', 'u0002-p000', 'u0003-p000', 'u0004-p000'])
            self.assertTrue((out_inf / 'halt_dev.json').exists())
            fbs = json.loads((out_inf / 'fallbacks_dev.json').read_text(encoding='utf-8'))
            self.assertEqual(len(fbs), 4)


if __name__ == '__main__':
    unittest.main()
