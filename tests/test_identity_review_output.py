from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import read_json, seal_artifact, sha256_file, utc_now
from dual_pipeline.graph_delta import build_planned_graph_delta
from dual_pipeline.identity import GraphEntityCatalog, resolve_bundle
from dual_pipeline.intake import (
    NEW_INTERNAL_ENTITY_DIGEST_KEY,
    NEW_INTERNAL_ENTITY_KEY,
    build_dispositions,
    build_graph_change_set,
    build_hitl_batches,
    build_ingestion_decision_explanation,
    build_ingestion_decision_ledger,
    render_ingestion_decision_ledger_markdown,
)
from dual_pipeline.normalization import normalize_copilot, normalize_docx_source

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "graph" / "nee_graph.sqlite"
DOCX_FIXTURE = (
    ROOT
    / "contracts"
    / "dual_pipeline"
    / "fixtures"
    / "valid"
    / "docx-source-intake.json"
)


def _profiled_docx(*, intent: str, node_type: str | None) -> dict:
    package = read_json(DOCX_FIXTURE)
    package["source"]["anchor_entity"] = {
        "reported_text": "Organic Program Alpha",
        "stable_identifier": "organic_program_alpha_2026",
        "context": "Source-derived candidate program.",
    }
    package["extensions"]["com.partner_advisory.nee.anchor-entity-profile"] = {
        "stable_identifier": "organic_program_alpha_2026",
        "entity_intent": intent,
        "node_type": node_type,
    }
    return seal_artifact(package)


def _routes(bundle: dict, route: str) -> list[dict]:
    return [
        {
            "subject_id": candidate["candidate_id"],
            "intake_route": route,
            "reason_codes": ["focused identity test"],
        }
        for candidate in bundle["candidates"]
    ]


class IdentityReviewOutputTests(unittest.TestCase):
    def test_exact_catalog_identity_uses_stable_id_name_and_governed_alias(self) -> None:
        catalog = GraphEntityCatalog(GRAPH)
        node_id = "pattern_agentic_control_plan"
        canonical = catalog.entities[node_id].canonical_name
        self.assertEqual(([node_id], "stable_identifier"), catalog.exact("unrelated", node_id))
        self.assertEqual(([node_id], "exact_normalized_name"), catalog.exact(canonical, None))
        self.assertEqual(([node_id], "governed_alias"), catalog.exact("agenticcontrolplan", None))

    def test_legacy_missing_type_reports_possible_duplicates_without_fuzzy_link(self) -> None:
        package = read_json(
            ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "copilot-meeting.json"
        )
        bundle = normalize_copilot(package, run_context="identity-review-output")
        resolutions = resolve_bundle(bundle, GRAPH)
        matt = next(value for value in resolutions if value["reported_name"] == "Matt K")
        review = matt["extensions"]["com.partner_advisory.nee.identity-review"]
        assessment = matt["extensions"]["com.partner_advisory.nee.new-node-assessment"]

        self.assertEqual("HITL_CONFIRM_ENTITY", review["automated_outcome"])
        self.assertEqual("possible_duplicate_evidence", review["confidence"]["band"])
        self.assertEqual(
            "possible_duplicate_evidence_only",
            review["confidence"]["similarity_authority"],
        )
        self.assertEqual("TYPE_REVIEW_REQUIRED", assessment["status"])
        self.assertIn("program", assessment["valid_node_type_choices"])
        create_option = next(
            option for option in review["label_options"] if option["decision"] == "CREATE_NEW"
        )
        self.assertFalse(create_option["available"])
        self.assertIsNone(matt["selected_entity_id"])

    def test_new_node_is_grouped_proposal_then_exact_review_then_operations(self) -> None:
        graph_hash_before = sha256_file(GRAPH)
        bundle = normalize_docx_source(
            _profiled_docx(intent="resolve_or_create", node_type="program"),
            run_context="grouped-new-node",
        )
        resolutions = resolve_bundle(bundle, GRAPH)
        self.assertEqual(
            {"CREATE_NEW_ENTITY_PROPOSAL"},
            {value["outcome"] for value in resolutions},
        )
        assessments = [
            value["extensions"]["com.partner_advisory.nee.new-node-assessment"]
            for value in resolutions
        ]
        self.assertEqual(1, len({value["group_id"] for value in assessments}))
        self.assertEqual(1, len({value["proposal_digest"] for value in assessments}))
        self.assertTrue(all(value["status"] == "PROPOSE_NEW_CANDIDATE" for value in assessments))

        routes = _routes(bundle, "NEW_ENTITY")
        before_dispositions = build_dispositions(bundle, routes, resolutions)
        self.assertEqual(
            {"QUARANTINE"}, {value["disposition"] for value in before_dispositions}
        )
        self.assertIsNone(
            build_graph_change_set(bundle, before_dispositions, resolutions, GRAPH)
        )
        hitl = build_hitl_batches(bundle, routes, resolutions, before_dispositions)
        review_items = [
            item for batch in hitl["review_batches"] for item in batch["items"]
        ]
        self.assertEqual(1, len(review_items))
        item = review_items[0]
        self.assertEqual(
            {candidate["candidate_id"] for candidate in bundle["candidates"]},
            set(item["candidate_ids"]),
        )

        assessment = assessments[0]
        decision = create_artifact(
            "hitl-decision",
            "hitl-organic-program-create",
            decision_id="hitl-organic-program-create",
            review_item_id=item["review_item_id"],
            run_id=bundle["run_id"],
            trace_id=bundle["trace_id"],
            tier=2,
            decision="CREATE_NEW",
            reviewer="identity-test-reviewer",
            decided_at=utc_now(),
            rationale="Approve the exact emitted source-bound proposal.",
            evidence_reviewed=item["evidence_ids"],
            alternatives=[],
            before_state_digest=item["before_state_digest"],
            proposed_after_state_digest=item["proposed_after_state_digest"],
            affected_candidate_ids=item["candidate_ids"],
            challenger_required=False,
            challenger_id=None,
            extensions={
                NEW_INTERNAL_ENTITY_KEY: assessment["proposal"],
                NEW_INTERNAL_ENTITY_DIGEST_KEY: assessment["proposal_digest"],
            },
        )
        after_dispositions = build_dispositions(bundle, routes, resolutions, [decision])
        change_set = build_graph_change_set(
            bundle, after_dispositions, resolutions, GRAPH, [decision]
        )
        self.assertIsNotNone(change_set)
        operations = change_set["operations"]
        self.assertEqual(1, sum(value["operation"] == "create_entity" for value in operations))
        self.assertEqual(
            len(bundle["candidates"]),
            sum(value["operation"] == "add_candidate_assertion" for value in operations),
        )
        self.assertEqual(graph_hash_before, sha256_file(GRAPH))

        graph_delta = build_planned_graph_delta(
            bundle=bundle, change_set=None, graph_path=GRAPH
        )
        ledger = build_ingestion_decision_ledger(
            bundle,
            routes,
            resolutions,
            before_dispositions,
            None,
            graph_delta,
        )
        explanation = build_ingestion_decision_explanation(
            ledger,
            technical_ledger_sha256="a" * 64,
            graph_delta=graph_delta,
        )
        validate_artifact(explanation)
        self.assertEqual(1, explanation["summary"]["decision_group_count"])
        self.assertEqual(1, explanation["summary"]["proposed_new_node_count"])
        self.assertEqual(0, explanation["summary"]["proposed_graph_operation_count"])
        table = render_ingestion_decision_ledger_markdown(explanation)
        self.assertIn("possible duplicate evidence only", table)
        self.assertIn("CREATE_NEW", table)
        self.assertNotIn("margin", table.casefold())
        self.assertNotIn("score", table.casefold())

    def test_source_only_and_invalid_type_fail_closed(self) -> None:
        source_only_bundle = normalize_docx_source(
            _profiled_docx(intent="source_only", node_type=None),
            run_context="source-only",
        )
        source_only = resolve_bundle(source_only_bundle, GRAPH)
        self.assertEqual({"SOURCE_ONLY"}, {value["outcome"] for value in source_only})
        self.assertTrue(
            all(
                value["extensions"]["com.partner_advisory.nee.new-node-assessment"]["proposal"]
                is None
                for value in source_only
            )
        )

        invalid_bundle = normalize_docx_source(
            _profiled_docx(intent="resolve_or_create", node_type="artifact"),
            run_context="invalid-type",
        )
        invalid = resolve_bundle(invalid_bundle, GRAPH)
        self.assertEqual({"HITL_CONFIRM_ENTITY"}, {value["outcome"] for value in invalid})
        self.assertTrue(
            all(
                value["extensions"]["com.partner_advisory.nee.new-node-assessment"]["status"]
                == "TYPE_REVIEW_REQUIRED"
                for value in invalid
            )
        )


class GraphEntityCatalogUnitTests(unittest.TestCase):
    """Focused unit tests for GraphEntityCatalog internals."""

    def test_external_concepts_are_excluded_from_catalog(self) -> None:
        """external_concept nodes must never appear in the identity catalog."""
        from dual_pipeline.core import connect_sqlite

        catalog = GraphEntityCatalog(GRAPH)
        # Verify no entity has node_type == external_concept
        for entity in catalog.entities.values():
            self.assertNotEqual(
                "external_concept",
                entity.node_type,
                f"{entity.entity_id} ({entity.canonical_name}) should not be in catalog",
            )
        # Verify external_concept nodes exist in the graph but not the catalog
        connection = connect_sqlite(GRAPH, readonly=True)
        try:
            external_count = connection.execute(
                "SELECT count(*) FROM node WHERE node_type='external_concept' AND active=1"
            ).fetchone()[0]
        finally:
            connection.close()
        if external_count > 0:
            external_ids = set()
            connection = connect_sqlite(GRAPH, readonly=True)
            try:
                for row in connection.execute(
                    "SELECT node_id FROM node WHERE node_type='external_concept' AND active=1"
                ):
                    external_ids.add(row["node_id"])
            finally:
                connection.close()
            self.assertEqual(
                set(), external_ids & set(catalog.entities.keys()),
                "External concept nodes leaked into catalog",
            )

    def test_catalog_includes_only_active_nodes(self) -> None:
        """All entities in the catalog must have active=1 in the node table."""
        from dual_pipeline.core import connect_sqlite

        catalog = GraphEntityCatalog(GRAPH)
        connection = connect_sqlite(GRAPH, readonly=True)
        try:
            for entity_id in list(catalog.entities.keys())[:50]:
                row = connection.execute(
                    "SELECT active FROM node WHERE node_id=?", (entity_id,)
                ).fetchone()
                self.assertIsNotNone(row, f"Entity {entity_id} not found in node table")
                self.assertEqual(1, row["active"], f"Entity {entity_id} is not active")
        finally:
            connection.close()

    def test_normalized_name_is_deterministic(self) -> None:
        """Re-normalizing canonical_name must produce the same normalized_name."""
        from dual_pipeline.core import normalize_name

        catalog = GraphEntityCatalog(GRAPH)
        for entity in list(catalog.entities.values())[:50]:
            with self.subTest(entity=entity.entity_id):
                self.assertEqual(
                    entity.normalized_name,
                    normalize_name(entity.canonical_name),
                )

    def test_alias_lookup_maps_to_valid_entities(self) -> None:
        """Every entity_id in the aliases dict must exist in entities."""
        catalog = GraphEntityCatalog(GRAPH)
        for normalized_alias, entity_ids in catalog.aliases.items():
            for entity_id in entity_ids:
                self.assertIn(
                    entity_id,
                    catalog.entities,
                    f"Alias {normalized_alias!r} references missing entity {entity_id}",
                )

    def test_minimum_ranked_candidate_score_is_positive(self) -> None:
        from dual_pipeline.identity import MINIMUM_RANKED_CANDIDATE_SCORE
        self.assertGreater(MINIMUM_RANKED_CANDIDATE_SCORE, 0.0)
        self.assertLess(MINIMUM_RANKED_CANDIDATE_SCORE, 1.0)

    def test_catalog_is_read_only(self) -> None:
        before = sha256_file(GRAPH)
        _catalog = GraphEntityCatalog(GRAPH)
        self.assertEqual(before, sha256_file(GRAPH))


if __name__ == "__main__":
    unittest.main()
