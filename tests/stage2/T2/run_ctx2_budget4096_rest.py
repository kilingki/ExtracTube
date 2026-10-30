"""남은 6개 대상만 P3_CTX2 저장 요청으로 max_tokens 4096 A/B를 최대 12회 비교한다."""

from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


import json
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ctx2_budget4096 as base
import run_t2

UNITS = (
    ("iVelddb1qQ4", 5),
    ("CvuE-jyg-N8", 59),
    ("Fw-8hZhnH3Q", 17),
    ("bjDkfjSmXVo", 0),
    ("Fw-8hZhnH3Q", 19),
    ("iVelddb1qQ4", 24),
)
ORDER = tuple(
    (video_id, unit_index, 0, condition, thinking)
    for video_id, unit_index in UNITS
    for condition, thinking in (("A", False), ("B", True))
)
PRIOR_RUN = "tests/T2/runs/20261020T175915Z_c239dd"
KNOWN_FINGERPRINT = "vllm-0.31.0-85781cf0"


def expected_order() -> list[tuple[str, int, int, str]]:
    rows = []
    for video_id, unit_index in UNITS:
        rows.append((video_id, unit_index, 0, "A"))
        rows.append((video_id, unit_index, 0, "B"))
    return rows


def input_chunks() -> tuple[dict[tuple[str, int], dict], dict[str, dict], list[str]]:
    videos, problems = run_t2.load_campaign(run_t2.ROOT, run_t2.INPUT_ROOT, run_t2.DATA_ROOT, True)
    found: dict[tuple[str, int], dict] = {}
    videos_by_id = {video["video_id"]: video for video in videos}
    for video in videos:
        _units, chunks, plan_problems = run_t2.plan_units(video)
        problems.extend(plan_problems)
        for chunk in chunks:
            found.setdefault((chunk["video_id"], chunk["unit_index"]), chunk)
            if (chunk["video_id"], chunk["unit_index"]) in found and found[(chunk["video_id"], chunk["unit_index"])] is not chunk:
                if chunk["chunk_index"] != found[(chunk["video_id"], chunk["unit_index"])]["chunk_index"]:
                    problems.append(
                        f"{chunk['video_id']} 단위 {chunk['unit_index']}: 청크가 1개가 아닙니다."
                    )
    counts: dict[tuple[str, int], int] = {}
    for video in videos:
        _units, chunks, _problems = run_t2.plan_units(video)
        for chunk in chunks:
            key = (chunk["video_id"], chunk["unit_index"])
            counts[key] = counts.get(key, 0) + 1
    for key, count in counts.items():
        if key in {(video_id, unit_index) for video_id, unit_index in UNITS} and count != 1:
            problems.append(f"{key[0]} 단위 {key[1]}: 실제 입력 청크가 {count}개입니다.")
    return found, videos_by_id, problems


def prepare() -> list[dict]:
    if len(ORDER) != 12:
        base.fail([f"실행 목록이 12개가 아닙니다: {len(ORDER)}"])
    if [(item[0], item[1], item[2], item[3]) for item in ORDER] != expected_order():
        base.fail([f"실행 순서가 지정과 다릅니다: {ORDER}"])
    calls = base.load_baseline_calls()
    chunks, videos_by_id, problems = input_chunks()
    plans: list[dict] = []
    by_target: dict[tuple[str, int, int], dict[str, dict]] = {}
    for video_id, unit_index, chunk_index, condition, thinking in ORDER:
        ident = (video_id, unit_index, chunk_index)
        stored = calls.get(ident)
        chunk = chunks.get((video_id, unit_index))
        if stored is None:
            problems.append(f"{ident}: 기준 호출이 없습니다.")
            continue
        if stored.get("chunk_index") != 0 or chunk_index != 0:
            problems.append(f"{video_id} 단위 {unit_index}: 기준 호출 chunk가 0이 아닙니다.")
        if chunk is None or chunk.get("chunk_index") != 0:
            problems.append(f"{video_id} 단위 {unit_index}: 실제 입력에서 chunk 0이 아닙니다.")
            continue
        body = base.build_body(stored, thinking)
        problems.extend(
            f"{video_id} 단위 {unit_index} {condition}: {item}"
            for item in base.stored_shape(stored["request_body"], body, thinking)
        )
        prompt = body["messages"][0]["content"]
        try:
            allowed = run_t2.request_sentence_indices(prompt)
        except (run_t2.SchemaError, KeyError, json.JSONDecodeError) as exc:
            problems.append(f"{video_id} 단위 {unit_index}: 대상 문장 index를 읽지 못했습니다: {exc}")
            allowed = []
        if allowed != list(range(chunk["sentence_start"], chunk["sentence_end"])):
            problems.append(f"{video_id} 단위 {unit_index}: 요청 문장 구간이 실제 입력 청크와 다릅니다.")
        video = videos_by_id.get(video_id)
        if video is not None and allowed:
            texts = {row["index"]: row["text"] for row in video["sentences"]}
            rows = run_t2.json_array_after(prompt, run_t2.SENTENCE_MARKER)
            if [row["text"] for row in rows] != [texts[index] for index in allowed]:
                problems.append(f"{video_id} 단위 {unit_index}: 요청 원문이 실제 입력과 다릅니다.")
        reason = (stored.get("parsed") or {}).get("reason")
        if isinstance(reason, str) and reason and reason in prompt:
            problems.append(f"{video_id} 단위 {unit_index}: 기준 판정 근거가 요청에 있습니다.")
        for label in run_t2.REVIEW_LABELS:
            if label in prompt:
                problems.append(f"{video_id} 단위 {unit_index}: 프롬프트에 실험 구분이 있습니다: {label}")
        plans.append(
            {
                "video_id": video_id,
                "unit_index": unit_index,
                "chunk_index": 0,
                "condition": condition,
                "thinking": thinking,
                "body": body,
                "allowed": allowed,
                "baseline_call": stored["_path"].relative_to(base.ROOT).as_posix(),
                "baseline_decision": stored.get("parsed"),
            }
        )
        by_target.setdefault(ident, {})[condition] = body
    for ident, pair in by_target.items():
        if set(pair) != {"A", "B"}:
            problems.append(f"{ident}: A/B가 짝이 아닙니다.")
            continue
        problems.extend(f"{ident[0]} 단위 {ident[1]}: {item}" for item in base.thinking_only(pair["A"], pair["B"]))
    if problems:
        base.fail(problems)
    print("server_preflight:", flush=True)
    print("checkpoint: unknown", flush=True)
    print(f"last_fingerprint: {KNOWN_FINGERPRINT}", flush=True)
    print("documented_weight_swap: none", flush=True)
    print("controller:", json.dumps(controller_status(), ensure_ascii=False), flush=True)
    print("confirm_http:", flush=True)
    for index, plan in enumerate(plans, 1):
        print(
            f"{index} {plan['video_id']} unit {plan['unit_index']} chunk {plan['chunk_index']} "
            f"{plan['condition']} thinking {str(plan['thinking']).lower()} max_tokens {base.MAX_TOKENS} "
            f"sentences {plan['allowed'][0]}-{plan['allowed'][-1]}",
            flush=True,
        )
    print("http_targets: 12", flush=True)
    print("reuse: none", flush=True)
    return plans


def controller_status() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8010/control/status", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        base.fail([f"vLLM 상태를 읽지 못했습니다: {exc}"])
    if not isinstance(payload, dict) or payload.get("state") != "ready" or payload.get("residency") != "resident":
        base.fail([f"vLLM이 추론 가능 상태가 아닙니다: {payload}"])
    return payload


def main() -> int:
    plans = prepare()
    run_id = run_t2.new_run_id()
    run_dir = base.RUNS_ROOT / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "calls").mkdir()
    manifest = {
        "experiment": "P3_CTX2_BUDGET4096_REST6",
        "run_id": run_id,
        "created_at": run_t2.utc_now(),
        "baseline_run_id": "20261018T163714Z_27eb28",
        "prior_ab_run": PRIOR_RUN,
        "max_tokens": base.MAX_TOKENS,
        "temperature": 0,
        "timeout_seconds": run_t2.TIMEOUT_SECONDS,
        "body_model": "qwen3.8-27b",
        "checkpoint": "unknown",
        "last_known_fingerprint": KNOWN_FINGERPRINT,
        "reuse": "none",
        "order": [
            {
                "video_id": plan["video_id"],
                "unit_index": plan["unit_index"],
                "chunk_index": plan["chunk_index"],
                "condition": plan["condition"],
                "enable_thinking": plan["thinking"],
                "baseline_call": plan["baseline_call"],
            }
            for plan in plans
        ],
    }
    run_t2.dump_json(run_dir / "manifest.json", manifest)
    client = run_t2.InferClient()
    results = []
    for plan in plans:
        transport = client.post(plan["body"])
        parsed, error, observed = base.decide(
            transport.get("http_status"),
            transport.get("response_body"),
            set(plan["allowed"]),
        )
        if transport.get("error"):
            error = transport["error"]
            parsed = None
        call_id = str(uuid.uuid4())
        response_body = transport.get("response_body")
        record = {
            "experiment": "P3_CTX2_BUDGET4096_REST6",
            "call_id": call_id,
            "video_id": plan["video_id"],
            "unit_index": plan["unit_index"],
            "chunk_index": plan["chunk_index"],
            "condition": plan["condition"],
            "reuse_key": None,
            "url": run_t2.INFER_URL,
            "request_body": plan["body"],
            "http_status": transport.get("http_status"),
            "response_body": response_body,
            "response_sha256": run_t2.sha256_text(response_body) if isinstance(response_body, str) else None,
            "content": observed["content"],
            "reasoning": observed["reasoning"],
            "thinking_requested": plan["thinking"],
            "thinking_observed": observed["thinking_observed"],
            "system_fingerprint": observed["system_fingerprint"],
            "finish_reason": observed["finish_reason"],
            "usage": observed["usage"],
            "parsed": parsed,
            "error": error,
            "elapsed_seconds": transport.get("elapsed_seconds"),
            "baseline_call": plan["baseline_call"],
            "baseline_decision": plan["baseline_decision"],
            "started_at": run_t2.utc_now(),
        }
        run_t2.dump_json(run_dir / "calls" / f"{call_id}.json", record)
        results.append(
            {
                "video_id": plan["video_id"],
                "unit_index": plan["unit_index"],
                "condition": plan["condition"],
                "call_id": call_id,
                "http_status": record["http_status"],
                "finish_reason": record["finish_reason"],
                "error": error,
                "elapsed_seconds": record["elapsed_seconds"],
                "parsed": parsed,
                "thinking_requested": plan["thinking"],
                "thinking_observed": observed["thinking_observed"],
                "system_fingerprint": observed["system_fingerprint"],
                "usage": observed["usage"],
            }
        )
        label = f"{plan['video_id']} unit {plan['unit_index']} {plan['condition']}"
        if error:
            print(f"{label} failure: {error}", flush=True)
            run_t2.dump_json(run_dir / "results.json", {"run_id": run_id, "stopped": True, "results": results})
            print(f"run_dir: {run_dir}", flush=True)
            return 1
        print(f"{label} success {parsed['decision']}", flush=True)
    run_t2.dump_json(run_dir / "results.json", {"run_id": run_id, "stopped": False, "results": results})
    print(f"run_dir: {run_dir}", flush=True)
    print("calls: 12", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
