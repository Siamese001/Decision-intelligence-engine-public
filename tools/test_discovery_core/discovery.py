"""
tools/test_discovery_core/discovery.py

Automated glob and marker-based test discovery engine.
Discovers and classifies tests into deterministic, e2e, and bge profiles,
respecting decentralized manifests and quarantined tests with expirations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

from .quarantine import get_quarantined_modules

ROOT = Path(__file__).resolve().parents[2]


def _path_to_module(path: Path, repo_root: Path) -> str:
    rel = path.relative_to(repo_root).with_suffix("")
    return rel.as_posix().replace("/", ".")


def discover_all_test_files(repo_root: Path | None = None) -> List[Tuple[str, Path]]:
    """Scans repo_root/tests for all test_*.py and *_test.py files."""
    root = repo_root or ROOT
    test_dir = root / "tests"
    if not test_dir.is_dir():
        return []

    discovered: List[Tuple[str, Path]] = []
    for p in sorted(test_dir.glob("**/test_*.py")):
        if p.name.startswith("."):
            continue
        mod = _path_to_module(p, root)
        discovered.append((mod, p))

    for p in sorted(test_dir.glob("**/*_test.py")):
        if p.name.startswith(".") or p.name.startswith("test_"):
            continue
        mod = _path_to_module(p, root)
        discovered.append((mod, p))

    return discovered


def categorize_test_file(
    mod: str,
    path: Path,
    quarantined: Set[str],
    base_deterministic: Sequence[str] = (),
    base_e2e: Sequence[str] = (),
    base_bge: Sequence[str] = (),
) -> str:
    """Classifies a test module into 'quarantine', 'bge', 'e2e', or 'deterministic'."""
    if mod in quarantined:
        return "quarantine"

    if mod in ("tests.test_bge_m3_preflight", "tests.test_bge_m3_runtime_qualification"):
        return "deterministic"

    rel_posix = path.as_posix()
    # 1. BGE tests: heavy models, BGE retrieval
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        if (
            "@pytest.mark.bge" in content
            or "heavy_model" in content
            or "NEE_RUN_BGE_TESTS" in content
        ):
            return "bge"
    except Exception:
        pass

    if (
        "test_bge_query_e2e" in mod
        or "test_generational_retriever" in mod
        or "test_conditional_l2" in mod
        or "test_retrieval_release_evaluation" in mod
    ):
        return "bge"

    # 2. Deterministic unit & property tests
    if (
        "tests/unit/" in rel_posix
        or "tests/property/" in rel_posix
        or "tests/fakes/" in rel_posix
    ):
        return "deterministic"

    det_set = set(base_deterministic) if base_deterministic else set()
    e2e_set = set(base_e2e) if base_e2e else set()
    bge_set = set(base_bge) if base_bge else set()

    if not det_set:
        try:
            from tools.ci_gates_core.base_tests import (
                BASE_BGE_TESTS,
                BASE_DETERMINISTIC_TESTS,
                BASE_E2E_TESTS,
            )

            det_set = set(BASE_DETERMINISTIC_TESTS)
            e2e_set = set(BASE_E2E_TESTS)
            bge_set = set(BASE_BGE_TESTS)
        except Exception:
            pass

    if mod in det_set and mod not in e2e_set and mod not in bge_set:
        return "deterministic"

    # 3. All other integration / pipeline tests resolve to e2e
    return "e2e"


def _deduplicate_preserve_order(items: Sequence[str]) -> Tuple[str, ...]:
    seen: Set[str] = set()
    result: List[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return tuple(result)


def _dedup_preserve_order(items: Sequence[str]) -> list[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _load_manifest_tests(
    manifest_dir: Path | None, root: Path, strict: bool
) -> tuple[list[str], list[str], list[str]]:
    det: list[str] = []
    e2e: list[str] = []
    bge: list[str] = []
    try:
        from tools.test_manifest_loader import discover_manifests

        m_dir = manifest_dir if manifest_dir is not None else (root / "tests" / "manifests")
        manifests = discover_manifests(m_dir, strict=strict)
        for m in manifests:
            det.extend(m.deterministic_tests)
            e2e.extend(m.e2e_tests)
            bge.extend(m.bge_tests)
    except Exception:
        pass
    return det, e2e, bge


def _discover_pattern_tests(
    root: Path,
    quarantined: Set[str],
    base_deterministic: Sequence[str],
    base_e2e: Sequence[str],
    base_bge: Sequence[str],
) -> tuple[list[str], list[str], list[str]]:
    det: list[str] = []
    e2e: list[str] = []
    bge: list[str] = []
    for mod, path in discover_all_test_files(root):
        cat = categorize_test_file(
            mod,
            path,
            quarantined,
            base_deterministic=base_deterministic,
            base_e2e=base_e2e,
            base_bge=base_bge,
        )
        if cat == "bge":
            bge.append(mod)
        elif cat == "e2e":
            e2e.append(mod)
        elif cat == "deterministic":
            det.append(mod)
    return det, e2e, bge


def resolve_all_tests(
    base_deterministic: Sequence[str] = (),
    base_e2e: Sequence[str] = (),
    base_bge: Sequence[str] = (),
    manifest_dir: Path | None = None,
    strict: bool = False,
    repo_root: Path | None = None,
) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    """Merges pattern-based test discovery with base CI tuples and decentralized manifests."""
    root = repo_root or ROOT
    quarantined = get_quarantined_modules()

    det_list: List[str] = list(base_deterministic)
    e2e_list: List[str] = list(base_e2e)
    bge_list: List[str] = list(base_bge)

    p_det, p_e2e, p_bge = _discover_pattern_tests(
        root, quarantined, base_deterministic, base_e2e, base_bge
    )
    det_list.extend(p_det)
    e2e_list.extend(p_e2e)
    bge_list.extend(p_bge)

    m_det, m_e2e, m_bge = _load_manifest_tests(manifest_dir, root, strict)
    det_list.extend(m_det)
    e2e_list.extend(m_e2e)
    bge_list.extend(m_bge)

    # Filter out quarantined modules from all active lists
    active_det = [t for t in det_list if t not in quarantined]
    active_e2e = [t for t in e2e_list if t not in quarantined]
    active_bge = [t for t in bge_list if t not in quarantined]

    # Deduplicate with strict precedence: BGE > E2E > Deterministic
    bge_set = set(active_bge)
    active_e2e = [t for t in active_e2e if t not in bge_set]
    e2e_set = set(active_e2e)
    active_det = [t for t in active_det if t not in e2e_set and t not in bge_set]

    return (
        tuple(_dedup_preserve_order(active_det)),
        tuple(_dedup_preserve_order(active_e2e)),
        tuple(_dedup_preserve_order(active_bge)),
    )
