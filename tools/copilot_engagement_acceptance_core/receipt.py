"""
tools/copilot_engagement_acceptance_core/receipt.py

Receipt construction, RFC-canonical hashing, predecessor verification,
and wave ledger synchronization.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def git_info(root: Path, *args: str) -> str:
    """Executes a git command in root and returns the stripped stdout."""
    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        text=True,
        timeout=60,
    ).strip()


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_digest(payload: Dict[str, Any]) -> str:
    """Computes deterministic canonical JSON SHA-256 digest."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seal_receipt(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Seals receipt with deterministic canonical_digest."""
    copy_payload = dict(payload)
    copy_payload.pop("canonical_digest", None)
    digest_val = canonical_json_digest(copy_payload)
    copy_payload["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "receipt_without_canonical_digest",
        "value": digest_val,
    }
    return copy_payload


def verify_predecessor_receipt(
    root: Path, predecessor_path: Path, expected_wave: int
) -> Dict[str, Any]:
    """Verifies that the predecessor wave receipt exists and passed."""
    if not predecessor_path.is_file():
        raise RuntimeError(f"Missing required parent receipt: {predecessor_path}")
    data = json.loads(predecessor_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Corrupt predecessor receipt: {predecessor_path}")
    if data.get("wave") != expected_wave:
        raise RuntimeError(
            f"Predecessor wave mismatch: expected {expected_wave}, got {data.get('wave')}"
        )
    if data.get("terminal_outcome") != "PASS":
        raise RuntimeError(
            f"Predecessor receipt did not pass: {data.get('terminal_outcome')}"
        )
    rel_path = (
        predecessor_path.relative_to(root).as_posix()
        if predecessor_path.is_relative_to(root)
        else str(predecessor_path)
    )
    return {
        "path": rel_path,
        "sha256": compute_file_sha256(predecessor_path),
        "wave": expected_wave,
    }


def collect_deliverables(root: Path, deliverables: List[str]) -> List[Dict[str, str]]:
    """Checks and records SHA-256 for all declared deliverables."""
    rows: List[Dict[str, str]] = []
    for rel_path in deliverables:
        full_path = root / rel_path
        if not full_path.is_file():
            raise RuntimeError(f"Missing mandatory deliverable: {rel_path}")
        rows.append({"path": rel_path, "sha256": compute_file_sha256(full_path)})
    return rows


def collect_fixtures(root: Path, fixture_paths: List[str]) -> Dict[str, str]:
    """Computes digests for all frozen fixture paths."""
    fixtures_map: Dict[str, str] = {}
    for rel_path in fixture_paths:
        full_path = root / rel_path
        if not full_path.is_file():
            raise RuntimeError(f"Missing frozen fixture: {rel_path}")
        fixtures_map[rel_path] = compute_file_sha256(full_path)
    return fixtures_map


def update_wave_ledger(
    ledger_path: Path, wave: int, receipt_rel_path: str, outcome: str
) -> None:
    """Updates wave ledger status to COMPLETE and links receipt."""
    if not ledger_path.is_file():
        return
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    waves = data.get("waves", [])
    updated = False
    for entry in waves:
        if entry.get("wave") == wave:
            entry["status"] = "COMPLETE" if outcome == "PASS" else "BLOCKED"
            entry["runtime_receipt"] = receipt_rel_path
            entry["validation_result"] = outcome
            entry["next_authorization"] = (
                f"Wave {wave + 1} authorization" if outcome == "PASS" else "Remediation"
            )
            updated = True
            break
    if updated:
        ledger_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
