#!/usr/bin/env python3
"""Focused contracts for lane-isolated, evidence-bound entity facets."""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from dual_pipeline.contracts import create_artifact  # noqa: E402
from dual_pipeline.core import seal_artifact, sha256_bytes  # noqa: E402
from dual_pipeline.index import (  # noqa: E402
    ENTITY_FACET_MAX_ASSERTIONS,
    ENTITY_FACET_MAX_TEXT_CHARACTERS,
    HybridRetriever,
    build_sidecar,
    render_entity_facet_records,
    render_retrieval_records,
    resolve_retrieval_mode,
    retrieval_metadata_for_record,
    validate_sidecar,
)
from dual_pipeline.query import hydrate_entity_facet_results  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


def _source_record(
    identifier: str,
    *,
    lane: str,
    entity_ids: list[str],
    classification: str,
    audiences: list[str],
    text: str,
    record_type: str = "assertion",
) -> dict[str, object]:
    metadata = retrieval_metadata_for_record(record_type, lane)
    record_id = f"retrieval-source-{identifier}"
    return create_artifact(
        "retrieval-record",
        record_id,
        record_id=record_id,
        record_type=record_type,
        namespace=f"nee.{lane}.assertions",
        lane=lane,
        title=f"Source {identifier}",
        canonical_entities=[f"entity-{entity_ids[0]}"],
        retrieval_text=text,
        retrieval_text_hash=sha256_bytes(text.encode("utf-8")),
        evidence_cluster_id=f"evidence-cluster-{identifier}",
        evidence_ids=[f"evidence-{identifier}"],
        graph_snapshot="graph-snapshot-entity-facet-test",
        classification=classification,
        audiences=audiences,
        lifecycle="current" if lane == "approved" else "candidate",
        effective_date=None,
        action_status="open" if record_type == "action_item" else None,
        **metadata,
        extensions={
            "com.partner_advisory.nee.graph-object-id": f"assertion-{identifier}",
            "com.partner_advisory.nee.graph-entity-ids": entity_ids,
            "com.partner_advisory.nee.graph-evidence-ids": [f"evidence-{identifier}"],
        },
    )


class EntityFacetProjectionTests(unittest.TestCase):
    def test_projection_is_lane_isolated_evidence_bound_and_access_safe(self) -> None:
        records = [
            _source_record(
                "approved-public",
                lane="approved",
                entity_ids=["node-main"],
                classification="public",
                audiences=["shared", "public-audience"],
                text="Main entity is related to the approved public assertion.",
            ),
            _source_record(
                "approved-confidential",
                lane="approved",
                entity_ids=["node-main"],
                classification="confidential",
                audiences=["shared", "restricted-audience"],
                text="Main entity has a confidential approved relationship.",
                record_type="edge",
            ),
            _source_record(
                "candidate-restricted",
                lane="candidate",
                entity_ids=["node-main"],
                classification="restricted",
                audiences=["shared", "candidate-audience"],
                text="Main entity has a candidate-only relationship.",
                record_type="edge",
            ),
            _source_record(
                "candidate-restricted-second",
                lane="candidate",
                entity_ids=["node-main"],
                classification="restricted",
                audiences=["shared", "candidate-second-audience"],
                text="Main entity has a second candidate-only relationship.",
            ),
            _source_record(
                "no-common-one",
                lane="approved",
                entity_ids=["node-no-common"],
                classification="internal",
                audiences=["first"],
                text="No common entity first assertion.",
            ),
            _source_record(
                "no-common-two",
                lane="approved",
                entity_ids=["node-no-common"],
                classification="internal",
                audiences=["second"],
                text="No common entity second assertion.",
            ),
            _source_record(
                "ignored-action",
                lane="approved",
                entity_ids=["node-action"],
                classification="internal",
                audiences=["shared"],
                text="This action must not create an entity facet.",
                record_type="action_item",
            ),
        ]
        entity_names = {
            "node-main": "Main entity",
            "node-no-common": "No common entity",
            "node-action": "Action entity",
        }
        facets = render_entity_facet_records(
            records,
            entity_names=entity_names,
            graph_snapshot="graph-snapshot-entity-facet-test",
        )
        self.assertEqual(
            facets,
            render_entity_facet_records(
                list(reversed(records)),
                entity_names=entity_names,
                graph_snapshot="graph-snapshot-entity-facet-test",
            ),
        )
        self.assertEqual(2, len(facets))
        by_lane = {facet["lane"]: facet for facet in facets}

        approved = by_lane["approved"]
        self.assertEqual("entity_facet", approved["record_type"])
        self.assertEqual("entity_facet", approved["retrieval_kind"])
        self.assertEqual("navigation_only", approved["answer_use"])
        self.assertEqual("confidential", approved["classification"])
        self.assertEqual(["shared"], approved["audiences"])
        self.assertEqual(
            ["assertion-approved-confidential", "assertion-approved-public"],
            approved["extensions"]["com.partner_advisory.nee.graph-assertion-ids"],
        )
        self.assertEqual(
            ["evidence-approved-confidential", "evidence-approved-public"],
            approved["extensions"]["com.partner_advisory.nee.graph-evidence-ids"],
        )
        self.assertNotIn("candidate-only", approved["retrieval_text"])

        candidate = by_lane["candidate"]
        self.assertEqual("restricted", candidate["classification"])
        self.assertEqual(["shared"], candidate["audiences"])
        self.assertEqual(
            [
                "assertion-candidate-restricted",
                "assertion-candidate-restricted-second",
            ],
            candidate["extensions"]["com.partner_advisory.nee.graph-assertion-ids"],
        )
        self.assertNotIn("confidential approved", candidate["retrieval_text"])
        self.assertNotIn(
            "node-no-common",
            {facet["extensions"]["com.partner_advisory.nee.graph-entity-ids"][0] for facet in facets},
        )
        self.assertNotIn(
            "node-action",
            {facet["extensions"]["com.partner_advisory.nee.graph-entity-ids"][0] for facet in facets},
        )

    def test_profile_text_is_deterministically_bounded(self) -> None:
        records = [
            _source_record(
                f"bounded-{index}",
                lane="approved",
                entity_ids=["node-bounded"],
                classification="internal",
                audiences=["shared"],
                text=f"Bounded record {index}. " + ("detail " * 100),
            )
            for index in range(ENTITY_FACET_MAX_ASSERTIONS + 2)
        ]
        facet = render_entity_facet_records(
            records,
            entity_names={"node-bounded": "Bounded entity"},
            graph_snapshot="graph-snapshot-entity-facet-test",
        )[0]
        self.assertLessEqual(
            len(facet["retrieval_text"]), ENTITY_FACET_MAX_TEXT_CHARACTERS
        )
        self.assertEqual(
            ENTITY_FACET_MAX_ASSERTIONS,
            len(facet["extensions"]["com.partner_advisory.nee.entity-facet-profile-record-ids"]),
        )
        self.assertEqual(
            ENTITY_FACET_MAX_ASSERTIONS + 2,
            len(facet["extensions"]["com.partner_advisory.nee.entity-facet-source-record-ids"]),
        )

    def test_sidecar_parity_and_strict_entity_navigation(self) -> None:
        backend = BgeM3TestBackend()
        with tempfile.TemporaryDirectory(prefix="nee-entity-facet-") as directory:
            root = Path(directory)
            graph_path = root / "graph.sqlite"
            self._write_graph(graph_path)
            records = render_retrieval_records(
                graph_path, "graph-snapshot-entity-facet-sidecar"
            )
            facets = [record for record in records if record["record_type"] == "entity_facet"]
            self.assertEqual(4, len(facets))
            self.assertTrue(
                all(
                    "RAW NODE DESCRIPTION MUST NOT APPEAR" not in record["retrieval_text"]
                    for record in facets
                )
            )

            sidecar_path = root / "sidecar.sqlite"
            build_sidecar(
                graph_path=graph_path,
                sidecar_path=sidecar_path,
                generation_id="entity-facet-generation",
                graph_snapshot="graph-snapshot-entity-facet-sidecar",
                backend=backend,
            )
            validation = validate_sidecar(sidecar_path)
            self.assertEqual(len(records), validation["counts"]["records"])
            self.assertEqual(
                validation["counts"]["records"], validation["counts"]["dense"]
            )
            self.assertEqual(
                validation["counts"]["records"], validation["counts"]["sparse_records"]
            )
            self.assertEqual(
                validation["counts"]["records"], validation["counts"]["colbert_records"]
            )

            retriever = HybridRetriever(sidecar_path, backend)
            approved = retriever.retrieve_entity_facets(
                "Alpha entity", "approved", classification="internal", audiences=["shared"]
            )
            candidate = retriever.retrieve_entity_facets(
                "Alpha entity", "candidate", classification="internal", audiences=["shared"]
            )
            self.assertEqual("entity_navigation", approved["retrieval_mode"])
            self.assertEqual(
                "candidate_entity_navigation", candidate["retrieval_mode"]
            )
            self.assertTrue(approved["entity_navigation"])
            self.assertTrue(candidate["entity_navigation"])
            self.assertEqual(2, candidate["authorized_record_count"])
            hydrated_approved = hydrate_entity_facet_results(approved, graph_path)
            hydrated_candidate = hydrate_entity_facet_results(candidate, graph_path)
            self.assertEqual(2, len(hydrated_approved["navigation_anchors"]))
            self.assertEqual(1, hydrated_approved["factual_evidence_count"])
            self.assertEqual(0, hydrated_approved["candidate_evidence_count"])
            self.assertEqual([], hydrated_approved["candidate_evidence"])
            self.assertEqual(2, len(hydrated_candidate["navigation_anchors"]))
            self.assertEqual(0, hydrated_candidate["factual_evidence_count"])
            self.assertEqual(1, hydrated_candidate["candidate_evidence_count"])
            self.assertEqual([], hydrated_candidate["factual_evidence"])
            with self.assertRaisesRegex(ValueError, "strict entity navigation"):
                hydrate_entity_facet_results({"lane": "approved"}, graph_path)
            cross_lane = copy.deepcopy(approved)
            cross_lane["results"][0]["record"]["extensions"][
                "com.partner_advisory.nee.graph-assertion-ids"
            ] = ["assertion-candidate"]
            with self.assertRaisesRegex(ValueError, "DIGEST_MISMATCH"):
                hydrate_entity_facet_results(cross_lane, graph_path)
            cross_lane["results"][0]["record"] = seal_artifact(
                cross_lane["results"][0]["record"]
            )
            with self.assertRaisesRegex(RuntimeError, "lane no longer matches"):
                hydrate_entity_facet_results(cross_lane, graph_path)
            for result in approved["results"] + candidate["results"]:
                self.assertEqual("entity_facet", result["record"]["record_type"])
                self.assertEqual("entity_facet", result["record"]["retrieval_kind"])
                self.assertEqual("navigation_only", result["record"]["answer_use"])
            with self.assertRaisesRegex(ValueError, "approved or candidate"):
                retriever.retrieve_entity_facets(
                    "Alpha entity",
                    "external_public",
                    classification="internal",
                    audiences=["shared"],
                )

    def test_entity_navigation_modes_are_fixed_and_lane_bound(self) -> None:
        name, policy = resolve_retrieval_mode(["approved"], "entity_navigation")
        self.assertEqual("entity_navigation", name)
        self.assertEqual(frozenset({"navigation_only"}), policy["approved"])
        name, policy = resolve_retrieval_mode(
            ["candidate"], "candidate_entity_navigation"
        )
        self.assertEqual("candidate_entity_navigation", name)
        self.assertEqual(frozenset({"navigation_only"}), policy["candidate"])
        with self.assertRaisesRegex(ValueError, "requires lanes"):
            resolve_retrieval_mode(["candidate"], "entity_navigation")

    @staticmethod
    def _write_graph(path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    description TEXT
                );
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_node_id TEXT,
                    object_literal TEXT,
                    scope_text TEXT,
                    canonical_status TEXT NOT NULL,
                    validation_state TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    qualifiers_json TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    allowed_audiences_json TEXT NOT NULL,
                    valid_from TEXT,
                    primary_source_id TEXT
                );
                CREATE TABLE assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    evidence_relationship TEXT NOT NULL
                );
                CREATE TABLE source (
                    source_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_posture TEXT NOT NULL
                );
                CREATE TABLE evidence_item (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_version_id TEXT NOT NULL,
                    locator_kind TEXT NOT NULL,
                    locator_value TEXT NOT NULL,
                    evidence_directness TEXT NOT NULL,
                    source_authority_rank INTEGER NOT NULL
                );
                CREATE TABLE search_document (
                    search_rowid INTEGER PRIMARY KEY,
                    assertion_id TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    title TEXT,
                    body TEXT
                );
                """
            )
            connection.executemany(
                "INSERT INTO node VALUES (?,?,?)",
                [
                    (
                        "node-alpha",
                        "Alpha entity",
                        "RAW NODE DESCRIPTION MUST NOT APPEAR",
                    ),
                    ("node-beta", "Beta entity", "Other raw node description"),
                ],
            )
            assertions = [
                (
                    "assertion-approved",
                    "node-alpha",
                    "relates_to",
                    "node-beta",
                    None,
                    "approved scope",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    "{}",
                    "internal",
                    json.dumps(["shared"]),
                    "2026-07-25",
                    "source-approved",
                ),
                (
                    "assertion-candidate",
                    "node-alpha",
                    "proposes",
                    "node-beta",
                    None,
                    "candidate scope",
                    "candidate",
                    "pending",
                    "current",
                    0,
                    "{}",
                    "internal",
                    json.dumps(["shared"]),
                    "2026-07-25",
                    "source-candidate",
                ),
                (
                    "assertion-architecture-component",
                    "node-alpha",
                    "has_component",
                    None,
                    "candidate architecture component",
                    "architecture scope",
                    "candidate",
                    "pending",
                    "current",
                    0,
                    json.dumps({"dual_pipeline_candidate_type": "architecture_component"}),
                    "internal",
                    json.dumps(["shared"]),
                    "2026-07-25",
                    "source-architecture-component",
                ),
            ]
            connection.executemany(
                "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                assertions,
            )
            connection.executemany(
                "INSERT INTO assertion_evidence VALUES (?,?,?)",
                [
                    ("assertion-approved", "evidence-approved", "supports"),
                    ("assertion-candidate", "evidence-candidate", "supports"),
                    (
                        "assertion-architecture-component",
                        "evidence-architecture-component",
                        "supports",
                    ),
                ],
            )
            connection.executemany(
                "INSERT INTO source VALUES (?,?,?)",
                [
                    ("source-approved", "Approved source", "governed_internal"),
                    ("source-candidate", "Candidate source", "working_notes"),
                    (
                        "source-architecture-component",
                        "Architecture source",
                        "working_notes",
                    ),
                ],
            )
            connection.executemany(
                "INSERT INTO evidence_item VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        "evidence-approved",
                        "source-approved",
                        "version-approved",
                        "record",
                        "approved",
                        "direct",
                        1,
                    ),
                    (
                        "evidence-candidate",
                        "source-candidate",
                        "version-candidate",
                        "record",
                        "candidate",
                        "derivative",
                        5,
                    ),
                    (
                        "evidence-architecture-component",
                        "source-architecture-component",
                        "version-architecture",
                        "record",
                        "architecture",
                        "derivative",
                        5,
                    ),
                ],
            )
            connection.executemany(
                "INSERT INTO search_document VALUES (?,?,?,?,?)",
                [
                    (
                        1,
                        "assertion-approved",
                        "assertion",
                        "Alpha relates to Beta",
                        "Alpha entity relates to Beta entity.",
                    ),
                    (
                        2,
                        "assertion-candidate",
                        "assertion",
                        "Alpha proposes Beta",
                        "Alpha entity proposes Beta entity.",
                    ),
                    (
                        3,
                        "assertion-architecture-component",
                        "assertion",
                        "Alpha architecture component",
                        "Alpha entity has a candidate architecture component.",
                    ),
                ],
            )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
