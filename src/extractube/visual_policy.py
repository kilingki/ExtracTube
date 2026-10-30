"""Fixed image-first policy and narrowly projected dependency configuration."""

import platform
from dataclasses import asdict
from pathlib import Path
from .store import digest, sha

POLICY = 'extractube.stage2.image_first.v1'
STAGES = ('t1', 't2', 't3', 't4', 't5')
POLICIES = {'sentences':'stage1_projection.v1','t1':'t1.e01.p1.v1','trace':'t1.e01.p1.v1','t2':'t2.budget_guard.v1','t3':'t3.screen_changes.tile_or.grid_support.v1','t4':'t4.a03.p1.v1','frames':'t4.a03.p1.v1','t5':'t5.image_first.v1'}
SUFFIXES = {'sentences':'sentences','t1':'semantic_units','trace':'semantic_trace','t2':'visual_candidates','t3':'screen_changes','t4':'selection','frames':'frames','t5':'visual_assets'}


def snapshot(settings):
    import PIL
    from . import prompts
    from . import screen_changes as changes
    codes = {name + '.py': sha(Path(__file__).parent / (name + '.py')) for name in ['_frame_extract', '_screen_change', 'assets', 'candidates', 'errors', 'frames', 'handoff', 'infer', 'media', 'pipeline', 'prompts', 'screen_changes', 'selection', 'semantic', 'settings', 'store', 'timestamps', 'vision', 'visual_input', 'visual_policy']}
    common = {k:codes[k] for k in ('visual_input.py','timestamps.py','errors.py','pipeline.py','store.py','settings.py')}
    modules = {'t1':['semantic.py','infer.py','prompts.py'], 't2':['candidates.py','infer.py','prompts.py'], 't3':['screen_changes.py','_screen_change.py'], 't4':['selection.py','frames.py','_frame_extract.py','infer.py','prompts.py'], 't5':['assets.py','selection.py','infer.py','prompts.py']}
    versions = {'python':platform.python_version(),'pillow':PIL.__version__, 'ffmpeg':changes.tool_identity(settings.ffmpeg)['version_line'],'ffprobe':changes.tool_identity(settings.ffprobe)['version_line']}
    projections = {stage:{'code':{**common, **{m:codes[m] for m in files}}, 'model':settings.model_profile() if stage != 't3' else None, 'policy':POLICIES[stage], 'versions':versions if stage in ('t3','t4') else {}} for stage,files in modules.items()}
    return {'schema_version':'extractube.stage2.config.v1','policy_id':POLICY,'settings':asdict(settings),'projections':projections,'prompts':{k:{'text':getattr(prompts,k),'sha256':digest_text(getattr(prompts,k))} for k in prompts.NAMES},'cache_epoch':settings.cache_epoch,'code_sha256':codes,'versions':versions}

def digest_text(text):
    import hashlib
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def envelope(ctx, stage, payload, warnings=None, status=None):
    warnings = warnings or []
    return {'schema_version':f'extractube.stage2.{SUFFIXES[stage]}.v1','run_id':ctx.run_id,'video_id':ctx.video_id,'policy_id':POLICIES[stage],'input_digest':ctx.input['input_digest'],'config_sha256':ctx.config_sha,'execution_status':status or ('ok_with_warnings' if warnings else 'ok'),'warnings':warnings,'payload':payload}

def warning(code, stage, subject=None, detail=None):
    return {'code':code,'stage':stage,'subject_id':subject,'detail':detail or code}
