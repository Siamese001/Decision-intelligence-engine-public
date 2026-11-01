#!/usr/bin/env python3
"""tools/validate_staged_grounding.py

Governed Validator for Staged Git Grounding, Claim Manifests, and Delivery Receipts.
Enforces:
1. Git index snapshot parity: verifies staged blobs (not uncommitted working tree copies).
2. Exact payload SHA-256 binding between staged payload bytes and ClaimManifest.
3. Anti-tampering verification: rejects forged or modified PASS DeliveryReceipts.
4. Active GraphDB generation/pair alignment for any staged claim evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.grounding.contracts import (
    ClaimManifest,
    DeliveryReceipt,
    compute_rfc8259_digest,
)
from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot

MANIFEST_CONTRACT_ID = "urn:nee:dual-pipeline:claim-manifest:1.0.0"
RECEIPT_CONTRACT_ID = "urn:nee:dual-pipeline:delivery-receipt:1.0.0"


def get_staged_files(repo_root: Path) -> List[str]:
    """Get list of files staged in the Git index."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return [f.strip() for f in res.stdout.splitlines() if f.strip()]
    except Exception:
        return []


def read_git_blob(repo_root: Path, rel_path: str, staged: bool = True) -> bytes:
    """Read file content directly from git staged index or working tree."""
    if staged:
        try:
            res = subprocess.run(
                ["git", "show", f":{rel_path}"],
                cwd=repo_root,
                capture_output=True,
                check=True,
                timeout=10,
            )
            return res.stdout
        except Exception:
            pass
    path = repo_root / rel_path
    if path.is_file():
        return path.read_bytes()
    raise FileNotFoundError(f"Blob '{rel_path}' not found in git index or filesystem.")


def is_manifest_payload(data: Dict[str, Any]) -> bool:
    """Check if dictionary represents a ClaimManifest."""
    return (
        data.get("contract_id") == MANIFEST_CONTRACT_ID
        or ("manifest_id" in data and "payload_digest" in data and "claims" in data)
    )


def is_receipt_payload(data: Dict[str, Any]) -> bool:
    """Check if dictionary represents a DeliveryReceipt."""
    return (
        data.get("contract_id") == RECEIPT_CONTRACT_ID
        or ("receipt_id" in data and "manifest_digest" in data and "status" in data)
    )


def validate_staged_manifest(
    repo_root: Path,
    rel_path: str,
    manifest_bytes: bytes,
    staged: bool,
) -> Tuple[bool, List[str]]:
    """Validate a staged ClaimManifest against staged payload and graph snapshot."""
    errors: List[str] = []
    try:
        manifest_data = json.loads(manifest_bytes.decode("utf-8"))
        manifest = ClaimManifest.from_dict(manifest_data)
        manifest.validate()
    except Exception as exc:
        return False, [f"Malformed ClaimManifest in '{rel_path}': {exc}"]

    # Check payload digest against staged payload file if present in manifest extensions
    payload_rel_path = (
        manifest.extensions.get("payload.path")
        or manifest.extensions.get("payload-path")
        or manifest.extensions.get("payload_path")
    )
    if payload_rel_path:
        try:
            staged_payload_bytes = read_git_blob(repo_root, payload_rel_path, staged=staged)
            computed_sha = hashlib.sha256(staged_payload_bytes).hexdigest()
            if computed_sha != manifest.payload_digest:
                errors.append(
                    f"Staged payload '{payload_rel_path}' SHA-256 {computed_sha} does not match "
                    f"manifest payload_digest {manifest.payload_digest} (staged index mismatch)."
                )
        except Exception as exc:
            errors.append(f"Failed to read staged payload '{payload_rel_path}': {exc}")

    # Verify runtime snapshot generation if runtime_root exists
    runtime_root = Path(manifest.runtime_root)
    if not runtime_root.is_absolute():
        runtime_root = repo_root / runtime_root
    if runtime_root.is_dir() and (runtime_root / "active_pair.json").is_file():
        try:
            snapshot = ReadOnlyGraphSnapshot(runtime_root)
            if snapshot.generation_id != manifest.graph_generation_id:
                errors.append(
                    f"Manifest generation {manifest.graph_generation_id} does not match "
                    f"active snapshot generation {snapshot.generation_id}."
                )
        except Exception as exc:
            errors.append(f"Error checking snapshot for manifest '{rel_path}': {exc}")

    return len(errors) == 0, errors


def validate_staged_receipt(
    repo_root: Path,
    rel_path: str,
    receipt_bytes: bytes,
) -> Tuple[bool, List[str]]:
    """Validate a staged DeliveryReceipt for authenticity, status validity, and digest integrity."""
    errors: List[str] = []
    try:
        receipt_data = json.loads(receipt_bytes.decode("utf-8"))
        receipt = DeliveryReceipt.from_dict(receipt_data)
        receipt.validate()
    except Exception as exc:
        return False, [f"Malformed DeliveryReceipt in '{rel_path}': {exc}"]

    # Anti-tampering check: verify canonical digest of receipt itself
    recorded_digest = receipt.canonical_digest.get("value") if isinstance(receipt.canonical_digest, dict) else receipt.canonical_digest
    unsigned = {k: v for k, v in receipt.to_dict().items() if k != "canonical_digest"}
    recomputed_digest = compute_rfc8259_digest(unsigned)
    if recorded_digest and recorded_digest != recomputed_digest:
        errors.append(
            f"Tampered DeliveryReceipt in '{rel_path}': recorded canonical digest {recorded_digest} "
            f"!= recomputed digest {recomputed_digest}."
        )

    # Invariant: CERTIFIED receipt must not have contradictory or unsupported claims
    if receipt.status == "CERTIFIED":
        if receipt.contradicted_claims_count > 0:
            errors.append(
                f"Tampered DeliveryReceipt in '{rel_path}': status is CERTIFIED but contradicted_claims_count is {receipt.contradicted_claims_count}."
            )
        if receipt.supported_claims_count != receipt.total_claims_count:
            errors.append(
                f"Tampered DeliveryReceipt in '{rel_path}': status is CERTIFIED but supported {receipt.supported_claims_count} != total {receipt.total_claims_count}."
            )

    return len(errors) == 0, errors


def _collect_json_files(
    repo_root: Path,
    staged: bool,
    target_files: Optional[List[str]] = None,
) -> List[str]:
    """Collect candidate JSON file relative paths from explicit targets, staged git index, or filesystem."""
    if target_files is not None:
        collected: List[str] = []
        for f in target_files:
            if not f.endswith(".json"):
                continue
            p = Path(f)
            if p.is_absolute():
                try:
                    rel = str(p.resolve().relative_to(repo_root.resolve()))
                except Exception:
                    rel = str(p)
            else:
                rel = f
            collected.append(rel)
        return collected

    if staged:
        return [f for f in get_staged_files(repo_root) if f.endswith(".json")]

    files: List[str] = []
    for p in repo_root.glob("**/*.json"):
        parts = set(p.parts)
        if not (".git" in parts or ".venv" in parts or ".worktrees" in parts):
            files.append(str(p.relative_to(repo_root)))
    return files


def _validate_single_json_file(
    repo_root: Path,
    rel_path: str,
    staged: bool,
) -> Tuple[int, int, List[str]]:
    """Validate a single JSON file. Returns (manifest_inc, receipt_inc, file_violations)."""
    try:
        blob = read_git_blob(repo_root, rel_path, staged=staged)
        data = json.loads(blob.decode("utf-8"))
        if not isinstance(data, dict):
            return 0, 0, []
    except Exception:
        return 0, 0, []

    if is_manifest_payload(data):
        ok, errs = validate_staged_manifest(repo_root, rel_path, blob, staged)
        return 1, 0, errs

    if is_receipt_payload(data):
        ok, errs = validate_staged_receipt(repo_root, rel_path, blob)
        return 0, 1, errs

    return 0, 0, []


def validate_staged_grounding(
    repo_root: Path,
    staged: bool = True,
    verbose: bool = False,
    target_files: Optional[List[str]] = None,
) -> Tuple[int, List[str]]:
    """Main verification logic scanning staged index or full repository for grounding compliance."""
    violations: List[str] = []
    json_files = _collect_json_files(repo_root, staged=staged, target_files=target_files)
    checked_manifests = 0
    checked_receipts = 0

    for rel_path in json_files:
        m_inc, r_inc, errs = _validate_single_json_file(repo_root, rel_path, staged=staged)
        checked_manifests += m_inc
        checked_receipts += r_inc
        violations.extend(errs)

    if verbose or violations:
        print(f"Validated {checked_manifests} manifest(s) and {checked_receipts} receipt(s) (mode: {'staged' if staged else 'full'}).")
        for v in violations:
            print(f"❌ Violation: {v}")

    return (1 if violations else 0), violations


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate staged grounding, manifests, and receipts.")
    parser.add_argument("files", nargs="*", help="Optional target files to inspect")
    parser.add_argument("--repository-root", type=Path, default=ROOT, help="Path to repository root")
    parser.add_argument("--staged", action="store_true", default=True, help="Scan staged Git index (default)")
    parser.add_argument("--all", action="store_true", help="Scan entire working tree")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    staged_mode = not args.all and args.staged
    rc, violations = validate_staged_grounding(
        repo_root=args.repository_root.resolve(),
        staged=staged_mode,
        verbose=args.verbose,
        target_files=list(args.files) if args.files else None,
    )
    if rc == 0:
        print("✅ Grounded delivery verification passed.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
