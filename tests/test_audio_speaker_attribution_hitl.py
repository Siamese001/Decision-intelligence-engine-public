"""Unit tests for audio speaker attribution, role discrepancy detection, and HITL gating."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.speaker_attribution_core import (
    AudioSpeakerAttributionEngine,
    SpeakerAttributionCollisionError,
)
from dual_pipeline.speaker_attribution_core.audio_uncertainty_detector import (
    detect_audio_role_discrepancies,
    extract_roster_with_roles,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


def test_extract_roster_with_roles():
    metadata = {
        "attendees": {
            "Apex Global Enterprises": [
                "Marcus Balk (Enterprise Architect)",
                "David Childers (Scrum Master)",
            ],
            "PartnerAdvisory": [
                "Amit Ayer (Lead Enterprise Architect)",
                "Jen Pan (Principal Consultant)",
            ],
        }
    }
    roster = extract_roster_with_roles(metadata)
    assert len(roster) == 4
    names = [r["name"] for r in roster]
    assert "David Childers" in names
    assert "Amit Ayer" in names
    roles = {r["name"]: r["role"] for r in roster}
    assert roles["David Childers"] == "Scrum Master"
    assert roles["Amit Ayer"] == "Lead Enterprise Architect"


def test_detect_audio_role_discrepancies():
    transcript = (
        "### [00:00:16]\n\n"
        "**Amit Ayer:** I'll show on the next slide. We're shooting for everything to get into UAT.\n\n"
        "### [00:00:48]\n\n"
        "**Amit Ayer:** So, from a PM standpoint, you know, looking at the roadmap, we're where we thought we were going to be.\n\n"
        "### [00:02:08]\n\n"
        "**Amit Ayer:** This is our buffer here Brian, there's nothing else lined up here, this is our sole focus.\n\n"
        "### [00:04:08]\n\n"
        "**Amit Ayer:** The other one was very close. We still have to do our diligence on the kicking off.\n"
    )
    metadata = {
        "attendees": {
            "Apex Global Enterprises": [
                "David Childers (Scrum Master)",
                "Speaker_Alpha Muschett (Enterprise Architect)",
            ],
            "PartnerAdvisory": [
                "Amit Ayer (Lead Enterprise Architect)",
                "Jen Pan (Principal Consultant)",
            ],
        },
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }
    discrepancies = detect_audio_role_discrepancies(transcript, metadata)
    assert len(discrepancies) >= 1
    disc = discrepancies[0]
    assert disc.current_speaker == "Amit Ayer"
    assert disc.detected_role == "pm_scrum_master"
    assert disc.is_ambiguous is True
    assert len(disc.candidates) >= 2
    cand_names = [c.attendee_name for c in disc.candidates]
    assert "David Childers" in cand_names or "Jen Pan" in cand_names


def test_hitl_gating_blocks_on_ambiguous_role_discrepancy(tmp_path):
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    transcript = (
        "### [00:00:48]\n\n"
        "**Amit Ayer:** So, from a PM standpoint, you know, looking at the roadmap, we're where we thought we were going to be.\n"
    )
    metadata = {
        "attendees": {
            "Apex Global Enterprises": ["David Childers (Scrum Master)"],
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)", "Jen Pan (Principal Consultant)"],
        },
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }

    # Should raise HITLRequiredError because margin between David Childers and Jen Pan is 0.0 (< 0.25)
    with pytest.raises(HITLRequiredError) as exc_info:
        engine.analyze_and_resolve(transcript, metadata)

    assert "L1 ATTRIBUTION GATE BLOCKED" in str(exc_info.value)
    assert "Amit Ayer" in str(exc_info.value)


def test_interactive_resolver_resolves_discrepancy(tmp_path):
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    transcript = (
        "### [00:00:48]\n\n"
        "**Amit Ayer:** So, from a PM standpoint, you know, looking at the roadmap, we're where we thought we were going to be.\n"
    )
    metadata = {
        "attendees": {
            "Apex Global Enterprises": ["David Childers (Scrum Master)"],
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)"],
        },
        "source_type": "AUDIO_ONLY_UNDIARIZED",
        "date_filename": "09182026",
    }

    def dummy_resolver(prompt: str, options: list[str]) -> str:
        return "David Childers"

    result = engine.analyze_and_resolve(transcript, metadata, interactive_resolver=dummy_resolver)
    assert result.turn_reassignments.get("Amit Ayer") == "David Childers"


def test_generic_speaker_uses_prior_ledger_resolution(tmp_path):
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    store.record_decision(
        decision_id="speaker_attribution_speaker_1_09172026",
        category="speaker_attribution",
        prompt="Attribute Speaker 1",
        options=["Jennifer Pan", "harper Hoffman", "Amit Ayer"],
        selected_option="Jennifer Pan",
        metadata={"cluster": "Speaker 1", "project": "project_lens"},
    )

    engine = AudioSpeakerAttributionEngine(decision_store=store)
    transcript = (
        "**Speaker 1** [00:00:00]: We as engineers tend to be conservative.\n"
        "**Speaker 1** [00:06:37]: Hey, Mandy!\n"
    )
    metadata = {
        "attendees": {
            "PartnerAdvisory": ["Jennifer Pan (Technical Lead)", "Amit Ayer (Lead Enterprise Architect)"],
            "Apex Global Enterprises": ["harper Hoffman (Product Owner)"],
        },
        "project": "project_lens",
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }

    result = engine.analyze_and_resolve(transcript, metadata, autonomous_if_decisive=True)
    assert result.speaker_map.get("Speaker 1") == "Jennifer Pan"
    assert "**Jennifer Pan:** [00:00:00]: We as engineers" in result.resolved_transcript


def test_generic_speaker_interactive_resolver(tmp_path):
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    transcript = (
        "**Speaker 1** [00:00:00]: We as engineers tend to be conservative.\n"
    )
    metadata = {
        "attendees": {
            "PartnerAdvisory": ["Jennifer Pan (Technical Lead)", "Amit Ayer (Lead Enterprise Architect)"],
            "Apex Global Enterprises": ["harper Hoffman (Product Owner)"],
        },
        "project": "project_lens",
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }

    def resolver(prompt: str, options: list[str]) -> str:
        return "Jennifer Pan"

    result = engine.analyze_and_resolve(transcript, metadata, interactive_resolver=resolver)
    assert result.speaker_map.get("Speaker 1") == "Jennifer Pan"
    assert "**Jennifer Pan:** [00:00:00]:" in result.resolved_transcript


def test_injective_mapping_excludes_assigned_attendees_and_deduces_final_cluster(tmp_path):
    """Verifies that assigned attendees are excluded from downstream candidates and bijective deduction triggers for sole remaining attendee."""
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    store.record_decision(
        decision_id="speaker_attribution_speaker_1_09172026",
        category="speaker_attribution",
        prompt="Attribute Speaker 1",
        options=["Jennifer Pan", "harper Hoffman", "Amit Ayer"],
        selected_option="Jennifer Pan",
        metadata={"cluster": "Speaker 1", "project": "project_lens"},
    )
    store.record_decision(
        decision_id="speaker_attribution_speaker_2_09172026",
        category="speaker_attribution",
        prompt="Attribute Speaker 2",
        options=["harper Hoffman", "Amit Ayer", "Jennifer Pan"],
        selected_option="harper Hoffman",
        metadata={"cluster": "Speaker 2", "project": "project_lens"},
    )

    engine = AudioSpeakerAttributionEngine(decision_store=store)
    transcript = (
        "**Speaker 1** [00:00:00]: We as engineers tend to be conservative.\n"
        "**Speaker 2** [00:01:46]: I moved our UX review meeting.\n"
        "**Speaker 3** [00:08:15]: I think that's what we had estimated.\n"
    )
    metadata = {
        "attendees": {
            "PartnerAdvisory": ["Jennifer Pan (Technical Lead)", "Amit Ayer (Lead Enterprise Architect)"],
            "Apex Global Enterprises": ["harper Hoffman (Product Owner)"],
        },
        "project": "project_lens",
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }

    # Autonomous resolution must deduce Speaker 3 -> Amit Ayer with 100% confidence by bijective deduction
    result = engine.analyze_and_resolve(transcript, metadata, autonomous_if_decisive=True)
    assert result.speaker_map.get("Speaker 1") == "Jennifer Pan"
    assert result.speaker_map.get("Speaker 2") == "harper Hoffman"
    assert result.speaker_map.get("Speaker 3") == "Amit Ayer"
    assert "**Amit Ayer:** [00:08:15]:" in result.resolved_transcript


def test_duplicate_speaker_attribution_fails_closed(tmp_path):
    """Verifies that assigning an already assigned attendee raises SpeakerAttributionCollisionError."""
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    store.record_decision(
        decision_id="speaker_attribution_speaker_1_09172026",
        category="speaker_attribution",
        prompt="Attribute Speaker 1",
        options=["Jennifer Pan", "harper Hoffman", "Amit Ayer"],
        selected_option="Jennifer Pan",
        metadata={"cluster": "Speaker 1", "project": "project_lens"},
    )

    engine = AudioSpeakerAttributionEngine(decision_store=store)
    transcript = (
        "**Speaker 1** [00:00:00]: We as engineers tend to be conservative.\n"
        "**Speaker 3** [00:08:15]: I think that's what we had estimated.\n"
    )
    metadata = {
        "attendees": {
            "PartnerAdvisory": ["Jennifer Pan (Technical Lead)", "Amit Ayer (Lead Enterprise Architect)"],
            "Apex Global Enterprises": ["harper Hoffman (Product Owner)"],
        },
        "project": "project_lens",
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }

    # Force resolver to return Jennifer Pan for Speaker 3 (duplicate assignment)
    def colliding_resolver(prompt: str, options: list[str]) -> str:
        return "Jennifer Pan"

    with pytest.raises(SpeakerAttributionCollisionError) as exc_info:
        engine.analyze_and_resolve(transcript, metadata, interactive_resolver=colliding_resolver)

    assert "Duplicate speaker attribution detected" in str(exc_info.value)
    assert "Injective mapping violated" in str(exc_info.value)


def test_stale_ledger_collision_ignored_under_injective_mapping(tmp_path):
    """Verifies that a prior ledger entry violating injective mapping is ignored."""
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    store.record_decision(
        decision_id="speaker_attribution_speaker_1_09172026",
        category="speaker_attribution",
        prompt="Attribute Speaker 1",
        options=["Jennifer Pan", "harper Hoffman", "Amit Ayer"],
        selected_option="Jennifer Pan",
        metadata={"cluster": "Speaker 1", "project": "project_lens"},
    )
    store.record_decision(
        decision_id="speaker_attribution_speaker_2_09172026",
        category="speaker_attribution",
        prompt="Attribute Speaker 2",
        options=["harper Hoffman", "Amit Ayer", "Jennifer Pan"],
        selected_option="harper Hoffman",
        metadata={"cluster": "Speaker 2", "project": "project_lens"},
    )
    # Defective historical record assigning Speaker 3 to Jennifer Pan
    store.record_decision(
        decision_id="speaker_attribution_speaker_3_09172026",
        category="speaker_attribution",
        prompt="Attribute Speaker 3",
        options=["Amit Ayer", "harper Hoffman", "Jennifer Pan"],
        selected_option="Jennifer Pan",
        metadata={"cluster": "Speaker 3", "project": "project_lens"},
    )

    engine = AudioSpeakerAttributionEngine(decision_store=store)
    transcript = (
        "**Speaker 1** [00:00:00]: We as engineers tend to be conservative.\n"
        "**Speaker 2** [00:01:46]: I moved our UX review meeting.\n"
        "**Speaker 3** [00:08:15]: I think that's what we had estimated.\n"
    )
    metadata = {
        "attendees": {
            "PartnerAdvisory": ["Jennifer Pan (Technical Lead)", "Amit Ayer (Lead Enterprise Architect)"],
            "Apex Global Enterprises": ["harper Hoffman (Product Owner)"],
        },
        "project": "project_lens",
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }

    # Stale record for Speaker 3 is ignored because Jennifer Pan is already assigned to Speaker 1.
    # Bijective deduction correctly deduces Speaker 3 -> Amit Ayer.
    result = engine.analyze_and_resolve(transcript, metadata, autonomous_if_decisive=True)
    assert result.speaker_map.get("Speaker 3") == "Amit Ayer"


def test_held_out_cross_specialty_discussion_blocks_without_hitl(tmp_path):
    """Verifies that an attendee discussing another attendee's specialty blocks for HITL review."""
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    # Lead Enterprise Architect discussing PM/Scrum backlog and sprints
    transcript = (
        "### [00:01:15]\n\n"
        "**Amit Ayer:** So from a PM standpoint, we need to groom these user stories and align sprint velocity.\n"
    )
    metadata = {
        "attendees": {
            "Apex Global Enterprises": ["David Childers (Scrum Master)"],
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)", "Jen Pan (Principal Consultant)"],
        },
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }

    # Must require HITL because speaker role is Enterprise Architect but discourse is PM/Scrum
    with pytest.raises(HITLRequiredError) as exc_info:
        engine.analyze_and_resolve(transcript, metadata)
    assert "L1 ATTRIBUTION GATE BLOCKED" in str(exc_info.value)


def test_held_out_short_affirmations_preserve_ambiguity(tmp_path):
    """Verifies that short conversational affirmations ('Yeah', 'Right') do not force false role attribution."""
    metadata = {
        "attendees": {
            "Apex Global Enterprises": ["David Childers (Scrum Master)"],
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)"],
        },
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }
    # Short affirmation turn
    transcript = (
        "### [00:03:00]\n\n"
        "**Speaker 1:** Yeah, exactly, right.\n"
    )
    discrepancies = detect_audio_role_discrepancies(transcript, metadata)
    # Short affirmations do not contain role keywords, so no role discrepancy is falsely asserted
    assert len(discrepancies) == 0


def test_held_out_shared_room_overlap_requires_hitl(tmp_path):
    """Verifies that shared-room overlap with ambiguous candidates requires HITL resolution."""
    ledger_file = tmp_path / "test_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_file)
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    transcript = (
        "### [00:05:00]\n\n"
        "**Speaker 1:** We're reviewing the architecture from the conference room.\n"
        "### [00:05:30]\n\n"
        "**Speaker 2:** From a PM standpoint, the timeline is tight.\n"
    )
    metadata = {
        "attendees": {
            "PartnerAdvisory": [
                "Amit Ayer (Lead Enterprise Architect)",
                "Jen Pan (Principal Consultant)",
            ],
            "Apex Global Enterprises": [
                "David Childers (Scrum Master)",
                "harper Hoffman (Product Owner)",
            ],
        },
        "source_type": "AUDIO_ONLY_UNDIARIZED",
    }

    # Interactive resolution allows disambiguation
    def interactive_resolver(prompt: str, options: list[str]) -> str:
        if "Speaker Attribution for Speaker 1" in prompt or "Candidate Cluster**: `Speaker 1`" in prompt or ("Speaker 1" in prompt and "Speaker 2" not in prompt):
            return "Amit Ayer"
        return "David Childers"

    res = engine.analyze_and_resolve(transcript, metadata, interactive_resolver=interactive_resolver)
    assert res.speaker_map.get("Speaker 1") == "Amit Ayer"
    assert res.speaker_map.get("Speaker 2") == "David Childers"


class AudioSpeakerAttributionHitlTests(unittest.TestCase):
    def test_extract_roster_with_roles(self):
        test_extract_roster_with_roles()

    def test_detect_audio_role_discrepancies(self):
        test_detect_audio_role_discrepancies()

    def test_hitl_gating_blocks_on_ambiguous_role_discrepancy(self):
        with tempfile.TemporaryDirectory() as td:
            test_hitl_gating_blocks_on_ambiguous_role_discrepancy(Path(td))

    def test_interactive_resolver_resolves_discrepancy(self):
        with tempfile.TemporaryDirectory() as td:
            test_interactive_resolver_resolves_discrepancy(Path(td))

    def test_generic_speaker_uses_prior_ledger_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            test_generic_speaker_uses_prior_ledger_resolution(Path(td))

    def test_generic_speaker_interactive_resolver(self):
        with tempfile.TemporaryDirectory() as td:
            test_generic_speaker_interactive_resolver(Path(td))

    def test_injective_mapping_excludes_assigned_attendees_and_deduces_final_cluster(self):
        with tempfile.TemporaryDirectory() as td:
            test_injective_mapping_excludes_assigned_attendees_and_deduces_final_cluster(Path(td))

    def test_duplicate_speaker_attribution_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            test_duplicate_speaker_attribution_fails_closed(Path(td))

    def test_stale_ledger_collision_ignored_under_injective_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            test_stale_ledger_collision_ignored_under_injective_mapping(Path(td))

    def test_held_out_cross_specialty_discussion_blocks_without_hitl(self):
        with tempfile.TemporaryDirectory() as td:
            test_held_out_cross_specialty_discussion_blocks_without_hitl(Path(td))

    def test_held_out_short_affirmations_preserve_ambiguity(self):
        with tempfile.TemporaryDirectory() as td:
            test_held_out_short_affirmations_preserve_ambiguity(Path(td))

    def test_held_out_shared_room_overlap_requires_hitl(self):
        with tempfile.TemporaryDirectory() as td:
            test_held_out_shared_room_overlap_requires_hitl(Path(td))


if __name__ == "__main__":
    unittest.main()
