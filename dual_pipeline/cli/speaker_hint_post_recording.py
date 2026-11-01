"""
dual_pipeline/cli/speaker_hint_post_recording.py

Reconciles operator speaker hints directly against post-recording structured utterances
(*.utterances.json), transcript cache (*.transcript_cache.json), and 01_*.md deliverables
when live segments.jsonl is absent.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.cli.speaker_hint_post_recording")


def _update_utterance_sidecar(uf: Path, hint_obj: Dict[str, Any], speaker: str) -> int:
    from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import (
        apply_quote_hints_to_turns,
        apply_quote_hints_to_utterances,
    )
    from dual_pipeline.transcript_pipeline_core.cache_seal import (
        create_cache_seal,
        find_associated_sidecars,
    )

    utts = json.loads(uf.read_text(encoding="utf-8"))
    if not isinstance(utts, list) or not utts:
        return 0
    updated_utts, _ = apply_quote_hints_to_utterances(utts, [hint_obj])
    if not hint_obj.get("turn_matched"):
        return 0

    uf.write_text(json.dumps(updated_utts, indent=2), encoding="utf-8")
    count = sum(1 for u in updated_utts if u.get("speaker") == speaker)
    s_file, c_file, _, seal_file = find_associated_sidecars(uf)
    if c_file and c_file.is_file():
        c_updated, _ = apply_quote_hints_to_turns(c_file.read_text(encoding="utf-8"), [hint_obj])
        c_file.write_text(c_updated, encoding="utf-8")
    if s_file and s_file.is_file() and c_file and c_file.is_file():
        audio_sha, cache_key = "", ""
        if seal_file and seal_file.is_file():
            s_data = json.loads(seal_file.read_text(encoding="utf-8"))
            audio_sha = s_data.get("audio_sha256", "")
            cache_key = s_data.get("cache_key", "")
        create_cache_seal(s_file, c_file, uf, audio_sha256=audio_sha, cache_key=cache_key)
    return count


def reconcile_post_recording_utterances(
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    speaker: str,
    quote: str,
    hint_queue: Any,
    queued_hint: Any,
) -> Optional[Dict[str, Any]]:
    """Reconciles a speaker hint directly against post-recording *.utterances.json and 01_*.md."""
    from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import apply_quote_hints_to_turns

    search_dirs = [d for d in (target_output_dir, target_cache_dir) if d and d.is_dir()]
    utt_files: List[Path] = []
    for d in search_dirs:
        utt_files.extend(sorted(d.glob("*.utterances.json")))
        if (d / ".cache").is_dir():
            utt_files.extend(sorted((d / ".cache").glob("*.utterances.json")))

    matched_cluster: Optional[str] = None
    updated_count = 0
    hint_obj: Dict[str, Any] = {"speaker": speaker, "speaker_name": speaker, "quote": quote, "status": "QUEUED"}

    for uf in utt_files:
        try:
            cnt = _update_utterance_sidecar(uf, hint_obj, speaker)
            if cnt > 0:
                matched_cluster = "TURN_OVERRIDE"
                updated_count += cnt
        except Exception as e:
            logger.debug("Post-recording utterance hint reconciliation skipped for %s: %s", uf, e)

    for md_f in sorted(target_output_dir.glob("01_*.md")):
        try:
            updated_md, reassignments = apply_quote_hints_to_turns(md_f.read_text(encoding="utf-8"), [hint_obj])
            if reassignments:
                md_f.write_text(updated_md, encoding="utf-8")
                matched_cluster = matched_cluster or "TURN_OVERRIDE"
                updated_count = max(updated_count, len(reassignments))
        except Exception as e:
            logger.debug("Failed updating 01 markdown %s: %s", md_f, e)

    if not matched_cluster:
        return None

    hint_queue.mark_reconciled(
        hint_id=queued_hint.hint_id,
        voice_id=matched_cluster,
        updated_segments_count=updated_count,
        ledger_digest=None,
    )
    return {
        "status": "PASS",
        "command": "speaker-hint",
        "action": "APPLIED",
        "voice_id": matched_cluster,
        "speaker_name": speaker,
        "quote": quote,
        "updated_segments_count": updated_count,
        "details": f"Hint reconciled against post-recording artifacts ({updated_count} turn(s) updated).",
    }
