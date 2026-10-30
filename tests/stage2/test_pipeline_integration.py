"""Continuous pipeline, lock scopes, and upstream handoff identity."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from extractube import pipeline, store, vision
from extractube.errors import VisionError
from extractube.settings import Settings
from support import seed, adapters, settings, FakeTransport, VIDEO_ID

class PipelineIntegration(unittest.TestCase):
    def test_stage1_continues_to_visual_with_released_lock_and_exact_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed(root)
            fake = FakeTransport()
            with adapters(fake), patch.object(pipeline, "_reusable", return_value=True):
                result = pipeline.run("https://example.test", Settings(data_root=root), video_id=VIDEO_ID, through="stage2", vision_settings=settings(), log=None)
                self.assertTrue(result.vision.stage3_usable)
                self.assertEqual(len(fake.calls), 7)
                self.assertEqual(tuple(result.stages), store.STAGES)
                again = pipeline.run("https://example.test", Settings(data_root=root), video_id=VIDEO_ID, through="stage2", vision_settings=settings(), log=None)
                self.assertEqual(again.vision.inference_calls, 0)
                self.assertEqual(again.vision.run_id, result.vision.run_id)
                self.assertEqual(len(fake.calls), 7)

    def test_stage1_default_does_not_enter_visual_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed(root)
            with patch.object(pipeline, "_reusable", return_value=True), patch.object(vision, "run") as called:
                result = pipeline.run("https://example.test", Settings(data_root=root), video_id=VIDEO_ID, log=None)
            self.assertIsNone(result.vision)
            called.assert_not_called()

    def test_changed_stage1_identity_is_rejected_before_visual_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = seed(root)
            fake = FakeTransport()
            original = vision.run
            def changed(*args, **kwargs):
                meta = store.read(work / "metadata.json")
                meta["results"]["sentences"]["result_id"] += "-changed"
                store.write(work / "metadata.json", meta)
                return original(*args, **kwargs)
            with adapters(fake), patch.object(pipeline, "_reusable", return_value=True), patch.object(vision, "run", side_effect=changed):
                with self.assertRaises(VisionError):
                    pipeline.run("https://example.test", Settings(data_root=root), video_id=VIDEO_ID, through="stage2", vision_settings=settings(), log=None)
            self.assertEqual(fake.calls, [])
