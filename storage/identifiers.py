"""
Dual-Artery Enterprise Hybrid Architecture: Storage Cryptographic Identifiers.
Hardware-tied keychain resolution and HMAC entity anchoring.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import platform
import re
import subprocess
from typing import Optional

from storage.credentials import CredentialManager

logger = logging.getLogger("storage.identifiers")


def resolve_keychain_secret(service: str = "com.nee.decision_engine", account: str = "shadow_graph_key") -> str:
    """
    Resolves hardware/OS-bound encryption secret for the shadow graph.
    Hierarchy:
      1. macOS Keychain via `/usr/bin/security` (Production M5 Pro hardware)
      2. Environment Variable `INTERNAL_ADVISORY_KEY`
      3. Secure local derivation based on hardware machine UUID & user salt
    """
    # 1. Try macOS Keychain
    if platform.system() == "Darwin":
        try:
            cmd = ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5.0)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception as e:
            logger.debug(f"Keychain lookup failed: {e}")

    # 2. Try Environment Variable
    env_key = CredentialManager.get_credential("INTERNAL_ADVISORY_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    # 3. Deterministic hardware-tied fallback salt (dev/test environment)
    machine_id = "local_dev_m5_pro"
    if platform.system() == "Darwin":
        try:
            cmd = ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]
            ioreg_out = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5.0)
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', ioreg_out.stdout)
            if m:
                machine_id = m.group(1)
        except Exception:
            pass

    derived = hashlib.sha256(f"{machine_id}:{os.getlogin()}:nee_shadow_salt".encode()).hexdigest()
    return derived


def compute_entity_hmac(node_id: str, salt: Optional[str] = None) -> str:
    """
    Computes zero-knowledge deterministic HMAC anchor linking advisory nodes
    to canonical nodes without cross-database foreign key leaks.
    """
    secret_salt = salt or CredentialManager.get_credential("HMAC_ENTITY_SALT", "nee_canonical_salt_2026")
    return hmac.new(secret_salt.encode("utf-8"), node_id.encode("utf-8"), hashlib.sha256).hexdigest()


def deterministic_canonical_node_id(evidence_id: str, node_type: str, name: str) -> str:
    """Generates deterministic ID for canonical facts to guarantee idempotent upserts."""
    raw = f"{evidence_id}:{node_type}:{name}".encode("utf-8")
    return f"cnode_{hashlib.sha256(raw).hexdigest()[:12]}"


def deterministic_advisory_node_id(evidence_id: str, category_or_type: str, name: str) -> str:
    """Generates deterministic ID for internal advisory facts to guarantee idempotent upserts."""
    raw = f"{evidence_id}:{category_or_type}:{name}".encode("utf-8")
    return f"adv_{hashlib.sha256(raw).hexdigest()[:12]}"


def deterministic_edge_id(evidence_id: str, from_node_id: str, predicate: str, to_node_id: str) -> str:
    """Generates deterministic ID for relational edges to guarantee idempotent upserts."""
    raw = f"{evidence_id}:{from_node_id}:{predicate}:{to_node_id}".encode("utf-8")
    return f"edge_{hashlib.sha256(raw).hexdigest()[:12]}"


def deterministic_assertion_id(evidence_id: str, node_id: str, predicate: str) -> str:
    """Generates deterministic ID for assertions to guarantee idempotent upserts."""
    raw = f"{evidence_id}:{node_id}:{predicate}".encode("utf-8")
    return f"asrt_{hashlib.sha256(raw).hexdigest()[:12]}"
