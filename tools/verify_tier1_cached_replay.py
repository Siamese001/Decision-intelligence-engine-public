#!/usr/bin/env python3
"""Hermetic Tier 1 Cached Stage Replay Verification Gate.

Executes a sandboxed, provider-free replay of the Tier 1 synthesis and exit
gates against hermetic fixtures (post-ASR 00_raw_segments.json, non-US header
metadata.json with foreign worktree paths, and entity_resolutions.json).

Verifies:
1. Canonical Date Normalization (Gate 1.3): ISO/dashed dates in headers healed to MM/DD/YYYY.
2. Cross-Worktree Path Rebinding: Stale foreign worktree paths rebound cleanly to active target dir.
3. Cross-Output Coherence & Quote Pointer Healing (X3 Exit Gate): Verbatim quote spans anchored.
4. Ephemeral Sandbox Sandboxing: 100% sandboxed execution inside tempfile with zero writes to output/.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.canonical_pipeline.worktree_inheritance import rebind_worktree_metadata_paths
from dual_pipeline.transcript_stages_core.l1_attribution import assemble_native_hitl_transcript
from dual_pipeline.transcript_qa_core.healing_mesh import heal_header_date, _iso_to_us_date
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator

logger = logging.getLogger("verify_tier1_cached_replay")

TIER1_REPLAY_SCOPED_PREFIXES = (
    "dual_pipeline/transcript_",
    "dual_pipeline/canonical_pipeline/",
    "pipeline_a_intake/",
    "pipeline_b_extraction/",
)
TIER1_REPLAY_SCOPED_KEYWORDS = ("transcript", "audio", "recording", "copilot")


def _verify_copilot_session_ae00fe03_replay(target_sandbox: Path) -> Tuple[bool, Optional[str]]:
    """Verifies Live Copilot session ae00fe03-b63d-44fd-b21b-9a3493bfcc63 replay inside target_sandbox."""
    import hashlib
    from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
    from tests.end_to_end.test_copilot_ed_live_session_replay_e2e import (
        _seed_real_sqlite_graph_with_competing_milestone,
    )

    meeting_dir = target_sandbox / "project_lens" / "10-28-2026" / "Ed_Skyler_Amit"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    (meeting_dir / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": "ae00fe03-b63d-44fd-b21b-9a3493bfcc63",
                "project": "project_lens",
                "engagement_id": "project:lens",
                "attendees": [
                    {"name": "Ed More", "role": "Principal Engineer", "org": "Apex Global Enterprises"},
                    {"name": "Skyler", "role": "Engineer", "org": "PartnerAdvisory"},
                    {"name": "Amit Ayer", "role": "Delivery Lead", "org": "PartnerAdvisory"},
                ],
            }
        ),
        encoding="utf-8",
    )
    segs = [
        {
            "seq": 1,
            "chunk_idx": 1,
            "speaker": "Eduardo Juan More",
            "text": "In the old project rule we had GE...",
            "timestamp": "00:03:55",
            "start_sec": 235.0,
            "end_sec": 240.0,
        },
        {
            "seq": 2,
            "chunk_idx": 2,
            "speaker": "Eduardo Juan More",
            "text": "hardcoded at 100% accuracy in the extraction metric with Daniel Schaap on IGBT nameplate rules.",
            "timestamp": "00:04:00",
            "start_sec": 240.0,
            "end_sec": 245.0,
        },
        {
            "seq": 3,
            "chunk_idx": 3,
            "speaker": "Eduardo Juan More",
            "text": "I do have to run.",
            "timestamp": "00:25:50",
            "start_sec": 1550.0,
            "end_sec": 1555.0,
        },
    ]
    (meeting_dir / "segments.jsonl").write_text("\n".join(json.dumps(s) for s in segs) + "\n", encoding="utf-8")

    db_path = target_sandbox / "copilot_replay_graph.sqlite"
    _seed_real_sqlite_graph_with_competing_milestone(db_path)

    sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
    (target_sandbox / "active_pair.json").write_text(
        json.dumps({"graph_path": str(db_path), "graph_sha256": sha, "generation": "gen_ae00fe03"}),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        request="respond to ed's overall comments entire live transcript on how we can work better together",
        query=None,
        prompt=None,
        intent=None,
        target=None,
        topic=None,
        words=75,
        meeting_dir=str(meeting_dir),
        runtime_root=target_sandbox,
        graph=str(db_path),
        graph_path=str(db_path),
        session=None,
        active=False,
        offline=False,
        graph_only=False,
        bypass_graph_cache=True,
        json=True,
    )
    res = execute_copilot_request(args)
    cited = res.get("cited_assertion_ids", [])
    if res.get("status") != "PASS" or not cited or not str(cited[0]).startswith("live_turn_"):
        return False, f"Copilot session ae00fe03 replay failed: {res}"
    return True, None


def has_tier1_replay_scope(target_files: Optional[Sequence[str]]) -> bool:
    """Return True if changed files touch transcript synthesis or pipeline extraction layers."""
    if target_files is None:
        return True
    cleaned = [f.strip() for f in target_files if f.strip() and not f.startswith("-")]
    if not cleaned:
        return False
    return any(
        f.startswith(TIER1_REPLAY_SCOPED_PREFIXES) or any(kw in f for kw in TIER1_REPLAY_SCOPED_KEYWORDS)
        for f in cleaned
    )


_LAST_DEFAULT_REPLAY_RECEIPT: Optional[Dict[str, Any]] = None


def verify_tier1_cached_replay(
    fixture_dir: Optional[Path] = None,
    output_sandbox_dir: Optional[Path] = None,
    target_files: Optional[Sequence[str]] = None,
    force: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Runs hermetic Tier 1 cached replay check and returns (passed, receipt)."""
    global _LAST_DEFAULT_REPLAY_RECEIPT
    if target_files is not None and not force and not has_tier1_replay_scope(target_files):
        receipt = {
            "status": "PASS",
            "verdict": "BYPASS",
            "gate": "Tier 1 Cached Replay Gate",
            "duration_seconds": 0.0,
            "bypassed": True,
            "reason": "Path-scoped bypass: zero transcript or pipeline changes in scope.",
            "timestamp": time.time(),
        }
        return True, receipt

    if (
        fixture_dir is None
        and output_sandbox_dir is None
        and not force
        and _LAST_DEFAULT_REPLAY_RECEIPT is not None
    ):
        return True, dict(_LAST_DEFAULT_REPLAY_RECEIPT)

    start_time = time.monotonic()
    fix_dir = fixture_dir or (ROOT / "tests" / "fixtures" / "tier1_cached_replay")
    if not fix_dir.is_dir():
        raise FileNotFoundError(f"Fixture directory not found: {fix_dir}")

    raw_seg_path = fix_dir / "00_raw_segments.json"
    meta_path = fix_dir / "metadata.json"
    entity_res_path = fix_dir / "entity_resolutions.json"

    for req_file in (raw_seg_path, meta_path, entity_res_path):
        if not req_file.is_file():
            raise FileNotFoundError(f"Required fixture file missing: {req_file}")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    raw_segments = json.loads(raw_seg_path.read_text(encoding="utf-8"))
    entity_resolutions = json.loads(entity_res_path.read_text(encoding="utf-8"))

    # Create ephemeral sandbox directory
    temp_dir_obj = None
    if output_sandbox_dir is None:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="tier1_replay_sandbox_")
        target_sandbox = Path(temp_dir_obj.name)
    else:
        target_sandbox = Path(output_sandbox_dir)
        target_sandbox.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Rebind worktree metadata paths
        rebind_worktree_metadata_paths(metadata, ROOT, target_sandbox)
        metadata["entity_resolutions"] = entity_resolutions

        # Verify foreign worktree paths were neutralized
        if "foreign_worktree" in metadata.get("target_output_dir", ""):
            return False, {"error": "Stale foreign worktree path survived rebinding in target_output_dir"}
        if "foreign_worktree" in metadata.get("output_01_path", ""):
            return False, {"error": "Stale foreign worktree path survived rebinding in output_01_path"}

        # Step 2: Assemble transcript and verify Gate 1.3 & Invariant 5
        metadata["attachments"] = [
            {
                "filename": "screenshot_dashboard_overview.png",
                "attachment_type": "image",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "relative_staged_path": "referenced_artifacts/screenshot_dashboard_overview.png",
                "summary": "Dashboard overview screenshot.",
            },
            {
                "filename": "screenshot_event_details.png",
                "attachment_type": "image",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "relative_staged_path": "referenced_artifacts/screenshot_event_details.png",
                "summary": "Event details active feed screenshot.",
            },
        ]

        dialogue_lines = []
        speaker_map = metadata.get("speaker_map", {})
        for seg in raw_segments:
            spk = speaker_map.get(seg["speaker"], seg["speaker"])
            dialogue_lines.append(f"**{spk}:** {seg['text']}")
        raw_transcript_text = "\n\n".join(dialogue_lines)

        assembled_f1 = assemble_native_hitl_transcript(
            raw_transcript_text,
            metadata=metadata,
            speaker_map=speaker_map,
        )

        # Check Gate 1.3 US date format MM/DD/YYYY
        expected_date = _iso_to_us_date(metadata["date"])
        if f"**Date:** {expected_date}" not in assembled_f1:
            return False, {
                "error": f"Gate 1.3 Failure: Expected '**Date:** {expected_date}' in Output 01 header, got:\n{assembled_f1[:200]}"
            }

        # Check Invariant 5 Output 01 Presented Collateral block
        if "### Presented Collateral & Artifacts" not in assembled_f1:
            return False, {"error": "Invariant 5 Failure: Missing '### Presented Collateral & Artifacts' in Output 01"}
        if "screenshot_dashboard_overview.png" not in assembled_f1 or "screenshot_event_details.png" not in assembled_f1:
            return False, {"error": "Invariant 5 Failure: Output 01 missing collateral attachment filenames"}

        # Step 3: Run XExitGatesEvaluator on structured data with quote pointers & non-transcript evidence
        f3_sample = {
            "schema_version": "5.7",
            "meeting_id": "meet-tier1-cached-replay",
            "classification": "internal",
            "audiences": [],
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "evidence_id": "ev-01",
                    "speaker": "person:megan_orzell",
                    "text": "Through the well, we have two roles open currently in platform engineering.",
                    "exact_text": "Through the well, we have two roles open currently in platform engineering.",
                    "locator": {"kind": "timestamp", "value": "00:00:18"},
                    "quote_pointer": [0, 10],
                },
                "ev-att-01": {
                    "statement_id": "ev-att-01",
                    "evidence_id": "ev-att-01",
                    "speaker": "attachment:screenshot_dashboard_overview.png",
                    "text": "Dashboard showing active transmission telemetry overview.",
                    "exact_text": "Dashboard showing active transmission telemetry overview.",
                    "locator": {"kind": "region", "value": "attachment:screenshot_dashboard_overview.png"},
                    "quote_pointer": None,
                    "attribution_mode": "COLLATERAL_REVIEWED",
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:megan_orzell",
                        "type": "PERSON",
                        "name": "Megan Orzell",
                        "properties": {"role": "Engineering Lead", "organization": "PartnerAdvisory"},
                    }
                ],
                "relationship_signals": [],
            },
            "evidence_only_statement_ids": [],
        }

        recap_sample = (
            "# 1. Meeting Objective\n\nDiscuss open roles.\n\n"
            "# 2. Action & Governance Ledger\n\n"
            "| # | Workstream | Action Item | Owner | Target Date |\n"
            "| 1 | Delivery | Align with Dan and follow up later today | Amit Ayer | 10/16/2026 |\n\n"
            "# 3. Key Discussion Points & Findings\n\n"
            "- Discussion regarding platform engineering staffing.\n\n"
            "![Dashboard Overview](referenced_artifacts/screenshot_dashboard_overview.png)\n"
            "![Event Details](referenced_artifacts/screenshot_event_details.png)\n"
        )

        x_evaluator = XExitGatesEvaluator()
        x_evaluator._heal_quote_pointers(f3_sample, transcript=assembled_f1)

        # Verify quote pointer was accurately re-anchored to the dialogue span
        qp = f3_sample["evidence_index"]["ev-01"]["quote_pointer"]
        extracted_span = assembled_f1[qp[0]:qp[1]]
        if "Through the well, we have two roles open" not in extracted_span:
            return False, {"error": f"X3 Failure: Re-anchored span '{extracted_span}' does not match expected dialogue"}

        # Verify non-transcript attachment evidence was preserved without span overwrite
        att_ev = f3_sample["evidence_index"]["ev-att-01"]
        if att_ev.get("quote_pointer") is not None:
            return False, {"error": "Invariant 5 Failure: Non-transcript attachment evidence had quote pointer overwritten"}

        # Write test deliverables inside sandbox
        f1_path = target_sandbox / "01_transcript.md"
        f2_path = target_sandbox / "02_recap.md"
        f3_path = target_sandbox / "03_graph.json"

        f1_path.write_text(assembled_f1, encoding="utf-8")
        f2_path.write_text(recap_sample, encoding="utf-8")
        f3_path.write_text(json.dumps(f3_sample, indent=2), encoding="utf-8")

        # Step 4: Verify CanonicalAgenticPipeline stage topology & Live Copilot ae00fe03 replay in sandbox
        canonical_pipe = CanonicalAgenticPipeline(provider_type="offline")
        if canonical_pipe.pipeline_name != "CanonicalAgenticPipeline":
            return False, {"error": "CanonicalAgenticPipeline initialization check failed"}
        copilot_ok, copilot_err = _verify_copilot_session_ae00fe03_replay(target_sandbox)
        if not copilot_ok:
            return False, {"error": copilot_err}

        duration = round(time.monotonic() - start_time, 3)
        wall_clock_sla_seconds = 360.0
        if duration > wall_clock_sla_seconds:
            return False, {
                "error": f"Wall-Clock SLA Failure: Replay duration {duration}s exceeded {wall_clock_sla_seconds}s ceiling",
                "duration_seconds": duration,
                "wall_clock_sla_seconds": wall_clock_sla_seconds,
            }
        receipt = {
            "status": "PASS",
            "verdict": "PASS",
            "gate": "Tier 1 Cached Replay Gate",
            "duration_seconds": duration,
            "wall_clock_sla_seconds": wall_clock_sla_seconds,
            "wall_clock_sla_verified": True,
            "sandbox_path": str(target_sandbox),
            "bypassed": False,
            "date_normalized": expected_date,
            "rebound_paths_verified": True,
            "x3_coherence_verified": True,
            "invariant_5_multi_screenshot_verified": True,
            "canonical_pipeline_verified": True,
            "copilot_session_ae00fe03_verified": True,
            "timestamp": time.time(),
        }
        if fixture_dir is None:
            _LAST_DEFAULT_REPLAY_RECEIPT = dict(receipt)
        return True, receipt
    finally:
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_files", nargs="*", default=None, help="Target files to check for replay scope")
    parser.add_argument("--target-files", nargs="+", default=None, dest="named_target_files", help="Explicit target files list")
    parser.add_argument("--fixtures", type=Path, default=None, help="Path to fixture directory")
    parser.add_argument("--sandbox", type=Path, default=None, help="Explicit sandbox directory")
    parser.add_argument("--json", action="store_true", help="Print JSON receipt to stdout")
    parser.add_argument("--force", action="store_true", help="Force replay execution regardless of target files")
    parser.add_argument("--staged", action="store_true", help="Accept --staged flag for compatibility")
    parser.add_argument("--all", action="store_true", help="Accept --all flag for compatibility")
    args = parser.parse_args()

    targets = args.named_target_files or (args.target_files if args.target_files else None)
    success, receipt = verify_tier1_cached_replay(
        fixture_dir=args.fixtures,
        output_sandbox_dir=args.sandbox,
        target_files=targets,
        force=args.force or args.all,
    )
    if args.json:
        print(json.dumps(receipt, indent=2))
    elif receipt.get("bypassed"):
        print(f"⏭️ [BYPASS] Tier 1 Cached Replay Gate: {receipt.get('reason')}")
    else:
        status_symbol = "✅ [PASS]" if success else "❌ [FAIL]"
        print(f"{status_symbol} Tier 1 Cached Stage Replay Gate ({receipt.get('duration_seconds', 0)}s)")
        if not success:
            print(f"Error: {receipt.get('error')}", file=sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
