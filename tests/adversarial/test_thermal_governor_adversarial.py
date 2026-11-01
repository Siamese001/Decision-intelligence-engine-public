"""Adversarial and Edge-Case Test Suite for Darwin Thermal Pressure Governor (Wave 5).

Covers 4 mandatory adversarial categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: Boundary thermal levels (NOMINAL=0, CRITICAL=3), zero check intervals.
2. Malformed input: Out-of-bounds raw integer states (-1, 999), exceptions raised inside callbacks.
3. Concurrency & race: Concurrent multi-threaded thermal polling across 16 threads.
4. Invariant bypass attempt: Verifying audio_priority_preserved invariant cannot be dropped.
"""
from __future__ import annotations

import threading
from unittest import TestCase
from unittest.mock import patch

from dual_pipeline.runtime.thermal_governor import (
    DarwinThermalGovernor,
    ThermalSnapshot,
    ThermalState,
)


class TestThermalGovernorAdversarial(TestCase):
    """Adversarial and edge-case tests for Wave 5 thermal governor."""

    def test_boundary_thermal_state_limits(self) -> None:
        """Verifies boundary states 0 (NOMINAL) and 3 (CRITICAL) produce correct flags."""
        governor = DarwinThermalGovernor(check_interval_sec=0.0)

        # Lower bound
        governor.set_override_state(ThermalState.NOMINAL)
        snap_min = governor.poll_thermal_state(force=True)
        self.assertEqual(snap_min.raw_level, 0)
        self.assertFalse(snap_min.should_throttle_background)

        # Upper bound
        governor.set_override_state(ThermalState.CRITICAL)
        snap_max = governor.poll_thermal_state(force=True)
        self.assertEqual(snap_max.raw_level, 3)
        self.assertTrue(snap_max.should_throttle_background)

    def test_malformed_out_of_bounds_raw_states(self) -> None:
        """Verifies invalid or unknown integer thermal states safely fall back to NOMINAL."""
        governor = DarwinThermalGovernor(check_interval_sec=0.0)

        for invalid_val in [-5, 4, 100, 9999]:
            with patch.object(governor, "_query_native_thermal_state", return_value=invalid_val):
                snap = governor.poll_thermal_state(force=True)
                self.assertEqual(snap.state, ThermalState.NOMINAL, f"Invalid value {invalid_val} must fallback to NOMINAL")
                self.assertFalse(snap.should_throttle_background)

    def test_malformed_callback_exception_isolation(self) -> None:
        """Verifies an exception in a user callback does not crash governor or interrupt polling."""
        governor = DarwinThermalGovernor(check_interval_sec=0.0)

        def failing_callback(_snap: ThermalSnapshot) -> None:
            raise RuntimeError("Simulated callback explosion")

        governor.register_callback(failing_callback)
        governor.set_override_state(ThermalState.SERIOUS)

        # Must not raise RuntimeError
        snap = governor.poll_thermal_state(force=True)
        self.assertEqual(snap.state, ThermalState.SERIOUS)
        self.assertTrue(snap.should_throttle_background)

    def test_concurrency_race_multi_threaded_polling(self) -> None:
        """Verifies 16 concurrent threads polling governor complete without race conditions."""
        governor = DarwinThermalGovernor(check_interval_sec=0.01)
        snapshots: list[ThermalSnapshot] = []
        lock = threading.Lock()

        def worker(thread_id: int) -> None:
            for _ in range(20):
                # Dynamically alternate states to stress transitions
                state = ThermalState.SERIOUS if thread_id % 2 == 0 else ThermalState.NOMINAL
                governor.set_override_state(state)
                snap = governor.poll_thermal_state(force=True)
                with lock:
                    snapshots.append(snap)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(snapshots), 320)
        for s in snapshots:
            self.assertIn(s.state, [ThermalState.NOMINAL, ThermalState.SERIOUS])

    def test_invariant_bypass_audio_priority_preserved(self) -> None:
        """Verifies audio_priority_preserved is guaranteed across all possible thermal states."""
        governor = DarwinThermalGovernor(check_interval_sec=0.0)

        for state in [ThermalState.NOMINAL, ThermalState.FAIR, ThermalState.SERIOUS, ThermalState.CRITICAL]:
            governor.set_override_state(state)
            snap = governor.poll_thermal_state(force=True)
            self.assertTrue(
                snap.audio_priority_preserved,
                f"Audio priority must never be dropped under thermal state {state.name}",
            )
