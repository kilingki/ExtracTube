"""Rebuild timing tables from the immutable call journals and bounded measurements."""
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

from run import ROOT, read, write


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['task']].append(row)
    result = {}
    for task, group in groups.items():
        times = sorted(r['elapsed_seconds'] for r in group if r.get('elapsed_seconds') is not None)
        result[task] = {
            'calls': len(group), 'http_seconds': sum(times),
            'mean_seconds': statistics.mean(times) if times else None,
            'p50_seconds': statistics.median(times) if times else None,
            'p95_seconds': times[min(len(times)-1, int((len(times)-1)*.95))] if times else None,
            'max_seconds': max(times) if times else None,
            'statuses': {s: sum(r.get('status') == s for r in group) for s in sorted({str(r.get('status')) for r in group})},
            'prompt_tokens': sum((r.get('usage') or {}).get('prompt_tokens', 0) for r in group),
            'completion_tokens': sum((r.get('usage') or {}).get('completion_tokens', 0) for r in group)}
    return result


def historical(run):
    """Historical HTTP only: no wall-clock estimates from mtimes or resumed start/end."""
    result = []
    videos = set(read(run/'manifest.json')['stage1'])
    for campaign in sorted((ROOT/'tests/stage3/outputs').glob('e2e_*')):
        manifest = read(campaign/'manifest.json')
        for video in sorted(videos):
            work = campaign/'data'/video
            for namespace in ('vision', 'content'):
                for state in sorted((work/namespace/'runs').glob('*/state.json')):
                    directory = state.parent
                    rows = []
                    for journal in directory.glob('jobs/*.json'):
                        job = read(journal)
                        for attempt in job['attempts']:
                            path = work/attempt['path']/'record.json'
                            record = read(path) if path.exists() else {'status': 'unknown'}
                            rows.append({'task': job['task'], **record})
                    row = {'campaign': str(campaign.relative_to(ROOT)), 'video_id': video,
                           'stage': namespace, 'run_id': directory.name, 'state': read(state),
                           'task_timings': aggregate(rows),
                           'same_campaign_source_code': manifest.get('source_code') == read(run/'manifest.json')['source_code']}
                    if (directory/'plan.json').exists():
                        plan = read(directory/'plan.json')
                        row['stage_counts'] = plan.get('stage_counts', {'generation_units': plan.get('generation_units')})
                    for rel, keys in [('t2/candidates.json', ['candidates']), ('frames.json', ['frames', 'associations'])]:
                        path = directory/rel
                        if path.exists():
                            payload = read(path).get('payload', {})
                            row.update({k+'_count': len(payload.get(k, [])) for k in keys})
                    result.append(row)
    write(run/'historical.json', result)
    return result


def main(run, quiet=False):
    manifest = read(run/'manifest.json')
    limits = manifest.get('limits', {})
    unbounded = all(limits.get(k) is None for k in ('phase', 'stage2', 'stage3'))
    has_in_place_s3 = 'stage3_sources' in manifest
    summary = read(run/'summary.json')
    corrected_path = run/'validated_stage3/summary.json'
    corrected = (read(corrected_path) if corrected_path.exists()
                 else {'videos': [], 'status': 'in_place_exact_lineage' if has_in_place_s3 else 'pending'})
    corrected_by_video = {r['video_id']: r['stage3'] for r in corrected['videos']}
    rows = []
    all_s2_calls, all_s3_calls = [], []
    phase_totals = defaultdict(float)
    phase_videos = defaultdict(int)
    for item in summary['videos']:
        row = {'video_id': item['video_id'], 'url': item['url']}
        for stage in ('stage2', 'stage3'):
            measured = corrected_by_video.get(item['video_id'], item[stage]) if stage == 'stage3' else item[stage]
            stage_calls = measured.get('calls', [])
            if stage == 'stage2':
                all_s2_calls.extend(stage_calls)
                for ph, sec in measured.get('phase_seconds', {}).items():
                    phase_totals[ph] += sec
                    phase_videos[ph] += 1
            else:
                all_s3_calls.extend(stage_calls)
            timing = aggregate(stage_calls)
            row[stage] = {k: measured[k] for k in ('status', 'wall_seconds', 'last_phase', 'phase_seconds', 'error', 'reason', 'input_origin') if k in measured}
            row[stage]['task_timings'] = timing
            row[stage]['http_seconds'] = sum(t['http_seconds'] for t in timing.values())
            row[stage]['http_calls'] = sum(t['calls'] for t in timing.values())
            row[stage]['functions_by_self_time'] = sorted(
                [{'function': name, **stats} for name, stats in measured.get('functions', {}).items()],
                key=lambda v: v['self_seconds'], reverse=True)
            if stage == 'stage3' and item['video_id'] in corrected_by_video:
                row[stage]['measurement_directory'] = str((run/'validated_stage3/measurements'/item['video_id']/'stage3').relative_to(ROOT))
                row[stage]['initial_attempt_excluded'] = {k: item[stage][k] for k in ('status', 'error', 'wall_seconds') if k in item[stage]}
        rows.append(row)
    campaign_s2_tasks = aggregate(all_s2_calls)
    campaign_s3_tasks = aggregate(all_s3_calls)
    write(run/'analysis.json', {
        'status': summary['status'],
        'unbounded': unbounded,
        'limits': limits,
        'stage3_correction_status': corrected['status'],
        'stage2_phase_totals': dict(phase_totals),
        'stage2_task_totals': campaign_s2_tasks,
        'stage3_task_totals': campaign_s3_tasks,
        'videos': rows,
    })
    hist = read(run/'historical.json') if (run/'historical.json').exists() else historical(run)
    limit_desc = (
        '하위 단계 및 Stage2/Stage3 wall-clock 강제 중단 상한을 제거하고 끝까지 실행한다 (`limits: None`). '
        f'제품 누적 예산 한도는 `vision max_http_seconds={manifest["settings"]["vision"]["max_http_seconds"]:g}`, '
        f'`content max_http_seconds={manifest["settings"]["content"]["max_http_seconds"]:g}`로 설정했으며, '
        '단일 HTTP 요청 소켓 timeout은 제품 기본값 300초다.'
        if unbounded else
        f'하위 단계 {limits.get("phase")}초, Stage2 {limits.get("stage2")}초, Stage3 {limits.get("stage3")}초를 초과하면 '
        '다음 계측 경계에서 중단한다. 진행 중인 요청/로컬 작업 때문에 기준보다 늦게 중단될 수 있다. 단일 HTTP timeout은 제품 기본값 300초다.'
    )
    lines = ['# Stage2 · Stage3 병목 측정', '',
             f'캠페인: `{run.name}` ({len(rows)}/{len(manifest["urls"])} 완료). '
             f'배치 상태: `{summary["status"]}`. Stage3 계보 방식: `{corrected["status"]}`.', '',
             'Stage1은 해시를 검증한 기존 산출물을 사용했다. 새 Stage2/Stage3 추론 캐시는 비어 있으며, 모델 서버는 이미 적재된 상태다. '
             '동시 요청은 1개다. ' + limit_desc, '',
             'Stage3는 이번 실행에서 완주된 Stage2 handoff(`same_run`)를 우선 사용하며, Stage2가 실패한 영상에 한해 과거 완주 handoff(`historical_handoff`)가 있으면 '
             '해당 handoff의 정확한 원본 Stage1 계보로 검증 후 측정한다. 입력 검증도 Stage3 시간에 포함한다. 실패/입력 차단을 성능 성공으로 세지 않는다.']
    if not has_in_place_s3:
        lines += ['',
                  '초기 Stage3 분리 시 현재 Stage1 문장 파일과 과거 handoff를 섞은 입력 구성 오류가 있었다. '
                  '해당 검증 실패는 원본 그대로 남기고, 아래 Stage3 값은 `validated_stage3`의 정확한 원본 계보로 다시 측정한 값으로 교체한다. '
                  '해당 보정이 끝나기 전 값은 잠정값이다.']
    lines += ['',
              '| 영상 | Stage2 상태 / 초 / HTTP 호출 | 마지막 단계 | Stage3 상태 / 초 / HTTP 호출 | Stage3 입력 |',
              '|---|---|---|---|---|']
    for r in rows:
        a, b = r['stage2'], r['stage3']
        def fmt(s):
            return f'{s["status"]} / {s.get("wall_seconds", 0):.1f} / {s["http_calls"]}' if 'wall_seconds' in s else s['status']
        lines.append(f'| {r["video_id"]} | {fmt(a)} | {a.get("last_phase", "—")} | {fmt(b)} | {b.get("input_origin", {}).get("kind", "없음")} |')
    s2_wall = sum(r['stage2'].get('wall_seconds', 0) for r in rows)
    s2_http = sum(r['stage2']['http_seconds'] for r in rows)
    s3_wall = sum(r['stage3'].get('wall_seconds', 0) for r in rows)
    s3_http = sum(r['stage3']['http_seconds'] for r in rows)
    s2_ok = [r for r in rows if r['stage2']['status'] in ('complete', 'complete_with_warnings', 'empty')]
    s3_ok = [r for r in rows if r['stage3']['status'] in ('complete', 'complete_with_warnings', 'empty')]
    lines += ['', '## 전체 단계 · 호출 병목 요약', '',
              f'- **Stage2 누적**: wall {s2_wall:.1f}초 ({s2_wall/60:.1f}분), HTTP {s2_http:.1f}초 ({s2_http/s2_wall*100 if s2_wall else 0:.1f}%), '
              f'완주 {len(s2_ok)}/{len(rows)}개.',
              f'- **Stage3 누적**: wall {s3_wall:.1f}초 ({s3_wall/60:.1f}분), HTTP {s3_http:.1f}초 ({s3_http/s3_wall*100 if s3_wall else 0:.1f}%), '
              f'완주 {len(s3_ok)}/{len(rows)}개.']
    if phase_totals:
        lines += ['', '### Stage2 하위 단계별 Wall Time 합계', '',
                  '| 단계 | 도달 영상 수 | 합계(초) | 합계(분) | Stage2 비중(%) |',
                  '|---|---:|---:|---:|---:|']
        for ph in sorted(phase_totals, key=lambda k: phase_totals[k], reverse=True):
            sec = phase_totals[ph]
            lines.append(f'| {ph} | {phase_videos[ph]} | {sec:.2f} | {sec/60:.1f} | {sec/s2_wall*100 if s2_wall else 0:.1f}% |')
    if campaign_s2_tasks or campaign_s3_tasks:
        lines += ['', '### 추론 Task별 HTTP 시간 합계', '',
                  '| Stage | 호출 유형 | 호출 수 | HTTP 합계(초) | 평균(초) | p95(초) | 입력 토큰 합계 | 출력 토큰 합계 |',
                  '|---|---|---:|---:|---:|---:|---:|---:|']
        for st_label, tasks in [('stage2', campaign_s2_tasks), ('stage3', campaign_s3_tasks)]:
            for task, t in sorted(tasks.items(), key=lambda kv: kv[1]['http_seconds'], reverse=True):
                lines.append(
                    f'| {st_label} | {task} | {t["calls"]} | {t["http_seconds"]:.2f} | '
                    f'{t["mean_seconds"] or 0:.2f} | {t["p95_seconds"] or 0:.2f} | '
                    f'{t["prompt_tokens"]} | {t["completion_tokens"]} |'
                )
    lines += ['', '## 병목 해석과 개선 우선순위', '',
              '1. **T4 호출 수를 먼저 줄일 설계를 검토한다.** T3는 0.5초 간격으로 관측하고, '
              '`selection.run`은 모든 관측 sample을 원본 PNG/VLM 이미지로 만든 뒤 3~4장씩 판정한다. '
              '현재 대표 프레임 연결은 T5 대상을 줄이지만, 이미 수행해야 하는 T4 판정 자체는 줄이지 못한다. '
              '관측 데이터 보존과 VLM 판정 대상 선정을 분리하는 것이 우선 후보다. 임의 고정 장수 제한을 적용한 것은 아니며 '
              '짧은 화면·작은 수치 변경 보존을 별도로 검증해야 한다.', '',
              '2. **T1의 다수 직렬 호출과 T2의 thinking 비용을 별도로 다룬다.** '
              'T1은 boundary 제안, conflict 처리, 경계별 verify 호출을 순서대로 수행한다. '
              'T2는 thinking이 켜져 있다. task별 호출 수·평균·출력 토큰 기록으로 '
              '호출 통합과 reasoning 예산 조정 실험의 기준을 잡을 수 있다. 정확도 확인 없이 검증 단계를 제거하지 않는다.', '',
              '3. **Stage3는 입력 검증·outline 실패·본문 생성을 구분한다.** '
              'handoff 검증은 저장된 요청 재파싱, B05 재생, PNG decode를 포함한다. '
              'outline 계약 실패 시 본문 생성 성능을 측정한 것으로 볼 수 없으며, 실패 시간은 빠른 완료 시간이 아니다.', '',
              '4. **운영 시 wall time 예산과 단계 진행을 노출한다.** 현재 제품의 HTTP 누적 예산만으로는 '
              '로컬 이미지 처리·검증 시간을 제한하지 못한다. 단계별 wall time과 실제 완료 호출 수를 함께 기록하는 방식이 유용하다.']
    for row in rows:
        lines += ['', '## '+row['video_id'], '']
        for stage in ('stage2', 'stage3'):
            s = row[stage]
            lines += [f'### {stage}', '', f'상태: `{s["status"]}`.']
            if s.get('error') or s.get('reason'):
                lines += ['', str(s.get('error') or s.get('reason'))]
            if s.get('phase_seconds'):
                lines += ['', '단계별 wall time: '+', '.join(f'{k}={v:.2f}s' for k, v in s['phase_seconds'].items())+'.']
            if s['task_timings']:
                lines += ['', '| 호출 유형 | 호출 수 | HTTP 합계(초) | 평균(초) | p95(초) |', '|---|---:|---:|---:|---:|']
                for task, t in s['task_timings'].items():
                    lines.append(f'| {task} | {t["calls"]} | {t["http_seconds"]:.2f} | {t["mean_seconds"] or 0:.2f} | {t["p95_seconds"] or 0:.2f} |')
            if s['functions_by_self_time']:
                lines += ['', '계측한 함수의 self time 상위 항목(중첩 계측 시간 제외):', '']
                for t in s['functions_by_self_time'][:6]:
                    lines.append(f'- `{t["function"]}`: {t["self_seconds"]:.2f}s, {t["calls"]}회 (포함 시간 {t["wall_seconds"]:.2f}s).')
    lines += ['', '## 과거 실행의 보조 증거', '',
              '아래는 이번 측정과 합산하지 않은 과거 HTTP 기록이다. 재개·유휴 시간이 섞인 시작/종료 시각으로 wall time을 추정하지 않았다. '
              '`same code`는 과거 캠페인 manifest와 이번 제품 코드 해시의 일치 여부다.', '',
              '| 캠페인 / 영상 / Stage | 보존된 상태 | same code | 호출 수 | HTTP 합계(초) | 최다 HTTP 시간 유형 |', '|---|---|---|---:|---:|---|']
    for r in hist:
        t = r['task_timings']
        dominant = max(t, key=lambda k: t[k]['http_seconds']) if t else '—'
        lines.append(f'| {Path(r["campaign"]).name} / {r["video_id"]} / {r["stage"]} | {r["state"]["execution_status"]} | {r["same_campaign_source_code"]} | '
                     f'{sum(v["calls"] for v in t.values())} | {sum(v["http_seconds"] for v in t.values()):.1f} | {dominant} |')
    lines += ['', '## 재현과 해석 범위', '',
              '- `manifest.json`: 입력/제품 코드 해시, 설정, 중단 기준.',
              '- `measurements/<video>/<stage>/events.jsonl`: UTC·단조 시계 기반 함수 진입/종료와 진행 로그.',
              '- `summary.json`, `analysis.json`: 새 측정 결과. `historical.json`: 별도 과거 증거.',
              '- `data/`, `stage3data/`: 원본 요청·응답·상태·중간 산출물. 원본 Stage1과 이전 캠페인 내용은 수정하지 않았다.',
              '- HTTP 시간은 클라이언트 관측값으로 큐·전송·prefill·decode가 합쳐져 있다. 모델 내부 시간으로 단정하지 않는다.',
              '- 계측 자체의 소량 I/O 오버헤드가 포함된다. 중첩 wall time을 서로 더하면 이중 계산된다.',
              '- 품질 검수·사용자 승인을 수행한 결과가 아니다.', '',
              f'Stage1 보존 확인: `{summary.get("stage1_unchanged", "pending")}`. 제품 코드 보존 확인: `{summary.get("source_code_unchanged", "pending")}`.', '']
    s3_preserved = summary.get('stage3_upstreams_unchanged', corrected.get('upstreams_unchanged', 'pending'))
    lines += [f'Stage3 입력 보존 확인: `{s3_preserved}`.', '']
    (run/'report.md').write_text('\n'.join(lines))
    if not quiet:
        print(json.dumps([{'video': r['video_id'], **{s: {k: r[s].get(k) for k in ('status', 'wall_seconds', 'http_calls', 'http_seconds')} for s in ('stage2', 'stage3')}} for r in rows], indent=2))


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
