#!/usr/bin/env python3
"""Focused fail-closed tests for the BGE-M3 production preflight."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli import main as cli_main  # noqa: E402
from dual_pipeline.core import digest, sha256_file  # noqa: E402
from dual_pipeline.index import FlagEmbeddingBackend  # noqa: E402
from dual_pipeline.preflight import (  # noqa: E402
    PreflightConfig,
    check_cpu,
    check_model_manifest,
    check_offline_bge_m3_smoke,
    check_pip_dependencies,
    check_production_authorization,
    check_report_destination,
    check_runtime_root,
    check_seed_graph,
    check_tracked_secrets,
    load_seed_contract,
    run_preflight,
)


@pytest.mark.heavy_model
class BgeM3PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-bge-preflight-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_cpu_runtime_contract_passes_without_cuda(self) -> None:
        result = check_cpu()
        self.assertEqual("PASS", result["status"])
        self.assertEqual("cpu", result["device"])
        self.assertEqual(10.0, result["tensor_probe"])

    def test_batch_encoding_passes_the_bounded_batch_size_to_flagembedding(self) -> None:
        backend = object.__new__(FlagEmbeddingBackend)
        backend._model = Mock()
        backend._model.encode.return_value = {
            "dense_vecs": [[1.0] * 1024, [1.0] * 1024],
            "lexical_weights": [{"token": 1.0}, {"token": 1.0}],
            "colbert_vecs": [
                [[1.0, 0.0], [0.0, 1.0]],
                [[1.0, 0.0], [0.0, 1.0]],
            ],
        }
        backend._assert_cpu_residency = Mock()

        encoded = backend.encode_many(["first", "second"])

        self.assertEqual(2, len(encoded))
        self.assertEqual(
            2, backend._model.encode.call_args.kwargs["batch_size"]
        )

    def test_runtime_and_report_paths_fail_closed(self) -> None:
        runtime = self.root / "runtime"
        result = check_runtime_root(runtime)
        self.assertEqual("PASS", result["status"])
        self.assertEqual([], list(runtime.iterdir()))
        (runtime / "prior-evidence.json").write_text("{}", encoding="utf-8")
        self.assertEqual("FAIL", check_runtime_root(runtime)["status"])
        self.assertEqual(
            "FAIL",
            check_report_destination(runtime / "receipt.json", runtime)["status"],
        )

    def test_unsafe_path_layout_is_rejected_before_any_write(self) -> None:
        model = self.root / "model"
        model.mkdir()
        runtime = model / "runtime"
        report = self.root / "receipt.json"
        config = PreflightConfig(
            runtime_root=runtime,
            graph=self.root / "graph.sqlite",
            model_path=model,
            model_revision="a" * 40,
            model_manifest=self.root / "manifest.json",
            report=report,
        )
        with self.assertRaisesRegex(ValueError, "unsafe preflight path layout"):
            run_preflight(config)
        self.assertFalse(runtime.exists())
        self.assertFalse(report.exists())

    def test_seed_graph_requires_exact_hash_and_integrity(self) -> None:
        graph = self.root / "seed.sqlite"
        connection = sqlite3.connect(graph)
        try:
            connection.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY)")
            connection.commit()
        finally:
            connection.close()
        graph_hash = sha256_file(graph)
        passed = check_seed_graph(graph, graph_hash)
        self.assertEqual("PASS", passed["status"])
        self.assertEqual("ok", passed["integrity_check"])
        self.assertEqual("FAIL", check_seed_graph(graph, "0" * 64)["status"])

    def test_real_seed_contract_helper_returns_a_pass_check(self) -> None:
        expected = "a" * 64
        (self.root / "release_state.json").write_text(
            json.dumps({"graph_sha256": expected}), encoding="utf-8"
        )
        (self.root / "manifest.json").write_text(
            json.dumps({"baseline": {"database_sha256": expected}}),
            encoding="utf-8",
        )
        result = load_seed_contract(self.root)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(expected, result["expected_sha256"])
        self.assertEqual(64, len(result["release_state_sha256"]))
        self.assertEqual(64, len(result["package_manifest_sha256"]))

    def test_tracked_secret_scan_reports_type_and_path_not_value(self) -> None:
        safe = self.root / "safe.txt"
        safe.write_text("ordinary configuration", encoding="utf-8")
        self.assertEqual(
            "PASS",
            check_tracked_secrets(self.root, tracked_paths=[safe])["status"],
        )
        secret = self.root / "credential.pem"
        secret.write_bytes(b"-----BEGIN " + b"PRIVATE KEY-----\nnot-a-real-key")
        failed = check_tracked_secrets(self.root, tracked_paths=[safe, secret])
        self.assertEqual("FAIL", failed["status"])
        rendered = json.dumps(failed)
        self.assertIn("private_key", rendered)
        self.assertNotIn("not-a-real-key", rendered)

    def test_pip_check_timeout_is_bounded_and_reason_coded(self) -> None:
        class StuckPipProcess:
            returncode: int | None = None

            def __init__(self) -> None:
                self.killed = threading.Event()

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:
                self.returncode = -9
                self.killed.set()

            def communicate(self) -> tuple[str, str]:
                self.killed.wait(timeout=1)
                return "", ""

        process = StuckPipProcess()
        with patch("dual_pipeline.preflight.subprocess.Popen", return_value=process):
            result = check_pip_dependencies(timeout_seconds=1)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual("PIP_CHECK_TIMEOUT", result["reason_code"])
        self.assertEqual(-9, result["exit_code"])
        self.assertTrue(process.killed.is_set())

    @unittest.skipUnless(
        os.environ.get("NEE_RUN_REAL_BGE_SMOKE") == "1",
        "set NEE_RUN_REAL_BGE_SMOKE=1 to exercise the pinned local BGE-M3 smoke",
    )
    def test_offline_smoke_requires_cpu_and_all_three_outputs(self) -> None:
        revision = "5617a9f61b028005a4858fdac845db406aefb181"
        result = check_offline_bge_m3_smoke(
            Path(rf"C:\NEE\models\bge-m3\{revision}"),
            Path(rf"C:\NEE\models\bge-m3\bge-m3-manifest-{revision}.json"),
            revision,
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual(1024, result["dense_dimensions"])
        self.assertGreater(result["sparse_weight_count"], 0)
        self.assertGreater(result["colbert_token_count"], 0)
        self.assertNotIn("production preflight validates", json.dumps(result))
        self.assertNotEqual("1", os.environ.get("TRANSFORMERS_OFFLINE"))

    def test_model_manifest_revision_must_match_tracked_pin(self) -> None:
        pin_path = self.root / "bge-m3-model.json"
        pinned_revision = "a" * 40
        pin_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "model_id": "BAAI/bge-m3",
                    "revision": pinned_revision,
                    "revision_type": "git_commit",
                    "source": f"https://huggingface.co/BAAI/bge-m3/tree/{pinned_revision}",
                    "snapshot_policy": "complete",
                    "network_policy_at_runtime": "offline",
                    "expected_dense_dimensions": 1024,
                    "expected_manifest_digest": "c" * 64,
                    "expected_snapshot_file_count": 1,
                    "expected_snapshot_size_bytes": 123,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "tracked immutable pin"):
            check_model_manifest(
                self.root / "model",
                self.root / "manifest.json",
                "b" * 40,
                pin_path,
            )
        verified = {
            "status": "PASS",
            "model_id": "BAAI/bge-m3",
            "model_revision": pinned_revision,
            "manifest_digest": "c" * 64,
            "file_count": 1,
            "snapshot_size_bytes": 123,
        }
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_digest": "c" * 64,
                    "files": [{"size_bytes": 123}],
                }
            ),
            encoding="utf-8",
        )
        with patch(
            "dual_pipeline.preflight.validate_bge_model_manifest",
            return_value=verified,
        ):
            result = check_model_manifest(
                self.root / "model",
                self.root / "manifest.json",
                pinned_revision,
                pin_path,
            )
        self.assertEqual("PASS", result["status"])
        self.assertEqual("c" * 64, result["model_manifest_digest"])

    def test_strict_authorization_binds_resources_and_manifest(self) -> None:
        copilot = self.root / "copilot.json"
        architecture = self.root / "architecture.json"
        question = self.root / "question.txt"
        copilot.write_text('{"private":"copilot content"}', encoding="utf-8")
        architecture.write_text('{"private":"architecture content"}', encoding="utf-8")
        question.write_text("  What   PDLC opportunity?  ", encoding="utf-8")
        manifest_digest = "b" * 64
        context = Mock()
        context.approved_model_manifest_digest = manifest_digest
        context.audit_record.return_value = {
            "subject": "operator",
            "token_digest": "c" * 64,
            "context_digest": "d" * 64,
        }
        config = PreflightConfig(
            runtime_root=self.root / "runtime",
            graph=self.root / "graph.sqlite",
            model_path=self.root / "model",
            model_revision="a" * 40,
            model_manifest=self.root / "manifest.json",
            report=self.root / "receipt.json",
            strict_production=True,
            auth_token=self.root / "token.jwt",
            auth_public_key=self.root / "public.pem",
            auth_issuer="https://identity.example/nee",
            auth_audience="nee-production",
            copilot_package=copilot,
            architecture_package=architecture,
            pdlc_question_file=question,
            approval_id="APPROVAL-123",
        )
        with patch(
            "dual_pipeline.preflight.load_production_authorization_context",
            return_value=context,
        ) as loader, patch(
            "dual_pipeline.preflight.validate_file",
            side_effect=[
                {"contract_id": "urn:nee:dual-pipeline:copilot-intake:1.0.0"},
                {
                    "contract_id": (
                        "urn:nee:dual-pipeline:architecture-diagram-intake:1.0.0"
                    )
                },
            ],
        ):
            result = check_production_authorization(config, manifest_digest)
        self.assertEqual("PASS", result["status"])
        loader.assert_called_once()
        resources = context.require_resources.call_args.args[0]
        self.assertEqual(
            {"architecture_package", "copilot_package", "pdlc_question", "release_approval"},
            set(resources),
        )
        rendered = json.dumps(result)
        self.assertNotIn("copilot content", rendered)
        self.assertNotIn("What PDLC", rendered)
        self.assertNotIn("APPROVAL-123", rendered)

    def test_infrastructure_receipt_is_sealed_but_not_production_ready(self) -> None:
        runtime = self.root / "runtime"
        report = self.root / "receipt.json"
        config = PreflightConfig(
            runtime_root=runtime,
            graph=self.root / "graph.sqlite",
            model_path=self.root / "model",
            model_revision="a" * 40,
            model_manifest=self.root / "manifest.json",
            report=report,
            repo_root=self.root,
        )
        passed = {"status": "PASS"}
        manifest = {
            "status": "PASS",
            "model_manifest_digest": "b" * 64,
            "model_revision": "a" * 40,
        }
        seed_contract = {
            "status": "PASS",
            "expected_sha256": "c" * 64,
            "release_state_sha256": "d" * 64,
            "package_manifest_sha256": "e" * 64,
        }
        seed = {"status": "PASS", "sha256": "c" * 64}
        smoke = {"status": "PASS", "model_manifest_digest": "b" * 64}
        with (
            patch("dual_pipeline.preflight.check_report_destination", return_value=passed),
            patch("dual_pipeline.preflight.check_runtime_root", return_value=passed),
            patch("dual_pipeline.preflight.check_runtime_contract", return_value=passed),
            patch("dual_pipeline.preflight.check_pip_dependencies", return_value=passed),
            patch("dual_pipeline.preflight.check_locked_environment", return_value=passed),
            patch("dual_pipeline.preflight.check_cpu", return_value=passed),
            patch("dual_pipeline.preflight.check_model_manifest", return_value=manifest),
            patch("dual_pipeline.preflight.load_seed_contract", return_value=seed_contract),
            patch("dual_pipeline.preflight.check_seed_graph", return_value=seed),
            patch("dual_pipeline.preflight.check_tracked_secrets", return_value=passed),
            patch("dual_pipeline.preflight.check_offline_bge_m3_smoke", return_value=smoke),
        ):
            receipt = run_preflight(config)
        self.assertEqual("PASS", receipt["status"])
        self.assertFalse(receipt["production_execution_ready"])
        self.assertEqual("SKIP", receipt["checks"]["production_authorization"]["status"])
        stored = json.loads(report.read_text(encoding="utf-8"))
        sealed = dict(stored)
        receipt_digest = sealed.pop("receipt_digest")
        self.assertEqual(receipt_digest, digest(sealed))

    def test_strict_mode_missing_auth_cannot_pass(self) -> None:
        config = PreflightConfig(
            runtime_root=self.root / "runtime",
            graph=self.root / "graph.sqlite",
            model_path=self.root / "model",
            model_revision="a" * 40,
            model_manifest=self.root / "manifest.json",
            report=self.root / "receipt.json",
            strict_production=True,
        )
        result = check_production_authorization(config, "b" * 64)
        self.assertEqual("FAIL", result["status"])
        self.assertIn("all four", result["error"])

    def test_cli_returns_nonzero_for_failed_preflight_receipt(self) -> None:
        arguments = [
            "production-preflight",
            "--model-path",
            str(self.root / "model"),
            "--model-manifest",
            str(self.root / "manifest.json"),
            "--model-revision",
            "a" * 40,
            "--report",
            str(self.root / "receipt.json"),
        ]
        with (
            patch("dual_pipeline.cli.execute", return_value={"status": "FAIL"}),
            patch("builtins.print"),
        ):
            self.assertEqual(1, cli_main(arguments))


if __name__ == "__main__":
    unittest.main(verbosity=2)
