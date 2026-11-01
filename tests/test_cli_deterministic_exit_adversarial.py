"""Adversarial and edge-case tests for CLI deterministic process termination and telemetry shutdown."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import dual_pipeline.otel_engine as otel_engine
import nee

ROOT = Path(__file__).resolve().parents[1]


def test_shutdown_telemetry_when_uninitialized_is_noop() -> None:
    """Category 1 (Boundary payload): shutdown_telemetry before initialization returns True cleanly."""
    with otel_engine._INIT_LOCK:
        otel_engine._TRACER_INITIALIZED = False
        otel_engine._ACTIVE_PROVIDER = None
    assert otel_engine.shutdown_telemetry(timeout_millis=100) is True
    # Repeated idempotent call
    assert otel_engine.shutdown_telemetry(timeout_millis=100) is True


def test_shutdown_telemetry_bounds_hung_exporter() -> None:
    """Category 4 (Overflow & timeout exhaustion): stalled exporter during shutdown times out cleanly."""

    class _HungProvider:
        def force_flush(self, timeout_millis: int = 30000) -> bool:
            time.sleep(5.0)
            return True

        def shutdown(self) -> None:
            time.sleep(5.0)

    with otel_engine._INIT_LOCK:
        otel_engine._TRACER_INITIALIZED = True
        otel_engine._ACTIVE_PROVIDER = _HungProvider()  # type: ignore[assignment]

    t0 = time.monotonic()
    ok = otel_engine.shutdown_telemetry(timeout_millis=150)
    elapsed = time.monotonic() - t0

    assert ok is False
    assert elapsed < 1.0, f"shutdown_telemetry did not bound hung provider (took {elapsed:.2f}s)"
    assert otel_engine._TRACER_INITIALIZED is False
    assert otel_engine._ACTIVE_PROVIDER is None


def test_shutdown_telemetry_handles_exporter_exception() -> None:
    """Category 2 (Malformed / broken state): provider raising RuntimeError during flush/shutdown is caught."""

    class _BrokenProvider:
        def force_flush(self, timeout_millis: int = 30000) -> bool:
            raise RuntimeError("broken pipe on OTLP socket")

        def shutdown(self) -> None:
            raise RuntimeError("already closed")

    with otel_engine._INIT_LOCK:
        otel_engine._TRACER_INITIALIZED = True
        otel_engine._ACTIVE_PROVIDER = _BrokenProvider()  # type: ignore[assignment]

    ok = otel_engine.shutdown_telemetry(timeout_millis=500)
    assert ok is False
    assert otel_engine._TRACER_INITIALIZED is False


def test_concurrent_shutdown_telemetry_race_safety(tmp_path: Path) -> None:
    """Category 3 (Concurrency & race): 8 threads calling shutdown_telemetry concurrently."""
    otel_engine.init_telemetry(jsonl_path=tmp_path / "race_traces.jsonl")
    results: list[bool] = []
    lock = threading.Lock()

    def _worker() -> None:
        res = otel_engine.shutdown_telemetry(timeout_millis=500)
        with lock:
            results.append(res)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert len(results) == 8
    assert all(isinstance(r, bool) for r in results)
    assert otel_engine._TRACER_INITIALIZED is False


def test_nonzero_exit_code_preserved_under_lingering_non_daemon_thread(tmp_path: Path) -> None:
    """Category 7 (Exact production regression): preserve exact exit code when non-daemon threads linger."""
    harness = tmp_path / "run_nonzero_exit.py"
    harness.write_text(
        f"""
import sys
import threading
import time

sys.path.insert(0, {repr(str(ROOT))})
import nee

def _lingering():
    time.sleep(60.0)

t = threading.Thread(target=_lingering, daemon=False)
t.start()

nee._terminate_cli_process(42)
""",
        encoding="utf-8",
    )

    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(harness)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    elapsed = time.monotonic() - t0
    assert proc.returncode == 42
    assert elapsed < 2.5


def test_clean_exit_uses_sys_exit_when_no_lingering_threads() -> None:
    """Category 6 (Rollback & clean teardown): when no non-daemon threads exist, standard sys.exit is invoked."""
    with (
        patch.object(nee, "_has_lingering_non_daemon_threads", return_value=False),
        patch("os._exit") as mock_os_exit,
        patch("sys.exit", side_effect=SystemExit(0)) as mock_sys_exit,
    ):
        try:
            nee._terminate_cli_process(0)
        except SystemExit as exc:
            assert exc.code == 0
        mock_sys_exit.assert_called_once_with(0)
        mock_os_exit.assert_not_called()
