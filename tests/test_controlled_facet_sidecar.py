#!/usr/bin/env python3
"""Focused tests for controlled-facet sidecar metadata and filtering."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from dual_pipeline.index import (  # noqa: E402
    OBJECT_FACET_METADATA_KEYS,
    HybridRetriever,
    build_sidecar,
    validate_sidecar,
)
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


def _write_graph(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL
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
            "INSERT INTO node VALUES (?,?)",
            [
                ("node-alpha", "Alpha platform"),
                ("node-beta", "Beta capability"),
                ("node-gamma", "Gamma delivery"),
                ("node-delta", "Delta outcome"),
            ],
        )
        connection.executemany(
            """INSERT INTO assertion
               (assertion_id,subject_node_id,predicate,object_node_id,object_literal,
                scope_text,canonical_status,validation_state,currentness_state,
                retrieval_eligible,qualifiers_json,classification,
                allowed_audiences_json,valid_from,primary_source_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "assertion-alpha",
                    "node-alpha",
                    "USES",
                    "node-beta",
                    None,
                    "Alpha uses a governed platform capability.",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    "{}",
                    "internal",
                    json.dumps(["shared"]),
                    "2026-07-27",
                    "source-alpha",
                ),
                (
                    "assertion-gamma",
                    "node-gamma",
                    "DELIVERS",
                    "node-delta",
                    None,
                    "Gamma delivers a governed portfolio outcome.",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    "{}",
                    "internal",
                    json.dumps(["shared"]),
                    "2026-07-27",
                    "source-gamma",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO assertion_evidence VALUES (?,?,?)",
            [
                ("assertion-alpha", "evidence-alpha", "supports"),
                ("assertion-gamma", "evidence-gamma", "supports"),
            ],
        )
        connection.executemany(
            """INSERT INTO search_document
               (search_rowid,assertion_id,object_type,title,body)
               VALUES (?,?,?,?,?)""",
            [
                (
                    1,
                    "assertion-alpha",
                    "assertion",
                    "Alpha platform capability",
                    "Alpha uses a governed platform capability.",
                ),
                (
                    2,
                    "assertion-gamma",
                    "assertion",
                    "Gamma portfolio delivery",
                    "Gamma delivers a governed portfolio outcome.",
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _install_assignments(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE controlled_value_registry (
                vocabulary TEXT NOT NULL,
                value TEXT NOT NULL,
                active INTEGER NOT NULL,
                PRIMARY KEY (vocabulary,value)
            ) WITHOUT ROWID;
            CREATE TABLE object_facet_assignment (
                assignment_id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                vocabulary TEXT NOT NULL,
                value TEXT NOT NULL,
                assignment_method TEXT NOT NULL,
                review_state TEXT NOT NULL,
                active INTEGER NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE legacy_claim_assertion_map (
                claim_id TEXT PRIMARY KEY,
                assertion_id TEXT NOT NULL
            ) WITHOUT ROWID;
            """
        )
        connection.executemany(
            "INSERT INTO controlled_value_registry VALUES (?,?,?)",
            [
                ("semantic_domain", "architecture", 1),
                ("semantic_domain", "delivery", 1),
                ("semantic_domain", "inactive-domain", 0),
                ("topic", "platform", 1),
                ("topic", "portfolio", 1),
                ("topic", "pending-review", 1),
            ],
        )
        connection.execute(
            "INSERT INTO legacy_claim_assertion_map VALUES (?,?)",
            ("claim-gamma", "assertion-gamma"),
        )
        connection.executemany(
            """INSERT INTO object_facet_assignment
               (assignment_id,object_type,object_id,vocabulary,value,
                assignment_method,review_state,active)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (
                    "facet-alpha-domain",
                    "assertion",
                    "assertion-alpha",
                    "semantic_domain",
                    "architecture",
                    "explicit",
                    "approved",
                    1,
                ),
                (
                    "facet-alpha-topic",
                    "node",
                    "node-alpha",
                    "topic",
                    "platform",
                    "rule_derived",
                    "approved",
                    1,
                ),
                (
                    "facet-alpha-pending",
                    "node",
                    "node-alpha",
                    "topic",
                    "pending-review",
                    "rule_derived",
                    "needs_review",
                    1,
                ),
                (
                    "facet-gamma-topic",
                    "node",
                    "node-gamma",
                    "topic",
                    "portfolio",
                    "rule_derived",
                    "approved",
                    1,
                ),
                (
                    "facet-gamma-domain",
                    "claim",
                    "claim-gamma",
                    "semantic_domain",
                    "delivery",
                    "inherited",
                    "approved",
                    1,
                ),
                (
                    "facet-gamma-inactive-value",
                    "assertion",
                    "assertion-gamma",
                    "semantic_domain",
                    "inactive-domain",
                    "explicit",
                    "approved",
                    1,
                ),
                (
                    "facet-gamma-inactive-assignment",
                    "assertion",
                    "assertion-gamma",
                    "topic",
                    "platform",
                    "explicit",
                    "approved",
                    0,
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _representation_snapshot(path: Path) -> dict[str, list[tuple[object, ...]]]:
    connection = sqlite3.connect(path)
    try:
        return {
            "text_hashes": list(
                connection.execute(
                    """SELECT record_id,retrieval_text_hash
                         FROM retrieval_record ORDER BY record_id"""
                )
            ),
            "dense": list(
                connection.execute(
                    "SELECT record_id,dimensions,vector FROM dense_vector ORDER BY record_id"
                )
            ),
            "sparse": list(
                connection.execute(
                    """SELECT record_id,token,weight
                         FROM sparse_weight ORDER BY record_id,token"""
                )
            ),
            "colbert": list(
                connection.execute(
                    """SELECT record_id,position,token,dimensions,vector
                         FROM colbert_token ORDER BY record_id,position"""
                )
            ),
        }
    finally:
        connection.close()


def _assertion_ids(result: dict[str, object]) -> set[str]:
    lanes = result["lanes"]
    assert isinstance(lanes, dict)
    approved = lanes["approved"]
    assert isinstance(approved, dict)
    return {
        str(item["record"]["extensions"]["com.partner_advisory.nee.graph-object-id"])
        for item in approved["results"]
    }


class ControlledFacetSidecarTests(unittest.TestCase):
    def test_facets_are_manifest_bound_filterable_and_embedding_neutral(self) -> None:
        backend = BgeM3TestBackend()
        with tempfile.TemporaryDirectory(prefix="nee-controlled-facets-") as directory:
            root = Path(directory)
            graph = root / "graph.sqlite"
            baseline_sidecar = root / "baseline-sidecar.sqlite"
            faceted_sidecar = root / "faceted-sidecar.sqlite"
            _write_graph(graph)
            build_sidecar(
                graph_path=graph,
                sidecar_path=baseline_sidecar,
                generation_id="generation-before-facets",
                graph_snapshot="snapshot-before-facets",
                backend=backend,
            )
            _install_assignments(graph)
            receipt = build_sidecar(
                graph_path=graph,
                sidecar_path=faceted_sidecar,
                generation_id="generation-after-facets",
                graph_snapshot="snapshot-after-facets",
                backend=backend,
            )

            self.assertEqual(
                _representation_snapshot(baseline_sidecar),
                _representation_snapshot(faceted_sidecar),
            )
            validation = validate_sidecar(faceted_sidecar)
            self.assertTrue(validation["object_facet_filtering_supported"])
            self.assertEqual(4, validation["object_facet_assignment_count"])
            self.assertEqual(4, receipt["object_facet_assignment_count"])
            self.assertEqual(
                validation["object_facet_manifest_digest"],
                receipt["object_facet_manifest_digest"],
            )

            connection = sqlite3.connect(faceted_sidecar)
            try:
                projected = list(
                    connection.execute(
                        """SELECT object_type,object_id,vocabulary,value,review_state
                             FROM retrieval_record_object_facet
                            ORDER BY assignment_id"""
                    )
                )
            finally:
                connection.close()
            self.assertEqual(
                [
                    (
                        "assertion",
                        "assertion-alpha",
                        "semantic_domain",
                        "architecture",
                        "approved",
                    ),
                    ("node", "node-alpha", "topic", "platform", "approved"),
                    (
                        "claim",
                        "claim-gamma",
                        "semantic_domain",
                        "delivery",
                        "approved",
                    ),
                    ("node", "node-gamma", "topic", "portfolio", "approved"),
                ],
                projected,
            )

            retriever = HybridRetriever(faceted_sidecar, backend)
            common = {
                "classification": "internal",
                "audiences": ["shared"],
                "retrieval_mode": "factual",
            }
            architecture = retriever.retrieve(
                "governed capability and portfolio outcome",
                ["approved"],
                facet_filters={"semantic_domain": ["architecture"]},
                **common,
            )
            self.assertEqual({"assertion-alpha"}, _assertion_ids(architecture))
            self.assertEqual(
                "and_across_vocabularies_or_within_values",
                architecture["lanes"]["approved"]["facet_filter_semantics"],
            )
            combined = retriever.retrieve(
                "governed capability and portfolio outcome",
                ["approved"],
                facet_filters={
                    "semantic_domain": ["architecture"],
                    "topic": ["platform"],
                },
                **common,
            )
            self.assertEqual({"assertion-alpha"}, _assertion_ids(combined))
            either_domain = retriever.retrieve(
                "governed capability and portfolio outcome",
                ["approved"],
                facet_filters={
                    "semantic_domain": ["architecture", "delivery"],
                },
                **common,
            )
            self.assertEqual(
                {"assertion-alpha", "assertion-gamma"},
                _assertion_ids(either_domain),
            )
            exact_scope = retriever.retrieve(
                "governed capability and portfolio outcome",
                ["approved"],
                allowed_assertion_ids=["assertion-gamma"],
                **common,
            )
            self.assertEqual({"assertion-gamma"}, _assertion_ids(exact_scope))
            self.assertEqual(1, exact_scope["assertion_scope_count"])
            self.assertIsNotNone(exact_scope["assertion_scope_digest"])
            empty_scope = retriever.retrieve(
                "governed capability and portfolio outcome",
                ["approved"],
                allowed_assertion_ids=[],
                **common,
            )
            self.assertEqual(set(), _assertion_ids(empty_scope))
            self.assertEqual(0, empty_scope["assertion_scope_count"])
            pending = retriever.retrieve(
                "governed capability and portfolio outcome",
                ["approved"],
                facet_filters={"topic": ["pending-review"]},
                **common,
            )
            self.assertEqual(set(), _assertion_ids(pending))
            inactive = retriever.retrieve(
                "governed capability and portfolio outcome",
                ["approved"],
                facet_filters={"semantic_domain": ["inactive-domain"]},
                **common,
            )
            self.assertEqual(set(), _assertion_ids(inactive))

    def test_facet_filtering_fails_closed_for_a_legacy_sidecar(self) -> None:
        backend = BgeM3TestBackend()
        with tempfile.TemporaryDirectory(prefix="nee-legacy-facets-") as directory:
            root = Path(directory)
            graph = root / "graph.sqlite"
            sidecar = root / "legacy-sidecar.sqlite"
            _write_graph(graph)
            build_sidecar(
                graph_path=graph,
                sidecar_path=sidecar,
                generation_id="legacy-facet-generation",
                graph_snapshot="legacy-facet-snapshot",
                backend=backend,
            )
            connection = sqlite3.connect(sidecar)
            try:
                connection.execute("DROP TABLE retrieval_record_object_facet")
                placeholders = ",".join("?" for _ in OBJECT_FACET_METADATA_KEYS)
                connection.execute(
                    f"DELETE FROM metadata WHERE key IN ({placeholders})",
                    list(OBJECT_FACET_METADATA_KEYS),
                )
                connection.commit()
            finally:
                connection.close()

            validation = validate_sidecar(sidecar)
            self.assertFalse(validation["object_facet_filtering_supported"])
            retriever = HybridRetriever(sidecar, backend)
            unfiltered = retriever.retrieve(
                "governed platform",
                ["approved"],
                classification="internal",
                audiences=["shared"],
                retrieval_mode="factual",
            )
            self.assertTrue(_assertion_ids(unfiltered))
            with self.assertRaisesRegex(
                RuntimeError, "legacy sidecar without controlled-facet metadata"
            ):
                retriever.retrieve(
                    "governed platform",
                    ["approved"],
                    classification="internal",
                    audiences=["shared"],
                    retrieval_mode="factual",
                    facet_filters={"topic": ["platform"]},
                )

    def test_facet_manifest_tampering_is_rejected(self) -> None:
        backend = BgeM3TestBackend()
        with tempfile.TemporaryDirectory(prefix="nee-facet-manifest-") as directory:
            root = Path(directory)
            graph = root / "graph.sqlite"
            sidecar = root / "sidecar.sqlite"
            _write_graph(graph)
            _install_assignments(graph)
            build_sidecar(
                graph_path=graph,
                sidecar_path=sidecar,
                generation_id="facet-manifest-generation",
                graph_snapshot="facet-manifest-snapshot",
                backend=backend,
            )
            connection = sqlite3.connect(sidecar)
            try:
                connection.execute(
                    """UPDATE retrieval_record_object_facet
                          SET value='tampered'
                        WHERE assignment_id='facet-alpha-domain'"""
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(
                RuntimeError, "object-facet manifest binding mismatch"
            ):
                validate_sidecar(sidecar)


if __name__ == "__main__":
    unittest.main(verbosity=2)
