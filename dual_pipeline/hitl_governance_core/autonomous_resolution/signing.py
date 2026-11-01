"""
dual_pipeline/hitl_governance_core/autonomous_resolution/signing.py

Cryptographic signing and verification for governed autonomous resolutions.
Enforces RFC 8785 (JCS) canonical serialization, domain-separated Ed25519 signatures,
and strictly rejects bare digests or unauthenticated payloads.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

AUTONOMOUS_RESOLUTION_PREFIX = b"AUTONOMOUS-RESOLUTION/v1\n"


def canonical_json_bytes(data: Mapping[str, Any]) -> bytes:
    """Produces deterministic RFC 8785 canonical JSON bytes."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_canonical_digest(data: Mapping[str, Any]) -> str:
    """Computes a SHA-256 hex digest over canonical JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(data)).hexdigest()


def generate_autonomous_keypair() -> Tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generates a fresh Ed25519 keypair for autonomous resolution authority."""
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def export_private_key_pem(priv: Ed25519PrivateKey) -> str:
    """Exports private key as PKCS8 PEM."""
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def export_public_key_pem(pub: Ed25519PublicKey) -> str:
    """Exports public key as SubjectPublicKeyInfo PEM."""
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def load_private_key_pem(pem_str: str) -> Ed25519PrivateKey:
    """Loads private key from PEM string."""
    key = serialization.load_pem_private_key(pem_str.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Key is not an Ed25519 private key")
    return key


def load_public_key_pem(pem_str: str) -> Ed25519PublicKey:
    """Loads public key from PEM string."""
    key = serialization.load_pem_public_key(pem_str.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Key is not an Ed25519 public key")
    return key


def sign_autonomous_payload(
    private_key: Ed25519PrivateKey,
    payload: Mapping[str, Any],
) -> str:
    """
    Signs a canonical autonomous resolution payload using Ed25519.
    Returns base64-encoded signature.
    """
    msg = AUTONOMOUS_RESOLUTION_PREFIX + canonical_json_bytes(payload)
    sig_bytes = private_key.sign(msg)
    return base64.b64encode(sig_bytes).decode("utf-8")


def verify_autonomous_signature(
    public_key: Ed25519PublicKey,
    payload: Mapping[str, Any],
    signature_b64: str,
) -> bool:
    """
    Verifies an Ed25519 digital signature over a canonical autonomous payload.
    Rejects bare digests and tampered payloads.
    """
    if not signature_b64 or not signature_b64.strip():
        return False
    try:
        sig_bytes = base64.b64decode(signature_b64)
        if len(sig_bytes) != 64:
            return False
        msg = AUTONOMOUS_RESOLUTION_PREFIX + canonical_json_bytes(payload)
        public_key.verify(sig_bytes, msg)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
