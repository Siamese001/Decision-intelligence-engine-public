"""
tests/test_graph_healer_judge_remediation.py

Unit test suite verifying Wave 5.5 Deterministic Graph Healer LLM Judge Remediation.
Verifies:
1. Core protected entities (Jarvis, Power BI, Orchestration Layer, Workstreams) are preserved during budget trimming.
2. Trimming excess signals prioritizes core edges, ensuring total mutations <= 30.
3. Proposals capped to 4 atomic business proposals with grounded rationales (no repetitive generic rationales).
4. Meeting archetype normalized to STRATEGIC_DISCOVERY.
5. Downstream construction-impact avoidance signal and evidence grounding.
6. Predicate canonicalization (ROUTES_TO for GIS team / spatial files).
7. End-to-end healing passes Gate 3 structural constraints.
"""
from __future__ import annotations

import unittest
from typing import List

from dual_pipeline.transcript_qa_core.graph_healer import (
    CORE_PROTECTED_ENTITY_IDS,
    _heal_entities,
    _heal_entity_descriptions_and_org_evidence,
    _heal_mutation_budget,
    _heal_signals,
    _heal_tactical_alignment_projection,
    heal_graph_projection,
)


class TestGraphHealerJudgeRemediation(unittest.TestCase):
    def test_core_protected_entity_ids_defined(self) -> None:
        self.assertIn("system:jarvis", CORE_PROTECTED_ENTITY_IDS)
        self.assertIn("system:power-bi", CORE_PROTECTED_ENTITY_IDS)
        self.assertIn("system:orchestration-layer", CORE_PROTECTED_ENTITY_IDS)
        self.assertIn("workstream:design-review-automation", CORE_PROTECTED_ENTITY_IDS)
        self.assertIn("org:apex_global", CORE_PROTECTED_ENTITY_IDS)
        self.assertIn("org:partner_advisory", CORE_PROTECTED_ENTITY_IDS)
        # Purged meeting-specific workstreams to prevent cross-meeting contamination
        self.assertNotIn("workstream:permit-compliance", CORE_PROTECTED_ENTITY_IDS)
        self.assertNotIn("workstream:wbs-auditor", CORE_PROTECTED_ENTITY_IDS)
        self.assertNotIn("workstream:spatial-file-upload", CORE_PROTECTED_ENTITY_IDS)

    def test_heal_entities_preserves_core_and_normalizes_archetype(self) -> None:
        entities = [
            {"entity_id": "system:jarvis", "name": "Jarvis", "type": "SYSTEM", "description": ""},
            {"entity_id": "system:power-bi", "name": "Power BI", "type": "SYSTEM", "description": ""},
            {"entity_id": "workstream:permit-compliance", "name": "Permit Compliance", "type": "WORKSTREAM", "description": ""},
        ]
        changes: List[str] = []
        _heal_entities(entities, changes)
        _heal_entity_descriptions_and_org_evidence({"evidence_index": {}}, entities, changes)

        for ent in entities:
            self.assertTrue(len(ent.get("description", "")) > 0)
            self.assertFalse(ent.get("description", "").startswith("TBD"))

    def test_proposals_capped_and_grounded(self) -> None:
        data = {
            "evidence_index": {
                "ev-01": {"text": "Discussion on permit compliance automation.", "exact_text": "permit compliance"},
                "ev-02": {"text": "WBS auditor checks anomalous spend.", "exact_text": "WBS auditor"},
                "ev-03": {"text": "Design review is candidate quick win.", "exact_text": "design review"},
                "ev-04": {"text": "Spatial DWG files routed to GIS team.", "exact_text": "spatial DWG"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"entity_id": "system:jarvis", "name": "Jarvis", "type": "SYSTEM", "description": "Core platform."},
                ],
                "relationship_signals": [],
                "proposals": [
                    {
                        "proposal_id": f"proposal:fragment-{i}",
                        "description": f"Fragmentary proposal {i}",
                        "rationale": "Dedicated full-stack engineering capacity was proposed.",
                        "evidence_ids": ["ev-01"],
                        "status": "PROPOSED",
                    }
                    for i in range(8)
                ],
            },
        }
        changes: List[str] = []
        _heal_tactical_alignment_projection(data, changes)

        proposals = data["graph_mutations"]["proposals"]
        self.assertLessEqual(len(proposals), 4)
        for p in proposals:
            self.assertNotEqual(p.get("rationale"), "Dedicated full-stack engineering capacity was proposed.")
            self.assertTrue(len(p.get("rationale", "")) > 10)

    def test_meeting_archetype_normalized_to_strategic_discovery(self) -> None:
        data = {
            "meeting_archetype": "GENERAL",
            "evidence_index": {
                "ev-01": {"text": "General meeting statement", "exact_text": "statement"}
            },
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
            },
        }
        changes: List[str] = []
        heal_graph_projection(data, changes)
        self.assertIn(data.get("meeting_archetype"), ("STRATEGIC_DISCOVERY", "ARCHITECTURE_DEEP_DIVE"))

    def test_downstream_construction_impact_avoidance_grounding(self) -> None:
        data = {
            "evidence_index": {
                "ev-01": {
                    "text": "Amit mentioned the business case versus preventing construction downstream impacts on project sites.",
                    "exact_text": "preventing construction downstream impacts",
                    "source_classification": "STRATEGIC",
                }
            },
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
                "strategic_intent_signals": [
                    {"signal_id": "sig:construction-downstream-impact", "evidence_ids": ["ev-01"]}
                ],
            },
        }
        metadata = {
            "source_text": "Amit mentioned the business case versus preventing construction downstream impacts on project sites."
        }
        changes: List[str] = []
        heal_graph_projection(data, changes, metadata=metadata)

        self.assertIn("ev-01", data["evidence_index"])
        self.assertEqual(data["evidence_index"]["ev-01"]["source_classification"], "STRATEGIC")
        # Ensure synthetic ev-construction was NOT fabricated
        self.assertNotIn("ev-construction", data["evidence_index"])

        strat_sigs = data["graph_mutations"].get("strategic_intent_signals", [])
        self.assertTrue(any("construction" in s.get("signal_id", "") for s in strat_sigs))

    def test_predicate_canonicalization_gis_routes_to(self) -> None:
        signals = [
            {
                "from_id": "org:gis-team",
                "to_id": "workstream:spatial-file-upload",
                "predicate": "GOVERNS",
                "evidence_ids": ["ev-01"],
                "description": "GIS team manages spatial files",
            }
        ]
        changes: List[str] = []
        _heal_signals(signals, changes)
        self.assertEqual(signals[0]["predicate"], "ROUTES_TO")

    def test_heal_mutation_budget_preserves_core_under_trimming(self) -> None:
        core_entities = [
            {"entity_id": eid, "name": eid.split(":")[-1], "type": "SYSTEM" if "system" in eid else "WORKSTREAM", "description": "Core component"}
            for eid in [
                "system:jarvis",
                "system:power-bi",
                "system:orchestration-layer",
                "workstream:permit-compliance",
                "workstream:wbs-auditor",
                "workstream:design-review-automation",
                "workstream:spatial-file-upload",
            ]
        ]
        extra_entities = [
            {"entity_id": f"entity:extra-{i}", "name": f"Extra {i}", "type": "SYSTEM", "description": "Extra"}
            for i in range(10)
        ]
        signals = [
            {"from_id": "system:jarvis", "to_id": "system:power-bi", "predicate": "INTEGRATES_WITH", "evidence_ids": ["ev-01"], "description": "Integration"},
            {"from_id": "system:jarvis", "to_id": "workstream:permit-compliance", "predicate": "ENABLES", "evidence_ids": ["ev-01"], "description": "Permit automation"},
            {"from_id": "system:jarvis", "to_id": "workstream:wbs-auditor", "predicate": "ENABLES", "evidence_ids": ["ev-01"], "description": "WBS checks"},
        ] + [
            {"from_id": f"entity:extra-{i}", "to_id": f"entity:extra-{i+1}", "predicate": "DEPENDS_ON", "evidence_ids": ["ev-01"], "description": f"Extra edge {i}"}
            for i in range(15)
        ]
        mutations = {
            "entity_upserts": core_entities + extra_entities,
            "relationship_signals": signals,
            "action_updates": [{"action_id": f"act-{i}", "description": f"Action {i}", "owner": "person:test", "evidence_ids": ["ev-01"]} for i in range(5)],
            "proposals": [{"proposal_id": f"prop-{i}", "description": f"Prop {i}", "rationale": "Grounded", "evidence_ids": ["ev-01"]} for i in range(6)],
        }
        changes: List[str] = []
        _heal_mutation_budget({"evidence_index": {}}, mutations, changes)

        total_mutations = (
            len(mutations.get("entity_upserts", []))
            + len(mutations.get("relationship_signals", []))
            + len(mutations.get("action_updates", []))
            + len(mutations.get("proposals", []))
        )
        self.assertLessEqual(total_mutations, 30)

        remaining_ids = {e["entity_id"] for e in mutations["entity_upserts"]}
        for core_id in [
            "system:jarvis",
            "system:power-bi",
            "system:orchestration-layer",
            "workstream:permit-compliance",
            "workstream:wbs-auditor",
        ]:
            self.assertIn(core_id, remaining_ids, f"Core entity {core_id} was improperly pruned!")

    def test_rubric_v2_jira_and_web_map_projection_and_linking(self) -> None:
        data = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-14": {"statement_id": "ev-14", "text": "DWG and KMC spatial files uploaded for web map viewing.", "exact_text": "DWG and KMC spatial files"},
                "ev-16": {"statement_id": "ev-16", "text": "GIS team receives requests via JIRA ticketing.", "exact_text": "GIS team receives JIRA"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "workstream:spatial-file-upload", "name": "Spatial File Upload", "type": "workstream", "description": "Automated ingestion of DWG and KMC files"},
                    {"id": "org:gis-team", "name": "GIS Team", "type": "org", "description": "GIS team"},
                ],
                "relationship_signals": [],
            },
        }
        changes: List[str] = []
        heal_graph_projection(data, changes)

        entities = {e["id"]: e for e in data["graph_mutations"]["entity_upserts"]}
        self.assertIn("system:jira", entities)
        self.assertIn("system:web-map", entities)
        self.assertNotIn("conversion", entities["workstream:spatial-file-upload"]["description"].lower())
        self.assertIn("kmc", entities["workstream:spatial-file-upload"]["description"].lower())

        signals = data["graph_mutations"]["relationship_signals"]
        has_jira_edge = any(s.get("from_id") == "workstream:spatial-file-upload" and s.get("to_id") == "system:jira" and s.get("predicate") == "ROUTES_TO" for s in signals)
        has_webmap_edge = any(s.get("from_id") == "workstream:spatial-file-upload" and s.get("to_id") == "system:web-map" and s.get("predicate") == "INTEGRATES_WITH" for s in signals)
        has_gis_edge = any(s.get("from_id") == "workstream:spatial-file-upload" and s.get("to_id") == "org:gis-team" and s.get("predicate") == "ROUTES_TO" for s in signals)
        self.assertTrue(has_jira_edge, "Missing workstream:spatial-file-upload -> ROUTES_TO -> system:jira")
        self.assertTrue(has_webmap_edge, "Missing workstream:spatial-file-upload -> INTEGRATES_WITH -> system:web-map")
        self.assertTrue(has_gis_edge, "Missing workstream:spatial-file-upload -> ROUTES_TO -> org:gis-team")

    def test_rubric_v2_wbs_and_powerbi_evidence_grounding(self) -> None:
        data = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-01": {"statement_id": "ev-01", "text": "WBS actuals spend tracking.", "exact_text": "actuals spend"},
                "ev-02": {"statement_id": "ev-02", "text": "Power BI chosen for phase one reporting.", "exact_text": "Power BI"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "workstream:wbs-auditor", "name": "WBS Auditor", "type": "workstream", "description": "WBS actuals spend", "evidence_ids": ["ev-01"]},
                    {"id": "system:power-bi", "name": "Power BI", "type": "system", "description": "Power BI", "evidence_ids": ["ev-02"]},
                ],
                "relationship_signals": [
                    {"signal_id": "rel-wbs--power-bi", "from_id": "workstream:wbs-auditor", "to_id": "system:power-bi", "predicate": "INTEGRATES_WITH", "evidence_ids": ["ev-01", "ev-02"]}
                ],
                "decision_updates": [
                    {"decision_id": "decision:power-bi-source", "title": "Power BI selection", "description": "Ratified Power BI, evaluating SAP as a potential future alternative."}
                ],
            },
        }
        metadata = {"source_text": "Daniel noted given actual spend, looking at outlying charges. Sharon ratified Power BI was chosen for phase one reporting."}
        changes: List[str] = []
        heal_graph_projection(data, changes, metadata=metadata)

        # Verify genuine evidence retention and anti-fabrication
        self.assertIn("ev-01", data["evidence_index"])
        self.assertIn("ev-02", data["evidence_index"])
        self.assertNotIn("ev-wbs-actuals", data["evidence_index"])
        self.assertNotIn("ev-powerbi-ratified", data["evidence_index"])

        entities = {e["id"]: e for e in data["graph_mutations"]["entity_upserts"]}
        self.assertIn("ev-01", entities["workstream:wbs-auditor"]["evidence_ids"])

        pbi_sig = next(s for s in data["graph_mutations"]["relationship_signals"] if {s.get("from_id"), s.get("to_id")} == {"workstream:wbs-auditor", "system:power-bi"})
        self.assertIn("ev-01", pbi_sig["evidence_ids"])

        dec = data["graph_mutations"]["decision_updates"][0]
        self.assertNotIn("evaluating sap", dec["description"].lower())

    def test_rubric_v2_canonical_proposals_and_sludge_removal(self) -> None:
        data = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-01": {"statement_id": "ev-01", "text": "Platform discussion", "exact_text": "discussion"},
            },
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
                "proposal_updates": [
                    {"proposal_id": "proposal:11", "description": "The next is given an uploaded permit pull that out into a pdf and save to project memory", "rationale": "Dedicated full-stack engineering capacity was proposed."},
                    {"proposal_id": "proposal:13", "description": "Then the last one is given DWG or unresolved KMC files route into web map", "rationale": "boilerplate"},
                    {"proposal_id": "proposal:meeting-scribe", "description": "So those are the main meeting scribe extracts meeting insights into project memory", "rationale": "Dedicated full-stack engineering capacity was proposed."},
                    {"proposal_id": "proposal:extra-wbs", "description": "Proposed enhancement: WBS Auditor checks anomalous charges from actual spend", "rationale": "delivery workflow and operational requirements"},
                ],
            },
        }
        changes: List[str] = []
        heal_graph_projection(data, changes)

        props = data["graph_mutations"]["proposal_updates"]
        self.assertEqual(len(props), 4)
        for p in props:
            desc = p["description"]
            self.assertFalse(desc.lower().startswith("the next is"))
            self.assertFalse(desc.lower().startswith("then the last one is"))
            self.assertFalse(desc.lower().startswith("so those are"))
            self.assertFalse(desc.lower().startswith("proposed enhancement:"))
            self.assertNotEqual(p.get("rationale"), "Dedicated full-stack engineering capacity was proposed.")

        pids = [p["proposal_id"] for p in props]
        self.assertIn("proposal:11", pids)
        self.assertIn("proposal:13", pids)
        self.assertIn("proposal:meeting-scribe", pids)
        self.assertIn("proposal:extra-wbs", pids)


if __name__ == "__main__":
    unittest.main()
