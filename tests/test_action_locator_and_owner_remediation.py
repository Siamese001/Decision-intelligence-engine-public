"""Wave 4 end-to-end sandbox remediation verification for Output 02/03 action owners and locators."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from tools.remediate_action_ledger_locators_and_owners import remediate_meeting_directory

REPO_ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = REPO_ROOT / "output/partner_alliances/10-27-2026/Weekly_PartnerAdvisory_Gcp_Alignment"
MONTHLY_DIR = REPO_ROOT / "output/partner_alliances/10-27-2026/Google_Nee_PartnerAdvisory_Monthly"


def _seed_sandbox_db(db_path: Path, actions_by_meeting: list[tuple[str, list[dict]]]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT
            );
            CREATE TABLE claim (
                claim_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                claim_text TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                claim_id TEXT,
                assertion_type TEXT,
                subject_node_id TEXT
            );
            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                assertion_id TEXT,
                locator_value TEXT,
                transcript_timestamp TEXT,
                exact_quote TEXT
            );
            INSERT INTO node (node_id, canonical_name) VALUES
                ('person_amit', 'Amit Ayer'),
                ('person_dan_okray', 'Drew Oakley'),
                ('person_robson_silva', 'Robson Silva'),
                ('person_alex_brannan', 'Alex Brannan'),
                ('person_stefanos_georgiades', 'Stefanos Georgiades'),
                ('person_neha_dixit', 'Neha Dixit'),
                ('person_rusty_gattmann', 'Rusty Gattmann');
            """
        )
        for mtg_prefix, actions in actions_by_meeting:
            fallback_ts = "00:00:32" if "weekly" in mtg_prefix else "00:00:00"
            for idx, act in enumerate(actions, 1):
                is_hr_row = "HR-work activities, outcomes, and technical details" in act["action"]
                aid = (
                    "assertion-e9e0258999df751ccc7eb9cda252"
                    if is_hr_row
                    else f"assertion-{mtg_prefix}-{idx:02d}"
                )
                cid = f"claim-{mtg_prefix}-{idx:02d}"
                evid = f"ev-{mtg_prefix}-{idx:02d}"
                orig_owner = act.get("reported_owner") or "Drew Oakley"
                orig_subj = "person_dan_okray" if is_hr_row else "person_unassigned"
                claim_txt = f"{orig_owner} to {act['action'][0].lower() + act['action'][1:]}"
                conn.execute(
                    "INSERT INTO claim (claim_id, subject_node_id, claim_text) VALUES (?, ?, ?)",
                    (cid, orig_subj, claim_txt),
                )
                conn.execute(
                    "INSERT INTO assertion (assertion_id, claim_id, assertion_type, subject_node_id) VALUES (?, ?, 'action_item', ?)",
                    (aid, cid, orig_subj),
                )
                conn.execute(
                    "INSERT INTO evidence_item (evidence_id, assertion_id, locator_value, transcript_timestamp, exact_quote) VALUES (?, ?, ?, ?, ?)",
                    (evid, aid, fallback_ts, fallback_ts, act["action"]),
                )
        conn.commit()
    finally:
        conn.close()


def test_end_to_end_remediation_of_10_27_2026_meetings_and_sandbox_db(tmp_path: Path) -> None:
    """Verify all 14 actions across both 10-27-2026 meetings resolve to exact timestamps and owners in an isolated sandbox."""
    weekly_sbx = tmp_path / "Weekly_PartnerAdvisory_Gcp_Alignment"
    monthly_sbx = tmp_path / "Google_Nee_PartnerAdvisory_Monthly"
    weekly_sbx.mkdir(parents=True, exist_ok=True)
    monthly_sbx.mkdir(parents=True, exist_ok=True)

    for src_dir, dst_dir in ((WEEKLY_DIR, weekly_sbx), (MONTHLY_DIR, monthly_sbx)):
        for f in src_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, dst_dir / f.name)

    weekly_orig_pkg = json.loads((weekly_sbx / "intake_package.json").read_text(encoding="utf-8"))
    monthly_orig_pkg = json.loads((monthly_sbx / "intake_package.json").read_text(encoding="utf-8"))

    sandbox_db = tmp_path / "sandbox_graph.sqlite"
    _seed_sandbox_db(
        sandbox_db,
        [
            ("weekly", weekly_orig_pkg["actions"]),
            ("monthly", monthly_orig_pkg["actions"]),
        ],
    )

    res_weekly = remediate_meeting_directory(weekly_sbx, sandbox_db=sandbox_db, dry_run=False)
    res_monthly = remediate_meeting_directory(monthly_sbx, sandbox_db=sandbox_db, dry_run=False)

    assert res_weekly["reconciled_action_count"] == 7
    assert res_monthly["reconciled_action_count"] == 7

    # Independent read-only verification of sandbox SQLite database
    ro_conn = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
    try:
        integrity = ro_conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"

        hr_row = ro_conn.execute(
            """
            SELECT a.subject_node_id, c.claim_text, e.locator_value, e.transcript_timestamp, e.exact_quote
            FROM assertion a
            JOIN claim c ON a.claim_id = c.claim_id
            JOIN evidence_item e ON a.assertion_id = e.assertion_id
            WHERE a.assertion_id = 'assertion-e9e0258999df751ccc7eb9cda252'
            """
        ).fetchone()
        assert hr_row is not None
        subj_id, claim_text, loc_val, ts_val, exact_quote = hr_row
        assert subj_id == "person_amit"
        assert claim_text.startswith("Amit Ayer to share PartnerAdvisory")
        assert loc_val == "00:13:17"
        assert ts_val == "00:13:17"
        assert "I just sent it in the chat" in exact_quote

        all_ev = ro_conn.execute(
            "SELECT locator_value, transcript_timestamp FROM evidence_item"
        ).fetchall()
        assert len(all_ev) == 14
        assert all(loc not in ("00:00:00", "00:00:32") for loc, _ in all_ev)
        assert all(ts not in ("00:00:00", "00:00:32") for _, ts in all_ev)
    finally:
        ro_conn.close()
