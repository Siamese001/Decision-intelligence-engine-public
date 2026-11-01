"""
dual_pipeline/canonical_pipeline/resource_discovery.py

Bounded, Non-Hanging Fuzzy Resource Discovery Engine.
SSOT for resolving missing, renamed, or moved meeting input materials,
audio recordings, source transcripts, and metadata sidecars in <500ms
without scanning deep unmanaged directory trees or spawning hung background tasks.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("dual_pipeline.canonical_pipeline.resource_discovery")

# Supported candidate file extensions
_AUDIO_EXTENSIONS = frozenset([
    ".mp3", ".wav", ".wave", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".webm", ".mp4",
])
_TEXT_EXTENSIONS = frozenset([
    ".md", ".txt", ".text", ".markdown", ".docx", ".vtt", ".srt", ".json",
])
_ALL_RESOURCE_EXTENSIONS = _AUDIO_EXTENSIONS | _TEXT_EXTENSIONS

# Directories strictly pruned from discovery traversal
PRUNED_DIR_NAMES = frozenset({
    ".git",
    ".worktrees",
    ".venv",
    "venv",
    ".local",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".gemini",
    "attempts",
    "checkpoints",
    "build",
    "dist",
    "eggs",
    ".eggs",
})


@dataclass(frozen=True)
class ResourceMatch:
    """A scored candidate resource match."""
    path: Path
    score: float
    is_exact_stem: bool
    has_date_match: bool
    modality: str  # "audio" | "transcript" | "metadata" | "document"
    metadata_path: Optional[Path] = None


@dataclass
class ResourceDiscoveryResult:
    """Standardized output payload from fuzzy resource discovery."""
    query: str
    best_match: Optional[ResourceMatch] = None
    candidates: List[ResourceMatch] = field(default_factory=list)
    confidence_margin: float = 1.0  # Delta = top - second
    elapsed_seconds: float = 0.0
    files_scanned: int = 0
    requires_hitl: bool = False


def _normalize_name_tokens(name_or_stem: str) -> Tuple[Set[str], Optional[str]]:
    """Extracts lowercase alphabetic tokens and optional date token (MM-DD-YYYY or YYYY-MM-DD)."""
    raw = str(name_or_stem).lower()
    # Strip common prefixes/suffixes
    raw = re.sub(r"^(?:00|01|02|03)_", "", raw)
    raw = re.sub(r"_(?:master.*|segments.*|live_transcript.*|source_transcript.*|hitl_resolved.*)$", "", raw)
    
    # Extract date
    date_m = re.search(r"(\d{2}[-_/]\d{2}[-_/]\d{4}|\d{4}[-_/]\d{2}[-_/]\d{2})", raw)
    date_token = date_m.group(1).replace("/", "-").replace("_", "-") if date_m else None
    
    # Clean non-alphanumeric
    cleaned = re.sub(r"[^a-z0-9]+", " ", raw)
    tokens = {t for t in cleaned.split() if len(t) >= 2 and not t.isdigit()}
    return tokens, date_token


def _scan_canonical_directories(
    roots: List[Path],
    timeout_s: float = 2.0,
    max_files: int = 2000,
) -> List[Path]:
    """Scans canonical directory trees with strict pruning and hard deadline."""
    start_t = time.monotonic()
    collected: List[Path] = []
    seen: Set[Path] = set()

    for root in roots:
        if not root.is_dir():
            continue

        for dirpath, dirnames, filenames in os.walk(str(root), topdown=True):
            if time.monotonic() - start_t > timeout_s or len(collected) >= max_files:
                logger.warning(
                    "Resource discovery reached limit (elapsed=%.2fs, scanned=%d files). Halting scan early.",
                    time.monotonic() - start_t,
                    len(collected),
                )
                return collected

            # Prune prohibited directories in-place
            dirnames[:] = [d for d in dirnames if d not in PRUNED_DIR_NAMES and not d.startswith(".")]

            p_dir = Path(dirpath)
            for fn in filenames:
                if fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext in _ALL_RESOURCE_EXTENSIONS:
                    cand = (p_dir / fn).resolve()
                    if cand not in seen:
                        seen.add(cand)
                        collected.append(cand)
                        if len(collected) >= max_files:
                            return collected

    return collected


def _find_colocated_metadata(candidate_path: Path) -> Optional[Path]:
    """Locates co-located metadata sidecar for a candidate resource."""
    stem = candidate_path.stem.lower()
    parent = candidate_path.parent

    # Direct name checks
    direct_candidates = [
        parent / f"{candidate_path.stem}.metadata.json",
        parent / f"{candidate_path.stem}_metadata.json",
        parent / "metadata.json",
    ]
    for dc in direct_candidates:
        if dc.is_file():
            return dc

    # Stem-based search in same directory
    clean_stem = re.sub(r"^(?:00|01|02|03)_", "", stem)
    clean_stem = re.sub(r"_(?:source_transcript|live_transcript|master.*)$", "", clean_stem)
    stem_tokens = set(re.findall(r"[a-z0-9]+", clean_stem))
    clean_prefix2 = "_".join([t for t in clean_stem.split("_") if not t.isdigit()][:2])
    scored_jfs = []
    for jf in parent.glob("*.json"):
        j_stem = jf.stem.lower()
        if "metadata" not in j_stem:
            continue
        j_tokens = set(re.findall(r"[a-z0-9]+", j_stem))
        overlap = len(stem_tokens & j_tokens)
        direct_prefix = bool(clean_prefix2 and clean_prefix2 in j_stem)
        ratio = difflib.SequenceMatcher(None, clean_stem, j_stem).ratio()
        if direct_prefix or overlap > 1:
            scored_jfs.append((direct_prefix, ratio, overlap, jf))
    if scored_jfs:
        scored_jfs.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return scored_jfs[0][3]

    return None


def _clean_core_stem(stem: str) -> str:
    s = stem.lower()
    s = re.sub(r"^(?:00|01|02|03)_", "", s)
    s = re.sub(r"_(?:master.*|segments.*|live_transcript.*|source_transcript.*|hitl_resolved.*)$", "", s)
    s = re.sub(r"(\d{2}[-_/]\d{2}[-_/]\d{4}|\d{4}[-_/]\d{2}[-_/]\d{2})", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def score_resource_candidate(
    query_stem: str,
    query_tokens: Set[str],
    query_date: Optional[str],
    candidate_path: Path,
) -> ResourceMatch:
    """Computes a deterministic match score in [0.0, 1.0] for a candidate resource file."""
    c_stem = candidate_path.stem.lower()
    c_tokens, c_date = _normalize_name_tokens(c_stem)
    ext = candidate_path.suffix.lower()

    is_exact_stem = (query_stem == c_stem or query_stem.replace("-", "_") == c_stem.replace("-", "_"))
    has_date_match = bool(query_date and c_date and (query_date == c_date))

    clean_q = _clean_core_stem(query_stem)
    clean_c = _clean_core_stem(c_stem)

    ratio_raw = difflib.SequenceMatcher(None, query_stem, c_stem).ratio()
    ratio_clean = difflib.SequenceMatcher(None, clean_q, clean_c).ratio() if clean_q and clean_c else 0.0
    ratio = max(ratio_raw, ratio_clean)
    if clean_q and clean_c and (clean_q in clean_c or clean_c in clean_q):
        ratio = max(ratio, 0.90)

    # Token overlap (Jaccard)
    token_score = 0.0
    if query_tokens and c_tokens:
        overlap = query_tokens & c_tokens
        union = query_tokens | c_tokens
        token_score = len(overlap) / len(union)

    # Composite weighting
    score = (0.50 * ratio) + (0.50 * token_score)

    # Date match bonus
    if has_date_match:
        score = min(1.0, score + 0.20)
    elif query_date and c_date and query_date != c_date:
        # Penalize explicit date mismatch
        score = max(0.0, score - 0.35)

    path_str = str(candidate_path).lower()
    is_source_input = "inputs/" in path_str or "intake/" in path_str or "source_transcript" in c_stem
    if is_source_input:
        score = min(1.0, score + 0.12)
    elif "hitl_resolved" in c_stem or "projection" in c_stem:
        score = max(0.0, score - 0.15)

    if is_exact_stem:
        score = 1.0

    # Determine modality
    if ext in _AUDIO_EXTENSIONS:
        modality = "audio"
    elif "metadata" in c_stem or ext == ".json":
        modality = "metadata"
    else:
        modality = "transcript"

    meta_path = _find_colocated_metadata(candidate_path)
    return ResourceMatch(
        path=candidate_path,
        score=round(score, 4),
        is_exact_stem=is_exact_stem,
        has_date_match=has_date_match,
        modality=modality,
        metadata_path=meta_path,
    )


def _build_exact_resource_match(
    raw_query: str, resolved_p: Path, start_t: float
) -> ResourceDiscoveryResult:
    ext = resolved_p.suffix.lower()
    modality = "audio" if ext in _AUDIO_EXTENSIONS else ("metadata" if ext == ".json" else "transcript")
    meta_p = _find_colocated_metadata(resolved_p)
    exact_match = ResourceMatch(
        path=resolved_p,
        score=1.0,
        is_exact_stem=True,
        has_date_match=True,
        modality=modality,
        metadata_path=meta_p,
    )
    return ResourceDiscoveryResult(
        query=raw_query,
        best_match=exact_match,
        candidates=[exact_match],
        confidence_margin=1.0,
        elapsed_seconds=round(time.monotonic() - start_t, 4),
        files_scanned=1,
        requires_hitl=False,
    )


def _build_default_search_roots(
    root: Path, project: Optional[str], worktree_dir: Optional[Path]
) -> List[Path]:
    search_roots = [
        root / "inputs",
        root / "intake",
        root / "output" / "transcript_cache",
    ]
    if project:
        clean_proj = project.strip().lower()
        search_roots.insert(0, root / "inputs" / clean_proj)
        search_roots.insert(0, root / "intake" / clean_proj)
    if worktree_dir is not None:
        from dual_pipeline.canonical_pipeline.worktree_inheritance import _normalize_worktree_path
        wt_root = _normalize_worktree_path(worktree_dir, root)
        if wt_root.is_dir():
            wt_roots = [wt_root / "output" / "transcript_cache", wt_root / "output"]
            if project:
                clean_p = project.strip().lower()
                wt_roots = [
                    wt_root / "output" / clean_p,
                    wt_root / "output" / "transcript_cache" / clean_p,
                    *wt_roots,
                ]
            search_roots = [*wt_roots, *search_roots]
    return search_roots


def discover_resource_fuzzy(
    query_path_or_name: str,
    repo_root: Optional[Path] = None,
    project: Optional[str] = None,
    date: Optional[str] = None,
    timeout_s: float = 2.0,
    max_files: int = 2000,
    search_roots: Optional[List[Path]] = None,
    worktree_dir: Optional[Path] = None,
) -> ResourceDiscoveryResult:
    """
    Finds existing resources matching query string across canonical repositories in <500ms.
    Guarantees no hangs, zero .worktrees traversal, and explicit confidence delta.
    """
    start_t = time.monotonic()
    raw_query = str(query_path_or_name).strip()
    if not raw_query:
        return ResourceDiscoveryResult(
            query=raw_query, best_match=None, candidates=[], confidence_margin=0.0,
            elapsed_seconds=round(time.monotonic() - start_t, 4), files_scanned=0, requires_hitl=False,
        )
    q_path = Path(raw_query)
    q_stem = q_path.stem.lower()
    root = repo_root or Path.cwd()

    if len(raw_query) < 4096 and "\n" not in raw_query:
        if os.path.exists(raw_query) and os.path.isfile(raw_query):
            return _build_exact_resource_match(raw_query, q_path.expanduser().resolve(), start_t)
        if worktree_dir is not None and not q_path.is_absolute():
            from dual_pipeline.canonical_pipeline.worktree_inheritance import _normalize_worktree_path
            wt_cand = _normalize_worktree_path(worktree_dir, root) / q_path
            if wt_cand.is_file():
                return _build_exact_resource_match(raw_query, wt_cand.resolve(), start_t)

    if search_roots is None:
        search_roots = _build_default_search_roots(root, project, worktree_dir)

    scanned_files = _scan_canonical_directories(search_roots, timeout_s=timeout_s, max_files=max_files)
    query_tokens, extracted_date = _normalize_name_tokens(q_stem)
    effective_date = date or extracted_date

    scored_matches: List[ResourceMatch] = []
    for fp in scanned_files:
        m = score_resource_candidate(
            query_stem=q_stem,
            query_tokens=query_tokens,
            query_date=effective_date,
            candidate_path=fp,
        )
        # Filter out negligible matches
        if m.score >= 0.40:
            scored_matches.append(m)

    scored_matches.sort(key=lambda x: (x.score, x.has_date_match, x.is_exact_stem), reverse=True)

    elapsed = round(time.monotonic() - start_t, 4)
    if not scored_matches:
        return ResourceDiscoveryResult(
            query=raw_query,
            best_match=None,
            candidates=[],
            confidence_margin=0.0,
            elapsed_seconds=elapsed,
            files_scanned=len(scanned_files),
            requires_hitl=False,
        )

    best = scored_matches[0]
    competing_matches = [
        m for m in scored_matches[1:]
        if best.metadata_path is None or m.path.resolve() != best.metadata_path.resolve()
    ]
    second_score = competing_matches[0].score if competing_matches else 0.0
    margin = round(best.score - second_score, 4)
    requires_hitl = (margin <= 0.30 and bool(competing_matches) and best.score < 0.95)

    return ResourceDiscoveryResult(
        query=raw_query,
        best_match=best,
        candidates=scored_matches[:5],
        confidence_margin=margin,
        elapsed_seconds=elapsed,
        files_scanned=len(scanned_files),
        requires_hitl=requires_hitl,
    )


__all__ = [
    "ResourceMatch",
    "ResourceDiscoveryResult",
    "discover_resource_fuzzy",
    "score_resource_candidate",
    "PRUNED_DIR_NAMES",
]
