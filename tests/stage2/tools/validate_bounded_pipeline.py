"""Bounded real-media Stage2 closeout. Source experiments and product policy stay unchanged."""

import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import subprocess
import uuid
from extractube.vision import run
from extractube.handoff import verify
from extractube.store import read, write, sha, result_record, identity
from extractube.settings import VisionSettings
from extractube.visual_policy import snapshot
from extractube.visual_input import read_audio_start

ROOT = Path(__file__).resolve().parents[3]
SOURCE_ID = 'Fw-8hZhnH3Q'
VIDEO_ID = 'Bounded0001'
START = Decimal('52.6')
END = Decimal('59.2')
CHUNKS = (10, 11)
SETTINGS = VisionSettings(max_inference_calls=12, max_http_seconds=600)


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])
    return result.stdout


def frame_hashes(path, original=False):
    args = ['ffmpeg', '-v', 'error', '-i', str(path)]
    if original:
        args += ['-ss', str(START), '-t', str(END-START)]
    args += ['-map', '0:v:0', '-an', '-pix_fmt', 'rgb24', '-fps_mode', 'passthrough', '-f', 'framemd5', '-']
    return [line.split(',')[-1].strip() for line in command(args).decode().splitlines() if line and not line.startswith('#')]


def prepare():
    token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    base = ROOT/'tests/stage2/live'/('bounded_'+token)
    work = base/'data'/VIDEO_ID
    (work/'source').mkdir(parents=True)
    (work/'audio').mkdir()
    source = ROOT/'data'/SOURCE_ID
    meta = read(source/'metadata.json')
    source_video = source/meta['results']['video']['path']
    source_sentences = source/meta['results']['sentences']['path']
    assert sha(source_video) == meta['results']['video']['sha256']
    assert sha(source_sentences) == meta['results']['sentences']['sha256']
    video = work/'source/video.avi'
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(START), '-i', str(source_video), '-t', str(END-START), '-map', '0:v:0', '-map', '0:a:0', '-vf', 'format=rgb24', '-c:v', 'ffv1', '-pix_fmt', 'bgr0', '-level', '3', '-c:a', 'pcm_s16le', '-f', 'avi', str(video)]
    command(args)
    original_hashes, derived_hashes = frame_hashes(source_video, True), frame_hashes(video)
    assert original_hashes == derived_hashes and len(derived_hashes) == 198, 'decoded RGB frames changed'
    video_probe = __import__('json').loads(command(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(video)]))
    video_stream = next(s for s in video_probe['streams'] if s['codec_type'] == 'video')
    video_start = Decimal(video_stream['start_time'])
    audio_start = read_audio_start('ffprobe', video)
    assert video_start == audio_start == 0
    original = read(source_sentences)
    selected = [c for c in original['chunks'] if c['index'] in CHUNKS]
    assert [c['index'] for c in selected] == list(CHUNKS)
    sentences = copy.deepcopy(original)
    sentences['sentence_policy_id'] += '.bounded_timestamp_projection_v1'
    sentences['chunks'] = copy.deepcopy(selected)
    sentences['omitted_chunks'] = []
    sentences['audio']['num_samples'] = int((END-START)*sentences['audio']['sample_rate'])
    for chunk in sentences['chunks']:
        for item in chunk['items']:
            item['start_time'] = float(Decimal(str(item['start_time']))-START)
            item['end_time'] = float(Decimal(str(item['end_time']))-START)
        for key in ('start_sample','end_sample'):
            if key in chunk:
                chunk[key] -= int(START*sentences['audio']['sample_rate'])
    sentence_file = work/'audio/alignment_sentences.json'
    write(sentence_file, sentences, immutable=True)
    wav = work/'audio/mono.wav'
    command(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(video), '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(wav)])
    binding = {'scope':'bounded_real_video_fixture', 'source_video_id':SOURCE_ID, 'source_video':meta['results']['video'], 'source_sentences':meta['results']['sentences'], 'source_range_seconds':[str(START),str(END)], 'source_chunk_indices':list(CHUNKS), 'text_unchanged':True, 'timestamp_transform':'fixture_seconds = original_seconds - 52.6', 'stage1_asr_fa_rerun':False, 'derived_stage1_records':'fixture projections of existing alignment; not fresh ASR/FA results', 'decoded_rgb_frames_identical':True, 'frames':len(derived_hashes), 'ffmpeg_command':args, 'video_probe':video_probe, 'original_frame_rgb_md5':original_hashes, 'derived_frame_rgb_md5':derived_hashes}
    write(work/'source_binding.json', binding, immutable=True)
    records = {}
    paths = {'video':'source/video.avi', 'wav':'audio/mono.wav', 'asr':'audio/asr_projection.json', 'fa':'audio/fa_projection.json', 'sentences':'audio/alignment_sentences.json'}
    chain = {'video':(), 'wav':('video',), 'asr':('wav',), 'fa':('wav','asr'), 'sentences':('fa',)}
    media = {'video_start_time_seconds':0, 'source_audio_start_time_seconds':0, 'audio_to_video_offset_seconds':0}
    for kind, parents in chain.items():
        path = work/paths[kind]
        if kind in ('asr','fa'):
            write(path, {'kind':'bounded_alignment_projection','source':meta['results'][kind], 'binding_sha256':sha(work/'source_binding.json'), 'chunks':sentences['chunks']}, immutable=True)
        records[kind] = result_record(kind=kind, relative_path=paths[kind], file_hash=sha(path), upstream=[identity(records[p]) for p in parents], settings={'fixture':'bounded_real_media_v1'}, media=media if kind=='video' else None)
    write(work/'metadata.json', {'video_id':VIDEO_ID, 'source_url':meta.get('source_url'), 'results':records, **media}, immutable=True)
    plan = {'scope':'bounded_real_video_fixture', 'original_full_video_completed':False, 'data_root':str(base/'data'), 'video_id':VIDEO_ID, 'source_binding':str(work/'source_binding.json'), 'sentences':len(selected), 'duration_seconds':str(END-START), 'limits':{'max_calls':12,'max_http_seconds':600,'max_t3_samples':24}, 'settings':snapshot(SETTINGS), 'old_full_run_state_sha256':sha(ROOT/f'data/{SOURCE_ID}/vision/runs/20261029T024137Z_dd88367570a1/state.json'), 'product_files':{str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'src/extractube').rglob('*.py'))}}
    write(base/'bounded_plan.json', plan, immutable=True)
    print('BOUNDED_PLAN='+str(base/'bounded_plan.json'), flush=True)
    print('prepared: 6.6 seconds, 2 original sentences, 198 lossless frames; max 12 calls/600 HTTP seconds', flush=True)


def execute(plan_path):
    plan_path = Path(plan_path)
    plan = read(plan_path)
    assert plan['settings'] == snapshot(SETTINGS), 'frozen code/settings changed'
    base = plan_path.parent
    assert not (base/'closeout.json').exists(), 'already closed'
    result = run(VIDEO_ID, SETTINGS, data_root=Path(plan['data_root']), log=lambda s:print(s,flush=True))
    work = result.work_dir
    if not result.stage3_usable:
        write(base/'stopped.json', {'status':result.execution_status,'run_id':result.run_id,'new_calls':result.inference_calls}, immutable=True)
        raise RuntimeError('budget/unknown stop: no automatic expansion or retry')
    export = read(result.handoff_path)
    assert export['coverage']['t3_samples'] <= 24
    assert export['coverage']['total_sentences'] == 2
    expected = [c['text'] for c in read(work/'audio/alignment_sentences.json')['chunks']]
    assert [s['text'] for u in export['units'] for s in u['sentences']] == expected
    assert export['coverage']['selected_assets'] > 0, 'empty asset success is insufficient for this fixture'
    current_before = (work/'vision/current.json').read_bytes()
    verify_result = verify(VIDEO_ID, data_root=Path(plan['data_root']))
    assert verify_result['ok']
    again = run(VIDEO_ID, SETTINGS, data_root=Path(plan['data_root']), log=lambda s:print(s,flush=True))
    assert again.inference_calls == 0 and again.run_id == result.run_id
    assert (work/'vision/current.json').read_bytes() == current_before
    assert plan['old_full_run_state_sha256'] == sha(ROOT/f'data/{SOURCE_ID}/vision/runs/20261029T024137Z_dd88367570a1/state.json')
    assert all(sha(ROOT/p)==value for p,value in plan['product_files'].items())
    write(base/'closeout.json', {'scope':plan['scope'], 'execution_status':result.execution_status, 'run_id':result.run_id, 'handoff':str(result.handoff_path), 'html':str(result.handoff_path.with_name('review.html')), 'coverage':export['coverage'], 'budget':read(result.handoff_path.parent/'result.json')['budget'], 'verify':verify_result, 'repeat_new_calls':again.inference_calls, 'repeat_same_run':True, 'current_bytes_unchanged':True, 'source_text_preserved':True, 'source_pixels_preserved':True, 'original_full_run_unchanged':True, 'product_code_unchanged':True, 'original_full_video_completed':False, 'quality_review_performed':False}, immutable=True)
    print('BOUNDED_CLOSEOUT='+str(base/'closeout.json'),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute-plan')
    args = parser.parse_args()
    if args.execute_plan: execute(args.execute_plan)
    else: prepare()


