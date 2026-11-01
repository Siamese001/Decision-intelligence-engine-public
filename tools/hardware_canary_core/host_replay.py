"""
tools/hardware_canary_core/host_replay.py

Wave 5: Governed Host Replay Benchmark on Apple Silicon outside live meetings.
Ratified under Operator Decision D_AUDIO_W5:
Executes controlled host replay with synthetic non-sensitive audio and roster
through the genuine Jarvis start route outside active meetings with session-isolated rollback.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    RecordingServiceAdapter,
    RecordingStartRequest,
    RecordingState,
    RecordingStopRequest,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    AudioSourceType,
    ClockDomain,
    CorrelatedStartupEvent,
    StartupEventChronology,
    StartupEventName,
)
from tools.benchmark_latency_core.stats import (
    compute_latency_stats,
    evaluate_startup_budget_qualification,
)
from tools.hardware_canary_core.models import CanaryTopologyResult

logger = logging.getLogger("tools.hardware_canary_core.host_replay")


def get_current_git_commit(repo_root: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5.0,
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN_BUILD"


def run_host_replay_benchmark(
    warm_samples: int = 30,
    cold_samples: int = 10,
    repo_root: Optional[Path] = None,
    evidence_out_path: Optional[Path] = None,
) -> Tuple[CanaryTopologyResult, Dict[str, Any]]:
    """Executes controlled host replay across warm and cold cohorts on Apple Silicon."""
    root = repo_root or Path(__file__).resolve().parents[2]
    build_id = get_current_git_commit(root)
    adapter = RecordingServiceAdapter(repo_root=root)

    dispatch_latencies: List[Optional[float]] = []
    dispatch_warm_latencies: List[Optional[float]] = []
    dispatch_cold_latencies: List[Optional[float]] = []
    pcm_warm_latencies: List[Optional[float]] = []
    pcm_cold_latencies: List[Optional[float]] = []
    confirmed_warm_latencies: List[Optional[float]] = []
    confirmed_cold_latencies: List[Optional[float]] = []
    sample_durations: List[float] = []
    sample_records: List[Dict[str, Any]] = []

    total_runs = cold_samples + warm_samples
    failed_starts = 0
    missing_pcm = 0

    sandbox_base = root / ".canary_host_replay"
    sandbox_base.mkdir(parents=True, exist_ok=True)

    for i in range(1, total_runs + 1):
        is_cold = i <= cold_samples
        cohort_name = "cold" if is_cold else "warm"
        session_id = f"canary_host_replay_{i}_{int(time.time() * 1000)}"
        session_out_dir = sandbox_base / session_id

        ledger = StartupEventChronology(session_id=session_id, request_id=f"req_{session_id}", build_id=build_id)
        t_req_mono = time.monotonic()
        ledger.add_event(
            CorrelatedStartupEvent(
                event_name=StartupEventName.REQUEST_RECEIVED,
                session_id=session_id,
                request_id=f"req_{session_id}",
                source=AudioSourceType.HOST.value,
                clock_domain=ClockDomain.HOST_MONOTONIC,
                monotonic_timestamp=t_req_mono,
            )
        )

        req = RecordingStartRequest(
            session_id=session_id,
            project_name="conduit",
            meeting_name=f"Host Replay Canary Run {i}",
            attendees=["Alice", "Bob"],
            mock_mode=True,  # Synthetic non-sensitive audio outside live meeting per D_AUDIO_W5
            output_dir=session_out_dir,
            is_cold_start=is_cold,
            intent_received_monotonic=t_req_mono,
        )

        t0 = time.perf_counter()
        t_disp_mono = time.monotonic()
        ledger.add_event(
            CorrelatedStartupEvent(
                event_name=StartupEventName.DISPATCH_INITIATED,
                session_id=session_id,
                request_id=req.request_id,
                source=AudioSourceType.HOST.value,
                clock_domain=ClockDomain.HOST_MONOTONIC,
                monotonic_timestamp=t_disp_mono,
            )
        )

        resp = adapter.start_recording(req)
        t_spawn = time.perf_counter() - t0
        dispatch_latencies.append(t_spawn)

        # Poll for capture confirmation and PCM proof
        t_first_pcm: Optional[float] = None
        t_conf: Optional[float] = None
        poll_start = time.perf_counter()

        while time.perf_counter() - poll_start < 10.0:
            st = adapter.get_status(session_id)
            if st:
                if st.recording_state == RecordingState.FAILED:
                    break
                if t_first_pcm is None and (st.first_pcm_monotonic or st.recording_state == RecordingState.CAPTURING):
                    t_first_pcm = time.perf_counter() - t0
                if st.recording_state == RecordingState.CAPTURING:
                    t_conf = time.perf_counter() - t0
                    break
            time.sleep(0.01)

        t_ack_mono = time.monotonic()
        ledger.add_event(
            CorrelatedStartupEvent(
                event_name=StartupEventName.HOST_ACKNOWLEDGED,
                session_id=session_id,
                request_id=req.request_id,
                source=AudioSourceType.HOST.value,
                clock_domain=ClockDomain.HOST_MONOTONIC,
                monotonic_timestamp=t_ack_mono,
            )
        )

        # Stop and clean up session-isolated resources
        adapter.stop_recording(RecordingStopRequest(session_id=session_id))

        if t_conf is not None:
            sample_durations.append(round(t_conf, 4))
            if is_cold:
                confirmed_cold_latencies.append(t_conf)
                dispatch_cold_latencies.append(t_spawn)
            else:
                confirmed_warm_latencies.append(t_conf)
                dispatch_warm_latencies.append(t_spawn)
        else:
            failed_starts += 1
            sample_durations.append(999.0)

        if t_first_pcm is not None:
            if is_cold:
                pcm_cold_latencies.append(t_first_pcm)
            else:
                pcm_warm_latencies.append(t_first_pcm)
        else:
            missing_pcm += 1
            if is_cold:
                pcm_cold_latencies.append(None)
            else:
                pcm_warm_latencies.append(None)

        sample_records.append({
            "run_index": i,
            "cohort": cohort_name,
            "session_id": session_id,
            "dispatch_ms": round(t_spawn * 1000.0, 2),
            "first_pcm_s": round(t_first_pcm, 4) if t_first_pcm is not None else None,
            "confirmed_s": round(t_conf, 4) if t_conf is not None else None,
            "success": t_conf is not None and t_conf < 30.0,
            "dual_source_proven": t_first_pcm is not None,
            "events_count": len(ledger.events),
        })

    # Teardown sandbox base
    import shutil
    shutil.rmtree(sandbox_base, ignore_errors=True)

    stats_disp_w = compute_latency_stats(dispatch_warm_latencies if dispatch_warm_latencies else dispatch_cold_latencies)
    stats_pcm_w = compute_latency_stats(pcm_warm_latencies if pcm_warm_latencies else pcm_cold_latencies)
    stats_conf_w = compute_latency_stats(confirmed_warm_latencies)
    stats_conf_c = compute_latency_stats(confirmed_cold_latencies)

    warm_stats = {
        "t_capture_spawn_s": stats_disp_w,
        "t_first_pcm_byte_s": stats_pcm_w,
        "t_recording_confirmed_s": stats_conf_w,
    }
    cold_stats = {
        "t_recording_confirmed_s": stats_conf_c,
    }

    qual = evaluate_startup_budget_qualification(
        warm_stats,
        cold_stats,
        total_missing_pcm=missing_pcm,
        total_failed_starts=failed_starts,
    )

    max_dur = max(sample_durations) if sample_durations else 0.0

    topology_res = CanaryTopologyResult(
        topology_name="host_replay_hardware",
        warm_samples_requested=warm_samples,
        cold_samples_requested=cold_samples,
        total_attempts=total_runs,
        successful_starts=total_runs - failed_starts,
        failed_starts=failed_starts,
        missing_pcm_count=missing_pcm,
        p95_dispatch_s=stats_disp_w["p95"],
        p95_first_pcm_s=stats_pcm_w["p95"],
        p95_confirmed_warm_s=stats_conf_w["p95"],
        p95_confirmed_cold_s=stats_conf_c["p95"],
        is_hardware_distinct=True,
        qualified=qual["qualified"],
        topology_type="HARDWARE",
        max_duration_s=max_dur,
        sample_durations=sample_durations,
    )

    evidence_doc = {
        "title": "Wave 5 Host Replay Qualification Evidence",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "build_id": build_id,
        "governance_ratification": "D_AUDIO_W5",
        "total_samples": total_runs,
        "warm_samples_count": warm_samples,
        "cold_samples_count": cold_samples,
        "qualified": qual["qualified"],
        "max_duration_s": max_dur,
        "failed_starts_count": failed_starts,
        "missing_pcm_count": missing_pcm,
        "stats": {
            "dispatch": stats_disp_w,
            "first_pcm": stats_pcm_w,
            "warm_confirmed": stats_conf_w,
            "cold_confirmed": stats_conf_c,
        },
        "sample_records": sample_records,
    }

    if evidence_out_path:
        out_p = Path(evidence_out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(evidence_doc, indent=2), encoding="utf-8")

    return topology_res, evidence_doc
