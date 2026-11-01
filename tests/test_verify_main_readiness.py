#!/usr/bin/env python3
"""Tests for verify_main_readiness entrypoint and unified readiness execution."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.readiness_core import ROOT
from tools.verify_main_readiness import verify_main_readiness


class TestVerifyMainReadiness(unittest.TestCase):
    """Test suite for verify_main_readiness entrypoint and CLI qualification."""

    def test_dry_run_simulation(self) -> None:
        """Dry run simulation mode succeeds with exit code 0."""
        rc = verify_main_readiness(
            dry_run=True,
            parallel_gates=False,
            allow_behind=True,
        )
        self.assertEqual(rc, 0)

    def test_invalid_gate_out_of_range(self) -> None:
        """Specifying an invalid gate index returns exit code 1."""
        rc = verify_main_readiness(
            gate=999,
            allow_behind=True,
        )
        self.assertEqual(rc, 1)

    def test_gate_negative_out_of_range(self) -> None:
        """Specifying a negative gate index returns exit code 1."""
        rc = verify_main_readiness(
            gate=-1,
            allow_behind=True,
        )
        self.assertEqual(rc, 1)

    def test_single_gate_execution(self) -> None:
        """Specifying a valid single gate executes only that gate."""
        with patch("tools.verify_main_readiness.run_stage") as mock_run:
            mock_run.return_value = (True, "mock gate 1 passed", 0.05)
            rc = verify_main_readiness(
                gate=1,
                use_cache=False,
                allow_behind=True,
            )
            self.assertEqual(rc, 0)
            mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
