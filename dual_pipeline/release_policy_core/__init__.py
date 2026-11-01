"""Modular release policy core package."""

from __future__ import annotations

from .digest_seal import (
    _collect_referenced_attachments_text,
    _is_evidence_envelope_package,
    _read_first_glob,
    _read_json_safe,
    _resolve_release_artifacts,
    build_artifact_manifest,
    compute_decision_seal,
    compute_release_evaluation_digest,
    verify_decision_seal,
    verify_release_evaluation_digest,
)
from .package_gate import (
    _check_decision_authorization,
    _check_judge_report,
    _check_manifest_and_seal,
    _check_out3_threshold,
    _is_signed_out3_authorized_exception,
    _verify_judge_digest_match,
    _verify_single_package_gate,
    verify_authoritative_release_gate,
)
from .scorecard_validators import (
    RETRIEVAL_RELEASE_11_GATES,
    _val_num,
    _validate_out1_dims,
    _validate_out2_dims,
    _validate_out3_dims,
    evaluate_document_release_gate,
    evaluate_retrieval_11_gates,
    validate_micro_judge_scorecard,
    validate_scorecard_provenance,
)

__all__ = [
    "RETRIEVAL_RELEASE_11_GATES",
    "_check_decision_authorization",
    "_check_judge_report",
    "_check_manifest_and_seal",
    "_check_out3_threshold",
    "_collect_referenced_attachments_text",
    "_is_evidence_envelope_package",
    "_is_signed_out3_authorized_exception",
    "_read_first_glob",
    "_read_json_safe",
    "_resolve_release_artifacts",
    "_val_num",
    "_validate_out1_dims",
    "_validate_out2_dims",
    "_validate_out3_dims",
    "_verify_judge_digest_match",
    "_verify_single_package_gate",
    "build_artifact_manifest",
    "compute_decision_seal",
    "compute_release_evaluation_digest",
    "evaluate_document_release_gate",
    "evaluate_retrieval_11_gates",
    "validate_micro_judge_scorecard",
    "validate_scorecard_provenance",
    "verify_authoritative_release_gate",
    "verify_decision_seal",
    "verify_release_evaluation_digest",
]
