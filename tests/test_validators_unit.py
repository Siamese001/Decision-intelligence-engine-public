"""Unit tests for dual_pipeline._validators.

Verifies:
1. require_text string validation and whitespace stripping.
2. require_sha256 canonical hex digest verification.
3. require_positive_int integer boundaries.
4. require_strict_keys exact dictionary schema adherence.
5. parse_iso_time and format_time_text temporal conversions.
6. SQLite helper functions (table_exists, sql_placeholders, row_hash).
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline._validators import (
    format_time_text,
    parse_iso_time,
    require_positive_int,
    require_sha256,
    require_strict_keys,
    require_text,
    row_hash,
    sql_placeholders,
    table_exists,
)


class TestValidatorsUnit(unittest.TestCase):
    """Unit tests for reusable validation functions."""

    def test_require_text_valid_and_stripped(self) -> None:
        """require_text strips whitespace and returns text."""
        self.assertEqual(require_text("  hello world  ", field="title"), "hello world")
        self.assertEqual(require_text("", field="opt", allow_empty=True), "")

    def test_require_text_invalid(self) -> None:
        """require_text rejects non-strings and blank strings."""
        with self.assertRaises(ValueError):
            require_text(123, field="test")
        with self.assertRaises(ValueError):
            require_text("   ", field="test")
        with self.assertRaises(TypeError):
            require_text(None, field="test", error_type=TypeError)

    def test_require_sha256(self) -> None:
        """require_sha256 enforces lowercase 64-char hex strings."""
        valid_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        self.assertEqual(require_sha256(valid_digest, field="hash"), valid_digest)

        # Too short
        with self.assertRaises(ValueError):
            require_sha256("e3b0c442", field="hash")

        # Uppercase rejected
        with self.assertRaises(ValueError):
            require_sha256(valid_digest.upper(), field="hash")

        # Non-hex characters
        invalid_hex = valid_digest[:-1] + "g"
        with self.assertRaises(ValueError):
            require_sha256(invalid_hex, field="hash")

    def test_require_positive_int(self) -> None:
        """require_positive_int validates positive integers and rejects booleans."""
        self.assertEqual(require_positive_int(1, label="count"), 1)
        self.assertEqual(require_positive_int(42, label="count"), 42)
        self.assertEqual(require_positive_int(0, label="count", allow_zero=True), 0)

        # Booleans inherit from int in Python; must be explicitly rejected
        with self.assertRaises(ValueError):
            require_positive_int(True, label="bool_val")

        # Zero disallowed by default
        with self.assertRaises(ValueError):
            require_positive_int(0, label="zero_val")

        # Negative disallowed
        with self.assertRaises(ValueError):
            require_positive_int(-5, label="neg_val")

    def test_require_strict_keys(self) -> None:
        """require_strict_keys enforces exact key sets."""
        payload = {"name": "Alice", "role": "Engineer"}
        require_strict_keys(payload, required={"name", "role"}, label="payload")

        # Missing key
        with self.assertRaises(ValueError):
            require_strict_keys({"name": "Alice"}, required={"name", "role"}, label="payload")

        # Unknown key
        with self.assertRaises(ValueError):
            require_strict_keys(
                {"name": "Alice", "role": "Engineer", "extra": 123},
                required={"name", "role"},
                label="payload",
            )

    def test_parse_iso_time_and_format(self) -> None:
        """parse_iso_time correctly normalizes to UTC-aware datetime."""
        ts_str = "2026-09-12T15:30:00Z"
        dt = parse_iso_time(ts_str, field="time")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 9)
        self.assertEqual(dt.day, 12)

        # Round-trip format
        formatted = format_time_text(dt)
        self.assertEqual(formatted, "2026-09-12T15:30:00Z")

        # None handling
        self.assertIsNone(parse_iso_time(None, field="time", allow_none=True))
        self.assertIsNone(format_time_text(None))

        # Invalid formats
        with self.assertRaises(ValueError):
            parse_iso_time("not-a-date", field="time")

    def test_table_exists(self) -> None:
        """table_exists detects SQLite table presence."""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test_table (id TEXT PRIMARY KEY)")
        self.assertTrue(table_exists(conn, "test_table"))
        self.assertFalse(table_exists(conn, "missing_table"))
        conn.close()

    def test_sql_placeholders(self) -> None:
        """sql_placeholders produces comma-separated question marks."""
        self.assertEqual(sql_placeholders(["a"]), "?")
        self.assertEqual(sql_placeholders(["a", "b", "c"]), "?,?,?")
        with self.assertRaises(ValueError):
            sql_placeholders([])

    def test_row_hash(self) -> None:
        """row_hash hashes dict and returns zero_hash for None."""
        zero = "0000000000000000000000000000000000000000000000000000000000000000"
        self.assertEqual(row_hash(None, zero_hash=zero, digest_fn=lambda x: "custom"), zero)
        self.assertEqual(
            row_hash({"col": "val"}, zero_hash=zero, digest_fn=lambda x: "custom"),
            "custom",
        )


if __name__ == "__main__":
    unittest.main()
