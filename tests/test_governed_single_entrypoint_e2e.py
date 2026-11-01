"""
tests/test_governed_single_entrypoint_e2e.py

End-to-End test suite verifying that 'nee.py ingest-meeting' and 'nee.py record-meeting'
serve as the sole governed CLI entrypoints to the transcript synthesis and audio capture pipelines.
Validates:
1. 'nee ingest-meeting --dry-run' validates parameters, metadata scaffolding, and paths.
2. 'nee record-meeting --dry-run' validates audio devices, WebSocket, and output paths.
3. Full pipeline run produces Deliverables 01, 02, 03 via Universal Write Gateway with QA gates.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestGovernedSingleEntrypointE2E(unittest.TestCase):
    def _run_cmd(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_nee_ingest_meeting_dry_run_e2e(self) -> None:
        """Verify 'nee ingest-meeting --dry-run' succeeds end-to-end via canonical CLI."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tx_path = Path(tmpdir) / "sample_transcript.md"
            tx_path.write_text(
                "**harper Hoffman:** [00:00:10] Reviewing LENS wind turbine asset counts.\n"
                "**Amit Ayer:** [00:01:20] Confirmed 150 assets per turbine.\n",
                encoding="utf-8",
            )
            cmd = [
                sys.executable,
                "nee.py",
                "ingest-meeting",
                "--input", str(tx_path),
                "--project", "project_lens",
                "--meeting", "LENS_E2E_Test",
                "--output-dir", str(tmpdir),
                "--date", "09/24/2026",
                "--time", "04:44 PM - 04:57 PM ET",
                "--attendees", "harper Hoffman, Amit Ayer",
                "--dry-run",
            ]
            res = self._run_cmd(cmd)
            self.assertEqual(res.returncode, 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")
            payload = json.loads(res.stdout)
            self.assertEqual(payload.get("status"), "COMPLETE")
            self.assertEqual(payload.get("release_status"), "PASS")
            self.assertIn("01_transcript", payload)
            self.assertIn("02_recap", payload)
            self.assertIn("03_graph", payload)

    def test_nee_record_meeting_dry_run_e2e(self) -> None:
        """Verify 'nee record-meeting --dry-run' succeeds end-to-end via canonical CLI."""
        cmd = [
            sys.executable,
            "nee.py",
            "record-meeting",
            "--project", "project_lens",
            "--meeting", "LENS_Record_E2E",
            "--attendees", "harper Hoffman, Jennifer Pan",
            "--dry-run",
        ]
        res = self._run_cmd(cmd)
        self.assertEqual(res.returncode, 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")
        self.assertIn("[OK] Dry run successful", res.stdout)
        self.assertIn("LENS_Record_E2E", res.stdout)
        self.assertIn("ws://127.0.0.1", res.stdout)

    def test_nee_ingest_meeting_full_lifecycle_e2e(self) -> None:
        """Verify 'nee ingest-meeting' executes full stage progression with QA reports."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tx_path = Path(tmpdir) / "live_tx.md"
            tx_path.write_text(
                "# Live Meeting Transcript: LENS_E2E_Run\n\n"
                "**Date:** 09/24/2026  \n"
                "**Start Time:** 04:44 PM ET  \n"
                "**End Time:** 04:57 PM ET  \n"
                "**Project:** `project_lens`  \n"
                "**Status:** `RECORDING_COMPLETED`  \n\n"
                "### Attendees\n"
                "- **harper Hoffman** (Participant, NEA)\n"
                "- **Jennifer Pan** (Product Manager / Lead Engineer, PartnerAdvisory)\n"
                "- **Amit Ayer** (Lead Enterprise Architect, PartnerAdvisory)\n\n"
                "---\n\n"
                "## Live Feed\n\n"
                "**[00:02:29]** harper Hoffman: 150 Assets per turbine in 17(?) Work Orders.\n\n"
                "**[00:03:14]** harper Hoffman: 5 sites collecting, might be 80(?) turbines per site. 68 work orders completed.\n\n"
                "---\n*Session paused at 04:57 PM ET. Total chunks processed: 1*\n",
                encoding="utf-8",
            )
            out_target = Path(tmpdir) / "deliverables"
            cmd = [
                sys.executable,
                "nee.py",
                "ingest-meeting",
                "--input", str(tx_path),
                "--output-dir", str(out_target),
                "--project", "project_lens",
                "--meeting", "LENS_E2E_Run",
                "--date", "09/24/2026",
                "--time", "04:44 PM - 04:57 PM ET",
                "--attendees", "harper Hoffman, Jennifer Pan, Amit Ayer",
            ]
            res = self._run_cmd(cmd)
            self.assertEqual(res.returncode, 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")
            payload = json.loads(res.stdout)
            self.assertEqual(payload.get("status"), "COMPLETE")
            self.assertEqual(payload.get("release_status"), "PASS")

            # Check that files were written
            self.assertTrue(Path(payload["01_transcript"]).exists())
            self.assertTrue(Path(payload["02_recap"]).exists())
            self.assertTrue(Path(payload["03_graph"]).exists())
            self.assertTrue(Path(payload["qa_gates_report"]).exists())
            self.assertTrue(Path(payload["execution_receipt"]).exists())


if __name__ == "__main__":
    unittest.main()
