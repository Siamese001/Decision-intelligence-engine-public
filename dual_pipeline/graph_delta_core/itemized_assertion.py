"""Itemized assertion delta tracking and Table 2 generation for GraphDB mutations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.database import create_sqlite_conn, sqlite3


def _fetch_assertions(graph: Path | str | sqlite3.Connection | None) -> dict[str, dict[str, Any]]:
    """Fetch all assertions from a SQLite graph database, indexed by assertion_id."""
    if graph is None:
        return {}

    conn: sqlite3.Connection | None = None
    should_close = False
    try:
        if isinstance(graph, sqlite3.Connection):
            conn = graph
        else:
            p = Path(graph)
            if not p.is_file():
                return {}
            conn = create_sqlite_conn(p, readonly=True)
            should_close = True

        conn.row_factory = sqlite3.Row
        # Check if assertion table exists
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assertion'")
        if not cur.fetchone():
            return {}

        cols = {row[1] for row in conn.execute("PRAGMA table_info(assertion)").fetchall()}
        rows = conn.execute("SELECT * FROM assertion").fetchall()

        assertions: dict[str, dict[str, Any]] = {}
        for r in rows:
            r_dict = dict(r)
            aid = str(r_dict.get("assertion_id") or "")
            if not aid:
                continue

            subj = str(r_dict.get("subject_node_id") or r_dict.get("subject_id") or "")
            pred = str(r_dict.get("predicate") or r_dict.get("legacy_predicate") or "HAS_CLAIM_TEXT")
            lit = str(
                r_dict.get("claim_text")
                or r_dict.get("object_literal")
                or r_dict.get("object_value")
                or r_dict.get("object_node_id")
                or ""
            )

            superseded_by = r_dict.get("superseded_by_assertion_id")
            raw_st = (
                r_dict.get("canonical_status")
                or r_dict.get("validation_state")
                or r_dict.get("lifecycle_state")
                or r_dict.get("status")
                or "ACTIVE"
            )

            if superseded_by:
                status = "SUPERSEDED"
            elif raw_st in ("canonical", "CANONICAL"):
                status = "CANONICAL"
            elif raw_st in ("needs_review", "source_statement", "SOURCE_STATEMENT"):
                status = "SOURCE_STATEMENT"
            else:
                status = str(raw_st).upper()

            assertions[aid] = {
                "assertion_id": aid,
                "subject_node_id": subj,
                "predicate": pred,
                "claim_text": lit,
                "status": status,
                "superseded_by_assertion_id": superseded_by,
                "raw_dict": r_dict,
            }
        return assertions
    except Exception:
        return {}
    finally:
        if should_close and conn is not None:
            conn.close()


def format_itemized_assertion_table(
    items: list[dict[str, Any]],
    *,
    is_noop: bool = False,
) -> str:
    """Format Table 2 (Itemized Assertions) markdown according to AGENTS.md invariant."""
    lines = [
        "### 2. Assertions Inserted into `assertion` (Itemized Assertions)",
        "",
        "| Assertion ID | Subject Node | Predicate | Literal / Claim Text | Status |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    if is_noop:
        lines.append(
            "| — | — | — | *Idempotent no-op: zero assertions inserted or modified.* | `IDEMPOTENT_NOOP` |"
        )
    elif items:
        for asrt in items:
            aid = asrt.get("assertion_id", "")
            subj = asrt.get("subject_node_id", "")
            pred = asrt.get("predicate", "HAS_CLAIM_TEXT")
            lit = (
                asrt.get("claim_text")
                or asrt.get("literal")
                or asrt.get("object_literal")
                or asrt.get("object_node_id")
                or ""
            )
            st = asrt.get("status", "SOURCE_STATEMENT")
            lines.append(f"| `{aid}` | `{subj}` | `{pred}` | *{lit}* | `{st}` |")
    else:
        lines.append("| — | — | — | *No assertions inserted or modified.* | `UNCHANGED` |")

    return "\n".join(lines)


def build_itemized_assertion_delta(
    before_graph: Path | str | sqlite3.Connection | None,
    after_graph: Path | str | sqlite3.Connection | None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Compare assertions before and after mutation to itemize CREATE, MODIFY, SUPERSEDE, NOOP.

    Returns a structured dictionary with counts, categorized item lists, and rendered Table 2 markdown.
    """
    b_assertions = _fetch_assertions(before_graph)
    a_assertions = _fetch_assertions(after_graph)

    b_ids = set(b_assertions.keys())
    a_ids = set(a_assertions.keys())

    created_ids = a_ids - b_ids
    deleted_ids = b_ids - a_ids
    common_ids = a_ids & b_ids

    items: list[dict[str, Any]] = []
    created_count = len(created_ids)
    deleted_count = len(deleted_ids)
    modified_count = 0
    superseded_count = 0
    noop_count = 0

    # 1. Created assertions (in after but not before)
    for aid in sorted(created_ids):
        info = dict(a_assertions[aid])
        info["action"] = "CREATE"
        items.append(info)

    # 2. Common assertions: check for modifications or supersessions
    superseded_items: list[dict[str, Any]] = []
    modified_items: list[dict[str, Any]] = []
    noop_items: list[dict[str, Any]] = []

    for aid in sorted(common_ids):
        b_info = b_assertions[aid]
        a_info = a_assertions[aid]

        if a_info.get("superseded_by_assertion_id") and not b_info.get("superseded_by_assertion_id"):
            superseded_count += 1
            info = dict(a_info)
            info["action"] = "SUPERSEDE"
            superseded_items.append(info)
        elif (
            a_info.get("status") != b_info.get("status")
            or a_info.get("claim_text") != b_info.get("claim_text")
            or a_info.get("predicate") != b_info.get("predicate")
        ):
            modified_count += 1
            info = dict(a_info)
            info["action"] = "MODIFY"
            modified_items.append(info)
        else:
            noop_count += 1
            info = dict(a_info)
            info["action"] = "NOOP"
            noop_items.append(info)

    # 3. Deleted assertions (in before but not after)
    deleted_items: list[dict[str, Any]] = []
    for aid in sorted(deleted_ids):
        info = dict(b_assertions[aid])
        info["action"] = "DELETE"
        info["status"] = "DELETED"
        deleted_items.append(info)

    all_action_items = items + superseded_items + modified_items + deleted_items
    is_noop = (created_count == 0 and modified_count == 0 and superseded_count == 0 and deleted_count == 0)

    # Filtered/limited item list for table rendering (prioritize mutating changes)
    rendered_items = all_action_items if all_action_items else noop_items
    if limit is not None and limit > 0:
        rendered_items = rendered_items[:limit]

    table_md = format_itemized_assertion_table(all_action_items, is_noop=is_noop)

    return {
        "total_before": len(b_ids),
        "total_after": len(a_ids),
        "created_count": created_count,
        "modified_count": modified_count,
        "superseded_count": superseded_count,
        "deleted_count": deleted_count,
        "noop_count": noop_count,
        "is_noop": is_noop,
        "items": rendered_items,
        "markdown_table": table_md,
    }
