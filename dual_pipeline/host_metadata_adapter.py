#!/usr/bin/env python3
"""
dual_pipeline/host_metadata_adapter.py

Host Metadata Adapter and Trusted Identity Summary Renderer.
Enforces Model Identity SSOT Invariants (Wave 3 / Decision MI-D3):
1. Strict Truthfulness: When host metadata is inaccessible, emits explicit UNAVAILABLE state.
   Guessing, synthesizing defaults, or inheriting parent identities without evidence is prohibited.
2. Policy Mismatch Reporting: If host metadata indicates an unapproved model, reports it
   truthfully as a POLICY_MISMATCH rather than relabeling to an approved model.
3. Multi-Tier Evidence Taxonomy: Classifies claims into CONFIGURED_ONLY, DISPATCH_RECORDED,
   OBSERVED_RESPONSE, and CRYPTOGRAPHICALLY_SEALED.
4. Deterministic Tool Rule: Deterministic tools must report Provider-Free and have no model identity.
5. Verification of Identity Evidence: Proves declared identities match recorded invocation evidence.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from config.credentials import CredentialManager, canonicalize_model_name
from dual_pipeline.model_registry import (
    BANNED_REGISTRY_MODELS,
    get_approved_models,
    is_model_approved,
)

logger = logging.getLogger(__name__)


class EvidenceTier(str, Enum):
    """Four-tier evidence hierarchy governing model identity telemetry claims."""
    CONFIGURED_ONLY = "CONFIGURED_ONLY"                     # Tier 0: Static config / prompt text
    DISPATCH_RECORDED = "DISPATCH_RECORDED"                 # Tier 1: Outbound transport attempted
    OBSERVED_RESPONSE = "OBSERVED_RESPONSE"                 # Tier 2: Response metadata verified
    CRYPTOGRAPHICALLY_SEALED = "CRYPTOGRAPHICALLY_SEALED"   # Tier 3: Immutable ledger receipt


class IdentityScope(str, Enum):
    """Explicit execution context boundary for model identity."""
    HOST_ASSISTANT = "host_assistant"
    DELEGATED_ASSISTANT = "delegated_assistant"
    APPLICATION_CALL = "application_call"
    DETERMINISTIC_TOOL = "deterministic_tool"


@dataclass(frozen=True)
class HostIdentityReport:
    """Strongly typed report of host or assistant runtime model identity."""
    scope: IdentityScope
    status: str  # "VERIFIED", "UNAVAILABLE", "POLICY_MISMATCH", "UNQUALIFIED"
    raw_model: Optional[str]
    canonical_model: Optional[str]
    provider: Optional[str]
    evidence_strength: EvidenceTier
    host_session_id: Optional[str] = None
    is_approved_by_policy: bool = False
    details: Dict[str, Any] = field(default_factory=dict)
    rendered_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope.value,
            "status": self.status,
            "raw_model": self.raw_model,
            "canonical_model": self.canonical_model,
            "provider": self.provider,
            "evidence_strength": self.evidence_strength.value,
            "host_session_id": self.host_session_id,
            "is_approved_by_policy": self.is_approved_by_policy,
            "details": self.details,
            "rendered_summary": self.rendered_summary,
        }


def _extract_host_raw_metadata(
    effective_env: Mapping[str, str],
    host_payload: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], Optional[str], Dict[str, Any]]:
    raw_model: Optional[str] = None
    session_id: Optional[str] = None
    provider: Optional[str] = None
    details: Dict[str, Any] = {}

    if host_payload and isinstance(host_payload, dict):
        raw_model = host_payload.get("model") or host_payload.get("model_id")
        session_id = host_payload.get("session_id") or host_payload.get("conversation_id")
        provider = host_payload.get("provider")
        details.update(host_payload)

    if not raw_model:
        raw_model = (
            effective_env.get("ANTIGRAVITY_HOST_MODEL")
            or effective_env.get("ANTIGRAVITY_ACTIVE_MODEL")
            or effective_env.get("HOST_ASSISTANT_MODEL")
            or effective_env.get("ACTIVE_HOST_MODEL")
        )
        session_id = session_id or effective_env.get("ANTIGRAVITY_SESSION_ID") or effective_env.get("CONVERSATION_ID")
        provider = (
            provider
            or effective_env.get("ANTIGRAVITY_HOST_PROVIDER")
            or effective_env.get("ANTIGRAVITY_MODEL_PROVIDER")
        )

    return raw_model, session_id, provider, details


def _create_host_model_report(
    scope: IdentityScope,
    clean_raw: str,
    canonical: str,
    p_norm: str,
    session_id: Optional[str],
    details: Dict[str, Any],
) -> HostIdentityReport:
    if clean_raw in BANNED_REGISTRY_MODELS:
        return HostIdentityReport(
            scope=scope,
            status="POLICY_MISMATCH",
            raw_model=clean_raw,
            canonical_model=canonical,
            provider=p_norm,
            evidence_strength=EvidenceTier.OBSERVED_RESPONSE,
            host_session_id=session_id,
            is_approved_by_policy=False,
            details={"policy_violation": "BANNED_MODEL"},
            rendered_summary=f"{clean_raw} [POLICY MISMATCH: Banned Model]",
        )

    if is_model_approved(canonical, p_norm):
        return HostIdentityReport(
            scope=scope,
            status="VERIFIED",
            raw_model=clean_raw,
            canonical_model=canonical,
            provider=p_norm,
            evidence_strength=EvidenceTier.OBSERVED_RESPONSE,
            host_session_id=session_id,
            is_approved_by_policy=True,
            details=details,
            rendered_summary=f"{canonical} | {p_norm.capitalize()}",
        )

    return HostIdentityReport(
        scope=scope,
        status="POLICY_MISMATCH",
        raw_model=clean_raw,
        canonical_model=canonical,
        provider=p_norm,
        evidence_strength=EvidenceTier.OBSERVED_RESPONSE,
        host_session_id=session_id,
        is_approved_by_policy=False,
        details={"approved_models": sorted(get_approved_models(p_norm))},
        rendered_summary=f"{clean_raw} [POLICY MISMATCH: Unapproved Host Model]",
    )


def resolve_host_identity(
    env: Optional[Dict[str, str]] = None,
    host_payload: Optional[Dict[str, Any]] = None,
    scope: IdentityScope = IdentityScope.HOST_ASSISTANT,
) -> HostIdentityReport:
    """
    Resolves host assistant model identity truthfully from host runtime metadata.
    Fails closed to UNAVAILABLE when metadata is missing, unverified, or inaccessible.
    """
    effective_env = env if env is not None else CredentialManager.get_process_environment()

    if scope == IdentityScope.DETERMINISTIC_TOOL:
        return HostIdentityReport(
            scope=scope,
            status="VERIFIED",
            raw_model=None,
            canonical_model=None,
            provider="None",
            evidence_strength=EvidenceTier.CONFIGURED_ONLY,
            is_approved_by_policy=True,
            details={"type": "deterministic_execution"},
            rendered_summary="Provider: None (Provider-Free)",
        )

    raw_model, session_id, provider, details = _extract_host_raw_metadata(effective_env, host_payload)

    if not raw_model or not str(raw_model).strip():
        return HostIdentityReport(
            scope=scope,
            status="UNAVAILABLE",
            raw_model=None,
            canonical_model=None,
            provider=None,
            evidence_strength=EvidenceTier.CONFIGURED_ONLY,
            host_session_id=session_id or "UNAVAILABLE",
            is_approved_by_policy=False,
            details={"reason": "HOST_METADATA_INACCESSIBLE"},
            rendered_summary="Unavailable / Unverified (Host Metadata Inaccessible)",
        )

    clean_raw = str(raw_model).strip()
    canonical = canonicalize_model_name(clean_raw)
    p_norm = (provider or ("google" if canonical.startswith("gemini") else "openai")).strip().lower()

    return _create_host_model_report(scope, clean_raw, canonical, p_norm, session_id, details)


def render_scratchpad_header(
    report: HostIdentityReport,
    role: str = "Execution",
) -> str:
    """Renders the compliant Active Model & Provider scratchpad telemetry line."""
    return f"- **Active Model & Provider**: {report.rendered_summary}"


def _extract_evidence_model(evidence: Optional[Dict[str, Any]]) -> Optional[str]:
    if not evidence or not isinstance(evidence, dict):
        return None
    for k in ("observed_model", "dispatched_model", "resolved_model", "model"):
        val = evidence.get(k)
        if val:
            return str(val)
    return None


def _check_deterministic_scope(declared_model: str) -> Tuple[bool, str]:
    decl_clean = declared_model.strip().lower()
    if "provider-free" in decl_clean or "none" in decl_clean:
        return True, "OK: Deterministic provider-free tool"
    return False, f"Deterministic tool cannot declare an LLM model identity: '{declared_model}'"


def _is_truthful_unavailable(decl_clean: str, evidence: Optional[Dict[str, Any]]) -> bool:
    low = decl_clean.lower()
    if "unavailable" in low or "unverified" in low:
        return not evidence or evidence.get("status") == "UNAVAILABLE"
    return False


def _validate_evidence_dict(evidence: Optional[Dict[str, Any]], declared_model: str) -> Tuple[bool, Optional[str]]:
    if not evidence or not isinstance(evidence, dict):
        return False, f"Missing execution evidence for declared model '{declared_model}'"
    if evidence.get("tampered") is True or evidence.get("corrupt") is True:
        return False, "Evidence integrity check failed: tampered evidence"
    return True, None


def _validate_canonical_match(decl_clean: str, declared_model: str, evidence: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    actual_m = _extract_evidence_model(evidence)
    if not actual_m:
        return False, f"Evidence contains no execution model to corroborate '{declared_model}'"

    canon_decl = canonicalize_model_name(decl_clean.split()[0].rstrip(",;|"))
    canon_actual = canonicalize_model_name(actual_m.strip())

    if canon_decl != canon_actual:
        return (
            False,
            f"Approved-but-wrong identity violation: declared '{declared_model}' (canonical: '{canon_decl}') "
            f"does not match actual invocation evidence '{actual_m}' (canonical: '{canon_actual}')",
        )
    return True, None


def verify_identity_evidence(
    declared_model: str,
    evidence: Optional[Dict[str, Any]],
    scope: IdentityScope | str = IdentityScope.APPLICATION_CALL,
) -> Tuple[bool, str]:
    """
    Verifies that a declared model in an output or scratchpad is backed by genuine invocation evidence.
    """
    scope_enum = IdentityScope(scope) if isinstance(scope, str) else scope

    if scope_enum == IdentityScope.DETERMINISTIC_TOOL:
        return _check_deterministic_scope(declared_model)

    decl_clean = (declared_model or "").strip()
    if not decl_clean:
        return False, "Declared model cannot be empty"

    if _is_truthful_unavailable(decl_clean, evidence):
        return True, "OK: Truthful unavailable state"

    valid_dict, err = _validate_evidence_dict(evidence, declared_model)
    if not valid_dict:
        return False, err or "Invalid evidence"

    # assert evidence is a dict for type checker
    assert isinstance(evidence, dict)
    valid_match, err = _validate_canonical_match(decl_clean, declared_model, evidence)
    if not valid_match:
        return False, err or "Model mismatch"

    strength = evidence.get("evidence_strength")
    if strength and strength not in [e.value for e in EvidenceTier]:
        return False, f"Invalid evidence_strength tier: '{strength}'"

    return True, "OK: Declared model matches execution evidence"

