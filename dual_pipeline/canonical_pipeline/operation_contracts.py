"""
dual_pipeline/canonical_pipeline/operation_contracts.py

Versioned Ingestion Operation Contract & Modality Boundary SSOT.
Enforces:
1. Positive mode binding across physical format, semantic modality, and operation kind.
2. Lifecycle state transitions: CAPTURING -> ASR_READY -> STOP_REQUESTED -> CAPTURE_STOPPED
   -> SEALED_PREVIEW -> ATTRIBUTION_PENDING -> PACKAGE_READY -> PARTIAL -> PUBLISHED_UNVERIFIED -> COMPLETE.
3. Modality deliverable contracts (Transcripts: 01,02,03; Notes: 02,03; Document: 03).
4. Strict dependency invalidation matrix and conflicting flag detection.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality


class OperationKind(str, Enum):
    LIVE_RECORDING = "LIVE_RECORDING"
    FINALIZED_TRANSCRIPT = "FINALIZED_TRANSCRIPT"
    MEETING_NOTES = "MEETING_NOTES"
    WRITTEN_DOCUMENT = "WRITTEN_DOCUMENT"
    SELECTIVE_MEETING_REGENERATION = "SELECTIVE_MEETING_REGENERATION"
    MIXED_SOURCE_SET = "MIXED_SOURCE_SET"
    LIVE_COPILOT_QUERY = "LIVE_COPILOT_QUERY"


class LifecycleState(str, Enum):
    CAPTURING = "CAPTURING"
    ASR_READY = "ASR_READY"
    STOP_REQUESTED = "STOP_REQUESTED"
    CAPTURE_STOPPED = "CAPTURE_STOPPED"
    SEALED_PREVIEW = "SEALED_PREVIEW"
    ATTRIBUTION_PENDING = "ATTRIBUTION_PENDING"
    PACKAGE_READY = "PACKAGE_READY"
    PARTIAL = "PARTIAL"
    PUBLISHED_UNVERIFIED = "PUBLISHED_UNVERIFIED"
    COMPLETE = "COMPLETE"


class EvidencePhase(str, Enum):
    RAW_CAPTURE = "RAW_CAPTURE"
    FORENSIC_TRANSCRIPT = "FORENSIC_TRANSCRIPT"
    SUMMARY_RECAP = "SUMMARY_RECAP"
    GRAPH_PROJECTION = "GRAPH_PROJECTION"


CONTRACT_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "contracts"
    / "dual_pipeline"
    / "ingestion_operation_contract_v2.json"
)

# Immutable Deliverables SSOT
MODALITY_REQUIRED_DELIVERABLES: Dict[CanonicalModality, List[str]] = {
    CanonicalModality.AUDIO: ["01", "02", "03"],
    CanonicalModality.MEETING_TRANSCRIPT: ["01", "02", "03"],
    CanonicalModality.MEETING_NOTES: ["02", "03"],
    CanonicalModality.DOCUMENT: ["03"],
}

MODALITY_NOT_APPLICABLE_DELIVERABLES: Dict[CanonicalModality, List[str]] = {
    CanonicalModality.AUDIO: [],
    CanonicalModality.MEETING_TRANSCRIPT: [],
    CanonicalModality.MEETING_NOTES: ["01"],
    CanonicalModality.DOCUMENT: ["01", "02"],
}


def resolve_canonical_modality(modality: Union[CanonicalModality, str]) -> CanonicalModality:
    """Coerces string or enum into CanonicalModality with fail-closed validation."""
    if isinstance(modality, CanonicalModality):
        return modality
    val = str(modality).strip().upper()
    if val in ("LIVE", "LIVE_RECORDING"):
        return CanonicalModality.AUDIO
    if val in ("NOTES", "COPILOT_NOTES"):
        return CanonicalModality.MEETING_NOTES
    try:
        return CanonicalModality[val]
    except KeyError:
        raise ValueError(f"Unknown or invalid modality: {modality}")


def resolve_required_deliverables(
    modality: Union[CanonicalModality, str],
    operation_kind: Optional[Union[OperationKind, str]] = None,
    selective_outputs: Optional[List[str]] = None,
) -> List[str]:
    """Returns the immutable required deliverables for a given modality and operation kind."""
    c_mod = resolve_canonical_modality(modality)
    base_required = list(MODALITY_REQUIRED_DELIVERABLES.get(c_mod, ["03"]))

    if selective_outputs:
        cleaned = [str(o).strip().zfill(2) if str(o).strip().isdigit() else str(o).strip() for o in selective_outputs]
        for sel in cleaned:
            if sel not in base_required:
                raise ValueError(
                    f"Deliverable '{sel}' is not valid for modality {c_mod.name}. "
                    f"Valid set: {base_required}"
                )
        return cleaned

    return base_required


def get_not_applicable_deliverables(modality: Union[CanonicalModality, str]) -> List[str]:
    """Returns deliverables explicitly declared NOT_APPLICABLE for this modality."""
    c_mod = resolve_canonical_modality(modality)
    return list(MODALITY_NOT_APPLICABLE_DELIVERABLES.get(c_mod, []))


def check_conflicting_flags(
    modality: Union[CanonicalModality, str],
    flags: Dict[str, Any],
) -> List[str]:
    """Detects invalid or conflicting flag combinations that violate modality invariants."""
    c_mod = resolve_canonical_modality(modality)
    conflicts: List[str] = []

    if flags.get("cloud_transcription") or flags.get("cloud_asr"):
        conflicts.append("Cloud transcription/ASR is strictly banned across all modalities.")

    if flags.get("output3_only"):
        if c_mod in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
            # Selective meeting output3 is permitted, but must NOT coerce modality to DOCUMENT
            if flags.get("modality") == "DOCUMENT" or flags.get("is_document"):
                conflicts.append("output3_only cannot coerce a meeting or audio source to DOCUMENT modality.")

    if flags.get("skip_transcript_checks") and c_mod in (
        CanonicalModality.AUDIO,
        CanonicalModality.MEETING_TRANSCRIPT,
    ):
        conflicts.append(f"skip_transcript_checks cannot be applied to {c_mod.name} modality.")

    if flags.get("fast_pilot_budget") and c_mod != CanonicalModality.DOCUMENT:
        conflicts.append("fast_pilot_budget is strictly reserved for DOCUMENT synthesis and cannot cap meetings/live.")

    return conflicts


def validate_operation_invariants(
    modality: Union[CanonicalModality, str],
    flags: Dict[str, Any],
    requested_outputs: Optional[List[str]] = None,
) -> Tuple[bool, List[str]]:
    """Validates full operation invariants before provider invocation or stage execution."""
    conflicts = check_conflicting_flags(modality, flags)
    c_mod = resolve_canonical_modality(modality)

    if requested_outputs:
        base_valid = set(MODALITY_REQUIRED_DELIVERABLES.get(c_mod, []))
        for req in requested_outputs:
            norm = str(req).strip().zfill(2) if str(req).strip().isdigit() else str(req).strip()
            if norm not in base_valid:
                conflicts.append(f"Requested output {req} is outside declared deliverables for {c_mod.name}.")

    return len(conflicts) == 0, conflicts


def get_invalidation_matrix() -> List[Dict[str, Any]]:
    """Returns the versioned dependency invalidation matrix."""
    if CONTRACT_MANIFEST_PATH.is_file():
        try:
            data = json.loads(CONTRACT_MANIFEST_PATH.read_text(encoding="utf-8"))
            return data.get("dependency_invalidation_matrix", [])
        except Exception:
            pass
    return [
        {
            "changed_input_or_stage": "spoken_source_bytes_or_speaker_map",
            "must_invalidate_revalidate": ["01", "02", "03", "publication_coverage"],
            "must_preserve": ["raw_capture", "historical_receipts"],
        },
        {
            "changed_input_or_stage": "frozen_accepted_01",
            "must_invalidate_revalidate": ["02", "03", "cross_artifact_release_proof"],
            "must_preserve": ["original_source", "source_integrity_evidence"],
        },
        {
            "changed_input_or_stage": "recap_02_only",
            "must_invalidate_revalidate": ["02", "cross_artifact_release_checks"],
            "must_preserve": ["01", "03_when_independent_of_02"],
        },
        {
            "changed_input_or_stage": "graph_03_only",
            "must_invalidate_revalidate": ["03", "qa_judge_publication_readback"],
            "must_preserve": ["01", "02"],
        },
        {
            "changed_input_or_stage": "document_source_or_formula_values",
            "must_invalidate_revalidate": ["document_03", "downstream_evidence"],
            "must_preserve": ["independent_source_set_members"],
        },
        {
            "changed_input_or_stage": "notes_source",
            "must_invalidate_revalidate": ["02", "03", "notes_release_proof"],
            "must_preserve": ["no_manufactured_01_dependency"],
        },
        {
            "changed_input_or_stage": "attachment_classification_or_audience",
            "must_invalidate_revalidate": ["affected_derived_evidence", "authorization", "release_proof"],
            "must_preserve": ["original_capture", "unaffected_validated_stages"],
        },
        {
            "changed_input_or_stage": "judge_only_policy",
            "must_invalidate_revalidate": ["judge_quality_release_proof"],
            "must_preserve": ["independently_bound_extraction_candidate"],
        },
        {
            "changed_input_or_stage": "modality_phase_tenant_model_prompt_schema",
            "must_invalidate_revalidate": ["all_affected_stage_bindings"],
            "must_preserve": ["unrelated_operations", "original_immutable_evidence"],
        },
    ]


def get_invalidation_scope(changed_stage: str) -> Dict[str, List[str]]:
    """Resolves invalidation and preservation requirements for a changed stage or input."""
    matrix = get_invalidation_matrix()
    for row in matrix:
        if row.get("changed_input_or_stage") == changed_stage:
            return {
                "must_invalidate_revalidate": list(row.get("must_invalidate_revalidate", [])),
                "must_preserve": list(row.get("must_preserve", [])),
            }
    return {
        "must_invalidate_revalidate": ["all_affected_stage_bindings"],
        "must_preserve": ["unrelated_operations"],
    }


class AuthorityScope(str, Enum):
    CAPTURE_LOCAL_PROCESSING = "CAPTURE_LOCAL_PROCESSING"
    PERMITTED_EGRESS = "PERMITTED_EGRESS"
    SOURCE_ADMISSION = "SOURCE_ADMISSION"
    INGESTION_INTENT = "INGESTION_INTENT"
    SPEAKER_RESOLUTION = "SPEAKER_RESOLUTION"
    EXACT_DELTA_PUBLICATION = "EXACT_DELTA_PUBLICATION"


def validate_authority_scope(
    granted_scope: Union[AuthorityScope, str],
    required_scope: Union[AuthorityScope, str],
) -> Tuple[bool, str]:
    """Ensures granted authentic authority matches the required execution scope without cross-scope leakage."""
    g = str(granted_scope).strip().upper()
    r = str(required_scope).strip().upper()
    if g != r:
        return False, f"AUTHORIZATION_SCOPE_MISMATCH: Granted scope '{g}' cannot authorize required scope '{r}'."
    return True, "AUTHORIZED"


def verify_independent_authority_evidence(
    evidence_records: Dict[Any, Any],
    required_scopes: Set[Any],
) -> Tuple[bool, List[str]]:
    """Ensures each required authority scope is backed by distinct, authentic evidence."""
    errors: List[str] = []
    norm_records = {str(k).strip().upper(): v for k, v in evidence_records.items()}
    seen_proofs: Set[str] = set()

    for req in required_scopes:
        norm_req = str(req).strip().upper()
        if norm_req not in norm_records:
            errors.append(f"Missing required authority evidence for scope: {norm_req}")
            continue
        proof = norm_records[norm_req]
        if not proof:
            errors.append(f"Empty or null authority evidence for scope: {norm_req}")
            continue

        proof_key = json.dumps(proof, sort_keys=True) if isinstance(proof, dict) else str(proof)
        if proof_key in seen_proofs:
            errors.append(f"Evidence for scope '{norm_req}' is duplicated from another scope (scopes must be independently evidenced)")
        else:
            seen_proofs.add(proof_key)

    return len(errors) == 0, errors


def validate_cloud_asr_prohibition(
    backend: Optional[str] = None,
    flags: Optional[Dict[str, Any]] = None,
    has_ingestion_approval: bool = False,
) -> None:
    """Ingestion approval never authorizes cloud ASR or cloud fallback for audio capture."""
    flags = flags or {}
    b = (backend or flags.get("backend") or "").strip().lower()
    if b and b not in ("auto", "mlx", "mock"):
        raise ValueError(
            f"Transcription backend '{backend}' is prohibited. "
            "Only local offline MLX Metal GPU transcription is permitted for live meeting audio. "
            "Ingestion permission never authorizes cloud ASR."
        )
    if flags.get("cloud_asr") or flags.get("cloud_transcription"):
        raise ValueError(
            "Cloud transcription/ASR is strictly banned. Ingestion permission never authorizes cloud ASR."
        )


def validate_completion_readiness(
    modality: Union[CanonicalModality, str],
    written_artifacts: Dict[str, Any],
    is_published: bool = False,
    is_readback_verified: bool = False,
    current_state: Optional[Union[LifecycleState, str]] = None,
) -> Tuple[bool, str, LifecycleState]:
    """Validates terminal readiness against modality-specific deliverable set and verified publication readback."""
    c_mod = resolve_canonical_modality(modality)
    curr = LifecycleState(current_state) if current_state else LifecycleState.PACKAGE_READY

    # 1. State separation: recording stopped or preview is not package ready or complete
    if curr in (
        LifecycleState.CAPTURING,
        LifecycleState.ASR_READY,
        LifecycleState.STOP_REQUESTED,
        LifecycleState.CAPTURE_STOPPED,
        LifecycleState.SEALED_PREVIEW,
    ):
        return False, f"State '{curr.value}' cannot claim completion; package has not been finalized", curr

    # 2. Modality deliverables check
    required = MODALITY_REQUIRED_DELIVERABLES.get(c_mod, ["03"])
    missing: List[str] = []
    art_str = " ".join(str(k) for k in written_artifacts.keys())
    for req in required:
        if (
            req not in written_artifacts
            and f"0{req}" not in written_artifacts
            and f"output_{req}" not in written_artifacts
            and f"output{req}" not in art_str
            and f"0{req}_" not in art_str
            and f"{req}_" not in art_str
        ):
            missing.append(req)

    if missing:
        return False, f"Modality {c_mod.name} is missing required outputs: {missing}", LifecycleState.PARTIAL

    # 3. Publication & readback verification check
    if is_published:
        if not is_readback_verified:
            return False, "Publication active but independent coverage readback unverified", LifecycleState.PUBLISHED_UNVERIFIED
        return True, "Operation fully complete and verified", LifecycleState.COMPLETE

    return True, "Package ready for publication approval", LifecycleState.PACKAGE_READY


def validate_preparation_route(
    is_prepare_only: bool,
    graph_path: Optional[Path] = None,
    active_pair_file: Optional[Path] = None,
    initial_graph_bytes: Optional[bytes] = None,
) -> Tuple[bool, str]:
    """Ensures preparation-only executions make zero writes to canonical graph and do not claim completion."""
    if is_prepare_only:
        if graph_path and graph_path.is_file() and initial_graph_bytes is not None:
            current_bytes = graph_path.read_bytes()
            if current_bytes != initial_graph_bytes:
                return False, "PREPARE_ONLY_VIOLATION: Production graph was mutated during prepare-only execution"
        return True, "Preparation route respected: zero production graph mutation"
    return True, "Standard route"


def validate_proposal_bindings(
    proposal: Any,
) -> Tuple[bool, List[str]]:
    """Validates that a proposal contains non-empty source, output, and base generation bindings."""
    errors: List[str] = []
    source_hash = getattr(proposal, "source_hash", None) or (proposal.get("source_hash") if isinstance(proposal, dict) else None)
    output_hashes = getattr(proposal, "output_hashes", None) or (proposal.get("output_hashes") if isinstance(proposal, dict) else None)
    expected_base = getattr(proposal, "expected_base_generation", None) or (proposal.get("expected_base_generation") if isinstance(proposal, dict) else None)

    if not source_hash or not str(source_hash).strip():
        errors.append("source_hash must be non-empty")
    if not output_hashes:
        errors.append("output_hashes must be non-empty")
    if not expected_base or str(expected_base).strip() in ("", "None"):
        errors.append("expected_base_generation must be non-empty")

    return len(errors) == 0, errors


