"""Multi-Hop Query Navigator Agent for Iterative Graph Frontier Expansion.

Executes a bounded Observe -> Decide -> Act -> Evaluate loop (max_steps=3, max_tool_calls=6)
over a read-only graph snapshot or assertion set to resolve multi-hop coverage gaps across
bridge predicates (ADDRESSES, DEPENDS_ON, CONTRIBUTES_TO, SUPPORTED_BY, CONTRADICTS, SUPERSEDES).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from dual_pipeline.a2a.protocol import ClaimCheckPointer
from dual_pipeline.agent_architecture_core.outcome_streaming import stream_navigator_outcome
from dual_pipeline.agent_architecture_core.runtime_loop import (
    AgentRuntimeLoop,
    LoopTerminalOutcome,
    RuntimeLoopConfig,
)
from dual_pipeline.core import connect_sqlite

DEFAULT_BRIDGE_PREDICATES: Tuple[str, ...] = (
    "ADDRESSES",
    "DEPENDS_ON",
    "CONTRIBUTES_TO",
    "SUPPORTED_BY",
    "CONTRADICTS",
    "SUPERSEDES",
    "RELATED_TO",
    "HAS_RISK",
    "CONSTRAINS",
    "OWNS_OR_LEADS",
    "DELIVERS",
    "GOVERNS",
    "VALIDATES",
    "USES_PATTERN",
    "HAS_ROLE_IN",
    "CANDIDATE_FOR",
    "CONTEXT_FOR",
    "CONTAINS",
    "AFFILIATED_WITH",
    "REPORTS_TO",
    "TRANSITIONS_TO",
)

_SQ_STOPWORDS = {
    "which", "what", "where", "when", "does", "how", "connect", "address",
    "depend", "contribute", "pain", "point", "initiative", "workstream",
    "capability", "governed", "paths", "reach", "from", "with", "that", "this",
}


@dataclass(frozen=True)
class HopExpansionRecord:
    """Telemetry record for a single hop expansion step."""

    hop_index: int
    frontier_in: List[str]
    frontier_out: List[str]
    assertions_added: int
    predicates_traversed: List[str]
    coverage_ratio: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NavigationResult:
    """Verified multi-hop expansion result produced by MultiHopQueryNavigatorAgent."""

    query: str
    seed_entities: List[str]
    expanded_entities: List[str]
    expanded_assertions: List[Dict[str, Any]]
    hop_records: List[HopExpansionRecord]
    resolved_subquestions: List[str]
    unresolved_subquestions: List[str]
    coverage_ratio: float
    steps_executed: int
    tool_calls_used: int
    terminal_outcome: str
    claim_checks: List[Dict[str, Any]]
    quarantined_filtered_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "seed_entities": list(self.seed_entities),
            "expanded_entities": list(self.expanded_entities),
            "expanded_assertions": list(self.expanded_assertions),
            "hop_records": [h.to_dict() for h in self.hop_records],
            "resolved_subquestions": list(self.resolved_subquestions),
            "unresolved_subquestions": list(self.unresolved_subquestions),
            "coverage_ratio": round(self.coverage_ratio, 4),
            "steps_executed": self.steps_executed,
            "tool_calls_used": self.tool_calls_used,
            "terminal_outcome": self.terminal_outcome,
            "claim_checks": list(self.claim_checks),
            "quarantined_filtered_count": self.quarantined_filtered_count,
        }


def _is_quarantined_assertion(row: Mapping[str, Any]) -> bool:
    """Blocks INTERNAL_ADVISORY or quarantined shadow records from entering query frontier."""
    ns = str(row.get("namespace", "")).strip().upper()
    if ns == "INTERNAL_ADVISORY" or bool(row.get("is_quarantined", False)):
        return True
    return float(row.get("stealth_score", 0.0) or 0.0) >= 1.0


def _load_canonical_assertion_table(conn: Any, tables: set[str]) -> List[Dict[str, Any]]:
    node_names = (
        {str(nr[0]): str(nr[1]) for nr in conn.execute("SELECT node_id, canonical_name FROM node").fetchall() if nr[0] and nr[1]}
        if "node" in tables else {}
    )
    rows = []
    for r in conn.execute(
        "SELECT assertion_id, subject_node_id, predicate, object_node_id, object_literal, scope_text "
        "FROM assertion WHERE retrieval_eligible = 1 AND currentness_state = 'current'"
    ).fetchall():
        d = dict(r)
        d["subject_name"] = node_names.get(str(d.get("subject_node_id") or ""), "")
        d["object_name"] = node_names.get(str(d.get("object_node_id") or ""), "")
        d["claim_text"] = d.get("object_literal") or d.get("object_name") or d.get("scope_text") or ""
        rows.append(d)
    return rows


def _load_readonly_sqlite_assertions(db_path: Path) -> List[Dict[str, Any]]:
    """Loads assertions from a governed read-only SQLite connection."""
    if not db_path.is_file():
        raise FileNotFoundError(f"Graph database not found: {db_path}")
    conn = connect_sqlite(db_path, readonly=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "assertions" in tables:
            return [dict(r) for r in conn.execute("SELECT * FROM assertions").fetchall()]
        if "assertion" in tables:
            return _load_canonical_assertion_table(conn, tables)
        return []
    finally:
        conn.close()


def _build_claim_check(assertion: Mapping[str, Any]) -> ClaimCheckPointer:
    raw = json.dumps(dict(assertion), sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    a_id = str(assertion.get("assertion_id") or assertion.get("id") or digest[:12])
    src_id = str(assertion.get("source_id") or "src:graph:canonical")
    return ClaimCheckPointer(source_id=src_id, evidence_id=f"ev:assertion:{a_id}", content_digest=digest, locator={"table": "assertions", "assertion_id": a_id})


def _extract_required_sq_tokens(sq: str) -> List[str]:
    raw_tokens = [t.strip(".,?!'\"()") for t in sq.lower().split()]
    return [t for t in raw_tokens if ("_" in t) or (len(t) >= 5 and t not in _SQ_STOPWORDS)]


def _match_subquestions(
    subquestions: Sequence[str],
    _known_entities: Set[str],
    assertions: Sequence[Mapping[str, Any]],
) -> Tuple[List[str], List[str]]:
    """Checks which subquestions have full entity and claim coverage in the expanded subgraph."""
    if not subquestions:
        return [], []
    claims_blob = " ".join(
        f"{a.get('subject_id', '')} {a.get('predicate', '')} {a.get('object_id', '')} {a.get('claim_text', '')}"
        for a in assertions
    ).lower()

    resolved: List[str] = []
    unresolved: List[str] = []
    for sq in subquestions:
        req_tokens = _extract_required_sq_tokens(sq)
        if assertions and req_tokens and all(tok in claims_blob for tok in req_tokens):
            resolved.append(sq)
        else:
            unresolved.append(sq)
    return resolved, unresolved


def _extract_assertion_endpoints(row: Mapping[str, Any]) -> Tuple[str, str, str, str]:
    a_id = str(row.get("assertion_id") or row.get("id") or "")
    pred = str(row.get("predicate") or "").upper()
    subj = str(row.get("subject_id") or row.get("subject_node_id") or row.get("subject") or "")
    obj = str(row.get("object_id") or row.get("object_node_id") or row.get("object") or "")
    return a_id, pred, subj, obj


def _row_touches_frontier(row: Mapping[str, Any], subj: str, obj: str, frontier_lower: Set[str]) -> tuple[bool, list[str]]:
    subj_name = str(row.get("subject_name") or row.get("subject_canonical_name") or "")
    obj_name = str(row.get("object_name") or row.get("object_canonical_name") or "")
    variants = [v for v in (subj, subj_name, obj, obj_name) if v]
    touches = any(v.lower() in frontier_lower for v in variants)
    new_endpoints = [v for v in variants if v.lower() not in frontier_lower]
    return touches, new_endpoints


class MultiHopQueryNavigatorAgent:
    """Iterative multi-hop graph navigator resolving query subquestion coverage gaps."""

    def __init__(
        self,
        *,
        max_steps: int = 3,
        max_tool_calls: int = 6,
        timeout_seconds: float = 30.0,
        bridge_predicates: Sequence[str] = DEFAULT_BRIDGE_PREDICATES,
        integrator: Optional[Any] = None,
        stream_outcomes: bool = False,
    ) -> None:
        if max_steps <= 0 or max_steps > 5:
            raise ValueError("max_steps must be within [1, 5].")
        if max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be positive.")
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.bridge_predicates = tuple(p.upper() for p in bridge_predicates)
        self.integrator = integrator
        self.stream_outcomes = stream_outcomes

    def _expand_one_hop(
        self,
        frontier: Set[str],
        candidate_assertions: Sequence[Mapping[str, Any]],
        seen_ids: Set[str],
    ) -> Tuple[Set[str], List[Dict[str, Any]], List[str]]:
        next_frontier: Set[str] = set()
        added_assertions: List[Dict[str, Any]] = []
        predicates_used: List[str] = []
        frontier_lower = {f.lower() for f in frontier}

        for row in candidate_assertions:
            a_id, pred, subj, obj = _extract_assertion_endpoints(row)
            if a_id in seen_ids or pred not in self.bridge_predicates:
                continue
            touches, new_endpoints = _row_touches_frontier(row, subj, obj, frontier_lower)
            if not touches:
                continue
            seen_ids.add(a_id)
            added_assertions.append(dict(row))
            if pred not in predicates_used:
                predicates_used.append(pred)
            next_frontier.update(new_endpoints)
        return next_frontier, added_assertions, predicates_used

    def _partition_safe_assertions(
        self, assertions: Optional[Sequence[Mapping[str, Any]]], db_path: Optional[Path | str]
    ) -> Tuple[List[Mapping[str, Any]], int]:
        raw: List[Mapping[str, Any]] = list(assertions or [])
        if db_path is not None:
            raw.extend(_load_readonly_sqlite_assertions(Path(db_path)))
        safe = [r for r in raw if not _is_quarantined_assertion(r)]
        return safe, len(raw) - len(safe)

    def _execute_hop_step(
        self,
        step_idx: int,
        loop: AgentRuntimeLoop,
        active_frontier: Set[str],
        all_entities: Set[str],
        safe_assertions: Sequence[Mapping[str, Any]],
        seen_assertion_ids: Set[str],
        expanded_assertions: List[Dict[str, Any]],
        hop_records: List[HopExpansionRecord],
        claim_checks: List[ClaimCheckPointer],
        subquestions: Sequence[str],
    ) -> Tuple[Set[str], Dict[str, Any]]:
        frontier_in = sorted(active_frontier)
        next_f, new_rows, preds = self._expand_one_hop(active_frontier, safe_assertions, seen_assertion_ids)
        step_ptrs = [_build_claim_check(r) for r in new_rows]
        claim_checks.extend(step_ptrs)
        expanded_assertions.extend(new_rows)
        all_entities.update(next_f)
        task, _ = loop.dispatch_subagent_task(
            recipient_agent_id="retrieval_specialist",
            objective=f"Execute hop {step_idx} bridge traversal",
            scope_in=["assertions"],
            scope_out=["shadow_graph_nodes"],
            allowed_tools=["query_knowledge_graph"],
            deliverable={"hop": step_idx, "added": len(new_rows)},
            definition_of_done="Hop assertions verified with SHA-256 claim checks",
            claim_checks=step_ptrs,
        )
        resolved, _ = _match_subquestions(subquestions, all_entities, expanded_assertions)
        cov = len(resolved) / max(1, len(subquestions)) if subquestions else (1.0 if expanded_assertions else 0.0)
        hop_records.append(HopExpansionRecord(step_idx, frontier_in, sorted(next_f), len(new_rows), preds, round(cov, 4)))
        act_res = {
            "tool_calls_used": 2, "tokens_used": 120, "a2a_task_id": task.task_id,
            "new_rows_count": len(new_rows), "coverage_ratio": cov,
            "state_updates": {"entities": sorted(all_entities), "assertion_ids": sorted(seen_assertion_ids), "coverage_ratio": round(cov, 4)},
        }
        return next_f, act_res

    def navigate(
        self,
        *,
        query: str,
        seed_entities: Sequence[str],
        subquestions: Sequence[str] = (),
        assertions: Optional[Sequence[Mapping[str, Any]]] = None,
        db_path: Optional[Path | str] = None,
        readonly: bool = True,
    ) -> NavigationResult:
        """Executes iterative multi-hop expansion over read-only graph assertions."""
        if not readonly:
            raise PermissionError("READ_ONLY_SNAPSHOT_REQUIRED: MultiHopQueryNavigatorAgent forbids write mode.")
        if not query or not query.strip():
            raise ValueError("Query must be a non-empty string.")

        safe_assertions, quarantined_count = self._partition_safe_assertions(assertions, db_path)
        loop = AgentRuntimeLoop(
            RuntimeLoopConfig(
                agent_id="multihop_query_navigator_agent",
                goal=f"Multi-hop frontier expansion for query: {query[:80]}",
                max_steps=self.max_steps, max_tool_calls=self.max_tool_calls, timeout_seconds=self.timeout_seconds,
                allowed_tools=("query_knowledge_graph", "inspect_graph_snapshot", "read_assertion_provenance"),
            ),
            integrator=self.integrator,
            stream_outcomes=self.stream_outcomes,
            _suppress_loop_success=True,
        )
        all_entities: Set[str] = {s.strip() for s in seed_entities if s and s.strip()}
        active_frontier: Set[str] = set(all_entities)
        seen_ids: Set[str] = set()
        expanded_assertions: List[Dict[str, Any]] = []
        hop_records: List[HopExpansionRecord] = []
        claim_checks: List[ClaimCheckPointer] = []

        def act_fn(_s: Dict[str, Any], _d: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
            nonlocal active_frontier
            active_frontier, res = self._execute_hop_step(
                step_idx, loop, active_frontier, all_entities, safe_assertions,
                seen_ids, expanded_assertions, hop_records, claim_checks, subquestions,
            )
            return res

        def eval_fn(_s: Dict[str, Any], act_res: Dict[str, Any], _i: int) -> Dict[str, Any]:
            cov = float(act_res["coverage_ratio"])
            done = (cov >= 1.0 and len(expanded_assertions) > 0) or (int(act_res["new_rows_count"]) > 0 and not active_frontier and not subquestions)
            return {"done": done, "quality_score": round(cov + (0.05 * len(expanded_assertions)), 4)}

        loop_res = loop.execute(
            initial_state={"entities": sorted(all_entities), "assertion_ids": [], "coverage_ratio": 0.0},
            observe_fn=lambda _s, i: {"hop": i, "active_frontier": sorted(active_frontier)},
            decide_fn=lambda obs, i: {"action": f"hop_{i}", "delegated": i == 1, "candidates": [("expand", 0.90 if obs["active_frontier"] else 0.30), ("halt", 0.10)]},
            act_fn=act_fn,
            evaluate_fn=eval_fn,
        )
        resolved_sq, unresolved_sq = _match_subquestions(subquestions, all_entities, expanded_assertions)
        final_cov = len(resolved_sq) / max(1, len(subquestions)) if subquestions else (1.0 if expanded_assertions else 0.0)
        stream_navigator_outcome(
            self.integrator, stream_outcomes=self.stream_outcomes, query=query,
            unresolved_subquestions=unresolved_sq, coverage_ratio=final_cov, trace_id=loop_res.trace_id,
        )
        return NavigationResult(
            query=query, seed_entities=list(seed_entities), expanded_entities=sorted(all_entities),
            expanded_assertions=expanded_assertions, hop_records=hop_records, resolved_subquestions=resolved_sq,
            unresolved_subquestions=unresolved_sq, coverage_ratio=final_cov, steps_executed=loop_res.steps_executed,
            tool_calls_used=loop_res.total_tool_calls,
            terminal_outcome=loop_res.terminal_outcome.value if isinstance(loop_res.terminal_outcome, LoopTerminalOutcome) else str(loop_res.terminal_outcome),
            claim_checks=[c.to_dict() for c in claim_checks], quarantined_filtered_count=quarantined_count,
        )


QueryReasoningNavigatorAgent = MultiHopQueryNavigatorAgent

__all__ = [
    "DEFAULT_BRIDGE_PREDICATES",
    "HopExpansionRecord",
    "MultiHopQueryNavigatorAgent",
    "NavigationResult",
    "QueryReasoningNavigatorAgent",
]
