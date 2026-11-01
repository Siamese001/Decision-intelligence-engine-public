#!/usr/bin/env python3
"""End-to-End test for Decentralized CI Manifest and SSOT Architecture (Wave 3).

Simulates:
1. Creating an isolated feature branch test manifest under tests/manifests/.
2. Loading, validating, and resolving CI test suites dynamically without modifying run_ci_gates.py.
3. Executing CLI commands (list, validate) for the decentralized manifest engine.
4. Testing dynamic project taxonomy discovery under config/governance/projects.d/.
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from dual_pipeline.parameters import get_known_projects, get_project_aliases
from tools.test_manifest_loader import (
    cmd_list,
    cmd_validate,
    discover_manifests,
    resolve_all_tests,
)


class DecentralizedCIManifestE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="manifest_e2e_")
        self.manifest_dir = Path(self.tmp_dir) / "manifests"
        self.manifest_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_manifest_discovery_and_resolution_e2e(self) -> None:
        """Verify dynamic discovery and resolution merges test suites seamlessly."""
        manifest_data = {
            "name": "feat_super_fast_rebase",
            "description": "Super fast rebase tests",
            "branch": "feat/super-fast-rebase",
            "deterministic_tests": [
                "tests.adversarial.test_fast_1",
                "tests.adversarial.test_fast_2",
            ],
            "e2e_tests": [
                "tests.end_to_end.test_fast_lifecycle_e2e",
            ],
            "bge_tests": [],
        }
        manifest_file = self.manifest_dir / "feat_super_fast_rebase.json"
        manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

        manifests = discover_manifests(self.manifest_dir)
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0].name, "feat_super_fast_rebase")
        self.assertEqual(manifests[0].total_tests(), 3)

        base_det = ("tests.test_existing_unit",)
        base_e2e = ("tests.end_to_end.test_existing_e2e",)

        det, e2e, bge = resolve_all_tests(
            base_deterministic=base_det,
            base_e2e=base_e2e,
            manifest_dir=self.manifest_dir,
        )

        self.assertIn("tests.test_existing_unit", det)
        self.assertIn("tests.adversarial.test_fast_1", det)
        self.assertIn("tests.adversarial.test_fast_2", det)
        self.assertIn("tests.end_to_end.test_existing_e2e", e2e)
        self.assertIn("tests.end_to_end.test_fast_lifecycle_e2e", e2e)

    def test_02_cli_commands_list_and_validate(self) -> None:
        """Verify CLI sub-commands list and validate work with clean output."""
        manifest_data = {
            "name": "feat_cli_test",
            "description": "CLI testing manifest",
            "deterministic_tests": ["tests.test_cli"],
        }
        (self.manifest_dir / "feat_cli.json").write_text(json.dumps(manifest_data), encoding="utf-8")

        buf_list = io.StringIO()
        with redirect_stdout(buf_list):
            exit_code_list = cmd_list(self.manifest_dir)
        self.assertEqual(exit_code_list, 0)
        self.assertIn("feat_cli_test", buf_list.getvalue())
        self.assertIn("1 tests", buf_list.getvalue())

        buf_val = io.StringIO()
        with redirect_stdout(buf_val):
            exit_code_val = cmd_validate(self.manifest_dir)
        self.assertEqual(exit_code_val, 0)
        self.assertIn("feat_cli.json: Valid", buf_val.getvalue())

    def test_03_projects_decentralized_fragment_e2e(self) -> None:
        """Verify get_known_projects and get_project_aliases load project fragments."""
        known = get_known_projects()
        aliases = get_project_aliases()

        self.assertIsInstance(known, set)
        self.assertIn("project_lens", known)
        self.assertIn("conduit", known)

        self.assertIsInstance(aliases, dict)
        self.assertEqual(aliases.get("lens"), "project_lens")


if __name__ == "__main__":
    unittest.main()
