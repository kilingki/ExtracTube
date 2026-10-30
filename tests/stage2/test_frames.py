import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
from fractions import Fraction
from extractube import screen_changes as changes, frames
from extractube.store import HashCache, sha, digest, write
from extractube.timestamps import seconds, span, actual
from extractube.settings import VisionSettings

class FFmpegTests(unittest.TestCase):
    def make(self,work,variant):
        path=work/'source.mp4'
        expression='setpts=PTS+10/TB' if variant=='nonzero' else ('select=not(mod(n\\,3))+not(mod(n\\,7))' if variant=='vfr' else 'null')
        command=['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc=size=160x90:rate=10:duration=3','-vf',expression,'-fps_mode','vfr','-c:v','libx264','-pix_fmt','yuv420p',str(path)]
        subprocess.run(command,check=True,capture_output=True)
        stream=changes.probe_video('ffprobe',path);stream.pop('time_base_fraction');stream.pop('args');stream.pop('elapsed_seconds');stream=changes.plain(stream)
        settings=VisionSettings();directory=work/'vision/runs/fixture';directory.mkdir(parents=True)
        config={'versions':{'ffmpeg':changes.tool_identity('ffmpeg')['version_line']}}
        ctx=SimpleNamespace(work=work,directory=directory,run_id='fixture',video_id='Fixture1234',settings=settings,config=config,input={'stream':stream,'video_ref':{'path':'source.mp4','sha256':sha(path)},'stage1_identities':{'video':{'kind':'video','result_id':'fixture','sha256':sha(path)}}},video_path=path,hashes=HashCache(),folder=lambda c:'u000000_c000000')
        return ctx
    def test_cfr_vfr_nonzero_start_and_exact_rgb_roundtrip(self):
        for variant in ('cfr','vfr','nonzero'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as temp:
                ctx=self.make(Path(temp),variant)
                if variant=='nonzero':self.assertGreater(Decimal(ctx.input['stream']['start_time']),0)
                last=changes.confirm_end('ffmpeg',ctx.video_path,ctx.input['stream']);self.assertLess(Decimal(last['last_video_seconds']),Decimal(ctx.input['stream']['duration']))
                c={'candidate_id':'Fixture1234:u0:c0','effective_video_range':span('.25','2.25','video_start'),'requested_video_range':span('.25','2.25','video_start')}
                p=changes.detect(ctx,c)
                self.assertTrue(p['samples']);self.assertEqual(p['differences'],changes.replay(p));self.assertEqual(p['samples'][-1]['roles'][-1],'tail')
                self.assertEqual(sorted(i for g in p['segments'] for i in g['sample_indices']),list(range(len(p['samples']))))
                result=frames.extract(ctx,p['samples'])
                self.assertEqual(set(result),{s['frame_id'] for s in p['samples']})
                for fid,f in result.items():
                    self.assertEqual(f['video_seconds'],seconds(actual(f['pts'],f['time_base'],ctx.input['stream']['start_time'])))
                    self.assertEqual(f['full']['width'],160);frames.check_image(ctx,f['full']);frames.check_image(ctx,f['vlm'])
                again=frames.extract(ctx,p['samples']);self.assertEqual({k:v['full'] for k,v in result.items()},{k:v['full'] for k,v in again.items()})
    def test_corrupt_png_and_time_base_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'broken.png';p.write_bytes(b'not png')
            with self.assertRaises(Exception):frames.image_info(p)
        from extractube._frame_extract import _accept_time_base
        self.assertIsNotNone(_accept_time_base({'time_base_in':(1,2),'time_base_out':None},(1,3)))
