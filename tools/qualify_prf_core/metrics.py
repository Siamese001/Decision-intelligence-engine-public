"""Scoring freeze rules, alignment metrics, and counterfactual tests for PRF qualification."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Set, Tuple


def compute_alignment_metrics(
    reference_words: List[Dict[str, Any]],
    hypothesis_words: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Scores alignment between reference words and hypothesis words.

    Enforces scoring freeze rules:
    - Inserted words cannot disappear from denominator.
    - Dropping words cannot improve qualification.
    - Duplicating named words cannot improve qualification.
    - Each reference word contributes at most once.
    """
    total_ref_words = len(reference_words)
    total_hyp_words = len(hypothesis_words)

    if total_ref_words == 0:
        return {
            "reference_words_count": 0,
            "hypothesis_words_count": total_hyp_words,
            "reference_named_coverage_pct": 0.0,
            "named_attribution_accuracy_pct": 0.0,
            "true_identified_share_pct": 0.0,
            "substitutions": 0,
            "deletions": 0,
            "insertions": total_hyp_words,
        }

    correct_matches = 0
    substitutions = 0
    matched_ref_indices: Set[int] = set()

    for hyp_idx, hyp in enumerate(hypothesis_words):
        ref_idx = hyp.get("ref_index", hyp_idx if hyp_idx < total_ref_words else None)
        if ref_idx is not None and ref_idx < total_ref_words and ref_idx not in matched_ref_indices:
            ref = reference_words[ref_idx]
            matched_ref_indices.add(ref_idx)
            ref_spk = ref.get("speaker")
            hyp_spk = hyp.get("speaker")
            if hyp_spk and hyp_spk != "Unknown":
                if hyp_spk == ref_spk:
                    correct_matches += 1
                else:
                    substitutions += 1

    deletions = total_ref_words - len(matched_ref_indices)
    insertions = max(0, total_hyp_words - len(matched_ref_indices))
    named_hyp_words = sum(1 for h in hypothesis_words if h.get("speaker") not in (None, "Unknown", "[Client Speaker]", "Client Speaker"))

    coverage = round((len(matched_ref_indices) / total_ref_words) * 100, 2)
    accuracy = round((correct_matches / max(1, named_hyp_words)) * 100, 2)
    true_share = round((correct_matches / total_ref_words) * 100, 2)

    return {
        "reference_words_count": total_ref_words,
        "hypothesis_words_count": total_hyp_words,
        "matched_reference_words": len(matched_ref_indices),
        "correct_matches": correct_matches,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "reference_named_coverage_pct": coverage,
        "named_attribution_accuracy_pct": accuracy,
        "true_identified_share_pct": true_share,
    }


def verify_counterfactual_invariance(
    hypothesis: List[Dict[str, Any]],
    reference_a: List[Dict[str, Any]],
    reference_b: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Verifies counterfactual property: changing reference does NOT alter hypothesis bytes."""
    hyp_bytes_before = json.dumps(hypothesis, sort_keys=True).encode("utf-8")
    score_a = compute_alignment_metrics(reference_a, hypothesis)
    hyp_bytes_after = json.dumps(hypothesis, sort_keys=True).encode("utf-8")
    score_b = compute_alignment_metrics(reference_b, hypothesis)

    bytes_identical = hyp_bytes_before == hyp_bytes_after
    score_changed = score_a["named_attribution_accuracy_pct"] != score_b["named_attribution_accuracy_pct"]

    return {
        "hypothesis_bytes_invariant": bytes_identical,
        "score_responsiveness_verified": score_changed,
        "score_a": score_a,
        "score_b": score_b,
    }


def get_scoring_protocol() -> Dict[str, Any]:
    """Returns the frozen scoring protocol definition."""
    return {
        "estimation_mode": "FULL_RECORDING_CENSUS_PLUS_HELDOUT_INTERVALS",
        "rules": {
            "insertion_rule": "Inserted named hypothesis words cannot disappear from denominator; treated as incorrect for release gate.",
            "deletion_rule": "Omitted reference speech words remain in reference denominator; penalizes coverage.",
            "substitution_rule": "Attributable substitutions reported separately from lexical speech recognition errors.",
            "repetition_rule": "Duplicate hypothesis words cannot increase reference coverage; each reference word aligned at most once.",
            "overlap_rule": "Overlapping speech intervals scored with overlap penalties; display exclusive paths do not erase overlap in evidence.",
            "zero_duration_rule": "Zero-duration turns cannot contribute to valid dialogue coverage.",
            "dropping_speech_prohibition": "Excluding difficult or unintelligible speech after observing candidate predictions is strictly prohibited.",
        },
        "worked_examples": [
            {
                "case": "isolated_insertion",
                "reference": ["Hello", "world"],
                "hypothesis": ["Hello", "I", "world"],
                "effect": "Hypothesis word 'I' counted as insertion; cannot improve coverage or vanish from accuracy denominator.",
            },
            {
                "case": "reference_deletion",
                "reference": ["I", "think", "as", "we", "talk"],
                "hypothesis": ["as", "we", "talk"],
                "effect": "Words 'I' and 'think' marked deleted; reference coverage denominator retains all 5 words.",
            },
            {
                "case": "speaker_permutation",
                "reference_speakers": ["Clement", "Venkat"],
                "hypothesis_speakers": ["Venkat", "Clement"],
                "effect": "Permuting predicted speakers causes substitution errors and lowers accuracy to 0%.",
            },
        ],
    }
