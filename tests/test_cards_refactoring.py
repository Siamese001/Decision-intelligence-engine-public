#!/usr/bin/env python3
"""Focused safety contracts for governed facets and card views."""
from __future__ import annotations

import inspect
import json
import sqlite3
import unittest
from copy import deepcopy
from unittest.mock import patch

from dual_pipeline import card_views, facets
from dual_pipeline.core import digest

APPLIED_AT = "2026-07-27T12:00:00Z"
APPROVED_REFERENCE = "approval-cards-refactor-approved"
PENDING_REFERENCE = "approval-cards-refactor-pending"
CARD_ID = "NEE_TEST"
CARD_VIEW_ID = "card-view-test-v1"
GRAPH_SHA256 = "a" * 64
GENERATION_ID = "generation-cards-refactoring-test"


BASE_SCHEMA_SQL = """
CREATE TABLE governance_approval (
  approval_reference TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  active INTEGER NOT NULL CHECK (active IN (0,1))
);

CREATE TABLE controlled_value_registry (
  vocabulary TEXT NOT NULL,
  value TEXT NOT NULL,
  definition TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL,
  PRIMARY KEY (vocabulary,value)
) WITHOUT ROWID;

CREATE TABLE property_registry (
  property_name TEXT NOT NULL,
  applies_to TEXT NOT NULL,
  data_type TEXT NOT NULL,
  required_flag INTEGER NOT NULL,
  controlled_vocabulary TEXT,
  validation_rule TEXT NOT NULL,
  temporal_meaning TEXT,
  searchable_flag INTEGER NOT NULL,
  retrieval_use TEXT NOT NULL,
  PRIMARY KEY (property_name,applies_to)
) WITHOUT ROWID;

CREATE TABLE schema_migration (
  migration_id TEXT PRIMARY KEY,
  checksum_sha256 TEXT NOT NULL,
  description TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  applied_by TEXT NOT NULL,
  rollback_supported INTEGER NOT NULL,
  release_version TEXT NOT NULL
);

CREATE TABLE node (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE claim (
  claim_id TEXT PRIMARY KEY,
  card_id TEXT NOT NULL
);

CREATE TABLE assertion (
  assertion_id TEXT PRIMARY KEY,
  subject_node_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  assertion_type TEXT NOT NULL,
  canonical_status TEXT NOT NULL,
  currentness_state TEXT NOT NULL,
  validation_state TEXT NOT NULL,
  canonical_eligible INTEGER NOT NULL,
  retrieval_eligible INTEGER NOT NULL,
  classification TEXT NOT NULL,
  allowed_audiences_json TEXT NOT NULL CHECK (json_valid(allowed_audiences_json)),
  approval_reference TEXT
);

CREATE VIEW v_decision_safe_assertion AS
SELECT *
FROM assertion
WHERE canonical_status='canonical'
  AND currentness_state='current'
  AND validation_state='accepted_legacy'
  AND canonical_eligible=1
  AND retrieval_eligible=1
  AND approval_reference IS NOT NULL;

CREATE TABLE card (
  card_id TEXT PRIMARY KEY,
  card_name TEXT NOT NULL,
  card_path TEXT NOT NULL,
  canonical_topic TEXT NOT NULL
);

CREATE TABLE canonical_route (
  route_id TEXT PRIMARY KEY,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  card_id TEXT,
  route_role TEXT NOT NULL,
  is_primary INTEGER NOT NULL,
  route_status TEXT NOT NULL,
  source_mechanism TEXT NOT NULL,
  intentional_unrouted_reason TEXT,
  active INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE assertion_evidence (
  assertion_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  evidence_relationship TEXT NOT NULL,
  PRIMARY KEY (assertion_id,evidence_id)
) WITHOUT ROWID;
"""


class CardsRefactoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(BASE_SCHEMA_SQL)
        self._seed_graph()
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_graph(self) -> None:
        self.connection.executemany(
            """INSERT INTO governance_approval
               (approval_reference,status,active)
               VALUES (?,?,?)""",
            [
                (APPROVED_REFERENCE, "approved", 1),
                (PENDING_REFERENCE, "pending", 1),
            ],
        )
        self.connection.execute(
            """INSERT INTO card(card_id,card_name,card_path,canonical_topic)
               VALUES (?,?,?,?)""",
            (CARD_ID, "Test card", "cards/NEE_TEST.md", "test_topic"),
        )
        self.connection.execute(
            "INSERT INTO node(node_id,node_type,active) VALUES (?,?,1)",
            ("node-test", "strategy"),
        )
        self.connection.execute(
            "INSERT INTO claim(claim_id,card_id) VALUES (?,?)",
            ("claim-test", CARD_ID),
        )
        assertions = [
            (
                "assertion-safe-alpha",
                "node-test",
                "SUPPORTS",
                "relationship",
                "canonical",
                "current",
                "accepted_legacy",
                1,
                1,
                "internal",
                json.dumps(["alpha"]),
                APPROVED_REFERENCE,
            ),
            (
                "assertion-unsafe-alpha",
                "node-test",
                "SUPPORTS",
                "relationship",
                "canonical",
                "current",
                "accepted_legacy",
                1,
                1,
                "internal",
                json.dumps(["alpha"]),
                None,
            ),
            (
                "assertion-safe-beta",
                "node-test",
                "SUPPORTS",
                "relationship",
                "canonical",
                "current",
                "accepted_legacy",
                1,
                1,
                "internal",
                json.dumps(["beta"]),
                APPROVED_REFERENCE,
            ),
        ]
        self.connection.executemany(
            """INSERT INTO assertion(
                 assertion_id,subject_node_id,predicate,assertion_type,
                 canonical_status,currentness_state,validation_state,
                 canonical_eligible,retrieval_eligible,classification,
                 allowed_audiences_json,approval_reference
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            assertions,
        )
        self.connection.executemany(
            """INSERT INTO assertion_evidence
               (assertion_id,evidence_id,evidence_relationship)
               VALUES (?,?,'supports')""",
            [
                ("assertion-safe-alpha", "evidence-alpha"),
                ("assertion-unsafe-alpha", "evidence-unsafe"),
                ("assertion-safe-beta", "evidence-beta"),
            ],
        )
        self.connection.executemany(
            """INSERT INTO canonical_route(
                 route_id,object_type,object_id,card_id,route_role,is_primary,
                 route_status,source_mechanism,intentional_unrouted_reason,
                 active,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "route-node",
                    "node",
                    "node-test",
                    CARD_ID,
                    "topic",
                    1,
                    "active",
                    "test",
                    None,
                    1,
                    APPLIED_AT,
                ),
                (
                    "route-assertion",
                    "assertion",
                    "assertion-safe-alpha",
                    CARD_ID,
                    "topic",
                    1,
                    "active",
                    "test",
                    None,
                    1,
                    APPLIED_AT,
                ),
                (
                    "route-intentional-unrouted",
                    "assertion",
                    "assertion-unsafe-alpha",
                    None,
                    "unrouted",
                    0,
                    "intentional_unrouted",
                    "test",
                    "No unique primary card was established.",
                    1,
                    APPLIED_AT,
                ),
                (
                    "route-claim",
                    "claim",
                    "claim-test",
                    CARD_ID,
                    "primary",
                    1,
                    "active",
                    "test",
                    None,
                    1,
                    APPLIED_AT,
                ),
            ],
        )

    def _install_facet_schema(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        facets.install_facet_schema(
            self.connection,
            applied_at=APPLIED_AT,
            applied_by="cards-refactoring-test",
        )
        self.connection.commit()

    def _selector(self, *assertion_ids: str) -> dict[str, object]:
        return {
            "object_types": ["assertion"],
            "route_card_ids": [],
            "explicit_object_ids": list(assertion_ids),
            "facets": [],
            "predicates": [],
            "assertion_types": [],
            "canonical_statuses": ["canonical"],
            "currentness_states": ["current"],
            "retrieval_eligible_only": True,
        }

    def _insert_card_view(
        self,
        selector: dict[str, object],
        *,
        approval_reference: str = APPROVED_REFERENCE,
        selector_digest_value: str | None = None,
        allowed_audiences: list[str] | None = None,
        card_view_id: str = CARD_VIEW_ID,
    ) -> None:
        self.connection.execute(
            """INSERT INTO card_view_definition(
                 card_view_id,card_id,view_version,view_kind,
                 selector_schema_version,selector_json,selector_digest,
                 authority_mode,classification_ceiling,
                 eligible_audiences_json,editorial_owner,view_state,
                 approval_reference,active,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                card_view_id,
                CARD_ID,
                1,
                "explicit_objects",
                "1.0.0",
                json.dumps(selector, sort_keys=True, separators=(",", ":")),
                selector_digest_value or facets.selector_digest(selector),
                "approved_only",
                "internal",
                json.dumps(allowed_audiences or ["alpha"]),
                "knowledge-graph-governance",
                "active",
                approval_reference,
                1,
                APPLIED_AT,
            ),
        )
        self.connection.commit()

    def _authorization_context(self, audience: str) -> dict[str, object]:
        return {
            "classification_clearance": "internal",
            "audiences": [audience],
            "candidate_review": False,
        }

    def _route_snapshot(self) -> list[tuple[object, ...]]:
        return [
            tuple(row)
            for row in self.connection.execute(
                """SELECT route_id,object_type,object_id,card_id,route_role,
                          is_primary,route_status,source_mechanism,
                          intentional_unrouted_reason,active,created_at
                     FROM canonical_route
                    ORDER BY route_id"""
            )
        ]

    def test_schema_install_rolls_back_with_the_callers_transaction(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            facets.install_facet_schema(
                self.connection,
                applied_at=APPLIED_AT,
                applied_by="cards-refactoring-test",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(
                    """INSERT INTO object_facet_assignment(
                         assignment_id,object_type,object_id,vocabulary,value,
                         assignment_method,review_state,derivation_rule_id,
                         rationale,approval_reference,active,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "assignment-invalid",
                        "node",
                        "missing-node",
                        "missing-vocabulary",
                        "missing-value",
                        "explicit",
                        "approved",
                        None,
                        "Deliberately invalid transaction test.",
                        APPROVED_REFERENCE,
                        1,
                        APPLIED_AT,
                    ),
                )
            raise RuntimeError("inject failure after additive schema installation")
        except RuntimeError:
            self.connection.rollback()

        self.assertFalse(facets.schema_is_installed(self.connection))
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM schema_migration WHERE migration_id=?",
                (facets.CARDS_REFACTOR_MIGRATION_ID,),
            ).fetchone()
        )

    def test_derivation_rule_requires_an_active_approved_approval(self) -> None:
        self._install_facet_schema()
        rule_definition = {"kind": "test-rule", "version": 1}
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """INSERT INTO facet_derivation_rule(
                     derivation_rule_id,rule_version,policy_version,
                     rule_definition_json,rule_digest,applicability_policy_digest,
                     approval_reference,active,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    "rule-pending",
                    facets.FACET_RULE_VERSION,
                    facets.CARDS_REFACTOR_POLICY_VERSION,
                    json.dumps(rule_definition),
                    digest(rule_definition),
                    digest({"applies_to": ["assertion"]}),
                    PENDING_REFERENCE,
                    1,
                    APPLIED_AT,
                ),
            )

        self.connection.execute(
            """INSERT INTO facet_derivation_rule(
                 derivation_rule_id,rule_version,policy_version,
                 rule_definition_json,rule_digest,applicability_policy_digest,
                 approval_reference,active,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "rule-approved",
                facets.FACET_RULE_VERSION,
                facets.CARDS_REFACTOR_POLICY_VERSION,
                json.dumps(rule_definition),
                digest(rule_definition),
                digest({"applies_to": ["assertion"]}),
                APPROVED_REFERENCE,
                1,
                APPLIED_AT,
            ),
        )
        self.assertIsNotNone(
            self.connection.execute(
                """SELECT 1 FROM facet_derivation_rule
                    WHERE derivation_rule_id='rule-approved'"""
            ).fetchone()
        )

    def test_facet_assignment_requires_an_active_approved_approval(self) -> None:
        self._install_facet_schema()
        self.connection.execute(
            """INSERT INTO controlled_value_registry
               (vocabulary,value,definition,active,sort_order)
               VALUES ('topic','test_topic','Test topic',1,1)"""
        )
        self.connection.execute(
            """INSERT INTO property_registry(
                 property_name,applies_to,data_type,required_flag,
                 controlled_vocabulary,validation_rule,temporal_meaning,
                 searchable_flag,retrieval_use
               ) VALUES ('topic','assertion','controlled_text_many',0,
                         'topic','active controlled value',NULL,1,
                         'facet filtering only')"""
        )
        rule_definition = {"kind": "test-rule", "version": 1}
        self.connection.execute(
            """INSERT INTO facet_derivation_rule(
                 derivation_rule_id,rule_version,policy_version,
                 rule_definition_json,rule_digest,applicability_policy_digest,
                 approval_reference,active,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "rule-approved",
                facets.FACET_RULE_VERSION,
                facets.CARDS_REFACTOR_POLICY_VERSION,
                json.dumps(rule_definition),
                digest(rule_definition),
                digest({"applies_to": ["assertion"]}),
                APPROVED_REFERENCE,
                1,
                APPLIED_AT,
            ),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """INSERT INTO object_facet_assignment(
                     assignment_id,object_type,object_id,vocabulary,value,
                     assignment_method,review_state,derivation_rule_id,
                     derivation_rule_version,rationale,approval_reference,
                     active,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "assignment-pending",
                    "assertion",
                    "assertion-safe-alpha",
                    "topic",
                    "test_topic",
                    "rule_derived",
                    "approved",
                    "rule-approved",
                    facets.FACET_RULE_VERSION,
                    "Derived for the approval-guard test.",
                    PENDING_REFERENCE,
                    1,
                    APPLIED_AT,
                ),
            )

        self.connection.execute(
            """INSERT INTO object_facet_assignment(
                 assignment_id,object_type,object_id,vocabulary,value,
                 assignment_method,review_state,derivation_rule_id,
                 derivation_rule_version,rationale,approval_reference,
                 active,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "assignment-approved",
                "assertion",
                "assertion-safe-alpha",
                "topic",
                "test_topic",
                "rule_derived",
                "approved",
                "rule-approved",
                facets.FACET_RULE_VERSION,
                "Derived for the approval-guard test.",
                APPROVED_REFERENCE,
                1,
                APPLIED_AT,
            ),
        )
        self.assertIsNotNone(
            self.connection.execute(
                """SELECT 1 FROM object_facet_assignment
                    WHERE assignment_id='assignment-approved'"""
            ).fetchone()
        )

    def test_card_view_requires_an_active_approved_approval(self) -> None:
        self._install_facet_schema()
        selector = self._selector("assertion-safe-alpha")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_card_view(
                selector,
                approval_reference=PENDING_REFERENCE,
                card_view_id="card-view-pending",
            )

        self._insert_card_view(selector)
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT 1 FROM card_view_definition WHERE card_view_id=?",
                (CARD_VIEW_ID,),
            ).fetchone()
        )

    def test_selector_rejects_unknown_fields_and_sql_shaped_values(self) -> None:
        selector = self._selector("assertion-safe-alpha")
        unknown_field = deepcopy(selector)
        unknown_field["where_sql"] = "1=1"
        with self.assertRaises(ValueError):
            card_views.validate_card_selector(unknown_field)

        sql_shaped = deepcopy(selector)
        sql_shaped["predicates"] = ["SUPPORTS') OR 1=1 --"]
        with self.assertRaises(ValueError):
            card_views.validate_card_selector(sql_shaped)

    def test_materialization_rejects_selector_digest_tampering(self) -> None:
        self._install_facet_schema()
        original = self._selector("assertion-safe-alpha")
        tampered = self._selector("assertion-safe-beta")
        self._insert_card_view(
            tampered,
            selector_digest_value=facets.selector_digest(original),
        )
        arguments: dict[str, object] = {
            "card_id": CARD_ID,
            "generation_id": GENERATION_ID,
            "graph_sha256": GRAPH_SHA256,
        }
        if "authorization_context" in inspect.signature(
            card_views.materialize_card_view
        ).parameters:
            arguments["authorization_context"] = self._authorization_context("beta")
        with self.assertRaisesRegex(RuntimeError, "selector digest mismatch"):
            card_views.materialize_card_view(self.connection, **arguments)

    def test_approved_selector_resolves_through_decision_safe_view(self) -> None:
        selector = self._selector(
            "assertion-safe-alpha",
            "assertion-unsafe-alpha",
        )
        self.assertEqual(
            ["assertion-safe-alpha"],
            card_views.resolve_card_selector(
                self.connection,
                selector,
                authority_lane="approved",
            ),
        )

    def test_materialization_is_authorization_filtered_and_deterministic(self) -> None:
        self._install_facet_schema()
        selector = self._selector("assertion-safe-alpha", "assertion-safe-beta")
        self._insert_card_view(
            selector,
            allowed_audiences=["alpha", "beta"],
        )
        alpha = self._authorization_context("alpha")
        beta = self._authorization_context("beta")

        def fake_artifact(
            artifact_type: str,
            artifact_id: str,
            **payload: object,
        ) -> dict[str, object]:
            return {
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
                **payload,
            }

        with patch.object(card_views, "create_artifact", side_effect=fake_artifact):
            alpha_first = card_views.materialize_card_view(
                self.connection,
                card_id=CARD_ID,
                generation_id=GENERATION_ID,
                graph_sha256=GRAPH_SHA256,
                authorization_context=alpha,
                materialized_at=APPLIED_AT,
            )
            alpha_replay = card_views.materialize_card_view(
                self.connection,
                card_id=CARD_ID,
                generation_id=GENERATION_ID,
                graph_sha256=GRAPH_SHA256,
                authorization_context=alpha,
                materialized_at=APPLIED_AT,
            )
            beta_receipt = card_views.materialize_card_view(
                self.connection,
                card_id=CARD_ID,
                generation_id=GENERATION_ID,
                graph_sha256=GRAPH_SHA256,
                authorization_context=beta,
                materialized_at=APPLIED_AT,
            )

        self.assertEqual(alpha_first, alpha_replay)
        self.assertEqual(
            ["assertion-safe-alpha"],
            [
                entry["object_id"]
                for entry in alpha_first["lane_manifests"]["approved"]
            ],
        )
        self.assertEqual(
            ["assertion-safe-beta"],
            [
                entry["object_id"]
                for entry in beta_receipt["lane_manifests"]["approved"]
            ],
        )
        self.assertEqual([], alpha_first["lane_manifests"]["candidate_review"])
        self.assertEqual([], beta_receipt["lane_manifests"]["candidate_review"])
        self.assertNotEqual(
            alpha_first["object_manifest_digest"],
            beta_receipt["object_manifest_digest"],
        )
        alpha_binding = alpha_first.get("authorization_context_digest") or (
            alpha_first.get("extensions", {}).get(
                "com.partner_advisory.nee.authorization-context-digest"
            )
        )
        beta_binding = beta_receipt.get("authorization_context_digest") or (
            beta_receipt.get("extensions", {}).get(
                "com.partner_advisory.nee.authorization-context-digest"
            )
        )
        self.assertEqual(
            digest(card_views.normalize_authorization_context(alpha)),
            alpha_binding,
        )
        self.assertEqual(
            digest(card_views.normalize_authorization_context(beta)),
            beta_binding,
        )

    def test_schema_and_backfill_planning_preserve_legacy_routes(self) -> None:
        route_snapshot = self._route_snapshot()
        claim_snapshot = tuple(
            self.connection.execute(
                "SELECT claim_id,card_id FROM claim WHERE claim_id='claim-test'"
            ).fetchone()
        )

        self._install_facet_schema()
        semantic_operations = facets.build_semantic_domain_operations(self.connection)
        topic_operations, _ = facets.build_topic_operations(self.connection)

        self.assertEqual(route_snapshot, self._route_snapshot())
        self.assertEqual(
            claim_snapshot,
            tuple(
                self.connection.execute(
                    "SELECT claim_id,card_id FROM claim WHERE claim_id='claim-test'"
                ).fetchone()
            ),
        )
        self.assertTrue(semantic_operations)
        self.assertTrue(topic_operations)
        self.assertFalse(
            [
                operation
                for operation in [*semantic_operations, *topic_operations]
                if operation["operation"]
                in {
                    "delete_canonical_route",
                    "retire_canonical_route",
                    "update_canonical_route",
                }
            ]
        )


if __name__ == "__main__":
    unittest.main()
