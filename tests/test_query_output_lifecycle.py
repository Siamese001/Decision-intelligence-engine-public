from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.query import _claim_new_directory


class QueryOutputLifecycleTests(unittest.TestCase):
    def test_existing_output_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-query-output-lifecycle-") as directory:
            root = Path(directory)
            output = root / "query-output"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            with self.assertRaisesRegex(
                FileExistsError, "query output directory must be a new directory"
            ):
                _claim_new_directory(output, label="query output directory")

            self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
