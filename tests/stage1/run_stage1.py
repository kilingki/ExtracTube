#!/usr/bin/env python3
"""Stage 1 checks. Synthetic failures use the real pipeline; the live section uses YouTube."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from extractube import media, sentences, store
from extractube.errors import PipelineError
from extractube.infer import HttpInferClient, InferResponse, validate_alignment
from extractube.pipeline import _expected_settings, run
from extractube.settings import Settings

ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "outputs" / "stage1_report.json"
# Live YouTube check. Change this URL only; the work directory follows its video ID.
LIVE_URL = "https://www.youtube.com/watch?v=cqklrHin_XA"
CHECKS: list[dict[str, Any]] = []


def check(name: str, ok: bool, **detail: Any) -> None:
    CHECKS.append({"name": name, "ok": bool(ok), **detail})
    state = "ok" if ok else "FAIL"
    print(f"[{state}] {name}")


def dumps(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def make_video(path: Path, seconds: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=160x120:rate=10:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=16000:duration={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-500:])


def make_wav(path: Path, seconds: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=16000:cl=mono",
            "-t",
            str(seconds),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-500:])


def seed_media(root: Path, video_id: str, *, wav_seconds: int | None = None) -> tuple[Settings, int]:
    settings = Settings(data_root=root)
    work = root / video_id
    work.mkdir(parents=True)
    (work / "source").mkdir()
    (work / "audio").mkdir()
    video_tmp = work / "source" / ".seed-video.mp4"
    make_video(video_tmp, 1)
    probed = media.probe(video_tmp)
    meta: dict[str, Any] = {
        "video_id": video_id,
        "source_url": "https://example.invalid/watch?v=" + video_id,
        "results": {},
        "video_start_time_seconds": probed.video_start_time_seconds,
        "source_audio_start_time_seconds": probed.source_audio_start_time_seconds,
        "audio_to_video_offset_seconds": probed.audio_to_video_offset_seconds,
    }
    video_record = store.result_record(
        kind="video",
        relative_path="source/video.mp4",
        file_hash=store.sha256_file(video_tmp),
        upstream=[],
        settings=_expected_settings("video", settings, video_id),
        media={
            "ext": "mp4",
            "video_start_time_seconds": probed.video_start_time_seconds,
            "source_audio_start_time_seconds": probed.source_audio_start_time_seconds,
            "audio_to_video_offset_seconds": probed.audio_to_video_offset_seconds,
        },
    )
    meta = store.apply_result(meta, "video", video_record)
    store.commit_files(work, "source/video.mp4", video_tmp, meta)

    if wav_seconds is None:
        wav_tmp = work / "source" / ".seed.wav"
        media.extract_wav(work / "source" / "video.mp4", wav_tmp)
    else:
        wav_tmp = work / "source" / ".seed.wav"
        make_wav(wav_tmp, wav_seconds)
    info = media.inspect_wav(wav_tmp)
    meta = store.load_metadata(work, video_id, meta["source_url"])
    wav_record = store.result_record(
        kind="wav",
        relative_path="source/source.wav",
        file_hash=store.sha256_file(wav_tmp),
        upstream=[store.identity(meta["results"]["video"])],
        settings=_expected_settings("wav", settings, video_id),
        media={
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "sample_width": info.sample_width,
            "num_samples": info.num_samples,
        },
    )
    meta = store.apply_result(meta, "wav", wav_record)
    store.commit_files(work, "source/source.wav", wav_tmp, meta)
    return settings, info.num_samples


def asr_payload(num_samples: int, chunks: list[dict[str, Any]] | None = None, text: str = "안녕.") -> dict[str, Any]:
    if chunks is None:
        chunks = [
            {
                "index": 0,
                "start_sample": 0,
                "end_sample": num_samples,
                "text": text,
                "language": "ko",
            }
        ]
    return {
        "task": "transcribe",
        "language": "ko",
        "duration": num_samples / 16000,
        "text": text,
        "segments": [],
        "audio": {"sample_rate": 16000, "channels": 1, "num_samples": num_samples},
        "chunks": chunks,
    }


def fa_payload(
    num_samples: int,
    *,
    text: str = "안녕.",
    items: list[dict[str, Any]] | None = None,
    status: str = "aligned",
    language: str = "Korean",
) -> dict[str, Any]:
    if items is None:
        items = [{"text": "안녕", "start_time": 0.0, "end_time": min(0.4, max(num_samples, 1) / 16000)}]
    chunk: dict[str, Any] = {
        "index": 0,
        "start_sample": 0,
        "end_sample": num_samples,
        "text": text,
        "status": status,
        "items": items,
    }
    if status == "aligned":
        chunk["language"] = language
    return {
        "audio": {"sample_rate": 16000, "channels": 1, "num_samples": num_samples},
        "time_reference": "audio_start",
        "overlap_deduplicated": False,
        "chunks": [chunk],
    }


class FakeInfer:
    def __init__(
        self,
        asr_body: bytes,
        fa_body: bytes,
        *,
        asr_status: int = 200,
        fa_status: int = 200,
        asr_exc: BaseException | None = None,
    ) -> None:
        self.asr_body = asr_body
        self.fa_body = fa_body
        self.asr_status = asr_status
        self.fa_status = fa_status
        self.asr_exc = asr_exc
        self.asr_requests = 0
        self.fa_requests = 0
        self.payloads: list[bytes] = []

    def transcribe(self, wav_path: Path) -> InferResponse:
        self.asr_requests += 1
        if self.asr_exc is not None:
            raise self.asr_exc
        return InferResponse(self.asr_status, self.asr_body)

    def align(self, wav_path: Path, payload: bytes) -> InferResponse:
        self.fa_requests += 1
        self.payloads.append(payload)
        return InferResponse(self.fa_status, self.fa_body)


def run_id(root: Path, video_id: str, settings: Settings, client: FakeInfer, **kwargs: Any):
    return run(
        "https://example.invalid/watch?v=" + video_id,
        settings,
        client=client,
        video_id=video_id,
        log=None,
        **kwargs,
    )


def test_alignment_validation() -> None:
    num_samples = 16000
    asr = asr_payload(num_samples)

    def expect(name: str, fa: dict[str, Any], ok: bool) -> None:
        kind = ""
        try:
            validate_alignment(asr, fa)
        except PipelineError as exc:
            kind = exc.kind
        check(name, kind == ("" if ok else "validation"), kind=kind)

    expect("fa_audio_matches_asr", fa_payload(num_samples), True)
    for field, value in (("sample_rate", 8000), ("channels", 2), ("num_samples", num_samples - 1)):
        mismatched = fa_payload(num_samples)
        mismatched["audio"][field] = value
        expect(f"fa_audio_{field}_mismatch", mismatched, False)

    missing_audio = fa_payload(num_samples)
    del missing_audio["audio"]
    expect("fa_audio_missing", missing_audio, False)

    not_object = fa_payload(num_samples)
    not_object["audio"] = []
    expect("fa_audio_not_object", not_object, False)

    missing_field = fa_payload(num_samples)
    del missing_field["audio"]["sample_rate"]
    expect("fa_audio_field_missing", missing_field, False)

    expect(
        "fa_item_start_reversed",
        fa_payload(
            num_samples,
            items=[
                {"text": "가", "start_time": 0.5, "end_time": 0.6},
                {"text": "나", "start_time": 0.3, "end_time": 0.4},
            ],
        ),
        False,
    )
    expect(
        "fa_item_end_reversed",
        fa_payload(
            num_samples,
            items=[
                {"text": "가", "start_time": 0.1, "end_time": 0.4},
                {"text": "나", "start_time": 0.2, "end_time": 0.3},
            ],
        ),
        False,
    )
    expect(
        "fa_item_equal_times",
        fa_payload(
            num_samples,
            items=[
                {"text": "가", "start_time": 0.2, "end_time": 0.4},
                {"text": "나", "start_time": 0.2, "end_time": 0.4},
            ],
        ),
        True,
    )


def test_sentences() -> None:
    audio = {"sample_rate": 16000, "channels": 1, "num_samples": 48000}

    def chunk(index: int, start: int, end: int, text: str, items: list[dict[str, Any]], status: str = "aligned") -> dict[str, Any]:
        body = {
            "index": index,
            "start_sample": start,
            "end_sample": end,
            "text": text,
            "status": status,
            "items": items,
        }
        if status == "aligned":
            body["language"] = "ko"
        return body

    quote = sentences.convert(
        {
            "audio": audio,
            "chunks": [chunk(0, 0, 16000, '안녕."', [{"text": "안녕", "start_time": 0.1, "end_time": 0.4}])],
        }
    )
    check("sentence_closing_quote", quote["chunks"][0]["text"] == '안녕."' and len(quote["chunks"]) == 1)

    decimal = sentences.convert(
        {
            "audio": audio,
            "chunks": [
                chunk(
                    0,
                    0,
                    16000,
                    "값은 3.14이다.",
                    [
                        {"text": "값은", "start_time": 0.1, "end_time": 0.2},
                        {"text": "3", "start_time": 0.2, "end_time": 0.3},
                        {"text": "14", "start_time": 0.3, "end_time": 0.4},
                        {"text": "이다", "start_time": 0.4, "end_time": 0.5},
                    ],
                )
            ],
        }
    )
    check("sentence_decimal", len(decimal["chunks"]) == 1 and decimal["chunks"][0]["text"] == "값은 3.14이다.")

    plus = sentences.convert(
        {
            "audio": audio,
            "chunks": [chunk(0, 0, 16000, "S+급.", [{"text": "S급", "start_time": 0.1, "end_time": 0.4}])],
        }
    )
    check("sentence_plus_kept", len(plus["chunks"]) == 1 and plus["chunks"][0]["text"] == "S+급.")

    ellipsis = sentences.convert(
        {
            "audio": audio,
            "chunks": [
                chunk(
                    0,
                    0,
                    16000,
                    "잠깐... 계속.",
                    [
                        {"text": "잠깐", "start_time": 0.1, "end_time": 0.2},
                        {"text": "계속", "start_time": 0.3, "end_time": 0.5},
                    ],
                )
            ],
        }
    )
    check("sentence_ellipsis", len(ellipsis["chunks"]) == 1 and "계속." in ellipsis["chunks"][0]["text"])

    tail = sentences.convert(
        {
            "audio": audio,
            "chunks": [chunk(0, 0, 16000, "계속", [{"text": "계속", "start_time": 0.1, "end_time": 0.2}])],
        }
    )
    check("sentence_last_buffer", tail["chunks"][0]["text"] == "계속")

    overlap = sentences.convert(
        {
            "audio": audio,
            "chunks": [
                chunk(
                    0,
                    0,
                    32000,
                    "가나다.",
                    [
                        {"text": "가나", "start_time": 0.6, "end_time": 0.7},
                        {"text": "다", "start_time": 0.8, "end_time": 0.9},
                    ],
                ),
                chunk(
                    1,
                    16000,
                    48000,
                    "다라마.",
                    [
                        {"text": "다", "start_time": 1.1, "end_time": 1.2},
                        {"text": "라마", "start_time": 1.3, "end_time": 1.5},
                    ],
                ),
            ],
        }
    )
    joined = "".join(item["text"] for item in overlap["chunks"][0]["items"])
    check("sentence_overlap_longest", joined == "가나다라마", items=joined)

    early = sentences.convert(
        {
            "audio": audio,
            "chunks": [
                chunk(
                    0,
                    0,
                    32000,
                    "가나다.",
                    [
                        {"text": "가나", "start_time": 0.1, "end_time": 0.2},
                        {"text": "다", "start_time": 0.2, "end_time": 0.3},
                    ],
                ),
                chunk(
                    1,
                    16000,
                    48000,
                    "다라마.",
                    [
                        {"text": "다", "start_time": 1.1, "end_time": 1.2},
                        {"text": "라마", "start_time": 1.3, "end_time": 1.5},
                    ],
                ),
            ],
        }
    )
    early_items = [item["text"] for item in early["chunks"][0]["items"]]
    check("sentence_overlap_time_fallback", early_items[:2] == ["가나", "다"], items=early_items)

    kept = sentences.convert(
        {
            "audio": audio,
            "chunks": [
                chunk(0, 0, 16000, "다시.", [{"text": "다시", "start_time": 0.1, "end_time": 0.2}]),
                chunk(1, 16000, 32000, "다시.", [{"text": "다시", "start_time": 1.1, "end_time": 1.2}]),
            ],
        }
    )
    repeated = [item["text"] for chunk in kept["chunks"] for item in chunk["items"]]
    check("sentence_repeat_outside_overlap", repeated == ["다시", "다시"], items=repeated)

    omitted_doc = sentences.convert(
        {
            "audio": audio,
            "chunks": [
                chunk(0, 0, 8000, "   ", [], status="skipped_empty_text"),
                chunk(1, 8000, 16000, "남았다.", [{"text": "남았다", "start_time": 0.6, "end_time": 0.8}]),
            ],
        }
    )
    check(
        "sentence_omitted_and_policy",
        omitted_doc["omitted_chunks"] == [
            {"index": 0, "status": "skipped_empty_text", "start_sample": 0, "end_sample": 8000}
        ]
        and omitted_doc["sentence_policy_id"] == sentences.POLICY_ID
        and omitted_doc["time_reference"] == "audio_start"
        and omitted_doc["overlap_deduplicated"] is True,
    )

    empty = sentences.convert(
        {
            "audio": audio,
            "chunks": [chunk(0, 0, 16000, " ", [], status="skipped_empty_text")],
        }
    )
    check("sentence_zero", empty["chunks"] == [] and len(empty["omitted_chunks"]) == 1)

    raised = False
    try:
        sentences.convert(
            {
                "audio": audio,
                "chunks": [chunk(0, 0, 16000, "abc", [{"text": "zzz", "start_time": 0.1, "end_time": 0.2}])],
            }
        )
    except sentences.SentenceError:
        raised = True
    check("sentence_mismatch_raises", raised)

    # FA omits the separator from items; preserve it in source sentence text.
    math_items = [
        {"text": "a", "start_time": 0.1, "end_time": 0.2},
        {"text": "b는", "start_time": 0.3, "end_time": 0.4},
        {"text": "c다", "start_time": 0.5, "end_time": 0.6},
    ]
    math_doc = sentences.convert({
        "audio": audio,
        "chunks": [chunk(0, 0, 16000, "a-b는 c다.", math_items)],
    })
    check("sentence_math_hyphen_preserved",
          math_doc["chunks"][0]["text"] == "a-b는 c다."
          and math_doc["chunks"][0]["items"] == math_items)



def test_pipeline(tmp: Path) -> None:
    settings, num_samples = seed_media(tmp, "pipevid")
    asr = dumps(asr_payload(num_samples))
    fa = dumps(fa_payload(num_samples))
    client = FakeInfer(asr, fa)
    result = run_id(tmp, "pipevid", settings, client, from_stage="asr")
    asr_bytes = (tmp / "pipevid" / "audio" / "asr.json").read_bytes()
    check(
        "pipeline_happy",
        result.asr_requests == 1
        and result.fa_requests == 1
        and client.payloads == [asr_bytes]
        and result.empty_sentences is False
        and (tmp / "pipevid" / "audio" / "alignment_sentences.json").is_file(),
        asr_requests=result.asr_requests,
        fa_requests=result.fa_requests,
    )
    first_ids = {name: outcome.record["result_id"] for name, outcome in result.stages.items()}

    again = FakeInfer(asr, fa)
    reused = run_id(tmp, "pipevid", settings, again)
    check(
        "pipeline_reuse_no_infer",
        again.asr_requests == 0
        and again.fa_requests == 0
        and reused.stages["asr"].record["result_id"] == first_ids["asr"]
        and reused.stages["fa"].record["result_id"] == first_ids["fa"],
        asr_requests=again.asr_requests,
        fa_requests=again.fa_requests,
    )

    sentence_path = tmp / "pipevid" / "audio" / "alignment_sentences.json"
    original_sentence = sentence_path.read_bytes()
    sentence_path.write_bytes(original_sentence + b" ")
    sentences_only = FakeInfer(asr, fa)
    regenerated = run_id(tmp, "pipevid", settings, sentences_only)
    check(
        "pipeline_sentences_only",
        sentences_only.asr_requests == 0
        and sentences_only.fa_requests == 0
        and regenerated.stages["sentences"].record["result_id"] != first_ids["sentences"]
        and regenerated.stages["fa"].record["result_id"] == first_ids["fa"],
        asr_requests=sentences_only.asr_requests,
        fa_requests=sentences_only.fa_requests,
    )

    from_fa = FakeInfer(asr, fa)
    fa_again = run_id(tmp, "pipevid", settings, from_fa, from_stage="fa")
    check(
        "pipeline_from_fa",
        from_fa.asr_requests == 0
        and from_fa.fa_requests == 1
        and fa_again.stages["asr"].record["result_id"] == first_ids["asr"]
        and fa_again.stages["fa"].record["result_id"] != first_ids["fa"]
        and fa_again.stages["sentences"].record["result_id"] != regenerated.stages["sentences"].record["result_id"],
        asr_requests=from_fa.asr_requests,
        fa_requests=from_fa.fa_requests,
    )

    changed = settings.copy(fa_model_id="qwen-fa-alt")
    alt = FakeInfer(asr, fa)
    alt_result = run_id(tmp, "pipevid", changed, alt)
    check(
        "pipeline_setting_invalidates_downstream",
        alt.asr_requests == 0
        and alt.fa_requests == 1
        and alt_result.stages["asr"].record["result_id"] == first_ids["asr"]
        and alt_result.stages["video"].record["result_id"] == first_ids["video"]
        and alt_result.stages["fa"].record["result_id"] != fa_again.stages["fa"].record["result_id"],
        asr_requests=alt.asr_requests,
        fa_requests=alt.fa_requests,
    )

    # Restore the original model id with one more FA so later checks share a valid tree.
    restored = FakeInfer(asr, fa)
    current = run_id(tmp, "pipevid", settings, restored, from_stage="fa")
    before_sentence = (tmp / "pipevid" / "audio" / "alignment_sentences.json").read_bytes()
    bad_fa = dumps(
        fa_payload(
            num_samples,
            text="안녕.",
            items=[{"text": "zzz", "start_time": 0.0, "end_time": 0.2}],
        )
    )
    mismatch = FakeInfer(asr, bad_fa)
    mismatch_kind = ""
    try:
        run_id(tmp, "pipevid", settings, mismatch, from_stage="fa")
    except PipelineError as exc:
        mismatch_kind = exc.kind
    meta_after = json.loads((tmp / "pipevid" / "metadata.json").read_text(encoding="utf-8"))
    check(
        "pipeline_sentence_mismatch_keeps_alignment",
        mismatch_kind == "sentences"
        and mismatch.fa_requests == 1
        and "sentences" not in meta_after["results"]
        and (tmp / "pipevid" / "audio" / "alignment.json").read_bytes() == bad_fa
        and (tmp / "pipevid" / "audio" / "alignment_sentences.json").read_bytes() == before_sentence,
        kind=mismatch_kind,
    )
    # The failed FA commit cleared the sentence record. Rebuild FA and sentences.
    rebuild = FakeInfer(asr, fa)
    run_id(tmp, "pipevid", settings, rebuild, from_stage="fa")

    meta = json.loads((tmp / "pipevid" / "metadata.json").read_text(encoding="utf-8"))
    meta["results"]["fa"]["sha256"] = "0" * 64
    (tmp / "pipevid" / "metadata.json").write_text(store.dumps(meta), encoding="utf-8")
    broken = FakeInfer(asr, fa)
    upstream_error = False
    try:
        run_id(tmp, "pipevid", settings, broken, from_stage="sentences")
    except PipelineError as exc:
        upstream_error = exc.kind == "upstream"
    check(
        "pipeline_from_sentences_invalid_upstream",
        upstream_error and broken.asr_requests == 0 and broken.fa_requests == 0,
    )
    meta["results"]["fa"]["sha256"] = store.sha256_file(tmp / "pipevid" / "audio" / "alignment.json")
    (tmp / "pipevid" / "metadata.json").write_text(store.dumps(meta), encoding="utf-8")

    asr_before = (tmp / "pipevid" / "audio" / "asr.json").read_bytes()
    bad = FakeInfer(b'{"chunks":[]}', fa)
    bad_raised = False
    try:
        run_id(tmp, "pipevid", settings, bad, from_stage="asr")
    except PipelineError as exc:
        bad_raised = exc.kind == "validation" and exc.diagnostic is not None
    check(
        "pipeline_bad_200_no_promote",
        bad_raised
        and bad.asr_requests == 1
        and bad.fa_requests == 0
        and (tmp / "pipevid" / "audio" / "asr.json").read_bytes() == asr_before,
        asr_requests=bad.asr_requests,
        fa_requests=bad.fa_requests,
    )

    http_body = b'{"error":"nope","src":"inferswap","code":"BAD"}'
    http_fake = FakeInfer(http_body, fa, asr_status=400)
    http_raised = False
    source = ""
    try:
        run_id(tmp, "pipevid", settings, http_fake, from_stage="asr")
    except PipelineError as exc:
        http_raised = exc.kind == "http"
        if exc.diagnostic is not None:
            source = json.loads((exc.diagnostic / "error.json").read_text(encoding="utf-8"))["source"]
    check(
        "pipeline_non_200_once",
        http_raised and http_fake.asr_requests == 1 and http_fake.fa_requests == 0 and source == "inferswap",
        asr_requests=http_fake.asr_requests,
    )

    timeout_fake = FakeInfer(asr, fa, asr_exc=TimeoutError("timed out"))
    timeout_raised = False
    try:
        run_id(tmp, "pipevid", settings, timeout_fake, from_stage="asr")
    except PipelineError as exc:
        timeout_raised = exc.kind == "timeout" and "계속" in str(exc)
    check(
        "pipeline_timeout_once",
        timeout_raised and timeout_fake.asr_requests == 1 and timeout_fake.fa_requests == 0,
        asr_requests=timeout_fake.asr_requests,
    )

    meta_bytes = (tmp / "pipevid" / "metadata.json").read_bytes()
    asr_bytes = (tmp / "pipevid" / "audio" / "asr.json").read_bytes()
    replacement = dumps(asr_payload(num_samples, text="다시."))
    fault_client = FakeInfer(replacement, fa)
    fault_raised = False
    try:
        run_id(tmp, "pipevid", settings, fault_client, from_stage="asr", fault="metadata")
    except PipelineError as exc:
        fault_raised = exc.kind == "commit"
    check(
        "pipeline_metadata_failure_restores",
        fault_raised
        and (tmp / "pipevid" / "metadata.json").read_bytes() == meta_bytes
        and (tmp / "pipevid" / "audio" / "asr.json").read_bytes() == asr_bytes
        and not (tmp / "pipevid" / ".commit-backup").exists(),
    )


def test_redirect_and_body_limit(tmp: Path) -> None:
    settings, num_samples = seed_media(tmp, "httpvid")
    bodies: list[bytes] = []
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            bodies.append(body)
            paths.append(self.path)
            payload = b'{"error":"redirect","src":"inferswap","code":"REDIRECT"}'
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        local = settings.copy(infer_base_url=f"http://127.0.0.1:{port}")
        client = HttpInferClient(local)
        raised = False
        try:
            run(
                "https://example.invalid/watch?v=httpvid",
                local,
                client=client,
                video_id="httpvid",
                from_stage="asr",
                log=None,
            )
        except PipelineError as exc:
            raised = exc.kind == "http" and exc.diagnostic is not None
        posted = bodies[0] if bodies else b""
        check(
            "pipeline_redirect_not_followed",
            raised
            and client.asr_requests == 1
            and client.fa_requests == 0
            and len(bodies) == 1
            and paths == ["/infer?model=qwen-asr"]
            and b'name="response_format"' in posted
            and b"verbose_json" in posted
            and b'name="include_chunks"' in posted
            and b'name="model"' not in posted
            and b'name="language"\r\n\r\nko\r\n' in posted,
            requests=len(bodies),
            asr_requests=client.asr_requests,
        )
    finally:
        server.shutdown()
        server.server_close()

    tiny = settings.copy(max_body_bytes=32)
    client = HttpInferClient(tiny)
    raised = False
    try:
        run(
            "https://example.invalid/watch?v=httpvid",
            tiny,
            client=client,
            video_id="httpvid",
            from_stage="asr",
            log=None,
        )
    except PipelineError as exc:
        raised = exc.kind == "body"
    check("pipeline_body_limit_not_sent", raised and client.asr_requests == 0 and client.fa_requests == 0)


def test_long_chunk(tmp: Path) -> None:
    settings, num_samples = seed_media(tmp, "longvid", wav_seconds=181)
    chunks = [
        {
            "index": 0,
            "start_sample": 0,
            "end_sample": num_samples,
            "text": "길다.",
            "language": "ko",
        }
    ]
    client = FakeInfer(dumps(asr_payload(num_samples, chunks, text="길다.")), b"{}")
    raised = False
    try:
        run_id(tmp, "longvid", settings, client, from_stage="asr")
    except PipelineError as exc:
        raised = exc.kind == "config" and "180" in str(exc)
    check(
        "pipeline_180_seconds_no_fa",
        raised and client.asr_requests == 1 and client.fa_requests == 0,
        asr_requests=client.asr_requests,
        fa_requests=client.fa_requests,
        samples=num_samples,
    )


def test_empty_transcript(tmp: Path) -> None:
    settings, num_samples = seed_media(tmp, "emptyvid")
    asr = asr_payload(
        num_samples,
        chunks=[
            {
                "index": 0,
                "start_sample": 0,
                "end_sample": num_samples,
                "text": "   ",
            }
        ],
        text="",
    )
    fa = {
        "audio": asr["audio"],
        "time_reference": "audio_start",
        "overlap_deduplicated": False,
        "chunks": [
            {
                "index": 0,
                "start_sample": 0,
                "end_sample": num_samples,
                "text": "   ",
                "status": "skipped_empty_text",
                "items": [],
            }
        ],
    }
    client = FakeInfer(dumps(asr), dumps(fa))
    result = run_id(tmp, "emptyvid", settings, client, from_stage="asr")
    check("pipeline_empty_sentences_success", result.empty_sentences is True and result.fa_requests == 1)


def test_commit_crash(tmp: Path) -> None:
    work = tmp / "crashvid"
    audio = work / "audio"
    audio.mkdir(parents=True)
    artifact = audio / "asr.json"
    artifact.write_text('{"old":true}\n', encoding="utf-8")
    meta = work / "metadata.json"
    meta.write_text("{}\n", encoding="utf-8")
    backup = work / ".commit-backup"
    backup.mkdir()
    (backup / "manifest.json").write_text(
        store.dumps(
            {
                "artifact_rel": "audio/asr.json",
                "had_artifact": True,
                "had_metadata": True,
            }
        ),
        encoding="utf-8",
    )
    shutil.copy2(artifact, backup / "artifact")
    shutil.copy2(meta, backup / "metadata.json")
    artifact.write_text('{"new":true}\n', encoding="utf-8")
    meta.write_text('{"results":{}}\n', encoding="utf-8")
    store.recover_interrupted_commit(work)
    check(
        "commit_crash_restores_previous",
        artifact.read_text(encoding="utf-8") == '{"old":true}\n'
        and meta.read_text(encoding="utf-8") == "{}\n"
        and not backup.exists(),
    )


def test_lock(tmp: Path) -> None:
    work = tmp / "lockvid"
    work.mkdir()
    lock = store.WorkLock(work)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "\n".join(
                    [
                        "from pathlib import Path",
                        "from extractube.errors import PipelineError",
                        "from extractube.pipeline import run",
                        "from extractube.settings import Settings",
                        "try:",
                        f"    run('https://example.invalid', Settings(data_root=Path({str(tmp)!r})), video_id='lockvid', from_stage='sentences', log=None)",
                        "except PipelineError as exc:",
                        "    print(exc.kind)",
                        "    raise SystemExit(1)",
                    ]
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        lock.release()
    check(
        "lock_second_run_rejected",
        proc.returncode == 1 and "lock" in proc.stdout,
        stdout=proc.stdout.strip(),
    )


def test_cli_usage() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "extractube", "run", "https://example.invalid", "--force", "--from", "fa"],
        capture_output=True,
        text=True,
        check=False,
    )
    check("cli_force_and_from_rejected", proc.returncode == 2, stderr=proc.stderr.strip())


def test_video_id() -> None:
    rejected = True
    try:
        media.validate_video_id("../etc")
        rejected = False
    except PipelineError:
        rejected = True
    check("video_id_rejects_dotdot", rejected)


def _stage_ids(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if " id=" not in line or "=" not in line:
            continue
        stage = line.split("=", 1)[0]
        marker = "id="
        start = line.find(marker)
        if start < 0:
            continue
        rest = line[start + len(marker) :]
        found[stage] = rest.split(" ", 1)[0]
    return found


def _item_key(item: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (item.get("text"), item.get("start_time"), item.get("end_time"))


def assess_live(work: Path) -> dict[str, Any]:
    """Structural checks for the real video. Transcript wording is reported, not scored."""
    problems: list[str] = []
    meta = json.loads((work / "metadata.json").read_text(encoding="utf-8"))
    results = meta.get("results") if isinstance(meta.get("results"), dict) else {}
    video_record = results.get("video") if isinstance(results.get("video"), dict) else {}
    wav_record = results.get("wav") if isinstance(results.get("wav"), dict) else {}
    video_path = work / str(video_record.get("path", ""))
    wav_path = work / str(wav_record.get("path", ""))
    asr_path = work / "audio" / "asr.json"
    fa_path = work / "audio" / "alignment.json"
    sentence_path = work / "audio" / "alignment_sentences.json"
    for path in (video_path, wav_path, asr_path, fa_path, sentence_path):
        if not path.is_file():
            problems.append(f"missing {path.name}")
    if problems:
        return {"ok": False, "problems": problems, "sample": {}}

    probed = media.probe(video_path)
    wav = media.inspect_wav(wav_path)
    wav_media = wav_record.get("media") if isinstance(wav_record.get("media"), dict) else {}
    if wav.num_samples != wav_media.get("num_samples"):
        problems.append("wav frame count differs from metadata")
    if probed.video_start_time_seconds != meta.get("video_start_time_seconds"):
        problems.append("video start_time differs from metadata")
    if probed.audio_to_video_offset_seconds != meta.get("audio_to_video_offset_seconds"):
        problems.append("offset differs from metadata")
    height = (video_record.get("media") or {}).get("height")
    if isinstance(height, int) and height > 1080:
        problems.append(f"video height {height} exceeds 1080")

    asr = json.loads(asr_path.read_text(encoding="utf-8"))
    fa = json.loads(fa_path.read_text(encoding="utf-8"))
    sentences_doc = json.loads(sentence_path.read_text(encoding="utf-8"))
    for key in ("task", "language", "duration", "text", "segments", "audio", "chunks"):
        if key not in asr:
            problems.append(f"asr missing {key}")
    for key in ("raw", "canonical"):
        if key in asr:
            problems.append(f"asr invented {key}")
    audio = asr.get("audio") if isinstance(asr.get("audio"), dict) else {}
    if audio.get("num_samples") != wav.num_samples:
        problems.append("asr num_samples differs from wav")
    if fa.get("time_reference") != "audio_start" or fa.get("overlap_deduplicated") is not False:
        problems.append("fa time reference is not audio_start")
    if (
        sentences_doc.get("time_reference") != "audio_start"
        or sentences_doc.get("overlap_deduplicated") is not True
        or sentences_doc.get("sentence_policy_id") != sentences.POLICY_ID
    ):
        problems.append("sentence document contract failed")
    for payload in (asr, fa, sentences_doc):
        if "audio_to_video_offset_seconds" in payload:
            problems.append("video offset was written into an audio artifact")

    fa_chunks = fa.get("chunks") if isinstance(fa.get("chunks"), list) else []
    sentence_chunks = sentences_doc.get("chunks") if isinstance(sentences_doc.get("chunks"), list) else []
    fa_items = {
        _item_key(item)
        for chunk in fa_chunks
        if isinstance(chunk, dict)
        for item in chunk.get("items") or []
        if isinstance(item, dict)
    }
    sentence_items = [
        item
        for chunk in sentence_chunks
        if isinstance(chunk, dict)
        for item in chunk.get("items") or []
        if isinstance(item, dict)
    ]
    if sentence_items and any(_item_key(item) not in fa_items for item in sentence_items):
        problems.append("sentence item times are not copied from FA")

    edges = []
    for chunk in fa_chunks:
        if not isinstance(chunk, dict):
            continue
        items = chunk.get("items") if isinstance(chunk.get("items"), list) else []
        edges.append(
            {
                "index": chunk.get("index"),
                "start_sample": chunk.get("start_sample"),
                "end_sample": chunk.get("end_sample"),
                "text": chunk.get("text"),
                "first_item": items[0] if items else None,
                "last_item": items[-1] if items else None,
            }
        )
    sample = {
        "video_id": meta.get("video_id"),
        "duration_seconds": meta.get("duration_seconds"),
        "audio_to_video_offset_seconds": meta.get("audio_to_video_offset_seconds"),
        "video_start_time_seconds": meta.get("video_start_time_seconds"),
        "source_audio_start_time_seconds": meta.get("source_audio_start_time_seconds"),
        "time_reference": sentences_doc.get("time_reference"),
        "sentence_count": len(sentence_chunks),
        "omitted_count": len(sentences_doc.get("omitted_chunks") or []),
        "first_sentence": sentence_chunks[0].get("text") if sentence_chunks else "",
        "last_sentence": sentence_chunks[-1].get("text") if sentence_chunks else "",
        "fa_edges": edges,
        "wav": wav_media,
        "video": video_record.get("media"),
        "asr_model": (results.get("asr") or {}).get("settings"),
        "fa_model": (results.get("fa") or {}).get("settings"),
    }
    return {"ok": not problems, "problems": problems, "sample": sample}


def run_live(repo: Path) -> dict[str, Any]:
    data_root = repo / "data"
    try:
        video_id = media.resolve_video_id(LIVE_URL, Settings(data_root=data_root))
    except PipelineError as exc:
        return {"ok": False, "phase": "video_id", "stderr": str(exc)}
    work = data_root / video_id
    if work.exists():
        shutil.rmtree(work)
    cmd = [
        sys.executable,
        "-m",
        "extractube",
        "run",
        LIVE_URL,
        "--data-root",
        str(data_root),
    ]
    print("live: first run", flush=True)
    first = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if first.returncode != 0:
        return {"ok": False, "phase": "first", "stdout": first.stdout, "stderr": first.stderr}
    print("live: reuse run", flush=True)
    second = subprocess.run(cmd, capture_output=True, text=True, check=False)
    work_line = next((line for line in first.stdout.splitlines() if line.startswith("work_dir=")), "")
    work = Path(work_line.split("=", 1)[1]) if work_line else work
    assessed = assess_live(work) if work.is_dir() else {"ok": False, "problems": ["missing work dir"], "sample": {}}

    print("live: lock rejection", flush=True)
    lock_result: dict[str, Any] = {}
    if work.is_dir():
        lock = store.WorkLock(work)
        try:
            locked = subprocess.run(cmd, capture_output=True, text=True, check=False)
        finally:
            lock.release()
        lock_result = {"returncode": locked.returncode, "stderr": locked.stderr.strip()}

    def field(text: str, name: str) -> str:
        for line in text.splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
        return ""

    first_ids = _stage_ids(first.stdout)
    second_ids = _stage_ids(second.stdout)
    counts_ok = (
        field(first.stdout, "asr_requests") == "1"
        and field(first.stdout, "fa_requests") == "1"
        and field(second.stdout, "asr_requests") == "0"
        and field(second.stdout, "fa_requests") == "0"
        and second.returncode == 0
        and first_ids == second_ids
        and set(first_ids) == {"video", "wav", "asr", "fa", "sentences"}
    )
    return {
        "ok": counts_ok and assessed["ok"] and lock_result.get("returncode") == 1,
        "first_returncode": first.returncode,
        "second_returncode": second.returncode,
        "first_asr_requests": field(first.stdout, "asr_requests"),
        "first_fa_requests": field(first.stdout, "fa_requests"),
        "second_asr_requests": field(second.stdout, "asr_requests"),
        "second_fa_requests": field(second.stdout, "fa_requests"),
        "ids_stable": first_ids == second_ids,
        "media_problems": assessed["problems"],
        "lock": lock_result,
        "sample": assessed["sample"],
        "stderr": first.stderr[-2000:],
    }


def main() -> int:
    import tempfile
    import argparse
    parser = argparse.ArgumentParser(description="Stage1 checks; --live explicitly enables real YouTube/ASR/FA")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    test_alignment_validation()
    test_sentences()
    test_video_id()
    test_cli_usage()
    with tempfile.TemporaryDirectory(prefix="extractube-stage1-") as name:
        tmp = Path(name)
        test_commit_crash(tmp)
        test_lock(tmp)
        test_pipeline(tmp / "pipe")
        test_redirect_and_body_limit(tmp / "http")
        test_long_chunk(tmp / "long")
        test_empty_transcript(tmp / "empty")
    repo = ROOT.parents[1]
    if not args.live:
        failed = [item["name"] for item in CHECKS if not item["ok"]]
        print("local_checks=" + str(len(CHECKS)) + " live=excluded")
        return 1 if failed else 0
    live = run_live(repo)
    check(
        "live_youtube",
        bool(live.get("ok")),
        first_asr_requests=live.get("first_asr_requests"),
        first_fa_requests=live.get("first_fa_requests"),
        second_asr_requests=live.get("second_asr_requests"),
        second_fa_requests=live.get("second_fa_requests"),
        lock_returncode=(live.get("lock") or {}).get("returncode"),
    )
    report = {"checks": CHECKS, "live": live}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed = [item["name"] for item in CHECKS if not item["ok"]]
    print(f"report={REPORT_PATH}")
    if failed:
        print("failed=" + ",".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
