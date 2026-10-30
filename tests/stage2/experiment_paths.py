"""Canonical test locations; historical artifact path strings remain unchanged."""

from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]


def resolve_path(path):
    candidate = Path(os.path.normpath(Path(path).absolute()))
    try:
        relative = candidate.relative_to(ROOT).as_posix()
    except ValueError:
        return candidate
    aliases = {"tests/T1_sweep": "tests/stage2/T1/sweep", "tests/run_stage1.py": "tests/stage1/run_stage1.py", "tests/test_inputs.txt": "tests/stage1/test_inputs.txt", "tests/outputs": "tests/stage1/outputs"}
    aliases.update({f"local/t{i}_execution.md": f"local/stage2/t{i}_execution.md" for i in range(1, 6)})
    aliases.update({
        'local/t3_preflight_20261022': 'tests/stage2/T3/reviews/20261022_preflight',
        'local/t3_b01full_protected_hashes.txt': 'tests/stage2/T3/reviews/20261022_preflight/protected/t3_b01full_protected_hashes.txt',
        'local/t3_b02_protected_hashes.txt': 'tests/stage2/T3/reviews/20261022_preflight/protected/t3_b02_protected_hashes.txt',
        'local/t3_b03_protected_hashes.txt': 'tests/stage2/T3/reviews/20261022_preflight/protected/t3_b03_protected_hashes.txt',
        'local/t3_b03_protected_jpeg_rollups.txt': 'tests/stage2/T3/reviews/20261022_preflight/protected/t3_b03_protected_jpeg_rollups.txt',
        'local/t3_b04_protected_hashes.txt': 'tests/stage2/T3/reviews/20261022_preflight/protected/t3_b04_protected_hashes.txt',
        'local/t3_b05_protected_hashes.txt': 'tests/stage2/T3/reviews/20261022_preflight/protected/t3_b05_protected_hashes.txt',
        'local/20261028_formula_inputs': 'tests/stage2/reviews/20261028_formula_inputs',
    })
    aliases.update({"tests/" + name: "tests/stage2/" + name for name in ("T1", "T2", "T3", "T4", "T5")})
    for old, new in aliases.items():
        if relative == old or relative.startswith(old + "/"):
            return ROOT / (new + relative[len(old):])
    return candidate
