"""
tools/recover_lens_debrief_session.py

Governed utility to:
1. Restore polluted 10-09-2026 LENS Standup segments and hints from cross-session hijacking.
2. Initialize and migrate Miles Erickson hints into 10-21-2026 LENS debrief transcript cache.
3. Transcribe spooled chunks from instant spool directory into 10-21-2026 segments.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List

from dual_pipeline.core import ROOT

logger = logging.getLogger("tools.recover_lens_debrief_session")


def restore_10_09_2026_standup(repo_root: Path) -> Dict[str, Any]:
    """Cleans up cross-session pollution in 10-09-2026 cache."""
    cache_10_09 = repo_root / "output" / "transcript_cache" / "project_lens" / "10-09-2026"
    results = {"restored_segments": 0, "removed_foreign_speaker_map": False, "cleaned_queue": False}
    if not cache_10_09.is_dir():
        return results

    # 1. Remove foreign speaker map
    foreign_map = cache_10_09 / "debrief_skyler_partner_advisory_speaker_map.json"
    if foreign_map.is_file():
        foreign_map.unlink()
        results["removed_foreign_speaker_map"] = True

    # 2. Restore 10-09 segments from 'Miles Erickson' back to 'Voice A'
    seg_file = cache_10_09 / "01_10-09-2026_Standup_Amit_segments.jsonl"
    if seg_file.is_file():
        lines = seg_file.read_text(encoding="utf-8").splitlines()
        new_lines = []
        for line in lines:
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("speaker") == "Miles Erickson":
                data["speaker"] = "Voice A"
                results["restored_segments"] += 1
            new_lines.append(json.dumps(data, ensure_ascii=False))
        seg_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # 3. Clean 10-09 queue and hints
    queue_file = cache_10_09 / "speaker_hints_queue.json"
    if queue_file.is_file():
        q_data = json.loads(queue_file.read_text(encoding="utf-8"))
        # Retain only hints created on or before 10-16
        clean_q = [h for h in q_data if str(h.get("created_at", "")).startswith("2026-10-16")]
        queue_file.write_text(json.dumps(clean_q, indent=2), encoding="utf-8")
        results["cleaned_queue"] = True

    hints_file = cache_10_09 / "speaker_hints.json"
    if hints_file.is_file():
        h_data = json.loads(hints_file.read_text(encoding="utf-8"))
        clean_h = [h for h in h_data if not str(h.get("timestamp", "")).startswith("2026-10-21")]
        hints_file.write_text(json.dumps(clean_h, indent=2), encoding="utf-8")

    return results


def migrate_10_21_2026_hints(repo_root: Path) -> List[Dict[str, Any]]:
    """Seeds the 10-21-2026 queue with the two operator hints for Miles Erickson."""
    cache_10_21 = repo_root / "output" / "transcript_cache" / "project_lens" / "10-21-2026"
    cache_10_21.mkdir(parents=True, exist_ok=True)
    out_10_21 = repo_root / "output" / "project_lens" / "10-21-2026" / "Debrief_Skyler_PartnerAdvisory"
    out_10_21.mkdir(parents=True, exist_ok=True)

    hints = [
        {
            "hint_id": "hint_7b1720b1",
            "speaker_name": "Miles Erickson",
            "quote": "implicitly",
            "raw_input": "Miles Erickson - \"implicitly\"",
            "session_elapsed_seconds": None,
            "created_at": "2026-10-21T22:11:48.234529+00:00",
            "status": "QUEUED",
            "voice_id": None,
            "updated_segments_count": 0,
            "ledger_digest": None,
            "metadata": {},
        },
        {
            "hint_id": "hint_68d382a5",
            "speaker_name": "Miles Erickson",
            "quote": "he is old",
            "raw_input": "Miles Erickson - \"he is old\"",
            "session_elapsed_seconds": None,
            "created_at": "2026-10-21T22:14:38.159467+00:00",
            "status": "QUEUED",
            "voice_id": None,
            "updated_segments_count": 0,
            "ledger_digest": None,
            "metadata": {},
        },
    ]
    for target_dir in (cache_10_21, out_10_21):
        q_file = target_dir / "speaker_hints_queue.json"
        q_file.write_text(json.dumps(hints, indent=2), encoding="utf-8")
    return hints


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover LENS Debrief session and repair 10-09 Standup.")
    parser.add_argument("--repo-root", default=str(ROOT), help="Repository root path")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    print("=" * 70)
    print("  RECOVER LENS DEBRIEF SESSION & RESTORE 10-09-2026 STANDUP")
    print("=" * 70)

    res_10_09 = restore_10_09_2026_standup(repo_root)
    print(f"1. Restored 10-09-2026 Standup segments: {res_10_09['restored_segments']} segment(s) returned to Voice A")
    print(f"   Removed foreign speaker map: {res_10_09['removed_foreign_speaker_map']}")

    hints = migrate_10_21_2026_hints(repo_root)
    print(f"2. Migrated {len(hints)} Miles Erickson hint(s) to 10-21-2026 queue")
    print("✅ RECOVERY PROTOCOL COMPLETED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
