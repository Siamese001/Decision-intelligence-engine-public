"""End-to-End test suite verifying Tier 1 deterministic self-healing lifecycle.

Validates simultaneous Pushdown Automaton (PDA) JSON repair, SQLite write lock
backoff and stale lock eviction, and input roster provenance preservation during
live intake pipeline workflows.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    ORIGIN_INPUT_PROVENANCE,
    PreSynthesisEntityResolver,
)
from dual_pipeline.transcript_metadata_helper import (
    _populate_roster_and_grouped_attendees,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    _normalize_transcript_entities,
)
from dual_pipeline.validation.pydantic_gate import (
    DeterministicPDAParser,
    repair_json_tokens,
)
from storage.connection import (
    configure_pragmas,
    create_sqlite_conn,
    evict_stale_session_locks,
    execute_with_retry,
)


class TestTier1SelfHealingE2E:
    """E2E lifecycle testing for Tier 1 deterministic healing components."""

    @pytest.fixture
    def isolated_env(self, tmp_path: Path) -> Dict[str, Any]:
        """Provisions an isolated runtime environment with DB and lock directory."""
        db_path = tmp_path / "graph_store.sqlite"
        lock_dir = tmp_path / ".session_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)

        conn = create_sqlite_conn(db_path)
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS intake_projections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    roster_json TEXT NOT NULL,
                    projection_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
        conn.close()

        return {
            "tmp_path": tmp_path,
            "db_path": db_path,
            "lock_dir": lock_dir,
        }

    def test_tier1_full_intake_lifecycle_e2e(self, isolated_env: Dict[str, Any]) -> None:
        """E2E verification of simultaneous roster provenance, PDA repair, and DB lock recovery."""
        db_path = isolated_env["db_path"]
        lock_dir = isolated_env["lock_dir"]

        # ----------------------------------------------------------------------
        # Step 1: Ingestion metadata with explicit operator roster
        # ----------------------------------------------------------------------
        raw_metadata: Dict[str, Any] = {
            "meeting_date": "10-09-2026",
            "meeting_title": "Platform Strategy & Architecture Review",
            "attendees": ["Noemi Monnerville", "Michael Fielder", "Taylor Swift"],
            "roster": [
                {
                    "name": "Noemi Monnerville",
                    "company": "PartnerAdvisory",
                    "role": "Consulting Director",
                },
                {
                    "name": "Michael Fielder",
                    "company": "PartnerAdvisory",
                    "role": "Solutions Architect",
                },
            ],
        }

        # Populate structured roster
        _populate_roster_and_grouped_attendees(raw_metadata)
        assert len(raw_metadata["roster"]) == 3

        # Run U0 normalization
        raw_text = (
            "**Noemi Monnerville:** Welcome everyone to the review.\n"
            "**Michael Fielder:** Thanks Noemi, let's review the architecture."
        )
        clean_text = _normalize_transcript_entities(raw_text, raw_metadata)
        assert clean_text is not None

        roster_entries = {r["name"]: r for r in raw_metadata["roster"]}
        assert "Noemi Monnerville" in roster_entries
        assert "Michael Fielder" in roster_entries

        noemi_r = roster_entries["Noemi Monnerville"]
        fielder_r = roster_entries["Michael Fielder"]

        assert noemi_r["org"] == "PartnerAdvisory"
        assert noemi_r["provenance_origin"] == ORIGIN_INPUT_PROVENANCE
        assert fielder_r["org"] == "PartnerAdvisory"
        assert fielder_r["provenance_origin"] == ORIGIN_INPUT_PROVENANCE

        # ----------------------------------------------------------------------
        # Step 2: Corrupted Output 03 generation healed by PDA parser
        # ----------------------------------------------------------------------
        damaged_output_3 = (
            "Here is the generated graph projection from the model:\n"
            "```json\n"
            "{\n"
            '  "nodes": [\n'
            '    {"id": "sys:platform", "type": "system", "label": "Core Engine"},\n'
            '    {"id": "org:partner_advisory", "type": "organization", "label": "PartnerAdvisory"}\n'
            "  ],\n"
            '  "edges": [\n'
            '    {"source": "person:noemi", "target": "org:partner_advisory", "rel": "MEMBER_OF"},\n'
            "  ]\n"
        )  # Note: missing closing brace '}', trailing comma in edges, markdown preamble

        healed_raw, changes = DeterministicHealer.heal_output_3(
            damaged_output_3, metadata=raw_metadata
        )
        assert len(changes) > 0
        parsed_projection = json.loads(healed_raw)
        assert "nodes" in parsed_projection
        assert len(parsed_projection["nodes"]) == 2
        assert "edges" in parsed_projection
        assert len(parsed_projection["edges"]) == 1

        # ----------------------------------------------------------------------
        # Step 3: SQLite Stale Lock Eviction & Concurrent Write Recovery
        # ----------------------------------------------------------------------
        # Create a stale lock from a deceased PID
        dead_pid = 99999999
        stale_lock = lock_dir / "intake_session_dead.flock"
        stale_lock.write_text(f"{dead_pid}\n")
        assert stale_lock.exists()

        # Evict stale locks
        evicted = evict_stale_session_locks(lock_dir=lock_dir)
        assert "intake_session_dead.flock" in evicted
        assert not stale_lock.exists()

        # Simulate a transient write conflict on the SQLite DB
        lock_held = threading.Event()
        lock_released = threading.Event()

        def transient_contender() -> None:
            c = sqlite3.connect(str(db_path), timeout=0.1)
            c.execute("BEGIN EXCLUSIVE;")
            lock_held.set()
            time.sleep(0.12)
            c.execute("COMMIT;")
            c.close()
            lock_released.set()

        t = threading.Thread(target=transient_contender, daemon=True)
        t.start()
        lock_held.wait(timeout=2.0)

        # Main thread attempts write through execute_with_retry
        conn = create_sqlite_conn(db_path)
        roster_json = json.dumps([
            {"name": r["name"], "org": r.get("org"), "provenance": r.get("provenance_origin")}
            for r in raw_metadata["roster"]
        ])
        execute_with_retry(
            conn,
            """
            INSERT INTO intake_projections (session_id, roster_json, projection_json, created_at)
            VALUES (?, ?, ?, ?);
            """,
            ("sess-1009", roster_json, healed_raw, time.time()),
        )
        t.join(timeout=3.0)

        # ----------------------------------------------------------------------
        # Step 4: Independent Verification of State & Behavior
        # ----------------------------------------------------------------------
        cur = conn.execute("SELECT session_id, roster_json, projection_json FROM intake_projections;")
        row = cur.fetchone()
        assert row is not None
        assert row["session_id"] == "sess-1009"

        saved_roster = json.loads(row["roster_json"])
        saved_projection = json.loads(row["projection_json"])

        # Check preserved provenance in saved SQLite row
        noemi_row = next(r for r in saved_roster if r["name"] == "Noemi Monnerville")
        assert noemi_row["org"] == "PartnerAdvisory"
        assert noemi_row["provenance"] == ORIGIN_INPUT_PROVENANCE

        # Check healed projection in saved SQLite row
        assert len(saved_projection["nodes"]) == 2
        conn.close()

    def test_pda_repair_under_live_stream_truncation(self) -> None:
        """E2E test verifying PDA healing of truncated streams and malformed tokens."""
        cases = [
            ('{"status": "in_progress", "is_complete": fal', False),
            ('{"title": "Sync", "verified": tru', True),
            ('{"count": 42, "details": nul', None),
            ('{"tags": ["platform", "engine",', ["platform", "engine"]),
        ]
        for truncated, expected_val in cases:
            repaired = repair_json_tokens(truncated)
            data = json.loads(repaired)
            if "is_complete" in data:
                assert data["is_complete"] is expected_val
            elif "verified" in data:
                assert data["verified"] is expected_val
            elif "details" in data:
                assert data["details"] is expected_val
            elif "tags" in data:
                assert data["tags"] == expected_val
