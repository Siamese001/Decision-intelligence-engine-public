"""Ledger signature and Merkle chain verifier for governed HITL decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage.connection import create_sqlite_conn, sqlite3
from dual_pipeline.hitl_governance_core.crypto import verify_hitl_approval_signature
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    resolve_ledger_paths,
    verify_chain_integrity,
)


def _default_ledger_sqlite() -> Path:
    sqlite_p, _ = resolve_ledger_paths()
    return sqlite_p


def verify_ledger_signatures(
    ledger_path: Optional[Path] = None,
) -> Tuple[bool, List[str]]:
    """Verify Ed25519 digital signatures on all signed records in the HITL ledger."""
    target_sqlite = ledger_path or _default_ledger_sqlite()
    if not target_sqlite.exists():
        return False, [f"Ledger file not found: {target_sqlite}"]

    errors: List[str] = []
    try:
        conn = create_sqlite_conn(target_sqlite, readonly=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT entry_id, decision_id, metadata
            FROM hitl_decisions
            ORDER BY sequence_number ASC
            """
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        return False, [f"Database read failed: {exc}"]

    for r in rows:
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except Exception:
            continue

        alr = meta.get("_alr_envelope")
        if isinstance(alr, dict) and "payload" in alr and "signature" in alr:
            payload = alr["payload"]
            sig = alr["signature"]
            pub_key = alr.get("public_key_pem")
            if not pub_key:
                errors.append(f"Entry {r['entry_id']}: missing public_key_pem in ALR envelope.")
                continue
            is_valid = verify_hitl_approval_signature(payload, sig, pub_key)
            if not is_valid:
                errors.append(f"Entry {r['entry_id']}: invalid Ed25519 signature.")

    return len(errors) == 0, errors


def verify_ledger_chain(
    sqlite_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Verify unbroken Merkle hash chain across hitl_decisions."""
    target = sqlite_path or _default_ledger_sqlite()
    return verify_chain_integrity(target)


def verify_active_pair_rollback(
    active_pair_path: Path,
    expected_baseline_sha256: str,
) -> Tuple[bool, str]:
    """Verify that active_pair.json matches expected baseline SHA-256 post rollback."""
    if not active_pair_path.exists():
        return False, f"active_pair.json not found at {active_pair_path}"

    raw_bytes = active_pair_path.read_bytes()
    computed_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_sha != expected_baseline_sha256:
        return False, (
            f"Rollback mismatch: expected baseline {expected_baseline_sha256[:12]}..., "
            f"got {computed_sha[:12]}..."
        )
    return True, f"Verified baseline SHA-256 digest on disk: {computed_sha}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify HITL decision ledger signatures & chain.")
    parser.add_argument("--ledger", type=Path, default=None, help="Path to hitl_decision_ledger.sqlite")
    parser.add_argument("--active-pair", type=Path, default=None, help="Path to active_pair.json")
    parser.add_argument("--expected-baseline-sha", type=str, default=None, help="Expected baseline SHA-256")
    args = parser.parse_args()

    chain_ok, chain_msg = verify_ledger_chain(args.ledger)
    print(f"Merkle Chain Integrity: {'PASS' if chain_ok else 'FAIL'}")
    if not chain_ok:
        print(f"  Error: {chain_msg}")

    sig_ok, sig_errors = verify_ledger_signatures(args.ledger)
    print(f"Ed25519 Signatures: {'PASS' if sig_ok else 'FAIL'}")
    for err in sig_errors:
        print(f"  Error: {err}")

    if args.active_pair and args.expected_baseline_sha:
        pair_ok, pair_msg = verify_active_pair_rollback(args.active_pair, args.expected_baseline_sha)
        print(f"Active Pair Rollback Verification: {'PASS' if pair_ok else 'FAIL'}")
        print(f"  {pair_msg}")
        if not pair_ok:
            return 1

    return 0 if (chain_ok and sig_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
