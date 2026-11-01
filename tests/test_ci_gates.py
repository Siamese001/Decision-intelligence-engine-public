from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

from dual_pipeline.core import canonical_bytes, sha256_bytes
from tests.embedding_fixture import BgeM3TestBackend
from tools.run_ci_gates import (
    BGE_TESTS,
    DETERMINISTIC_TESTS,
    _claim_profile_output_root,
    _terminate_process_tree,
    _write_profile_receipt,
    bge_commands,
    bge_environment,
    deterministic_commands,
    main,
    run_commands,
)


class CiGateProfileTests(unittest.TestCase):
    def test_profile_output_root_is_claimed_exclusively(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-ci-output-root-") as directory:
            output_root = Path(directory) / "profile"
            self.assertEqual(output_root, _claim_profile_output_root(output_root))
            with self.assertRaisesRegex(
                FileExistsError, "CI profile output root must be a new directory"
            ):
                _claim_profile_output_root(output_root)

    def test_deterministic_profile_excludes_real_bge_release_evaluation(self) -> None:
        commands = deterministic_commands(
            python=sys.executable,
            repository_root=Path("."),
            output_root=Path(".local/ci"),
        )
        rendered = "\n".join(" ".join(command) for command in commands)
        self.assertNotIn("tests.test_retrieval_release_evaluation ", rendered)
        self.assertIn("RetrievalReleaseStaticGateTests", rendered)
        self.assertIn("tools/validate_dual_pipeline_contracts.py", rendered)
        self.assertIn(
            "DualPipelineWave1Tests.test_01_complete_contract_suite_passes", rendered
        )
        self.assertIn(
            "DualPipelineEvaluationHarnessTests.test_route_confusion_suite_is_provider_embedding_and_l2_free",
            rendered,
        )
        self.assertIn("tests.test_dev_retrieval_smoke", DETERMINISTIC_TESTS)
        self.assertNotIn("tests.test_conditional_l2", DETERMINISTIC_TESTS)
        self.assertIn("tests.test_drift_guard", rendered)
        self.assertIn("tests.test_eval_harness", rendered)
        self.assertIn("eval-route-run", rendered)
        self.assertIn("query-route-adversarial-e3.json", rendered)
        self.assertIn("query-route-robustness-e3.json", rendered)
        self.assertIn("tests.test_bge_m3_preflight", rendered)
        self.assertIn("tests.test_bge_m3_runtime_qualification", rendered)

    def test_bge_profile_keeps_real_e2e_suite_explicit(self) -> None:
        commands = bge_commands(python=sys.executable)
        self.assertEqual(
            [sys.executable, "-B", "-m", "unittest", "-v", *BGE_TESTS],
            commands[1],
        )
        self.assertEqual([sys.executable, "-B", "-c"], commands[0][:3])
        self.assertIn("check_cpu", commands[0][3])
        compile(commands[0][3], "<bge-preflight>", "exec")
        self.assertIn("tests.test_bge_query_e2e.BgeQueryE2ETests", commands[1])
        self.assertNotIn("tests.test_retrieval_release_evaluation", DETERMINISTIC_TESTS)
        self.assertIn("tests.test_conditional_l2", BGE_TESTS)

    def test_bge_profile_can_bind_an_explicit_pinned_model_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-ci-bge-") as directory:
            root = Path(directory)
            model = root / "model"
            model.mkdir()
            manifest = root / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")

            environment = bge_environment(
                base_environment={"PATH": "fixture-path"},
                model_path=model,
                manifest_path=manifest,
            )

            self.assertEqual(str(model.resolve()), environment["NEE_BGE_M3_MODEL_PATH"])
            self.assertEqual(
                str(manifest.resolve()), environment["NEE_BGE_M3_MODEL_MANIFEST"]
            )

    def test_bge_profile_rejects_partial_snapshot_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be supplied together"):
            bge_environment(
                base_environment={},
                model_path=Path("model"),
                manifest_path=None,
            )

    def test_cli_rejects_partial_bge_snapshot_configuration(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--profile", "bge", "--bge-model-path", "model"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("must be supplied together", stderr.getvalue())

    @patch("tools.run_ci_gates._terminate_process_tree")
    @patch("tools.run_ci_gates.subprocess.Popen")
    def test_command_timeout_returns_a_distinct_failure(self, popen, terminate) -> None:
        process = popen.return_value
        process.pid = 1234
        process.wait.side_effect = subprocess.TimeoutExpired([sys.executable], 3)
        self.assertEqual(
            124,
            run_commands(
                commands=[[sys.executable, "-c", "pass"]],
                repository_root=Path("."),
                timeout_seconds=3,
                total_timeout_seconds=30,
            ),
        )
        popen.assert_called_once()
        command, kwargs = popen.call_args
        self.assertEqual([sys.executable, "-c", "pass"], command[0])
        self.assertEqual(Path("."), kwargs["cwd"])
        self.assertIn(
            "creationflags" if os.name == "nt" else "start_new_session",
            kwargs,
        )
        terminate.assert_called_once_with(process)

    @patch("tools.run_ci_gates.os.name", "nt")
    @patch("tools.run_ci_gates.subprocess.run")
    def test_windows_timeout_uses_recursive_process_termination(self, run) -> None:
        process = Mock(pid=4321)
        _terminate_process_tree(process)
        run.assert_called_once_with(
            ["taskkill", "/PID", "4321", "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        process.wait.assert_called_once_with(timeout=5)

    @patch.dict(os.environ, {"NEE_RUN_BGE_TESTS": "0"})
    def test_real_bge_fixture_requires_explicit_opt_in(self) -> None:
        with self.assertRaises(unittest.SkipTest):
            BgeM3TestBackend()

    def test_command_output_is_retained_in_a_profile_log(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-ci-log-test-") as directory:
            results: list[dict[str, object]] = []
            self.assertEqual(
                0,
                run_commands(
                    commands=[[sys.executable, "-c", "print('gate output')"]],
                    repository_root=Path("."),
                    timeout_seconds=3,
                    total_timeout_seconds=10,
                    command_results=results,
                    log_root=Path(directory),
                ),
            )
            self.assertEqual(1, len(results))
            log_path = Path(results[0]["log_path"])
            self.assertEqual("gate output\n", log_path.read_text(encoding="utf-8"))

    def test_profile_receipt_is_digest_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-ci-receipt-test-") as directory:
            path = Path(directory) / "deterministic-gate-receipt.json"
            result = _write_profile_receipt(
                path=path,
                profile="deterministic",
                repository_root=Path(__file__).resolve().parents[1],
                output_root=Path(directory),
                command_timeout_seconds=3,
                total_timeout_seconds=30,
                command_results=[{"status": "PASS", "return_code": 0}],
                status_code=0,
                started_at="2026-09-03T00:00:00+00:00",
            )
            payload = json.loads(result.read_text(encoding="utf-8"))
            digest = payload.pop("canonical_digest")["value"]
            self.assertEqual(digest, sha256_bytes(canonical_bytes(payload)))
            self.assertEqual("PASS", payload["status"])
            self.assertEqual("COMPLETED", payload["terminal_reason"])
            self.assertFalse(payload["production_qualified"])
