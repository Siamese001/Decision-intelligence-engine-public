#!/usr/bin/env python3
"""Regression coverage for citation-only source evidence on candidate review."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from dual_pipeline.index import HybridRetriever, build_sidecar
from dual_pipeline.l2 import evaluate_semantic_quality, validate_decision_model_output
from dual_pipeline.query import exact_retrieval, reason_over_packet, validate_answer
from tests.embedding_fixture import BgeM3TestBackend

QUESTION = (
    "What candidate evidence is available for promotion review concerning "
    "transition packages, backlog ownership, and reusable patterns?"
)


def _write_graph(path: Path) -> None:
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
                search_document_id TEXT NOT NULL,
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
        connection.execute(
            "INSERT INTO node VALUES (?,?,?,?)",
            ("node-edo", "EDO program status", "program", 1),
        )
        connection.execute(
            """INSERT INTO assertion
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "assertion-june-source-evidence",
                "node-edo",
                "reports",
                None,
                "Transition packages, backlog ownership, and reusable patterns.",
                "June status-report source excerpt.",
                "candidate",
                "needs_review",
                "current",
                1,
                json.dumps({"dual_pipeline_candidate_type": "source_chunk"}),
                "internal",
                json.dumps(["internal_consulting"]),
                "2026-06-29",
                "source-edo-status-2026-06-29",
            ),
        )
        connection.execute(
            "INSERT INTO assertion_evidence VALUES (?,?,?)",
            (
                "assertion-june-source-evidence",
                "evidence-june-source-evidence",
                "supports",
            ),
        )
        # Source chunks are intentionally excluded from the generic FTS
        # corpus. Candidate review reaches them through a policy-authorized
        # exact anchor and never turns them into ordinary factual retrieval.
        connection.execute(
            """INSERT INTO search_document
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                1,
                "search-june-source-evidence",
                "assertion",
                "assertion-june-source-evidence",
                "June candidate source evidence",
                "Make transition packages explicit. Close the backlog ownership gap. "
                "Turn proof points into reusable patterns.",
                "candidate",
                "current",
                0,
                "source-edo-status-2026-06-29",
                "assertion-june-source-evidence",
            ),
        )
        connection.commit()
    finally:
        connection.close()


class CandidateSourceEvidenceTests(unittest.TestCase):
    def test_candidate_review_surfaces_source_chunks_as_citation_only(self) -> None:
        backend = BgeM3TestBackend()
        with tempfile.TemporaryDirectory(prefix="nee-candidate-source-evidence-") as directory:
            root = Path(directory)
            graph_path = root / "graph.sqlite"
            sidecar_path = root / "sidecar.sqlite"
            _write_graph(graph_path)

            exact = exact_retrieval(
                QUESTION,
                graph_path,
                classification="internal",
                audiences=["internal_consulting"],
                retrieval_mode="candidate_review",
            )
            self.assertEqual(1, len(exact["exact_anchors"]))
            self.assertEqual("citation_only", exact["exact_anchors"][0]["answer_use"])
            self.assertEqual([], exact["bm25_anchors"])

            build_sidecar(
                graph_path=graph_path,
                sidecar_path=sidecar_path,
                generation_id="generation-candidate-source-evidence",
                graph_snapshot="graph-snapshot-candidate-source-evidence",
                backend=backend,
            )
            retrieval = HybridRetriever(sidecar_path, backend).retrieve(
                QUESTION,
                ["approved", "candidate"],
                classification="internal",
                audiences=["internal_consulting"],
                retrieval_mode="candidate_review",
                bm25_assertion_ranks_by_lane=exact["bm25_ranks_by_lane"],
                exact_anchor_assertion_ids_by_lane={
                    "candidate": ["assertion-june-source-evidence"]
                },
            )
            candidates = retrieval["lanes"]["candidate"]["results"]
            self.assertEqual(1, len(candidates))
            self.assertTrue(candidates[0]["exact_anchor_injected"])
            self.assertEqual("source_chunk", candidates[0]["record"]["record_type"])
            self.assertEqual("citation_only", candidates[0]["record"]["answer_use"])

            packet = {
                "approved": [],
                "candidate": [
                    {
                        "record": candidates[0]["record"],
                        "retrieval_metadata": {"answer_use": "citation_only"},
                        "evidence": [
                            {"evidence_id": "evidence-june-source-evidence"}
                        ],
                    }
                ],
                "external_public": [],
                "counterevidence": [],
                "gaps": [],
                "graph_paths": [],
            }
            plan = {
                "query_type": "candidate_promotion_review",
                "materiality": "medium",
            }
            answer = reason_over_packet(plan, packet)
            self.assertEqual([], answer["hypotheses"])
            self.assertEqual(1, len(answer["source_evidence"]))
            self.assertEqual(
                "CANDIDATE_SOURCE_EVIDENCE",
                answer["source_evidence"][0]["label"],
            )
            self.assertTrue(
                validate_decision_model_output(
                    answer,
                    evidence_packet=packet,
                    expected_query_type="candidate_promotion_review",
                ).valid
            )
            self.assertEqual("PASS", validate_answer(answer, packet)["status"])
            self.assertEqual(
                (),
                evaluate_semantic_quality(
                    question=QUESTION,
                    plan=plan,
                    evidence_packet=packet,
                    answer=answer,
                ),
            )

            forged = deepcopy(answer)
            forged["source_evidence"] = []
            forged["hypotheses"] = [
                {
                    "hypothesis": "CANDIDATE: source excerpt presented as a claim.",
                    "citations": [candidates[0]["record"]["record_id"]],
                    "evidence_ids": ["evidence-june-source-evidence"],
                    "lane": "candidate",
                }
            ]
            validation = validate_decision_model_output(
                forged,
                evidence_packet=packet,
                expected_query_type="candidate_promotion_review",
            )
            self.assertFalse(validation.valid)
            self.assertTrue(
                any("answer-use" in issue.message for issue in validation.issues)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
