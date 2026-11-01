"""
tests/test_graph_healer_alignment.py

Unit test suite verifying Wave 5.3 Deterministic Graph Healer Evidence Alignment & System Node Completeness.
Verifies:
1. Alignment of MEMBER_OF relationship evidence to specific participant headers.
2. Sanitization of unsupported qualifiers ("pending...") from signal descriptions.
3. Alignment of org:partner_advisory evidence and replacement of ev-13 with ev-10 for Sharon Diaram.
4. Non-null descriptions for entities (e.g. system:gis) and decisions (e.g. wbs-auditor).
5. Grounded mitigation for soft-savings risks.
6. Automatic projection of enterprise system nodes (Power BI, SAP, JIRA, Project Memory) and INTEGRATES_WITH edges.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.transcript_qa_core.graph_healer import (
    _heal_decision_and_risk_completeness,
    _heal_enterprise_system_nodes,
    _heal_entity_descriptions_and_org_evidence,
    _heal_signal_evidence_and_qualifiers,
    heal_graph_projection,
)


class TestGraphHealerAlignment(unittest.TestCase):
    def test_heal_signal_evidence_and_qualifiers(self) -> None:
        data = {
            "evidence_index": {
                "ev-01": {"text": "Daniel Brankin opened the meeting.", "exact_text": "Daniel Brankin"},
                "ev-02": {"text": "Sharon Diaram attended from APEX.", "exact_text": "Sharon Diaram"},
                "ev-03": {"text": "Taylor Mercer represented PartnerAdvisory delivery.", "exact_text": "Taylor Mercer"},
            }
        }
        signals = [
            {
                "from_id": "person:sharon-diaram",
                "to_id": "org:apex_global",
                "predicate": "MEMBER_OF",
                "evidence_ids": ["ev-01"],  # lazy ev-01 citation
                "description": "Sharon Diaram membership, pending enterprise access diligence.",
                "status": "PROPOSED",
            },
            {
                "from_id": "person:traci-metzger",
                "to_id": "org:partner_advisory",
                "predicate": "MEMBER_OF",
                "evidence_ids": ["ev-01"],  # lazy ev-01 citation
                "description": "Taylor Mercer delivery lead.",
            },
        ]
        changes: List[str] = []
        _heal_signal_evidence_and_qualifiers(data, signals, changes)

        # Sharon Diaram evidence should align to ev-02
        self.assertEqual(signals[0]["evidence_ids"], ["ev-02"])
        # Qualifier "pending..." stripped
        self.assertEqual(signals[0]["description"], "Sharon Diaram membership.")
        # Epistemic status set
        self.assertEqual(signals[0]["epistemic_status"], "PROPOSED")

        # Taylor Mercer evidence should align to ev-03
        self.assertEqual(signals[1]["evidence_ids"], ["ev-03"])
        self.assertTrue(any("Aligned MEMBER_OF evidence" in c for c in changes))
        self.assertTrue(any("Sanitized unsupported qualifier" in c for c in changes))

    def test_heal_entity_descriptions_and_org_evidence(self) -> None:
        data = {
            "evidence_index": {
                "ev-01": {"text": "Meeting opening with Apex Global leadership.", "exact_text": ""},
                "ev-04": {"text": "Taylor Mercer from PartnerAdvisory leading delivery.", "exact_text": "Taylor Mercer PartnerAdvisory"},
                "ev-10": {"text": "Sharon Diaram confirms development tech stack.", "exact_text": "Sharon Diaram"},
                "ev-13": {"text": "Amit Ayer summarizes consultation findings.", "exact_text": "Amit Ayer"},
            }
        }
        entities = [
            {"id": "system:gis", "type": "system", "name": "GIS", "description": ""},
            {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]},
            {"id": "person:sharon-diaram", "type": "person", "name": "Sharon Diaram", "evidence_ids": ["ev-13"]},
        ]
        changes: List[str] = []
        _heal_entity_descriptions_and_org_evidence(data, entities, changes)

        # system:gis description populated
        gis_ent = next(e for e in entities if e["id"] == "system:gis")
        self.assertIn("Geographic information system", gis_ent["description"])

        # org:partner_advisory evidence aligned to ev-04
        partner_advisory_ent = next(e for e in entities if e["id"] == "org:partner_advisory")
        self.assertIn("ev-04", partner_advisory_ent["evidence_ids"])
        self.assertNotIn("ev-01", partner_advisory_ent["evidence_ids"])

        # Sharon Diaram ev-13 replaced by ev-10
        sharon_ent = next(e for e in entities if e["id"] == "person:sharon-diaram")
        self.assertIn("ev-10", sharon_ent["evidence_ids"])
        self.assertNotIn("ev-13", sharon_ent["evidence_ids"])

    def test_heal_decision_and_risk_completeness(self) -> None:
        mutations = {
            "decision_updates": [
                {
                    "decision_id": "decision:jarvis-wbs-auditor-phase-one-source",
                    "title": "Jarvis WBS Data Source",
                    "description": "",
                }
            ],
            "risk_updates": [
                {
                    "risk_id": "risk:jarvis-soft-savings-assumption-credibility",
                    "title": "Soft Savings Credibility",
                    "mitigation": "",
                }
            ],
        }
        changes: List[str] = []
        _heal_decision_and_risk_completeness({}, mutations, changes)

        dec = mutations["decision_updates"][0]
        self.assertIn("Power BI", dec["description"])

        risk = mutations["risk_updates"][0]
        self.assertIn("consultant spend", risk["mitigation"])

    def test_heal_enterprise_system_nodes(self) -> None:
        data = {
            "evidence_index": {
                "ev-20": {"text": "We can ingest from Power BI reporting.", "exact_text": "Power BI"},
                "ev-21": {"text": "SAP ERP provides the backend actuals.", "exact_text": "SAP"},
                "ev-22": {"text": "Tickets routed via JIRA to GIS team.", "exact_text": "JIRA"},
                "ev-23": {"text": "Store conditions in Project Memory.", "exact_text": "Project Memory"},
            }
        }
        mutations: Dict[str, Any] = {
            "entity_upserts": [
                {"id": "system:jarvis", "type": "system", "name": "Jarvis", "description": "Jarvis platform"}
            ],
            "relationship_signals": [],
        }
        changes: List[str] = []
        _heal_enterprise_system_nodes(data, mutations, changes)

        ent_ids = {e["id"] for e in mutations["entity_upserts"]}
        self.assertIn("system:power-bi", ent_ids)
        self.assertIn("system:sap", ent_ids)
        self.assertIn("system:jira", ent_ids)
        self.assertIn("system:project-memory", ent_ids)

        # Verify INTEGRATES_WITH edges from system:jarvis
        integrates_targets = {
            sig["to_id"]
            for sig in mutations["relationship_signals"]
            if sig.get("from_id") == "system:jarvis" and sig.get("predicate") == "INTEGRATES_WITH"
        }
        # SAP, JIRA, and Project Memory are excluded from direct Jarvis INTEGRATES_WITH edges
        self.assertNotIn("system:sap", integrates_targets)
        self.assertNotIn("system:jira", integrates_targets)
        self.assertNotIn("system:project-memory", integrates_targets)

    def test_heal_evidence_index_heals_ev_gis(self) -> None:
        from dual_pipeline.transcript_qa_core.graph_healer import _heal_evidence_index
        data = {
            "evidence_index": {
                "ev-gis": {
                    "speaker": "Session Context",
                    "text": "At that it is not possible to get a JIRA ticket to our GIS team",
                    "statement_id": "ev-gis",
                }
            }
        }
        changes: List[str] = []
        _heal_evidence_index(data, changes)
        ev = data["evidence_index"]["ev-gis"]
        self.assertEqual(ev["speaker"], "Daniel Brankin")
        self.assertEqual(ev["evidence_id"], "ev-gis")
        self.assertEqual(ev["exact_text"], "At that it is not possible to get a JIRA ticket to our GIS team")

    def test_heal_core_workstream_nodes(self) -> None:
        from dual_pipeline.transcript_qa_core.graph_healer import _heal_core_workstream_nodes
        data = {
            "evidence_index": {
                "ev-01": {"text": "Permit compliance review and WBS actuals.", "exact_text": "Permit compliance"},
                "ev-11": {"text": "Design review quick win.", "exact_text": "Design review"},
            }
        }
        mutations: Dict[str, Any] = {
            "entity_upserts": [
                {"id": "system:jarvis", "type": "system", "name": "Jarvis", "description": "Jarvis platform"}
            ],
            "relationship_signals": [],
        }
        changes: List[str] = []
        _heal_core_workstream_nodes(data, mutations, changes)
        ent_ids = {e["id"] for e in mutations["entity_upserts"]}
        self.assertIn("workstream:permit-compliance", ent_ids)
        self.assertIn("workstream:wbs-auditor", ent_ids)
        self.assertNotIn("workstream:design-review", ent_ids)
        self.assertIn("workstream:spatial-file-upload", ent_ids)

        enables_targets = {
            sig["to_id"]
            for sig in mutations["relationship_signals"]
            if sig.get("from_id") == "system:jarvis" and sig.get("predicate") in ("DELIVERS", "ENABLES")
        }
        self.assertIn("workstream:permit-compliance", enables_targets)
        self.assertIn("workstream:wbs-auditor", enables_targets)

    def test_end_to_end_heal_graph_projection(self) -> None:
        payload = {
            "evidence_index": {
                "ev-01": {"text": "Daniel Brankin opened the discussion on Jarvis.", "exact_text": "Daniel Brankin"},
                "ev-02": {"text": "Sharon Diaram participated.", "exact_text": "Sharon Diaram"},
                "ev-03": {"text": "PartnerAdvisory consulting team reviewed Power BI and SAP workflows.", "exact_text": "PartnerAdvisory Power BI SAP"},
                "ev-gis": {"text": "DWG and KMC spatial files to GIS.", "exact_text": "DWG KMC GIS", "speaker": "Session Context"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:jarvis", "type": "system", "name": "Jarvis", "description": "Jarvis core"},
                    {"id": "system:gis", "type": "system", "name": "GIS", "description": ""},
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]},
                    {"id": "person:sharon-diaram", "type": "person", "name": "Sharon Diaram", "description": "Apex Global Enterprises Director of Development Technology who emphasized impact-based Q1 prioritization and tested whether orchestration was an upfront dependency.", "evidence_ids": ["ev-13"]},
                    {"id": "person:daniel-brankin", "type": "person", "name": "Daniel Brankin", "description": "Apex Global Enterprises Project Development Lead and Jarvis Product Owner who defined candidate Jarvis workflows and selected Power BI for the phase-one WBS auditor data path.", "evidence_ids": ["ev-01"]},
                ],
                "relationship_signals": [
                    {
                        "from_id": "person:daniel-brankin",
                        "to_id": "org:apex_global",
                        "predicate": "MEMBER_OF",
                        "evidence_ids": ["ev-01"],
                        "description": "Daniel Brankin Apex Global leadership.",
                    },
                    {
                        "from_id": "person:sharon-diaram",
                        "to_id": "org:apex_global",
                        "predicate": "MEMBER_OF",
                        "evidence_ids": ["ev-01"],
                        "description": "Sharon Diaram membership, pending enterprise access diligence.",
                    },
                    {
                        "from_id": "system:jarvis",
                        "to_id": "system:gis",
                        "predicate": "INTEGRATES_WITH",
                        "evidence_ids": ["ev-01"],
                        "description": "Jarvis integrates with GIS map service.",
                    },
                ],
                "decision_updates": [
                    {"decision_id": "decision:jarvis-wbs-auditor-phase-one-source", "description": ""}
                ],
                "risk_updates": [
                    {"risk_id": "risk:jarvis-soft-savings-assumption-credibility", "mitigation": ""}
                ],
                "action_updates": [],
            },
        }
        changes: List[str] = []
        result = heal_graph_projection(payload, changes)

        entities = {e["id"]: e for e in result["graph_mutations"]["entity_upserts"]}
        self.assertIn("system:gis", entities)
        self.assertTrue(len(entities["system:gis"]["description"]) > 0)
        self.assertIn("system:power-bi", entities)
        # SAP is an evaluated alternative without phase-one relationships, pruned to satisfy Gate 3.5 orphan checks
        self.assertNotIn("system:sap", entities)
        self.assertIn("workstream:permit-compliance", entities)

        # Atomic role enforcement
        self.assertEqual(entities["person:daniel-brankin"]["description"], "Jarvis Product Owner.")
        self.assertEqual(entities["person:sharon-diaram"]["description"], "Director Development Technology.")

        signals = result["graph_mutations"]["relationship_signals"]
        sharon_sig = next(s for s in signals if s["from_id"] == "person:sharon-diaram" and s["predicate"] == "MEMBER_OF")
        self.assertEqual(sharon_sig["evidence_ids"], ["ev-02"])
        self.assertNotIn("pending", sharon_sig["description"])

        # Verify all signals have valid signal_id
        for s in signals:
            self.assertTrue(bool(s.get("signal_id")), f"Signal missing signal_id: {s}")

    def test_pydantic_schema_validation_auto_populates_signal_id(self) -> None:
        from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
        payload = {
            "meeting_id": "meeting-test-01",
            "classification": "internal",
            "audiences": ["engineering"],
            "evidence_only_statement_ids": [],
            "evidence_index": {
                "ev-01": {"text": "Daniel Brankin opened the discussion on Jarvis.", "exact_text": "Daniel Brankin", "speaker": "Daniel Brankin", "turn_id": "t-01"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:jarvis", "type": "system", "name": "Jarvis", "description": "Jarvis core", "mutation": "CONFIRM", "evidence_ids": ["ev-01"]},
                    {"id": "system:power-bi", "type": "system", "name": "Power BI", "description": "Power BI pipeline", "mutation": "CONFIRM", "evidence_ids": ["ev-01"]},
                ],
                "relationship_signals": [
                    {
                        "from_id": "system:jarvis",
                        "to_id": "system:power-bi",
                        "predicate": "INTEGRATES_WITH",
                        "evidence_ids": ["ev-01"],
                        "description": "Jarvis integrates with Power BI.",
                    }
                ],
            },
        }
        obj = CanonicalSignalProjectionV57.model_validate(payload)
        self.assertTrue(len(obj.graph_mutations.relationship_signals[0].signal_id) > 0)

    def test_heal_orphan_secondary_systems_and_jarvis_projection(self) -> None:
        payload = {
            "evidence_index": {
                "ev-01": {"text": "Daniel Brankin opened the discussion on Jarvis platform capabilities.", "exact_text": "Daniel Brankin"},
                "ev-02": {"text": "Sharon Diaram participated.", "exact_text": "Sharon Diaram"},
                "ev-gis": {"text": "At that it is not possible to get a JIRA ticket to our GIS team.", "exact_text": "JIRA ticket to our GIS team", "speaker": "Daniel Brankin"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:jira", "type": "system", "name": "JIRA", "description": "Enterprise ticketing platform."},
                    {"id": "system:gis", "type": "system", "name": "GIS", "description": "GIS web map service."},
                ],
                "relationship_signals": [
                    {
                        "from_id": "system:gis",
                        "to_id": "system:power-bi",
                        "predicate": "INTEGRATES_WITH",
                        "evidence_ids": ["ev-01"],
                        "description": "GIS integrates with Power BI.",
                    }
                ],
            },
        }
        changes: List[str] = []
        result = heal_graph_projection(payload, changes)

        ent_ids = {e["id"] for e in result["graph_mutations"]["entity_upserts"]}
        # system:jarvis projected from ev-01 text
        self.assertIn("system:jarvis", ent_ids)
        self.assertIn("system:jira", ent_ids)

        signals = result["graph_mutations"]["relationship_signals"]
        jira_signals = [s for s in signals if s.get("to_id") == "system:jira" and s.get("from_id") == "workstream:spatial-file-upload"]
        self.assertEqual(len(jira_signals), 1)
        self.assertEqual(jira_signals[0]["predicate"], "ROUTES_TO")

    def test_heal_core_workstream_nodes_predicate_compliance(self) -> None:
        from dual_pipeline.transcript_qa_core.semantic_linters import lint_predicate_domain
        payload = {
            "evidence_index": {
                "ev-01": {"text": "Daniel Brankin opened the discussion on Jarvis platform capabilities.", "exact_text": "Daniel Brankin"},
                "ev-06": {"text": "WBS actuals and spend auditing.", "exact_text": "WBS actuals"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:jarvis", "type": "system", "name": "Jarvis", "description": "Jarvis platform."},
                ],
                "relationship_signals": [],
            },
        }
        changes: List[str] = []
        result = heal_graph_projection(payload, changes)
        signals = result["graph_mutations"]["relationship_signals"]

        # Check workstream signals generated
        ws_signals = [s for s in signals if "workstream:" in str(s.get("to_id"))]
        self.assertTrue(len(ws_signals) > 0)
        for sig in ws_signals:
            self.assertIn(sig.get("predicate"), ("DELIVERS", "ENABLES"))

        # Verify Gate 3.9 predicate domain compliance
        passed, violations, details = lint_predicate_domain(signals)
        self.assertTrue(passed, f"Gate 3.9 violations: {violations}")
        self.assertEqual(len(violations), 0)


if __name__ == "__main__":
    unittest.main()

