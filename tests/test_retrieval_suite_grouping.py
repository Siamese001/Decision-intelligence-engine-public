#!/usr/bin/env python3
"""Regression tests for semantically coherent automatic delta groups."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.index import _exact_entity_title_matches  # noqa: E402
from dual_pipeline.retrieval_suite import _groups  # noqa: E402


def _entity_facet(record_id: str, entity_id: str) -> dict[str, object]:
    return {
        "record_id": record_id,
        "record_type": "entity_facet",
        "lane": "approved",
        "answer_use": "navigation_only",
        "classification": "internal",
        "audiences": ["internal_consulting"],
        "canonical_entities": [entity_id],
        "extensions": {
            "com.partner_advisory.nee.graph-assertion-ids": [f"assertion-{record_id}"],
        },
    }


class RetrievalSuiteGroupingTests(unittest.TestCase):
    def test_exact_entity_title_matches_only_the_explicitly_named_facet(self) -> None:
        records = {
            "entity-facet-ai-hub": {
                "record_type": "entity_facet",
                "title": "Entity facet: AI Hub Foundations / Catalyst",
            },
            "entity-facet-access": {
                "record_type": "entity_facet",
                "title": "Entity facet: Governed internal-data access",
            },
        }
        self.assertEqual(
            ["entity-facet-ai-hub"],
            _exact_entity_title_matches(
                "Locate the governed records titled: Entity facet: AI Hub Foundations / Catalyst",
                records,
            ),
        )

    def test_scoped_unrelated_entity_facets_are_not_batched(self) -> None:
        groups = _groups(
            [
                _entity_facet("entity-facet-a", "workstream_project_beacon"),
                _entity_facet("entity-facet-b", "workstream_ai_hub"),
            ],
            max_size=50,
        )

        self.assertEqual(
            [["entity-facet-a"], ["entity-facet-b"]],
            sorted([record["record_id"] for record in group] for group in groups),
        )

    def test_scoped_records_for_the_same_subject_remain_groupable(self) -> None:
        groups = _groups(
            [
                _entity_facet("entity-facet-a", "workstream_project_beacon"),
                _entity_facet("entity-facet-b", "workstream_project_beacon"),
            ],
            max_size=50,
        )
        self.assertEqual(
            [["entity-facet-a", "entity-facet-b"]],
            [[record["record_id"] for record in group] for group in groups],
        )

    def test_scoped_non_entity_facet_assertions_batch_across_subjects(self) -> None:
        records = [
            {
                "record_id": f"assertion-rec-{i}",
                "record_type": "assertion",
                "lane": "candidate",
                "answer_use": "candidate_hypothesis",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "canonical_entities": [f"distinct_entity_{i}"],
                "extensions": {
                    "com.partner_advisory.nee.graph-assertion-ids": [f"asrt-{i}"],
                },
            }
            for i in range(10)
        ]
        groups = _groups(records, max_size=8)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]), 8)
        self.assertEqual(len(groups[1]), 2)

    def test_extract_assertion_ids_includes_relationships_and_approved(self) -> None:
        import json
        import tempfile
        from dual_pipeline.retrieval_release_core.metrics import _extract_assertion_ids_from_runs

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "runs" / "test_run"
            run_dir.mkdir(parents=True)
            cs = {
                "operations": [
                    {"operation": "add_relationship", "target_id": "rel-1"},
                    {"operation": "add_approved_assertion", "target_id": "appr-1"},
                    {"operation": "add_action_item", "target_id": "act-1"},
                    {"operation": "supersede_assertion", "target_id": "sup-1"},
                ]
            }
            (run_dir / "graph_change_set.json").write_text(json.dumps(cs), encoding="utf-8")
            superseded, added = _extract_assertion_ids_from_runs(
                Path(td), [{"run_id": "test_run"}]
            )
            self.assertEqual(superseded, {"sup-1"})
            self.assertEqual(added, {"rel-1", "appr-1", "act-1"})

    def test_retrieval_service_filters_unsupported_query_kwargs(self) -> None:
        import tempfile
        from unittest.mock import MagicMock
        from dual_pipeline.retrieval_service import RetrievalService

        mock_qe = MagicMock()
        mock_qe.ask = MagicMock(return_value={"run_id": "test_run"})
        # Define ask signature with only (question, k=10)
        def ask_fn(question: str, k: int = 10):
            return {"run_id": "test_run"}
        mock_qe.ask.side_effect = ask_fn
        mock_qe.ask.__signature__ = None

        service = RetrievalService(runtime_root=Path("/tmp"), query_engine=mock_qe)
        with tempfile.TemporaryDirectory() as td:
            service.runtime_root = Path(td)
            (Path(td) / "runs" / "test_run").mkdir(parents=True)
            (Path(td) / "runs" / "test_run" / "evidence_packet.json").write_text("{}", encoding="utf-8")
            res = service.execute("test question", k=5, model_tier="frontier")
            self.assertIsNotNone(res)

    def test_build_passing_resumption_prefers_final_artifacts(self) -> None:
        import tempfile
        from dual_pipeline.canonical_pipeline.attachments import _build_passing_resumption

        with tempfile.TemporaryDirectory() as tmpdir:
            att_dir = Path(tmpdir) / "attempt_1"
            att_dir.mkdir()
            (att_dir / "output_3.ckpt.json").write_text('{"nodes": []}', encoding="utf-8")
            (att_dir / "03_final_canonical_signal_projection.json").write_text('{"nodes": ["healed"]}', encoding="utf-8")
            (att_dir / "output_1.ckpt.md").write_text("# Old", encoding="utf-8")
            (att_dir / "01_final_hitl_resolved_transcript.md").write_text("# Final", encoding="utf-8")

            dec = {"evaluation": {"composite_score": 95.0}}
            meta = {}
            att_num, syn_res, qa_res = _build_passing_resumption(att_dir, dec, meta)
            self.assertEqual(att_num, 1)
            self.assertEqual(syn_res.output_3, '{"nodes": ["healed"]}')
            self.assertEqual(syn_res.output_1, "# Final")


if __name__ == "__main__":
    unittest.main()
