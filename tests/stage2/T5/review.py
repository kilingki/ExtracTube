"""Validate the existing reviewed original-frame handoff without inference or rebuilding."""

import sys as _path_sys
from pathlib import Path as _WorkspacePath
_path_sys.path.insert(0, str(next(parent for parent in _WorkspacePath(__file__).resolve().parents if (parent / "experiment_paths.py").is_file())))
from experiment_paths import ROOT as EXPERIMENT_ROOT, resolve_path as resolve_experiment_path

from collections import Counter
from decimal import Decimal
from pathlib import Path
from PIL import Image
import common as s
import inputs
from common import HashCache

HERE = Path(__file__).resolve().parent
ROOT = EXPERIMENT_ROOT
REVIEW = HERE / "visual_review.json"
MATERIAL = HERE / "runs/20261028T030129Z_7948a1_e15_task_modes/manifest.json"
MATERIAL_SHA = "ec071032abbbd74ac8ac73e4908ee71c54b33deed3cf75acce69ac8f48786b40"
POINTER = HERE / "visual_closeout.json"

def unit_id(f):
    return f["frame_id"].split(":")[0] + ":u" + str(f["text_ref"]["unit_index"])

def source_frames():
    errors, manifest = inputs.load_manifest(HashCache())
    if errors:
        raise ValueError(errors)
    if s.sha(MATERIAL) != MATERIAL_SHA:
        raise ValueError("supplemental source manifest changed")
    return manifest, manifest["frames"] + s.read(MATERIAL)["frames"][:4]

def checked_frames():
    manifest, frames = source_frames()
    review = s.read(REVIEW)
    decisions = {d["frame_id"]: d for d in review["frames"]}
    if len(decisions) != len(frames) or set(decisions) != {f["frame_id"] for f in frames}:
        raise ValueError("review must cover exactly the fixed 29 frames")
    for f in frames:
        d = decisions[f["frame_id"]]
        if d["decision"] not in {"keep", "reject"} or not d["reason"]:
            raise ValueError("unreviewed frame")
        if d["sha256"] != f["full"]["sha256"] or s.sha(resolve_experiment_path(ROOT / f["full"]["path"])) != d["sha256"]:
            raise ValueError("review is not bound to current source bytes")
        with Image.open(resolve_experiment_path(ROOT / f["full"]["path"])) as im:
            if im.format != "PNG" or im.size != (1920, 1080):
                raise ValueError("unexpected source image")
            im.verify()
    return manifest, frames, decisions

def validate(doc, folder):
    errors = []
    try:
        _, frames, decisions = checked_frames()
        by_frame = {f["frame_id"]: f for f in frames}
        unit_rows = doc["semantic_units"]
        units = {u["unit_id"]: u for u in unit_rows}
        assert len(units) == len(unit_rows) == 270, "unit inventory"
        assessed = {unit_id(f) for f in frames}
        assert len(assessed) == 16, "review scope"
        assert doc["stage3_scope"] == "reviewed_subset_only", "scope"
        assert doc["publication_policy"]["unverified_ocr_as_facts"] is False, "OCR policy"
        selected = {k for k, d in decisions.items() if d["decision"] == "keep"}
        assets = doc["assets"]
        assert {a["frame_id"] for a in assets} == selected, "selected/rejected set"
        assert len(assets) == len(selected), "duplicate assets"
        for a in assets:
            f = by_frame[a["frame_id"]]
            assert a["unit_id"] == unit_id(f) and a["unit_id"] in units, "unit link"
            assert a["candidate_id"] == f["candidate_id"], "candidate"
            assert a["source"] == {k: f[k] for k in ("full","pts","time_base","video_seconds","stream_index","media","text_ref","sentences")}, "source provenance"
            seconds = Decimal(f["pts"]) * Decimal(f["time_base"]["numerator"]) / Decimal(f["time_base"]["denominator"])
            assert abs(seconds - Decimal(f["video_seconds"])) < Decimal("0.000001"), "PTS"
            u = units[a["unit_id"]]["context"]
            assert Decimal(str(u["start_time"])) <= seconds <= Decimal(str(u["end_time"])), "unit time"
            assert a["verified_text"] == [] and a["latex"] is None and a["table_html"] is None, "unverified structured content"
            assert a["caption"] == decisions[a["frame_id"]]["caption"], "reviewed caption"
            assert a["kind"] == "original_frame" and a["transform"] == "none", "original only"
            path = (folder / a["path"]).resolve()
            assert path.is_relative_to(folder.resolve()) and s.sha(path) == f["full"]["sha256"], "asset bytes"
        asset_ids = {a["asset_id"] for a in assets}
        for uid, u in units.items():
            source = u["t1_source"]
            assert s.sha(resolve_experiment_path(ROOT / source["canonical_path"])) == source["sha256"], "T1 hash"
            original = s.read(resolve_experiment_path(ROOT / source["canonical_path"]))
            assert original["video_id"] + ":u" + str(u["context"]["index"]) == uid, "unit identity"
            assert next(x for x in original["units"] if x["index"] == u["context"]["index"]) == u["context"], "unit context"
            ids = [a["asset_id"] for a in assets if a["unit_id"] == uid]
            assert u["asset_ids"] == ids and set(ids) <= asset_ids, "asset references"
            expected = "selected" if ids else ("reviewed_omitted" if uid in assessed else "not_assessed")
            assert u["visual_status"] == expected, "coverage must distinguish unassessed"
        expected_counts = dict(Counter(u["visual_status"] for u in units.values()))
        assert doc["counts"] == {**expected_counts, "semantic_units":270, "reviewed_units":16, "reviewed_frames":29, "selected_assets":len(assets)}, "counts"
    except (AssertionError, KeyError, ValueError, OSError, StopIteration) as e:
        errors.append(str(e) or type(e).__name__)
    return errors

def check_current():
    pointer = s.read(POINTER)
    folder = resolve_experiment_path(ROOT / pointer["run"])
    handoff = resolve_experiment_path(ROOT / pointer["handoff"])
    if s.sha(handoff) != pointer["handoff_sha256"]:
        raise ValueError("published handoff changed")
    errors = validate(s.read(handoff), folder)
    if errors:
        raise ValueError(errors)
    return pointer["counts"]

