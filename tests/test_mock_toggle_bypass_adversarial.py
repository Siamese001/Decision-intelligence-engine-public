"""
tests/test_mock_toggle_bypass_adversarial.py

Adversarial test suite attempting to bypass fail-closed provider policies,
inject deleted mock toggles, and forge mock release receipts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.publish_helper import publish_graph_if_requested
from dual_pipeline.release_policy import verify_authoritative_release_gate

ROOT = Path(__file__).resolve().parent.parent


class TestMockToggleBypassAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)
        self.sample_txt = self.output_dir / "sample.txt"
        self.sample_txt.write_text("Adversarial payload test text.", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_cli(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env.pop("PYTEST_CURRENT_TEST", None)
        env.pop("NEE_ALLOW_TEST_FIXTURES", None)
        return subprocess.run(
            [sys.executable, "-m", "dual_pipeline.cli", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_adversarial_provider_case_variants_rejected(self) -> None:
        """Adversarial attempt: use uppercase or whitespace variants of offline."""
        for bad_choice in ["OFFLINE", "Offline", "offline ", " offline", "mock"]:
            res = self._run_cli([
                "ingest-meeting",
                "--input", str(self.sample_txt),
                "--project", "project_nexus",
                "--meeting", "AdvMeeting",
                "--date", "09/26/2026",
                "--provider", bad_choice,
            ])
            self.assertNotEqual(res.returncode, 0, f"Expected rejection for {bad_choice}")
            self.assertIn("invalid choice", res.stderr)

    def test_adversarial_mock_flag_injection_across_subcommands(self) -> None:
        """Adversarial attempt: inject --mock across different CLI entrypoints."""
        subcommands = [
            ("ingest-meeting", ["--input", str(self.sample_txt)]),
            ("ingest-document", ["--input", str(self.sample_txt)]),
            ("ask", ["--question", "What is the status?"]),
            ("intake-publish", ["--package", str(self.sample_txt)]),
        ]
        for subcmd, base_args in subcommands:
            res = self._run_cli([subcmd, *base_args, "--mock"])
            self.assertNotEqual(res.returncode, 0, f"Expected unrecognized --mock on {subcmd}")
            self.assertIn("unrecognized arguments: --mock", res.stderr)

    def test_adversarial_allow_unjudged_injection_across_subcommands(self) -> None:
        """Adversarial attempt: inject --allow-unjudged-package across different CLI entrypoints."""
        subcommands = [
            ("ingest-meeting", ["--input", str(self.sample_txt)]),
            ("ingest-document", ["--input", str(self.sample_txt)]),
            ("intake-publish", ["--package", str(self.sample_txt)]),
        ]
        for subcmd, base_args in subcommands:
            res = self._run_cli([subcmd, *base_args, "--allow-unjudged-package"])
            self.assertNotEqual(res.returncode, 0, f"Expected unrecognized --allow-unjudged-package on {subcmd}")
            self.assertIn("unrecognized arguments: --allow-unjudged-package", res.stderr)

    def test_adversarial_offline_reasoning_injection_across_subcommands(self) -> None:
        """Adversarial attempt: inject purged offline reasoning flags."""
        for flag in ["--offline-ingestion-reasoning", "--offline-external-l2"]:
            res = self._run_cli(["ingest-meeting", "--input", str(self.sample_txt), flag])
            self.assertNotEqual(res.returncode, 0, f"Expected unrecognized {flag}")
            self.assertIn(f"unrecognized arguments: {flag}", res.stderr)

    def test_adversarial_forged_mock_decision_blocked_by_release_gate(self) -> None:
        """Adversarial attempt: forge final_release_decision with valid status but mock flag."""
        pkg = self.output_dir / "intake_package.json"
        pkg.write_text(
            json.dumps({
                "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
                "package_id": "forged-pkg-001",
            }),
            encoding="utf-8",
        )
        decision = self.output_dir / "final_release_decision.json"
        decision.write_text(
            json.dumps({
                "status": "PASS",
                "final_pass": True,
                "authoritative_write_authorized": True,
                "output_3_llm_score_pass": True,
                "is_mock": True,
            }),
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeError) as ctx:
            verify_authoritative_release_gate([pkg])
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))

    def test_adversarial_forged_short_circuit_decision_blocked(self) -> None:
        """Adversarial attempt: forge authoritative write with short_circuit=True."""
        pkg = self.output_dir / "intake_package.json"
        pkg.write_text(
            json.dumps({
                "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
                "package_id": "forged-pkg-002",
            }),
            encoding="utf-8",
        )
        decision = self.output_dir / "final_release_decision.json"
        decision.write_text(
            json.dumps({
                "status": "PASS",
                "authoritative_write_authorized": True,
                "output_3_llm_score_pass": True,
                "short_circuit": True,
                "short_circuit_reason": "DETERMINISTIC_QA_COLLAPSE",
            }),
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeError) as ctx:
            verify_authoritative_release_gate([pkg])
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))
        self.assertIn("Evaluation was short-circuited", str(ctx.exception))

    def test_adversarial_publish_helper_rejects_mock_mutation(self) -> None:
        """Adversarial attempt: directly call publish_graph_if_requested with is_mock=True."""
        pkg = self.output_dir / "intake_package.json"
        pkg.write_text("{}", encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            publish_graph_if_requested(
                mutate_graph=True,
                intake_pkg_path=pkg,
                metadata={"is_mock": True, "project": "project_nexus"},
                written={"03_graph": str(self.output_dir / "03_graph.json")},
                directive_id="dir-adv",
                reviewer="adversary",
                rationale="bypass",
                embedding_backend="bge-m3-dev",
            )
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
