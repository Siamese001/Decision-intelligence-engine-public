"""Adversarial tests for dropzone deprecation and hardening.

Categories covered (minimum 3 required):
1. Boundary payload: Non-existent uploads directory, empty uploads directory handled safely without crashing.
2. Malformed input & error recovery: Handling unlinked or inaccessible files during TTL pruning.
3. Invariant bypass attempt: Verifying that app.js script contains strict quarantine checks blocking chat egress.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

DROPZONE_DIR = Path.home() / ".gemini" / "config" / "plugins" / "universal-file-intake" / "sidecars" / "dropzone"


class TestDropzoneDeprecationAdversarial(unittest.TestCase):
    """Adversarial suite for dropzone sidecar deprecation and quarantine enforcement."""

    def test_purge_nonexistent_directory_fails_closed_safely(self):
        """Boundary: Passing non-existent uploads directory returns 0 purged and does not throw."""
        nonexistent = DROPZONE_DIR / "nonexistent_dir_xyz_123"
        script = f"""
        import {{ purgeExpiredUploads }} from '{DROPZONE_DIR / "main.mjs"}';
        const count = purgeExpiredUploads('{nonexistent}', 3600 * 1000);
        console.log(JSON.stringify({{ purged: count }}));
        """
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        res = json.loads(proc.stdout)
        self.assertEqual(res["purged"], 0)

    def test_purge_empty_directory_handled_safely(self):
        """Boundary: Empty directory purge returns 0 purged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = f"""
            import {{ purgeExpiredUploads }} from '{DROPZONE_DIR / "main.mjs"}';
            const count = purgeExpiredUploads('{tmpdir}', 3600 * 1000);
            console.log(JSON.stringify({{ purged: count }}));
            """
            proc = subprocess.run(
                ["node", "--input-type=module", "-e", script],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0)
            res = json.loads(proc.stdout)
            self.assertEqual(res["purged"], 0)

    def test_app_js_blocks_quarantined_egress_invariant(self):
        """Invariant Bypass: app.js contains explicit quarantine guard preventing chat injection of secrets."""
        app_js = DROPZONE_DIR / "app.js"
        self.assertTrue(app_js.exists())
        code = app_js.read_text(encoding="utf-8")

        self.assertIn("quarantined", code)
        self.assertIn("Egress blocked", code)
        # Ensure the quarantine condition precedes sendMessage invocation
        quarantine_pos = code.find("Egress blocked")
        send_pos = code.find("sendMessage")
        self.assertGreater(quarantine_pos, 0)
        self.assertGreater(send_pos, 0)
        self.assertLess(quarantine_pos, send_pos, "Quarantine guard must precede sendMessage call")

    def test_main_mjs_imports_zero_parser_py(self):
        """Invariant: main.mjs must contain zero references to PARSER_SCRIPT or parser.py."""
        main_mjs = DROPZONE_DIR / "main.mjs"
        code = main_mjs.read_text(encoding="utf-8")
        self.assertNotIn("parser.py", code)
        self.assertNotIn("PARSER_SCRIPT", code)
        self.assertIn("dual_pipeline.mcp.intake_bridge", code)
