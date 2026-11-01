#!/usr/bin/env python3
"""
tools/verify_retriever_partitioning.py

Verification tool for Wave 4: Query Traversal & Offline Context Retriever Partitioning.
Verifies workstream-partitioned offline context extraction, Markdown fencing, and test suite passage.
Produces reports/wave4_retriever_partitioning_receipt.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.query_core.offline_retriever import OfflineGraphContextRetriever


def verify_wave4() -> dict:
    graph_path = REPO_ROOT / "graph" / "nee_graph.sqlite"
    assert graph_path.is_file(), f"Database not found at {graph_path}"

    retriever = OfflineGraphContextRetriever(default_graph_path=graph_path)

    # 1. Project LENS retrieval check
    lens_pkt = retriever.retrieve_meeting_prior_context(project_name="project_lens")
    lens_entity_ids = [e.node_id for e in lens_pkt.entities]
    lens_checks = {
        "jen_pan_present": "person_jen_pan" in lens_entity_ids,
        "alex_sanchez_present": "person_alex_sanchez" in lens_entity_ids,
        "zero_sherpa_entities": not any("sherpa" in eid.lower() or eid in ("person_clay_palumbo", "person_lillian") for eid in lens_entity_ids),
        "zero_sherpa_assertions": not any("sherpa" in (a.assertion_id + str(a.subject_node_id) + str(a.object_node_id)).lower() for a in (lens_pkt.decisions + lens_pkt.risks)),
        "lens_markdown_header_present": "#### Project LENS (org:nea)" in lens_pkt.formatted_prior_context,
        "sherpa_markdown_header_absent": "#### Project Beacon" not in lens_pkt.formatted_prior_context,
    }

    # 2. Project Beacon retrieval check
    sherpa_pkt = retriever.retrieve_meeting_prior_context(project_name="project_beacon")
    sherpa_entity_ids = [e.node_id for e in sherpa_pkt.entities]
    sherpa_checks = {
        "clay_palumbo_present": "person_clay_palumbo" in sherpa_entity_ids,
        "lillian_present": "person_lillian" in sherpa_entity_ids,
        "zero_jen_pan_entity": "person_jen_pan" not in sherpa_entity_ids,
        "zero_lens_entity": "workstream_lens" not in sherpa_entity_ids,
        "zero_jen_pan_assertions": not any("jen_pan" in (a.assertion_id + str(a.subject_node_id) + str(a.object_node_id)).lower() for a in (sherpa_pkt.decisions + sherpa_pkt.risks)),
        "sherpa_markdown_header_present": "#### Project Beacon (org:nee_it)" in sherpa_pkt.formatted_prior_context,
        "lens_markdown_header_absent": "#### Project LENS" not in sherpa_pkt.formatted_prior_context,
    }

    # 3. Associated workstreams checks
    jen_ent = next((e for e in lens_pkt.entities if e.node_id == "person_jen_pan"), None)
    clay_ent = next((e for e in sherpa_pkt.entities if e.node_id == "person_clay_palumbo"), None)
    annotation_checks = {
        "jen_associated_with_lens": jen_ent is not None and "workstream_lens" in jen_ent.associated_workstreams,
        "clay_associated_with_sherpa": clay_ent is not None and "workstream_project_beacon" in clay_ent.associated_workstreams,
    }

    # 4. Run pytest suite
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(REPO_ROOT / "tests" / "test_offline_retriever_attribution.py"),
        "-v",
        "--timeout=60",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), env=env)
    pytest_passed = (res.returncode == 0)

    checks = {
        "lens_checks": lens_checks,
        "sherpa_checks": sherpa_checks,
        "annotation_checks": annotation_checks,
        "pytest_suite_passed": pytest_passed,
    }

    all_passed = (
        all(lens_checks.values())
        and all(sherpa_checks.values())
        and all(annotation_checks.values())
        and pytest_passed
    )

    receipt = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "wave": "Wave 4: Query Traversal & Offline Context Retriever Partitioning",
        "database_path": str(graph_path),
        "checks": checks,
        "pytest_output_summary": res.stdout.splitlines()[-1] if res.stdout else "",
        "pass": all_passed,
    }

    receipt_path = REPO_ROOT / "reports" / "wave4_retriever_partitioning_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"Wave 4 receipt written to {receipt_path}")
    print(f"Overall status: {'PASS' if all_passed else 'FAIL'}")
    return receipt


if __name__ == "__main__":
    receipt = verify_wave4()
    if not receipt["pass"]:
        sys.exit(1)
