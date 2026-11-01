#!/usr/bin/env python3
"""Unit tests for git changeset blast radius and scope confinement validator."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.lint_blast_radius import (
    FileDiffStat,
    evaluate_blast_radius,
    load_scope_manifest,
    parse_numstat_line,
    parse_numstat_output,
)


class BlastRadiusValidatorUnitTests(unittest.TestCase):
    def test_parse_numstat_line(self) -> None:
        stat = parse_numstat_line("12\t5\ttools/foo.py")
        self.assertIsNotNone(stat)
        self.assertEqual(stat.path, "tools/foo.py")
        self.assertEqual(stat.added, 12)
        self.assertEqual(stat.deleted, 5)
        self.assertEqual(stat.total_churn, 17)
        self.assertFalse(stat.is_binary)

        bin_stat = parse_numstat_line("-\t-\tassets/image.png")
        self.assertIsNotNone(bin_stat)
        self.assertTrue(bin_stat.is_binary)
        self.assertEqual(bin_stat.total_churn, 0)

        self.assertIsNone(parse_numstat_line(""))
        self.assertIsNone(parse_numstat_line("invalid line"))

    def test_parse_numstat_output(self) -> None:
        raw = "10\t2\tdual_pipeline/core.py\n20\t0\tstorage/credentials.py\n"
        stats = parse_numstat_output(raw)
        self.assertEqual(len(stats), 2)
        self.assertEqual(stats[0].path, "dual_pipeline/core.py")
        self.assertEqual(stats[1].path, "storage/credentials.py")

    def test_scope_confinement_allowed(self) -> None:
        stats = [
            FileDiffStat("tools/lint_a.py", 10, 2),
            FileDiffStat("tests/test_a.py", 25, 0),
        ]
        report = evaluate_blast_radius(stats, declared_scopes=["tools/*", "tests/*"])
        self.assertTrue(report.is_compliant)
        self.assertEqual(len(report.out_of_scope_files), 0)
        self.assertEqual(report.total_churn, 37)

    def test_scope_confinement_detects_out_of_scope(self) -> None:
        stats = [
            FileDiffStat("tools/lint_a.py", 10, 2),
            FileDiffStat("pipeline_a_intake/unintended.py", 50, 10),
        ]
        report = evaluate_blast_radius(stats, declared_scopes=["tools/*"])
        self.assertFalse(report.is_compliant)
        self.assertEqual(len(report.out_of_scope_files), 1)
        self.assertIn("pipeline_a_intake/unintended.py", report.out_of_scope_files)

    def test_protected_path_violation(self) -> None:
        stats = [
            FileDiffStat(".github/workflows/deploy.yml", 5, 2),
        ]
        report = evaluate_blast_radius(stats)
        self.assertFalse(report.is_compliant)
        self.assertEqual(len(report.protected_path_violations), 1)
        self.assertIn(".github/workflows/deploy.yml", report.protected_path_violations)

    def test_churn_limit_detection(self) -> None:
        stats = [
            FileDiffStat("tools/large_file.py", 600, 500),  # churn = 1100
        ]
        report = evaluate_blast_radius(stats, max_churn=1000)
        self.assertFalse(report.is_compliant)
        self.assertTrue(report.is_churn_exceeded)
        self.assertEqual(report.total_churn, 1100)

    def test_file_count_limit_detection(self) -> None:
        stats = [FileDiffStat(f"src/mod_{i}.py", 1, 0) for i in range(15)]
        report = evaluate_blast_radius(stats, max_files=10)
        self.assertFalse(report.is_compliant)
        self.assertTrue(report.is_file_limit_exceeded)
        self.assertEqual(report.total_files, 15)

    def test_load_scope_manifest(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"allowed_scopes": ["tools/*", "storage/*"]}, f)
            temp_path = Path(f.name)

        try:
            scopes = load_scope_manifest(temp_path)
            self.assertEqual(scopes, ["tools/*", "storage/*"])
        finally:
            temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
