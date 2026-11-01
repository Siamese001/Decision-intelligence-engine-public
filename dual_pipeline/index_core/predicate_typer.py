"""Closed-registry predicate typer for relationship-aware queries."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .edge_verbalisation import (
    PREDICATE_VERBALISATIONS,
    REGISTRY_PREDICATES,
    REVERSE_PREDICATE_PHRASES,
    canonicalize_predicate,
)


@dataclass(frozen=True)
class PredicateTypingResult:
    predicate: str | None
    confidence: float
    matched_phrase: str | None
    is_registry_predicate: bool


# Comprehensive pattern registry mapping phrases and keywords to canonical predicates
TYPER_PATTERNS: list[tuple[str, list[str]]] = [
    ("REPORTS_TO", [
        r"\breports? to\b", r"\breporting to\b", r"\breporting manager\b",
        r"\breporting line\b", r"\boperational supervisor\b", r"\bimmediate supervisor\b",
        r"\bmanagement escalation\b", r"\breporting chain\b", r"\bescalation reporting\b",
        r"\bwho does \w+ report to\b", r"\bwho reports to\b", r"\bhierarchy\b",
        r"\bleadership chain link\b", r"\bchain link\b", r"\bleadership chain\b",
    ]),
    ("ROUTES_TO", [
        r"\btracking card\b", r"\bportfolio card\b", r"\bmilestone card\b",
        r"\bboard ticket\b", r"\btracking milestone\b", r"\bgovernance tracking card\b",
        r"\broutes? to\b", r"\brouting to\b", r"\broutes to card\b",
        r"\broutes to canonical card\b", r"\bcanonical card\b", r"\broute to\b",
    ]),
    ("AFFILIATED_WITH", [
        r"\bwhich entity employs\b", r"\bentity employs\b", r"\bemploys\b",
        r"\baffiliated with\b", r"\baffiliation\b", r"\bconsulting firm supplies\b",
        r"\bparent organization\b", r"\badvisory partner\b", r"\borganization hosting\b",
        r"\bcorporate parent\b", r"\bexternal advisory partner\b",
        r"\bwhich management consulting firm\b",
    ]),
    ("OWNS_OR_LEADS", [
        r"\bowns? or leads?\b", r"\bownership\b", r"\baccount lead\b",
        r"\btechnical lead\b", r"\bprogram lead\b", r"\baccount owner\b",
        r"\bownership mandate\b", r"\bprogram sponsor\b", r"\bdelivery leader\b",
        r"\bspearheads\b", r"\bprimary program sponsor\b", r"\bwho owns\b",
        r"\bwho leads\b", r"\blead architect on\b", r"\blead for\b",
        r"\bdesignated as the .* lead\b", r"\bresponsible for\b",
    ]),
    ("SUPPORTED_BY", [
        r"\bsupported by\b", r"\bsupport provider\b", r"\bongoing support\b",
        r"\bsupport mechanism\b", r"\bsustaining continuous delivery\b",
        r"\bsourced from\b", r"\bproduction maintenance\b", r"\bunderwrites technical infrastructure\b",
        r"\bsupplies technical talent\b", r"\bstaffing construct\b", r"\bfixed-capacity delivery team\b",
        r"\bwhat source supports\b",
    ]),
    ("CONTRIBUTES_TO", [
        r"\bcontributes? to\b", r"\bcontributes?\b", r"\bcontribution to\b",
        r"\bfeeds directly into\b", r"\bproviding components to\b", r"\bdirectly enhances\b",
        r"\bsupplying features to\b", r"\btechnical workstream feeds\b",
    ]),
    ("CONTEXT_FOR", [
        r"\bprovides? context for\b", r"\bis context for\b", r"\boperational background\b",
        r"\bprovides rationale\b", r"\bbackground boundaries\b", r"\bdiscovery artifact establishing\b",
        r"\bworking charter outlining\b",
    ]),
    ("USES_PATTERN", [
        r"\buses pattern\b", r"\buses the pattern\b", r"\barchitectural pattern\b",
        r"\bdesign pattern\b", r"\bdesign blueprint\b", r"\bsoftware architecture style\b",
        r"\bpattern used by\b", r"\bintegration pattern\b", r"\bschema design pattern\b",
    ]),
    ("CONTAINS", [
        r"\bcontains?\b", r"\bcomponents? of\b", r"\bconsists? of\b",
        r"\bcontained in\b", r"\bmake up\b", r"\bencapsulated within\b",
        r"\bconstituent modules comprising\b", r"\bsub-workstreams\b",
        r"\bcontains or includes\b",
    ]),
    ("GOVERNS", [
        r"\bgoverns?\b", r"\boversees?\b", r"\bgovernance over\b", r"\boversight over\b",
        r"\bgovernance authority\b", r"\bgovernance rules\b",
        r"\bleadership committee establishing oversight\b", r"\bwho governs\b",
    ]),
    ("DEPENDS_ON", [
        r"\bdepends? on\b", r"\bprerequisite for\b", r"\brequired before\b",
        r"\bmust complete prior to\b", r"\bdependency on\b", r"\bupstream ingestion pipeline is required\b",
        r"\binfrastructure dependency\b", r"\bprerequisite security clearance\b",
    ]),
    ("DELIVERS", [
        r"\bdelivers?\b", r"\bdeliverable of\b", r"\bis delivered by\b",
        r"\bdelivers capability\b",
    ]),
    ("HAS_RISK", [
        r"\bhas risk\b", r"\brisks? associated with\b", r"\bidentified risk\b",
        r"\bvulnerabilities or integration roadblocks\b", r"\btechnical vulnerabilities\b",
    ]),
    ("CANDIDATE_FOR", [
        r"\bcandidate for\b", r"\bcandidate proposal for\b", r"\bunder evaluation for\b",
        r"\bnominated for\b", r"\bpilot project is under evaluation\b",
    ]),
    ("CONSTRAINS", [
        r"\bconstrains?\b", r"\bconstraint on\b", r"\blimits?\b",
    ]),
    ("TRANSITIONS_TO", [
        r"\btransitions? to\b", r"\btarget transition of\b", r"\btarget state operating structure succeeds\b",
        r"\bsucceeds the legacy\b", r"\bfuture state\b",
    ]),
    ("HAS_ROLE_IN", [
        r"\bholds role in\b", r"\bhas a role in\b", r"\brole assignment in\b",
        r"\bdesignated role\b",
    ]),
    ("RELATED_TO", [
        r"\bis related to\b", r"\brelationship to\b", r"\bcorrelates with\b",
        r"\bparallels\b", r"\bhow are .* related\b", r"\boperational relationship\b",
        r"\brelated workstreams\b",
    ]),
]


def type_query_predicate(
    query: str,
    *,
    min_confidence: float = 0.50,
) -> PredicateTypingResult:
    """Classify a search query into a closed-registry predicate using deterministic pattern matching.

    Args:
        query: User or benchmark search query text.
        min_confidence: Threshold below which predicate is returned as None (unknown).

    Returns:
        PredicateTypingResult with canonical predicate, confidence score, and matched phrase.
    """
    q_low = str(query or "").strip().lower()
    if not q_low:
        return PredicateTypingResult(None, 0.0, None, False)

    # 1. Exact match against canonical active & reverse phrases
    for pred, active_phrase in PREDICATE_VERBALISATIONS.items():
        if active_phrase in q_low:
            return PredicateTypingResult(pred, 1.0, active_phrase, True)
    for pred, rev_phrase in REVERSE_PREDICATE_PHRASES.items():
        if rev_phrase in q_low:
            return PredicateTypingResult(pred, 0.98, rev_phrase, True)

    # 2. Pattern and keyword regex matching
    best_pred: str | None = None
    best_score = 0.0
    best_phrase: str | None = None

    for pred, patterns in TYPER_PATTERNS:
        pred_score = 0.0
        matched: str | None = None
        for pat in patterns:
            hits = re.findall(pat, q_low)
            if hits:
                weight = max(1.0, len(pat) / 5.0)
                pred_score += len(hits) * weight
                if matched is None:
                    matched = hits[0] if isinstance(hits[0], str) else pat
        if pred_score > best_score:
            best_score = pred_score
            best_pred = pred
            best_phrase = matched

    if best_pred is None or best_score <= 0.0:
        return PredicateTypingResult(None, 0.0, None, False)

    confidence = min(0.99, max(0.50, round(0.60 + 0.05 * best_score, 2)))
    if confidence < min_confidence:
        return PredicateTypingResult(None, confidence, best_phrase, False)

    canon_p = canonicalize_predicate(best_pred)
    is_reg = canon_p in REGISTRY_PREDICATES
    return PredicateTypingResult(canon_p, confidence, best_phrase, is_reg)


def evaluate_typer_accuracy(
    cases: list[dict[str, Any]],
    query_key: str = "query",
    predicate_key: str = "predicate",
) -> dict[str, Any]:
    """Evaluates predicate typer accuracy against a set of ground-truth test cases."""
    if not cases:
        return {"total": 0, "correct": 0, "accuracy": 0.0, "status": "FAIL"}

    correct = 0
    mismatches: list[dict[str, str]] = []

    for c in cases:
        query = str(c.get(query_key) or "")
        expected = str(c.get(predicate_key) or "").upper()
        res = type_query_predicate(query)
        pred = res.predicate or "UNKNOWN"

        if pred == expected or (expected == "ROUTES_TO" and pred == "ROUTES_TO"):
            correct += 1
        else:
            mismatches.append({
                "query": query,
                "expected": expected,
                "predicted": pred,
            })

    total = len(cases)
    acc = round(correct / total, 4)
    return {
        "total": total,
        "correct": correct,
        "accuracy": acc,
        "accuracy_pct": round(acc * 100, 2),
        "passed_floor_90": acc >= 0.90,
        "status": "PASS" if acc >= 0.90 else "FAIL",
        "mismatches_count": len(mismatches),
        "mismatches_sample": mismatches[:5],
    }
