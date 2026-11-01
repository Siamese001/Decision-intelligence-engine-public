"""Pre-flight verification for HITL resolution wave runner."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List, Optional, Tuple


def compute_file_digest(file_path: Path) -> str:
    """Computes SHA-256 digest of a single file."""
    if not file_path.is_file():
        return "0" * 64
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_tree_digest(root: Path, relative_paths: List[str]) -> str:
    """Computes deterministic combined SHA-256 digest across tracked target paths."""
    hasher = hashlib.sha256()
    for rel in sorted(relative_paths):
        p = root / rel
        if p.is_file():
            hasher.update(rel.encode("utf-8"))
            hasher.update(compute_file_digest(p).encode("utf-8"))
        elif p.is_dir():
            for sub in sorted(p.rglob("*")):
                if sub.is_file():
                    sub_rel = str(sub.relative_to(root))
                    hasher.update(sub_rel.encode("utf-8"))
                    hasher.update(compute_file_digest(sub).encode("utf-8"))
    return hasher.hexdigest()


def validate_environment(
    runtime_root: Path,
    receipt_root: Path,
    wave: int,
    repo_root: Path,
) -> Tuple[bool, str]:
    """Validates that runtime and receipt roots conform to isolation invariants."""
    resolved_runtime = runtime_root.resolve()
    resolved_receipt = receipt_root.resolve()
    resolved_repo = repo_root.resolve()

    # Runtime root cannot be repo root or production output/
    if resolved_runtime == resolved_repo:
        return False, "Runtime root cannot be the primary repository root."
    if (resolved_repo / "output") in resolved_runtime.parents or resolved_runtime == (resolved_repo / "output"):
        return False, "Runtime root cannot reside within production output/ directory."

    # Receipt root must exist or be creatable and must not conflict with an existing completed receipt
    receipt_target = resolved_receipt / f"wave_{wave}_receipt.json"
    if receipt_target.exists():
        return False, f"Receipt file already exists at {receipt_target}; immutable paths cannot be overwritten."

    return True, "Environment validation passed."


def validate_parent_receipt(
    receipt_root: Path,
    parent_wave: Optional[int],
) -> Tuple[bool, Optional[str], str]:
    """Validates that the predecessor wave completed with an authentic COMPLETE receipt."""
    if parent_wave is None:
        return True, None, "Wave 1 requires no parent receipt."

    parent_receipt_path = receipt_root / f"wave_{parent_wave}_receipt.json"
    if not parent_receipt_path.is_file():
        return False, None, f"Parent receipt missing for Wave {parent_wave} at {parent_receipt_path}"

    try:
        data = json.loads(parent_receipt_path.read_text(encoding="utf-8"))
        if data.get("terminal_result") != "COMPLETE":
            return False, None, f"Parent wave {parent_wave} terminal_result is {data.get('terminal_result')}, expected COMPLETE"
        p_digest = compute_file_digest(parent_receipt_path)
        return True, p_digest, f"Parent wave {parent_wave} verified with digest {p_digest[:16]}..."
    except Exception as e:
        return False, None, f"Failed parsing parent receipt {parent_receipt_path}: {e}"
