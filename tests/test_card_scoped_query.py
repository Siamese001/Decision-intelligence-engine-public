#!/usr/bin/env python3
"""Fail-closed query boundary tests for exact card assertion scopes."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import digest
from dual_pipeline.query import (
    build_evidence_packet,
    exact_retrieval,
    hydrate_results,
)


def _graph(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE node (
              node_id TEXT PRIMARY KEY,
              canonical_name TEXT NOT NULL,
              node_type TEXT NOT NULL,
              active INTEGER NOT NULL
            );
            CREATE TABLE assertion (
              assertion_id TEXT PRIMARY KEY,
              subject_node_id TEXT NOT NULL,
              predicate TEXT NOT NULL,
              object_node_id TEXT,
              object_literal TEXT,
              classification TEXT NOT NULL,
              allowed_audiences_json TEXT NOT NULL,
              canonical_status TEXT NOT NULL,
              validation_state TEXT NOT NULL,
              currentness_state TEXT NOT NULL,
              retrieval_eligible INTEGER NOT NULL,
              qualifiers_json TEXT NOT NULL
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
              search_document_id TEXT PRIMARY KEY,
              object_type TEXT NOT NULL,
              object_id TEXT NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              canonical_status TEXT NOT NULL,
              currentness_state TEXT NOT NULL,
              retrieval_eligible INTEGER NOT NULL,
              source_id TEXT NOT NULL,
              assertion_id TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO node VALUES (?,?,?,1)",
            [
                ("node-alpha", "Alpha", "strategy"),
                ("node-beta", "Beta", "strategy"),
            ],
        )
        connection.execute(
            "INSERT INTO source VALUES ('source-test','Test','governed_internal')"
        )
        for suffix in ("alpha", "beta"):
            assertion_id = f"assertion-{suffix}"
            connection.execute(
                """INSERT INTO assertion VALUES
                   (?,?,?,NULL,?,?,?,?,?,?,?,?)""",
                (
                    assertion_id,
                    f"node-{suffix}",
                    "SUPPORTS",
                    f"{suffix} governed card scope",
                    "internal",
                    json.dumps(["shared"]),
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    "{}",
                ),
            )
            connection.execute(
                "INSERT INTO assertion_evidence VALUES (?,?,'supports')",
                (assertion_id, f"evidence-{suffix}"),
            )
            connection.execute(
                """INSERT INTO evidence_item VALUES
                   (?,?,?,'record',?,'direct',1)""",
                (
                    f"evidence-{suffix}",
                    "source-test",
                    "source-version-test",
                    suffix,
                ),
            )
            connection.execute(
                """INSERT INTO search_document VALUES
                   (?,?,?,?,?,'canonical','current',1,'source-test',?)""",
                (
                    f"search-{suffix}",
                    "assertion",
                    assertion_id,
                    f"{suffix} title",
                    f"{suffix} governed card scope",
                    assertion_id,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _retrieval(assertion_id: str) -> dict[str, object]:
    record_id = f"record-{assertion_id}"
    return {
        "lanes": {
            "approved": {
                "results": [
                    {
                        "record_id": record_id,
                        "lane": "approved",
                        "record": {
                            "record_id": record_id,
                            "record_type": "factual_assertion",
                            "extensions": {
                                "com.partner_advisory.nee.graph-object-id": assertion_id,
                                "com.partner_advisory.nee.retrieval-kind": "factual_assertion",
                                "com.partner_advisory.nee.answer-use": "factual_support",
                                "com.partner_advisory.nee.policy-version": "test",
                            },
                        },
                    }
                ]
            },
            "candidate": {"results": []},
            "external_public": {"results": []},
        }
    }


class CardScopedQueryTests(unittest.TestCase):
    def test_exact_and_hydration_scope_cannot_fall_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-card-query-") as directory:
            graph = Path(directory) / "graph.sqlite"
            _graph(graph)
            alpha = exact_retrieval(
                "alpha governed scope",
                graph,
                classification="internal",
                audiences=["shared"],
                retrieval_mode="factual",
                allowed_assertion_ids=["assertion-alpha"],
            )
            self.assertEqual(
                ["assertion-alpha"],
                [item["assertion_id"] for item in alpha["exact_anchors"]],
            )
            empty = exact_retrieval(
                "alpha governed scope",
                graph,
                classification="internal",
                audiences=["shared"],
                retrieval_mode="factual",
                allowed_assertion_ids=[],
            )
            self.assertEqual([], empty["exact_anchors"])
            self.assertEqual(0, empty["assertion_scope_count"])

            hydrated = hydrate_results(
                _retrieval("assertion-beta"),
                graph,
                allowed_assertion_ids=["assertion-alpha"],
            )
            self.assertEqual([], hydrated["strata"]["approved"])
            self.assertEqual([], hydrated["graph_paths"])

    def test_evidence_packet_binds_card_membership_digest(self) -> None:
        card_scope = {
            "card_id": "NEE_TEST",
            "card_view_id": "card-view-test",
            "view_version": 1,
            "selector_digest": "a" * 64,
            "membership_digest": "b" * 64,
            "lanes": {
                "approved": ["assertion-alpha"],
                "candidate_review": [],
            },
        }
        hydrated = {
            "strata": {
                "approved": [],
                "candidate": [],
                "external_public": [],
            },
            "graph_paths": [],
        }
        packet = build_evidence_packet(
            hydrated=hydrated,
            graph_snapshot="graph-snapshot-test",
            exact={"exact_anchors": []},
            retrieval_mode="factual",
            card_scope=card_scope,
        )
        self.assertEqual("b" * 64, packet["card_scope"]["membership_digest"])
        body = {key: value for key, value in packet.items() if key != "packet_digest"}
        self.assertEqual(digest(body), packet["packet_digest"])


if __name__ == "__main__":
    unittest.main()
