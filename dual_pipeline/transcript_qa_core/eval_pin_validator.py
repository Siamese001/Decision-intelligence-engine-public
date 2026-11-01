"""Evaluation pin validation and compatibility enforcement."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _is_valid_pin_dict(pin: Any) -> bool:
    return isinstance(pin, dict) and not bool(pin.get("fatal_codes") or pin.get("fatal_defect_codes"))


def check_pin(
    pinned: Dict[str, Any],
    key1: str,
    key2: str,
    min_score: int,
    expected_sha: str,
    text: Optional[str] = None,
    *,
    expected_c0_digest: Optional[str] = None,
    expected_prompt_hash: Optional[str] = None,
    expected_rubric_version: Optional[str] = None,
    expected_topology: Optional[str] = None,
    f1_text: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Validate and return pinned evaluation scorecard if SHA matches, score passes, and exact compatibility holds."""
    if not isinstance(pinned, dict):
        return None
    pin = (
        pinned.get(key1)
        or pinned.get(key2)
        or pinned.get(f"{key1}_evaluation")
        or pinned.get(f"{key2}_evaluation")
    )
    if not _is_valid_pin_dict(pin):
        return None
    pin_sha = (
        pin.get("artifact_sha256")
        or pin.get("content_sha256")
        or pinned.get("artifact_manifest", {}).get(f"{key1}_sha256")
        or pinned.get("artifact_manifest", {}).get(f"{key2}_sha256")
    )
    if not pin_sha or pin_sha != expected_sha:
        logger.warning("Rejected evaluation pin for %s: missing or mismatched SHA (expected %s, got %s)", key1, expected_sha, pin_sha)
        return None
    if text and key1 in ("output_1", "output_01"):
        from dual_pipeline.llm_judge_core.judges.forensic import check_generic_speaker_ratio
        is_excessive, _, _, _ = check_generic_speaker_ratio(text)
        if is_excessive:
            logger.warning("Rejected evaluation pin for %s: un-attributed generic turns exceed 15%%", key1)
            return None
    if text:
        from dual_pipeline.transcript_qa_core.citation_utils import validate_judge_card_citations
        search_texts = [text] if not f1_text or f1_text == text else [text, f1_text]
        valid_cit, miss_cit = validate_judge_card_citations(pin, search_texts)
        if not valid_cit:
            logger.warning("Rejected evaluation pin for %s: citations not found in candidate text: %s", key1, miss_cit)
            return None
    raw_score = pin.get("composite_score")
    if raw_score is None:
        if pin.get("reason") == "FROZEN_PRIOR_ARTIFACT" and pin.get("status") == "SKIPPED":
            raw_score = min_score
            pin["composite_score"] = min_score
        else:
            logger.warning("Rejected evaluation pin for %s: missing composite_score on non-frozen card", key1)
            return None
    try:
        score_val = int(raw_score)
    except (ValueError, TypeError):
        logger.warning("Rejected evaluation pin for %s: non-numeric composite_score", key1)
        return None

    # Strict scorecard validation: reject malformed pinned scorecards
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    output_num = 1 if key1 in ("output_1", "output_01") else (2 if key1 in ("output_2", "output_02") else 3)
    is_valid, errs = validate_micro_judge_scorecard(pin, output_num=output_num)
    if not is_valid:
        logger.warning("Rejected evaluation pin for %s: scorecard validation failed: %s", key1, errs)
        return None

    # Exact compatibility checks: invalidate pins on C0, prompt, rubric, or topology change
    if pin.get("c0_invalidated") or pinned.get("c0_invalidated"):
        logger.warning("Rejected evaluation pin for %s: invalidated by C0 resolution", key1)
        return None

    pin_c0 = pin.get("c0_receipt_digest") or pinned.get("c0_receipt_digest")
    if expected_c0_digest and expected_c0_digest != "none":
        if pin_c0 and pin_c0 != expected_c0_digest:
            logger.warning(
                "Rejected evaluation pin for %s: C0 receipt digest mismatch (expected %s, got %s)",
                key1, expected_c0_digest, pin_c0,
            )
            return None

    pin_prompt = pin.get("prompt_hash") or pin.get("judge_prompt_hash") or pinned.get(f"{key1}_prompt_hash")
    if expected_prompt_hash and pin_prompt and pin_prompt != expected_prompt_hash:
        logger.warning(
            "Rejected evaluation pin for %s: prompt hash mismatch (expected %s, got %s)",
            key1, expected_prompt_hash, pin_prompt,
        )
        return None

    pin_rubric = pin.get("rubric_version") or pinned.get("rubric_version")
    if expected_rubric_version and pin_rubric and pin_rubric != expected_rubric_version:
        logger.warning(
            "Rejected evaluation pin for %s: rubric version mismatch (expected %s, got %s)",
            key1, expected_rubric_version, pin_rubric,
        )
        return None

    pin_topo = pin.get("topology") or pinned.get("topology")
    if expected_topology and pin_topo and pin_topo != expected_topology:
        logger.warning(
            "Rejected evaluation pin for %s: topology mismatch (expected %s, got %s)",
            key1, expected_topology, pin_topo,
        )
        return None

    if score_val >= min_score and pin.get("verdict") in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"):
        res = dict(pin)
        res["source"] = "PINNED_CACHE"
        res["artifact_sha256"] = pin_sha
        return res
    return None
