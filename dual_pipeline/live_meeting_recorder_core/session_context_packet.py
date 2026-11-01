"""
dual_pipeline/live_meeting_recorder_core/session_context_packet.py

SessionContextPacket and CopilotPrefetchBridge implementations for live meeting sessions.
Provides pre-fetched knowledge graph context, query specification builders,
and context formatting under Rule C-19 and engagement scoping.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

from .c19_firewall import C19_FORBIDDEN_MARKERS, has_c19_marker
from .copilot_connection_policy import is_assertion_in_scope
from .copilot_neighborhood import CopilotNeighborhoodStore, compute_neighborhood_chains
from .copilot_predicates import (
    PREFETCH_ACTION_PREDICATES,
    PREFETCH_DECISION_PREDICATES,
    PREFETCH_RISK_PREDICATES,
    normalize_copilot_predicate,
)
from .prefetch_query_builder import (
    DOMAIN_ACRONYM_MAP,
    build_prefetch_query_specs,
    normalize_project_terms,
)

if TYPE_CHECKING:
    from .copilot_retriever import CopilotGraphRetriever

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.session_context_packet")

__all__ = [
    "C19_FORBIDDEN_MARKERS",
    "CopilotPrefetchBridge",
    "DOMAIN_ACRONYM_MAP",
    "SessionContextPacket",
    "_find_attendee_seeds",
    "build_prefetch_query_specs",
    "has_c19_marker",
    "normalize_project_terms",
]


def _sanitize_packet_assertions(lst: List[Dict[str, Any]], engagement_id: str) -> List[Dict[str, Any]]:
    res: List[Dict[str, Any]] = []
    for d in lst:
        if not isinstance(d, dict):
            continue
        txt = str(d.get("text", "") or d.get("object_literal", "") or d.get("claim", ""))
        if has_c19_marker(txt):
            continue
        if engagement_id and not is_assertion_in_scope(d, engagement_id, None):
            continue
        res.append(d)
    return res


def _sanitize_packet_chains(lst: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    res: List[Dict[str, Any]] = []
    for item in lst:
        if not isinstance(item, dict):
            continue
        chain_str = str(item.get("chain_str", ""))
        last_claim = str(item.get("last_claim", ""))
        path_str = " ".join(str(p) for p in item.get("path", []))
        if not has_c19_marker(chain_str) and not has_c19_marker(last_claim) and not has_c19_marker(path_str):
            res.append(item)
    return res


@dataclass(frozen=True)
class SessionContextPacket:
    """Immutable packet containing pre-fetched knowledge graph context for a live meeting session."""

    project_scope: str = ""
    engagement_id: str = ""
    session_id: str = ""
    attendee_entities: List[str] = field(default_factory=list)
    graph_sha256: str = ""
    generation: int | str = 0
    stale_after: float = 0.0
    prefetch_status: str = "PENDING"
    roster: List[str] = field(default_factory=list)
    last_3_meetings: List[Dict[str, Any]] = field(default_factory=list)
    open_decisions: List[Dict[str, Any]] = field(default_factory=list)
    open_risks: List[Dict[str, Any]] = field(default_factory=list)
    open_action_items: List[Dict[str, Any]] = field(default_factory=list)
    recent_assertions: List[Dict[str, Any]] = field(default_factory=list)
    connected_chains: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_latency_ms: float = 0.0
    packet_hash: str = ""
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        object.__setattr__(self, "open_decisions", _sanitize_packet_assertions(self.open_decisions, self.engagement_id))
        object.__setattr__(self, "open_risks", _sanitize_packet_assertions(self.open_risks, self.engagement_id))
        object.__setattr__(self, "open_action_items", _sanitize_packet_assertions(self.open_action_items, self.engagement_id))
        object.__setattr__(self, "recent_assertions", _sanitize_packet_assertions(self.recent_assertions, self.engagement_id))
        object.__setattr__(self, "connected_chains", _sanitize_packet_chains(self.connected_chains))

        if not self.packet_hash:
            object.__setattr__(self, "packet_hash", self.compute_hash())

    @property
    def is_empty(self) -> bool:
        return not (
            self.open_decisions
            or self.open_risks
            or self.open_action_items
            or self.recent_assertions
            or self.connected_chains
        )

    @property
    def total_items_count(self) -> int:
        return (
            len(self.open_decisions)
            + len(self.open_risks)
            + len(self.open_action_items)
            + len(self.recent_assertions)
            + len(self.connected_chains)
        )

    def compute_hash(self) -> str:
        data = {
            "project_scope": self.project_scope,
            "engagement_id": self.engagement_id,
            "session_id": self.session_id,
            "graph_sha256": self.graph_sha256,
            "open_decisions": self.open_decisions,
            "open_risks": self.open_risks,
            "open_action_items": self.open_action_items,
            "recent_assertions": self.recent_assertions,
            "connected_chains": self.connected_chains,
        }

        def _json_serial(o: Any) -> Any:
            if isinstance(o, (set, frozenset)):
                return sorted(list(o))
            return str(o)

        encoded = json.dumps(data, sort_keys=True, default=_json_serial).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SessionContextPacket:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def empty(cls, project_scope: str = "", engagement_id: str = "", session_id: str = "") -> SessionContextPacket:
        return cls(project_scope=project_scope, engagement_id=engagement_id, session_id=session_id, prefetch_status="EMPTY")

    @staticmethod
    def _format_section(items: List[Dict[str, Any]], tag: str, sub_key: str = "", own_key: str = "") -> List[str]:
        out = []
        for it in items:
            sub = f" ({it[sub_key]})" if sub_key and it.get(sub_key) else ""
            own = f" [{it[own_key]}]" if own_key and it.get(own_key) else ""
            pred = it.get("predicate", tag) if tag == "DYNAMIC" else tag
            txt = it.get("claim") or it.get("text") or it.get("object_literal", "")
            out.append(f"[{pred}]{sub}{own} {txt}")
        return out

    def format_for_copilot_prompt(
        self, max_chars: int = 2500, salient_entities: Optional[List[str]] = None
    ) -> str:
        if self.is_empty:
            return ""
        salient_low = [s.lower() for s in (salient_entities or []) if s]

        def _prio(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if not salient_low:
                return items
            return sorted(
                items,
                key=lambda it: sum(
                    1
                    for s in salient_low
                    if s in str(it.get("claim") or it.get("text") or it.get("subject") or "").lower()
                ),
                reverse=True,
            )

        lines = [f"[Pre-Fetched Graph Context - Project: {self.project_scope or 'general'}]"]
        lines.extend(self._format_section(_prio(self.open_decisions), "DECISION", sub_key="subject"))
        lines.extend(self._format_section(_prio(self.open_risks), "RISK", sub_key="subject"))
        lines.extend(self._format_section(_prio(self.open_action_items), "ACTION", own_key="owner"))
        lines.extend(self._format_section(_prio(self.recent_assertions), "DYNAMIC"))
        for c in self.connected_chains[:5]:
            if c.get("chain_str"):
                lines.append(f"[CONNECTED] {c['chain_str']}")
        joined = "\n".join(lines)
        return joined[:max_chars] + "... [truncated]" if len(joined) > max_chars else joined


def _find_attendee_seeds(attendees: Optional[List[Any]], nodes: Dict[str, Any]) -> Set[str]:
    if not attendees or not isinstance(nodes, dict):
        return set()
    names: Set[str] = set()
    for att in attendees:
        if isinstance(att, dict):
            name_val = att.get("name")
            if name_val:
                names.add(str(name_val).strip().lower())
        elif isinstance(att, str) and att.strip():
            names.add(att.strip().lower())
    return {
        nid
        for nid, nd in nodes.items()
        if isinstance(nd, dict) and str(nd.get("canonical_name", "")).lower() in names
    }


def _resolve_project_seed(project_name: str, nodes: Dict[str, Any]) -> Set[str]:
    if not project_name or not isinstance(nodes, dict):
        return set()
    norm = project_name.lower().replace("-", "_")
    return {cand for cand in (f"project:{norm}", norm) if cand in nodes}


def _resolve_item_seeds(matched_items: Iterable[Dict[str, Any]], nodes: Dict[str, Any]) -> Set[str]:
    if not isinstance(nodes, dict):
        return set()
    seeds: Set[str] = set()
    for item in matched_items:
        s_node = item.get("subject_node_id") or item.get("subject")
        if s_node and s_node in nodes:
            seeds.add(s_node)
    return seeds


def _resolve_prefetch_seeds(
    topology: Any,
    retriever: Optional[Any],
    attendees: Optional[List[Dict[str, str]]],
    project_name: str,
    matched_items: Iterable[Dict[str, Any]],
) -> Set[str]:
    """Resolves attendee, project, and matched assertion nodes as neighborhood traversal seeds."""
    nodes = getattr(topology, "nodes", {})
    seeds = set(retriever._attendee_node_ids) if retriever and hasattr(retriever, "_attendee_node_ids") else set()
    seeds.update(_find_attendee_seeds(attendees, nodes))
    seeds.update(_resolve_project_seed(project_name, nodes))
    seeds.update(_resolve_item_seeds(matched_items, nodes))
    return seeds


def _bind_retriever_scope(ret: Optional[Any], norm_eng: str, session_id: Optional[str]) -> None:
    if not ret or not norm_eng:
        return
    if not getattr(ret, "engagement_id", None):
        ret.engagement_id = norm_eng
    if session_id and not getattr(ret, "session_id", None):
        ret.session_id = session_id


def _resolve_prefetch_chains(
    topology: Any,
    ret: Optional[Any],
    attendees: Optional[List[Dict[str, str]]],
    project_name: str,
    all_matched: Tuple[Any, ...],
    norm_eng: str,
    rem_sec: float,
) -> Tuple[List[Dict[str, Any]], bool]:
    if not topology or rem_sec <= 0:
        return [], (rem_sec <= 0)
    seeds = _resolve_prefetch_seeds(topology, ret, attendees, project_name, all_matched)
    if not seeds:
        return [], False
    chains, nh_status = compute_neighborhood_chains(
        topology=topology,
        seed_node_ids=seeds,
        timeout_seconds=rem_sec,
        engagement_id=norm_eng or None,
    )
    if ret:
        ret.neighborhood_store = CopilotNeighborhoodStore(
            chains=chains, status=nh_status, engagement_id=norm_eng or None
        )
    return chains, (nh_status == "PARTIAL")


def _parse_generation(raw_gen: Any) -> int | str:
    try:
        return int(raw_gen)
    except (ValueError, TypeError):
        return str(raw_gen)


def _categorize_assertion(
    a: Dict[str, Any],
    decisions: List[Dict[str, Any]],
    risks: List[Dict[str, Any]],
    actions: List[Dict[str, Any]],
    recent: List[Dict[str, Any]],
) -> None:
    item = dict(a)
    if "text" not in item:
        item["text"] = a.get("claim_text", "")
    pred = normalize_copilot_predicate(a.get("predicate", ""))
    if pred in PREFETCH_DECISION_PREDICATES:
        decisions.append(item)
    elif pred in PREFETCH_RISK_PREDICATES:
        risks.append(item)
    elif pred in PREFETCH_ACTION_PREDICATES:
        actions.append(item)
    else:
        recent.append(item)


class CopilotPrefetchBridge:
    """Bridge for pre-fetching knowledge graph context at recording start."""

    def __init__(
        self,
        in_memory_graph: Optional[Any] = None,
        retriever: Optional[Any] = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.in_memory_graph = in_memory_graph
        self.retriever = retriever
        self.timeout_seconds = timeout_seconds

    def prefetch(
        self,
        project_name: str = "",
        meeting_name: str = "",
        attendees: Optional[List[Dict[str, str]]] = None,
        engagement_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> SessionContextPacket:
        t0 = time.time()
        project_scope = project_name if project_name else "general"
        ret = self.retriever
        norm_eng = engagement_id or getattr(ret, "engagement_id", "") or ""
        if not self.in_memory_graph and not ret:
            return SessionContextPacket.empty(
                project_scope=project_scope,
                engagement_id=norm_eng,
                session_id=session_id or "",
            )

        _bind_retriever_scope(ret, norm_eng, session_id)
        specs = build_prefetch_query_specs(project_name, meeting_name, attendees)
        decisions, risks, actions, recent = self._collect_prefetch(ret, specs) if ret else ([], [], [], [])

        rem_sec = self.timeout_seconds - (time.time() - t0)
        topology = getattr(ret, "_topology", None) if ret else self.in_memory_graph
        all_matched = (*decisions, *risks, *actions, *recent)
        chains, is_partial = _resolve_prefetch_chains(
            topology, ret, attendees, project_name, all_matched, norm_eng, rem_sec
        )

        elapsed_ms = max(0.01, (time.time() - t0) * 1000)
        return SessionContextPacket(
            project_scope=project_scope,
            engagement_id=norm_eng,
            session_id=session_id or "",
            graph_sha256=getattr(ret, "graph_sha256", "") or "",
            generation=_parse_generation(getattr(ret, "generation", 0) or 0),
            open_decisions=decisions[:5],
            open_risks=risks[:5],
            open_action_items=actions[:5],
            recent_assertions=recent[:5],
            connected_chains=chains[:10],
            retrieval_latency_ms=elapsed_ms,
            prefetch_status="PARTIAL" if is_partial else "SUCCESS",
        )

    def _collect_prefetch(
        self, ret: Any, specs: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        decisions: List[Dict[str, Any]] = []
        risks: List[Dict[str, Any]] = []
        actions: List[Dict[str, Any]] = []
        recent: List[Dict[str, Any]] = []
        for spec in specs:
            for a in ret.retrieve_structured_assertions(spec.get("query_text", ""), max_results=5):
                _categorize_assertion(a, decisions, risks, actions, recent)
        return decisions, risks, actions, recent
