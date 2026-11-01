"""tools/pr_governance_core/complexity.py

Deterministic refactoring complexity assessment engine for PR governance.
Evaluates diff churn, file count, git divergence, merge conflict risk,
architectural blast radius, and open wave plans to assign standard T-shirt sizes
(XS, S, M, L, XL) and numeric complexity ranks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

COMPLEXITY_SIZES = ("XS", "S", "M", "L", "XL")
COMPLEXITY_RANK_MAP: Dict[str, int] = {size: idx for idx, size in enumerate(COMPLEXITY_SIZES)}


@dataclass
class ComplexityAssessment:
    size: str  # "XS", "S", "M", "L", "XL"
    score: int
    churn: int
    file_count: int
    behind: int
    clean_merge: bool
    layer_crossings: List[str] = field(default_factory=list)
    rationale: str = ""

    @property
    def rank(self) -> int:
        return COMPLEXITY_RANK_MAP.get(self.size, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "size": self.size,
            "rank": self.rank,
            "score": self.score,
            "churn": self.churn,
            "file_count": self.file_count,
            "behind": self.behind,
            "clean_merge": self.clean_merge,
            "layer_crossings": self.layer_crossings,
            "rationale": self.rationale,
        }


def _score_churn(churn: int) -> int:
    if churn < 50:
        return 1
    if churn < 250:
        return 2
    if churn < 800:
        return 3
    if churn < 2500:
        return 4
    return 5


def _score_file_count(count: int) -> int:
    if count <= 2:
        return 0
    if count <= 5:
        return 1
    if count <= 15:
        return 2
    return 3


def _score_divergence(behind: int, clean_merge: bool) -> int:
    score = 0
    if behind > 20:
        score += 3
    elif behind > 5:
        score += 2
    elif behind > 0:
        score += 1

    if not clean_merge:
        score += 3
    return score


def _detect_layer_crossings(changed_files: List[str]) -> Tuple[int, List[str]]:
    crossings: List[str] = []
    score = 0

    has_storage = any("storage/" in f or "database" in f for f in changed_files)
    has_pipeline = any("dual_pipeline/" in f or "pipeline_" in f for f in changed_files)
    has_tools = any("tools/" in f for f in changed_files)
    has_contracts = any("contracts/" in f for f in changed_files)

    if has_storage:
        crossings.append("Storage/Database (L0)")
        score += 2
    if has_pipeline:
        crossings.append("Core Pipeline (L1-L4)")
        score += 2
    if has_contracts:
        crossings.append("Contracts/Schemas (L0)")
        score += 1
    if has_tools and not (has_storage or has_pipeline):
        crossings.append("Tools/Governance")
        score += 1

    return score, crossings


def _score_open_waves(open_waves: str) -> int:
    if not open_waves or open_waves in ("—", "?", "None"):
        return 0
    parts = [p.strip() for p in open_waves.split(",") if p.strip()]
    return min(len(parts), 3)


def evaluate_refactoring_complexity(
    item: Dict[str, Any],
    numstat: Optional[Dict[str, Tuple[int, int]]] = None,
) -> ComplexityAssessment:
    """Evaluates multi-factor refactoring complexity and assigns an explicit T-shirt size."""
    # 1. Churn & File Count
    churn = int(item.get("churn", 0))
    file_count = int(item.get("file_count", 0))
    raw_changed = item.get("changed_files")
    if isinstance(raw_changed, (list, tuple, set)):
        changed_files = list(raw_changed)
    elif isinstance(raw_changed, int):
        file_count = file_count or raw_changed
        changed_files = []
    else:
        changed_files = []



    if numstat:
        calc_churn = sum(adds + dels for adds, dels in numstat.values())
        if calc_churn > 0 or churn == 0:
            churn = calc_churn
        if len(numstat) > 0 or file_count == 0:
            file_count = len(numstat)
        if not changed_files:
            changed_files = list(numstat.keys())

    behind = int(item.get("behind", 0))
    clean_merge = bool(item.get("clean_merge", True))
    open_waves = str(item.get("open_waves", "—"))

    # Calculate factor points
    churn_pts = _score_churn(churn)
    file_pts = _score_file_count(file_count)
    divergence_pts = _score_divergence(behind, clean_merge)
    layer_pts, layer_crossings = _detect_layer_crossings(changed_files)
    wave_pts = _score_open_waves(open_waves)

    total_score = churn_pts + file_pts + divergence_pts + layer_pts + wave_pts

    # Sizing brackets
    if total_score <= 3:
        size = "XS"
        summary = "Trivial localized change"
    elif total_score <= 7:
        size = "S"
        summary = "Small scoped feature / low divergence"
    elif total_score <= 11:
        size = "M"
        summary = "Moderate feature / isolated changes"
    elif total_score <= 15:
        size = "L"
        summary = "Substantial refactor / high divergence or layer crossing"
    else:
        size = "XL"
        summary = "Major architectural overhaul / multi-layer risk"

    rationale_parts = [summary, f"churn={churn}", f"files={file_count}"]
    if behind > 0:
        rationale_parts.append(f"behind={behind}")
    if not clean_merge:
        rationale_parts.append("merge_conflicts")
    if layer_crossings:
        rationale_parts.append(f"layers=[{', '.join(layer_crossings)}]")

    return ComplexityAssessment(
        size=size,
        score=total_score,
        churn=churn,
        file_count=file_count,
        behind=behind,
        clean_merge=clean_merge,
        layer_crossings=layer_crossings,
        rationale="; ".join(rationale_parts),
    )
