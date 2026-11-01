#!/usr/bin/env python3
"""Wave 19 unit tests: C0 Retrieval & Evidence Packet Enrichment."""
from __future__ import annotations

import unittest

from dual_pipeline.query import build_evidence_packet


class Wave19C0EnrichmentTests(unittest.TestCase):
    def test_bidirectional_counterevidence_citations(self) -> None:
        hydrated = {
            "strata": {
                "approved": [],
                "candidate": [],
                "external_public": [],
            },
            "graph_paths": [],
        }
        exact = {
            "exact_anchors": [{"search_document_id": "doc_1"}, {"search_document_id": "doc_1"}],
            "bm25_anchors": [{"search_document_id": "doc_2"}, {"search_document_id": "doc_2"}],
        }
        connected_evidence_map = {
            "map_id": "map_test_1",
            "canonical_digest": {"value": "digest_123"},
            "requested_hops": 2,
            "max_hop_reached": 2,
            "metrics": {},
            "counterevidence": [
                {
                    "record_id": "rec_A",
                    "target_record_id": "rec_B",
                    "source_record_id": "rec_C",
                    "kind": "contradicting_evidence",
                },
                {
                    "record_id": "rec_X",
                    "kind": "assertion_conflict",
                },
            ],
            "gaps": ["gap_1", "gap_2"],
        }
        packet = build_evidence_packet(
            hydrated=hydrated,
            graph_snapshot="test_snapshot",
            exact=exact,
            retrieval_mode="factual",
            connected_evidence_map=connected_evidence_map,
        )

        # Verify anchor deduplication
        self.assertEqual(packet["exact_anchor_ids"], ["doc_1"])
        self.assertEqual(packet["bm25_anchor_ids"], ["doc_2"])

        # Verify bidirectional citations
        self.assertEqual(len(packet["counterevidence"]), 2)
        ce_first = packet["counterevidence"][0]
        self.assertEqual(ce_first["citations"], ["rec_A", "rec_B", "rec_C"])
        self.assertIn("Governed contradicting source evidence", ce_first["statement"])

        ce_second = packet["counterevidence"][1]
        self.assertEqual(ce_second["citations"], ["rec_X"])
        self.assertIn("unresolved assertion conflict", ce_second["statement"])

        # Verify gaps
        self.assertIn("gap_1", packet["gaps"])
        self.assertIn("gap_2", packet["gaps"])

    def test_empty_connected_evidence_map(self) -> None:
        hydrated = {
            "strata": {
                "approved": [],
                "candidate": [],
                "external_public": [],
            },
            "graph_paths": [],
        }
        exact = {
            "exact_anchors": [],
        }
        packet = build_evidence_packet(
            hydrated=hydrated,
            graph_snapshot="test_snapshot",
            exact=exact,
            retrieval_mode="strategic",
            connected_evidence_map=None,
        )
        self.assertEqual(packet["counterevidence"], [])
        self.assertEqual(packet["exact_anchor_ids"], [])
        self.assertEqual(packet["bm25_anchor_ids"], [])
        self.assertTrue(len(packet["gaps"]) >= 3)
        self.assertEqual(packet["retrieval_mode"], "strategic")


if __name__ == "__main__":
    unittest.main()
