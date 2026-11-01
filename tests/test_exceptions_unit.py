"""Unit tests for dual_pipeline.exceptions hierarchy.

Verifies:
1. Root DualPipelineError inheritance.
2. Multiple inheritance with builtin ValueError and RuntimeError.
3. ProviderError subclassing.
4. Catch semantics preserving backward compatibility with builtin except clauses.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.exceptions import (
    DualPipelineError,
    DualPipelineRuntimeError,
    DualPipelineValueError,
    ProviderError,
)


class TestExceptionsUnit(unittest.TestCase):
    """Unit tests for dual-pipeline exception hierarchy."""

    def test_root_exception_hierarchy(self) -> None:
        """Root DualPipelineError inherits from Exception."""
        err = DualPipelineError("generic pipeline error")
        self.assertIsInstance(err, Exception)
        self.assertEqual(str(err), "generic pipeline error")

    def test_value_error_multiple_inheritance(self) -> None:
        """DualPipelineValueError inherits from DualPipelineError and builtin ValueError."""
        err = DualPipelineValueError("invalid argument format")
        self.assertIsInstance(err, DualPipelineError)
        self.assertIsInstance(err, ValueError)

        # Verify catching as builtin ValueError
        caught_builtin = False
        try:
            raise err
        except ValueError as exc:
            caught_builtin = True
            self.assertEqual(str(exc), "invalid argument format")
        self.assertTrue(caught_builtin)

        # Verify catching as DualPipelineError
        caught_pipeline = False
        try:
            raise err
        except DualPipelineError as exc:
            caught_pipeline = True
            self.assertEqual(str(exc), "invalid argument format")
        self.assertTrue(caught_pipeline)

    def test_runtime_error_multiple_inheritance(self) -> None:
        """DualPipelineRuntimeError inherits from DualPipelineError and builtin RuntimeError."""
        err = DualPipelineRuntimeError("runtime service failed")
        self.assertIsInstance(err, DualPipelineError)
        self.assertIsInstance(err, RuntimeError)

        caught_builtin = False
        try:
            raise err
        except RuntimeError as exc:
            caught_builtin = True
            self.assertEqual(str(exc), "runtime service failed")
        self.assertTrue(caught_builtin)

    def test_provider_error_hierarchy(self) -> None:
        """ProviderError inherits from DualPipelineRuntimeError and RuntimeError."""
        err = ProviderError("external model endpoint unreachable")
        self.assertIsInstance(err, ProviderError)
        self.assertIsInstance(err, DualPipelineRuntimeError)
        self.assertIsInstance(err, DualPipelineError)
        self.assertIsInstance(err, RuntimeError)

        caught_runtime = False
        try:
            raise err
        except RuntimeError as exc:
            caught_runtime = True
            self.assertEqual(str(exc), "external model endpoint unreachable")
        self.assertTrue(caught_runtime)


if __name__ == "__main__":
    unittest.main()
