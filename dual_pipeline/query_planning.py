"""Provider-free deterministic query-plan augmentation for L1 Wave 3.

Wave 3 deliberately compiles a controller-bound *candidate* from the existing
v2 plan and coverage manifest.  It does not route a request, initialize a
retriever, call a provider, mutate a graph, or read/write a receipt.  Runtime
orchestration remains behind the existing ``off`` path until later waves.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .core import digest, normalize_space, seal_artifact
from .l1_control import L1Action, L1ControlError, L1ControlPolicy, create_action

QUERY_PLAN_V3_CONTRACT = "urn:nee:dual-pipeline:query-plan:3.0.0"
QUERY_PLAN_V3_VERSION = "3.0.0"
SUPPORTED_MODES = ("off", "shadow", "bounded")
TERMINAL_PREDICATES = (
    "coverage_complete", "explicit_gap", "no_progress", "budget_exhausted",
    "unauthorized", "conflict", "hitl_required", "shadow",
)

# These tokens describe presentation rather than evidence.  They are removed
# from deterministic requirement queries; no synonyms or model paraphrases are
# introduced by this module.
_OUTPUT_ONLY_TERMS = re.compile(
    r"\b(?:slide|slides|ppt|pptx|powerpoint|table|tables|column|columns|"
    r"format|formatting|fenced\s+block|markdown|word\s+count|font|fonts|legend|output)\b",
    re.IGNORECASE,
)
_OUTPUT_ONLY_VERBS = re.compile(r"\b(?:create|provide|use|return|format|present|show|include|write|as|in)\b", re.IGNORECASE)
_PUNCTUATION = re.compile(r"^[\s,;:|.!?()\[\]{}\-]+|[\s,;:|.!?()\[\]{}\-]+$")
_NON_SUBSTANTIVE = {"a", "an", "and", "as", "for", "in", "of", "on", "the", "to", "use", "with"}


class QueryPlanningError(L1ControlError):
    """Closed error for invalid or unauthorized Wave 3 planning inputs."""


class RequirementQueryError(QueryPlanningError):
    """A requirement cannot be reduced to a bounded deterministic query."""

    def __init__(self, message: str) -> None:
        super().__init__("L1_REQUIREMENT_QUERY_INVALID", message)


@dataclass(frozen=True)
class CompiledRequirementQuery:
    requirement_id: str
    query: str
    input_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "requirement_id": self.requirement_id,
            "query": self.query,
            "input_digest": self.input_digest,
        }


@dataclass(frozen=True)
class ActionCompilation:
    actions: tuple[L1Action, ...]
    refinable_requirement_ids: tuple[str, ...]
    omitted_requirement_ids: tuple[str, ...]
    capped: bool
    terminal_reason: str | None

    @property
    def selected_requirement_ids(self) -> tuple[str, ...]:
        selected: list[str] = []
        for action in self.actions:
            selected.extend(action.requirement_ids)
        return tuple(selected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions": [action.to_dict() for action in self.actions],
            "refinable_requirement_ids": list(self.refinable_requirement_ids),
            "selected_requirement_ids": list(self.selected_requirement_ids),
            "omitted_requirement_ids": list(self.omitted_requirement_ids),
            "capped": self.capped, "terminal_reason": self.terminal_reason,
        }


@dataclass(frozen=True)
class QueryPlanningResult:
    mode: str
    plan: dict[str, Any]
    actions: tuple[L1Action, ...]
    omitted_requirement_ids: tuple[str, ...] = ()
    terminal_reason: str | None = None

    @property
    def shadow_work(self) -> bool:
        """Whether this result is an unexecuted v3 candidate."""
        return self.mode == "shadow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "plan": deepcopy(self.plan),
            "actions": [action.to_dict() for action in self.actions],
            "omitted_requirement_ids": list(self.omitted_requirement_ids),
            "terminal_reason": self.terminal_reason,
        }


def _require_digest(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise QueryPlanningError("L1_INTERNAL_INVALID", f"{label} must be a lowercase SHA-256 digest")
    return value


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    canonical = manifest.get("canonical_digest")
    if not isinstance(canonical, Mapping):
        raise QueryPlanningError("L1_INTERNAL_INVALID", "coverage manifest is not sealed")
    return _require_digest(canonical.get("value"), "coverage manifest digest")


def _plan_digest(plan: Mapping[str, Any]) -> str:
    canonical = plan.get("canonical_digest")
    if not isinstance(canonical, Mapping):
        raise QueryPlanningError("L1_INTERNAL_INVALID", "query plan is not sealed")
    return _require_digest(canonical.get("value"), "query plan digest")


def _strip_output_only(value: str) -> str:
    """Remove presentation-only terms while retaining source wording."""

    text = normalize_space(value)
    had_output_term = bool(_OUTPUT_ONLY_TERMS.search(text))
    text = _OUTPUT_ONLY_TERMS.sub(" ", text)
    text = _OUTPUT_ONLY_VERBS.sub(" ", text)
    text = re.sub(r"\s*[,;:|]\s*", " ", text)
    text = _PUNCTUATION.sub("", normalize_space(text))
    tokens = [
        token
        for token in text.split()
        if token.casefold() not in _NON_SUBSTANTIVE
        and not (had_output_term and token.isdigit())
    ]
    if not tokens or all(token.isdigit() for token in tokens):
        return ""
    return normalize_space(" ".join(tokens))


def compile_requirement_query(
    requirement: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    max_length: int = 512,
) -> CompiledRequirementQuery:
    """Compile one bounded query from canonical entities, horizon, and label.

    Formatting instructions are excluded.  The function never calls a model,
    expands synonyms, or uses semantic similarity.
    """

    if isinstance(max_length, bool) or not isinstance(max_length, int) or max_length < 1:
        raise QueryPlanningError("L1_INTERNAL_INVALID", "max_length must be a positive integer")
    requirement_id = requirement.get("requirement_id")
    if not isinstance(requirement_id, str) or not requirement_id:
        raise RequirementQueryError("requirement_id is missing")
    label = requirement.get("requirement_label")
    if not isinstance(label, str) or not normalize_space(label):
        raise RequirementQueryError(f"requirement {requirement_id} has an empty label")
    entities = plan.get("primary_entities")
    if not isinstance(entities, Sequence) or isinstance(entities, (str, bytes)):
        raise RequirementQueryError(f"requirement {requirement_id} has no canonical entities")
    normalized_entities = [normalize_space(str(entity)) for entity in entities if normalize_space(str(entity))]
    if not normalized_entities:
        raise RequirementQueryError(f"requirement {requirement_id} has no canonical entities")
    cleaned_label = _strip_output_only(label)
    if not cleaned_label:
        raise RequirementQueryError(f"requirement {requirement_id} contains only output formatting")
    horizon_value = plan.get("time_horizon")
    horizon = normalize_space(str(horizon_value)) if horizon_value else ""
    parts = [", ".join(normalized_entities)]
    if horizon:
        parts.append(horizon)
    parts.append(cleaned_label)
    query = normalize_space("; ".join(parts))
    if not query or len(query) > max_length:
        raise RequirementQueryError(
            f"requirement {requirement_id} query is empty or exceeds {max_length} characters"
        )
    input_digest = digest(
        {
            "query": query,
            "plan_id": plan.get("plan_id"),
            "query_type": plan.get("query_type"),
            "time_horizon": horizon or None,
        }
    )
    return CompiledRequirementQuery(requirement_id, query, input_digest)


def classify_refinability(outcome: str, reason_code: str) -> bool:
    """Return true only for an explicit retrieval-coverage gap."""

    return outcome == "explicit_gap" and reason_code == "RETRIEVAL_COVERAGE_MISSING"


def _observation(value: Any, requirement: Mapping[str, Any]) -> tuple[str, str]:
    if value is None:
        if requirement.get("missing_evidence_disposition") == "explicit_gap":
            return "explicit_gap", "RETRIEVAL_COVERAGE_MISSING"
        return "excluded", "REQUIREMENT_DISPOSITION_NON_REFINABLE"
    if isinstance(value, Mapping):
        outcome, reason = value.get("outcome"), value.get("reason_code")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        outcome, reason = value[0], value[1]
    else:
        outcome, reason = value, "RETRIEVAL_COVERAGE_MISSING"
    if not isinstance(outcome, str) or not isinstance(reason, str):
        raise QueryPlanningError("L1_INTERNAL_INVALID", "requirement observations must bind outcome and reason_code")
    return outcome, reason


def _group_refinable_requirements(
    requirements: Sequence[Any],
    plan: Mapping[str, Any],
    observations: Mapping[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]], list[str]]:
    refinable_ids: list[str] = []
    grouped: dict[str, dict[str, Any]] = {}
    group_order: list[str] = []
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            raise QueryPlanningError("L1_INTERNAL_INVALID", "coverage requirement must be an object")
        requirement_id = requirement.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id:
            raise QueryPlanningError("L1_INTERNAL_INVALID", "coverage requirement ID is invalid")
        outcome, reason = _observation(observations.get(requirement_id), requirement)
        if not classify_refinability(outcome, reason):
            continue
        compiled = compile_requirement_query(requirement, plan)
        refinable_ids.append(requirement_id)
        grouping_key = compiled.query.casefold()
        if grouping_key not in grouped:
            grouped[grouping_key] = {"compiled": compiled, "requirement_ids": []}
            group_order.append(grouping_key)
        grouped[grouping_key]["requirement_ids"].append(requirement_id)
    return refinable_ids, grouped, group_order


def compile_requirement_actions(
    plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    policy: L1ControlPolicy,
    *,
    observations: Mapping[str, Any] | None = None,
    max_targets: int = 8,
    mode: str = "shadow",
) -> ActionCompilation:
    """Compile ordered, deduplicated shadow actions without executing them."""

    if mode not in SUPPORTED_MODES:
        raise QueryPlanningError("L1_INTERNAL_INVALID", f"unsupported planning mode: {mode}")
    if mode == "off":
        return ActionCompilation((), (), (), False, None)
    if isinstance(max_targets, bool) or not isinstance(max_targets, int) or max_targets < 1:
        raise QueryPlanningError("L1_INTERNAL_INVALID", "max_targets must be a positive integer")
    requirements = coverage_manifest.get("requirements")
    if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)):
        raise QueryPlanningError("L1_INTERNAL_INVALID", "coverage manifest requirements are not an ordered list")
    plan_digest = _plan_digest(plan)
    manifest_digest = _manifest_digest(coverage_manifest)
    authorization_digest = _require_digest(
        coverage_manifest.get("authorization_scope_digest"),
        "authorization scope digest",
    )
    refinable_ids, grouped, group_order = _group_refinable_requirements(
        requirements, plan, observations or {}
    )

    # The caller may request a larger target cap, but the closed controller
    # budget remains the hard upper bound before an action is materialized.
    target_cap = min(max_targets, policy.max_actions)
    selected_groups = group_order[:target_cap]
    omitted_ids = tuple(
        requirement_id
        for grouping_key in group_order[target_cap:]
        for requirement_id in grouped[grouping_key]["requirement_ids"]
    )
    actions: list[L1Action] = []
    for attempt_ordinal, grouping_key in enumerate(selected_groups):
        group = grouped[grouping_key]
        compiled = group["compiled"]
        actions.append(
            create_action(
                parent_plan_digest=plan_digest,
                coverage_manifest_digest=manifest_digest,
                authorization_scope_digest=authorization_digest,
                attempt_ordinal=attempt_ordinal,
                action_type="requirement_hybrid_retrieval",
                requirement_ids=tuple(group["requirement_ids"]),
                input_digest=compiled.input_digest,
            )
        )
    capped = bool(omitted_ids)
    return ActionCompilation(
        actions=tuple(actions),
        refinable_requirement_ids=tuple(refinable_ids),
        omitted_requirement_ids=omitted_ids,
        capped=capped,
        terminal_reason="L1_BUDGET_EXHAUSTED" if capped else None,
    )


def compile_query_plan_candidate(
    plan_v2: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    policy: L1ControlPolicy,
    *,
    mode: str = "shadow",
    observations: Mapping[str, Any] | None = None,
    max_targets: int = 8,
) -> QueryPlanningResult:
    """Return the v2 plan unchanged in off mode or a sealed v3 shadow candidate."""

    if mode not in SUPPORTED_MODES:
        raise QueryPlanningError("L1_INTERNAL_INVALID", f"unsupported planning mode: {mode}")
    if mode == "off":
        return QueryPlanningResult("off", deepcopy(dict(plan_v2)), ())
    if plan_v2.get("schema_version") != "2.0.0":
        raise QueryPlanningError("L1_INTERNAL_INVALID", "Wave 3 candidate input must be a v2 query plan")
    source_plan_digest = _plan_digest(plan_v2)
    if coverage_manifest.get("query_plan_digest") != source_plan_digest:
        raise QueryPlanningError("L1_INTERNAL_INVALID", "coverage manifest is not bound to the v2 query plan")
    compilation = compile_requirement_actions(
        plan_v2,
        coverage_manifest,
        policy,
        observations=observations,
        max_targets=max_targets,
        mode=mode,
    )
    candidate = deepcopy(dict(plan_v2))
    candidate.pop("canonical_digest", None)
    candidate["contract_id"] = QUERY_PLAN_V3_CONTRACT
    candidate["schema_version"] = QUERY_PLAN_V3_VERSION
    candidate["controller"] = {
        "mode": mode, "policy_digest": policy.policy_digest,
        "coverage_manifest_digest": _manifest_digest(coverage_manifest),
        "action_allowlist": list(policy.action_allowlist),
        "aggregate_budget": {
            "max_additional_attempts": policy.max_additional_attempts,
            "max_actions": policy.max_actions, "max_unique_records": policy.max_unique_records,
            "max_result_bytes": policy.max_result_bytes, "max_elapsed_ms": policy.max_elapsed_ms,
        },
        "progress_rule": policy.progress_rule,
        "initial_action_ids": [action.action_id for action in compilation.actions],
        "terminal_predicates": list(TERMINAL_PREDICATES),
    }
    extensions = deepcopy(candidate.get("extensions") or {})
    extensions["com.partner_advisory.nee.l1-agentic"] = {
        "wave": "W3",
        "source_plan_digest": source_plan_digest,
        "omitted_requirement_ids": list(compilation.omitted_requirement_ids),
        "terminal_reason": compilation.terminal_reason,
    }
    candidate["extensions"] = extensions
    candidate = seal_artifact(candidate)
    return QueryPlanningResult(
        mode=mode, plan=candidate, actions=compilation.actions,
        omitted_requirement_ids=compilation.omitted_requirement_ids,
        terminal_reason=compilation.terminal_reason,
    )


# Short alias used by callers that already refer to the artifact as a plan.
compile_v3_plan = compile_query_plan_candidate


__all__ = (
    "ActionCompilation", "CompiledRequirementQuery", "QUERY_PLAN_V3_CONTRACT",
    "QueryPlanningError", "QueryPlanningResult", "RequirementQueryError",
    "classify_refinability", "compile_query_plan_candidate",
    "compile_requirement_actions", "compile_requirement_query", "compile_v3_plan",
)
