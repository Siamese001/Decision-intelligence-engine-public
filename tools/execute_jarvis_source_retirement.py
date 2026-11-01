#!/usr/bin/env python3
"""Governed source retirement for contaminated source-9fa8676b5dc6cc11a3b522de."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dual_pipeline.cli.handlers import _backend
from dual_pipeline.release_core.manager import ReleaseManager
from dual_pipeline.retrieval_release import RetrievalReleaseEvaluator
from dual_pipeline.retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
from dual_pipeline.source_retirement_core.constants import _TARGET_KINDS, _TARGET_TABLES
from dual_pipeline.source_retirement_core.plan import build_source_retirement_plan
from dual_pipeline.source_retirement_core.release import (
    prepare_source_retirement_release,
    record_source_retirement_release_events,
)
from dual_pipeline.source_retirement_core.targets import _source_scoped_ids
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from dual_pipeline.transcript_pipeline_core.release_runner import _resolve_model_asset

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def execute_source_retirement() -> dict[str, str]:
    root = Path(".").resolve()
    canon_runtime_root = (root / ".local" / "dual_pipeline").resolve()
    manager = ReleaseManager(canon_runtime_root)
    active = manager.active_pair()
    active_graph = (canon_runtime_root / active["graph_path"]).resolve()

    sid = "source-9fa8676b5dc6cc11a3b522de"
    logger.info("Connecting to active graph: %s", active_graph)
    conn = GovernedStorageContext.connect(active_graph)
    conn.row_factory = Row
    src_row = conn.execute("SELECT * FROM source WHERE source_id=?", (sid,)).fetchone()
    if not src_row:
        raise ValueError(f"Target source {sid} not found in active graph.")
    sv_row = conn.execute("SELECT * FROM source_version WHERE source_id=?", (sid,)).fetchone()
    if not sv_row:
        raise ValueError(f"Target source_version for {sid} not found in active graph.")

    remove = {}
    for kind in _TARGET_KINDS:
        table, id_field, source_field = _TARGET_TABLES[kind]
        remove[kind] = _source_scoped_ids(
            conn,
            table=table,
            id_field=id_field,
            source_field=source_field,
            source_id=sid,
        )
    conn.close()

    logger.info(
        "Targeting removal: %d assertions, %d evidence items",
        len(remove.get("assertion_ids", [])),
        len(remove.get("evidence_ids", [])),
    )

    control = {
        "schema_version": "1.0.0",
        "target_source_id": sid,
        "remove": remove,
        "retain": {k: [] for k in _TARGET_KINDS},
        "related_removals": [],
        "supersession_detachments": [],
        "source_updates": [
            {
                "source_id": sid,
                "expected_source_name": src_row["source_name"],
                "expected_source_posture": src_row["source_posture"],
                "expected_notes": src_row["notes"],
                "source_name": f"{src_row['source_name']} (RETIRED - RCA MISATTRIBUTION)",
                "source_posture": "retired_historical",
                "notes": "Retired due to project_nexus routing misattribution. Replaced by project_jarvis canonical intake.",
            }
        ],
        "source_version_updates": [
            {
                "source_version_id": sv_row["source_version_id"],
                "expected_scope_text": sv_row["scope_text"],
                "expected_posture_text": sv_row["posture_text"],
                "scope_text": "Retired candidate intake.",
                "posture_text": "retired_historical",
            }
        ],
        "reason": "RCA remediation: Retire contaminated source from project_nexus before re-publishing to project_jarvis",
    }

    plan = build_source_retirement_plan(active_graph, active_pair=active, control=control)
    logger.info("Source retirement plan built: %s", plan["plan_id"])

    rev = "5617a9f61b028005a4858fdac845db406aefb181"
    args = argparse.Namespace(
        runtime_root=canon_runtime_root,
        embedding_backend="bge-m3-dev",
        allow_dev_bge_m3=True,
        allow_internal_operational=False,
        internal_operational_policy=None,
        model_path=_resolve_model_asset(f"models/bge-m3/{rev}", "NEE_BGE_M3_MODEL_PATH"),
        model_manifest=_resolve_model_asset(f"models/bge-m3/bge-m3-manifest-{rev}.json", "NEE_BGE_M3_MODEL_MANIFEST"),
        model_revision=rev,
        embedding_device="auto",
        embedding_fp16=None,
    )
    backend = _backend(args)

    intake_summary_path = (
        canon_runtime_root
        / "runs"
        / "run-intake-b93d978b79d08dad509b8b04"
        / "intake_summary.json"
    )
    intake_summary = json.loads(intake_summary_path.read_text())

    receipt, receipt_path = prepare_source_retirement_release(
        manager=manager,
        backend=backend,
        plan=plan,
        run_id=intake_summary["run_id"],
        trace_id=intake_summary["trace_id"],
        source_id=sid,
        source_version_id=sv_row["source_version_id"],
        intake_summary=intake_summary,
        approval_id="Amit Ayer",
    )
    logger.info("Prepared source retirement release receipt: %s", receipt_path)

    suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
        manager.runtime_root, active_graph
    ).resolve(receipt_path)
    evaluator = RetrievalReleaseEvaluator(manager.runtime_root, active_graph)
    evaluation_result = evaluator.evaluate(
        receipt_path,
        Path(suite_resolution["path"]),
        backend=backend,
    )
    eval_status = evaluation_result["evaluation"]["status"]
    logger.info("Retrieval release evaluation status: %s", eval_status)
    if eval_status != "PASS":
        raise RuntimeError(f"Retirement retrieval evaluation failed: {evaluation_result}")

    record_source_retirement_release_events(
        runtime_root=manager.runtime_root,
        receipt=receipt,
    )

    manager.activate(receipt_path)
    new_gen = receipt["next_pair"]["generation_id"]
    logger.info("Successfully activated source retirement generation: %s", new_gen)
    return {
        "status": "PASS",
        "retired_source_id": sid,
        "base_generation_id": active["generation_id"],
        "activated_generation_id": new_gen,
        "receipt_path": str(receipt_path),
    }


if __name__ == "__main__":
    result = execute_source_retirement()
    print(json.dumps(result, indent=2))
