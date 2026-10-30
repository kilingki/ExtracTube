"""Consumer safeguards for the image-only handoff; no model calls."""

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path

import copy
import unittest
import review as v

class HandoffSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pointer=v.s.read(v.POINTER)
        cls.folder=v.s.resolve_path(v.ROOT/pointer["run"])
        cls.doc=v.s.read(cls.folder/"stage3_handoff.json")

    def changed(self):
        return copy.deepcopy(self.doc)

    def test_retired_paths_resolve_without_changing_records(self):
        for name, target in v.s.MOVED.items():
            old = v.ROOT / "tests/T5" / name
            current = v.ROOT / "tests/stage2/T5" / target
            self.assertEqual(v.s.resolve_path(old), current)
        path = v.ROOT / "tests/stage2/T4/runs/source.json"
        self.assertEqual(v.s.resolve_path(path), path)
        index = v.s.read(v.ROOT / "tests/T5/legacy/index.json")
        archive = v.ROOT / "tests/T5/legacy/sources.tar.gz"
        self.assertEqual(v.s.sha(archive), index["archive_sha256"])

    def test_current_package(self):
        self.assertEqual(v.validate(self.doc,self.folder),[])

    def test_unverified_ocr_cannot_become_fact(self):
        doc=self.changed();doc["assets"][0]["verified_text"]=["guessed number"]
        self.assertTrue(v.validate(doc,self.folder))

    def test_wrong_unit_link_is_rejected(self):
        doc=self.changed();doc["assets"][0]["unit_id"]="wrong:u0"
        self.assertTrue(v.validate(doc,self.folder))

    def test_unreviewed_coverage_cannot_be_claimed(self):
        doc=self.changed()
        next(u for u in doc["semantic_units"] if u["visual_status"]=="not_assessed")["visual_status"]="reviewed_omitted"
        self.assertTrue(v.validate(doc,self.folder))

    def test_changed_source_time_is_rejected(self):
        doc=self.changed();doc["assets"][0]["source"]["pts"]+=1
        self.assertTrue(v.validate(doc,self.folder))

    def test_rejected_image_cannot_be_exported(self):
        doc=self.changed();doc["assets"][0]["frame_id"]=next(d["frame_id"] for d in v.s.read(v.REVIEW)["frames"] if d["decision"]=="reject")
        self.assertTrue(v.validate(doc,self.folder))

    def test_wrong_asset_bytes_are_rejected(self):
        doc=self.changed();doc["assets"][0]["path"]=doc["assets"][1]["path"]
        self.assertTrue(v.validate(doc,self.folder))

if __name__=="__main__":
    unittest.main()
