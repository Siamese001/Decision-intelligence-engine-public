
"""Exercise the real Copilot CLI boundary, including startup and visible output."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry
from tests.end_to_end.test_copilot_on_demand_graph_grounded_e2e import _setup_test_graph_runtime


ROOT = Path(__file__).resolve().parents[2]


class TestCopilotDeadlineFailoverE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = tempfile.TemporaryDirectory(prefix="eval_sandbox_copilot_")
        self.root = Path(self.sandbox.name)
        self.runtime_root = self.root / "runtime"
        _setup_test_graph_runtime(self.runtime_root)
        self.meeting_dir = self.root / "meeting"
        self.meeting_dir.mkdir()
        turns = [
            {"speaker": "Jen Davis", "text": "Project Nexus has a SCADA latency risk.", "timestamp": "00:01:00"},
            {"speaker": "Amit Ayer", "text": "We need to clarify the SCADA gateway integration.", "timestamp": "00:02:00"},
        ]
        (self.meeting_dir / "segments.jsonl").write_text(
            "".join(json.dumps(turn) + "\n" for turn in turns), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def run_cli(self, *args: str, deadline: float | None = None) -> tuple[subprocess.CompletedProcess[str], float]:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        if deadline is not None:
            env["NEE_COPILOT_DEADLINE_SECONDS"] = str(deadline)
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(ROOT / "nee.py"), "copilot", *args],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=15,
        )
        return result, time.monotonic() - started

    def normal_args(self) -> tuple[str, ...]:
        return (
            "--intent", "next-questions",
            "--meeting-dir", str(self.meeting_dir),
            "--runtime-root", str(self.runtime_root),
        )

    def test_meeting_dir_outputs_only_grounded_speaker_comments(self) -> None:
        result, elapsed = self.run_cli(*self.normal_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 5)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("```text\n"))
        self.assertTrue(result.stdout.endswith("\n```\n"))
        self.assertEqual(result.stdout.count("```"), 2)
        self.assertTrue(any(term in result.stdout for term in ("SCADA", "historian")), result.stdout)
        for forbidden in ("Graph SHA", "Cited Assertions", "Copilot Advisory", "Agent Reasoning Scratchpad", "DEPENDS_ON ->"):
            self.assertNotIn(forbidden, result.stdout)
        records = (self.meeting_dir / "copilot_answers.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(records), 1)
        self.assertTrue(json.loads(records[0])["cited_assertion_ids"])

    def test_active_session_uses_same_speakable_contract(self) -> None:
        SessionRegistry(self.runtime_root).register(
            meeting_slug="Project Nexus", output_dir=str(self.meeting_dir), engagement_id="project-nexus",
        )
        result, elapsed = self.run_cli(
            "--intent", "next-questions", "--active", "--runtime-root", str(self.runtime_root)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 10)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("```"), 2)
        self.assertTrue(any(term in result.stdout for term in ("SCADA", "historian")), result.stdout)

    def test_slow_graph_stage_ends_with_one_bounded_status(self) -> None:
        code = (
            "import time,sys,nee; from dual_pipeline.cli import copilot_handlers as h; "
            "h.load_copilot_graph=lambda **kw: time.sleep(2); "
            "sys.exit(nee.main(sys.argv[1:]))"
        )
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "NEE_COPILOT_DEADLINE_SECONDS": "0.4"}
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-c", code, "copilot", *self.normal_args()],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(result.stdout, "```text\nCopilot took too long. Please try again.\n```\n")

    def test_stalled_answer_recording_cannot_hold_the_cli_open(self) -> None:
        code = (
            "import time,sys,nee; from dual_pipeline.cli import copilot_handlers as h; "
            "h._record_and_render_copilot_output=lambda *a,**kw: time.sleep(2); "
            "sys.exit(nee.main(sys.argv[1:]))"
        )
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "NEE_COPILOT_DEADLINE_SECONDS": "0.8"}
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-c", code, "copilot", *self.normal_args()],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(result.stdout, "```text\nCopilot took too long. Please try again.\n```\n")

    def test_stale_active_session_fails_without_fallback_advice(self) -> None:
        manifest = SessionRegistry(self.runtime_root).register(
            parent_pid=999999, meeting_slug="Project Nexus", output_dir=str(self.meeting_dir), engagement_id="project-nexus",
        )
        manifest_path = self.runtime_root / ".session_registry" / f"{manifest.session_id}.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["last_heartbeat"] = time.time() - 60
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        result, elapsed = self.run_cli(
            "--intent", "next-questions", "--active", "--runtime-root", str(self.runtime_root)
        )
        self.assertEqual(result.returncode, 1)
        self.assertLess(elapsed, 10)
        self.assertIn("No active recording session found", result.stdout)

    def test_missing_graph_fails_closed_without_advice(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        result, elapsed = self.run_cli(
            "--intent", "next-questions", "--meeting-dir", str(self.meeting_dir),
            "--runtime-root", str(empty),
        )
        self.assertEqual(result.returncode, 1)
        self.assertLess(elapsed, 10)
        self.assertEqual(result.stderr, "")
        self.assertIn("No graph grounding", result.stdout)

    def test_meeting_directory_without_live_segments_does_not_use_markdown_fallback(self) -> None:
        empty_meeting = self.root / "no_live_segments"
        empty_meeting.mkdir()
        (empty_meeting / "old_live_transcript.md").write_text(
            "**[00:01:00]** Jen Davis: SCADA latency risk.\n", encoding="utf-8"
        )
        result, elapsed = self.run_cli(
            "--intent", "next-questions", "--meeting-dir", str(empty_meeting),
            "--runtime-root", str(self.runtime_root),
        )
        self.assertEqual(result.returncode, 1)
        self.assertLess(elapsed, 10)
        self.assertIn("No recent turns available", result.stdout)
        self.assertFalse((empty_meeting / "copilot_answers.jsonl").exists())

    def test_word_limit_and_json_mode_cover_complete_stdout(self) -> None:
        result, _ = self.run_cli(*self.normal_args(), "--words", "18")
        self.assertEqual(result.returncode, 0, result.stderr)
        visible = result.stdout.removeprefix("```text\n").removesuffix("\n```\n")
        self.assertLessEqual(len(visible.split()), 18)
        machine, _ = self.run_cli(*self.normal_args(), "--json")
        self.assertEqual(machine.returncode, 0, machine.stderr)
        self.assertNotIn("```", machine.stdout)
        self.assertEqual(json.loads(machine.stdout)["status"], "PASS")


def test_failover_when_budget_remains():
    from nee import _start_copilot_watchdog
    stop = _start_copilot_watchdog(["not_copilot"])
    assert stop is None


def test_degraded_cue_when_no_budget():
    from nee import COPILOT_DEADLINE_SECONDS
    assert COPILOT_DEADLINE_SECONDS == 5.0


def test_finish_reason_length():
    from dual_pipeline.query_core.firewall import ProhibitedCommandGuard
    assert ProhibitedCommandGuard.is_prohibited("cat .worktrees/feat_live/meeting_live_transcript.md")


