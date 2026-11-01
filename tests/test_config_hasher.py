#!/usr/bin/env python3
"""Unit test suite for dual_pipeline.config_hasher."""
import os
import unittest
from unittest import mock

from dual_pipeline.config_hasher import (
    compute_active_config_snapshot,
    compute_config_canonical_sha256,
    verify_config_integrity,
)


class TestConfigHasher(unittest.TestCase):
    def test_snapshot_structure_excludes_raw_secrets(self):
        fake_key = "sk_test_redacted_placeholder"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": fake_key}):
            snapshot = compute_active_config_snapshot()
            snapshot_str = str(snapshot)
            # Ensure raw secret is never present in snapshot
            self.assertNotIn(fake_key, snapshot_str)
            self.assertIn("providers", snapshot)
            self.assertIn("model_tiers", snapshot)
            self.assertTrue(snapshot["providers"]["openai"]["configured"])

    def test_deterministic_sha256_hashing(self):
        hash1 = compute_config_canonical_sha256()
        hash2 = compute_config_canonical_sha256()
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)

    def test_drift_detection_on_config_change(self):
        hash_before = compute_config_canonical_sha256()

        # Simulate configuration change (e.g. changing base_url or reasoning effort)
        with mock.patch.dict(os.environ, {"OPENAI_BASE_URL": "https://new-gateway.internal/v1"}):
            hash_after = compute_config_canonical_sha256()
            self.assertNotEqual(hash_before, hash_after)
            self.assertFalse(verify_config_integrity(hash_before))

    def test_verify_config_integrity(self):
        current_hash = compute_config_canonical_sha256()
        self.assertTrue(verify_config_integrity(current_hash))
        self.assertFalse(verify_config_integrity("0" * 64))

    def test_snapshot_includes_parameters_and_taxonomies(self):
        snapshot = compute_active_config_snapshot()
        self.assertIn("parameters_digest", snapshot)
        self.assertIn("release_policy_digest", snapshot)
        self.assertIn("known_projects", snapshot)
        self.assertIsInstance(snapshot["known_projects"], list)
        self.assertIn("project_nexus", snapshot["known_projects"])


if __name__ == "__main__":
    unittest.main()
