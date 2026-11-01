"""Wave 12 terminal-artifact-only, non-authoritative L1 shadow learning."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import create_artifact, validate_artifact
from .core import digest, stable_id
from .exceptions import DualPipelineValueError

FINDING_CATEGORIES = {
    "requirement_compiler_miss", "repeated_true_gap", "inefficient_action",
    "false_positive_candidate_action", "budget_exhaustion", "no_progress",
    "checkpoint_failure",
}
COHORT_KEYS = {
    "graph_sha256", "model_or_index_generation", "policy_digest",
    "authorization_scope_digest", "runtime_tier",
}


class L1ShadowLearningError(DualPipelineValueError):
    """Closed failure for unsafe or incomparable learning inputs."""


@dataclass(frozen=True)
class ShadowLearningPolicy:
    policy_id: str
    policy_digest: str
    minimum_repeat_count: int
    zero_tolerance_categories: frozenset[str]


def load_shadow_learning_policy(value: Mapping[str, Any]) -> ShadowLearningPolicy:
    expected = {"policy_id", "schema_version", "runtime_default", "minimum_repeat_count", "zero_tolerance_categories", "automatic_policy_change_count", "raw_content_registry_allowed", "extensions"}
    if set(value) != expected:
        raise L1ShadowLearningError("L1_SHADOW_POLICY_KEYS_NOT_CLOSED")
    categories = value.get("zero_tolerance_categories")
    if (
        value.get("schema_version") != "1.0.0" or value.get("runtime_default") != "off"
        or not isinstance(value.get("minimum_repeat_count"), int) or value["minimum_repeat_count"] < 2
        or not isinstance(categories, list) or not set(categories) <= FINDING_CATEGORIES
        or value.get("automatic_policy_change_count") != 0 or value.get("raw_content_registry_allowed") is not False
    ):
        raise L1ShadowLearningError("L1_SHADOW_POLICY_NOT_ADVISORY")
    return ShadowLearningPolicy(str(value["policy_id"]), digest(dict(value)), value["minimum_repeat_count"], frozenset(categories))


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise L1ShadowLearningError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _terminal_is_admissible(pipeline: str, terminal: Mapping[str, Any]) -> None:
    validate_artifact(dict(terminal))
    contract_id = terminal.get("contract_id")
    if pipeline == "query":
        valid = contract_id == "urn:nee:dual-pipeline:query-plan-terminal:1.0.0"
    else:
        valid = contract_id in {
            "urn:nee:dual-pipeline:ingestion-plan-checkpoint:1.0.0",
            "urn:nee:dual-pipeline:ingestion-shadow-checkpoint:1.0.0",
        } and terminal.get("terminal_state") in {"COMPLETED", "BLOCKED", "CANCELLED", "IDEMPOTENT_NOOP"}
    if not valid:
        raise L1ShadowLearningError("L1_SHADOW_TERMINAL_NOT_ADMISSIBLE")


def admit_l1_shadow_observation(*, pipeline: str, run_id: str, terminal: Mapping[str, Any], evaluation: Mapping[str, Any], cohort: Mapping[str, str], finding_category: str, reason_code: str, evidence_event_id: str) -> dict[str, Any]:
    """Admit only contract-valid terminal and passing evaluation artifacts."""

    if pipeline not in {"query", "ingestion"} or finding_category not in FINDING_CATEGORIES:
        raise L1ShadowLearningError("L1_SHADOW_OBSERVATION_CLASSIFICATION_INVALID")
    _terminal_is_admissible(pipeline, terminal)
    validate_artifact(dict(evaluation))
    if evaluation.get("status") != "PASS":
        raise L1ShadowLearningError("L1_SHADOW_EVALUATION_NOT_ADMISSIBLE")
    if set(cohort) != COHORT_KEYS:
        raise L1ShadowLearningError("L1_SHADOW_COHORT_BINDING_INVALID")
    normalized_cohort = dict(cohort)
    for field in ("graph_sha256", "policy_digest", "authorization_scope_digest"):
        _sha(normalized_cohort[field], field)
    if not normalized_cohort["model_or_index_generation"] or not normalized_cohort["runtime_tier"]:
        raise L1ShadowLearningError("L1_SHADOW_COHORT_BINDING_INVALID")
    terminal_digest = terminal["canonical_digest"]["value"]
    evaluation_digest = evaluation["canonical_digest"]["value"]
    observation_id = stable_id("l1-shadow-observation", pipeline, run_id, terminal_digest, evaluation_digest, finding_category, reason_code, length=28)
    return create_artifact(
        "l1-shadow-observation", observation_id, observation_id=observation_id,
        pipeline=pipeline, run_id=run_id, terminal_contract_id=terminal["contract_id"],
        terminal_digest=terminal_digest, evaluation_contract_id=evaluation["contract_id"],
        evaluation_digest=evaluation_digest, cohort=normalized_cohort,
        finding_category=finding_category, reason_code=reason_code,
        evidence_event_id=evidence_event_id, raw_content_present=False, status="ADMITTED", extensions={},
    )


def build_l1_shadow_finding(*, policy: ShadowLearningPolicy, observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Create an immutable finding only from compatible admitted observations."""

    if not observations:
        raise L1ShadowLearningError("L1_SHADOW_FINDING_REQUIRES_OBSERVATIONS")
    values = [dict(item) for item in observations]
    for item in values:
        validate_artifact(item)
        if item.get("contract_id") != "urn:nee:dual-pipeline:l1-shadow-observation:1.0.0" or item.get("status") != "ADMITTED" or item.get("raw_content_present"):
            raise L1ShadowLearningError("L1_SHADOW_OBSERVATION_NOT_ADMITTED")
    first = values[0]
    compatibility = (first["pipeline"], first["finding_category"], first["reason_code"], first["cohort"])
    if any((item["pipeline"], item["finding_category"], item["reason_code"], item["cohort"]) != compatibility for item in values):
        raise L1ShadowLearningError("L1_SHADOW_COHORT_MISMATCH")
    if len({item["canonical_digest"]["value"] for item in values}) != len(values):
        raise L1ShadowLearningError("L1_SHADOW_DUPLICATE_OBSERVATION")
    category = first["finding_category"]
    eligible = len(values) >= policy.minimum_repeat_count or category in policy.zero_tolerance_categories
    finding_id = stable_id("l1-shadow-finding", first["pipeline"], category, first["reason_code"], first["cohort"], [item["canonical_digest"]["value"] for item in values], length=28)
    return create_artifact(
        "shadow-finding", finding_id, finding_id=finding_id, pipeline=first["pipeline"],
        finding_category=category, reason_code=first["reason_code"], cohort=first["cohort"],
        observation_digests=[item["canonical_digest"]["value"] for item in values],
        evidence_event_ids=[item["evidence_event_id"] for item in values], observation_count=len(values),
        eligible_for_proposal=eligible, automatic_policy_change_count=0, extensions={},
    )


def propose_l1_shadow_change(*, finding: Mapping[str, Any], observations: Sequence[Mapping[str, Any]], target_field: str, isolated_replay_suite_digest: str, created_at: str, expires_at: str) -> dict[str, Any]:
    """Build an approval-required proposal; it cannot write or deploy a change."""

    validate_artifact(dict(finding))
    if finding.get("contract_id") != "urn:nee:dual-pipeline:shadow-finding:1.0.0" or not finding.get("eligible_for_proposal"):
        raise L1ShadowLearningError("L1_SHADOW_PROPOSAL_NOT_ELIGIBLE")
    if target_field not in {"vocabulary", "refinability_rule", "action_ordering", "budget"}:
        raise L1ShadowLearningError("L1_SHADOW_PROPOSAL_TARGET_INVALID")
    _sha(isolated_replay_suite_digest, "isolated replay suite digest")
    matching = [dict(item) for item in observations if item.get("canonical_digest", {}).get("value") in finding["observation_digests"]]
    if len(matching) != finding["observation_count"]:
        raise L1ShadowLearningError("L1_SHADOW_PROPOSAL_EVIDENCE_BINDING_DRIFT")
    proposal_id = stable_id("l1-shadow-learning-proposal", finding["finding_id"], target_field, length=28)
    proposal_type = {"vocabulary": "alias_addition", "refinability_rule": "routing", "action_ordering": "workflow", "budget": "threshold"}[target_field]
    return create_artifact(
        "learning-change-proposal", proposal_id, proposal_id=proposal_id,
        run_ids=[item["run_id"] for item in matching], proposal_type=proposal_type, status="proposed",
        problem=f"Repeated {finding['finding_category']} finding {finding['finding_id']} requires review.",
        evidence_event_ids=finding["evidence_event_ids"],
        proposed_change=f"Evaluate a human-reviewed {target_field} adjustment; do not apply it automatically.",
        evaluation_plan=["Use the isolated replay suite bound below.", "Implement only on a new branch after approval and Wave 0/1/8-equivalent validation."],
        risk="high" if finding["finding_category"] == "checkpoint_failure" else "medium",
        approval_required=True, auto_apply=False, created_at=created_at,
        extensions={"com.nee.l1-shadow-proposal-binding": {"finding_digest": finding["canonical_digest"]["value"], "target_field": target_field, "expected_improvement": "Reduce the repeated finding without weakening hard gates.", "guardrails": ["automatic_policy_change_count=0", "no graph mutation", "no runtime deployment"], "isolated_replay_suite_digest": isolated_replay_suite_digest, "rollback": "Revert the separately approved implementation branch and retain this proposal.", "expires_at": expires_at, "requires_new_implementation_branch": True}},
    )
