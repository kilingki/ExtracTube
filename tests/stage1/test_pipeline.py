"""Stage1 local regression suite; live YouTube/ASR/FA is opt-in in run_stage1.py."""
import runpy
import tempfile
import unittest
from pathlib import Path


class Stage1Regression(unittest.TestCase):
    def test_existing_local_contracts(self):
        source = Path(__file__).with_name("run_stage1.py")
        tests = runpy.run_path(str(source))
        for name in ("test_alignment_validation", "test_sentences", "test_video_id", "test_cli_usage"):
            tests[name]()
        with tempfile.TemporaryDirectory(prefix="extractube-stage1-local-") as tmp:
            root = Path(tmp)
            tests["test_commit_crash"](root)
            tests["test_lock"](root)
            for name, folder in (("test_pipeline", "pipe"), ("test_redirect_and_body_limit", "http"), ("test_long_chunk", "long"), ("test_empty_transcript", "empty")):
                tests[name](root / folder)
        self.assertEqual(len(tests["CHECKS"]), 41)
        self.assertTrue(all(row["ok"] for row in tests["CHECKS"]))
