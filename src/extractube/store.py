from __future__ import annotations
import fcntl
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from extractube.errors import PipelineError
import re
from .errors import VisionError

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


# Immutable visual artifacts and provenance.
"""Durable artifacts, contained paths, immutable entries and per-run hashing."""

SHA = re.compile(r'[0-9a-f]{64}\Z')
RUN = re.compile(r'\d{8}T\d{6}Z_[0-9a-f]{12}\Z')

def wire(obj, *, canonical=False):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'), sort_keys=canonical, allow_nan=False).encode('utf-8')

def digest(obj):
    return hashlib.sha256(wire(obj, canonical=True)).hexdigest()

def sha(path):
    return sha256_file(Path(path))

def safe(work, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise VisionError('안전하지 않은 artifact 경로입니다.', kind='input')
    p = PurePosixPath(relative)
    if p.is_absolute() or '..' in p.parts or '.' in p.parts:
        raise VisionError('작업 폴더 밖 경로는 허용하지 않습니다.', kind='input')
    path = Path(work) / relative
    try:
        path.resolve().relative_to(Path(work).resolve())
    except ValueError as exc:
        raise VisionError('작업 폴더 밖 symlink는 허용하지 않습니다.', kind='input') from exc
    return path

def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def write(path, obj, *, immutable=False, raw=False):
    path = Path(path)
    data = obj if raw else wire(obj) + b'\n'
    if immutable and path.exists():
        if path.read_bytes() != data:
            raise VisionError(f'불변 artifact 덮어쓰기 금지: {path.name}', kind='commit')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)

def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result

def loads(data):
    if isinstance(data, bytes):
        data = data.decode('utf-8', errors='strict')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))

def read(path):
    try:
        return loads(Path(path).read_bytes())
    except (OSError, ValueError, UnicodeError) as exc:
        raise VisionError(f'JSON 읽기 실패: {Path(path).name}', kind='validation') from exc

def ref(work, path):
    relative = Path(path).relative_to(work).as_posix()
    safe(work, relative)
    return {'path': relative, 'sha256': sha(path)}

class HashCache:
    def __init__(self):
        self.entries = {}
    def hash(self, path):
        path = Path(path)
        s = path.stat()
        key = (str(path.resolve()), s.st_ino, s.st_size, s.st_mtime_ns)
        if key not in self.entries:
            self.entries[key] = sha(path)
        return self.entries[key]
    def check(self, work, reference):
        if not isinstance(reference, dict) or not SHA.fullmatch(str(reference.get('sha256', ''))):
            raise VisionError('SHA-256 참조 형식 오류입니다.', kind='validation')
        p = safe(work, reference.get('path'))
        if not p.is_file() or self.hash(p) != reference['sha256']:
            raise VisionError(f'파일 hash가 다릅니다: {reference["path"]}', kind='validation')
        return p
