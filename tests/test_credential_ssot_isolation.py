#!/usr/bin/env python3
"""
tests/test_credential_ssot_isolation.py

Permanent Class A invariant test suite for Wave 3: Credential Isolation & Environment Variable SSOT Hardening.
Enforces:
1. CredentialManager SSOT capabilities (get, has, set, prepend_path, get_process_environment, is_local_execution_mode, temporary_env).
2. SSOT re-export facades (dual_pipeline.credentials, storage.credentials, config.credentials).
3. Complete eradication of direct os.environ / os.getenv accesses across dual_pipeline, storage, pipeline_a_intake, and pipeline_b_extraction.
"""
from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path

import config.credentials as canonical_creds
import dual_pipeline.credentials as dual_creds
import storage.credentials as storage_creds
from config.credentials import CredentialManager

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestCredentialSSOTIsolation(unittest.TestCase):
    """Verifies that CredentialManager acts as the single source of truth and no leaks exist."""

    def test_credential_manager_facades_parity(self):
        """All three credential facades expose the same CredentialManager."""
        self.assertIs(canonical_creds.CredentialManager, CredentialManager)
        self.assertIs(storage_creds.CredentialManager, CredentialManager)
        self.assertIs(dual_creds.CredentialManager, CredentialManager)

    def test_has_and_get_credential(self):
        """has_credential and get_credential behave consistently."""
        with CredentialManager.temporary_env({"_TEST_NEE_VAR_ALPHA": "value_123"}):
            self.assertTrue(CredentialManager.has_credential("_TEST_NEE_VAR_ALPHA"))
            self.assertEqual(CredentialManager.get_credential("_TEST_NEE_VAR_ALPHA"), "value_123")
            self.assertEqual(CredentialManager.get_credential("_TEST_NEE_VAR_ALPHA", default="fallback"), "value_123")

        self.assertFalse(CredentialManager.has_credential("_TEST_NEE_VAR_BETA_NONEXISTENT"))
        self.assertIsNone(CredentialManager.get_credential("_TEST_NEE_VAR_BETA_NONEXISTENT"))
        self.assertEqual(
            CredentialManager.get_credential("_TEST_NEE_VAR_BETA_NONEXISTENT", default="def_val"),
            "def_val",
        )

    def test_set_credential_and_set_credentials(self):
        """set_credential and set_credentials properly update environment."""
        with CredentialManager.temporary_env({}):
            CredentialManager.set_credential("_TEST_SET_KEY", "foo")
            self.assertEqual(CredentialManager.get_credential("_TEST_SET_KEY"), "foo")

            CredentialManager.set_credentials({"_TEST_K1": "v1", "_TEST_K2": "v2"})
            self.assertEqual(CredentialManager.get_credential("_TEST_K1"), "v1")
            self.assertEqual(CredentialManager.get_credential("_TEST_K2"), "v2")

    def test_is_local_execution_mode(self):
        """is_local_execution_mode properly recognizes boolean truthy flags."""
        truthy_values = ["true", "True", "1", "yes", "YES"]
        for val in truthy_values:
            with CredentialManager.temporary_env({"LOCAL_EXECUTION_MODE": val}):
                self.assertTrue(CredentialManager.is_local_execution_mode(), f"Failed for {val}")

        falsy_values = ["false", "False", "0", "no", "", "other"]
        for val in falsy_values:
            with CredentialManager.temporary_env({"LOCAL_EXECUTION_MODE": val}):
                self.assertFalse(CredentialManager.is_local_execution_mode(), f"Failed for {val}")

    def test_prepend_path(self):
        """prepend_path safely prepends directory to PATH without duplicate contamination."""
        test_dir = "/tmp/test_nee_bin_dir"
        orig_path = os.environ.get("PATH", "")
        with CredentialManager.temporary_env({"PATH": orig_path}):
            CredentialManager.prepend_path(test_dir)
            curr = os.environ.get("PATH", "").split(os.pathsep)
            self.assertEqual(curr[0], test_dir)

            # Idempotency check: prepending again does not add duplicate
            CredentialManager.prepend_path(test_dir)
            curr2 = os.environ.get("PATH", "").split(os.pathsep)
            self.assertEqual(curr2.count(test_dir), 1)

    def test_get_process_environment(self):
        """get_process_environment returns a safe dict copy of os.environ."""
        env_map = CredentialManager.get_process_environment()
        self.assertIsInstance(env_map, dict)
        self.assertEqual(env_map.get("PATH"), os.environ.get("PATH"))
        # Verify modifying returned dict does not alter os.environ
        env_map["_SYNTHETIC_MUTATION_CHECK"] = "should_not_leak"
        self.assertNotIn("_SYNTHETIC_MUTATION_CHECK", os.environ)

    def test_temporary_env_lifecycle(self):
        """temporary_env context manager cleanly rolls back added and modified keys."""
        os.environ["_EXISTING_KEY_XYZ"] = "original_val"
        try:
            with CredentialManager.temporary_env({
                "_EXISTING_KEY_XYZ": "modified_val",
                "_NEW_TEMP_KEY_XYZ": "temporary_val",
            }):
                self.assertEqual(CredentialManager.get_credential("_EXISTING_KEY_XYZ"), "modified_val")
                self.assertEqual(CredentialManager.get_credential("_NEW_TEMP_KEY_XYZ"), "temporary_val")

            self.assertEqual(CredentialManager.get_credential("_EXISTING_KEY_XYZ"), "original_val")
            self.assertFalse(CredentialManager.has_credential("_NEW_TEMP_KEY_XYZ"))
        finally:
            os.environ.pop("_EXISTING_KEY_XYZ", None)
            os.environ.pop("_NEW_TEMP_KEY_XYZ", None)

    def test_zero_raw_environ_in_governed_packages(self):
        """AST check: 0 raw os.environ / os.getenv accesses in dual_pipeline, storage, pipeline_a_intake, pipeline_b_extraction."""
        governed_dirs = [
            REPO_ROOT / "dual_pipeline",
            REPO_ROOT / "storage",
            REPO_ROOT / "pipeline_a_intake",
            REPO_ROOT / "pipeline_b_extraction",
        ]

        allowed_ssot_files = {
            REPO_ROOT / "config" / "credentials.py",
            REPO_ROOT / "storage" / "credentials.py",
            REPO_ROOT / "dual_pipeline" / "credentials.py",
        }

        violations = []
        for gdir in governed_dirs:
            for py_file in gdir.rglob("*.py"):
                if py_file.resolve() in allowed_ssot_files:
                    continue
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                except Exception as exc:
                    self.fail(f"Could not parse {py_file}: {exc}")

                for node in ast.walk(tree):
                    # Check for os.environ[...] or os.environ.get(...)
                    if isinstance(node, ast.Attribute) and node.attr == "environ":
                        if isinstance(node.value, ast.Name) and node.value.id == "os":
                            violations.append((str(py_file.relative_to(REPO_ROOT)), node.lineno, "os.environ"))
                    # Check for os.getenv(...)
                    elif isinstance(node, ast.Attribute) and node.attr == "getenv":
                        if isinstance(node.value, ast.Name) and node.value.id == "os":
                            violations.append((str(py_file.relative_to(REPO_ROOT)), node.lineno, "os.getenv"))

        if violations:
            msg = "\n".join(f"  {f}:{line} -> {sym}" for f, line, sym in violations)
            self.fail(f"Found {len(violations)} raw os.environ / os.getenv accesses outside SSOT:\n{msg}")

        self.assertEqual(len(violations), 0)


if __name__ == "__main__":
    unittest.main()
