"""
tests/test_engineering_jarvis_qa_healers.py

Unit and regression tests for LENS Engineering Jarvis healers, contracts, and QA gates.
Verifies:
1. Role stripping in output2_contract does not classify attendee roles as persona anchors.
2. Speaker tier matching in appendix_standard correctly excludes generic participant labels.
3. Deterministic healing and evaluation of all 3 LENS Engineering Jarvis deliverables returns PASS.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.prompts.contracts.output2_contract import (
    validate_section3_thematic_decoupling,
)
from dual_pipeline.transcript_qa_core.appendix_standard import (
    SpeakerTier,
    _get_speaker_tier,
)
from dual_pipeline.transcript_qa_core.engineering_jarvis_ontology import (
    heal_engineering_jarvis_recap,
    is_engineering_jarvis,
)
from dual_pipeline.transcript_qa_core.evaluator import evaluate_all_qa_gates
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2


def test_is_engineering_jarvis_detection():
    meta_valid = {"meeting_name": "LENS Engineering Jarvis", "project": "project_lens"}
    assert is_engineering_jarvis(meta_valid, text="") is True

    meta_kickoff = {"meeting_name": "Jarvis Kickoff Call", "project": "project_lens"}
    assert is_engineering_jarvis(meta_kickoff, text="") is False


def test_output2_contract_pure_thematic_anchors_pass():
    header_with_roles = (
        "## Attendees\n"
        "- Kaleena McHugo (Apex Global Enterprises, Engineering Manager)\n"
        "- Jason Lickliter (Apex Global Enterprises, Software Engineer, NEA Contractor (Bright Light))\n"
        "- Taylor Mercer (PartnerAdvisory, Delivery Lead)\n"
        "- Mitch Garstka (PartnerAdvisory, Lead Software Engineer)\n"
        "- Amit Ayer (PartnerAdvisory, Lead Client Partner)\n\n"
        "## Key Discussion Points & Findings\n"
        "- **Jira Project Provisioning & Workstream Tracking**: "
        "Kaleena McHugo confirmed she will create the Jira project for Jarvis to track early implementation work, technical spikes, "
        "and development phases [00:03:40].\n"
    )
    passed, fails = validate_section3_thematic_decoupling(header_with_roles)
    assert passed is True
    assert fails == []


def test_appendix_standard_speaker_tier_excludes_generic_participant():
    shares = {
        "Mitch Garstka": 0.40,
        "Kaleena McHugo": 0.127,
        "Jason Lickliter": 0.086,
        "Apex Global Enterprises (NEA) Participant": 0.3744,
    }
    tier_kaleena = _get_speaker_tier("Kaleena McHugo — Apex Global Enterprises (NEA) — Engineering Manager", shares)
    tier_jason = _get_speaker_tier("Jason Lickliter — Apex Global Enterprises (NEA) — Software Engineer", shares)
    tier_mitch = _get_speaker_tier("Mitch Garstka — PartnerAdvisory — Lead Software Engineer", shares)

    assert tier_mitch == SpeakerTier.PRIMARY
    assert tier_kaleena == SpeakerTier.PARTICIPANT
    assert tier_jason == SpeakerTier.PARTICIPANT


def test_engineering_jarvis_recap_healer_and_qa_gates():
    base = Path("output/project_lens/10-07-2026/Engineering_Jarvis_Kaleena_PartnerAdvisory")
    f1_path = base / "01_Engineering_Jarvis_Kaleena_PartnerAdvisory_10-07-2026_0235PM0330PMET_hitl_resolved_transcript.md"
    f2_path = base / "02_Engineering_Jarvis_Kaleena_PartnerAdvisory_10-07-2026_0235PM0330PMET_executive_recap.md"
    f3_path = base / "03_Engineering_Jarvis_Kaleena_PartnerAdvisory_10-07-2026_0235PM0330PMET_canonical_signal_projection.json"

    meta = {
        "meeting_name": "LENS Engineering Jarvis",
        "title": "LENS Engineering Jarvis",
        "date": "10/07/2026",
        "project": "project_lens",
        "attendees": [
            "Kaleena McHugo (Engineering Manager, Apex Global Enterprises)",
            "Taylor Mercer (Delivery Lead, PartnerAdvisory)",
            "Mitch Garstka (Lead Software Engineer, PartnerAdvisory)",
            "Jason Lickliter (Software Engineer, NEA Contractor, Bright Light)",
            "Amit Ayer (Lead Client Partner, PartnerAdvisory)",
        ],
    }

    f1_text = f1_path.read_text(encoding="utf-8")
    f2_text = f2_path.read_text(encoding="utf-8")
    f3_text = f3_path.read_text(encoding="utf-8")

    rep = evaluate_all_qa_gates(
        f1_text=f1_text,
        f2_text=f2_text,
        f3_text=f3_text,
        metadata=meta,
        auto_heal=False,
    )

    assert rep["status"] == "PASS"
    assert rep["passed"] is True
    assert rep["output_01_forensic_transcript"]["passed"] is True
    assert rep["output_02_executive_recap"]["passed"] is True
    assert rep["output_03_canonical_signal_projection"]["passed"] is True
    assert rep["cross_artifact_consistency"]["passed"] is True
