"""P3 변경 규칙 1개(3문장)의 소규모 확인. 전체 249 실행이 아니다.

대상마다 요청은 한 번이다. timeout이어도 같은 본문을 다시 보내지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_t2

ROOT = run_t2.ROOT
RUNS_ROOT = ROOT / "tests" / "T2" / "runs"
MARKER = "수량·금액·시간이 조건과 결과로 서로 이어지면 needed다."
ORDER = (
    ("iEMuJiVY9Oc", 0),
    ("hGVL9XRU9IQ", 0),
    ("hGVL9XRU9IQ", 4),
    ("CvuE-jyg-N8", 59),
    ("7EJ-xotXiSE", 18),
    ("bjDkfjSmXVo", 0),
    ("iVelddb1qQ4", 0),
    ("CvuE-jyg-N8", 39),
    ("Fw-8hZhnH3Q", 19),
    ("iEMuJiVY9Oc", 13),
)


def fail(message: str) -> None:
    print(message, flush=True)
    raise SystemExit(1)


def load_selected() -> tuple[str, list[dict], dict[tuple[str, int], dict]]:
    if MARKER not in run_t2.ACTIVE_PROMPT:
        fail("활성 프롬프트에 조건-결과 문장이 없습니다.")
    if len(ORDER) != 10:
        fail(f"확인 대상이 10개가 아닙니다: {len(ORDER)}")
    videos, problems = run_t2.load_campaign(ROOT, run_t2.INPUT_ROOT, run_t2.DATA_ROOT, True)
    if problems:
        fail("\n".join(problems))
    flat: list[dict] = []
    units: dict[tuple[str, int], dict] = {}
    for video in videos:
        planned, chunks, plan_problems = run_t2.plan_units(video)
        if plan_problems:
            fail("\n".join(plan_problems))
        for unit in planned:
            units[(video["video_id"], unit["unit_index"])] = unit
        flat.extend(chunks)
    selected, error = run_t2.select_confirm(flat, ORDER)
    if error:
        fail(error)
    if [(item["video_id"], item["unit_index"]) for item in selected] != list(ORDER):
        fail(f"확인 순서가 다릅니다: {selected}")
    return run_t2.new_run_id(), selected, units


def authoritative_body(chunk: dict, unit: dict) -> tuple[str, dict]:
    prompt = run_t2.render_prompt(
        unit["unit_index"],
        unit["sentence_start"],
        unit["sentence_end"],
        chunk["chunk_index"],
        len(unit["chunks"]),
        chunk["sentences"],
        chunk["context_before"],
        chunk["context_after"],
    )
    if run_t2.sha256_text(prompt) != chunk["rendered_sha256"]:
        fail(f"{chunk['video_id']} 단위 {chunk['unit_index']}: 렌더링 hash가 다릅니다.")
    if MARKER not in prompt:
        fail(f"{chunk['video_id']} 단위 {chunk['unit_index']}: 조건-결과 문장이 요청에 없습니다.")
    return prompt, run_t2.request_body(prompt)


def one_call(client: run_t2.InferClient, run_dir: Path, run_id: str, chunk: dict, body: dict) -> dict:
    transport = client.post(body)
    observed = run_t2.observe_response(transport.get("response_body"))
    content = None
    finish_reason = observed["finish_reason"]
    usage = observed["usage"]
    parsed = None
    error = transport.get("error")
    if error is None and isinstance(transport.get("response_body"), str):
        interpreted, interpret_error = run_t2.interpret_response(transport["http_status"], transport["response_body"])
        if interpret_error:
            error = interpret_error
            content = observed["content"]
        elif interpreted is not None:
            content = interpreted["content"]
            finish_reason = interpreted["finish_reason"]
            usage = interpreted["usage"]
            try:
                parsed = run_t2.parse_decision(
                    content,
                    set(range(chunk["sentence_start"], chunk["sentence_end"])),
                )
            except run_t2.SchemaError as exc:
                error = str(exc)
    record = run_t2.call_record(
        ROOT,
        run_id,
        chunk,
        body,
        transport,
        content,
        finish_reason,
        usage,
        parsed,
        error,
        reasoning=observed["reasoning"],
        thinking_observed=observed["thinking_observed"],
        system_fingerprint=observed["system_fingerprint"],
    )
    run_t2.dump_json(run_dir / "calls" / f"{record['call_id']}.json", record)
    return record


def done_pairs(run_dir: Path) -> set[tuple[str, int]]:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return set()
    payload = json.loads(summary_path.read_text())
    return {(item["video_id"], item["unit_index"]) for item in payload.get("results", [])}


def main() -> int:
    resume = sys.argv[1] if len(sys.argv) == 2 else None
    run_id, selected, units = load_selected()
    if resume:
        run_id = resume
    run_dir = RUNS_ROOT / run_id
    (run_dir / "calls").mkdir(parents=True, exist_ok=True)
    finished = done_pairs(run_dir) if resume else set()
    manifest = {
        "schema_version": "t2.rel_confirm.v1",
        "run_id": run_id,
        "purpose": "P3 변경 규칙 1개(3문장)의 10청크 확인. 대상당 요청 1회. 전체 후보 결과가 아니다.",
        "prompt_id": "P3_CTX2_REL_B4096",
        "prompt": run_t2.ACTIVE_PROMPT,
        "prompt_sha256": run_t2.sha256_text(run_t2.ACTIVE_PROMPT),
        "settings": {
            "model": run_t2.BODY_MODEL,
            "temperature": run_t2.TEMPERATURE,
            "enable_thinking": run_t2.ENABLE_THINKING,
            "max_tokens": run_t2.MAX_TOKENS,
            "timeout_seconds": run_t2.TIMEOUT_SECONDS,
            "context_sentences": run_t2.CONTEXT_SENTENCES,
            "infer_url": run_t2.INFER_URL,
        },
        "order": [{"video_id": video_id, "unit_index": unit_index} for video_id, unit_index in ORDER],
        "reuse": "none",
    }
    if not resume:
        run_t2.dump_json(run_dir / "manifest.json", manifest)
    client = run_t2.InferClient()
    summary = []
    if resume and (run_dir / "summary.json").exists():
        summary = json.loads((run_dir / "summary.json").read_text()).get("results", [])
    for index, chunk in enumerate(selected, start=1):
        if (chunk["video_id"], chunk["unit_index"]) in finished:
            print(f"{index}/10 {chunk['video_id']} u{chunk['unit_index']} skip", flush=True)
            continue
        unit = units[(chunk["video_id"], chunk["unit_index"])]
        if len(unit["chunks"]) != 1:
            fail(f"{chunk['video_id']} 단위 {chunk['unit_index']}: 청크가 1개가 아닙니다.")
        _prompt, body = authoritative_body(chunk, unit)
        print(f"{index}/10 {chunk['video_id']} u{chunk['unit_index']}", flush=True)
        record = one_call(client, run_dir, run_id, chunk, body)
        decision = None if not isinstance(record.get("parsed"), dict) else record["parsed"].get("decision")
        summary.append(
            {
                "video_id": chunk["video_id"],
                "unit_index": chunk["unit_index"],
                "decision": decision,
                "reason": None if not isinstance(record.get("parsed"), dict) else record["parsed"].get("reason"),
                "error": record.get("error"),
                "elapsed_seconds": record.get("elapsed_seconds"),
                "usage": record.get("usage"),
                "call_ref": record.get("call_ref"),
                "finish_reason": record.get("finish_reason"),
            }
        )
        print(f"  {decision or record.get('error')}", flush=True)
        run_t2.dump_json(run_dir / "summary.json", {"results": summary, "stopped": False})
        if record.get("error") and decision is None and "예산 제약" not in str(record.get("error")):
            summary_payload = {"results": summary, "stopped": True, "stop_reason": record.get("error")}
            run_t2.dump_json(run_dir / "summary.json", summary_payload)
            print(f"run_dir: {run_dir}", flush=True)
            return 1
    run_t2.dump_json(run_dir / "summary.json", {"results": summary, "stopped": False})
    print(f"run_dir: {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
