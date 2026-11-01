"""Tests for normalize_iso_date in dual_pipeline.core."""

import unittest

from dual_pipeline.core import normalize_iso_date


class NormalizeIsoDateTests(unittest.TestCase):
    def test_iso_standard_date(self) -> None:
        self.assertEqual(normalize_iso_date("2026-09-12"), "2026-09-12")

    def test_iso_datetime_with_tz(self) -> None:
        self.assertEqual(normalize_iso_date("2026-09-12T05:25:25-04:00"), "2026-09-12")
        self.assertEqual(normalize_iso_date("2026-07-14T18:24:41Z"), "2026-07-14")

    def test_us_slash_format_padded(self) -> None:
        self.assertEqual(normalize_iso_date("09/12/2026"), "2026-09-12")

    def test_us_slash_format_unpadded(self) -> None:
        self.assertEqual(normalize_iso_date("9/2/2026"), "2026-09-02")
        self.assertEqual(normalize_iso_date("09/2/2026"), "2026-09-02")
        self.assertEqual(normalize_iso_date("9/02/2026"), "2026-09-02")

    def test_us_dash_format_padded(self) -> None:
        self.assertEqual(normalize_iso_date("09-12-2026"), "2026-09-12")

    def test_us_dash_format_unpadded(self) -> None:
        self.assertEqual(normalize_iso_date("9-2-2026"), "2026-09-02")
        self.assertEqual(normalize_iso_date("09-2-2026"), "2026-09-02")
        self.assertEqual(normalize_iso_date("9-02-2026"), "2026-09-02")

    def test_slash_iso_format(self) -> None:
        self.assertEqual(normalize_iso_date("2026/09/12"), "2026-09-12")
        self.assertEqual(normalize_iso_date("2026/9/2"), "2026-09-02")

    def test_none_and_empty_values(self) -> None:
        self.assertIsNone(normalize_iso_date(None))
        self.assertIsNone(normalize_iso_date(""))
        self.assertIsNone(normalize_iso_date("   "))
        self.assertIsNone(normalize_iso_date("null"))
        self.assertIsNone(normalize_iso_date("none"))
        self.assertIsNone(normalize_iso_date("None"))
        self.assertIsNone(normalize_iso_date("NULL"))

    def test_invalid_types_and_strings(self) -> None:
        self.assertIsNone(normalize_iso_date(12345))
        self.assertIsNone(normalize_iso_date(["2026-09-12"]))
        self.assertIsNone(normalize_iso_date({"date": "2026-09-12"}))
        self.assertIsNone(normalize_iso_date("invalid-date-string"))
        self.assertIsNone(normalize_iso_date("not a date"))
        self.assertIsNone(normalize_iso_date("0000-00-00"))
        self.assertIsNone(normalize_iso_date("9999-99-99"))


if __name__ == "__main__":
    unittest.main()
