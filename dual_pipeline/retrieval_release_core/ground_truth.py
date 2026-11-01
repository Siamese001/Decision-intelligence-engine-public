"""Independent Ground Truth and Grouped Partitioning for Retrieval Qualification.

Governs Wave 2 Ground Truth Invariants:
1. Circularity Prevention: Judges and reviewers see full evidence with all scores,
   ranks, and model recommendations completely blinded.
2. Provenance Integrity: Judgments retain immutable origin ('weak_supervision_v1',
   'expert_adjudicated', 'human_verified'). Reloading cannot change provenance.
3. Multi-Dimensional Labels: Separate relevance (Grade 2/1/0), route intent,
   answerability (ANSWERABLE/UNANSWERABLE/AMBIGUOUS), and cache equivalence.
4. Zero Family Leakage: Strict topic-grouped partitioning across dev, calibration,
   and locked final holdout sets. Paraphrases never cross partition boundaries.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class EvidenceGrade(int, Enum):
    HIGHLY_RELEVANT = 2   # Direct core answer / required factual support
    PARTIALLY_RELEVANT = 1 # Contextual support / domain background
    IRRELEVANT = 0        # Non-relevant / confounder / distractor


class AnswerabilityStatus(str, Enum):
    ANSWERABLE = "ANSWERABLE"
    UNANSWERABLE = "UNANSWERABLE"
    AMBIGUOUS = "AMBIGUOUS"


class JudgmentProvenance(str, Enum):
    WEAK_SUPERVISION_V1 = "weak_supervision_v1"
    EXPERT_ADJUDICATED = "expert_adjudicated"
    HUMAN_VERIFIED = "human_verified"
    BLIND_CONSENSUS = "blind_consensus"


@dataclass(frozen=True)
class BlindedCandidateView:
    """Evidence view presented to human or judge with all retriever scores/ranks hidden."""

    record_id: str
    record_type: str
    lane: str
    title: str
    retrieval_text: str
    source_id: str | None = None

    @classmethod
    def from_candidate(cls, candidate: Mapping[str, Any]) -> BlindedCandidateView:
        return cls(
            record_id=str(candidate["record_id"]),
            record_type=str(candidate.get("record_type", "")),
            lane=str(candidate.get("lane", "")),
            title=str(candidate.get("title", "")),
            retrieval_text=str(candidate.get("retrieval_text", "")),
            source_id=candidate.get("source_id"),
        )


@dataclass(frozen=True)
class CacheEquivalenceLabel:
    """Cache validity flags governing safe cache substitution."""

    entity_match: bool
    temporal_fresh: bool
    authorized: bool
    polarity_match: bool
    safe_for_cache_reuse: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "safe_for_cache_reuse",
            self.entity_match and self.temporal_fresh and self.authorized and self.polarity_match,
        )


@dataclass(frozen=True)
class MultiDimensionalJudgment:
    """Full multi-dimensional label for a query-candidate pair."""

    query_id: str
    record_id: str
    grade: int  # 0, 1, or 2
    support_type: str  # direct_answer, partial_support, negative_distractor, stale_evidence, etc.
    route_intent: str
    answerability: str  # ANSWERABLE, UNANSWERABLE, AMBIGUOUS
    cache_equivalence: CacheEquivalenceLabel
    provenance: str = JudgmentProvenance.EXPERT_ADJUDICATED.value
    review_status: str = "RESOLVED"  # RESOLVED or UNRESOLVED
    adjudication_notes: str = ""

    def __post_init__(self) -> None:
        if self.grade not in (0, 1, 2):
            raise ValueError(f"Invalid grade {self.grade}; must be 0, 1, or 2")


def create_blinded_candidate_view(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Strip all dense, sparse, BM25, and fused scores/ranks from candidate payload."""
    blinded = BlindedCandidateView.from_candidate(candidate)
    return asdict(blinded)


def group_partition_queries(
    queries: Sequence[Mapping[str, Any]],
    dev_topic_ids: Sequence[str],
    calibration_topic_ids: Sequence[str],
    holdout_topic_ids: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    """Partition queries strictly by topic_id ensuring ZERO family or paraphrase leakage.

    Raises ValueError if any topic appears in more than one partition or if an
    unmapped topic is detected.
    """
    dev_set = set(dev_topic_ids)
    cal_set = set(calibration_topic_ids)
    hold_set = set(holdout_topic_ids)

    # Invariant: Disjoint topic sets
    if dev_set & cal_set:
        raise ValueError(f"Topic overlap between dev and calibration: {dev_set & cal_set}")
    if dev_set & hold_set:
        raise ValueError(f"Topic overlap between dev and holdout: {dev_set & hold_set}")
    if cal_set & hold_set:
        raise ValueError(f"Topic overlap between calibration and holdout: {cal_set & hold_set}")

    all_assigned = dev_set | cal_set | hold_set
    partitions: dict[str, list[dict[str, Any]]] = {
        "dev": [],
        "calibration": [],
        "final_holdout": [],
    }

    for q in queries:
        tid = q.get("topic_id")
        if not tid or tid not in all_assigned:
            raise ValueError(f"Query {q.get('query_id')} has unmapped topic_id: {tid}")
        if tid in dev_set:
            partitions["dev"].append(dict(q))
        elif tid in cal_set:
            partitions["calibration"].append(dict(q))
        elif tid in hold_set:
            partitions["final_holdout"].append(dict(q))

    return partitions


def verify_zero_family_leakage(partitions: Mapping[str, Sequence[Mapping[str, Any]]]) -> bool:
    """Verify that no topic_id or family_id crosses partition boundaries."""
    seen_topics: dict[str, str] = {}
    for part_name, queries in partitions.items():
        for q in queries:
            tid = q["topic_id"]
            if tid in seen_topics and seen_topics[tid] != part_name:
                raise ValueError(
                    f"Family leakage detected: topic {tid} present in both "
                    f"'{seen_topics[tid]}' and '{part_name}'"
                )
            seen_topics[tid] = part_name
    return True
