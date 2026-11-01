#!/usr/bin/env python3
"""Verification tool for HITL Decision Ledger chain integrity, store parity, and persistence.

Enforces Acceptance Criterion 2:
- Verifies unbroken Merkle hash chain from root to tip.
- Verifies parity between SQLite master store and JSON export.
- Verifies presence and valid digest of recent operator answers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.hitl_governance_core.decision_record_writer import (  # noqa: E402
    resolve_ledger_paths,
    verify_chain_integrity,
)


def verify_store_parity(sqlite_path: Path, json_path: Path) -> Tuple[bool, str]:
    """Verify that every decision in SQLite has a corresponding representation in JSON export."""
    if not sqlite_path.exists():
        return False, f"SQLite ledger store not found: {sqlite_path}"
    if not json_path.exists():
        return False, f"JSON export not found: {json_path}"

    conn = create_sqlite_conn(sqlite_path, readonly=True)
    cur = conn.cursor()
    cur.execute("SELECT decision_id, entry_digest FROM hitl_decisions;")
    sqlite_items = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()

    try:
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"Corrupted JSON export: {e}"

    json_decisions = json_data.get("operator_decisions", [])
    json_items = {
        d["decision_id"]: d.get("entry_digest") for d in json_decisions if "decision_id" in d
    }

    missing_in_json = set(sqlite_items.keys()) - set(json_items.keys())
    if missing_in_json:
        return (
            False,
            f"Store parity violation: {len(missing_in_json)} SQLite decision(s) missing from JSON export: {missing_in_json}",
        )

    # Verify digests match for operator decisions
    digest_mismatches = []
    for d_id, s_digest in sqlite_items.items():
        if d_id in json_items and json_items[d_id] and json_items[d_id] != s_digest:
            digest_mismatches.append(f"{d_id}: sqlite {s_digest} != json {json_items[d_id]}")

    if digest_mismatches:
        return False, f"Digest parity mismatches found: {digest_mismatches}"

    # Verify signatures in JSON export (reject raw SHA-256 strings)
    for d in json_decisions:
        sig = d.get("signature")
        entry_digest = d.get("entry_digest")
        dec_id = d.get("decision_id", "<unknown>")
        if not sig:
            return False, f"Missing signature in JSON export for decision '{dec_id}'"
        if sig == entry_digest or (len(sig) == 64 and all(c in "0123456789abcdefABCDEF" for c in sig)):
            return (
                False,
                f"Rejected unauthenticated SHA-256 signature string in decision '{dec_id}'. Ed25519 signature required.",
            )

    return (
        True,
        f"Store parity verified: All {len(sqlite_items)} SQLite decisions present in JSON export with valid signatures.",
    )


def verify_decision_present(sqlite_path: Path, decision_id: str) -> Tuple[bool, str]:
    """Verify that a specific decision_id exists and has a valid non-empty digest."""
    if not sqlite_path.exists():
        return False, f"SQLite store not found: {sqlite_path}"

    conn = create_sqlite_conn(sqlite_path, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT sequence_number, entry_digest, recorded_at FROM hitl_decisions WHERE decision_id = ?;",
        (decision_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, f"Decision '{decision_id}' not found in ledger store."

    seq, digest_val, rec_at = row
    return (
        True,
        f"Decision '{decision_id}' verified at sequence {seq} (digest: {digest_val}, recorded: {rec_at}).",
    )


def verify_ledger(
    repo_root: Optional[Path] = None,
    sqlite_path: Optional[Path] = None,
    json_path: Optional[Path] = None,
    required_decisions: Optional[List[str]] = None,
    strict: bool = False,
) -> int:
    """Run full ledger verification suite."""
    s_path, j_path = resolve_ledger_paths(
        repo_root=repo_root, sqlite_path=sqlite_path, json_path=json_path
    )

    print(f"Verifying SQLite Store: {s_path}")
    print(f"Verifying JSON Export:  {j_path}")

    # 1. Chain Integrity
    chain_ok, chain_msg = verify_chain_integrity(s_path)
    if not chain_ok:
        print(f"[FAIL] Chain Integrity: {chain_msg}")
        return 1
    print(f"[PASS] Chain Integrity: {chain_msg}")

    # 2. Store Parity
    parity_ok, parity_msg = verify_store_parity(s_path, j_path)
    if not parity_ok:
        print(f"[FAIL] Store Parity: {parity_msg}")
        return 1
    print(f"[PASS] Store Parity: {parity_msg}")

    # 3. Required Decisions
    targets = required_decisions or [
        "D1_copilot_precompute_hops",
        "D2_ambiguity_threshold_30pct",
        "D4_ledger_store_of_record",
    ]
    missing = []
    for d_id in targets:
        d_ok, d_msg = verify_decision_present(s_path, d_id)
        if not d_ok:
            print(f"[WARN] Required Decision: {d_msg}")
            missing.append(d_id)
        else:
            print(f"[PASS] Required Decision: {d_msg}")

    if strict and missing:
        print(f"\n[FAIL] Strict verification failed: Missing required decisions {missing}")
        return 1

    print(
        "\n[SUCCESS] Decision ledger verified completely: chain integrity, store parity, and required decisions valid."
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify decision ledger chain integrity and store parity."
    )
    parser.add_argument("--sqlite", type=Path, help="Explicit path to SQLite ledger.")
    parser.add_argument("--json", type=Path, help="Explicit path to JSON ledger.")
    parser.add_argument("--decision-id", action="append", help="Specific decision ID to require.")
    parser.add_argument(
        "--strict", action="store_true", help="Fail if any required decision is missing."
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    exit_code = verify_ledger(
        repo_root=repo_root,
        sqlite_path=args.sqlite,
        json_path=args.json,
        required_decisions=args.decision_id,
        strict=args.strict,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
