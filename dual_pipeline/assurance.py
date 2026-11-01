"""Assurance-tier semantics for development, internal, and enterprise runtimes.

The historical ``production_qualified`` boolean remains an enterprise
authorization boundary.  It is deliberately not overloaded to describe a
trusted, team-operated internal runtime.  Internal operational qualification
is represented by a separately sealed receipt bound to one exact active pair,
policy, completed ingestion directive, and retrieval evaluation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .contracts import validate_artifact
from .core import ROOT, read_json

DEVELOPMENT = "development"
INTERNAL_OPERATIONAL = "internal_operational"
ENTERPRISE_PRODUCTION = "enterprise_production"
ASSURANCE_TIERS = frozenset(
    {DEVELOPMENT, INTERNAL_OPERATIONAL, ENTERPRISE_PRODUCTION}
)

DEFAULT_INTERNAL_OPERATIONAL_POLICY = (
    ROOT / "requirements" / "internal-operational-policy.json"
)
INTERNAL_BACKEND_ID = "flagembedding-bge-m3-internal"
INTERNAL_POLICY_CONTRACT = (
    "urn:nee:dual-pipeline:internal-operational-policy:1.0.0"
)
INTERNAL_QUALIFICATION_CONTRACT = (
    "urn:nee:dual-pipeline:internal-operational-qualification-receipt:1.0.0"
)
INTERNAL_REVOCATION_CONTRACT = (
    "urn:nee:dual-pipeline:internal-operational-revocation-receipt:1.0.0"
)


def assurance_status_label(tier: str, operational_status: str | None = None) -> str:
    """Return the operator-facing assurance terminology for a runtime pair.

    ``production_qualified`` remains a persisted enterprise authorization
    control for compatibility and release enforcement.  It is not an
    operator-facing description of a qualified internal runtime.
    """

    if tier == INTERNAL_OPERATIONAL:
        status = (operational_status or "PENDING").upper()
        if status == "QUALIFIED":
            return "internal-operational qualified"
        if status == "REVOKED":
            return "internal-operational revoked"
        if status == "INVALID":
            return "internal-operational evidence invalid"
        return "internal-operational qualification pending"
    if tier == ENTERPRISE_PRODUCTION:
        return "enterprise-production authorization boundary"
    return "development"


def _with_assurance_status(value: dict[str, Any]) -> dict[str, Any]:
    """Attach the canonical human-readable assurance state to an output."""

    return {
        **value,
        "assurance_status": assurance_status_label(
            str(value["assurance_tier"]),
            value.get("operational_status"),
        ),
    }


def backend_assurance_tier(backend: Any) -> str:
    """Return the explicit tier, or infer it for legacy backend objects."""

    explicit = getattr(backend, "assurance_tier", None)
    if explicit in ASSURANCE_TIERS:
        return str(explicit)
    if bool(getattr(backend, "production_qualified", False)):
        return ENTERPRISE_PRODUCTION
    return DEVELOPMENT


def pair_assurance_tier(pair: dict[str, Any]) -> str:
    """Return a pair tier while preserving backward compatibility."""

    explicit = pair.get("assurance_tier")
    if explicit in ASSURANCE_TIERS:
        return str(explicit)
    if bool(pair.get("production_qualified")):
        return ENTERPRISE_PRODUCTION
    if pair.get("embedding_backend") == INTERNAL_BACKEND_ID:
        return INTERNAL_OPERATIONAL
    return DEVELOPMENT


def policy_digest(policy: dict[str, Any]) -> str:
    canonical = policy.get("canonical_digest")
    if not isinstance(canonical, dict) or not isinstance(canonical.get("value"), str):
        raise ValueError("internal operational policy has no canonical digest")
    return canonical["value"]


def load_internal_operational_policy(
    path: Path = DEFAULT_INTERNAL_OPERATIONAL_POLICY,
) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    value = read_json(resolved)
    validate_artifact(value)
    if value.get("contract_id") != INTERNAL_POLICY_CONTRACT:
        raise ValueError("internal operational policy has the wrong contract")
    if value.get("status") != "CONFIGURED":
        raise PermissionError("internal operational policy is not configured")
    if value.get("assurance_tier") != INTERNAL_OPERATIONAL:
        raise ValueError("internal operational policy has the wrong assurance tier")
    if value.get("deployment_scope") != "local_team":
        raise ValueError("internal operational policy must remain local-team scoped")
    controls = value.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("internal operational policy controls are missing")
    if controls.get("autonomous_graph_writes_allowed") is not False:
        raise PermissionError("internal operational policy cannot allow autonomous graph writes")
    if controls.get("manual_hitl_required") is not True:
        raise PermissionError("internal operational policy must require manual HITL")
    if controls.get("ingestion_provider_call_limit") != 0:
        raise PermissionError("internal operational ingestion must remain provider-free")
    if controls.get("query_provider_egress_default") is not False:
        raise PermissionError("internal operational query egress must default closed")
    return value


def validate_internal_backend_policy(
    *,
    policy: dict[str, Any],
    model_id: str,
    model_revision: str | None,
    model_manifest_digest: str | None,
) -> None:
    if INTERNAL_BACKEND_ID not in policy["allowed_backends"]:
        raise PermissionError("internal operational policy does not allow the backend")
    model = policy["model_binding"]
    actual = {
        "model_id": model_id,
        "model_revision": model_revision,
        "model_manifest_digest": model_manifest_digest,
    }
    expected = {
        "model_id": model["model_id"],
        "model_revision": model["model_revision"],
        "model_manifest_digest": model["model_manifest_digest"],
    }
    if actual != expected:
        raise PermissionError(
            f"internal operational model binding differs from policy: "
            f"expected={expected}, actual={actual}"
        )


def pair_binding(pair: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "generation_id",
        "graph_snapshot",
        "graph_sha256",
        "sidecar_sha256",
        "embedding_backend",
        "model_id",
        "model_revision",
        "model_manifest_digest",
        "production_qualified",
    )
    missing = [field for field in fields if field not in pair]
    if missing:
        raise ValueError(f"active pair is missing assurance binding fields: {missing}")
    return {
        **{field: pair[field] for field in fields},
        "assurance_tier": pair_assurance_tier(pair),
        "internal_operational_policy_digest": pair.get(
            "internal_operational_policy_digest"
        ),
    }


def qualification_path(runtime_root: Path, generation_id: str) -> Path:
    return (
        Path(runtime_root).expanduser().resolve()
        / "internal_operational"
        / "qualifications"
        / f"qualification-{generation_id}.json"
    )


def revocations_dir(runtime_root: Path) -> Path:
    return (
        Path(runtime_root).expanduser().resolve()
        / "internal_operational"
        / "revocations"
    )


def _matching_revocations(
    runtime_root: Path,
    qualification: dict[str, Any],
) -> list[dict[str, Any]]:
    directory = revocations_dir(runtime_root)
    if not directory.is_dir():
        return []
    matches: list[dict[str, Any]] = []
    expected_digest = qualification["canonical_digest"]["value"]
    for path in sorted(directory.glob("revocation-*.json")):
        value = read_json(path)
        validate_artifact(value)
        if value.get("contract_id") != INTERNAL_REVOCATION_CONTRACT:
            raise RuntimeError(f"unexpected internal operational revocation contract: {path}")
        if (
            value.get("qualification_id") == qualification["qualification_id"]
            and value.get("qualification_digest") == expected_digest
        ):
            matches.append(value)
    return matches


def internal_operational_status(
    runtime_root: Path,
    pair: dict[str, Any],
    *,
    policy_path: Path = DEFAULT_INTERNAL_OPERATIONAL_POLICY,
) -> dict[str, Any]:
    tier = pair_assurance_tier(pair)
    base = {
        "assurance_tier": tier,
        "deployment_scope": (
            "enterprise" if tier == ENTERPRISE_PRODUCTION else "local_team"
        ),
        "production_qualified": bool(pair.get("production_qualified")),
        "generation_id": pair.get("generation_id"),
    }
    if tier != INTERNAL_OPERATIONAL:
        return _with_assurance_status({
            **base,
            "operational_status": "NOT_APPLICABLE",
            "qualification_id": None,
            "qualification_digest": None,
            "qualification_path": None,
            "revocation_count": 0,
        })
    policy = load_internal_operational_policy(policy_path)
    expected_policy_digest = policy_digest(policy)
    if pair.get("internal_operational_policy_digest") != expected_policy_digest:
        return _with_assurance_status({
            **base,
            "operational_status": "UNQUALIFIED",
            "reason_code": "ACTIVE_PAIR_POLICY_BINDING_MISMATCH",
            "qualification_id": None,
            "qualification_digest": None,
            "qualification_path": None,
            "revocation_count": 0,
        })
    path = qualification_path(runtime_root, str(pair["generation_id"]))
    if not path.is_file():
        return _with_assurance_status({
            **base,
            "operational_status": "UNQUALIFIED",
            "reason_code": "QUALIFICATION_RECEIPT_NOT_FOUND",
            "qualification_id": None,
            "qualification_digest": None,
            "qualification_path": str(path),
            "revocation_count": 0,
        })
    qualification = read_json(path)
    validate_artifact(qualification)
    if qualification.get("contract_id") != INTERNAL_QUALIFICATION_CONTRACT:
        raise RuntimeError("internal operational qualification has the wrong contract")
    if qualification.get("status") != "QUALIFIED":
        raise RuntimeError("internal operational qualification is not QUALIFIED")
    if qualification.get("pair") != pair_binding(pair):
        raise RuntimeError("internal operational qualification does not bind the active pair")
    if qualification["policy_binding"]["policy_digest"] != expected_policy_digest:
        return _with_assurance_status({
            **base,
            "operational_status": "UNQUALIFIED",
            "reason_code": "QUALIFICATION_POLICY_IS_STALE",
            "qualification_id": qualification.get("qualification_id"),
            "qualification_digest": qualification["canonical_digest"]["value"],
            "qualification_path": str(path),
            "revocation_count": 0,
        })
    revocations = _matching_revocations(runtime_root, qualification)
    return _with_assurance_status({
        **base,
        "operational_status": "REVOKED" if revocations else "QUALIFIED",
        "reason_code": "QUALIFICATION_REVOKED" if revocations else None,
        "qualification_id": qualification["qualification_id"],
        "qualification_digest": qualification["canonical_digest"]["value"],
        "qualification_path": str(path),
        "revocation_count": len(revocations),
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "policy_digest": expected_policy_digest,
        "authorization_mode": policy["authorization_mode"],
        "allowed_audiences": list(policy["allowed_audiences"]),
        "allowed_classifications": list(policy["allowed_classifications"]),
        "autonomous_graph_writes_allowed": False,
    })


def require_internal_operational_use(
    runtime_root: Path,
    pair: dict[str, Any],
    *,
    policy_path: Path = DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    classification: str | None = None,
    audiences: Iterable[str] = (),
) -> dict[str, Any]:
    """Fail closed unless the exact active pair has a current qualification."""

    status = internal_operational_status(
        runtime_root,
        pair,
        policy_path=policy_path,
    )
    if pair_assurance_tier(pair) != INTERNAL_OPERATIONAL:
        return status
    if status["operational_status"] != "QUALIFIED":
        raise PermissionError(
            "internal operational use requires a current exact-pair qualification: "
            f"{status.get('reason_code') or status['operational_status']}"
        )
    if classification is not None and classification not in set(
        status["allowed_classifications"]
    ):
        raise PermissionError(
            f"classification is outside internal operational policy: {classification}"
        )
    requested = {value for value in audiences if value}
    allowed = set(status["allowed_audiences"])
    if requested and not requested.issubset(allowed):
        raise PermissionError(
            f"audiences are outside internal operational policy: {sorted(requested - allowed)}"
        )
    return status
