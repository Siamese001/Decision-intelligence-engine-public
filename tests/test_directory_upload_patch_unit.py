"""Unit and regression tests for Antigravity arbitrary directory and file upload patch."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

from tools.verify_file_upload_patch import (
    MAIN_JS,
    WORKBENCH_JS,
    PROD_JSON,
)


class DirectoryUploadPatchUnitTests(unittest.TestCase):
    """Test suite verifying arbitrary directory and file upload capability in Antigravity IDE."""

    def test_01_app_bundle_presence(self) -> None:
        """Verifies that the patched application files and backups exist."""
        self.assertTrue(MAIN_JS.is_file(), f"Target main.js not found at {MAIN_JS}")
        self.assertTrue(WORKBENCH_JS.is_file(), f"Target workbench.desktop.main.js not found at {WORKBENCH_JS}")
        self.assertTrue(PROD_JSON.is_file(), f"Target product.json not found at {PROD_JSON}")

    def test_02_syntax_validation_passes(self) -> None:
        """Ensures Node.js validates both bundles without syntax errors."""
        res_j = subprocess.run(["node", "--check", str(MAIN_JS)], capture_output=True, text=True)
        self.assertEqual(res_j.returncode, 0, f"Syntax check failed on {MAIN_JS}: {res_j.stderr}")

        res_w = subprocess.run(["node", "--check", str(WORKBENCH_JS)], capture_output=True, text=True)
        self.assertEqual(res_w.returncode, 0, f"Syntax check failed on {WORKBENCH_JS}: {res_w.stderr}")

    def test_03_manifest_checksum_matches_both_bundles(self) -> None:
        """Asserts that the base64 sha256 checksum in product.json strictly matches both bundles."""
        with open(PROD_JSON, "r", encoding="utf-8") as f:
            prod_data = json.load(f)

        with open(MAIN_JS, "rb") as f:
            actual_j_hash = base64.b64encode(hashlib.sha256(f.read()).digest()).decode().rstrip("=")
        manifest_j_hash = prod_data.get("checksums", {}).get("jetskiAgent/main.js")
        self.assertEqual(actual_j_hash, manifest_j_hash, f"Hash mismatch on jetskiAgent: actual {actual_j_hash} != manifest {manifest_j_hash}")

        with open(WORKBENCH_JS, "rb") as f:
            actual_w_hash = base64.b64encode(hashlib.sha256(f.read()).digest()).decode().rstrip("=")
        manifest_w_hash = prod_data.get("checksums", {}).get("vs/workbench/workbench.desktop.main.js")
        self.assertEqual(actual_w_hash, manifest_w_hash, f"Hash mismatch on workbench: actual {actual_w_hash} != manifest {manifest_w_hash}")

    def test_04_dual_bundle_accept_filter_removed(self) -> None:
        """Asserts that restrictive accept filters are completely removed from both bundles."""
        j_content = MAIN_JS.read_text(encoding="utf-8", errors="ignore")
        w_content = WORKBENCH_JS.read_text(encoding="utf-8", errors="ignore")

        self.assertIn("accept:void 0", j_content)
        self.assertNotIn(".png,.jpg,.jpeg,.gif,image/png", j_content)

        self.assertIn("accept:void 0", w_content)
        self.assertNotIn(".png,.jpg,.jpeg,.gif,image/png", w_content)

    def test_05_workbench_vtt_and_binary_fallback_present(self) -> None:
        """Asserts that .vtt and binary arrayBuffer fallback are present in workbench."""
        w_content = WORKBENCH_JS.read_text(encoding="utf-8", errors="ignore")

        self.assertIn('vtt:"text/vtt"', w_content)
        self.assertIn('application/octet-stream', w_content)
        self.assertIn('wzo=100*1024*1024', w_content)
        self.assertIn('Azo=100*1024*1024', w_content)

    def test_06_verify_script_exit_code_zero(self) -> None:
        """Executes the dual-bundle verification suite and asserts returncode == 0."""
        script_path = Path(__file__).resolve().parent.parent / "tools" / "verify_file_upload_patch.py"
        res = subprocess.run(["python3", str(script_path)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"verify script failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("ALL DUAL-BUNDLE VERIFICATION GATES PASSED (100%)", res.stdout)


if __name__ == "__main__":
    unittest.main()
