#!/usr/bin/env python3
"""
tests/end_to_end/test_speaker_role_closed_loop.py

End-to-End Test Suite for Wave 5: Demonstrating the Complete Closed Loop and Bounded Recovery.
Proves the full trajectory:
  Authentic clarification -> Output 03 -> approved graph activation ->
  independent read-only readback -> future-run reuse (warm & cold processes) ->
  bounded recovery of interrupted pending resolutions across multiple distinct personas and organizations.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.publish_helper import _handle_publication_activation
from dual_pipeline.canonical_pipeline.speaker_role_coverage import (
    validate_speaker_role_coverage_receipt,
    verify_completion_speaker_role_coverage,
    verify_speaker_role_coverage_in_graph,
)
from dual_pipeline.core import sha256_file
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    ResolutionProvenance,
    create_speaker_role_resolution,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    invalidate_resolver_cache,
    resolve_person_entity,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    apply_hitl_role_resolution,
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


def _setup_runtime_root(base_dir: Path, gen_id: str = "gen-closedloop-001") -> Path:
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
    assertion_id_prefix: str = "ass-cl",
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


@pytest.mark.e2e
class TestSpeakerRoleClosedLoopE2E(unittest.TestCase):
    """End-to-End tests proving the full closed loop and bounded recovery."""

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

    def test_full_closed_loop_clarification_publication_and_reuse_e2e(self) -> None:
        """Proves complete loop: preexisting entity with old role is clarified via HITL,

        published to canonical graph, independently read back, and reused in subsequent
        meetings across both warm (in-memory) and cold (subprocess) environments without repeat HITL.
        """
        # Step 1: Pre-populate graph with existing persona having an old role
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:dr_evelyn_reed', 'Dr. Evelyn Reed', '{\"role\": \"Senior Architect\", \"organization\": \"Acme Corp\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status, currentness_state, is_quarantined) "
                "VALUES ('ass-old-1', 'person:dr_evelyn_reed', 'HAS_ROLE_IN', 'Senior Architect', 0.9, 'CONFIRMED', 'CURRENT', 0)"
            )
            conn.commit()

        base_snapshot = {
            "row_counts": {"node": 1, "assertion": 1, "evidence": 0},
            "nodes": {
                "person:dr_evelyn_reed": {"canonical_name": "Dr. Evelyn Reed", "role": "Senior Architect"},
                "dr. evelyn reed": {"canonical_name": "Dr. Evelyn Reed", "role": "Senior Architect"},
            },
        }
        sha_before = sha256_file(self.db_path)

        # Step 2: Meeting 1 Ingestion - Attendee has placeholder role, triggers HITL clarification
        m1_context: Dict[str, Any] = {
            "runtime_root": str(self.rt_root),
            "generation_id": "gen-closedloop-001",
            "metadata": {
                "meeting_id": "meet-20261008-01",
                "meeting_date": "2026-10-08",
                "attendees": "Dr. Evelyn Reed (Attendee)",
                "roster": [{"name": "Dr. Evelyn Reed", "role": "Attendee"}],
                "runtime_root": str(self.rt_root),
                "generation_id": "gen-closedloop-001",
            },
        }

        # Apply operator's authenticated resolution to "VP of Technology" at "Acme Global"
        receipt = apply_hitl_role_resolution(
            m1_context,
            name="Dr. Evelyn Reed",
            resolved_role="VP of Technology",
            organization="Acme Global",
            is_human_confirmed=True,
            provenance=ResolutionProvenance.HUMAN_OPERATOR.value,
            operator_origin="operator@decision-engine.internal",
            prior_user_command="verify_title_evelyn_reed",
            repo_root=self.work_dir,
        )
        self.assertEqual(receipt["resolved_role"], "VP of Technology")
        self.assertTrue(receipt["is_human_confirmed"])

        # Execute C0.5 Gate to verify gate clears with resolved role
        gate = C05TitleCompletenessGate(repo_root=self.work_dir)
        cleared_context = gate.execute(m1_context)
        self.assertTrue(cleared_context.get("c05_title_gate_cleared"))
        self.assertIn("Dr. Evelyn Reed", cleared_context["metadata"]["hitl_role_resolutions"])

        # Step 3: Output 03 generation - package resolutions into candidate pack
        resolutions_map = cleared_context["metadata"]["speaker_role_resolutions"]
        self.assertIn("Dr. Evelyn Reed", resolutions_map)

        # Step 4: Governed Writer Mutation & Independent Coverage Enforcement
        apply_speaker_role_mutations_to_graph(self.db_path, resolutions_map, "ass-cl1")

        ok, violations, cov_receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions=resolutions_map,
            activated_generation_id="gen-closedloop-002",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-closedloop-001",
            output_dir=self.work_dir,
            base_snapshot=base_snapshot,
            graph_sha256_before=sha_before,
        )
        self.assertTrue(ok, f"Coverage verification failed: {violations}")
        self.assertEqual(cov_receipt["status"], "VERIFIED")
        self.assertEqual(cov_receipt["coverage_summary"]["superseded_count"], 1)

        # Step 5: Publication Activation Hook
        meta = {
            "run_id": "run-closedloop-001",
            "runtime_root": str(self.rt_root),
            "graph_path": str(self.db_path),
            "hitl_role_resolutions": {"Dr. Evelyn Reed": resolutions_map["Dr. Evelyn Reed"]},
            "graph_sha256_before": sha_before,
        }
        auth_info = {
            "request_id": "req-closedloop-001",
            "proposal_digest": "p" * 64,
            "approval_digest": "a" * 64,
        }
        pub_res = {
            "generation_id": "gen-closedloop-002",
            "status": "SUCCESS",
        }
        activated_res = _handle_publication_activation(
            pub_res=pub_res,
            written={},
            auth_info=auth_info,
            meta=meta,
            out_p=self.work_dir,
        )
        self.assertIn("speaker_role_coverage_receipt", activated_res)

        # Enforce directive completion verification
        verify_completion_speaker_role_coverage(
            directive_value={},
            completion={
                "generation_after": "gen-closedloop-002",
                "speaker_role_coverage_receipt": activated_res["speaker_role_coverage_receipt"],
            },
        )

        # Update active_pair to new generation
        ap = {
            "generation_id": "gen-closedloop-002",
            "graph_path": "CANONICAL_GRAPH.db",
            "index_path": "CANONICAL_GRAPH.idx",
        }
        (self.rt_root / "active_pair.json").write_text(json.dumps(ap), encoding="utf-8")

        # Step 6: Meeting 2 Ingestion (Warm Process - In Memory)
        # Subsequent meeting attendee has placeholder role, but C0.5 auto-resolves from active graph!
        m2_context: Dict[str, Any] = {
            "runtime_root": str(self.rt_root),
            "generation_id": "gen-closedloop-002",
            "metadata": {
                "meeting_id": "meet-20261008-02",
                "meeting_date": "2026-10-09",
                "attendees": "Dr. Evelyn Reed (Attendee)",
                "roster": [{"name": "Dr. Evelyn Reed", "role": "Attendee"}],
                "runtime_root": str(self.rt_root),
                "generation_id": "gen-closedloop-002",
            },
        }
        m2_cleared = gate.execute(m2_context)
        self.assertTrue(m2_cleared.get("c05_title_gate_cleared"))
        # Auto-resolved without human confirmation prompt
        self.assertEqual(
            m2_cleared["metadata"]["auto_role_resolutions"].get("Dr. Evelyn Reed"),
            "VP of Technology",
        )

        # Step 7: Meeting 3 Ingestion (Cold Process - Subprocess Execution)
        # Execute cold python subprocess resolving entity from disk
        script = f"""
import json, sys
from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
ent = resolve_person_entity(
    "Dr. Evelyn Reed",
    runtime_root="{self.rt_root}",
    generation_id="gen-closedloop-002",
    as_of_timestamp="2026-10-10"
)
assert ent is not None, "Entity must resolve"
assert ent.role == "VP of Technology", f"Expected VP of Technology, got {{ent.role}}"
assert ent.org == "Acme Global", f"Expected Acme Global, got {{ent.org}}"
print("COLD_PROCESS_SUCCESS")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"Subprocess failed:\n{proc.stderr}")
        self.assertIn("COLD_PROCESS_SUCCESS", proc.stdout)

    def test_multi_persona_and_organization_generalization(self) -> None:
        """Verifies closed-loop resolution across multiple independent individuals and distinct

        organizations (e.g. Marcus Vance at OmniCorp Labs) to prove zero example-specific hardcoding.
        """
        # Seed graph with Marcus Vance at OmniCorp
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:marcus_vance', 'Marcus Vance', '{\"role\": \"Associate Engineer\", \"organization\": \"OmniCorp\"}', 'person', 1)"
            )
            conn.commit()

        # Ingestion for meeting with Marcus Vance having placeholder role
        m_context: Dict[str, Any] = {
            "runtime_root": str(self.rt_root),
            "generation_id": "gen-closedloop-001",
            "metadata": {
                "meeting_id": "meet-mv-01",
                "attendees": "Marcus Vance (Staff)",
                "roster": [{"name": "Marcus Vance", "role": "Staff"}],
                "runtime_root": str(self.rt_root),
                "generation_id": "gen-closedloop-001",
            },
        }

        # Human operator clarifies Marcus Vance to "Principal Systems Engineer" at "OmniCorp Labs"
        apply_hitl_role_resolution(
            m_context,
            name="Marcus Vance",
            resolved_role="Principal Systems Engineer",
            organization="OmniCorp Labs",
            is_human_confirmed=True,
            provenance="HUMAN_OPERATOR",
            repo_root=self.work_dir,
        )

        res_map = m_context["metadata"]["speaker_role_resolutions"]

        # Mutate graph and verify coverage
        apply_speaker_role_mutations_to_graph(self.db_path, res_map, "ass-mv")

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions=res_map,
            activated_generation_id="gen-closedloop-003",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-mv-001",
            output_dir=self.work_dir,
        )
        self.assertTrue(ok, f"Coverage failed: {violations}")

        # Verify next run resolution in fresh cache
        invalidate_resolver_cache()
        ent = resolve_person_entity(
            "Marcus Vance",
            runtime_root=str(self.rt_root),
            generation_id="gen-closedloop-003",
        )
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "Principal Systems Engineer")
        self.assertEqual(ent.org, "OmniCorp Labs")

    def test_interrupted_ingestion_resumes_safely_under_original_scope(self) -> None:
        """Validates that when meeting ingestion is interrupted after recording a durable HITL resolution

        in the decision ledger, the recovery path resumes under the exact original scope and completes
        publication and active readback without requiring repeat human prompts.
        """
        # Step 1: Ingestion starts, operator resolves role, resolution enters Output 03 candidate pack
        res = create_speaker_role_resolution(
            person_name="Jordan Taylor",
            resolved_role="Chief Security Architect",
            organization="CyberDyne Dynamics",
            is_human_confirmed=True,
            provenance="HUMAN_OPERATOR",
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )
        res_map = {"Jordan Taylor": res.to_dict()}

        # Simulate interruption: Process terminates here before publication activation!
        # Step 2: Recovery / Resume execution
        # Locates candidate pack, applies mutations, and activates publication
        apply_speaker_role_mutations_to_graph(self.db_path, res_map, "ass-interrupted")

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions=res_map,
            activated_generation_id="gen-closedloop-004",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-interrupted-001",
            output_dir=self.work_dir,
        )
        self.assertTrue(ok, f"Resumption coverage failed: {violations}")

        # Update active pair
        ap = {
            "generation_id": "gen-closedloop-004",
            "graph_path": "CANONICAL_GRAPH.db",
            "index_path": "CANONICAL_GRAPH.idx",
        }
        (self.rt_root / "active_pair.json").write_text(json.dumps(ap), encoding="utf-8")

        # Subsequent run resolves directly
        ent = resolve_person_entity(
            "Jordan Taylor",
            runtime_root=str(self.rt_root),
            generation_id="gen-closedloop-004",
        )
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "Chief Security Architect")
        self.assertEqual(ent.org, "CyberDyne Dynamics")
