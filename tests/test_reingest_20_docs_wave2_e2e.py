"""
Wave 2 E2E verification for re-sealing all 20 intake packages, QA/judge receipts,
and staging candidate bundles + delta distillation report.
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.core import ROOT
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from tests.test_reingest_20_docs_wave1_e2e import TARGET_20_DOC_DIRS

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_all_20_intake_packages_and_release_decisions_sealed_and_valid() -> None:
    for rel_dir in TARGET_20_DOC_DIRS:
        doc_dir = REPO_ROOT / rel_dir
        qa = json.loads((doc_dir / "qa_gates_report.json").read_text(encoding="utf-8"))
        assert qa.get("passed") is True, f"qa_gates_report failed in {rel_dir}"

        judge = json.loads((doc_dir / "llm_judge_evaluation.json").read_text(encoding="utf-8"))
        assert judge.get("output_3_evaluation", {}).get("verdict") == "PASS"

        final_dec = json.loads((doc_dir / "final_release_decision.json").read_text(encoding="utf-8"))
        assert final_dec.get("status") == "PASS"
        assert final_dec.get("authoritative_write_authorized") is True

        pkg = json.loads((doc_dir / "intake_package.json").read_text(encoding="utf-8"))
        assert pkg.get("contract_id") == "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0"
        assert pkg.get("canonical_digest", {}).get("value")
        ext_proj = pkg.get("extensions", {}).get("com.partner_advisory.nee.canonical-signal-projection", {})
        assert len(ext_proj.get("entity_upserts", [])) > 0
        assert len(ext_proj.get("relationship_signals", [])) > 0


def test_delta_distillation_report_and_staged_candidate_bundles_exist() -> None:
    report_path = REPO_ROOT / "reports" / "reingest_20_docs_delta_distillation_report.json"
    assert report_path.is_file(), "Missing reports/reingest_20_docs_delta_distillation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("documents_count") == 20
    run_ids = report.get("run_ids", [])
    assert len(run_ids) == 20

    rt = resolve_authoritative_runtime_root(ROOT)
    for run_id in run_ids:
        run_dir = rt / "runs" / run_id
        assert (run_dir / "candidate_bundle.json").is_file(), f"Missing candidate_bundle.json for {run_id}"
        assert (run_dir / "intake_summary.json").is_file(), f"Missing intake_summary.json for {run_id}"
