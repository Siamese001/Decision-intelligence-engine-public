from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import ROOT, connect_sqlite, sha256_file
from dual_pipeline.label_quality import (
    LabelQualityError,
    apply_approved_deterministic_label_migration_candidate,
    approved_validation_state,
    audit_graph_labels,
    build_deterministic_label_migration_candidate_plan,
    build_legacy_label_migration_plan,
    canonical_audiences_json,
    canonicalize_audiences,
    compare_label_quality,
    validate_deterministic_label_migration_candidate_plan,
)
from tools.validate_dual_pipeline_contracts import SchemaRegistry, validate_artifact

SEED_GRAPH = ROOT / "graph" / "nee_graph.sqlite"
MIGRATION = ROOT / "migrations" / "0010_graph_labeling_hardening" / "schema.sql"


class GraphLabelQualityTests(unittest.TestCase):
    @staticmethod
    def _harden_graph(graph: Path) -> None:
        graph.write_bytes(SEED_GRAPH.read_bytes())
        connection = connect_sqlite(graph)
        try:
            connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        finally:
            connection.close()

    @staticmethod
    def _approval_record(plan: dict) -> dict:
        return {
            "approval_id": "approval-graph-label-candidate-test",
            "approval_reference": "AD-GRAPH-LABEL-CANDIDATE-TEST-001",
            "approval_type": "release_implementation",
            "scope_json": json.dumps(
                {
                    "base_graph_sha256": plan["base_graph_sha256"],
                    "graph_label_candidate_plan_digest": plan["canonical_digest"][
                        "value"
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "baseline_zip_sha256": None,
            "baseline_graph_sha256": plan["base_graph_sha256"],
            "proposal_sha256": "a" * 64,
            "decision_record_sha256": None,
            "card_proposal_sha256": None,
            "gate_o1_sha256": None,
            "approval_statement_sha256": "b" * 64,
            "approved_at": "2026-08-06T00:00:00Z",
            "expires_at": None,
            "status": "approved",
            "approved_by": "test-governance-owner",
            "release_version": "1.0.0",
            "active": 1,
            "created_at": "2026-08-06T00:00:00Z",
        }

    def test_audiences_are_sorted_unique_and_fail_closed(self) -> None:
        self.assertEqual(
            ("internal_consulting", "nee-engineering"),
            canonicalize_audiences(
                '["nee-engineering","internal_consulting","nee-engineering"]'
            ),
        )
        self.assertEqual(
            '["internal_consulting","nee-engineering"]',
            canonical_audiences_json(["nee-engineering", "internal_consulting"]),
        )
        for invalid in ("not-json", "[]", '[" leading-space"]', '{"x":1}'):
            with self.subTest(invalid=invalid), self.assertRaises(LabelQualityError):
                canonicalize_audiences(invalid)

    def test_audit_is_read_only_and_bound_to_graph_hash(self) -> None:
        before = sha256_file(SEED_GRAPH)
        report = audit_graph_labels(SEED_GRAPH)
        self.assertEqual(before, report["graph"]["sha256"])
        self.assertEqual(before, sha256_file(SEED_GRAPH))
        self.assertEqual(0, report["provider_call_count"])
        self.assertEqual(0, report["graph_mutation_count"])
        self.assertIn(report["status"], {"PASS", "PASS_WITH_LEGACY_DEBT"})
        self.assertGreater(report["metrics"]["assertion_count"], 0)
        self.assertGreater(
            report["metrics"]["noncanonical_evidence_directness_count"], 0
        )

    def test_strict_audit_fails_on_known_legacy_debt(self) -> None:
        report = audit_graph_labels(SEED_GRAPH, strict=True)
        self.assertEqual("FAIL", report["status"])
        self.assertGreater(report["blocking_violation_count"], 0)

    def test_migration_plan_is_deterministic_and_review_first(self) -> None:
        first = build_legacy_label_migration_plan(SEED_GRAPH)
        second = build_legacy_label_migration_plan(SEED_GRAPH)
        self.assertEqual(first["plan_digest"], second["plan_digest"])
        self.assertEqual("PROPOSED_REVIEW_ONLY", first["status"])
        self.assertFalse(first["mutation_authorized"])
        self.assertGreater(first["summary"]["hitl_required_count"], 0)
        self.assertEqual(
            first["summary"]["total_item_count"],
            len(first["items"]),
        )

    def test_deterministic_candidate_plan_is_exact_read_only_and_excludes_hitl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph.sqlite"
            graph.write_bytes(SEED_GRAPH.read_bytes())
            before = sha256_file(graph)
            review_plan = build_legacy_label_migration_plan(graph)
            first = build_deterministic_label_migration_candidate_plan(graph)
            second = build_deterministic_label_migration_candidate_plan(graph)

            self.assertEqual(first, second)
            self.assertEqual(before, sha256_file(graph))
            self.assertEqual("CANDIDATE_ONLY_NOT_AUTHORIZED", first["status"])
            self.assertTrue(first["candidate_only"])
            self.assertFalse(first["mutation_authorized"])
            self.assertEqual(0, first["provider_call_count"])
            self.assertEqual(0, first["graph_mutation_count"])
            self.assertEqual(
                review_plan["summary"]["deterministic_map_count"],
                first["summary"]["deterministic_operation_count"],
            )
            self.assertEqual(
                review_plan["summary"]["hitl_required_count"],
                first["summary"]["excluded_hitl_required_count"],
            )
            self.assertTrue(
                all(
                    operation["record_type"] in {"source", "evidence", "assertion"}
                    for operation in first["operations"]
                )
            )
            registry = SchemaRegistry(ROOT / "contracts" / "dual_pipeline" / "schemas")
            registry.load()
            validate_artifact(first, registry)
            self.assertEqual(first, validate_deterministic_label_migration_candidate_plan(graph, first))

            tampered = copy.deepcopy(first)
            tampered["operations"][0]["record_id"] = "source-tampered"
            with self.assertRaises(LabelQualityError):
                validate_deterministic_label_migration_candidate_plan(graph, tampered)

    def test_approved_candidate_adapter_never_mutates_source_or_legacy_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.sqlite"
            candidate = Path(temporary) / "candidate.sqlite"
            source.write_bytes(SEED_GRAPH.read_bytes())
            plan = build_deterministic_label_migration_candidate_plan(source)
            candidate.write_bytes(source.read_bytes())
            approval = self._approval_record(plan)
            source_before = sha256_file(source)
            candidate_before = sha256_file(candidate)

            with self.assertRaises(LabelQualityError):
                apply_approved_deterministic_label_migration_candidate(
                    source,
                    source,
                    plan=plan,
                    approval_record=approval,
                    run_id="run-graph-label-candidate-test",
                )
            self.assertEqual(source_before, sha256_file(source))

            invalid_approval = copy.deepcopy(approval)
            invalid_approval["scope_json"] = json.dumps(
                {
                    "base_graph_sha256": plan["base_graph_sha256"],
                    "graph_label_candidate_plan_digest": "0" * 64,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            with self.assertRaises(LabelQualityError):
                apply_approved_deterministic_label_migration_candidate(
                    source,
                    candidate,
                    plan=plan,
                    approval_record=invalid_approval,
                    run_id="run-graph-label-candidate-test",
                )
            self.assertEqual(candidate_before, sha256_file(candidate))

            receipt = apply_approved_deterministic_label_migration_candidate(
                source,
                candidate,
                plan=plan,
                approval_record=approval,
                run_id="run-graph-label-candidate-test",
            )

            self.assertEqual(source_before, sha256_file(source))
            self.assertNotEqual(candidate_before, sha256_file(candidate))
            self.assertEqual("COMMITTED", receipt["status"])
            self.assertTrue(receipt["candidate_only"])
            self.assertTrue(receipt["schema_migration"]["applied"])
            self.assertEqual(0, receipt["provider_call_count"])
            self.assertEqual(len(plan["operations"]), receipt["operation_count"])

            source_connection = connect_sqlite(source, readonly=True)
            candidate_connection = connect_sqlite(candidate, readonly=True)
            try:
                for table, columns in (
                    ("source", "source_id,source_posture"),
                    ("evidence_item", "evidence_id,evidence_directness"),
                    (
                        "assertion",
                        "assertion_id,confidence_label,confidence_score,validation_state",
                    ),
                ):
                    expected = source_connection.execute(
                        f"SELECT {columns} FROM {table} ORDER BY 1"
                    ).fetchall()
                    actual = candidate_connection.execute(
                        f"SELECT {columns} FROM {table} ORDER BY 1"
                    ).fetchall()
                    self.assertEqual([tuple(row) for row in expected], [tuple(row) for row in actual])
                self.assertEqual(
                    len(plan["operations"]),
                    candidate_connection.execute(
                        "SELECT count(*) FROM label_mapping_event"
                    ).fetchone()[0],
                )
                self.assertEqual(
                    len(plan["operations"]),
                    candidate_connection.execute(
                        """SELECT count(*) FROM audit_event
                           WHERE actor_or_process='nee-graph-label-deterministic-candidate'"""
                    ).fetchone()[0],
                )
                self.assertEqual(
                    0,
                    candidate_connection.execute(
                        """SELECT count(*) FROM assertion_label_state
                           WHERE validation_state<>'accepted_legacy'"""
                    ).fetchone()[0],
                )
            finally:
                candidate_connection.close()
                source_connection.close()

    def test_no_regression_gate_detects_growth(self) -> None:
        before = audit_graph_labels(SEED_GRAPH)
        after = copy.deepcopy(before)
        after["graph"]["sha256"] = "f" * 64
        metric = "missing_nonlegacy_extraction_run_count"
        after["metrics"][metric] += 1
        result = compare_label_quality(before, after)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(metric, result["regressions"][0]["metric"])

    def test_additive_schema_enables_approved_state_and_guards_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph.sqlite"
            graph.write_bytes(SEED_GRAPH.read_bytes())
            connection = connect_sqlite(graph)
            try:
                connection.executescript(MIGRATION.read_text(encoding="utf-8"))
                self.assertEqual("approved", approved_validation_state(connection))
                policy = connection.execute(
                    """SELECT policy_version,enforcement_state
                       FROM label_policy_version
                       WHERE policy_id='nee-graph-labeling'"""
                ).fetchone()
                self.assertEqual(("1.0.0", "shadow"), tuple(policy))
                source_id = connection.execute(
                    "SELECT source_id FROM source ORDER BY source_id LIMIT 1"
                ).fetchone()[0]
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """INSERT INTO source_label_state
                           (source_id,source_kind,source_format,source_origin,
                            source_posture_code,source_posture_note,mapping_state,
                            mapping_rule_id,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            source_id,
                            "governed_intake",
                            "json",
                            "internal",
                            "invented_posture",
                            None,
                            "native",
                            None,
                            "2026-07-27T00:00:00Z",
                        ),
                    )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
