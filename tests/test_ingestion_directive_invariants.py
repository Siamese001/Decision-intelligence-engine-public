#!/usr/bin/env python3
"""Fast adversarial tests for sealed ingestion-directive invariants."""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.completion_core import IngestionCompletionGuard
from dual_pipeline.completion_core.verification import _seal_directive, _verify_directive
from dual_pipeline.core import digest, sha256_file, stable_id, write_json  # noqa: E402
from dual_pipeline.intake import INTAKE_STAGES  # noqa: E402


class IngestionDirectiveInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-directive-invariants-")
        self.scratch = Path(self.temp.name)
        self.runtime = self.scratch / "runtime"
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        self.package = self.scratch / "package.json"
        self.package.write_bytes(b'{"source":"exact"}\n')
        self.guard = IngestionCompletionGuard(self.runtime, self.graph)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _open_directive(self, directive_id: str = "directive-invariant") -> dict:
        package = self.guard._package_binding(self.package)
        package_set_digest = digest([package])
        recorded_at = "2026-07-30T12:00:00Z"
        target = {
            "runtime_root": str(self.runtime.resolve()),
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "model_manifest_digest": "a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e",
            "production_qualified": False,
        }
        return _seal_directive(
            {
                "schema_version": "1.0.0",
                "directive_id": directive_id,
                "status": "OPEN",
                "terminal": False,
                "scope": "Bind exact package bytes until a proven terminal outcome.",
                "baseline_pair": {
                    "generation_id": "generation-baseline",
                    "graph_sha256": "a" * 64,
                    "sidecar_sha256": "b" * 64,
                    "graph_snapshot": "graph-snapshot-baseline",
                    "embedding_backend": "flagembedding-bge-m3-dev",
                    "model_id": "BAAI/bge-m3",
                    "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
                    "model_manifest_digest": "a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e",
                    "production_qualified": False,
                },
                "required_embedding_target": target,
                "packages": [package],
                "package_set_digest": package_set_digest,
                "batch_admission": None,
                "approval": None,
                "opened_at": recorded_at,
                "updated_at": recorded_at,
                "events": [
                    {
                        "sequence": 1,
                        "event_type": "DIRECTIVE_OPENED",
                        "recorded_at": recorded_at,
                        "package_set_digest": package_set_digest,
                        "package_count": 1,
                        "approval_bound": False,
                        "embedding_target_digest": digest(target),
                    }
                ],
                "block": None,
                "cancellation": None,
                "completion": None,
            }
        )

    def _completed_noop_directive(self) -> dict:
        value = self._open_directive()
        classification_digest = "c" * 64
        reviewer = "operator"
        rationale = "Approve the exact zero-operation package set."
        approval = {
            "approval_id": stable_id(
                "hitl-approval",
                value["directive_id"],
                value["package_set_digest"],
                classification_digest,
                digest(value["required_embedding_target"]),
                reviewer,
                rationale,
                length=28,
            ),
            "binding_type": "HITL_CLASSIFICATION_APPROVAL",
            "bound_at": "2026-07-30T12:01:00Z",
            "package_set_digest": value["package_set_digest"],
            "classification_digest": classification_digest,
            "classification_id": "classification-exact",
            "decision": "APPROVE",
            "embedding_target_digest": digest(value["required_embedding_target"]),
            "reviewer": reviewer,
            "rationale": rationale,
        }
        completed_at = "2026-07-30T12:02:00Z"
        completion = {
            "status": "PASS",
            "completed_at": completed_at,
            "approval_id": approval["approval_id"],
            "package_set_digest": value["package_set_digest"],
            "run_ids": ["run-exact"],
            "run_receipts": [{"run_id": "run-exact"}],
            "receipt_path": str(
                (self.runtime / "releases" / "noop-receipt.json").resolve()
            ),
            "receipt_id": "noop-receipt-exact",
            "receipt_digest": "d" * 64,
            "activation_path": None,
            "activation_id": None,
            "activation_digest": None,
            "activation_not_applicable_reason": "No governed graph operations existed.",
            "generation_before": value["baseline_pair"]["generation_id"],
            "generation_after": value["baseline_pair"]["generation_id"],
            "graph_sha256_before": value["baseline_pair"]["graph_sha256"],
            "graph_sha256_after": value["baseline_pair"]["graph_sha256"],
            "graph_operation_count": 0,
            "mutation_outcome": "IDEMPOTENT_NOOP",
            "required_embedding_target": value["required_embedding_target"],
            "embedding_target_verified": True,
            "executed_stages": INTAKE_STAGES,
            "not_applicable_stages": {
                "W0": "No governed graph operations existed to write.",
                "E0": "No new graph/index generation existed to activate.",
            },
        }
        completion["completion_digest"] = digest(completion)
        value["status"] = "COMPLETE"
        value["terminal"] = True
        value["approval"] = approval
        value["completion"] = completion
        value["updated_at"] = completed_at
        value["events"].extend(
            [
                {
                    "sequence": 2,
                    "event_type": "CLASSIFICATION_READY",
                    "recorded_at": "2026-07-30T12:00:30Z",
                    "package_set_digest": value["package_set_digest"],
                    "classification_id": approval["classification_id"],
                    "classification_digest": approval["classification_digest"],
                },
                {
                    "sequence": 3,
                    "event_type": "HITL_CLASSIFICATION_APPROVED",
                    "recorded_at": approval["bound_at"],
                    "package_set_digest": value["package_set_digest"],
                    "classification_digest": approval["classification_digest"],
                    "embedding_target_digest": approval["embedding_target_digest"],
                    "reviewer_digest": digest(approval["reviewer"]),
                },
                {
                    "sequence": 4,
                    "event_type": "DIRECTIVE_COMPLETED_NOOP",
                    "recorded_at": completed_at,
                    "completion_digest": completion["completion_digest"],
                    "receipt_digest": completion["receipt_digest"],
                    "mutation_outcome": completion["mutation_outcome"],
                },
            ]
        )
        return _seal_directive(value)

    @staticmethod
    def _reseal(value: dict) -> dict:
        value["package_set_digest"] = digest(value["packages"])
        return _seal_directive(value)

    def test_valid_minimal_open_directive_is_accepted(self) -> None:
        _verify_directive(self._open_directive())

    def test_resealed_semantic_corruption_is_rejected(self) -> None:
        def non_string_id(value: dict) -> None:
            value["directive_id"] = 123

        def blank_scope(value: dict) -> None:
            value["scope"] = " \t "

        def malformed_baseline(value: dict) -> None:
            value["baseline_pair"]["graph_sha256"] = "not-a-digest"

        def relative_package_path(value: dict) -> None:
            value["packages"][0]["path"] = "relative/package.json"

        def boolean_package_size(value: dict) -> None:
            value["packages"][0]["bytes"] = True

        def invalid_package_digest(value: dict) -> None:
            value["packages"][0]["sha256"] = "f" * 63

        def broken_event_sequence(value: dict) -> None:
            value["events"][0]["sequence"] = 2

        def terminal_payload_on_open(value: dict) -> None:
            value["block"] = {
                "reason_code": "SOURCE_ADAPTER_MISSING",
                "reason": "This payload conflicts with OPEN.",
                "blocked_at": value["updated_at"],
            }

        def unbound_approval(value: dict) -> None:
            value["approval"] = {
                "approval_id": "approval-unbound",
                "binding_type": "HITL_CLASSIFICATION_APPROVAL",
                "bound_at": value["updated_at"],
                "package_set_digest": "f" * 64,
                "classification_digest": "c" * 64,
                "classification_id": "classification-exact",
                "decision": "APPROVE",
                "embedding_target_digest": digest(
                    value["required_embedding_target"]
                ),
                "reviewer": "operator",
                "rationale": "Deliberately bound to the wrong package set.",
            }

        def approval_without_event(value: dict) -> None:
            reviewer = "operator"
            rationale = "No matching state transition exists."
            classification_digest = "c" * 64
            value["approval"] = {
                "approval_id": stable_id(
                    "hitl-approval",
                    value["directive_id"],
                    value["package_set_digest"],
                    classification_digest,
                    digest(value["required_embedding_target"]),
                    reviewer,
                    rationale,
                    length=28,
                ),
                "binding_type": "HITL_CLASSIFICATION_APPROVAL",
                "bound_at": value["updated_at"],
                "package_set_digest": value["package_set_digest"],
                "classification_digest": classification_digest,
                "classification_id": "classification-exact",
                "decision": "APPROVE",
                "embedding_target_digest": digest(
                    value["required_embedding_target"]
                ),
                "reviewer": reviewer,
                "rationale": rationale,
            }

        def complete_without_proof(value: dict) -> None:
            value["status"] = "COMPLETE"
            value["terminal"] = True
            value["events"].append(
                {
                    "sequence": 2,
                    "event_type": "DIRECTIVE_COMPLETED",
                    "recorded_at": "2026-07-30T12:01:00Z",
                }
            )
            value["updated_at"] = value["events"][-1]["recorded_at"]

        mutations = {
            "non-string directive ID": non_string_id,
            "blank scope": blank_scope,
            "malformed baseline": malformed_baseline,
            "relative package path": relative_package_path,
            "boolean package size": boolean_package_size,
            "invalid package digest": invalid_package_digest,
            "broken event sequence": broken_event_sequence,
            "terminal payload on open": terminal_payload_on_open,
            "approval for another package set": unbound_approval,
            "approval without state event": approval_without_event,
            "complete without proof": complete_without_proof,
        }
        original = self._open_directive()
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                corrupted = copy.deepcopy(original)
                mutate(corrupted)
                corrupted = self._reseal(corrupted)
                with self.assertRaises(ValueError):
                    _verify_directive(corrupted)

    def test_completion_proof_is_semantically_bound_not_just_outer_sealed(self) -> None:
        valid = self._completed_noop_directive()
        _verify_directive(valid)

        corruptions = {}

        wrong_inner_digest = copy.deepcopy(valid)
        wrong_inner_digest["completion"]["completion_digest"] = "e" * 64
        corruptions["wrong inner digest"] = _seal_directive(wrong_inner_digest)

        graph_changed = copy.deepcopy(valid)
        graph_changed["completion"]["graph_sha256_after"] = "f" * 64
        graph_changed["completion"]["completion_digest"] = digest(
            {
                key: item
                for key, item in graph_changed["completion"].items()
                if key != "completion_digest"
            }
        )
        corruptions["no-op changed graph"] = _seal_directive(graph_changed)

        activated = copy.deepcopy(valid)
        activated["completion"]["activation_id"] = "activation-for-noop"
        activated["completion"]["completion_digest"] = digest(
            {
                key: item
                for key, item in activated["completion"].items()
                if key != "completion_digest"
            }
        )
        corruptions["no-op has activation"] = _seal_directive(activated)

        wrong_approval = copy.deepcopy(valid)
        wrong_approval["completion"]["approval_id"] = "approval-for-other-scope"
        wrong_approval["completion"]["completion_digest"] = digest(
            {
                key: item
                for key, item in wrong_approval["completion"].items()
                if key != "completion_digest"
            }
        )
        corruptions["wrong approval"] = _seal_directive(wrong_approval)

        forged_approval = copy.deepcopy(valid)
        forged_approval["approval"]["approval_id"] = "hitl-approval-forged"
        forged_approval["completion"]["approval_id"] = "hitl-approval-forged"
        forged_approval["completion"]["completion_digest"] = digest(
            {
                key: item
                for key, item in forged_approval["completion"].items()
                if key != "completion_digest"
            }
        )
        forged_approval["events"][-1]["completion_digest"] = forged_approval[
            "completion"
        ]["completion_digest"]
        corruptions["forged approval ID"] = _seal_directive(forged_approval)

        unhashable_run = copy.deepcopy(valid)
        unhashable_run["completion"]["run_ids"] = [{"run_id": "nested"}]
        unhashable_run["completion"]["completion_digest"] = digest(
            {
                key: item
                for key, item in unhashable_run["completion"].items()
                if key != "completion_digest"
            }
        )
        corruptions["non-string run ID"] = _seal_directive(unhashable_run)

        for label, corrupted in corruptions.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                _verify_directive(corrupted)

    @unittest.skipUnless(os.name == "nt", "case-folding is a Windows path invariant")
    def test_case_variant_paths_are_deduplicated_and_rejected_in_receipts(self) -> None:
        case_variant = Path(str(self.package).upper())
        bindings = self.guard._package_bindings([self.package, case_variant])
        self.assertEqual(1, len(bindings))

        malformed = self._open_directive()
        duplicate = copy.deepcopy(malformed["packages"][0])
        duplicate["path"] = str(Path(duplicate["path"])).upper()
        malformed["packages"].append(duplicate)
        malformed = self._reseal(malformed)
        with self.assertRaisesRegex(ValueError, "unique package paths"):
            _verify_directive(malformed)

    def test_package_change_during_hashing_is_rejected(self) -> None:
        def hash_then_mutate(path: Path) -> str:
            result = sha256_file(path)
            path.write_bytes(b'{"source":"changed-during-hash"}\n')
            return result

        with mock.patch(
            "dual_pipeline.completion_core.guard.sha256_file", side_effect=hash_then_mutate
        ), self.assertRaisesRegex(RuntimeError, "changed while it was being bound"):
            self.guard._package_binding(self.package)

    def test_directive_cannot_be_swapped_under_another_storage_id(self) -> None:
        stored_id = "directive-storage-id"
        directive = self._open_directive("directive-different-id")
        self.guard.directive_dir.mkdir(parents=True, exist_ok=True)
        write_json(self.guard._path(stored_id), directive)
        with self.assertRaisesRegex(ValueError, "immutable storage path"):
            self.guard.status(stored_id)

    def test_zero_operation_receipt_cannot_use_graph_release_path(self) -> None:
        directive = self._open_directive()
        baseline = directive["baseline_pair"]
        target = directive["required_embedding_target"]
        receipt = {
            "status": "PREPARED",
            "approval_id": "approval-exact",
            "previous_pair": {
                "generation_id": baseline["generation_id"],
                "graph_sha256": baseline["graph_sha256"],
                "sidecar_sha256": baseline["sidecar_sha256"],
            },
            "next_pair": {
                "generation_id": "generation-empty-release",
                "graph_sha256": baseline["graph_sha256"],
                "sidecar_sha256": "c" * 64,
                "embedding_backend": target["embedding_backend"],
                "model_id": target["model_id"],
                "model_revision": target["model_revision"],
                "model_manifest_digest": target["model_manifest_digest"],
                "production_qualified": target["production_qualified"],
            },
            "pair_validation": {"status": "PASS"},
            "graph_write_receipt": {"operation_count": 0},
            "component_runs": [],
        }
        with (
            mock.patch.object(
                self.guard, "_receipt_path", return_value=self.scratch / "receipt.json"
            ),
            mock.patch("dual_pipeline.completion_core.guard.read_json", return_value=receipt),
            mock.patch("dual_pipeline.completion_core.guard._verify_receipt"),
            mock.patch.object(
                self.guard, "_receipt_run_ids", return_value=["run-exact"]
            ),
            mock.patch.object(
                self.guard, "_validate_release_request_state", return_value=[]
            ),
            mock.patch.object(
                self.guard.manager, "validate_pair", return_value={"status": "PASS"}
            ),self.assertRaisesRegex(
            RuntimeError, "intake-completion-noop"
        )
        ):
            self.guard._preflight_activation_state(
                directive, self.scratch / "receipt.json"
            )


if __name__ == "__main__":
    unittest.main()
