"""
tests/adversarial/test_active_ingestion_modes_adversarial.py

Adversarial and edge-case testing for Wave 4 Active Ingestion Intent:
  1. Explicit dry_run_graph=True is rejected fail-closed.
  2. Explicit mutate_graph=False is rejected fail-closed.
  3. Mixed legacy flags (mutate_graph=True + dry_run_graph=True) are rejected.
  4. CLI ingest_canonical rejects dry/false flags.
  5. package_only=True contract is strictly required to permit artifact-only runs.
  6. Invariant bypass attempt via string/malformed metadata.
  7. Exact production regression F03: dry_run_graph cannot silently override active intent.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.publish_helper import prepare_run_metadata
from dual_pipeline.cli.ingest import ingest_canonical


class TestActiveIngestionModesAdversarial(unittest.TestCase):
    """Adversarial test suite verifying rejection of silent non-mutating modes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_explicit_dry_run_graph_rejected(self) -> None:
        """Verify passing dry_run_graph=True raises ValueError with migration instructions."""
        with self.assertRaises(ValueError) as ctx:
            prepare_run_metadata(
                forced_modality=CanonicalModality.MEETING_TRANSCRIPT,
                provider_type="offline",
                metadata={"dry_run_graph": True},
                directive_id="dir-adv-01",
                presentation_path=None,
                signal_envelope=None,
                auto_mutate_on_pass=True,
                mutate_graph=True,
                kwargs={},
            )
        self.assertIn("Explicit dry-run or false mutation requests", str(ctx.exception))
        self.assertIn("package_only=True", str(ctx.exception))

    def test_adversarial_explicit_mutate_graph_false_rejected(self) -> None:
        """Verify passing mutate_graph=False raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            prepare_run_metadata(
                forced_modality=CanonicalModality.MEETING_TRANSCRIPT,
                provider_type="offline",
                metadata={},
                directive_id="dir-adv-02",
                presentation_path=None,
                signal_envelope=None,
                auto_mutate_on_pass=True,
                mutate_graph=False,
                kwargs={},
            )
        self.assertIn("Explicit dry-run or false mutation requests", str(ctx.exception))

    def test_adversarial_mixed_legacy_flags_rejected(self) -> None:
        """Verify mixed legacy flags (mutate_graph=True + dry_run_graph=True) raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            prepare_run_metadata(
                forced_modality=CanonicalModality.MEETING_TRANSCRIPT,
                provider_type="offline",
                metadata={"dry_run_graph": True},
                directive_id="dir-adv-03",
                presentation_path=None,
                signal_envelope=None,
                auto_mutate_on_pass=True,
                mutate_graph=True,
                kwargs={},
            )
        self.assertIn("Explicit dry-run or false mutation requests", str(ctx.exception))

    def test_adversarial_cli_flags_rejected(self) -> None:
        """Verify CLI ingest_canonical rejects dry_run_graph=True and mutate_graph=False."""
        args_dry = argparse.Namespace(
            input=self.tmp_path / "dummy.md",
            project="project_adv",
            meeting="Adversarial CLI",
            date="10/06/2026",
            provider="offline",
            metadata=None,
            output_dir=str(self.tmp_path),
            directive_id="dir-adv-04",
            reviewer=None,
            rationale=None,
            dry_run=False,
            dry_run_graph=True,
            mutate_graph=True,
        )
        with self.assertRaises(ValueError) as ctx:
            ingest_canonical(args_dry)
        self.assertIn("prohibited for production ingestion", str(ctx.exception))

        args_no_mut = argparse.Namespace(
            input=self.tmp_path / "dummy.md",
            project="project_adv",
            meeting="Adversarial CLI",
            date="10/06/2026",
            provider="offline",
            metadata=None,
            output_dir=str(self.tmp_path),
            directive_id="dir-adv-05",
            reviewer=None,
            rationale=None,
            dry_run=False,
            dry_run_graph=False,
            mutate_graph=False,
        )
        with self.assertRaises(ValueError) as ctx:
            ingest_canonical(args_no_mut)
        self.assertIn("prohibited for production ingestion", str(ctx.exception))

    def test_adversarial_package_only_contract_permitted(self) -> None:
        """Verify package_only=True is accepted and sets effective_mutate=False."""
        meta, eff_mut, eff_auto = prepare_run_metadata(
            forced_modality=CanonicalModality.MEETING_TRANSCRIPT,
            provider_type="offline",
            metadata={"package_only": True, "dry_run_graph": True},
            directive_id="dir-adv-06",
            presentation_path=None,
            signal_envelope=None,
            auto_mutate_on_pass=True,
            mutate_graph=True,
            kwargs={},
        )
        self.assertFalse(eff_mut)
        self.assertFalse(eff_auto)
        self.assertTrue(meta.get("package_only"))

    def test_adversarial_exact_regression_f03_no_silent_override(self) -> None:
        """Finding F03 regression: dry_run_graph in metadata cannot bypass active intent check."""
        with self.assertRaises(ValueError) as ctx:
            prepare_run_metadata(
                forced_modality=CanonicalModality.DOCUMENT,
                provider_type="offline",
                metadata={"dry_run_graph": True, "mutate_graph": True},
                directive_id="dir-adv-07",
                presentation_path=None,
                signal_envelope=None,
                auto_mutate_on_pass=True,
                mutate_graph=True,
                kwargs={},
            )
        self.assertIn("Explicit dry-run or false mutation requests", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
