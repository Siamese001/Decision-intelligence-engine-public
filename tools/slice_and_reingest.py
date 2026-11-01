#!/usr/bin/env python3
"""
tools/slice_and_reingest.py

Governed audio slicing and multi-segment re-ingestion utility.
Uses ContainerValidator for pre/post-flight container structural validation,
derives precise slice cutpoints via ffmpeg, and prepares governed intake payloads.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.audio_ingestion import ensure_ffmpeg
from dual_pipeline.audio_ingestion_core.container_validator import (
    validate_audio_container_headers,
)
from dual_pipeline.meeting_paths import standardize_meeting_slug


def parse_timestamp_to_seconds(ts: Union[str, float, int]) -> float:
    """Parses HH:MM:SS, MM:SS, or raw seconds into float seconds."""
    if isinstance(ts, (int, float)):
        if ts < 0:
            raise ValueError(f"Timestamp cannot be negative: {ts}")
        return float(ts)

    ts_str = str(ts).strip()
    if not ts_str:
        raise ValueError("Timestamp string cannot be empty")

    if ts_str.startswith("-"):
        raise ValueError(f"Timestamp cannot be negative: {ts_str}")

    parts = ts_str.split(":")
    for p in parts:
        if p.strip().startswith("-"):
            raise ValueError(f"Timestamp components cannot be negative: {ts_str}")

    if len(parts) == 1:
        val = float(parts[0])
        if val < 0:
            raise ValueError(f"Timestamp cannot be negative: {val}")
        return val
    elif len(parts) == 2:
        minutes, seconds = float(parts[0]), float(parts[1])
        if minutes < 0 or seconds < 0:
            raise ValueError(f"Timestamp components cannot be negative: {ts_str}")
        return minutes * 60.0 + seconds
    elif len(parts) == 3:
        hours, minutes, seconds = float(parts[0]), float(parts[1]), float(parts[2])
        if hours < 0 or minutes < 0 or seconds < 0:
            raise ValueError(f"Timestamp components cannot be negative: {ts_str}")
        return hours * 3600.0 + minutes * 60.0 + seconds
    else:
        raise ValueError(f"Invalid timestamp format: {ts_str}")


def format_seconds_to_hh_mm_ss(sec: float) -> str:
    """Formats float seconds to HH:MM:SS."""
    total_sec = max(0, int(round(sec)))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def slice_audio(
    input_file: Path,
    output_file: Path,
    start_sec: float,
    end_sec: Optional[float] = None,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """
    Slices audio file using ffmpeg to a governed 16kHz mono PCM WAV.
    Validates output container headers upon completion.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{start_sec:.3f}",
    ]
    if end_sec is not None:
        cmd.extend(["-to", f"{end_sec:.3f}"])

    cmd.extend([
        "-i",
        str(input_file),
        "-c:a",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_file),
    ])

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to slice audio (code {res.returncode}):\n{res.stderr[-500:]}"
        )

    # Validate output container
    validate_audio_container_headers(output_file)
    return output_file


def process_audio_slices(
    input_file: Path,
    slices: List[Dict[str, Any]],
    output_dir: Optional[Path] = None,
    dry_run: bool = False,
    reingest: bool = False,
    ffmpeg_bin: str = "ffmpeg",
) -> List[Dict[str, Any]]:
    """
    Validates input master file and extracts specified segments into dedicated audio slices.
    """
    input_path = Path(input_file).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input audio file not found: {input_path}")

    # Validate input master container
    input_spec = validate_audio_container_headers(input_path)
    dest_dir = Path(output_dir).resolve() if output_dir else input_path.parent

    results: List[Dict[str, Any]] = []

    for idx, s in enumerate(slices, start=1):
        start_raw = s.get("start_time", 0.0)
        end_raw = s.get("end_time")

        start_sec = parse_timestamp_to_seconds(start_raw)
        end_sec = parse_timestamp_to_seconds(end_raw) if end_raw is not None else None

        if end_sec is not None and end_sec <= start_sec:
            raise ValueError(f"Slice {idx}: end_time ({end_sec}s) must be greater than start_time ({start_sec}s)")

        project = str(s.get("project") or "").strip()
        if not project:
            from dual_pipeline.transcript_metadata_helper import _resolve_project
            project = _resolve_project(None, s, input_path.stem, meeting_name=s.get("meeting")) or "internal_consulting"
        meeting = str(s.get("meeting") or f"segment_{idx}").strip()
        attendees = s.get("attendees") or ""
        date_str = str(s.get("date") or "").strip()

        slug = standardize_meeting_slug(meeting, attendees=attendees, max_len=40, project_name=project)
        out_filename = f"{input_path.stem}_part{idx}_{slug}.wav"
        out_path = dest_dir / out_filename

        record = {
            "slice_index": idx,
            "project": project,
            "meeting": meeting,
            "attendees": attendees,
            "date": date_str,
            "start_time": format_seconds_to_hh_mm_ss(start_sec),
            "end_time": format_seconds_to_hh_mm_ss(end_sec) if end_sec is not None else "EOF",
            "start_seconds": start_sec,
            "end_seconds": end_sec,
            "output_path": str(out_path),
            "status": "PENDING",
        }

        if dry_run:
            record["status"] = "PLANNED"
            if reingest:
                record["reingest_command"] = (
                    f"nee ingest-meeting --input \"{out_path}\" --project \"{project}\" "
                    f"--meeting \"{meeting}\" {f'--date \"{date_str}\"' if date_str else ''} "
                    f"{f'--attendees \"{attendees}\"' if attendees else ''}"
                ).strip()
            results.append(record)
            continue

        slice_audio(input_path, out_path, start_sec, end_sec, ffmpeg_bin=ffmpeg_bin)
        out_spec = validate_audio_container_headers(out_path)
        record["status"] = "SLICED"
        record["container_spec"] = {
            "format": out_spec.format_name,
            "sample_rate": out_spec.sample_rate,
            "channels": out_spec.channels,
            "data_size_bytes": out_spec.data_size_bytes,
        }

        if reingest:
            record["reingest_command"] = (
                f"nee ingest-meeting --input \"{out_path}\" --project \"{project}\" "
                f"--meeting \"{meeting}\" {f'--date \"{date_str}\"' if date_str else ''} "
                f"{f'--attendees \"{attendees}\"' if attendees else ''}"
            ).strip()
            record["status"] = "SLICED_AND_READY_FOR_REINGEST"

        results.append(record)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Governed audio slicer and multi-meeting ingestion runner.")
    parser.add_argument("--input", "-i", required=True, help="Input master audio file path.")
    parser.add_argument("--slices", "-s", required=True, help="JSON string or file path containing list of slice definitions.")
    parser.add_argument("--output-dir", "-o", help="Destination directory for audio slices.")
    parser.add_argument("--reingest", action="store_true", help="Formulate governed re-ingestion commands for each slice.")
    parser.add_argument("--receipt", help="Path to write the output execution receipt JSON.")

    args = parser.parse_args()

    ensure_ffmpeg()

    # Load slices
    slices_arg = args.slices.strip()
    if os.path.exists(slices_arg):
        with open(slices_arg, "r", encoding="utf-8") as f:
            slices_data = json.load(f)
    else:
        try:
            slices_data = json.loads(slices_arg)
        except Exception as e:
            print(f"❌ Failed to parse --slices as JSON: {e}", file=sys.stderr)
            return 1

    if not isinstance(slices_data, list) or len(slices_data) == 0:
        print("❌ Slices specification must be a non-empty list of slice objects.", file=sys.stderr)
        return 1

    input_file = Path(args.input).resolve()
    try:
        results = process_audio_slices(
            input_file=input_file,
            slices=slices_data,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            reingest=args.reingest,
        )
    except Exception as e:
        print(f"❌ Audio slicing failed: {e}", file=sys.stderr)
        return 1

    summary = {
        "status": "SUCCESS",
        "input_file": str(input_file),
        "total_slices": len(results),
        "slices": results,
    }

    if args.receipt:
        receipt_path = Path(args.receipt).resolve()
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"✅ Receipt written to {receipt_path}")

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
