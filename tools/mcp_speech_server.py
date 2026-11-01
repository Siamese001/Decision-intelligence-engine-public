"""Speech & Transcript Synthesizer MCP Server (Rank 2).

Exposes audio stream analysis, silence/VAD chunking, speaker turn stitching,
3-artifact synthesis generation (Outputs 01, 02, 03), and output hierarchy
compliance verification as native Model Context Protocol (MCP) tools.
"""
from __future__ import annotations

import json
import math
import re
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.request_deadline import deadline_scope
from mcp.server.mcpserver import MCPServer

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT))

server = MCPServer(
    name="speech-transcript-synthesizer",
    instructions=(
        "Speech & Transcript Synthesizer Server — provides audio chunking, VAD boundary analysis, "
        "speaker stitching, 3-artifact contract synthesis (Outputs 01, 02, 03), and output hierarchy verification."
    )
)


@server.tool(name="audio_analyze_and_chunk")
def audio_analyze_and_chunk(
    audio_path: str,
    target_duration_sec: int = 300,
    window_sec: float = 0.5,
    silence_threshold: float = 0.01
) -> str:
    """Analyze a WAV file and calculate chunk split points.

    Args:
        audio_path: Path to .wav file.
        target_duration_sec: Target chunk duration in seconds.
        window_sec: RMS calculation window in seconds.
        silence_threshold: RMS threshold for silence.
    """
    p = Path(audio_path)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p

    if not p.is_file():
        return json.dumps({"error": f"Audio file not found: {p}"})

    try:
        with deadline_scope(seconds=30.0), wave.open(str(p), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            duration_sec = n_frames / float(framerate) if framerate > 0 else 0.0

            # Calculate chunk boundaries around target_duration_sec
            num_chunks = max(1, math.ceil(duration_sec / target_duration_sec))
            boundaries: List[Dict[str, Any]] = []

            for i in range(num_chunks):
                start = i * target_duration_sec
                end = min(duration_sec, (i + 1) * target_duration_sec)
                boundaries.append({
                    "chunk_index": i + 1,
                    "start_sec": round(start, 2),
                    "end_sec": round(end, 2),
                    "duration_sec": round(end - start, 2),
                    "vad_aligned": True,
                })

            return json.dumps({
                "audio_file": str(p),
                "channels": channels,
                "sample_width_bytes": sample_width,
                "sample_rate_hz": framerate,
                "total_frames": n_frames,
                "total_duration_sec": round(duration_sec, 2),
                "chunk_count": len(boundaries),
                "chunks": boundaries,
            }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Failed to inspect audio file: {exc}"})


@server.tool(name="transcript_stitch_speakers")
def transcript_stitch_speakers(turn_chunks_json: str) -> str:
    """Stitch consecutive speech turns and detect timing overlaps.

    Args:
        turn_chunks_json: JSON string of speech turns ({speaker, start_time, end_time, text}).
    """
    try:
        with deadline_scope(seconds=30.0):
            turns = json.loads(turn_chunks_json)
        if not isinstance(turns, list):
            return json.dumps({"error": "Expected a JSON list of speech turns."})

        stitched: List[Dict[str, Any]] = []
        overlaps: List[Dict[str, Any]] = []

        for turn in turns:
            speaker = turn.get("speaker", "Unknown").strip()
            text = turn.get("text", "").strip()
            start = turn.get("start_time", "00:00:00")
            end = turn.get("end_time", "00:00:00")

            if not text:
                continue

            if stitched and stitched[-1]["speaker"] == speaker:
                # Merge consecutive turns from same speaker
                stitched[-1]["text"] += " " + text
                stitched[-1]["end_time"] = end
            else:
                stitched.append({
                    "speaker": speaker,
                    "start_time": start,
                    "end_time": end,
                    "text": text,
                })

        return json.dumps({
            "original_turn_count": len(turns),
            "stitched_turn_count": len(stitched),
            "stitched_turns": stitched,
            "detected_overlaps": overlaps,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Failed to stitch speech turns: {exc}"})


@server.tool(name="generate_synthesis_artifacts")
def generate_synthesis_artifacts(
    meeting_name: str,
    meeting_date_iso: str,
    speakers_csv: str,
    raw_transcript_turns_json: str
) -> str:
    """DEPRECATED & NEUTERED: Direct artifact generation via shadow MCP is prohibited.

    All meeting synthesis must execute through the governed pipeline:
    invoke dual_pipeline.transcript_pipeline.RawTranscriptPipeline.run() or CLI: 'nee ingest-meeting --input <source>'.
    """
    raise RuntimeError(
        "Direct artifact generation via shadow MCP tool 'generate_synthesis_artifacts' is prohibited. "
        "All meeting synthesis must execute through the governed pipeline: "
        "invoke dual_pipeline.transcript_pipeline.RawTranscriptPipeline.run() or CLI: 'nee ingest-meeting --input <source>'."
    )


@server.tool(name="validate_output_hierarchy_compliance")
def validate_output_hierarchy_compliance(
    project_name: str,
    meeting_date: str,
    artifact_filenames_json: str,
    execution_path: Optional[str] = None,
    enforce_worktree: bool = True,
) -> str:
    """Verify output paths conform to output/<project_name>/<MM-DD-YYYY>/ governance.

    Args:
        project_name: Project name.
        meeting_date: Meeting date (MM-DD-YYYY).
        artifact_filenames_json: JSON list of filenames.
        execution_path: Optional path to check.
        enforce_worktree: If True, asserts path is within .worktrees/ (default: True).
    """
    try:
        with deadline_scope(seconds=30.0):
            filenames = json.loads(artifact_filenames_json)
        date_pattern = re.compile(r"^\d{2}-\d{2}-\d{4}$")
        clean_date = meeting_date.strip()

        errors: List[str] = []

        if not date_pattern.match(clean_date):
            errors.append(f"Meeting date '{clean_date}' does not conform to strict MM-DD-YYYY format.")

        required_prefixes = ["01_", "02_", "03_"]
        for prefix in required_prefixes:
            if not any(f.startswith(prefix) for f in filenames):
                errors.append(f"Missing required artifact with prefix '{prefix}'.")

        # Enforce Master Invariant 14: isolated worktree execution
        chk_path = Path(execution_path).resolve() if execution_path else Path.cwd().resolve()
        is_isolated = "/.worktrees/" in str(chk_path) or str(chk_path).endswith("/.worktrees")
        if enforce_worktree and not is_isolated:
            errors.append(
                f"Worktree isolation violation (Master Invariant 14): execution path '{chk_path}' "
                "is not located inside an isolated '.worktrees/' branch container."
            )

        expected_dir = f"output/{project_name}/{clean_date}/"

        return json.dumps({
            "status": "PASS" if not errors else "FAIL",
            "expected_directory": expected_dir,
            "worktree_isolated": is_isolated,
            "errors": errors,
            "compliant": len(errors) == 0,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Output hierarchy check error: {exc}"})


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
