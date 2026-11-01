"""
dual_pipeline/hitl_governance_core/crypto.py

Cryptographic signing and verification utilities for Human-in-the-Loop (HITL)
governance and Ambiguity Lock Register (ALR) receipts.
Enforces RFC 8785 (JCS) canonical byte encoding and Ed25519 digital signatures.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from dual_pipeline.core import (
    canonical_bytes,
    digest,
    sha256_bytes,
    utc_now,
)

from .ledger import HITLDecisionStore, _find_default_ledger_path

ALR_SUBJECT_PREFIX = b"ALR-SUBJECT/v1\n"
ALR_APPROVAL_PREFIX = b"ALR-HITL-APPROVAL/v1\n"
ALR_ENTRY_PREFIX = b"ALR-LEDGER-ENTRY/v1\n"


def generate_hitl_keypair() -> Tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair for HITL approval signing."""
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def export_private_key_pem(private_key: Ed25519PrivateKey) -> str:
    """Export an Ed25519 private key as PKCS#8 unencrypted PEM."""
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def export_public_key_pem(public_key: Ed25519PublicKey) -> str:
    """Export an Ed25519 public key as SubjectPublicKeyInfo PEM."""
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def load_private_key_pem(pem_str: str) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from PEM string."""
    key = serialization.load_pem_private_key(pem_str.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Key is not an Ed25519 private key")
    return key


def load_public_key_pem(pem_str: str) -> Ed25519PublicKey:
    """Load an Ed25519 public key from PEM string."""
    key = serialization.load_pem_public_key(pem_str.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Key is not an Ed25519 public key")
    return key


def get_governed_hitl_keypair(
    key_dir: Optional[Union[Path, str]] = None,
) -> Tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Load or generate the persistent Ed25519 keypair for HITL approval authority."""
    target_dir = Path(".local/keys") if key_dir is None else Path(key_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    priv_path = target_dir / "hitl_authority_ed25519.pem"
    pub_path = target_dir / "hitl_authority_ed25519_pub.pem"

    if priv_path.is_file() and pub_path.is_file():
        priv_key = load_private_key_pem(priv_path.read_text(encoding="utf-8"))
        pub_key = load_public_key_pem(pub_path.read_text(encoding="utf-8"))
        return priv_key, pub_key

    priv_key, pub_key = generate_hitl_keypair()
    priv_pem = export_private_key_pem(priv_key)
    pub_pem = export_public_key_pem(pub_key)

    priv_path.write_text(priv_pem, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(priv_path, 0o600)
    pub_path.write_text(pub_pem, encoding="utf-8")

    return priv_key, pub_key


def canonicalize_subject_dict(subject: Dict[str, Any]) -> Dict[str, Any]:
    """Canonicalize meeting subject fields (date to MM/DD/YYYY, project alias, and base attendee names)."""
    norm = dict(subject)
    if "meeting_date" in norm and norm["meeting_date"]:
        d_str = str(norm["meeting_date"]).strip()
        m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", d_str)
        if m:
            norm["meeting_date"] = f"{int(m.group(2)):02d}/{int(m.group(3)):02d}/{m.group(1)}"
        else:
            m2 = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", d_str)
            if m2:
                norm["meeting_date"] = f"{int(m2.group(1)):02d}/{int(m2.group(2)):02d}/{m2.group(3)}"
    if "project" in norm and norm["project"]:
        norm["project"] = str(norm["project"]).lower().strip()
    if "attendees" in norm and isinstance(norm["attendees"], (list, set, tuple)):
        clean_attendees = set()
        for att in norm["attendees"]:
            if isinstance(att, str):
                base = re.sub(r"\s*\([^)]*\)", "", att).strip()
                if base:
                    clean_attendees.add(base)
            elif isinstance(att, dict) and "name" in att:
                base = re.sub(r"\s*\([^)]*\)", "", str(att["name"])).strip()
                if base:
                    clean_attendees.add(base)
        norm["attendees"] = sorted(clean_attendees)
    return norm


def compute_alr_subject_digest(subject: Dict[str, Any]) -> str:
    """Compute the SHA-256 digest of canonical RFC 8785 (JCS) subject bytes."""
    canon_subject = canonicalize_subject_dict(subject)
    payload_bytes = canonical_bytes(canon_subject)
    return sha256_bytes(ALR_SUBJECT_PREFIX + payload_bytes)


def sign_hitl_approval_payload(
    payload: Dict[str, Any],
    private_key: Ed25519PrivateKey,
) -> str:
    """Sign an approval payload over RFC 8785 JCS bytes with prefix; return unpadded base64url."""
    msg = ALR_APPROVAL_PREFIX + canonical_bytes(payload)
    signature_bytes = private_key.sign(msg)
    return base64.urlsafe_b64encode(signature_bytes).decode("ascii").rstrip("=")


def verify_hitl_approval_signature(
    payload: Dict[str, Any],
    signature_b64: Optional[str] = None,
    public_key: Optional[Union[Ed25519PublicKey, str]] = None,
) -> bool:
    """Verify an Ed25519 signature over RFC 8785 canonical bytes with approval prefix (supports 1-arg entry or 3-arg)."""
    if signature_b64 is None and public_key is None and isinstance(payload, dict):
        if "payload" in payload and "signature" in payload:
            inner_payload = payload.get("payload")
            sig = payload.get("signature")
            pub_pem = payload.get("public_key_pem")
            if not isinstance(inner_payload, dict) or not sig or not pub_pem:
                return False
            payload = inner_payload
            signature_b64 = str(sig)
            public_key = str(pub_pem)
        else:
            return False
    if not signature_b64 or not public_key:
        return False
    try:
        pub = load_public_key_pem(public_key) if isinstance(public_key, str) else public_key

        sig_str = signature_b64.strip()
        padding = 4 - (len(sig_str) % 4)
        if padding < 4:
            sig_str += "=" * padding
        sig_bytes = base64.urlsafe_b64decode(sig_str)

        msg = ALR_APPROVAL_PREFIX + canonical_bytes(payload)
        pub.verify(sig_bytes, msg)
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


def verify_hitl_approval_entry(entry: Dict[str, Any]) -> bool:
    """Verifies a full signed approval entry's payload signature using its public key."""
    if not isinstance(entry, dict):
        return False
    payload = entry.get("payload")
    sig = entry.get("signature")
    pub = entry.get("public_key_pem")
    if not isinstance(payload, dict) or not sig or not pub:
        return False
    return verify_hitl_approval_signature(payload, sig, pub)


def create_signed_hitl_approval_entry(
    *,
    subject_sha256: str,
    case_ids: List[str],
    decision: str = "APPROVE",
    operator_id: str = "human_operator",
    private_key: Optional[Ed25519PrivateKey] = None,
    public_key: Optional[Ed25519PublicKey] = None,
    ledger_id: str = "hitl-ledger-main",
    sequence: int = 1,
    previous_entry_sha256: str = "0" * 64,
    metadata: Optional[Dict[str, Any]] = None,
    meeting_slug: Optional[str] = None,
    meeting_date: Optional[str] = None,
    ttl_seconds: int = 86400,
) -> Dict[str, Any]:
    """Construct, canonicalize, and sign an immutable HITL approval ledger entry."""
    if private_key is None or public_key is None:
        priv, pub = get_governed_hitl_keypair()
        private_key = private_key or priv
        public_key = public_key or pub

    now = datetime.now(timezone.utc)
    now_iso = utc_now()
    expires_iso = datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    auth_event_id = f"auth-ev-{secrets.token_hex(16)}"
    challenge_nonce = secrets.token_urlsafe(32)

    resolved_slug = meeting_slug or (metadata.get("meeting_slug") or metadata.get("meeting_name") if metadata else None)
    resolved_date = meeting_date or (metadata.get("meeting_date") if metadata else None)

    payload = {
        "schema": "hitl-approval/v1",
        "ledger_id": ledger_id,
        "sequence": sequence,
        "previous_entry_sha256": previous_entry_sha256,
        "subject_sha256": subject_sha256,
        "case_ids": case_ids,
        "decision": decision,
        "operator": {
            "issuer": "local_operator_auth",
            "subject": operator_id,
        },
        "authentication_event_id": auth_event_id,
        "challenge_nonce": challenge_nonce,
        "issued_at": now_iso,
        "approved_at": now_iso,
        "expires_at": expires_iso,
        "signing_key_id": sha256_bytes(export_public_key_pem(public_key).encode("utf-8"))[:16],
    }
    if resolved_slug:
        payload["meeting_slug"] = resolved_slug
    if resolved_date:
        payload["meeting_date"] = resolved_date
    if metadata:
        payload["metadata"] = metadata

    sig = sign_hitl_approval_payload(payload, private_key)
    entry_hash = sha256_bytes(ALR_ENTRY_PREFIX + canonical_bytes({"payload": payload, "signature": sig}))

    entry: Dict[str, Any] = {
        "schema": "hitl-approval/v1",
        "entry_id": auth_event_id,
        "decision_id": auth_event_id,
        "category": "speaker_attribution_hitl",
        "selected_key": decision,
        "operator_id": operator_id,
        "subject_sha256": subject_sha256,
        "payload": payload,
        "signature": sig,
        "public_key_pem": export_public_key_pem(public_key),
        "entry_sha256": entry_hash,
        "entry_digest": entry_hash,
        "recorded_at": now_iso,
    }
    if resolved_slug:
        entry["meeting_slug"] = resolved_slug
    if resolved_date:
        entry["meeting_date"] = resolved_date
    return entry


def append_signed_approval_to_ledger(
    entry: Dict[str, Any],
    ledger_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Append a signed HITL approval record to hitl_decision_ledger.json under file locking."""
    target_ledger = Path(ledger_path).resolve() if ledger_path else _find_default_ledger_path().resolve()
    store = HITLDecisionStore(ledger_path=target_ledger, use_sqlite=False)
    with store._ledger_lock():
        records = store._load_records()
        previous_entry_hash = records[-1].get("entry_digest", "0" * 64) if records else ("0" * 64)
        entry["sequence_number"] = len(records)
        entry["previous_entry_hash"] = previous_entry_hash
        entry_sans_digest = {k: v for k, v in entry.items() if k != "entry_digest"}
        entry["entry_digest"] = digest(entry_sans_digest)
        records.append(entry)
        store._save_records(records)
    return entry



def _unwrap_approval_record(r: Any) -> Dict[str, Any]:
    if not isinstance(r, dict):
        return {}
    if not r.get("payload"):
        meta = r.get("metadata")
        if isinstance(meta, dict):
            alr_env = meta.get("_alr_envelope")
            if isinstance(alr_env, dict) and alr_env.get("payload"):
                return {**r, **alr_env}
            raw = meta.get("_raw_record")
            if isinstance(raw, dict) and raw.get("payload"):
                return raw
    return r


def _is_matching_signed_approval(r: Any, subject_sha256: Optional[str]) -> bool:
    target = _unwrap_approval_record(r)
    p = target.get("payload")
    if not isinstance(p, dict) or p.get("schema") != "hitl-approval/v1":
        return False
    if subject_sha256 is not None:
        p_subj = p.get("subject_sha256")
        r_subj = target.get("subject_sha256")
        if p_subj != subject_sha256 and r_subj != subject_sha256:
            return False
    return True


def _find_common_root_ledger() -> Optional[Path]:
    try:
        import subprocess
        git_common = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"], text=True, stderr=subprocess.DEVNULL, timeout=10.0
        ).strip()
        if not git_common:
            return None
        c_dir = Path(git_common).resolve()
        common_root = c_dir.parent if c_dir.name == ".git" else c_dir
        if common_root.name == ".git":
            common_root = common_root.parent
        root_json = common_root / "hitl_decision_ledger.json"
        return root_json.resolve() if root_json.is_file() else None
    except Exception:
        return None


def _discover_ledger_paths(ledger_path: Optional[Union[Path, str]]) -> List[Path]:
    target = Path(ledger_path).resolve() if ledger_path else _find_default_ledger_path().resolve()
    paths = [target]
    common = _find_common_root_ledger()
    if common and common not in paths:
        paths.append(common)
    return paths


def _read_ledger_records(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        recs = data.get("operator_decisions") or data.get("decisions") or data.get("entries", [])
        return recs if isinstance(recs, list) else []
    except Exception:
        return []


def _upsert_approval(
    approvals: List[Dict[str, Any]], seen_ids: set[str], unwrapped: Dict[str, Any], eid: Optional[str]
) -> None:
    if not eid:
        approvals.append(unwrapped)
        return
    if eid not in seen_ids:
        approvals.append(unwrapped)
        seen_ids.add(eid)
        return
    for idx, existing in enumerate(approvals):
        cur_id = existing.get("entry_id") or existing.get("decision_id")
        if cur_id == eid:
            if not existing.get("payload") and unwrapped.get("payload"):
                approvals[idx] = unwrapped
            break


def find_signed_approvals(
    subject_sha256: Optional[str] = None,
    ledger_path: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """Scan the governed decision ledger for valid signed approval entries."""
    seen_ids: set[str] = set()
    approvals: List[Dict[str, Any]] = []
    for path in _discover_ledger_paths(ledger_path):
        for r in _read_ledger_records(path):
            unwrapped = _unwrap_approval_record(r)
            if _is_matching_signed_approval(unwrapped, subject_sha256):
                eid = unwrapped.get("entry_id") or unwrapped.get("decision_id")
                _upsert_approval(approvals, seen_ids, unwrapped, eid)
    return approvals

