"""End-to-end lifecycle verification for deterministic CLI process exit."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_exits_promptly_with_non_daemon_thread_and_active_telemetry(tmp_path: Path) -> None:
    """Verify nee.py flushes spans and exits within < 3.0s even if a non-daemon worker thread hangs."""
    trace_file = tmp_path / "traces_e2e.jsonl"
    harness = tmp_path / "run_cli_with_lingering_thread.py"
    harness.write_text(
        f"""
import runpy
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, {repr(str(ROOT))})

from dual_pipeline.otel_engine import init_telemetry

# 1. Initialize telemetry writing to sandbox JSONL
tracer = init_telemetry(jsonl_path=Path({repr(str(trace_file))}))
with tracer.start_as_current_span("ingestion_stage_mock") as span:
    span.set_attribute("pipeline.stage", "UWG_ACTIVATION")

# 2. Spawn a non-daemon thread simulating PyTorch/FlagEmbedding worker pool
def _hung_pool_worker():
    time.sleep(120.0)

t = threading.Thread(target=_hung_pool_worker, name="bge_m3_non_daemon_worker", daemon=False)
t.start()

# 3. Invoke nee.py as __main__ with a fast read-only command ('formats --json')
sys.argv = [{repr(str(ROOT / "nee.py"))}, "formats", "--json"]
runpy.run_path({repr(str(ROOT / "nee.py"))}, run_name="__main__")
""",
        encoding="utf-8",
    )

    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(harness)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    elapsed = time.monotonic() - t0

    assert proc.returncode == 0, f"Unexpected returncode {proc.returncode}: stderr={proc.stderr}"
    assert elapsed < 3.0, f"Process hung for {elapsed:.2f}s despite deterministic exit guard"

    # Verify stdout JSON payload is intact and flushed
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(proc.stdout.lstrip())
    assert isinstance(payload, (dict, list))

    # Verify telemetry span was flushed to disk before exit
    assert trace_file.is_file(), "Expected telemetry JSONL file to be flushed before exit"
    lines = [line.strip() for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 1
    span_record = json.loads(lines[0])
    assert span_record["name"] == "ingestion_stage_mock"
    assert span_record["attributes"].get("pipeline.stage") == "UWG_ACTIVATION"
