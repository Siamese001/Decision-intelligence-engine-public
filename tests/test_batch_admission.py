#!/usr/bin/env python3
"""Focused coverage for source-bound scalable batch admission."""
from __future__ import annotations

import copy
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.authorization import AuthorizationContext  # noqa: E402
from dual_pipeline.batch_admission import (  # noqa: E402
    BATCH_MANIFEST_RESOURCE,
    create_manifest,
    load_manifest,
    policy_approval_id,
)
from dual_pipeline.cli import build_parser, execute  # noqa: E402
from dual_pipeline.completion import IngestionCompletionGuard  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    digest,
    read_json,
    seal_artifact,
    sha256_bytes,
    sha256_file,
    write_json,
    write_json_exclusive,
)
from dual_pipeline.index import CLASSIFICATION_RANK, HybridRetriever  # noqa: E402
from dual_pipeline.intake import IntakeEngine  # noqa: E402
from dual_pipeline.release import ReleaseManager, release_action_resources  # noqa: E402
from dual_pipeline.retrieval_release import RetrievalReleaseEvaluator  # noqa: E402
from dual_pipeline.retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


class _ProductionTestBackend(BgeM3TestBackend):
    """Real BGE-M3 executor carrying production authorization posture."""

    backend_id = "flagembedding-bge-m3"
    production_qualified = True


class BatchAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-batch-admission-")
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        base_pkg = read_json(
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "evidence-envelope-intake.json"
        )
        self.first_package = self.root / "evidence-envelope-first.json"
        first = copy.deepcopy(base_pkg)
        first["meeting"]["classification"] = "internal"
        write_json(self.first_package, seal_artifact(first))

        self.second_package = self.root / "evidence-envelope-follow-up.json"
        follow_up = copy.deepcopy(base_pkg)
        follow_up["artifact_id"] = "evidence-envelope-batch-follow-up-20260726"
        follow_up["meeting"]["meeting_id"] = "meeting-batch-follow-up-20260726"
        follow_up["meeting"]["title"] = "Follow-up Evidence Envelope Meeting"
        follow_up["meeting"]["classification"] = "internal"
        write_json(self.second_package, seal_artifact(follow_up))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _authorization(
        *,
        token_id: str,
        manifest_digest: str,
        resources: dict[str, str],
        capabilities: tuple[str, ...],
        classification_clearance: str = "internal",
        audiences: tuple[str, ...] = ("internal_consulting", "nee-engineering"),
    ) -> AuthorizationContext:
        now = int(time.time())
        bounded = {
            "subject": "batch-admission-service",
            "issuer": "https://identity.example/nee",
            "relying_party": "nee-production",
            "token_id": token_id,
            "issued_at": now - 5,
            "expires_at": now + 300,
            "classification_clearance": classification_clearance,
            "audiences": audiences,
            "capabilities": capabilities,
            "authorized_resources": resources,
            "approved_model_manifest_digest": manifest_digest,
            "token_digest": digest(["batch-test-token", token_id]),
        }
        return AuthorizationContext(
            **bounded,
            context_digest=digest(["batch-test-context", bounded]),
        )

    def _manifest(self, *, admission_mode: str) -> Path:
        path = self.root / f"batch-{admission_mode.casefold()}.json"
        manifest = create_manifest(
            artifact_id=f"batch-admission-{admission_mode.casefold()}-20260726",
            packages=[self.first_package, self.second_package],
            policy_id="nee-standard-candidate-batch",
            policy_version="1.0.0",
            admission_mode=admission_mode,
        )
        write_json_exclusive(path, manifest)
        return path

    def test_signed_policy_admits_two_same_kind_packages_with_one_manifest(self) -> None:
        backend = _ProductionTestBackend()
        manager = ReleaseManager(self.runtime, self.graph)
        bootstrap_auth = self._authorization(
            token_id="bootstrap",
            manifest_digest=backend.model_manifest_digest,
            resources={},
            capabilities=("knowledge:release",),
        )
        active = manager.bootstrap(backend, authorization_context=bootstrap_auth)
        manifest_path = self._manifest(admission_mode="SIGNED_POLICY")
        _manifest, binding = load_manifest(manifest_path)
        guard = IngestionCompletionGuard(self.runtime, self.graph)
        directive_id = "directive-signed-policy-batch"
        opened = guard.open(
            directive_id=directive_id,
            scope="Admit two exact low-risk candidate packages as one batch",
            packages=[self.first_package, self.second_package],
            batch_admission_manifest=manifest_path,
        )
        self.assertTrue(
            opened["classification"]["auto_candidate_admission_eligible"]
        )
        self.assertFalse(opened["classification"]["human_approval_required"])
        self.assertEqual(
            "CANDIDATE_AUTO_ADMISSION_ELIGIBLE",
            opened["classification"]["embedding_recommendation"],
        )
        approval_id = policy_approval_id(
            directive_id=directive_id,
            package_set_digest=opened["package_set_digest"],
            binding=binding,
        )
        authorization = self._authorization(
            token_id="batch-admission-and-release",
            manifest_digest=backend.model_manifest_digest,
            resources={
                BATCH_MANIFEST_RESOURCE: binding.manifest_file_sha256,
                "release_approval": sha256_bytes(approval_id.encode("utf-8")),
            },
            capabilities=("knowledge:intake", "knowledge:batch-admit", "knowledge:release"),
        )
        admitted = guard.record_signed_batch_policy_admission(
            directive_id,
            authorization_context=authorization,
        )
        self.assertEqual("SIGNED_BATCH_POLICY_ADMISSION", admitted["approval"]["binding_type"])

        intake = IntakeEngine(
            manager._resolve(active["graph_path"]),
            self.runtime,
            retriever=HybridRetriever(
                manager._resolve(active["sidecar_path"]), backend
            ),
            retrieval_audiences=["internal_consulting", "nee-engineering"],
            authorization_context=authorization,
            active_pair=active,
            batch_admission_manifest=manifest_path,
        )
        first = intake.ingest(self.first_package)
        second = intake.ingest(self.second_package)
        self.assertEqual(
            binding.manifest_file_sha256,
            first["batch_admission"]["manifest_file_sha256"],
        )
        self.assertGreater(first["graph_operation_count"], 0)
        self.assertGreater(second["graph_operation_count"], 0)

        release_validation = guard.validate_release_request(
            directive_id, run_ids=[first["run_id"], second["run_id"]]
        )
        receipt = manager.prepare_many(
            run_ids=[first["run_id"], second["run_id"]],
            approval_id=release_validation["approval_id"],
            backend=backend,
            authorization_context=authorization,
        )
        self.assertEqual(2, len(receipt["component_runs"]))
        self.assertTrue(receipt["production_qualified"])

        receipt_path = (
            self.runtime
            / "releases"
            / f"release-{receipt['next_pair']['generation_id']}.json"
        )
        suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
            self.runtime, self.graph
        ).build(receipt_path)
        suite_path = Path(suite_resolution["path"])
        suite = read_json(suite_path)
        suite_clearance = max(
            (case["classification"] for case in suite["cases"]),
            key=CLASSIFICATION_RANK.__getitem__,
        )
        suite_audiences = tuple(
            sorted(
                {
                    audience
                    for case in suite["cases"]
                    for audience in case["audiences"]
                }
            )
        )
        evaluation_auth = self._authorization(
            token_id="batch-receipt-retrieval-evaluation",
            manifest_digest=backend.model_manifest_digest,
            resources={
                "retrieval_release_receipt": receipt["receipt_digest"],
                "retrieval_release_suite": sha256_file(suite_path),
                "retrieval_release_policy": suite["acceptance_policy"][
                    "policy_digest"
                ],
            },
            capabilities=("knowledge:query",),
            classification_clearance=suite_clearance,
            audiences=suite_audiences,
        )
        evaluation = RetrievalReleaseEvaluator(self.runtime, self.graph).evaluate(
            receipt_path,
            suite_path,
            backend=backend,
            authorization_context=evaluation_auth,
        )
        self.assertEqual("PASS", evaluation["evaluation"]["status"])

        activation_auth = self._authorization(
            token_id="batch-receipt-activation",
            manifest_digest=backend.model_manifest_digest,
            resources={
                **release_action_resources(receipt, "activate"),
                "retrieval_release_evaluation": evaluation["evaluation"][
                    "canonical_digest"
                ]["value"],
            },
            capabilities=("knowledge:release",),
        )
        activation = manager.activate(receipt_path, authorization_context=activation_auth)
        completion = guard.complete(directive_id, receipt_path)
        self.assertEqual("ACTIVE", activation["status"])
        self.assertEqual("COMPLETE", completion["directive_status"])
        self.assertEqual("GRAPH_CHANGED", completion["completion"]["mutation_outcome"])

    def test_batch_release_command_completes_one_human_approved_batch(self) -> None:
        runtime = self.root / "service-runtime"
        manager = ReleaseManager(runtime, self.graph)
        backend = BgeM3TestBackend()
        active = manager.bootstrap(backend)
        manifest_path = self._manifest(admission_mode="HITL_REQUIRED")
        guard = IngestionCompletionGuard(runtime, self.graph)
        directive_id = "directive-service-batch"
        opened = guard.open(
            directive_id=directive_id,
            scope="Run one automated release after one batch HITL decision",
            packages=[self.first_package, self.second_package],
            batch_admission_manifest=manifest_path,
        )
        intake = IntakeEngine(
            manager._resolve(active["graph_path"]),
            runtime,
            retriever=HybridRetriever(manager._resolve(active["sidecar_path"]), backend),
            retrieval_audiences=["internal_consulting", "nee-engineering"],
            active_pair=active,
            batch_admission_manifest=manifest_path,
        )
        first = intake.ingest(self.first_package)
        second = intake.ingest(self.second_package)
        guard.record_human_decision(
            directive_id,
            decision="APPROVE",
            classification_digest=opened["classification"]["classification_digest"],
            reviewer="batch-operator",
            rationale="One decision covers this exact homogeneous candidate batch.",
        )
        with mock.patch("dual_pipeline.cli._backend", return_value=backend):
            result = execute(
                build_parser().parse_args(
                    [
                        "intake-batch-release",
                        "--directive-id",
                        directive_id,
                        "--run-id",
                        first["run_id"],
                        "--run-id",
                        second["run_id"],
                        "--runtime-root",
                        str(runtime),
                        "--graph",
                        str(self.graph),
                    ]
                )
            )
        self.assertEqual("ACTIVE", result["status"])
        self.assertEqual("COMPLETE", result["ingestion_completion"]["directive_status"])
        self.assertEqual(2, len(result["receipt"]["component_runs"]))

    def test_bge_batch_release_automatically_evaluates_before_activation(self) -> None:
        runtime = self.root / "bge-delivery-runtime"
        backend = BgeM3TestBackend()
        manager = ReleaseManager(runtime, self.graph)
        active = manager.bootstrap(backend)
        guard = IngestionCompletionGuard(runtime, self.graph)
        directive_id = "directive-bge-delivery-gate"
        opened = guard.open(
            directive_id=directive_id,
            scope="Require sealed BGE evaluation before candidate activation.",
            packages=[self.first_package],
        )
        intake = IntakeEngine(
            manager._resolve(active["graph_path"]),
            runtime,
            retriever=HybridRetriever(manager._resolve(active["sidecar_path"]), backend),
            retrieval_audiences=["internal_consulting", "nee-engineering"],
            active_pair=active,
        ).ingest(self.first_package)
        guard.record_human_decision(
            directive_id,
            decision="APPROVE",
            classification_digest=opened["classification"]["classification_digest"],
            reviewer="bge-release-operator",
            rationale="The exact source package may enter the candidate lane.",
        )

        arguments = [
            "intake-batch-release",
            "--directive-id",
            directive_id,
            "--run-id",
            intake["run_id"],
            "--runtime-root",
            str(runtime),
            "--graph",
            str(self.graph),
            "--embedding-backend",
            "bge-m3-dev",
            "--allow-dev-bge-m3",
        ]
        with mock.patch("dual_pipeline.cli._backend", return_value=backend):
            result = execute(build_parser().parse_args(arguments))

        self.assertEqual("ACTIVE", result["status"], result)
        self.assertEqual(
            "automatic",
            result["retrieval_release_suite_resolution"]["source"],
        )
        self.assertTrue(
            Path(result["retrieval_release_suite_resolution"]["path"]).is_file()
        )
        self.assertEqual("PASS", result["retrieval_evaluation"]["status"])
        self.assertNotEqual(active["generation_id"], manager.active_pair()["generation_id"])
        self.assertTrue(guard.status(directive_id)["terminal"])
        self.assertEqual(
            "COMPLETE",
            result["ingestion_completion"]["directive_status"],
        )

    def test_intake_publish_automatically_admits_a_signed_low_risk_batch(self) -> None:
        runtime = self.root / "unified-service-runtime"
        manifest_path = self._manifest(admission_mode="SIGNED_POLICY")
        _manifest, binding = load_manifest(manifest_path)
        directive_id = "directive-unified-signed-policy"
        package_set_digest = digest(
            IngestionCompletionGuard._package_bindings(
                [self.first_package, self.second_package]
            )
        )
        approval_id = policy_approval_id(
            directive_id=directive_id,
            package_set_digest=package_set_digest,
            binding=binding,
        )
        authorization = self._authorization(
            token_id="unified-signed-policy",
            manifest_digest="b" * 64,
            resources={
                BATCH_MANIFEST_RESOURCE: binding.manifest_file_sha256,
                "release_approval": sha256_bytes(approval_id.encode("utf-8")),
            },
            capabilities=("knowledge:intake", "knowledge:batch-admit", "knowledge:release"),
        )
        arguments = [
            "intake-publish",
            "--directive-id",
            directive_id,
            "--package",
            str(self.first_package),
            "--package",
            str(self.second_package),
            "--batch-manifest",
            str(manifest_path),
            "--runtime-root",
            str(runtime),
            "--graph",
            str(self.graph),
        ]
        with mock.patch(
            "dual_pipeline.cli._authorization", return_value=authorization
        ), mock.patch(
            "dual_pipeline.cli._backend", return_value=BgeM3TestBackend()
        ):
            result = execute(build_parser().parse_args(arguments))
        self.assertEqual("COMPLETE", result["status"])
        self.assertEqual(
            "SIGNED_BATCH_POLICY_ADMISSION", result["admission"]["binding_type"]
        )
        self.assertFalse(result["classification"]["human_approval_required"])
        self.assertEqual(2, len(result["runs"]))
        self.assertEqual(
            "COMPLETE", result["ingestion_completion"]["directive_status"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
