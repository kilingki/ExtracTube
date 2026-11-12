"""E05 CPU-only source-preserving T3 plan & dossier compiler; 0 model calls, no mutation of prior runs."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import copy
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import uuid

import run_t3 as t
import run_e04_plan as e04
from contracts import exact, parse_structure

HERE = t.HERE
ROOT = t.ROOT
E04_RUN = HERE / "runs/20261030T161144Z_e04_80623c"
E02_RUN = HERE / "runs/20261030T151221Z_e02_22f2fa"
T1_REVIEW = HERE.parent / "T1/runs/20261030T070419Z_95b43c03/source_review.json"
T1_REVIEW_SHA = "69f5f99f5ecadea0344fa1fca4956fc449c3e038c58b1ad864a153c256fc86ac"

SCHEMA_PATH = E04_RUN / "e05_source_plan_schema_draft.json"
SCHEMA_SHA = "e5ff22aab47d0e4b48e897de04323dc5c30e819b1a94a5504f54bef0c1724235"
DESIGN_PATH = E04_RUN / "next_candidate_design.json"
DESIGN_SHA = "77b4faa760ed4068b384b7cb4fdb0afad790e492154824a952c5e8f6495f61d6"
E04_REVIEW_PATH = E04_RUN / "reviews/repair_p07.json"
E04_REVIEW_SHA = "968693501849b4e304749f99823136e4c8cffc2ca7b9034cd3a1525f186dfc4c"

SCHEMA_VERSION = "extractube.stage3.t3.plan.source_preserving.experimental.v2"
BUNDLE_SCHEMA_VERSION = "extractube.stage3.t3.source_bundle.experimental.v1"
DOSSIER_SCHEMA_VERSION = "extractube.stage3.t3.resolved_dossiers.experimental.v2"

T4_INPUT_HARD_CAP = 12000
T4_INSTRUCTION_RESERVE = 4000
MATERIAL_PAYLOAD_CAP = T4_INPUT_HARD_CAP - T4_INSTRUCTION_RESERVE

CONSTRAINT_IDS = (
    "cover_all_assigned_information",
    "maintain_source_order",
    "preserve_compound_scope_and_negation",
    "do_not_resolve_uncertain_STT",
    "preserve_attribution_and_modality",
    "no_unverified_visual_claims",
)

POLICY_CATALOG = {
    "schema_version": "extractube.stage3.t3.writer_policy_catalog.experimental.v1",
    "plan_schema_version": SCHEMA_VERSION,
    "constraints": {
        "cover_all_assigned_information": {
            "id": "cover_all_assigned_information",
            "rule": "담당 required_source_indices에 배정된 모든 원문 문장의 주장·절차·수치·구체 예시·조건·예외를 누락 없이 반영하며, 임의로 문장이나 세부 조건을 자동 삭제하지 않는다.",
        },
        "maintain_source_order": {
            "id": "maintain_source_order",
            "rule": "담당 구간 내 원문 문장의 시간순·절차순 전개와 하위 목차 경계를 유지하며, 후반 설명을 앞당기거나 서로 다른 단계의 조건을 합치지 않는다.",
        },
        "preserve_compound_scope_and_negation": {
            "id": "preserve_compound_scope_and_negation",
            "rule": "단독 발화·복합 어구·부정 표현('아니죠', '아니니까' 등)·가능성 표현('될 수 있겠죠', '될 수 있고' 등)의 수식 범위를 원문 그대로 보존하고, 단어를 임의 분리해 원문에 없는 유지/삭제 확정 목록이나 대체 분류를 만들지 않는다.",
            "caution_directive": "원문 단독 발화와 복합 어구·부정·가능성의 결합 범위를 임의로 분해하거나 상식으로 보충해 유지/삭제 확정 분류 목록을 생성하지 말고, 지시 대상이 모호한 구간은 원문 범위 안에서만 서술한다.",
        },
        "do_not_resolve_uncertain_STT": {
            "id": "do_not_resolve_uncertain_STT",
            "rule": "전사 오류 의심 구간·잘린 어절·의도적 오타 검색어 예시를 상식이나 외부 지식으로 임의 교정하거나 실제 UI 필드·사실로 확정하지 않고 불확실성을 보존한다.",
        },
        "preserve_attribution_and_modality": {
            "id": "preserve_attribution_and_modality",
            "rule": "화자의 개인 경험·권고·추정·전언('알고 있거든요', '해준다고 해요', '추천드려요' 등)과 시점 대비('예전에는/이제는')를 화자 귀속 수준 그대로 유지하며 독립 검증된 현재 플랫폼 공식 정책으로 격상하지 않는다.",
        },
        "no_unverified_visual_claims": {
            "id": "no_unverified_visual_claims",
            "rule": "시각 판독기(reader) 제외 원칙을 유지하며, 승인된 시각 검수 근거 없이 화면 선택 상태·레이아웃·가려진 텍스트를 추정해 서술하지 않고 defer 자산은 본문 사실 근거로 사용하지 않는다.",
        },
    },
}

FORBIDDEN_DERIVED_STRINGS = (
    "기구를 삭제 대상으로",
    "욕실만 분리해 유지",
    "'기구', '액체', '싱크대')는 삭제",
    "'압력', '미니', '욕실'과 같이",
)


def current():
    return HERE / "runs" / t.read(HERE / "e05_campaign.json")["run_id"]


def relpath(p):
    return str(Path(p).resolve().relative_to(ROOT.resolve()))


def resolve_pointer(doc, pointer):
    if not isinstance(pointer, str):
        raise ValueError("json_pointer_type")
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise ValueError("json_pointer_syntax")
    cur = doc
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if token not in cur:
                raise ValueError("json_pointer_missing_key:" + token)
            cur = cur[token]
        elif isinstance(cur, list):
            if token != "0" and (not token.isdigit() or token.startswith("0")):
                raise ValueError("json_pointer_array_index:" + token)
            idx = int(token)
            if not 0 <= idx < len(cur):
                raise ValueError("json_pointer_index_range:" + token)
            cur = cur[idx]
        else:
            raise ValueError("json_pointer_non_container")
    return cur


def validate_e05_schema(value, s):
    if "const" in s:
        if type(value) is not type(s["const"]) or value != s["const"]:
            raise ValueError("schema_const_mismatch")
    if "enum" in s:
        if not any(type(value) is type(opt) and value == opt for opt in s["enum"]):
            raise ValueError("schema_enum_mismatch")
    kind = s.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError("schema_object_type")
        req = s.get("required", [])
        if any(k not in value for k in req):
            raise ValueError("schema_missing_required")
        props = s.get("properties", {})
        if s.get("additionalProperties") is False and any(k not in props for k in value):
            raise ValueError("schema_additional_properties")
        for k, v in value.items():
            if k in props:
                validate_e05_schema(v, props[k])
    elif kind == "array":
        if not isinstance(value, list):
            raise ValueError("schema_array_type")
        if len(value) < s.get("minItems", 0):
            raise ValueError("schema_min_items")
        if "maxItems" in s and len(value) > s["maxItems"]:
            raise ValueError("schema_max_items")
        if s.get("uniqueItems") and len({t.digest(x) for x in value}) != len(value):
            raise ValueError("schema_unique_items")
        if "items" in s:
            for item in value:
                validate_e05_schema(item, s["items"])
    elif kind == "integer":
        if type(value) is not int:
            raise ValueError("schema_integer_type")
        if "minimum" in s and value < s["minimum"]:
            raise ValueError("schema_minimum")
        if "maximum" in s and value > s["maximum"]:
            raise ValueError("schema_maximum")
    elif kind == "string":
        if not isinstance(value, str):
            raise ValueError("schema_string_type")
        if len(value) < s.get("minLength", 0):
            raise ValueError("schema_min_length")
        if "maxLength" in s and len(value) > s["maxLength"]:
            raise ValueError("schema_max_length")
        if "pattern" in s and re.fullmatch(s["pattern"], value) is None:
            raise ValueError("schema_pattern")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("schema_boolean_type")
    elif kind is not None:
        raise ValueError("schema_unsupported_type:" + str(kind))
    return value


def extract_leaves(structure, target_range):
    parse_structure(structure, list(target_range))
    leaves = []
    leaf_idx = 0
    for r_idx, root_node in enumerate(structure["sections"]):
        r_start, r_end = root_node["sentence_start"], root_node["sentence_end"]
        children = root_node["children"]
        if children:
            for c_idx, ch in enumerate(children):
                leaves.append(
                    {
                        "root_index": r_idx,
                        "root_title": root_node["title"],
                        "root_range": [r_start, r_end],
                        "child_index_in_root": c_idx,
                        "leaf_index": leaf_idx,
                        "leaf_title": ch["title"],
                        "sentence_start": ch["sentence_start"],
                        "sentence_end": ch["sentence_end"],
                    }
                )
                leaf_idx += 1
        else:
            leaves.append(
                {
                    "root_index": r_idx,
                    "root_title": root_node["title"],
                    "root_range": [r_start, r_end],
                    "child_index_in_root": None,
                    "leaf_index": leaf_idx,
                    "leaf_title": root_node["title"],
                    "sentence_start": r_start,
                    "sentence_end": r_end,
                }
            )
            leaf_idx += 1
    cursor = target_range[0]
    for lf in leaves:
        if lf["sentence_start"] != cursor or lf["sentence_end"] <= lf["sentence_start"]:
            raise ValueError("leaf_partition_gap_or_overlap")
        cursor = lf["sentence_end"]
    if cursor != target_range[1]:
        raise ValueError("leaf_partition_end_mismatch")
    return leaves


def build_source_bundle(t3_inp, confirm_sentences, t2_input_ref):
    t.verify_sources(t3_inp, confirm_sentences)
    p_vid = t3_inp["primary_video_id"]
    c_vid = "H2WN130OzLg"
    if p_vid == c_vid:
        raise ValueError("primary_confirmation_video_collision")
    p_ref = t3_inp["primary_sentences"][0]["source_ref"]
    c_ref = confirm_sentences[0]["source_ref"]
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "immutable": True,
        "total_sentences": len(t3_inp["primary_sentences"]) + len(confirm_sentences),
        "videos": {
            p_vid: {
                "role": "primary",
                "video_id": p_vid,
                "sentence_range": [0, 217],
                "sentence_count": len(t3_inp["primary_sentences"]),
                "stage1_source_ref": {"path": p_ref["path"], "sha256": p_ref["sha256"]},
                "upstream_input_ref": {
                    "path": relpath(t.INPUT),
                    "sha256": t.INPUT_SHA,
                    "json_pointer": "/primary_sentences",
                },
                "sentences": copy.deepcopy(t3_inp["primary_sentences"]),
            },
            c_vid: {
                "role": "confirmation_validation_only",
                "video_id": c_vid,
                "sentence_range": [134, 192],
                "sentence_count": len(confirm_sentences),
                "stage1_source_ref": {"path": c_ref["path"], "sha256": c_ref["sha256"]},
                "upstream_input_ref": t2_input_ref,
                "sentences": copy.deepcopy(confirm_sentences),
            },
        },
    }


def build_editorial_reference_notes():
    t.bound_sha(T1_REVIEW, T1_REVIEW_SHA)
    e02_sel = E02_RUN / "selected/p02.json"
    e02_sel_sha = t.sha(e02_sel)
    return {
        "schema_version": "extractube.stage3.t3.editorial_reference_notes.experimental.v1",
        "auto_deleted_in_e05": False,
        "included_in_plan_contract": False,
        "purpose": "Historical editorial/transition observations kept strictly outside automatic source deletion; E05 preserves 100% of assigned sentences.",
        "notes": [
            {
                "video_id": "Fw-8hZhnH3Q",
                "sentence_indices": [1],
                "status": "must_preserve_speaker_attribution",
                "origin_ref": {"path": relpath(T1_REVIEW), "sha256": T1_REVIEW_SHA, "json_pointer": "/audio/Fw-8hZhnH3Q:1"},
                "note": "자기소개('어카미입니다.')는 비정보성 인사로 자동 삭제하지 않고 화자 귀속 정보로 보존한다.",
            },
            {
                "video_id": "Fw-8hZhnH3Q",
                "sentence_indices": [39],
                "status": "transition_reference_only_not_deleted",
                "origin_ref": {"path": relpath(e02_sel), "sha256": e02_sel_sha, "json_pointer": "/omit_candidates/0"},
                "note": "E02 검수에서 마무리 전환 발화로 기록되었으나 E05는 no_automatic_deletion 정책에 따라 원문을 삭제하지 않고 그대로 제공한다.",
            },
            {
                "video_id": "Fw-8hZhnH3Q",
                "sentence_indices": [66, 90],
                "status": "repetition_or_transition_reference_only_not_deleted",
                "origin_ref": {"path": relpath(T1_REVIEW), "sha256": T1_REVIEW_SHA, "json_pointer": "/audio"},
                "note": "T1 검수에서 연결 발화(66) 및 인접 중복 언급(90) 참고 기록이 있으나 E05는 자동 삭제하지 않고 권고·조작 맥락과 함께 원문 전체를 유지한다.",
            },
        ],
    }


def primary_caution_descriptors():
    d = t.read(t.INPUT)
    t.bound_sha(t.INPUT, t.INPUT_SHA)
    t.bound_sha(E04_REVIEW_PATH, E04_REVIEW_SHA)
    items = []
    for idx, c in enumerate(d["source_cautions"]):
        items.append(
            {
                "sentence_indices": [c["sentence_index"]],
                "ref": {
                    "path": relpath(t.INPUT),
                    "sha256": t.INPUT_SHA,
                    "json_pointer": f"/source_cautions/{idx}",
                },
            }
        )
    finding0 = t.read(E04_REVIEW_PATH)["findings"][0]
    if finding0["source_indices"] != [134, 135]:
        raise ValueError("unexpected_e04_finding_indices")
    items.append(
        {
            "sentence_indices": [134, 135],
            "ref": {
                "path": relpath(E04_REVIEW_PATH),
                "sha256": E04_REVIEW_SHA,
                "json_pointer": "/findings/0",
            },
        }
    )
    return items


def build_plan(
    *,
    video_id,
    unit_prefix,
    target_range,
    source_bundle_ref,
    structure_ref,
    structure_obj,
    caution_descriptors,
    connections,
    assets,
):
    leaves = extract_leaves(structure_obj, target_range)
    assigned_cautions = 0
    units = []
    for lf in leaves:
        a, z = lf["sentence_start"], lf["sentence_end"]
        unit_id = f"{unit_prefix}_u{lf['leaf_index']:02d}"
        unit_cautions = []
        for cd in sorted(caution_descriptors, key=lambda x: (min(x["sentence_indices"]), x["ref"]["json_pointer"])):
            s_idxs = cd["sentence_indices"]
            if all(a <= ix < z for ix in s_idxs):
                unit_cautions.append(copy.deepcopy(cd["ref"]))
                assigned_cautions += 1
            elif any(a <= ix < z for ix in s_idxs):
                raise ValueError("caution_spans_leaf_boundary")

        internal_links = []
        prev_indices = set()
        for conn in connections:
            frm = conn["from_sentence_index"]
            if a <= frm < z:
                internal_tos = []
                for to_ix in conn["to_sentence_indices"]:
                    if to_ix < a:
                        prev_indices.add(to_ix)
                    elif a <= to_ix < frm:
                        internal_tos.append(to_ix)
                    else:
                        raise ValueError("forward_or_self_reference_in_connection")
                if internal_tos:
                    internal_links.append(
                        {
                            "from_sentence_index": frm,
                            "to_sentence_indices": sorted(set(internal_tos)),
                        }
                    )

        unit_asset_ids = []
        for asset in assets:
            if asset["video_id"] != video_id:
                continue
            tref = asset["source"]["text_ref"]
            if tref["sentence_start"] < z and a < tref["sentence_end"]:
                unit_asset_ids.append(asset["asset_id"])
        asset_action = "defer" if unit_asset_ids else "skip"

        units.append(
            {
                "unit_id": unit_id,
                "root_index": lf["root_index"],
                "leaf_index": lf["leaf_index"],
                "source_range": {"sentence_start": a, "sentence_end": z},
                "required_source_indices": list(range(a, z)),
                "required_source_policy": "preserve_all_information",
                "caution_refs": unit_cautions,
                "internal_source_links": internal_links,
                "previous_context_indices": sorted(prev_indices),
                "asset_ids": unit_asset_ids,
                "asset_action": asset_action,
                "omission_policy": "no_automatic_deletion",
                "writer_constraint_ids": list(CONSTRAINT_IDS),
            }
        )

    if assigned_cautions != len(caution_descriptors):
        raise ValueError("unassigned_or_duplicate_caution")

    return {
        "schema_version": SCHEMA_VERSION,
        "video_id": video_id,
        "source_bundle_ref": copy.deepcopy(source_bundle_ref),
        "structure_ref": copy.deepcopy(structure_ref),
        "writer_constraints": {cid: True for cid in CONSTRAINT_IDS},
        "units": units,
        "reader_policy": "excluded",
        "model_fact_restatement": False,
        "new_visual_evidence": [],
    }


def _default_loader(ref, root):
    p = Path(root) / ref["path"]
    t.bound_sha(p, ref["sha256"])
    return t.read(p)


def resolve_and_validate_plan(
    plan,
    *,
    schema_obj,
    policy_catalog,
    expected_video_id,
    expected_range,
    expected_caution_refs,
    upstream_connections,
    upstream_assets,
    allowed_reviewed_assets=frozenset(),
    root=ROOT,
    file_loader=None,
    resolved_sentence_mutator=None,
):
    loader = file_loader or (lambda ref: _default_loader(ref, root))
    validate_e05_schema(plan, schema_obj)

    if plan["video_id"] != expected_video_id:
        raise ValueError("video_id_mismatch")
    if plan["reader_policy"] != "excluded" or plan["model_fact_restatement"] is not False or plan["new_visual_evidence"] != []:
        raise ValueError("top_level_policy_violation")

    catalog_ids = set(policy_catalog["constraints"])
    if set(plan["writer_constraints"]) != catalog_ids or any(v is not True for v in plan["writer_constraints"].values()):
        raise ValueError("writer_constraints_mismatch")

    bundle_doc = loader(plan["source_bundle_ref"])
    sentences = resolve_pointer(bundle_doc, plan["source_bundle_ref"]["json_pointer"])
    if not isinstance(sentences, list) or [s["sentence_index"] for s in sentences] != list(range(*expected_range)):
        raise ValueError("source_bundle_range_mismatch")

    by_index = {}
    for s in sentences:
        if s["video_id"] != expected_video_id:
            raise ValueError("cross_video_sentence_in_bundle")
        s_ref = s["source_ref"]
        stage1_doc = loader(s_ref)
        original = stage1_doc["chunks"][s["sentence_index"]]
        if s["text"] != original["text"] or s["source_chunk_index"] != original["index"]:
            raise ValueError("stage1_source_text_mismatch")
        by_index[s["sentence_index"]] = s

    structure_doc = loader(plan["structure_ref"])
    structure = resolve_pointer(structure_doc, plan["structure_ref"]["json_pointer"])
    leaves = extract_leaves(structure, expected_range)
    if len(plan["units"]) != len(leaves):
        raise ValueError("unit_count_mismatch")

    assets_by_id = {a["asset_id"]: a for a in upstream_assets if a["video_id"] == expected_video_id}
    seen_indices = set()
    seen_caution_digests = []
    observed_internal_links = {}
    observed_prev_by_unit = {}
    resolved_units = []

    for unit, lf in zip(plan["units"], leaves):
        a, z = lf["sentence_start"], lf["sentence_end"]
        sr = unit["source_range"]
        if unit["root_index"] != lf["root_index"] or unit["leaf_index"] != lf["leaf_index"]:
            raise ValueError("unit_hierarchy_index_mismatch")
        if sr["sentence_start"] != a or sr["sentence_end"] != z or a >= z:
            raise ValueError("unit_source_range_mismatch")
        req = unit["required_source_indices"]
        if req != list(range(a, z)):
            raise ValueError("required_source_indices_not_exact_range")
        if seen_indices & set(req):
            raise ValueError("duplicate_sentence_responsibility")
        seen_indices.update(req)

        cids = unit["writer_constraint_ids"]
        if any(cid not in catalog_ids for cid in cids) or set(cids) != catalog_ids or len(cids) != len(catalog_ids):
            raise ValueError("invalid_writer_constraint_ids")

        prev = unit["previous_context_indices"]
        if prev != sorted(set(prev)):
            raise ValueError("unsorted_or_duplicate_previous_context")
        for px in prev:
            if px >= a:
                raise ValueError("forward_or_current_context_leakage")
            if px not in by_index:
                raise ValueError("previous_context_outside_video_scope")
        observed_prev_by_unit[unit["leaf_index"]] = prev

        resolved_internal = []
        for link in unit["internal_source_links"]:
            frm = link["from_sentence_index"]
            tos = link["to_sentence_indices"]
            if not (a <= frm < z) or not tos or tos != sorted(set(tos)):
                raise ValueError("invalid_internal_source_link")
            if any(not (a <= tx < frm) for tx in tos):
                raise ValueError("forward_or_external_internal_link")
            matching = [
                c for c in upstream_connections
                if c["from_sentence_index"] == frm and sorted(x for x in c["to_sentence_indices"] if a <= x < frm) == tos
            ]
            if not matching:
                raise ValueError("unbacked_internal_source_link")
            observed_internal_links[frm] = tos
            resolved_internal.append(
                {
                    "from_sentence_index": frm,
                    "to_sentence_indices": tos,
                    "relation": matching[0]["relation"],
                    "target_sentences": [
                        {"sentence_index": tx, "text": by_index[tx]["text"]}
                        for tx in tos
                    ],
                }
            )

        resolved_cautions = []
        for cref in unit["caution_refs"]:
            cdoc = loader(cref)
            target = resolve_pointer(cdoc, cref["json_pointer"])
            seen_caution_digests.append(t.digest(cref))
            if "sentence_index" in target:
                if target.get("video_id") != expected_video_id:
                    raise ValueError("cross_video_caution_ref")
                s_ix = target["sentence_index"]
                if not (a <= s_ix < z):
                    raise ValueError("caution_outside_unit_range")
                resolved_cautions.append(
                    {
                        "caution_ref": copy.deepcopy(cref),
                        "sentence_indices": [s_ix],
                        "kind": target["kind"],
                        "guidance": target["detail"],
                        "derived_classification": None,
                    }
                )
            elif "source_indices" in target and target.get("id") == "M01":
                s_ixs = target["source_indices"]
                if expected_video_id != "Fw-8hZhnH3Q" or any(not (a <= ix < z) for ix in s_ixs):
                    raise ValueError("prior_review_caution_outside_unit_range")
                resolved_cautions.append(
                    {
                        "caution_ref": copy.deepcopy(cref),
                        "sentence_indices": list(s_ixs),
                        "kind": "prior_plan_distortion_guard_no_classification",
                        "linked_constraint_id": "preserve_compound_scope_and_negation",
                        "guidance": policy_catalog["constraints"]["preserve_compound_scope_and_negation"]["caution_directive"],
                        "faulty_prior_plan_prose_excluded": True,
                        "derived_classification": None,
                    }
                )
            else:
                raise ValueError("unsupported_caution_target")

        aids = unit["asset_ids"]
        action = unit["asset_action"]
        if not aids:
            if action != "skip":
                raise ValueError("empty_assets_must_skip")
            resolved_assets = []
        else:
            if len(aids) != len(set(aids)):
                raise ValueError("duplicate_asset_id")
            resolved_assets = []
            for aid in aids:
                if aid not in assets_by_id:
                    raise ValueError("unknown_or_cross_video_asset_id")
                asset = assets_by_id[aid]
                tref = asset["source"]["text_ref"]
                if not (tref["sentence_start"] < z and a < tref["sentence_end"]):
                    raise ValueError("asset_outside_legacy_overlap_range")
                if action == "use_already_reviewed_only" and (aid, unit["unit_id"]) not in allowed_reviewed_assets:
                    raise ValueError("unverified_visual_claim_use_not_approved")
                if action == "skip":
                    raise ValueError("present_asset_marked_skip_instead_of_defer")
                resolved_assets.append(
                    {
                        "asset_id": aid,
                        "sha256": asset["sha256"],
                        "video_seconds": asset["source"]["video_seconds"],
                        "legacy_unit_range": [tref["sentence_start"], tref["sentence_end"]],
                        "caption": asset["caption"],
                        "caption_role": asset["caption_role"],
                        "review_reason": asset["review_reason"],
                        "limitations": asset["limitations"],
                        "verified_text": asset["verified_text"],
                    }
                )

        resolved_sentences = [
            {
                "sentence_index": ix,
                "text": by_index[ix]["text"],
                "audio_start": by_index[ix]["audio_start"],
                "audio_end": by_index[ix]["audio_end"],
                "source_ref": copy.deepcopy(by_index[ix]["source_ref"]),
            }
            for ix in req
        ]
        if resolved_sentence_mutator is not None:
            resolved_sentence_mutator(unit["unit_id"], resolved_sentences)

        for rs in resolved_sentences:
            ix = rs["sentence_index"]
            if rs["text"] != by_index[ix]["text"]:
                raise ValueError("resolved_sentence_text_tampered")

        if expected_video_id == "Fw-8hZhnH3Q" and a <= 135 < z:
            s134 = next((r["text"] for r in resolved_sentences if r["sentence_index"] == 134), None)
            s135 = next((r["text"] for r in resolved_sentences if r["sentence_index"] == 135), None)
            if s134 != "기구." or s135 is None or "욕실 뚜렷봉 액체 아니니까" not in s135:
                raise ValueError("composite_phrase_134_135_truncated_or_modified")

        prior_links = [
            {
                "from_sentence_index": c["from_sentence_index"],
                "to_sentence_indices": sorted(x for x in c["to_sentence_indices"] if x < a),
                "relation": c["relation"],
            }
            for c in upstream_connections
            if a <= c["from_sentence_index"] < z and any(x < a for x in c["to_sentence_indices"])
        ]

        resolved_units.append(
            {
                "unit_id": unit["unit_id"],
                "video_id": expected_video_id,
                "section_hierarchy": {
                    "root_index": lf["root_index"],
                    "root_title": lf["root_title"],
                    "root_range": lf["root_range"],
                    "leaf_index": lf["leaf_index"],
                    "child_index_in_root": lf["child_index_in_root"],
                    "leaf_title": lf["leaf_title"],
                    "unit_position": f"{lf['leaf_index'] + 1}/{len(leaves)}",
                },
                "source_range": copy.deepcopy(sr),
                "required_source_policy": unit["required_source_policy"],
                "omission_policy": unit["omission_policy"],
                "required_source_sentences": resolved_sentences,
                "internal_source_links": resolved_internal,
                "previous_context": {
                    "role": "read_only_prior_context_do_not_duplicate_as_current_body",
                    "previous_context_indices": prev,
                    "links_from_current_unit": prior_links,
                    "sentences": [
                        {
                            "sentence_index": px,
                            "text": by_index[px]["text"],
                            "audio_start": by_index[px]["audio_start"],
                            "audio_end": by_index[px]["audio_end"],
                        }
                        for px in prev
                    ],
                },
                "resolved_cautions": resolved_cautions,
                "assets": {
                    "asset_ids": list(aids),
                    "asset_action": action,
                    "reader_policy": "excluded",
                    "resolved_assets": resolved_assets,
                },
                "writer_constraints": [
                    {
                        "id": cid,
                        "rule": policy_catalog["constraints"][cid]["rule"],
                    }
                    for cid in cids
                ],
                "model_fact_restatement": False,
                "new_visual_evidence": [],
            }
        )

    if seen_indices != set(range(*expected_range)):
        raise ValueError("incomplete_sentence_coverage")

    expected_digests = sorted(t.digest(r) for r in expected_caution_refs)
    if sorted(seen_caution_digests) != expected_digests:
        raise ValueError("missing_or_unexpected_caution_refs")

    for conn in upstream_connections:
        frm = conn["from_sentence_index"]
        owner = next(lf for lf in leaves if lf["sentence_start"] <= frm < lf["sentence_end"])
        a = owner["sentence_start"]
        expected_internal = sorted(x for x in conn["to_sentence_indices"] if a <= x < frm)
        expected_prev = set(x for x in conn["to_sentence_indices"] if x < a)
        if expected_internal and observed_internal_links.get(frm) != expected_internal:
            raise ValueError("missing_internal_connection")
        if not expected_prev.issubset(set(observed_prev_by_unit[owner["leaf_index"]])):
            raise ValueError("missing_previous_context_connection")

    dossier_doc = {
        "schema_version": DOSSIER_SCHEMA_VERSION,
        "plan_schema_version": plan["schema_version"],
        "plan_sha256": t.digest(plan),
        "video_id": expected_video_id,
        "sentence_range": list(expected_range),
        "sentence_count": expected_range[1] - expected_range[0],
        "unit_count": len(resolved_units),
        "document_outline": copy.deepcopy(structure),
        "reader_policy": "excluded",
        "model_fact_restatement": False,
        "new_visual_evidence": [],
        "units": resolved_units,
    }

    serialized = t.wire(dossier_doc).decode("utf-8")
    for bad in FORBIDDEN_DERIVED_STRINGS:
        if bad in serialized:
            raise ValueError("forbidden_derived_classification_leaked")

    return dossier_doc


def assert_bundle_isolation(primary_dossiers, confirm_dossiers):
    if primary_dossiers["video_id"] == confirm_dossiers["video_id"]:
        raise ValueError("bundle_video_id_collision")
    p_vids = {u["video_id"] for u in primary_dossiers["units"]} | {
        s["source_ref"]["path"] for u in primary_dossiers["units"] for s in u["required_source_sentences"]
    }
    c_vids = {u["video_id"] for u in confirm_dossiers["units"]} | {
        s["source_ref"]["path"] for u in confirm_dossiers["units"] for s in u["required_source_sentences"]
    }
    if p_vids & c_vids:
        raise ValueError("cross_contaminated_sentences")
    p_assets = {aid for u in primary_dossiers["units"] for aid in u["assets"]["asset_ids"]}
    c_assets = {aid for u in confirm_dossiers["units"] for aid in u["assets"]["asset_ids"]}
    if p_assets & c_assets or c_assets:
        raise ValueError("cross_contaminated_assets")
    return True


def dossier_material_payload(dossier_doc, unit):
    return {
        "video_id": dossier_doc["video_id"],
        "document_outline": dossier_doc["document_outline"],
        "unit": unit,
    }


def measure_payload_tokens(primary_dossiers, confirm_dossiers):
    entries = []
    for phase, doc in [("primary", primary_dossiers), ("confirmation", confirm_dossiers)]:
        for unit in doc["units"]:
            payload_str = t.wire(dossier_material_payload(doc, unit)).decode("utf-8")
            entries.append(
                {
                    "unit_id": unit["unit_id"],
                    "phase": phase,
                    "video_id": doc["video_id"],
                    "sentence_count": len(unit["required_source_sentences"]),
                    "previous_context_count": len(unit["previous_context"]["sentences"]),
                    "caution_count": len(unit["resolved_cautions"]),
                    "payload_bytes": len(payload_str.encode("utf-8")),
                    "content": payload_str,
                }
            )
    script = '''import hashlib, json, os, sys
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import transformers
from transformers import AutoProcessor
expected = {"MODEL_PATH":"/models/qwen3.8-27b","SERVED_MODEL_NAME":"qwen3.8-27b","MAX_MODEL_LEN":"32768","MAX_OUTPUT_TOKENS":"4096","MODEL_PROFILE":"qwen3.8-27b"}
if any(os.environ.get(k) != v for k, v in expected.items()) or json.loads(os.environ.get("MM_PROCESSOR_KWARGS", "{}")) != {"max_pixels": 262144}:
    raise RuntimeError("runtime_profile_mismatch")
processor = AutoProcessor.from_pretrained("/models/qwen3.8-27b", local_files_only=True, max_pixels=262144)
items = json.load(sys.stdin)["entries"]
rows = []
for item in items:
    raw_ids = processor.tokenizer.encode(item["content"], add_special_tokens=False)
    wrapped = processor.apply_chat_template(
        [{"role": "user", "content": item["content"]}],
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
        enable_thinking=False,
        processor_kwargs={"images_kwargs": {"max_pixels": 262144}},
    )
    rows.append({
        "unit_id": item["unit_id"],
        "phase": item["phase"],
        "video_id": item["video_id"],
        "sentence_count": item["sentence_count"],
        "previous_context_count": item["previous_context_count"],
        "caution_count": item["caution_count"],
        "payload_bytes": item["payload_bytes"],
        "raw_material_tokens": int(len(raw_ids)),
        "chat_wrapped_material_tokens": int(wrapped["input_ids"].shape[-1]),
    })
print(json.dumps({
    "transformers": transformers.__version__,
    "processor": type(processor).__name__,
    "image_processor": type(processor.image_processor).__name__,
    "image_processor_size": vars(processor.image_processor.size),
    "template_sha256": hashlib.sha256(str(processor.chat_template).encode()).hexdigest(),
    "max_pixels": 262144,
    "runtime_environment": expected,
    "weights_loaded": False,
    "units": rows,
}))'''
    r = subprocess.run(
        ["docker", "exec", "-i", t.old.CONTAINER, "python3", "-c", script],
        input=t.wire({"entries": entries}),
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError("cpu_token_plan_failed:" + r.stderr.decode("utf-8", "replace"))
    measured = t.loads(r.stdout.decode("utf-8"))
    for row in measured["units"]:
        row["remaining_for_t4_instructions_under_12k"] = T4_INPUT_HARD_CAP - row["chat_wrapped_material_tokens"]
        row["within_material_payload_cap"] = row["chat_wrapped_material_tokens"] <= MATERIAL_PAYLOAD_CAP
        if not row["within_material_payload_cap"]:
            raise ValueError("unit_exceeds_material_payload_cap:" + row["unit_id"])

    p_rows = [u for u in measured["units"] if u["phase"] == "primary"]
    c_rows = [u for u in measured["units"] if u["phase"] == "confirmation"]
    measured.update(
        {
            "scope": "CPU-only T4 writing material payload tokenizer measurement (weights_loaded=False). T4 prompt instructions are not yet finalized; do NOT report as full T4 request prompt_tokens or model execution.",
            "model_calls": 0,
            "t4_input_hard_cap": T4_INPUT_HARD_CAP,
            "t4_instruction_reserve_tokens": T4_INSTRUCTION_RESERVE,
            "unit_material_payload_cap_tokens": MATERIAL_PAYLOAD_CAP,
            "summary": {
                "primary_unit_count": len(p_rows),
                "primary_raw_tokens_sum": sum(u["raw_material_tokens"] for u in p_rows),
                "primary_chat_wrapped_tokens_max": max(u["chat_wrapped_material_tokens"] for u in p_rows),
                "primary_chat_wrapped_tokens_min": min(u["chat_wrapped_material_tokens"] for u in p_rows),
                "confirmation_unit_count": len(c_rows),
                "confirmation_raw_tokens_sum": sum(u["raw_material_tokens"] for u in c_rows),
                "confirmation_chat_wrapped_tokens_max": max(u["chat_wrapped_material_tokens"] for u in c_rows),
                "confirmation_chat_wrapped_tokens_min": min(u["chat_wrapped_material_tokens"] for u in c_rows),
                "all_units_within_cap": True,
            },
        }
    )
    return measured


def check(run):
    e04.check(E04_RUN)
    for p, h in t.read(run / "state.json")["frozen_files"].items():
        t.bound_sha(p, h)
    if (run / "confirmation_freeze.json").exists():
        for p, h in t.read(run / "confirmation_freeze.json")["frozen_files"].items():
            t.bound_sha(p, h)
    return t.read(run / "config.json")


def prepare():
    if (HERE / "e05_campaign.json").exists():
        check(current())
        print("E05_prepare_reused:" + str(current()))
        return

    prev_cfg = e04.check(E04_RUN)
    t.bound_sha(SCHEMA_PATH, SCHEMA_SHA)
    t.bound_sha(DESIGN_PATH, DESIGN_SHA)
    t.bound_sha(E04_REVIEW_PATH, E04_REVIEW_SHA)

    schema_obj = t.read(SCHEMA_PATH)
    d = t.read(t.INPUT)
    t2_inp_path = t.PARENT / "input.json"
    t2_inp_sha = t.sha(t2_inp_path)
    confirm_sentences = t.read(t2_inp_path)["fresh_sentences"]
    t.verify_sources(d, confirm_sentences)

    confirm_struct_ref = d["validation_only"]["confirmation_structure_ref"]
    t.bound_sha(ROOT / confirm_struct_ref["path"], confirm_struct_ref["sha256"])

    rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_e05_" + uuid.uuid4().hex[:6]
    run = HERE / "runs" / rid
    run.mkdir(parents=True)

    bundle = build_source_bundle(
        d,
        confirm_sentences,
        {"path": relpath(t2_inp_path), "sha256": t2_inp_sha, "json_pointer": "/fresh_sentences"},
    )
    bundle_path = run / "source_bundle.json"
    t.create(bundle_path, bundle)
    bundle_sha = t.sha(bundle_path)

    policy_path = run / "policy_catalog.json"
    t.create(policy_path, POLICY_CATALOG)
    policy_sha = t.sha(policy_path)

    notes_path = run / "editorial_reference_notes.json"
    t.create(notes_path, build_editorial_reference_notes())

    p_cautions = primary_caution_descriptors()
    p_bundle_ref = {
        "path": relpath(bundle_path),
        "sha256": bundle_sha,
        "json_pointer": f"/videos/{d['primary_video_id']}/sentences",
    }
    p_struct_ref = {
        "path": relpath(t.INPUT),
        "sha256": t.INPUT_SHA,
        "json_pointer": "/primary_structure",
    }
    primary_plan = build_plan(
        video_id=d["primary_video_id"],
        unit_prefix="p",
        target_range=[0, 217],
        source_bundle_ref=p_bundle_ref,
        structure_ref=p_struct_ref,
        structure_obj=d["primary_structure"],
        caution_descriptors=p_cautions,
        connections=d["connections"],
        assets=d["primary_assets"],
    )
    primary_resolved = resolve_and_validate_plan(
        primary_plan,
        schema_obj=schema_obj,
        policy_catalog=POLICY_CATALOG,
        expected_video_id=d["primary_video_id"],
        expected_range=[0, 217],
        expected_caution_refs=[x["ref"] for x in p_cautions],
        upstream_connections=d["connections"],
        upstream_assets=d["primary_assets"],
    )
    t.create(run / "primary_plan.json", primary_plan)
    t.create(run / "resolved_primary_dossiers.json", primary_resolved)

    critical = {str(p): t.sha(p) for p in E04_RUN.rglob("*") if p.is_file() and p.suffix != ".lock"}
    for p in [HERE / "run_e05_plan.py", HERE / "test_e05_plan.py"]:
        critical[str(p)] = t.sha(p)

    cfg = {
        "candidate": "E05",
        "method": "CPU-compiled source-preserving T3 plan dossiers without model factual restatement",
        "contract_change": True,
        "plan_schema_version": SCHEMA_VERSION,
        "plan_schema_ref": {"path": relpath(SCHEMA_PATH), "sha256": SCHEMA_SHA},
        "design_ref": {"path": relpath(DESIGN_PATH), "sha256": DESIGN_SHA},
        "policy_catalog_sha256": policy_sha,
        "source_bundle_sha256": bundle_sha,
        "parent_run": str(E04_RUN),
        "critical_files": critical,
        "model_execution_enabled": False,
        "new_inference_calls_planned": 0,
        "new_http_seconds_planned": 0,
        "family_max_calls": 15,
        "family_max_http_seconds": 4200,
        "prior_calls": 6,
        "prior_http_seconds": 276.185664,
        "candidate_variants": 1,
        "policy_revisions_after_freeze": 0,
        "reader_policy": "excluded",
        "t4_payload_budget": {
            "t4_input_hard_cap": T4_INPUT_HARD_CAP,
            "t4_instruction_reserve_tokens": T4_INSTRUCTION_RESERVE,
            "unit_material_payload_cap_tokens": MATERIAL_PAYLOAD_CAP,
        },
        "token_identity": prev_cfg["token_identity"],
        "input_sha256": t.INPUT_SHA,
        "authorization": "2026-10-31 user E05 implement, full source review, and T3 closure judgement",
    }
    t.create(run / "config.json", cfg)
    (run / "input.json").write_bytes(t.INPUT.read_bytes())
    (run / "plan_schema.json").write_bytes(SCHEMA_PATH.read_bytes())
    (run / "spec_snapshot.md").write_bytes(t.SPEC.read_bytes())
    (run / "source_code").mkdir()
    for p in [
        HERE / "run_e05_plan.py",
        HERE / "test_e05_plan.py",
        HERE / "render_e05.py",
        HERE / "closeout_e05.py",
        HERE / "run_e04_plan.py",
        HERE / "run_e03_plan.py",
        HERE / "run_e02_plan.py",
        HERE / "run_t3.py",
    ]:
        (run / "source_code" / p.name).write_bytes(p.read_bytes())

    r_test = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(HERE), "-p", "test_*.py", "-v"],
        capture_output=True,
    )
    t.create(
        run / "offline_check.json",
        {
            "returncode": r_test.returncode,
            "output": r_test.stdout.decode("utf-8", "replace") + r_test.stderr.decode("utf-8", "replace"),
            "inference_calls": 0,
        },
    )
    r_test.check_returncode()

    frozen = {str(p): t.sha(p) for p in run.rglob("*") if p.is_file()}
    frozen.update(critical)
    t.create(run / "state.json", {"frozen_files": frozen})
    t.create(
        HERE / "e05_campaign.json",
        {
            "run_id": rid,
            "model_execution_enabled": False,
            "new_inference_calls_planned": 0,
            "family_max_calls": 15,
            "family_max_http_seconds": 4200,
        },
    )
    print(
        json.dumps(
            {
                "run_id": rid,
                "primary_units": len(primary_plan["units"]),
                "primary_sentences": 217,
                "protected_files": len(critical),
                "offline_tests": "passed",
                "inference_calls": 0,
            },
            ensure_ascii=False,
        )
    )


def freeze_and_confirm():
    run = current()
    cfg = check(run)
    if (run / "confirmation_freeze.json").exists():
        print("E05_confirmation_already_frozen:" + str(run))
        return

    for rname, expected_target in [
        ("reviews/gate1_known38_comparison.json", list(range(112, 150))),
        ("reviews/gate2_new30_review.json", list(range(57, 87))),
        ("reviews/primary_review.json", list(range(0, 217))),
    ]:
        rp = run / rname
        if not rp.exists():
            raise ValueError("missing_primary_gate_review:" + rname)
        rev = t.read(rp)
        if rev.get("decision") != "accepted" or rev.get("reviewed_sentence_indices") != expected_target:
            raise ValueError("unaccepted_primary_gate:" + rname)
        if rev.get("primary_plan_sha256") != t.sha(run / "primary_plan.json"):
            raise ValueError("review_plan_sha_mismatch:" + rname)

    freeze_paths = [
        SCHEMA_PATH,
        run / "policy_catalog.json",
        run / "source_bundle.json",
        run / "primary_plan.json",
        run / "resolved_primary_dossiers.json",
        run / "reviews/gate1_known38_comparison.json",
        run / "reviews/gate2_new30_review.json",
        run / "reviews/primary_review.json",
        HERE / "run_e05_plan.py",
        HERE / "test_e05_plan.py",
    ]
    freeze_record = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy_revisions_after_freeze": 0,
        "schema_revisions_after_freeze": 0,
        "code_revisions_after_freeze": 0,
        "frozen_files": {str(p): t.sha(p) for p in freeze_paths},
    }

    d = t.read(t.INPUT)
    schema_obj = t.read(SCHEMA_PATH)
    confirm_struct_ref = d["validation_only"]["confirmation_structure_ref"]
    confirm_struct = t.read(ROOT / confirm_struct_ref["path"])
    c_bundle_ref = {
        "path": relpath(run / "source_bundle.json"),
        "sha256": t.sha(run / "source_bundle.json"),
        "json_pointer": "/videos/H2WN130OzLg/sentences",
    }
    c_struct_ref = {
        "path": confirm_struct_ref["path"],
        "sha256": confirm_struct_ref["sha256"],
        "json_pointer": "",
    }

    confirmation_plan = build_plan(
        video_id="H2WN130OzLg",
        unit_prefix="c",
        target_range=[134, 192],
        source_bundle_ref=c_bundle_ref,
        structure_ref=c_struct_ref,
        structure_obj=confirm_struct,
        caution_descriptors=[],
        connections=[],
        assets=[],
    )
    confirmation_resolved = resolve_and_validate_plan(
        confirmation_plan,
        schema_obj=schema_obj,
        policy_catalog=POLICY_CATALOG,
        expected_video_id="H2WN130OzLg",
        expected_range=[134, 192],
        expected_caution_refs=[],
        upstream_connections=[],
        upstream_assets=[],
    )
    primary_resolved = t.read(run / "resolved_primary_dossiers.json")
    assert_bundle_isolation(primary_resolved, confirmation_resolved)

    tp = measure_payload_tokens(primary_resolved, confirmation_resolved)
    for k, v in cfg["token_identity"].items():
        if tp[k] != v:
            raise ValueError("token_identity_changed:" + k)

    t.create(run / "confirmation_plan.json", confirmation_plan)
    t.create(run / "resolved_confirmation_dossiers.json", confirmation_resolved)
    t.create(run / "token_plan.json", tp)
    for extra in [run / "confirmation_plan.json", run / "resolved_confirmation_dossiers.json", run / "token_plan.json"]:
        freeze_record["frozen_files"][str(extra)] = t.sha(extra)
    t.create(run / "confirmation_freeze.json", freeze_record)
    print(
        json.dumps(
            {
                "run_id": run.name,
                "confirmation_units": len(confirmation_plan["units"]),
                "confirmation_sentences": 58,
                "token_summary": tp["summary"],
                "policy_revisions_after_freeze": 0,
                "inference_calls": 0,
            },
            ensure_ascii=False,
        )
    )


def verify():
    run = current()
    cfg = check(run)
    d = t.read(t.INPUT)
    schema_obj = t.read(SCHEMA_PATH)
    p_cautions = primary_caution_descriptors()
    p_plan = t.read(run / "primary_plan.json")
    p_res = resolve_and_validate_plan(
        p_plan,
        schema_obj=schema_obj,
        policy_catalog=POLICY_CATALOG,
        expected_video_id=d["primary_video_id"],
        expected_range=[0, 217],
        expected_caution_refs=[x["ref"] for x in p_cautions],
        upstream_connections=d["connections"],
        upstream_assets=d["primary_assets"],
    )
    if p_res != t.read(run / "resolved_primary_dossiers.json"):
        raise ValueError("resolved_primary_dossiers_drift")

    if (run / "confirmation_plan.json").exists():
        c_plan = t.read(run / "confirmation_plan.json")
        c_res = resolve_and_validate_plan(
            c_plan,
            schema_obj=schema_obj,
            policy_catalog=POLICY_CATALOG,
            expected_video_id="H2WN130OzLg",
            expected_range=[134, 192],
            expected_caution_refs=[],
            upstream_connections=[],
            upstream_assets=[],
        )
        if c_res != t.read(run / "resolved_confirmation_dossiers.json"):
            raise ValueError("resolved_confirmation_dossiers_drift")
        assert_bundle_isolation(p_res, c_res)
        tp = t.read(run / "token_plan.json")
        for k, v in cfg["token_identity"].items():
            if tp[k] != v:
                raise ValueError("token_identity_drift")

    print("E01/E02/E03/E04/E05 protected files, schemas, and resolved dossiers verified; new_calls=0")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["prepare", "confirm", "verify"])
    a = ap.parse_args()
    with (HERE / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.action == "prepare":
            prepare()
        elif a.action == "confirm":
            freeze_and_confirm()
        else:
            verify()


if __name__ == "__main__":
    main()
