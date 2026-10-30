"""E00~E12 순차 반복 실행기."""

from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path


import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine
import evaluate

SWEEP_ROOT = Path(__file__).resolve().parent
CAMPAIGNS = SWEEP_ROOT / "campaigns"
HASH_FILES = {
    "engine_sha256": SWEEP_ROOT / "engine.py",
    "prompts_sha256": SWEEP_ROOT / "prompts.py",
    "evaluate_sha256": SWEEP_ROOT / "evaluate.py",
    "runner_sha256": SWEEP_ROOT / "run_sweep.py",
    "plan_sha256": SWEEP_ROOT / "experiment_plan.json",
}
KST = timezone(timedelta(hours=9))
CURRENT = {"proc": None, "state": None, "campaign": None}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_hashes() -> dict[str, str]:
    return {name: file_sha256(path) for name, path in HASH_FILES.items()}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def append_event(campaign: Path, event: dict) -> None:
    event = dict(event)
    event["at"] = engine.utc_now()
    with (campaign / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def t1_inputs() -> list[dict]:
    return load_json(engine.T1_OUTPUTS / "run.json")["inputs"]


def verify_inputs(expected: list[dict] | None = None) -> list[str]:
    recorded = t1_inputs()
    if expected is not None and expected != recorded:
        return ["manifest 입력 해시가 T1 run.json과 다릅니다."]
    problems = []
    for item in recorded:
        alignment = engine.DATA_ROOT / item["video_id"] / "audio" / "alignment_sentences.json"
        metadata = engine.DATA_ROOT / item["video_id"] / "metadata.json"
        if not alignment.is_file() or file_sha256(alignment) != item["alignment_sha256"]:
            problems.append(f"{item['video_id']} alignment 해시가 다릅니다.")
        if not metadata.is_file() or file_sha256(metadata) != item["metadata_sha256"]:
            problems.append(f"{item['video_id']} metadata 해시가 다릅니다.")
    plan = engine.load_plan()
    counts = plan["inputs"]["per_video_sentences"]
    videos, load_problems = engine.load_selected(engine.DATA_ROOT, plan["inputs"]["all_ids"])
    problems.extend(load_problems)
    if not load_problems:
        for video in videos:
            if len(video["sentences"]) != counts[video["video_id"]]:
                problems.append(f"{video['video_id']} 문장 수가 계획과 다릅니다.")
    return problems


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def probe_server(base_url: str, timeout: float = 5) -> dict:
    identity = {
        "reachable": False,
        "checkpoint": "unknown",
        "revision": "unknown",
        "quantization": "unknown",
        "engine": "unknown",
        "chat_template": "unknown",
        "context_limit": "unknown",
        "server_generation_defaults": "unknown",
        "model_ids": "unknown",
        "endpoints": {},
    }
    opener = urllib.request.build_opener(_NoRedirect())
    for path in ("/health", "/v1/models", "/version"):
        url = base_url.rstrip("/") + path
        try:
            with opener.open(url, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                identity["reachable"] = True
                identity["endpoints"][path] = {"status": response.status, "body": body[:1500]}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            identity["reachable"] = True
            identity["endpoints"][path] = {"status": exc.code, "body": raw[:1500]}
        except Exception as exc:  # noqa: BLE001
            identity["endpoints"][path] = {"error": str(exc)}
    models = identity["endpoints"].get("/v1/models") or {}
    body = models.get("body")
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list):
            names = [item.get("id") for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]
            if names:
                identity["model_ids"] = sorted(names)
    parsed = urllib.request.urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not identity["reachable"]:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                identity["reachable"] = True
        except OSError as exc:
            identity["connect_error"] = str(exc)
    return identity


def identity_conflict(previous: dict, current: dict) -> str | None:
    for key in ("model_ids", "engine", "revision", "checkpoint", "quantization", "context_limit"):
        old = previous.get(key, "unknown")
        new = current.get(key, "unknown")
        if old in (None, "unknown") or new in (None, "unknown"):
            continue
        if old != new:
            return key
    return None


def fill_identity(previous: dict, current: dict) -> list[str]:
    filled = []
    for key, value in current.items():
        if key == "endpoints":
            continue
        if previous.get(key) in (None, "unknown") and value not in (None, "unknown"):
            previous[key] = value
            filled.append(key)
    return filled


def new_slot(phase: str, experiment_id: str | None, repeat: str) -> dict:
    return {
        "status": "pending",
        "phase": phase,
        "experiment_id": experiment_id,
        "repeat": repeat,
        "attempts_started": 0,
        "accepted_attempt": None,
        "failure_class": None,
        "mode_effect": None,
        "reason": None,
    }


def initial_state(campaign_id: str, plan: dict) -> dict:
    slots = {}
    for repeat in plan["repeats"]:
        for experiment_id in plan["experiment_ids"]:
            key = f"search/{experiment_id}/{repeat}"
            slots[key] = new_slot("search", experiment_id, repeat)
    return {
        "campaign_id": campaign_id,
        "elapsed_seconds": 0.0,
        "stopped_reason": None,
        "slots": slots,
    }


def create_campaign() -> Path:
    plan = engine.load_plan()
    problems = verify_inputs()
    if problems:
        raise SystemExit("입력 해시 검증 실패:\n" + "\n".join(problems))
    campaign_id = datetime.now(KST).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    campaign = CAMPAIGNS / campaign_id
    campaign.mkdir(parents=True)
    identity = probe_server(plan["common"]["infer_base_url"])
    manifest = {
        "campaign_id": campaign_id,
        "created_at": engine.utc_now(),
        "hashes": code_hashes(),
        "input_hashes": t1_inputs(),
        "identity": identity,
        "response_fingerprint": None,
        "time_limit_seconds": plan["campaign_time_limit_seconds"],
        "control_limits": [
            "확인되지 않은 체크포인트, 리비전, 양자화, 엔진, 채팅 템플릿, 컨텍스트 상한, 서버 기본값은 unknown이다.",
            "응답 model 문자열이나 system_fingerprint만으로 동일 가중치를 보장하지 않는다.",
        ],
    }
    write_json(campaign / "manifest.json", manifest)
    write_json(campaign / "state.json", initial_state(campaign_id, plan))
    (campaign / "events.jsonl").write_text("", encoding="utf-8")
    runner = Path(__file__).resolve().relative_to(engine.ROOT).as_posix()
    command = f"python3 -B {runner} --resume {campaign.relative_to(engine.ROOT).as_posix()}"
    (campaign / "resume_command.txt").write_text(command + "\n", encoding="utf-8")
    (campaign / "pid.txt").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    append_event(campaign, {"event": "created", "pid": os.getpid()})
    return campaign


def ensure_hashes(campaign: Path) -> None:
    manifest = load_json(campaign / "manifest.json")
    current = code_hashes()
    if current != manifest["hashes"]:
        raise SystemExit("코드, 프롬프트 또는 계획 해시가 campaign과 다릅니다. 새 campaign으로 시작해야 합니다.")
    problems = verify_inputs(manifest["input_hashes"])
    if problems:
        raise SystemExit("입력 해시가 바뀌어 중단합니다:\n" + "\n".join(problems))


def search_terminal(state: dict, plan: dict) -> bool:
    for repeat in plan["repeats"]:
        for experiment_id in plan["experiment_ids"]:
            status = state["slots"][f"search/{experiment_id}/{repeat}"]["status"]
            if status not in {"validated", "failed", "skipped"}:
                return False
    return True


def ensure_confirm_slots(state: dict, plan: dict, selection: dict) -> None:
    for repeat in plan["repeats"]:
        control = f"confirm/E00/{repeat}"
        if control not in state["slots"]:
            state["slots"][control] = new_slot("confirm", "E00", repeat)
        selected = selection.get("selected_id")
        if selected:
            key = f"confirm/{selected}/{repeat}"
            if key not in state["slots"]:
                state["slots"][key] = new_slot("confirm", selected, repeat)
        else:
            key = f"confirm/selected/{repeat}"
            if key not in state["slots"]:
                slot = new_slot("confirm", None, repeat)
                slot["status"] = "skipped"
                slot["reason"] = "no_eligible_candidate"
                state["slots"][key] = slot


def maybe_select(campaign: Path, state: dict, plan: dict) -> dict | None:
    path = campaign / "selection.json"
    if path.is_file():
        selection = load_json(path)
        selected = selection.get("selected_id")
        if selected:
            current = evaluate.canonical_hash(engine.experiment_config(selected, plan).public_dict())
            if current != selection.get("config_hash"):
                raise SystemExit("selection.json의 설정 해시가 현재 계획과 다릅니다.")
        ensure_confirm_slots(state, plan, selection)
        return selection
    if not search_terminal(state, plan):
        return None
    selection = evaluate.build_selection(campaign, state, plan)
    write_json(path, selection)
    append_event(campaign, {"event": "selected", "selected_id": selection["selected_id"], "method": selection["method"]})
    ensure_confirm_slots(state, plan, selection)
    write_json(campaign / "state.json", state)
    return selection


def decide_resume_action(slot: dict, attempt_complete: bool, attempt_limit: int = 2) -> str:
    status = slot["status"]
    if status in {"validated", "failed", "skipped"}:
        return "skip"
    if status in {"running", "validating"}:
        if attempt_complete:
            return "accept"
        if slot.get("attempts_started", 0) >= attempt_limit:
            return "fail"
        return "retry"
    if status == "pending":
        return "start"
    return "fail"


def attempt_complete(campaign: Path, slot: dict, plan: dict) -> bool:
    attempt = slot.get("attempts_started") or 0
    if attempt < 1 or not slot.get("experiment_id"):
        return False
    path = evaluate.attempt_dir(campaign, slot["phase"], slot["experiment_id"], slot["repeat"], attempt)
    validation = load_json(path / "validation.json") if (path / "validation.json").is_file() else None
    if validation and validation.get("ok"):
        return True
    result = load_json(path / "attempt_result.json") if (path / "attempt_result.json").is_file() else None
    if not result or result.get("code") != 0:
        return False
    input_ids = plan["inputs"]["search_ids"] if slot["phase"] == "search" else plan["inputs"]["all_ids"]
    checked = evaluate.validate_attempt(path, engine.experiment_config(slot["experiment_id"], plan), input_ids)
    write_json(path / "validation.json", checked)
    return bool(checked["ok"])


def next_slot_key(state: dict) -> str | None:
    for key, slot in state["slots"].items():
        if slot["status"] not in {"validated", "failed", "skipped"}:
            return key
    return None


def add_elapsed(state: dict, started: float) -> None:
    state["elapsed_seconds"] += time.perf_counter() - started


def remember_fingerprint(campaign: Path, manifest: dict, fingerprints: list[str]) -> str | None:
    if not fingerprints:
        return None
    current = fingerprints[0]
    if any(value != current for value in fingerprints):
        return "response_fingerprint"
    expected = manifest.get("response_fingerprint")
    if expected and expected != current:
        return "response_fingerprint"
    if not expected:
        manifest["response_fingerprint"] = current
        write_json(campaign / "manifest.json", manifest)
        append_event(campaign, {"event": "fingerprint", "value": current})
    return None


def run_attempt(campaign: Path, state: dict, slot_key: str, plan: dict) -> str:
    slot = state["slots"][slot_key]
    manifest = load_json(campaign / "manifest.json")
    limit = manifest["time_limit_seconds"]
    if state["elapsed_seconds"] >= limit:
        state["stopped_reason"] = "time_limit"
        return "stop"
    base_url = plan["common"]["infer_base_url"]
    identity = None
    for probe_index in range(plan["server_probe_attempts"]):
        if state["elapsed_seconds"] >= limit:
            state["stopped_reason"] = "time_limit"
            return "stop"
        started = time.perf_counter()
        identity = probe_server(base_url)
        add_elapsed(state, started)
        if identity["reachable"]:
            break
        if probe_index + 1 < plan["server_probe_attempts"]:
            wait = plan["server_probe_interval_seconds"]
            remaining = limit - state["elapsed_seconds"]
            if remaining <= 0:
                state["stopped_reason"] = "time_limit"
                return "stop"
            time.sleep(min(wait, remaining))
            state["elapsed_seconds"] += min(wait, remaining)
    if identity is None or not identity["reachable"]:
        state["stopped_reason"] = "server_unreachable"
        append_event(campaign, {"event": "server_unreachable", "slot": slot_key})
        return "stop"
    conflict = identity_conflict(manifest["identity"], identity)
    if conflict:
        state["stopped_reason"] = "identity_change"
        append_event(campaign, {"event": "identity_change", "field": conflict, "slot": slot_key})
        return "stop"
    filled = fill_identity(manifest["identity"], identity)
    if filled:
        write_json(campaign / "manifest.json", manifest)
        append_event(campaign, {"event": "identity_filled", "fields": filled})
    attempt = slot["attempts_started"] + 1
    slot["attempts_started"] = attempt
    slot["status"] = "running"
    write_json(campaign / "state.json", state)
    append_event(campaign, {"event": "start", "slot": slot_key, "attempt": attempt})
    directory = evaluate.attempt_dir(campaign, slot["phase"], slot["experiment_id"], slot["repeat"], attempt)
    directory.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "--execute-attempt",
        str(campaign),
        slot["phase"],
        slot["experiment_id"],
        slot["repeat"],
        str(attempt),
    ]
    log_path = directory / "stdout.log"
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(command, cwd=engine.ROOT, stdout=log_handle, stderr=subprocess.STDOUT)
        CURRENT["proc"] = proc
        remaining = limit - state["elapsed_seconds"]
        timed_out = False
        try:
            proc.wait(timeout=max(0, remaining))
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)
    CURRENT["proc"] = None
    add_elapsed(state, started)
    if timed_out or state["elapsed_seconds"] >= limit and proc.returncode is None:
        state["stopped_reason"] = "time_limit"
        append_event(campaign, {"event": "time_limit", "slot": slot_key, "attempt": attempt})
        write_json(campaign / "state.json", state)
        return "stop"
    result_path = directory / "attempt_result.json"
    result = load_json(result_path) if result_path.is_file() else None
    code = proc.returncode
    if result and result.get("fingerprints"):
        fingerprint_conflict = remember_fingerprint(campaign, manifest, result["fingerprints"])
        if fingerprint_conflict:
            slot["status"] = "failed"
            slot["failure_class"] = "identity_change"
            slot["reason"] = "identity_change"
            state["stopped_reason"] = "identity_change"
            append_event(campaign, {"event": "identity_change", "field": fingerprint_conflict, "slot": slot_key})
            write_json(campaign / "state.json", state)
            return "stop"
    if code == 0 and result and result.get("code") == 0:
        slot["status"] = "validating"
        write_json(campaign / "state.json", state)
        input_ids = plan["inputs"]["search_ids"] if slot["phase"] == "search" else plan["inputs"]["all_ids"]
        validation = evaluate.validate_attempt(directory, engine.experiment_config(slot["experiment_id"], plan), input_ids)
        write_json(directory / "validation.json", validation)
        if not validation["ok"]:
            slot["status"] = "failed"
            slot["failure_class"] = "code_bug"
            slot["reason"] = "replay_or_structure"
            state["stopped_reason"] = "code_bug"
            append_event(campaign, {"event": "code_bug", "slot": slot_key, "problems": validation["problems"][:8]})
            write_json(campaign / "state.json", state)
            return "stop"
        slot["status"] = "validated"
        slot["accepted_attempt"] = attempt
        slot["failure_class"] = None
        slot["mode_effect"] = result.get("mode_effect")
        slot["reason"] = "mode_effect_unconfirmed" if result.get("mode_effect") == "unconfirmed" else None
        append_event(campaign, {"event": "validated", "slot": slot_key, "attempt": attempt})
        write_json(campaign / "state.json", state)
        return "continue"
    failure_class = (result or {}).get("failure_class") or "contract"
    if code == 3 or failure_class == "unsupported":
        slot["status"] = "skipped"
        slot["failure_class"] = "unsupported"
        slot["reason"] = "unsupported"
        append_event(campaign, {"event": "skipped", "slot": slot_key, "reason": "unsupported"})
        write_json(campaign / "state.json", state)
        return "continue"
    if code == 4 or failure_class in {"unreachable", "server"}:
        processing = (result or {}).get("processing_calls") or 0
        if failure_class == "unreachable" and processing == 0:
            slot["attempts_started"] = max(0, slot["attempts_started"] - 1)
            slot["status"] = "pending"
            state["stopped_reason"] = "server_unreachable"
        else:
            slot["status"] = "failed"
            slot["failure_class"] = failure_class
            slot["reason"] = failure_class
            state["stopped_reason"] = "identity_change" if failure_class == "server" else "server_unreachable"
        append_event(campaign, {"event": "server_stop", "slot": slot_key, "failure_class": failure_class})
        write_json(campaign / "state.json", state)
        return "stop"
    if code == 5 or failure_class == "code_bug":
        slot["status"] = "failed"
        slot["failure_class"] = "code_bug"
        slot["reason"] = "code_bug"
        state["stopped_reason"] = "code_bug"
        append_event(campaign, {"event": "code_bug", "slot": slot_key, "message": (result or {}).get("message")})
        write_json(campaign / "state.json", state)
        return "stop"
    if failure_class == "transient" and attempt < plan["slot_attempt_limit"]:
        append_event(campaign, {"event": "retry", "slot": slot_key, "attempt": attempt})
        write_json(campaign / "state.json", state)
        wait = plan["retry_wait_seconds"]
        remaining = limit - state["elapsed_seconds"]
        if remaining <= 0:
            state["stopped_reason"] = "time_limit"
            write_json(campaign / "state.json", state)
            return "stop"
        time.sleep(min(wait, remaining))
        state["elapsed_seconds"] += min(wait, remaining)
        return "retry"
    slot["status"] = "failed"
    slot["failure_class"] = failure_class
    slot["reason"] = failure_class
    append_event(campaign, {"event": "failed", "slot": slot_key, "failure_class": failure_class})
    write_json(campaign / "state.json", state)
    return "continue"


def recover_slot(campaign: Path, state: dict, slot_key: str, plan: dict) -> str:
    slot = state["slots"][slot_key]
    action = decide_resume_action(slot, False, plan["slot_attempt_limit"])
    if slot["status"] in {"running", "validating"}:
        action = decide_resume_action(slot, attempt_complete(campaign, slot, plan), plan["slot_attempt_limit"])
    if action == "accept":
        slot["status"] = "validated"
        slot["accepted_attempt"] = slot["attempts_started"]
        append_event(campaign, {"event": "recovered", "slot": slot_key})
        write_json(campaign / "state.json", state)
        return "continue"
    if action == "fail":
        slot["status"] = "failed"
        slot["reason"] = slot.get("reason") or "attempt_limit"
        append_event(campaign, {"event": "failed", "slot": slot_key, "reason": "attempt_limit"})
        write_json(campaign / "state.json", state)
        return "continue"
    if action == "skip":
        return "continue"
    return run_attempt(campaign, state, slot_key, plan)


def run_campaign(campaign: Path) -> int:
    ensure_hashes(campaign)
    plan = engine.load_plan()
    state = load_json(campaign / "state.json")
    CURRENT["state"] = state
    CURRENT["campaign"] = campaign
    (campaign / "pid.txt").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    if state.get("stopped_reason") == "time_limit":
        print("시간 한도에 도달한 campaign입니다. 한도를 늘리지 않습니다.", flush=True)
        evaluate.rebuild_reports(campaign, state, plan, load_json(campaign / "manifest.json"))
        return 0
    if state.get("stopped_reason") == "code_bug":
        print("코드 버그로 중단된 campaign입니다. 수정 후 새 campaign으로 시작해야 합니다.", flush=True)
        return 2
    if state.get("stopped_reason") in {"user_stop", "server_unreachable", "identity_change"}:
        if state["stopped_reason"] == "identity_change":
            print("서버 정체성 변경으로 중단된 campaign입니다.", flush=True)
            return 2
        state["stopped_reason"] = None
        write_json(campaign / "state.json", state)
    append_event(campaign, {"event": "loop", "pid": os.getpid()})
    while True:
        if state["elapsed_seconds"] >= plan["campaign_time_limit_seconds"]:
            state["stopped_reason"] = "time_limit"
            write_json(campaign / "state.json", state)
            break
        selection = maybe_select(campaign, state, plan)
        key = next_slot_key(state)
        if key is None:
            if selection is None and search_terminal(state, plan):
                continue
            break
        print(f"slot {key} {state['slots'][key]['status']}", flush=True)
        outcome = recover_slot(campaign, state, key, plan)
        evaluate.rebuild_reports(campaign, state, plan, load_json(campaign / "manifest.json"))
        if outcome == "stop":
            break
        if outcome == "retry":
            continue
    evaluate.rebuild_reports(campaign, state, plan, load_json(campaign / "manifest.json"))
    counts: dict[str, int] = {}
    for slot in state["slots"].values():
        counts[slot["status"]] = counts.get(slot["status"], 0) + 1
    print(json.dumps({"campaign": str(campaign), "counts": counts, "stopped_reason": state.get("stopped_reason")}, ensure_ascii=False), flush=True)
    return 0 if state.get("stopped_reason") is None else 1


def execute_attempt(campaign: Path, phase: str, experiment_id: str, repeat: str, attempt: int) -> int:
    try:
        ensure_hashes(campaign)
        plan = engine.load_plan()
        manifest = load_json(campaign / "manifest.json")
        identity = probe_server(plan["common"]["infer_base_url"])
        if not identity["reachable"]:
            result = {"code": 4, "failure_class": "unreachable", "message": "server unreachable", "processing_calls": 0, "fingerprints": []}
            directory = evaluate.attempt_dir(campaign, phase, experiment_id, repeat, attempt)
            directory.mkdir(parents=True, exist_ok=True)
            write_json(directory / "attempt_result.json", result)
            return 4
        conflict = identity_conflict(manifest["identity"], identity)
        if conflict:
            result = {"code": 4, "failure_class": "server", "message": conflict, "processing_calls": 0, "fingerprints": []}
            directory = evaluate.attempt_dir(campaign, phase, experiment_id, repeat, attempt)
            directory.mkdir(parents=True, exist_ok=True)
            write_json(directory / "attempt_result.json", result)
            return 4
        config = engine.experiment_config(experiment_id, plan)
        input_ids = plan["inputs"]["search_ids"] if phase == "search" else plan["inputs"]["all_ids"]
        expected_key = "search" if phase == "search" else "all"
        directory = evaluate.attempt_dir(campaign, phase, experiment_id, repeat, attempt)
        directory.mkdir(parents=True, exist_ok=True)
        effective = {
            "config": config.public_dict(),
            "prompts": config.prompt_texts(),
            "input_ids": input_ids,
            "expected_count": len(input_ids),
            "expected_sentence_count": plan["inputs"]["expected_sentence_count"][expected_key],
            "phase": phase,
            "repeat": repeat,
            "attempt": attempt,
        }
        write_json(directory / "effective_config.json", effective)
        outcome = engine.execute_slot(
            engine.DATA_ROOT,
            directory,
            config,
            input_ids,
            len(input_ids),
            plan["inputs"]["expected_sentence_count"][expected_key],
            expected_fingerprint=manifest.get("response_fingerprint"),
            phase=phase,
            repeat=repeat,
            attempt=attempt,
        )
        write_json(directory / "attempt_result.json", outcome)
        return int(outcome["code"])
    except engine.IdentityChange as exc:
        directory = evaluate.attempt_dir(campaign, phase, experiment_id, repeat, attempt)
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / "attempt_result.json", {"code": 4, "failure_class": "server", "message": str(exc), "processing_calls": 0, "fingerprints": []})
        return 4
    except Exception as exc:  # noqa: BLE001
        directory = evaluate.attempt_dir(campaign, phase, experiment_id, repeat, attempt)
        directory.mkdir(parents=True, exist_ok=True)
        write_json(
            directory / "attempt_result.json",
            {"code": 5, "failure_class": "code_bug", "message": f"{type(exc).__name__}: {exc}", "processing_calls": 0, "fingerprints": []},
        )
        return 5


def check_resume() -> None:
    assert decide_resume_action({"status": "validated", "attempts_started": 1}, True) == "skip"
    assert decide_resume_action({"status": "failed", "attempts_started": 2}, False) == "skip"
    assert decide_resume_action({"status": "skipped", "attempts_started": 0}, False) == "skip"
    assert decide_resume_action({"status": "running", "attempts_started": 1}, True) == "accept"
    assert decide_resume_action({"status": "validating", "attempts_started": 1}, False) == "retry"
    assert decide_resume_action({"status": "running", "attempts_started": 2}, False) == "fail"
    assert decide_resume_action({"status": "pending", "attempts_started": 0}, False) == "start"
    plan = engine.load_plan()
    state = initial_state("test", plan)
    assert next_slot_key(state) == "search/E00/r01"
    state["slots"]["search/E00/r01"]["status"] = "validated"
    assert next_slot_key(state) == "search/E01/r01"
    for slot in state["slots"].values():
        slot["status"] = "validated"
    assert search_terminal(state, plan)
    assert next_slot_key(state) is None


def run_check() -> int:
    failed = False
    if engine.run_checks() != 0:
        failed = True
    for name, function in (("resume", check_resume), ("selection", evaluate.check_helpers)):
        try:
            function()
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"check {name} failure: {exc}", flush=True)
        else:
            print(f"check {name} ok", flush=True)
    if failed:
        return 1
    print("sweep check ok", flush=True)
    return 0


def handle_signal(signum: int, _frame) -> None:
    proc = CURRENT["proc"]
    if proc is not None and proc.poll() is None:
        proc.terminate()
    state = CURRENT["state"]
    campaign = CURRENT["campaign"]
    if state is not None and campaign is not None:
        state["stopped_reason"] = "user_stop"
        write_json(campaign / "state.json", state)
        append_event(campaign, {"event": "user_stop", "signal": signum})
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--run-all", action="store_true")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--execute-attempt", nargs=5, metavar=("CAMPAIGN", "PHASE", "EXPERIMENT", "REPEAT", "ATTEMPT"))
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.check:
        return run_check()
    if args.execute_attempt:
        campaign, phase, experiment_id, repeat, attempt = args.execute_attempt
        return execute_attempt(Path(campaign), phase, experiment_id, repeat, int(attempt))
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    if args.run_all:
        campaign = create_campaign()
        print(campaign, flush=True)
        return run_campaign(campaign)
    if args.resume:
        return run_campaign(args.resume.resolve())
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
