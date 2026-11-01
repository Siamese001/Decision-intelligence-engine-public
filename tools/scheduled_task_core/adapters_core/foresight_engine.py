"""tools/scheduled_task_core/adapters_core/foresight_engine.py

Multi-Hop Knowledge Graph Traversal & Topological Blind-Spot Foresight Engine
for the Corner Foresight Agent (`corner_foresight_blindspot_scout`).
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

CAUSAL_DOMINO_PREDICATES: frozenset[str] = frozenset({
    "DEPENDS_ON", "CONSTRAINS", "DELIVERS", "OWNS_OR_LEADS", "CONTRIBUTES_TO",
    "SUPPORTED_BY", "ROUTES_TO", "GOVERNS", "HAS_RISK", "TRANSITIONS_TO",
    "AFFILIATED_WITH", "HAS_ROLE_IN", "RELATED_TO", "REPORTS_TO",
})

HIGH_VALUE_NODE_TYPES: frozenset[str] = frozenset({
    "workstream", "deliverable", "sow", "program", "product", "stakeholder", "risk", "org",
})


def _clean_text(val: Any) -> str:
    """Sanitizes database strings to eliminate em-dashes, en-dashes, and double-hyphen spacers."""
    return (
        str(val or "")
        .replace("\u2014", " - ")
        .replace("\u2013", "-")
        .replace(" -- ", " - ")
        .strip()
    )


@dataclass
class ForesightBlindSpot:
    """Represents a multi-hop graph-grounded blind spot with domino blast radius."""

    blindspot_id: str
    pillar: str
    issue: str
    title: str
    severity: str
    urgency_score: float
    domino_blast_radius_score: float
    epistemic_fragility_score: float
    structural_bridge_score: float
    why_off_radar: str
    domino_effect_14_to_45d: str
    multi_hop_paths: list[str] = field(default_factory=list)
    evidence_citations: list[str] = field(default_factory=list)
    impacted_entities: list[str] = field(default_factory=list)
    preemptive_move_15m: str = ""
    file: str = "storage/CANONICAL_GRAPH.db"

    @property
    def composite_foresight_score(self) -> float:
        return round(
            0.30 * self.urgency_score
            + 0.30 * self.domino_blast_radius_score
            + 0.20 * self.epistemic_fragility_score
            + 0.20 * self.structural_bridge_score,
            3,
        )

    def to_finding_dict(self) -> dict[str, Any]:
        return {
            "blindspot_id": self.blindspot_id,
            "pillar": self.pillar,
            "file": self.file,
            "issue": self.issue,
            "title": self.title,
            "description": f"{self.title} (Score: {self.composite_foresight_score:.2f}, Impacted: {len(self.impacted_entities)} entities)",
            "severity": self.severity,
            "composite_foresight_score": self.composite_foresight_score,
            "urgency_score": round(self.urgency_score, 3),
            "domino_blast_radius_score": round(self.domino_blast_radius_score, 3),
            "epistemic_fragility_score": round(self.epistemic_fragility_score, 3),
            "structural_bridge_score": round(self.structural_bridge_score, 3),
            "why_off_radar": self.why_off_radar,
            "domino_effect_14_to_45d": self.domino_effect_14_to_45d,
            "multi_hop_paths": self.multi_hop_paths,
            "evidence_citations": self.evidence_citations,
            "impacted_entities": self.impacted_entities,
            "preemptive_move_15m": self.preemptive_move_15m,
        }


def _read_pair_graph(pair_file: Path) -> Path | None:
    if not pair_file.is_file():
        return None
    try:
        data = json.loads(pair_file.read_text(encoding="utf-8"))
        g_rel = data.get("graph_path")
        return (pair_file.parent / g_rel).resolve() if g_rel else None
    except Exception:
        return None


def resolve_target_graph_db(fixture_root: Path) -> Path | None:
    """Locates the SQLite Knowledge Graph inside a workspace or test sandbox."""
    candidates: list[Path] = []
    for rel in (".local/dual_pipeline/active_pair.json", "storage/runtime/active_pair.json"):
        resolved = _read_pair_graph(fixture_root / rel)
        if resolved:
            candidates.append(resolved)
    candidates.extend([
        fixture_root / "graph.db",
        fixture_root / "graph.sqlite",
        fixture_root / "storage" / "CANONICAL_GRAPH.db",
        fixture_root / "graph" / "nee_graph.sqlite",
    ])
    for c in candidates:
        if c.is_file():
            return c
    return None


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return row is not None


def _fetch_table_dicts(conn: Any, sql_literal: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql_literal)
    cols = [str(d[0]) for d in (cur.description or [])]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _resolve_chain(start: str, raw_map: dict[str, str]) -> str:
    curr = start
    seen: set[str] = set()
    while curr in raw_map and curr not in seen:
        seen.add(curr)
        curr = raw_map[curr]
    return curr


def _load_redirects(conn: Any) -> dict[str, str]:
    if not _table_exists(conn, "entity_redirect"):
        return {}
    rows = _fetch_table_dicts(conn, "SELECT * FROM entity_redirect")
    raw = {
        str(r.get("from_node_id", "")): str(r.get("to_node_id", ""))
        for r in rows
        if r.get("from_node_id") and r.get("to_node_id") and int(r.get("active", 1) or 1) == 1
    }
    return {k: _resolve_chain(k, raw) for k in raw}


def _load_nodes(conn: Any, redirects: dict[str, str]) -> dict[str, dict[str, Any]]:
    if not _table_exists(conn, "node"):
        return {}
    rows = _fetch_table_dicts(conn, "SELECT * FROM node")
    nodes: dict[str, dict[str, Any]] = {}
    for r in rows:
        nid = str(r.get("node_id") or r.get("id") or "")
        canon_id = redirects.get(nid, nid)
        if nid and (canon_id not in nodes or nid == canon_id):
            label = r.get("canonical_name") or r.get("label") or canon_id
            act = r.get("active")
            nodes[canon_id] = {
                "node_id": canon_id,
                "name": _clean_text(label),
                "node_type": str(r.get("node_type") or "entity").lower(),
                "active": int(act) if act is not None else 1,
            }
    return nodes


def _load_evidence_counts(conn: Any) -> dict[str, int]:
    if not _table_exists(conn, "assertion_evidence"):
        return {}
    rows = conn.execute(
        "SELECT assertion_id, COUNT(*) FROM assertion_evidence GROUP BY assertion_id"
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows if r[0]}


def _is_canonical_assertion_row(r: dict[str, Any]) -> bool:
    if "canonical_status" in r and "canonical_eligible" in r:
        if r.get("canonical_status") != "canonical" or int(r.get("canonical_eligible") or 0) != 1:
            return False
    if "currentness_state" in r and r.get("currentness_state") not in ("current", None, ""):
        return False
    if r.get("superseded_by_assertion_id"):
        return False
    return True


def _row_to_assertion(r: dict[str, Any], redirects: dict[str, str], ev_counts: dict[str, int]) -> dict[str, Any]:
    aid = str(r.get("assertion_id") or r.get("id") or "")
    subj_raw = str(r.get("subject_node_id") or r.get("entity_id") or "")
    obj_raw = str(r.get("object_node_id") or "")
    conf = r.get("confidence_score")
    return {
        "assertion_id": aid,
        "subject_id": redirects.get(subj_raw, subj_raw),
        "predicate": str(r.get("predicate") or "HAS_CLAIM_TEXT"),
        "object_id": redirects.get(obj_raw, obj_raw) if obj_raw else "",
        "claim_text": _clean_text(r.get("object_literal") or r.get("claim") or r.get("scope_text") or ""),
        "assertion_type": str(r.get("assertion_type") or "extracted_claim"),
        "confidence": float(conf) if conf is not None else 0.85,
        "valid_to": _clean_text(r.get("valid_to") or ""),
        "created_at": _clean_text(r.get("created_at") or ""),
        "source_id": _clean_text(r.get("primary_source_id") or ""),
        "evidence_count": ev_counts.get(aid, 1),
    }


def _load_canonical_assertions(
    conn: Any, redirects: dict[str, str], ev_counts: dict[str, int]
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "assertion"):
        return []
    rows = _fetch_table_dicts(conn, "SELECT * FROM assertion")
    return [_row_to_assertion(r, redirects, ev_counts) for r in rows if _is_canonical_assertion_row(r)]


class CornerForesightGraphEngine:
    """Executes read-only multi-hop graph traversal to surface 5-pillar blind spots."""

    def __init__(self, graph_path: Path, workspace_root: Path | None = None) -> None:
        self.graph_path = graph_path
        self.workspace_root = workspace_root or graph_path.parent
        self.out_adj: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        self.in_adj: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        self.nodes: dict[str, dict[str, Any]] = {}
        self.assertions: list[dict[str, Any]] = []
        self.node_workstreams: dict[str, set[str]] = defaultdict(set)

    def _index_edge(self, u: str, v: str, pred: str, aid: str) -> None:
        if not u or not v or u == v:
            return
        self.out_adj[u].append((v, pred, aid))
        self.in_adj[v].append((u, pred, aid))
        if self.nodes.get(v, {}).get("node_type") in ("workstream", "program", "sow"):
            self.node_workstreams[u].add(v)
        if self.nodes.get(u, {}).get("node_type") in ("workstream", "program", "sow"):
            self.node_workstreams[v].add(u)

    def _build_topology(self, conn: Any) -> dict[str, str]:
        redirects = _load_redirects(conn)
        self.nodes = _load_nodes(conn, redirects)
        ev_counts = _load_evidence_counts(conn)
        self.assertions = _load_canonical_assertions(conn, redirects, ev_counts)
        for a in self.assertions:
            self._index_edge(a["subject_id"], a["object_id"], a["predicate"], a["assertion_id"])
        return redirects

    def compute_bridge_score(self, node_id: str) -> float:
        """Computes topological bridge & bottleneck centrality across workstreams."""
        ws_count = len(self.node_workstreams.get(node_id, set()))
        degree = len(self.out_adj.get(node_id, [])) + len(self.in_adj.get(node_id, []))
        return min(1.0, round(0.35 * min(ws_count, 2) + min(degree, 15) / 50.0, 3))

    def _node_label(self, node_id: str) -> tuple[str, str, str]:
        info = self.nodes.get(node_id, {"name": node_id, "node_type": "entity"})
        return info["name"], info["node_type"], f"{info['name']} ({info['node_type']})"

    def _incident_causal_steps(self, curr: str) -> list[tuple[str, str, str, str]]:
        steps = [(nxt, p, a, "->") for nxt, p, a in self.out_adj.get(curr, []) if p in CAUSAL_DOMINO_PREDICATES]
        steps.extend((prv, p, a, "<-") for prv, p, a in self.in_adj.get(curr, []) if p in CAUSAL_DOMINO_PREDICATES)
        return steps

    def _record_domino_hop(
        self, nbr: str, pred: str, aid: str, arrow: str, depth: int, path_str: str,
        paths: list[str], impacted: list[str],
    ) -> str:
        n_name, n_type, n_label = self._node_label(nbr)
        next_path = f"{path_str} {arrow}[{pred} ({aid})] {n_label}"
        if n_type in HIGH_VALUE_NODE_TYPES or (depth == 0 and n_type not in ("source", "card")):
            paths.append(next_path)
            if n_name not in impacted:
                impacted.append(n_name)
        return next_path

    def trace_domino_blast_radius(
        self, seed_node_id: str, max_hops: int = 3
    ) -> tuple[list[str], list[str], float]:
        """Traces 1-to-k hop causal domino chains from seed_node_id."""
        if not seed_node_id:
            return [], [], 0.25
        _, _, seed_label = self._node_label(seed_node_id)
        queue: deque[tuple[str, int, str]] = deque([(seed_node_id, 0, seed_label)])
        visited: set[str] = {seed_node_id}
        paths: list[str] = []
        impacted: list[str] = []

        while queue and len(paths) < 6:
            curr, depth, path_str = queue.popleft()
            if depth < max_hops:
                for nbr, pred, aid, arrow in self._incident_causal_steps(curr):
                    if nbr not in visited:
                        visited.add(nbr)
                        next_path = self._record_domino_hop(nbr, pred, aid, arrow, depth, path_str, paths, impacted)
                        queue.append((nbr, depth + 1, next_path))

        score = min(1.0, round(0.30 + 0.18 * len(impacted) + 0.08 * len(paths), 3))
        return paths[:4], impacted[:8], score

    def _build_expiring_spot(self, a: dict[str, Any]) -> ForesightBlindSpot:
        subj = a["subject_id"]
        paths, impacted, blast = self.trace_domino_blast_radius(subj, max_hops=3)
        bridge = self.compute_bridge_score(subj)
        s_name = self.nodes.get(subj, {}).get("name", subj)
        targets = impacted or [s_name]
        fallback_path = f"{s_name} -> [{a['predicate']} ({a['assertion_id']})] -> {a['object_id'] or 'target'}"
        return ForesightBlindSpot(
            blindspot_id=f"p1-exp-{a['assertion_id'][:12]}",
            pillar="Pillar 1: Forgotten Commitments, Unclosed Loops & Expiring Decision Frames",
            issue="EXPIRING_COMMITMENT_BLINDSPOT",
            title=f"Expiring Horizon ({a['valid_to']}) on {s_name}",
            severity="HIGH",
            urgency_score=0.92,
            domino_blast_radius_score=blast,
            epistemic_fragility_score=0.85 if a["evidence_count"] <= 1 else 0.55,
            structural_bridge_score=max(0.45, bridge),
            why_off_radar=f"Tracked under {a['assertion_id']} with expiration {a['valid_to']} and no downstream validation assertion.",
            domino_effect_14_to_45d=f"Cascades across {len(targets)} connected graph entities: {', '.join(targets[:4])}.",
            multi_hop_paths=paths or [fallback_path],
            evidence_citations=[f"{a['assertion_id']} (conf={a['confidence']:.2f}, valid_to={a['valid_to']}): {a['claim_text'][:140]}"],
            impacted_entities=targets,
            preemptive_move_15m=f"Lock in a 15-minute resolution check with owners of {s_name} before {a['valid_to']}.",
        )

    def _discover_pillar1_expiring_and_actions(self, conn: Any) -> list[ForesightBlindSpot]:
        expiring_assertions = sorted(
            (a for a in self.assertions if a["valid_to"]),
            key=lambda x: (x["valid_to"] >= "2026-10-01", x["valid_to"]),
            reverse=True,
        )
        spots = [self._build_expiring_spot(a) for a in expiring_assertions[:2]]
        if not _table_exists(conn, "p0_p1_action_register"):
            return spots[:3]
        rows = conn.execute(
            "SELECT action_id, action_title, owner, due_date, blocked_until, recommended_next_step "
            "FROM p0_p1_action_register WHERE status != 'CLOSED' LIMIT 2"
        ).fetchall()
        for r in rows:
            act_id, title, owner = _clean_text(r[0]), _clean_text(r[1]), _clean_text(r[2])
            due, blocked, step = _clean_text(r[3]), _clean_text(r[4]), _clean_text(r[5])
            spots.append(ForesightBlindSpot(
                blindspot_id=f"p1-act-{act_id}",
                pillar="Pillar 1: Forgotten Commitments, Unclosed Loops & Expiring Decision Frames",
                issue="UNCLOSED_P0_P1_ACTION_LOOP",
                title=f"Unclosed Action Gate: {title} (Owner: {owner})",
                severity="HIGH",
                urgency_score=0.88,
                domino_blast_radius_score=0.75,
                epistemic_fragility_score=0.70,
                structural_bridge_score=0.65,
                why_off_radar=f"Action {act_id} remains open (due: {due or 'imminent'}, blocked_until: {blocked or 'active'}).",
                domino_effect_14_to_45d=f"Stalls downstream sign-off if {owner} does not clear the dependency.",
                multi_hop_paths=[f"{owner} -> [ACTION_OWNER ({act_id})] -> {title}"],
                evidence_citations=[f"p0_p1_action_register:{act_id}: {step or title}"],
                impacted_entities=[owner, title],
                preemptive_move_15m=step or f"Follow up with {owner} on {title} today.",
            ))
        return spots[:3]

    def _find_dormant_workstreams(self) -> tuple[list[tuple[str, int, str]], str]:
        ws_dates: dict[str, list[str]] = defaultdict(list)
        for a in self.assertions:
            if a["created_at"] and self.nodes.get(a["subject_id"], {}).get("node_type") == "workstream":
                ws_dates[a["subject_id"]].append(a["created_at"][:10])
        all_dates = sorted({d for dates in ws_dates.values() for d in dates if len(d) >= 10})
        max_date = all_dates[-1] if all_dates else ""
        cutoff = max_date[:7] + "-01" if max_date else ""
        dormant = [
            (ws_id, len(dates), max(dates))
            for ws_id, dates in ws_dates.items()
            if len(dates) >= 5 and cutoff and max(dates) < cutoff
        ]
        dormant.sort(key=lambda x: x[1], reverse=True)
        return dormant, max_date

    def _discover_pillar2_drift_and_tensions(self, conn: Any) -> list[ForesightBlindSpot]:
        spots: list[ForesightBlindSpot] = []
        dormant, max_date = self._find_dormant_workstreams()
        if dormant:
            top_ws, cnt, last_dt = dormant[0]
            ws_name = self.nodes.get(top_ws, {}).get("name", top_ws)
            paths, impacted, blast = self.trace_domino_blast_radius(top_ws, max_hops=2)
            spots.append(ForesightBlindSpot(
                blindspot_id=f"p2-drift-{top_ws[:16]}",
                pillar="Pillar 2: Silent Stakeholder Drift & Cross-Meeting Contradictions",
                issue="SILENT_WORKSTREAM_VELOCITY_DRIFT",
                title=f"Dormant Workstream Velocity Cliff: {ws_name} ({len(dormant)} workstreams silent)",
                severity="MEDIUM",
                urgency_score=0.78,
                domino_blast_radius_score=blast,
                epistemic_fragility_score=0.72,
                structural_bridge_score=self.compute_bridge_score(top_ws),
                why_off_radar=f"{ws_name} accumulated {cnt} canonical assertions but recorded zero updates since {last_dt}.",
                domino_effect_14_to_45d="Creates unaligned portfolio assumptions and stale dependencies entering quarterly review.",
                multi_hop_paths=paths or [f"{ws_name} -> [DORMANT_SINCE ({last_dt})] -> {len(dormant)} workstreams"],
                evidence_citations=[f"Workstream {top_ws}: {cnt} assertions, last_active={last_dt} vs frontier={max_date}"],
                impacted_entities=impacted or [ws_name],
                preemptive_move_15m=f"Confirm with portfolio leadership whether {ws_name} and {len(dormant) - 1} peer workstreams are archived or active.",
            ))
        if _table_exists(conn, "cross_bu_tension"):
            for r in conn.execute(
                "SELECT tension_id, source_bu, target_bu, description, mitigation_strategy FROM cross_bu_tension ORDER BY severity_score DESC LIMIT 1"
            ).fetchall():
                spots.append(ForesightBlindSpot(
                    blindspot_id=f"p2-tension-{r[0]}",
                    pillar="Pillar 2: Silent Stakeholder Drift & Cross-Meeting Contradictions",
                    issue="CROSS_BU_REORG_TENSION_BLINDSPOT",
                    title=f"Cross-BU Transition Friction: {r[1]} vs {r[2]}",
                    severity="HIGH",
                    urgency_score=0.84,
                    domino_blast_radius_score=0.80,
                    epistemic_fragility_score=0.68,
                    structural_bridge_score=0.85,
                    why_off_radar=str(r[3]),
                    domino_effect_14_to_45d=f"Unresolved handoff friction between {r[1]} and {r[2]} stalls shared platform delivery.",
                    multi_hop_paths=[f"{r[1]} -> [CROSS_BU_TENSION ({r[0]})] -> {r[2]}"],
                    evidence_citations=[f"cross_bu_tension:{r[0]}: {r[3]}"],
                    impacted_entities=[str(r[1]), str(r[2])],
                    preemptive_move_15m=str(r[4]),
                ))
        return spots

    def _is_single_source_strategic_bet(self, a: dict[str, Any]) -> bool:
        if a["evidence_count"] > 1:
            return False
        if a["assertion_type"] in ("decision", "assumption", "hypothesis"):
            return True
        return a["predicate"] in ("DEPENDS_ON", "CONSTRAINS", "DELIVERS", "HAS_RISK")

    def _discover_pillar3_single_source_bridges(self) -> list[ForesightBlindSpot]:
        candidates: list[tuple[float, dict[str, Any], list[str], list[str], float, float]] = []
        for a in self.assertions:
            if not self._is_single_source_strategic_bet(a):
                continue
            subj = a["subject_id"]
            bridge = self.compute_bridge_score(subj)
            paths, impacted, blast = self.trace_domino_blast_radius(subj, max_hops=3)
            if impacted or a["assertion_type"] in ("assumption", "hypothesis", "decision"):
                candidates.append((blast + bridge, a, paths, impacted, blast, bridge))
        candidates.sort(key=lambda x: x[0], reverse=True)
        spots: list[ForesightBlindSpot] = []
        for _, a, paths, impacted, blast, bridge in candidates[:2]:
            s_name = self.nodes.get(a["subject_id"], {}).get("name", a["subject_id"])
            targets = impacted or [s_name]
            fallback = f"{s_name} -> [{a['predicate']} ({a['assertion_id']})] -> {a['object_id'] or 'claim'}"
            spots.append(ForesightBlindSpot(
                blindspot_id=f"p3-fragile-{a['assertion_id'][:12]}",
                pillar="Pillar 3: Uncorroborated Single-Source Bets & Fragile Dependencies",
                issue="SINGLE_SOURCE_BRIDGE_FRAGILITY",
                title=f"Single-Source Strategic Dependency on Bridge Node {s_name}",
                severity="HIGH" if blast >= 0.60 else "MEDIUM",
                urgency_score=0.80,
                domino_blast_radius_score=blast,
                epistemic_fragility_score=0.90,
                structural_bridge_score=max(0.50, bridge),
                why_off_radar=f"High-conviction {a['assertion_type']} ({a['assertion_id']}) rests on a single uncorroborated source (evidence_count=1).",
                domino_effect_14_to_45d=f"If invalidated, disrupts {len(targets)} downstream nodes: {', '.join(targets[:4])}.",
                multi_hop_paths=paths or [fallback],
                evidence_citations=[f"{a['assertion_id']} (type={a['assertion_type']}, evidence_count=1): {a['claim_text'][:140]}"],
                impacted_entities=targets,
                preemptive_move_15m=f"Secure written second-source corroboration for {s_name} ({a['assertion_id']}) before next milestone gate.",
            ))
        return spots

    def _discover_pillar4_operational_cliffs(self) -> list[ForesightBlindSpot]:
        active_nodes = [n for n in self.nodes.values() if n.get("active", 1) == 1]
        orphans = [
            n["node_id"] for n in active_nodes
            if not self.out_adj.get(n["node_id"]) and not self.in_adj.get(n["node_id"])
        ]
        if len(active_nodes) < 50 or len(orphans) < 25:
            return []
        ratio = len(orphans) / max(1, len(active_nodes))
        return [ForesightBlindSpot(
            blindspot_id="p4-orphan-trajectory",
            pillar="Pillar 4: Approaching Operational & Trajectory Cliffs",
            issue="ORPHAN_NODE_TRAJECTORY_CLIFF",
            title=f"Disconnected Graph Node Accumulation ({len(orphans)} orphan nodes, {ratio:.1%})",
            severity="MEDIUM",
            urgency_score=0.72,
            domino_blast_radius_score=0.60,
            epistemic_fragility_score=0.65,
            structural_bridge_score=0.50,
            why_off_radar="Primary integrity checks pass while unlinked entity nodes quietly accumulate across batch intakes.",
            domino_effect_14_to_45d="Inflates entity-resolution candidate lists and dilutes multi-hop graph traversal recall.",
            multi_hop_paths=[f"Graph Topology ({len(active_nodes)} active nodes) -> [DEGREE_ZERO] -> {len(orphans)} disconnected nodes"],
            evidence_citations=[f"Active nodes={len(active_nodes)}, degree_zero_orphans={len(orphans)} ({ratio:.1%})"],
            impacted_entities=["Knowledge Graph Entity Resolution", "Multi-Hop Retrieval Recall"],
            preemptive_move_15m="Schedule an out-of-band graph hygiene pass to merge or link disconnected entity nodes.",
        )]

    def synthesize_unasked_questions(self, spots: list[ForesightBlindSpot]) -> list[str]:
        """Synthesizes Top 3 Unasked Executive Questions from top-scored multi-hop blind spots."""
        questions: list[str] = []
        for idx, s in enumerate(spots[:3], 1):
            targets = ", ".join(s.impacted_entities[:3]) if s.impacted_entities else s.title
            questions.append(
                f"Q{idx} ({s.title}): Given that {s.why_off_radar[0].lower() + s.why_off_radar[1:]} "
                f"what is our concrete owner and date this week to protect {targets} before the 14-to-45 day domino impact hits?"
            )
        while len(questions) < 3:
            questions.append(
                f"Q{len(questions) + 1} (Portfolio Horizon Alignment): Which cross-workstream dependencies expiring in the next 30 days lack a designated second-source verification owner?"
            )
        return questions

    def run_discovery(self) -> dict[str, Any]:
        """Executes full 5-pillar read-only graph discovery and seals zero-mutation proof."""
        pre_sha256 = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()
        conn = create_sqlite_conn(self.graph_path, readonly=True)
        try:
            redirects = self._build_topology(conn)
            spots = (
                self._discover_pillar1_expiring_and_actions(conn)
                + self._discover_pillar2_drift_and_tensions(conn)
                + self._discover_pillar3_single_source_bridges()
                + self._discover_pillar4_operational_cliffs()
            )
        finally:
            conn.close()

        post_sha256 = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()
        spots.sort(key=lambda s: s.composite_foresight_score, reverse=True)
        total_paths = sum(len(s.multi_hop_paths) for s in spots)
        bridges = sum(1 for n in self.nodes if self.compute_bridge_score(n) >= 0.65)

        return {
            "pre_graph_sha256": pre_sha256,
            "post_graph_sha256": post_sha256,
            "zero_mutation_verified": pre_sha256 == post_sha256,
            "graph_traversal_telemetry": {
                "graph_path": f"{self.graph_path.parent.name}/{self.graph_path.name}",
                "nodes_traversed": len(self.nodes),
                "redirects_resolved": len(redirects),
                "canonical_assertions_evaluated": len(self.assertions),
                "causal_edges_indexed": sum(len(v) for v in self.out_adj.values()),
                "multi_hop_paths_traced": total_paths,
                "structural_bridge_nodes_detected": bridges,
            },
            "blind_spots": [s.to_finding_dict() for s in spots],
            "top_3_unasked_questions": self.synthesize_unasked_questions(spots),
        }


def render_foresight_radar_markdown(report: dict[str, Any], radar_date: str = "10/30/2026") -> str:
    """Renders the 5-pillar blind-spot radar brief in clean Markdown without em-dashes."""
    tel = report.get("graph_traversal_telemetry", {})
    lines = [
        f"# Look-Around-the-Corner Foresight & Blind-Spot Radar Brief ({radar_date})",
        "",
        "- **Task ID**: `corner_foresight_blindspot_scout`",
        f"- **Radar Date**: `{radar_date}`",
        f"- **Pre-Flight Graph SHA-256 (`PRE_GRAPH_SHA256`)**: `{report.get('pre_graph_sha256', '')}`",
        f"- **Post-Flight Graph SHA-256 (`POST_GRAPH_SHA256`)**: `{report.get('post_graph_sha256', '')}` (`ZERO_MUTATION_VERIFIED={report.get('zero_mutation_verified')}`)",
        f"- **Graph Traversal Depth**: `{tel.get('nodes_traversed', 0)}` nodes, `{tel.get('canonical_assertions_evaluated', 0)}` canonical assertions, `{tel.get('causal_edges_indexed', 0)}` causal edges, `{tel.get('multi_hop_paths_traced', 0)}` multi-hop domino paths traced",
        "",
        "---",
    ]
    for idx, spot in enumerate(report.get("blind_spots", []), 1):
        lines.extend([
            "",
            f"## Blind Spot {idx}: {spot['title']}",
            f"- **Category Pillar**: **{spot['pillar']}**",
            f"- **Composite Foresight Score**: `{spot['composite_foresight_score']:.2f}` (Urgency: `{spot['urgency_score']:.2f}`, Domino Blast Radius: `{spot['domino_blast_radius_score']:.2f}`, Epistemic Fragility: `{spot['epistemic_fragility_score']:.2f}`, Bridge Centrality: `{spot['structural_bridge_score']:.2f}`)",
            f"- **Why It's Off Your Radar Right Now**: {spot['why_off_radar']}",
            f"- **The 14-to-45 Day Domino Effect**: {spot['domino_effect_14_to_45d']}",
            "- **Multi-Hop Graph Traversal Paths**:",
        ])
        for p in spot.get("multi_hop_paths", []):
            lines.append(f"  - `{p}`")
        lines.append("- **Concrete Evidence Trail**:")
        for ev in spot.get("evidence_citations", []):
            lines.append(f"  - `{ev}`")
        lines.append(f"- **The Preemptive Move (15-Minute Action)**: {spot['preemptive_move_15m']}")
        lines.append("")
        lines.append("---")

    lines.extend(["", "## Pillar 5 Horizon Synthesis: Top 3 Unasked Questions for This Week's Meetings", ""])
    for idx, q in enumerate(report.get("top_3_unasked_questions", []), 1):
        lines.append(f"{idx}. {q}")
    return "\n".join(lines) + "\n"
