"""Provider-free W5.1 semantic expansion for agentic readiness.

This module defines a candidate-only, source-bound chain for agent, version,
deployment, tool, evaluation, and readiness observations.  It intentionally
does not write graph rows, accept raw telemetry, or qualify a deployment for
production.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import msgspec

from ._validators import require_sha256, require_text
from .authorization import AuthorizationContext
from .exceptions import DualPipelineValueError
from .reference_monitor import ReferenceMonitor, ReferenceMonitorError

RECORD_TYPES = frozenset(
    {"agent", "agent_version", "tool", "agent_deployment", "evaluation_run", "readiness_observation"}
)
ENVIRONMENTS = frozenset({"development", "pilot", "production"})
LIFECYCLE_STATES = frozenset({"PROPOSED", "PILOT", "ACTIVE", "SUSPENDED", "RETIRED"})
EVALUATION_STATES = frozenset({"PASS", "FAIL", "INCOMPLETE"})
READINESS_DIMENSIONS = frozenset({"safety", "quality", "ownership", "tool_access", "operational"})
READINESS_STATES = frozenset({"READY", "AT_RISK", "BLOCKED", "UNKNOWN"})
RAW_TELEMETRY_FIELDS = frozenset(
    {"raw_prompt", "prompt", "trace", "trace_events", "tool_calls", "raw_output", "tokens", "telemetry"}
)
COMMON_FIELDS = frozenset(
    {"record_id", "record_type", "source_assertion_ids", "classification", "audiences", "source_as_of", "recorded_at"}
)
TYPE_FIELDS = {
    "agent": frozenset({"agent_id", "name", "owner_scope_id", "purpose"}),
    "agent_version": frozenset({"agent_version_id", "agent_id", "version", "model_id", "prompt_policy_digest"}),
    "tool": frozenset({"tool_id", "name", "purpose", "access_scope", "egress_policy_digest"}),
    "agent_deployment": frozenset({"deployment_id", "agent_version_id", "environment", "lifecycle_state", "tool_ids", "effective_at"}),
    "evaluation_run": frozenset({"evaluation_run_id", "deployment_id", "suite_digest", "result_state", "score", "evaluated_at"}),
    "readiness_observation": frozenset({"observation_id", "deployment_id", "dimension", "state", "value", "observed_at", "stale_after", "evaluation_run_ids"}),
}


class SemanticExpansionError(DualPipelineValueError):
    """Raised when a W5.1 readiness record or projection is unsafe."""


def _text(value: object, *, field: str) -> str:
    return require_text(value, field=field, error_type=SemanticExpansionError)


def _digest(value: object, *, field: str) -> str:
    return require_sha256(value, field=field, error_type=SemanticExpansionError)


def _ids(value: object, *, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise SemanticExpansionError(f"{field} must be a unique nonempty list of text")
    return sorted(item.strip() for item in value)


def _time(value: object, *, field: str) -> datetime:
    text = _text(value, field=field).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SemanticExpansionError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AgentRecord(msgspec.Struct, forbid_unknown_fields=True):
    record_id: str
    record_type: str
    source_assertion_ids: list[str]
    classification: str
    audiences: list[str]
    source_as_of: str
    recorded_at: str
    agent_id: str
    name: str
    owner_scope_id: str
    purpose: str


class AgentVersionRecord(msgspec.Struct, forbid_unknown_fields=True):
    record_id: str
    record_type: str
    source_assertion_ids: list[str]
    classification: str
    audiences: list[str]
    source_as_of: str
    recorded_at: str
    agent_version_id: str
    agent_id: str
    version: str
    model_id: str
    prompt_policy_digest: str


class ToolRecord(msgspec.Struct, forbid_unknown_fields=True):
    record_id: str
    record_type: str
    source_assertion_ids: list[str]
    classification: str
    audiences: list[str]
    source_as_of: str
    recorded_at: str
    tool_id: str
    name: str
    purpose: str
    access_scope: str
    egress_policy_digest: str


class AgentDeploymentRecord(msgspec.Struct, forbid_unknown_fields=True):
    record_id: str
    record_type: str
    source_assertion_ids: list[str]
    classification: str
    audiences: list[str]
    source_as_of: str
    recorded_at: str
    deployment_id: str
    agent_version_id: str
    environment: str
    lifecycle_state: str
    tool_ids: list[str]
    effective_at: str


class EvaluationRunRecord(msgspec.Struct, forbid_unknown_fields=True):
    record_id: str
    record_type: str
    source_assertion_ids: list[str]
    classification: str
    audiences: list[str]
    source_as_of: str
    recorded_at: str
    evaluation_run_id: str
    deployment_id: str
    result_state: str
    suite_digest: str
    score: float
    evaluated_at: str


class ReadinessObservationRecord(msgspec.Struct, forbid_unknown_fields=True):
    record_id: str
    record_type: str
    source_assertion_ids: list[str]
    classification: str
    audiences: list[str]
    source_as_of: str
    recorded_at: str
    observation_id: str
    deployment_id: str
    dimension: str
    state: str
    value: str
    observed_at: str
    stale_after: str
    evaluation_run_ids: list[str]


RECORD_STRUCT_MAP = {
    "agent": AgentRecord,
    "agent_version": AgentVersionRecord,
    "tool": ToolRecord,
    "agent_deployment": AgentDeploymentRecord,
    "evaluation_run": EvaluationRunRecord,
    "readiness_observation": ReadinessObservationRecord,
}


def _score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SemanticExpansionError("score must be numeric")
    if not 0 <= float(value) <= 1:
        raise SemanticExpansionError("score must be between 0 and 1")
    return float(value)


def _validate_agent_record(result: dict[str, Any]) -> None:
    for field in ("agent_id", "name", "owner_scope_id", "purpose"):
        result[field] = _text(result[field], field=field)
    if result["record_id"] != result["agent_id"]:
        raise SemanticExpansionError("agent record_id must equal agent_id")


def _validate_version_record(result: dict[str, Any]) -> None:
    for field in ("agent_version_id", "agent_id", "version", "model_id"):
        result[field] = _text(result[field], field=field)
    result["prompt_policy_digest"] = _digest(result["prompt_policy_digest"], field="prompt_policy_digest")
    if result["record_id"] != result["agent_version_id"]:
        raise SemanticExpansionError("agent_version record_id must equal agent_version_id")


def _validate_tool_record(result: dict[str, Any]) -> None:
    for field in ("tool_id", "name", "purpose", "access_scope"):
        result[field] = _text(result[field], field=field)
    result["egress_policy_digest"] = _digest(result["egress_policy_digest"], field="egress_policy_digest")
    if result["record_id"] != result["tool_id"]:
        raise SemanticExpansionError("tool record_id must equal tool_id")


def _validate_deployment_record(result: dict[str, Any]) -> None:
    for field in ("deployment_id", "agent_version_id", "environment", "lifecycle_state"):
        result[field] = _text(result[field], field=field)
    if result["environment"] not in ENVIRONMENTS:
        raise SemanticExpansionError("environment is outside the closed vocabulary")
    if result["lifecycle_state"] not in LIFECYCLE_STATES:
        raise SemanticExpansionError("lifecycle_state is outside the closed vocabulary")
    result["tool_ids"] = _ids(result["tool_ids"], field="tool_ids")
    _time(result["effective_at"], field="effective_at")
    if result["record_id"] != result["deployment_id"]:
        raise SemanticExpansionError("deployment record_id must equal deployment_id")


def _validate_evaluation_record(result: dict[str, Any]) -> None:
    for field in ("evaluation_run_id", "deployment_id", "result_state"):
        result[field] = _text(result[field], field=field)
    if result["result_state"] not in EVALUATION_STATES:
        raise SemanticExpansionError("result_state is outside the closed vocabulary")
    result["suite_digest"] = _digest(result["suite_digest"], field="suite_digest")
    result["score"] = _score(result["score"])
    _time(result["evaluated_at"], field="evaluated_at")
    if result["record_id"] != result["evaluation_run_id"]:
        raise SemanticExpansionError("evaluation record_id must equal evaluation_run_id")


def _validate_observation_record(result: dict[str, Any]) -> None:
    for field in ("observation_id", "deployment_id", "dimension", "state", "value"):
        result[field] = _text(result[field], field=field)
    if result["dimension"] not in READINESS_DIMENSIONS:
        raise SemanticExpansionError("readiness dimension is outside the closed vocabulary")
    if result["state"] not in READINESS_STATES:
        raise SemanticExpansionError("readiness state is outside the closed vocabulary")
    observed_at = _time(result["observed_at"], field="observed_at")
    stale_after = _time(result["stale_after"], field="stale_after")
    if stale_after < observed_at:
        raise SemanticExpansionError("stale_after cannot be before observed_at")
    result["evaluation_run_ids"] = _ids(result["evaluation_run_ids"], field="evaluation_run_ids")
    if result["record_id"] != result["observation_id"]:
        raise SemanticExpansionError("readiness record_id must equal observation_id")


_RECORD_SPECIFIC_VALIDATORS = {
    "agent": _validate_agent_record,
    "agent_version": _validate_version_record,
    "tool": _validate_tool_record,
    "agent_deployment": _validate_deployment_record,
    "evaluation_run": _validate_evaluation_record,
    "readiness_observation": _validate_observation_record,
}


def validate_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one exact typed readiness record using msgspec Struct and domain rules."""

    if not isinstance(value, Mapping):
        raise SemanticExpansionError("readiness record must be an object")
    record_type = _text(value.get("record_type"), field="record_type")
    if record_type not in RECORD_TYPES:
        raise SemanticExpansionError("record_type is outside the closed vocabulary")
    expected = COMMON_FIELDS | TYPE_FIELDS[record_type]
    if set(value) != expected:
        extra = sorted(set(value) - expected)
        missing = sorted(expected - set(value))
        raise SemanticExpansionError(f"{record_type} fields invalid; missing={missing}, extra={extra}")
    if RAW_TELEMETRY_FIELDS.intersection(value):
        raise SemanticExpansionError("raw telemetry is outside the W5.1 semantic bundle")

    struct_cls = RECORD_STRUCT_MAP.get(record_type)
    if struct_cls is not None:
        try:
            msgspec.convert(dict(value), struct_cls)
        except (msgspec.ValidationError, msgspec.DecodeError, TypeError, ValueError) as exc:
            raise SemanticExpansionError(f"Schema validation error in {record_type}: {exc}") from exc

    result = deepcopy(dict(value))
    result["record_id"] = _text(result["record_id"], field="record_id")
    result["source_assertion_ids"] = _ids(result["source_assertion_ids"], field="source_assertion_ids")
    result["classification"] = _text(result["classification"], field="classification")
    result["audiences"] = _ids(result["audiences"], field="audiences")
    source_as_of = _time(result["source_as_of"], field="source_as_of")
    recorded_at = _time(result["recorded_at"], field="recorded_at")
    if source_as_of > recorded_at:
        raise SemanticExpansionError("source_as_of cannot be after recorded_at")
    result["source_as_of"] = source_as_of.isoformat().replace("+00:00", "Z")
    result["recorded_at"] = recorded_at.isoformat().replace("+00:00", "Z")

    validator = _RECORD_SPECIFIC_VALIDATORS[record_type]
    validator(result)
    return result


def validate_bundle(value: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Validate typed records and their closed-world links."""

    required = {"agents", "agent_versions", "tools", "deployments", "evaluations", "readiness_observations"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise SemanticExpansionError(f"bundle must contain exactly {sorted(required)}")
    type_by_key = {
        "agents": "agent",
        "agent_versions": "agent_version",
        "tools": "tool",
        "deployments": "agent_deployment",
        "evaluations": "evaluation_run",
        "readiness_observations": "readiness_observation",
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for key, record_type in type_by_key.items():
        if not isinstance(value[key], list):
            raise SemanticExpansionError(f"{key} must be a list")
        rows = [validate_record(item) for item in value[key]]
        if any(row["record_type"] != record_type for row in rows):
            raise SemanticExpansionError(f"{key} contains the wrong record_type")
        result[key] = rows
    all_records = [row for rows in result.values() for row in rows]
    record_ids = [row["record_id"] for row in all_records]
    if len(record_ids) != len(set(record_ids)):
        raise SemanticExpansionError("record_id values must be globally unique")
    agent_ids = {row["agent_id"] for row in result["agents"]}
    version_ids = {row["agent_version_id"] for row in result["agent_versions"]}
    tool_ids = {row["tool_id"] for row in result["tools"]}
    deployment_ids = {row["deployment_id"] for row in result["deployments"]}
    evaluation_ids = {row["evaluation_run_id"] for row in result["evaluations"]}
    if any(row["agent_id"] not in agent_ids for row in result["agent_versions"]):
        raise SemanticExpansionError("agent_version references an unknown agent")
    if any(
        row["agent_version_id"] not in version_ids or not set(row["tool_ids"]).issubset(tool_ids)
        for row in result["deployments"]
    ):
        raise SemanticExpansionError("deployment references an unknown version or tool")
    if any(row["deployment_id"] not in deployment_ids for row in result["evaluations"]):
        raise SemanticExpansionError("evaluation references an unknown deployment")
    if any(
        row["deployment_id"] not in deployment_ids
        or not set(row["evaluation_run_ids"]).issubset(evaluation_ids)
        for row in result["readiness_observations"]
    ):
        raise SemanticExpansionError("readiness observation references an unknown deployment or evaluation")
    return result


def _parse_projection_time(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SemanticExpansionError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_ids(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted({item for row in rows for item in row["source_assertion_ids"]})


def _determine_readiness_state(
    deployment: Mapping[str, Any],
    versions: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    current: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    current_states: set[str],
) -> tuple[str, list[str], list[str]]:
    gaps: list[str] = []
    validation_questions: list[str] = []
    if conflicts:
        state = "HITL_REQUIRED"
        gaps.append("Current readiness observations conflict and require review.")
        validation_questions.append("Which current readiness observation is authoritative, and what supersedes the other?")
    elif not current:
        state = "STALE_OR_UNKNOWN"
        gaps.append("No current readiness observation is available at the requested as-of time.")
        validation_questions.append("What source-bound readiness observation should be captured?")
    elif current_states.intersection({"BLOCKED", "AT_RISK", "UNKNOWN"}):
        state = "HITL_REQUIRED"
        gaps.append("A current readiness dimension is blocked, at risk, or unknown.")
        validation_questions.append("What owner and remediation evidence resolve the readiness gap?")
    elif deployment["lifecycle_state"] in {"SUSPENDED", "RETIRED"}:
        state = "HITL_REQUIRED"
        gaps.append("The deployment lifecycle state is not active for a readiness recommendation.")
        validation_questions.append("Is the deployment lifecycle state current and approved for the intended scope?")
    elif not versions or not agents:
        state = "CANDIDATE_WITH_GAPS"
        gaps.append("The audience-visible agent or agent-version link is incomplete.")
        validation_questions.append("Which source-bound agent and version are in scope for this deployment?")
    elif not evaluations or any(row["result_state"] != "PASS" for row in evaluations):
        state = "CANDIDATE_WITH_GAPS"
        gaps.append("A passing evaluation run is missing for the visible deployment.")
        validation_questions.append("Which bounded evaluation suite and threshold support readiness?")
    elif len(tools) != len(deployment["tool_ids"]):
        state = "CANDIDATE_WITH_GAPS"
        gaps.append("One or more declared tool bindings are not audience-visible.")
        validation_questions.append("Which governed tool bindings are approved for this audience and deployment?")
    else:
        state = "READY_FOR_REVIEW"
        validation_questions.append("Which human reviewer confirms the evidence and bounded deployment scope?")
    return state, gaps, validation_questions


def project_agentic_readiness(
    bundle: Mapping[str, Any],
    *,
    deployment_id: str,
    audience: str,
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, Any]:
    """Project one deployment's readiness without exposing filtered records."""

    typed = validate_bundle(bundle)
    deployment_id = _text(deployment_id, field="deployment_id")
    as_of_time = _parse_projection_time(as_of, field="as_of")
    known_time = _parse_projection_time(known_at, field="known_at")
    if as_of_time > known_time:
        raise SemanticExpansionError("as_of cannot be after known_at")
    records = [row for rows in typed.values() for row in rows]
    try:
        visible = reference_monitor.filter_retrieval_records(
            records,
            audience=audience,
            authorization_context=authorization_context,
            known_at=known_at,
        )
    except ReferenceMonitorError as exc:
        raise SemanticExpansionError(str(exc)) from exc
    visible = [
        row
        for row in visible
        if _parse_projection_time(row["source_as_of"], field="source_as_of") <= known_time
        and _parse_projection_time(row["recorded_at"], field="recorded_at") <= known_time
    ]
    visible_by_type = {
        record_type: [row for row in visible if row["record_type"] == record_type]
        for record_type in RECORD_TYPES
    }
    deployments = [row for row in visible_by_type["agent_deployment"] if row["deployment_id"] == deployment_id]
    if not deployments:
        return {
            "artifact_type": "agentic_readiness_projection",
            "authority": "DERIVED_CANDIDATE_ONLY",
            "audience": audience,
            "classification": "public",
            "audiences": [],
            "deployment_id": deployment_id,
            "state": "STALE_OR_UNKNOWN",
            "as_of": as_of,
            "known_at": known_at,
            "source_assertion_ids": [],
            "record_ids": [],
            "gaps": ["No audience-visible deployment evidence is available."],
            "validation_questions": ["Which source-bound deployment should be reviewed?"],
            "falsification_criteria": ["A visible, current deployment record would change this abstention."],
            "graph_mutation_authorized": False,
            "provider_calls": 0,
            "graph_mutations": 0,
            "release_activation": False,
        }
    deployment = deployments[0]
    versions = [row for row in visible_by_type["agent_version"] if row["agent_version_id"] == deployment["agent_version_id"]]
    agents = [row for row in visible_by_type["agent"] if versions and row["agent_id"] == versions[0]["agent_id"]]
    tools = [row for row in visible_by_type["tool"] if row["tool_id"] in set(deployment["tool_ids"])]
    evaluations = [row for row in visible_by_type["evaluation_run"] if row["deployment_id"] == deployment_id]
    readiness = [row for row in visible_by_type["readiness_observation"] if row["deployment_id"] == deployment_id]
    current = [
        row
        for row in readiness
        if _parse_projection_time(row["observed_at"], field="observed_at") <= as_of_time
        and _parse_projection_time(row["stale_after"], field="stale_after") >= as_of_time
    ]
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for row in current:
        by_dimension.setdefault(row["dimension"], []).append(row)
    conflicts = [
        row
        for rows in by_dimension.values()
        if len({(item["state"], item["value"]) for item in rows}) > 1
        for row in rows
    ]
    current_states = {row["state"] for row in current}
    state, gaps, validation_questions = _determine_readiness_state(
        deployment, versions, agents, tools, evaluations, current, conflicts, current_states
    )
    included = [*agents, *versions, deployment, *tools, *evaluations, *current]
    source_assertion_ids = _source_ids(included)
    labels = reference_monitor.derived_labels(source_assertion_ids)
    if audience not in labels["audiences"]:
        raise SemanticExpansionError("visible readiness sources do not grant the requested audience")
    return {
        "artifact_type": "agentic_readiness_projection",
        "authority": "DERIVED_CANDIDATE_ONLY",
        "audience": audience,
        "classification": labels["classification"],
        "audiences": labels["audiences"],
        "deployment_id": deployment_id,
        "agent_id": agents[0]["agent_id"] if agents else None,
        "agent_version_id": deployment["agent_version_id"],
        "tool_ids": sorted(row["tool_id"] for row in tools),
        "evaluation_run_ids": sorted(row["evaluation_run_id"] for row in evaluations),
        "readiness_observation_ids": sorted(row["observation_id"] for row in current),
        "state": state,
        "as_of": as_of,
        "known_at": known_at,
        "source_assertion_ids": source_assertion_ids,
        "record_ids": sorted(row["record_id"] for row in included),
        "gaps": gaps,
        "validation_questions": validation_questions,
        "falsification_criteria": ["A current failing evaluation, conflicting observation, or revoked source changes this candidate state."],
        "graph_mutation_authorized": False,
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
    }


__all__ = ["SemanticExpansionError", "project_agentic_readiness", "validate_bundle", "validate_record"]
