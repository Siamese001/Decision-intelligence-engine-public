#!/usr/bin/env python3
"""Fast tests for content-bound sidecar-validation caching."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.index import (  # noqa: E402
    _cached_sidecar_validation,
    validate_sidecar,
)


class SidecarValidationCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-sidecar-cache-")
        self.sidecar = Path(self.temp.name) / "sidecar.sqlite"
        self.sidecar.write_bytes(b"immutable-sidecar-v1")
        _cached_sidecar_validation.cache_clear()

    def tearDown(self) -> None:
        _cached_sidecar_validation.cache_clear()
        self.temp.cleanup()

    @staticmethod
    def _validation(path: Path, **_expected: object) -> dict:
        return {
            "status": "PASS",
            "integrity": {"integrity_check": "ok"},
            "metadata": {"path": str(path)},
        }

    def test_exact_content_reuses_validation_and_returns_independent_results(self) -> None:
        with mock.patch(
            "dual_pipeline.index._validate_sidecar_uncached",
            side_effect=self._validation,
        ) as uncached:
            first = validate_sidecar(self.sidecar)
            first["metadata"]["caller_mutation"] = True
            second = validate_sidecar(self.sidecar)

        self.assertEqual(1, uncached.call_count)
        self.assertNotIn("caller_mutation", second["metadata"])

    def test_changed_bytes_force_a_new_full_validation(self) -> None:
        with mock.patch(
            "dual_pipeline.index._validate_sidecar_uncached",
            side_effect=self._validation,
        ) as uncached:
            validate_sidecar(self.sidecar)
            self.sidecar.write_bytes(b"immutable-sidecar-v2")
            validate_sidecar(self.sidecar)

        self.assertEqual(2, uncached.call_count)

    def test_expected_pair_bindings_are_part_of_the_cache_key(self) -> None:
        with mock.patch(
            "dual_pipeline.index._validate_sidecar_uncached",
            side_effect=self._validation,
        ) as uncached:
            validate_sidecar(
                self.sidecar,
                expected_generation_id="generation-one",
            )
            validate_sidecar(
                self.sidecar,
                expected_generation_id="generation-two",
            )

        self.assertEqual(2, uncached.call_count)

    def test_change_during_validation_fails_closed_and_clears_cache(self) -> None:
        def mutate(path: Path, **_expected: object) -> dict:
            result = self._validation(path)
            path.write_bytes(b"changed-during-validation")
            return result

        with mock.patch(
            "dual_pipeline.index._validate_sidecar_uncached",
            side_effect=mutate,
        ), self.assertRaisesRegex(RuntimeError, "changed while"):
            validate_sidecar(self.sidecar)

        self.assertEqual(0, _cached_sidecar_validation.cache_info().currsize)


if __name__ == "__main__":
    unittest.main()
