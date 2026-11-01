#!/usr/bin/env python3
"""Verification test suite for Single Source of Truth (env_di) environment configuration."""
from __future__ import annotations

import unittest
from pathlib import Path

from dotenv import dotenv_values

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


class SSOTEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        from config.credentials import resolve_ssot_env_file
        self.ssot_path = resolve_ssot_env_file()
        self.legacy_dot_env = WORKSPACE_ROOT / ".env"
        self.legacy_dot_env_di = WORKSPACE_ROOT / ".env_di"
        self.legacy_env = WORKSPACE_ROOT / "env"

    def test_ssot_env_di_exists_and_is_regular_physical_file(self) -> None:
        """Verify env_di exists and is a regular physical file (not a broken symlink)."""
        self.assertTrue(
            self.ssot_path.is_file(),
            f"Expected {self.ssot_path} to exist as a physical file.",
        )

    def test_dot_env_is_strictly_absent(self) -> None:
        """Verify .env does not exist anywhere in repository root (symlink or file)."""
        self.assertFalse(
            self.legacy_dot_env.exists(),
            f"Expected {self.legacy_dot_env} to be completely removed.",
        )
        self.assertFalse(
            self.legacy_dot_env.is_symlink(),
            f"Expected {self.legacy_dot_env} symlink to be deleted.",
        )

    def test_legacy_dot_env_di_and_env_are_strictly_absent(self) -> None:
        """Verify legacy .env_di and un-namespaced env files are strictly absent."""
        self.assertFalse(
            self.legacy_dot_env_di.exists(),
            f"Expected {self.legacy_dot_env_di} to be deleted.",
        )
        self.assertFalse(
            self.legacy_env.exists(),
            f"Expected {self.legacy_env} to be deleted.",
        )

    def test_ssot_contains_all_required_keys(self) -> None:
        """Verify all critical API keys, SSL certificates, models, and M5 Pro flags reside in env_di."""
        values = dotenv_values(self.ssot_path)

        required_keys = [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GITHUB_TOKEN",
            "HF_TOKEN",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "NEE_BGE_M3_MODEL_PATH",
            "NEE_BGE_M3_MODEL_MANIFEST",
            "NEE_EMBEDDING_DEVICE",
            "NEE_EMBEDDING_FP16",
        ]

        for key in required_keys:
            self.assertIn(
                key,
                values,
                f"Missing required SSOT environment variable: {key}",
            )
            self.assertTrue(
                values[key],
                f"Required SSOT environment variable {key} must not be empty.",
            )

        self.assertNotIn(
            "GEMINI_API_KEY",
            values,
            "GEMINI_API_KEY must not be present in SSOT; standardize on GOOGLE_API_KEY.",
        )

        self.assertEqual(values["NEE_EMBEDDING_DEVICE"], "mps")
        self.assertEqual(values["NEE_EMBEDDING_FP16"], "1")

    def test_central_workstation_ssot_exists_and_is_secure(self) -> None:
        """Verify central workstation SSOT (~/.config/ai_env/common.env) exists and has secure permissions."""
        from config.credentials import CENTRAL_SSOT_FILE, FALLBACK_SSOT_FILE
        central_path = CENTRAL_SSOT_FILE if CENTRAL_SSOT_FILE.is_file() else (FALLBACK_SSOT_FILE if FALLBACK_SSOT_FILE.is_file() else None)
        self.assertIsNotNone(central_path, "Central workstation SSOT must exist at ~/.config/ai_env/common.env or ~/env/common.env")
        assert central_path is not None

        # Check permissions (0600 or 0400)
        mode = central_path.stat().st_mode & 0o777
        self.assertIn(mode, (0o600, 0o400), f"Central SSOT permissions {oct(mode)} must be 0600 or 0400")

        central_vals = dotenv_values(central_path)
        for shared_key in ["ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GITHUB_TOKEN", "HF_TOKEN"]:
            self.assertIn(shared_key, central_vals, f"Missing shared key {shared_key} in central SSOT")
            self.assertTrue(central_vals[shared_key], f"Shared key {shared_key} must not be empty in central SSOT")

        # Prohibited domain keys
        self.assertNotIn("OPENAI_API_KEY", central_vals, "OPENAI_API_KEY must not reside in central SSOT; belongs in repo profile")

    def test_hierarchical_waterfall_resolution(self) -> None:
        """Verify CredentialManager resolves credentials via hierarchical waterfall."""
        from config.credentials import CredentialManager, resolve_openai_credentials
        self.assertTrue(CredentialManager.ensure_ssot_env_loaded())

        # OPENAI_API_KEY must resolve from env_di (PartnerAdvisory key)
        creds = resolve_openai_credentials(allow_mock=False)
        self.assertTrue(creds.has_live_key)
        self.assertEqual(creds.source, "env_di")
        self.assertTrue(creds.masked_key.startswith("sk-proj-"))


if __name__ == "__main__":
    unittest.main()
