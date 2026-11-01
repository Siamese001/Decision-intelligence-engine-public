"""tests/test_output_hierarchy_adversarial.py

Adversarial unit tests for output hierarchy validation and intake package authorization rules.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.validate_output_hierarchy import _validate_meeting_container


class OutputHierarchyAdversarialTests(unittest.TestCase):
    """Adversarial stress tests for meeting containers and release receipts."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _create_base_triad(self, container: Path, stem: str) -> None:
        container.mkdir(parents=True, exist_ok=True)
        (container / f"01_{stem}_hitl_resolved_transcript.md").write_text("# Transcript", encoding="utf-8")
        (container / f"02_{stem}_executive_recap.md").write_text("# Recap", encoding="utf-8")
        (container / f"03_{stem}_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
        (container / "qa_gates_report.json").write_text("{}", encoding="utf-8")
        (container / "llm_judge_evaluation.json").write_text("{}", encoding="utf-8")

    def test_failed_release_does_not_require_intake_package(self) -> None:
        """When release fails, intake_package.json is forbidden and must not be flagged missing."""
        container = self.root / "project_beacon" / "09-18-2026" / "Sprint_Demo_PartnerAdvisory"
        stem = "Sprint_Demo_PartnerAdvisory_09-18-2026_0930AM_1030AM_ET"
        self._create_base_triad(container, stem)

        (container / "final_release_decision.json").write_text(json.dumps({
            "status": "FAIL",
            "authoritative_write_authorized": False,
            "final_pass": False,
        }), encoding="utf-8")

        violations = _validate_meeting_container(
            container=container,
            proj_name="project_beacon",
            meeting_date="09-18-2026",
            container_label="project_beacon/09-18-2026/Sprint_Demo_PartnerAdvisory",
        )
        self.assertEqual(violations, [])

    def test_advisory_pass_without_authoritative_write_does_not_require_intake_package(self) -> None:
        """PASS_ADVISORY status has authoritative_write_authorized=False; no intake package expected."""
        container = self.root / "project_atlas" / "09-18-2026" / "Rewire_Review_Amit"
        stem = "Rewire_Review_Amit_09-18-2026_1100AM_1200PM_ET"
        self._create_base_triad(container, stem)

        (container / "final_release_decision.json").write_text(json.dumps({
            "status": "PASS_ADVISORY",
            "authoritative_write_authorized": False,
        }), encoding="utf-8")

        violations = _validate_meeting_container(
            container=container,
            proj_name="project_atlas",
            meeting_date="09-18-2026",
            container_label="project_atlas/09-18-2026/Rewire_Review_Amit",
        )
        self.assertEqual(violations, [])

    def test_authorized_release_without_intake_package_flags_missing_receipt(self) -> None:
        """When release is authorized (PASS + authoritative_write_authorized=True), intake_package is required."""
        container = self.root / "project_nexus" / "09-18-2026" / "Nexus_Sync_Jen_Amit"
        stem = "Nexus_Sync_Jen_Amit_09-18-2026_0200PM_0300PM_ET"
        self._create_base_triad(container, stem)

        (container / "final_release_decision.json").write_text(json.dumps({
            "status": "PASS",
            "authoritative_write_authorized": True,
        }), encoding="utf-8")

        violations = _validate_meeting_container(
            container=container,
            proj_name="project_nexus",
            meeting_date="09-18-2026",
            container_label="project_nexus/09-18-2026/Nexus_Sync_Jen_Amit",
        )
        self.assertTrue(any("[MISSING_RECEIPT] Missing intake_package.json" in v for v in violations))

    def test_corrupt_or_empty_decision_does_not_leak_intake_package_requirement(self) -> None:
        """Corrupt or non-JSON final_release_decision.json fails closed and does not require intake package."""
        container = self.root / "project_lens" / "09-18-2026" / "Lens_Catchup_PartnerAdvisory"
        stem = "Lens_Catchup_PartnerAdvisory_09-18-2026_0400PM_0500PM_ET"
        self._create_base_triad(container, stem)

        (container / "final_release_decision.json").write_text("{corrupt-json-truncated...", encoding="utf-8")

        violations = _validate_meeting_container(
            container=container,
            proj_name="project_lens",
            meeting_date="09-18-2026",
            container_label="project_lens/09-18-2026/Lens_Catchup_PartnerAdvisory",
        )
        self.assertFalse(any("Missing intake_package.json" in v for v in violations))


if __name__ == "__main__":
    unittest.main()
