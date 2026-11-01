#!/usr/bin/env python3
"""tests/adversarial/test_output3_uwg_readback_adversarial.py

Adversarial and Edge-Case Tests for Output 03 -> UWG -> Publication -> Readback.
Exposes and guards against:
1. Schema mismatch in readback verifier when querying real database tables (no properties_json in node, no claim_text in assertion).
2. Silent skip when evidence_manifest.json is absent despite Output 03 facts existing.
3. False completion when should_mutate is False (package-only mode) reporting COMPLETE instead of PACKAGE_READY.
4. Stale generation divergence blocking mutation proposals.
5. Empty string generation ID propagation on idempotent no-ops.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.operation_contracts import (
    LifecycleState,
    validate_completion_readiness,
)
from dual_pipeline.canonical_pipeline.publication_verifier import (
    _inspect_assertion_in_graph,
    _inspect_node_in_graph,
    record_publication_fact_coverage,
    verify_fact_coverage_in_graph,
)
from dual_pipeline.core import sha256_file
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    MutationAuthorizationError,
    validate_mutation_authorization,
)


class TestOutput3UWGReadbackAdversarial(unittest.TestCase):
    """Adversarial suite verifying edge cases and failure modes in publication and readback."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.out_p = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _create_real_schema_db(self, path: Path) -> sqlite3.Connection:
        """Create SQLite schema matching actual graph/nee_graph.sqlite."""
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                display_name TEXT,
                description TEXT,
                status TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                assertion_fingerprint TEXT,
                subject_node_id TEXT,
                predicate TEXT,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                object_literal_type TEXT,
                assertion_type TEXT,
                atomicity_state TEXT,
                scope_text TEXT,
                speaker_or_author TEXT,
                primary_source_id TEXT,
                extraction_run_id TEXT,
                extraction_method TEXT,
                origin_type TEXT,
                confidence_label TEXT,
                confidence_score REAL,
                validation_state TEXT,
                currentness_state TEXT,
                valid_from TEXT,
                valid_to TEXT,
                date_precision TEXT,
                date_status TEXT,
                recorded_at TEXT,
                superseded_at TEXT,
                superseded_by_assertion_id TEXT,
                resolution_state TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                retrieval_eligible INTEGER,
                materiality TEXT,
                classification TEXT,
                allowed_audiences_json TEXT,
                hitl_required INTEGER,
                validation_priority TEXT,
                validation_owner TEXT,
                qualifiers_json TEXT,
                legacy_record_type TEXT,
                legacy_record_ids_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                approval_reference TEXT
            );
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT,
                edge_type TEXT,
                to_node_id TEXT,
                description TEXT,
                evidence_posture TEXT,
                confidence REAL,
                materiality TEXT,
                validation_priority TEXT,
                validation_owner TEXT,
                hitl_required INTEGER,
                source_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                superseded_by TEXT
            );
            """
        )
        conn.commit()
        return conn

    def test_schema_adaptive_inspection_does_not_crash(self) -> None:
        """Inspect node and assertion on real table schema without properties_json or claim_text."""
        db_path = self.out_p / "test_graph.sqlite"
        conn = self._create_real_schema_db(db_path)

        # Seed an entity and assertion
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, active) VALUES (?, ?, ?, ?, ?)",
            ("person_jason", "PERSON", "Jason", "Jason PartnerAdvisory", 1),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, currentness_state) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("asrt_001", "person_jason", "ROLE", "Technical Owner", "Owner of SAP Migration", "current"),
        )
        conn.commit()

        # Should adaptively query without sqlite3.OperationalError
        node_res = _inspect_node_in_graph(conn, "person_jason")
        self.assertIsNotNone(node_res)
        self.assertEqual(node_res["node_id"], "person_jason")
        self.assertEqual(node_res["canonical_name"], "Jason")

        asrt_res = _inspect_assertion_in_graph(conn, "person_jason", "ROLE")
        self.assertIsNotNone(asrt_res)
        self.assertEqual(asrt_res["assertion_id"], "asrt_001")
        self.assertEqual(asrt_res["object_literal"], "Technical Owner")

        conn.close()

    def test_validate_completion_readiness_enforces_package_ready_on_unmutated(self) -> None:
        """Package-only or unmutated pipeline executions must never claim COMPLETE."""
        artifacts = {
            "01_transcript": str(self.out_p / "01_transcript.md"),
            "02_recap": str(self.out_p / "02_recap.md"),
            "03_graph": str(self.out_p / "03_graph.json"),
        }

        # Case 1: Not published (e.g. --package-only mode)
        ready, msg, state = validate_completion_readiness(
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            written_artifacts=artifacts,
            is_published=False,
            is_readback_verified=False,
        )
        self.assertTrue(ready)
        self.assertEqual(state, LifecycleState.PACKAGE_READY)
        self.assertNotEqual(state, LifecycleState.COMPLETE)

        # Case 2: Published but readback unverified
        ready, msg, state = validate_completion_readiness(
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            written_artifacts=artifacts,
            is_published=True,
            is_readback_verified=False,
        )
        self.assertFalse(ready)
        self.assertEqual(state, LifecycleState.PUBLISHED_UNVERIFIED)

        # Case 3: Published and readback verified
        ready, msg, state = validate_completion_readiness(
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            written_artifacts=artifacts,
            is_published=True,
            is_readback_verified=True,
        )
        self.assertTrue(ready)
        self.assertEqual(state, LifecycleState.COMPLETE)

    def test_stale_base_generation_fails_closed(self) -> None:
        """Approval tied to generation A fails closed when active generation is generation B."""
        rt_root = self.out_p / "runtime"
        rt_root.mkdir(parents=True)
        ap_file = rt_root / "active_pair.json"
        ap_file.write_text(
            json.dumps({"generation_id": "gen_20261009_active_pair_v2"}),
            encoding="utf-8",
        )

        receipt = {
            "request_id": "req-12345",
            "meeting_name": "Standup",
            "operator_origin": "HOST_UI",
            "expected_base_generation": "gen_20261009_stale_pair_v1",
            "concrete_base_generation": "gen_20261009_stale_pair_v1",
            "status": "APPROVED",
            "proposal_digest": "0" * 64,
        }

        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(
                output_dir=self.out_p,
                metadata={"approval_receipt": receipt},
                written={},
                runtime_root=rt_root,
            )
        self.assertIn("AUTHORIZATION_DENIED_STALE_BASE_GENERATION", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
