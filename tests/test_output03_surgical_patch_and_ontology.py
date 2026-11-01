"""
tests/test_output03_surgical_patch_and_ontology.py

Comprehensive acceptance and regression test suite for:
- Wave 1: Entity Nickname Preservation & C0 Roster Role Alignment
- Wave 2: Output 03 Surgical JSON Patching & Single-Pass Remediation
"""
from __future__ import annotations

import json
import unittest
from typing import Any, Dict

from dual_pipeline.transcript_metadata_core.entity_resolver_graph import _clean_node_names
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.lens_ontology import _reground_system_lens, heal_lens_core_systems


class TestOutput03SurgicalPatchAndOntologyWave1(unittest.TestCase):
    """Wave 1 Acceptance Suite: Node nickname cleaning, role preservation, and collateral grounding."""

    def test_wave1_clean_node_names_preserves_formal_name_precedence(self):
        """Verify formal canonical name takes precedence over parenthetical nickname."""
        c_raw, c_name, clean_canon, clean_disp, nicknames = _clean_node_names(
            "Eduardo (Ed) More", "Eduardo More"
        )
        self.assertEqual(c_name, "Eduardo More")
        self.assertEqual(clean_canon, "Eduardo More")
        self.assertIn("Ed", nicknames)

        # Even if display name is empty, formal name is preferred over nickname
        c_raw2, c_name2, clean_canon2, clean_disp2, nicknames2 = _clean_node_names(
            "Eduardo (Ed) More", None
        )
        self.assertEqual(c_name2, "Eduardo More")
        self.assertIn("Ed", nicknames2)

    def test_wave1_amit_ayer_roster_role_preservation(self):
        """Verify person:amit-ayer description preserves C0 verified roster role."""
        data = {
            "evidence_index": {
                "ev-01": {"text": "Discussion on architecture", "exact_text": "Discussion on architecture"}
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:amit-ayer",
                        "name": "Amit Ayer",
                        "description": "Principal Architect & Technology Strategist at PartnerAdvisory.",
                    }
                ],
                "relationship_signals": []
            }
        }
        meta = {
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (Principal Architect & Technology Strategist, PartnerAdvisory)"]
            }
        }
        res_str, changes = DeterministicHealer.heal_output_3(data, metadata=meta)
        res = json.loads(res_str)
        ents = {e["id"]: e for e in res["graph_mutations"]["entity_upserts"]}
        self.assertIn("person:amit-ayer", ents)
        self.assertEqual(
            ents["person:amit-ayer"]["description"],
            "Principal Architect & Technology Strategist at PartnerAdvisory."
        )

    def test_wave1_split_compound_role_specialist_vp(self):
        """Verify compound role:rule-specialist-vp is split into atomic roles and edge re-targeted."""
        data = {
            "evidence_index": {
                "ev-01": {"text": "Role definitions", "exact_text": "Role definitions"}
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:eduardo-more",
                        "name": "Eduardo More",
                        "description": "Atlas Lead at Apex Global Enterprises.",
                        "evidence_ids": ["ev-01"]
                    },
                    {
                        "id": "role:rule-specialist-vp",
                        "name": "Rule Specialist & VP",
                        "description": "Compound role",
                        "evidence_ids": ["ev-01"]
                    }
                ],
                "relationship_signals": [
                    {
                        "from_id": "person:eduardo-more",
                        "to_id": "role:rule-specialist-vp",
                        "predicate": "HAS_ROLE"
                    }
                ]
            }
        }
        res_str, changes = DeterministicHealer.heal_output_3(data, metadata={"project": "project_lens"})
        res = json.loads(res_str)
        ents = {e["id"]: e for e in res["graph_mutations"]["entity_upserts"]}
        self.assertNotIn("role:rule-specialist-vp", ents)
        self.assertIn("role:rule-specialist", ents)
        self.assertIn("role:vice-president", ents)
        self.assertEqual(ents["role:rule-specialist"]["name"], "Rule Specialist")
        self.assertEqual(ents["role:vice-president"]["name"], "Vice President")

        signals = res["graph_mutations"]["relationship_signals"]
        has_role_sigs = [s for s in signals if s.get("from_id") == "person:eduardo-more"]
        self.assertTrue(any(s.get("to_id") == "role:rule-specialist" for s in has_role_sigs))
        self.assertTrue(any(s.get("to_id") == "role:vice-president" for s in has_role_sigs))

    def test_wave1_ground_lens_collateral_entities_and_evidence(self):
        """Verify LENS admin dashboard and review queue are grounded from collateral evidence."""
        data = {
            "evidence_index": {
                "ev-01": {
                    "text": "We are reviewing Project LENS system architecture.",
                    "exact_text": "We are reviewing Project LENS system architecture.",
                    "locator": {"kind": "timestamp", "value": "[00:01:00]"}
                },
                "ev-06": {
                    "text": "LENS Admin Dashboard: Images Processed (Today) 200, Auto Accepted Rate 85.5%",
                    "exact_text": "Images Processed (Today) 200",
                    "locator": {"kind": "attachment", "value": "lens_admin_dashboard_200_processed.png"},
                    "source_type": "ATTACHMENT"
                },
                "ev-05": {
                    "text": "Review Queue: 93 pending review items awaiting manual inspection before final write",
                    "exact_text": "Review Queue: 93 pending review items",
                    "locator": {"kind": "attachment", "value": "lens_review_queue_93_pending.png"},
                    "source_type": "ATTACHMENT"
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:lens", "name": "Project LENS", "evidence_ids": ["ev-01"]}
                ],
                "relationship_signals": []
            }
        }
        meta = {
            "project": "project_lens",
            "source_text": "[00:01:00] We are reviewing Project LENS system architecture."
        }
        res_str, changes = DeterministicHealer.heal_output_3(data, metadata=meta)
        res = json.loads(res_str)
        ents = {e["id"]: e for e in res["graph_mutations"]["entity_upserts"]}

        self.assertIn("system:lens-admin-dashboard", ents)
        self.assertEqual(ents["system:lens-admin-dashboard"]["name"], "LENS Admin Dashboard")
        self.assertIn("ev-06", ents["system:lens-admin-dashboard"]["evidence_ids"])

        self.assertIn("system:lens-review-queue", ents)
        self.assertEqual(ents["system:lens-review-queue"]["name"], "LENS Review Queue")
        self.assertIn("ev-05", ents["system:lens-review-queue"]["evidence_ids"])

        # system:lens linked to collateral evidence
        self.assertIn("system:lens", ents)
        self.assertTrue(any(ev in ents["system:lens"]["evidence_ids"] for ev in ("ev-06", "ev-05")))


class TestOutput03SurgicalPatchWave2(unittest.TestCase):
    """Wave 2 Acceptance Suite: Output 03 Surgical JSON Patching & Single-Pass Remediation."""

    def setUp(self):
        self.sample_candidate = {
            "schema_version": "5.7",
            "meeting_id": "meeting-20261016-ed-amit-lens",
            "evidence_index": {
                "ev-01": {"text": "Discussion on architecture", "exact_text": "Discussion on architecture"},
                "ev-06": {"text": "Dashboard 200 processed", "exact_text": "Dashboard 200 processed"}
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:eduardo-more",
                        "name": "Eduardo More",
                        "description": "Atlas Lead",
                        "evidence_ids": ["ev-01"]
                    },
                    {
                        "id": "person:amit-ayer",
                        "name": "Amit Ayer",
                        "description": "Lead Client Partner",
                        "evidence_ids": ["ev-01"]
                    },
                    {
                        "id": "system:lens",
                        "name": "Project LENS",
                        "evidence_ids": ["ev-01"]
                    },
                    {
                        "id": "system:unsupported-fake",
                        "name": "Fake System",
                        "evidence_ids": ["ev-01"]
                    }
                ],
                "relationship_signals": [
                    {
                        "from_id": "system:lens",
                        "to_id": "system:unsupported-fake",
                        "relation_type": "CONNECTS_TO"
                    }
                ]
            }
        }

    def test_wave2_apply_json_critique_patch_adds_and_updates_entities(self):
        """Verify surgical patch adds missing entities and updates existing entity properties."""
        from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher

        critique = {
            "operations": {
                "add_entities": [
                    {
                        "id": "system:lens-admin-dashboard",
                        "name": "LENS Admin Dashboard",
                        "entity_type": "SYSTEM",
                        "evidence_ids": ["ev-06"]
                    }
                ],
                "update_entities": [
                    {
                        "id": "person:amit-ayer",
                        "description": "Principal Architect & Technology Strategist at PartnerAdvisory."
                    }
                ],
                "add_relationships": [
                    {
                        "from_id": "person:amit-ayer",
                        "to_id": "system:lens",
                        "relation_type": "ARCHITECTS"
                    },
                    {
                        "from_id": "system:lens",
                        "to_id": "system:lens-admin-dashboard",
                        "relation_type": "HAS_COMPONENT"
                    }
                ]
            }
        }
        meta = {
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (Principal Architect & Technology Strategist, PartnerAdvisory)"]
            }
        }
        patched_str, audit = SurgicalPatcher.apply_json_critique_patch(
            original_json=self.sample_candidate,
            critique_diff_or_envelope=critique,
            metadata=meta
        )
        self.assertEqual(audit["status"], "APPLIED")
        patched = json.loads(patched_str)
        ents = {e["id"]: e for e in patched["graph_mutations"]["entity_upserts"]}

        self.assertIn("system:lens-admin-dashboard", ents)
        self.assertEqual(ents["person:amit-ayer"]["description"], "Principal Architect & Technology Strategist at PartnerAdvisory.")

        rels = patched["graph_mutations"]["relationship_signals"]
        self.assertTrue(any(
            r.get("from_id") == "person:amit-ayer" and r.get("to_id") == "system:lens"
            for r in rels
        ))

    def test_wave2_apply_json_critique_patch_removes_ungrounded_entity_and_cascades_relationships(self):
        """Verify removing an ungrounded entity purges it and cascades to all connected relationships."""
        from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher

        critique = {
            "remove_entities": ["system:unsupported-fake"]
        }
        patched_str, audit = SurgicalPatcher.apply_json_critique_patch(
            original_json=self.sample_candidate,
            critique_diff_or_envelope=critique
        )
        self.assertEqual(audit["status"], "APPLIED")
        patched = json.loads(patched_str)
        ents = {e["id"]: e for e in patched["graph_mutations"]["entity_upserts"]}
        self.assertNotIn("system:unsupported-fake", ents)

        rels = patched["graph_mutations"]["relationship_signals"]
        self.assertFalse(any(
            r.get("from_id") == "system:unsupported-fake" or r.get("to_id") == "system:unsupported-fake"
            for r in rels
        ))

    def test_wave2_apply_json_critique_patch_enforces_protected_invariance(self):
        """Verify attempting to remove or mutate a protected entity raises ProtectedPathMutationError."""
        from dual_pipeline.transcript_qa_core.surgical_patcher import ProtectedPathMutationError, SurgicalPatcher

        critique = {
            "remove_entities": ["person:eduardo-more"],
            "protected_entities": ["person:eduardo-more"]
        }
        with self.assertRaises(ProtectedPathMutationError):
            SurgicalPatcher.apply_json_critique_patch(
                original_json=self.sample_candidate,
                critique_diff_or_envelope=critique,
                protected_entities=["person:eduardo-more"]
            )

    def test_wave2_apply_json_critique_patch_boundary_and_malformed_inputs(self):
        """Verify boundary payloads (empty string, empty dict) and malformed inputs fail closed or handle cleanly."""
        from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher

        # Boundary: Empty string
        res, audit = SurgicalPatcher.apply_json_critique_patch("", {})
        self.assertEqual(res, "")
        self.assertEqual(audit.get("status"), "SKIPPED")

        # Malformed: Invalid JSON string
        with self.assertRaises(ValueError):
            SurgicalPatcher.apply_json_critique_patch("{not valid json...", {})

        # Boundary: Empty dict
        res_empty, audit_empty = SurgicalPatcher.apply_json_critique_patch({}, {})
        self.assertEqual(audit_empty.get("status"), "APPLIED")
        parsed = json.loads(res_empty)
        self.assertIn("graph_mutations", parsed)

    def test_wave2_synthesis_strategy_surgical_patch_wiring(self):
        """Verify FullMeetingSynthesisStrategy._try_apply_output_3_surgical_patch executes cleanly."""
        import tempfile
        from pathlib import Path
        from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy

        with tempfile.TemporaryDirectory() as td:
            cand_p = Path(td) / "output_3.ckpt.json"
            cand_p.write_text(json.dumps(self.sample_candidate), encoding="utf-8")

            critique = {
                "operations": {
                    "update_entities": [
                        {"id": "person:amit-ayer", "description": "Principal Architect & Technology Strategist at PartnerAdvisory."}
                    ]
                }
            }
            meta = {
                "attendees": {
                    "PartnerAdvisory": ["Amit Ayer (Principal Architect & Technology Strategist, PartnerAdvisory)"]
                }
            }
            res = FullMeetingSynthesisStrategy._try_apply_output_3_surgical_patch(
                prev_3=cand_p,
                critique_env_3=critique,
                needs_rem_3=False,
                meta=meta
            )
            self.assertIsNotNone(res)
            parsed = json.loads(res)
            ents = {e["id"]: e for e in parsed["graph_mutations"]["entity_upserts"]}
            self.assertEqual(ents["person:amit-ayer"]["description"], "Principal Architect & Technology Strategist at PartnerAdvisory.")

    def test_wave2_ed_amit_recovery_from_judge_remediation(self):
        """Verify full single-pass recovery of candidate applying judge critique envelope."""
        from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher

        critique_env = {
            "artifact": "OUTPUT03",
            "target_path": "graph_mutations",
            "protected_paths": ["person:eduardo-more", "person:amit-ayer", "system:lens"],
            "operations": {
                "update_entities": [
                    {
                        "id": "person:amit-ayer",
                        "description": "Principal Architect & Technology Strategist at PartnerAdvisory."
                    }
                ],
                "add_entities": [
                    {
                        "id": "system:lens-admin-dashboard",
                        "name": "LENS Admin Dashboard",
                        "entity_type": "SYSTEM",
                        "evidence_ids": ["ev-06"]
                    }
                ],
                "add_relationships": [
                    {
                        "from_id": "system:lens",
                        "to_id": "system:lens-admin-dashboard",
                        "relation_type": "HAS_COMPONENT"
                    }
                ],
                "remove_entities": ["system:unsupported-fake"]
            }
        }
        meta = {
            "project": "project_lens",
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (Principal Architect & Technology Strategist, PartnerAdvisory)"],
                "Apex Global Enterprises (NEA)": ["Eduardo More (Atlas Lead, Apex Global Enterprises)"]
            }
        }
        res_str, audit = SurgicalPatcher.apply_json_critique_patch(
            original_json=self.sample_candidate,
            critique_diff_or_envelope=critique_env,
            metadata=meta
        )
        self.assertEqual(audit["status"], "APPLIED")
        self.assertIn("person:eduardo-more", audit["protected_entities_verified"])
        self.assertIn("person:amit-ayer", audit["protected_entities_verified"])
        self.assertIn("system:lens", audit["protected_entities_verified"])

        parsed = json.loads(res_str)
        ents = {e["id"]: e for e in parsed["graph_mutations"]["entity_upserts"]}
        self.assertIn("system:lens-admin-dashboard", ents)
        self.assertNotIn("system:unsupported-fake", ents)
        self.assertEqual(ents["person:amit-ayer"]["description"], "Principal Architect & Technology Strategist at PartnerAdvisory.")


if __name__ == "__main__":
    unittest.main()
