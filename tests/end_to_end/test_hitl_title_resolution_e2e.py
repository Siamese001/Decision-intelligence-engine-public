#!/usr/bin/env python3
"""
End-to-End Test Suite for HITL Attendee Title Resolution Lifecycle.

Exercises the complete lifecycle:
1. Raw ingestion with organizational tokens / missing attendee titles (APEX / PartnerAdvisory).
2. Normalization stripping org-as-role tokens.
3. Gate C0.5 intercepting ungrounded roles and raising atomic HITLRequiredError.
4. Operator resolution via apply_hitl_role_resolution, writing signed receipts to Merkle ledger.
5. Invalidation of cached prior context (PL-2 defense).
6. Gate C0.5 clearance upon re-execution.
7. Output 01, 02, and 03 generation with full QA Gate validation:
   - Gate 1.2a (Attribution title completeness)
   - Gate 1.7a (Cryptographic ALR receipt binding)
   - Gate 2.12 (Recap company-grouped & Appendix title consistency)
   - Gate 3.11 (Person description and HAS_ROLE validation)
   - Gate 3.12 (MEMBER_OF organization consistency)
8. StakeholderResolutionValidator coherence check.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import clean_attendee_entry
from dual_pipeline.transcript_pipeline_core.context_builder import load_prior_meeting_context
from dual_pipeline.transcript_pipeline_core.stakeholder_validator import (
    StakeholderResolutionValidator,
)
from dual_pipeline.transcript_qa_core.gate1_alr import _check_hitl_receipt_binding
from dual_pipeline.transcript_qa_core.gate1_attribution import _check_attendee_title_completeness
from dual_pipeline.transcript_qa_core.gate2_titles import check_gate_2_12_attendee_titles
from dual_pipeline.transcript_qa_core.gate3_roles import (
    lint_member_of_consistency,
    lint_person_descriptions,
)
from dual_pipeline.transcript_qa_core.semantic_linters import lint_predicate_domain
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    apply_hitl_role_resolution,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


@pytest.mark.e2e
class TestHitlTitleResolutionE2E(unittest.TestCase):
    """E2E suite verifying full closed-loop title clarification and QA gate compliance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.ledger_path = self.work_dir / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)
        from dual_pipeline.transcript_metadata_core.entity_resolver import (
            _CANONICAL_ENTERPRISE_DIRECTORY,
        )
        self._orig_dir = dict(_CANONICAL_ENTERPRISE_DIRECTORY)
        for key in ("daniel brankin", "daniel", "sharon diaram", "sharon"):
            _CANONICAL_ENTERPRISE_DIRECTORY.pop(key, None)

    def tearDown(self) -> None:
        from dual_pipeline.transcript_metadata_core.entity_resolver import (
            _CANONICAL_ENTERPRISE_DIRECTORY,
        )
        _CANONICAL_ENTERPRISE_DIRECTORY.clear()
        _CANONICAL_ENTERPRISE_DIRECTORY.update(self._orig_dir)
        self.temp_dir.cleanup()

    def test_e2e_hitl_title_resolution_lifecycle(self) -> None:
        """Full test of ingestion -> HITL interception -> resolution -> QA clearance."""
        # Step 1: Raw attendee strings with organizational acronyms masquerading as titles
        raw_attendees = [
            "Daniel Brankin (APEX)",
            "Sharon Diaram (APEX)",
            "Amit Ayer (PartnerAdvisory)",
        ]

        cleaned_roster = []
        for raw in raw_attendees:
            name, role, org = clean_attendee_entry(raw)
            cleaned_roster.append({"name": name, "role": role, "org": org})

        # Daniel & Sharon should have role cleared because "APEX" is an org token
        self.assertEqual(cleaned_roster[0]["name"], "Daniel Brankin")
        self.assertEqual(cleaned_roster[0]["role"], "")
        self.assertEqual(cleaned_roster[0]["org"], "Apex Global Enterprises (NEA)")

        self.assertEqual(cleaned_roster[1]["name"], "Sharon Diaram")
        self.assertEqual(cleaned_roster[1]["role"], "")
        self.assertEqual(cleaned_roster[1]["org"], "Apex Global Enterprises (NEA)")

        # Amit Ayer is resolved from directory prior
        self.assertEqual(cleaned_roster[2]["name"], "Amit Ayer")
        self.assertEqual(cleaned_roster[2]["role"], "Principal Architect & Technology Strategist")
        self.assertEqual(cleaned_roster[2]["org"], "PartnerAdvisory")

        # Step 2: C0.5 Title Completeness Gate detects missing roles on pre-enrichment roster
        l0_context = {
            "metadata": {
                "meeting_name": "Jarvis_Q1_Charter_Sharon_PartnerAdvisory",
                "date": "09/28/2026",
                "roster": cleaned_roster,
            }
        }

        gate = C05TitleCompletenessGate()
        with self.assertRaises(HITLRequiredError) as exc_info:
            gate.execute(l0_context)

        err = exc_info.exception
        self.assertEqual(err.hitl_type, "role_clarification")
        self.assertIn("Daniel Brankin", err.prompt)
        self.assertTrue(len(err.options) >= 2)

        # Step 3: Operator applies atomic resolution for both attendees
        apply_hitl_role_resolution(
            l0_context,
            "Daniel Brankin",
            "Director of Digital Technology",
            store=self.store,
        )
        apply_hitl_role_resolution(
            l0_context,
            "Sharon Diaram",
            "VP of Digital Operations",
            store=self.store,
        )

        # Step 4: Verify metadata updates and invalidation flag
        meta = l0_context["metadata"]
        self.assertTrue(meta.get("invalidate_prior_outputs"))
        self.assertIn("Daniel Brankin", meta.get("hitl_role_receipts", {}))
        self.assertIn("Sharon Diaram", meta.get("hitl_role_receipts", {}))

        # Verify prior context contamination defense
        prior_dir = self.work_dir / "prior_meeting"
        prior_dir.mkdir()
        (prior_dir / "01_prior_hitl_resolved_transcript.md").write_text("Stale (specific title not provided)", encoding="utf-8")
        load_prior_meeting_context(str(prior_dir), meta)
        self.assertNotIn("prior_forensic_transcript", meta)

        # Step 5: Re-executing C0.5 passes cleanly
        recheck_context = gate.execute(l0_context)
        self.assertTrue(recheck_context.get("c05_title_gate_cleared"))

        # Step 6: Verify Output 01 adheres to QA gates
        o1_text = """# Forensic Meeting Transcript
Meeting: Jarvis Q1 Charter Review
Date: 09/28/2026
Attendees:
- Apex Global Enterprises (NEA): Daniel Brankin (Director of Digital Technology), Sharon Diaram (VP of Digital Operations)
- PartnerAdvisory: Amit Ayer (Lead Enterprise Architect)

Turn 1: [00:00:10] Daniel Brankin: Welcome to the Q1 Charter discussion.
Turn 2: [00:00:30] Sharon Diaram: We need to align on the deliverables.
"""
        failures_1_2a = _check_attendee_title_completeness(o1_text, meta)
        self.assertEqual(len(failures_1_2a), 0)

        failures_1_7a = _check_hitl_receipt_binding(meta, ["Daniel Brankin", "Sharon Diaram"])
        self.assertEqual(len(failures_1_7a), 0)

        # Step 7: Verify Output 02 adheres to Gate 2.12 (Header + Appendix)
        o2_text = """# Executive Recap
Date: 09/28/2026
Meeting: Jarvis Q1 Charter Review

## Attendees
- Apex Global Enterprises (NEA): Daniel Brankin (Director of Digital Technology), Sharon Diaram (VP of Digital Operations)
- PartnerAdvisory: Amit Ayer (Lead Enterprise Architect)

## Executive Summary
Strategic review of Jarvis deliverables.

## Appendix: Persona-Centric Insights
### Daniel Brankin — Apex Global Enterprises (NEA), Director of Digital Technology
Identified governance criteria.

### Sharon Diaram — Apex Global Enterprises (NEA), VP of Digital Operations
Confirmed milestone timelines.

### Amit Ayer — PartnerAdvisory, Lead Enterprise Architect
Presented architecture roadmap.
"""
        passed_2_12, failures_2_12, _ = check_gate_2_12_attendee_titles(o2_text)
        self.assertTrue(passed_2_12)
        self.assertEqual(len(failures_2_12), 0)

        # Step 8: Verify Output 03 adheres to Gate 3.11, 3.12, and predicate registry
        o3_entities = [
            {"id": "person:daniel_brankin", "name": "Daniel Brankin", "description": "Director of Digital Technology at Apex Global Enterprises (NEA)."},
            {"id": "person:sharon_diaram", "name": "Sharon Diaram", "description": "VP of Digital Operations at Apex Global Enterprises (NEA)."},
            {"id": "person:amit_ayer", "name": "Amit Ayer", "description": "Lead Enterprise Architect at PartnerAdvisory."},
        ]
        p_311, errs_311, _ = lint_person_descriptions(o3_entities)
        self.assertTrue(p_311)
        self.assertEqual(len(errs_311), 0)

        o3_relationships = [
            {"signal_id": "r1", "predicate": "MEMBER_OF", "target_node_id": "org:apex_global_energy", "description": "Apex Global Enterprises executive leadership."},
            {"signal_id": "r2", "predicate": "HAS_ROLE", "from_id": "person:daniel_brankin", "to_id": "role:director_of_digital_technology"},
            {"signal_id": "r3", "predicate": "HAS_ROLE", "from_id": "person:sharon_diaram", "to_id": "role:vp_of_digital_operations"},
        ]
        p_312, errs_312, _ = lint_member_of_consistency(o3_relationships)
        self.assertTrue(p_312)
        self.assertEqual(len(errs_312), 0)

        passed_preds, pred_violations, _ = lint_predicate_domain(o3_relationships)
        self.assertTrue(passed_preds)
        self.assertEqual(len(pred_violations), 0)

        # Step 9: Verify StakeholderResolutionValidator
        validator = StakeholderResolutionValidator(repo_root=self.work_dir)
        report = validator.validate_deliverables(
            o1_text,
            o2_text,
            {"entity_upserts": o3_entities},
            fail_closed=False,
            metadata=meta,
        )
        self.assertTrue(report.is_consistent)
        self.assertEqual(len(report.discrepancies), 0)
