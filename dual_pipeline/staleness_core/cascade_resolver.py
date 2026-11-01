"""Cascade Invalidation Resolver for Stage L1 / Universal Write Gateway.

Traverses the active SQLite knowledge graph to deterministically identify and
supersede child assertions and action register items matching strategic scope modulations.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Set

from dual_pipeline.storage.governed_context import GovernedStorageContext

from .models import InvalidationResolution, StrategicIntentSignal
from .two_track_classifier import TwoTrackClassifier


def _matches_topic(text: str, topic_tokens: Set[str], topic_raw: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    if topic_raw in lower:
        return True
    tokens = set(re.findall(r"[a-z0-9]+", lower))
    if topic_tokens.issubset(tokens):
        return True
    # Stemmed token subset (handles singular/plural e.g. pod vs pods)
    stemmed_text = {t[:-1] if t.endswith("s") and len(t) > 3 else t for t in tokens}
    stemmed_topic = {t[:-1] if t.endswith("s") and len(t) > 3 else t for t in topic_tokens}
    return stemmed_topic.issubset(stemmed_text) if stemmed_topic else False


class CascadeResolver:
    """Resolves and executes cascade invalidation on SQLite knowledge graph."""

    def __init__(
        self,
        classifier: Optional[TwoTrackClassifier] = None,
        dry_run: bool = False,
    ):
        self.classifier = classifier or TwoTrackClassifier()
        self.dry_run = dry_run

    def resolve_and_apply(
        self,
        conn: Any,
        signals: List[StrategicIntentSignal],
        directive_id: str = "direct-cascade",
        meeting_id: str = "unknown-meeting",
        meeting_date_iso: Optional[str] = None,
    ) -> InvalidationResolution:
        """Resolve signals against live SQLite database and optionally apply mutations."""
        resolution = InvalidationResolution(
            directive_id=directive_id,
            meeting_id=meeting_id,
        )
        if not signals:
            return resolution

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_to_date = meeting_date_iso or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        cursor = conn.cursor()

        for signal in signals:
            topic_raw = signal.target_topic_or_entity.lower().strip()
            topic_suffix = topic_raw.split(":", 1)[-1]
            topic_tokens = set(re.findall(r"[a-z0-9]+", topic_suffix))

            # 1. Search p0_p1_action_register
            cursor.execute(
                """SELECT 1 FROM sqlite_master WHERE type='table' AND name='p0_p1_action_register'"""
            )
            if cursor.fetchone():
                cursor.execute(
                    """SELECT action_id, source_validation_id, action_title, lane, priority, supporting_context
                       FROM p0_p1_action_register
                       WHERE status = 'open'"""
                )
                open_actions = cursor.fetchall()

                for row in open_actions:
                    action_id, source_val_id, title, lane, priority, ctx = row
                    combined_act_text = f"{action_id} {source_val_id} {title} {lane} {ctx}"

                    # Check if action matches topic
                    if _matches_topic(combined_act_text, topic_tokens, topic_suffix):
                        classification = self.classifier.classify_item(
                            item_id=action_id,
                            item_type="action",
                            title_or_claim=title,
                            lane_or_predicate=lane,
                            priority=priority,
                            context=ctx,
                        )

                        if classification.track == "TRACK_B_COMPLIANCE":
                            resolution.quarantined_compliance_items.append(action_id)
                        elif classification.track == "TRACK_A_STRATEGIC":
                            resolution.closed_actions.append(action_id)
                            if not self.dry_run:
                                cursor.execute(
                                    """UPDATE p0_p1_action_register
                                       SET status = 'closed / superseded_by_strategic_readout'
                                       WHERE action_id = ?""",
                                    (action_id,),
                                )

            # 2. Search assertions
            cursor.execute(
                """SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion'"""
            )
            if cursor.fetchone():
                cursor.execute("PRAGMA table_info(assertion)")
                assert_cols = {r[1] for r in cursor.fetchall()}
                has_valid_from = "valid_from" in assert_cols

                cursor.execute(
                    """SELECT assertion_id, subject_node_id, predicate, object_literal, validation_priority
                       FROM assertion
                       WHERE currentness_state = 'current' AND retrieval_eligible = 1"""
                )
                assertions = cursor.fetchall()

                for row in assertions:
                    assert_id, sub_id, pred, obj_lit, val_prio = row
                    combined_assert_text = f"{assert_id} {sub_id} {pred} {obj_lit}"

                    if _matches_topic(combined_assert_text, topic_tokens, topic_suffix):
                        classification = self.classifier.classify_item(
                            item_id=assert_id,
                            item_type="assertion",
                            title_or_claim=str(obj_lit or pred),
                            lane_or_predicate=pred,
                            priority=val_prio,
                        )

                        if classification.track == "TRACK_B_COMPLIANCE":
                            resolution.quarantined_compliance_items.append(assert_id)
                        elif classification.track == "TRACK_A_STRATEGIC":
                            resolution.superseded_assertions.append(assert_id)
                            if not self.dry_run:
                                if has_valid_from:
                                    cursor.execute(
                                        """UPDATE assertion
                                           SET currentness_state = 'historical',
                                               valid_to = CASE
                                                   WHEN valid_from IS NOT NULL AND valid_from > ? THEN valid_from
                                                   ELSE ?
                                               END,
                                               superseded_at = ?,
                                               retrieval_eligible = 0
                                           WHERE assertion_id = ?""",
                                        (valid_to_date, valid_to_date, now_iso, assert_id),
                                    )
                                else:
                                    cursor.execute(
                                        """UPDATE assertion
                                           SET currentness_state = 'historical',
                                               valid_to = ?,
                                               superseded_at = ?,
                                               retrieval_eligible = 0
                                           WHERE assertion_id = ?""",
                                        (valid_to_date, now_iso, assert_id),
                                    )
                                # Sync search_document if table exists
                                cursor.execute(
                                    """SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_document'"""
                                )
                                if cursor.fetchone():
                                    cursor.execute(
                                        """UPDATE search_document
                                           SET currentness_state = 'historical',
                                               retrieval_eligible = 0,
                                               updated_at = ?
                                           WHERE assertion_id = ?""",
                                        (now_iso, assert_id),
                                    )

            # 3. Check executive action tables (dan_next_best_action, account_team_next_best_action)
            for table_name in ("dan_next_best_action", "account_team_next_best_action"):
                cursor.execute(
                    f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table_name}'"
                )
                if cursor.fetchone():
                    cursor.execute(
                        f"""SELECT action_rank, action, owner, why_it_matters, linked_validation_ids
                           FROM {table_name}"""
                    )
                    actions = cursor.fetchall()
                    for rank, act_title, owner, why, linked_vals in actions:
                        combined_text = f"{act_title} {why} {linked_vals}"
                        if _matches_topic(combined_text, topic_tokens, topic_suffix):
                            classification = self.classifier.classify_item(
                                item_id=f"{table_name}_rank_{rank}",
                                item_type="executive_action",
                                title_or_claim=act_title,
                                lane_or_predicate=why,
                            )
                            if classification.track == "TRACK_A_STRATEGIC":
                                resolution.closed_actions.append(f"{table_name}_rank_{rank}: {act_title}")
                                if not self.dry_run:
                                    cursor.execute(
                                        f"DELETE FROM {table_name} WHERE action_rank = ?",
                                        (rank,),
                                    )

        # Re-rank executive action tables if any were deleted
        if not self.dry_run:
            for table_name in ("dan_next_best_action", "account_team_next_best_action"):
                cursor.execute(
                    f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table_name}'"
                )
                if cursor.fetchone():
                    cursor.execute(f"SELECT action_rank FROM {table_name} ORDER BY action_rank")
                    remaining = [r[0] for r in cursor.fetchall()]
                    for new_rank, old_rank in enumerate(remaining, start=1):
                        if new_rank != old_rank:
                            cursor.execute(
                                f"UPDATE {table_name} SET action_rank = ? WHERE action_rank = ?",
                                (new_rank, old_rank),
                            )

        # Deduplicate results
        resolution.closed_actions = sorted(list(set(resolution.closed_actions)))
        resolution.superseded_assertions = sorted(list(set(resolution.superseded_assertions)))
        resolution.quarantined_compliance_items = sorted(
            list(set(resolution.quarantined_compliance_items))
        )

        return resolution

    def resolve_for_active_graph(
        self,
        signals: List[StrategicIntentSignal],
        runtime_root: Optional[Path] = None,
        directive_id: str = "direct-cascade",
        meeting_id: str = "unknown-meeting",
        meeting_date_iso: Optional[str] = None,
    ) -> InvalidationResolution:
        """Resolve and apply signals against the currently active graph generation."""
        import json
        rt = runtime_root or Path(".local/dual_pipeline")
        ap_file = rt / "active_pair.json"
        if not ap_file.exists():
            raise FileNotFoundError(f"active_pair.json not found at {ap_file}")
        with open(ap_file, "r", encoding="utf-8") as f:
            ap_data = json.load(f)

        graph_rel_path = ap_data.get("graph_path")
        if not graph_rel_path:
            raise ValueError("graph_path missing from active_pair.json")

        graph_file = (rt.parent / graph_rel_path).resolve() if (rt.parent / graph_rel_path).exists() else (rt / graph_rel_path).resolve()
        if not graph_file.exists():
            raise FileNotFoundError(f"Active graph file not found at {graph_file}")

        conn = GovernedStorageContext.connect(graph_file, readonly=self.dry_run)
        try:
            resolution = self.resolve_and_apply(
                conn=conn,
                signals=signals,
                directive_id=directive_id,
                meeting_id=meeting_id,
                meeting_date_iso=meeting_date_iso,
            )
            if not self.dry_run:
                conn.commit()
            return resolution
        finally:
            conn.close()
