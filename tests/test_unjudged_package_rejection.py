"""
tests/test_unjudged_package_rejection.py

Wave 2 Verification: Asserts that '--allow-unjudged-package' and '--mock' are purged
from public CLI parsers, and that intake publication strictly rejects unjudged packages.
"""
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.release_policy import verify_authoritative_release_gate


class TestUnjudgedPackageRejection(unittest.TestCase):
    """Test suite asserting rejection of unjudged packages and purged mock flags."""

    def test_cli_rejects_allow_unjudged_package_flag(self):
        """Verify that '--allow-unjudged-package' is no longer recognized by the CLI parser."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as exc_info:
            parser.parse_args([
                "intake-publish",
                "--allow-unjudged-package",
                "--package", "dummy.json",
            ])
        self.assertEqual(exc_info.exception.code, 1)

    def test_cli_rejects_mock_flag_on_record(self):
        """Verify that '--mock' is no longer recognized on 'record-meeting' command."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as exc_info:
            parser.parse_args([
                "record-meeting",
                "--mock",
            ])
        self.assertEqual(exc_info.exception.code, 1)

    def test_verify_authoritative_release_gate_rejects_unjudged_package(self):
        """Verify that verify_authoritative_release_gate rejects packages lacking judge evaluation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_dir = Path(tmpdir)
            pkg_file = pkg_dir / "intake_package.json"
            # Write a package claiming evidence envelope schema
            pkg_file.write_text(
                '{"contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0", "items": []}',
                encoding="utf-8",
            )

            # verify_authoritative_release_gate must fail closed because final_release_decision.json is missing
            with self.assertRaises(RuntimeError) as exc_info:
                verify_authoritative_release_gate([pkg_file], allow_unjudged=False)
            self.assertIn("Missing mandatory release authority receipt", str(exc_info.exception))
            self.assertIn("GraphDB mutation blocked by fail-closed release authority", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
