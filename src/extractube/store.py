from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from extractube.errors import PipelineError

STAGES = ("video", "wav", "asr", "fa", "sentences")
DOWNSTREAM = {
    "video": ("wav", "asr", "fa", "sentences"),
    "wav": ("asr", "fa", "sentences"),
    "asr": ("fa", "sentences"),
    "fa": ("sentences",),
    "sentences": (),
}
BACKUP_DIR = ".commit-backup"


class WorkLock:
    """Exclusive lock released when the process exits or release() is called."""

    def __init__(self, work: Path) -> None:
        self.path = work / ".extractube.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fd.close()
            raise PipelineError(
                f"같은 영상 작업이 이미 실행 중입니다: {work}",
                kind="lock",
            ) from exc

    def release(self) -> None:
        if self._fd.closed:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._fd.close()


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_metadata(video_id: str, source_url: str) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "source_url": source_url,
        "results": {},
    }


def metadata_path(work: Path) -> Path:
    return work / "metadata.json"


def load_metadata(work: Path, video_id: str, source_url: str) -> dict[str, Any]:
    path = metadata_path(work)
    if not path.is_file():
        return empty_metadata(video_id, source_url)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_metadata(video_id, source_url)
    if not isinstance(payload, dict):
        return empty_metadata(video_id, source_url)
    payload.setdefault("video_id", video_id)
    payload.setdefault("source_url", source_url)
    results = payload.get("results")
    if not isinstance(results, dict):
        payload["results"] = {}
    return payload


def identity(record: dict[str, Any]) -> dict[str, str]:
    return {
        "kind": record["kind"],
        "result_id": record["result_id"],
        "sha256": record["sha256"],
    }


def result_record(
    *,
    kind: str,
    relative_path: str,
    file_hash: str,
    upstream: list[dict[str, str]],
    settings: dict[str, Any],
    media: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "result_id": str(uuid.uuid4()),
        "path": relative_path,
        "sha256": file_hash,
        "created_at": now_iso(),
        "kind": kind,
        "upstream": upstream,
        "settings": settings,
    }
    if media:
        record["media"] = media
    if extra:
        record.update(extra)
    return record


def _backup_dir(work: Path) -> Path:
    return work / BACKUP_DIR


def recover_interrupted_commit(work: Path) -> None:
    """Roll back a commit that did not finish. A COMPLETE marker means it did."""
    backup = _backup_dir(work)
    if not backup.exists():
        return
    if (backup / "COMPLETE").is_file():
        shutil.rmtree(backup, ignore_errors=True)
        return
    _restore_backup(work)


def _fsync_path(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _restore_backup(work: Path) -> None:
    backup = _backup_dir(work)
    manifest_path = backup / "manifest.json"
    if not manifest_path.is_file():
        shutil.rmtree(backup, ignore_errors=True)
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = work / manifest["artifact_rel"]
    if manifest.get("had_artifact"):
        artifact.parent.mkdir(parents=True, exist_ok=True)
        os.replace(backup / "artifact", artifact)
    elif artifact.exists():
        artifact.unlink()
    meta = metadata_path(work)
    if manifest.get("had_metadata"):
        os.replace(backup / "metadata.json", meta)
    elif meta.exists():
        meta.unlink()
    shutil.rmtree(backup, ignore_errors=True)


def commit_files(
    work: Path,
    artifact_rel: str,
    temp_file: Path,
    metadata: dict[str, Any],
    *,
    fault: str | None = None,
) -> None:
    """Promote one artifact and metadata. Existing bytes stay recoverable until done.

    The pair is not one atomic replace. A crash before COMPLETE restores the
    previous success file and metadata. After COMPLETE, leftover backup is deleted.
    """
    backup = _backup_dir(work)
    if backup.exists():
        recover_interrupted_commit(work)
    backup.mkdir(parents=True, exist_ok=False)
    artifact = work / artifact_rel
    meta = metadata_path(work)
    had_artifact = artifact.is_file()
    had_metadata = meta.is_file()
    manifest = {
        "artifact_rel": artifact_rel,
        "had_artifact": had_artifact,
        "had_metadata": had_metadata,
    }
    try:
        (backup / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
        if had_artifact:
            shutil.copy2(artifact, backup / "artifact")
            _fsync_path(backup / "artifact")
        if had_metadata:
            shutil.copy2(meta, backup / "metadata.json")
            _fsync_path(backup / "metadata.json")
        _fsync_path(backup / "manifest.json")

        artifact.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_file, artifact)
        if fault == "metadata":
            raise PipelineError(
                "metadata 저장 중 오류가 발생했습니다.",
                kind="commit",
            )
        temporary_meta = work / ".metadata.json.tmp"
        temporary_meta.write_text(dumps(metadata), encoding="utf-8")
        _fsync_path(temporary_meta)
        os.replace(temporary_meta, meta)
        complete = backup / "COMPLETE"
        complete.write_text("ok\n", encoding="utf-8")
        _fsync_path(complete)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if temp_file.exists():
            temp_file.unlink()
        _restore_backup(work)
        raise


def apply_result(metadata: dict[str, Any], stage: str, record: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(metadata))
    results = updated.setdefault("results", {})
    results[stage] = record
    for downstream in DOWNSTREAM[stage]:
        results.pop(downstream, None)
    return updated


def write_diagnostic(
    work: Path,
    attempt_id: str,
    payload: dict[str, Any],
    body: bytes | None,
) -> Path:
    dest = work / "diagnostics" / attempt_id
    dest.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (dest / "body").write_bytes(body)
        payload = {**payload, "body_path": f"diagnostics/{attempt_id}/body"}
    (dest / "error.json").write_text(dumps(payload), encoding="utf-8")
    return dest
