"""Single SSOT loader for co-located speaker_map.json sidecar files."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger("dual_pipeline.transcript_pipeline_core.speaker_map_loader")


def _load_json_dict(cand: Path, log_name: str) -> Optional[Dict[str, Any]]:
    if not cand.is_file():
        return None
    try:
        data = json.loads(cand.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            logger.info("Loaded co-located speaker_map sidecar: %s", log_name)
            return data
    except json.JSONDecodeError as e:
        raise ValueError(f"Fail-Fast: Corrupted speaker map sidecar {log_name}: {e}") from e
    except Exception as e:
        logger.warning("Failed to parse speaker_map sidecar %s: %s", log_name, e)
    return None


def _load_hints_map(hints_file: Path) -> Optional[Dict[str, Any]]:
    if not hints_file.is_file():
        return None
    try:
        hints_data = json.loads(hints_file.read_text(encoding="utf-8"))
        raw_list = []
        if isinstance(hints_data, list):
            raw_list = hints_data
        elif isinstance(hints_data, dict):
            raw_list = hints_data.get("hints") or hints_data.get("queue") or []
        if raw_list:
            h_map = {
                h.get("voice_id"): h.get("speaker_name")
                for h in raw_list
                if isinstance(h, dict) and h.get("voice_id") and h.get("speaker_name")
            }
            if h_map:
                logger.info("Loaded speaker map from %s: %s", hints_file.name, h_map)
                return h_map
    except Exception as e:
        logger.warning("Failed to parse %s: %s", hints_file.name, e)
    return None


def load_colocated_speaker_map(input_candidate: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """
    Auto-detects and loads co-located speaker_map.json sidecar next to input file.
    Follows priority:
    1. Exact matches: {clean_stem}_speaker_map.json
    2. Fallback: {stem}_speaker_map.json
    3. Directory wildcard: *_speaker_map.json (if single unique candidate)
    """
    try:
        in_p = Path(input_candidate)
        if in_p.is_dir():
            parent_d = in_p
            clean_stem = in_p.name
        elif in_p.is_file():
            parent_d = in_p.parent
            clean_stem = in_p.stem.replace("_live_transcript", "").replace("_master", "")
        else:
            return None

        candidates = [
            parent_d / f"{clean_stem}_speaker_map.json",
            parent_d / f"{in_p.stem}_speaker_map.json",
        ]

        for cand in candidates:
            res = _load_json_dict(cand, cand.name)
            if res is not None and bool(res):
                return res

        # Wildcard fallback
        matches = list(parent_d.glob("*_speaker_map.json"))
        if len(matches) == 1:
            res = _load_json_dict(matches[0], matches[0].name)
            if res is not None and bool(res):
                return res

        # Fallback to speaker_hints.json and speaker_hints_queue.json
        res = _load_hints_map(parent_d / "speaker_hints.json")
        if res and bool(res):
            return res
        res_q = _load_hints_map(parent_d / "speaker_hints_queue.json")
        if res_q and bool(res_q):
            return res_q

        # Sibling cache directory fallback (if parent_d is in output/<project>/<date>/<slug>)
        if len(parent_d.parents) >= 3 and parent_d.parents[2].name == "output":
            tc_dir = parent_d.parents[2] / "transcript_cache" / parent_d.parents[1].name / parent_d.parent.name
            if tc_dir.is_dir():
                s_map = _load_hints_map(tc_dir / "speaker_hints_queue.json")
                if s_map and bool(s_map):
                    return s_map
                s_hints = _load_hints_map(tc_dir / "speaker_hints.json")
                if s_hints and bool(s_hints):
                    return s_hints

        return None
    except ValueError:
        raise
    except Exception as e:
        logger.debug("Could not inspect input path for speaker_map: %s", e)
        return None
