"""End-to-End Tests for Wave 3 Stage 3 Document Extraction, 5-Agent Synthesis, and Bounded Patch.

Exercises the full Stage 3 document extraction pipeline (`DocumentGraphExtractorExecutor.execute`),
5-agent pre-pass synthesis under offline/mock mode, bounded patch repair cycle, and provenance gates.
"""

from __future__ import annotations

import json

from dual_pipeline.document_pipeline_core.bounded_patch import build_bounded_patch_request
from dual_pipeline.document_pipeline_core.executor import (
    DocumentGraphExtractorExecutor,
    validate_attachment_assertion_provenance,
)
from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
    inject_5_agent_intelligence_if_needed,
    reconcile_5_agent_entities,
    run_5_agent_document_intelligence,
)


class _StubSynthesisProvider:
    """Deterministic L2SynthesisProvider stub for E2E Stage 3 synthesis and bounded patch."""

    def __init__(self, initial_response: str, patch_response_fn=None) -> None:
        self._initial_response = initial_response
        self._patch_response_fn = patch_response_fn
        self.calls: list[tuple[str, str]] = []

    def synthesize_graph(self, system_prompt: str, user_content: str) -> str:
        self.calls.append((system_prompt, user_content))
        if self._patch_response_fn is not None and "patch_request" in user_content:
            return self._patch_response_fn(user_content)
        return self._initial_response


def test_stage3_e2e_non_nee_orals_proposal_dynamic_extraction_without_bleed() -> None:
    """E2E: Non-NEE Orals Proposal extracts grounded workstreams/options with zero NEE bleed."""
    doc_text = (
        "## Page 1\n"
        "Northwind Energy Grid Modernization RFP Response and Orals Proposal\n"
        "Proposal Validity: 90 Days | Client References and Representative Team Profiles\n"
        "Workstream 1: Substation Telemetry Ingestion Pipeline\n"
        "Workstream 2: Outage Prediction Feature Store\n"
        "Option 1: Managed Cloud Telemetry Foundation ($890K Fixed Fee)\n"
        "Option 2: Co-Delivery Engineering Pod ($450K Time & Materials)\n"
    )
    stub_json = json.dumps(
        {
            "schema_version": "5.7",
            "is_document": True,
            "output3_only": True,
            "evidence_index": {
                "ev-nw-1": {
                    "evidence_id": "ev-nw-1",
                    "statement_id": "ev-nw-1",
                    "speaker": "Northwind Proposal",
                    "source_classification": "FACT",
                    "kind": "summary",
                    "text": "Northwind Energy Grid Modernization Orals Proposal",
                    "exact_text": "Northwind Energy Grid Modernization Orals Proposal",
                    "start_offset": 10,
                    "end_offset": 60,
                    "quote_pointer": [10, 60],
                    "locator": {"kind": "slide", "value": "p1"},
                    "grounding_status": "GROUNDED",
                }
            },
            "evidence_only_statement_ids": [],
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "org:northwind-energy",
                        "type": "org",
                        "name": "Northwind Energy",
                        "description": "Utility client organization.",
                        "mutation": "CREATE",
                        "epistemic_status": "CONFIRMED",
                        "evidence_ids": ["ev-nw-1"],
                    }
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
                "strategic_intent_signals": [],
            },
        }
    )
    provider = _StubSynthesisProvider(stub_json)
    executor = DocumentGraphExtractorExecutor(provider=provider)
    meta = {"title": "Northwind Orals Proposal.pptx", "page_count": 1}
    out_str = executor.execute(doc_text, pa_context={"metadata": meta})
    out_data = json.loads(out_str)

    entity_ids = {e["id"] for e in out_data["graph_mutations"]["entity_upserts"]}
    proposal_ids = {p["proposal_id"] for p in out_data["graph_mutations"].get("proposal_updates", [])}

    assert "org:apex_global-energy" not in entity_ids
    assert "proposal:opt1-managed-outcomes" not in proposal_ids
    assert any(eid.startswith("workstream:workstream-1") for eid in entity_ids)
    assert any(pid.startswith("proposal:option-1") for pid in proposal_ids)

    validate_attachment_assertion_provenance(out_data, metadata=meta)


def test_stage3_e2e_5_agent_synthesis_and_reconciliation_offline() -> None:
    """E2E: 5-Agent Document Intelligence runs offline, verifies grounding, and reconciles entities."""
    doc_text = (
        "## Page 1\n"
        "Enterprise Delivery Role Charter\n"
        "The Product Owner collaborates closely with the Solution Owner and Scrum Master "
        "to prioritize the Platform Architect backlog across delivery pods. "
        "The Agile Coach supports the Delivery Lead on sprint ceremonies and governance.\n"
    )
    meta = {
        "title": "Enterprise_Role_Charter.pdf",
        "page_count": 1,
        "enable_5_agent_intelligence": True,
        "provider_type": "offline",
    }

    report = run_5_agent_document_intelligence(
        document_text=doc_text,
        document_title=meta["title"],
        provider_type="offline",
    )
    assert report.arbiter_verdict == "PASS"
    assert len(report.entities) >= 3

    inject_5_agent_intelligence_if_needed(doc_text, meta)
    assert "5-AGENT MULTI-ENTITY DECOMPOSITION MANIFEST" in meta.get("prior_context", "")

    projection: dict = {
        "evidence_index": {},
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
        },
    }
    reconcile_5_agent_entities(projection, meta, doc_text)
    assert len(projection["graph_mutations"]["entity_upserts"]) >= 3
    assert len(projection["graph_mutations"]["relationship_signals"]) >= 1

    for ev in projection["evidence_index"].values():
        assert ev.get("grounding_status") == "GROUNDED"
        assert ev.get("evidence_id") == ev.get("statement_id")
        assert ev.get("speaker") == "Enterprise_Role_Charter.pdf"


def test_stage3_e2e_bounded_patch_repairs_object_in_executor() -> None:
    """E2E: Executor executes digest-bound patch repair and validates preservation + Gate 3."""
    doc_text = (
        "## Page 1\n"
        "Meridian Core Platform Architecture\n"
        "The Meridian Billing Service integrates via gRPC API with the Stripe Settlement Gateway."
    )
    initial_candidate = {
        "schema_version": "5.7",
        "is_document": True,
        "output3_only": True,
        "evidence_index": {
            "ev-1": {
                "evidence_id": "ev-1",
                "statement_id": "ev-1",
                "speaker": "Meridian Spec",
                "source_classification": "FACT",
                "kind": "summary",
                "text": "The Meridian Billing Service integrates via gRPC API with the Stripe Settlement Gateway.",
                "exact_text": "The Meridian Billing Service integrates via gRPC API with the Stripe Settlement Gateway.",
                "start_offset": 46,
                "end_offset": 134,
                "quote_pointer": [46, 134],
                "locator": {"kind": "page", "value": "p1"},
                "grounding_status": "GROUNDED",
            }
        },
        "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:meridian-billing",
                    "type": "system",
                    "name": "Meridian Billing Service",
                    "description": "Meridian Billing Service handles core billing.",
                    "mutation": "CREATE",
                    "epistemic_status": "CONFIRMED",
                    "evidence_ids": ["ev-1"],
                },
                {
                    "id": "system:stripe-gateway",
                    "type": "system",
                    "name": "Stripe Settlement Gateway",
                    "description": "Stripe Settlement Gateway processes payments.",
                    "mutation": "CREATE",
                    "epistemic_status": "CONFIRMED",
                    "evidence_ids": ["ev-1"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-meridian-1",
                    "from_id": "system:meridian-billing",
                    "to_id": "system:stripe-gateway",
                    "predicate": "INTEGRATES_WITH",
                    "description": "Meridian Billing Service integrates via gRPC API with Stripe Settlement Gateway.",
                    "mutation": "CREATE",
                    "epistemic_status": "CONFIRMED",
                    "evidence_ids": ["ev-1"],
                }
            ],
            "decision_updates": [],
            "proposal_updates": [],
            "risk_updates": [],
            "strategic_intent_signals": [],
        },
    }
    meta = {"title": "Meridian_Architecture.pdf", "page_count": 1, "is_document": True}
    init_executor = DocumentGraphExtractorExecutor(
        provider=_StubSynthesisProvider(json.dumps(initial_candidate))
    )
    prior_candidate = json.loads(
        init_executor.execute(doc_text, pa_context={"metadata": dict(meta)})
    )
    judge_report = {
        "status": "FAIL",
        "output_3_evaluation": {
            "verdict": "FAIL",
            "failed_claims": [
                {
                    "collection": "relationship_signals",
                    "id": "sig-meridian-1",
                    "reason": "Refine relationship confidence and description on sig-meridian-1",
                }
            ],
        },
    }
    patch_req = build_bounded_patch_request(prior_candidate, judge_report, doc_text)

    def _build_patch_response(user_content: str) -> str:
        payload = json.loads(user_content)
        req = payload["patch_request"]
        allowed_obj = req["allowed_objects"][0]
        updated_val = dict(allowed_obj["value"])
        updated_val["confidence"] = 0.96
        return json.dumps(
            {
                "contract_version": req["contract_version"],
                "candidate_sha256": req["candidate_sha256"],
                "source_sha256": req["source_sha256"],
                "request_sha256": req["request_sha256"],
                "changes": [
                    {
                        "collection": "relationship_signals",
                        "id": "sig-meridian-1",
                        "before_sha256": allowed_obj["before_sha256"],
                        "value": updated_val,
                    }
                ],
            }
        )

    provider = _StubSynthesisProvider("{}", patch_response_fn=_build_patch_response)
    executor = DocumentGraphExtractorExecutor(provider=provider)
    meta = {"title": "Meridian_Architecture.pdf", "page_count": 1, "is_document": True}

    patched_str = executor.execute(
        doc_text,
        pa_context={"metadata": meta},
        prior_graph_candidate=prior_candidate,
        document_patch_request=patch_req,
    )
    patched_data = json.loads(patched_str)
    sig = patched_data["graph_mutations"]["relationship_signals"][0]
    assert sig["signal_id"] == "sig-meridian-1"
    assert sig["confidence"] == 0.96
    validate_attachment_assertion_provenance(patched_data, metadata=meta)
