#!/usr/bin/env python3
"""Focused acceptance for atomic multi-intake graph/index releases."""
from __future__ import annotations

import copy
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.authorization import AuthorizationContext  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    digest,
    read_json,
    seal_artifact,
    sha256_bytes,
    sha256_file,
    write_json,
    write_json_exclusive,
)
from dual_pipeline.index import HybridRetriever  # noqa: E402
from dual_pipeline.intake import INTAKE_STAGES, IntakeEngine  # noqa: E402
from dual_pipeline.release import ReleaseManager, _seal_receipt  # noqa: E402
from dual_pipeline.telemetry import RunRecorder  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


class BatchReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-batch-release-")
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        self.seed_hash = sha256_file(self.graph)
        self.backend = BgeM3TestBackend()
        self.manager = ReleaseManager(self.runtime, self.graph)
        self.bootstrap = self.manager.bootstrap(self.backend)
        self.intake = IntakeEngine(
            self.manager._resolve(self.bootstrap["graph_path"]),
            self.runtime,
            retriever=HybridRetriever(
                self.manager._resolve(self.bootstrap["sidecar_path"]), self.backend
            ),
            retrieval_audiences=["internal_consulting", "nee-engineering"],
        )
        source_fixture = (
            ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "copilot-meeting.json"
        )
        first_package = self.root / "copilot-exact-identity.json"
        exact_identity = copy.deepcopy(read_json(source_fixture))
        exact_identity["speakers"][0]["role_or_org"] = "Project Beacon workstream"
        write_json(first_package, seal_artifact(exact_identity))
        second_package = self.root / "copilot-follow-up.json"
        follow_up = copy.deepcopy(exact_identity)
        follow_up["artifact_id"] = "copilot-package-teams-2026-07-22-nea-follow-up"
        follow_up["meeting"]["meeting_id"] = "teams-2026-07-22-nea-follow-up"
        follow_up["meeting"]["title"] = "NEA Engineering Follow-up"
        follow_up["meeting"]["teams_reference"] = "teams://meeting/teams-2026-07-22-nea-follow-up"
        follow_up["meeting"]["source_location"] = "partner_advisory-approved-location/nea/2026-07-22"
        follow_up["meeting"]["source_hash"] = "c" * 64
        for index, statement in enumerate(follow_up["speakers"][0]["statements"], 1):
            statement["statement_id"] = f"stmt-follow-up-{index}"
            statement["text"] = f"{statement['text']} Confirmed in the follow-up source."
            statement["locator"]["value"] = f"Speaker summary / Matt K / follow-up {index}"
        follow_up["actions"][0]["action_id"] = "action-follow-up-monitoring-owner"
        follow_up["actions"][0]["locator"]["value"] = "Action items / follow-up 1"
        write_json(second_package, seal_artifact(follow_up))
        self.first = self.intake.ingest(first_package)
        self.second = self.intake.ingest(second_package)
        self.assertGreater(self.first["graph_operation_count"], 0)
        self.assertGreater(self.second["graph_operation_count"], 0)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare(self, *, fail_after_operations: int | None = None) -> dict:
        return self.manager.prepare_many(
            run_ids=[self.first["run_id"], self.second["run_id"]],
            approval_id="batch-release-test-approval",
            backend=self.backend,
            fail_after_operations=fail_after_operations,
        )

    def _audit(self, run_id: str) -> dict:
        paths = sorted((self.runtime / "runs" / run_id / "learning_events").glob("*.json"))
        first = read_json(paths[0])
        return RunRecorder(
            self.runtime,
            run_id=run_id,
            trace_id=first["trace_id"],
            use_case=first["use_case"],
            graph_snapshot=first.get("graph_snapshot"),
            model_or_index_generation=first.get("model_or_index_generation"),
        ).audit(INTAKE_STAGES + ["W0", "E0"])

    @staticmethod
    def _without_e3_activation_gate():
        """Isolate atomic release mechanics from the separately tested E3 gate.

        Every graph/index pair in this class is still built with the pinned
        real BGE-M3 backend.  The end-to-end BGE evaluation/activation path is
        covered by the batch-admission and retrieval-release test modules;
        these cases focus on rollback, locking, and durable receipt behavior.
        """

        return patch(
            "dual_pipeline.retrieval_release.validate_activation_evaluation",
            return_value={"required": False},
        )

    @staticmethod
    def _authorization(
        token_id: str,
        *,
        model_manifest_digest: str,
        capabilities: tuple[str, ...] = ("knowledge:release",),
        resources: dict[str, str] | None = None,
    ) -> AuthorizationContext:
        now = int(time.time())
        bounded = {
            "subject": "release-test-operator",
            "issuer": "https://identity.example/nee",
            "relying_party": "nee-production",
            "token_id": token_id,
            "issued_at": now - 1,
            "expires_at": now + 300,
            "classification_clearance": "confidential",
            "audiences": ("nee-engineering",),
            "capabilities": capabilities,
            "authorized_resources": resources or {},
            "approved_model_manifest_digest": model_manifest_digest,
            "token_digest": digest(["token", token_id]),
        }
        return AuthorizationContext(
            **bounded,
            context_digest=digest(["authorization-context", bounded]),
        )

    def test_batch_release_preserves_component_provenance_and_rolls_back(self) -> None:
        receipt = self._prepare()
        self.assertEqual(receipt, self._prepare())
        self.assertEqual(2, len(receipt["component_runs"]))
        self.assertEqual(
            {self.first["run_id"], self.second["run_id"]},
            {component["run_id"] for component in receipt["component_runs"]},
        )
        receipt_path = (
            self.runtime / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"
        )
        with self._without_e3_activation_gate():
            activation = self.manager.activate(receipt_path)
        self.assertEqual("ACTIVE", activation["status"])
        self.assertEqual(receipt, self._prepare())
        active_graph = self.manager._resolve(activation["active_pair"]["graph_path"])
        connection = sqlite3.connect(active_graph)
        try:
            for component in receipt["component_runs"]:
                approved = read_json(
                    self.runtime
                    / "runs"
                    / component["run_id"]
                    / "graph_change_set_approved.json"
                )
                source_id = approved["extensions"]["com.partner_advisory.nee.source"]["source_id"]
                for operation in approved["operations"]:
                    assertion = connection.execute(
                        "SELECT primary_source_id FROM assertion WHERE assertion_id=?",
                        (operation["target_id"],),
                    ).fetchone()
                    self.assertEqual((source_id,), assertion)
                    audit = connection.execute(
                        "SELECT source_id,migration_or_ingestion_run_id "
                        "FROM audit_event WHERE record_id=?",
                        (operation["target_id"],),
                    ).fetchone()
                    self.assertEqual((source_id, component["run_id"]), audit)
        finally:
            connection.close()
        self.assertEqual("PASS", self._audit(self.first["run_id"])["status"])
        self.assertEqual("PASS", self._audit(self.second["run_id"])["status"])
        rollback = self.manager.rollback(receipt_path)
        self.assertEqual("ROLLED_BACK", rollback["status"])
        self.assertEqual(receipt, self._prepare())
        self.assertEqual(
            self.bootstrap["generation_id"], self.manager.active_pair()["generation_id"]
        )
        self.assertEqual(self.seed_hash, sha256_file(self.graph))

        proposed_path = (
            self.runtime
            / "runs"
            / self.first["run_id"]
            / "graph_change_set.json"
        )
        proposed = read_json(proposed_path)
        operation_id = proposed["operations"][0]["operation_id"]
        proposed["extensions"]["com.partner_advisory.nee.operation-payloads"][operation_id][
            "candidate"
        ]["normalized_text"] += " Resealed tamper probe."
        write_json(proposed_path, seal_artifact(proposed))
        with self.assertRaisesRegex(RuntimeError, "sealed intake ledger"):
            self._prepare()

    def test_release_rejects_a_run_without_its_required_decision_ledger(self) -> None:
        run_dir = self.runtime / "runs" / self.first["run_id"]
        ledger_path = run_dir / "ingestion_decision_ledger.json"
        self.assertTrue(ledger_path.is_file())
        ledger_path.unlink()
        with self.assertRaisesRegex(RuntimeError, "required ingestion decision ledger"):
            self._prepare()

    def test_release_rejects_missing_or_tampered_english_explanation(self) -> None:
        run_dir = self.runtime / "runs" / self.first["run_id"]
        english_path = run_dir / "ingestion_decision_ledger.english.json"
        self.assertTrue(english_path.is_file())
        original = english_path.read_bytes()
        english_path.unlink()
        with self.assertRaisesRegex(RuntimeError, "required ingestion decision ledger"):
            self._prepare()

        english_path.write_bytes(original)
        explanation = read_json(english_path)
        explanation["decision_groups"][0]["new_node_explanation"] += " Tampered."
        write_json(english_path, seal_artifact(explanation))
        with self.assertRaisesRegex(RuntimeError, "ingestion decision ledger"):
            self._prepare()

    def test_batch_release_rejects_resealed_cross_component_tamper(self) -> None:
        first_change_set = read_json(
            self.runtime / "runs" / self.first["run_id"] / "graph_change_set.json"
        )
        second_path = self.runtime / "runs" / self.second["run_id"] / "graph_change_set.json"
        second_change_set = read_json(second_path)
        old_operation_id = second_change_set["operations"][0]["operation_id"]
        duplicate_operation_id = first_change_set["operations"][0]["operation_id"]
        second_change_set["operations"][0]["operation_id"] = duplicate_operation_id
        payloads = second_change_set["extensions"]["com.partner_advisory.nee.operation-payloads"]
        payloads[duplicate_operation_id] = payloads.pop(old_operation_id)
        write_json(second_path, seal_artifact(second_change_set))
        with self.assertRaisesRegex(RuntimeError, "sealed intake ledger"):
            self._prepare()
        self.assertEqual(
            self.bootstrap["generation_id"], self.manager.active_pair()["generation_id"]
        )

    def test_completed_noop_intake_is_bound_to_the_same_release_receipt(self) -> None:
        architecture = self.intake.ingest(
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "architecture-diagram.json"
        )
        self.assertEqual(0, architecture["graph_operation_count"])
        receipt = self.manager.prepare_many(
            run_ids=[self.first["run_id"], architecture["run_id"]],
            approval_id="batch-release-noop-test-approval",
            backend=self.backend,
        )
        component = next(
            value
            for value in receipt["component_runs"]
            if value["run_id"] == architecture["run_id"]
        )
        self.assertIsNone(component["change_set_id"])
        self.assertEqual([], component["operation_ids"])
        receipt_path = (
            self.runtime / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"
        )
        with self._without_e3_activation_gate():
            self.manager.activate(receipt_path)
        self.assertEqual("PASS", self._audit(architecture["run_id"])["status"])
        self.manager.rollback(receipt_path)
        self.assertEqual(
            self.bootstrap["generation_id"], self.manager.active_pair()["generation_id"]
        )

    def test_batch_writer_failure_rolls_back_every_component(self) -> None:
        first_change_set = read_json(
            self.runtime / "runs" / self.first["run_id"] / "graph_change_set.json"
        )
        with self.assertRaisesRegex(RuntimeError, "injected graph-writer failure"):
            self._prepare(fail_after_operations=len(first_change_set["operations"]) + 1)
        self.assertEqual(
            self.bootstrap["generation_id"], self.manager.active_pair()["generation_id"]
        )
        for candidate_graph in self.runtime.glob("generations/*/graph.sqlite"):
            connection = sqlite3.connect(candidate_graph)
            try:
                target_count = connection.execute(
                    "SELECT count(*) FROM assertion WHERE assertion_id IN ({})".format(
                        ",".join(
                            "?"
                            for _ in (
                                first_change_set["operations"]
                                + read_json(
                                    self.runtime
                                    / "runs"
                                    / self.second["run_id"]
                                    / "graph_change_set.json"
                                )["operations"]
                            )
                        )
                    ),
                    [
                        operation["target_id"]
                        for operation in (
                            first_change_set["operations"]
                            + read_json(
                                self.runtime
                                / "runs"
                                / self.second["run_id"]
                                / "graph_change_set.json"
                            )["operations"]
                        )
                    ],
                ).fetchone()[0]
                self.assertEqual(0, target_count)
            finally:
                connection.close()
        self.assertEqual(self.seed_hash, sha256_file(self.graph))

    def test_prepare_ledger_failure_is_recovered_from_immutable_receipt(self) -> None:
        original_record = RunRecorder.record

        def fail_second_embedding(recorder, *args, **kwargs):
            if (
                recorder.run_id == self.second["run_id"]
                and kwargs.get("stage") == "E0"
                and kwargs.get("event_type") == "embedding_generation_receipt"
            ):
                raise RuntimeError("injected preparation ledger failure")
            return original_record(recorder, *args, **kwargs)

        with patch.object(RunRecorder, "record", new=fail_second_embedding):
            with self.assertRaisesRegex(RuntimeError, "preparation ledger failure"):
                self._prepare()

        receipt_paths = list((self.runtime / "releases").glob("release-*.json"))
        self.assertEqual(1, len(receipt_paths))
        durable = read_json(receipt_paths[0])
        recovered = self._prepare()
        self.assertEqual(durable, recovered)
        self.assertEqual("PASS", self._audit(self.first["run_id"])["status"])
        self.assertEqual("PASS", self._audit(self.second["run_id"])["status"])

    def test_activation_ledger_failure_restores_previous_pair_without_receipt(self) -> None:
        receipt = self._prepare()
        receipt_path = (
            self.runtime / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"
        )
        activation_path = (
            self.runtime
            / "releases"
            / f"activation-{receipt['next_pair']['generation_id']}.json"
        )

        with (
            self._without_e3_activation_gate(),
            patch.object(
                RunRecorder, "seal", side_effect=RuntimeError("ledger seal failed")
            ),self.assertRaisesRegex(RuntimeError, "ledger seal failed")
        ):
            self.manager.activate(receipt_path)

        self.assertEqual(
            self.bootstrap["generation_id"], self.manager.active_pair()["generation_id"]
        )
        self.assertFalse(activation_path.exists())
        events = [
            read_json(path)
            for path in sorted(
                (
                    self.runtime
                    / "runs"
                    / self.first["run_id"]
                    / "learning_events"
                ).glob("*.json")
            )
        ]
        active_events = [
            event for event in events if event["event_type"] == "release_receipt"
        ]
        aborted_events = [
            event
            for event in events
            if event["event_type"] == "release_activation_aborted"
        ]
        self.assertEqual(1, len(active_events))
        self.assertEqual(1, len(aborted_events))
        aborted_payload = read_json(
            self.runtime
            / "runs"
            / self.first["run_id"]
            / "ledger_payloads"
            / aborted_events[0]["payload_reference"].rsplit("/", 1)[-1]
        )
        active_payload = read_json(
            self.runtime
            / "runs"
            / self.first["run_id"]
            / "ledger_payloads"
            / active_events[0]["payload_reference"].rsplit("/", 1)[-1]
        )
        self.assertEqual("ABORTED", aborted_payload["status"])
        self.assertEqual(
            active_payload["receipt_digest"],
            aborted_payload["activation_receipt_digest"],
        )
        self.assertEqual("PASS", self._audit(self.first["run_id"])["status"])

    def test_concurrent_activation_cannot_diverge_pointer_and_receipt(self) -> None:
        receipt = self._prepare()
        next_generation = receipt["next_pair"]["generation_id"]
        receipt_path = self.runtime / "releases" / f"release-{next_generation}.json"
        activation_path = self.runtime / "releases" / f"activation-{next_generation}.json"
        primary_at_ledger = threading.Event()
        allow_primary_to_finish = threading.Event()
        secondary_saw_swapped_pair = threading.Event()
        original_record = RunRecorder.record
        original_active_pair = ReleaseManager.active_pair
        outcomes: dict[str, object] = {}

        def gated_record(recorder, *args, **kwargs):
            if (
                threading.current_thread().name == "activation-primary"
                and kwargs.get("stage") == "E0"
                and not primary_at_ledger.is_set()
            ):
                primary_at_ledger.set()
                if not allow_primary_to_finish.wait(5):
                    raise TimeoutError("test activation barrier timed out")
            return original_record(recorder, *args, **kwargs)

        def observed_active_pair(manager):
            pair = original_active_pair(manager)
            if (
                threading.current_thread().name == "activation-secondary"
                and pair["generation_id"] == next_generation
            ):
                secondary_saw_swapped_pair.set()
            return pair

        def run_activation(name: str) -> None:
            try:
                outcomes[name] = self.manager.activate(receipt_path)
            except Exception as exc:  # asserted below with the durable state
                outcomes[name] = exc

        with (
            patch.object(RunRecorder, "record", new=gated_record),
            patch.object(ReleaseManager, "active_pair", new=observed_active_pair),
            patch.object(self.manager, "validate_pair", return_value={"status": "PASS"}),
            self._without_e3_activation_gate(),
        ):
            primary = threading.Thread(
                target=run_activation,
                args=("primary",),
                name="activation-primary",
            )
            secondary = threading.Thread(
                target=run_activation,
                args=("secondary",),
                name="activation-secondary",
            )
            primary.start()
            try:
                self.assertTrue(primary_at_ledger.wait(10))
                secondary.start()
                # Vulnerable implementations observe the swapped pointer before the
                # first activation has durably written its receipt. Correct locking
                # may instead keep the secondary blocked until the receipt exists.
                secondary_saw_swapped_pair.wait(1)
            finally:
                allow_primary_to_finish.set()
                primary.join(10)
                if secondary.ident is not None:
                    secondary.join(10)

        self.assertFalse(primary.is_alive())
        self.assertFalse(secondary.is_alive())
        self.assertNotIsInstance(outcomes.get("primary"), Exception)
        active = self.manager.active_pair()
        self.assertEqual(
            activation_path.exists(),
            active["generation_id"] == next_generation,
            "active pair and durable activation receipt must transition together",
        )
        if activation_path.exists():
            activation = read_json(activation_path)
            self.assertEqual(
                active["generation_id"], activation["active_pair"]["generation_id"]
            )

    def test_unsafe_run_id_is_rejected_before_release_preparation(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe intake run ID"):
            self.manager.prepare_many(
                run_ids=["../escape"],
                approval_id="batch-release-unsafe-run-id",
                backend=self.backend,
            )
        self.assertEqual(
            self.bootstrap["generation_id"], self.manager.active_pair()["generation_id"]
        )

    def test_fresh_production_action_tokens_bind_exact_release_resources(self) -> None:
        receipt = self._prepare()
        receipt_path = (
            self.runtime / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"
        )
        model_manifest_digest = "b" * 64
        preparation = self._authorization(
            "preparation-token",
            model_manifest_digest=model_manifest_digest,
            capabilities=("knowledge:release", "knowledge:production-qualify"),
        )
        receipt["production_qualified"] = True
        receipt["next_pair"]["production_qualified"] = True
        receipt["previous_pair"]["production_qualified"] = True
        receipt["next_pair"]["model_manifest_digest"] = model_manifest_digest
        receipt["authorization"] = preparation.audit_record()
        receipt = _seal_receipt(receipt)
        write_json(receipt_path, receipt)

        common_resources = {
            "release_receipt": receipt["receipt_digest"],
            "release_generation": sha256_bytes(
                receipt["next_pair"]["generation_id"].encode("utf-8")
            ),
        }
        activation_context = self._authorization(
            "fresh-activation-token",
            model_manifest_digest=model_manifest_digest,
            resources={
                **common_resources,
                "release_action": sha256_bytes(b"activate"),
            },
        )
        rollback_context = self._authorization(
            "fresh-rollback-token",
            model_manifest_digest=model_manifest_digest,
            resources={
                **common_resources,
                "release_action": sha256_bytes(b"rollback"),
            },
        )
        wrong_rollback_context = self._authorization(
            "wrong-rollback-token",
            model_manifest_digest=model_manifest_digest,
            resources={
                **common_resources,
                "release_action": sha256_bytes(b"activate"),
            },
        )

        # This test isolates action-token binding from sidecar model metadata;
        # prepare already exercised the complete pair validation above.
        with (
            patch.object(self.manager, "validate_pair", return_value={"status": "PASS"}),
            self._without_e3_activation_gate(),
        ):
            activation = self.manager.activate(
                receipt_path, authorization_context=activation_context
            )
            self.assertEqual("ACTIVE", activation["status"])
            with self.assertRaisesRegex(PermissionError, "requested resources"):
                self.manager.rollback(
                    receipt_path, authorization_context=wrong_rollback_context
                )
            rollback = self.manager.rollback(
                receipt_path, authorization_context=rollback_context
            )

        self.assertEqual("ROLLED_BACK", rollback["status"])
        self.assertNotEqual(
            preparation.context_digest, activation_context.context_digest
        )
        self.assertNotEqual(
            activation_context.context_digest, rollback_context.context_digest
        )

    def test_public_action_cannot_reuse_qualification_context_and_expired_compensation_is_narrow(self) -> None:
        receipt = self._prepare()
        receipt_path = (
            self.runtime / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"
        )
        model_manifest_digest = "b" * 64
        qualification = self._authorization(
            "qualification-transition-token",
            model_manifest_digest=model_manifest_digest,
            capabilities=("knowledge:release", "knowledge:production-qualify"),
        )
        receipt["production_qualified"] = True
        receipt["next_pair"]["production_qualified"] = True
        receipt["previous_pair"]["production_qualified"] = True
        receipt["next_pair"]["model_manifest_digest"] = model_manifest_digest
        receipt["authorization"] = qualification.audit_record()
        receipt = _seal_receipt(receipt)
        write_json(receipt_path, receipt)

        downgraded = copy.deepcopy(receipt)
        downgraded["production_qualified"] = False
        downgraded = _seal_receipt(downgraded)
        downgraded_path = self.runtime / "releases" / "release-downgraded-copy.json"
        write_json(downgraded_path, downgraded)
        with self._without_e3_activation_gate():
            with self.assertRaisesRegex(ValueError, "production policy bindings disagree"):
                self.manager.activate(downgraded_path)

        with (
            patch.object(self.manager, "validate_pair", return_value={"status": "PASS"}),
            self._without_e3_activation_gate(),
        ):
            with self.assertRaisesRegex(PermissionError, "requested resources"):
                self.manager.activate(
                    receipt_path, authorization_context=qualification
                )
            activation = self.manager._activate_for_qualification(
                receipt_path, authorization_context=qualification
            )
            self.assertEqual("ACTIVE", activation["status"])
            with self.assertRaisesRegex(PermissionError, "requested resources"):
                self.manager.rollback(
                    receipt_path, authorization_context=qualification
                )
            with patch(
                "dual_pipeline.authorization.time.time",
                return_value=qualification.expires_at + 61,
            ):
                with self.assertRaisesRegex(PermissionError, "expired"):
                    self.manager._rollback_for_qualification(
                        receipt_path, authorization_context=qualification
                    )
                rollback = self.manager._compensate_failed_qualification(
                    receipt_path, authorization_context=qualification
                )
        self.assertEqual("ROLLED_BACK", rollback["status"])

    def test_post_publish_interruptions_reconcile_action_receipts_and_pointer(self) -> None:
        receipt = self._prepare()
        receipt_path = (
            self.runtime / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"
        )

        def publish_then_interrupt(path: Path, value: dict) -> None:
            write_json_exclusive(path, value)
            raise KeyboardInterrupt("simulated post-publish interruption")

        with (
            self._without_e3_activation_gate(),
            patch(
                "dual_pipeline.release.write_json_exclusive",
                side_effect=publish_then_interrupt,
            ),self.assertRaises(KeyboardInterrupt)
        ):
            self.manager.activate(receipt_path)
        self.assertEqual(
            receipt["next_pair"]["generation_id"],
            self.manager.active_pair()["generation_id"],
        )
        with self._without_e3_activation_gate():
            activation = self.manager.activate(receipt_path)
        self.assertEqual("ACTIVE", activation["status"])

        with patch(
            "dual_pipeline.release.write_json_exclusive",
            side_effect=publish_then_interrupt,
        ), self.assertRaises(KeyboardInterrupt):
            self.manager.rollback(receipt_path)
        self.assertEqual(
            receipt["previous_pair"]["generation_id"],
            self.manager.active_pair()["generation_id"],
        )
        rollback = self.manager.rollback(receipt_path)
        self.assertEqual("ROLLED_BACK", rollback["status"])

    def test_reviewed_proposal_history_remains_releaseable_and_replay_is_idempotent(self) -> None:
        decisions = self.root / "empty-decisions.json"
        write_json(decisions, [])
        reviewed = self.intake.review(self.first["run_id"], decisions)
        replay = self.intake.review(self.first["run_id"], decisions)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            reviewed["learning_event_chain_digest"],
            replay["learning_event_chain_digest"],
        )
        receipt = self.manager.prepare(
            run_id=self.first["run_id"],
            approval_id="reviewed-release-test-approval",
            backend=self.backend,
        )
        self.assertEqual("PREPARED", receipt["status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
