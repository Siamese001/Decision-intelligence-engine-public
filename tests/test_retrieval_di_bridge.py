"""Regression coverage for the source-bound retrieval-to-decision connector."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.authorization import AuthorizationContext
from dual_pipeline.core import digest, seal_artifact
from dual_pipeline.retrieval_di_bridge import (
    SidecarRetrievalDecisionBridge,
    verify_source_selection,
    verify_use_case_projection,
)


def _authorization(*, audience: str = "internal_consulting") -> AuthorizationContext:
    now = int(time.time())
    values = {
        "subject": "bridge-test",
        "issuer": "test",
        "relying_party": "test",
        "token_id": f"test-{audience}",
        "issued_at": now - 60,
        "expires_at": now + 900,
        "classification_clearance": "restricted",
        "audiences": (audience,),
        "capabilities": ("query",),
        "authorized_resources": {},
        "approved_model_manifest_digest": None,
        "token_digest": "a" * 64,
    }
    return AuthorizationContext(**values, context_digest=digest(values))


def _use_case(use_case_id: str, *, audience: str = "internal_consulting") -> dict[str, object]:
    return {
        "use_case_id": use_case_id,
        "question": f"What is the source-bound posture for {use_case_id}?",
        "scope_id": f"scope-{use_case_id}",
        "audience": audience,
        "required_dimensions": ["delivery", "readiness"],
        "claim_ids": [f"claim-{use_case_id}"],
        "candidate_answer_sections": ["current_status", "evidence"],
    }


def _make_graph(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL);
            CREATE TABLE source_version (
                source_version_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                source_date_normalized TEXT NOT NULL
            );
            CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL);
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_literal TEXT NOT NULL,
                scope_text TEXT,
                qualifiers_json TEXT NOT NULL,
                primary_source_id TEXT NOT NULL,
                extraction_run_id TEXT NOT NULL,
                classification TEXT NOT NULL,
                allowed_audiences_json TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                recorded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                currentness_state TEXT NOT NULL,
                canonical_status TEXT NOT NULL,
                canonical_eligible INTEGER NOT NULL,
                retrieval_eligible INTEGER NOT NULL,
                approval_reference TEXT,
                confidence_label TEXT,
                confidence_score REAL,
                hitl_required INTEGER NOT NULL
            );
            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_version_id TEXT NOT NULL,
                locator_kind TEXT NOT NULL,
                locator_value TEXT NOT NULL,
                direct_quote TEXT NOT NULL
            );
            CREATE TABLE assertion_evidence (assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL);
            """
        )
        connection.execute("INSERT INTO source VALUES (?, ?)", ("source-1", "Bridge source"))
        connection.execute(
            "INSERT INTO source_version VALUES (?, ?, ?, ?, ?)",
            ("source-version-1", "source-1", "b" * 64, "c" * 64, "2026-08-01T00:00:00Z"),
        )
        connection.execute("INSERT INTO node VALUES (?, ?)", ("subject-1", "Bridge subject"))
        projection = {
            "schema_version": "1.0",
            "use_case_ids": [
                "ai-pdlc-pov",
                "ai-productivity-metrics",
                "agentic-readiness",
            ],
            "observations": [
                {
                    "dimension": "delivery",
                    "value": {"status": "source_bound"},
                    "observed_at": "2026-08-02T00:00:00Z",
                    "stale_after": "2026-08-20T00:00:00Z",
                },
                {
                    "dimension": "readiness",
                    "value": {"status": "source_bound"},
                    "observed_at": "2026-08-02T00:00:00Z",
                    "stale_after": "2026-08-20T00:00:00Z",
                },
            ],
            "evidence": [
                {
                    "claim_id": "claim-ai-pdlc-pov",
                    "evidence_role": "supporting",
                    "expires_at": "2026-09-01T00:00:00Z",
                },
                {
                    "claim_id": "claim-ai-pdlc-pov",
                    "evidence_role": "counterevidence",
                    "expires_at": "2026-09-01T00:00:00Z",
                },
                {
                    "claim_id": "claim-ai-productivity-metrics",
                    "evidence_role": "supporting",
                    "expires_at": "2026-09-01T00:00:00Z",
                },
                {
                    "claim_id": "claim-ai-productivity-metrics",
                    "evidence_role": "counterevidence",
                    "expires_at": "2026-09-01T00:00:00Z",
                },
                {
                    "claim_id": "claim-agentic-readiness",
                    "evidence_role": "supporting",
                    "expires_at": "2026-09-01T00:00:00Z",
                },
                {
                    "claim_id": "claim-agentic-readiness",
                    "evidence_role": "counterevidence",
                    "expires_at": "2026-09-01T00:00:00Z",
                },
            ],
        }
        connection.execute(
            """
            INSERT INTO assertion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "assertion-1",
                "subject-1",
                "HAS_CLAIM_TEXT",
                "Source-bound delivery and readiness statement.",
                "Bridge fixture",
                json.dumps({"decision_intelligence_projection": projection}, sort_keys=True),
                "source-1",
                "extract-1",
                "internal",
                '["internal_consulting"]',
                "2026-08-02T00:00:00Z",
                None,
                "2026-08-02T00:01:00Z",
                "2026-08-02T00:02:00Z",
                "current",
                "canonical",
                1,
                1,
                None,
                "high",
                0.95,
                0,
            ),
        )
        connection.execute(
            "INSERT INTO evidence_item VALUES (?, ?, ?, ?, ?, ?)",
            (
                "evidence-1",
                "source-1",
                "source-version-1",
                "fixture-row",
                "row-1",
                "Source-bound delivery and readiness statement.",
            ),
        )
        connection.execute("INSERT INTO assertion_evidence VALUES (?, ?)", ("assertion-1", "evidence-1"))
        connection.commit()
    finally:
        connection.close()


def _make_sidecar(path: Path, *, evidence_ids: list[str] | None = None) -> None:
    text = "Source-bound delivery and readiness statement."
    artifact = seal_artifact(
        {
            "schema_version": "1.0",
            "record_id": "record-1",
            "lane": "approved",
            "record_type": "assertion",
            "provenance_kind": "source_bound",
            "graph_snapshot": "graph-snapshot-1",
            "retrieval_text": text,
            "retrieval_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "extensions": {
                "com.partner_advisory.nee.graph-assertion-ids": ["assertion-1"],
                "com.partner_advisory.nee.graph-evidence-ids": evidence_ids or ["evidence-1"],
            },
        }
    )
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE retrieval_record (
                record_id TEXT PRIMARY KEY,
                lane TEXT NOT NULL,
                record_type TEXT NOT NULL,
                provenance_kind TEXT NOT NULL,
                retrieval_text_hash TEXT NOT NULL,
                artifact_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO retrieval_record VALUES (?, ?, ?, ?, ?, ?)",
            (
                "record-1",
                "approved",
                "assertion",
                "source_bound",
                artifact["retrieval_text_hash"],
                json.dumps(artifact, sort_keys=True),
            ),
        )
        connection.commit()
    finally:
        connection.close()


class RetrievalDIBridgeTests(unittest.TestCase):
    def _bridge(self, root: Path, *, evidence_ids: list[str] | None = None):
        graph = root / "graph.sqlite"
        sidecar = root / "sidecar.sqlite"
        _make_graph(graph)
        _make_sidecar(sidecar, evidence_ids=evidence_ids)
        return graph, sidecar, SidecarRetrievalDecisionBridge(
            graph_path=graph, sidecar_path=sidecar
        )

    def test_visible_retrieval_record_can_feed_all_priority_use_cases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-bridge-") as directory:
            root = Path(directory)
            graph, sidecar, bridge = self._bridge(root)
            selection = bridge.select_retrieval_records(
                record_ids=["record-1"],
                audience="internal_consulting",
                authorization_context=_authorization(),
                known_at="2026-08-09T00:00:00Z",
            )
            verified = verify_source_selection(selection)
            projections = [
                bridge.project_use_case(
                    use_case=_use_case(use_case_id),
                    source_selection=selection,
                    as_of="2026-08-09T00:00:00Z",
                    known_at="2026-08-09T00:00:00Z",
                    authorization_context=_authorization(),
                )
                for use_case_id in (
                    "ai-pdlc-pov",
                    "ai-productivity-metrics",
                    "agentic-readiness",
                )
            ]
        self.assertEqual(1, len(verified["visible_records"]))
        self.assertEqual(
            ["graph-assertion:assertion-1:evidence:evidence-1"],
            verified["visible_source_assertion_ids"],
        )
        self.assertTrue(all(item["source_selection_consistent"] for item in projections))
        self.assertTrue(all(item["answer_posture"] == "CANDIDATE_ONLY" for item in projections))
        self.assertTrue(all(verify_use_case_projection(item) for item in projections))

    def test_monitor_denial_exposes_no_source_or_record_identifiers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-bridge-") as directory:
            root = Path(directory)
            _, _, bridge = self._bridge(root)
            selection = bridge.select_retrieval_records(
                record_ids=["record-1"],
                audience="client_approved_external",
                authorization_context=_authorization(audience="client_approved_external"),
                known_at="2026-08-09T00:00:00Z",
            )
            projection = bridge.project_use_case(
                use_case=_use_case("ai-pdlc-pov", audience="client_approved_external"),
                source_selection=selection,
                as_of="2026-08-09T00:00:00Z",
                known_at="2026-08-09T00:00:00Z",
                authorization_context=_authorization(audience="client_approved_external"),
            )

        self.assertEqual([], selection["visible_records"])
        self.assertEqual([], selection["visible_source_assertion_ids"])
        self.assertEqual(1, selection["monitor_filtered_record_count"])
        self.assertEqual("NO_ANSWER", projection["answer_posture"])
        self.assertEqual([], projection["displayed_source_assertion_ids"])
        self.assertNotIn("record-1", json.dumps(selection, sort_keys=True))
        self.assertNotIn("assertion-1", json.dumps(selection, sort_keys=True))

    def test_incomplete_sidecar_source_mapping_is_withheld(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-bridge-") as directory:
            root = Path(directory)
            _, _, bridge = self._bridge(root, evidence_ids=["evidence-1", "missing-evidence"])
            selection = bridge.select_retrieval_records(
                record_ids=["record-1"],
                audience="internal_consulting",
                authorization_context=_authorization(),
                known_at="2026-08-09T00:00:00Z",
            )

        self.assertEqual([], selection["visible_records"])
        self.assertEqual([], selection["visible_source_assertion_ids"])
        self.assertEqual(1, selection["unmapped_record_count"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
