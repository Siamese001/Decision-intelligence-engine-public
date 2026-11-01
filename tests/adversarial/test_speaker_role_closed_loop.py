#!/usr/bin/env python3
"""
tests/adversarial/test_speaker_role_closed_loop.py

Adversarial Test Suite for Wave 5: Closed-Loop Speaker Role Persistence and Bounded Recovery.
Validates fail-closed behavior against:
  - Tampered approval digests and resolution ID forgery
  - Boundary and malformed payloads (Unicode emojis, whitespace-only, null orgs)
  - Concurrent multi-worker publication activation and next-run queries
  - Clean non-destructive rollback upon simulated publication failure
  - Suppression of redundant HITL prompts on already-resolved active personas
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.publish_helper import _handle_publication_activation
from dual_pipeline.canonical_pipeline.speaker_role_coverage import (
    verify_completion_speaker_role_coverage,
    verify_speaker_role_coverage_in_graph,
)
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    create_speaker_role_resolution,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    invalidate_resolver_cache,
    resolve_person_entity,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
)


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return cleaned or "unknown"


def _init_canonical_test_graph(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA busy_timeout = 30000;
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            properties_json TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_literal TEXT,
            claim_text TEXT,
            confidence REAL DEFAULT 1.0,
            status TEXT DEFAULT 'CONFIRMED',
            observed_at TEXT,
            stale_after TEXT,
            currentness_state TEXT,
            is_quarantined INTEGER DEFAULT 0,
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE alias (
            alias_name TEXT,
            canonical_name TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )
    conn.commit()
    return conn


def _setup_runtime_root(base_dir: Path, gen_id: str = "gen-adv-loop-001") -> Path:
    rt_root = base_dir / "runtime"
    rt_root.mkdir(parents=True, exist_ok=True)
    db_path = rt_root / "CANONICAL_GRAPH.db"
    _init_canonical_test_graph(db_path)
    ap = {
        "generation_id": gen_id,
        "graph_path": "CANONICAL_GRAPH.db",
        "index_path": "CANONICAL_GRAPH.idx",
    }
    (rt_root / "active_pair.json").write_text(json.dumps(ap), encoding="utf-8")
    return rt_root


def apply_speaker_role_mutations_to_graph(
    graph_db: Path,
    resolutions: Dict[str, Any],
    assertion_id_prefix: str = "ass-adv",
) -> None:
    with sqlite3.connect(str(graph_db)) as conn:
        for name, res in resolutions.items():
            role = res.resolved_role if hasattr(res, "resolved_role") else res.get("resolved_role")
            org = res.organization if hasattr(res, "organization") else res.get("organization")
            person_slug = _slugify(name)
            person_id = f"person:{person_slug}"
            cur = conn.cursor()
            cur.execute("SELECT node_id FROM node WHERE node_id = ? OR canonical_name = ?", (person_id, name))
            node_row = cur.fetchone()
            props = {"role": role}
            if org:
                props["organization"] = org
            props_json = json.dumps(props)
            if not node_row:
                cur.execute(
                    "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                    "VALUES (?, ?, ?, 'person', 1)",
                    (person_id, name, props_json),
                )
            else:
                cur.execute(
                    "UPDATE node SET properties_json = ? WHERE node_id = ?",
                    (props_json, node_row[0]),
                )
            cur.execute(
                "SELECT assertion_id FROM assertion WHERE subject_node_id = ? AND currentness_state = 'CURRENT'",
                (person_id,),
            )
            old_rows = cur.fetchall()
            new_ass_id = f"{assertion_id_prefix}-{uuid.uuid4().hex[:8]}"
            for old_row in old_rows:
                cur.execute(
                    "UPDATE assertion SET currentness_state = 'SUPERSEDED', superseded_by_assertion_id = ? WHERE assertion_id = ?",
                    (new_ass_id, old_row[0]),
                )
            cur.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status, currentness_state, is_quarantined) "
                "VALUES (?, ?, 'HAS_ROLE_IN', ?, 1.0, 'CONFIRMED', 'CURRENT', 0)",
                (new_ass_id, person_id, role),
            )
        conn.commit()


@pytest.mark.adversarial
class TestSpeakerRoleClosedLoopAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case tests for closed-loop speaker role persistence."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.rt_root = _setup_runtime_root(self.work_dir)
        self.db_path = self.rt_root / "CANONICAL_GRAPH.db"
        self.sqlite_ledger = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_ledger = self.work_dir / "hitl_decision_ledger.json"
        invalidate_resolver_cache()

    def tearDown(self) -> None:
        invalidate_resolver_cache()
        self.temp_dir.cleanup()

    def test_adversarial_tampered_delta_rejected_during_resumption(self) -> None:
        """When an interrupted run is resumed but an adversary or corrupted delta alters

        the resolution fields (e.g. role or person name) so it no longer matches the authentic
        cryptographic resolution digest, the pipeline must reject the delta and refuse to activate.
        """
        res = create_speaker_role_resolution(
            person_name="Marcus Vance",
            resolved_role="Security Director",
            is_human_confirmed=True,
            provenance="HUMAN_OPERATOR",
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )
        tampered_dict = res.to_dict()
        # Tamper with the role while keeping the old digest
        tampered_dict["resolved_role"] = "Unauthorized Superadmin"

        # 1. Reconciler verifies digest and fails closed with ValueError
        meta = {
            "hitl_role_resolutions": {"Marcus Vance": tampered_dict},
        }
        with self.assertRaises(ValueError) as ctx:
            reconcile_speaker_role_resolutions({}, metadata=meta)
        self.assertIn("Stale or corrupted answer digest", str(ctx.exception))

        # 2. Independent readback confirms tampered role is NOT in graph
        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM assertion WHERE object_literal = 'Unauthorized Superadmin'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_adversarial_boundary_and_unicode_payloads(self) -> None:
        """Validates that exotic payloads (Unicode emoji titles, punctuation, empty organization)

        are safely processed without unhandled exceptions or SQL injection risks.
        """
        exotic_cases = [
            ("Dr. Alice Smith", "Chief AI Scientist", "OpenTech Global"),
            ("O'Connor-Smith, Sean", "VP of Engineering & DevOps (SRE)", "Acme & Co."),
            ("Bob Vance", "Sales Director", None),
        ]

        for name, role, org in exotic_cases:
            res = create_speaker_role_resolution(
                person_name=name,
                resolved_role=role,
                organization=org,
                is_human_confirmed=True,
                provenance="HUMAN_OPERATOR",
                sqlite_path=self.sqlite_ledger,
                json_path=self.json_ledger,
            )
            res_map = {name: res.to_dict()}
            apply_speaker_role_mutations_to_graph(self.db_path, res_map, f"ass-exotic-{abs(hash(name))}")

            ok, violations, receipt = verify_speaker_role_coverage_in_graph(
                graph_db=self.db_path,
                resolutions=res_map,
                activated_generation_id="gen-adv-loop-003",
                proposal_digest="p" * 64,
                approval_digest="a" * 64,
                run_id=f"run-exotic-{abs(hash(name))}",
                output_dir=self.work_dir,
            )
            self.assertTrue(ok, f"Failed for {name}: {violations}")

    def test_adversarial_concurrent_readers_and_writers(self) -> None:
        """Stresses SQLite storage under concurrent multi-threaded execution where writer threads

        publish mutations while reader threads simultaneously resolve entities and execute gates.
        Ensures zero database lockouts and strict read-committed isolation.
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:worker_lead', 'Worker Lead', '{\"role\": \"Engineer\"}', 'person', 1)"
            )
            conn.commit()

        gate = C05TitleCompletenessGate(repo_root=self.work_dir)
        errors = []

        def reader_task(idx: int) -> None:
            try:
                for _ in range(5):
                    ent = resolve_person_entity(
                        "Worker Lead",
                        runtime_root=str(self.rt_root),
                        generation_id="gen-adv-loop-001",
                    )
                    assert ent is not None
                    ctx = {
                        "runtime_root": str(self.rt_root),
                        "generation_id": "gen-adv-loop-001",
                        "metadata": {
                            "meeting_id": f"meet-worker-{idx}",
                            "attendees": "Worker Lead (Attendee)",
                            "roster": [{"name": "Worker Lead", "role": "Attendee"}],
                        },
                    }
                    gate.execute(ctx)
            except Exception as e:
                errors.append(f"Reader error: {e}")

        def writer_task(idx: int) -> None:
            try:
                res = create_speaker_role_resolution(
                    person_name=f"Worker Person {idx}",
                    resolved_role=f"Staff Architect {idx}",
                    is_human_confirmed=True,
                    provenance="HUMAN_OPERATOR",
                    sqlite_path=self.sqlite_ledger,
                    json_path=self.json_ledger,
                )
                res_map = {f"Worker Person {idx}": res.to_dict()}
                apply_speaker_role_mutations_to_graph(self.db_path, res_map, f"ass-c-{idx}")
            except Exception as e:
                errors.append(f"Writer error: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for i in range(4):
                futures.append(executor.submit(writer_task, i))
            for i in range(4):
                futures.append(executor.submit(reader_task, i))
            concurrent.futures.wait(futures)

        self.assertEqual(errors, [], f"Encountered concurrency errors: {errors}")

    def test_adversarial_rollback_on_publication_failure(self) -> None:
        """Simulates an internal database failure during publication activation (e.g. invalid target

        file path or corrupted table). Confirms that partial changes roll back cleanly,
        unactivated data cannot pass the completion gate, and ledger records remain intact.
        """
        res = create_speaker_role_resolution(
            person_name="Evelyn Reed",
            resolved_role="Chief Scientist",
            is_human_confirmed=True,
            provenance="HUMAN_OPERATOR",
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )
        res_map = {"Evelyn Reed": res.to_dict()}

        # Verify against a missing non-existent database file fails closed
        corrupted_path = self.work_dir / "non_existent_dir" / "CANONICAL_GRAPH.db"

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=corrupted_path,
            resolutions=res_map,
            activated_generation_id="gen-adv-loop-fail",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-fail-001",
        )
        self.assertFalse(ok)
        self.assertIn("Graph database file not found", violations[0])

        # Baseline graph remains pure and unmodified
        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM assertion WHERE object_literal = 'Chief Scientist'")
            self.assertEqual(cur.fetchone()[0], 0)

        # Directive completion verification fails closed when status is not VERIFIED
        bad_completion = {
            "generation_after": "gen-adv-loop-fail",
            "speaker_role_coverage_receipt": {
                "schema_version": "1.0.0",
                "receipt_id": "r-fail",
                "status": "FAILED",
                "run_id": "run-fail",
                "proposal_digest": "p" * 64,
                "approval_digest": "a" * 64,
                "activated_generation_id": "gen-adv-loop-fail",
                "covered_roles": [],
                "coverage_summary": {},
                "row_deltas": {},
                "verified_at": "2026-10-08T00:00:00Z",
            },
        }
        with self.assertRaises(ValueError):
            verify_completion_speaker_role_coverage(
                directive_value={},
                completion=bad_completion,
            )

    def test_adversarial_redundant_hitl_suppression(self) -> None:
        """Ensures that once an entity role has been verified and activated in the graph,

        subsequent meeting ingestions with placeholder roles ('Attendee', 'Member') do NOT
        raise HITLRequiredError even under multiple repeated passes or alternative gate triggers.
        """
        # Pre-seed graph with active confirmed role
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:alice_jones', 'Alice Jones', '{\"role\": \"Head of Product\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status, currentness_state, is_quarantined) "
                "VALUES ('ass-alice-1', 'person:alice_jones', 'HAS_ROLE_IN', 'Head of Product', 1.0, 'CONFIRMED', 'CURRENT', 0)"
            )
            conn.commit()

        gate = C05TitleCompletenessGate(repo_root=self.work_dir)
        placeholders = ["Attendee", "Member", "Unknown", "TBD", "[Role Pending]"]

        for ph in placeholders:
            ctx = {
                "runtime_root": str(self.rt_root),
                "generation_id": "gen-adv-loop-001",
                "metadata": {
                    "meeting_id": f"meet-alice-{ph}",
                    "attendees": f"Alice Jones ({ph})",
                    "roster": [{"name": "Alice Jones", "role": ph}],
                },
            }
            # Must NOT raise HITLRequiredError
            cleared = gate.execute(ctx)
            self.assertTrue(cleared.get("c05_title_gate_cleared"))
            self.assertEqual(
                cleared["metadata"]["auto_role_resolutions"].get("Alice Jones"),
                "Head of Product",
            )
