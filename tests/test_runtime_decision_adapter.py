#!/usr/bin/env python3
"""Regression coverage for the read-only source-bound runtime adapter."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.authorization import AuthorizationContext
from dual_pipeline.core import digest
from dual_pipeline.runtime_decision_adapter import (
    RuntimeDecisionSourceAdapter,
    RuntimeDecisionSourceError,
)

USE_CASE = {
    "use_case_id": "ai-pdlc-pov",
    "question": "What should the AI-PDLC POV be for NEA?",
    "scope_id": "nea-ai-pdlc",
    "audience": "internal_consulting",
    "claim_ids": ["claim-ai-pdlc-pov"],
}


def _authorization(*, audience: str = "internal_consulting") -> AuthorizationContext:
    now = int(time.time())
    values = {
        "subject": "runtime-adapter-test",
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


def _make_graph(
    path: Path,
    *,
    projection: dict[str, object] | None,
    approval_reference: str | None = "approval-1",
) -> None:
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
        connection.execute("INSERT INTO source VALUES (?, ?)", ("source-1", "NEA source"))
        connection.execute(
            "INSERT INTO source_version VALUES (?, ?, ?, ?, ?)",
            ("source-version-1", "source-1", "b" * 64, "c" * 64, "2026-08-01T00:00:00Z"),
        )
        connection.execute("INSERT INTO node VALUES (?, ?)", ("nea-ai-pdlc", "NEA AI-PDLC"))
        qualifiers = {} if projection is None else {"decision_intelligence_projection": projection}
        connection.execute(
            """
            INSERT INTO assertion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "assertion-1",
                "nea-ai-pdlc",
                "HAS_CLAIM_TEXT",
                "NEA AI-PDLC delivery and readiness source statement.",
                "NEA AI-PDLC",
                json.dumps(qualifiers, sort_keys=True),
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
                approval_reference,
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
                "NEA AI-PDLC delivery and readiness source statement.",
            ),
        )
        connection.execute("INSERT INTO assertion_evidence VALUES (?, ?)", ("assertion-1", "evidence-1"))
        connection.commit()
    finally:
        connection.close()


class RuntimeDecisionSourceAdapterTests(unittest.TestCase):
    def test_declared_projection_yields_source_bound_w3_and_w6_inputs(self) -> None:
        projection = {
            "schema_version": "1.0",
            "use_case_ids": ["ai-pdlc-pov"],
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
            ],
        }
        with tempfile.TemporaryDirectory(prefix="nee-runtime-adapter-") as directory:
            graph = Path(directory) / "graph.sqlite"
            _make_graph(graph, projection=projection, approval_reference=None)
            adapter = RuntimeDecisionSourceAdapter(graph)
            inputs = adapter.inputs_for_use_case(
                use_case=USE_CASE,
                authorization_context=_authorization(),
                as_of="2026-08-09T00:00:00Z",
                known_at="2026-08-09T00:00:00Z",
            )

        self.assertEqual(2, len(inputs["observations"]))
        self.assertEqual(2, len(inputs["evidence_packets"]))
        self.assertTrue(inputs["selection"]["monitor_filtered_before_exposure"])
        self.assertFalse(inputs["selection"]["typed_projection_missing"])
        self.assertTrue(
            all(item["source_assertion_ids"] for item in inputs["observations"])
        )
        self.assertTrue(
            all(item["review_state"] == "SOURCE_BOUND" for item in inputs["evidence_packets"])
        )

    def test_unmapped_lexical_match_remains_expired_context_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-runtime-adapter-") as directory:
            graph = Path(directory) / "graph.sqlite"
            _make_graph(graph, projection=None)
            adapter = RuntimeDecisionSourceAdapter(graph)
            inputs = adapter.inputs_for_use_case(
                use_case=USE_CASE,
                authorization_context=_authorization(),
                as_of="2026-08-09T00:00:00Z",
                known_at="2026-08-09T00:00:00Z",
            )

        self.assertEqual([], inputs["observations"])
        self.assertTrue(inputs["selection"]["typed_projection_missing"])
        self.assertEqual(1, len(inputs["evidence_packets"]))
        packet = inputs["evidence_packets"][0]
        self.assertEqual("context", packet["evidence_role"])
        self.assertEqual("SOURCE_BOUND", packet["review_state"])
        self.assertIn("assertion_text", packet["content"])

    def test_monitor_denial_returns_no_ids_or_content(self) -> None:
        projection = {
            "schema_version": "1.0",
            "use_case_ids": ["ai-pdlc-pov"],
            "observations": [],
            "evidence": [],
        }
        with tempfile.TemporaryDirectory(prefix="nee-runtime-adapter-") as directory:
            graph = Path(directory) / "graph.sqlite"
            _make_graph(graph, projection=projection)
            adapter = RuntimeDecisionSourceAdapter(graph)
            denied_use_case = dict(USE_CASE, audience="client_approved_external")
            inputs = adapter.inputs_for_use_case(
                use_case=denied_use_case,
                authorization_context=_authorization(audience="client_approved_external"),
                as_of="2026-08-09T00:00:00Z",
                known_at="2026-08-09T00:00:00Z",
            )

        self.assertEqual([], inputs["observations"])
        self.assertEqual([], inputs["evidence_packets"])
        self.assertEqual(0, inputs["selection"]["visible_context_source_count"])
        self.assertEqual(0, inputs["selection"]["visible_declared_projection_count"])

    def test_retrieval_record_mapping_filters_before_source_ids_are_exposed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-runtime-adapter-") as directory:
            graph = Path(directory) / "graph.sqlite"
            _make_graph(graph, projection=None)
            adapter = RuntimeDecisionSourceAdapter(graph)
            visible = adapter.visible_source_assertions_for_retrieval_record(
                graph_assertion_ids=["assertion-1"],
                evidence_ids=["evidence-1"],
                audience="internal_consulting",
                authorization_context=_authorization(),
                known_at="2026-08-09T00:00:00Z",
            )
            denied = adapter.visible_source_assertions_for_retrieval_record(
                graph_assertion_ids=["assertion-1"],
                evidence_ids=["evidence-1"],
                audience="client_approved_external",
                authorization_context=_authorization(audience="client_approved_external"),
                known_at="2026-08-09T00:00:00Z",
            )

        self.assertTrue(visible["monitor_filtered_before_exposure"])
        self.assertEqual(1, visible["matched_source_assertion_count"])
        self.assertEqual(1, visible["visible_source_assertion_count"])
        self.assertEqual(["graph-assertion:assertion-1:evidence:evidence-1"], [
            item["source_assertion_id"] for item in visible["source_assertions"]
        ])
        self.assertEqual(1, denied["matched_source_assertion_count"])
        self.assertEqual(0, denied["visible_source_assertion_count"])
        self.assertEqual(1, denied["monitor_filtered_source_assertion_count"])
        self.assertEqual([], denied["source_assertions"])

    def test_retrieval_selection_can_only_use_registered_source_assertions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-runtime-adapter-") as directory:
            graph = Path(directory) / "graph.sqlite"
            _make_graph(graph, projection=None)
            adapter = RuntimeDecisionSourceAdapter(graph)
            inputs = adapter.inputs_for_use_case(
                use_case=USE_CASE,
                authorization_context=_authorization(),
                as_of="2026-08-09T00:00:00Z",
                known_at="2026-08-09T00:00:00Z",
                allowed_source_assertion_ids=[],
            )
            with self.assertRaisesRegex(RuntimeDecisionSourceError, "not in the source registry"):
                adapter.inputs_for_use_case(
                    use_case=USE_CASE,
                    authorization_context=_authorization(),
                    as_of="2026-08-09T00:00:00Z",
                    known_at="2026-08-09T00:00:00Z",
                    allowed_source_assertion_ids=["unknown-source-assertion"],
                )

        self.assertTrue(inputs["selection"]["retrieval_source_filter_applied"])
        self.assertEqual(0, inputs["selection"]["retrieval_source_assertion_count"])
        self.assertEqual([], inputs["observations"])
        self.assertEqual([], inputs["evidence_packets"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
