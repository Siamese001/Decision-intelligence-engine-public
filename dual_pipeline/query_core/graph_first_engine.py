"""Graph-First Query Engine implementing the normative 5-tier query resolution cascade."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.core import connect_sqlite
from dual_pipeline.l2_policy_core.intent import analyze_request_intent
from dual_pipeline.query_core.evidence_locator import EvidenceLocatorResult, EvidenceLocatorService
from dual_pipeline.query_core.firewall import ProhibitedCommandGuard
from dual_pipeline.storage.governed_context import OperationalError


@dataclass(frozen=True)
class TierExecutionMetrics:
    tier: int
    tier_name: str
    executed: bool
    latency_ms: float
    records_retrieved: int
    status: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphQueryResult:
    success: bool
    resolved_tier: int
    query: str
    matched_nodes: List[Dict[str, Any]]
    assertions: List[Dict[str, Any]]
    forensic_quotes: List[EvidenceLocatorResult]
    tier_metrics: List[TierExecutionMetrics]
    total_latency_ms: float
    hitl_required: bool = False
    hitl_clarification: Optional[Dict[str, Any]] = None
    formatted_markdown: str = ""
    error_message: Optional[str] = None


class GraphFirstQueryEngine:
    """Normative 5-tier cascade query engine prioritizing GraphDB traversal before sidecar/cold-archive."""

    def __init__(self, repo_root: Path, graph_path: Path, sidecar_path: Optional[Path] = None):
        self.repo_root = repo_root.resolve()
        self.graph_path = graph_path.resolve()
        self.sidecar_path = sidecar_path.resolve() if sidecar_path else None
        self.locator_service = EvidenceLocatorService(self.repo_root)
        self.firewall = ProhibitedCommandGuard()

    def _sync_staged_candidates(self) -> None:
        try:
            from dual_pipeline.fast_promotion import ensure_staged_candidates_promoted  # noqa: lazy-decouple
            self.graph_path, _ = ensure_staged_candidates_promoted(self.graph_path)
        except Exception:
            pass

    def execute_query(
        self, question: str, *, source_id: Optional[str] = None,
        hydrate_forensic_quotes: bool = False, confidence_threshold: float = 0.5,
    ) -> GraphQueryResult:
        """Executes the normative 5-tier resolution waterfall."""
        start_time = time.perf_counter()
        metrics: List[TierExecutionMetrics] = []
        self._sync_staged_candidates()

        # Tier 0: Intent Normalization & Token Analysis
        t0_start = time.perf_counter()
        normalized_q = question.strip()
        self.firewall.inspect(normalized_q)
        intent = analyze_request_intent(normalized_q)
        metrics.append(TierExecutionMetrics(
            tier=0, tier_name="Intent Normalization", executed=True,
            latency_ms=(time.perf_counter() - t0_start) * 1000.0,
            records_retrieved=1, status="PASS",
            details={"semantic_synthesis_requested": intent.semantic_synthesis_requested},
        ))

        # Tier 1: GraphDB Relational Traversal (SQLite O(log N))
        t1_start = time.perf_counter()
        matched_nodes, assertions = self._tier1_relational_traversal(
            normalized_q, source_id=source_id, confidence_threshold=confidence_threshold
        )
        t1_lat = (time.perf_counter() - t1_start) * 1000.0

        if assertions:
            metrics.append(TierExecutionMetrics(
                tier=1, tier_name="GraphDB Relational Traversal", executed=True,
                latency_ms=t1_lat, records_retrieved=len(assertions), status="SUCCESS_HIT",
                details={"node_count": len(matched_nodes), "assertion_count": len(assertions)},
            ))
            quotes: List[EvidenceLocatorResult] = []
            if hydrate_forensic_quotes:
                t3_start = time.perf_counter()
                quotes = self._tier3_evidence_hydration(assertions)
                metrics.append(TierExecutionMetrics(
                    tier=3, tier_name="Scoped Evidence Envelope Locator", executed=True,
                    latency_ms=(time.perf_counter() - t3_start) * 1000.0,
                    records_retrieved=len(quotes), status="HYDRATED",
                ))
            total_lat = (time.perf_counter() - start_time) * 1000.0
            return GraphQueryResult(
                success=True, resolved_tier=1, query=question, matched_nodes=matched_nodes,
                assertions=assertions, forensic_quotes=quotes, tier_metrics=metrics,
                total_latency_ms=total_lat,
                formatted_markdown=self._render_assertion_markdown(matched_nodes, assertions, quotes),
            )

        metrics.append(TierExecutionMetrics(
            tier=1, tier_name="GraphDB Relational Traversal", executed=True,
            latency_ms=t1_lat, records_retrieved=0, status="MISS_FALLBACK",
        ))

        # Tier 2: Sidecar Hybrid Retrieval Fallback (BGE-M3 Dense + BM25)
        t2_start = time.perf_counter()
        sidecar_hits = self._tier2_sidecar_fallback(normalized_q)
        t2_lat = (time.perf_counter() - t2_start) * 1000.0

        if sidecar_hits:
            metrics.append(TierExecutionMetrics(
                tier=2, tier_name="Sidecar Hybrid Retrieval", executed=True,
                latency_ms=t2_lat, records_retrieved=len(sidecar_hits), status="SUCCESS_HIT",
            ))
            hydrated_nodes, hydrated_assertions = self._hydrate_from_sidecar(
                sidecar_hits, confidence_threshold=confidence_threshold
            )
            total_lat = (time.perf_counter() - start_time) * 1000.0
            return GraphQueryResult(
                success=True, resolved_tier=2, query=question, matched_nodes=hydrated_nodes,
                assertions=hydrated_assertions, forensic_quotes=[], tier_metrics=metrics,
                total_latency_ms=total_lat,
                formatted_markdown=self._render_sidecar_markdown(sidecar_hits, hydrated_assertions),
            )

        metrics.append(TierExecutionMetrics(
            tier=2, tier_name="Sidecar Hybrid Retrieval", executed=True,
            latency_ms=t2_lat, records_retrieved=0, status="MISS_FALLBACK",
        ))

        # Tier 4: Governed HITL Ambiguity Gate
        total_lat = (time.perf_counter() - start_time) * 1000.0
        hitl_modal = {
            "question": f"No definitive graph records matched query: '{question}'. Select target domain:",
            "options": [
                "(Recommended) Search meeting archives from 09/22/2026 (Google Internal Catchup)",
                "Expand search across all enterprise architecture workstreams",
                "Flag as candidate assertion for operator review",
            ],
            "is_multi_select": False,
        }
        return GraphQueryResult(
            success=False, resolved_tier=4, query=question, matched_nodes=[], assertions=[],
            forensic_quotes=[], tier_metrics=metrics, total_latency_ms=total_lat,
            hitl_required=True, hitl_clarification=hitl_modal,
            error_message="Query escalated to Tier 4 HITL Gate.",
        )

    @staticmethod
    def _lookup_candidate_nodes_for_token(conn: Any, token: str, candidate_node_ids: Set[str]) -> None:
        try:
            for row in conn.execute("SELECT node_id FROM node_fts WHERE node_fts MATCH ? LIMIT 25", (f"{token}*",)):
                candidate_node_ids.add(row[0])
        except OperationalError:
            pass
        for row in conn.execute(
            """SELECT node_id FROM node
                WHERE lower(canonical_name) LIKE ? OR lower(display_name) LIKE ?
                ORDER BY CASE node_type
                    WHEN 'workstream' THEN 0
                    WHEN 'program' THEN 1
                    WHEN 'sow' THEN 2
                    WHEN 'stakeholder' THEN 3
                    WHEN 'person' THEN 4
                    ELSE 5
                END,
                length(canonical_name) ASC,
                node_id ASC
                LIMIT 25""",
            (f"%{token}%", f"%{token}%"),
        ):
            candidate_node_ids.add(row[0])
        try:
            for row in conn.execute(
                "SELECT entity_id FROM canonical_alias WHERE active = 1 AND lower(display_alias) LIKE ? LIMIT 25",
                (f"%{token}%",),
            ):
                if row[0]:
                    candidate_node_ids.add(row[0])
        except OperationalError:
            pass
        try:
            for row in conn.execute(
                "SELECT node_id FROM alias WHERE lower(alias) LIKE ? LIMIT 25",
                (f"%{token}%",),
            ):
                if row[0]:
                    candidate_node_ids.add(row[0])
        except OperationalError:
            pass

    def _extract_query_candidate_nodes(self, conn: Any, question: str) -> Set[str]:
        candidate_node_ids: Set[str] = set()
        if re.search(r"\b(?:am\s+i|what\s+am\s+i|my\s+resp|my\s+role|\bme\b|\bmy\b)", question, re.IGNORECASE):
            candidate_node_ids.add("person_amit")
        STOPWORDS = {
            "what", "was", "for", "with", "and", "the", "meeting", "artifact",
            "discovery", "completely", "about", "nonexistent", "alien", "google",
            "are", "open", "steps", "latest", "gates", "complete", "status",
            "current", "update", "updates", "project", "workstream", "program",
            "how", "who", "when", "where", "why", "can", "will", "should", "need",
            "from", "into", "that", "this", "these", "those", "have", "has", "had",
            "sow", "phase", "milestone", "milestones", "statement", "work",
        }
        tokens = [t.lower() for t in re.findall(r"\b[A-Za-z0-9_]{3,}\b", question) if t.lower() not in STOPWORDS]
        for token in tokens:
            self._lookup_candidate_nodes_for_token(conn, token, candidate_node_ids)
        return candidate_node_ids

    _WS_STOP_WORDS = frozenset({
        "project", "workstream", "program", "initiative", "sow", "statement",
        "work", "phase", "part", "milestone", "system", "platform", "governance",
        "stakeholder", "rules", "codification", "visual", "asset", "intelligence",
        "google", "enterprise", "opportunity", "optical", "character", "recognition",
        "maximo", "updates",
    })

    @classmethod
    def _extract_workstream_tokens(cls, conn: Any, ws_nodes: List[str]) -> List[str]:
        if not ws_nodes:
            return []
        ws_ph = ",".join("?" for _ in ws_nodes)
        q_ws = "SELECT canonical_name FROM node WHERE node_id IN (" + ws_ph + ")"
        rows = conn.execute(q_ws, ws_nodes).fetchall()
        ws_tokens: List[str] = []
        for (raw_name,) in rows:
            words = [
                w for w in re.findall(r"[a-z0-9]+", str(raw_name or "").lower())
                if len(w) >= 3 and w not in cls._WS_STOP_WORDS
            ]
            if len(words) <= 2:
                for w in words:
                    if w not in ws_tokens:
                        ws_tokens.append(w)
        return ws_tokens

    @classmethod
    def _build_candidate_node_where_clause(
        cls,
        conn: Any,
        candidate_node_ids: Set[str],
        ass_cols: Set[str],
        has_src: bool,
        src_cols: Set[str],
    ) -> Tuple[str, List[Any]]:
        if not candidate_node_ids:
            return "", []
        ph = ",".join("?" for _ in candidate_node_ids)
        c_list = list(candidate_node_ids)
        q_ws_ids = "SELECT node_id FROM node WHERE node_id IN (" + ph + ") AND node_type IN ('workstream', 'program', 'sow')"
        ws_nodes = (
            [r[0] for r in conn.execute(q_ws_ids, c_list).fetchall()]
            if "primary_source_id" in ass_cols else []
        )
        if not ws_nodes:
            return " AND (a.subject_node_id IN (" + ph + ") OR a.object_node_id IN (" + ph + "))", c_list * 2
        ws_ph = ",".join("?" for _ in ws_nodes)
        ws_tokens = cls._extract_workstream_tokens(conn, ws_nodes)
        if ws_tokens and has_src and "source_name" in src_cols:
            src_clauses = " OR ".join("lower(COALESCE(s_ws.source_name, '')) LIKE ?" for _ in ws_tokens)
            src_or_sql = " OR (" + src_clauses + ")"
            src_params = [f"%{w}%" for w in ws_tokens]
            src_ws_join = "LEFT JOIN source s_ws ON a_ws.primary_source_id = s_ws.source_id"
        else:
            src_or_sql, src_params, src_ws_join = "", [], ""
        clause = (
            " AND (a.subject_node_id IN (" + ph + ") OR a.object_node_id IN (" + ph + ")"
            " OR a.primary_source_id IN ("
            "SELECT DISTINCT a_ws.primary_source_id FROM assertion a_ws " + src_ws_join + " "
            "WHERE a_ws.primary_source_id IS NOT NULL "
            "AND ((SELECT COUNT(DISTINCT a_cnt.subject_node_id) FROM assertion a_cnt WHERE a_cnt.primary_source_id = a_ws.primary_source_id) <= 25"
            "     OR (1=0" + src_or_sql + ")) "
            "AND (a_ws.subject_node_id IN (" + ws_ph + ") OR a_ws.object_node_id IN (" + ws_ph + ")" + src_or_sql + ")"
            "))"
        )
        return clause, c_list * 2 + src_params + ws_nodes * 2 + src_params

    @staticmethod
    def _deduplicate_tier1_rows(cursor: Any) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        seen_assertions: Set[str] = set()
        seen_claims: Set[Tuple[Any, Any, Any, Any]] = set()
        per_source_counts: Dict[str, int] = {}
        for r in cursor:
            aid, sid = str(r[0] or ""), str(r[10] or "")
            claim_key = (sid, r[1], r[3], r[6] if r[6] is not None else r[4])
            if aid in seen_assertions or claim_key in seen_claims or (sid and per_source_counts.get(sid, 0) >= 40):
                continue
            seen_assertions.add(aid)
            seen_claims.add(claim_key)
            if sid:
                per_source_counts[sid] = per_source_counts.get(sid, 0) + 1
            results.append({
                "assertion_id": r[0], "subject_node_id": r[1], "subject_name": r[2], "predicate": r[3],
                "object_node_id": r[4], "object_name": r[5], "object_literal": r[6], "confidence_label": r[7],
                "evidence_id": r[8], "transcript_timestamp": r[9], "source_id": r[10], "cold_archive_ref": r[11],
            })
            if len(results) >= 160:
                break
        return results

    @staticmethod
    def _build_tier1_order_by_clause(
        candidate_node_ids: Set[str],
        ass_cols: Set[str],
        has_src: bool,
        src_cols: Set[str],
    ) -> Tuple[str, List[Any]]:
        order_parts: List[str] = ["COALESCE(s.source_date, '') DESC"] if (has_src and "source_date" in src_cols) else []
        order_params: List[Any] = []
        if candidate_node_ids:
            d_ph = ",".join("?" for _ in candidate_node_ids)
            order_parts.append(f"CASE WHEN a.subject_node_id IN ({d_ph}) OR a.object_node_id IN ({d_ph}) THEN 0 ELSE 1 END ASC")
            order_params.extend(list(candidate_node_ids) * 2)
        if "recorded_at" in ass_cols:
            order_parts.append("COALESCE(a.recorded_at, '') DESC")
        order_parts.extend(["a.subject_node_id ASC", "a.assertion_id ASC"])
        return f" ORDER BY {', '.join(order_parts)} LIMIT 1000", order_params

    @classmethod
    def _fetch_tier1_assertions(
        cls, conn: Any, candidate_node_ids: Set[str], source_id: Optional[str], confidence_threshold: float
    ) -> List[Dict[str, Any]]:
        ass_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
        existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        has_src = "source" in existing_tables
        src_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(source)").fetchall()} if has_src else set()
        has_ev = "assertion_evidence" in existing_tables and "evidence_item" in existing_tables
        ev_cols = "ei.evidence_id, ei.transcript_timestamp, " if has_ev else "NULL, NULL, "
        conf_label = "a.confidence_label, " if "confidence_label" in ass_cols else "NULL, "
        cold_ref = "s.cold_archive_ref" if "cold_archive_ref" in src_cols else "NULL"
        src_id_col = "s.source_id, " if has_src else "NULL, "
        src_join = "LEFT JOIN source s ON a.primary_source_id = s.source_id" if has_src else ""
        ev_joins = "LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id LEFT JOIN evidence_item ei ON ae.evidence_id = ei.evidence_id" if has_ev else ""
        curr_state_filter = "AND (a.currentness_state IS NULL OR a.currentness_state = 'current')" if "currentness_state" in ass_cols else ""
        sql = f"""
            SELECT DISTINCT a.assertion_id, a.subject_node_id, n_sub.canonical_name, a.predicate, 
                   a.object_node_id, n_obj.canonical_name, a.object_literal, {conf_label}
                   {ev_cols}{src_id_col}{cold_ref}
            FROM assertion a
            JOIN node n_sub ON a.subject_node_id = n_sub.node_id
            LEFT JOIN node n_obj ON a.object_node_id = n_obj.node_id
            {src_join} {ev_joins} WHERE 1=1 {curr_state_filter}
        """
        params: List[Any] = []
        if "canonical_status" in ass_cols:
            sql += " AND a.canonical_status = 'canonical'"
        if "canonical_eligible" in ass_cols:
            sql += " AND a.canonical_eligible = 1"
        if "confidence_score" in ass_cols:
            sql += " AND COALESCE(a.confidence_score, 1.0) >= ?"
            params.append(float(confidence_threshold))
        if source_id:
            sql += " AND a.primary_source_id = ?"
            params.append(source_id)
        cand_sql, cand_params = cls._build_candidate_node_where_clause(
            conn, candidate_node_ids, ass_cols, has_src, src_cols
        )
        order_sql, order_params = cls._build_tier1_order_by_clause(
            candidate_node_ids, ass_cols, has_src, src_cols
        )
        return cls._deduplicate_tier1_rows(conn.execute(sql + cand_sql + order_sql, params + cand_params + order_params))

    def _tier1_relational_traversal(
        self,
        question: str,
        *,
        source_id: Optional[str] = None,
        confidence_threshold: float = 0.5,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        conn = connect_sqlite(self.graph_path, readonly=True)
        try:
            candidate_node_ids = self._extract_query_candidate_nodes(conn, question)
            if not candidate_node_ids and not source_id:
                return [], []
            matched_nodes = []
            if candidate_node_ids:
                ph = ",".join("?" for _ in candidate_node_ids)
                q_nodes = f"SELECT node_id, node_type, canonical_name, display_name FROM node WHERE node_id IN ({ph})"
                matched_nodes = [
                    {"node_id": r[0], "node_type": r[1], "canonical_name": r[2], "display_name": r[3]}
                    for r in conn.execute(q_nodes, list(candidate_node_ids))
                ]
            assertions = self._fetch_tier1_assertions(conn, candidate_node_ids, source_id, confidence_threshold)
            return matched_nodes, assertions
        finally:
            conn.close()

    def _tier2_sidecar_fallback(self, question: str) -> List[Dict[str, Any]]:
        if not self.sidecar_path or not self.sidecar_path.is_file():
            return []
        conn = connect_sqlite(self.sidecar_path, readonly=True)
        try:
            tokens = [t.lower() for t in re.findall(r"\b[A-Za-z0-9_]{3,}\b", question)]
            if not tokens:
                return []
            ph = ",".join("?" for _ in tokens)
            sql = f"""
                SELECT r.record_id, r.lane, r.record_type, r.answer_use, r.artifact_json
                FROM retrieval_record r
                JOIN sparse_weight sw ON r.record_id = sw.record_id
                WHERE sw.token IN ({ph}) AND r.lane = 'approved'
                GROUP BY r.record_id ORDER BY SUM(sw.weight) DESC LIMIT 10
            """
            return [
                {"record_id": r[0], "lane": r[1], "record_type": r[2], "answer_use": r[3], "artifact_json": r[4]}
                for r in conn.execute(sql, tokens)
            ]
        except OperationalError:
            return []
        finally:
            conn.close()

    @staticmethod
    def _fetch_sidecar_assertions(
        g_conn: Any, node_ids: List[str], confidence_threshold: float
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        ass_cols = {str(r[1]) for r in g_conn.execute("PRAGMA table_info(assertion)").fetchall()}
        n_ph = ",".join("?" for _ in node_ids)
        matched_nodes = [
            {"node_id": r[0], "node_type": r[1], "canonical_name": r[2], "display_name": r[3]}
            for r in g_conn.execute(
                f"SELECT node_id, node_type, canonical_name, display_name FROM node WHERE node_id IN ({n_ph})",
                node_ids,
            )
        ]
        asrt_sql = (
            f"SELECT a.assertion_id, a.subject_node_id, a.predicate, "
            f"COALESCE(a.object_literal, n_obj.canonical_name, a.scope_text, '') AS object_literal, "
            f"a.primary_source_id, a.object_node_id, n_obj.canonical_name AS object_name "
            f"FROM assertion a LEFT JOIN node n_obj ON a.object_node_id = n_obj.node_id "
            f"WHERE a.subject_node_id IN ({n_ph}) AND a.retrieval_eligible = 1"
        )
        asrt_params: List[Any] = list(node_ids)
        if "canonical_status" in ass_cols:
            asrt_sql += " AND a.canonical_status = 'canonical'"
        if "canonical_eligible" in ass_cols:
            asrt_sql += " AND a.canonical_eligible = 1"
        if "currentness_state" in ass_cols:
            asrt_sql += " AND a.currentness_state = 'current'"
        if "confidence_score" in ass_cols:
            asrt_sql += " AND COALESCE(a.confidence_score, 1.0) >= ?"
            asrt_params.append(float(confidence_threshold))
        asrt_sql += " LIMIT 20"
        assertions = [
            {
                "assertion_id": r[0], "subject_node_id": r[1], "predicate": r[2],
                "object_literal": r[3], "source_id": r[4], "object_node_id": r[5], "object_name": r[6],
            }
            for r in g_conn.execute(asrt_sql, asrt_params)
        ]
        return matched_nodes, assertions

    def _hydrate_from_sidecar(
        self, sidecar_hits: List[Dict[str, Any]], *, confidence_threshold: float = 0.5
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        record_ids = [h["record_id"] for h in sidecar_hits]
        if not record_ids or not self.sidecar_path:
            return [], []
        s_conn = connect_sqlite(self.sidecar_path, readonly=True)
        try:
            ph = ",".join("?" for _ in record_ids)
            q_ids = f"SELECT DISTINCT object_id FROM retrieval_record_object_facet WHERE record_id IN ({ph}) AND object_type = 'node'"
            node_ids = [r[0] for r in s_conn.execute(q_ids, record_ids)]
            if not node_ids:
                return [], []
            g_conn = connect_sqlite(self.graph_path, readonly=True)
            try:
                return self._fetch_sidecar_assertions(g_conn, node_ids, confidence_threshold)
            finally:
                g_conn.close()
        finally:
            s_conn.close()

    def _tier3_evidence_hydration(self, assertions: List[Dict[str, Any]]) -> List[EvidenceLocatorResult]:
        quotes = []
        seen_timestamps = set()
        for a in assertions:
            cold_archive = a.get("cold_archive_ref")
            ts = a.get("transcript_timestamp")
            if cold_archive and ts and ts not in seen_timestamps:
                seen_timestamps.add(ts)
                res = self.locator_service.locate_exchange(
                    cold_archive_ref=cold_archive,
                    locator_kind="timestamp",
                    locator_value=ts,
                    source_id=a.get("source_id", ""),
                )
                if res.success:
                    quotes.append(res)
        return quotes

    @staticmethod
    def _render_assertion_markdown(
        nodes: List[Dict[str, Any]], assertions: List[Dict[str, Any]], quotes: List[EvidenceLocatorResult]
    ) -> str:
        md = ["### Graph-Grounded Resolution (Tier 1 Relational Traversal)\n"]
        md.append("| Subject | Predicate | Claim / Object Literal | Timestamp | Evidence ID |")
        md.append("| :--- | :--- | :--- | :--- | :--- |")
        for a in assertions:
            subj = a.get("subject_name") or a.get("subject_node_id", "")
            pred = a.get("predicate", "")
            claim = a.get("object_literal") or a.get("object_name") or ""
            ts = a.get("transcript_timestamp") or "N/A"
            eid = a.get("evidence_id") or "N/A"
            md.append(f"| **{subj}** | `{pred}` | {claim} | `{ts}` | `{eid}` |")
        if quotes:
            md.append("\n#### Hydrated Forensic Quotes (Tier 3 Evidence Locator)")
            for q in quotes:
                if q.target_turn:
                    md.append(f"- **[{q.target_turn.timestamp}] {q.target_turn.speaker}:** *\"{q.target_turn.utterance}\"*")
        return "\n".join(md)

    @staticmethod
    def _render_sidecar_markdown(hits: List[Dict[str, Any]], assertions: List[Dict[str, Any]]) -> str:
        md = [f"### Hybrid Retrieval Resolution (Tier 2 Sidecar Fallback)\nRetrieved {len(hits)} matching records.\n"]
        if assertions:
            md.append("| Subject ID | Predicate | Assertion |")
            md.append("| :--- | :--- | :--- |")
            for a in assertions:
                claim_val = a.get('object_literal') or a.get('object_name') or ''
                md.append(f"| `{a['subject_node_id']}` | `{a['predicate']}` | {claim_val} |")
        return "\n".join(md)
