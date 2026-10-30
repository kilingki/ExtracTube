"""File, time and upstream-image helpers for offline T5 evidence review."""
from __future__ import annotations

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path

import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = EXPERIMENT_ROOT
SECOND = Decimal("0.000000001")

# Historical JSON keeps its original paths; only readers resolve moved folders.
MOVED = {
    "EXPERIMENTS.md": "archive/EXPERIMENTS.md",
    "legacy": "archive/legacy",
    "comparison": "archive/comparison",
    "model_storage": "archive/model_storage",
    "naming_migration": "archive/naming_migration",
    "work": "archive/work",
    "tools": "resources/tools",
    "vendor": "resources/vendor",
}


def resolve_path(path: Path) -> Path:
    path = resolve_experiment_path(Path(path))
    base = resolve_experiment_path(ROOT / "tests/T5")
    try:
        relative = path.absolute().relative_to(base)
    except ValueError:
        return path
    prefix = MOVED.get(relative.parts[0]) if relative.parts else None
    return base / prefix / Path(*relative.parts[1:]) if prefix else path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with resolve_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

class HashCache:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def file(self, path: Path) -> str:
        key = str(path.resolve())
        cached = self._values.get(key)
        if cached is None:
            cached = sha256_file(path)
            self._values[key] = cached
        return cached

def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()

def read_json(path: Path) -> Any:
    return json.loads(resolve_path(path).read_text(encoding="utf-8"))

def decimal_text(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise TypeError("수치를 문자열로 바꾸지 못했습니다.")
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError("유한하지 않은 수치입니다.")
    return format(number, "f")

def video_seconds(pts: int, numerator: int, denominator: int) -> str:
    if numerator <= 0 or denominator <= 0:
        raise ValueError("time_base가 유효하지 않습니다.")
    value = (Decimal(pts) * Decimal(numerator) / Decimal(denominator)).quantize(SECOND)
    return format(value, "f")

def load_png_info():
    path = resolve_experiment_path(ROOT / "tests" / "T4" / "extract.py")
    spec = importlib.util.spec_from_file_location("t4_extract_png", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("T4 PNG 검사를 불러오지 못했습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.png_info

sha = sha256_file
read = read_json
