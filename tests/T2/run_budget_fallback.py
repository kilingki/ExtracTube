"""실패 5개의 저장 요청에서 enable_thinking만 false로 바꾼 fallback. 최대 5회."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_t2

ROOT = run_t2.ROOT
SOURCE_RUN = ROOT / "tests" / "T2" / "runs" / "20261020T184601Z_b6d662"
FALLBACK_DIR = SOURCE_RUN / "fallback"
P2_DIR = run_t2.RUNS_ROOT / run_t2.P2_RUN_ID
TARGETS = (
    ("H2WN130OzLg", 1, 0),
    ("H2WN130OzLg", 3, 0),
    ("H2WN130OzLg", 7, 0),
    ("H2WN130OzLg", 25, 0),
    ("iVelddb1qQ4", 21, 0),
)
MAX_CALLS = 5


def fail(problems: list[str]) -> None:
    for problem in problems:
        print(problem, flush=True)
    raise SystemExit(1)


def preserved_paths(records: list[dict]) -> list[Path]:
    paths = [SOURCE_RUN / "manifest.json"]
    paths.extend(record["_path"] for record in records)
    return paths


def snapshot(paths: list[Path]) -> dict[str, str]:
    return {run_t2.display_path(ROOT, path): run_t2.sha256_file(path) for path in paths}


def load_documents() -> dict[str, dict]:
    documents = {}
    for path in sorted(SOURCE_RUN.glob("*/visual_candidates.json")):
        document = run_t2.read_json(path)
        documents[document["video_id"]] = document
    return documents


def load_targets(documents: dict[str, dict]) -> list[dict]:
    problems: list[str] = []
    ok = 0
    failed = 0
    pending = 0
    for document in documents.values():
        for _unit, chunk in run_t2.iter_chunks(document):
            if chunk["status"] == "ok":
                ok += 1
            elif chunk["status"] == "failed":
                failed += 1
            elif chunk["status"] == "pending":
                pending += 1
            else:
                problems.append(f"알 수 없는 청크 상태: {chunk['status']}")
    if ok != 244 or failed != 5 or pending != 0:
        problems.append(f"집계가 성공 244, 실패 5, 미실행 0이 아닙니다: ok {ok} failed {failed} pending {pending}")
    manifest = run_t2.read_json(SOURCE_RUN / "manifest.json")
    planned = {
        (chunk["video_id"], chunk["unit_index"], chunk["chunk_index"]): chunk
        for chunk in manifest["plan"]
    }
    records = []
    for ident in TARGETS:
        video_id, unit_index, chunk_index = ident
        document = documents.get(video_id)
        if document is None:
            problems.append(f"{video_id}: 문서가 없습니다.")
            continue
        unit = next((item for item in document["units"] if item["unit_index"] == unit_index), None)
        if unit is None or len(unit["chunks"]) != 1:
            problems.append(f"{video_id} 단위 {unit_index}: 청크가 하나가 아닙니다.")
            continue
        chunk = unit["chunks"][0]
        if chunk["chunk_index"] != chunk_index or chunk["status"] != "failed":
            problems.append(f"{video_id} 단위 {unit_index}: 실패 청크가 아닙니다.")
            continue
        plan = planned.get(ident)
        if plan is None or (plan["sentence_start"], plan["sentence_end"]) != (chunk["sentence_start"], chunk["sentence_end"]):
            problems.append(f"{video_id} 단위 {unit_index}: 계획 문장 구간과 다릅니다.")
            continue
        ref = chunk.get("call_ref")
        if not isinstance(ref, str):
            problems.append(f"{video_id} 단위 {unit_index}: 원본 call_ref가 없습니다.")
            continue
        path = ROOT / ref
        stored = run_t2.read_json(path)
        stored["_path"] = path
        if (stored.get("video_id"), stored.get("unit_index"), stored.get("chunk_index")) != ident:
            problems.append(f"{video_id} 단위 {unit_index}: 원본 호출 대상이 다릅니다.")
            continue
        if stored.get("error") != "예산 제약으로 비교 불완전" or stored.get("finish_reason") != "length":
            problems.append(f"{video_id} 단위 {unit_index}: 원본이 reasoning 예산 소진 실패가 아닙니다.")
            continue
        body = stored.get("request_body")
        kwargs = body.get("chat_template_kwargs") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("max_tokens") != 4096
            or body.get("model") != run_t2.BODY_MODEL
            or body.get("temperature") != 0
            or kwargs != {"enable_thinking": True}
        ):
            problems.append(f"{video_id} 단위 {unit_index}: 저장 요청이 thinking true, max_tokens 4096이 아닙니다.")
            continue
        records.append(
            {
                "ident": ident,
                "document": document,
                "unit": unit,
                "chunk": chunk,
                "stored": stored,
                "_path": path,
            }
        )
    if len(records) != MAX_CALLS:
        problems.append(f"fallback 대상이 {MAX_CALLS}개가 아닙니다: {len(records)}")
    if problems:
        fail(problems)
    return records


def fallback_body(stored_body: dict) -> dict:
    body = copy.deepcopy(stored_body)
    body["chat_template_kwargs"]["enable_thinking"] = False
    return body


def body_is_thinking_off_only(stored_body: dict, built: dict) -> list[str]:
    problems = []
    if list(stored_body) != list(built):
        problems.append(f"요청 키가 달라졌습니다: {list(stored_body)} / {list(built)}")
    for key, value in stored_body.items():
        if key == "chat_template_kwargs":
            continue
        if built.get(key) != value:
            problems.append(f"{key}가 저장 요청과 다릅니다.")
    if built.get("chat_template_kwargs") != {"enable_thinking": False}:
        problems.append(f"enable_thinking만 false가 아닙니다: {built.get('chat_template_kwargs')}")
    if built.get("max_tokens") != 4096:
        problems.append("max_tokens가 4096이 아닙니다.")
    return problems


def sentence_rows(document: dict) -> dict[int, dict]:
    return {sentence["index"]: sentence for unit in document["units"] for sentence in unit["sentences"]}


def span_lines(rows: dict[int, dict], start: object, end: object) -> list[str]:
    if not run_t2.is_int(start) or not run_t2.is_int(end) or start == end:
        return ["빈 배열 []."]
    lines = []
    for index in range(start, end):
        row = rows.get(index)
        if row is None:
            lines.append(f"- [{index}] 문장을 찾지 못했습니다.")
        else:
            lines.append(
                f"- [{row['index']}] {run_t2.time_literal(row['start_time'])}-{run_t2.time_literal(row['end_time'])} "
                f"{run_t2.md_escape(row['text'])}"
            )
    return lines


def validate_content(content: str | None, chunk: dict) -> tuple[dict | None, str | None]:
    allowed = set(range(chunk["sentence_start"], chunk["sentence_end"]))
    if not isinstance(content, str) or content == "":
        return None, "content가 없습니다."
    try:
        parsed = run_t2.parse_decision(content, allowed)
    except run_t2.SchemaError as exc:
        return None, str(exc)
    return parsed, None


def execute(records: list[dict]) -> list[dict]:
    ready = run_t2.controller_ready_problem()
    if ready:
        fail([ready])
    client = run_t2.InferClient()
    outcomes = []
    posts = 0
    for record in records:
        if posts >= MAX_CALLS:
            fail(["최대 5회를 넘기려 했습니다."])
        video_id, unit_index, _chunk_index = record["ident"]
        built = fallback_body(record["stored"]["request_body"])
        mismatch = body_is_thinking_off_only(record["stored"]["request_body"], built)
        if mismatch:
            fail([f"{video_id} 단위 {unit_index}: {item}" for item in mismatch])
        transport = client.post(built)
        posts += 1
        observed = run_t2.observe_response(transport.get("response_body"))
        error = transport.get("error")
        content = observed["content"]
        finish_reason = observed["finish_reason"]
        usage = observed["usage"]
        parsed = None
        if error is None and isinstance(transport.get("response_body"), str):
            interpreted, interpret_error = run_t2.interpret_response(transport["http_status"], transport["response_body"])
            if interpret_error:
                error = interpret_error
            elif interpreted is not None:
                content = interpreted["content"]
                finish_reason = interpreted["finish_reason"]
                usage = interpreted["usage"]
                parsed, parse_error = validate_content(content, record["chunk"])
                error = parse_error
        adopted = parsed if error is None and parsed is not None else None
        call = {
            "schema_version": run_t2.SCHEMA_VERSION,
            "condition": "fallback_thinking_off",
            "source_run_id": "20261020T184601Z_b6d662",
            "call_id": __import__("uuid").uuid4().hex,
            "video_id": video_id,
            "unit_index": unit_index,
            "chunk_index": 0,
            "original_call_ref": run_t2.display_path(ROOT, record["_path"]),
            "original_error": record["stored"].get("error"),
            "url": run_t2.INFER_URL,
            "request_body": built,
            "http_status": transport.get("http_status"),
            "response_body": transport.get("response_body"),
            "response_sha256": run_t2.sha256_text(transport["response_body"]) if isinstance(transport.get("response_body"), str) else None,
            "content": content,
            "reasoning": observed["reasoning"],
            "thinking_requested": False,
            "thinking_observed": observed["thinking_observed"],
            "system_fingerprint": observed["system_fingerprint"],
            "finish_reason": finish_reason,
            "usage": usage,
            "parsed": adopted,
            "error": error,
            "elapsed_seconds": transport.get("elapsed_seconds"),
            "started_at": run_t2.utc_now(),
        }
        call_path = FALLBACK_DIR / "calls" / f"{call['call_id']}.json"
        run_t2.dump_json(call_path, call)
        call_ref = run_t2.display_path(ROOT, call_path)
        link = {
            "video_id": video_id,
            "unit_index": unit_index,
            "chunk_index": 0,
            "sentence_start": record["chunk"]["sentence_start"],
            "sentence_end": record["chunk"]["sentence_end"],
            "audio_start": record["chunk"]["audio_start"],
            "audio_end": record["chunk"]["audio_end"],
            "video_start": record["chunk"]["video_start"],
            "video_end": record["chunk"]["video_end"],
            "original_call_ref": call["original_call_ref"],
            "original_error": record["stored"].get("error"),
            "original_finish_reason": record["stored"].get("finish_reason"),
            "fallback_call_ref": call_ref,
            "thinking_requested": False,
            "thinking_observed": observed["thinking_observed"],
            "validation": "ok" if adopted is not None else error,
            "adopted": adopted is not None,
            "adopted_decision": adopted,
        }
        outcomes.append({"record": record, "call": call, "link": link})
        label = "success" if adopted is not None else f"failure: {error}"
        print(f"{video_id} unit {unit_index} chunk 0 {label}", flush=True)
    if posts != MAX_CALLS:
        fail([f"호출 수가 {MAX_CALLS}가 아닙니다: {posts}"])
    return outcomes


def write_results(documents: dict[str, dict], outcomes: list[dict], before: dict[str, str]) -> None:
    after = snapshot(preserved_paths([item["record"] for item in outcomes]))
    if after != before:
        fail(["원본 manifest 또는 실패 호출 hash가 달라졌습니다."])
    links = {
        "source_run_id": "20261020T184601Z_b6d662",
        "condition": "fallback_thinking_off",
        "change": "저장된 요청에서 enable_thinking만 false. max_tokens 4096과 메시지·모델·temperature는 유지.",
        "max_calls": MAX_CALLS,
        "calls_made": len(outcomes),
        "not_thinking_on": True,
        "preserved_sha256": before,
        "items": [item["link"] for item in outcomes],
    }
    run_t2.dump_json(FALLBACK_DIR / "links.json", links)
    result_lines = [
        "# fallback 5개",
        "",
        "조건은 fallback_thinking_off이다. thinking ON 결과가 아니다.",
        "실패는 not_needed나 uncertain으로 바꾸지 않았다.",
        "품질 통과나 T2 채택이 아니다.",
        "",
    ]
    for item in outcomes:
        record = item["record"]
        link = item["link"]
        chunk = record["chunk"]
        rows = sentence_rows(record["document"])
        decision = link["adopted_decision"] or {}
        result_lines.extend(
            [
                f"## {link['video_id']} 단위 {link['unit_index']}",
                "",
                f"- 조건: fallback_thinking_off. thinking_requested false. thinking_observed {link['thinking_observed']}.",
                f"- 원본 호출: `{link['original_call_ref']}`",
                f"- 원본 오류: {link['original_error']}. finish_reason {link['original_finish_reason']}.",
                f"- fallback 호출: `{link['fallback_call_ref']}`",
                f"- 검증: {link['validation']}",
                f"- 채택: {link['adopted']}",
                f"- 판정: {decision.get('decision')}",
                f"- 근거: {decision.get('reason')}",
                f"- evidence: {decision.get('evidence_sentence_indices')}",
                f"- 문장 구간: [{chunk['sentence_start']},{chunk['sentence_end']})",
                f"- 오디오: {run_t2.time_literal(chunk['audio_start'])}-{run_t2.time_literal(chunk['audio_end'])}",
                f"- 영상: {run_t2.time_literal(chunk['video_start'])}-{run_t2.time_literal(chunk['video_end'])}",
                "",
                "### 앞 문맥",
                "",
                *span_lines(rows, chunk.get("context_before_start"), chunk.get("context_before_end")),
                "",
                "### 원문",
                "",
                *span_lines(rows, chunk["sentence_start"], chunk["sentence_end"]),
                "",
                "### 뒤 문맥",
                "",
                *span_lines(rows, chunk.get("context_after_start"), chunk.get("context_after_end")),
                "",
            ]
        )
    (FALLBACK_DIR / "results.md").write_text("\n".join(result_lines) + "\n", encoding="utf-8")
    write_index(documents, outcomes)
    write_comparison(documents, outcomes)


def adopted_view(documents: dict[str, dict], outcomes: list[dict]) -> dict[tuple[str, int], dict]:
    view = {}
    linked = {item["link"]["video_id"] + f":{item['link']['unit_index']}": item["link"] for item in outcomes}
    for video_id, document in documents.items():
        for unit in document["units"]:
            key = (video_id, unit["unit_index"])
            link = linked.get(f"{video_id}:{unit['unit_index']}")
            chunk = unit["chunks"][0]
            if link is None:
                view[key] = {
                    "condition": "thinking_on",
                    "selection": unit["selection"],
                    "status": unit["status"],
                    "decision": chunk.get("decision"),
                    "reason": chunk.get("reason"),
                    "evidence": chunk.get("evidence_sentence_indices"),
                    "call_ref": chunk.get("call_ref") or (chunk.get("reused_from") or {}).get("call_ref"),
                }
                continue
            decision = link["adopted_decision"] or {}
            if link["adopted"]:
                selection = "selected" if decision.get("decision") in {"needed", "uncertain"} else "not_selected"
                status = "ok"
            else:
                selection = "unresolved"
                status = "failed"
            view[key] = {
                "condition": "fallback_thinking_off",
                "selection": selection,
                "status": status,
                "decision": decision.get("decision"),
                "reason": decision.get("reason"),
                "evidence": decision.get("evidence_sentence_indices"),
                "call_ref": link["fallback_call_ref"],
                "validation": link["validation"],
            }
    return view


def write_index(documents: dict[str, dict], outcomes: list[dict]) -> None:
    view = adopted_view(documents, outcomes)
    counts = {"thinking_on_ok": 0, "fallback_ok": 0, "fallback_failed": 0, "pending": 0}
    lines = [
        "# 전체 검수 index",
        "",
        "thinking ON 244개와 fallback_thinking_off 5개를 구분한다.",
        "fallback 실패는 unresolved이며 not_needed나 uncertain이 아니다.",
        "품질 통과나 T2 채택이 아니다.",
        "",
    ]
    for (video_id, unit_index), item in view.items():
        if item["condition"] == "thinking_on" and item["status"] == "ok":
            counts["thinking_on_ok"] += 1
        elif item["condition"] == "fallback_thinking_off" and item["status"] == "ok":
            counts["fallback_ok"] += 1
        elif item["condition"] == "fallback_thinking_off":
            counts["fallback_failed"] += 1
        elif item["status"] == "pending":
            counts["pending"] += 1
    lines.extend(
        [
            f"- thinking ON 성공: {counts['thinking_on_ok']}",
            f"- fallback 성공: {counts['fallback_ok']}",
            f"- fallback 실패: {counts['fallback_failed']}",
            f"- 미실행: {counts['pending']}",
            f"- 합계: {len(view)}",
            "",
        ]
    )
    current = None
    for video_id, unit_index in sorted(view, key=lambda item: (run_t2.EXPECTED_IDS.index(item[0]), item[1])):
        item = view[(video_id, unit_index)]
        if video_id != current:
            lines.extend([f"## {video_id}", ""])
            current = video_id
        lines.append(
            f"- 단위 {unit_index}: {item['condition']} / {item['selection']} / {item['status']} / "
            f"판정 {item['decision']} / evidence {item['evidence']} / `{item['call_ref']}`"
        )
    lines.append("")
    (FALLBACK_DIR / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_comparison(documents: dict[str, dict], outcomes: list[dict]) -> None:
    view = adopted_view(documents, outcomes)
    lines = [
        "# thinking ON·fallback과 P2 대조",
        "",
        "5개 fallback은 thinking ON 결과가 아니다.",
        "바뀐 단위 수는 품질 점수가 아니다. T2 채택이 아니다.",
        "",
    ]
    changed = []
    unresolved = []
    for video_id in run_t2.EXPECTED_IDS:
        p2_document = run_t2.read_json(P2_DIR / video_id / "visual_candidates.json")
        p2_units = {unit["unit_index"]: unit for unit in p2_document["units"]}
        document = documents[video_id]
        for unit in document["units"]:
            item = view[(video_id, unit["unit_index"])]
            p2_unit = p2_units[unit["unit_index"]]
            prefix = f"{video_id} / {unit['unit_index']} / {item['condition']}"
            if item["status"] != "ok":
                unresolved.append(
                    f"- {prefix}: 채택 판정 없음. 검증 {item.get('validation')}. P2 {p2_unit.get('selection')}."
                )
                continue
            if item["selection"] == p2_unit.get("selection"):
                continue
            p2_chunk = p2_unit["chunks"][0]
            changed.append(
                "\n".join(
                    [
                        f"### {prefix}",
                        "",
                        f"- P2 선택: {p2_unit.get('selection')}",
                        f"- 채택 선택: {item['selection']}",
                        f"- P2: {p2_chunk.get('decision')} / {p2_chunk.get('reason')} / {p2_chunk.get('evidence_sentence_indices')}",
                        f"- 채택: {item['decision']} / {item['reason']} / {item['evidence']}",
                        "",
                    ]
                )
            )
    lines.extend(["## 선택이 달라진 단위", ""])
    lines.extend(changed or ["- 없음", ""])
    lines.extend(["## 채택하지 않은 fallback", ""])
    lines.extend(unresolved or ["- 없음", ""])
    (FALLBACK_DIR / "p2_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if FALLBACK_DIR.exists():
        fail([f"fallback 디렉터리가 이미 있습니다: {FALLBACK_DIR}"])
    documents = load_documents()
    records = load_targets(documents)
    before = snapshot(preserved_paths(records))
    outcomes = execute(records)
    write_results(documents, outcomes, before)
    adopted = sum(1 for item in outcomes if item["link"]["adopted"])
    print(f"fallback_ok {adopted} fallback_failed {len(outcomes) - adopted} posts {len(outcomes)}", flush=True)


if __name__ == "__main__":
    main()
