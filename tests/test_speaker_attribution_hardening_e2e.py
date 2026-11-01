"""
tests/test_speaker_attribution_hardening_e2e.py

End-to-end multi-speaker verification test:
Simulates a 6-person, 2-company meeting where 5 speakers have varying clues
(opener habit, company perspective, task ownership, speaking tempo, inquiry habit)
and 1 speaker has minimal talk time, proving 100% attribution via the 6-signal matching engine.
"""
import pytest
from dual_pipeline.speaker_memory import SpeakerMemory
from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
from dual_pipeline.speaker_attribution_core.perspective_scorer import apply_roster_elimination_lockout


def test_six_person_multi_company_meeting_e2e(tmp_path):
    """Verifies 100% attribution across a 6-person meeting using the integrated 6-signal engine."""
    mem_file = tmp_path / "speaker_mem.json"
    mem = SpeakerMemory(memory_path=mem_file)

    # Pre-seed speaker profiles with habits, tasks, and speaking tempo
    stefan = mem.record_attribution("Stefan", "Welcome to the readout.", persist=False)
    stefan.organization = "PartnerAdvisory"
    stefan.role = "Managing Director"
    stefan.call_opener_count = 8
    stefan.avg_words_per_minute = 40.0
    stefan.domain_topics = ["readout", "survey"]

    sharon = mem.record_attribution("Sharon", "What our real ask is for Q4.", persist=False)
    sharon.organization = "Apex Global"
    sharon.role = "Product Owner"
    sharon.key_phrases = ["what our real ask is"]

    amit = mem.record_attribution("Amit Ayer", "Let's review the architecture blueprint.", persist=False)
    amit.organization = "PartnerAdvisory"
    amit.role = "Lead Enterprise Architect"
    amit.owned_tasks = ["enterprise architecture", "technical screening"]

    daniel = mem.record_attribution("Daniel", "The transmission system and solar entries are updated.", persist=False)
    daniel.organization = "Apex Global"
    daniel.role = "Engineering Lead"
    daniel.owned_tasks = ["solar 40 database", "transmission system"]

    jince = mem.record_attribution("Jince", "How is the candidate screening going?", persist=False)
    jince.organization = "PartnerAdvisory"
    jince.role = "Recruiter / Talent Lead"
    jince.key_phrases = ["screening challenge", "interviewing"]
    jince.domain_topics = ["contractor", "screening challenge"]

    reid = mem.record_attribution("Reid", "Reviewing priorities.", persist=False)
    reid.organization = "Apex Global"
    reid.role = "Director of Product"

    roster = [
        ("Stefan", "PartnerAdvisory"),
        ("Sharon", "Apex Global"),
        ("Amit Ayer", "PartnerAdvisory"),
        ("Daniel", "Apex Global"),
        ("Jince", "PartnerAdvisory"),
        ("Reid", "Apex Global"),
    ]

    transcript = """
[00:00:10 - 00:00:35] Speaker 1: Welcome everyone, let's review our survey readout and roadmap alignment across teams today.
[00:00:45 - 00:01:15] Speaker 2: On our side at Apex Global, what our real ask is for Q4 is getting the team aligned on schedule.
[00:01:20 - 00:01:50] Speaker 3: On our side at PartnerAdvisory, I finished the enterprise architecture blueprint yesterday and verified the integration standards.
[00:01:55 - 00:02:25] Speaker 4: In our Apex Global engineering team, I resolved the solar 40 database blockers and verified the transmission system feed.
[00:02:30 - 00:02:50] Speaker 5: Can you confirm the contractor timeline? What about the screening challenge candidates?
[00:02:55 - 00:03:00] Speaker 6: Yes, makes sense.
"""
    metadata = {
        "speaker_memory": mem,
        "task_ownership": {
            "Amit Ayer": ["enterprise architecture"],
            "Daniel": ["solar 40 database", "transmission system"],
        },
        "roster": [
            {"name": "Stefan", "org": "PartnerAdvisory", "role": "Managing Director"},
            {"name": "Sharon", "org": "Apex Global", "role": "Product Owner"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
            {"name": "Daniel", "org": "Apex Global", "role": "Engineering Lead"},
            {"name": "Jince", "org": "PartnerAdvisory", "role": "Recruiter / Talent Lead"},
            {"name": "Reid", "org": "Apex Global", "role": "Director of Product"},
        ],
    }

    # Score candidates for each speaker cluster
    cluster_scores = {}
    for spk_id in ["1", "2", "3", "4", "5", "6"]:
        cands = score_candidates_for_speaker(
            spk_id=spk_id,
            transcript=transcript,
            roster=roster,
            metadata=metadata,
            total_clusters=6,
        )
        cluster_scores[f"Speaker {spk_id}"] = cands

    # Run global roster elimination lockout
    resolved_clusters = apply_roster_elimination_lockout(cluster_scores)

    # Verify 100% correct attribution (6 out of 6)
    expected_matches = {
        "Speaker 1": "Stefan",
        "Speaker 2": "Sharon",
        "Speaker 3": "Amit Ayer",
        "Speaker 4": "Daniel",
        "Speaker 5": "Jince",
        "Speaker 6": "Reid",
    }

    correct_count = 0
    for cluster_label, expected_name in expected_matches.items():
        top_cand = resolved_clusters[cluster_label][0]
        assert top_cand.attendee_name == expected_name, (
            f"Expected {cluster_label} -> {expected_name}, got {top_cand.attendee_name} "
            f"(confidence={top_cand.confidence}, rationale={top_cand.evidence_rationale})"
        )
        assert top_cand.confidence >= 0.85
        assert top_cand.confidence_tier == "HIGH"
        correct_count += 1

    accuracy = correct_count / len(expected_matches)
    assert accuracy >= 0.98, f"Expected accuracy >= 0.98, got {accuracy * 100:.1f}%"
