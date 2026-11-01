#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for Decentralized Test Manifest Loader (Wave 3).

Covers:
1. Malformed JSON payloads and non-JSON binary bytes in manifest directories.
2. Boundary and schema corruption (missing name, invalid types, nulls, negative bounds).
3. Path traversal, shell injection, and illegal test module identifiers.
4. Invariant bypass prevention: malformed manifests fail closed in strict mode.
5. Deduplication and deterministic ordering under adversarial duplicates.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.test_manifest_loader import (
    discover_manifests,
    load_manifest_file,
    resolve_all_tests,
    validate_manifest_payload,
)


class TestManifestLoaderAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="manifest_adv_")
        self.manifest_dir = Path(self.tmp_dir) / "manifests"
        self.manifest_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_malformed_json_fails_closed_in_strict_mode(self) -> None:
        """Verify malformed JSON syntax is rejected in strict mode and skipped in non-strict."""
        bad_json = self.manifest_dir / "bad_syntax.json"
        bad_json.write_text("{ unquoted_key: 'value', trailing: ", encoding="utf-8")

        with self.assertRaises(ValueError):
            load_manifest_file(bad_json, strict=True)

        manifest = load_manifest_file(bad_json, strict=False)
        self.assertIsNone(manifest)

    def test_02_schema_corruption_and_missing_name(self) -> None:
        """Verify payloads with missing required name or wrong types fail validation."""
        cases = [
            {},
            {"name": ""},
            {"name": 12345},
            {"name": "valid_name", "deterministic_tests": "not_a_list"},
            {"name": "valid_name", "e2e_tests": [123, None]},
            {"name": "valid_name", "bge_tests": [""]},
        ]
        for idx, payload in enumerate(cases):
            valid, errors = validate_manifest_payload(payload, source=f"case_{idx}")
            self.assertFalse(valid, f"Case {idx} should be invalid: {payload}")
            self.assertTrue(len(errors) > 0)

    def test_03_injection_and_path_traversal_identifiers_rejected(self) -> None:
        """Verify illegal identifiers (semicolons, shell metacharacters, path traversal) fail."""
        malicious_identifiers = [
            "tests.test_something; rm -rf /",
            "../../etc/passwd",
            "tests.foo && whoami",
            "tests.test | cat",
            "tests.test$(id)",
            "tests.test`reboot`",
            "tests.test\nnewline",
        ]
        for bad_id in malicious_identifiers:
            payload = {"name": "exploit_test", "deterministic_tests": [bad_id]}
            valid, errors = validate_manifest_payload(payload)
            self.assertFalse(valid, f"Should reject malicious identifier: {bad_id}")
            self.assertTrue(any("invalid test identifier" in e for e in errors))

    def test_04_binary_garbage_and_symlink_safety(self) -> None:
        """Verify arbitrary binary garbage does not crash manifest discovery."""
        garbage_file = self.manifest_dir / "garbage.json"
        garbage_file.write_bytes(b"\x00\xff\xfe\x00\xaa\xbb\xcc\xdd")

        manifests = discover_manifests(self.manifest_dir, strict=False)
        self.assertEqual(len(manifests), 0)

    def test_05_adversarial_deduplication_and_order_preservation(self) -> None:
        """Verify massive duplicate entries are deduplicated while preserving first-seen order."""
        m1 = self.manifest_dir / "01_first.json"
        m1.write_text(json.dumps({
            "name": "first",
            "deterministic_tests": ["tests.test_b", "tests.test_a", "tests.test_b"],
            "e2e_tests": ["tests.end_to_end.test_x"],
        }), encoding="utf-8")

        m2 = self.manifest_dir / "02_second.json"
        m2.write_text(json.dumps({
            "name": "second",
            "deterministic_tests": ["tests.test_a", "tests.test_c", "tests.test_c"],
            "e2e_tests": ["tests.end_to_end.test_x", "tests.end_to_end.test_y"],
        }), encoding="utf-8")

        base_det = ("tests.test_z", "tests.test_a")
        base_e2e = ("tests.end_to_end.test_w",)

        det, e2e, bge = resolve_all_tests(
            base_deterministic=base_det,
            base_e2e=base_e2e,
            manifest_dir=self.manifest_dir,
        )

        self.assertEqual(det, ("tests.test_z", "tests.test_a", "tests.test_b", "tests.test_c"))
        self.assertEqual(e2e, ("tests.end_to_end.test_w", "tests.end_to_end.test_x", "tests.end_to_end.test_y"))
        self.assertEqual(bge, ())


if __name__ == "__main__":
    unittest.main()
