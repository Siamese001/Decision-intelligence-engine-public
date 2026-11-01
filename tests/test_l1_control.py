#!/usr/bin/env python3
"""Wave 2 pure-controller transition tests."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from dual_pipeline.l1_control import (
    BudgetState,
    GuardStatus,
    L1ControlError,
    ProgressStatus,
    apply_action_result,
    create_action,
    guard_action,
    load_policy,
    observe_progress,
    reduce_terminal,
    replay_digest,
    validate_action_chain,
)
from tools.validate_dual_pipeline_contracts import SchemaRegistry, load_json, validate_artifact

ROOT = Path(__file__).resolve().parents[1]


class L1ControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw = json.loads((ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8"))
        cls.policy = load_policy(raw)
        cls.plan = "a" * 64
        cls.manifest = "b" * 64
        cls.auth = "c" * 64

    def action(self, ordinal: int = 0, **kwargs):
        return create_action(
            parent_plan_digest=self.plan,
            coverage_manifest_digest=self.manifest,
            authorization_scope_digest=self.auth,
            attempt_ordinal=ordinal,
            action_type=kwargs.pop("action_type", "requirement_hybrid_retrieval"),
            requirement_ids=kwargs.pop("requirement_ids", ["req-1"]),
            input_digest=kwargs.pop("input_digest", "d" * 64),
            **kwargs,
        )

    def test_policy_is_strict_and_closed(self) -> None:
        raw = json.loads((ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8"))
        mutated = copy.deepcopy(raw)
        mutated["unexpected"] = True
        with self.assertRaisesRegex(L1ControlError, "L1_INTERNAL_INVALID"):
            load_policy(mutated)
        self.assertEqual("off", self.policy.mode)
        self.assertEqual(("requirement_hybrid_retrieval", "stop"), self.policy.action_allowlist)
        self.assertEqual(0, self.policy.provider_calls_before_l2d)
        self.assertEqual(0, self.policy.graph_mutations_before_l2d)

    def test_action_ids_are_stable_and_input_bound(self) -> None:
        first = self.action()
        second = self.action()
        changed = self.action(input_digest="e" * 64)
        self.assertEqual(first.action_id, second.action_id)
        self.assertEqual(first.attempt_id, second.attempt_id)
        self.assertNotEqual(first.action_id, changed.action_id)
        self.assertNotEqual(first.attempt_id, changed.attempt_id)

    def test_pre_action_guard_allows_first_and_one_bounded_replan(self) -> None:
        state = BudgetState.initial()
        first = self.action()
        self.assertEqual(GuardStatus.ALLOW, guard_action(self.policy, state, first).status)
        state = apply_action_result(
            state,
            first,
            self.policy,
            record_ids_by_lane={"approved": ["record-1"]},
            result_bytes=10,
            elapsed_ms=5,
            graph_sha256_before="f" * 64,
            graph_sha256_after="f" * 64,
        )
        second = self.action(ordinal=1, input_digest="e" * 64)
        self.assertEqual(GuardStatus.ALLOW, guard_action(self.policy, state, second).status)
        state = apply_action_result(
            state,
            second,
            self.policy,
            record_ids_by_lane={"approved": ["record-2"]},
            result_bytes=10,
            elapsed_ms=5,
            graph_sha256_before="f" * 64,
            graph_sha256_after="f" * 64,
        )
        third = self.action(ordinal=2, input_digest="1" * 64)
        result = guard_action(self.policy, state, third)
        self.assertEqual(GuardStatus.BLOCK, result.status)
        self.assertEqual("L1_BUDGET_EXHAUSTED", result.reason)

    def test_budget_boundaries_are_blocked_before_execution(self) -> None:
        boundaries = (
            ("max_actions", "L1_BUDGET_EXHAUSTED"),
            ("max_unique_records", "L1_BUDGET_EXHAUSTED"),
            ("max_result_bytes", "L1_BUDGET_EXHAUSTED"),
            ("max_elapsed_ms", "L1_BUDGET_EXHAUSTED"),
        )
        for field, reason in boundaries:
            with self.subTest(field=field):
                action = self.action(
                    estimated_records=1,
                    estimated_result_bytes=1,
                    estimated_elapsed_ms=1,
                )
                state = BudgetState(
                    actions_used=self.policy.max_actions if field == "max_actions" else 0,
                    records_by_lane=(("approved", tuple(f"record-{i}" for i in range(self.policy.max_unique_records))),)
                    if field == "max_unique_records"
                    else (),
                    result_bytes=self.policy.max_result_bytes if field == "max_result_bytes" else 0,
                    elapsed_ms=self.policy.max_elapsed_ms if field == "max_elapsed_ms" else 0,
                )
                result = guard_action(self.policy, state, action)
                self.assertEqual(GuardStatus.BLOCK, result.status)
                self.assertEqual(reason, result.reason)

    def test_action_result_rejects_graph_drift_provider_work_and_duplicate_records(self) -> None:
        action = self.action()
        with self.assertRaisesRegex(L1ControlError, "L1_INTERNAL_INVALID"):
            apply_action_result(
                BudgetState.initial(), action, record_ids_by_lane={"approved": ["r"]}, result_bytes=1, elapsed_ms=1,
                policy=self.policy,
                graph_sha256_before="a" * 64, graph_sha256_after="b" * 64,
            )
        with self.assertRaisesRegex(L1ControlError, "L1_POLICY_BLOCKED"):
            apply_action_result(
                BudgetState.initial(), action, record_ids_by_lane={"approved": ["r"]}, result_bytes=1, elapsed_ms=1,
                policy=self.policy,
                graph_sha256_before="a" * 64, graph_sha256_after="a" * 64, provider_call_count=1,
            )
        with self.assertRaisesRegex(L1ControlError, "L1_INTERNAL_INVALID"):
            apply_action_result(
                BudgetState.initial(), action, record_ids_by_lane={"approved": ["r", "r"]}, result_bytes=1, elapsed_ms=1,
                policy=self.policy,
                graph_sha256_before="a" * 64, graph_sha256_after="a" * 64,
            )

    def test_progress_requires_newly_covered_requirement_not_new_records(self) -> None:
        previous = {"req-1": "explicit_gap", "req-2": "covered"}
        current = {"req-1": "covered", "req-2": "covered"}
        observation = observe_progress(previous, current)
        self.assertEqual(ProgressStatus.COMPLETE, observation.status)
        self.assertEqual(("req-1",), observation.newly_covered_requirement_ids)
        no_progress = observe_progress(previous, {"req-1": "explicit_gap", "req-2": "covered"})
        self.assertEqual(ProgressStatus.NO_PROGRESS, no_progress.status)
        self.assertEqual((), no_progress.newly_covered_requirement_ids)
        with self.assertRaisesRegex(L1ControlError, "L1_INTERNAL_INVALID"):
            observe_progress(previous, {"req-1": "covered"})

    def test_terminal_reduction_covers_every_closed_reason(self) -> None:
        complete = observe_progress({"req-1": "explicit_gap"}, {"req-1": "covered"})
        self.assertEqual("L1_COMPLETE", reduce_terminal(complete, state=BudgetState.initial(), policy=self.policy).reason)
        gap = observe_progress({"req-1": "explicit_gap", "req-2": "explicit_gap"}, {"req-1": "covered", "req-2": "explicit_gap"})
        self.assertEqual("L1_EXPLICIT_GAP", reduce_terminal(gap, state=BudgetState.initial(), policy=self.policy).reason)
        self.assertEqual("L1_NON_REFINABLE", reduce_terminal(gap, state=BudgetState.initial(), policy=self.policy, non_refinable=True).reason)
        self.assertEqual("L1_POLICY_BLOCKED", reduce_terminal(gap, state=BudgetState.initial(), policy=self.policy, policy_blocked=True).reason)
        self.assertEqual("L1_INTERNAL_INVALID", reduce_terminal(gap, state=BudgetState.initial(), policy=self.policy, internal_invalid=True).reason)
        unauthorized = observe_progress({"req-1": "explicit_gap"}, {"req-1": "unauthorized"})
        self.assertEqual("L1_UNAUTHORIZED", reduce_terminal(unauthorized, state=BudgetState.initial(), policy=self.policy).reason)
        conflict = observe_progress({"req-1": "explicit_gap"}, {"req-1": "conflict"})
        self.assertEqual("L1_CONFLICT", reduce_terminal(conflict, state=BudgetState.initial(), policy=self.policy).reason)
        no_progress = observe_progress({"req-1": "explicit_gap"}, {"req-1": "explicit_gap"})
        self.assertEqual("L1_NO_PROGRESS", reduce_terminal(no_progress, state=BudgetState.initial(), policy=self.policy).reason)
        progressed = observe_progress({"req-1": "explicit_gap", "req-2": "explicit_gap"}, {"req-1": "covered", "req-2": "explicit_gap"})
        exhausted = BudgetState(actions_used=self.policy.max_actions)
        self.assertEqual("L1_BUDGET_EXHAUSTED", reduce_terminal(progressed, state=exhausted, policy=self.policy).reason)

    def test_attempt_chain_is_ordered_unique_and_same_run(self) -> None:
        first = self.action()
        second = self.action(ordinal=1, input_digest="e" * 64)
        chain_digest = validate_action_chain([first, second], parent_plan_digest=self.plan, coverage_manifest_digest=self.manifest)
        self.assertEqual(64, len(chain_digest))
        for actions in ([second, first], [first, first]):
            with self.subTest(actions=actions):
                with self.assertRaisesRegex(L1ControlError, "L1_INTERNAL_INVALID"):
                    validate_action_chain(actions, parent_plan_digest=self.plan, coverage_manifest_digest=self.manifest)
        with self.assertRaisesRegex(L1ControlError, "L1_INTERNAL_INVALID"):
            validate_action_chain([self.action()], parent_plan_digest="f" * 64, coverage_manifest_digest=self.manifest)

    def test_replay_digest_is_invariant_to_mapping_insertion_order(self) -> None:
        left = BudgetState(records_by_lane=(("approved", ("r-1", "r-2")), ("candidate", ("r-3",))))
        right = BudgetState(records_by_lane=(("approved", ("r-1", "r-2")), ("candidate", ("r-3",))))
        self.assertEqual(replay_digest(left), replay_digest(right))

    def test_wave2_completion_receipt_is_schema_valid_and_binds_wave1(self) -> None:
        contracts = ROOT / "contracts" / "dual_pipeline"
        registry = SchemaRegistry(contracts / "schemas")
        registry.load()
        receipt = load_json(ROOT / "governance" / "dual_pipeline" / "l1_agentic" / "wave-02-completion.json")
        validate_artifact(receipt, registry)
        self.assertEqual("W2", receipt["wave_id"])
        self.assertEqual(
            "4a8eea6c68914d879954921cf3fc1e4ec23791efbf25d5180310f3d121975077",
            receipt["predecessor_receipt_digest"],
        )
        self.assertTrue(receipt["next_wave_authorized"])


if __name__ == "__main__":
    unittest.main()
