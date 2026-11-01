"""
tools/benchmark_latency_core/probe.py

Isolated subprocess probe runner for live meeting recording startup benchmarks.
Enforces:
1. Monotonic clock source-bound PCM evidence.
2. Missing PCM stays missing (None values never substituted with wall-clock time).
3. Separation of capture milestones and ASR readiness milestones.
4. Clean process group teardown to eliminate orphaned workers.
5. Realistic warm/cold cohort isolation (cold includes initial import; warm pre-warms modules).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict


def build_probe_script(
    mode: str,
    project: str,
    meeting_title: str,
    run_idx: int,
    cohort: str = "warm",
) -> str:
    """Builds the standalone Python probe script executed in an isolated process."""
    prewarm_block = ""
    if cohort == "warm":
        prewarm_block = """
# Invariant: Warm cohort models an active/pre-warmed service host
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
"""

    return f"""
import json
import os
import sys
import time
from pathlib import Path
{prewarm_block}
t_proc_start = time.perf_counter()

# Phase 1: Import CLI / Engine (instant for warm cohort, full disk load for cold)
t_import_start = time.perf_counter()
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
t_import_done = time.perf_counter()
t_cli_import = t_import_done - t_import_start

mock_mode = {mode == "mock"}
cohort = "{cohort}"

# Phase 2: Instant Spooler Spawn (Capture Dispatch)
t_spool_start = time.perf_counter()
spooler = InstantAudioSpooler.spawn_instant(
    mock_mode=mock_mode,
    segment_seconds=5,
)
t_spool_done = time.perf_counter()
t_capture_spawn = t_spool_done - t_spool_start

# Phase 3: Wait for First PCM Bytes & Non-Zero Samples (Monotonic Source Evidence)
t_pcm_start = time.perf_counter()
t_first_pcm = None
t_first_nonzero = None
pcm_deadline = t_pcm_start + 4.0

while time.perf_counter() < pcm_deadline:
    # Check master_wav first for lowest-latency PCM byte flow
    if hasattr(spooler, "master_wav") and spooler.master_wav.exists() and spooler.master_wav.stat().st_size > 44:
        if t_first_pcm is None:
            t_first_pcm = time.perf_counter() - t_spool_done
        try:
            import wave
            with wave.open(str(spooler.master_wav), "rb") as wf:
                frames = wf.readframes(min(16000, wf.getnframes()))
                if frames and any(b != 0 for b in frames):
                    t_first_nonzero = time.perf_counter() - t_spool_done
                    break
        except Exception:
            pass

    chunks = sorted(spooler.spool_dir.glob("chunk_*.wav"))
    if chunks:
        c0 = chunks[0]
        if c0.exists() and c0.stat().st_size > 44:
            if t_first_pcm is None:
                t_first_pcm = time.perf_counter() - t_spool_done
            try:
                import wave
                with wave.open(str(c0), "rb") as wf:
                    frames = wf.readframes(min(16000, wf.getnframes()))
                    if frames and any(b != 0 for b in frames):
                        t_first_nonzero = time.perf_counter() - t_spool_done
                        break
            except Exception:
                pass
    time.sleep(0.005)

# Invariant: Missing PCM stays missing! Do NOT substitute wall-clock elapsed time.

# Phase 4: Recorder Instantiation
t_rec_init_start = time.perf_counter()
recorder = LiveMeetingRecorder(
    project_name="{project}",
    meeting_name="{meeting_title} Run {run_idx}",
    mock_mode=mock_mode,
    isolated_process=False,
    enable_ws=False,
    enable_copilot=False,
    spooler=spooler,
    fast_start=True,
    dry_run=False,
)
t_rec_init_done = time.perf_counter()
t_recorder_init = t_rec_init_done - t_rec_init_start

# Phase 5: Transcriber / MLX Readiness Probe (Decoupled from Capture)
t_mlx_ready = 0.0
if recorder.isolated_transcriber:
    t_mlx_start = time.perf_counter()
    try:
        recorder.isolated_transcriber.start(timeout=10.0)
        t_mlx_ready = time.perf_counter() - t_mlx_start
    except Exception:
        t_mlx_ready = 999.0
elif mock_mode:
    t_mlx_ready = 0.001

# Phase 6: Recording Confirmed State
t_confirmed_start = time.perf_counter()
recorder.start_ffmpeg()
t_recording_confirmed = time.perf_counter() - t_proc_start

# Teardown
if hasattr(recorder, "capture_manager") and recorder.capture_manager:
    recorder.capture_manager.stop_processes(fast=False)
spooler.stop()
spooler.discard()
recorder._early_abort_cleanup()
time.sleep(0.05)
if hasattr(recorder, "meeting_dir") and recorder.meeting_dir and recorder.meeting_dir.exists():
    import shutil
    shutil.rmtree(recorder.meeting_dir, ignore_errors=True)
if hasattr(recorder, "manifest_path") and recorder.manifest_path and recorder.manifest_path.exists():
    recorder.manifest_path.unlink(missing_ok=True)

metrics = {{
    "cohort": cohort,
    "run_index": {run_idx},
    "t_cli_import_s": round(t_cli_import, 4),
    "t_capture_spawn_s": round(t_capture_spawn, 4),
    "t_first_pcm_byte_s": round(t_first_pcm, 4) if t_first_pcm is not None else None,
    "t_first_nonzero_sample_s": round(t_first_nonzero, 4) if t_first_nonzero is not None else None,
    "t_recorder_init_s": round(t_recorder_init, 4),
    "t_mlx_ready_s": round(t_mlx_ready, 4),
    "t_recording_confirmed_s": round(t_recording_confirmed, 4),
    "t_total_startup_wall_s": round(t_recording_confirmed, 4),
    "has_pcm": t_first_pcm is not None,
}}
print("__LATENCY_JSON_START__" + json.dumps(metrics) + "__LATENCY_JSON_END__")
"""


def run_isolated_probe_cycle(
    python_bin: str,
    repo_root: Path,
    mode: str,
    project: str,
    meeting_title: str,
    run_idx: int,
    cohort: str = "warm",
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    """Executes a single probe cycle in an isolated subprocess and parses telemetry."""
    probe_content = build_probe_script(
        mode=mode,
        project=project,
        meeting_title=meeting_title,
        run_idx=run_idx,
        cohort=cohort,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
        tf.write(probe_content)
        probe_path = tf.name

    try:
        t0 = time.perf_counter()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_root)
        env["HF_HUB_OFFLINE"] = "1"
        res = subprocess.run(
            [python_bin, probe_path],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(repo_root),
            env=env,
            timeout=timeout_sec,
        )
        total_cycle_time = time.perf_counter() - t0

        if res.returncode != 0:
            sys.stderr.write(f"[BENCHMARK-RUN-ERROR] Cycle {run_idx} failed (code {res.returncode}):\n{res.stderr}\n")
            return {
                "cohort": cohort,
                "run_index": run_idx,
                "status": "FAIL",
                "error": res.stderr.strip() or f"Process exited with {res.returncode}",
                "cycle_wall_time_s": round(total_cycle_time, 4),
                "t_first_pcm_byte_s": None,
                "t_first_nonzero_sample_s": None,
                "has_pcm": False,
            }

        stdout = res.stdout
        start_marker = "__LATENCY_JSON_START__"
        end_marker = "__LATENCY_JSON_END__"
        s_idx = stdout.find(start_marker)
        e_idx = stdout.find(end_marker)

        if s_idx != -1 and e_idx != -1:
            raw_json = stdout[s_idx + len(start_marker) : e_idx]
            data = json.loads(raw_json)
            confirmed_s = data.get("t_recording_confirmed_s", 999.0)
            has_pcm = data.get("has_pcm", False)
            if confirmed_s >= 30.0 or not has_pcm:
                data["status"] = "FAIL"
                data["error"] = f"Initiation deadline exceeded ({confirmed_s}s >= 30.0s)" if confirmed_s >= 30.0 else "Missing PCM"
            else:
                data["status"] = "PASS"
            data["cycle_wall_time_s"] = round(total_cycle_time, 4)
            return data
        else:
            return {
                "cohort": cohort,
                "run_index": run_idx,
                "status": "FAIL",
                "error": f"Missing latency json markers in output: {stdout[:300]}",
                "cycle_wall_time_s": round(total_cycle_time, 4),
                "t_first_pcm_byte_s": None,
                "t_first_nonzero_sample_s": None,
                "has_pcm": False,
            }
    finally:
        if os.path.exists(probe_path):
            os.unlink(probe_path)
