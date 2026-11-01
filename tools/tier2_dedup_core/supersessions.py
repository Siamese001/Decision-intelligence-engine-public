"""
tools/tier2_dedup_core/supersessions.py

Assertion supersession logic for competing assessments on identical facts.
"""
from __future__ import annotations

from typing import Any, Callable, List, Tuple

from tools.tier2_common import utc_now


def execute_assertion_supersessions(
    conn: Any,
    audit_cb: Callable[..., None],
) -> Tuple[int, List[dict]]:
    """Supersede lower-confidence canonical current assertions."""
    rows = conn.execute(
        """SELECT a1.assertion_id AS id_a, a1.confidence_score AS conf_a,
                  a1.confidence_label AS label_a, a1.primary_source_id AS src_a,
                  a2.assertion_id AS id_b, a2.confidence_score AS conf_b,
                  a2.confidence_label AS label_b, a2.primary_source_id AS src_b,
                  a1.subject_node_id, a1.predicate, a1.object_node_id,
                  n.canonical_name AS subject_name
           FROM assertion a1
           JOIN assertion a2
             ON a1.subject_node_id = a2.subject_node_id
            AND a1.predicate = a2.predicate
            AND a1.object_node_id = a2.object_node_id
            AND a1.assertion_id < a2.assertion_id
           JOIN node n ON a1.subject_node_id = n.node_id
           WHERE a1.canonical_status = 'canonical'
             AND a2.canonical_status = 'canonical'
             AND a1.currentness_state = 'current'
             AND a2.currentness_state = 'current'
             AND a1.superseded_by_assertion_id IS NULL
             AND a2.superseded_by_assertion_id IS NULL
             AND a1.object_node_id IS NOT NULL
             AND a1.confidence_score != a2.confidence_score"""
    ).fetchall()

    superseded_count = 0
    supersessions: List[dict] = []
    ts = utc_now()
    sdoc_cols = {row[1] for row in conn.execute("PRAGMA table_info(search_document)").fetchall()}
    has_status_col = "status" in sdoc_cols

    for row in rows:
        if row["conf_a"] >= row["conf_b"]:
            winner_id, loser_id = row["id_a"], row["id_b"]
            winner_conf, loser_conf = row["conf_a"], row["conf_b"]
        else:
            winner_id, loser_id = row["id_b"], row["id_a"]
            winner_conf, loser_conf = row["conf_b"], row["conf_a"]

        delta = abs(winner_conf - loser_conf)
        if delta < 0.05:
            continue

        conn.execute(
            """UPDATE assertion
               SET superseded_by_assertion_id = ?,
                   superseded_at = ?,
                   currentness_state = 'historical',
                   canonical_status = 'historical',
                   retrieval_eligible = 0,
                   updated_at = ?
               WHERE assertion_id = ?""",
            (winner_id, ts, ts, loser_id),
        )

        if has_status_col:
            conn.execute(
                """UPDATE search_document
                   SET currentness_state = 'historical',
                       retrieval_eligible = 0,
                       status = 'historical',
                       updated_at = ?
                   WHERE assertion_id = ?""",
                (ts, loser_id),
            )
        else:
            conn.execute(
                """UPDATE search_document
                   SET currentness_state = 'historical',
                       retrieval_eligible = 0,
                       updated_at = ?
                   WHERE assertion_id = ?""",
                (ts, loser_id),
            )

        transferred = conn.execute(
            """INSERT OR IGNORE INTO assertion_evidence
               (assertion_id, evidence_id, evidence_relationship,
                independent_corroboration, created_at)
               SELECT ?, evidence_id, evidence_relationship,
                      independent_corroboration, ?
               FROM assertion_evidence
               WHERE assertion_id = ?""",
            (winner_id, ts, loser_id),
        ).rowcount

        audit_cb(
            conn,
            "assertion_supersession",
            "assertion",
            loser_id,
            {"confidence": loser_conf, "currentness_state": "current"},
            {
                "superseded_by": winner_id,
                "currentness_state": "historical",
                "confidence_delta": delta,
            },
            f"Tier2 supersession: {loser_id} (conf={loser_conf}) superseded by {winner_id} (conf={winner_conf})",
        )

        superseded_count += 1
        supersessions.append(
            {
                "winner": winner_id,
                "loser": loser_id,
                "subject": row["subject_name"],
                "predicate": row["predicate"],
                "winner_conf": winner_conf,
                "loser_conf": loser_conf,
                "delta": delta,
                "evidence_transferred": transferred,
            }
        )

    return superseded_count, supersessions
