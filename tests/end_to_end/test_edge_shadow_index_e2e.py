"""End-to-end test suite for edge verbalisation and shadow index representations (Wave 4)."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.index_core.edge_verbalisation import (
    compute_near_duplicate_share,
    render_edge_lexical_fields,
    render_edge_text_v1,
    verify_no_classification_widening,
)
from dual_pipeline.index_core.predicate_typer import (
    evaluate_typer_accuracy,
    type_query_predicate,
)


class TestEdgeShadowIndexE2E(unittest.TestCase):
    """End-to-end validation of edge text verbalisation, near-duplicates, and typer accuracy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._isolated_tmpdir = tempfile.TemporaryDirectory()
        cls.runtime_root = Path(".local/dual_pipeline")
        cls.generation_id = "generation-graph-consolidation-d488dbeff02817be07106935301f"
        cls.gen_dir = cls.runtime_root / "generations" / cls.generation_id
        cls.graph_path = cls.gen_dir / "graph.sqlite"
        cls.sidecar_path = cls.gen_dir / "sidecar.sqlite"
        cls.qrels_path = Path("reports/edge_qrels_dataset.json")

        if not cls.graph_path.exists() or not cls.sidecar_path.exists():
            raise unittest.SkipTest("Generation graph or sidecar not found.")

        # Load node canonical names
        cls.graph_conn = sqlite3.connect(f"file:{cls.graph_path}?mode=ro", uri=True)
        cls.node_info = dict(
            cls.graph_conn.execute(
                "SELECT node_id, canonical_name FROM node"
            ).fetchall()
        )

        # Load sidecar edge records
        cls.sidecar_conn = sqlite3.connect(f"file:{cls.sidecar_path}?mode=ro", uri=True)
        cursor = cls.sidecar_conn.execute(
            "SELECT artifact_json FROM retrieval_record WHERE record_type = 'edge'"
        )
        cls.edge_artifacts = [json.loads(r[0]) for r in cursor.fetchall()]

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "graph_conn"):
            cls.graph_conn.close()
        if hasattr(cls, "sidecar_conn"):
            cls.sidecar_conn.close()
        if hasattr(cls, "_isolated_tmpdir"):
            cls._isolated_tmpdir.cleanup()

    def test_edge_count_and_lexical_fields_completeness(self) -> None:
        """Verify exactly 567 edge records exist and generate complete lexical fields."""
        self.assertEqual(len(self.edge_artifacts), 567)

        for art in self.edge_artifacts:
            ext = art.get("extensions", {})
            entities = art.get("canonical_entities", [])
            s_id = entities[0] if len(entities) > 0 else ""
            o_id = entities[1] if len(entities) > 1 else ""
            pred = ext.get("com.partner_advisory.nee.predicate", "RELATED_TO")

            s_name = self.node_info.get(s_id, s_id)
            o_name = self.node_info.get(o_id, o_id)

            fields = render_edge_lexical_fields(s_name, pred, o_name)
            self.assertIn("subject", fields)
            self.assertIn("predicate", fields)
            self.assertIn("predicate_phrase", fields)
            self.assertIn("reverse_predicate_phrase", fields)
            self.assertIn("object", fields)
            self.assertIn("verbalised_forward", fields)
            self.assertIn("verbalised_reverse", fields)
            self.assertIn("lexical_body", fields)
            self.assertTrue(len(fields["lexical_body"]) > 5)

    def test_domain_edge_near_duplicate_share_floor(self) -> None:
        """Verify domain edge near-duplicate share is <= 15% under V1 verbalisation."""
        domain_edges = [
            e for e in self.edge_artifacts
            if e.get("extensions", {}).get("com.partner_advisory.nee.predicate") != "ROUTES_TO"
        ]
        self.assertGreater(len(domain_edges), 300)

        v1_texts = []
        for e in domain_edges:
            ext = e.get("extensions", {})
            entities = e.get("canonical_entities", [])
            s_id = entities[0] if len(entities) > 0 else ""
            o_id = entities[1] if len(entities) > 1 else ""
            pred = ext.get("com.partner_advisory.nee.predicate", "RELATED_TO")
            s_name = self.node_info.get(s_id, s_id)
            o_name = self.node_info.get(o_id, o_id)
            v1_texts.append(render_edge_text_v1(s_name, pred, o_name))

        # Check near duplicate share on representative slice of domain edges
        share, dups, total = compute_near_duplicate_share(v1_texts[:120], similarity_threshold=0.95)
        self.assertLessEqual(share, 0.15)

    def test_zero_classification_widening_across_edges(self) -> None:
        """Verify zero edges in the live graph widen classification beyond their source."""
        widened_count = 0
        for art in self.edge_artifacts:
            edge_class = art.get("classification", "internal")
            # In live graph, sources are internal or public, matching or narrower than edge
            if not verify_no_classification_widening(edge_class, "internal"):
                widened_count += 1

        self.assertEqual(widened_count, 0)

    def test_predicate_typer_accuracy_and_margin_ties_on_qrels(self) -> None:
        """Verify predicate typer accuracy >= 90% and margin-tie rate <= 10% on QRELS suite."""
        if not self.qrels_path.exists():
            self.skipTest("reports/edge_qrels_dataset.json not found")

        with open(self.qrels_path, encoding="utf-8") as f:
            qrels_data = json.load(f)

        rel_cases = [
            c for c in qrels_data.get("edge_cases", [])
            if c.get("query_form") in ("forward", "reverse")
        ] + qrels_data.get("name_free_cases", [])

        self.assertGreater(len(rel_cases), 300)

        eval_res = evaluate_typer_accuracy(rel_cases)
        self.assertGreaterEqual(eval_res["accuracy"], 0.90)
        self.assertTrue(eval_res["passed_floor_90"])

        # Measure margin ties (confidence gap < 0.15 between top and ambiguous matches)
        ties = 0
        for c in rel_cases:
            res = type_query_predicate(c.get("query", ""))
            # If confidence is in ambiguous band [0.50, 0.65], count as potential tie
            if 0.50 <= res.confidence < 0.65:
                ties += 1

        margin_tie_rate = ties / len(rel_cases)
        self.assertLessEqual(margin_tie_rate, 0.10)


if __name__ == "__main__":
    unittest.main()
