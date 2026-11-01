"""Graded QREL (0-3) builders, low-overlap paraphrase synthesis, and hard-negative mining."""
from __future__ import annotations

import re
from typing import Any, Sequence

from .constants import _MAX_QUERY_CHARACTERS

_CONTENT_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
_QREL_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "have", "has", "had", "not", "but", "what", "which", "who", "how", "when",
    "where", "why", "can", "will", "into", "about", "concerning", "governed",
    "information", "recorded", "records", "current", "retrieve", "find",
    "evidence", "backed", "knowledge", "described", "titled", "locate",
})


def _bounded_text(value: str, *, limit: int = _MAX_QUERY_CHARACTERS) -> str:
    normalized = " ".join(str(value).split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"


def _extract_scope_ids(record: dict[str, Any]) -> list[str]:
    extensions = record.get("extensions", {})
    values = set(extensions.get("com.partner_advisory.nee.graph-assertion-ids", []))
    object_id = extensions.get("com.partner_advisory.nee.graph-object-id")
    if object_id:
        values.add(object_id)
    return sorted(values)


def _content_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in _CONTENT_TOKEN_RE.findall(str(text).casefold())
        if tok not in _QREL_STOPWORDS
    }


def compute_content_word_jaccard(text_a: str, text_b: str) -> float:
    """Compute content-word Jaccard similarity between two strings."""
    tokens_a = _content_tokens(text_a)
    tokens_b = _content_tokens(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    return len(tokens_a & tokens_b) / len(union)


def synthesize_low_overlap_paraphrase(
    record: dict[str, Any],
    variant_index: int = 1,
) -> str:
    """Synthesize an intent-preserving paraphrase with <25% content-word overlap."""
    rec_type = str(record.get("record_type") or "assertion")
    lane = str(record.get("lane") or "approved")
    answer_use = str(record.get("answer_use") or "factual_answer")
    entities = list(record.get("canonical_entities") or [])
    anchor = entities[0] if entities else str(record.get("record_id", "subject"))[:16]
    target_tokens = _content_tokens(str(record.get("retrieval_text") or ""))
    safe_anchor_words = [
        w for w in _CONTENT_TOKEN_RE.findall(anchor.casefold()) if w not in target_tokens
    ]
    anchor_hint = (
        " ".join(safe_anchor_words[:2])
        if safe_anchor_words
        else rec_type.replace("_", " ")
    )
    templates = (
        f"Summarize verified {answer_use.replace('_', ' ')} posture for {anchor_hint} in {lane} scope",
        f"Provide corroborated operational takeaways regarding {anchor_hint} under {rec_type.replace('_', ' ')}",
        f"Identify key strategic commitments and status indicators linked to {anchor_hint}",
    )
    candidate = templates[(max(1, variant_index) - 1) % len(templates)]
    if compute_content_word_jaccard(candidate, str(record.get("retrieval_text") or "")) >= 0.25:
        candidate = (
            f"Evaluate executive decision context for {lane} "
            f"{rec_type.replace('_', ' ')} item {variant_index}"
        )
    return _bounded_text(candidate)


def mine_sibling_hard_negatives(
    target_records: Sequence[dict[str, Any]],
    corpus_records: Sequence[dict[str, Any]],
    max_negatives: int = 3,
) -> list[dict[str, Any]]:
    """Mine in-corpus sibling distractors with lexical/entity overlap but distinct scope."""
    target_ids = {r["record_id"] for r in target_records}
    target_scopes = {sid for r in target_records for sid in _extract_scope_ids(r)}
    target_entities = {e for r in target_records for e in r.get("canonical_entities", [])}
    target_vocab = set().union(*(
        _content_tokens(r.get("retrieval_text", "")) for r in target_records
    ))
    primary_lane = target_records[0]["lane"] if target_records else "approved"
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for cand in corpus_records:
        cid = cand.get("record_id", "")
        if not cid or cid in target_ids or cand.get("lane") != primary_lane:
            continue
        cand_scopes = set(_extract_scope_ids(cand))
        if target_scopes and cand_scopes and (cand_scopes <= target_scopes):
            continue
        cand_entities = set(cand.get("canonical_entities", []))
        cand_vocab = _content_tokens(cand.get("retrieval_text", ""))
        ent_overlap = len(target_entities & cand_entities)
        lex_overlap = len(target_vocab & cand_vocab)
        if ent_overlap > 0 or lex_overlap > 0:
            score = (ent_overlap * 2.0) + float(lex_overlap)
            scored.append((score, cid, cand))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[: max(1, max_negatives)]]


_BARE_IDENTIFICATION_STUB_RE = re.compile(
    r"\bis an identified (?:workstream|control|technical_anchor|role_group|org|stakeholder)\s+in\b"
    r"|\bgood news is that\b"
    r"|\bwe are still maintaining a nice\b"
    r"|\bhas Total Headcount\b",
    re.IGNORECASE,
)

_SUPPORTING_ONLY_FACT_RE = re.compile(
    r"\b(?:contractor specialist assigned to|reported that .* had not yet been tied to)\b",
    re.IGNORECASE,
)

HUMAN_LABELED_GOLD_QRELS: dict[str, dict[str, Any]] = {
    "qrel-human-001": {
        "query": "What is the release plan and system boundary for moving vanguard to production?",
        "retrieval_mode": "factual",
        "grades": {
            "retrieval-record-00121205a4757d3096a057c27b9f": 3,
            "retrieval-record-39254e59bd5837da76f4319cefb4": 3,
            "retrieval-record-1a47f1c475b31b27677e1f7d9495": 1,
            "retrieval-record-3675daac22f24138e7bfd2f989ba": 0,
        },
    },
    "qrel-human-002": {
        "query": "How are documents ingested, classified, and routed into Documentum?",
        "retrieval_mode": "factual",
        "grades": {
            "retrieval-record-0029e474c633945c8dc09b3b15a6": 3,
            "retrieval-record-037d7d0633ece98ae1587b211a9a": 1,
            "retrieval-record-2e406ec82f444219bca89e61ae44": 0,
        },
    },
    "qrel-human-003": {
        "query": "What are the repository separation rules and staffing status for the Nuclear AI workstream?",
        "retrieval_mode": "strategic",
        "grades": {
            "retrieval-record-0abf77a77a84f0e40e86e7f30abc": 3,
            "retrieval-record-00641904f0f3ab181fbfff0c08fd": 3,
            "retrieval-record-0e8d5bd19651fcf089944f00bcfb": 2,
            "retrieval-record-08f2b599d0d74098e50419309fea": 0,
        },
    },
    "qrel-human-004": {
        "query": "What middleware and work order transitions are planned for GRID_OS in Q4?",
        "retrieval_mode": "strategic",
        "grades": {
            "retrieval-record-0fbd9e005bf3e447b44436ab18a2": 1,
            "retrieval-record-1ad5c1de26bae5575859aecacd4a": 1,
            "retrieval-record-002d2dc7572974554be8610c83a4": 0,
        },
    },
    "qrel-human-005": {
        "query": "Who leads Scheduling Update Automation and which vertical owns it?",
        "retrieval_mode": "entity_navigation",
        "grades": {
            "retrieval-record-05ebf59c525f538d927831b269b1": 3,
            "retrieval-record-8a84fb6dd8f9aec74d3313c02d52": 2,
            "retrieval-record-80cadb279d6133afb93e96122ffd": 1,
        },
    },
    "qrel-human-006": {
        "query": "Why is Project LENS Phase 1 milestone acceptance at risk?",
        "retrieval_mode": "status_snapshot_current",
        "grades": {
            "retrieval-record-0466ee2f6fca1d654ab42c3d1d36": 3,
            "retrieval-record-04c5a17f38041fe0ab3aa0e9f5c8": 2,
            "retrieval-record-05b7026e86852ff5bf789a0f07b0": 1,
        },
    },
    "qrel-human-007": {
        "query": "What was the July status of RegAI, who leads it, and what is pending for FlexAI?",
        "retrieval_mode": "status_snapshot_history",
        "grades": {
            "retrieval-record-466b5fafa0dfde960da5d7531bf7": 3,
            "retrieval-record-6462761644d182bba14bf610321c": 1,
        },
    },
    "qrel-human-008": {
        "query": "How should we structure the rollout of the Documentum document backfill?",
        "retrieval_mode": "guidance",
        "grades": {
            "retrieval-record-0029e474c633945c8dc09b3b15a6": 2,
            "retrieval-record-037d7d0633ece98ae1587b211a9a": 1,
        },
    },
    "qrel-human-009": {
        "query": "How is the quarterly team cost for Google and PartnerAdvisory structured and subsidized during ramp-up?",
        "retrieval_mode": "guidance",
        "grades": {
            "retrieval-record-af1cd3ceb3ee1528534e20b337b3": 3,
            "retrieval-record-075db3428b6dedc1730f05b3f882": 1,
        },
    },
    "qrel-human-010": {
        "query": "Is the uploaded TRM Scope of Work an executed contract or a draft request for proposal?",
        "retrieval_mode": "candidate_review",
        "grades": {
            "retrieval-record-078a21bed214467b146028365eb7": 3,
            "retrieval-record-079204ec0ae3ea952f3d5c83f0cc": 1,
        },
    },
    "qrel-human-011": {
        "query": "Who is Brian Mendicino and what project is he working on?",
        "retrieval_mode": "entity_navigation",
        "grades": {
            "entity-facet-04e98d7007148fae47fcee9d25e4": 2,
            "retrieval-record-039197fc853a017b415a2a9a37da": 0,
        },
    },
    "qrel-human-012": {
        "query": "What data sources and cloud layers are included in the Grid Optimization Data Product?",
        "retrieval_mode": "factual",
        "grades": {
            "retrieval-record-00e3ed2e7d825beb69315ecc6da4": 3,
            "retrieval-record-079204ec0ae3ea952f3d5c83f0cc": 0,
        },
    },
    "qrel-human-013": {
        "query": "Has the outcome-velocity criticism been tied to a specific metric or shared scorecard yet?",
        "retrieval_mode": "candidate_review",
        "grades": {
            "retrieval-record-084f08ff8f50d6666a93a14fbef6": 2,
        },
    },
    "qrel-human-014": {
        "query": "What are the current extraction and OCR accuracy rates on Project LENS?",
        "retrieval_mode": "status_snapshot_current",
        "grades": {
            "retrieval-record-05b7026e86852ff5bf789a0f07b0": 1,
            "retrieval-record-0466ee2f6fca1d654ab42c3d1d36": 2,
        },
    },
    "qrel-human-015": {
        "query": "Who is the Product Manager for Property Tax Appeal and which Enterprise AI vertical owns it?",
        "retrieval_mode": "entity_navigation",
        "grades": {
            "retrieval-record-77e17c801f1f7a2a3f8e8d45b170": 1,
            "retrieval-record-b08d172f7c0cbbffe4f6d3762598": 1,
        },
    },
}


def is_bare_identification_stub(text: str) -> bool:
    """Return True when a record is a bare stub, uncalibrated quote, or clunky row dump."""
    return bool(_BARE_IDENTIFICATION_STUB_RE.search(str(text or "")))


def is_supporting_only_record(text: str) -> bool:
    """Return True when a record is a single-meeting anecdotal claim or brief role bio capped at Grade 2."""
    return bool(_SUPPORTING_ONLY_FACT_RE.search(str(text or "")))


def _target_intrinsic_grade(text: str) -> int:
    if is_bare_identification_stub(text):
        return 1
    if is_supporting_only_record(text):
        return 2
    return 3


def _classify_candidate_qrel_grade(
    cand: dict[str, Any],
    target_entities: set[str],
    target_scopes: set[str],
    existing_grade: int,
) -> int | None:
    cand_text = str(cand.get("retrieval_text") or cand.get("title") or "")
    has_ent = bool(set(cand.get("canonical_entities", [])) & target_entities)
    has_scope = bool(target_scopes and (set(_extract_scope_ids(cand)) & target_scopes))
    if is_bare_identification_stub(cand_text):
        return 1 if (has_ent or has_scope) else None
    if (cand.get("record_type") == "entity_facet" and has_ent) or has_scope:
        return 2
    if has_ent and existing_grade != 0:
        return 1
    return None


def build_graded_qrels_for_group(
    target_records: Sequence[dict[str, Any]],
    corpus_records: Sequence[dict[str, Any]] | None = None,
    human_overrides: dict[str, int] | None = None,
) -> dict[str, int]:
    """Build 0-3 graded QRELs (3=authoritative, 2=supporting facet, 1=topical/stub, 0=hard negative)."""
    grades: dict[str, int] = {}
    target_ids = {r["record_id"] for r in target_records}
    target_entities = {e for r in target_records for e in r.get("canonical_entities", [])}
    target_scopes = {sid for r in target_records for sid in _extract_scope_ids(r)}
    for rec in target_records:
        text = str(rec.get("retrieval_text") or rec.get("title") or "")
        grades[rec["record_id"]] = _target_intrinsic_grade(text)
    for neg in mine_sibling_hard_negatives(target_records, corpus_records or (), max_negatives=5):
        grades.setdefault(neg["record_id"], 0)
    for cand in corpus_records or ():
        cid = cand.get("record_id", "")
        if cid and cid not in target_ids:
            g = _classify_candidate_qrel_grade(cand, target_entities, target_scopes, grades.get(cid, -1))
            if g is not None:
                grades[cid] = g
    if human_overrides:
        grades.update({k: int(v) for k, v in human_overrides.items()})
    return grades
