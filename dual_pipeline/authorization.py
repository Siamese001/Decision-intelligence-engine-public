"""Fail-closed enterprise-production authorization and resource binding.

The runtime accepts an offline RS256 JWT produced by the deployment identity
boundary.  It never accepts an unsigned claim set or retrieves keys over the
network.  Only a digest and the minimum non-sensitive authorization facts are
persisted in run evidence.
"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .core import ROOT, digest, read_json, sha256_bytes
from .parameters import get_authorization_parameters

CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
_AUTH_PARAMS = get_authorization_parameters()
MAX_TOKEN_LIFETIME_SECONDS = int(_AUTH_PARAMS["max_token_lifetime_seconds"])
CLOCK_SKEW_SECONDS = int(_AUTH_PARAMS["clock_skew_seconds"])
PRODUCTION_TRUST_POLICY = ROOT / "requirements" / "production-trust-policy.json"


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValueError("authorization token contains invalid base64url data") from exc


def _string_list(claims: dict[str, Any], name: str) -> tuple[str, ...]:
    value = claims.get(name)
    if not isinstance(value, list) or not value:
        raise ValueError(f"authorization token requires nonempty {name}")
    result = tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    if len(result) != len(value):
        raise ValueError(f"authorization token {name} must contain unique nonempty strings")
    return result


def _timestamp(claims: dict[str, Any], name: str) -> int:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"authorization token requires numeric {name}")
    return int(value)


@dataclass(frozen=True)
class AuthorizationContext:
    subject: str
    issuer: str
    relying_party: str
    token_id: str
    issued_at: int
    expires_at: int
    classification_clearance: str
    audiences: tuple[str, ...]
    capabilities: tuple[str, ...]
    authorized_resources: dict[str, str]
    approved_model_manifest_digest: str | None
    token_digest: str
    context_digest: str

    def assert_fresh(self, *, now: int | None = None) -> None:
        current = int(time.time()) if now is None else int(now)
        if self.issued_at > current + CLOCK_SKEW_SECONDS:
            raise PermissionError("authorization context is not yet valid")
        if self.expires_at <= current - CLOCK_SKEW_SECONDS:
            raise PermissionError("authorization context is expired")

    def require(self, *capabilities: str) -> None:
        self.assert_fresh()
        missing = sorted(set(capabilities) - set(self.capabilities))
        if missing:
            raise PermissionError(f"authorization context lacks capabilities: {missing}")

    def require_resources(self, resources: dict[str, str]) -> None:
        self.assert_fresh()
        mismatches = {
            name: {"authorized": self.authorized_resources.get(name), "actual": value}
            for name, value in resources.items()
            if self.authorized_resources.get(name) != value
        }
        if mismatches:
            raise PermissionError(
                f"authorization context is not bound to the requested resources: {sorted(mismatches)}"
            )

    def audit_record(self) -> dict[str, Any]:
        return {
            "authentication_method": "offline-rs256-jwt",
            "subject": self.subject,
            "issuer": self.issuer,
            "relying_party": self.relying_party,
            "token_id": self.token_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "classification_clearance": self.classification_clearance,
            "audiences": list(self.audiences),
            "capabilities": list(self.capabilities),
            "authorized_resource_names": sorted(self.authorized_resources),
            "approved_model_manifest_digest": self.approved_model_manifest_digest,
            "token_digest": self.token_digest,
            "context_digest": self.context_digest,
        }


def _parse_and_verify_jwt(
    token: str,
    public_key_path: Path,
    _verified_public_key_bytes: bytes | None,
) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise ValueError("authorization token must be a compact three-part JWT")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("authorization token header or payload is not valid JSON") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise ValueError("authorization token header and payload must be JSON objects")
    if header.get("alg") != "RS256" or header.get("typ") not in {None, "JWT"}:
        raise ValueError("authorization token must use RS256 and JWT type")
    if header.get("crit"):
        raise ValueError("authorization token uses unsupported critical headers")
    public_key_bytes = (
        public_key_path.resolve().read_bytes()
        if _verified_public_key_bytes is None
        else _verified_public_key_bytes
    )
    key = serialization.load_pem_public_key(public_key_bytes)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise ValueError("authorization key must be an RSA public key of at least 2048 bits")
    try:
        key.verify(
            _b64url_decode(parts[2]),
            f"{parts[0]}.{parts[1]}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise PermissionError("authorization token signature is invalid") from exc
    return claims


def _validate_jwt_timestamps_and_party(
    claims: dict[str, Any],
    *,
    current: int,
    expected_issuer: str,
    expected_audience: str,
) -> tuple[int, int, str]:
    issued_at = _timestamp(claims, "iat")
    not_before = _timestamp(claims, "nbf")
    expires_at = _timestamp(claims, "exp")
    if issued_at > current + CLOCK_SKEW_SECONDS or not_before > current + CLOCK_SKEW_SECONDS:
        raise PermissionError("authorization token is not yet valid")
    if expires_at <= current - CLOCK_SKEW_SECONDS:
        raise PermissionError("authorization token is expired")
    if expires_at <= issued_at or expires_at - issued_at > MAX_TOKEN_LIFETIME_SECONDS:
        raise PermissionError("authorization token lifetime exceeds the one-hour maximum")
    issuer = str(claims.get("iss") or "")
    if issuer != expected_issuer:
        raise PermissionError("authorization token issuer mismatch")
    raw_audience = claims.get("aud")
    token_audiences = [raw_audience] if isinstance(raw_audience, str) else raw_audience
    if not isinstance(token_audiences, list) or expected_audience not in token_audiences:
        raise PermissionError("authorization token audience mismatch")
    return issued_at, expires_at, issuer


def _extract_nee_bounded_claims(
    claims: dict[str, Any],
) -> tuple[str, str, str, tuple[str, ...], tuple[str, ...], dict[str, str], str | None]:
    subject = str(claims.get("sub") or "").strip()
    token_id = str(claims.get("jti") or "").strip()
    if not subject or not token_id:
        raise ValueError("authorization token requires nonempty sub and jti")
    clearance = str(claims.get("nee_classification_clearance") or "")
    if clearance not in CLASSIFICATIONS:
        raise ValueError("authorization token has invalid nee_classification_clearance")
    audiences = _string_list(claims, "nee_audiences")
    capabilities = _string_list(claims, "nee_capabilities")
    resources = claims.get("nee_authorized_resources", {})
    if not isinstance(resources, dict) or any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(value, str)
        or not re.fullmatch(r"[0-9a-f]{64}", value)
        for name, value in resources.items()
    ):
        raise ValueError("nee_authorized_resources must map names to SHA-256 digests")
    approved_manifest = claims.get("nee_model_manifest_digest")
    if approved_manifest is not None and (
        not isinstance(approved_manifest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", approved_manifest)
    ):
        raise ValueError("nee_model_manifest_digest must be a SHA-256 digest")
    return subject, token_id, clearance, audiences, capabilities, resources, approved_manifest


def load_authorization_context(
    token_path: Path,
    public_key_path: Path,
    *,
    expected_issuer: str,
    expected_audience: str,
    required_capabilities: Iterable[str] = (),
    now: int | None = None,
    _verified_public_key_bytes: bytes | None = None,
) -> AuthorizationContext:
    """Verify a short-lived RS256 token and return its bounded claims."""

    token = token_path.resolve().read_text(encoding="utf-8").strip()
    claims = _parse_and_verify_jwt(token, public_key_path, _verified_public_key_bytes)
    current = int(time.time()) if now is None else int(now)
    issued_at, expires_at, issuer = _validate_jwt_timestamps_and_party(
        claims,
        current=current,
        expected_issuer=expected_issuer,
        expected_audience=expected_audience,
    )
    subject, token_id, clearance, audiences, capabilities, resources, approved_manifest = (
        _extract_nee_bounded_claims(claims)
    )
    token_digest = sha256_bytes(token.encode("utf-8"))
    bounded = {
        "subject": subject,
        "issuer": issuer,
        "relying_party": expected_audience,
        "token_id": token_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "classification_clearance": clearance,
        "audiences": audiences,
        "capabilities": capabilities,
        "authorized_resources": dict(sorted(resources.items())),
        "approved_model_manifest_digest": approved_manifest,
        "token_digest": token_digest,
    }
    context = AuthorizationContext(**bounded, context_digest=digest(bounded))
    context.require(*required_capabilities)
    return context


def load_production_authorization_context(
    token_path: Path,
    public_key_path: Path,
    *,
    supplied_issuer: str | None,
    supplied_audience: str | None,
    required_capabilities: Iterable[str] = (),
    trust_policy_path: Path | None = None,
    now: int | None = None,
) -> AuthorizationContext:
    """Load enterprise-production authorization through the repository-pinned trust anchor.

    The public key, issuer, and audience are not trusted merely because a caller
    supplied them on the command line.  They must exactly match the reviewed
    deployment trust policy.  Tests may point this helper at an isolated policy;
    production entry points intentionally expose no such override.
    """

    policy_path = (
        PRODUCTION_TRUST_POLICY if trust_policy_path is None else trust_policy_path
    ).resolve()
    policy = read_json(policy_path)
    expected_keys = {
        "schema_version",
        "status",
        "issuer",
        "audience",
        "public_key_sha256",
        "owner",
    }
    if not isinstance(policy, dict) or set(policy) != expected_keys:
        raise ValueError("production trust policy is malformed")
    if policy.get("schema_version") != "1.0.0":
        raise ValueError("production trust policy schema is unsupported")
    if policy.get("status") != "CONFIGURED":
        raise PermissionError(
            "production trust policy is unconfigured; the deployment identity/security owner must pin it"
        )
    issuer = policy.get("issuer")
    audience = policy.get("audience")
    key_digest = policy.get("public_key_sha256")
    if (
        not isinstance(issuer, str)
        or not issuer.strip()
        or not isinstance(audience, str)
        or not audience.strip()
        or not isinstance(key_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", key_digest)
    ):
        raise ValueError("configured production trust policy is incomplete")
    if supplied_issuer != issuer or supplied_audience != audience:
        raise PermissionError(
            "caller authorization issuer/audience do not match the pinned production trust policy"
        )
    public_key_bytes = public_key_path.resolve().read_bytes()
    if sha256_bytes(public_key_bytes) != key_digest:
        raise PermissionError(
            "authorization public key does not match the pinned production trust anchor"
        )
    return load_authorization_context(
        token_path,
        public_key_path,
        expected_issuer=issuer,
        expected_audience=audience,
        required_capabilities=required_capabilities,
        now=now,
        _verified_public_key_bytes=public_key_bytes,
    )
