#!/usr/bin/env python3
"""Unit test suite for config.secret_providers."""
import tempfile
import unittest
from pathlib import Path

from config.secret_providers import (
    EnvFileSecretProvider,
    HierarchicalSecretProvider,
    MockSecretProvider,
)


class TestSecretProviders(unittest.TestCase):
    def test_mock_secret_provider(self):
        provider = MockSecretProvider({"OPENAI_API_KEY": "sk-proj-mock123", "CUSTOM_KEY": "secret_val"})
        self.assertEqual(provider.get_secret("OPENAI_API_KEY"), "sk-proj-mock123")
        self.assertEqual(provider.get_secret("CUSTOM_KEY"), "secret_val")
        self.assertIsNone(provider.get_secret("NON_EXISTENT"))

    def test_env_file_secret_provider(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("# Comment line\n")
            f.write("OPENAI_API_KEY=sk-proj-tempfilekey\n")
            f.write("OPENAI_MODEL='gpt-5.6-sol'\n")
            f.flush()
            tmp_path = Path(f.name)

        try:
            provider = EnvFileSecretProvider(tmp_path)
            self.assertEqual(provider.get_secret("OPENAI_API_KEY"), "sk-proj-tempfilekey")
            self.assertEqual(provider.get_secret("OPENAI_MODEL"), "gpt-5.6-sol")
            self.assertIsNone(provider.get_secret("UNSET_VAR"))
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_hierarchical_secret_provider(self):
        p1 = MockSecretProvider({"KEY_A": "from_p1"})
        p2 = MockSecretProvider({"KEY_A": "from_p2", "KEY_B": "from_p2"})
        hier = HierarchicalSecretProvider([p1, p2])

        # Priority resolution
        self.assertEqual(hier.get_secret("KEY_A"), "from_p1")
        self.assertEqual(hier.get_secret("KEY_B"), "from_p2")
        self.assertIsNone(hier.get_secret("KEY_C"))


if __name__ == "__main__":
    unittest.main()
