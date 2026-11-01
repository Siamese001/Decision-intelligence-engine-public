"""
tests/test_ungoverned_entrypoints_adversarial.py

Adversarial test suite verifying that all killed/decommissioned entrypoints
fail closed with exit code 1 and emit the required governance violation message.
Exercises attempts to bypass governance via flags, arguments, and varied process states.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestUngovernedEntrypointsAdversarial(unittest.TestCase):
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

    def test_live_audio_transcriber_fails_closed(self) -> None:
        """Verify scripts/live_audio_transcriber.py fails closed with exit code 1."""
        script_path = str(ROOT / "scripts" / "live_audio_transcriber.py")
        res = self._run_cmd([sys.executable, script_path, "--project", "foo", "--meeting", "bar"])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("scripts/live_audio_transcriber.py has been decommissioned", res.stderr)

    def test_mac_system_audio_transcriber_fails_closed(self) -> None:
        """Verify scripts/mac_system_audio_transcriber.py fails closed with exit code 1."""
        script_path = str(ROOT / "scripts" / "mac_system_audio_transcriber.py")
        res = self._run_cmd([sys.executable, script_path, "--list-devices"])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("scripts/mac_system_audio_transcriber.py has been decommissioned", res.stderr)

    def test_transcribe_gpu_mlx_fails_closed(self) -> None:
        """Verify scripts/transcribe_gpu_mlx.py fails closed with exit code 1."""
        script_path = str(ROOT / "scripts" / "transcribe_gpu_mlx.py")
        res = self._run_cmd([sys.executable, script_path, "/tmp/fake.wav"])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("scripts/transcribe_gpu_mlx.py has been decommissioned", res.stderr)

    def test_transcript_pipeline_cli_fails_closed(self) -> None:
        """Verify python -m dual_pipeline.transcript_pipeline fails closed with exit code 1."""
        res = self._run_cmd([sys.executable, "-m", "dual_pipeline.transcript_pipeline", "--help"])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("Direct CLI execution of dual_pipeline.transcript_pipeline is strictly prohibited", res.stderr)

    def test_transcript_cli_direct_execution_fails_closed(self) -> None:
        """Verify python -m dual_pipeline.transcript_cli fails closed with exit code 1."""
        res = self._run_cmd([sys.executable, "-m", "dual_pipeline.transcript_cli", "--source", "foo.txt"])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("Direct CLI execution of dual_pipeline.transcript_cli is strictly prohibited", res.stderr)

    def test_live_transcript_engine_fails_closed(self) -> None:
        """Verify direct execution of dual_pipeline/live_transcript_engine.py fails closed."""
        script_path = str(ROOT / "dual_pipeline" / "live_transcript_engine.py")
        res = self._run_cmd([sys.executable, script_path, "input.txt"])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("Direct CLI execution of dual_pipeline.live_transcript_engine is strictly prohibited", res.stderr)

    def test_execute_hemant_desai_judge_fails_closed(self) -> None:
        """Verify scripts/execute_hemant_desai_judge.py fails closed with exit code 1."""
        script_path = str(ROOT / "scripts" / "execute_hemant_desai_judge.py")
        res = self._run_cmd([sys.executable, script_path])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("scripts/execute_hemant_desai_judge.py has been decommissioned", res.stderr)

    def test_execute_hamilton_evans_judge_fails_closed(self) -> None:
        """Verify scripts/execute_hamilton_evans_judge.py fails closed with exit code 1."""
        script_path = str(ROOT / "scripts" / "execute_hamilton_evans_judge.py")
        res = self._run_cmd([sys.executable, script_path])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("scripts/execute_hamilton_evans_judge.py has been decommissioned", res.stderr)

    def test_execute_nexus_0922_judge_fails_closed(self) -> None:
        """Verify scripts/execute_nexus_0922_judge.py fails closed with exit code 1."""
        script_path = str(ROOT / "scripts" / "execute_nexus_0922_judge.py")
        res = self._run_cmd([sys.executable, script_path])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("scripts/execute_nexus_0922_judge.py has been decommissioned", res.stderr)

    def test_audit_and_judge_project_nexus_fails_closed(self) -> None:
        """Verify scripts/audit_and_judge_project_nexus.py fails closed with exit code 1."""
        script_path = str(ROOT / "scripts" / "audit_and_judge_project_nexus.py")
        res = self._run_cmd([sys.executable, script_path])
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)
        self.assertIn("scripts/audit_and_judge_project_nexus.py has been decommissioned", res.stderr)

    def test_adversarial_flag_bypass_rejected(self) -> None:
        """Attempts to pass mock or bypass flags to decommissioned scripts are strictly rejected."""
        decommissioned = (
            "live_audio_transcriber.py",
            "mac_system_audio_transcriber.py",
            "transcribe_gpu_mlx.py",
            "execute_hemant_desai_judge.py",
            "execute_hamilton_evans_judge.py",
            "execute_nexus_0922_judge.py",
            "audit_and_judge_project_nexus.py",
        )
        for script_name in decommissioned:
            script_path = str(ROOT / "scripts" / script_name)
            res = self._run_cmd([sys.executable, script_path, "--mock", "--force", "--bypass", "--debug"])
            self.assertEqual(res.returncode, 1, f"{script_name} must fail closed on adversarial flags")
            self.assertIn("GOVERNANCE VIOLATION", res.stderr)


if __name__ == "__main__":
    unittest.main()

