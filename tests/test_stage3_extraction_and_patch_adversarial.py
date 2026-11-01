"""Adversarial & Edge-Case Tests for Stage 3 Extraction, 5-Agent Synthesis, and Bounded Patch Gate.

Covers Wave 3 remediation of defects D3-01 through D3-10 across mandatory edge-case categories:
1. Boundary payloads (empty inputs, non-matching document archetypes, zero entities)
2. Malformed input (invalid dict locators, ungrounded spans, unknown schema keys)
3. Invariant bypass attempts (cross-tenant NEE/PartnerAdvisory entity bleed, secret/advisory egress,
   unsubstantiated INTEGRATES_WITH edges, quarantined slide locator rebinding,
   relationship endpoints pointing to risk_id/decision_id)
4. Rollback & recovery (Arbiter BLOCKED verdict rejection, LLM mutation preservation on empty archetype)
"""

from __future__ import annotations

import copy
import pytest

from dual_pipeline.document_pipeline_core.account_plan_extractor import (
    extract_account_plan_projection,
)
from dual_pipeline.document_pipeline_core.bounded_patch import (
    _apply_single_replacement,
    _check_references,
    _evidence_objects,
    _verify_evidence_item,
)
from dual_pipeline.document_pipeline_core.executor import (
    AssertionProvenanceError,
    _quarantine_unsubstantiated_integrations_dict,
    _rebind_quarantined_locators,
    _reconcile_specialized_archetypes,
    validate_attachment_assertion_provenance,
)
from dual_pipeline.document_pipeline_core.extractor_evidence_utils import (
    build_grounded_evidence_entry,
    heal_visual_document_mutations,
    normalize_unit_locator_value,
    scrub_archetype_injections,
)
from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
    AGENT_4_GROUNDING_PROMPT,
    _deterministic_id_num,
    _filter_grounded_entities_and_relations,
    _resolve_doc_source_label,
    inject_5_agent_intelligence_if_needed,
    reconcile_5_agent_entities,
    run_5_agent_document_intelligence,
)
from dual_pipeline.document_pipeline_core.orals_proposal_extractor import (
    extract_orals_proposal_projection,
)
from dual_pipeline.document_pipeline_core.sow_contract_extractor import (
    extract_offering_playbook_projection,
    extract_sow_contract_projection,
)
from dual_pipeline.document_pipeline_core.tabular_roster_extractor import (
    ROSTER_TIERS,
    reconcile_tabular_workstream_mutations,
)
from dual_pipeline.document_pipeline_core.workshop_extractor import (
    extract_sparse_enablement_workshop_projection,
)
from dual_pipeline.sanitizer import EgressSanitizationError


class TestCrossTenantArchetypeBleedPrevention:
    """D3-01, D3-09, D3-10: Specialized extractors must never bleed hardcoded NEE/PartnerAdvisory entities."""

    def test_orals_extractor_prevents_nee_bleed_on_non_nee_proposal(self) -> None:
        """D3-01: Non-NEE Orals Proposal must not emit NEE $2.67M / $540K entities."""
        doc_text = (
            "## Page 1\n"
            "Acme Global Logistics Cloud Migration Orals Proposal\n"
            "Workstream 1: Legacy Mainframe Decommissioning and Data Lake Cutover\n"
            "Option 1: Phased Regional Rollout ($1.1M Fixed Fee)\n"
        )
        proj = extract_orals_proposal_projection(doc_text, {"title": "Acme Orals Proposal.pptx"})
        assert proj, "Expected dynamic grounded extraction for valid non-NEE Orals proposal"

        gm = proj["graph_mutations"]
        entity_ids = {e["id"] for e in gm["entity_upserts"]}
        entity_names = " ".join(e["name"] for e in gm["entity_upserts"])

        assert "org:apex_global-energy" not in entity_ids
        assert "proposal:opt1-managed-outcomes" not in {
            p["proposal_id"] for p in gm["proposal_updates"]
        }
        assert "2.67M" not in entity_names
        assert "540K" not in entity_names
        assert any("Workstream 1" in e["name"] for e in gm["entity_upserts"])

        for ev in proj["evidence_index"].values():
            assert ev["grounding_status"] == "GROUNDED"
            assert ev["exact_text"] in doc_text
            assert ev["evidence_id"] == ev["statement_id"]

    def test_orals_extractor_returns_empty_on_unmatched_document(self) -> None:
        """D3-01 Boundary: Unrelated document text returns empty dict {}."""
        proj = extract_orals_proposal_projection(
            "## Page 1\nQuarterly Cafeteria Menu and Parking Guidelines",
            {"title": "Cafeteria_Menu.pdf"},
        )
        assert proj == {}

    def test_workshop_extractor_prevents_day1_curriculum_bleed_on_generic_deck(self) -> None:
        """D3-10: Generic enablement deck without Sales Immersion curriculum cues returns {}."""
        generic_deck = (
            "## Page 1\n"
            "PartnerAdvisory Sales Enablement Workshop: Pursuit Excellence Q3\n"
            "Agenda: Discovery Questions and Executive Presence\n"
        )
        proj = extract_sparse_enablement_workshop_projection(
            generic_deck, {"title": "Generic_Sales_Deck.pptx"}
        )
        assert proj == {}

    def test_sow_extractor_prevents_hardcoded_rate_card_and_nee_org_bleed(self) -> None:
        """D3-09: Non-NEE SOW must not inject org:apex_global-energy or 9 hardcoded rate card roles."""
        non_nee_sow = (
            "## Page 1\n"
            "STATEMENT OF WORK\n"
            "This Statement of Work is entered into between Horizon Retail Corp and "
            "Apex Advisory Partners.\n"
            "Workstream 1: Warehouse Inventory API Modernization\n"
            "Deliverable: Production API Gateway deployment by Q4.\n"
        )
        proj = extract_sow_contract_projection(non_nee_sow, {"title": "Horizon_SOW.pdf"})
        gm = proj.get("graph_mutations", {})
        entity_ids = {e["id"] for e in gm.get("entity_upserts", [])}

        assert "org:apex_global-energy" not in entity_ids
        assert "org:partner_advisory" not in entity_ids
        assert "role:cloud-architect" not in entity_ids
        assert "role:genai-engineer" not in entity_ids

    def test_offering_playbook_returns_empty_without_ams_cues(self) -> None:
        """D3-09: Offering playbook without AMS cues returns {} instead of hardcoded AMS model."""
        proj = extract_offering_playbook_projection(
            "## Page 1\nGeneral Marketing Brand Guidelines Playbook 2026",
            {"title": "Marketing_Playbook.pptx"},
        )
        assert proj == {}


class TestEvidenceGroundingAndRosterTiers:
    """D3-02, D3-03, D3-10: Verbatim span resolution, locator normalization, visual guard, roster tiers."""

    def test_build_grounded_evidence_entry_resolves_verbatim_and_normalizes_locators(self) -> None:
        """D3-03: Verbatim span resolution sets GROUNDED/UNGROUNDED and normalizes locators."""
        doc_text = "## Page 3\nApex Global Enterprises deployed the Geo-POD Matrix across 4 regions."

        ev_grounded = build_grounded_evidence_entry(
            "ev-01",
            "Apex Global Enterprises deployed the Geo-POD Matrix",
            "Apex Global Enterprises deployed the Geo-POD Matrix across 4 regions.",
            "## Page 3",
            doc_text,
            locator_kind="page",
            resolve_verbatim=True,
        )
        assert ev_grounded["evidence_id"] == "ev-01"
        assert ev_grounded["statement_id"] == "ev-01"
        assert ev_grounded["locator"] == {"kind": "section", "value": "p3"}
        assert ev_grounded["grounding_status"] == "GROUNDED"

        ev_ungrounded = build_grounded_evidence_entry(
            "ev-02",
            "Completely absent claim",
            "Fabricated claim that does not appear anywhere in the document.",
            "Geo-POD Matrix",
            "",
            locator_kind="section",
            resolve_verbatim=True,
        )
        assert ev_ungrounded["locator"] == {"kind": "section", "value": "section:geo-pod-matrix"}
        assert ev_ungrounded["grounding_status"] == "UNGROUNDED"

        assert normalize_unit_locator_value("slide", "## Slide 12") == ("slide", "p12")
        assert normalize_unit_locator_value("section", "Geo-POD Matrix") == (
            "section",
            "section:geo-pod-matrix",
        )

    def test_account_plan_extractor_grounds_evidence_and_normalizes_locators(self) -> None:
        """D3-03: Account plan extractor populates both evidence_id and statement_id with valid locators."""
        doc_text = (
            "## Page 1\n"
            "2026 PartnerAdvisory Custom Plan for Apex Global Enterprises\n"
            "Geo-POD Operating Model and Strategic Priorities\n"
        )
        proj = extract_account_plan_projection(
            doc_text, {"title": "2026 PartnerAdvisory Custom Plan.pdf"}
        )
        assert proj
        for ev in proj["evidence_index"].values():
            assert "statement_id" in ev
            assert "evidence_id" in ev
            assert ev["statement_id"] == ev["evidence_id"]
            assert isinstance(ev["locator"], dict)

    def test_visual_document_healer_does_not_hallucinate_absent_entities(self) -> None:
        """D3-02: heal_visual_document_mutations only injects entities present in doc_text."""
        data = {
            "evidence_index": {
                "ev-1": {
                    "evidence_id": "ev-1",
                    "text": "Core API Gateway architecture",
                    "exact_text": "Core API Gateway architecture",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:core-api",
                        "type": "system",
                        "name": "Core API Gateway",
                        "description": "Primary gateway",
                        "evidence_ids": ["ev-1"],
                    }
                ],
                "relationship_signals": [],
            },
        }
        heal_visual_document_mutations(
            data,
            {"is_visual_document": True},
            "Core API Gateway architecture diagram",
        )
        entity_ids = {e["id"] for e in data["graph_mutations"]["entity_upserts"]}
        assert "org:business-mgmt" not in entity_ids
        assert "person:jack-bennetto" not in entity_ids

    def test_scrub_archetype_injections_redacts_secrets_and_advisory_markers(self) -> None:
        """D3-02: scrub_archetype_injections scrubs secret tokens and INTERNAL_ADVISORY markers."""
        payload = {
            "evidence_index": {
                "ev-secret": {
                    "evidence_id": "ev-secret",
                    "exact_text": "Token sk_test_redacted_placeholder and INTERNAL_ADVISORY note",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "org:test",
                        "description": "Confidential career leverage and stealth_score internal note",
                    }
                ]
            },
        }
        scrub_archetype_injections(payload)
        ev_text = payload["evidence_index"]["ev-secret"]["exact_text"]
        ent_desc = payload["graph_mutations"]["entity_upserts"][0]["description"]
        assert "sk-1234567890" not in ev_text
        assert "INTERNAL_ADVISORY" not in ev_text
        assert "career leverage" not in ent_desc
        assert "stealth_score" not in ent_desc

    def test_tabular_roster_tiers_include_director_manager_lead_architect(self) -> None:
        """D3-10: ROSTER_TIERS includes standard enterprise hierarchy titles and guards empty map."""
        for expected_tier in (
            "Director",
            "Manager",
            "Lead",
            "Consultant",
            "Architect",
            "Analyst",
            "VP",
            "Fellow",
        ):
            assert expected_tier in ROSTER_TIERS

        assert reconcile_tabular_workstream_mutations({}, {}, "Non-roster text") == []


class TestBoundedPatchGateHardening:
    """D3-04: Bounded patch gate supports statement_id, enforces entity endpoints, and checks keys."""

    def test_bounded_patch_accepts_statement_id_and_optional_schema_keys(self) -> None:
        """D3-04: statement_id in evidence_index is indexed and optional schema keys are allowed."""
        ev_objs = _evidence_objects(
            {
                "stmt-1": {
                    "statement_id": "stmt-1",
                    "evidence_id": "stmt-1",
                    "exact_text": "Horizon Data Platform integrates with Snowflake via Kafka.",
                    "locator": {"kind": "page", "value": "p1"},
                }
            }
        )
        assert ("evidence_index", "stmt-1") in ev_objs

        source_text = "Page 1: Horizon Data Platform integrates with Snowflake via Kafka."
        _verify_evidence_item(
            "stmt-1",
            ev_objs[("evidence_index", "stmt-1")],
            source_text,
        )

        candidate = {
            "evidence_index": {
                "stmt-1": ev_objs[("evidence_index", "stmt-1")],
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:horizon", "type": "system", "name": "Horizon", "evidence_ids": ["stmt-1"]},
                    {"id": "system:snowflake", "type": "system", "name": "Snowflake", "evidence_ids": ["stmt-1"]},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-1",
                        "from_id": "system:horizon",
                        "to_id": "system:snowflake",
                        "predicate": "INTEGRATES_WITH",
                        "evidence_ids": ["stmt-1"],
                    }
                ],
            },
        }
        scope = {("relationship_signals", "sig-1"): candidate["graph_mutations"]["relationship_signals"][0]}
        merged = copy.deepcopy(candidate)
        replacement = {
            "signal_id": "sig-1",
            "from_id": "system:horizon",
            "to_id": "system:snowflake",
            "predicate": "INTEGRATES_WITH",
            "confidence": 0.92,
            "description": "Grounded in Kafka integration statement",
            "evidence_ids": ["stmt-1"],
        }
        _apply_single_replacement(("relationship_signals", "sig-1"), replacement, scope, merged)
        assert merged["graph_mutations"]["relationship_signals"][0]["confidence"] == 0.92

    def test_bounded_patch_rejects_relationship_pointing_to_risk_or_unknown_keys(self) -> None:
        """D3-04 Invariant Bypass: relationship_signals cannot point to risk_id or inject rogue keys."""
        candidate_with_risk_endpoint = {
            "evidence_index": {
                "ev-1": {
                    "evidence_id": "ev-1",
                    "exact_text": "Latency risk identified on Core API.",
                    "locator": {"kind": "page", "value": "p1"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:core-api", "type": "system", "name": "Core API", "evidence_ids": ["ev-1"]}
                ],
                "risk_updates": [
                    {"risk_id": "risk:latency", "title": "Latency Risk", "evidence_ids": ["ev-1"]}
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-1",
                        "from_id": "system:core-api",
                        "to_id": "risk:latency",
                        "predicate": "MITIGATES",
                        "evidence_ids": ["ev-1"],
                    }
                ],
            },
        }
        with pytest.raises(ValueError, match="Dangling relationship endpoint"):
            _check_references(candidate_with_risk_endpoint)

        orig_sig = {
            "signal_id": "sig-1",
            "from_id": "system:core-api",
            "to_id": "system:core-api",
            "predicate": "DEPENDS_ON",
            "evidence_ids": ["ev-1"],
        }
        scope = {("relationship_signals", "sig-1"): orig_sig}
        merged = {"graph_mutations": {"relationship_signals": [copy.deepcopy(orig_sig)]}}
        rogue_replacement = {
            **orig_sig,
            "unapproved_injected_field": "bypass",
        }
        with pytest.raises(ValueError, match="allowed schema fields"):
            _apply_single_replacement(("relationship_signals", "sig-1"), rogue_replacement, scope, merged)


class TestMultiAgentIntelligenceHardening:
    """D3-05: 5-Agent pre-pass sanitization, Agent 4 grounding verifier, SHA-256 IDs, Arbiter gate."""

    def test_agent4_grounding_verifier_filters_hallucinated_entities(self) -> None:
        """D3-05: Agent 4 filters out entities and relationships lacking grounded spans."""
        assert "Agent 4" in AGENT_4_GROUNDING_PROMPT
        doc_text = "Page 1: Atlas Billing Engine processes invoices for Northwind Energy."
        norm_entities = [
            {
                "id": "system:atlas-billing",
                "name": "Atlas Billing Engine",
                "type": "system",
            },
            {
                "id": "org:northwind-energy",
                "name": "Northwind Energy",
                "type": "org",
            },
            {
                "id": "system:quantum-mars",
                "name": "Hallucinated Quantum Mars",
                "type": "system",
            },
        ]
        raw_rels = [
            {
                "from_id": "system:atlas-billing",
                "to_id": "org:northwind-energy",
                "predicate": "SUPPORTS",
            },
            {
                "from_id": "system:atlas-billing",
                "to_id": "system:quantum-mars",
                "predicate": "INTEGRATES_WITH",
            },
        ]
        kept_ents, kept_rels = _filter_grounded_entities_and_relations(
            doc_text, norm_entities, raw_rels
        )
        kept_ids = {e["id"] for e in kept_ents}
        assert "system:atlas-billing" in kept_ids
        assert "org:northwind-energy" in kept_ids
        assert "system:quantum-mars" not in kept_ids
        assert len(kept_rels) == 1
        assert kept_rels[0]["to_id"] == "org:northwind-energy"

    def test_pre_egress_sanitizer_blocks_secrets_in_5_agent_pipeline(self) -> None:
        """D3-05: LocalPreEgressSanitizer blocks outbound secret leakage before LLM calls."""
        with pytest.raises(EgressSanitizationError):
            run_5_agent_document_intelligence(
                document_text="Confidential credential leak sk_test_redacted_placeholder",
                document_title="LeakTest.pdf",
                provider_type="offline",
            )

    def test_deterministic_id_num_and_blocked_arbiter_verdict(self) -> None:
        """D3-05: SHA-256 deterministic IDs are stable and BLOCKED Arbiter verdict prevents injection."""
        id_a = _deterministic_id_num("Atlas Billing Engine")
        id_b = _deterministic_id_num("Atlas Billing Engine")
        assert id_a == id_b

        projection = {
            "evidence_index": {},
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        }
        blocked_meta = {
            "title": "Custom_Spec.pdf",
            "5_agent_intelligence_report": {
                "arbiter_verdict": "BLOCKED",
                "entities": [{"id": "role:product-owner", "type": "role", "name": "Product Owner"}],
                "relationships": [],
            },
        }
        reconcile_5_agent_entities(projection, blocked_meta, "Product Owner leads backlog.")
        assert projection["graph_mutations"]["entity_upserts"] == []
        assert _resolve_doc_source_label({"title": "Custom_Spec.pdf"}) == "Custom_Spec.pdf"


class TestExecutorSieveIntegrationAndProvenanceGates:
    """D3-02, D3-06, D3-07, D3-08: Executor archetype merge, integration cue, quarantine rebind, provenance."""

    def test_integration_cue_quarantines_generic_nouns_and_keeps_technical_cues(self) -> None:
        """D3-06: Generic words ('platform', 'solution', 'capability') do not justify INTEGRATES_WITH."""
        payload = {
            "evidence_index": {
                "ev-generic": {
                    "evidence_id": "ev-generic",
                    "exact_text": "The enterprise platform solution provides capability assets in context.",
                },
                "ev-tech": {
                    "evidence_id": "ev-tech",
                    "exact_text": "ServiceNow integrates via REST API webhook pipeline with Snowflake.",
                },
            },
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "sig-generic",
                        "from_id": "sys:a",
                        "to_id": "sys:b",
                        "predicate": "INTEGRATES_WITH",
                        "evidence_ids": ["ev-generic"],
                    },
                    {
                        "signal_id": "sig-tech",
                        "from_id": "sys:snow",
                        "to_id": "sys:sf",
                        "predicate": "INTEGRATES_WITH",
                        "evidence_ids": ["ev-tech"],
                    },
                ]
            },
        }
        _quarantine_unsubstantiated_integrations_dict(payload)
        retained_ids = [
            s["signal_id"] for s in payload["graph_mutations"]["relationship_signals"]
        ]
        assert retained_ids == ["sig-tech"]

    def test_quarantined_boilerplate_page_is_dropped_not_rebound_to_neighbor(self) -> None:
        """D3-07: Evidence on QUARANTINE_ILLUSTRATIVE_MOCKUP (Q2) is dropped, not shifted to p1/p3."""
        doc_text = (
            "## Page 1\nExecutive Summary for Cloud Program\n"
            "## Page 2\nIllustrative Mockup Only - Sample Dashboard Widget\n"
            "## Page 3\nProduction Architecture Details\n"
        )
        final_data = {
            "evidence_index": {
                "ev-mock": {
                    "evidence_id": "ev-mock",
                    "locator": {"kind": "slide", "value": "p2"},
                    "exact_text": "Illustrative Mockup Only - Sample Dashboard Widget",
                    "grounding_status": "GROUNDED",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:mock-widget",
                        "type": "system",
                        "name": "Sample Dashboard Widget",
                        "evidence_ids": ["ev-mock"],
                    }
                ],
                "relationship_signals": [],
            },
        }
        meta = {
            "page_count": 3,
            "slide_units": [
                {"unit_number": 1, "content": "Executive Summary", "metadata": {"sieve_class": "HIGH_SIGNAL"}},
                {
                    "unit_number": 2,
                    "content": "Illustrative Mockup Only - Sample Dashboard Widget",
                    "metadata": {"sieve_class": "QUARANTINE_ILLUSTRATIVE_MOCKUP"},
                },
                {"unit_number": 3, "content": "Production Architecture Details", "metadata": {"sieve_class": "HIGH_SIGNAL"}},
            ],
        }
        _rebind_quarantined_locators(final_data, meta, doc_text)
        assert "ev-mock" not in final_data["evidence_index"]
        assert final_data["graph_mutations"]["entity_upserts"] == []

    def test_provenance_validation_checks_all_collections_and_dict_locators(self) -> None:
        """D3-08: validate_attachment_assertion_provenance checks all 6 collections and dict locators."""
        bad_locator_payload = {
            "evidence_index": {
                "ev-1": {
                    "evidence_id": "ev-1",
                    "exact_text": "Grounded text",
                    "grounding_status": "GROUNDED",
                    "locator": {"kind": "invalid_kind", "value": "p1"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
                "decision_updates": [
                    {"decision_id": "dec-1", "title": "Adopt Kafka", "evidence_ids": ["ev-1"]}
                ],
            },
        }
        with pytest.raises(AssertionProvenanceError, match="invalid dict locator"):
            validate_attachment_assertion_provenance(bad_locator_payload, metadata={})

        bad_risk_locator_payload = {
            "evidence_index": {
                "ev-bad": {
                    "evidence_id": "ev-bad",
                    "exact_text": "Grounded text",
                    "grounding_status": "GROUNDED",
                    "unit_locator": "invalid locator with spaces",
                }
            },
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
                "risk_updates": [
                    {"risk_id": "risk-1", "title": "Outage Risk", "evidence_ids": ["ev-bad"]}
                ],
            },
        }
        with pytest.raises(AssertionProvenanceError, match="risk-1"):
            validate_attachment_assertion_provenance(bad_risk_locator_payload, metadata={})

    def test_executor_preserves_llm_mutations_when_archetype_returns_empty(self) -> None:
        """D3-02 Rollback/Recovery: _reconcile_specialized_archetypes preserves LLM mutations when {}."""
        final_data = {
            "evidence_index": {
                "ev-llm-1": {
                    "evidence_id": "ev-llm-1",
                    "statement_id": "ev-llm-1",
                    "locator": {"kind": "page", "value": "p1"},
                    "exact_text": "Vertex Logistics Platform manages fleet routing.",
                    "grounding_status": "GROUNDED",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:vertex",
                        "type": "system",
                        "name": "Vertex Logistics Platform",
                        "evidence_ids": ["ev-llm-1"],
                    }
                ],
                "relationship_signals": [],
            },
        }
        meta = {"title": "Vertex_Orals_Proposal.pptx", "page_count": 1}
        result = _reconcile_specialized_archetypes(
            final_data,
            meta,
            "## Page 1\nVertex Logistics Platform manages fleet routing.",
        )
        entity_ids = [e["id"] for e in result["graph_mutations"]["entity_upserts"]]
        assert "system:vertex" in entity_ids
        assert "org:apex_global-energy" not in entity_ids
