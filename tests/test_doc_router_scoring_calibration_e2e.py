"""End-to-end tests for Wave 1: Router Scoring Calibration & Mandatory Pre-Execution Plan Emission."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
from dual_pipeline.document_pipeline_core.ingestion_planner import build_document_ingestion_plan
from dual_pipeline.document_pipeline_core.signal_value_router import (
    SignalTier,
    assess_document_signal_profile,
)


class _DummyProvider:
    def synthesize_graph(self, system_prompt: str, user_content: str) -> str:
        return json.dumps({
            "schema_version": "5.7",
            "is_document": True,
            "output3_only": True,
            "evidence_index": {
                "ev-1": {
                    "evidence_id": "ev-1",
                    "text": "Apex Global Analytics DocIQ pipeline architecture integrates with Documentum, Maximo, and WIT Classifier.",
                    "exact_text": "Apex Global Analytics DocIQ pipeline architecture integrates with Documentum, Maximo, and WIT Classifier.",
                    "locator": {"kind": "slide", "value": "p1"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:dociq", "type": "system", "name": "DocIQ", "mutation": "CREATE", "evidence_ids": ["ev-1"]},
                    {"id": "system:documentum", "type": "system", "name": "Documentum", "mutation": "CREATE", "evidence_ids": ["ev-1"]},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-1",
                        "from_id": "system:dociq",
                        "to_id": "system:documentum",
                        "predicate": "INTEGRATES_WITH",
                        "relationship": "INTEGRATES_WITH",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["ev-1"],
                    }
                ],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
            },
        })


def test_role_framework_catalog_scores_high_signal_authoritative() -> None:
    text = (
        "## Page 1\n"
        "me@partner_advisory: Career Framework Role Profiles\n"
        "Understanding Role Profile Components and Relationship to Other Roles.\n"
        "## Page 95\n"
        "Scrum Master\n"
        "Facilitates Scrum ceremonies and removes team impediments.\n"
        "## Page 197\n"
        "Solution Owner\n"
        "Lead-from-the-front technology and agile delivery expert under Product Engineering Methodology (PEM).\n"
    )
    profile = assess_document_signal_profile(text, {})
    assert profile.archetype == "ROLE_FRAMEWORK_CATALOG"
    assert profile.signal_tier == SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value
    assert profile.strategic_value_score >= 0.55
    assert profile.dynamic_mutation_budget >= 90

    plan = build_document_ingestion_plan(text, {})
    assert plan["document_archetype"] == "ROLE_FRAMEWORK_CATALOG"
    assert plan["signal_tier"] == SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value
    assert plan["recommended_mutation_budget"] >= 90


def test_technical_architecture_specs_score_above_workshop_clamp() -> None:
    hr360_text = (
        "## Page 1\n"
        "HR360 Position Request Form Process Flow and Architecture.\n"
        "Apex Global Enterprises and PartnerAdvisory scoping for HR360 integration with Tyk Gateway, "
        "SuccessFactors Employee Central, SuccessFactors Recruiting, and IAS authentication.\n"
    )
    prof_hr360 = assess_document_signal_profile(hr360_text, {})
    assert prof_hr360.signal_tier in (
        SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value,
        SignalTier.MEDIUM_SIGNAL_OPERATIONAL.value,
    )
    assert prof_hr360.strategic_value_score >= 0.55
    assert prof_hr360.dynamic_mutation_budget >= 90


def test_reconstructs_text_from_slide_units_and_persists_00_plan() -> None:
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
        out_dir = Path(tmp)
        metadata = {
            "slug": "Dociq_Update_Jack_PartnerAdvisory",
            "output_dir": str(out_dir),
            "slide_units": [
                {
                    "unit_number": 1,
                    "content": "Apex Global Analytics DocIQ pipeline architecture integrates with Documentum, Maximo, and WIT Classifier.",
                },
                {
                    "unit_number": 2,
                    "content": "DocIQ write-back API and backfill pipeline performance metrics for PartnerAdvisory and Apex Global.",
                },
            ],
        }
        executor = DocumentGraphExtractorExecutor(_DummyProvider())
        result_json = executor.execute("", pa_context={"metadata": metadata})
        assert result_json
        assert metadata["document_signal_profile"]["strategic_value_score"] >= 0.55
        assert metadata["document_signal_profile"]["dynamic_mutation_budget"] >= 90
        plan_files = list(out_dir.glob("00_*_ingestion_plan.json"))
        assert len(plan_files) == 1
        plan_data = json.loads(plan_files[0].read_text(encoding="utf-8"))
        assert plan_data["planned_before_execution"] is True
        assert plan_data["recommended_mutation_budget"] >= 90
        assert plan_data["total_pages_detected"] == 2
