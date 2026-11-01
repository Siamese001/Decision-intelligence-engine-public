#!/usr/bin/env python3
"""tools/stress_test_live_recording_harness.py

Diagnostic stress harness for live meeting recording pipeline.
Probes for:
1. Worktree chunk staging file read/write race conditions under heavy concurrency.
2. Dropped or skipped audio chunks under rapid intake and queue pressure.
3. Shutdown boundary drain failures, trailing stubs, and stitch guarantees.
4. Lossless frame and duration continuity across staging migrations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import validate_final_chunk
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    _copy_chunk_if_missing,
    migrate_staged_files,
)


def generate_pcm_chunk(path: Path, num_samples: int = 16000, freq: float = 440.0) -> int:
    """Writes a valid 16kHz 16-bit mono PCM WAV chunk and returns sample count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        samples = [int(1500 * math.sin(2 * math.pi * freq * i / 16000)) for i in range(num_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))
    return num_samples


def run_stress_cycle(
    num_chunks: int = 50,
    concurrency: int = 4,
    inject_active_growth: bool = True,
    temp_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Executes an isolated concurrency stress cycle."""
    t0 = time.perf_counter()
    own_temp = False
    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="rec_stress_"))
        own_temp = True

    src_dir = temp_dir / "staging_src"
    dst_dir = temp_dir / "worktree_dst"
    master_wav = temp_dir / "master.wav"
    src_dir.mkdir(parents=True, exist_ok=True)
    dst_dir.mkdir(parents=True, exist_ok=True)

    errors: List[str] = []
    stop_pollers = threading.Event()
    chunks_created = 0
    total_input_frames = 0

    def poller_worker(worker_id: int) -> None:
        while not stop_pollers.is_set():
            try:
                migrate_staged_files(src_dir, dst_dir, [], is_final=False)
            except Exception as e:
                errors.append(f"Poller-{worker_id}: {type(e).__name__}: {e}")
            time.sleep(0.005)

    poll_threads = [threading.Thread(target=poller_worker, args=(i,)) for i in range(concurrency)]
    for pt in poll_threads:
        pt.start()

    # Generate chunks
    for i in range(num_chunks):
        cf = src_dir / f"chunk_{i:05d}.wav"
        n_samples = 4000 + (i % 5) * 2000
        n = generate_pcm_chunk(cf, num_samples=n_samples, freq=300.0 + (i * 10))
        total_input_frames += n
        chunks_created += 1

        if inject_active_growth and i % 10 == 0:
            # Simulate FFmpeg active append
            with open(cf, "ab") as f:
                f.write(b"\x00" * 400)
            total_input_frames += 200  # 400 bytes = 200 int16 samples
        time.sleep(0.005)

    time.sleep(0.02)
    stop_pollers.set()
    for pt in poll_threads:
        pt.join(timeout=3.0)

    # Final drain migration
    try:
        migrate_staged_files(src_dir, dst_dir, [], is_final=True)
    except Exception as e:
        errors.append(f"FinalDrain: {type(e).__name__}: {e}")

    # Validate final chunk
    try:
        validate_final_chunk(dst_dir, segment_seconds=1)
    except Exception as e:
        errors.append(f"ValidateFinalChunk: {type(e).__name__}: {e}")

    # Stitch master WAV
    stitch_receipt = None
    actual_frames = 0
    try:
        stitch_receipt = AudioMasterStitcher.stitch_master_wav(dst_dir, master_wav, pad_missing_or_corrupt=True)
        with wave.open(str(master_wav), "rb") as mw:
            actual_frames = mw.getnframes()
    except Exception as e:
        errors.append(f"StitchMasterWAV: {type(e).__name__}: {e}")

    elapsed_s = time.perf_counter() - t0
    orphan_tmps = len(list(dst_dir.glob(".tmp_*")))
    migrated_chunks = len(list(dst_dir.glob("chunk_*.wav")))

    result = {
        "status": "PASS" if not errors and orphan_tmps == 0 and migrated_chunks == num_chunks else "FAIL",
        "elapsed_seconds": round(elapsed_s, 4),
        "chunks_created": chunks_created,
        "chunks_migrated": migrated_chunks,
        "orphan_tmp_files": orphan_tmps,
        "total_input_frames": total_input_frames,
        "master_wav_frames": actual_frames,
        "frame_delta": actual_frames - total_input_frames,
        "stitch_status": stitch_receipt.status if stitch_receipt else "NONE",
        "padded_chunks": stitch_receipt.padded_chunks if stitch_receipt else -1,
        "errors": errors,
    }

    if own_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Recording Concurrency & Lossless Stress Harness")
    parser.add_argument("--chunks", type=int, default=50, help="Number of chunks per cycle (default: 50)")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of poller threads (default: 4)")
    parser.add_argument("--cycles", type=int, default=1, help="Number of sequential stress cycles (default: 1)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON results")
    args = parser.parse_args()

    results: List[Dict[str, Any]] = []
    overall_pass = True

    for cycle in range(1, args.cycles + 1):
        r = run_stress_cycle(num_chunks=args.chunks, concurrency=args.concurrency)
        r["cycle"] = cycle
        results.append(r)
        if r["status"] != "PASS":
            overall_pass = False

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Executed {len(results)} stress cycle(s): {'PASS' if overall_pass else 'FAIL'}")
        for r in results:
            print(f"  Cycle {r['cycle']}: status={r['status']} migrated={r['chunks_migrated']}/{r['chunks_created']} "
                  f"tmps={r['orphan_tmp_files']} stitch={r['stitch_status']} pad={r['padded_chunks']} elapsed={r['elapsed_seconds']}s")
            if r["errors"]:
                for err in r["errors"]:
                    print(f"    ERROR: {err}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
