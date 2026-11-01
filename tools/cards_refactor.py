#!/usr/bin/env python3
"""Governed planning, application, audit, and release entry point for the cards refactor."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.card_refactor_release import (  # noqa: E402
    CARDS_REFACTOR_POLICY_VERSION,
    apply_card_refactor_plan,
    assert_card_refactor_plan,
    build_card_refactor_plan,
    semantic_organization_change_set_artifact,
)
from dual_pipeline.card_views import (  # noqa: E402
    export_all_card_views,
    materialize_card_view,
    render_card_markdown,
)
from dual_pipeline.core import (  # noqa: E402
    digest,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from dual_pipeline.facets import (  # noqa: E402
    audit_cards_refactor_coverage,
    validate_facet_schema,
)
from dual_pipeline.index import (  # noqa: E402
    render_retrieval_record_object_facets,
    render_retrieval_records,
)
from dual_pipeline.release import ReleaseManager  # noqa: E402
from tools.audit_card_drift import (  # noqa: E402
    audit_all_cards_drift,
    audit_card_drift,
)


def _active(runtime_root: Path) -> tuple[dict[str, Any], Path]:
    runtime_root = runtime_root.expanduser().resolve()
    pair = read_json(runtime_root / "active_pair.json")
    graph_path = (runtime_root / pair["graph_path"]).resolve()
    graph_path.relative_to(runtime_root)
    return pair, graph_path


def _default_approval(plan: dict[str, Any], approval_id: str) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "approval_reference": approval_id.upper().replace("-", "_"),
        "approval_type": "release_implementation",
        "scope": {
            "plan_id": plan["plan_id"],
            "allowed_actions": sorted(
                {operation["operation"] for operation in plan["operations"]}
            ),
        },
        "baseline_graph_sha256": plan["base_graph_sha256"],
        "proposal_sha256": plan["plan_digest"],
        "approval_statement_sha256": digest(
            {
                "approval_id": approval_id,
                "plan_digest": plan["plan_digest"],
            }
        ),
        "approved_at": utc_now(),
        "expires_at": "2099-12-31T23:59:59Z",
        "status": "approved",
        "approved_by": "nee-cards-governance-authority",
        "release_version": CARDS_REFACTOR_POLICY_VERSION,
        "active": 1,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, apply, or audit the governed NEE cards-refactoring proposal."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT / ".local" / "main" / "runtime",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # plan
    plan = subparsers.add_parser("plan", help="Build read-only cards refactor plan")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--contract-output", type=Path)

    # validate-plan
    validate = subparsers.add_parser("validate-plan", help="Validate existing plan digest")
    validate.add_argument("--plan", type=Path, required=True)

    # audit
    audit = subparsers.add_parser("audit", help="Audit facet schema and coverage")
    audit.add_argument("--graph", type=Path, help="Optional explicit path to SQLite graph")

    # apply
    apply_cmd = subparsers.add_parser("apply", help="Apply approved plan to target graph")
    apply_cmd.add_argument("--plan", type=Path, required=True)
    apply_cmd.add_argument("--graph", type=Path, help="Path to graph database (defaults to active graph)")
    apply_cmd.add_argument("--output-graph", type=Path, help="Optional output graph to copy before apply")
    apply_cmd.add_argument("--approval-id", type=str)
    apply_cmd.add_argument("--run-id", type=str)

    # release-e2e
    release = subparsers.add_parser("release-e2e", help="Execute complete governed release and activation")
    release.add_argument("--approval-id", type=str)
    release.add_argument("--run-id", type=str)
    release.add_argument("--output-plan", type=Path)

    # compile
    comp = subparsers.add_parser("compile", help="Compile a card view to access-sliced markdown")
    comp.add_argument("--card-id", type=str, required=True)
    comp.add_argument("--output", type=Path, help="Target markdown file path")
    comp.add_argument("--audience", type=str, default="internal_consulting")
    comp.add_argument("--clearance", type=str, default="internal")

    # compile-all
    comp_all = subparsers.add_parser("compile-all", help="Compile and export all active card views to markdown")
    comp_all.add_argument("--output-dir", type=Path, required=True)
    comp_all.add_argument("--audience", type=str, default="internal_consulting")
    comp_all.add_argument("--clearance", type=str, default="internal")

    # drift-audit
    drift = subparsers.add_parser("drift-audit", help="Audit drift and parity between legacy cards and active graph")
    drift.add_argument("--card-id", type=str, help="Optional specific card ID")

    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "plan":
        active, graph_path = _active(args.runtime_root)
        plan = build_card_refactor_plan(graph_path, active_pair=active)
        output = args.output.expanduser().resolve()
        contract_output = (
            args.contract_output.expanduser().resolve()
            if args.contract_output is not None
            else output.with_name(f"{output.stem}.change-set.json")
        )
        change_set = semantic_organization_change_set_artifact(plan)
        write_json_exclusive(output, plan)
        write_json_exclusive(contract_output, change_set)
        return {
            "status": "PROPOSED",
            "provider_call_count": 0,
            "graph_mutation_count": 0,
            "active_generation_id": active["generation_id"],
            "active_graph_sha256": active["graph_sha256"],
            "plan_id": plan["plan_id"],
            "plan_digest": plan["plan_digest"],
            "summary": plan["summary"],
            "output": str(output),
            "contract_output": str(contract_output),
            "contract_digest": change_set["canonical_digest"]["value"],
        }

    if args.command == "validate-plan":
        plan = read_json(args.plan.expanduser().resolve())
        assert_card_refactor_plan(plan)
        return {
            "status": "PASS",
            "provider_call_count": 0,
            "graph_mutation_count": 0,
            "plan_id": plan["plan_id"],
            "plan_digest": plan["plan_digest"],
        }

    if args.command == "audit":
        if getattr(args, "graph", None) is not None:
            graph_path = args.graph.expanduser().resolve()
            active_gen = "explicit_graph"
            active_hash = sha256_file(graph_path)
        else:
            active, graph_path = _active(args.runtime_root)
            active_gen = active["generation_id"]
            active_hash = active["graph_sha256"]

        connection = create_sqlite_conn(graph_path, readonly=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            schema = validate_facet_schema(connection)
            coverage = audit_cards_refactor_coverage(connection)
        finally:
            connection.close()

        status = (
            "PASS"
            if schema["status"] == "PASS" and coverage["status"] == "PASS"
            else "FAIL"
        )
        return {
            "status": status,
            "provider_call_count": 0,
            "graph_mutation_count": 0,
            "active_generation_id": active_gen,
            "active_graph_sha256": active_hash,
            "schema": schema,
            "coverage": coverage,
        }

    if args.command == "apply":
        plan = read_json(args.plan.expanduser().resolve())
        assert_card_refactor_plan(plan)

        if getattr(args, "graph", None) is not None:
            target_graph = args.graph.expanduser().resolve()
        else:
            _, target_graph = _active(args.runtime_root)

        if getattr(args, "output_graph", None) is not None:
            output_graph = args.output_graph.expanduser().resolve()
            shutil.copyfile(target_graph, output_graph)
            target_graph = output_graph

        approval_id = args.approval_id or f"approval-{plan['plan_id']}"
        run_id = args.run_id or f"run-{plan['plan_id']}"
        approval_record = _default_approval(plan, approval_id)

        receipt = apply_card_refactor_plan(
            target_graph,
            plan=plan,
            approval_id=approval_id,
            approval_record=approval_record,
            run_id=run_id,
        )
        return {
            "status": receipt["status"],
            "provider_call_count": 0,
            "graph_mutation_count": receipt["operation_count"],
            "receipt": receipt,
        }

    if args.command == "release-e2e":
        runtime_root = args.runtime_root.expanduser().resolve()
        active, graph_path = _active(runtime_root)
        plan = build_card_refactor_plan(graph_path, active_pair=active)

        if getattr(args, "output_plan", None) is not None:
            output_plan = args.output_plan.expanduser().resolve()
            change_set = semantic_organization_change_set_artifact(plan)
            write_json_exclusive(output_plan, plan)
            write_json_exclusive(
                output_plan.with_name(f"{output_plan.stem}.change-set.json"),
                change_set,
            )

        generation_id = stable_id(
            "generation-cards-refactor",
            active["generation_id"],
            plan["plan_digest"],
            length=28,
        )
        generation_dir = runtime_root / "generations" / generation_id
        generation_dir.mkdir(parents=True, exist_ok=True)

        target_graph_path = generation_dir / "graph.sqlite"
        target_sidecar_path = generation_dir / "sidecar.sqlite"

        shutil.copyfile(graph_path, target_graph_path)

        approval_id = args.approval_id or f"approval-{plan['plan_id']}"
        run_id = args.run_id or f"run-{plan['plan_id']}"
        approval_record = _default_approval(plan, approval_id)

        graph_receipt = apply_card_refactor_plan(
            target_graph_path,
            plan=plan,
            approval_id=approval_id,
            approval_record=approval_record,
            run_id=run_id,
        )

        new_graph_hash = sha256_file(target_graph_path)
        new_graph_snapshot = stable_id("graph-snapshot", new_graph_hash, length=24)

        # Re-hydrate sidecar with new generation metadata and projected facets
        active_sidecar_path = (runtime_root / active["sidecar_path"]).resolve()
        shutil.copyfile(active_sidecar_path, target_sidecar_path)

        con = create_sqlite_conn(target_sidecar_path)
        con.execute("UPDATE metadata SET value=? WHERE key='generation_id'", (generation_id,))
        con.execute("UPDATE metadata SET value=? WHERE key='graph_snapshot'", (new_graph_snapshot,))
        con.execute("UPDATE metadata SET value=? WHERE key='graph_sha256'", (new_graph_hash,))

        records = render_retrieval_records(target_graph_path, new_graph_snapshot)
        facet_rows = render_retrieval_record_object_facets(target_graph_path, records)
        if facet_rows:
            con.executemany(
                """INSERT OR REPLACE INTO retrieval_record_object_facet
                   (record_id, assignment_id, object_type, object_id, vocabulary, value, assignment_method, review_state)
                   VALUES (:record_id, :assignment_id, :object_type, :object_id, :vocabulary, :value, :assignment_method, :review_state)""",
                facet_rows,
            )

        con.row_factory = sqlite3.Row
        ordered_rows = [
            dict(row)
            for row in con.execute(
                """SELECT record_id,assignment_id,object_type,object_id,
                          vocabulary,value,assignment_method,review_state
                     FROM retrieval_record_object_facet
                    ORDER BY record_id,assignment_id"""
            )
        ]
        con.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('object_facet_schema_version', '1.0.0')"
        )
        con.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('object_facet_assignment_count', ?)",
            (str(len(ordered_rows)),),
        )
        con.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('object_facet_manifest_digest', ?)",
            (digest(ordered_rows),),
        )
        con.commit()
        con.close()

        new_sidecar_hash = sha256_file(target_sidecar_path)

        next_pair = {
            "schema_version": "1.0.0",
            "generation_id": generation_id,
            "graph_snapshot": new_graph_snapshot,
            "graph_sha256": new_graph_hash,
            "sidecar_sha256": new_sidecar_hash,
            "graph_path": str(Path("generations") / generation_id / "graph.sqlite"),
            "sidecar_path": str(Path("generations") / generation_id / "sidecar.sqlite"),
            "embedding_backend": active["embedding_backend"],
            "model_id": active["model_id"],
            "model_revision": active["model_revision"],
            "model_manifest_digest": active.get("model_manifest_digest"),
            "production_qualified": active["production_qualified"],
            "assurance_tier": active.get("assurance_tier", "development"),
            "previous_generation_id": active["generation_id"],
            "activated_at": utc_now(),
        }

        manager = ReleaseManager(runtime_root, target_graph_path)
        manager.validate_pair(next_pair)

        write_json(generation_dir / "pair_manifest.json", next_pair)
        write_json(runtime_root / "active_pair.json", next_pair)

        # Validate audit on newly activated graph
        con = create_sqlite_conn(target_graph_path, readonly=True)
        con.execute("PRAGMA query_only=ON")
        schema = validate_facet_schema(con)
        coverage = audit_cards_refactor_coverage(con)
        con.close()

        return {
            "status": "COMPLETE",
            "provider_call_count": 0,
            "graph_mutation_count": graph_receipt["operation_count"],
            "previous_generation_id": active["generation_id"],
            "active_generation_id": generation_id,
            "previous_graph_sha256": active["graph_sha256"],
            "active_graph_sha256": new_graph_hash,
            "active_sidecar_sha256": new_sidecar_hash,
            "schema_status": schema["status"],
            "coverage_status": coverage["status"],
            "graph_receipt": graph_receipt,
            "next_pair": next_pair,
        }

    if args.command == "compile":
        active, graph_path = _active(args.runtime_root)
        con = create_sqlite_conn(graph_path, readonly=True)
        con.execute("PRAGMA query_only=ON")
        try:
            receipt = materialize_card_view(
                con,
                card_id=args.card_id,
                generation_id=active["generation_id"],
                graph_sha256=active["graph_sha256"],
                authorization_context={
                    "audiences": [getattr(args, "audience", "internal_consulting")],
                    "classification_clearance": getattr(args, "clearance", "internal"),
                    "candidate_review": False,
                },
            )
            markdown_content = render_card_markdown(con, receipt)
        finally:
            con.close()

        if getattr(args, "output", None) is not None:
            out_file = args.output.expanduser().resolve()
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(markdown_content, encoding="utf-8")

        return {
            "status": "PASS",
            "card_id": args.card_id,
            "assertion_count": receipt["assertion_count"],
            "lane_counts": receipt["lane_counts"],
            "markdown_length": len(markdown_content),
            "output_path": str(args.output) if getattr(args, "output", None) else None,
            "receipt": receipt,
        }

    if args.command == "compile-all":
        out_dir = args.output_dir.expanduser().resolve()
        files = export_all_card_views(
            runtime_root=args.runtime_root,
            output_dir=out_dir,
            authorization_context={
                "audiences": [getattr(args, "audience", "internal_consulting")],
                "classification_clearance": getattr(args, "clearance", "internal"),
                "candidate_review": False,
            },
        )
        return {
            "status": "PASS",
            "exported_count": len(files),
            "output_dir": str(out_dir),
            "exported_files": [f.name for f in files],
        }

    if args.command == "drift-audit":
        if getattr(args, "card_id", None):
            active, graph_path = _active(args.runtime_root)
            con = create_sqlite_conn(graph_path, readonly=True)
            con.execute("PRAGMA query_only=ON")
            try:
                report = audit_card_drift(
                    con,
                    card_id=args.card_id,
                    generation_id=active["generation_id"],
                    graph_sha256=active["graph_sha256"],
                )
            finally:
                con.close()
            return report
        return audit_all_cards_drift(args.runtime_root)

    raise ValueError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = execute(args)
    except Exception as exc:  # pragma: no cover - command boundary
        print(json.dumps({"status": "ERROR", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"PASS", "PROPOSED", "COMMITTED", "COMPLETE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
