"""Offline unit & adversarial tests for E05 source-preserving T3 plan & dossier compiler (0 inference calls)."""
import copy
import unittest

import run_t3 as t
import run_e05_plan as e05


class TestE05Plan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t.bound_sha(e05.SCHEMA_PATH, e05.SCHEMA_SHA)
        t.bound_sha(e05.DESIGN_PATH, e05.DESIGN_SHA)
        t.bound_sha(e05.E04_REVIEW_PATH, e05.E04_REVIEW_SHA)
        cls.schema = t.read(e05.SCHEMA_PATH)
        cls.t3_inp = t.read(t.INPUT)
        t2_inp_path = t.PARENT / "input.json"
        cls.confirm_sentences = t.read(t2_inp_path)["fresh_sentences"]
        cls.confirm_struct_ref = cls.t3_inp["validation_only"]["confirmation_structure_ref"]
        cls.confirm_struct = t.read(e05.ROOT / cls.confirm_struct_ref["path"])

        cls.bundle = e05.build_source_bundle(
            cls.t3_inp,
            cls.confirm_sentences,
            {
                "path": e05.relpath(t2_inp_path),
                "sha256": t.sha(t2_inp_path),
                "json_pointer": "/fresh_sentences",
            },
        )
        cls.bundle_sha = t.digest(cls.bundle)
        cls.bundle_virtual_path = "virtual/e05/source_bundle.json"

        cls.p_cautions = e05.primary_caution_descriptors()
        cls.primary_plan = e05.build_plan(
            video_id="Fw-8hZhnH3Q",
            unit_prefix="p",
            target_range=[0, 217],
            source_bundle_ref={
                "path": cls.bundle_virtual_path,
                "sha256": cls.bundle_sha,
                "json_pointer": "/videos/Fw-8hZhnH3Q/sentences",
            },
            structure_ref={
                "path": e05.relpath(t.INPUT),
                "sha256": t.INPUT_SHA,
                "json_pointer": "/primary_structure",
            },
            structure_obj=cls.t3_inp["primary_structure"],
            caution_descriptors=cls.p_cautions,
            connections=cls.t3_inp["connections"],
            assets=cls.t3_inp["primary_assets"],
        )
        cls.confirm_plan = e05.build_plan(
            video_id="H2WN130OzLg",
            unit_prefix="c",
            target_range=[134, 192],
            source_bundle_ref={
                "path": cls.bundle_virtual_path,
                "sha256": cls.bundle_sha,
                "json_pointer": "/videos/H2WN130OzLg/sentences",
            },
            structure_ref={
                "path": cls.confirm_struct_ref["path"],
                "sha256": cls.confirm_struct_ref["sha256"],
                "json_pointer": "",
            },
            structure_obj=cls.confirm_struct,
            caution_descriptors=[],
            connections=[],
            assets=[],
        )

    def _loader(self, overrides=None):
        overrides = overrides or {}

        def load(ref):
            p = ref["path"]
            if p in overrides:
                doc, expected_sha = overrides[p]
                if ref["sha256"] != expected_sha:
                    raise ValueError("sha256_mismatch:" + p)
                return doc
            if p == self.bundle_virtual_path:
                if ref["sha256"] != self.bundle_sha:
                    raise ValueError("sha256_mismatch:bundle")
                return self.bundle
            full = e05.ROOT / p
            t.bound_sha(full, ref["sha256"])
            return t.read(full)

        return load

    def _resolve_primary(self, plan=None, **kwargs):
        p = self.primary_plan if plan is None else plan
        defaults = dict(
            schema_obj=self.schema,
            policy_catalog=e05.POLICY_CATALOG,
            expected_video_id="Fw-8hZhnH3Q",
            expected_range=[0, 217],
            expected_caution_refs=[x["ref"] for x in self.p_cautions],
            upstream_connections=self.t3_inp["connections"],
            upstream_assets=self.t3_inp["primary_assets"],
            file_loader=self._loader(),
        )
        defaults.update(kwargs)
        return e05.resolve_and_validate_plan(p, **defaults)

    def _resolve_confirm(self, plan=None, **kwargs):
        p = self.confirm_plan if plan is None else plan
        defaults = dict(
            schema_obj=self.schema,
            policy_catalog=e05.POLICY_CATALOG,
            expected_video_id="H2WN130OzLg",
            expected_range=[134, 192],
            expected_caution_refs=[],
            upstream_connections=[],
            upstream_assets=[],
            file_loader=self._loader(),
        )
        defaults.update(kwargs)
        return e05.resolve_and_validate_plan(p, **defaults)

    def test_real_primary_and_confirmation_compile_and_resolve(self):
        p_res = self._resolve_primary()
        c_res = self._resolve_confirm()
        self.assertEqual(len(p_res["units"]), 16)
        self.assertEqual(len(c_res["units"]), 7)
        self.assertEqual(p_res["sentence_count"], 217)
        self.assertEqual(c_res["sentence_count"], 58)
        self.assertTrue(e05.assert_bundle_isolation(p_res, c_res))

        # Verify all 5 explicit primary connections split cleanly into 1 internal + 4 cross-leaf previous_context
        u_by_id = {u["unit_id"]: u for u in p_res["units"]}
        self.assertEqual(
            u_by_id["p_u03"]["internal_source_links"][0]["from_sentence_index"], 33
        )
        self.assertEqual(
            u_by_id["p_u03"]["internal_source_links"][0]["to_sentence_indices"], [30]
        )
        self.assertEqual(u_by_id["p_u04"]["previous_context"]["previous_context_indices"], [6, 7])
        self.assertEqual(u_by_id["p_u07"]["previous_context"]["previous_context_indices"], [6])
        self.assertEqual(u_by_id["p_u09"]["previous_context"]["previous_context_indices"], [40, 41])
        self.assertEqual(u_by_id["p_u14"]["previous_context"]["previous_context_indices"], [183, 184, 185])

        # Verify V01 deferred on [0, 40) leaves (p_u00..p_u03) and skipped elsewhere
        for i in range(16):
            uid = f"p_u{i:02d}"
            if i < 4:
                self.assertEqual(u_by_id[uid]["assets"]["asset_ids"], ["V01"])
                self.assertEqual(u_by_id[uid]["assets"]["asset_action"], "defer")
            else:
                self.assertEqual(u_by_id[uid]["assets"]["asset_ids"], [])
                self.assertEqual(u_by_id[uid]["assets"]["asset_action"], "skip")

        # Verify literal 134 & 135 preservation and absence of derived keep/delete classification
        u10 = u_by_id["p_u10"]
        s_map = {s["sentence_index"]: s["text"] for s in u10["required_source_sentences"]}
        self.assertEqual(s_map[134], "기구.")
        self.assertEqual(
            s_map[135],
            "뚜렷봉 액체 아니죠, 싱크대 아니죠, 액체 아니죠, 압력 될 수 있겠죠, 뚜렷봉 미니 뚜렷봉 될 수 있고, 욕실 뚜렷봉 액체 아니니까 이런 것들은 다 지워주세요.",
        )
        m01_cautions = [c for c in u10["resolved_cautions"] if c["sentence_indices"] == [134, 135]]
        self.assertEqual(len(m01_cautions), 1)
        self.assertTrue(m01_cautions[0]["faulty_prior_plan_prose_excluded"])
        self.assertIsNone(m01_cautions[0]["derived_classification"])

    def test_wrong_source_or_structure_hash_rejected(self):
        bad_bundle = copy.deepcopy(self.primary_plan)
        bad_bundle["source_bundle_ref"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self._resolve_primary(bad_bundle)

        bad_struct = copy.deepcopy(self.primary_plan)
        bad_struct["structure_ref"]["sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            self._resolve_primary(bad_struct)

        tampered_bundle = copy.deepcopy(self.bundle)
        tampered_bundle["videos"]["Fw-8hZhnH3Q"]["sentences"][59]["text"] = "임의 교정된 문장"
        tampered_sha = t.digest(tampered_bundle)
        tampered_plan = copy.deepcopy(self.primary_plan)
        tampered_plan["source_bundle_ref"]["sha256"] = tampered_sha
        with self.assertRaises(ValueError):
            self._resolve_primary(
                tampered_plan,
                file_loader=self._loader({self.bundle_virtual_path: (tampered_bundle, tampered_sha)}),
            )

    def test_empty_gap_reorder_or_duplicate_responsibility_rejected(self):
        # Empty required_source_indices
        empty_p = copy.deepcopy(self.primary_plan)
        empty_p["units"][0]["required_source_indices"] = []
        with self.assertRaises(ValueError):
            self._resolve_primary(empty_p)

        # Omitted sentence (e.g. dropping transition sentence 39)
        omitted_p = copy.deepcopy(self.primary_plan)
        omitted_p["units"][3]["required_source_indices"].remove(39)
        with self.assertRaises(ValueError):
            self._resolve_primary(omitted_p)

        # Reordered indices
        reordered_p = copy.deepcopy(self.primary_plan)
        reordered_p["units"][0]["required_source_indices"] = [0, 2, 1, 3, 4]
        with self.assertRaises(ValueError):
            self._resolve_primary(reordered_p)

        # Duplicate responsibility across units
        dup_p = copy.deepcopy(self.primary_plan)
        dup_p["units"][1]["source_range"]["sentence_start"] = 4
        dup_p["units"][1]["required_source_indices"] = list(range(4, 15))
        with self.assertRaises(ValueError):
            self._resolve_primary(dup_p)

    def test_forward_context_and_invalid_internal_links_rejected(self):
        # Forward context (index inside or after current unit)
        fwd_p = copy.deepcopy(self.primary_plan)
        fwd_p["units"][4]["previous_context_indices"] = [6, 7, 40]
        with self.assertRaises(ValueError):
            self._resolve_primary(fwd_p)

        fwd_later = copy.deepcopy(self.primary_plan)
        fwd_later["units"][4]["previous_context_indices"] = [6, 7, 120]
        with self.assertRaises(ValueError):
            self._resolve_primary(fwd_later)

        # Forward internal link (to >= from)
        bad_int = copy.deepcopy(self.primary_plan)
        bad_int["units"][3]["internal_source_links"] = [{"from_sentence_index": 30, "to_sentence_indices": [33]}]
        with self.assertRaises(ValueError):
            self._resolve_primary(bad_int)

        # Unbacked fabricated internal link
        fab_int = copy.deepcopy(self.primary_plan)
        fab_int["units"][3]["internal_source_links"].append({"from_sentence_index": 25, "to_sentence_indices": [20]})
        with self.assertRaises(ValueError):
            self._resolve_primary(fab_int)

        # Dropped prior connection (e.g., removing 183..185 on p_u14)
        drop_prev = copy.deepcopy(self.primary_plan)
        drop_prev["units"][14]["previous_context_indices"] = []
        with self.assertRaises(ValueError):
            self._resolve_primary(drop_prev)

    def test_missing_or_misplaced_caution_rejected(self):
        # Missing caution on sentence 59
        missing_c = copy.deepcopy(self.primary_plan)
        missing_c["units"][5]["caution_refs"].pop()
        with self.assertRaises(ValueError):
            self._resolve_primary(missing_c)

        # Misplaced caution (put p_u05's sentence 59 caution onto p_u06 [66, 87))
        misplaced_c = copy.deepcopy(self.primary_plan)
        cref = misplaced_c["units"][5]["caution_refs"].pop()
        misplaced_c["units"][6]["caution_refs"].append(cref)
        with self.assertRaises(ValueError):
            self._resolve_primary(misplaced_c)

    def test_invalid_policy_id_or_schema_violations_rejected(self):
        bad_pol = copy.deepcopy(self.primary_plan)
        bad_pol["units"][0]["writer_constraint_ids"][0] = "invent_missing_facts"
        with self.assertRaises(ValueError):
            self._resolve_primary(bad_pol)

        extra_field = copy.deepcopy(self.primary_plan)
        extra_field["units"][10]["instruction"] = "임의 요약 지시"
        with self.assertRaises(ValueError):
            self._resolve_primary(extra_field)

        restatement_true = copy.deepcopy(self.primary_plan)
        restatement_true["model_fact_restatement"] = True
        with self.assertRaises(ValueError):
            self._resolve_primary(restatement_true)

        bool_as_int = copy.deepcopy(self.primary_plan)
        bool_as_int["units"][0]["required_source_indices"][1] = True
        with self.assertRaises(ValueError):
            self._resolve_primary(bool_as_int)

    def test_fake_visual_evidence_or_unapproved_asset_use_rejected(self):
        fake_vis = copy.deepcopy(self.primary_plan)
        fake_vis["new_visual_evidence"] = [{"asset_id": "V01", "ocr": "가짜 판독"}]
        with self.assertRaises(ValueError):
            self._resolve_primary(fake_vis)

        fake_asset = copy.deepcopy(self.primary_plan)
        fake_asset["units"][0]["asset_ids"] = ["V99"]
        with self.assertRaises(ValueError):
            self._resolve_primary(fake_asset)

        unapproved_use = copy.deepcopy(self.primary_plan)
        unapproved_use["units"][1]["asset_action"] = "use_already_reviewed_only"
        with self.assertRaises(ValueError):
            self._resolve_primary(unapproved_use)

        out_of_range_asset = copy.deepcopy(self.primary_plan)
        out_of_range_asset["units"][5]["asset_ids"] = ["V01"]
        out_of_range_asset["units"][5]["asset_action"] = "defer"
        with self.assertRaises(ValueError):
            self._resolve_primary(out_of_range_asset)

    def test_resolved_text_tampering_and_composite_135_truncation_rejected(self):
        def truncate_135(uid, rows):
            if uid == "p_u10":
                for r in rows:
                    if r["sentence_index"] == 135:
                        r["text"] = r["text"].replace("욕실 뚜렷봉 액체 아니니까", "욕실 될 수 있고")

        with self.assertRaises(ValueError):
            self._resolve_primary(resolved_sentence_mutator=truncate_135)

        bad_catalog = copy.deepcopy(e05.POLICY_CATALOG)
        bad_catalog["constraints"]["preserve_compound_scope_and_negation"]["caution_directive"] = (
            "기구를 삭제 대상으로 하고 욕실만 분리해 유지한다."
        )
        with self.assertRaises(ValueError):
            self._resolve_primary(policy_catalog=bad_catalog)

    def test_cross_video_contamination_rejected(self):
        p_res = self._resolve_primary()
        c_res = self._resolve_confirm()

        contaminated = copy.deepcopy(c_res)
        contaminated["units"][0]["assets"]["asset_ids"] = ["V01"]
        with self.assertRaises(ValueError):
            e05.assert_bundle_isolation(p_res, contaminated)

        contaminated2 = copy.deepcopy(c_res)
        contaminated2["units"][0]["required_source_sentences"][0]["source_ref"]["path"] = (
            "data/Fw-8hZhnH3Q/audio/alignment_sentences.json"
        )
        with self.assertRaises(ValueError):
            e05.assert_bundle_isolation(p_res, contaminated2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
