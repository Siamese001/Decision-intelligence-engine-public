"""
dual_pipeline/rbac.py

Role-Based Access Control (RBAC) and credential authorization for authoritative GraphDB mutations.
Enforces capability binding (knowledge:release) and subject verification against the declared reviewer.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union

from dual_pipeline.credentials import CredentialManager

logger = logging.getLogger("dual_pipeline.rbac")

REQUIRED_MUTATION_CAPABILITY = "knowledge:release"


class RBACAuthorizationError(PermissionError):
    """Raised when an operator lacks required capability or subject binding for graph mutation."""


def _decode_token_payload(token_str: str) -> Dict[str, Any]:
    """Decode raw JSON or JWT token payload into claims dictionary."""
    clean = token_str.strip()
    if clean.startswith("{") and clean.endswith("}"):
        try:
            return json.loads(clean)
        except Exception as e:
            raise RBACAuthorizationError(f"Malformed JSON auth token: {e}") from e

    parts = clean.split(".")
    if len(parts) >= 2:
        payload_b64 = parts[1]
        try:
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            decoded_bytes = base64.urlsafe_b64decode(padded)
            return json.loads(decoded_bytes.decode("utf-8"))
        except Exception as e:
            raise RBACAuthorizationError(f"Malformed JWT payload in auth token: {e}") from e

    # Simple base64 token fallback
    try:
        padded = clean + "=" * (-len(clean) % 4)
        decoded_bytes = base64.urlsafe_b64decode(padded)
        return json.loads(decoded_bytes.decode("utf-8"))
    except Exception:
        raise RBACAuthorizationError("Invalid auth token format: expected JWT, base64-encoded JSON, or JSON string.")


def _resolve_raw_token_str(auth_token: Optional[Union[str, Path]]) -> Optional[str]:
    if not auth_token:
        return CredentialManager.get_credential("NEE_AUTH_TOKEN")
    p = Path(auth_token) if not isinstance(auth_token, Path) else auth_token
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception as e:
            raise RBACAuthorizationError(f"Cannot read auth token from file '{auth_token}': {e}") from e
    return str(auth_token).strip()


def _extract_token_capabilities(claims: Dict[str, Any]) -> Set[str]:
    raw_caps = claims.get("capabilities") or claims.get("roles") or claims.get("scopes") or []
    if isinstance(raw_caps, str):
        return set(raw_caps.split())
    if isinstance(raw_caps, (list, tuple)):
        return set(str(c).strip() for c in raw_caps)
    return set()


def _verify_token_claims_and_capabilities(
    claims: Dict[str, Any], reviewer: Optional[str], required_capability: str
) -> Dict[str, Any]:
    exp = claims.get("exp")
    if exp is not None:
        try:
            if float(exp) < time.time():
                raise RBACAuthorizationError(f"Auth token expired at timestamp {exp} (current: {time.time():.0f}).")
        except (ValueError, TypeError) as e:
            raise RBACAuthorizationError(f"Invalid 'exp' claim in auth token: {e}") from e

    token_subject = str(claims.get("sub") or claims.get("subject") or "").strip()
    if not token_subject:
        raise RBACAuthorizationError("Auth token missing required 'sub' or 'subject' claim.")
    if reviewer and reviewer.strip() and token_subject.lower() != reviewer.strip().lower():
        raise RBACAuthorizationError(
            f"RBAC_SUBJECT_MISMATCH: Auth token subject '{token_subject}' does not match declared reviewer '{reviewer}'."
        )

    token_caps = _extract_token_capabilities(claims)
    if required_capability not in token_caps and "*" not in token_caps:
        raise RBACAuthorizationError(
            f"RBAC_CAPABILITY_DENIED: Token for '{token_subject}' lacks required capability "
            f"'{required_capability}'. Available capabilities: {sorted(token_caps)}"
        )

    return {
        "subject": token_subject,
        "reviewer": token_subject,
        "capabilities": list(token_caps),
        "auth_type": "BEARER_TOKEN",
        "authorized": True,
    }


def validate_mutation_authorization(
    auth_token: Optional[Union[str, Path]] = None,
    reviewer: Optional[str] = None,
    required_capability: str = REQUIRED_MUTATION_CAPABILITY,
    allow_dev_bypass: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Validates authorization for authoritative GraphDB mutation."""
    token_str = _resolve_raw_token_str(auth_token)
    if not token_str:
        raise RBACAuthorizationError(
            f"MUTATION_REJECTED: Authoritative GraphDB mutation (--mutate-graph) requires a valid "
            f"--auth-token with '{required_capability}' capability."
        )

    claims = _decode_token_payload(token_str)
    return _verify_token_claims_and_capabilities(claims, reviewer, required_capability)
