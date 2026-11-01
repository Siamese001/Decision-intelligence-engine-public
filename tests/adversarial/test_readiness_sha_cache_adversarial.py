import json
import tempfile
import unittest
from pathlib import Path

from tools.readiness_core import _load_cached_receipt_for_sha


class TestReadinessShaCacheAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.receipt_path = Path(self.temp_dir.name) / "receipt.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_receipt(self, data):
        self.receipt_path.write_text(json.dumps(data), encoding="utf-8")

    def test_adversarial_sha_collision_bypass_attempt(self):
        """Test that a partial match (e.g. prefix) does not fool the cache."""
        self._write_receipt({
            "head_commit_sha": "abcd12345",
            "terminal_outcome": "PASS",
            "stages": [{"stage_index": 1, "status": "PASS"}]
        })
        # Try to load with a prefix of the SHA
        res = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res)

        # Try to load with a different but similar string
        res = _load_cached_receipt_for_sha("abcd12345_fake", self.receipt_path)
        self.assertIsNone(res)

    def test_adversarial_partial_pass_fabrication(self):
        """Test that a receipt that claims PASS but has empty stages array is rejected."""
        self._write_receipt({
            "head_commit_sha": "abcd123",
            "terminal_outcome": "PASS",
            "stages": []  # Empty stages
        })
        res = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res)

        self._write_receipt({
            "head_commit_sha": "abcd123",
            "terminal_outcome": "PASS",
            "stages": "not_an_array"  # Invalid type
        })
        res2 = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res2)

    def test_adversarial_cache_corruption(self):
        """Test that file access errors or corrupted data structures are handled safely."""
        self._write_receipt({
            "head_commit_sha": "abcd123",
            "terminal_outcome": "PASS",
            "stages": [{"stage_index": 1, "status": "PASS"}]
        })

        # Corrupt the file to be a directory
        self.receipt_path.unlink()
        self.receipt_path.mkdir()

        # Should gracefully return None when reading fails
        res = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res)

if __name__ == "__main__":
    unittest.main()
