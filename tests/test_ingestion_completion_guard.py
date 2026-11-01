#!/usr/bin/env python3
"""Focused prevention tests for persistent ingestion completion directives."""
from __future__ import annotations

import contextlib
import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli import (  # noqa: E402
    _assert_backend_matches_pair,
    build_parser,
    execute,
)
from dual_pipeline.completion import IngestionCompletionGuard  # noqa: E402
from dual_pipeline.core import digest  # noqa: E402
from dual_pipeline.index import (  # noqa: E402
    HybridRetriever,
)
from dual_pipeline.intake import IntakeEngine  # noqa: E402
from dual_pipeline.release import ReleaseManager  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


class IngestionCompletionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-ingestion-completion-")
        self.scratch = Path(self.temp.name)
        self.runtime = self.scratch / "runtime"
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        self.backend = BgeM3TestBackend()
        self.manager = ReleaseManager(self.runtime, self.graph)
        self.bootstrap = self.manager.bootstrap(self.backend)
        self.guard = IngestionCompletionGuard(self.runtime, self.graph)
        self.copilot_package = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "copilot-meeting.json"
        )
        self.architecture_package = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "architecture-diagram.json"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _intake_engine(self) -> IntakeEngine:
        pair = self.manager.active_pair()
        return IntakeEngine(
            self.manager._resolve(pair["graph_path"]),
            self.runtime,
            retriever=HybridRetriever(
                self.manager._resolve(pair["sidecar_path"]), self.backend
            ),
            retrieval_audiences=["internal_consulting", "nee-engineering"],
            active_pair=pair,
        )

    def _cli(self, *arguments: str):
        with mock.patch("dual_pipeline.cli._backend", return_value=self.backend):
            return execute(build_parser().parse_args(list(arguments)))

    def test_cli_requires_directive_and_auto_closes_only_after_activation(self) -> None:
        parser = build_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "ingest-copilot",
                    "--package",
                    str(self.copilot_package),
                    "--runtime-root",
                    str(self.runtime),
                    "--graph",
                    str(self.graph),
                ]
            )
        batch_args = parser.parse_args(
            [
                "intake-release-prepare",
                "--directive-id",
                "directive-batch-parser",
                "--run-id",
                "run-first",
                "--run-id",
                "run-second",
                "--runtime-root",
                str(self.runtime),
                "--graph",
                str(self.graph),
            ]
        )
        self.assertEqual(["run-first", "run-second"], batch_args.run_id)

        directive_id = "directive-cli-full-lifecycle"
        opened = self._cli(
            "intake-directive-open",
            "--directive-id",
            directive_id,
            "--scope",
            "Ingest the exact Copilot fixture and activate its candidate release",
            "--package",
            str(self.copilot_package),
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        self.assertEqual("OPEN", opened["status"])
        self.assertFalse(opened["terminal"])
        self.assertEqual("internal", opened["classification"]["recommended_classification"])
        self.assertTrue(opened["classification"]["human_approval_required"])

        intake = self._cli(
            "ingest",
            "--directive-id",
            directive_id,
            "--package",
            str(self.copilot_package),
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        self.assertEqual("COMPLETED", intake["status"])
        self.assertEqual("OPEN", intake["ingestion_directive"]["status"])
        self.assertGreater(intake["graph_operation_count"], 0)
        with self.assertRaisesRegex(PermissionError, "no recorded HITL approval"):
            self.guard.validate_release_request(
                directive_id,
                run_ids=[intake["run_id"]],
            )

        approved = self._cli(
            "intake-directive-approve",
            "--directive-id",
            directive_id,
            "--decision",
            "APPROVE",
            "--classification-digest",
            opened["classification"]["classification_digest"],
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        self.assertEqual("HITL_CLASSIFICATION_APPROVAL", approved["approval"]["binding_type"])
        self.assertEqual(
            digest(approved["required_embedding_target"]),
            approved["approval"]["embedding_target_digest"],
        )
        self.assertEqual(
            "flagembedding-bge-m3-dev",
            approved["required_embedding_target"]["embedding_backend"],
        )

        prepared = self._cli(
            "intake-release-prepare",
            "--directive-id",
            directive_id,
            "--run-id",
            intake["run_id"],
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        receipt_path = Path(prepared["receipt_path"])
        with self.assertRaisesRegex(RuntimeError, "has not been activated"):
            self.guard.complete(directive_id, receipt_path)
        self.assertEqual("OPEN", self.guard.status(directive_id)["status"])

        evaluation = self._cli(
            "retrieval-release-evaluate",
            "--receipt",
            str(receipt_path),
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
            "--embedding-backend",
            "bge-m3-dev",
            "--allow-dev-bge-m3",
        )
        self.assertEqual("PASS", evaluation["evaluation"]["status"])

        activated = self._cli(
            "intake-release-activate",
            "--directive-id",
            directive_id,
            "--receipt",
            str(receipt_path),
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        completion = activated["ingestion_completion"]
        self.assertEqual("PASS", completion["status"])
        self.assertEqual("COMPLETE", completion["directive_status"])
        self.assertTrue(completion["terminal"])
        self.assertEqual("GRAPH_CHANGED", completion["completion"]["mutation_outcome"])
        self.assertNotEqual(
            completion["completion"]["graph_sha256_before"],
            completion["completion"]["graph_sha256_after"],
        )
        self.assertGreater(completion["completion"]["graph_operation_count"], 0)
        self.assertTrue(completion["completion"]["embedding_target_verified"])
        self.assertEqual(
            approved["required_embedding_target"],
            completion["completion"]["required_embedding_target"],
        )
        self.assertTrue(
            all(
                row["audit_status"] == "PASS"
                and row["activation_ledger_count"] == 1
                and row["automatic_policy_change_count"] == 0
                for row in completion["completion"]["run_receipts"]
            )
        )
        replay = self.guard.complete(directive_id, receipt_path)
        self.assertTrue(replay["idempotent"])

    def test_delivery_status_composes_receipts_from_admission_to_completion(self) -> None:
        directive_id = "directive-delivery-status"
        opened = self._cli(
            "intake-directive-open",
            "--directive-id",
            directive_id,
            "--scope",
            "Prove the delivery projection joins every lifecycle receipt.",
            "--package",
            str(self.copilot_package),
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        initial = self._cli(
            "intake-delivery-status",
            "--directive-id",
            directive_id,
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        self.assertEqual("ADMISSION_REQUIRED", initial["delivery_state"])

        intake = self._cli(
            "ingest",
            "--directive-id",
            directive_id,
            "--package",
            str(self.copilot_package),
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        self._cli(
            "intake-directive-approve",
            "--directive-id",
            directive_id,
            "--decision",
            "APPROVE",
            "--classification-digest",
            opened["classification"]["classification_digest"],
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        pending = self.guard.delivery_status(directive_id)
        self.assertEqual("INGESTION_OR_RELEASE_PENDING", pending["delivery_state"])

        prepared = self._cli(
            "intake-release-prepare",
            "--directive-id",
            directive_id,
            "--run-id",
            intake["run_id"],
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        with mock.patch.object(
            ReleaseManager,
            "validate_pair",
            side_effect=AssertionError("status must not rerun full pair validation"),
        ):
            ready = self.guard.delivery_status(directive_id)
        self.assertEqual("EVALUATION_REQUIRED", ready["delivery_state"])
        self.assertEqual("PUBLISHED", ready["sidecar"]["publication_state"])
        self.assertTrue(ready["sidecar"]["exists"])
        self.assertTrue(ready["sidecar"]["sha256_matches"])
        self.assertGreater(ready["release"]["graph_operation_count"], 0)
        self.assertTrue(ready["retrieval_evaluation"]["required"])
        self.assertFalse(ready["retrieval_evaluation"]["exists"])

        receipt_path = Path(prepared["receipt_path"])
        evaluation = self._cli(
            "retrieval-release-evaluate",
            "--receipt",
            str(receipt_path),
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
            "--embedding-backend",
            "bge-m3-dev",
            "--allow-dev-bge-m3",
        )
        self.assertEqual("PASS", evaluation["evaluation"]["status"])
        self.manager.activate(receipt_path)
        active = self.guard.delivery_status(directive_id)
        self.assertEqual("ACTIVE_AWAITING_COMPLETION", active["delivery_state"])
        self.assertTrue(active["activation"]["active_pair_matches"])

        self.guard.complete(directive_id, receipt_path)
        complete = self._cli(
            "intake-delivery-status",
            "--directive-id",
            directive_id,
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        self.assertEqual("COMPLETE", complete["delivery_state"])
        self.assertTrue(complete["terminal"])
        self.assertEqual("PUBLISHED", complete["sidecar"]["publication_state"])
        self.assertTrue(complete["activation"]["active_pair_matches"])

    def test_required_bge_m3_fails_before_hitl_on_non_bge_runtime(self) -> None:
        directive_id = "directive-requires-bge-m3"
        non_bge_pair = copy.deepcopy(self.manager.active_pair())
        non_bge_pair.update(
            {
                "embedding_backend": "non-bge-test-backend",
                "model_id": "not-bge-m3",
                "model_revision": None,
                "model_manifest_digest": None,
            }
        )
        with (
            mock.patch.object(ReleaseManager, "active_pair", return_value=non_bge_pair),
            self.assertRaisesRegex(PermissionError, "requires BGE-M3 embedding"),
        ):
            self._cli(
                "intake-directive-open",
                "--directive-id",
                directive_id,
                "--scope",
                "Publish only when HITL is bound to BGE-M3.",
                "--package",
                str(self.copilot_package),
                "--require-bge-m3",
                "--runtime-root",
                str(self.runtime),
                "--graph",
                str(self.graph),
            )
        with self.assertRaises(FileNotFoundError):
            self.guard.status(directive_id)
        publish_id = "directive-publish-requires-bge-m3"
        with (
            mock.patch.object(ReleaseManager, "active_pair", return_value=non_bge_pair),
            self.assertRaisesRegex(RuntimeError, "backend/model does not match the active pair"),
        ):
                self._cli(
                    "intake-publish",
                    "--directive-id",
                    publish_id,
                    "--package",
                    str(self.copilot_package),
                    "--require-bge-m3",
                    "--runtime-root",
                    str(self.runtime),
                    "--graph",
                    str(self.graph),
                )
        with self.assertRaises(FileNotFoundError):
            self.guard.status(publish_id)

    def test_embedding_target_mismatch_is_rejected_after_hitl(self) -> None:
        directive_id = "directive-target-mismatch"
        opened = self.guard.open(
            directive_id=directive_id,
            scope="Bind approval and release to one embedding target.",
            packages=[self.copilot_package],
        )
        self.guard.record_human_decision(
            directive_id,
            decision="APPROVE",
            classification_digest=opened["classification"][
                "classification_digest"
            ],
            reviewer="test-operator",
            rationale="Approve only for the recorded embedding target.",
        )
        mismatched_pair = copy.deepcopy(self.manager.active_pair())
        mismatched_pair["embedding_backend"] = "flagembedding-bge-m3-dev"
        mismatched_pair["model_id"] = "BAAI/bge-m3"
        mismatched_pair["model_revision"] = "a" * 40
        mismatched_pair["model_manifest_digest"] = "b" * 64
        with self.assertRaisesRegex(
            RuntimeError, "HITL-bound embedding target"
        ):
            self.guard._assert_pair_matches_embedding_target(
                self.guard.status(directive_id),
                mismatched_pair,
                stage="prepared release",
            )
        non_bge_pair = copy.deepcopy(self.manager.active_pair())
        non_bge_pair.update(
            {
                "embedding_backend": "non-bge-test-backend",
                "model_id": "not-bge-m3",
                "model_revision": None,
                "model_manifest_digest": None,
            }
        )
        with (
            mock.patch.object(self.guard.manager, "active_pair", return_value=non_bge_pair),
            self.assertRaisesRegex(RuntimeError, "HITL-bound embedding target"),
        ):
            self.guard.assert_embedding_requirement(
                directive_id, require_bge_m3=True
            )

    def test_required_bge_m3_is_sealed_into_hitl_approval(self) -> None:
        directive_id = "directive-bge-target-approval"
        bge_pair = copy.deepcopy(self.manager.active_pair())
        bge_pair.update(
            {
                "embedding_backend": "flagembedding-bge-m3-dev",
                "model_id": "BAAI/bge-m3",
                "model_revision": "a" * 40,
                "model_manifest_digest": "b" * 64,
                "production_qualified": False,
            }
        )
        with mock.patch.object(
            self.guard.manager, "active_pair", return_value=bge_pair
        ):
            opened = self.guard.open(
                directive_id=directive_id,
                scope="Approve only the exact pinned BGE-M3 embedding target.",
                packages=[self.copilot_package],
                require_bge_m3=True,
            )
            approved = self.guard.record_human_decision(
                directive_id,
                decision="APPROVE",
                classification_digest=opened["classification"][
                    "classification_digest"
                ],
                reviewer="test-operator",
                rationale="Approve the exact BGE-M3 target shown in this directive.",
            )
        target = approved["required_embedding_target"]
        self.assertEqual("flagembedding-bge-m3-dev", target["embedding_backend"])
        self.assertEqual("BAAI/bge-m3", target["model_id"])
        self.assertEqual("a" * 40, target["model_revision"])
        self.assertEqual("b" * 64, target["model_manifest_digest"])
        self.assertEqual(
            digest(target), approved["approval"]["embedding_target_digest"]
        )

    def test_requested_model_must_match_active_pair_before_hitl(self) -> None:
        requested = copy.copy(self.backend)
        requested.model_revision = "different-revision"
        with self.assertRaisesRegex(
            RuntimeError, "backend/model does not match the active pair"
        ):
            _assert_backend_matches_pair(
                requested,
                self.manager.active_pair(),
                operation="candidate publication",
            )

    def test_intake_publish_uses_one_classification_hitl_and_one_command(self) -> None:
        directive_id = "directive-unified-candidate-publish"
        base = (
            "intake-publish",
            "--directive-id",
            directive_id,
            "--package",
            str(self.copilot_package),
            "--runtime-root",
            str(self.runtime),
            "--graph",
            str(self.graph),
        )
        with mock.patch("dual_pipeline.release_policy.verify_authoritative_release_gate"):
            waiting = self._cli(*base)
        self.assertIn(waiting["status"], ("HITL_REQUIRED", "AWAITING_APPROVAL"))
        self.assertTrue(waiting["classification"]["human_approval_required"])
        self.assertTrue(waiting["classification"]["auto_candidate_admission_eligible"])
        self.assertNotIn("approval_id", waiting)
        self.assertEqual(
            "flagembedding-bge-m3-dev",
            waiting["required_embedding_target"]["embedding_backend"],
        )

        self.guard.record_human_decision(
            directive_id,
            decision="APPROVE",
            classification_digest=waiting["classification"]["classification_digest"],
            reviewer="test-operator",
            rationale="One classification decision covers this exact candidate package.",
        )
        with mock.patch("dual_pipeline.release_policy.verify_authoritative_release_gate"):
            completed = self._cli(*base)
        self.assertEqual("COMPLETE", completed["status"])
        self.assertEqual("candidate_release", completed["pipeline"])
        self.assertEqual(
            waiting["required_embedding_target"],
            completed["required_embedding_target"],
        )
        self.assertEqual(
            digest(completed["required_embedding_target"]),
            completed["admission"]["embedding_target_digest"],
        )
        self.assertEqual("COMPLETE", completed["ingestion_completion"]["directive_status"])
        self.assertEqual(
            "GRAPH_CHANGED",
            completed["ingestion_completion"]["completion"]["mutation_outcome"],
        )
        self.assertEqual("COMPLETE", self.guard.status(directive_id)["status"])

        with mock.patch("dual_pipeline.release_policy.verify_authoritative_release_gate"):
            replay = self._cli(*base)
        self.assertEqual("COMPLETE", replay["status"])
        self.assertTrue(replay["idempotent_replay"])

    def test_blank_follow_on_preserves_scope_and_new_package_requires_reapproval(self) -> None:
        directive_id = "directive-additive-follow-on"
        opened = self.guard.open(
            directive_id=directive_id,
            scope="Preserve active ingestion intent across follow-on turns",
            packages=[self.copilot_package],
        )
        unchanged = self.guard.attach(directive_id, [])
        self.assertEqual(opened["directive_digest"], unchanged["directive_digest"])
        self.assertEqual(1, len(unchanged["packages"]))
        self.assertIsNone(unchanged["approval"])

        expanded = self.guard.attach(directive_id, [self.architecture_package])
        self.assertEqual("OPEN", expanded["status"])
        self.assertEqual(2, len(expanded["packages"]))
        self.assertIsNone(expanded["approval"])
        self.assertTrue(
            any(event["event_type"] == "CLASSIFICATION_READY" for event in expanded["events"])
        )
        classification = self.guard.classify(directive_id)["classification"]
        rebound = self.guard.record_human_decision(
            directive_id,
            decision="APPROVE",
            classification_digest=classification["classification_digest"],
            reviewer="test-operator",
            rationale="Reviewed the refreshed two-package classification.",
        )
        self.assertEqual(
            rebound["package_set_digest"],
            rebound["approval"]["package_set_digest"],
        )

        first = self._intake_engine().ingest(self.copilot_package)
        with self.assertRaisesRegex(PermissionError, "supplied approval ID"):
            self.guard.validate_release_request(
                directive_id,
                run_ids=[first["run_id"]],
                approval_id="wrong-approval",
            )
        with self.assertRaisesRegex(RuntimeError, "exact package set"):
            self.guard.validate_release_request(
                directive_id,
                run_ids=[first["run_id"]],
            )

    def test_explicit_block_and_cancel_are_terminal(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe ingestion directive ID"):
            self.guard.open(
                directive_id="unsafe:windows-path",
                scope="Reject unsafe directive file names",
                packages=[self.copilot_package],
            )
        blocked_id = "directive-explicit-block"
        self.guard.open(
            directive_id=blocked_id,
            scope="Block only with a concrete code and reason",
            packages=[self.copilot_package],
        )
        blocked = self.guard.block(
            blocked_id,
            reason_code="SOURCE_ADAPTER_MISSING",
            reason="No deterministic adapter exists for the supplied bytes.",
        )
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertTrue(blocked["terminal"])
        with self.assertRaisesRegex(RuntimeError, "terminal"):
            self.guard.attach(blocked_id, [self.architecture_package])

        cancelled_id = "directive-explicit-cancel"
        self.guard.open(
            directive_id=cancelled_id,
            scope="Cancel only from an explicit user instruction",
            packages=[self.copilot_package],
        )
        cancelled = self.guard.cancel(
            cancelled_id, reason="The user explicitly cancelled this ingestion."
        )
        self.assertEqual("CANCELLED", cancelled["status"])
        self.assertTrue(cancelled["terminal"])
        with self.assertRaisesRegex(RuntimeError, "terminal"):
            self.guard.record_human_decision(
                cancelled_id,
                decision="APPROVE",
                classification_digest="0" * 64,
                reviewer="test-operator",
                rationale="This must remain terminal.",
            )

    def test_zero_operation_release_is_a_proven_idempotent_noop(self) -> None:
        directive_id = "directive-idempotent-noop"
        self.guard.open(
            directive_id=directive_id,
            scope="Prove an all-noop architecture intake without claiming mutation",
            packages=[self.architecture_package],
        )
        intake = self._intake_engine().ingest(self.architecture_package)
        self.assertEqual(0, intake["graph_operation_count"])
        classification = self.guard.classify(directive_id)["classification"]
        self.guard.record_human_decision(
            directive_id,
            decision="APPROVE",
            classification_digest=classification["classification_digest"],
            reviewer="test-operator",
            rationale="Reviewed the source-bound no-op classification.",
        )
        self.guard.validate_release_request(
            directive_id,
            run_ids=[intake["run_id"]],
        )
        completion = self.guard.complete_noop(
            directive_id,
            run_ids=[intake["run_id"]],
        )["completion"]
        self.assertEqual("IDEMPOTENT_NOOP", completion["mutation_outcome"])
        self.assertEqual(0, completion["graph_operation_count"])
        self.assertEqual(
            completion["graph_sha256_before"], completion["graph_sha256_after"]
        )
        self.assertTrue(Path(completion["receipt_path"]).is_file())
        self.assertIsNone(completion["activation_id"])
        self.assertIn("W0", completion["not_applicable_stages"])
        self.assertIn("E0", completion["not_applicable_stages"])

    def test_optional_seed_graph_resolves_to_active_pair(self) -> None:
        """Verify IngestionCompletionGuard defaults seed_graph from active_pair.json."""
        guard = IngestionCompletionGuard(self.runtime)
        active = self.manager.active_pair()
        expected = (self.runtime / active["graph_path"]).resolve()
        self.assertEqual(guard.seed_graph, expected)


if __name__ == "__main__":
    unittest.main()
