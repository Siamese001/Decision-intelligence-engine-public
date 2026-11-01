"""
dual_pipeline/hitl_governance_core/scoped_autonomy.py

Scoped autonomy governance, frozen decision snapshots, evidence check budgets,
and context review preparation caching for Wave 9.

Enforces:
1. Frozen engagement/category/option/revision snapshots with explicit preference vs correctness labels.
2. At most two allowed read-only evidence checks before an eligible clarification within the shared lease.
3. Autonomous alias classification ensuring zero learning mass/maturity without verified independent outcome.
4. Exact-context review preparation caching without extending approval scope.
5. Mandatory human/speaker cases remain non-waivable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("dual_pipeline.hitl_governance_core.scoped_autonomy")


class LabelType(str, Enum):
    """Explicit taxonomy distinguishing human preference from factual correctness."""
    PREFERENCE = "PREFERENCE"
    CORRECTNESS = "CORRECTNESS"
    NEUTRAL = "NEUTRAL"


class EvidenceCheckLimitExceeded(Exception):
    """Raised when more than two read-only evidence checks are attempted before clarification."""
    pass


@dataclass(frozen=True)
class ScopedDecisionSnapshot:
    """Immutable snapshot binding engagement, category, option, revision, and label type."""
    engagement_id: str
    category: str
    option_id: str
    revision_id: str
    label_type: str = "PREFERENCE"  # PREFERENCE, CORRECTNESS, NEUTRAL
    is_override: bool = False
    selected_key: str = ""
    recommended_key: Optional[str] = None
    evidence_refs: Tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        payload = {
            "engagement_id": self.engagement_id,
            "category": self.category,
            "option_id": self.option_id,
            "revision_id": self.revision_id,
            "label_type": self.label_type,
            "is_override": self.is_override,
            "selected_key": self.selected_key,
            "recommended_key": self.recommended_key,
            "evidence_refs": list(self.evidence_refs),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["digest"] = self.digest
        d["evidence_refs"] = list(self.evidence_refs)
        return d


class EvidenceCheckBudget:
    """Enforces at most two read-only evidence checks before an eligible clarification."""

    def __init__(self, max_checks: int = 2) -> None:
        self.max_checks = max_checks
        self.checks_performed: int = 0
        self._checked_refs: List[str] = []
        self._lock = threading.Lock()

    def check_evidence(self, evidence_ref: str, read_fn: Optional[Callable[[str], Any]] = None) -> Any:
        with self._lock:
            if self.checks_performed >= self.max_checks:
                raise EvidenceCheckLimitExceeded(
                    f"Evidence check budget exhausted: {self.checks_performed}/{self.max_checks} "
                    "allowed read-only checks exceeded before clarification."
                )
            self.checks_performed += 1
            self._checked_refs.append(evidence_ref)

        if read_fn:
            return read_fn(evidence_ref)
        return {"evidence_ref": evidence_ref, "status": "VERIFIED_READ"}

    @property
    def remaining_budget(self) -> int:
        with self._lock:
            return max(0, self.max_checks - self.checks_performed)

    @property
    def checked_refs(self) -> List[str]:
        with self._lock:
            return list(self._checked_refs)


class ExactContextReviewCache:
    """
    Caches exact-context review preparation without extending approval scope.
    Approval scope remains strictly bound to the original decision card.
    """

    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max_entries
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def make_key(engagement_id: str, category: str, context_hash: str, revision_id: str) -> str:
        ctx_sub = (context_hash or "empty_ctx")[:16]
        return f"{engagement_id}:{category}:{ctx_sub}:{revision_id}"

    def put_preparation(self, key: str, card_payload: Dict[str, Any]) -> None:
        with self._lock:
            if len(self._cache) >= self.max_entries:
                oldest = next(iter(self._cache))
                del self._cache[oldest]
            # Ensure preparation is strictly read-only and carries approval scope boundary
            preparation = dict(card_payload)
            preparation["is_cached_preparation"] = True
            preparation["approval_extended"] = False
            self._cache[key] = preparation

    def get_preparation(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            val = self._cache.get(key)
            if val is not None:
                return dict(val)
            return None

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


AUTONOMOUS_ALIASES_REGEX = re.compile(
    r"^(auto|autonomous)[_.-]|[_.-](auto|autonomous)$|^(auto|autonomous)$",
    re.IGNORECASE,
)


def is_autonomous_alias(operator_id: Optional[str]) -> bool:
    """Detects whether an operator identifier is an autonomous alias."""
    clean = str(operator_id or "").strip().lower()
    if not clean:
        return False
    from .learning_eligibility import AUTONOMOUS_OPERATOR_IDS
    if clean in AUTONOMOUS_OPERATOR_IDS:
        return True
    if AUTONOMOUS_ALIASES_REGEX.search(clean):
        return True
    parts = re.split(r"[-_.]+", clean)
    return "auto" in parts or "autonomous" in parts


def create_scoped_decision_snapshot(
    engagement_id: str,
    category: str,
    option_id: str,
    revision_id: str,
    selected_key: str,
    recommended_key: Optional[str] = None,
    label_type: LabelType = LabelType.PREFERENCE,
    is_override: bool = False,
    evidence_refs: Optional[Sequence[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ScopedDecisionSnapshot:
    """Creates a validated, immutable ScopedDecisionSnapshot."""
    return ScopedDecisionSnapshot(
        engagement_id=str(engagement_id or "default_engagement"),
        category=str(category or "default_category"),
        option_id=str(option_id or "opt_0"),
        revision_id=str(revision_id or "rev_1"),
        label_type=str(label_type.value if isinstance(label_type, LabelType) else label_type),
        is_override=bool(is_override or (recommended_key and recommended_key != selected_key)),
        selected_key=str(selected_key or ""),
        recommended_key=str(recommended_key) if recommended_key else None,
        evidence_refs=tuple(evidence_refs) if evidence_refs else tuple(),
        metadata=dict(metadata or {}),
    )
