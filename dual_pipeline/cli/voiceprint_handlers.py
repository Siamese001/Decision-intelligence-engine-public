"""CLI handler for 'nee learn-voiceprints' command."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from dual_pipeline.audio_ingestion_core.voiceprint_harvester import (
    harvest_voiceprints_from_directory,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import VoiceprintStore

logger = logging.getLogger(__name__)


def _backfill_speaker_styles(input_dir: Path, project: Optional[str], max_meetings: Optional[int]) -> int:
    if not input_dir.is_dir():
        return 0
    turns = 0
    try:
        from dual_pipeline.speaker_memory import get_speaker_memory
        mem = get_speaker_memory()
        proj_low = project.strip().lower() if project else None
        for md_file in sorted(input_dir.glob("**/01_*_hitl_resolved_transcript.md"))[: max_meetings or 500]:
            if proj_low and proj_low not in str(md_file).lower():
                continue
            turns += mem.learn_from_resolved_transcript(md_file.read_text(encoding="utf-8"), persist=False)
        if turns > 0:
            mem.save()
    except Exception as exc:
        logger.debug("Speaker style backfill skipped: %s", exc)
    return turns


def handle_learn_voiceprints(args: argparse.Namespace) -> int:
    """Executes voiceprint learning and backfill across historical meeting archives."""
    input_dir = Path(getattr(args, "input_dir", "output"))
    project = getattr(args, "project", None)
    scan_only = bool(getattr(args, "scan_only", False) or getattr(args, "dry_run", False))
    db_path = getattr(args, "db_path", None)
    max_meetings = getattr(args, "max_meetings", None)

    print(f"🎙️  Harvesting speaker voiceprints from {input_dir} (project={project or 'ALL'}, scan_only={scan_only})...")

    store = VoiceprintStore(db_path=db_path) if db_path else VoiceprintStore()
    summary = harvest_voiceprints_from_directory(
        root_dir=input_dir,
        project=project,
        store=store,
        dry_run=scan_only,
        max_meetings=max_meetings,
    )

    scanned = summary.get("total_meetings_scanned", 0)
    enrolled = summary.get("total_speakers_enrolled", 0)
    style_turns = 0 if scan_only else _backfill_speaker_styles(input_dir, project, max_meetings)
    summary["total_style_turns_learned"] = style_turns

    print(f"✅ Harvester finished: scanned {scanned} meeting(s), enrolled {enrolled} speaker voiceprint(s), learned {style_turns} style turn(s).")
    summary["status"] = "COMPLETE"
    return summary


def add_learn_voiceprints_parser(subparsers: Any) -> None:
    """Registers the 'learn-voiceprints' subcommand with arguments."""
    command = subparsers.add_parser(
        "learn-voiceprints",
        help="Harvest speaker voiceprints from past meeting audio and Output 01 transcripts",
    )
    command.add_argument("--project", "-p", default=None, help="Target project filter (e.g. project_beacon, nea)")
    command.add_argument("--input-dir", "-i", type=Path, default="output", help="Root directory containing meeting folders (default: output)")
    command.add_argument("--scan-only", action="store_true", default=False, help="Scan and analyze meetings without enrolling into database")
    command.add_argument("--db-path", type=Path, default=None, help="Path to voiceprint SQLite database")
    command.add_argument("--max-meetings", type=int, default=None, help="Maximum number of meetings to scan")
    command.add_argument("--json", action="store_true", default=False, help="Emit harvest summary as JSON")

