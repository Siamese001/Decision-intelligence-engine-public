"""Action Register TTL Sweeper & Dynamic Decay.

Applies a continuous safety-net compactor for actions in p0_p1_action_register:
identifies items past due date (>30 days) and transitions them to 'closed / stale_ttl_expired'.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


class ActionTTLSweeper:
    """Sweeper that identifies and closes expired/zombie actions in p0_p1_action_register."""

    def __init__(self, ttl_days: int = 30, dry_run: bool = False):
        self.ttl_days = ttl_days
        self.dry_run = dry_run

    def sweep_action_register(
        self,
        conn: Any,
        as_of_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sweep open actions past TTL threshold and transition to closed status."""
        ref_dt = (
            datetime.fromisoformat(as_of_date)
            if as_of_date
            else datetime.now(timezone.utc)
        )
        cursor = conn.cursor()

        cursor.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table' AND name='p0_p1_action_register'"""
        )
        if not cursor.fetchone():
            return {"status": "NO_ACTION_REGISTER_TABLE", "swept_count": 0, "swept_actions": []}

        # Check table columns
        cursor.execute("PRAGMA table_info(p0_p1_action_register)")
        columns = {row[1] for row in cursor.fetchall()}

        target_date_col = None
        for candidate in ("target_resolution_date", "due_date", "created_at"):
            if candidate in columns:
                target_date_col = candidate
                break

        if not target_date_col:
            return {"status": "NO_DATE_COLUMN_FOUND", "swept_count": 0, "swept_actions": []}

        cursor.execute(
            f"""SELECT action_id, action_title, priority, lane, {target_date_col}
               FROM p0_p1_action_register
               WHERE status = 'open'"""
        )
        rows = cursor.fetchall()
        swept_actions = []

        for action_id, title, priority, lane, date_val in rows:
            if not date_val:
                continue
            # Do not auto-sweep P0 compliance items
            if priority == "P0" or "compliance" in str(lane).lower() or "security" in str(lane).lower():
                continue

            try:
                # Attempt parsing YYYY-MM-DD or MM/DD/YYYY
                if "-" in date_val:
                    act_dt = datetime.strptime(date_val[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                elif "/" in date_val:
                    act_dt = datetime.strptime(date_val[:10], "%m/%d/%Y").replace(tzinfo=timezone.utc)
                else:
                    continue

                age_days = (ref_dt - act_dt).days
                if age_days > self.ttl_days:
                    swept_actions.append(
                        {
                            "action_id": action_id,
                            "action_title": title,
                            "age_days": age_days,
                            "date_val": date_val,
                        }
                    )
                    if not self.dry_run:
                        cursor.execute(
                            """UPDATE p0_p1_action_register
                               SET status = 'closed / stale_ttl_expired'
                               WHERE action_id = ?""",
                            (action_id,),
                        )
            except Exception:
                continue

        return {
            "status": "PASS",
            "ttl_days": self.ttl_days,
            "swept_count": len(swept_actions),
            "swept_actions": swept_actions,
        }


def _parse_to_utc(val: Any) -> Optional[datetime]:
    if not val:
        return None
    s = str(val).strip()
    try:
        if "/" in s:
            dt = datetime.strptime(s[:10], "%m/%d/%Y")
            return dt.replace(tzinfo=timezone.utc)
        clean = s.replace("Z", "+00:00")
        if len(clean) == 10 and "-" in clean:
            clean = f"{clean}T23:59:59+00:00"
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class AssertionTTLSweeper:
    """Sweeper that transitions stale assertions (stale_after <= now) when superseded."""

    def __init__(self, dry_run: bool = False, require_supersession: bool = True):
        self.dry_run = dry_run
        self.require_supersession = require_supersession

    def _has_active_replacement(
        self, cursor: Any, aid: str, subject_id: str, predicate: str, stale_dt: datetime, columns: set[str]
    ) -> bool:
        if not subject_id or not predicate:
            return False
        curr_clause = "AND currentness_state = 'current'" if "currentness_state" in columns else ""
        status_clause = "AND status IN ('CONFIRMED', 'PROPOSED')" if "status" in columns else ""
        stale_col = "stale_after" if "stale_after" in columns else "NULL"
        rec_col = "recorded_at" if "recorded_at" in columns else ("created_at" if "created_at" in columns else "NULL")
        cursor.execute(
            f"""SELECT assertion_id, {stale_col}, {rec_col}
               FROM assertion
               WHERE subject_node_id = ?
                 AND predicate = ?
                 AND assertion_id != ?
                 {curr_clause}
                 {status_clause}""",
            (subject_id, predicate, aid),
        )
        for other_id, other_stale, other_rec in cursor.fetchall():
            if not other_stale:
                return True
            other_stale_dt = _parse_to_utc(other_stale)
            if other_stale_dt and other_stale_dt > stale_dt:
                return True
            other_rec_dt = _parse_to_utc(other_rec)
            if other_rec_dt and other_rec_dt > stale_dt:
                return True
        return False

    def sweep_stale_assertions(
        self,
        conn: Any,
        as_of_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sweep assertions past stale_after threshold and transition to SUPERSEDED when superseded."""
        cursor = conn.cursor()
        cursor.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion'"""
        )
        if not cursor.fetchone():
            return {"status": "NO_ASSERTION_TABLE", "swept_count": 0, "swept_assertions": []}

        # Check table columns
        cursor.execute("PRAGMA table_info(assertion)")
        columns = {row[1] for row in cursor.fetchall()}

        has_currentness = "currentness_state" in columns
        has_retrieval_eligible = "retrieval_eligible" in columns
        has_superseded_at = "superseded_at" in columns
        has_updated_at = "updated_at" in columns
        has_val_state = "validation_state" in columns
        has_asrt_type = "assertion_type" in columns
        has_qualifiers = "qualifiers_json" in columns
        has_valid_to = "valid_to" in columns
        has_status = "status" in columns
        sup_by_col = (
            "superseded_by_assertion_id"
            if "superseded_by_assertion_id" in columns
            else "NULL AS superseded_by_assertion_id"
        )

        cursor.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_document'"""
        )
        has_search_doc = cursor.fetchone() is not None

        ref_dt = _parse_to_utc(as_of_date) if as_of_date else datetime.now(timezone.utc)
        if ref_dt is None:
            ref_dt = datetime.now(timezone.utc)
        now_iso = ref_dt.isoformat()

        # Build query supporting both legacy (status/stale_after) and production (valid_to/qualifiers_json) schemas
        asrt_type_expr = "assertion_type" if has_asrt_type else "'extracted_claim' AS assertion_type"
        stale_expr = (
            "stale_after"
            if "stale_after" in columns
            else (
                "COALESCE(valid_to, json_extract(qualifiers_json, '$.stale_after'))"
                if has_valid_to and has_qualifiers
                else (
                    "valid_to"
                    if has_valid_to
                    else "NULL"
                )
            )
        )
        where_status = (
            "status IN ('CONFIRMED', 'PROPOSED')"
            if has_status
            else "currentness_state = 'current'"
        )

        cursor.execute(
            f"""SELECT assertion_id, subject_node_id, predicate,
                       {('status' if has_status else 'currentness_state')} AS status,
                       {stale_expr} AS stale_after,
                       {sup_by_col},
                       {asrt_type_expr}
                FROM assertion
                WHERE {where_status}
                  AND {stale_expr} IS NOT NULL
                  AND {stale_expr} != ''"""
        )
        rows = cursor.fetchall()
        swept_assertions = []
        retained_unsuperseded = []

        for row in rows:
            aid = row[0] if isinstance(row, (tuple, list)) else row["assertion_id"]
            subject_id = row[1] if isinstance(row, (tuple, list)) else row["subject_node_id"]
            predicate = row[2] if isinstance(row, (tuple, list)) else row["predicate"]
            status_val = row[3] if isinstance(row, (tuple, list)) else row["status"]
            stale_val = row[4] if isinstance(row, (tuple, list)) else row["stale_after"]
            sup_by_val = row[5] if isinstance(row, (tuple, list)) else row["superseded_by_assertion_id"]
            asrt_type = (row[6] if len(row) > 6 else "extracted_claim") if isinstance(row, (tuple, list)) else (row["assertion_type"] if "assertion_type" in (row.keys() if hasattr(row, "keys") else []) else "extracted_claim")

            if not stale_val:
                continue

            stale_dt = _parse_to_utc(stale_val)
            if stale_dt is None:
                continue

            if stale_dt <= ref_dt:
                is_exploratory = asrt_type in ("assumption", "hypothesis")
                if (
                    self.require_supersession
                    and not is_exploratory
                    and status_val in ("CONFIRMED", "current")
                    and not sup_by_val
                    and not self._has_active_replacement(cursor, aid, subject_id, predicate, stale_dt, columns)
                ):
                    retained_unsuperseded.append(aid)
                    continue

                swept_assertions.append({
                    "assertion_id": aid,
                    "subject_node_id": subject_id,
                    "predicate": predicate,
                    "stale_after": stale_val,
                })
                if not self.dry_run:
                    set_clauses: list[str] = []
                    params: list[Any] = []
                    if has_status:
                        set_clauses.append("status = 'SUPERSEDED'")
                    if has_currentness:
                        set_clauses.append("currentness_state = 'historical'")
                    if has_val_state:
                        set_clauses.append("validation_state = 'superseded'")
                    if has_retrieval_eligible:
                        set_clauses.append("retrieval_eligible = 0")
                    if has_superseded_at:
                        set_clauses.append("superseded_at = ?")
                        params.append(now_iso)
                    if has_updated_at:
                        set_clauses.append("updated_at = ?")
                        params.append(now_iso)
                    params.append(aid)

                    if set_clauses:
                        cursor.execute(
                            f"""UPDATE assertion
                                SET {', '.join(set_clauses)}
                                WHERE assertion_id = ?""",
                            params,
                        )
                    if has_search_doc:
                        cursor.execute(
                            """UPDATE search_document
                               SET currentness_state = 'historical',
                                   retrieval_eligible = 0,
                                   updated_at = ?
                               WHERE assertion_id = ?""",
                            (now_iso, aid),
                        )

        if hasattr(conn, "commit") and not self.dry_run:
            conn.commit()

        return {
            "status": "PASS",
            "as_of_date": ref_dt.isoformat(),
            "swept_count": len(swept_assertions),
            "swept_assertions": swept_assertions,
            "retained_unsuperseded_ids": retained_unsuperseded,
        }
