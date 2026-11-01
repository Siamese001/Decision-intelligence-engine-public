"""
Supersession Resolver agent (Wave 4).
Includes A4 logic to handle conflict groups based on policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from dual_pipeline.graph_freshness_core.policy import GraphFreshnessPolicy


@dataclass
class SupersessionFindings:
    # Maps loser assertion ID to winner assertion ID
    superseded_mapping: Dict[str, str]


_FUNCTIONAL_PREDICATES = frozenset({
    "REPORTS_TO",
    "HAS_ROLE",
    "HAS_TITLE",
    "HAS_STATUS",
    "HAS_DEPLOYMENT_STATUS",
    "ASSIGNED_TO_ORG",
    "LOCATED_IN",
    "EMPLOYED_BY",
    "HEAD_OF",
    "OWNS_PRIMARY",
})


def _is_functional_predicate(predicate: str) -> bool:
    """Return True if predicate is single-valued over time."""
    up = predicate.upper()
    if up == "HAS_CLAIM_TEXT" or up.endswith("_CLAIM_TEXT"):
        return False
    if up in _FUNCTIONAL_PREDICATES:
        return True
    return up.endswith("_STATUS") or up.startswith("STATUS_")



class SupersessionResolver:
    """A4: Resolves single-valued predicate conflicts by selecting a winner."""

    def _pick_winner(self, rows: List[Tuple[str, str, int]], policy_strategy: str) -> str:
        if policy_strategy == "newest_source_unless_authority_differs":
            sorted_rows = sorted(rows, key=lambda x: (x[2], x[1]), reverse=True)
            return sorted_rows[0][0]
        elif policy_strategy == "highest_authority_only":
            sorted_rows = sorted(rows, key=lambda x: x[2], reverse=True)
            return sorted_rows[0][0]
        elif policy_strategy == "newest_source_only":
            sorted_rows = sorted(rows, key=lambda x: x[1], reverse=True)
            return sorted_rows[0][0]
        return ""

    def _resolve_conflict_group(
        self, cursor: Any, subject: str, predicate: str, policy: GraphFreshnessPolicy
    ) -> List[Tuple[str, str]]:
        try:
            src_cols = {r[1] for r in cursor.execute("PRAGMA table_info(source)").fetchall()}
            src_pk = "id" if "id" in src_cols else "source_id"
            cursor.execute(f"""
                SELECT a.assertion_id, a.recorded_at, COALESCE(s.authority_rank, 0) as auth
                FROM assertion a
                LEFT JOIN source s ON a.primary_source_id = s.{src_pk}
                WHERE a.subject_node_id = ? AND a.predicate = ?
                  AND a.currentness_state = 'current' AND a.superseded_by_assertion_id IS NULL
            """, (subject, predicate))
        except Exception:
            cursor.execute("""
                SELECT assertion_id, recorded_at, 0 as auth
                FROM assertion
                WHERE subject_node_id = ? AND predicate = ?
                  AND currentness_state = 'current' AND superseded_by_assertion_id IS NULL
            """, (subject, predicate))
        rows = cursor.fetchall()
        if not rows or policy.supersession_strategy == "hitl_always":
            return []
        winner_id = self._pick_winner(rows, policy.supersession_strategy)
        if not winner_id:
            return []
        return [(row[0], winner_id) for row in rows if row[0] != winner_id]

    def _resolve_claim_topic_supersession(
        self, cursor: Any, policy: GraphFreshnessPolicy
    ) -> List[Tuple[str, str]]:
        if policy.supersession_strategy == "hitl_always":
            return []
        try:
            cursor.execute("""
                SELECT a1.assertion_id, a2.assertion_id, a1.recorded_at, a2.recorded_at,
                       COALESCE(s1.authority_rank, 0), COALESCE(s2.authority_rank, 0)
                FROM assertion a1
                JOIN assertion a2 ON a1.subject_node_id = a2.subject_node_id
                                 AND a1.predicate = a2.predicate
                                 AND a1.assertion_id < a2.assertion_id
                LEFT JOIN source s1 ON a1.primary_source_id = s1.id
                LEFT JOIN source s2 ON a2.primary_source_id = s2.id
                WHERE a1.predicate = 'HAS_CLAIM_TEXT'
                  AND a1.currentness_state = 'current' AND a1.retrieval_eligible = 1 AND a1.superseded_by_assertion_id IS NULL
                  AND a2.currentness_state = 'current' AND a2.retrieval_eligible = 1 AND a2.superseded_by_assertion_id IS NULL
                  AND a1.primary_source_id != a2.primary_source_id
                  AND json_extract(a1.qualifiers_json, '$.normalized_claim_topic') IS NOT NULL
                  AND json_extract(a1.qualifiers_json, '$.normalized_claim_topic') = json_extract(a2.qualifiers_json, '$.normalized_claim_topic')
            """)
            pairs: List[Tuple[str, str]] = []
            for a1_id, a2_id, a1_rec, a2_rec, s1_auth, s2_auth in cursor.fetchall():
                rows = [(a1_id, a1_rec, s1_auth), (a2_id, a2_rec, s2_auth)]
                winner_id = self._pick_winner(rows, policy.supersession_strategy)
                if winner_id:
                    loser_id = a1_id if winner_id == a2_id else a2_id
                    pairs.append((loser_id, winner_id))
            return pairs
        except Exception:
            return []

    def scan(self, conn: Any, policy: GraphFreshnessPolicy) -> SupersessionFindings:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT subject_node_id, predicate
            FROM assertion
            WHERE currentness_state='current' AND retrieval_eligible=1 AND superseded_by_assertion_id IS NULL
            GROUP BY subject_node_id, predicate
            HAVING COUNT(DISTINCT COALESCE(object_node_id, object_literal)) > 1
               AND COUNT(DISTINCT primary_source_id) > 1
        """)
        mapping: Dict[str, str] = {}
        for subject, predicate in cursor.fetchall():
            if _is_functional_predicate(predicate):
                for loser, winner in self._resolve_conflict_group(cursor, subject, predicate, policy):
                    mapping[loser] = winner
        for loser, winner in self._resolve_claim_topic_supersession(cursor, policy):
            mapping[loser] = winner
        return SupersessionFindings(superseded_mapping=mapping)
