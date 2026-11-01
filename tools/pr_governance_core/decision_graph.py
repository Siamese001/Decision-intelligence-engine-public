"""tools/pr_governance_core/decision_graph.py

Graph dependency detection and topological execution sequencing for PR Queue (Wave 3).
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.pr_overlap_core.rebase_plan import detect_stacked_lineage, is_ancestor


class DependencyEdgeType(str, Enum):
    DECLARED_PREREQUISITE = "DECLARED_PREREQUISITE"
    OUTSTANDING_PARENT = "OUTSTANDING_PARENT"
    FILE_OVERLAP = "FILE_OVERLAP"
    DUPLICATE_PATCH = "DUPLICATE_PATCH"
    PROPOSED_CONSOLIDATION = "PROPOSED_CONSOLIDATION"


def _process_declared_prereqs(
    declared: Dict[str, str],
    hard_graph: Dict[str, List[str]],
    ahead_map: Dict[str, int],
    repo_root: Path,
    base_ref: str,
) -> List[Dict[str, Any]]:
    edges = []
    for src, target in declared.items():
        if src in hard_graph and target:
            target_already_merged = is_ancestor(repo_root, target, base_ref)
            target_ahead = ahead_map.get(target, 1)
            if target_ahead is None:
                target_ahead = 1
            if target_already_merged or target_ahead == 0:
                continue
            edges.append({
                "source_branch": src, "target_branch": target,
                "edge_type": DependencyEdgeType.DECLARED_PREREQUISITE.value, "is_hard_prerequisite": True,
                "evidence": f"Declared prerequisite in plan or registry: {src} -> {target}",
                "shared_files": [],
            })
            hard_graph[src].append(target)
    return edges


def _process_stacked_parents(
    branch_names: List[str],
    ahead_map: Dict[str, int],
    hard_graph: Dict[str, List[str]],
    repo_root: Path,
    base_ref: str,
) -> List[Dict[str, Any]]:
    edges = []
    for b_a in branch_names:
        ahead_a = ahead_map.get(b_a, 1)
        if ahead_a <= 0:
            continue
        for b_b in branch_names:
            if b_a == b_b:
                continue
            ahead_b = ahead_map.get(b_b, 1)
            if ahead_b <= 0 or ahead_a > ahead_b:
                continue
            if detect_stacked_lineage(repo_root, b_a, b_b, base_ref=base_ref):
                edges.append({
                    "source_branch": b_b, "target_branch": b_a,
                    "edge_type": DependencyEdgeType.OUTSTANDING_PARENT.value, "is_hard_prerequisite": True,
                    "evidence": f"Git lineage stacked: {b_b} contains unmerged commits of {b_a}",
                    "shared_files": [],
                })
                if b_a not in hard_graph[b_b]:
                    hard_graph[b_b].append(b_a)
    return edges


def _process_file_overlaps(
    overlaps: Dict[str, Any],
    hard_graph: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    edges = []
    for src, shared in overlaps.items():
        shared_dict = shared if isinstance(shared, dict) else {}
        for tgt, files in shared_dict.items():
            if src in hard_graph and tgt in hard_graph:
                edges.append({
                    "source_branch": src, "target_branch": tgt,
                    "edge_type": DependencyEdgeType.FILE_OVERLAP.value, "is_hard_prerequisite": False,
                    "evidence": f"Shared files overlap ({len(files)} files)", "shared_files": list(files),
                })
    return edges


def _detect_graph_cycles(
    branch_names: List[str],
    hard_graph: Dict[str, List[str]],
) -> List[List[str]]:
    """Detect cycles in dependency graph using networkx simple_cycles."""
    import networkx as nx

    g = nx.DiGraph()
    for b in branch_names:
        g.add_node(b)
    for src, neighbors in hard_graph.items():
        for dst in neighbors:
            if dst in hard_graph:
                g.add_edge(src, dst)

    return [c + [c[0]] for c in nx.simple_cycles(g)]


def detect_hard_dependencies(
    branch_names: List[str],
    ahead_map: Dict[str, int],
    repo_root: Path,
    base_ref: str = "main",
    declared_prereqs: Optional[Dict[str, str]] = None,
    file_overlaps: Optional[Dict[str, List[str]]] = None,
) -> Tuple[List[Any], Dict[str, List[str]], List[List[str]]]:
    """Builds typed dependency edges, filters non-outstanding parents, and detects cycles."""
    from tools.pr_governance_core.decision_api import DependencyEdge

    hard_graph: Dict[str, List[str]] = {b: [] for b in branch_names}
    edge_dicts = []
    edge_dicts.extend(_process_declared_prereqs(declared_prereqs or {}, hard_graph, ahead_map, repo_root, base_ref))
    edge_dicts.extend(_process_stacked_parents(branch_names, ahead_map, hard_graph, repo_root, base_ref))
    edge_dicts.extend(_process_file_overlaps(file_overlaps or {}, hard_graph))

    edges = [DependencyEdge(**ed) for ed in edge_dicts]
    cycles = _detect_graph_cycles(branch_names, hard_graph)
    return edges, hard_graph, cycles


def _build_dependency_in_degrees(
    cand_map: Dict[str, Any],
    hard_graph: Dict[str, List[str]],
    cycle_nodes: Set[str],
) -> Tuple[Dict[str, int], Dict[str, List[str]], Dict[str, str]]:
    in_degree = {b: 0 for b in cand_map}
    reverse_deps: Dict[str, List[str]] = {b: [] for b in cand_map}
    blocked_reasons: Dict[str, str] = {}

    for src, prereqs in hard_graph.items():
        if src not in cand_map or src in cycle_nodes:
            continue
        for p in prereqs:
            if p in cand_map and p not in cycle_nodes:
                in_degree[src] += 1
                reverse_deps[p].append(src)
            elif p not in cand_map:
                blocked_reasons[src] = f"Blocked by unmerged prerequisite '{p}' outside eligible queue"
    return in_degree, reverse_deps, blocked_reasons


SINGLETON_HOTSPOTS = {
    "implementation_plan.md",
    "sidecar_ssot_reconciliation_receipt.json",
    "hitl_decision_ledger.json",
    "WAVE_0_AGENTIC_ARCHITECTURE_AUDIT_REPORT.md",
    "graph_healer.py",
    "evaluator.py",
}

PRIORITY_LEVELS = ("P0", "P1", "P2", "P3")


def _get_tier_level(tier: str) -> int:
    base = str(tier or "").split(".")[0].replace("*", "")
    if base in PRIORITY_LEVELS:
        return PRIORITY_LEVELS.index(base)
    return len(PRIORITY_LEVELS)


def _compute_contention_penalty(branch: str, cand_map: Dict[str, Any]) -> int:
    item = cand_map.get(branch, {})
    files = set(item.get("changed_files") or item.get("files") or [])
    if not files:
        return 0

    overlap_count = 0
    for other_b, other_item in cand_map.items():
        if other_b == branch:
            continue
        other_files = set(other_item.get("changed_files") or other_item.get("files") or [])
        if not files.isdisjoint(other_files):
            overlap_count += 1

    hotspot_count = sum(1 for f in files if Path(f).name in SINGLETON_HOTSPOTS or f in SINGLETON_HOTSPOTS)
    return overlap_count + (10 * hotspot_count)


def compute_topological_execution_sequence(
    eligible_branches: List[Dict[str, Any]],
    hard_graph: Dict[str, List[str]],
    cycles: List[List[str]],
) -> Tuple[List[str], Dict[str, str]]:
    """Orders eligible branches by topological dependency order, using work priority and minimum contention as tie-breakers."""
    cycle_nodes: Set[str] = set()
    blocked_reasons: Dict[str, str] = {}
    for c in cycles:
        cycle_nodes.update(c)
        msg = f"Cycle detected in hard dependencies: {' -> '.join(c)}"
        for node in c:
            blocked_reasons[node] = msg

    cand_map = {item["branch"]: item for item in eligible_branches}
    in_degree, reverse_deps, ext_blocked = _build_dependency_in_degrees(cand_map, hard_graph, cycle_nodes)
    blocked_reasons.update(ext_blocked)

    def _ready_sort_key(b: str) -> Tuple[int, int, int]:
        tier = cand_map[b].get("tier", "P3")
        tier_idx = _get_tier_level(tier)
        contention = _compute_contention_penalty(b, cand_map)
        arr_idx = eligible_branches.index(cand_map[b])
        return (tier_idx, contention, arr_idx)

    ready = [b for b, deg in in_degree.items() if deg == 0 and b not in cycle_nodes and b not in blocked_reasons]
    ready.sort(key=_ready_sort_key)

    sequence: List[str] = []
    while ready:
        curr = ready.pop(0)
        sequence.append(curr)
        for dependent in reverse_deps.get(curr, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0 and dependent not in blocked_reasons and dependent not in cycle_nodes:
                ready.append(dependent)
                ready.sort(key=_ready_sort_key)

    for b in cand_map:
        if b not in sequence and b not in blocked_reasons:
            unmet = [p for p in hard_graph.get(b, []) if p not in sequence]
            blocked_reasons[b] = f"Blocked by unmerged prerequisite(s): {', '.join(unmet)}"

    return sequence, blocked_reasons
