"""Offline checks for T5 evidence. Product execution uses extractube stage2."""

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import common
import review

HERE = Path(__file__).resolve().parent
ROOT = EXPERIMENT_ROOT


def check_archive(history=False):
    index = common.read(HERE / "archive/legacy/index.json")
    archive = HERE / "archive/legacy/sources.tar.gz"
    if common.sha(archive) != index["archive_sha256"]:
        raise ValueError("retired source archive changed")
    with tarfile.open(archive, "r:gz") as bundle:
        for entry in index["entries"]:
            saved = entry["preserved"]
            if saved["kind"] == "existing_snapshot":
                actual = common.sha(resolve_experiment_path(ROOT / saved["path"]))
            else:
                actual = hashlib.sha256(bundle.extractfile(saved["member"]).read()).hexdigest()
            if actual != entry["sha256"]:
                raise ValueError("retired source changed: " + entry["original_path"])
    if history:
        for entry in index["protected_artifacts"]:
            if common.sha(resolve_experiment_path(ROOT / entry["path"])) != entry["sha256"]:
                raise ValueError("historical artifact changed: " + entry["path"])
    return {"retired_sources": len(index["entries"]), "historical_files_checked": len(index["protected_artifacts"]) if history else 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check reviewed handoff and archived sources; no inference")
    parser.add_argument("--check-history", action="store_true", help="also hash all protected historical artifacts")
    args = parser.parse_args()
    if not (args.check or args.check_history):
        parser.print_help()
        return 0
    result = {"handoff": review.check_current(), "archive": check_archive(args.check_history), "new_inference_calls": 0}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
