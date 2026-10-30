"""P3_CTX2 저장 요청으로 max_tokens 4096의 thinking OFF/ON 4회만 비교한다."""

from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


import copy
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_t2

ROOT = EXPERIMENT_ROOT
BASELINE_RUN = resolve_experiment_path(ROOT / "tests" / "T2" / "runs" / "20261018T163714Z_27eb28")
RUNS_ROOT = resolve_experiment_path(ROOT / "tests" / "T2" / "runs")
MAX_TOKENS = 4096
BUDGET_ERROR = "예산 제약으로 비교 불완전"
ORDER = (
    ("bjDkfjSmXVo", 7, 0, "A", False),
    ("bjDkfjSmXVo", 7, 0, "B", True),
    ("7EJ-xotXiSE", 18, 0, "A", False),
    ("7EJ-xotXiSE", 18, 0, "B", True),
)


def fail(problems: list[str]) -> None:
    for problem in problems:
        print(problem, flush=True)
    raise SystemExit(1)


def load_baseline_calls() -> dict[tuple[str, int, int], dict]:
    found: dict[tuple[str, int, int], dict] = {}
    problems: list[str] = []
    for path in sorted((BASELINE_RUN / "calls").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        ident = (record["video_id"], record["unit_index"], record["chunk_index"])
        if ident in found:
            problems.append(f"{ident}: 기준 호출이 둘 이상입니다.")
        record["_path"] = path
        found[ident] = record
    if problems:
        fail(problems)
    return found


def build_body(stored: dict, thinking: bool) -> dict:
    body = copy.deepcopy(stored["request_body"])
    body["max_tokens"] = MAX_TOKENS
    body["chat_template_kwargs"] = {"enable_thinking": thinking}
    return body


def thinking_only(left: dict, right: dict) -> list[str]:
    problems: list[str] = []
    if list(left) != list(right):
        problems.append(f"요청 키가 다릅니다: {list(left)} / {list(right)}")
    for key, value in left.items():
        if key == "chat_template_kwargs":
            continue
        if right.get(key) != value:
            problems.append(f"요청 {key}가 A/B 사이에서 다릅니다.")
    if set(right) - set(left):
        problems.append(f"B에만 있는 키: {sorted(set(right) - set(left))}")
    if left.get("chat_template_kwargs") != {"enable_thinking": False}:
        problems.append(f"A thinking이 false가 아닙니다: {left.get('chat_template_kwargs')}")
    if right.get("chat_template_kwargs") != {"enable_thinking": True}:
        problems.append(f"B thinking이 true가 아닙니다: {right.get('chat_template_kwargs')}")
    return problems


def stored_shape(stored_body: dict, built: dict, thinking: bool) -> list[str]:
    problems: list[str] = []
    if list(stored_body) != ["model", "messages", "temperature", "max_tokens", "chat_template_kwargs"]:
        problems.append(f"기준 요청 키가 다릅니다: {list(stored_body)}")
    if stored_body.get("model") != "qwen3.8-27b" or stored_body.get("temperature") != 0:
        problems.append("기준 model 또는 temperature가 유지 조건과 다릅니다.")
    if stored_body.get("max_tokens") != 1024:
        problems.append("기준 max_tokens가 1024가 아닙니다.")
    if stored_body.get("chat_template_kwargs") != {"enable_thinking": False}:
        problems.append("기준 thinking이 false가 아닙니다.")
    messages = stored_body.get("messages")
    if messages != built.get("messages"):
        problems.append("메시지가 저장된 P3_CTX2 요청과 다릅니다.")
    if not isinstance(messages, list) or len(messages) != 1 or messages[0].get("role") != "user":
        problems.append("메시지가 user 하나가 아닙니다.")
    elif not isinstance(messages[0].get("content"), str) or messages[0]["content"] == "":
        problems.append("저장된 user content가 비어 있습니다.")
    if built.get("model") != stored_body.get("model") or built.get("temperature") != stored_body.get("temperature"):
        problems.append("model 또는 temperature를 바꿨습니다.")
    if built.get("max_tokens") != MAX_TOKENS:
        problems.append("max_tokens가 4096이 아닙니다.")
    if built.get("chat_template_kwargs") != {"enable_thinking": thinking}:
        problems.append("thinking 플래그가 조건과 다릅니다.")
    return problems


def prepare() -> list[dict]:
    if len(ORDER) != 4:
        fail([f"실행 목록이 4개가 아닙니다: {len(ORDER)}"])
    expected = [
        ("bjDkfjSmXVo", 7, 0, "A"),
        ("bjDkfjSmXVo", 7, 0, "B"),
        ("7EJ-xotXiSE", 18, 0, "A"),
        ("7EJ-xotXiSE", 18, 0, "B"),
    ]
    if [(item[0], item[1], item[2], item[3]) for item in ORDER] != expected:
        fail([f"실행 순서가 지정과 다릅니다: {ORDER}"])
    calls = load_baseline_calls()
    plans: list[dict] = []
    problems: list[str] = []
    by_target: dict[tuple[str, int, int], dict[str, dict]] = {}
    for video_id, unit_index, chunk_index, condition, thinking in ORDER:
        ident = (video_id, unit_index, chunk_index)
        stored = calls.get(ident)
        if stored is None:
            problems.append(f"{ident}: 기준 호출이 없습니다.")
            continue
        body = build_body(stored, thinking)
        problems.extend(f"{video_id} 단위 {unit_index} {condition}: {item}" for item in stored_shape(stored["request_body"], body, thinking))
        prompt = body["messages"][0]["content"]
        try:
            allowed = run_t2.request_sentence_indices(prompt)
        except (run_t2.SchemaError, KeyError, json.JSONDecodeError) as exc:
            problems.append(f"{video_id} 단위 {unit_index}: 대상 문장 index를 읽지 못했습니다: {exc}")
            allowed = []
        plans.append(
            {
                "video_id": video_id,
                "unit_index": unit_index,
                "chunk_index": chunk_index,
                "condition": condition,
                "thinking": thinking,
                "body": body,
                "allowed": allowed,
                "baseline_call": stored["_path"].relative_to(ROOT).as_posix(),
                "baseline_decision": stored.get("parsed"),
            }
        )
        by_target.setdefault(ident, {})[condition] = body
    for ident, pair in by_target.items():
        if set(pair) != {"A", "B"}:
            problems.append(f"{ident}: A/B가 짝이 아닙니다.")
            continue
        problems.extend(f"{ident[0]} 단위 {ident[1]}: {item}" for item in thinking_only(pair["A"], pair["B"]))
    if problems:
        fail(problems)
    print("confirm_http:", flush=True)
    for index, plan in enumerate(plans, 1):
        print(
            f"{index} {plan['video_id']} unit {plan['unit_index']} chunk {plan['chunk_index']} "
            f"{plan['condition']} thinking {str(plan['thinking']).lower()} max_tokens {MAX_TOKENS} "
            f"sentences {plan['allowed'][0]}-{plan['allowed'][-1]}",
            flush=True,
        )
    print("http_targets: 4", flush=True)
    print("reuse: none", flush=True)
    return plans


def observe(raw_text: object) -> dict:
    empty = {"content": None, "reasoning": None, "thinking_observed": None, "finish_reason": None, "usage": None, "system_fingerprint": None}
    if not isinstance(raw_text, str):
        return empty
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return empty
    if not isinstance(payload, dict):
        return empty
    content, _error = run_t2.extract_content(payload)
    return {
        "content": content if isinstance(content, str) else None,
        "reasoning": run_t2.extract_reasoning(payload),
        "thinking_observed": run_t2.thinking_observed_from(payload),
        "finish_reason": run_t2.extract_finish_reason(payload),
        "usage": payload.get("usage") if "usage" in payload else None,
        "system_fingerprint": payload.get("system_fingerprint") if isinstance(payload.get("system_fingerprint"), str) else None,
    }


def completion_tokens(usage: object) -> int | None:
    if isinstance(usage, dict) and run_t2.is_int(usage.get("completion_tokens")):
        return usage["completion_tokens"]
    return None


def decide(status: object, raw_text: object, allowed: set[int]) -> tuple[dict | None, str | None, dict]:
    observed = observe(raw_text)
    if not isinstance(status, int):
        return None, "HTTP 응답이 없습니다.", observed
    if status != 200:
        return None, f"HTTP {status}", observed
    if not isinstance(raw_text, str):
        return None, "응답 본문이 없습니다.", observed
    tokens = completion_tokens(observed["usage"])
    finish = observed["finish_reason"]
    if finish == "length" or (tokens is not None and tokens >= MAX_TOKENS and finish != "stop"):
        return None, BUDGET_ERROR, observed
    content = observed["content"]
    if content is None or content == "":
        return None, "응답 텍스트가 없습니다.", observed
    if finish != "stop":
        return None, f"finish_reason={finish}", observed
    try:
        parsed = run_t2.parse_decision(content, allowed)
    except run_t2.SchemaError as exc:
        return None, str(exc), observed
    return parsed, None, observed


def main() -> int:
    plans = prepare()
    run_id = run_t2.new_run_id()
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "calls").mkdir()
    manifest = {
        "experiment": "P3_CTX2_BUDGET4096",
        "run_id": run_id,
        "created_at": run_t2.utc_now(),
        "baseline_run_id": "20261018T163714Z_27eb28",
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "timeout_seconds": run_t2.TIMEOUT_SECONDS,
        "body_model": "qwen3.8-27b",
        "checkpoint": "unknown",
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
        parsed, error, observed = decide(transport.get("http_status"), transport.get("response_body"), set(plan["allowed"]))
        if transport.get("error"):
            error = transport["error"]
            parsed = None
        call_id = str(uuid.uuid4())
        response_body = transport.get("response_body")
        record = {
            "experiment": "P3_CTX2_BUDGET4096",
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
    print("calls: 4", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
