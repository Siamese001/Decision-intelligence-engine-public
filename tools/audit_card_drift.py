"""Bi-directional drift and parity audit between legacy static cards and active Knowledge Graph.

Cardinal Invariant:
Card prose is NEVER an input or evidence into the Knowledge Graph.
Cards are read-only, access-filtered saved view projections materialized
dynamically from verified graph assertions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.card_views import materialize_card_view
from dual_pipeline.core import read_json, utc_now


def _clean_markdown_text(text: str) -> str:
    cleaned = re.sub(r"[*_#`\[\]]", " ", text)
    cleaned = re.sub(r"\(https?://[^)]+\)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip().lower()


def _extract_legacy_propositions(card_path: Path) -> list[str]:
    lines = card_path.read_text(encoding="utf-8").splitlines()
    propositions: list[str] = []
    in_frontmatter = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if stripped.startswith("- ") or stripped.startswith("* ") or re.match(r"^\d+\.\s+", stripped):
            clean = _clean_markdown_text(stripped)
            if len(clean) > 15:
                propositions.append(clean)
    return propositions


def audit_card_drift(
    connection: Any,
    *,
    card_id: str,
    legacy_card_path: Path | None = None,
    generation_id: str,
    graph_sha256: str,
    authorization_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit parity between legacy static markdown prose and active graph assertions."""
    auth = authorization_context or {
        "audiences": ["internal_consulting"],
        "classification_clearance": "internal",
        "candidate_review": False,
    }

    receipt = materialize_card_view(
        connection,
        card_id=card_id,
        generation_id=generation_id,
        graph_sha256=graph_sha256,
        authorization_context=auth,
    )

    approved_manifest = receipt.get("lane_manifests", {}).get("approved", [])
    approved_ids = [item["object_id"] for item in approved_manifest]

    # Fetch assertion details
    graph_claims: list[dict[str, Any]] = []
    if approved_ids:
        placeholders = ",".join("?" for _ in approved_ids)
        rows = connection.execute(
            f"""SELECT assertion_id, subject_node_id, predicate, object_literal, object_node_id, confidence_score
                  FROM assertion
                 WHERE assertion_id IN ({placeholders})""",
            approved_ids,
        ).fetchall()
        for r in rows:
            text = str(r["object_literal"] or r["object_node_id"] or "")
            clean = _clean_markdown_text(f"{r['subject_node_id']} {r['predicate']} {text}")
            graph_claims.append({
                "assertion_id": str(r["assertion_id"]),
                "subject": str(r["subject_node_id"]),
                "predicate": str(r["predicate"]),
                "text": text,
                "clean_tokens": set(clean.split()),
            })

    # Resolve legacy card path
    card_file = legacy_card_path
    if card_file is None:
        cards_dir = ROOT / "cards"
        matches = list(cards_dir.glob(f"{card_id}*.md"))
        if matches:
            card_file = matches[0]

    legacy_props = _extract_legacy_propositions(card_file) if (card_file and card_file.is_file()) else []

    grounded_assertions: list[str] = []
    unrendered_in_legacy: list[str] = []

    for gc in graph_claims:
        tokens = gc["clean_tokens"]
        matched = False
        for lp in legacy_props:
            lp_tokens = set(lp.split())
            intersection = tokens & lp_tokens
            if len(intersection) >= min(3, len(tokens)):
                matched = True
                break
        if matched:
            grounded_assertions.append(gc["assertion_id"])
        else:
            unrendered_in_legacy.append(gc["assertion_id"])

    orphan_legacy_propositions: list[str] = []
    for lp in legacy_props:
        lp_tokens = set(lp.split())
        matched = False
        for gc in graph_claims:
            intersection = lp_tokens & gc["clean_tokens"]
            if len(intersection) >= min(3, len(gc["clean_tokens"])):
                matched = True
                break
        if not matched:
            orphan_legacy_propositions.append(lp)

    coverage = len(grounded_assertions) / len(graph_claims) if graph_claims else 1.0

    return {
        "status": "PASS",
        "card_id": card_id,
        "legacy_file_path": str(card_file) if card_file else None,
        "active_graph_assertions_count": len(graph_claims),
        "legacy_markdown_propositions_count": len(legacy_props),
        "grounded_assertions_count": len(grounded_assertions),
        "unrendered_graph_assertions_count": len(unrendered_in_legacy),
        "orphan_legacy_propositions_count": len(orphan_legacy_propositions),
        "graph_parity_ratio": round(coverage, 4),
        "sample_orphan_propositions": orphan_legacy_propositions[:5],
        "audited_at": utc_now(),
    }


def audit_all_cards_drift(
    runtime_root: Path,
    authorization_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_root = Path(runtime_root).resolve()
    active_pair = read_json(runtime_root / "active_pair.json")
    graph_path = (runtime_root / active_pair["graph_path"]).resolve()

    con = create_sqlite_conn(graph_path, readonly=True)
    results: list[dict[str, Any]] = []

    try:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(
            "SELECT card_id FROM card_view_definition WHERE active=1 AND view_state='active' ORDER BY card_id"
        ).fetchall()
        for r in rows:
            cid = str(r["card_id"])
            card_report = audit_card_drift(
                con,
                card_id=cid,
                generation_id=active_pair["generation_id"],
                graph_sha256=active_pair["graph_sha256"],
                authorization_context=authorization_context,
            )
            results.append(card_report)
    finally:
        con.close()

    total_graph = sum(r["active_graph_assertions_count"] for r in results)
    total_grounded = sum(r["grounded_assertions_count"] for r in results)
    total_orphans = sum(r["orphan_legacy_propositions_count"] for r in results)

    return {
        "status": "PASS",
        "active_generation_id": active_pair["generation_id"],
        "active_graph_sha256": active_pair["graph_sha256"],
        "cards_audited_count": len(results),
        "total_active_graph_assertions": total_graph,
        "total_grounded_assertions": total_grounded,
        "total_orphan_legacy_propositions": total_orphans,
        "fleet_parity_ratio": round(total_grounded / total_graph, 4) if total_graph else 1.0,
        "cards": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit parity and drift between static markdown cards and active Knowledge Graph assertions."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT / ".local" / "dual_pipeline",
        help="Runtime root",
    )
    parser.add_argument("--card-id", type=str, help="Specific card ID to audit")
    args = parser.parse_args()

    if args.card_id:
        active_pair = read_json(args.runtime_root / "active_pair.json")
        graph_path = (args.runtime_root / active_pair["graph_path"]).resolve()
        con = create_sqlite_conn(graph_path, readonly=True)
        try:
            con.execute("PRAGMA query_only=ON")
            report = audit_card_drift(
                con,
                card_id=args.card_id,
                generation_id=active_pair["generation_id"],
                graph_sha256=active_pair["graph_sha256"],
            )
        finally:
            con.close()
    else:
        report = audit_all_cards_drift(args.runtime_root)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
