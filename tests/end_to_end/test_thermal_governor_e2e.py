"""End-to-End Acceptance Suite for Darwin Thermal Pressure Governor (Wave 5).

Verifies:
1. Native Darwin thermal state polling retrieves valid system state.
2. Load-shedding triggers when thermal pressure reaches Level 2 (SERIOUS) or Level 3 (CRITICAL).
3. Background batch embeddings are paused under thermal strain while audio capture priority is preserved.
4. Governor telemetry captures poll counts, shed events, and state transitions accurately.
"""
from __future__ import annotations

import time
from unittest import TestCase

from dual_pipeline.runtime.thermal_governor import (
    DarwinThermalGovernor,
    ThermalSnapshot,
    ThermalState,
)


class TestThermalGovernorE2E(TestCase):
    """End-to-end tests for Wave 5 Darwin thermal governor and load-shedding."""

    def test_darwin_thermal_state_polling_native(self) -> None:
        """Verifies native Darwin thermal state polling produces a well-formed ThermalSnapshot."""
        governor = DarwinThermalGovernor(check_interval_sec=0.1)
        snapshot = governor.poll_thermal_state(force=True)

        self.assertIsInstance(snapshot, ThermalSnapshot)
        self.assertIn(snapshot.state, [ThermalState.NOMINAL, ThermalState.FAIR, ThermalState.SERIOUS, ThermalState.CRITICAL])
        self.assertGreater(snapshot.timestamp, 0.0)
        self.assertTrue(snapshot.audio_priority_preserved)

        # Dictionary serialization
        d = snapshot.to_dict()
        self.assertIn("state", d)
        self.assertIn("raw_level", d)
        self.assertIn("should_throttle_background", d)

    def test_thermal_governor_load_shedding_lifecycle(self) -> None:
        """Verifies complete load-shedding lifecycle from nominal to serious, critical, and recovery."""
        governor = DarwinThermalGovernor(check_interval_sec=0.0)

        # 1. Nominal state: tasks admitted
        governor.set_override_state(ThermalState.NOMINAL)
        snap0 = governor.poll_thermal_state(force=True)
        self.assertEqual(snap0.state, ThermalState.NOMINAL)
        self.assertFalse(snap0.should_throttle_background)
        self.assertFalse(snap0.should_pause_embeddings)
        self.assertTrue(governor.can_admit_background_task("batch_embedding"))

        # 2. Fair state: tasks still admitted
        governor.set_override_state(ThermalState.FAIR)
        snap1 = governor.poll_thermal_state(force=True)
        self.assertEqual(snap1.state, ThermalState.FAIR)
        self.assertFalse(snap1.should_throttle_background)
        self.assertTrue(governor.can_admit_background_task("batch_embedding"))

        # 3. Serious state (Level 2): load shed and background tasks rejected
        governor.set_override_state(ThermalState.SERIOUS)
        snap2 = governor.poll_thermal_state(force=True)
        self.assertEqual(snap2.state, ThermalState.SERIOUS)
        self.assertTrue(snap2.should_throttle_background)
        self.assertTrue(snap2.should_pause_embeddings)
        self.assertFalse(governor.can_admit_background_task("batch_embedding"))

        # 4. Critical state (Level 3): load shed persists
        governor.set_override_state(ThermalState.CRITICAL)
        snap3 = governor.poll_thermal_state(force=True)
        self.assertEqual(snap3.state, ThermalState.CRITICAL)
        self.assertTrue(snap3.should_throttle_background)
        self.assertFalse(governor.can_admit_background_task("batch_embedding"))

        # 5. Recovery back to Nominal
        governor.set_override_state(ThermalState.NOMINAL)
        snap_rec = governor.poll_thermal_state(force=True)
        self.assertEqual(snap_rec.state, ThermalState.NOMINAL)
        self.assertFalse(snap_rec.should_throttle_background)
        self.assertTrue(governor.can_admit_background_task("batch_embedding"))

    def test_load_shed_callback_invocation(self) -> None:
        """Verifies registered callbacks are invoked upon thermal escalation."""
        governor = DarwinThermalGovernor(check_interval_sec=0.0)
        invoked_snapshots: list[ThermalSnapshot] = []

        governor.register_callback(lambda s: invoked_snapshots.append(s))

        # Start nominal
        governor.set_override_state(ThermalState.NOMINAL)
        governor.poll_thermal_state(force=True)
        self.assertEqual(len(invoked_snapshots), 0)

        # Escalate to SERIOUS
        governor.set_override_state(ThermalState.SERIOUS)
        governor.poll_thermal_state(force=True)
        self.assertEqual(len(invoked_snapshots), 1)
        self.assertEqual(invoked_snapshots[0].state, ThermalState.SERIOUS)

    def test_telemetry_accounting(self) -> None:
        """Verifies get_telemetry captures operational counters."""
        governor = DarwinThermalGovernor(check_interval_sec=0.0)
        governor.set_override_state(ThermalState.NOMINAL)
        governor.poll_thermal_state(force=True)

        governor.set_override_state(ThermalState.SERIOUS)
        governor.poll_thermal_state(force=True)

        telemetry = governor.get_telemetry()
        self.assertIn("current_state", telemetry)
        self.assertGreaterEqual(telemetry["total_polls"], 2)
        self.assertGreaterEqual(telemetry["shed_events"], 1)
        self.assertEqual(telemetry["check_interval_sec"], 0.0)
