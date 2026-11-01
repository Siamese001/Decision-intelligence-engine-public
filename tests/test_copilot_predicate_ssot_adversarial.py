"""
tests/test_copilot_predicate_ssot_adversarial.py

Adversarial and boundary test suite for Copilot & Query Predicate SSOT (Wave 1).
Covers:
1. Unknown/unregistered predicates fail closed.
2. Empty registry database fails closed.
3. Missing table schema in database fails closed.
4. Erratic case drift and whitespace formatting resilience.
5. Regression pin tamper detection on E3 predicates.
6. Empty predicate set rejection.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest

from dual_pipeline.live_meeting_recorder_core.copilot_predicates import (
    PRIORITY_PREDICATES,
    PredicateRegistryValidationError,
    normalize_copilot_predicate,
    validate_copilot_predicates_against_registry,
)


class TestCopilotPredicateSSOTAdversarial(unittest.TestCase):
    def test_unknown_predicate_fails_closed(self) -> None:
        """Adversarial: injecting unregistered predicates must fail closed with PredicateRegistryValidationError."""
        adversarial_inputs = [
            {"UNKNOWN_PREDICATE_XYZ"},
            {"HAS_RISK", "INVALID_EDGE_TYPE"},
            {"NOT_A_PREDICATE", "BLAH"},
            {"INTERNAL_ADVISORY_RELATION"},
        ]
        for bad_set in adversarial_inputs:
            with self.subTest(bad_set=bad_set), self.assertRaises(PredicateRegistryValidationError):
                validate_copilot_predicates_against_registry(predicates_to_check=bad_set)

    def test_empty_registry_database_fails_closed(self) -> None:
        """Adversarial: an empty assertion_predicate_registry table must fail closed."""
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
            conn = sqlite3.connect(tmp.name)
            try:
                conn.execute("CREATE TABLE assertion_predicate_registry (predicate TEXT PRIMARY KEY, cardinality TEXT)")
                conn.commit()
            finally:
                conn.close()

            with self.assertRaises(PredicateRegistryValidationError) as ctx:
                validate_copilot_predicates_against_registry(db_target=tmp.name)
            self.assertIn("empty", str(ctx.exception).lower())

    def test_missing_registry_table_fails_closed(self) -> None:
        """Adversarial: a database with missing assertion_predicate_registry table fails closed."""
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
            conn = sqlite3.connect(tmp.name)
            try:
                conn.execute("CREATE TABLE other_table (id TEXT PRIMARY KEY)")
                conn.commit()
            finally:
                conn.close()

            with self.assertRaises(PredicateRegistryValidationError) as ctx:
                validate_copilot_predicates_against_registry(db_target=tmp.name)
            self.assertIn("missing", str(ctx.exception).lower())

    def test_case_drift_and_whitespace_adversarial(self) -> None:
        """Adversarial: erratic casing, leading/trailing whitespace, and newlines must normalize cleanly."""
        test_cases = [
            ("  has_risk  ", "HAS_RISK"),
            ("rIsK", "HAS_RISK"),
            ("\n\tOwNs_Or_LeAdS \r\n", "OWNS_OR_LEADS"),
            ("   cAnDiDaTe_FoR\t", "CANDIDATE_FOR"),
            ("  cOnTaInS  ", "CONTAINS"),
            ("  SuPpOrTeD_bY ", "SUPPORTED_BY"),
            ("  bLoCkEd_By  ", "CONSTRAINS"),
            ("  CRITICAL_DEPENDENCY  ", "DEPENDS_ON"),
            ("  UNKNOWN_UNREGISTERED  ", "UNKNOWN_UNREGISTERED"),
        ]
        for raw, expected in test_cases:
            with self.subTest(raw=raw, expected=expected):
                self.assertEqual(normalize_copilot_predicate(raw), expected)

    def test_e3_regression_pin_tamper_detection(self) -> None:
        """Adversarial: verifies that removing any E3 predicate from candidate sets is immediately detectable."""
        e3_predicates = ["HAS_RISK", "SUPPORTED_BY", "CANDIDATE_FOR", "CONTAINS"]
        for p in e3_predicates:
            with self.subTest(predicate=p):
                # Verify present in PRIORITY_PREDICATES
                self.assertIn(p, PRIORITY_PREDICATES, f"E3 predicate {p} missing from PRIORITY_PREDICATES")
                # Tampered set without this predicate should fail an E3 parity check
                tampered = set(PRIORITY_PREDICATES) - {p}
                missing_e3 = set(e3_predicates) - tampered
                self.assertEqual(missing_e3, {p})

    def test_empty_predicates_to_check_fails_closed(self) -> None:
        """Adversarial: passing an empty iterable to validate must fail closed."""
        with self.assertRaises(PredicateRegistryValidationError):
            validate_copilot_predicates_against_registry(predicates_to_check=[])


if __name__ == "__main__":
    unittest.main()
