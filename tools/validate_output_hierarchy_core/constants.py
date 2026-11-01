"""
Constants, regex patterns, and affinity mappings for output hierarchy governance.
"""
from __future__ import annotations

import re
from typing import Set

from dual_pipeline.parameters import get_known_projects

KNOWN_PROJECTS = get_known_projects()
ALLOWED_ROOT_AUX_DIRS: Set[str] = {"transcript_cache", "graph_scout"}
ALLOWED_ROOT_FILES: Set[str] = {".DS_Store"}
PROJECT_DIR_REGEX = re.compile(r"^[a-z][a-z0-9_]*$")
DATE_FOLDER_REGEX = re.compile(
    r"^(?:0?[1-9]|1[0-2])[-.](?:0?[1-9]|[12]\d|3[01])[-.](?:19|20)\d{2}(?:\s+\([^)]+\))?$"
)
PROHIBITED_EXTENSIONS: Set[str] = {
    ".wav", ".m4a", ".mp3", ".png", ".jpg", ".jpeg", ".docx", ".jsonl",
}

CONTROLLED_SUPPORTING_FILES: Set[str] = {
    "attribution_manifest.json",
    "base_graph_snapshot.json",
    "collateral_drop_log.json",
    "collateral_manifest.json",
    "enterprise_graph_review.json",
    "hardening_recommendations.md",
    "metadata.json",
    "ingestion_metadata.json",
    "inline_chat_attributions.json",
    "speaker_map.json",
    "speaker_hints.json",
    "speaker_hints_queue.json",
    "normalized_claim_ledger.json",
    "source_manifest.json",
    "test_judge_evaluation.json",
    "enterprise_playbook_intake.json",
    "enterprise_playbook_intake.md",
    "hitl_decision_ledger.json",
    "source_document.txt",
    "pipeline_execution_receipt.json",
    "graph_mutation_breakdown.md",
    "governed_delivery_lead_gap_analysis.json",
    "governed_delivery_lead_gap_analysis.md",
    "backtest_comparison_report.md",
    "graph_delta_preview.json",
    "graph_delta_preview.md",
    "backtest_promotion_receipt.json",
    "batch_requests.jsonl",
    "batch_results.jsonl",
    "copilot_answers.jsonl",
    "copilot_attempts.jsonl",
    "speaker_hint_conflicts.jsonl",
    "pending_batches.json",
    "batch_collection_receipt.json",
    "speaker_pattern_clusters.json",
    "publication_coverage_receipt.json",
    "judge_failure_diagnostics.json",
    "mutation_approval_receipt.json",
    "speaker_role_coverage_receipt.json",
    "stage_seals.json",
    "vector_staging.sqlite",
    "ingestion_plan.json",
    "output_1.ckpt.md",
    "output_2.ckpt.md",
    "output_3.ckpt.json",
    "acoustic_evidence_receipt.json",
}

CONTROLLED_SUPPORTING_DIRS: Set[str] = {
    "referenced_artifacts",
    "artifacts",
    "restricted",
    "attempts",
    "Delivery_Lead_Evaluation",
    "proposals",
    "approval_requests",
}

PROHIBITED_CACHE_RECEIPTS: Set[str] = {
    "qa_gates_report.json",
    "llm_judge_evaluation.json",
    "final_release_decision.json",
    "intake_package.json",
}

LEGACY_UNSCOPED_MULTI_MEETING_DATES: Set[str] = set()

BOILERPLATE_SUBFOLDER_PREFIXES = (
    "discuss_",
    "review_",
    "catchup_",
    "catch_up_",
    "project_lens_",
    "project_nexus_",
)

PROJECT_AFFINITY_MAP = {
    "enterprise_architecture": {"enterprise_architecture"},
    "infrastructure_automation": {"infrastructure_automation"},
    "gemini_enablement": {"gemini_enablement"},
    "project_beacon": {"project_beacon"},
    "project_spring": {"project_spring"},
    "project_lens": {"project_lens"},
    "project_jarvis": {"project_jarvis"},
    "project_nexus": {"project_nexus", "project_atlas"},
    "project_atlas": {"project_atlas"},
    "sherpa": {"project_beacon"},
    "regai": {"regai"},
    "apex_trans": {"apex_trans"},
    "lens": {"project_lens"},
    "nexus": {"project_nexus"},
    "nea": {"nea", "project_nea", "project_nexus"},
    "ea": {"enterprise_architecture"},
    "dociq": {"dociq"},
    "jarvis": {"project_jarvis"},
    "conduit": {"conduit"},
    "ec_scheduling": {"ec_scheduling"},
    "hrit": {"hrit"},
    "hr_it": {"hrit"},
    "hr360": {"hrit"},
    "enterprise4u": {"hrit", "apex_trans"},
    "bmo": {"bmo", "apex_trans"},
}


def _enrich_affinity_map_from_registry() -> None:
    import json
    from pathlib import Path

    reg_path = Path(__file__).resolve().parents[2] / "config" / "project_registry.json"
    if not reg_path.exists():
        return
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        for proj_key, meta in (data.get("projects") or {}).items():
            pk = str(proj_key).strip().lower()
            if not pk:
                continue
            PROJECT_AFFINITY_MAP.setdefault(pk, set()).add(pk)
            for alias in (meta.get("aliases") or []) if isinstance(meta, dict) else []:
                ak = str(alias).strip().lower().replace("-", "_").replace(" ", "_")
                if ak:
                    PROJECT_AFFINITY_MAP.setdefault(ak, set()).add(pk)
    except Exception:
        pass


_enrich_affinity_map_from_registry()
