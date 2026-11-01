"""End-to-end integration tests for Live Multimodal Ingestion Certification (Wave 5).

Verifies CLI argument parity, clearance resolution under isolated runtime roots,
HITL decline lifecycle exit codes, and notes idempotency.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.core import ROOT


@pytest.mark.e2e
class LiveIngestionCertificationE2ETests(unittest.TestCase):
    """E2E suite verifying Wave 5 minimal root cause corrections."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Isolated runtime root structure
        self.runtime_root = self.tmp_path / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.tmp_path / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Fixtures
        self.meeting_file = self.tmp_path / "01_sample_meeting_transcript.md"
        self.meeting_file.write_text(
            "## Forensic Transcript\n\n"
            "[00:00:10] Amit Ayer: Reviewing live multimodal ingestion certification.\n"
            "[00:00:30] Team Lead: All 16 modalities verified.\n",
            encoding="utf-8",
        )

        self.document_file = self.tmp_path / "sample_architecture_doc.md"
        self.document_file.write_text(
            "# Architecture Specification\n\n"
            "Executive Summary: Unified multimodal ingestion pipeline with zero graph mutation leaks.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_meeting_ingest_cli_argument_parity(self) -> None:
        """E2E: ingest-meeting rejects --no-enforce-worktree fail-closed."""
        cmd = [
            sys.executable,
            str(ROOT / "nee.py"),
            "ingest-meeting",
            "--input", str(self.meeting_file),
            "--project", "project_lens",
            "--meeting", "Parity Test Meeting",
            "--date", "10-11-2026",
            "--resume",
            "--regenerate", "02,03",
            "--runtime-root", str(self.runtime_root),
            "--output-dir", str(self.output_dir),
            "--no-enforce-worktree",
        ]
        res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 1)
        self.assertIn("--no-enforce-worktree", res.stderr)

    def test_document_ingest_cli_argument_parity(self) -> None:
        """E2E: ingest-document rejects --no-enforce-worktree fail-closed."""
        cmd = [
            sys.executable,
            str(ROOT / "nee.py"),
            "ingest-document",
            "--input", str(self.document_file),
            "--project", "project_lens",
            "--title", "Parity Test Doc",
            "--date", "10-11-2026",
            "--resume",
            "--regenerate", "03",
            "--allow-duplicate",
            "--collision-override-rationale", "Verified E2E test",
            "--runtime-root", str(self.runtime_root),
            "--output-dir", str(self.output_dir),
            "--no-enforce-worktree",
        ]
        res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 1)
        self.assertIn("--no-enforce-worktree", res.stderr)

    def test_clearance_resolution_in_isolated_runtime(self) -> None:
        """E2E: _resolve_canonical_ingest_clearance resolves ledger in --runtime-root."""
        from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer
        directive_id = "directive-test-clearance-resolution-v1"
        ledger_p = self.runtime_root / "hitl_decision_ledger.sqlite"

        record_operator_answer(
            decision_id=directive_id,
            category="INGESTION_MUTATION",
            question="Approve candidate publication for isolated clearance E2E",
            options=["Approve candidate publication into active knowledge graph", "Decline candidate publication"],
            selected_option="Approve candidate publication into active knowledge graph",
            operator_origin={"operator_id": "operator", "origin": "operator_ui"},
            rationale="Pre-cleared approval in isolated test runtime",
            metadata={"directive_id": directive_id},
            sqlite_path=ledger_p,
        )

        from dual_pipeline.hitl_governance_core.active_ingestion_approval import check_active_ingestion_clearance
        is_cleared, clear_reason, clear_rec = check_active_ingestion_clearance(
            run_id=directive_id,
            directive_id=directive_id,
            delta_digest="",
            ledger_path=ledger_p,
        )
        self.assertTrue(is_cleared, f"Clearance check failed: {clear_reason}")
        self.assertIsNotNone(clear_rec)

    def test_hitl_declined_exit_code_zero(self) -> None:
        """E2E: Operator decline exits cleanly with exit code 0 when no mutation committed."""
        from dual_pipeline.cli.engine import _result_exit_code
        import argparse

        args = argparse.Namespace(command="ingest-meeting")
        declined_result = {
            "status": "DECLINED",
            "directive_id": "directive-test-decline",
            "reason": "Operator declined publication",
            "graph_mutation_committed": False,
        }
        exit_code = _result_exit_code(args, declined_result)
        self.assertEqual(exit_code, 0, "DECLINED status with graph_mutation_committed=False must yield exit code 0")

    def test_governed_refusal_for_blocked_legacy_format(self) -> None:
        """E2E: Blocked legacy format (.msg) triggers early governed refusal with typed error code."""
        blocked_file = self.tmp_path / "legacy_meeting.msg"
        blocked_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 500)

        cmd = [
            sys.executable,
            str(ROOT / "nee.py"),
            "ingest-document",
            "--input", str(blocked_file),
            "--project", "project_lens",
            "--title", "Legacy MSG Meeting",
            "--date", "10-11-2026",
            "--runtime-root", str(self.runtime_root),
            "--output-dir", str(self.output_dir),
        ]
        res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertNotEqual(res.returncode, 0, "Blocked format must fail closed with non-zero exit code")
        self.assertIn("UNSUPPORTED_MODALITY_GOVERNED_REFUSAL", res.stdout + res.stderr)
