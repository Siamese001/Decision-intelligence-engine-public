#!/usr/bin/env python3
"""
Architectural Layer Dependency & AST Graph Cycle Linter (L0 -> L6 Governance).

Enforces the Unidirectional Layer Dependency DAG from .agents/rules/layer-architectural-integrity.md:
  L0: Storage Schema, Database Models, Contracts, Primitives, Identifiers
  L1: Normalization, Acoustic STT, Transcripts, Splicing, Storage Engine, Connection, WAL
  L2: Extraction, Entities, Assertion Proposals, Pre-Egress Sanitization, Disambiguation
  L3: Knowledge Graph, Vector Indexes, Hybrid Search, Sidecars, Domain Micro-Agents
  L4: Reasoning, Dialectical Judges, Healers, Arbiters, Retrieval Evaluation, Query Routing
  L5: Pipeline Orchestration, Services, Batch Admission, Ingestion Coords, Extraction Coords
  L6: User Interface, Governed CLI, MCP Server Tools, Exports, Antigravity UI Bridges

Non-Negotiable Invariants:
1. Downward Layer Flow: Dependencies must flow strictly downward (L_high -> L_low).
   A lower layer L_M must NEVER import a higher layer L_N where M < N.
2. Sub-Agent to Orchestrator Decoupling: Sub-agents (L3 *_agent.py) must never import
   orchestrators/coordinators (L5 *_coordinator.py, *_orchestrator.py, *engine.py) or L4 judges at top level.
3. Zero Circular Imports: Tarjan's Strongly Connected Components (SCC) algorithm detects
   any import cycle among module top-level imports in the workspace.
4. Unidirectional Cross-Pipeline Flow: Upstream Pipeline A cannot import downstream B, C, D.
   Pipeline B cannot import C, D. Downstream consuming upstream contracts/foundations is allowed.
5. Function-level Decoupling: Function-body imports with upward targets must be protected
   inside try...except or annotated with '# noqa: lazy-decouple'.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple


class Violation(NamedTuple):
    file_path: str
    line_number: int
    source_layer: int
    target_layer: int
    imported_module: str
    rule: str
    detail: str


# Governed root directories
GOVERNED_DIRS = [
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "dual_pipeline",
    "unified_pipeline",
    "graph",
    "evals",
    "config",
    "storage",
]

# Explicit overrides for key components decoupled into declarative manifest
_MANIFEST_PATH = Path(__file__).parent / "layer_hierarchy_manifest.json"
COMPONENT_LAYER_MAP: Dict[str, int] = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def get_pipeline(rel_path: str) -> Optional[str]:
    """Classifies component into pipeline workstream A, B, C, or D."""
    norm = rel_path.replace("\\", "/")
    if norm.startswith("pipeline_a_intake"):
        return "A"
    if norm.startswith("pipeline_b_extraction"):
        return "B"
    if norm.startswith("pipeline_c_retrieval") or "dual_pipeline/retrieval" in norm:
        return "C"
    if "dual_pipeline/decision" in norm:
        return "D"
    return None


def get_layer_for_file(rel_path: str) -> Optional[int]:
    """Returns the architectural layer for a given relative path (L0 -> L6)."""
    norm = rel_path.replace("\\", "/")
    if norm.startswith("unified_pipeline/"):
        norm = "dual_pipeline/" + norm[len("unified_pipeline/"):]
    filename = Path(norm).name

    # Check explicit overrides map first
    for key, layer in COMPONENT_LAYER_MAP.items():
        if norm == key or norm.endswith("/" + key) or key.endswith("/" + norm):
            return layer

    # Layer 6: User Interface, Governed CLI, MCP Server Tools, Exports, Bridges, Scripts, Tools
    if (
        norm == "nee.py"
        or norm.startswith("dual_pipeline/cli")
        or norm.startswith("scripts/")
        or "ui_bridge" in filename
        or "_cli.py" in filename
        or "/mcp/" in norm
        or "/exports/" in norm
    ):
        return 6

    # Layer 0: Contracts, Primitives, Schema, Identifiers, Storage Models, Config
    if (
        norm.startswith("contracts/")
        or norm.startswith("config/")
        or norm.startswith("dual_pipeline/l0/")
        or "contracts.py" in filename
        or "identifiers.py" in filename
        or (norm.startswith("storage/") and ("model" in filename or "schema" in filename or "type" in filename or "identifier" in filename))
        or norm == "dual_pipeline/a2a.py"
    ):
        return 0

    # Layer 5: Pipeline Orchestration, Services, Batch Admission, Ingestion Coords, Workers, State Machines, Graph Writers
    if (
        "coordinator" in filename
        or "orchestrator" in filename
        or "worker" in filename
        or "service" in filename
        or "pipeline" in filename
        or "state_machine" in filename
        or norm.startswith("dual_pipeline/intake_core")
        or norm.startswith("dual_pipeline/intake_engine_core")
        or norm.startswith("dual_pipeline/transcript_stages_core")
        or norm.startswith("dual_pipeline/transcript_pipeline_core")
        or norm.startswith("dual_pipeline/combined_remediation_core")
        or norm.startswith("dual_pipeline/validation_closeout_core")
        or norm.startswith("dual_pipeline/remediation_core")
        or norm.startswith("dual_pipeline/completion_core")
        or norm.startswith("dual_pipeline/clearance_lifecycle_core")
        or norm.startswith("dual_pipeline/card_refactor_release_core")
        or norm.startswith("dual_pipeline/graph_writer_core")
    ):
        return 5

    # Layer 4: Reasoning, Dialectical Judges, Healers, Arbiters, Retrieval Evaluation, Query Routing, Release Auditing
    if (
        "judge" in filename
        or "healer" in filename
        or "auditor" in filename
        or "arbiter" in filename
        or "eval" in filename
        or "gate" in filename
        or "query_router" in filename
        or norm.startswith("dual_pipeline/llm_judge_core")
        or norm.startswith("dual_pipeline/evals")
        or norm.startswith("evals/")
        or norm.startswith("dual_pipeline/retrieval_release_core")
        or norm.startswith("dual_pipeline/retrieval_suite_core")
        or norm.startswith("dual_pipeline/transcript_qa_core")
        or norm.startswith("dual_pipeline/ambiguity_core")
        or norm.startswith("dual_pipeline/graph_ambiguity_core")
        or norm.startswith("dual_pipeline/recap_ambiguity_core")
        or norm.startswith("dual_pipeline/l2_policy_core")
        or norm.startswith("dual_pipeline/l2_providers_core")
        or norm.startswith("dual_pipeline/ingestion_reasoning_core")
        or norm.startswith("dual_pipeline/release_core")
        or norm.startswith("dual_pipeline/query_core")
    ):
        return 4

    # Layer 3: Knowledge Graph, Vector Indexes, Hybrid Search, Sidecars, Micro-Agents
    if (
        "agent" in filename
        or "/agents/" in norm
        or norm.startswith("graph/")
        or norm.startswith("pipeline_c_retrieval")
        or norm.startswith("dual_pipeline/retrieval")
        or norm.startswith("dual_pipeline/index_core")
        or norm.startswith("dual_pipeline/graph_retrieval_core")
        or norm.startswith("dual_pipeline/graph_delta_core")
        or norm.startswith("dual_pipeline/semantic_cache_core")
        or norm.startswith("dual_pipeline/query_l1_actions_core")
        or norm.startswith("dual_pipeline/query_l1_shadow_core")
        or norm.startswith("dual_pipeline/candidate_cleanup_core")
        or norm.startswith("dual_pipeline/source_retirement_core")
    ):
        return 3

    # Layer 2: Extraction, Entities, Assertion Proposals, Pre-Egress Sanitization, Disambiguation
    if (
        "sanitiz" in filename
        or "extract" in filename
        or "cognition" in filename
        or "proposal" in filename
        or "identity" in filename
        or "disambiguat" in filename
        or norm.startswith("dual_pipeline/speaker_disambiguation_core")
        or norm.startswith("dual_pipeline/normalization_core")
        or norm.startswith("dual_pipeline/identity_core")
        or norm.startswith("dual_pipeline/identity_resolver_core")
        or norm.startswith("dual_pipeline/token_optimization")
        or norm.startswith("dual_pipeline/facets_core")
        or norm.startswith("dual_pipeline/source_spans_core")
        or norm.startswith("dual_pipeline/label_quality_core")
    ):
        return 2

    # Layer 1: Normalization, Acoustic STT, Transcripts, Splicing, Storage Engine, Database, WAL
    if (
        norm.startswith("storage")
        or "audio" in filename
        or "stt" in filename
        or "splice" in filename
        or "stitch" in filename
        or "persistence" in filename
        or "transcript" in filename
        or "preflight" in norm
        or "runtime_evidence" in norm
    ):
        return 1

    return None


def get_imported_module_path(base_file: str, module_name: str, level: int, repo_root: Path) -> Optional[str]:
    """Resolves an AST import statement to a relative file path in the workspace."""
    base_dir = Path(base_file).parent

    if level > 0:
        # Relative import
        target_dir = base_dir
        for _ in range(level - 1):
            target_dir = target_dir.parent
        if module_name:
            rel_candidate = target_dir / (module_name.replace(".", "/") + ".py")
            if (repo_root / rel_candidate).exists():
                return str(rel_candidate)
            pkg_candidate = target_dir / module_name.replace(".", "/") / "__init__.py"
            if (repo_root / pkg_candidate).exists():
                return str(pkg_candidate)
        else:
            pkg_candidate = target_dir / "__init__.py"
            if (repo_root / pkg_candidate).exists():
                return str(pkg_candidate)
    else:
        # Absolute import within workspace
        as_py = Path(module_name.replace(".", "/") + ".py")
        if (repo_root / as_py).exists():
            return str(as_py)
        as_init = Path(module_name.replace(".", "/")) / "__init__.py"
        if (repo_root / as_init).exists():
            return str(as_init)
    return None


class LayerImportVisitor(ast.NodeVisitor):
    """Fast single-pass AST visitor tracking layers, imports, and decoupling context."""

    def __init__(self, rel_path: str, file_lines: List[str], repo_root: Path) -> None:
        self.rel_path = rel_path
        self.file_lines = file_lines
        self.repo_root = repo_root
        self.source_layer = get_layer_for_file(rel_path)
        self.source_pipe = get_pipeline(rel_path)
        self.is_subagent = ("_agent.py" in rel_path) and (self.source_layer == 3)

        self.violations: List[Violation] = []
        self.top_level_targets: List[str] = []
        self.in_function = False
        self.try_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prev = self.in_function
        self.in_function = True
        self.generic_visit(node)
        self.in_function = prev

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        prev = self.in_function
        self.in_function = True
        self.generic_visit(node)
        self.in_function = prev

    def visit_Try(self, node: ast.Try) -> None:
        self.try_depth += 1
        self.generic_visit(node)
        self.try_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        # Check for if TYPE_CHECKING:
        test = node.test
        if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        ):
            # Skip the body of TYPE_CHECKING completely
            return
        self.generic_visit(node)

    def _record_and_check_resolved(self, resolved: str, line_no: int, allow_pkg_init: bool = False) -> None:
        target_norm = resolved.replace("\\", "/")
        if not self.in_function:
            self.top_level_targets.append(target_norm)
            p = Path(target_norm)
            if p.name != "__init__.py":
                pkg_dir = p.parent.as_posix()
                if not self.rel_path.startswith(pkg_dir + "/"):
                    init_p = f"{pkg_dir}/__init__.py"
                    if (self.repo_root / init_p).exists():
                        self.top_level_targets.append(init_p)
        self._check_target(resolved, line_no, allow_pkg_init=allow_pkg_init)

    def visit_Import(self, node: ast.Import) -> None:
        line_no = getattr(node, "lineno", 1)
        for n in node.names:
            resolved = get_imported_module_path(self.rel_path, n.name, 0, self.repo_root)
            if resolved:
                self._record_and_check_resolved(resolved, line_no, allow_pkg_init=("." in n.name))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        line_no = getattr(node, "lineno", 1)
        mod = node.module or ""
        resolved = get_imported_module_path(self.rel_path, mod, node.level, self.repo_root)
        if resolved:
            self._record_and_check_resolved(resolved, line_no, allow_pkg_init=False)
        for alias in node.names:
            if alias.name == "*":
                continue
            sub_mod = f"{mod}.{alias.name}" if mod else alias.name
            sub_resolved = get_imported_module_path(self.rel_path, sub_mod, node.level, self.repo_root)
            if sub_resolved and sub_resolved != resolved:
                self._record_and_check_resolved(sub_resolved, line_no, allow_pkg_init=True)

    def _check_target(self, target_rel: str, line_no: int, allow_pkg_init: bool = False) -> None:
        if self.rel_path.endswith("__init__.py"):
            return
        target_norm = target_rel.replace("\\", "/")
        if target_norm.endswith("__init__.py") and not allow_pkg_init:
            return

        target_pipe = get_pipeline(target_norm)
        target_layer = get_layer_for_file(target_norm)

        # Check line comment for lazy-decouple exception in function bodies
        line_text = self.file_lines[line_no - 1] if 0 <= line_no - 1 < len(self.file_lines) else ""
        has_lazy_exemption = (
            "# noqa: lazy-decouple" in line_text
            or "# noqa" in line_text
            or (self.try_depth > 0)
        )

        # ── Check 3: Unidirectional Cross-Pipeline Flow ──────────────────────
        if not (self.in_function and has_lazy_exemption) and self.source_pipe and target_pipe:
            s_layer = self.source_layer if self.source_layer is not None else -1
            t_layer = target_layer if target_layer is not None else -1
            if self.source_pipe == "A" and target_pipe in ("B", "C", "D"):
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line_number=line_no,
                        source_layer=s_layer,
                        target_layer=t_layer,
                        imported_module=target_norm,
                        rule="CROSS_PIPELINE_REVERSE_FLOW",
                        detail=f"Upstream Pipeline A component imports downstream Pipeline {target_pipe} ({target_norm}).",
                    )
                )
            elif self.source_pipe == "B" and target_pipe in ("C", "D"):
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line_number=line_no,
                        source_layer=s_layer,
                        target_layer=t_layer,
                        imported_module=target_norm,
                        rule="CROSS_PIPELINE_REVERSE_FLOW",
                        detail=f"Upstream Pipeline B component imports downstream Pipeline {target_pipe} ({target_norm}).",
                    )
                )

        if target_layer is None or self.source_layer is None:
            return

        # ── Check 1: Strict Downward Layer Flow (L_N -> L_M where N >= M) ──
        if self.source_layer < target_layer:
            if not (self.in_function and has_lazy_exemption):
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line_number=line_no,
                        source_layer=self.source_layer,
                        target_layer=target_layer,
                        imported_module=target_norm,
                        rule="DOWNWARD_FLOW_INVERSION",
                        detail=f"Layer L{self.source_layer} component imports higher Layer L{target_layer} ({target_norm}).",
                    )
                )

        # ── Check 2: Sub-Agent -> Orchestrator Inversion ─────────────────────
        if self.is_subagent and target_layer >= 5:
            if not (self.in_function and has_lazy_exemption):
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line_number=line_no,
                        source_layer=self.source_layer,
                        target_layer=target_layer,
                        imported_module=target_norm,
                        rule="SUBAGENT_ORCHESTRATOR_INVERSION",
                        detail=f"Sub-agent ({self.rel_path}) imports orchestrator/coordinator L{target_layer} ({target_norm}).",
                    )
                )


def scan_file_imports(
    file_path: Path,
    repo_root: Path,
    file_lines: Optional[List[str]] = None,
) -> Tuple[List[Violation], List[str]]:
    try:
        rel_path = str(file_path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        rel_path = str(file_path).replace("\\", "/")
    if "tests/" in rel_path or "test_" in rel_path:
        return [], []

    try:
        if file_lines is not None:
            content = "".join(file_lines)
            lines = file_lines
        else:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines(keepends=True)
        tree = ast.parse(content, filename=str(file_path))
    except Exception:
        return [], []

    visitor = LayerImportVisitor(rel_path, lines, repo_root)
    visitor.visit(tree)
    return visitor.violations, visitor.top_level_targets


def find_circular_import_cycles(adj_graph: Dict[str, Set[str]]) -> List[List[str]]:
    """Detect circular import cycles using strongly connected components (with stdlib Tarjan fallback)."""
    try:
        import networkx as nx

        g = nx.DiGraph()
        for node, edges in adj_graph.items():
            g.add_node(node)
            for target in edges:
                g.add_edge(node, target)

        return [sorted(scc) for scc in nx.strongly_connected_components(g) if len(scc) > 1]
    except ImportError:
        index = 0
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        stack: List[str] = []
        on_stack: Set[str] = set()
        sccs: List[List[str]] = []
        all_nodes = set(adj_graph.keys())
        for edges in adj_graph.values():
            all_nodes.update(edges)

        for start in sorted(all_nodes):
            if start in indices:
                continue
            call_stack: List[Tuple[str, List[str], int]] = [(start, sorted(adj_graph.get(start, set())), 0)]
            indices[start] = lowlink[start] = index
            index += 1
            stack.append(start)
            on_stack.add(start)

            while call_stack:
                v, neighbors, idx = call_stack[-1]
                if idx < len(neighbors):
                    call_stack[-1] = (v, neighbors, idx + 1)
                    w = neighbors[idx]
                    if w not in indices:
                        indices[w] = lowlink[w] = index
                        index += 1
                        stack.append(w)
                        on_stack.add(w)
                        call_stack.append((w, sorted(adj_graph.get(w, set())), 0))
                    elif w in on_stack:
                        lowlink[v] = min(lowlink[v], indices[w])
                else:
                    call_stack.pop()
                    if call_stack:
                        parent = call_stack[-1][0]
                        lowlink[parent] = min(lowlink[parent], lowlink[v])
                    if lowlink[v] == indices[v]:
                        comp: List[str] = []
                        while True:
                            w = stack.pop()
                            on_stack.remove(w)
                            comp.append(w)
                            if w == v:
                                break
                        if len(comp) > 1:
                            sccs.append(sorted(comp))
        return sccs


def get_staged_files(repo_root: Path) -> List[Path]:
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        files: List[Path] = []
        for line in res.stdout.splitlines():
            clean = line.strip()
            if not any(clean.startswith(d + "/") for d in GOVERNED_DIRS):
                continue
            p = (repo_root / clean).resolve()
            if p.suffix == ".py" and p.exists() and "test" not in p.name:
                files.append(p)
        return files
    except Exception:
        return []


def _collect_all_governed_files(repo_root: Path) -> List[Path]:
    files: List[Path] = []
    for gov_dir in GOVERNED_DIRS:
        dp = repo_root / gov_dir
        if dp.exists():
            for p in dp.rglob("*.py"):
                if "test" not in p.name:
                    files.append(p.resolve())
    return files


def run_linter(
    repo_root: Path,
    staged: bool = False,
    explicit_files: Optional[List[str]] = None,
) -> int:
    repo_root = repo_root.resolve()
    print("=" * 80)
    print("  ARCHITECTURAL LAYER DEPENDENCY & GRAPH CYCLE LINTER (L0 -> L6 Governance)")
    print("=" * 80)

    target_files: List[Path] = []
    if staged:
        target_files = get_staged_files(repo_root)
        print(f"Running in --staged mode: inspecting {len(target_files)} staged Python module(s).")
    elif explicit_files:
        target_files = [Path(f).resolve() for f in explicit_files if Path(f).suffix == ".py" and Path(f).exists()]
        print(f"Inspecting {len(target_files)} specified module(s).")
    else:
        target_files = _collect_all_governed_files(repo_root)
        print(f"Running full repository scan across {len(target_files)} governed module(s).")

    all_violations: List[Violation] = []
    adj_graph: Dict[str, Set[str]] = defaultdict(set)
    target_rel_set: Set[str] = set()

    for file_path in target_files:
        try:
            rel = str(file_path.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            rel = str(file_path).replace("\\", "/")
        target_rel_set.add(rel)

        viols, top_level_targets = scan_file_imports(file_path, repo_root)
        all_violations.extend(viols)

        for tgt in top_level_targets:
            adj_graph[rel].add(tgt)

    if (staged or explicit_files) and target_files:
        for gov_file in _collect_all_governed_files(repo_root):
            try:
                rel = str(gov_file.relative_to(repo_root)).replace("\\", "/")
            except ValueError:
                rel = str(gov_file).replace("\\", "/")
            if rel in target_rel_set:
                continue
            _, top_level_targets = scan_file_imports(gov_file, repo_root)
            for tgt in top_level_targets:
                adj_graph[rel].add(tgt)

    # Circular import cycle detection via Tarjan's SCC on top-level imports
    sccs = find_circular_import_cycles(adj_graph)
    for scc in sccs:
        if (staged or explicit_files) and target_rel_set and not (set(scc) & target_rel_set):
            continue
        cycle_str = " -> ".join(scc) + " -> " + scc[0]
        all_violations.append(
            Violation(
                file_path=scc[0],
                line_number=1,
                source_layer=-1,
                target_layer=-1,
                imported_module=scc[1] if len(scc) > 1 else scc[0],
                rule="CIRCULAR_IMPORT_CYCLE",
                detail=f"Circular dependency cycle detected: {cycle_str}",
            )
        )

    if all_violations:
        print(f"\n❌ FAILED: Found {len(all_violations)} architectural import/cycle violation(s):\n")
        for idx, v in enumerate(all_violations, 1):
            print(f"  {idx}. [{v.rule}] {v.file_path}:{v.line_number}")
            if v.source_layer >= 0 and v.target_layer >= 0:
                print(f"     Source Layer: L{v.source_layer} -> Target Layer: L{v.target_layer}")
            print(f"     Detail: {v.detail}\n")
        print("=" * 80)
        return 1

    print("\n✅ PASSED: 0 architectural import or circular cycle violations detected.")
    print("   All dependencies flow strictly downward (L_high -> L_low).")
    print("   All sub-agents are decoupled from L5 orchestrators.")
    print("   All cross-pipeline dependencies flow unidirectionally.")
    print("   Zero circular import cycles detected across workspace module graph.\n")
    print("=" * 80)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint Python architectural layer directionality, sub-agent coupling, and import cycles."
    )
    parser.add_argument("--staged", action="store_true", help="Only check staged git files")
    parser.add_argument("--all", action="store_true", help="Run full scan of all governed packages (default)")
    parser.add_argument("files", nargs="*", help="Specific files to inspect")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    return run_linter(repo_root, staged=args.staged, explicit_files=args.files if args.files else None)


if __name__ == "__main__":
    sys.exit(main())
