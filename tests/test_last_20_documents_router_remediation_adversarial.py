"""Adversarial regression tests for Wave 4: 20-Document Replay, Budget Guards & Architectural Compliance."""
from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile

from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    _compute_degrees,
    heal_disconnected_document_entities,
)
from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    canonicalize_projection_entities,
)
from dual_pipeline.document_pipeline_core.ingestion_planner import (
    build_document_ingestion_plan,
    persist_document_ingestion_plan,
)
from dual_pipeline.document_pipeline_core.signal_value_router import (
    assess_document_signal_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_adversarial_negative_and_malformed_budget_overrides_clamped() -> None:
    """Category 1 & 2 (Boundary & Malformed): Negative, zero, or non-numeric budget overrides never produce <30 budgets."""
    for bad_budget in (-500, 0, "not-a-number", None, -1):
        meta = {"max_mutation_budget": bad_budget, "title": "HR360 Position Request Form Process Flow"}
        text = "## Page 1\nApex Global Enterprises HR360 Position Request Form integration with SAP SuccessFactors and Tyk API Gateway."
        profile = assess_document_signal_profile(text, meta)
        assert profile.dynamic_mutation_budget >= 30
        plan = build_document_ingestion_plan(text, meta)
        assert plan["recommended_mutation_budget"] >= 30


def test_adversarial_cross_document_duplicate_person_and_org_collapse() -> None:
    """Category 6 & 7 (Idempotent recovery & production regression): Merge LENS and Market Intel variants deterministically."""
    merged_projection = {
        "evidence_index": {"ev-1": {"exact_text": "Eduardo (Ed) Juan More and Next Era Energy partnered with Jose Medina."}},
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:eduardo-ed-juan-more", "name": 'Eduardo "Ed" Juan More', "type": "person", "evidence_ids": ["ev-1"]},
                {"id": "person:eduardo-juan-more", "name": "Eduardo Juan More", "type": "person", "evidence_ids": ["ev-1"]},
                {"id": "person:medina_jose", "name": "medina_jose", "type": "person", "evidence_ids": ["ev-1"]},
                {"id": "org:next era-energy", "name": "Next Era Energy", "type": "org", "evidence_ids": ["ev-1"]},
                {"id": "org:apex_global", "name": "Apex Global", "type": "org", "evidence_ids": ["ev-1"]},
                {"id": "system:dociq", "name": "DocIQ", "type": "system", "evidence_ids": ["ev-1"]},
            ],
            "relationship_signals": [],
        },
    }
    first = canonicalize_projection_entities(copy.deepcopy(merged_projection))
    heal_disconnected_document_entities(first, {"slug": "cross-doc-regression"})
    second = canonicalize_projection_entities(copy.deepcopy(first))
    heal_disconnected_document_entities(second, {"slug": "cross-doc-regression"})

    assert first == second
    ids = [e["id"] for e in second["graph_mutations"]["entity_upserts"]]
    assert ids.count("person:eduardo-juan-more") == 1
    assert ids.count("org:apex_global-energy") == 1
    assert "person:jose-medina" in ids
    assert "person:eduardo-ed-juan-more" not in ids
    assert "org:next era-energy" not in ids

    deg, _ = _compute_degrees(
        second["graph_mutations"]["entity_upserts"],
        second["graph_mutations"]["relationship_signals"],
    )
    assert all(v >= 1 for v in deg.values())


def test_adversarial_path_traversal_slug_sanitized_on_plan_persistence() -> None:
    """Category 5 (Invariant bypass attempt): Path traversal tokens in slug cannot escape sandbox directory."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp_dir:
        sandbox = Path(tmp_dir)
        plan = build_document_ingestion_plan("## Page 1\nTest document", {"title": "Test"})
        out_path = persist_document_ingestion_plan(plan, sandbox, slug="../../etc/passwd")
        assert out_path.parent == sandbox
        assert out_path.exists()


def test_file_budgets_and_layer_imports_compliance() -> None:
    """Verify all modified and newly created document_pipeline_core modules pass file budget and layer import gates."""
    res_layers = subprocess.run(
        [sys.executable, "tools/lint_layer_imports.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_layers.returncode == 0, f"Layer import violation:\n{res_layers.stdout}\n{res_layers.stderr}"

    res_budgets = subprocess.run(
        [sys.executable, "tools/lint_file_budgets.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_budgets.returncode == 0, f"File budget violation:\n{res_budgets.stdout}\n{res_budgets.stderr}"
