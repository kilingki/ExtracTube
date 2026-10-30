"""저장 기록만으로 reasoning 예산 소진 청크를 누락 방지 후보로 남긴다. 추론은 하지 않는다."""

from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_t2

ROOT = EXPERIMENT_ROOT
SOURCE = resolve_experiment_path(ROOT / "tests" / "T2" / "runs" / "20261020T184601Z_b6d662")
OUT = SOURCE / "budget_guard"
P2 = run_t2.RUNS_ROOT / run_t2.P2_RUN_ID
EXECUTION_SOURCES = (
    resolve_experiment_path(ROOT / "tests" / "T2" / "run_t2.py"),
    resolve_experiment_path(ROOT / "tests" / "T2" / "apply_budget_guard.py"),
)


def fail(message: str) -> None:
    print(message, flush=True)
    raise SystemExit(1)


def source_files() -> list[Path]:
    files = []
    for path in SOURCE.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if "budget_guard" in path.parts:
            continue
        files.append(path)
    return sorted(files)


def snapshot(paths: list[Path]) -> dict[str, str]:
    return {run_t2.display_path(ROOT, path): run_t2.sha256_file(path) for path in paths}


def execution_source() -> list[dict[str, str]]:
    return [
        {"path": run_t2.display_path(ROOT, path), "sha256": run_t2.sha256_file(path)}
        for path in EXECUTION_SOURCES
    ]


def load_calls() -> tuple[dict[tuple[str, int, int], dict], dict[tuple[str, int, int], str]]:
    calls = {}
    refs = {}
    for path in sorted((SOURCE / "calls").glob("*.json")):
        record = run_t2.read_json(path)
        if not isinstance(record, dict):
            continue
        video_id = record.get("video_id")
        unit_index = record.get("unit_index")
        chunk_index = record.get("chunk_index")
        if not isinstance(video_id, str) or not run_t2.is_int(unit_index) or not run_t2.is_int(chunk_index):
            continue
        ident = (video_id, unit_index, chunk_index)
        if ident in calls:
            fail(f"같은 청크의 원시 호출이 둘입니다: {ident}")
        calls[ident] = record
        refs[ident] = run_t2.display_path(ROOT, path)
    return calls, refs


def load_fallback() -> dict[tuple[str, int, int], dict]:
    payload = run_t2.read_json(SOURCE / "fallback" / "links.json")
    found = {}
    for item in payload["items"]:
        ident = (item["video_id"], item["unit_index"], item["chunk_index"])
        if ident in found:
            fail(f"fallback 연결이 둘입니다: {ident}")
        found[ident] = item
    return found


def load_documents() -> list[dict]:
    documents = []
    for video_id in run_t2.EXPECTED_IDS:
        documents.append(run_t2.read_json(SOURCE / video_id / "visual_candidates.json"))
    return documents


def sentence_line(sentence: dict) -> str:
    return (
        f"- [{sentence['index']}] {run_t2.time_literal(sentence['start_time'])}-{run_t2.time_literal(sentence['end_time'])} "
        f"{run_t2.md_escape(sentence['text'])}"
    )


def write_details(documents: list[dict], derived: list[dict], calls: dict, refs: dict, inputs: dict[str, str]) -> None:
    by_id = {document["video_id"]: document for document in documents}
    lines = [
        "# 예산 소진 정책 적용 상세",
        "",
        f"- 정책 버전: `{run_t2.POLICY_VERSION}`",
        f"- 실행 소스: `{execution_source()[0]['path']}` `{execution_source()[0]['sha256']}`",
        f"- 적용 소스: `{execution_source()[1]['path']}` `{execution_source()[1]['sha256']}`",
        "- 모델 추가 호출은 0회다.",
        "- fallback not_needed여도 정책 후보인 상태를 유지한다. 모델 판정을 needed나 uncertain으로 바꾸지 않는다.",
        "",
    ]
    for document in derived:
        for unit in document["units"]:
            for chunk in unit["chunks"]:
                policy = chunk["policy"]
                if policy["candidate_source"] != "budget_guard":
                    continue
                ident = (document["video_id"], unit["unit_index"], chunk["chunk_index"])
                call_ref = refs[ident]
                source = by_id[document["video_id"]]
                rows = {
                    sentence["index"]: sentence
                    for source_unit in source["units"]
                    for sentence in source_unit["sentences"]
                }
                lines.extend(
                    [
                        f"## {document['video_id']} 단위 {unit['unit_index']}",
                        "",
                        f"- 정책 버전: `{policy['policy_version']}`",
                        f"- 원래 thinking ON 상태: {policy['thinking_on_status']}",
                        f"- 원래 오류: {policy['thinking_on_error']}",
                        f"- 원래 모델 판정: {policy['thinking_on_decision']}",
                        f"- 원래 호출: `{call_ref}`",
                        f"- 원래 호출 hash: `{inputs[call_ref]}`",
                        f"- HTTP {calls[ident].get('http_status')}, finish_reason {calls[ident].get('finish_reason')}, "
                        f"completion {(calls[ident].get('usage') or {}).get('completion_tokens')}, "
                        f"reasoning {((calls[ident].get('usage') or {}).get('completion_tokens_details') or {}).get('reasoning_tokens')}",
                        f"- fallback 판정: {policy['fallback_decision']}",
                        f"- fallback 근거: {policy['fallback_reason']}",
                        f"- fallback evidence: {policy['fallback_evidence']}",
                        f"- fallback 호출: `{policy['fallback_call_ref']}`",
                        "- 정책 후보: 예. candidate_source는 budget_guard다.",
                        "- 모델 추론 성공이 아니다.",
                        f"- 후보 유지 사유: {policy['retention_reason']}",
                        f"- 문장 구간: [{chunk['sentence_start']},{chunk['sentence_end']})",
                        f"- 오디오: {run_t2.time_literal(chunk['audio_start'])}-{run_t2.time_literal(chunk['audio_end'])}",
                        f"- 영상: {run_t2.time_literal(chunk['video_start'])}-{run_t2.time_literal(chunk['video_end'])}",
                        "",
                        "### 원문",
                        "",
                    ]
                )
                for index in range(chunk["sentence_start"], chunk["sentence_end"]):
                    row = rows.get(index)
                    lines.append(sentence_line(row) if row else f"- [{index}] 문장을 찾지 못했습니다.")
                lines.append("")
    (OUT / "policy_details.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index(derived: list[dict], summary: dict) -> None:
    lines = [
        "# 예산 소진 정책 index",
        "",
        f"- 정책 버전: `{run_t2.POLICY_VERSION}`",
        "- thinking_on은 모델이 needed 또는 uncertain으로 판정한 후보다.",
        "- budget_guard는 판정 JSON이 없어 누락 방지로 유지한 후보이며 모델 성공이 아니다.",
        "- 원래 추론 실패는 정책 적용 후에도 실패로 남는다.",
        "- 품질 통과나 T2 채택이 아니다.",
        "",
        f"- 모델 needed: {summary['model_decisions']['needed']}",
        f"- 모델 not_needed: {summary['model_decisions']['not_needed']}",
        f"- 모델 uncertain: {summary['model_decisions']['uncertain']}",
        f"- 모델 판정 없음: {summary['model_decisions']['none']}",
        f"- 원래 추론 실패: {summary['inference_failures']}",
        f"- 정책 적용 전 후보: {summary['pre_policy_candidates']}",
        f"- 정책 적용 후 후보: {summary['policy_candidates']}",
        f"- budget_guard 추가: {len(summary['budget_guard'])}",
        "",
        summary["expectation_note"],
        "",
    ]
    for document in derived:
        lines.extend([f"## {document['video_id']}", ""])
        for unit in document["units"]:
            for chunk in unit["chunks"]:
                policy = chunk["policy"]
                lines.append(
                    f"- 단위 {unit['unit_index']} 청크 {chunk['chunk_index']}: "
                    f"모델 {policy['thinking_on_status']}/{policy['thinking_on_decision']} / "
                    f"fallback {policy['fallback_decision']} / 정책 후보 {policy['policy_candidate']} / "
                    f"{policy['candidate_source']} / 모델 성공 {policy['model_inference_success']}"
                )
        lines.append("")
    (OUT / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_comparison(documents: list[dict], derived: list[dict]) -> None:
    before = {document["video_id"]: document for document in documents}
    lines = [
        "# P2 및 정책 적용 전 비교",
        "",
        f"- 정책 버전: `{run_t2.POLICY_VERSION}`",
        "- 후보 수 차이는 품질 점수가 아니다. T2 채택이 아니다.",
        "- budget_guard는 모델 needed가 아니다.",
        "",
        "## 정책으로 추가된 후보",
        "",
    ]
    for document in derived:
        previous = before[document["video_id"]]
        previous_ids = {candidate["candidate_id"] for candidate in previous["candidates"]}
        p2_document = run_t2.read_json(P2 / document["video_id"] / "visual_candidates.json")
        p2_units = {unit["unit_index"]: unit for unit in p2_document["units"]}
        previous_units = {unit["unit_index"]: unit for unit in previous["units"]}
        for unit in document["units"]:
            policy = unit["chunks"][0]["policy"]
            if policy["candidate_source"] != "budget_guard":
                continue
            candidate = next(item for item in document["candidates"] if item["unit_index"] == unit["unit_index"])
            p2_unit = p2_units[unit["unit_index"]]
            lines.extend(
                [
                    f"### {document['video_id']} / {unit['unit_index']}",
                    "",
                    f"- 정책 적용 전 선택: {previous_units[unit['unit_index']]['selection']}",
                    f"- 정책 적용 전 후보 포함: {candidate['candidate_id'] in previous_ids}",
                    f"- P2 선택: {p2_unit.get('selection')}",
                    f"- P2 판정: {p2_unit['chunks'][0].get('decision')} / {p2_unit['chunks'][0].get('reason')} / {p2_unit['chunks'][0].get('evidence_sentence_indices')}",
                    f"- fallback: {policy['fallback_decision']} / {policy['fallback_reason']} / {policy['fallback_evidence']}",
                    f"- 정책 후보 출처: {candidate['candidate_source']}. 모델 성공 {candidate['model_inference_success']}.",
                    f"- 유지 사유: {candidate['retention_reason']}",
                    f"- 문장 [{candidate['sentence_start']},{candidate['sentence_end']}) "
                    f"오디오 {run_t2.time_literal(candidate['audio_start'])}-{run_t2.time_literal(candidate['audio_end'])} "
                    f"영상 {run_t2.time_literal(candidate['video_start'])}-{run_t2.time_literal(candidate['video_end'])}",
                    "",
                ]
            )
    lines.extend(["## 정책 적용 전과 후보 집합이 다른 단위", ""])
    for document in derived:
        previous = before[document["video_id"]]
        previous_ids = {candidate["candidate_id"] for candidate in previous["candidates"]}
        current_ids = {candidate["candidate_id"] for candidate in document["candidates"]}
        for candidate_id in sorted(current_ids - previous_ids):
            lines.append(f"- 추가: {candidate_id}")
        for candidate_id in sorted(previous_ids - current_ids):
            lines.append(f"- 빠짐: {candidate_id}")
    lines.extend(["", "## P2 선택과 정책 후보 여부가 다른 단위", ""])
    for document in derived:
        p2_document = run_t2.read_json(P2 / document["video_id"] / "visual_candidates.json")
        p2_units = {unit["unit_index"]: unit for unit in p2_document["units"]}
        current = {candidate["unit_index"]: candidate for candidate in document["candidates"]}
        for unit in document["units"]:
            policy_selected = unit["unit_index"] in current
            p2_selected = p2_units[unit["unit_index"]].get("selection") == "selected"
            if policy_selected == p2_selected:
                continue
            source = current.get(unit["unit_index"], {}).get("candidate_source")
            lines.append(
                f"- {document['video_id']} / {unit['unit_index']}: P2 {p2_units[unit['unit_index']].get('selection')}, "
                f"정책 후보 {policy_selected}, 출처 {source}"
            )
    lines.append("")
    (OUT / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def expectation_note(summary: dict) -> str:
    guards = len(summary["budget_guard"])
    pre = summary["pre_policy_candidates"]
    post = summary["policy_candidates"]
    return (
        f"기록에서 다시 계산한 정책 적용 전 후보는 {pre}개다. "
        f"needed {summary['model_decisions']['needed']}개와 uncertain {summary['model_decisions']['uncertain']}개의 합이다. "
        f"예산 소진으로 추가한 후보는 {guards}개이고, 적용 후 후보는 {post}개다. "
        "135개에서 fallback 미선정 4개를 더해 139개가 된다는 예상과 구성이 다르다. "
        "실패 5개는 정책 적용 전 후보에 들어 있지 않고, fallback needed 1개도 모델 후보가 아니다. "
        "규칙을 139개에 맞추지 않았다."
    )


def verify(documents: list[dict], derived: list[dict], calls: dict, summary: dict, before: dict[str, str], inputs: list[Path]) -> None:
    after = snapshot(inputs)
    if after != before:
        fail("원본 기록의 hash가 달라졌습니다.")
    if run_t2.InferClient.created != 0:
        fail("모델 클라이언트를 만들었습니다.")
    ids = []
    for document in derived:
        original = next(item for item in documents if item["video_id"] == document["video_id"])
        original_units = {unit["unit_index"]: unit for unit in original["units"]}
        for unit in document["units"]:
            source_unit = original_units[unit["unit_index"]]
            if unit["selection"] != source_unit["selection"] or unit["status"] != source_unit["status"]:
                fail(f"{document['video_id']} 단위 {unit['unit_index']}: 모델 집계를 바꿨습니다.")
            for chunk, source_chunk in zip(unit["chunks"], source_unit["chunks"]):
                for field in ("status", "decision", "reason", "evidence_sentence_indices", "problems", "sentence_start", "sentence_end", "audio_start", "audio_end", "video_start", "video_end"):
                    if chunk.get(field) != source_chunk.get(field):
                        fail(f"{document['video_id']} 단위 {unit['unit_index']}: 모델 필드 {field}가 달라졌습니다.")
                policy = chunk["policy"]
                if source_chunk.get("status") == "ok" and source_chunk.get("decision") == "not_needed" and policy["policy_candidate"]:
                    fail(f"{document['video_id']} 단위 {unit['unit_index']}: 정상 not_needed를 후보로 바꿨습니다.")
        seen = set()
        for candidate in document["candidates"]:
            if candidate["candidate_id"] in seen:
                fail(f"후보가 중복입니다: {candidate['candidate_id']}")
            seen.add(candidate["candidate_id"])
            ids.append(candidate["candidate_id"])
            chunk = next(
                item
                for source_unit in original["units"]
                if source_unit["unit_index"] == candidate["unit_index"]
                for item in source_unit["chunks"]
                if item["chunk_index"] == candidate["chunk_index"]
            )
            for field in ("sentence_start", "sentence_end", "audio_start", "audio_end", "video_start", "video_end"):
                if candidate[field] != chunk[field]:
                    fail(f"{candidate['candidate_id']}: 후보 범위가 대상 청크와 다릅니다.")
            if candidate["candidate_source"] == "budget_guard":
                if candidate["model_inference_success"] or candidate["evidence_sentence_indices"] is not None:
                    fail(f"{candidate['candidate_id']}: budget_guard를 모델 성공으로 적었습니다.")
                ident = (document["video_id"], candidate["unit_index"], candidate["chunk_index"])
                if not run_t2.reasoning_budget_exhausted(calls.get(ident)):
                    fail(f"{candidate['candidate_id']}: 예산 소진 기록이 아닌데 정책을 적용했습니다.")
            elif candidate["candidate_source"] != "thinking_on" or candidate["model_inference_success"] is not True:
                fail(f"{candidate['candidate_id']}: 후보 출처가 구분되지 않습니다.")
    if len(ids) != len(set(ids)):
        fail("영상 사이에 후보 id가 겹칩니다.")
    discovered = {
        (item["video_id"], item["unit_index"], item["chunk_index"])
        for item in summary["budget_guard"]
    }
    matched = {
        ident
        for ident, call in calls.items()
        if run_t2.reasoning_budget_exhausted(call)
    }
    if discovered != matched:
        fail(f"정책 적용 대상과 원시 기록이 다릅니다: {sorted(discovered)} / {sorted(matched)}")
    if summary["inference_failures"] != sum(1 for document in documents for _unit, chunk in run_t2.iter_chunks(document) if chunk["status"] == "failed"):
        fail("원래 실패 수를 유지하지 않았습니다.")


def write_provenance(inputs: dict[str, str], outputs: list[Path]) -> str:
    payload = {
        "policy_version": run_t2.POLICY_VERSION,
        "model_calls": 0,
        "execution_source": execution_source(),
        "inputs": [{"path": path, "sha256": digest} for path, digest in sorted(inputs.items())],
        "outputs": [
            {"path": run_t2.display_path(ROOT, path), "sha256": run_t2.sha256_file(path)}
            for path in outputs
        ],
    }
    path = OUT / "provenance.json"
    run_t2.dump_json(path, payload)
    digest = run_t2.sha256_file(path)
    (OUT / "provenance.sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def main() -> None:
    if OUT.exists():
        fail(f"파생 디렉터리가 이미 있습니다: {OUT}")
    created = run_t2.InferClient.created
    inputs = source_files()
    before = snapshot(inputs)
    documents = load_documents()
    calls, refs = load_calls()
    fallback = load_fallback()
    for document in documents:
        for unit, chunk in run_t2.iter_chunks(document):
            ident = (document["video_id"], unit["unit_index"], chunk["chunk_index"])
            if ident in refs:
                continue
            reused = chunk.get("reused_from")
            if isinstance(reused, dict) and isinstance(reused.get("call_ref"), str):
                refs[ident] = reused["call_ref"]
    derived, summary = run_t2.apply_budget_guard(documents, calls, fallback, refs)
    summary["expectation_note"] = expectation_note(summary)
    verify(documents, derived, calls, summary, before, inputs)
    OUT.mkdir(parents=True)
    sources = execution_source()
    for document in derived:
        source_path = SOURCE / document["video_id"] / "visual_candidates.json"
        document["budget_guard_provenance"] = {
            "policy_version": run_t2.POLICY_VERSION,
            "source_document": run_t2.display_path(ROOT, source_path),
            "source_document_sha256": before[run_t2.display_path(ROOT, source_path)],
            "execution_source": sources,
        }
        run_t2.dump_json(OUT / document["video_id"] / "visual_candidates.json", document)
    write_index(derived, summary)
    write_details(documents, derived, calls, refs, before)
    write_comparison(documents, derived)
    review = [
        "# T2 예산 소진 정책 검토 기록",
        "",
        f"- 정책 버전: `{run_t2.POLICY_VERSION}`",
        f"- 실행 소스 hash: `{sources[0]['sha256']}` `{sources[1]['sha256']}`",
        "- 판단: pending_review. 전체 의미 품질 통과나 T2 최종 채택이 아니다.",
        "",
        "## 정책 추가 이유",
        "",
        "thinking ON 호출이 4096토큰을 reasoning에 모두 써 판정 JSON을 만들지 못한 뒤, thinking OFF fallback의 not_needed를 그대로 제외하면 화면이 빠질 수 있다. "
        "H2WN130OzLg 단위 7은 fallback이 not_needed였고, 원본 약 630초의 오디오·영상 구간 608.56–672.24 안에서 미용실·편의점 점포 수 비교 그래프가 확인됐다. "
        "이 관찰은 모델 evidence가 아니다. 후보 유지 근거는 예산 소진 오류와 누락 방지 정책이다.",
        "",
        "## 적용",
        "",
        f"- 원시 기록에서 예산 소진으로 확인한 청크: {len(summary['budget_guard'])}개.",
    ]
    for item in summary["budget_guard"]:
        review.append(f"- {item['video_id']} / {item['unit_index']} / chunk {item['chunk_index']}")
    review.extend(
        [
            f"- 실제로 추가된 후보: {len(summary['budget_guard'])}개. 정책 적용 전 {summary['pre_policy_candidates']}개, 적용 후 {summary['policy_candidates']}개.",
            f"- {summary['expectation_note']}",
            "- 모델 추가 호출: 0회.",
            "- 기존 manifest, 원시 호출, 실패 기록, fallback 응답, 기존 결과의 hash는 유지됐다.",
            "- 원래 추론 실패는 실패로 남아 있다. 정책이 실패 이력을 지우지 않는다.",
            "- 후속 분석 대상이 늘어나는 대신, 판정이 비어 필요한 화면이 빠지는 위험을 줄이는 선택이다.",
            "",
        ]
    )
    review_path = OUT / "review_decision.md"
    review_path.write_text("\n".join(review) + "\n", encoding="utf-8")
    outputs = [path for path in sorted(OUT.rglob("*")) if path.is_file() and not path.name.startswith(".")]
    digest = write_provenance(before, outputs)
    verify(documents, derived, calls, summary, before, inputs)
    if created != run_t2.InferClient.created:
        fail("적용 중 모델 클라이언트가 만들어졌습니다.")
    print(summary["expectation_note"], flush=True)
    print(
        f"pre {summary['pre_policy_candidates']} post {summary['policy_candidates']} "
        f"guard {len(summary['budget_guard'])} failures {summary['inference_failures']} "
        f"provenance {digest}",
        flush=True,
    )


if __name__ == "__main__":
    main()
