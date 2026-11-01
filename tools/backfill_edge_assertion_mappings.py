#!/usr/bin/env python3
"""tools/backfill_edge_assertion_mappings.py

Governed maintenance tool to align unmapped October edges:
1. Provisions canonical assertion for edge_waterfall_slide_anchor_lens.
2. Backfills 3 bridge mappings in legacy_edge_assertion_map.
3. Checkpoints WAL to primary database file.
4. Generates and validates mandatory 5-point physical proof receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from storage.connection import configure_pragmas, create_sqlite_conn
from tools.graph_mutation_proof_core import prove, snapshot


def resolve_active_graph_path() -> Path:
    runtime_root = resolve_authoritative_runtime_root()
    active_pair_path = runtime_root / "active_pair.json"
    if not active_pair_path.is_file():
        raise FileNotFoundError(f"Active pair file missing at {active_pair_path}")
    pair_data = json.loads(active_pair_path.read_text(encoding="utf-8"))
    graph_rel = pair_data.get("graph_path")
    if not graph_rel:
        raise ValueError("graph_path missing in active_pair.json")
    graph_path = runtime_root / graph_rel
    if not graph_path.is_file():
        raise FileNotFoundError(f"Graph database file missing at {graph_path}")
    return graph_path


def execute_backfill(db_path: Path, output_receipt: Path | None = None) -> Dict[str, Any]:
    # 1. Baseline snapshot before mutation
    before_snapshot = snapshot(db_path)

    asrt_id = "asrt_waterfall_slide_anchor_lens"
    fingerprint = hashlib.sha256(
        b"artifact_lens_1008_metrics_waterfall_slide:ROUTES_TO:workstream_lens"
    ).hexdigest()

    mappings = [
        ("edge_eduardo_more_leads_atlas", "asrt_eduardo_more_atlas_lead", 1.0, "structured", "2026-10-14T19:00:00Z"),
        ("edge_mandy_hopmann_contributes_to_project_atlas", "assertion-b4cb943e289ebaf12b40251f29c4", 1.0, "structured", "2026-10-14T19:00:00Z"),
        ("edge_waterfall_slide_anchor_lens", asrt_id, 1.0, "structured", "2026-10-14T19:00:00Z"),
    ]

    conn = create_sqlite_conn(db_path, readonly=False)
    configure_pragmas(conn, readonly=False)

    try:
        cur = conn.cursor()

        # Check if assertion already exists
        cur.execute("SELECT 1 FROM assertion WHERE assertion_id = ?", (asrt_id,))
        asrt_exists = cur.fetchone() is not None

        if not asrt_exists:
            cur.execute(
                """
                INSERT INTO assertion (
                    assertion_id, assertion_fingerprint, subject_node_id, predicate, legacy_predicate,
                    object_node_id, object_literal, object_literal_type, assertion_type, atomicity_state,
                    scope_text, speaker_or_author, primary_source_id, extraction_run_id, extraction_method,
                    origin_type, confidence_label, confidence_score, validation_state, currentness_state,
                    valid_from, valid_to, date_precision, date_status, recorded_at,
                    superseded_at, superseded_by_assertion_id, resolution_state, canonical_status,
                    canonical_eligible, retrieval_eligible, materiality, classification, allowed_audiences_json,
                    hitl_required, validation_priority, validation_owner, qualifiers_json, legacy_record_type,
                    legacy_record_ids_json, created_at, updated_at, approval_reference
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    asrt_id, fingerprint, "artifact_lens_1008_metrics_waterfall_slide", "ROUTES_TO", "ANCHOR_FOR",
                    "workstream_lens", None, None, "confirmed_canonical_fact", "atomic",
                    "Technical artifact anchor for LENS accuracy waterfall metrics presented during 10/08/2026 sprint review.",
                    "Amit Ayer", "source-20261008-lens-sprint-review", "extract_v48_legacy_backfill_001", "deterministic_legacy_edge_backfill",
                    "legacy_migration", "high", 0.95, "accepted_legacy", "current",
                    "2026-10-08", None, "day", "exact", "2026-10-14T19:00:00Z",
                    None, None, "resolved", "candidate",
                    1, 1, "medium", "internal", '["internal_consulting"]',
                    0, "P1", "Amit Ayer", '{"legacy_edge_ids":["edge_waterfall_slide_anchor_lens"],"legacy_predicate":"ANCHOR_FOR"}', "edge",
                    '["edge_waterfall_slide_anchor_lens"]', "2026-10-14T19:00:00Z", "2026-10-14T19:00:00Z", None
                ),
            )

        # Insert edge mappings
        for edge_id, assertion_id, conf, state, created_at in mappings:
            cur.execute(
                """
                INSERT OR REPLACE INTO legacy_edge_assertion_map (
                    edge_id, assertion_id, migration_confidence, migration_state, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (edge_id, assertion_id, conf, state, created_at),
            )

        conn.commit()

        # Checkpoint WAL so physical bytes reach the main database file
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    finally:
        conn.close()

    # 2. Snapshot after mutation
    after_snapshot = snapshot(db_path, tables=list(before_snapshot["row_counts"]))

    # 3. Readback probes
    probes = {
        "probe_canonical_assertion": (
            "SELECT count(*) FROM assertion WHERE assertion_id = ?",
            (asrt_id,),
        ),
        "probe_eduardo_mapping": (
            "SELECT count(*) FROM legacy_edge_assertion_map WHERE edge_id = ? AND assertion_id = ?",
            ("edge_eduardo_more_leads_atlas", "asrt_eduardo_more_atlas_lead"),
        ),
        "probe_mandy_mapping": (
            "SELECT count(*) FROM legacy_edge_assertion_map WHERE edge_id = ? AND assertion_id = ?",
            ("edge_mandy_hopmann_contributes_to_project_atlas", "assertion-b4cb943e289ebaf12b40251f29c4"),
        ),
        "probe_slide_anchor_mapping": (
            "SELECT count(*) FROM legacy_edge_assertion_map WHERE edge_id = ? AND assertion_id = ?",
            ("edge_waterfall_slide_anchor_lens", asrt_id),
        ),
    }

    expected_ids = [
        asrt_id,
        "edge_eduardo_more_leads_atlas",
        "edge_mandy_hopmann_contributes_to_project_atlas",
        "edge_waterfall_slide_anchor_lens",
    ]

    expected_deltas = {
        "assertion": 1 if not asrt_exists else 0,
        "legacy_edge_assertion_map": 3,
    }

    # 4. Mandatory 5-point proof verification
    proof_receipt = prove(
        before=before_snapshot,
        after=after_snapshot,
        expected_ids=expected_ids,
        expected_deltas=expected_deltas,
        readback_probes=probes,
        strict_delta=False,
    )

    if output_receipt:
        output_receipt.parent.mkdir(parents=True, exist_ok=True)
        output_receipt.write_text(json.dumps(proof_receipt, indent=2), encoding="utf-8")

    return proof_receipt


def _parse_qualifiers_dict(qraw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(qraw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _enrich_row_qualifiers(
    txt: str, cls: Any, qraw: Any
) -> str | None:
    from dual_pipeline.strategic_reasoning_core.constants import STRATEGIC_PROFILE_QUALIFIER
    from dual_pipeline.strategic_reasoning_core.profiling import strategic_profile_for_record
    from dual_pipeline.strategic_reasoning_core.quantitative_ontology import (
        classify_constraint_category,
        extract_quantitative_constraints,
        infer_object_literal_type,
        is_constraint_claim,
    )

    quant = extract_quantitative_constraints(txt)
    is_c = is_constraint_claim(txt)
    if not quant and not is_c:
        return None
    q = _parse_qualifiers_dict(qraw)
    orig_json = json.dumps(q, sort_keys=True)
    if quant:
        q["quantitative_constraints"] = quant
        q["inferred_literal_type"] = infer_object_literal_type(txt)
    if is_c:
        q["constraint_category"] = classify_constraint_category(txt)
        if STRATEGIC_PROFILE_QUALIFIER not in q:
            q[STRATEGIC_PROFILE_QUALIFIER] = strategic_profile_for_record(
                record_type="assertion",
                lane="approved",
                classification=cls,
                qualifiers=q,
                text=txt,
            )
    new_json = json.dumps(q, sort_keys=True)
    return new_json if new_json != orig_json else None


def _collect_quantitative_qualifier_updates(cur: Any, now_iso: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    rows = cur.execute(
        "SELECT assertion_id, scope_text, object_literal, classification, qualifiers_json FROM assertion WHERE currentness_state = 'current'"
    ).fetchall()
    updates: list[tuple[str, str, str]] = []
    sample_ids: list[str] = []
    for aid, st, ol, cls, qraw in rows:
        new_json = _enrich_row_qualifiers(str(st or ol or ""), cls, qraw)
        if new_json is not None:
            updates.append((new_json, now_iso, str(aid)))
            if len(sample_ids) < 6:
                sample_ids.append(str(aid))
    return updates, sample_ids


def execute_quantitative_qualifier_backfill(
    db_path: Path, output_receipt: Path | None = None
) -> Dict[str, Any]:
    """Backfill typed quantitative constraints, constraint categories, and strategic profiles into assertion.qualifiers_json with 5-point physical proof."""
    import datetime as dt
    import uuid

    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    before_snapshot = snapshot(db_path)
    audit_id = f"audit-quant-backfill-{uuid.uuid4().hex[:16]}"
    conn = create_sqlite_conn(db_path, readonly=False)
    configure_pragmas(conn, readonly=False)
    try:
        cur = conn.cursor()
        updates, sample_ids = _collect_quantitative_qualifier_updates(cur, now_iso)
        if updates:
            cur.executemany(
                "UPDATE assertion SET qualifiers_json = ?, updated_at = ? WHERE assertion_id = ?",
                updates,
            )
        cur.execute(
            "INSERT INTO audit_event (audit_event_id, actor_or_process, action, record_type, record_id, previous_value_json, new_value_json, source_id, event_timestamp, reason, validation_reference, approval_reference, migration_or_ingestion_run_id, release_version) VALUES (?, 'quantitative_ontology_backfill', 'backfill_quantitative_qualifiers', 'assertion', ?, NULL, ?, NULL, ?, 'Backfill Wave 1-7 quantitative constraints and constraint categories into assertion qualifiers_json', 'WAVE1-8-QUANT-BACKFILL', 'HITL-10-31-2026', 'run-quant-backfill-001', '1.0.0')",
            (audit_id, sample_ids[0] if sample_ids else "graph", json.dumps({"updated_assertions": len(updates)}), now_iso),
        )
        conn.commit()
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()

    after_snapshot = snapshot(db_path, tables=list(before_snapshot["row_counts"]))
    probes: dict[str, tuple[str, tuple[Any, ...]]] = {
        "probe_audit": ("SELECT 1 FROM audit_event WHERE audit_event_id = ?", (audit_id,)),
    }
    for idx, sid in enumerate(sample_ids[:3]):
        probes[f"probe_assertion_{idx}"] = (
            "SELECT 1 FROM assertion WHERE assertion_id = ? AND qualifiers_json LIKE '%quantitative_constraints%'",
            (sid,),
        )
    proof_receipt = prove(
        before=before_snapshot,
        after=after_snapshot,
        expected_ids=[audit_id, *sample_ids[:3]],
        expected_deltas={"audit_event": 1, "assertion": 0},
        readback_probes=probes,
        strict_delta=False,
    )
    proof_receipt["updated_assertions"] = len(updates)
    proof_receipt["sample_assertion_ids"] = sample_ids
    if output_receipt:
        output_receipt.parent.mkdir(parents=True, exist_ok=True)
        output_receipt.write_text(json.dumps(proof_receipt, indent=2), encoding="utf-8")
    return proof_receipt


def execute_active_generation_quantitative_promotion(
    output_receipt: Path | None = None,
) -> Dict[str, Any]:
    """Clone active generation, backfill quantitative qualifiers, sync sidecar retrieval records, and promote under active_pair.lock."""
    import datetime as dt

    from dual_pipeline.core import sha256_file
    from tools.tier2_dedup_core.hygiene_promotion import (
        finalize_promoted_generation,
        prepare_promoted_generation_paths,
    )

    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rt_root, active_pair_path, active_pair, new_gen_id, new_graph_path, new_sidecar_path, pair_manifest_path = (
        prepare_promoted_generation_paths(None, "quant-ontology-backfill")
    )
    proof_receipt = execute_quantitative_qualifier_backfill(new_graph_path)
    if proof_receipt.get("overall") != "PASS":
        raise RuntimeError(f"Physical proof failed on promoted generation {new_gen_id}: {proof_receipt}")
    new_graph_sha = sha256_file(new_graph_path)
    next_pair = finalize_promoted_generation(
        active_pair_path, active_pair, new_gen_id, new_graph_path,
        new_sidecar_path, pair_manifest_path, new_graph_sha, now_iso,
    )
    proof_receipt["promoted_generation_id"] = new_gen_id
    proof_receipt["previous_generation_id"] = active_pair.get("generation_id")
    proof_receipt["active_pair"] = next_pair
    if output_receipt:
        output_receipt.parent.mkdir(parents=True, exist_ok=True)
        output_receipt.write_text(json.dumps(proof_receipt, indent=2), encoding="utf-8")
    return proof_receipt



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="Path to graph.sqlite")
    parser.add_argument("--out", type=Path, default=None, help="Path to output proof receipt")
    parser.add_argument("--execute", action="store_true", help="Execute backfill")
    args = parser.parse_args(argv)

    db_path = args.db or resolve_active_graph_path()
    out_path = args.out or Path("reports/edge_assertion_backfill_receipt.json")

    print(f"Target Graph: {db_path}")
    print(f"Receipt Path: {out_path}")

    if not args.execute:
        print("Dry run complete. Specify --execute to apply mutations.")
        return 0

    receipt = execute_backfill(db_path, out_path)
    print(json.dumps(receipt, indent=2))

    if receipt.get("overall") == "PASS":
        print("✅ Backfill mutation proved successfully with 5 physical checks.")
        return 0
    else:
        print("❌ Backfill mutation proof FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
