"""
End-to-End multi-meeting speaker attribution test based on learned style, vocabulary, and interlocutors.
Wave 3 verification suite.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
from dual_pipeline.speaker_memory import SpeakerMemory


def test_consecutive_meeting_speaker_style_learning_e2e() -> None:
    """
    Simulates consecutive meetings:
    Meeting 1: Speaker attributions confirmed; system learns habits and style.
    Meeting 2: Generic speakers disambiguated automatically using learned style without operator hints.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / "runtime_speaker_memory.json"
        memory = SpeakerMemory(memory_path=memory_path)

        # -------------------------------------------------------------
        # MEETING 1: Attributions confirmed, learning style patterns
        # -------------------------------------------------------------
        # Turn 1: Marcus Vance opens meeting
        m1_t1 = "Let's review the telemetry data. Our Kubernetes ingress cluster had significant latency yesterday."
        memory.record_attribution("Marcus Vance", m1_t1, topics=["kubernetes", "telemetry"], interlocutor=None)

        # Turn 2: Elena Rostova follows Marcus
        m1_t2 = "Regarding that latency spike, the PostgreSQL connection pool became exhausted during migration."
        memory.record_attribution("Elena Rostova", m1_t2, topics=["database", "postgresql"], interlocutor="Marcus Vance")

        # Turn 3: Marcus responds to Elena
        m1_t3 = "Understood. We need to increase the replica connection pool count in the Helm values."
        memory.record_attribution("Marcus Vance", m1_t3, interlocutor="Elena Rostova")

        # Turn 4: Elena confirms
        m1_t4 = "I will submit the pull request for the Helm chart configuration update today."
        memory.record_attribution("Elena Rostova", m1_t4, interlocutor="Marcus Vance")

        # Verify memory learned habits
        marcus_prof = memory.get_profile("Marcus Vance")
        elena_prof = memory.get_profile("Elena Rostova")

        assert marcus_prof is not None
        assert "let's review" in marcus_prof.opener_phrases
        assert "kubernetes" in marcus_prof.common_vocabulary
        assert "Elena Rostova" in marcus_prof.frequent_interlocutors

        assert elena_prof is not None
        assert "postgresql" in elena_prof.common_vocabulary
        assert "Marcus Vance" in elena_prof.frequent_interlocutors

        # -------------------------------------------------------------
        # MEETING 2: Consecutive meeting, unresolved generic clusters
        # -------------------------------------------------------------
        # Acoustic similarity is tied at 0.50. Zero quote hints provided.
        # Only learned style, pacing, vocabulary, and interlocutor patterns distinguish them.
        m2_transcript = "\n\n".join([
            "**Speaker 1** [00:00:01]: Let's review the telemetry data from this morning's run.",
            "**Speaker 2** [00:00:15]: The PostgreSQL connection pool maintained zero latency drops.",
        ])

        roster = [("Marcus Vance", "PartnerAdvisory"), ("Elena Rostova", "Client Partner")]
        metadata: Dict[str, Any] = {
            "speaker_memory": memory,
            "attendees": ["Marcus Vance", "Elena Rostova"],
        }

        # Score Speaker 1
        cands_spk1 = score_candidates_for_speaker(
            "Speaker 1", m2_transcript, roster, metadata, total_clusters=2
        )
        top_spk1 = cands_spk1[0]
        assert top_spk1.attendee_name == "Marcus Vance"
        assert top_spk1.confidence >= 0.70
        assert "Learned opener phrase match" in top_spk1.evidence_rationale
        assert "Learned vocabulary alignment" in top_spk1.evidence_rationale

        # Score Speaker 2
        cands_spk2 = score_candidates_for_speaker(
            "Speaker 2", m2_transcript, roster, metadata, total_clusters=2
        )
        top_spk2 = cands_spk2[0]
        assert top_spk2.attendee_name == "Elena Rostova"
        assert top_spk2.confidence >= 0.65
        assert "Learned vocabulary alignment" in top_spk2.evidence_rationale
