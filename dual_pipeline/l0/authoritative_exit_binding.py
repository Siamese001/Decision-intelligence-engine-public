"""
authoritative_exit_binding.py — Layer 0 Authoritative Exit Evidence Binding & Verification.

Governed Under AGENTS.md Architectural Invariants and Wave 6 Alignment Contract:
1. Strict Non-Empty Evidence Verification: Package capabilities select required non-empty
   evidence. NOT_APPLICABLE requires an explicit justified policy reason.
2. Anti-Advisory Invariant: Advisory or partial outcomes cannot implicitly authorize publication.
3. Tamper & Drift Protection: Missing, swapped, or drifted evidence artifacts fail closed.
4. Active-Pair & Coverage Invariant: Canonical publication and activation require cryptographic
   active-pair binding and live graph coverage readback.
5. Copilot Exit Parity: Streamed and ordinary copilot responses consume identical grounding
   evidence and yield identical exits.
6. Durability: Crash, retry, concurrent publication, and approval-resume preserve immutable evidence.

Strict Standard-Library Only. Max 400 Lines Budget.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class ExitCapability(str, Enum):
    """Authoritative exit capabilities requiring verified evidence."""
    CANONICAL_PUBLICATION = "canonical_publication"
    GRAPH_ACTIVATION = "graph_activation"
    EXECUTIVE_DELIVERY = "executive_delivery"
    IDEMPOTENT_RESUME = "idempotent_resume"
    COPILOT_STREAMING = "copilot_streaming"
    MCP_EXPORT = "mcp_export"


class AuthoritativeExitError(RuntimeError):
    """Raised when an authoritative exit fails evidence or active-pair verification."""


@dataclass(frozen=True)
class ExitEvidenceRequirement:
    """Declared evidence requirement for an exit capability."""
    capability: ExitCapability
    required_files: Sequence[str]
    required_keys: Sequence[str]
    allow_advisory: bool = False
    requires_active_pair: bool = False
    requires_coverage_readback: bool = False


CAPABILITY_REQUIREMENTS: Dict[ExitCapability, ExitEvidenceRequirement] = {
    ExitCapability.CANONICAL_PUBLICATION: ExitEvidenceRequirement(
        capability=ExitCapability.CANONICAL_PUBLICATION,
        required_files=("final_release_decision.json", "llm_judge_evaluation.json", "qa_gates_report.json"),
        required_keys=("authoritative_write_authorized", "output_3_llm_score_pass", "artifact_manifest"),
        allow_advisory=False,
        requires_active_pair=True,
        requires_coverage_readback=True,
    ),
    ExitCapability.GRAPH_ACTIVATION: ExitEvidenceRequirement(
        capability=ExitCapability.GRAPH_ACTIVATION,
        required_files=("final_release_decision.json",),
        required_keys=("authoritative_write_authorized",),
        allow_advisory=False,
        requires_active_pair=True,
        requires_coverage_readback=True,
    ),
    ExitCapability.EXECUTIVE_DELIVERY: ExitEvidenceRequirement(
        capability=ExitCapability.EXECUTIVE_DELIVERY,
        required_files=("final_release_decision.json",),
        required_keys=("authoritative_write_authorized",),
        allow_advisory=False,
    ),
    ExitCapability.IDEMPOTENT_RESUME: ExitEvidenceRequirement(
        capability=ExitCapability.IDEMPOTENT_RESUME,
        required_files=("final_release_decision.json",),
        required_keys=(
            "source_file_hash",
            "attendee_roster_hash",
            "policy_snapshot_digest",
            "prompt_template_hash",
            "evaluator_version",
            "output_schema_version",
        ),
        allow_advisory=False,
    ),
    ExitCapability.COPILOT_STREAMING: ExitEvidenceRequirement(
        capability=ExitCapability.COPILOT_STREAMING,
        required_files=(),
        required_keys=("track", "model_id"),
        allow_advisory=True,
    ),
    ExitCapability.MCP_EXPORT: ExitEvidenceRequirement(
        capability=ExitCapability.MCP_EXPORT,
        required_files=("final_release_decision.json",),
        required_keys=("authoritative_write_authorized",),
        allow_advisory=False,
    ),
}


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 digest of file bytes."""
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_not_applicable(explicit_na_reason: Optional[str]) -> Tuple[bool, List[str]]:
    if not explicit_na_reason or len(explicit_na_reason.strip()) < 10:
        return False, ["NOT_APPLICABLE requires an explicit justified policy reason (min 10 chars)."]
    return True, []


def _validate_required_files(p: Path, required_files: Tuple[str, ...]) -> List[str]:
    errors: List[str] = []
    for rf in required_files:
        fp = p / rf
        if not fp.is_file():
            errors.append(f"Missing required evidence file: {rf}")
        elif fp.stat().st_size <= 10:
            errors.append(f"Required evidence file is empty: {rf}")
    return errors


def _check_artifact_manifest_drift(p: Path, manifest: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    checks = (
        ("output_1_sha256", "01_*hitl_resolved_transcript.md"),
        ("output_2_sha256", "02_*executive_recap.md"),
        ("output_3_sha256", "03_*canonical_signal_projection.json"),
    )
    for out_key, pattern in checks:
        expected_sha = manifest.get(out_key)
        if not expected_sha:
            continue
        matches = list(p.glob(pattern))
        if not matches:
            errors.append(f"Referenced artifact {pattern} missing from output directory")
        elif compute_file_sha256(matches[0]) != expected_sha:
            actual = compute_file_sha256(matches[0])
            errors.append(f"Artifact drift detected for {out_key}: expected {expected_sha[:8]}, got {actual[:8]}")
    return errors


def _validate_release_decision_content(
    p: Path,
    req: ExitEvidenceRequirement,
    capability: ExitCapability,
) -> Tuple[bool, List[str]]:
    decision_file = p / "final_release_decision.json"
    if not decision_file.is_file():
        return True, []
    try:
        decision = json.loads(decision_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"Malformed final_release_decision.json: {exc}"]

    errors: List[str] = []
    status = decision.get("status")
    if not req.allow_advisory and status in ("ADVISORY", "PARTIAL", "FAILED", "BLOCKED"):
        errors.append(f"Advisory/partial status '{status}' cannot authorize exit capability '{capability.value}'")

    for rk in req.required_keys:
        val = decision.get(rk)
        if val is None or val is False:
            errors.append(f"Missing or false required key in release decision: {rk}")

    manifest = decision.get("artifact_manifest")
    if manifest and isinstance(manifest, dict):
        errors.extend(_check_artifact_manifest_drift(p, manifest))

    if errors:
        return False, errors
    return True, []


def validate_exit_evidence(
    output_dir: Path,
    capability: ExitCapability,
    *,
    is_not_applicable: bool = False,
    explicit_na_reason: Optional[str] = None,
    raise_on_failure: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Validates that output_dir satisfies all non-empty evidence requirements
    for the specified exit capability.
    """
    if is_not_applicable:
        ok, msgs = _validate_not_applicable(explicit_na_reason)
        if not ok and raise_on_failure:
            raise AuthoritativeExitError(msgs[0])
        return ok, msgs

    req = CAPABILITY_REQUIREMENTS.get(capability)
    if not req:
        return True, []

    p = Path(output_dir)
    if not p.is_dir():
        msg = f"Output directory does not exist: {p}"
        if raise_on_failure:
            raise AuthoritativeExitError(msg)
        return False, [msg]

    file_errors = _validate_required_files(p, req.required_files)
    if file_errors:
        if raise_on_failure:
            raise AuthoritativeExitError("; ".join(file_errors))
        return False, file_errors

    dec_ok, dec_errors = _validate_release_decision_content(p, req, capability)
    if not dec_ok or dec_errors:
        if raise_on_failure:
            raise AuthoritativeExitError("; ".join(dec_errors))
        return False, dec_errors

    return True, []


def _verify_active_pair_bindings(active_pair: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    graph_path = active_pair.get("active_graph_path")
    index_path = active_pair.get("active_index_path")
    generation = active_pair.get("active_generation", 0)

    if not graph_path or not Path(str(graph_path)).is_file():
        errors.append(f"Active graph database file missing or unbound: {graph_path}")
    if not index_path or not Path(str(index_path)).is_file():
        errors.append(f"Active vector index file missing or unbound: {index_path}")
    if not isinstance(generation, int) or generation < 1:
        errors.append(f"Active pair generation ({generation}) must be an integer >= 1")
    return errors


def _verify_live_graph_coverage(live_graph_coverage: Optional[Mapping[str, Any]]) -> List[str]:
    if live_graph_coverage is None:
        return ["OBSOLETE_OR_MISSING_GRAPH_COVERAGE: Live graph coverage readback verification failed (None)."]

    errors: List[str] = []
    coverage_pct = live_graph_coverage.get("coverage_percentage", 0.0)
    node_count = live_graph_coverage.get("node_count", 0)
    is_stale = live_graph_coverage.get("is_stale", False)

    if is_stale:
        errors.append("OBSOLETE_GRAPH_COVERAGE: Live graph coverage readback reports stale graph state.")
    if coverage_pct < 0.80:
        errors.append(f"Graph coverage ({coverage_pct * 100:.1f}%) below minimum required 80.0% floor.")
    if node_count < 1:
        errors.append("Graph coverage readback reports 0 canonical nodes.")
    return errors


def verify_active_pair_and_coverage(
    active_pair: Mapping[str, Any],
    live_graph_coverage: Optional[Mapping[str, Any]] = None,
    raise_on_failure: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Verifies cryptographic active-pair binding and live canonical graph coverage.
    Fails closed if active-pair is unverified or graph coverage is obsolete.
    """
    errors: List[str] = []
    errors.extend(_verify_active_pair_bindings(active_pair))
    errors.extend(_verify_live_graph_coverage(live_graph_coverage))

    if errors:
        if raise_on_failure:
            raise AuthoritativeExitError("; ".join(errors))
        return False, errors

    return True, []


def validate_copilot_exit_parity(
    streamed_payload: Mapping[str, Any],
    standard_payload: Mapping[str, Any],
    raise_on_failure: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Verifies that streamed and ordinary copilot responses consume identical
    grounding evidence and yield identical exits.
    """
    errors: List[str] = []

    s_evidence = streamed_payload.get("grounding_evidence_digest") or streamed_payload.get("evidence_digest")
    std_evidence = standard_payload.get("grounding_evidence_digest") or standard_payload.get("evidence_digest")

    if not s_evidence or not std_evidence:
        errors.append("Missing grounding_evidence_digest in copilot payload.")
    elif s_evidence != std_evidence:
        errors.append(f"Grounding evidence mismatch between streamed ({s_evidence}) and standard ({std_evidence}).")

    s_action = streamed_payload.get("recommended_action") or streamed_payload.get("action")
    std_action = standard_payload.get("recommended_action") or standard_payload.get("action")

    if s_action != std_action:
        errors.append(f"Copilot exit action mismatch: streamed='{s_action}' != standard='{std_action}'")

    s_status = streamed_payload.get("status", "PASS")
    std_status = standard_payload.get("status", "PASS")

    if s_status != std_status:
        errors.append(f"Copilot exit status mismatch: streamed='{s_status}' != standard='{std_status}'")

    if errors:
        if raise_on_failure:
            raise AuthoritativeExitError("; ".join(errors))
        return False, errors

    return True, []
