"""
tests/test_graph_consolidation_cli.py

Unit, operational, and deprecation test suite for Wave 5:
1. CLI parsing and execution of `nee graph-consolidate {scan, plan, release}`.
2. Approval origin validation through the CLI entry point.
3. Plan generation and output file persistence.
4. Deprecation warnings for legacy `prepare_dedup_release` and `activate_dedup_release` shims.
5. Deprecation exit codes for retired CLI tools (`tier2_execute_dedup.py`, `tier2_finish_waves.py`).
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
import warnings
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from dual_pipeline.cli.handlers import (
    _graph_consolidate,
)
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.core import digest, exact_file_copy, write_json
from dual_pipeline.graph_consolidation_core import build_consolidation_plan
from dual_pipeline.release import ReleaseManager
from dual_pipeline.tier2_release import activate_dedup_release, prepare_dedup_release
from tools.tier2_execute_dedup import main as tier2_execute_main
from tools.tier2_finish_waves import main as tier2_finish_main


class MockCliBackend:
    """Mock BGE-M3 backend for CLI release testing."""

    def __init__(self, backend_id: str = "flagembedding-bge-m3-dev") -> None:
        self.backend_id = backend_id
        self.model_id = "BAAI/bge-m3"
        self.model_revision = "5617a9f61b028005a4858fdac845db406aefb181"
        self.model_manifest_digest = "a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e"
        self.production_qualified = False
        self.retrieval_encoder = "real-bge-m3"

    def verify_snapshot(self) -> None:
        pass


class TestGraphConsolidationCLI(unittest.TestCase):
    """Test suite for unified graph-consolidate CLI and legacy deprecation shims."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime_root = self.root / ".local" / "dual_pipeline"
        self.runtime_root.mkdir(parents=True)

        real_mgr = ReleaseManager(".local/dual_pipeline")
        real_active = real_mgr.active_pair()

        self.gen_id = real_active["generation_id"]
        self.gen_dir = self.runtime_root / "generations" / self.gen_id
        self.gen_dir.mkdir(parents=True)
        self.releases_dir = self.runtime_root / "releases"
        self.releases_dir.mkdir(parents=True)

        self.graph_path = self.gen_dir / "graph.sqlite"
        self.sidecar_path = self.gen_dir / "sidecar.sqlite"
        exact_file_copy(Path(real_mgr._resolve(real_active["graph_path"])), self.graph_path)
        exact_file_copy(Path(real_mgr._resolve(real_active["sidecar_path"])), self.sidecar_path)

        self.active_pair: Dict[str, Any] = dict(real_active)
        self.active_pair["graph_path"] = f"generations/{self.gen_id}/graph.sqlite"
        self.active_pair["sidecar_path"] = f"generations/{self.gen_id}/sidecar.sqlite"

        write_json(self.runtime_root / "active_pair.json", self.active_pair)
        write_json(self.gen_dir / "pair_manifest.json", self.active_pair)

        self.parser = build_parser()
        self.backend = MockCliBackend()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_parser_recognizes_subcommands(self) -> None:
        """Parser recognizes scan, plan, and release subactions under graph-consolidate."""
        args_scan = self.parser.parse_args(["graph-consolidate", "scan", "--runtime-root", str(self.runtime_root)])
        self.assertEqual(args_scan.command, "graph-consolidate")
        self.assertEqual(args_scan.action, "scan")

        out_path = self.root / "plan.json"
        args_plan = self.parser.parse_args([
            "graph-consolidate", "plan",
            "--runtime-root", str(self.runtime_root),
            "--output", str(out_path),
        ])
        self.assertEqual(args_plan.action, "plan")
        self.assertEqual(args_plan.output, out_path)

        args_rel = self.parser.parse_args([
            "graph-consolidate", "release",
            "--runtime-root", str(self.runtime_root),
            "--approval-id", "USER_INPUT: Operator approved",
        ])
        self.assertEqual(args_rel.action, "release")
        self.assertEqual(args_rel.approval_id, "USER_INPUT: Operator approved")

    def test_cli_scan_and_plan_generation(self) -> None:
        """Executing scan and plan generates contract-valid plan JSON."""
        out_plan = self.root / "reports" / "consolidation_plan.json"
        args = argparse.Namespace(
            command="graph-consolidate",
            action="plan",
            runtime_root=self.runtime_root,
            graph=None,
            output=out_plan,
            node_threshold=0.65,
            literal_threshold=0.85,
            evidence_threshold=0.90,
        )
        res = _graph_consolidate(args)
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(out_plan.exists())

        loaded_plan = json.loads(out_plan.read_text(encoding="utf-8"))
        self.assertEqual(loaded_plan["status"], "proposed")
        self.assertTrue(loaded_plan["approval_required"])
        self.assertGreater(loaded_plan["summary"]["total_operations_count"], 0)

    def test_cli_release_requires_approval(self) -> None:
        """Release action fails closed if approval is missing or synthetic."""
        args_no_appr = argparse.Namespace(
            command="graph-consolidate",
            action="release",
            runtime_root=self.runtime_root,
            graph=None,
            approval_id=None,
            directive_id=None,
            plan=None,
            output=None,
        )
        with self.assertRaises(PermissionError):
            _graph_consolidate(args_no_appr)

        args_synth = argparse.Namespace(
            command="graph-consolidate",
            action="release",
            runtime_root=self.runtime_root,
            graph=None,
            approval_id="stop hook blocked termination auto-approved",
            directive_id=None,
            plan=None,
            output=None,
        )
        with (
            patch("dual_pipeline.cli.handlers._backend", return_value=self.backend),
            self.assertRaises(PermissionError),
        ):
            _graph_consolidate(args_synth)

    def test_cli_release_lifecycle_idempotent_noop(self) -> None:
        """Release action on zero-op plan executes cleanly and emits IDEMPOTENT_NOOP."""
        plan = build_consolidation_plan(self.graph_path, active_pair=self.active_pair)
        zero_plan = dict(plan)
        zero_plan["operations"] = []
        zero_plan["proposed_operations"] = []
        zero_plan["summary"]["total_operations_count"] = 0
        zero_plan["plan_digest"] = digest({k: v for k, v in zero_plan.items() if k != "plan_digest"})

        plan_file = self.root / "zero_plan.json"
        write_json(plan_file, zero_plan)

        out_receipt = self.root / "receipt.json"
        args = argparse.Namespace(
            command="graph-consolidate",
            action="release",
            runtime_root=self.runtime_root,
            graph=None,
            plan=plan_file,
            output=out_receipt,
            approval_id="USER_INPUT: Operator Amit Ayer approves zero-op release",
            directive_id="directive-test-01",
            run_id="run-cli-test-noop",
        )
        with patch("dual_pipeline.cli.handlers._backend", return_value=self.backend):
            res = _graph_consolidate(args)

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["release_status"], "IDEMPOTENT_NOOP")
        self.assertEqual(res["activation_status"], "N/A")
        self.assertTrue(out_receipt.exists())

    def test_legacy_tier2_release_shims_emit_warnings(self) -> None:
        """Legacy tier2_release shims emit DeprecationWarning when called."""
        rm = ReleaseManager(self.runtime_root)
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            with patch("dual_pipeline.tier2_release.prepare_consolidation_release") as mock_prep:
                mock_prep.return_value = ({"status": "IDEMPOTENT_NOOP"}, self.root / "receipt.json")
                receipt, path = prepare_dedup_release(
                    manager=rm,
                    backend=self.backend,  # type: ignore
                    approval_id="test-approval",
                )
            self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in recorded))
            self.assertTrue(any("deprecated" in str(w.message).lower() for w in recorded))

        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            with patch("dual_pipeline.tier2_release.activate_consolidation_release") as mock_act:
                mock_act.return_value = {"status": "ACTIVE"}
                res = activate_dedup_release(
                    manager=rm,
                    receipt_path=self.root / "receipt.json",
                )
                self.assertEqual(res["status"], "ACTIVE")
            self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in recorded))

    def test_retired_cli_tools_exit_with_deprecation_notice(self) -> None:
        """Retired tools tier2_execute_dedup.py and tier2_finish_waves.py exit with code 1."""
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            code_exec = tier2_execute_main()
            self.assertEqual(code_exec, 1)
            self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in recorded))

        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            code_finish = tier2_finish_main()
            self.assertEqual(code_finish, 1)
            self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in recorded))


if __name__ == "__main__":
    unittest.main()
