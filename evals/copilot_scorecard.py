"""
evals/copilot_scorecard.py

Signal-quality evaluation scorecard for Live Copilot replays and benchmarks.
Measures:
1. Groundedness: Ratio of output claims supported by retrieved/cited assertions without hallucination.
2. Relevance: Match rate between retrieved context and target topic/intent entities.
3. Novelty: Non-redundancy ratio against prior session answers.
4. Speakability: Conformance to spoken word budget, single-question formatting, and absence of markup tags.
5. Silence-Precision & Silence-Recall: Precision/recall of deliberate abstention on trap/abstain cases.
6. Stage Latencies: p50, p95, max metrics for retrieve, verify, render, and end-to-end runtime.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("evals.copilot_scorecard")

_MARKUP_TAG_RE = re.compile(r"(\[.*?\]|```|<.*?>|\{.*?\})")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def _safe_float(val: Any) -> Optional[float]:
    """Safely converts value to non-negative float, returning None if invalid."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if (math.isfinite(f) and f >= 0.0) else None
    except (ValueError, TypeError):
        return None


def _percentile(values: List[float], p: float) -> float:
    """Computes nearest-rank percentile."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, int(math.ceil(p * len(sorted_vals))) - 1))
    return float(sorted_vals[idx])


def _compute_digest(data: Dict[str, Any]) -> str:
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass
class CopilotScorecard:
    """Immutable scorecard summarizing copilot signal quality across an evaluation corpus."""
    total_cases: int
    groundedness: float
    relevance: float
    novelty: float
    speakability: float
    silence_precision: float
    silence_recall: float
    stage_latencies: Dict[str, Dict[str, float]]
    strata_metrics: Dict[str, Dict[str, float]]
    reproducible_digest: str
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    passed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def passes_floors(
        self,
        min_groundedness: float = 0.95,
        min_silence_precision: float = 0.80,
        max_p95_total_ms: float = 1200.0,
    ) -> bool:
        p95_tot = self.stage_latencies.get("total_ms", {}).get("p95", 0.0)
        return (
            self.groundedness >= min_groundedness
            and self.silence_precision >= min_silence_precision
            and (p95_tot <= max_p95_total_ms or p95_tot == 0.0)
        )


def _check_speakability(text: str, max_words: int = 40) -> Tuple[bool, List[str]]:
    """Evaluates speakability: word budget, single-question limit, and absence of markup tags."""
    reasons = []
    if not text:
        return True, []
    words = text.strip().split()
    if len(words) > max_words:
        reasons.append(f"WORD_COUNT_EXCEEDED ({len(words)} > {max_words})")
    if _MARKUP_TAG_RE.search(text):
        reasons.append("PROHIBITED_MARKUP_DETECTED")
    q_marks = text.count("?")
    if q_marks > 1:
        reasons.append(f"MULTIPLE_QUESTIONS_DETECTED ({q_marks})")
    return len(reasons) == 0, reasons


def _check_novelty(response: str, recent_history: List[str]) -> bool:
    """Checks whether the response differs substantially from recent session outputs."""
    if not response or not recent_history:
        return True
    resp_tokens = set(re.findall(r"\b\w{3,}\b", response.lower()))
    if not resp_tokens:
        return True
    for past in recent_history[-5:]:
        past_tokens = set(re.findall(r"\b\w{3,}\b", past.lower()))
        if past_tokens:
            jaccard = len(resp_tokens & past_tokens) / len(resp_tokens | past_tokens)
            if jaccard > 0.75:
                return False
    return True


def compute_copilot_scorecard(
    cases: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
) -> CopilotScorecard:
    """Computes a deterministic signal-quality scorecard from evaluation cases and copilot outputs."""
    if not cases:
        empty_digest = _compute_digest({"empty": True})
        return CopilotScorecard(
            total_cases=0,
            groundedness=0.0,
            relevance=0.0,
            novelty=0.0,
            speakability=0.0,
            silence_precision=0.0,
            silence_recall=0.0,
            stage_latencies={},
            strata_metrics={},
            reproducible_digest=empty_digest,
            passed=False,
        )

    res_by_id = {str(r.get("id") or r.get("case_id")): r for r in results}

    grounded_scores: List[float] = []
    relevance_scores: List[float] = []
    novelty_scores: List[float] = []
    speakability_scores: List[float] = []

    true_negatives = 0
    false_positives = 0
    true_positives = 0
    false_negatives = 0

    latencies_retrieve: List[float] = []
    latencies_render: List[float] = []
    latencies_verify: List[float] = []
    latencies_total: List[float] = []

    strata_counts: Dict[str, Dict[str, int]] = {}
    recent_responses: List[str] = []

    for case in cases:
        cid = str(case.get("id"))
        stratum = str(case.get("strata") or "general")
        expected_act = str(case.get("expected_action") or "speak").lower()
        res = res_by_id.get(cid, {})

        status = str(res.get("status") or "FAIL")
        response_text = str(res.get("response") or res.get("response_text") or "").strip()
        spoke = bool(response_text)

        if stratum not in strata_counts:
            strata_counts[stratum] = {"total": 0, "correct_action": 0}
        strata_counts[stratum]["total"] += 1

        # Silence / abstention classification
        if expected_act in ("silent", "abstain", "trap"):
            if not spoke:
                true_negatives += 1
                strata_counts[stratum]["correct_action"] += 1
            else:
                false_positives += 1
        else:
            if spoke and status == "PASS":
                true_positives += 1
                strata_counts[stratum]["correct_action"] += 1
            else:
                false_negatives += 1

        # Groundedness & Relevance
        cited_ids = res.get("cited_assertion_ids") or []
        expected_ids = set(case.get("expected_assertion_ids") or [])
        assertion_texts = case.get("assertion_texts") or res.get("assertion_texts") or {}
        if spoke:
            # Did it cite at least one assertion and are they valid and grounded in assertion text?
            is_grounded = bool(cited_ids) and ("is an identified" not in response_text)
            if is_grounded and isinstance(assertion_texts, dict) and assertion_texts:
                resp_tokens = set(re.findall(r"\b\w{4,}\b", response_text.lower()))
                cited_tokens: set[str] = set()
                for aid in cited_ids:
                    a_txt = str(assertion_texts.get(aid) or "")
                    cited_tokens.update(re.findall(r"\b\w{4,}\b", a_txt.lower()))
                if cited_tokens and not (resp_tokens & cited_tokens):
                    is_grounded = False
            grounded_scores.append(1.0 if is_grounded else 0.0)

            # Relevance
            if expected_ids:
                rel = len(set(cited_ids) & expected_ids) / len(expected_ids)
                relevance_scores.append(min(1.0, rel))
            else:
                relevance_scores.append(1.0 if cited_ids else 0.0)

            # Novelty
            is_nov = _check_novelty(response_text, recent_responses)
            novelty_scores.append(1.0 if is_nov else 0.0)
            recent_responses.append(response_text)

            # Speakability
            is_spk, _ = _check_speakability(response_text)
            speakability_scores.append(1.0 if is_spk else 0.0)
        else:
            if expected_act in ("silent", "abstain", "trap"):
                # Clean abstention counts as grounded and relevant (did not hallucinate)
                grounded_scores.append(1.0)
                relevance_scores.append(1.0)
                novelty_scores.append(1.0)
                speakability_scores.append(1.0)
            else:
                # Omission when expected to speak: penalize groundedness, relevance, and speakability
                grounded_scores.append(0.0)
                relevance_scores.append(0.0)
                speakability_scores.append(0.0)

        # Stage latencies
        stage_lats = res.get("stage_latencies_ms") or {}
        r_ms = _safe_float(stage_lats.get("retrieve_ms"))
        if r_ms is not None:
            latencies_retrieve.append(r_ms)
        rn_ms = _safe_float(stage_lats.get("render_ms"))
        if rn_ms is not None:
            latencies_render.append(rn_ms)
        v_ms = _safe_float(stage_lats.get("verify_ms"))
        if v_ms is not None:
            latencies_verify.append(v_ms)
        tot_ms = _safe_float(stage_lats.get("total_ms"))
        if tot_ms is not None:
            latencies_total.append(tot_ms)
        else:
            fallback_tot = _safe_float(res.get("latency_ms"))
            if fallback_tot is not None:
                latencies_total.append(fallback_tot)

    # Summary metrics
    n_cases = len(cases)
    avg_grounded = sum(grounded_scores) / len(grounded_scores) if grounded_scores else 1.0
    avg_relevance = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 1.0
    avg_novelty = sum(novelty_scores) / len(novelty_scores) if novelty_scores else 1.0
    avg_speakability = sum(speakability_scores) / len(speakability_scores) if speakability_scores else 1.0

    sil_prec_denom = true_negatives + false_positives
    sil_precision = (true_negatives / sil_prec_denom) if sil_prec_denom > 0 else 1.0

    sil_rec_denom = true_negatives + false_negatives
    sil_recall = (true_negatives / sil_rec_denom) if sil_rec_denom > 0 else 1.0

    def _summarize_lats(lats: List[float]) -> Dict[str, float]:
        if not lats:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}
        return {
            "p50": round(_percentile(lats, 0.50), 2),
            "p95": round(_percentile(lats, 0.95), 2),
            "p99": round(_percentile(lats, 0.99), 2),
            "max": round(max(lats), 2),
            "mean": round(sum(lats) / len(lats), 2),
        }

    stage_latencies_summary = {
        "retrieve_ms": _summarize_lats(latencies_retrieve),
        "render_ms": _summarize_lats(latencies_render),
        "verify_ms": _summarize_lats(latencies_verify),
        "total_ms": _summarize_lats(latencies_total),
    }

    strata_metrics_summary: Dict[str, Dict[str, float]] = {}
    for st, counts in strata_counts.items():
        tot = counts["total"]
        acc = (counts["correct_action"] / tot) if tot > 0 else 1.0
        strata_metrics_summary[st] = {"total": float(tot), "accuracy": round(acc, 4)}

    # Compute deterministic digest over scores and case outcome mapping
    digest_payload = {
        "total_cases": n_cases,
        "groundedness": round(avg_grounded, 4),
        "relevance": round(avg_relevance, 4),
        "novelty": round(avg_novelty, 4),
        "speakability": round(avg_speakability, 4),
        "silence_precision": round(sil_precision, 4),
        "silence_recall": round(sil_recall, 4),
        "case_actions": sorted([(c.get("id"), bool(res_by_id.get(str(c.get("id")), {}).get("response"))) for c in cases]),
    }
    reproducible_digest = _compute_digest(digest_payload)

    return CopilotScorecard(
        total_cases=n_cases,
        groundedness=round(avg_grounded, 4),
        relevance=round(avg_relevance, 4),
        novelty=round(avg_novelty, 4),
        speakability=round(avg_speakability, 4),
        silence_precision=round(sil_precision, 4),
        silence_recall=round(sil_recall, 4),
        stage_latencies=stage_latencies_summary,
        strata_metrics=strata_metrics_summary,
        reproducible_digest=reproducible_digest,
        passed=(
            round(avg_grounded, 4) >= 0.95
            and round(sil_precision, 4) >= 0.80
            and (stage_latencies_summary["total_ms"]["p95"] <= 1200.0 or stage_latencies_summary["total_ms"]["p95"] == 0.0)
        ),
    )
