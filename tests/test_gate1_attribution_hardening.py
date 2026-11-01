"""
Tests for Output 1 HITL Attribution Hardening (Wave 6).

Covers:
- C0 HITL firewall: HITLRequiredError fires on raw placeholder roles (before entity enrichment)
- C0 HITL bypass: no HITLRequiredError when roles are explicitly supplied
- Gate 1.7 HITL receipt binding: fails when ALR not backed by autonomous decisions
- Gate 1.8: Attribution Summary table presence required for multi-party meetings
- Gate 1.9: ALR minimum row count (speakers + 1)
"""
from __future__ import annotations

import pytest

from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate1_attribution import (
    _has_placeholder_role,
    check_alr_row_count_minimum,
    check_ambiguity_lock_register_and_speaker_hitl,
)
from dual_pipeline.transcript_stages_core.c0_gates import C0RosterContextGate
from dual_pipeline.transcript_stages_core.models import HITLRequiredError

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_l0_context(roster: list, bypass: bool = False) -> dict:
    meta: dict[str, Any] = {"roster": roster}
    if bypass:
        meta["bypass_speaker_hitl"] = True
    return {
        "metadata": meta,
        "source_type": "UNDIARIZED_TEXT",
        "diarization_status": "UNDIARIZED",
    }


def _make_f1_with_alr(speakers: list[str], alr_rows: int = 5, has_summary: bool = True) -> str:
    """Build a minimal Output 1 markdown with configurable ALR rows and summary."""
    header = "# Test Meeting -- HITL-Resolved Transcript\n\n**Date:** 09/18/2026\n**Participants:** " + ", ".join(speakers) + "\n\n---\n\n"

    transcript = ""
    for i, spk in enumerate(speakers * 5):
        transcript += f"### [00:0{i}:00]\n\n**{spk}:** Dialogue turn {i}.\n\n"

    alr = "## Ambiguity Lock Register\n\n| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n|---|---|---|---|---|---|\n"
    for j in range(alr_rows):
        alr += f"| {j+1} | Speaker Attribution: {speakers[j % len(speakers)]} | [00:00:00 - Session End] | stmt-spk-{j} | Verified via anchor cue. | Multi-Tier Acoustic Protocol (Operator Verified) |\n"

    summary = ""
    if has_summary:
        summary = "\n## Attribution Summary\n\n| Speaker | Org | Role | Turns Attributed | Confidence Tier | Primary Anchor Cue |\n|---|---|---|---|---|---|\n"
        for spk in speakers:
            summary += f"| {spk} | PartnerAdvisory | Engineer | 10 | HIGH | Self-identification |\n"

    return header + transcript + alr + summary


# ─────────────────────────────────────────────────────────────────────────────
# Wave 1 Tests: C0 HITL Firewall
# ─────────────────────────────────────────────────────────────────────────────

class TestC0HITLFirewall:
    """Verify HITL fires on raw operator-supplied roles, not post-enrichment values."""

    def test_hitl_fires_for_placeholder_role_with_directory_match(self):
        """Jake Stark is in directory but operator supplied 'Participant, Unknown'.
        C0 should auto-resolve via AUTONOMOUS_APPROVED (Δ=57% ≥ 25%) and NOT raise.
        Autonomous decision must be stored in metadata.
        """
        gate = C0RosterContextGate()
        ctx = _make_l0_context([
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
            {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "Participant, Unknown"},
        ])
        # Should NOT raise — Jake is in directory, Δ=57% ≥ 25% auto-resolves
        result = gate.execute(ctx)
        meta = result["metadata"]
        # Autonomous decision must be stored
        assert "autonomous_hitl_role_decisions" in meta, "autonomous_hitl_role_decisions not stored"
        assert "Jake Stark" in meta["autonomous_hitl_role_decisions"]
        decision = meta["autonomous_hitl_role_decisions"]["Jake Stark"]
        assert decision["gating"] == "AUTONOMOUS_APPROVED"
        assert decision["raw_operator_role"] in ("Participant, Unknown", "participant, unknown")

    def test_hitl_fires_for_unknown_attendee_not_in_directory(self):
        """Attendee not in system directory with placeholder role → must raise HITLRequiredError."""
        gate = C0RosterContextGate()
        ctx = _make_l0_context([
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
            {"name": "Unknown Contractor Xyz", "org": "Unknown", "role": "Participant"},
        ])
        with pytest.raises(HITLRequiredError) as exc_info:
            gate.execute(ctx)
        err = exc_info.value
        assert err.hitl_type == "role_clarification"
        assert "Unknown Contractor Xyz" in str(err)

    def test_no_hitl_when_all_roles_explicitly_supplied(self):
        """When operator supplies all specific roles, no HITL should fire."""
        gate = C0RosterContextGate()
        ctx = _make_l0_context([
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
            {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
            {"name": "Miles Erickson", "org": "PartnerAdvisory", "role": "Software Engineer"},
        ])
        result = gate.execute(ctx)
        meta = result["metadata"]
        # No autonomous decisions needed — all roles were explicit
        assert not meta.get("autonomous_hitl_role_decisions"), (
            "Autonomous decisions should NOT be stored when operator supplied explicit roles"
        )

    def test_pre_enrichment_roles_snapshot_captured(self):
        """pre_enrichment_roles must capture the raw operator-supplied roles."""
        gate = C0RosterContextGate()
        ctx = _make_l0_context([
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
            {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "Participant, Unknown"},
        ])
        result = gate.execute(ctx)
        meta = result["metadata"]
        assert "pre_enrichment_roles" in meta
        assert "Jake Stark" in meta["pre_enrichment_roles"]
        # Must be the RAW value, not the post-enriched one
        raw_role = meta["pre_enrichment_roles"]["Jake Stark"]
        assert "Participant" in raw_role or "Unknown" in raw_role or raw_role == "", (
            f"Expected raw placeholder role but got: '{raw_role}'"
        )

    def test_bypass_skips_hitl_gate(self):
        """bypass_speaker_hitl=True must fail closed with ValueError."""
        gate = C0RosterContextGate()
        ctx = _make_l0_context([
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
            {"name": "Unknown Person", "org": "Unknown", "role": "Participant"},
        ], bypass=True)
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            gate.execute(ctx)


# ─────────────────────────────────────────────────────────────────────────────
# Wave 2 Tests: Gate 1.7 HITL Receipt Binding
# ─────────────────────────────────────────────────────────────────────────────

class TestGate17HITLReceiptBinding:
    """Verify Gate 1.7 requires HITL receipt binding when operator supplied placeholder roles."""

    def test_alr_not_hitl_backed_fails_gate_1_7(self):
        """When operator supplied placeholder roles but no autonomous decisions recorded → fail."""
        f1_text = _make_f1_with_alr(
            speakers=["Amit Ayer (PartnerAdvisory)", "Jake Stark (PartnerAdvisory)"],
            alr_rows=4,
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
            ],
            # pre_enrichment_roles shows operator supplied placeholder
            "pre_enrichment_roles": {
                "Amit Ayer": "Lead Enterprise Architect",
                "Jake Stark": "Participant, Unknown",  # was a placeholder
            },
            # No autonomous_hitl_role_decisions → simulates LLM-fabricated ALR
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)
        # Should fail due to missing HITL receipt binding
        assert not passed
        assert any("NOT_HITL_BACKED" in f for f in failures), (
            f"Expected NOT_HITL_BACKED failure. Got: {failures}"
        )

    def test_alr_passes_with_autonomous_hitl_decisions(self):
        """When autonomous decisions are recorded → Gate 1.7 passes for HITL binding check."""
        f1_text = _make_f1_with_alr(
            speakers=["Amit Ayer (PartnerAdvisory)", "Jake Stark (PartnerAdvisory)"],
            alr_rows=4,
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
            ],
            "pre_enrichment_roles": {
                "Amit Ayer": "Lead Enterprise Architect",
                "Jake Stark": "Participant, Unknown",
            },
            "autonomous_hitl_role_decisions": {
                "Jake Stark": {
                    "raw_operator_role": "Participant, Unknown",
                    "resolved_role": "UI and Prompting Lead",
                    "gating": "AUTONOMOUS_APPROVED",
                    "confidence": "HIGH (87%)",
                    "margin": "57%",
                },
            },
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)
        no_binding_failures = [f for f in failures if "NOT_HITL_BACKED" in f]
        assert not no_binding_failures, f"Unexpected NOT_HITL_BACKED failure: {no_binding_failures}"

    def test_bypass_speaker_hitl_skips_receipt_check(self):
        """bypass_speaker_hitl=True must fail closed with ValueError."""
        f1_text = _make_f1_with_alr(
            speakers=["Amit Ayer (PartnerAdvisory)", "Jake Stark (PartnerAdvisory)"],
            alr_rows=4,
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
            ],
            "pre_enrichment_roles": {"Jake Stark": "Participant"},
            "bypass_speaker_hitl": True,
        }
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)


# ─────────────────────────────────────────────────────────────────────────────
# Wave 5 Tests: Gate 1.8 + Gate 1.9
# ─────────────────────────────────────────────────────────────────────────────

class TestGate18AttributionSummary:
    """Gate 1.8: Attribution Summary table must be present for multi-party meetings."""

    def test_missing_attribution_summary_fails_gate_1_8(self):
        """Multi-party Output 1 without Attribution Summary should fail Gate 1.8."""
        f1_text = _make_f1_with_alr(
            speakers=["Amit Ayer (PartnerAdvisory)", "Jake Stark (PartnerAdvisory)", "Miles Erickson (PartnerAdvisory)"],
            alr_rows=4,
            has_summary=False,  # No attribution summary
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
                {"name": "Miles Erickson", "org": "PartnerAdvisory", "role": "Software Engineer"},
            ],
        }
        result = run_qa_gate_1(f1_text, metadata=meta)
        g18 = result.get("gates", {}).get("Gate_1_8_Attribution_Summary_Presence", {})
        assert not g18.get("passed"), (
            f"Gate 1.8 should FAIL when Attribution Summary is absent. Failures: {result.get('failures')}"
        )

    def test_attribution_summary_present_passes_gate_1_8(self):
        """Multi-party Output 1 with Attribution Summary should pass Gate 1.8."""
        f1_text = _make_f1_with_alr(
            speakers=["Amit Ayer (PartnerAdvisory)", "Jake Stark (PartnerAdvisory)", "Miles Erickson (PartnerAdvisory)"],
            alr_rows=4,
            has_summary=True,
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
                {"name": "Miles Erickson", "org": "PartnerAdvisory", "role": "Software Engineer"},
            ],
        }
        result = run_qa_gate_1(f1_text, metadata=meta)
        g18 = result.get("gates", {}).get("Gate_1_8_Attribution_Summary_Presence", {})
        assert g18.get("passed"), (
            f"Gate 1.8 should PASS when Attribution Summary is present. Failures: {result.get('failures')}"
        )


class TestGate19ALRRowCountMinimum:
    """Gate 1.9: ALR must have >= number of speakers + 1 rows."""

    def test_insufficient_alr_rows_fails_gate_1_9(self):
        """3-party meeting with only 2 ALR rows (< 4 minimum) should fail Gate 1.9."""
        f1_text = _make_f1_with_alr(
            speakers=["Amit Ayer (PartnerAdvisory)", "Jake Stark (PartnerAdvisory)", "Miles Erickson (PartnerAdvisory)"],
            alr_rows=2,  # Only 2 rows — minimum is 3+1=4
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
                {"name": "Miles Erickson", "org": "PartnerAdvisory", "role": "Software Engineer"},
            ],
        }
        passed, actual, minimum, failures = check_alr_row_count_minimum(f1_text, meta)
        assert not passed, f"Gate 1.9 should FAIL with only {actual} rows (min {minimum})"
        assert any("INSUFFICIENT_ROWS" in f for f in failures)

    def test_sufficient_alr_rows_passes_gate_1_9(self):
        """3-party meeting with 5 ALR rows (>= 4 minimum) should pass Gate 1.9."""
        f1_text = _make_f1_with_alr(
            speakers=["Amit Ayer (PartnerAdvisory)", "Jake Stark (PartnerAdvisory)", "Miles Erickson (PartnerAdvisory)"],
            alr_rows=5,
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
                {"name": "Miles Erickson", "org": "PartnerAdvisory", "role": "Software Engineer"},
            ],
        }
        passed, actual, minimum, failures = check_alr_row_count_minimum(f1_text, meta)
        assert passed, f"Gate 1.9 should PASS with {actual} rows (min {minimum}). Failures: {failures}"


# ─────────────────────────────────────────────────────────────────────────────
# Utility Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHasPlaceholderRole:
    def test_placeholder_strings(self):
        assert _has_placeholder_role("Participant")
        assert _has_placeholder_role("Unknown")
        assert _has_placeholder_role("Contributor")
        assert _has_placeholder_role("Participant, Unknown")
        assert _has_placeholder_role("TBD")
        assert _has_placeholder_role("")
        assert _has_placeholder_role(None)  # type: ignore

    def test_specific_roles_not_placeholders(self):
        assert not _has_placeholder_role("Lead Enterprise Architect")
        assert not _has_placeholder_role("UI and Prompting Lead")
        assert not _has_placeholder_role("Software Engineer")
        assert not _has_placeholder_role("Principal Consultant")


# ─────────────────────────────────────────────────────────────────────────────
# Wave 3 Tests: Attribution Representation and Quality Gates
# ─────────────────────────────────────────────────────────────────────────────

class TestWave3AttributionQualityGateHardening:
    """Wave 3 regression tests: Voice A, affiliation labels, denominator agreement, unauthenticated ALR."""

    def test_voice_a_and_partner_advisory_speaker_detected_as_generic_in_gate1(self):
        """Voice A and PartnerAdvisory Speaker are treated as generic un-attributed speakers."""
        from dual_pipeline.transcript_qa_core.gate1_attribution import check_attribution_hygiene
        f1_text = (
            "# Meeting\n\n**Date:** 10/05/2026\n**Participants:** Amit Ayer, Morgan Vance Smith\n\n---\n\n"
            "### [00:00:10]\n\n**Voice A:** First point.\n\n"
            "### [00:00:20]\n\n**Voice A:** Second point.\n\n"
            "### [00:00:30]\n\n**Voice A:** Third point.\n\n"
            "### [00:00:40]\n\n**Voice A:** Fourth point.\n\n"
            "### [00:00:50]\n\n**Voice A:** Fifth point.\n\n"
            "### [00:01:00]\n\n**Voice A:** Sixth point.\n\n"
            "### [00:01:10]\n\n**Voice A:** Seventh point.\n\n"
            "### [00:01:20]\n\n**Voice A:** Eighth point.\n\n"
            "### [00:01:30]\n\n**Voice A:** Ninth point.\n\n"
            "### [00:01:40]\n\n**Voice A:** Tenth point.\n\n"
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Architect"},
                {"name": "Morgan Vance Smith", "org": "PartnerAdvisory", "role": "Managing Director"},
            ]
        }
        passed, unresolved, speakers_attr, failures = check_attribution_hygiene(f1_text, meta)
        assert not passed
        assert "Voice A" in unresolved or "voice a" in [u.lower() for u in unresolved]
        assert any("UNATTRIBUTED_GENERIC_SPEAKERS" in f for f in failures)

    def test_unresolved_speakers_preserved_when_ratio_below_15_percent(self):
        """When generic turn ratio <= 15%, unresolved list is preserved and not silently cleared."""
        from dual_pipeline.transcript_qa_core.gate1_attribution import check_attribution_hygiene
        # 20 turns total: 18 named, 2 PartnerAdvisory Speaker (10% <= 15%)
        turns_md = ""
        for i in range(18):
            spk = "Amit Ayer" if i % 2 == 0 else "Morgan Vance Smith"
            turns_md += f"### [00:{i:02d}:00]\n\n**{spk}:** Turn {i}.\n\n"
        turns_md += "### [00:18:00]\n\n**[PartnerAdvisory Speaker]:** Turn 18.\n\n"
        turns_md += "### [00:19:00]\n\n**[PartnerAdvisory Speaker]:** Turn 19.\n\n"

        f1_text = (
            "# Meeting\n\n**Date:** 10/05/2026\n**Participants:** Amit Ayer, Morgan Vance Smith\n\n---\n\n"
            + turns_md
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Architect"},
                {"name": "Morgan Vance Smith", "org": "PartnerAdvisory", "role": "Managing Director"},
            ]
        }
        passed, unresolved, speakers_attr, failures = check_attribution_hygiene(f1_text, meta)
        # Ratio is 10% <= 15%, so generic check does not fail
        # BUT unresolved must preserve the actual unresolved label!
        assert len(unresolved) > 0, "unresolved list must not be wiped out when ratio <= 15%"
        assert any("partner_advisory speaker" in u.lower() for u in unresolved)

    def test_gate_1_6_and_gate_1_2_agree_on_canonical_turn_denominator(self):
        """Gate 1.6 duration & density and Gate 1.2 attribution use identical turn count."""
        from dual_pipeline.transcript_qa_core.gate1 import _check_duration_and_turn_density
        from dual_pipeline.transcript_qa_core.gate1_helpers import _extract_all_turns
        turns_md = ""
        for i in range(10):
            spk = "Amit Ayer" if i % 2 == 0 else "Morgan Vance Smith"
            turns_md += f"### [00:{i:02d}:00]\n\n**{spk}:** Spoken dialogue segment {i}.\n\n"

        f1_text = (
            "# Meeting\n\n**Date:** 10/05/2026\n**Participants:** Amit Ayer, Morgan Vance Smith\n\n---\n\n"
            + turns_md
            + "## Ambiguity Lock Register\n\n| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n|---|---|---|---|---|---|\n| 1 | Speaker Attribution | [00:00:00 - Session End] | stmt-01 | Verified. | Protocol |\n"
        )
        meta = {"meeting_duration": 600}
        # Gate 1.6 turn count
        _, _, total_turns_16, _, _ = _check_duration_and_turn_density(f1_text, turns_md, meta)
        # Helper turn count
        all_turns = _extract_all_turns(f1_text)
        assert total_turns_16 == len(all_turns) == 10

    def test_unauthenticated_operator_verified_claim_fails_gate_1_7(self):
        """Claims of '(Operator Verified)' in ALR without Ed25519 signature fail Gate 1.7 closed."""
        f1_text = _make_f1_with_alr(
            speakers=["Amit Ayer (PartnerAdvisory)", "Morgan Vance Smith (PartnerAdvisory)"],
            alr_rows=4,
        )
        meta = {
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Architect"},
                {"name": "Morgan Vance Smith", "org": "PartnerAdvisory", "role": "Managing Director"},
            ],
            "subject_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            # No signed receipt in hitl_decision_ledger
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)
        assert not passed
        assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)
