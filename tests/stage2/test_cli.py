import io
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
from extractube import cli, store
from extractube.store import read, write
from extractube.pipeline import VisionResult
from support import seed, adapters, settings, FakeTransport, VIDEO_ID

class CliTests(unittest.TestCase):
    def test_stage1_default_uses_existing_pipeline(self):
        self.assertEqual(store.STAGES,('video','wav','asr','fa','sentences'))
        from types import SimpleNamespace
        result=SimpleNamespace(stages={},empty_sentences=True,asr_requests=0,fa_requests=0,work_dir=Path('/fixture'),audio_to_video_offset_seconds=0,vision=None)
        with patch('extractube.cli.run',return_value=result) as call,redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['run','https://example.test']),0)
            self.assertEqual(call.call_args.kwargs['through'],'stage1')
    def test_stage2_verify_and_resume_cli_uses_saved_defaults(self):
        from extractube.vision import run
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=seed(root);fake=FakeTransport();small=replace(settings(),max_inference_calls=2,timeout_seconds=2)
            with adapters(fake),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                paused=run(VIDEO_ID,small,data_root=root,log=None)
                result=cli.main(['stage2',VIDEO_ID,'--data-root',str(root),'--resume',paused.run_id,'--max-inference-calls','30'])
                self.assertEqual(result,0)
                self.assertEqual(read(work/f'vision/runs/{paused.run_id}/config.json')['settings']['timeout_seconds'],2)
                self.assertEqual(cli.main(['stage2-verify',VIDEO_ID,'--data-root',str(root)]),0)
    def test_run_through_stage2_uses_continuous_pipeline(self):
        from extractube.settings import Settings
        from extractube import pipeline
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = seed(root)
            fake = FakeTransport()
            with adapters(fake), patch.object(pipeline, '_reusable', return_value=True), patch('extractube.media.resolve_video_id', return_value=VIDEO_ID), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = cli.main(['run', 'https://example.test', '--through', 'stage2', '--data-root', str(root)])
            self.assertEqual(result, 0)
            self.assertEqual(len(fake.calls), 7)
            self.assertTrue((work / 'vision/current.json').is_file())

    def test_invalid_settings_exit_two(self):
        with redirect_stderr(io.StringIO()):self.assertEqual(cli.main(['stage2',VIDEO_ID,'--timeout','nan']),2)
