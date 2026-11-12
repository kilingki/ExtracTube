"""Exact-PTS original PNG extraction, immutable image sharing and in-process VLM resizing."""

import io
import shutil
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path
import PIL
from PIL import Image
from . import _frame_extract as _rgb
from .store import safe, sha, ref, write, read, digest
from .timestamps import decimal, seconds, actual
from .errors import VisionError

EXTRACT_POLICY = {
    'policy': 'rgb_exact_pts.v1',
    'pixel_format': 'rgb24',
    'rotation': 'ffmpeg_default_display',
    'clusters': {'gap': '5', 'limit': 40},
    'prerolls': ['1', '2', '4', '8', 'actual'],
}
PROBE_POLICY = {'policy': 'midpoint_vlm_probe.v1', 'max_side': 1280, 'pixel_format': 'rgb24'}
_VERIFIED_IMAGES = {}


def image_info(path):
    data = Path(path).read_bytes()
    info = _rgb.png_info(data)
    with Image.open(io.BytesIO(data)) as im:
        im.verify()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if im.mode != 'RGB' or im.size != (info['width'], info['height']):
            raise VisionError('PNG RGB decode 오류입니다.', kind='image')
    return {
        'width': info['width'],
        'height': info['height'],
        'bytes': len(data),
        'format': 'png',
        'color': 'rgb24',
    }


def image_ref(work, path):
    r = ref(work, path)
    key = (r['sha256'], Path(path).stat().st_size)
    info = _VERIFIED_IMAGES.get(key)
    if info is None:
        info = image_info(path)
        _VERIFIED_IMAGES[(r['sha256'], info['bytes'])] = info
    return {**r, **info}


def check_image(ctx, reference):
    p = ctx.hashes.check(ctx.work, reference)
    key = (reference['sha256'], p.stat().st_size)
    info = _VERIFIED_IMAGES.get(key)
    if info is None:
        info = image_info(p)
        _VERIFIED_IMAGES[(reference['sha256'], info['bytes'])] = info
    if any(reference.get(k) != v for k, v in info.items()):
        raise VisionError('PNG 이미지 크기/형식 참조가 다릅니다.', kind='validation')
    return p


def frame_filename(stream, pts):
    return f'v{stream:03d}_pts{pts if pts>=0 else "m"+str(-pts)}.png'


def _vlm_dimensions(width, height):
    if max(width, height) <= 1280:
        return width, height
    if width >= height:
        nw = 1280
        nh = min(height, max(2, int(round(height * 1280 / width / 2)) * 2))
    else:
        nh = 1280
        nw = min(width, max(2, int(round(width * 1280 / height / 2)) * 2))
    return nw, nh


def vlm_image(ctx, full):
    source = check_image(ctx, full)
    nw, nh = _vlm_dimensions(full['width'], full['height'])
    filter_text = f'pillow_box_max1280:{nw}x{nh}'
    if (nw, nh) == (full['width'], full['height']):
        return dict(full), filter_text
    policy = digest({'filter': filter_text, 'pillow': PIL.__version__})
    dest = safe(ctx.work, f'vision/cache/vlm/{full["sha256"]}/{policy}.png')
    side = dest.with_suffix('.json')
    if side.is_file():
        try:
            reference = read(side)
            check_image(ctx, reference)
            return reference, filter_text
        except (VisionError, ValueError, OSError):
            dest = dest.with_name(policy + '_' + uuid.uuid4().hex + '.png')
            side = dest.with_suffix('.json')
    with Image.open(source) as im:
        im.load()
        if im.mode != 'RGB':
            raise VisionError('VLM 원본 RGB 모드 오류입니다.', kind='image')
        resized = im.resize((nw, nh), Image.Resampling.BOX)
    buf = io.BytesIO()
    resized.save(buf, format='PNG', compress_level=1)
    raw = buf.getvalue()
    info = _rgb.png_info(raw)
    if (
        max(info['width'], info['height']) > 1280
        or info['width'] > full['width']
        or info['height'] > full['height']
    ):
        raise VisionError('VLM 크기/확대 금지 조건 위반입니다.', kind='image')
    write(dest, raw, raw=True, immutable=True)
    reference = image_ref(ctx.work, dest)
    write(side, reference, immutable=True)
    return reference, filter_text


def extract(ctx, samples):
    unique = {s['frame_id']: s for s in samples}
    stream = ctx.input['stream']
    tb = stream['time_base']
    expected = (tb['numerator'], tb['denominator'])
    version = ctx.config['versions']['ffmpeg']
    profile = digest({'policy': EXTRACT_POLICY, 'version': version})
    base = f'vision/cache/frames/{ctx.input["video_ref"]["sha256"]}/{profile}'
    frames = {}
    missing = []
    paths = {}
    for fid, s in sorted(unique.items(), key=lambda pair: pair[1]['pts']):
        dest = safe(ctx.work, base + '/' + frame_filename(stream['index'], s['pts']))
        side = dest.with_suffix('.json')
        if side.is_file():
            try:
                saved = read(side)
                check_image(ctx, saved['full'])
                if (
                    saved['pts'] != s['pts']
                    or saved['source_video'] != ctx.input['stage1_identities']['video']
                    or saved['time_base'] != tb
                ):
                    raise VisionError('원본 프레임 identity 오류', kind='image')
                frames[fid] = saved
                continue
            except (VisionError, ValueError, OSError):
                dest = safe(
                    ctx.work,
                    base
                    + '_recovery_'
                    + uuid.uuid4().hex
                    + '/'
                    + frame_filename(stream['index'], s['pts']),
                )
        paths[fid] = dest
        missing.append(s)
    clusters = []
    for s in missing:
        t = actual(s['pts'], tb, stream['start_time'])
        if (
            not clusters
            or len(clusters[-1]) >= 40
            or t - actual(clusters[-1][-1]['pts'], tb, stream['start_time']) > 2
        ):
            clusters.append([])
        clusters[-1].append(s)
    for group in clusters:
        start = decimal(seconds(actual(group[0]['pts'], tb, stream['start_time'])))
        end = decimal(seconds(actual(group[-1]['pts'], tb, stream['start_time'])))
        seek = max(Decimal(0), start - 1)
        duration = end - seek + Decimal('1.5')
        temp = ctx.directory / f'.tmp/rgb_{uuid.uuid4().hex}'
        extra_temps = []
        try:
            result = _rgb.extract_window(
                ctx.settings.ffmpeg,
                ctx.video_path,
                [s['pts'] for s in group],
                seek=seek,
                duration=duration,
                destination=temp,
                expected_time_base=expected,
            )
            matches = {}
            if result['problem'] is None:
                if any(rec['pts'] not in {s['pts'] for s in group} for rec in result['frames']):
                    raise VisionError('요청 외 PTS가 추출되었습니다.', kind='image')
                for rec, path in zip(result['frames'], result['files']):
                    if rec['pts'] in matches:
                        raise VisionError('중복 PTS 추출입니다.', kind='image')
                    matches[rec['pts']] = (path, result)
            for s in group:
                fid = s['frame_id']
                t = decimal(seconds(actual(s['pts'], tb, stream['start_time'])))
                tried = set()
                if s['pts'] not in matches:
                    for pre in [Decimal(1), Decimal(2), Decimal(4), Decimal(8), t]:
                        seek = max(Decimal(0), t - pre)
                        if seek in tried:
                            continue
                        tried.add(seek)
                        tmp = ctx.directory / f'.tmp/one_{uuid.uuid4().hex}'
                        extra_temps.append(tmp)
                        one = _rgb.extract_one(
                            ctx.settings.ffmpeg,
                            ctx.video_path,
                            s['pts'],
                            seek=seek,
                            duration=t - seek + Decimal('1.5'),
                            destination=tmp,
                            expected_time_base=expected,
                        )
                        if one['problem'] is None:
                            matches[s['pts']] = (one['files'][0], one)
                            break
                if s['pts'] not in matches:
                    continue
                path, info = matches[s['pts']]
                image_info(path)
                dest = paths[fid]
                write(dest, path.read_bytes(), raw=True, immutable=True)
                logpath = ctx.directory / f't4/extractions/{uuid.uuid4().hex}.json'
                write(
                    logpath,
                    {
                        'pts': s['pts'],
                        'time_base': tb,
                        'argv': info['args'],
                        'returncode': info['returncode'],
                        'frames': info['frames'],
                        'stderr': info['stderr_tail'],
                    },
                    immutable=True,
                )
                full = image_ref(ctx.work, dest)
                frame = {
                    'frame_id': fid,
                    'stream_index': stream['index'],
                    'pts': s['pts'],
                    'time_base': tb,
                    'video_seconds': seconds(actual(s['pts'], tb, stream['start_time'])),
                    'source_video': ctx.input['stage1_identities']['video'],
                    'full': full,
                    'display_transform': 'ffmpeg_default_display',
                    'extraction_job_ref': {
                        'job_key': digest(
                            {
                                'video': ctx.input['video_ref']['sha256'],
                                'stream': stream['index'],
                                'pts': s['pts'],
                                'time_base': tb,
                                'policy': profile,
                            }
                        ),
                        'source_run_id': ctx.run_id,
                        'result': ref(ctx.work, logpath),
                    },
                }
                write(dest.with_suffix('.json'), frame, immutable=True)
                frames[fid] = frame
        finally:
            for d in (temp, *extra_temps):
                shutil.rmtree(d, ignore_errors=True)
    output = {}
    try:
        for fid, frame in frames.items():
            try:
                vlm, filter_text = vlm_image(ctx, frame['full'])
                output[fid] = {**frame, 'vlm': vlm, 'vlm_filter': filter_text}
            except (VisionError, OSError, ValueError, subprocess.SubprocessError):
                continue
    finally:
        shutil.rmtree(ctx.directory / '.tmp', ignore_errors=True)
    return output


def extract_probe(ctx, chunk_id, start_seconds, end_seconds):
    stream = ctx.input['stream']
    version = ctx.config['versions']['ffmpeg']
    start = decimal(start_seconds)
    end = decimal(end_seconds)
    mid = (start + end) / Decimal(2)
    profile = digest(
        {'policy': PROBE_POLICY, 'version': version, 'stream': stream['index'], 'mid': seconds(mid)}
    )
    key_name = digest({'chunk_id': chunk_id, 'mid': seconds(mid)})[:24]
    dest = safe(
        ctx.work, f'vision/cache/probes/{ctx.input["video_ref"]["sha256"]}/{profile}_{key_name}.png'
    )
    side = dest.with_suffix('.json')
    if side.is_file():
        try:
            saved = read(side)
            check_image(ctx, saved)
            return saved
        except (VisionError, ValueError, OSError):
            dest = dest.with_name(f'{profile}_{key_name}_{uuid.uuid4().hex}.png')
            side = dest.with_suffix('.json')
    vf = _rgb.scale_filter(int(stream['width']), int(stream['height']))
    temp = ctx.directory / f'.tmp/probe_{uuid.uuid4().hex}'
    try:
        temp.mkdir(parents=True, exist_ok=True)
        out_path = temp / 'probe.png'
        for seek in (mid, max(Decimal(0), mid - Decimal('0.5')), start):
            if out_path.exists():
                out_path.unlink()
            args = [
                ctx.settings.ffmpeg,
                '-hide_banner',
                '-nostdin',
                '-loglevel',
                'error',
                '-threads',
                _rgb.FFMPEG_THREADS,
                '-ss',
                format(seek, 'f'),
                '-i',
                str(ctx.video_path),
                '-map',
                '0:v:0',
                '-an',
                '-vf',
                vf,
                '-frames:v',
                '1',
                '-update',
                '1',
                str(out_path),
            ]
            res = _rgb.run_ffmpeg(args)
            if res['returncode'] == 0 and out_path.is_file() and out_path.stat().st_size > 0:
                break
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise VisionError('T2 probe 중앙 프레임을 추출하지 못했습니다.', kind='image')
        image_info(out_path)
        write(dest, out_path.read_bytes(), raw=True, immutable=True)
        reference = image_ref(ctx.work, dest)
        write(side, reference, immutable=True)
        return reference
    finally:
        shutil.rmtree(temp, ignore_errors=True)
        shutil.rmtree(ctx.directory / '.tmp', ignore_errors=True)
