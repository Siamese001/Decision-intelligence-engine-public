#!/usr/bin/env python3
"""End-to-end and CLI regression tests for the cards and facet refactor."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.card_views import (
    CARD_VIEW_RENDERER_VERSION,
    materialize_card_view,
)
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.core import read_json, sha256_file
from tools.cards_refactor import _default_approval, execute

ROOT = Path(__file__).resolve().parents[1]
SEED_GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class CardsRefactorCliAndE2ETests(unittest.TestCase):
    def test_parser_has_cards_refactor_subcommands(self) -> None:
        parser = build_parser()
        subparsers_action = None
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparsers_action = action
                break
        self.assertIsNotNone(subparsers_action)
        self.assertIn("cards-refactor", subparsers_action.choices)
        cards_parser = subparsers_action.choices["cards-refactor"]
        cards_sub = None
        for action in cards_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                cards_sub = action
                break
        self.assertIsNotNone(cards_sub)
        self.assertIn("plan", cards_sub.choices)
        self.assertIn("audit", cards_sub.choices)
        self.assertIn("apply", cards_sub.choices)
        self.assertIn("release-e2e", cards_sub.choices)

    def test_default_approval_shape(self) -> None:
        mock_plan = {
            "plan_id": "test-plan-1",
            "base_graph_sha256": "0" * 64,
            "plan_digest": "1" * 64,
            "operations": [
                {"operation": "install_card_refactor_schema"},
                {"operation": "assign_object_facet"},
            ],
        }
        approval = _default_approval(mock_plan, "approval-test-1")
        self.assertEqual("approval-test-1", approval["approval_id"])
        self.assertEqual("approved", approval["status"])
        self.assertEqual(1, approval["active"])
        self.assertEqual(
            ["assign_object_facet", "install_card_refactor_schema"],
            approval["scope"]["allowed_actions"],
        )

    def test_plan_and_validate_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir(parents=True)
            graph_copy = runtime_dir / "graph.sqlite"
            shutil.copyfile(SEED_GRAPH, graph_copy)

            # Create mock active pair for the baseline graph
            graph_hash = sha256_file(graph_copy)
            mock_pair = {
                "schema_version": "1.0.0",
                "generation_id": "generation-test-baseline",
                "graph_snapshot": "snapshot-test-baseline",
                "graph_sha256": graph_hash,
                "sidecar_sha256": "0" * 64,
                "graph_path": "graph.sqlite",
                "sidecar_path": "sidecar.sqlite",
                "embedding_backend": "flagembedding-bge-m3-dev",
                "model_id": "BAAI/bge-m3",
                "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
                "production_qualified": False,
            }
            with open(runtime_dir / "active_pair.json", "w") as f:
                json.dump(mock_pair, f)

            plan_file = temp_path / "plan.json"
            contract_file = temp_path / "plan.change-set.json"

            # Run plan
            args = argparse.Namespace(
                command="plan",
                runtime_root=runtime_dir,
                output=plan_file,
                contract_output=contract_file,
            )
            result = execute(args)
            self.assertEqual("PROPOSED", result["status"])
            self.assertTrue(plan_file.exists())
            self.assertTrue(contract_file.exists())

            # Validate the generated plan
            val_args = argparse.Namespace(
                command="validate-plan",
                plan=plan_file,
            )
            val_result = execute(val_args)
            self.assertEqual("PASS", val_result["status"])

    def test_audit_active_runtime(self) -> None:
        args = argparse.Namespace(
            command="audit",
            runtime_root=ROOT / ".local" / "dual_pipeline",
            graph=None,
        )
        result = execute(args)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("PASS", result["schema"]["status"])
        self.assertEqual("PASS", result["coverage"]["status"])
        self.assertEqual(0, result["coverage"]["active_assertion_intentional_unrouted"])

    def test_access_sliced_materialization_invariants(self) -> None:
        active_pair = read_json(ROOT / ".local" / "dual_pipeline" / "active_pair.json")
        graph_path = ROOT / ".local" / "dual_pipeline" / active_pair["graph_path"]

        con = sqlite3.connect(f"file:{graph_path.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        # Materialize NEE_01 as internal
        auth_ctx = {
            "classification_clearance": "internal",
            "audiences": ["internal_consulting", "nee-engineering"],
            "candidate_review": False,
        }
        receipt = materialize_card_view(
            con,
            card_id="NEE_01",
            generation_id=active_pair["generation_id"],
            graph_sha256=active_pair["graph_sha256"],
            authorization_context=auth_ctx,
        )

        con.close()

        # Invariant checks: Card prose is NEVER evidence
        self.assertEqual("MATERIALIZED", receipt["status"])
        self.assertEqual("NEE_01", receipt["card_id"])
        self.assertFalse(receipt["card_content_used_as_evidence"])
        self.assertEqual(CARD_VIEW_RENDERER_VERSION, receipt["renderer_version"])
        self.assertGreater(receipt["lane_counts"]["approved"], 0)
        self.assertGreater(receipt["assertion_count"], 0)
        self.assertEqual(
            "urn:nee:dual-pipeline:card-materialization-receipt:1.0.0",
            receipt["contract_id"],
        )


if __name__ == "__main__":
    unittest.main()
