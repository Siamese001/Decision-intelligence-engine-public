#!/usr/bin/env python3
"""Unit test suite verifying 14-table delta accounting and zero-op idempotency in dual_pipeline.graph_delta."""
from __future__ import annotations

import unittest

from dual_pipeline.graph_delta import (
    _COMPONENTS,
    _SIDECAR_COMPONENTS,
    format_markdown_mutation_table,
)


class GraphDelta14TablesTests(unittest.TestCase):
    """Test suite ensuring mandatory 14-table accounting and idempotency reporting."""

    def test_components_and_sidecars_sum_to_14(self) -> None:
        """The total monitored components and sidecars must exactly equal 14."""
        self.assertEqual(len(_COMPONENTS), 10)
        self.assertEqual(len(_SIDECAR_COMPONENTS), 4)
        total_tracked = len(_COMPONENTS) + len(_SIDECAR_COMPONENTS)
        self.assertEqual(total_tracked, 14)

    def test_idempotent_noop_renders_zero_deltas_for_all_components(self) -> None:
        """When status is IDEMPOTENT_NOOP, table must explicitly display +0 for nodes, sources, assertions, and sidecars."""
        summary = {
            "status": "IDEMPOTENT_NOOP",
            "active_generation": "generation_001",
            "graph_sha256": "abc123def456",
        }
        table_md = format_markdown_mutation_table(summary)
        self.assertIn("### 1. Granular Graph DB Delta (Table-by-Table)", table_md)
        self.assertIn("| `node` | — | — | `+0` |", table_md)
        self.assertIn("| `source` | — | — | `+0` |", table_md)
        self.assertIn("| `source_version` | — | — | `+0` |", table_md)
        self.assertIn("| `evidence_item` | — | — | `+0` |", table_md)
        self.assertIn("| `assertion` | — | — | `+0` |", table_md)
        self.assertIn("| `search_document` | — | — | `+0` |", table_md)
        self.assertIn("| `dense_vector` (sidecar) | — | — | `+0` |", table_md)
        self.assertIn("| `sparse_weight` (sidecar) | — | — | `+0` |", table_md)
        self.assertIn("| `colbert_token` (sidecar) | — | — | `+0` |", table_md)
        self.assertIn("| `retrieval_record` (sidecar) | — | — | `+0` |", table_md)

    def test_mutating_run_renders_all_14_table_rows(self) -> None:
        """When delta is present, table renders rows for all 10 core components and 4 sidecars."""
        table_rows = [
            {
                "component_key": key,
                "graph_component": label,
                "table": table,
                "baseline": 10,
                "actual_after": 12,
                "actual_delta": 2,
            }
            for key, label, table in _COMPONENTS
        ]
        sidecar_rows = [
            {
                "table": table,
                "label": label,
                "baseline": 100,
                "actual_after": 110,
                "actual_delta": 10,
            }
            for _key, label, table in _SIDECAR_COMPONENTS
        ]
        summary = {
            "status": "MUTATION_COMPLETE",
            "actual_graph_delta": {"table": table_rows},
            "sidecar_table": sidecar_rows,
        }
        table_md = format_markdown_mutation_table(summary)
        for _key, _label, table in _COMPONENTS:
            self.assertIn(f"`{table}`", table_md)
        for _key, _label, table in _SIDECAR_COMPONENTS:
            self.assertIn(f"`{table}`", table_md)

    def test_itemized_assertions_rendered(self) -> None:
        """When assertions_inserted is provided, table must itemize each assertion."""
        summary = {
            "status": "MUTATION_COMPLETE",
            "actual_graph_delta": {"table": []},
            "sidecar_table": [],
        }
        assertions = [
            {
                "assertion_id": "asrt-001",
                "subject_node_id": "org:apex",
                "predicate": "implements_program",
                "claim_text": "APEX implements Smart Grid Reliability Program.",
                "status": "CANONICAL",
            }
        ]
        table_md = format_markdown_mutation_table(summary, assertions_inserted=assertions)
        self.assertIn("### 2. Assertions Inserted", table_md)
        self.assertIn("asrt-001", table_md)
        self.assertIn("org:apex", table_md)
        self.assertIn("implements_program", table_md)
        self.assertIn("CANONICAL", table_md)


    def test_all_14_mandated_tables_present_in_noop_and_mutation(self) -> None:
        """All 10 SQLite tables and 4 sidecar tables must be explicitly present."""
        mandated_tables = (
            "`node`",
            "`source`",
            "`source_version`",
            "`extraction_run`",
            "`evidence_item`",
            "`assertion`",
            "`assertion_evidence`",
            "`search_document`",
            "`search_fts`",
            "`audit_event`",
            "`dense_vector` (sidecar)",
            "`sparse_weight` (sidecar)",
            "`colbert_token` (sidecar)",
            "`retrieval_record` (sidecar)",
        )
        # 1. In no-op:
        noop_summary = {"status": "IDEMPOTENT_NOOP"}
        noop_md = format_markdown_mutation_table(noop_summary)
        for tbl in mandated_tables:
            self.assertIn(tbl, noop_md, f"Missing table in no-op delta summary: {tbl}")

        # 2. In mutating run:
        mut_summary = {
            "status": "MUTATION_COMPLETE",
            "actual_graph_delta": {"table": []},
            "sidecar_table": [],
        }
        mut_md = format_markdown_mutation_table(mut_summary)
        for tbl in mandated_tables:
            self.assertIn(tbl, mut_md, f"Missing table in mutating delta summary: {tbl}")


if __name__ == "__main__":
    unittest.main()

