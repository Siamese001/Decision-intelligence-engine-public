import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from tools.test_timing_db import TestTimingDB


class TestTestTimingDBAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_timing_db.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_corrupt_json_file(self):
        """Test how TestTimingDB handles a completely malformed JSON file."""
        with open(self.db_path, "w") as f:
            f.write("{ invalid_json: [ }")

        db = TestTimingDB(self.db_path)
        # Should start empty, not crash
        self.assertEqual(db.entry_count(), 0)
        self.assertEqual(db.get_duration("any_test"), 0.0)

    def test_invalid_data_types_in_json(self):
        """Test how TestTimingDB handles valid JSON with wrong data types."""
        with open(self.db_path, "w") as f:
            json.dump(["this", "is", "a", "list", "not", "a", "dict"], f)

        db = TestTimingDB(self.db_path)
        # Should safely fall back to empty dict
        self.assertEqual(db.entry_count(), 0)

    def test_save_to_readonly_directory(self):
        """Test fallback when db save fails due to permissions (e.g. read-only filesystem)."""
        db = TestTimingDB(self.db_path)
        db.record("test_a", 1.0)

        # Make parent directory read-only
        os.chmod(self.temp_dir.name, stat.S_IRUSR | stat.S_IXUSR)

        try:
            # Should not crash, just swallow exception and clean up temp files if possible
            db.save()
            self.assertFalse(self.db_path.exists())
        finally:
            # Restore permissions for cleanup
            os.chmod(self.temp_dir.name, stat.S_IRWXU)

if __name__ == "__main__":
    unittest.main()
