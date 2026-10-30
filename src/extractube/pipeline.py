from __future__ import annotations
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from extractube import infer, media, sentences, store
from extractube.errors import PipelineError
from extractube.infer import ASR_FORM, SERVER_DEFAULTS
from extractube.sentences import POLICY_ID, SentenceError
from extractube.settings import Settings, VisionSettings

Log = Callable[[str], None]


@dataclass
class VisionResult:
    video_id: str
    work_dir: Path
    run_id: str
    execution_status: str
    handoff_path: Path | None
    stage3_usable: bool
    warnings: list[dict]
    inference_calls: int
    reused_jobs: int


@dataclass
class StageOutcome:
    reused: bool
    record: dict[str, Any]


@dataclass
class RunResult:
    video_id: str
    work_dir: Path
    stages: dict[str, StageOutcome] = field(default_factory=dict)
    empty_sentences: bool = False
    asr_requests: int = 0
    fa_requests: int = 0
    audio_to_video_offset_seconds: float | None = None
    vision: VisionResult | None = None


def run(
    url: str,
    settings: Settings,
    *,
    through: str = "stage1",
    vision_settings: VisionSettings | None = None,
    force: bool = False,
    from_stage: str | None = None,
    client: Any | None = None,
    video_id: str | None = None,
    fault: str | None = None,
    log: Log | None = print,
) -> RunResult:
    if through not in ("stage1", "stage2"):
        raise PipelineError("through 값이 잘못되었습니다.", kind="usage")
    if force and from_stage:
        raise PipelineError("--force와 --from은 함께 지정할 수 없습니다.", kind="usage")
    if from_stage is not None and from_stage not in store.STAGES:
        raise PipelineError(f"알 수 없는 재처리 단계입니다: {from_stage}", kind="usage")

    if video_id is None:
        video_id = media.resolve_video_id(url, settings)
    else:
        video_id = media.validate_video_id(video_id)

    work = settings.data_root / video_id
    work.mkdir(parents=True, exist_ok=True)
    (work / "source").mkdir(exist_ok=True)
    (work / "audio").mkdir(exist_ok=True)
    lock = store.WorkLock(work)
    try:
        store.recover_interrupted_commit(work)
        meta = store.load_metadata(work, video_id, url)
        infer_client = client if client is not None else infer.HttpInferClient(settings)
        outcomes: dict[str, StageOutcome] = {}
        start_index = store.STAGES.index(from_stage) if from_stage else 0
        for index, stage in enumerate(store.STAGES):
            upstream = _upstream(stage, outcomes)
            if from_stage is not None and index < start_index:
                if not _reusable(work, meta, stage, settings, video_id, upstream):
                    raise PipelineError(
                        f"--from {from_stage}에 필요한 {stage} 결과가 유효하지 않습니다.",
                        kind="upstream",
                    )
                outcomes[stage] = StageOutcome(True, meta["results"][stage])
                _emit(log, stage, reused=True, elapsed=0)
                continue
            must_run = force or (from_stage is not None and index >= start_index)
            if not must_run and _reusable(work, meta, stage, settings, video_id, upstream):
                outcomes[stage] = StageOutcome(True, meta["results"][stage])
                _emit(log, stage, reused=True, elapsed=0)
                continue
            started = time.perf_counter()
            meta = _execute(
                stage,
                url,
                work,
                meta,
                settings,
                video_id,
                outcomes,
                infer_client,
                fault,
            )
            outcomes[stage] = StageOutcome(False, meta["results"][stage])
            _emit(log, stage, reused=False, elapsed=time.perf_counter() - started)

        sentence_record = outcomes["sentences"].record
        sentence_payload = json.loads((work / sentence_record["path"]).read_text(encoding="utf-8"))
        chunks = sentence_payload.get("chunks")
        empty = isinstance(chunks, list) and len(chunks) == 0
        offset = meta.get("audio_to_video_offset_seconds")
        result = RunResult(
            video_id=video_id,
            work_dir=work,
            stages=outcomes,
            empty_sentences=empty,
            asr_requests=getattr(infer_client, "asr_requests", 0),
            fa_requests=getattr(infer_client, "fa_requests", 0),
            audio_to_video_offset_seconds=offset if isinstance(offset, (int, float)) else None,
        )
    finally:
        lock.release()

    if through == "stage2":
        from extractube.vision import run as run_visual

        expected = {
            kind: store.identity(result.stages[kind].record)
            for kind in ("video", "sentences")
        }
        result.vision = run_visual(
            result.video_id,
            vision_settings or VisionSettings(infer_base_url=settings.infer_base_url),
            data_root=settings.data_root,
            force=force,
            expected_inputs=expected,
            log=log,
        )
    return result


def _emit(log: Log | None, stage: str, *, reused: bool, elapsed: float) -> None:
    if log is None:
        return
    if reused:
        log(f"[{stage}] 재사용")
    else:
        log(f"[{stage}] 실행 {elapsed:.1f}s")


def _upstream(stage: str, outcomes: dict[str, StageOutcome]) -> list[dict[str, str]]:
    if stage == "video":
        return []
    if stage == "wav":
        needed = ("video",)
    elif stage == "asr":
        needed = ("wav",)
    elif stage == "fa":
        needed = ("wav", "asr")
    else:
        needed = ("fa",)
    return [store.identity(outcomes[name].record) for name in needed]


def _expected_settings(stage: str, settings: Settings, video_id: str) -> dict[str, Any]:
    if stage == "video":
        return {
            "video_id": video_id,
            "format": settings.yt_dlp_format,
            "merge_output_format": settings.merge_format,
            "noplaylist": True,
        }
    if stage == "wav":
        return {
            "sample_rate": media.PCM_SAMPLE_RATE,
            "channels": media.PCM_CHANNELS,
            "codec": "pcm_s16le",
            "full_audio": True,
        }
    if stage == "asr":
        return {
            "infer_model_id": settings.asr_model_id,
            "form": {**ASR_FORM, "language": settings.language},
        }
    if stage == "fa":
        return {"infer_model_id": settings.fa_model_id}
    return {"sentence_policy_id": POLICY_ID}


def _reusable(
    work: Path,
    meta: dict[str, Any],
    stage: str,
    settings: Settings,
    video_id: str,
    upstream: list[dict[str, str]],
) -> bool:
    record = meta.get("results", {}).get(stage)
    if not isinstance(record, dict):
        return False
    relative = record.get("path")
    if not isinstance(relative, str):
        return False
    path = work / relative
    if not path.is_file():
        return False
    try:
        if store.sha256_file(path) != record.get("sha256"):
            return False
    except OSError:
        return False
    if record.get("upstream") != upstream:
        return False
    if record.get("settings") != _expected_settings(stage, settings, video_id):
        return False
    try:
        _validate_structure(work, meta, stage, path)
    except PipelineError:
        return False
    return True


def _validate_structure(work: Path, meta: dict[str, Any], stage: str, path: Path) -> None:
    if stage == "video":
        media.probe(path)
        return
    if stage == "wav":
        media.inspect_wav(path)
        return
    if stage == "asr":
        wav_path = work / meta["results"]["wav"]["path"]
        info = media.inspect_wav(wav_path)
        infer.validate_asr(json.loads(path.read_text(encoding="utf-8")), info.num_samples)
        return
    if stage == "fa":
        asr_payload = json.loads((work / meta["results"]["asr"]["path"]).read_text(encoding="utf-8"))
        infer.validate_alignment(asr_payload, json.loads(path.read_text(encoding="utf-8")))
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PipelineError("문장 결과가 객체가 아닙니다.", kind="validation")
    if payload.get("sentence_policy_id") != POLICY_ID:
        raise PipelineError("문장 정책 id가 다릅니다.", kind="validation")
    if payload.get("time_reference") != "audio_start":
        raise PipelineError("문장 time_reference가 audio_start가 아닙니다.", kind="validation")
    if payload.get("overlap_deduplicated") is not True:
        raise PipelineError("문장 overlap_deduplicated가 true가 아닙니다.", kind="validation")
    if not isinstance(payload.get("audio"), dict):
        raise PipelineError("문장 audio가 없습니다.", kind="validation")
    if not isinstance(payload.get("chunks"), list) or not isinstance(payload.get("omitted_chunks"), list):
        raise PipelineError("문장 chunks가 없습니다.", kind="validation")


def _execute(
    stage: str,
    url: str,
    work: Path,
    meta: dict[str, Any],
    settings: Settings,
    video_id: str,
    outcomes: dict[str, StageOutcome],
    client: Any,
    fault: str | None,
) -> dict[str, Any]:
    if stage == "video":
        return _execute_video(url, work, meta, settings, video_id, fault)
    if stage == "wav":
        return _execute_wav(work, meta, settings, video_id, outcomes, fault)
    if stage == "asr":
        return _execute_asr(work, meta, settings, video_id, outcomes, client, fault)
    if stage == "fa":
        return _execute_fa(work, meta, settings, video_id, outcomes, client, fault)
    return _execute_sentences(work, meta, settings, video_id, outcomes, fault)


def _commit(
    work: Path,
    relative: str,
    temp: Path,
    metadata: dict[str, Any],
    fault: str | None,
) -> None:
    store.commit_files(work, relative, temp, metadata, fault=fault)


def _execute_video(
    url: str,
    work: Path,
    meta: dict[str, Any],
    settings: Settings,
    video_id: str,
    fault: str | None,
) -> dict[str, Any]:
    incoming = work / "source" / f".incoming-{uuid.uuid4().hex}"
    try:
        downloaded = media.download_video(url, settings, incoming)
        if downloaded.video_id != video_id:
            raise PipelineError(
                f"다운로드한 video ID가 다릅니다: {downloaded.video_id}",
                kind="media",
            )
        probed = media.probe(downloaded.path)
        relative = f"source/video.{downloaded.ext}"
        media_info = _present(
            format_id=downloaded.format_id,
            ext=downloaded.ext,
            vcodec=probed.vcodec or downloaded.vcodec,
            acodec=probed.acodec or downloaded.acodec,
            height=probed.height if probed.height is not None else downloaded.height,
            video_start_time_seconds=probed.video_start_time_seconds,
            source_audio_start_time_seconds=probed.source_audio_start_time_seconds,
            audio_to_video_offset_seconds=probed.audio_to_video_offset_seconds,
        )
        record = store.result_record(
            kind="video",
            relative_path=relative,
            file_hash=store.sha256_file(downloaded.path),
            upstream=[],
            settings=_expected_settings("video", settings, video_id),
            media=media_info,
        )
        updated = json.loads(json.dumps(meta))
        _remember_source(updated, downloaded, probed)
        updated = store.apply_result(updated, "video", record)
        _commit(work, relative, downloaded.path, updated, fault)
        return updated
    finally:
        shutil.rmtree(incoming, ignore_errors=True)


def _remember_source(meta: dict[str, Any], downloaded: media.DownloadedVideo, probed: media.Probe) -> None:
    if downloaded.title is not None:
        meta["title"] = downloaded.title
    if downloaded.webpage_url is not None:
        meta["webpage_url"] = downloaded.webpage_url
    if downloaded.duration is not None:
        meta["duration_seconds"] = downloaded.duration
    meta["video_start_time_seconds"] = probed.video_start_time_seconds
    meta["source_audio_start_time_seconds"] = probed.source_audio_start_time_seconds
    meta["audio_to_video_offset_seconds"] = probed.audio_to_video_offset_seconds


def _execute_wav(
    work: Path,
    meta: dict[str, Any],
    settings: Settings,
    video_id: str,
    outcomes: dict[str, StageOutcome],
    fault: str | None,
) -> dict[str, Any]:
    video = work / outcomes["video"].record["path"]
    temp = work / "source" / f".tmp-{uuid.uuid4().hex}.wav"
    try:
        media.extract_wav(video, temp)
        info = media.inspect_wav(temp)
        relative = "source/source.wav"
        record = store.result_record(
            kind="wav",
            relative_path=relative,
            file_hash=store.sha256_file(temp),
            upstream=_upstream("wav", outcomes),
            settings=_expected_settings("wav", settings, video_id),
            media={
                "sample_rate": info.sample_rate,
                "channels": info.channels,
                "sample_width": info.sample_width,
                "num_samples": info.num_samples,
            },
        )
        updated = store.apply_result(meta, "wav", record)
        _commit(work, relative, temp, updated, fault)
        return updated
    finally:
        if temp.exists():
            temp.unlink()


def _execute_asr(
    work: Path,
    meta: dict[str, Any],
    settings: Settings,
    video_id: str,
    outcomes: dict[str, StageOutcome],
    client: Any,
    fault: str | None,
) -> dict[str, Any]:
    wav_path = work / outcomes["wav"].record["path"]
    info = media.inspect_wav(wav_path)
    response = _call(work, "asr", lambda: client.transcribe(wav_path))
    if response.status != 200:
        _fail_http(work, "asr", response.status, response.body)
    try:
        payload = infer.parse_json_object(response.body, "ASR")
        infer.validate_asr(payload, info.num_samples)
    except PipelineError as exc:
        _fail_validation(work, "asr", response.body, exc)
    temp = work / "audio" / f".tmp-{uuid.uuid4().hex}.json"
    temp.write_bytes(response.body)
    record = store.result_record(
        kind="asr",
        relative_path="audio/asr.json",
        file_hash=store.sha256_file(temp),
        upstream=_upstream("asr", outcomes),
        settings=_expected_settings("asr", settings, video_id),
        extra={"server_defaults": dict(SERVER_DEFAULTS)},
    )
    updated = store.apply_result(meta, "asr", record)
    _commit(work, "audio/asr.json", temp, updated, fault)
    return updated


def _execute_fa(
    work: Path,
    meta: dict[str, Any],
    settings: Settings,
    video_id: str,
    outcomes: dict[str, StageOutcome],
    client: Any,
    fault: str | None,
) -> dict[str, Any]:
    wav_path = work / outcomes["wav"].record["path"]
    asr_path = work / outcomes["asr"].record["path"]
    asr_bytes = asr_path.read_bytes()
    asr_payload = infer.parse_json_object(asr_bytes, "ASR")
    try:
        infer.guard_fa_duration(asr_payload)
    except PipelineError as exc:
        path = store.write_diagnostic(
            work,
            infer.new_attempt_id(),
            {
                "stage": "fa",
                "http_status": None,
                "source": "extractube",
                "reason": str(exc),
            },
            None,
        )
        raise PipelineError(str(exc), kind=exc.kind, diagnostic=path) from exc
    response = _call(work, "fa", lambda: client.align(wav_path, asr_bytes))
    if response.status != 200:
        _fail_http(work, "fa", response.status, response.body)
    try:
        fa_payload = infer.parse_json_object(response.body, "FA")
        infer.validate_alignment(asr_payload, fa_payload)
    except PipelineError as exc:
        _fail_validation(work, "fa", response.body, exc)
    temp = work / "audio" / f".tmp-{uuid.uuid4().hex}.json"
    temp.write_bytes(response.body)
    record = store.result_record(
        kind="fa",
        relative_path="audio/alignment.json",
        file_hash=store.sha256_file(temp),
        upstream=_upstream("fa", outcomes),
        settings=_expected_settings("fa", settings, video_id),
    )
    updated = store.apply_result(meta, "fa", record)
    _commit(work, "audio/alignment.json", temp, updated, fault)
    return updated


def _execute_sentences(
    work: Path,
    meta: dict[str, Any],
    settings: Settings,
    video_id: str,
    outcomes: dict[str, StageOutcome],
    fault: str | None,
) -> dict[str, Any]:
    fa_path = work / outcomes["fa"].record["path"]
    fa_payload = infer.parse_json_object(fa_path.read_bytes(), "FA")
    try:
        converted = sentences.convert(fa_payload)
    except SentenceError as exc:
        raise PipelineError(
            f"문장 변환에 실패했습니다. alignment.json은 유지합니다. {exc}",
            kind="sentences",
        ) from exc
    temp = work / "audio" / f".tmp-{uuid.uuid4().hex}.json"
    temp.write_text(store.dumps(converted), encoding="utf-8")
    record = store.result_record(
        kind="sentences",
        relative_path="audio/alignment_sentences.json",
        file_hash=store.sha256_file(temp),
        upstream=_upstream("sentences", outcomes),
        settings=_expected_settings("sentences", settings, video_id),
    )
    updated = store.apply_result(meta, "sentences", record)
    _commit(work, "audio/alignment_sentences.json", temp, updated, fault)
    return updated


def _call(work: Path, stage: str, action: Callable[[], infer.InferResponse]) -> infer.InferResponse:
    try:
        return action()
    except TimeoutError as exc:
        path = _write_call_failure(
            work,
            stage,
            "timeout",
            "요청이 시간 초과되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다.",
        )
        raise PipelineError(
            "요청이 시간 초과되었습니다. 서버의 추론은 계속 처리 중일 수 있습니다.",
            kind="timeout",
            diagnostic=path,
        ) from exc
    except PipelineError as exc:
        if exc.kind not in {"timeout", "network", "body"}:
            raise
        path = _write_call_failure(work, stage, exc.kind, str(exc))
        raise PipelineError(str(exc), kind=exc.kind, diagnostic=path) from exc


def _write_call_failure(work: Path, stage: str, source: str, reason: str) -> Path:
    note = "서버의 추론은 계속 처리 중일 수 있습니다." if source == "timeout" else None
    return store.write_diagnostic(
        work,
        infer.new_attempt_id(),
        {
            "stage": stage,
            "http_status": None,
            "source": source,
            "reason": reason,
            "note": note,
        },
        None,
    )


def _fail_http(work: Path, stage: str, status: int, body: bytes) -> None:
    source, reason = infer.error_source(body)
    path = store.write_diagnostic(
        work,
        infer.new_attempt_id(),
        {
            "stage": stage,
            "http_status": status,
            "source": source,
            "reason": reason,
        },
        body,
    )
    raise PipelineError(
        f"{stage} 요청이 실패했습니다. HTTP {status}. 자동 재요청하지 않습니다.",
        kind="http",
        diagnostic=path,
    )


def _fail_validation(work: Path, stage: str, body: bytes, exc: PipelineError) -> None:
    path = store.write_diagnostic(
        work,
        infer.new_attempt_id(),
        {
            "stage": stage,
            "http_status": 200,
            "source": "validation",
            "reason": str(exc),
        },
        body,
    )
    raise PipelineError(
        f"{stage} 응답이 승격 검사를 통과하지 못했습니다.",
        kind="validation",
        diagnostic=path,
    ) from exc


def _present(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
