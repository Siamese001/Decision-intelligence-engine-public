import tempfile
import unittest
from pathlib import Path

from tools.run_tests_with_progress import _NamedTest
from tools.test_timing_db import TestTimingDB


class TestTestTimingDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_timing_db.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_and_get_duration(self):
        db = TestTimingDB(self.db_path)
        db.record("test_1", 2.0)
        self.assertEqual(db.get_duration("test_1"), 2.0)

    def test_ema_smoothing(self):
        db = TestTimingDB(self.db_path)
        db.record("test_1", 2.0)
        db.record("test_1", 4.0)
        # EMA alpha=0.5: 0.5*2.0 + 0.5*4.0 = 3.0
        self.assertEqual(db.get_duration("test_1"), 3.0)

    def test_save_and_load(self):
        db1 = TestTimingDB(self.db_path)
        db1.record("test_a", 5.5)
        db1.save()

        self.assertTrue(self.db_path.exists())

        db2 = TestTimingDB(self.db_path)
        self.assertEqual(db2.get_duration("test_a"), 5.5)
        self.assertEqual(db2.entry_count(), 1)

    def test_sort_cases_lpt(self):
        db = TestTimingDB(self.db_path)
        db.record("test_fast", 0.1)
        db.record("test_slow", 5.0)
        db.record("test_medium", 2.5)

        cases = [
            _NamedTest("test_fast"),
            _NamedTest("test_unknown"), # defaults to 0.0
            _NamedTest("test_slow"),
            _NamedTest("test_medium")
        ]

        sorted_cases = db.sort_cases_lpt(cases)
        # Expected: slow (5.0), medium (2.5), fast (0.1), unknown (0.0)
        self.assertEqual(sorted_cases[0].id(), "test_slow")
        self.assertEqual(sorted_cases[1].id(), "test_medium")
        self.assertEqual(sorted_cases[2].id(), "test_fast")
        self.assertEqual(sorted_cases[3].id(), "test_unknown")

    def test_negative_duration_ignored(self):
        db = TestTimingDB(self.db_path)
        db.record("test_1", 2.0)
        db.record("test_1", -1.0)
        self.assertEqual(db.get_duration("test_1"), 2.0)

if __name__ == "__main__":
    unittest.main()
