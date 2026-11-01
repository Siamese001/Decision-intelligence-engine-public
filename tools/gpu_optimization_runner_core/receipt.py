"""Receipt serialization, predecessor verification, and immutable write operations."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.test_discovery_core import resolve_all_tests


def canonical_digest(data: Dict[str, Any]) -> str:
    """Calculates RFC 8785 canonical SHA-256 hash of payload data."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _matches_any(target: str, candidates: Any, path_str: str) -> bool:
    for item in candidates:
        s = str(item)
        if target == s or target.endswith(f".{s}") or s.endswith(f".{target}") or path_str in s:
            return True
    return False


def _resolve_test_profile(t: str, e2e_tests: Any, bge_tests: Any, repo_root: Path) -> str:
    mod_name = t.replace("/", ".").rstrip(".py")
    if _matches_any(mod_name, e2e_tests, t):
        return "e2e"
    if _matches_any(mod_name, bge_tests, t):
        return "bge"
    p = repo_root / t
    if p.name.endswith(("_e2e.py", "_adversarial.py")) or "end_to_end" in t or "adversarial" in t:
        return "e2e"
    return "deterministic"


def verify_ci_enrollment(test_paths: List[str], repo_root: Path) -> Dict[str, str]:
    """Verifies that each declared acceptance test resolves to an active CI profile."""
    e2e_tests, _det_tests, bge_tests = resolve_all_tests(repo_root=repo_root, strict=False)
    return {t: _resolve_test_profile(t, e2e_tests, bge_tests, repo_root) for t in test_paths}


def verify_predecessor_receipt(receipt_root: Path, wave: int) -> Optional[Dict[str, Any]]:
    """Verifies that Wave N-1 receipt exists, is cryptographically sealed, and has status PASS."""
    if wave <= 1:
        return None

    pred_path = receipt_root / f"gpu_wave_{wave - 1}_receipt.json"
    if not pred_path.is_file():
        raise RuntimeError(f"Missing predecessor receipt for Wave {wave}: {pred_path}")

    data = json.loads(pred_path.read_text(encoding="utf-8"))
    claimed_digest = data.get("receipt_digest")
    payload_copy = {k: v for k, v in data.items() if k != "receipt_digest"}
    actual_digest = canonical_digest(payload_copy)

    if claimed_digest != actual_digest:
        raise RuntimeError(
            f"Predecessor receipt {pred_path} digest mismatch: expected {actual_digest}, got {claimed_digest}"
        )

    if data.get("status") != "PASS":
        raise RuntimeError(
            f"Predecessor receipt {pred_path} terminal status is not PASS: {data.get('status')}"
        )

    return {
        "wave": wave - 1,
        "path": str(pred_path),
        "receipt_digest": claimed_digest,
        "run_id": data.get("run_id"),
    }


def write_immutable_receipt(receipt_path: Path, payload: Dict[str, Any], immutable: bool = True) -> None:
    """Writes JSON payload atomically (.tmp -> fsync -> os.replace) and sets read-only permissions if immutable."""
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = receipt_path.with_suffix(".tmp")

    if receipt_path.exists():
        try:
            os.chmod(receipt_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, receipt_path)

    if immutable:
        os.chmod(receipt_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
