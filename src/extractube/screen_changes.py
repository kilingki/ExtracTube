"""Streaming B05 sampling: raw images are discarded after adjacent scalar extraction."""

import hashlib
from decimal import Decimal
from fractions import Fraction
from . import _screen_change as b
from .store import write
from .timestamps import decimal, seconds, score, actual, span
from .errors import VisionError

B05 = {
    'config_id': 'B05',
    'logic_version': 't3.screen_changes.visual_segments.v1',
    'grid_interval_seconds': '0.5',
    'padding_seconds': '0',
    'extra_sample': 'last_frame_before_end',
    'analysis_width': 640,
    'analysis_height': 'even_aspect',
    'scale_flags': 'area',
    'pixel_format': 'gray',
    'tile_width': 80,
    'tile_height': 90,
    'tile_origin': 'top_left',
    'tile_combine': 'any_pass',
    'neighbor_seconds': '5',
    'min_neighbor_count': 4,
    'absolute_threshold': '0.005',
    'mad_scale': '1.4826',
    'mad_multiplier': '3',
    'peak_radius_seconds': '0.5',
    'peak_neighbor': 'one_grid_request_step',
    'grid_suppress_rule': 'kept_neighbor_passing_tile_subset',
    'tie_break': 'earlier_time',
    'subtitle_bottom_row_merge': True,
    'subtitle_merge_max_seconds': '20',
    'decode_concurrency': 1,
    'ffmpeg_threads': 2,
    'subprocess_timeout_seconds': 300,
    'decode_margin_seconds': '0.15',
    'sample_gap_warning_seconds': '1',
}
probe_video = b.probe_video
tool_identity = b.tool_identity
tile_layout = b.tile_layout


# Rendering decimals happens only at the artifact boundary.
def plain(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, Fraction):
        return seconds(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def decode(ffmpeg, path, start, end, stream, on_frame):
    args = [
        ffmpeg,
        '-hide_banner',
        '-nostdin',
        '-loglevel',
        'info',
        '-threads',
        '2',
        '-copyts',
        '-ss',
        format(start, 'f'),
    ]
    if end is not None:
        args += ['-t', format(end - start + Decimal('0.15'), 'f')]
    args += [
        '-i',
        str(path),
        '-map',
        '0:v:0',
        '-an',
        '-vf',
        'scale=640:-2:flags=area,format=gray,showinfo',
        '-fps_mode',
        'passthrough',
        '-f',
        'rawvideo',
        '-pix_fmt',
        'gray',
        'pipe:1',
    ]
    last_pts = None

    def accept(rec, raw):
        nonlocal last_pts
        if last_pts is not None and rec['pts'] <= last_pts:
            raise VisionError('디코드 PTS가 증가하지 않습니다.', kind='media')
        last_pts = rec['pts']
        on_frame(rec, raw)

    result = b.run_ffmpeg(args, 300, accept)
    tb = Fraction(stream['time_base']['numerator'], stream['time_base']['denominator'])
    if result['time_base_in'] != tb or result['time_base_out'] not in (None, tb):
        raise VisionError('필터 time_base가 원본과 다릅니다.', kind='media')
    return result


def last_frame(ffmpeg, path, start, end, stream):
    last = None

    def accept(rec, raw):
        nonlocal last
        t = actual(rec['pts'], stream['time_base'], stream['start_time'])
        if t >= Fraction(start) and (end is None or t < Fraction(end)):
            last = (rec, raw, t)

    result = decode(ffmpeg, path, start, end, stream, accept)
    return last, result


def confirm_end(ffmpeg, path, stream):
    duration = decimal(stream['duration'])
    last, result = last_frame(ffmpeg, path, max(Decimal(0), duration - 3), None, stream)
    if last is None or last[2] < 0 or last[2] >= Fraction(duration):
        raise VisionError('스트림 마지막 프레임이 유효하지 않습니다.', kind='media')
    return {'last_pts': last[0]['pts'], 'last_video_seconds': seconds(last[2])}


def finish_rows(rows, samples, layout):
    return b._finish_tile_rows(rows, layout, samples, B05)


def replay(payload):
    samples = payload['samples']
    if not samples:
        raise VisionError('T3 sample이 없습니다.', kind='validation')
    layout = b.tile_layout(samples[0]['analysis_width'], samples[0]['analysis_height'])
    rows = []
    for row in payload['differences']:
        after = samples[row['after_sample']]
        rows.append(
            {
                'index': row['index'],
                'before_sample': row['before_sample'],
                'after_sample': row['after_sample'],
                'elapsed_video_seconds': decimal(row['elapsed_video_seconds']),
                'global_mean': decimal(row['global_mean']),
                't': decimal(after['video_seconds']),
                'tile_values': [decimal(t['value']) for t in row['tiles']],
            }
        )
    return plain(finish_rows(rows, samples, layout))


def select_representative_sample(sample_indices, adj_motion):
    if not sample_indices:
        return None
    if len(sample_indices) == 1:
        return int(sample_indices[0])
    sample_set = set(int(i) for i in sample_indices)
    pool = [int(i) for i in sample_indices[len(sample_indices) // 2 :]]

    def motion_score(si):
        vals = [
            adj_motion[(a, z)]
            for a, z in ((si - 1, si), (si, si + 1))
            if a in sample_set and z in sample_set and (a, z) in adj_motion
        ]
        return max(vals) if vals else Decimal(0)

    return min(pool, key=lambda si: (motion_score(si), si))


def consolidate_segments(candidate_id, samples, differences, boundaries, segments):
    if not segments:
        return []
    diffs_by_index = {int(d['index']): d for d in differences}
    adj_motion = {
        (int(d['before_sample']), int(d['after_sample'])): decimal(d['global_mean'])
        for d in differences
    }
    num_rows = num_cols = 0
    tile_by_idx = {}
    if samples:
        layout = b.tile_layout(
            int(samples[0]['analysis_width']), int(samples[0]['analysis_height'])
        )
        num_rows = max(t['row'] for t in layout) + 1
        num_cols = max(t['column'] for t in layout) + 1
        tile_by_idx = {t['tile_index']: t for t in layout}
    bottom_row = num_rows - 1
    groups = [{'segments': [segments[0]], 'merged_boundary_ids': []}]
    for k, bd in enumerate(boundaries):
        d = diffs_by_index[int(bd['difference_index'])]
        passing = [int(i) for i in d['passing_tile_indices']]
        merge = False
        if samples and num_rows >= 4:
            upper = [i for i in passing if tile_by_idx[i]['row'] < bottom_row]
            bottom = [i for i in passing if tile_by_idx[i]['row'] == bottom_row]
            gm = decimal(d['global_mean'])
            cur_start = decimal(groups[-1]['segments'][0]['video_range']['start'])
            bt = decimal(bd['video_seconds'])
            major_bottom = len(bottom) >= max(5, (num_cols * 5 + 7) // 8) and gm >= Decimal('0.035')
            span_exceeded = (bt - cur_start) >= Decimal('20')
            if not upper and not major_bottom and not span_exceeded:
                merge = True
        if merge:
            groups[-1]['segments'].append(segments[k + 1])
            groups[-1]['merged_boundary_ids'].append(bd['boundary_id'])
        else:
            groups.append({'segments': [segments[k + 1]], 'merged_boundary_ids': []})
    visual_segments = []
    for vs_idx, grp in enumerate(groups):
        segs_in_grp = grp['segments']
        left = decimal(segs_in_grp[0]['video_range']['start'])
        right = decimal(segs_in_grp[-1]['video_range']['end'])
        s_indices = [int(si) for sg in segs_in_grp for si in sg['sample_indices']]
        rep = select_representative_sample(s_indices, adj_motion)
        visual_segments.append(
            plain(
                {
                    'visual_segment_id': f'{candidate_id}:vs{vs_idx}',
                    'visual_segment_index': vs_idx,
                    'video_range': span(left, right, 'video_start'),
                    'source_segment_indices': [int(sg['segment_index']) for sg in segs_in_grp],
                    'sample_indices': s_indices,
                    'representative_sample_index': rep,
                    'merged_boundary_ids': list(grp['merged_boundary_ids']),
                }
            )
        )
    return visual_segments


class _CandidateCollector:
    def __init__(self, ctx, candidate):
        self.ctx = ctx
        self.candidate = candidate
        self.r = candidate['effective_video_range']
        self.start, self.end = decimal(self.r['start']), decimal(self.r['end'])
        self.start_frac, self.end_frac = Fraction(self.start), Fraction(self.end)
        self.stream = ctx.input['stream']
        self.requests = b.grid_requests(self.start, self.end)
        self.samples = []
        self.request_rows = []
        self.rows = []
        self.previous = None
        self.last = None
        self.at = 0
        self.layout = None

    def _add(self, rec, raw, hits, tail=False):
        t = actual(rec['pts'], self.stream['time_base'], self.stream['start_time'])
        if self.samples and self.samples[-1]['pts'] == rec['pts']:
            self.samples[-1]['roles'].append('tail')
            self.samples[-1]['request_indices'].append(len(self.requests))
            return
        shown = max(self.start, min(decimal(seconds(t)), self.end - Decimal('0.000000001')))
        sample = {
            'sample_index': len(self.samples),
            'frame_id': f'{self.ctx.video_id}:v{self.stream["index"]}:pts{rec["pts"]}',
            'stream_index': self.stream['index'],
            'pts': rec['pts'],
            'time_base': self.stream['time_base'],
            'video_seconds': seconds(shown),
            'request_indices': hits + ([len(self.requests)] if tail else []),
            'roles': (['grid'] if hits else []) + (['tail'] if tail else []),
            'analysis_width': rec['width'],
            'analysis_height': rec['height'],
            'gray_sha256': hashlib.sha256(raw).hexdigest(),
        }
        if self.layout is None:
            self.layout = b.tile_layout(rec['width'], rec['height'])
        if self.previous is not None:
            if (rec['width'], rec['height']) != (
                self.samples[-1]['analysis_width'],
                self.samples[-1]['analysis_height'],
            ):
                raise VisionError('분석 크기가 바뀌었습니다.', kind='media')
            mean, values = b.frame_tile_differences(
                self.previous, raw, rec['width'], rec['height'], self.layout, 80, 90
            )
            self.rows.append(
                {
                    'index': len(self.samples),
                    'before_sample': len(self.samples) - 1,
                    'after_sample': len(self.samples),
                    'elapsed_video_seconds': shown - decimal(self.samples[-1]['video_seconds']),
                    'global_mean': mean,
                    't': shown,
                    'tile_values': values,
                }
            )
        self.samples.append(sample)
        self.previous = raw

    def accept(self, rec, raw):
        t = actual(rec['pts'], self.stream['time_base'], self.stream['start_time'])
        if not self.start_frac <= t < self.end_frac:
            return
        self.last = (rec, raw, t)
        hits = []
        while self.at < len(self.requests) and Fraction(self.requests[self.at]) <= t:
            hits.append(self.at)
            self.at += 1
        if hits:
            self._add(rec, raw, hits)
            for hit in hits:
                self.request_rows.append(
                    {
                        'request_index': hit,
                        'kind': 'grid',
                        'requested_video_seconds': seconds(self.requests[hit]),
                        'strategy': 'first_at_or_after',
                        'status': 'ok',
                        'sample_index': len(self.samples) - 1,
                        'extraction_error_seconds': seconds(t - Fraction(self.requests[hit])),
                    }
                )

    def finalize(self, decoded_meta, stderr_rel):
        if self.last is None:
            raise VisionError('후보 안에 실제 프레임이 없습니다.', kind='media')
        self._add(self.last[0], self.last[1], [], tail=True)
        for hit in range(self.at, len(self.requests)):
            self.request_rows.append(
                {
                    'request_index': hit,
                    'kind': 'grid',
                    'requested_video_seconds': seconds(self.requests[hit]),
                    'strategy': 'first_at_or_after',
                    'status': 'missing',
                    'sample_index': None,
                    'extraction_error_seconds': None,
                }
            )
        self.request_rows.append(
            {
                'request_index': len(self.requests),
                'kind': 'tail',
                'requested_video_seconds': seconds(self.end),
                'strategy': 'last_before_end',
                'status': 'ok',
                'sample_index': len(self.samples) - 1,
                'extraction_error_seconds': seconds(self.last[2] - self.end_frac),
            }
        )
        differences = finish_rows(self.rows, self.samples, self.layout)
        native = [{**s, 'video_seconds': decimal(s['video_seconds'])} for s in self.samples]
        bounds = b._boundaries(differences, native, self.start, self.end)
        cid = self.candidate['candidate_id']
        for i, bd in enumerate(bounds):
            bd['boundary_id'] = f'{cid}:b{i}'
        segments = []
        edges = [self.start] + [v['video_seconds'] for v in bounds] + [self.end]
        for i, (left, right) in enumerate(zip(edges, edges[1:])):
            segments.append(
                {
                    'segment_id': f'{cid}:g{i}',
                    'segment_index': i,
                    'video_range': span(left, right, 'video_start'),
                    'left_boundary_id': None if i == 0 else bounds[i - 1]['boundary_id'],
                    'right_boundary_id': None if i == len(bounds) else bounds[i]['boundary_id'],
                    'sample_indices': [
                        s['sample_index']
                        for s in self.samples
                        if left <= decimal(s['video_seconds']) < right
                    ],
                }
            )
        visual_segments = consolidate_segments(cid, self.samples, differences, bounds, segments)
        warnings = []
        from .visual_policy import warning

        for req in self.request_rows:
            if req['status'] == 'missing':
                warnings.append(warning('sample_missing', 't3', cid))
        for a, z in zip(self.samples, self.samples[1:]):
            if (
                actual(z['pts'], z['time_base'], self.stream['start_time'])
                - actual(a['pts'], a['time_base'], self.stream['start_time'])
                > 1
            ):
                warnings.append(warning('sample_gap', 't3', cid))
        return plain(
            {
                'candidate_id': cid,
                'requested_range': self.candidate['requested_video_range'],
                'effective_range': self.r,
                'requests': self.request_rows,
                'samples': self.samples,
                'differences': differences,
                'boundaries': bounds,
                'segments': segments,
                'visual_segments': visual_segments,
                'decode': {**decoded_meta, 'stderr': stderr_rel},
                'warnings': warnings,
            }
        )


def detect(ctx, candidate):
    collector = _CandidateCollector(ctx, candidate)
    decoded = decode(
        ctx.settings.ffmpeg,
        ctx.video_path,
        collector.start,
        collector.end,
        collector.stream,
        collector.accept,
    )
    stderr_path = ctx.directory / f't3/{ctx.folder(candidate)}/decode.stderr'
    write(stderr_path, decoded.pop('stderr').encode(), raw=True, immutable=True)
    return collector.finalize(decoded, stderr_path.relative_to(ctx.work).as_posix())


_DEFAULT_DETECT = detect


def detect_many(ctx, candidates, max_gap=Decimal('5'), max_span=Decimal('180')):
    if not candidates:
        return {}
    if getattr(detect, '__wrapped__', detect) is not _DEFAULT_DETECT:
        return {c['candidate_id']: detect(ctx, c) for c in candidates}
    ordered = sorted(
        candidates,
        key=lambda c: (
            decimal(c['effective_video_range']['start']),
            decimal(c['effective_video_range']['end']),
        ),
    )
    spans = []
    for c in ordered:
        cs, ce = decimal(c['effective_video_range']['start']), decimal(
            c['effective_video_range']['end']
        )
        if (
            spans
            and cs - spans[-1]['end'] <= max_gap
            and max(spans[-1]['end'], ce) - spans[-1]['start'] <= max_span
        ):
            spans[-1]['candidates'].append(c)
            spans[-1]['end'] = max(spans[-1]['end'], ce)
        else:
            spans.append({'start': cs, 'end': ce, 'candidates': [c]})
    results = {}
    for sp_idx, sp in enumerate(spans):
        group = sp['candidates']
        if len(group) == 1:
            results[group[0]['candidate_id']] = detect(ctx, group[0])
            continue
        collectors = [_CandidateCollector(ctx, c) for c in group]
        try:

            def on_frame(rec, raw):
                for col in collectors:
                    col.accept(rec, raw)

            decoded = decode(
                ctx.settings.ffmpeg,
                ctx.video_path,
                sp['start'],
                sp['end'],
                ctx.input['stream'],
                on_frame,
            )
            stderr_bytes = decoded.pop('stderr').encode()
            for col in collectors:
                stderr_path = ctx.directory / f't3/{ctx.folder(col.candidate)}/decode.stderr'
                try:
                    finalized = col.finalize(
                        dict(decoded), stderr_path.relative_to(ctx.work).as_posix()
                    )
                except (b.T3Error, VisionError):
                    continue
                write(stderr_path, stderr_bytes, raw=True, immutable=True)
                results[col.candidate['candidate_id']] = finalized
        except (b.T3Error, VisionError):
            pass
    return results
