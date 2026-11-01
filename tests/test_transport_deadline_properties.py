"""
tests/test_transport_deadline_properties.py

Hypothesis Property-Based Testing Suite for Transport and Deadline Invariants.
Validates boundary values, malformed timeouts, nested budget invariance,
cooperative backoff bounds, and sibling cancellation propagation.

Governed under Decision H-8 (Property-Based Verification).
"""
from __future__ import annotations

import math
import socket
import threading
import time
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from config.request_deadline import (
    Deadline,
    RequestDeadlineExceeded,
    _positive_seconds,
    check_deadline,
    cooperative_sleep,
    deadline_scope,
    remaining_timeout,
)
from config.transport import SiblingPassAbortedError, execute_with_deadline_and_heartbeat


class TestTransportDeadlineProperties:
    """Property-based verification of transport deadline invariants."""

    @given(
        invalid_val=st.one_of(
            st.floats(max_value=0.0),
            st.just(float("nan")),
            st.just(float("inf")),
            st.just(float("-inf")),
            st.booleans(),
            st.text(),
        )
    )
    @settings(max_examples=50)
    def test_property_boundary_seconds_validation(self, invalid_val):
        """Boundary invariant: Non-finite or non-positive timeouts fail closed with ValueError."""
        with pytest.raises(ValueError):
            _positive_seconds(invalid_val)

    @given(
        parent_dur=st.floats(min_value=0.2, max_value=2.0),
        child_dur=st.floats(min_value=0.05, max_value=5.0),
    )
    @settings(max_examples=30)
    def test_property_nested_deadline_cannot_exceed_parent(self, parent_dur, child_dur):
        """Monotonic budget invariant: Nested deadline cannot extend parent budget."""
        with deadline_scope(parent_dur) as parent:
            assert parent is not None
            parent_rem_before = parent.remaining()

            with deadline_scope(child_dur) as child:
                assert child is not None
                child_rem = child.remaining()
                # Child remaining must be bounded by both parent and child durations
                assert child_rem <= child_dur + 0.05
                assert child_rem <= parent_rem_before + 0.05

    @given(sleep_budget=st.floats(min_value=0.01, max_value=0.05))
    @settings(max_examples=20)
    def test_property_cooperative_sleep_respects_deadline(self, sleep_budget):
        """Cooperative backoff invariant: sleep consumes budget without escaping scope."""
        with deadline_scope(0.2) as dl:
            assert dl is not None
            start = time.monotonic()
            cooperative_sleep(sleep_budget)
            elapsed = time.monotonic() - start
            assert elapsed >= sleep_budget * 0.8
            assert dl.remaining() <= 0.2 - (sleep_budget * 0.7)

    def test_property_cooperative_sleep_aborts_on_cancelled_deadline(self):
        """Cancellation invariant: cancelled deadline unblocks cooperative_sleep immediately."""
        with pytest.raises(RequestDeadlineExceeded):
            with deadline_scope(10.0) as dl:
                assert dl is not None

                def cancel_soon():
                    time.sleep(0.05)
                    dl.cancel()

                th = threading.Thread(target=cancel_soon, daemon=True)
                th.start()

                start = time.monotonic()
                try:
                    cooperative_sleep(5.0)
                finally:
                    elapsed = time.monotonic() - start
                    assert elapsed < 0.5

    @given(num_sockets=st.integers(min_value=1, max_value=10))
    @settings(max_examples=15)
    def test_property_socket_registration_and_abort(self, num_sockets):
        """Socket hygiene invariant: cancel() aborts all active registered sockets."""
        dl = Deadline(expires_at=time.monotonic() + 10.0)
        mock_socks = []
        for _ in range(num_sockets):
            s = MagicMock(spec=socket.socket)
            mock_socks.append(s)
            dl.register_socket(s)

        aborted_count = dl.abort_sockets()
        assert aborted_count == num_sockets
        for s in mock_socks:
            s.shutdown.assert_called_once_with(socket.SHUT_RDWR)
            s.close.assert_called_once()
        # Set should now be empty
        assert len(dl._sockets) == 0

    def test_property_sibling_abort_propagation(self):
        """Sibling task invariant: setting abort_event raises SiblingPassAbortedError promptly."""
        abort_event = threading.Event()
        abort_event.set()

        with pytest.raises(SiblingPassAbortedError, match="aborted because a sibling task failed"):
            execute_with_deadline_and_heartbeat(
                lambda: time.sleep(1.0),
                deadline_seconds=5.0,
                abort_event=abort_event,
                operation_name="Sibling Abort Test",
            )
