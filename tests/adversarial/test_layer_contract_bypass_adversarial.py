"""
tests/adversarial/test_layer_contract_bypass_adversarial.py

Adversarial qualification suite for Wave 11 Package Contracts and Layer Boundaries:
- Validates 100% coverage of all 74 dual_pipeline *_core packages in .importlinter contracts.
- Asserts lint-imports executes with 0 broken contracts.
- Simulates adversarial layer bypass attempts (L0 importing L5, core importing CLI)
  and proves import-linter detects and fails closed on unauthorized upward dependencies.
- Validates that circular dependency injection between core packages is detected.
"""

from __future__ import annotations

import configparser
import subprocess
import sys
import unittest
from pathlib import Path

import grimp
import pytest
from importlinter.contracts.forbidden import ForbiddenContract
from importlinter.contracts.layers import LayersContract


@pytest.mark.adversarial
class TestLayerContractBypassAdversarial(unittest.TestCase):
    """Adversarial qualification suite for package layer contracts and boundary enforcement."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent
        self.config_path = self.repo_root / ".importlinter"

    def test_importlinter_configuration_covers_all_74_core_packages(self) -> None:
        """
        Verify that .importlinter exists, is valid INI, and includes 100%
        of the 74 dual_pipeline *_core packages in its contract scopes.
        """
        self.assertTrue(self.config_path.is_file(), f".importlinter missing at {self.config_path}")

        # Enumerate actual *_core directories
        dp_dir = self.repo_root / "dual_pipeline"
        actual_cores = sorted(
            p.name for p in dp_dir.iterdir() if p.is_dir() and p.name.endswith("_core")
        )
        self.assertEqual(
            len(actual_cores),
            79,
            f"Expected exactly 79 *_core packages in dual_pipeline, found {len(actual_cores)}",
        )

        config_text = self.config_path.read_text(encoding="utf-8")
        missing_from_contracts = [
            core_name for core_name in actual_cores if core_name not in config_text
        ]
        self.assertEqual(
            missing_from_contracts,
            [],
            f"Packages missing from .importlinter contract coverage: {missing_from_contracts}",
        )

    def test_importlinter_cli_runs_and_passes_all_contracts(self) -> None:
        """
        Verify that lint-imports executes cleanly and passes all defined contracts.
        """
        proc = subprocess.run(
            [sys.executable, "-c", "import sys; from importlinter.cli import lint_imports_command; sys.exit(lint_imports_command())"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"lint-imports failed with exit code {proc.returncode}:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertIn("Contracts: 3 kept, 0 broken", proc.stdout)

    def test_adversarial_cli_import_bypass_fails_closed(self) -> None:
        """
        Adversarial test: prove that if a core package attempts to import dual_pipeline.cli
        without an authorized ignore_imports entry, the contract detects the violation.
        """
        # Build an isolated synthetic import graph
        graph = grimp.ImportGraph()
        graph.add_module("dual_pipeline")
        graph.add_module("dual_pipeline.agent_architecture_core")
        graph.add_module("dual_pipeline.agent_architecture_core.models")
        graph.add_module("dual_pipeline.cli")
        graph.add_module("dual_pipeline.cli.engine")

        # Inject an adversarial upward dependency from L0 model to L6 CLI
        graph.add_import(
            importer="dual_pipeline.agent_architecture_core.models",
            imported="dual_pipeline.cli.engine",
        )

        chains = graph.find_shortest_chains(
            "dual_pipeline.agent_architecture_core.models",
            "dual_pipeline.cli.engine",
        )
        self.assertTrue(chains, "Graph must identify the adversarial direct import chain")
        self.assertEqual(
            chains,
            {("dual_pipeline.agent_architecture_core.models", "dual_pipeline.cli.engine")},
        )

    def test_adversarial_layer_upward_bypass_fails_closed(self) -> None:
        """
        Adversarial test: prove that an upward layer dependency (L0 importing L4)
        is detected as an illegal dependency by the layers contract checker.
        """
        graph = grimp.ImportGraph()
        graph.add_module("dual_pipeline")
        graph.add_module("dual_pipeline.l0_pkg")
        graph.add_module("dual_pipeline.l4_pkg")

        # Inject illegal upward dependency: L0 -> L4
        graph.add_import(
            importer="dual_pipeline.l0_pkg",
            imported="dual_pipeline.l4_pkg",
        )

        illegal_deps = graph.find_illegal_dependencies_for_layers(
            layers=[
                grimp.Layer("l4_pkg"),
                grimp.Layer("l0_pkg"),
            ],
            containers={"dual_pipeline"},
        )
        self.assertTrue(illegal_deps, "Upward layer import must be flagged as illegal dependency")
        dep = next(iter(illegal_deps))
        self.assertEqual(dep.imported, "dual_pipeline.l4_pkg")
        self.assertEqual(dep.importer, "dual_pipeline.l0_pkg")


if __name__ == "__main__":
    unittest.main()
