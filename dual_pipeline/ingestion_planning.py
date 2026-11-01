"""Provider-free, proposal-only ingestion plan v2 compiler (Wave 10).

This module is intentionally not imported by the intake executor.  It creates
an immutable description of the existing deterministic obligations; Wave 11
may map that description to a shadow ledger only after equivalence proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import create_artifact, validate_artifact
from .core import digest, stable_id, write_json_exclusive
from .exceptions import DualPipelineValueError

STEP_SEQUENCE = (
    ("candidate_obligation", "bind_candidate", "normalized-candidate-bundle", "CONTINUE_NOOP"),
    ("entity_resolution", "resolve_entities", "entity-resolution", "HITL"),
    ("candidate_comparison", "compare_candidate", "candidate-comparison", "CONTINUE_NOOP"),
    ("governed_context_hydration", "hydrate_context", "governed-context", "BLOCK"),
    ("deterministic_route", "route_candidate", "route-decision", "BLOCK"),
    ("advisory_reference", "reference_advisory", "ingestion-reasoning-decision", "CONTINUE_NOOP"),
    ("disposition_hitl", "assign_disposition", "assertion-disposition", "HITL"),
    ("graph_change_set_proposal", "propose_change_set", "graph-change-set", "BLOCK"),
)
LIFECYCLE_SEQUENCE = (
    ("x1", "evaluate_x1", "x1-control", "BLOCK"),
    ("x2", "evaluate_x2", "x2-control", "BLOCK"),
    ("x3", "evaluate_x3", "x3-control", "HITL"),
    ("l4_terminal", "seal_terminal", "ingestion-completion", "BLOCK"),
)


class IngestionPlanningError(DualPipelineValueError):
    """Closed failure for a plan/checkpoint binding violation."""


@dataclass(frozen=True)
class IngestionL1ControlPolicy:
    policy_id: str
    policy_digest: str
    mode: str


@dataclass(frozen=True)
class IngestionL1LedgerPolicy:
    """Default-off control for the non-authoritative W11 shadow ledger."""
    policy_id: str
    policy_digest: str
    allowed_modes: tuple[str, ...]


def load_ingestion_l1_policy(value: Mapping[str, Any]) -> IngestionL1ControlPolicy:
    expected = {
        "policy_id", "schema_version", "mode", "runtime_default", "proposal_only",
        "provider_calls_before_l2d", "graph_mutations_before_l2d", "extensions",
    }
    if (
        set(value) != expected or value.get("schema_version") != "1.0.0"
        or value.get("mode") != "off" or value.get("runtime_default") != "off"
        or value.get("proposal_only") is not True or value.get("provider_calls_before_l2d") != 0
        or value.get("graph_mutations_before_l2d") != 0
    ):
        raise IngestionPlanningError("ingestion L1 policy must remain provider-free, proposal-only, and off")
    return IngestionL1ControlPolicy(str(value["policy_id"]), digest(dict(value)), "off")


def load_ingestion_l1_ledger_policy(value: Mapping[str, Any]) -> IngestionL1LedgerPolicy:
    expected = {
        "policy_id", "schema_version", "runtime_default", "allowed_modes",
        "enforced_mode_requires_shadow_equivalence", "provider_calls_before_l2d",
        "graph_mutations_before_l2d", "extensions",
    }
    modes = value.get("allowed_modes")
    if (
        set(value) != expected or value.get("schema_version") != "1.0.0"
        or value.get("runtime_default") != "off" or not isinstance(modes, list)
        or set(modes) != {"off", "shadow", "enforced"}
        or value.get("enforced_mode_requires_shadow_equivalence") is not True
        or value.get("provider_calls_before_l2d") != 0 or value.get("graph_mutations_before_l2d") != 0
    ):
        raise IngestionPlanningError("ingestion ledger policy must be default-off, closed, provider-free, and graph read-only")
    return IngestionL1LedgerPolicy(str(value["policy_id"]), digest(dict(value)), tuple(modes))


def resolve_ingestion_ledger_mode(policy: IngestionL1LedgerPolicy, requested_mode: str | None, *, shadow_equivalent: bool = False) -> str:
    """Resolve an explicit ledger mode; the checked-in default is always off."""

    mode = "off" if requested_mode is None else requested_mode
    if mode not in policy.allowed_modes:
        raise IngestionPlanningError("L1_LEDGER_MODE_NOT_ALLOWED")
    if mode == "enforced" and not shadow_equivalent:
        raise IngestionPlanningError("L1_ENFORCED_MODE_REQUIRES_SHADOW_EQUIVALENCE")
    return mode


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise IngestionPlanningError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _directive(value: Mapping[str, Any]) -> tuple[str, list[str]]:
    directive_id = value.get("directive_id")
    package_hashes = value.get("package_hashes")
    if not isinstance(directive_id, str) or not directive_id or not isinstance(package_hashes, list) or not package_hashes:
        raise IngestionPlanningError("directive ID and exact package hashes are required")
    hashes = sorted({_sha(item, "package hash") for item in package_hashes})
    if len(hashes) != len(package_hashes):
        raise IngestionPlanningError("directive package hashes must be unique")
    return directive_id, hashes


def _active_pair(value: Mapping[str, Any]) -> dict[str, str]:
    fields = ("generation_id", "graph_sha256", "embedding_backend", "model_id", "model_revision")
    if any(not isinstance(value.get(field), str) or not value[field] for field in fields):
        raise IngestionPlanningError("active pair is incomplete")
    _sha(value["graph_sha256"], "active pair graph hash")
    return {field: str(value[field]) for field in fields}


def _step(*, plan_id: str, ordinal: int, candidate_id: str | None, kind: str, action: str, output: str, failure: str, dependencies: Sequence[str], inputs: Mapping[str, str]) -> dict[str, Any]:
    return {
        "step_id": stable_id("ingestion-plan-v2-step", plan_id, str(ordinal), candidate_id or "lifecycle", length=26),
        "ordinal": ordinal, "candidate_id": candidate_id, "step_kind": kind,
        "dependencies": list(dependencies), "local_action": action,
        "idempotency_key": stable_id("ingestion-plan-v2-idempotency", plan_id, str(ordinal), candidate_id or "lifecycle", length=26),
        "input_artifact_digests": dict(inputs), "output_contract": output,
        "exit_predicate": f"{kind}_output_valid", "failure_disposition": failure,
        "provider_call_count": 0, "graph_mutation_count": 0,
    }


def compile_ingestion_plan_v2(*, bundle: Mapping[str, Any], plan_v1: Mapping[str, Any], directive: Mapping[str, Any], active_pair: Mapping[str, Any], authorization_scope_digest: str, policy: IngestionL1ControlPolicy) -> dict[str, Any]:
    """Compile a candidate-bound DAG without executing any ingestion stage."""

    validate_artifact(dict(bundle)); validate_artifact(dict(plan_v1))
    if bundle.get("run_id") != plan_v1.get("run_id") or bundle.get("trace_id") != plan_v1.get("trace_id"):
        raise IngestionPlanningError("v1 plan and candidate bundle must share run and trace")
    if policy.mode != "off":
        raise IngestionPlanningError("ingestion plan v2 compiler is default-off only")
    directive_id, package_hashes = _directive(directive)
    pair = _active_pair(active_pair); _sha(authorization_scope_digest, "authorization scope digest")
    candidates = bundle.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise IngestionPlanningError("candidate bundle must contain candidates")
    plan_id = stable_id("ingestion-plan-v2", str(bundle["run_id"]), directive_id, length=26)
    inputs = {"candidate_bundle": bundle["canonical_digest"]["value"], "plan_v1": plan_v1["canonical_digest"]["value"], "authorization_scope": authorization_scope_digest, "policy": policy.policy_digest}
    steps: list[dict[str, Any]] = []
    ordinal = 1
    candidate_tails: list[str] = []
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise IngestionPlanningError("candidate ID is required")
        prior: list[str] = []
        for kind, action, output, failure in STEP_SEQUENCE:
            step = _step(plan_id=plan_id, ordinal=ordinal, candidate_id=candidate_id, kind=kind, action=action, output=output, failure=failure, dependencies=prior, inputs=inputs)
            steps.append(step); prior = [step["step_id"]]; ordinal += 1
        candidate_tails.extend(prior)
    prior = candidate_tails
    for kind, action, output, failure in LIFECYCLE_SEQUENCE:
        step = _step(plan_id=plan_id, ordinal=ordinal, candidate_id=None, kind=kind, action=action, output=output, failure=failure, dependencies=prior, inputs=inputs)
        steps.append(step); prior = [step["step_id"]]; ordinal += 1
    artifact = create_artifact(
        "ingestion-plan-v2", plan_id, plan_id=plan_id, directive_id=directive_id,
        run_id=bundle["run_id"], trace_id=bundle["trace_id"], source_id=bundle["source_id"],
        source_version_id=bundle["source_version_id"], package_hashes=package_hashes,
        active_pair=pair, authorization_scope_digest=authorization_scope_digest,
        policy_digest=policy.policy_digest, proposal_only=True, steps=steps, extensions={},
    )
    validate_ingestion_plan_v2(artifact)
    return artifact


def validate_ingestion_plan_v2(plan: Mapping[str, Any]) -> None:
    """Validate DAG order and the immutable provider/mutation boundary."""

    validate_artifact(dict(plan))
    seen: set[str] = set(); seen_idempotency_keys: set[str] = set(); prior_ordinal = 0
    required = {kind for kind, *_ in STEP_SEQUENCE} | {kind for kind, *_ in LIFECYCLE_SEQUENCE}
    actual = {step["step_kind"] for step in plan["steps"]}
    if not required <= actual:
        raise IngestionPlanningError("plan omits a required ingestion obligation")
    for step in plan["steps"]:
        if step["ordinal"] <= prior_ordinal or step["step_id"] in seen or step["idempotency_key"] in seen_idempotency_keys:
            raise IngestionPlanningError("step ordinals, IDs, and idempotency keys must be unique and increasing")
        if any(dep not in seen for dep in step["dependencies"]):
            raise IngestionPlanningError("step dependency is missing or forward-referenced")
        if step["provider_call_count"] != 0 or step["graph_mutation_count"] != 0:
            raise IngestionPlanningError("v2 planning steps must remain provider-free and graph read-only")
        if step["step_kind"] in {kind for kind, *_ in STEP_SEQUENCE} and step["candidate_id"] is None:
            raise IngestionPlanningError("candidate obligation lacks candidate scope")
        seen.add(step["step_id"]); seen_idempotency_keys.add(step["idempotency_key"]); prior_ordinal = step["ordinal"]


def build_ingestion_plan_step_receipt(*, plan: Mapping[str, Any], step: Mapping[str, Any], status: str = "PLANNED") -> dict[str, Any]:
    """Seal a non-executing step receipt; it cannot authorize or perform work."""

    validate_ingestion_plan_v2(plan)
    if step not in plan["steps"]:
        raise IngestionPlanningError("step does not belong to the bound plan")
    if status not in {"PLANNED", "BLOCKED", "NOOP"}:
        raise IngestionPlanningError("step receipt status is not permitted")
    receipt_id = stable_id("ingestion-plan-step-receipt", plan["plan_id"], str(step["ordinal"]), status, length=26)
    return create_artifact(
        "ingestion-plan-step-receipt", receipt_id, receipt_id=receipt_id,
        plan_digest=plan["canonical_digest"]["value"], step_id=step["step_id"],
        candidate_id=step["candidate_id"], status=status,
        input_artifact_digests=step["input_artifact_digests"], output_digest=None,
        proposal_only=True, provider_call_count=0, graph_mutation_count=0, extensions={},
    )


def _shadow_bindings(plan: Mapping[str, Any]) -> dict[str, Any]:
    validate_ingestion_plan_v2(plan)
    return {
        "plan_digest": plan["canonical_digest"]["value"], "directive_id": plan["directive_id"],
        "run_id": plan["run_id"], "package_hashes": plan["package_hashes"],
        "active_pair_generation_id": plan["active_pair"]["generation_id"],
        "graph_sha256": plan["active_pair"]["graph_sha256"],
        "authorization_scope_digest": plan["authorization_scope_digest"], "policy_digest": plan["policy_digest"],
    }


def build_ingestion_plan_checkpoint(*, plan: Mapping[str, Any], completed_step_receipt_digests: Sequence[str], terminal_state: str) -> dict[str, Any]:
    """Build a terminal-only binding template; Wave 11 owns actual persistence."""

    if terminal_state not in {"COMPLETED", "BLOCKED", "CANCELLED", "IDEMPOTENT_NOOP"}:
        raise IngestionPlanningError("open or partial checkpoints are forbidden in Wave 10")
    values = [_sha(value, "completed step receipt digest") for value in completed_step_receipt_digests]
    if not values:
        raise IngestionPlanningError("checkpoint requires completed receipt bindings")
    cid = stable_id("ingestion-plan-checkpoint", plan["plan_id"], terminal_state, length=26)
    return create_artifact(
        "ingestion-plan-checkpoint", cid, checkpoint_id=cid,
        **_shadow_bindings(plan), completed_step_receipt_digests=list(values),
        terminal_state=terminal_state, extensions={},
    )


def validate_checkpoint_binding(checkpoint: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    validate_artifact(dict(checkpoint))
    bindings = _shadow_bindings(plan)
    if any(checkpoint.get(key) != value for key, value in bindings.items()):
        raise IngestionPlanningError("L1_CHECKPOINT_BINDING_DRIFT")


def _baseline_outputs(plan: Mapping[str, Any], values: Mapping[str, str]) -> None:
    expected = {step["step_id"] for step in plan["steps"]}
    if set(values) != expected:
        raise IngestionPlanningError("L1_SHADOW_BASELINE_OUTPUT_BINDING_DRIFT")
    for step_id, output_digest in values.items():
        _sha(output_digest, f"baseline output digest for {step_id}")


def _shadow_step_receipt(plan: Mapping[str, Any], step: Mapping[str, Any], output_digest: str) -> dict[str, Any]:
    receipt_id = stable_id("ingestion-shadow-step-receipt", plan["plan_id"], step["step_id"], output_digest, length=26)
    return create_artifact(
        "ingestion-shadow-step-receipt", receipt_id, receipt_id=receipt_id,
        plan_digest=plan["canonical_digest"]["value"], step_id=step["step_id"],
        ordinal=step["ordinal"], candidate_id=step["candidate_id"],
        idempotency_key=step["idempotency_key"], mode="shadow", status="OBSERVED",
        input_artifact_digests=step["input_artifact_digests"], output_contract=step["output_contract"],
        output_digest=output_digest, deterministic_equivalent=True,
        provider_call_count=0, graph_mutation_count=0, extensions={},
    )


def build_ingestion_shadow_checkpoint(*, plan: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Create a read-only W11 checkpoint from an ordered, sealed shadow chain."""

    bindings = _shadow_bindings(plan)
    if not receipts:
        raise IngestionPlanningError("L1_SHADOW_CHECKPOINT_REQUIRES_RECEIPTS")
    steps = plan["steps"]
    if len(receipts) > len(steps):
        raise IngestionPlanningError("L1_SHADOW_CHECKPOINT_CHAIN_INVALID")
    completed_ids: list[str] = []
    completed_digests: list[str] = []
    for index, receipt in enumerate(receipts):
        validate_artifact(dict(receipt))
        step = steps[index]
        if (
            receipt.get("contract_id") != "urn:nee:dual-pipeline:ingestion-shadow-step-receipt:1.0.0"
            or receipt.get("plan_digest") != bindings["plan_digest"]
            or receipt.get("step_id") != step["step_id"] or receipt.get("ordinal") != step["ordinal"]
            or receipt.get("idempotency_key") != step["idempotency_key"]
            or receipt.get("provider_call_count") != 0 or receipt.get("graph_mutation_count") != 0
        ):
            raise IngestionPlanningError("L1_SHADOW_CHECKPOINT_CHAIN_INVALID")
        completed_ids.append(step["step_id"])
        completed_digests.append(receipt["canonical_digest"]["value"])
    next_step_id = steps[len(receipts)]["step_id"] if len(receipts) < len(steps) else None
    terminal_state = "OPEN" if next_step_id else "COMPLETED"
    checkpoint_id = stable_id("ingestion-shadow-checkpoint", plan["plan_id"], str(len(receipts)), terminal_state, length=26)
    return create_artifact(
        "ingestion-shadow-checkpoint", checkpoint_id, checkpoint_id=checkpoint_id,
        **bindings, completed_step_receipt_digests=completed_digests,
        completed_step_ids=completed_ids, next_step_id=next_step_id,
        terminal_state=terminal_state, mode="shadow", provider_call_count=0,
        graph_mutation_count=0, extensions={},
    )


def validate_ingestion_shadow_checkpoint(*, checkpoint: Mapping[str, Any], plan: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]) -> None:
    """Fail closed on any resume binding, chain, or output drift."""

    validate_artifact(dict(checkpoint))
    bindings = _shadow_bindings(plan)
    if checkpoint.get("contract_id") != "urn:nee:dual-pipeline:ingestion-shadow-checkpoint:1.0.0" or any(checkpoint.get(key) != value for key, value in bindings.items()):
        raise IngestionPlanningError("L1_CHECKPOINT_BINDING_DRIFT")
    expected = build_ingestion_shadow_checkpoint(plan=plan, receipts=receipts)
    fields = ("completed_step_receipt_digests", "completed_step_ids", "next_step_id", "terminal_state")
    if any(checkpoint.get(field) != expected.get(field) for field in fields):
        raise IngestionPlanningError("L1_SHADOW_CHECKPOINT_CHAIN_INVALID")


def run_ingestion_shadow_ledger(*, plan: Mapping[str, Any], baseline_output_digests: Mapping[str, str], policy: IngestionL1LedgerPolicy, stop_after: int | None = None, prior_receipts: Sequence[Mapping[str, Any]] = (), checkpoint: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Map v1 outputs to receipts in the existing order without executing v1 work.

    The caller supplies sealed-output digests from an already completed v1 path.
    This function never invokes intake, provider, graph, release, or activation code.
    """

    if resolve_ingestion_ledger_mode(policy, "shadow") != "shadow":
        raise IngestionPlanningError("L1_SHADOW_MODE_UNAVAILABLE")
    _baseline_outputs(plan, baseline_output_digests)
    prior = list(prior_receipts)
    if checkpoint is not None:
        validate_ingestion_shadow_checkpoint(checkpoint=checkpoint, plan=plan, receipts=prior)
    elif prior:
        raise IngestionPlanningError("L1_SHADOW_RESUME_REQUIRES_CHECKPOINT")
    steps = plan["steps"]
    if stop_after is not None and (not isinstance(stop_after, int) or stop_after < len(prior) or stop_after < 1):
        raise IngestionPlanningError("L1_SHADOW_STOP_AFTER_INVALID")
    limit = len(steps) if stop_after is None else min(stop_after, len(steps))
    receipts = prior[:]
    for step in steps[len(prior):limit]:
        receipts.append(_shadow_step_receipt(plan, step, baseline_output_digests[step["step_id"]]))
    result_checkpoint = build_ingestion_shadow_checkpoint(plan=plan, receipts=receipts)
    return {"mode": "shadow", "receipts": receipts, "checkpoint": result_checkpoint, "provider_call_count": 0, "graph_mutation_count": 0}


def verify_ingestion_shadow_equivalence(*, plan: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]], baseline_output_digests: Mapping[str, str]) -> dict[str, Any]:
    """Compare every v1 output digest with its shadow receipt, candidate by candidate."""

    _baseline_outputs(plan, baseline_output_digests)
    terminal = build_ingestion_shadow_checkpoint(plan=plan, receipts=receipts)
    if terminal["terminal_state"] != "COMPLETED":
        raise IngestionPlanningError("L1_SHADOW_EQUIVALENCE_INCOMPLETE")
    mismatches: list[str] = []
    for step, receipt in zip(plan["steps"], receipts):
        if receipt.get("output_digest") != baseline_output_digests[step["step_id"]]:
            mismatches.append(step["step_id"])
    if mismatches:
        raise IngestionPlanningError("L1_SHADOW_EQUIVALENCE_DRIFT")
    return {
        "plan_digest": plan["canonical_digest"]["value"], "receipt_chain_digest": digest([item["canonical_digest"]["value"] for item in receipts]),
        "candidate_step_count": sum(step["candidate_id"] is not None for step in plan["steps"]),
        "lifecycle_step_count": sum(step["candidate_id"] is None for step in plan["steps"]),
        "equivalent": True, "provider_call_count": 0, "graph_mutation_count": 0,
    }


def schedule_ingestion_enforced_ledger(*, plan: Mapping[str, Any], policy: IngestionL1LedgerPolicy, shadow_equivalence: Mapping[str, Any]) -> tuple[str, ...]:
    """Produce the only permitted enforced schedule after a complete shadow proof.

    This is deliberately a scheduler, not an executor.  The live intake engine
    remains v1 until a future explicit integration approval binds this result.
    """

    if resolve_ingestion_ledger_mode(policy, "enforced", shadow_equivalent=True) != "enforced":
        raise IngestionPlanningError("L1_ENFORCED_MODE_UNAVAILABLE")
    if (
        shadow_equivalence.get("equivalent") is not True
        or shadow_equivalence.get("plan_digest") != plan.get("canonical_digest", {}).get("value")
        or shadow_equivalence.get("provider_call_count") != 0
        or shadow_equivalence.get("graph_mutation_count") != 0
    ):
        raise IngestionPlanningError("L1_ENFORCED_MODE_REQUIRES_SHADOW_EQUIVALENCE")
    validate_ingestion_plan_v2(plan)
    return tuple(step["step_id"] for step in plan["steps"])


def persist_ingestion_shadow_checkpoint(*, runtime_root: Path, checkpoint: Mapping[str, Any]) -> Path:
    """Atomically persist one immutable shadow checkpoint under its exact run only."""

    validate_artifact(dict(checkpoint))
    if checkpoint.get("contract_id") != "urn:nee:dual-pipeline:ingestion-shadow-checkpoint:1.0.0":
        raise IngestionPlanningError("L1_SHADOW_CHECKPOINT_TYPE_INVALID")
    path = runtime_root / "runs" / checkpoint["run_id"] / "l1_shadow" / f"{checkpoint['checkpoint_id']}.json"
    write_json_exclusive(path, dict(checkpoint))
    return path
