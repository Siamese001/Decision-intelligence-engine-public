#!/usr/bin/env python3
"""
config/secret_providers.py

Pluggable Secret Provider Interfaces and Vault Adapters.
Enforces 2026+ Zero-Trust Credential Resolution:
- Keyring / Vault Provider (OS Keyring / 1Password / Secret Manager)
- EnvFile Provider (Workspace env_di)
- Mock Provider (Hermetic CI/CD test runner)
"""
from __future__ import annotations

import logging
import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("config.secret_providers")


class BaseSecretProvider(ABC):
    """Abstract interface for secret resolution backends."""

    @abstractmethod
    def get_secret(self, key_name: str) -> Optional[str]:
        """Resolves secret value by key name or returns None."""
        raise NotImplementedError


class EnvFileSecretProvider(BaseSecretProvider):
    """Resolves secrets from a local env file (e.g. env_di)."""

    def __init__(self, env_path: Path) -> None:
        self.env_path = env_path
        self._cache: Optional[Dict[str, str]] = None

    @staticmethod
    def _parse_env_line(line: str) -> Optional[tuple[str, str]]:
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            return None
        k, v = clean.split("=", 1)
        val = v.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
            val = val[1:-1]
        return (k.strip(), val) if val else None

    def _load(self) -> Dict[str, str]:
        if self._cache is not None:
            return self._cache
        cache: Dict[str, str] = {}
        if self.env_path.is_file():
            try:
                for line in self.env_path.read_text(encoding="utf-8-sig").splitlines():
                    parsed = self._parse_env_line(line)
                    if parsed:
                        cache[parsed[0]] = parsed[1]
            except Exception as exc:
                logger.warning("Failed reading env file %s: %s", self.env_path, exc)
        self._cache = cache
        return self._cache

    def get_secret(self, key_name: str) -> Optional[str]:
        return self._load().get(key_name)


class SystemKeyringSecretProvider(BaseSecretProvider):
    """Resolves secrets from the OS Keyring (macOS Keychain / Secret-Tool / 1Password CLI)."""

    def __init__(self, service_name: str = "decision-intelligence-engine") -> None:
        self.service_name = service_name

    def get_secret(self, key_name: str) -> Optional[str]:
        # Try macOS security CLI if on Darwin
        import sys
        if sys.platform == "darwin":
            try:
                cmd = ["security", "find-generic-password", "-s", self.service_name, "-a", key_name, "-w"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
        return None


class MockSecretProvider(BaseSecretProvider):
    """Deterministic mock provider for hermetic testing and CI execution."""

    def __init__(self, mock_secrets: Optional[Dict[str, str]] = None) -> None:
        self.mock_secrets = mock_secrets or {}

    def get_secret(self, key_name: str) -> Optional[str]:
        return self.mock_secrets.get(key_name)


class HierarchicalSecretProvider(BaseSecretProvider):
    """Resolves secrets through a priority-ordered hierarchy of providers."""

    def __init__(self, providers: list[BaseSecretProvider]) -> None:
        self.providers = providers

    def get_secret(self, key_name: str) -> Optional[str]:
        for p in self.providers:
            val = p.get_secret(key_name)
            if val is not None and val.strip():
                return val.strip()
        # Ambient process fallback
        val = os.environ.get(key_name)
        return val.strip() if val and val.strip() else None


__all__ = [
    "BaseSecretProvider",
    "EnvFileSecretProvider",
    "SystemKeyringSecretProvider",
    "MockSecretProvider",
    "HierarchicalSecretProvider",
]
