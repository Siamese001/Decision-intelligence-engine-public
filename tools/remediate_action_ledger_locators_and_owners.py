#!/usr/bin/env python3
"""Governed remediation utility for Output 02/03 action locators and multi-turn handoff owners."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.transcript_qa_core.action_ledger_porter import (
    extract_reconciled_output02_actions,
    port_output02_actions_to_output03,
)
from tools.prepare_evidence_envelope_intake import prepare_package


def _resolve_owner_node_id(conn: Any, owner_name: str) -> str:
    clean = (owner_name or "").strip()
    if not clean:
        return "person_unassigned"
    low = clean.lower()
    if "amit" in low:
        row = conn.execute(
            "SELECT node_id FROM node WHERE node_id IN ('person_amit', 'person_amit_ayer') LIMIT 1"
        ).fetchone()
        if row:
            return str(row[0])
        return "person_amit"
    slug = "person_" + re.sub(r"[^a-z0-9]+", "_", low).strip("_")
    row = conn.execute(
        "SELECT node_id FROM node WHERE LOWER(canonical_name) = ? OR node_id = ? LIMIT 1",
        (low, slug),
    ).fetchone()
    return str(row[0]) if row else slug


def remediate_meeting_directory(
    meeting_dir: Path,
    *,
    sandbox_db: Path | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Reconcile Output 02 Action Ledger, port actions to Output 03 & intake_package.json, and optionally remediate a sandbox DB."""
    f1_cands = sorted(meeting_dir.glob("01_*_hitl_resolved_transcript.md"))
    f2_cands = sorted(meeting_dir.glob("02_*_executive_recap.md"))
    f3_cands = sorted(meeting_dir.glob("03_*_canonical_signal_projection.json"))
    if not (f1_cands and f2_cands and f3_cands):
        raise FileNotFoundError(f"Missing 01/02/03 artifacts in {meeting_dir}")

    f1_path, f2_path, f3_path = f1_cands[0], f2_cands[0], f3_cands[0]
    f1_text = f1_path.read_text(encoding="utf-8")
    f2_text = f2_path.read_text(encoding="utf-8")
    f3_dict = json.loads(f3_path.read_text(encoding="utf-8"))

    healed_f2, reconciled_actions = extract_reconciled_output02_actions(f2_text, f1_text=f1_text)
    ported_f3 = port_output02_actions_to_output03(
        healed_f2, f3_dict, f1_text=f1_text, preserve_action_updates=False
    )

    if not dry_run:
        if healed_f2 != f2_text:
            f2_path.write_text(healed_f2, encoding="utf-8")
        f3_path.write_text(json.dumps(ported_f3, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        pkg_path = meeting_dir / "intake_package.json"
        existing_loc = f"output/{meeting_dir.name}/{f1_path.name}"
        existing_cls = "restricted"
        existing_aud = ["internal_consulting", "nee-engineering"]
        if pkg_path.is_file():
            try:
                old_pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                mtg = old_pkg.get("meeting") or {}
                existing_loc = mtg.get("source_location") or existing_loc
                existing_cls = mtg.get("classification") or existing_cls
                existing_aud = mtg.get("audiences") or existing_aud
            except Exception:
                pass
        prepare_package(
            draft_path=f3_path,
            source_path=f1_path,
            output_path=pkg_path,
            source_location=existing_loc,
            classification=existing_cls,
            audiences=existing_aud,
            overwrite=True,
        )

    db_updates: List[Dict[str, Any]] = []
    if sandbox_db and sandbox_db.is_file() and not dry_run:
        with GovernedStorageContext.write_context(sandbox_db) as conn:
            for item in reconciled_actions:
                act_desc = item["action"]
                owner_name = item["reported_owner"]
                resolved_ts = item["resolved_timestamp"] or "00:00:00"
                ev_entries = item.get("evidence_entries") or []
                best_quote = ev_entries[0][1]["text"] if ev_entries else act_desc
                owner_node_id = _resolve_owner_node_id(conn, owner_name)

                rows = conn.execute(
                    """
                    SELECT a.assertion_id, a.claim_id, a.subject_node_id
                    FROM assertion a
                    JOIN claim c ON a.claim_id = c.claim_id
                    WHERE a.assertion_type = 'action_item'
                      AND c.claim_text LIKE ?
                    """,
                    (f"%{act_desc[:48]}%",),
                ).fetchall()
                for assertion_id, claim_id, old_subj in rows:
                    conn.execute(
                        "UPDATE assertion SET subject_node_id = ? WHERE assertion_id = ?",
                        (owner_node_id, assertion_id),
                    )
                    conn.execute(
                        "UPDATE claim SET subject_node_id = ?, claim_text = ? WHERE claim_id = ?",
                        (
                            owner_node_id,
                            f"{owner_name} to {act_desc[0].lower() + act_desc[1:]}",
                            claim_id,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE evidence_item
                        SET locator_value = ?, transcript_timestamp = ?, exact_quote = ?
                        WHERE assertion_id = ?
                        """,
                        (resolved_ts, resolved_ts, best_quote, assertion_id),
                    )
                    db_updates.append(
                        {
                            "assertion_id": assertion_id,
                            "old_subject_node_id": old_subj,
                            "new_subject_node_id": owner_node_id,
                            "resolved_timestamp": resolved_ts,
                        }
                    )

    return {
        "meeting_dir": str(meeting_dir),
        "reconciled_action_count": len(reconciled_actions),
        "actions": [
            {
                "action_id": a["action_id"],
                "reported_owner": a["reported_owner"],
                "resolved_timestamp": a["resolved_timestamp"],
                "evidence_ids": a["evidence_ids"],
            }
            for a in reconciled_actions
        ],
        "db_updates": db_updates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meeting-dir", type=Path, action="append", required=True)
    parser.add_argument("--sandbox-db", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = []
    for mdir in args.meeting_dir:
        results.append(
            remediate_meeting_directory(
                mdir.resolve(),
                sandbox_db=args.sandbox_db.resolve() if args.sandbox_db else None,
                dry_run=args.dry_run,
            )
        )
    print(json.dumps({"status": "PASS", "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
