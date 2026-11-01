"""
Topological clustering of git feature branches based on file overlap and domain tags.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from .matrix import TopologyMatrix


@dataclass
class BranchCluster:
    cluster_id: str
    name: str
    category: str  # DATA_HIGHWAY, PLATFORM_VELOCITY, LINEAGE_DEPENDENCY, SUPERSEDED_TOMBSTONE, ISOLATED_SINGLETON
    branches: List[str] = field(default_factory=list)
    total_files: int = 0
    shared_files_within: List[str] = field(default_factory=list)
    recommended_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _compute_lineage_clusters(matrix: TopologyMatrix, assigned_branches: Set[str], clusters: List[BranchCluster]) -> None:
    lineage_visited: Set[str] = set()

    # Build graph of lineage connections
    graph: Dict[str, Set[str]] = {b: set() for b in matrix.branches}
    for b in matrix.branches:
        diff = matrix.branch_diffs.get(b)
        if diff:
            for ancestor in diff.ancestor_of:
                graph[b].add(ancestor)
                graph[ancestor].add(b)
            for descendant in diff.descendant_of:
                graph[b].add(descendant)
                graph[descendant].add(b)

    for b in matrix.branches:
        if b in assigned_branches or b in lineage_visited:
            continue

        # Traverse component
        component = set()
        queue = [b]
        while queue:
            curr = queue.pop(0)
            if curr not in component and curr not in assigned_branches:
                component.add(curr)
                queue.extend([n for n in graph[curr] if n not in assigned_branches and n not in component])

        lineage_visited.update(component)
        if len(component) >= 2:
            clusters.append(
                BranchCluster(
                    cluster_id=f"lineage_{b.replace('/', '_')}",
                    name=f"Lineage Dependency Group: {b}",
                    category="LINEAGE_DEPENDENCY",
                    branches=sorted(list(component)),
                    recommended_action="Rebase parent first or cherry-pick clean child commits onto post-merge main",
                )
            )
            assigned_branches.update(component)

def compute_topological_clusters(
    matrix: TopologyMatrix,
    dispositions: Optional[Dict[str, Any]] = None,
) -> List[BranchCluster]:
    clusters: List[BranchCluster] = []
    assigned_branches: Set[str] = set()

    # 1. Detect Superseded / Tombstone Clusters
    for cand in matrix.superseded_candidates:
        older = cand["older_branch"]
        newer = cand["newer_branch"]
        if older not in assigned_branches:
            action_text = f"Archive and retire in favor of {newer} (shares {cand['shared_file_count']} files)"
            if dispositions and older in dispositions:
                disp = dispositions[older]
                act = getattr(disp, "action", "")
                if act == "COMMIT_FIRST":
                    action_text = f"Commit uncommitted work before retiring in favor of {newer}"
                elif getattr(disp, "reason", None):
                    action_text = f"[{act}] {disp.reason}"

            clusters.append(
                BranchCluster(
                    cluster_id=f"tombstone_{older.replace('/', '_')}",
                    name=f"Superseded Tombstone: {older}",
                    category="SUPERSEDED_TOMBSTONE",
                    branches=[older],
                    recommended_action=action_text,
                )
            )
            assigned_branches.add(older)

    # 2. Detect Client Deliverables Cluster (Data Highway)
    data_branches = [
        b
        for b, diff in matrix.branch_diffs.items()
        if diff.is_output_only and b not in assigned_branches
    ]
    if data_branches:
        clusters.append(
            BranchCluster(
                cluster_id="cluster_client_deliverables",
                name="Client Deliverables Train (Data Highway)",
                category="DATA_HIGHWAY",
                branches=sorted(data_branches),
                recommended_action="Fast-track via Data Highway: merge directly with Layer 2 schema validation (<30s)",
            )
        )
        assigned_branches.update(data_branches)

    # 3. Detect Platform Velocity Sibling Cluster
    platform_candidates = [
        b
        for b in matrix.branches
        if (
            "perf" in b.lower()
            or "latency" in b.lower()
            or "hook" in b.lower()
            or "rebase" in b.lower()
        )
        and b not in assigned_branches
    ]
    if len(platform_candidates) >= 2:
        clusters.append(
            BranchCluster(
                cluster_id="cluster_platform_velocity",
                name="Platform Velocity Cohort",
                category="PLATFORM_VELOCITY",
                branches=sorted(platform_candidates),
                recommended_action="Consolidate into single cohort PR: eliminates duplicate qualification cycles",
            )
        )
        assigned_branches.update(platform_candidates)

    # 4. Detect Lineage Dependencies (Ancestry chains)
    _compute_lineage_clusters(matrix, assigned_branches, clusters)

    # 5. Remaining branches: Isolated Singletons
    for b in matrix.branches:
        if b not in assigned_branches:
            diff = matrix.branch_diffs.get(b)
            behind = diff.behind if diff else 0
            is_fast_lane = behind == 0
            clusters.append(
                BranchCluster(
                    cluster_id=f"singleton_{b.replace('/', '_')}",
                    name=f"Isolated Singleton: {b}",
                    category="ISOLATED_SINGLETON",
                    branches=[b],
                    recommended_action="Land via Fast-Lane singleton"
                    if is_fast_lane
                    else "Rebase independently onto main",
                )
            )
            assigned_branches.add(b)

    return clusters
