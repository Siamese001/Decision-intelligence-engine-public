"""Wave 12 terminal-only advisory learning controls."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json
from dual_pipeline.l1_shadow_learning import (
    L1ShadowLearningError,
    admit_l1_shadow_observation,
    build_l1_shadow_finding,
    load_shadow_learning_policy,
    propose_l1_shadow_change,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 64


class L1ShadowLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_shadow_learning_policy(read_json(ROOT / "requirements/l1-shadow-learning-policy.json"))
        self.terminal = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/query-plan-terminal.json")
        self.evaluation = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/query-l1-controller-eval-result.json")
        self.cohort = {"graph_sha256": "b" * 64, "model_or_index_generation": "generation-l1-shadow-test", "policy_digest": "c" * 64, "authorization_scope_digest": "d" * 64, "runtime_tier": "fixture"}

    def _observation(self, run_id: str, event_id: str):
        return admit_l1_shadow_observation(pipeline="query", run_id=run_id, terminal=self.terminal, evaluation=self.evaluation, cohort=self.cohort, finding_category="no_progress", reason_code="L1_NO_PROGRESS", evidence_event_id=event_id)

    def test_repeated_compatible_terminal_observations_produce_human_only_proposal(self) -> None:
        observations = [self._observation("run-shadow-learning-a", "event-shadow-learning-a"), self._observation("run-shadow-learning-b", "event-shadow-learning-b")]
        for observation in observations:
            validate_artifact(observation)
            self.assertFalse(observation["raw_content_present"])
        finding = build_l1_shadow_finding(policy=self.policy, observations=observations)
        validate_artifact(finding)
        self.assertTrue(finding["eligible_for_proposal"])
        proposal = propose_l1_shadow_change(finding=finding, observations=observations, target_field="action_ordering", isolated_replay_suite_digest=SHA, created_at="2026-08-06T00:00:00Z", expires_at="2026-09-06T00:00:00Z")
        validate_artifact(proposal)
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["auto_apply"])
        binding = proposal["extensions"]["com.nee.l1-shadow-proposal-binding"]
        self.assertEqual("action_ordering", binding["target_field"])
        self.assertTrue(binding["requires_new_implementation_branch"])

    def test_open_unbound_cross_cohort_and_ineligible_inputs_fail_closed(self) -> None:
        nonterminal = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/query-plan-v3.json")
        with self.assertRaisesRegex(L1ShadowLearningError, "TERMINAL_NOT_ADMISSIBLE"):
            admit_l1_shadow_observation(pipeline="query", run_id="run-shadow-invalid", terminal=nonterminal, evaluation=self.evaluation, cohort=self.cohort, finding_category="no_progress", reason_code="L1_NO_PROGRESS", evidence_event_id="event-shadow-invalid")
        first = self._observation("run-shadow-learning-c", "event-shadow-learning-c")
        changed_cohort = copy.deepcopy(self.cohort)
        changed_cohort["runtime_tier"] = "other"
        second = admit_l1_shadow_observation(pipeline="query", run_id="run-shadow-learning-d", terminal=self.terminal, evaluation=self.evaluation, cohort=changed_cohort, finding_category="no_progress", reason_code="L1_NO_PROGRESS", evidence_event_id="event-shadow-learning-d")
        with self.assertRaisesRegex(L1ShadowLearningError, "COHORT_MISMATCH"):
            build_l1_shadow_finding(policy=self.policy, observations=[first, second])
        finding = build_l1_shadow_finding(policy=self.policy, observations=[first])
        self.assertFalse(finding["eligible_for_proposal"])
        with self.assertRaisesRegex(L1ShadowLearningError, "PROPOSAL_NOT_ELIGIBLE"):
            propose_l1_shadow_change(finding=finding, observations=[first], target_field="budget", isolated_replay_suite_digest=SHA, created_at="2026-08-06T00:00:00Z", expires_at="2026-09-06T00:00:00Z")

    def test_zero_tolerance_checkpoint_finding_is_eligible_but_still_non_authoritative(self) -> None:
        observation = admit_l1_shadow_observation(pipeline="query", run_id="run-shadow-learning-e", terminal=self.terminal, evaluation=self.evaluation, cohort=self.cohort, finding_category="checkpoint_failure", reason_code="L1_CHECKPOINT_BINDING_DRIFT", evidence_event_id="event-shadow-learning-e")
        finding = build_l1_shadow_finding(policy=self.policy, observations=[observation])
        self.assertTrue(finding["eligible_for_proposal"])
        self.assertEqual(0, finding["automatic_policy_change_count"])


if __name__ == "__main__":
    unittest.main()
