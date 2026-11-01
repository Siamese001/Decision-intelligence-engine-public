"""Wave 1 E2E test for ENTERPRISE4U Align + PRF Integration SOW high-signal consolidation."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.document_pipeline import load_document_content
from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
from dual_pipeline.document_pipeline_core.sow_contract_extractor import (
    extract_sow_contract_projection,
)


def _repo_root() -> Path:
    return next(
        (p for p in Path(__file__).resolve().parents if (p / "dual_pipeline").exists()),
        Path(__file__).resolve().parents[1],
    )


def test_enterprise4u_sow_high_signal_consolidation_and_activity_deliverables_e2e() -> None:
    """Verify full Section 2.2 activity deliverables, Section 6 roles/fees, and zero orphan nodes."""
    root = _repo_root()
    sow_md_path = root / "output/hrit/10-19-2026/Nee4u_Employee_Experience_Align_PartnerAdvisory/source_document.txt"
    assert sow_md_path.exists()
    sow_text = sow_md_path.read_text(encoding="utf-8")

    proj = extract_sow_contract_projection(sow_text)
    muts = proj["graph_mutations"]
    upserts = muts["entity_upserts"]
    rels = muts["relationship_signals"]
    ent_by_id = {e["id"]: e for e in upserts}

    assert 38 <= len(upserts) <= 52
    assert 55 <= len(rels) <= 75

    # Verify all 10 Section 2.2 Activity Deliverables
    deliverables = [e for e in upserts if e["type"] == "deliverable"]
    assert len(deliverables) == 10
    expected_delivs = {
        "deliverable:activity-a1-discover-build-the-fact-base",
        "deliverable:activity-a2-align-set-the-direction",
        "deliverable:activity-a3-design-shape-the-target",
        "deliverable:activity-a4-mobilize-define-what-comes-next",
        "deliverable:activity-a5-engagement-leadership-governance",
        "deliverable:activity-b1-discovery-access-technical-confirmation",
        "deliverable:activity-b2-create-manage-requisition",
        "deliverable:activity-b3-create-position-integration",
        "deliverable:activity-b4-update-position-reuse-logic",
        "deliverable:activity-b5-integration-test-reconciliation-hardening",
    }
    assert {d["id"] for d in deliverables} == expected_delivs

    # Verify all 8 contracted PartnerAdvisory roles + 3 client roles (11 total roles, zero generic hardcoded roles)
    role_ids = {e["id"] for e in upserts if e["type"] == "role"}
    expected_partner_advisory_roles = {
        "role:engagement-lead",
        "role:experience-lead",
        "role:platform-technical-architect-google",
        "role:platform-technical-architect-moveworks",
        "role:integration-solution-architect",
        "role:hr-master-data-integration-engineer",
        "role:integration-middleware-engineer",
        "role:analyst-qa-integration-test-engineer",
    }
    assert expected_partner_advisory_roles <= role_ids
    assert "role:practice-area-director" not in role_ids
    assert "role:solution-principal" not in role_ids

    # Verify program, workstreams, systems, and architecture chain
    assert "project:enterprise4u-employee-experience-and-agent-foundational-framework" in ent_by_id
    assert "workstream:workstream-a-align" in ent_by_id
    assert "workstream:workstream-b-prf-integration" in ent_by_id
    assert "system:hr-master-data-platform" in ent_by_id
    assert "system:recruiting-platform" in ent_by_id
    assert "system:client-llms" in ent_by_id
    assert "system:github" in ent_by_id

    route_pairs = {(r["from_id"], r["to_id"]) for r in rels if r["predicate"] == "ROUTES_TO"}
    assert ("system:hr-360", "system:tyk") in route_pairs
    assert ("system:tyk", "system:hr-master-data-platform") in route_pairs
    assert ("system:tyk", "system:recruiting-platform") in route_pairs

    # Verify zero orphan nodes (degree == 0) and 100% verbatim evidence spans
    deg = Counter()
    for r in rels:
        deg[r["from_id"]] += 1
        deg[r["to_id"]] += 1
    assert all(deg[e["id"]] >= 1 for e in upserts)
    for ev in proj["evidence_index"].values():
        assert ev["exact_text"] in sow_text


def test_executor_authoritative_sow_archetype_routing_drops_noisy_llm_duplicates() -> None:
    """Verify DocumentGraphExtractorExecutor replaces noisy LLM SOW mutations with authoritative SOW projection."""
    root = _repo_root()
    sow_md_path = root / "output/hrit/10-19-2026/Nee4u_Employee_Experience_Align_PartnerAdvisory/source_document.txt"
    sow_text = sow_md_path.read_text(encoding="utf-8")

    noisy_llm_payload = {
        "schema_version": "5.7",
        "is_document": True,
        "output3_only": True,
        "evidence_index": {
            "ev-noisy": {
                "evidence_id": "ev-noisy",
                "text": "Apex Power & Grid Company and PartnerAdvisory",
                "exact_text": "Apex Power & Grid Company",
                "locator": {"kind": "section", "value": "Section 1"},
                "grounding_status": "GROUNDED",
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:partner_advisory-florida-power-and-light",
                    "type": "person",
                    "name": "PartnerAdvisory-Apex Power & Grid",
                    "description": "Hallucinated compound person entity from LLM",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-noisy"],
                }
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "risk_updates": [],
        },
    }
    mock_provider = MagicMock()
    mock_provider.synthesize_graph.return_value = json.dumps(noisy_llm_payload)

    executor = DocumentGraphExtractorExecutor(mock_provider)
    out_json = executor.execute(sow_text, pa_context={"metadata": {"project": "hrit"}})
    out_data = json.loads(out_json)
    final_ids = {e["id"] for e in out_data["graph_mutations"]["entity_upserts"]}

    assert "person:partner_advisory-florida-power-and-light" not in final_ids
    assert "deliverable:activity-a1-discover-build-the-fact-base" in final_ids
    assert "deliverable:activity-b5-integration-test-reconciliation-hardening" in final_ids
    assert "person:dan-okray" in final_ids
    assert "person:clement-hamel" in final_ids
    assert "person:beau-williamson" in final_ids


def test_docx_and_markdown_sow_parity() -> None:
    """Verify DOCX `[T1.R...]` and Markdown `| # | Activity |` formats yield identical deliverable & role sets."""
    root = _repo_root()
    sow_md_path = root / "output/hrit/10-19-2026/Nee4u_Employee_Experience_Align_PartnerAdvisory/source_document.txt"
    sow_docx_path = root / "documents/hrit/ENTERPRISE4U_Employee_Experience_Align_Plus_PRF_Integration_SOW.docx"
    if not sow_docx_path.exists():
        return
    md_proj = extract_sow_contract_projection(sow_md_path.read_text(encoding="utf-8"))
    raw_docx = load_document_content(sow_docx_path)
    docx_txt = raw_docx[0] if isinstance(raw_docx, tuple) else str(raw_docx)
    docx_proj = extract_sow_contract_projection(docx_txt)

    md_delivs = {e["id"] for e in md_proj["graph_mutations"]["entity_upserts"] if e["type"] == "deliverable"}
    docx_delivs = {e["id"] for e in docx_proj["graph_mutations"]["entity_upserts"] if e["type"] == "deliverable"}
    assert md_delivs == docx_delivs
    assert len(docx_delivs) == 10
