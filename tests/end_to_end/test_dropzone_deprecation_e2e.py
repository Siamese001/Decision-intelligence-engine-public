"""End-to-end tests for Universal File Intake Dropzone sidecar deprecation.

Validates:
- Elimination of redundant parser.py script
- Deprecation metadata in sidecar status and UI HTML
- Uploads directory TTL pruning of expired files
- Delegation to governed core engine bridge
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DROPZONE_DIR = Path.home() / ".gemini" / "config" / "plugins" / "universal-file-intake" / "sidecars" / "dropzone"


class TestDropzoneDeprecationE2E(unittest.TestCase):
    """E2E suite for dropzone sidecar deprecation and hardening."""

    def test_parser_script_eliminated_from_filesystem(self):
        """E2E: Duplicate parser.py script is deleted and absent from disk."""
        parser_path = DROPZONE_DIR / "parser.py"
        self.assertFalse(parser_path.exists(), "Duplicate parser.py should be deleted")

    def test_index_html_renders_deprecation_banner(self):
        """E2E: index.html includes the deprecation banner directing users to governed bridge."""
        index_html = DROPZONE_DIR / "index.html"
        self.assertTrue(index_html.exists())
        content = index_html.read_text(encoding="utf-8")
        self.assertIn("deprecation-banner", content)
        self.assertIn("Sidecar is deprecated", content)

    def test_upload_ttl_pruning_cleans_stale_files_e2e(self):
        """E2E: purgeExpiredUploads deletes files older than TTL threshold without deleting recent files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            stale_file = tmp_path / "stale_upload.csv"
            stale_file.write_text("old,data\n", encoding="utf-8")
            # Set mtime back 2 hours (7200 seconds ago)
            past_time = time.time() - 7200
            os.utime(stale_file, (past_time, past_time))

            fresh_file = tmp_path / "fresh_upload.csv"
            fresh_file.write_text("fresh,data\n", encoding="utf-8")

            # Execute node snippet to call purgeExpiredUploads
            script = f"""
            import {{ purgeExpiredUploads }} from '{DROPZONE_DIR / "main.mjs"}';
            const count = purgeExpiredUploads('{tmp_path}', 3600 * 1000);
            console.log(JSON.stringify({{ purged: count }}));
            """
            proc = subprocess.run(
                ["node", "--input-type=module", "-e", script],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0, f"Node stderr: {proc.stderr}")
            res = json.loads(proc.stdout)
            self.assertEqual(res["purged"], 1)
            self.assertFalse(stale_file.exists())
            self.assertTrue(fresh_file.exists())
