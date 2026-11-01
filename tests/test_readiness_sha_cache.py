import json
import tempfile
import unittest
from pathlib import Path

from tools.readiness_core import _load_cached_receipt_for_sha


class TestReadinessShaCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.receipt_path = Path(self.temp_dir.name) / "receipt.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_receipt(self, data):
        self.receipt_path.write_text(json.dumps(data), encoding="utf-8")

    def test_cache_hit_valid_receipt(self):
        """Test that a valid PASS receipt for the matching SHA returns the receipt data."""
        self._write_receipt({
            "head_commit_sha": "abcd123",
            "terminal_outcome": "PASS",
            "stages": [{"stage_index": 1, "status": "PASS"}]
        })
        res = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNotNone(res)
        self.assertEqual(res["head_commit_sha"], "abcd123")
        self.assertEqual(res["terminal_outcome"], "PASS")

    def test_cache_miss_stale_sha(self):
        """Test that a receipt for a different SHA returns None."""
        self._write_receipt({
            "head_commit_sha": "old000",
            "terminal_outcome": "PASS",
            "stages": [{"stage_index": 1, "status": "PASS"}]
        })
        res = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res)

    def test_cache_miss_failed_receipt(self):
        """Test that a matching SHA but FAIL outcome returns None."""
        self._write_receipt({
            "head_commit_sha": "abcd123",
            "terminal_outcome": "FAIL",
            "stages": [{"stage_index": 1, "status": "FAIL"}]
        })
        res = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res)

    def test_cache_miss_missing_receipt_file(self):
        """Test behavior when the receipt path does not exist."""
        # file not created
        res = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res)

    def test_cache_miss_malformed_receipt(self):
        """Test behavior when the receipt file is malformed JSON or missing stages."""
        self.receipt_path.write_text("invalid json {", encoding="utf-8")
        res = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res)

        self._write_receipt({
            "head_commit_sha": "abcd123",
            "terminal_outcome": "PASS"
            # missing stages array
        })
        res2 = _load_cached_receipt_for_sha("abcd123", self.receipt_path)
        self.assertIsNone(res2)

if __name__ == "__main__":
    unittest.main()
