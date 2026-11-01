"""
dual_pipeline/query_core/offline_retriever.py

Offline Graph Context Retriever for Meeting Synthesis Prior Context.
Provides zero-cloud, read-only extraction of verified entities, active decisions,
open risks, and domain vocabulary from the active SQLite GraphDB.

Enforces:
1. Rule C-19: 100% quarantine of INTERNAL_ADVISORY and shadow graph data.
2. Rule C-16: Prior context is read-only reference for CONFIRM vs CREATE.
3. Sub-50ms bounded execution using read-only SQLite connections.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, List, Optional, Sequence, Set, Tuple

from storage.database import sqlite3

from .offline_models import (
    GraphAssertionReference,
    GraphContextPacket,
    GraphEntityReference,
)
from .traversal_helper import (
    clean_attendee_names,
    extract_attendee_nodes,
    extract_domain_terms,
    extract_project_nodes,
    resolve_active_graph_path,
    spot_transcript_entities,
    traverse_project_resources,
)

__all__ = [
    "DEFAULT_GRAPH_RELATIVE_PATH",
    "GraphAssertionReference",
    "GraphContextPacket",
    "GraphEntityReference",
    "OfflineGraphContextRetriever",
]

logger = logging.getLogger("dual_pipeline.query_core.offline_retriever")

DEFAULT_GRAPH_RELATIVE_PATH = "graph/nee_graph.sqlite"


def _is_assertion_excluded_by_workstream(
    sub: str, obj: str, asrt_id: str, scoped_workstreams: Sequence[str]
) -> bool:
    for sw in scoped_workstreams:
        sw_lower = str(sw).lower()
        if "lens" in sw_lower and ("sherpa" in sub.lower() or "sherpa" in obj.lower() or "sherpa_exclusion" in asrt_id):
            return True
        if "sherpa" in sw_lower and ("lens" in sub.lower() or "lens" in obj.lower() or "jen_pan" in sub.lower() or "jen_pan" in obj.lower()):
            return True
    return False


def _classify_assertion_kind(pred: str) -> str:
    if any(k in pred for k in ("DECISION", "CONFIRM", "APPROVE", "ROLE", "LEAD")):
        return "decision"
    return "summary"


def _parse_raw_claim(raw_val: Any) -> Optional[str]:
    if not raw_val:
        return None
    if isinstance(raw_val, str):
        s = raw_val.strip()
        if s.startswith("{"):
            try:
                data = json.loads(s)
                if isinstance(data, dict):
                    return str(data.get("claim") or data.get("text") or data.get("claim_text") or s)
            except Exception:
                pass
        return s
    elif isinstance(raw_val, dict):
        return str(raw_val.get("claim") or raw_val.get("text") or raw_val.get("claim_text") or "")
    return str(raw_val)


def _extract_assertions(
    cursor: Any,
    entity_ids: List[str],
    max_assertions: int,
    scoped_workstreams: Optional[Sequence[str]] = None,
) -> Tuple[List[GraphAssertionReference], List[GraphAssertionReference]]:
    if not entity_ids:
        return [], []
    ass_cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion)").fetchall()}
    has_ass_quarantine = "is_quarantined" in ass_cols
    has_currentness = "currentness_state" in ass_cols
    has_canonical_status = "canonical_status" in ass_cols
    has_canonical_eligible = "canonical_eligible" in ass_cols
    has_retrieval_eligible = "retrieval_eligible" in ass_cols

    placeholders = ",".join("?" for _ in entity_ids)
    query = (
        f"SELECT * FROM assertion WHERE ("
        f"subject_node_id IN ({placeholders}) OR object_node_id IN ({placeholders})"
        f")"
    )
    params = list(entity_ids) + list(entity_ids)
    if has_canonical_status:
        query += " AND canonical_status = 'canonical'"
    if has_canonical_eligible:
        query += " AND canonical_eligible = 1"
    if has_currentness:
        query += " AND (currentness_state IS NULL OR currentness_state = 'current')"
    if has_retrieval_eligible:
        query += " AND retrieval_eligible = 1"
    if has_ass_quarantine:
        query += " AND (is_quarantined IS NULL OR is_quarantined = 0)"
    query += " LIMIT ?"
    params.append(max_assertions)

    decisions: List[GraphAssertionReference] = []
    risks: List[GraphAssertionReference] = []
    for row in cursor.execute(query, params).fetchall():
        ad = dict(row)
        sub = str(ad.get("subject_node_id", ""))
        obj = str(ad.get("object_node_id", ""))
        asrt_id = str(ad.get("assertion_id", ""))

        if scoped_workstreams and _is_assertion_excluded_by_workstream(sub, obj, asrt_id, scoped_workstreams):
            continue

        pred = str(ad.get("predicate", "")).upper()
        raw_claim = (
            ad.get("object_literal")
            or ad.get("scope_text")
            or _parse_raw_claim(ad.get("claim_text") or ad.get("qualifiers_json"))
        )
        kind = _classify_assertion_kind(pred)
        ref = GraphAssertionReference(
            assertion_id=asrt_id,
            predicate=pred,
            subject_node_id=sub,
            object_node_id=ad.get("object_node_id"),
            claim_text=str(raw_claim)[:250] if raw_claim else None,
            kind=kind,
            observed_at=ad.get("observed_at"),
        )
        if kind == "decision":
            decisions.append(ref)
        elif any(k in pred for k in ("RISK", "BLOCK")):
            risks.append(ref)
    return decisions, risks


def _format_entity_line(e: GraphEntityReference) -> str:
    line = f"- `{e.node_id}` ({e.display_name})"
    if e.role:
        return line + f" — {e.role}"
    if e.organization:
        return line + f" — Org: {e.organization}, Type: {e.node_type}"
    return line + f" — Type: {e.node_type}"


def _group_entities_by_workstream(
    entities: List[GraphEntityReference],
) -> Tuple[List[str], List[str], List[str]]:
    lens_lines: List[str] = []
    sherpa_lines: List[str] = []
    general_lines: List[str] = []

    for e in entities:
        formatted = _format_entity_line(e)
        ws = e.associated_workstreams or ()
        is_lens = any("lens" in str(w).lower() for w in ws) or "lens" in e.node_id.lower()
        is_sherpa = any("sherpa" in str(w).lower() for w in ws) or "sherpa" in e.node_id.lower()

        if is_lens and not is_sherpa:
            lens_lines.append(formatted)
        elif is_sherpa and not is_lens:
            sherpa_lines.append(formatted)
        else:
            general_lines.append(formatted)
    return lens_lines, sherpa_lines, general_lines


def _format_entities_section(entities: List[GraphEntityReference]) -> str:
    lines = ["### Known Graph Entities & Roster Grounding"]
    lens_group, sherpa_group, general_group = _group_entities_by_workstream(entities)

    if lens_group:
        lines.append("#### Project LENS (org:nea)")
        lines.extend(lens_group)
    if sherpa_group:
        lines.append("#### Project Beacon (org:nee_it)")
        lines.extend(sherpa_group)
    if general_group:
        if lens_group or sherpa_group:
            lines.append("#### General & Shared Architecture")
        lines.extend(general_group)
    return "\n".join(lines)


def _format_context_markdown(
    entities: List[GraphEntityReference],
    decisions: List[GraphAssertionReference],
    risks: List[GraphAssertionReference],
    lexical_terms: List[str],
) -> str:
    blocks: List[str] = []
    if entities:
        blocks.append(_format_entities_section(entities))
    if decisions:
        lines = [
            f"- **{d.predicate}** [{d.subject_node_id}]: {d.claim_text or d.assertion_id}"
            + (f" *(Observed: {d.observed_at})*" if d.observed_at else "")
            for d in decisions[:10]
        ]
        blocks.append("### Active Predecessor Decisions & Commitments (Rule C-16)\n" + "\n".join(lines))
    if risks:
        lines = [
            f"- **{r.predicate}** [{r.subject_node_id}]: {r.claim_text or r.assertion_id}"
            for r in risks[:8]
        ]
        blocks.append("### In-Flight Risks & Dependencies\n" + "\n".join(lines))
    if lexical_terms:
        blocks.append("### Verified Domain Terminology & Acronyms\n" + ", ".join(lexical_terms[:25]))
    return "\n\n".join(blocks).strip()


def _determine_scoped_workstreams(project_name: Optional[str]) -> List[str]:
    if not project_name:
        return []
    p_clean = project_name.lower().replace("-", "_")
    if "lens" in p_clean:
        return ["workstream_lens"]
    if "sherpa" in p_clean:
        return ["workstream_project_beacon"]
    return []


def _is_entity_disjoint_from_scope(node_id: str, scoped_ws: Sequence[str]) -> bool:
    nid = node_id.lower()
    if "workstream_lens" in scoped_ws and ("sherpa" in nid or "clay_palumbo" in nid):
        return True
    if "workstream_project_beacon" in scoped_ws and ("lens" in nid or "jen_pan" in nid):
        return True
    return False


def _append_unique_entities(
    target: List[GraphEntityReference], incoming: Sequence[GraphEntityReference]
) -> None:
    seen = {e.node_id for e in target if e.node_id}
    for item in incoming:
        if item.node_id not in seen:
            target.append(item)
            seen.add(item.node_id)


class OfflineGraphContextRetriever:
    """
    Offline graph query service powering Outputs 01, 02, and 03 synthesis.
    Extracts topological neighborhood around meeting attendees and projects
    strictly from canonical storage, enforcing the Rule C-19 Career Firewall.
    """

    def __init__(
        self,
        default_graph_path: Optional[Path] = None,
        runtime_root: Optional[Path] = None,
    ) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.default_graph_path = (
            Path(default_graph_path)
            if default_graph_path
            else self.repo_root / DEFAULT_GRAPH_RELATIVE_PATH
        )
        self.runtime_root = Path(runtime_root) if runtime_root else None

    def _resolve_graph_path(self, override_path: Optional[Path] = None) -> Optional[Path]:
        """Resolves target graph database path with fallback logic."""
        return resolve_active_graph_path(
            repo_root=self.repo_root,
            runtime_root=self.runtime_root,
            default_graph_path=self.default_graph_path,
            override_path=override_path,
        )

    def retrieve_meeting_prior_context(
        self,
        project_name: Optional[str] = None,
        attendees: Optional[Sequence[Any]] = None,
        date_str: Optional[str] = None,
        graph_path: Optional[Path] = None,
        max_entities: int = 25,
        max_assertions: int = 25,
        transcript_text: Optional[str] = None,
    ) -> GraphContextPacket:
        """
        Executes bounded offline extraction of prior context from active graph.
        Traverses project topologies, resolves attendees, and spots in-dialogue resources.
        Returns a GraphContextPacket. Guaranteed fail-safe: never raises.
        """
        start_time = time.perf_counter()
        target_db = self._resolve_graph_path(graph_path)
        if not target_db:
            return GraphContextPacket(
                duration_ms=round((time.perf_counter() - start_time) * 1000, 2)
            )

        extracted_entities: List[GraphEntityReference] = []
        extracted_decisions: List[GraphAssertionReference] = []
        extracted_risks: List[GraphAssertionReference] = []
        lexical_terms: Set[str] = set()

        try:
            from ..storage.governed_context import GovernedStorageContext
            conn = GovernedStorageContext.connect(target_db, readonly=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            tables = {
                r[0] for r in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "node" not in tables:
                conn.close()
                return GraphContextPacket(
                    duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    graph_path_resolved=str(target_db),
                )

            node_cols = {r[1] for r in cursor.execute("PRAGMA table_info(node)").fetchall()}
            has_quarantine = "is_quarantined" in node_cols
            scoped_ws = _determine_scoped_workstreams(project_name)

            # 1. Project nodes
            p_entities, p_terms = extract_project_nodes(cursor, project_name, has_quarantine)
            if scoped_ws:
                p_entities = [e for e in p_entities if not _is_entity_disjoint_from_scope(e.node_id, scoped_ws)]
            extracted_entities.extend(p_entities)
            lexical_terms.update(p_terms)

            # 2. Traverse project resources connected via graph edges
            p_ids = [e.node_id for e in p_entities if e.node_id]
            r_entities, r_terms = traverse_project_resources(cursor, p_ids, has_quarantine, tables)
            _append_unique_entities(extracted_entities, r_entities)
            lexical_terms.update(r_terms)

            # 3. Attendee nodes
            clean_names = clean_attendee_names(attendees)
            a_entities, a_terms = extract_attendee_nodes(cursor, clean_names, has_quarantine, max_entities)
            if scoped_ws:
                a_entities = [ae for ae in a_entities if not _is_entity_disjoint_from_scope(ae.node_id, scoped_ws)]
            _append_unique_entities(extracted_entities, a_entities)
            lexical_terms.update(a_terms)

            # 4. Spot in-dialogue entities from raw transcript text
            s_entities, s_terms = spot_transcript_entities(
                cursor, transcript_text, has_quarantine, scoped_workstreams=scoped_ws
            )
            _append_unique_entities(extracted_entities, s_entities)
            lexical_terms.update(s_terms)

            # 5. Domain lexical terms
            lexical_terms.update(extract_domain_terms(cursor, has_quarantine))

            # 6. Predecessor assertions / decisions / risks
            if "assertion" in tables:
                target_ids = [e.node_id for e in extracted_entities if e.node_id]
                decs, rsks = _extract_assertions(
                    cursor, target_ids, max_assertions, scoped_workstreams=scoped_ws
                )
                extracted_decisions.extend(decs)
                extracted_risks.extend(rsks)

            conn.close()
        except Exception as e:
            logger.warning("Offline graph context extraction encountered error: %s", e)

        sorted_terms = sorted(list(lexical_terms))
        context_str = _format_context_markdown(
            extracted_entities, extracted_decisions, extracted_risks, sorted_terms
        )
        elapsed = round((time.perf_counter() - start_time) * 1000, 2)

        return GraphContextPacket(
            entities=extracted_entities,
            decisions=extracted_decisions,
            risks=extracted_risks,
            lexical_terms=sorted_terms,
            formatted_prior_context=context_str,
            duration_ms=elapsed,
            graph_path_resolved=str(target_db) if target_db else None,
        )
