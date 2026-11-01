"""
tests/test_pre_gate_screener_unit.py

Unit tests for Wave 1: Deterministic Pre-Gate Screener (Agent 3).
Validates zero-token heuristic validation of attendee rosters, metadata schemas,
source content structure, and scope bounds.
"""
from __future__ import annotations

import unittest
from dual_pipeline.token_optimization.pre_gate_screener import (
    DEFAULT_AVERTED_TOKENS_PER_RUN,
    PreGateResult,
    PreGateValidationError,
    screen_pre_gate,
    validate_attendee_roster,
    validate_metadata_schema,
    validate_or_raise,
    validate_scope_bounds,
    validate_source_content,
)


class PreGateScreenerUnitTests(unittest.TestCase):
    def test_01_attendee_roster_validation_valid_formats(self) -> None:
        """Verify attendee roster parsing and validation across formats."""
        # Comma-separated string
        meta1 = {"project": "lens", "attendees": "harper Hoffman, Amit Ayer"}
        v1, errs1, _ = validate_attendee_roster(meta1, modality="meeting")
        self.assertTrue(v1)
        self.assertEqual(len(errs1), 0)

        # List of strings
        meta2 = {"project": "lens", "attendees": ["harper Hoffman", "Amit Ayer"]}
        v2, errs2, _ = validate_attendee_roster(meta2, modality="meeting")
        self.assertTrue(v2)
        self.assertEqual(len(errs2), 0)

        # Dict format
        meta3 = {"project": "lens", "attendees": {"harper Hoffman": {}, "Amit Ayer": {}}}
        v3, errs3, _ = validate_attendee_roster(meta3, modality="meeting")
        self.assertTrue(v3)
        self.assertEqual(len(errs3), 0)

    def test_02_attendee_roster_validation_invalid_and_corrupt(self) -> None:
        """Verify invalid or corrupted attendee rosters fail closed in strict mode."""
        # Empty attendees in strict mode
        meta_empty = {"project": "lens", "attendees": ""}
        v1, errs1, _ = validate_attendee_roster(meta_empty, modality="meeting", strict=True)
        self.assertFalse(v1)
        self.assertIn("Missing or empty attendee roster", errs1[0])

        # Control characters in attendee name
        meta_ctrl = {"project": "lens", "attendees": "harper Hoffman, Bad\x00Name"}
        v2, errs2, _ = validate_attendee_roster(meta_ctrl, modality="meeting")
        self.assertFalse(v2)
        self.assertTrue(any("illegal control characters" in e for e in errs2))

        # Too short attendee name
        meta_short = {"project": "lens", "attendees": "A, B"}
        v3, errs3, _ = validate_attendee_roster(meta_short, modality="meeting")
        self.assertFalse(v3)
        self.assertTrue(any("too short" in e for e in errs3))

    def test_03_metadata_schema_validation(self) -> None:
        """Verify project presence and date formatting."""
        # Missing project
        v1, errs1, _ = validate_metadata_schema({})
        self.assertFalse(v1)
        self.assertTrue(any("project" in e for e in errs1))

        # Valid project and date formats
        valid_meta = {"project": "project_lens", "date": "10-07-2026"}
        v2, errs2, _ = validate_metadata_schema(valid_meta, strict=True)
        self.assertTrue(v2)

        # Invalid date format in strict mode
        invalid_date = {"project": "project_lens", "date": "Oct 7 2026"}
        v3, errs3, _ = validate_metadata_schema(invalid_date, strict=True)
        self.assertFalse(v3)
        self.assertTrue(any("date" in e for e in errs3))

    def test_04_source_content_validation(self) -> None:
        """Verify source text validation for nulls, length, and whitespace."""
        # None source
        v1, errs1, _ = validate_source_content(None)  # type: ignore[arg-type]
        self.assertFalse(v1)

        # Empty whitespace
        v2, errs2, _ = validate_source_content("   \n\t  ")
        self.assertFalse(v2)

        # Too short
        v3, errs3, _ = validate_source_content("Short")
        self.assertFalse(v3)

        # Null byte binary injection
        v4, errs4, _ = validate_source_content("Valid text with a null byte \x00 in the middle")
        self.assertFalse(v4)
        self.assertTrue(any("null bytes" in e for e in errs4))

        # Valid source
        v5, errs5, _ = validate_source_content("This is a valid meeting transcript text with enough characters.")
        self.assertTrue(v5)
        self.assertEqual(len(errs5), 0)

    def test_05_scope_bounds_validation(self) -> None:
        """Verify out-of-scope exclusions and max character ceiling."""
        meta_excluded = {"commercial_boundary_excluded": True}
        v1, errs1, _ = validate_scope_bounds(meta_excluded, "Valid source text")
        self.assertFalse(v1)

        # Exceeds max ceiling
        v2, errs2, _ = validate_scope_bounds({}, "A" * 100, max_char_limit=50)
        self.assertFalse(v2)

    def test_06_screen_pre_gate_latency_and_token_savings(self) -> None:
        """Verify screener executes in < 50ms and assigns averted token estimates."""
        meta = {
            "project": "project_lens",
            "date": "10-07-2026",
            "attendees": "harper Hoffman, Amit Ayer",
        }
        text = "Valid transcript text for the LENS internal debrief meeting session."

        result = screen_pre_gate(meta, text, modality="meeting", strict=True)
        self.assertTrue(result.is_valid)
        self.assertLess(result.elapsed_ms, 50.0)
        self.assertEqual(result.tokens_saved_estimate, 0)

        # Failing run should record estimated saved tokens
        bad_meta = {"project": "", "attendees": ""}
        bad_result = screen_pre_gate(bad_meta, text, modality="meeting", strict=True)
        self.assertFalse(bad_result.is_valid)
        self.assertLess(bad_result.elapsed_ms, 50.0)
        self.assertEqual(bad_result.tokens_saved_estimate, DEFAULT_AVERTED_TOKENS_PER_RUN)

    def test_07_validate_or_raise_raises_pre_gate_error(self) -> None:
        """Verify validate_or_raise raises PreGateValidationError on failure."""
        with self.assertRaises(PreGateValidationError) as ctx:
            validate_or_raise({}, "Too short", strict=True)
        self.assertIn("Pre-gate validation failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
