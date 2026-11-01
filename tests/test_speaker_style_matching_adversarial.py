"""
Adversarial and edge-case test suite for speaker style, vocabulary, and habit matching.
Wave 3 verification suite.
Covers 5 categories:
1. Boundary payload (empty turns, 1-word affirmations, punctuation only)
2. Malformed input (corrupted JSON, negative turn counts, missing attributes)
3. Look-alike speakers (shared colloquialisms but distinctive technical vocabulary)
4. Concurrency and race (16 parallel threads updating speaker memory)
5. Idempotent recovery (repeated replay of identical attributions)
"""

from __future__ import annotations

import concurrent.futures
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
from dual_pipeline.speaker_memory import SpeakerMemory, SpeakerProfile


def test_adversarial_boundary_empty_and_one_word_turns() -> None:
    """
    Category: Boundary payload.
    Empty turns, punctuation-only turns, and 1-word affirmations must not crash,
    must not learn empty opener phrases, and must not pollute vocabulary.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / "test_memory.json"
        memory = SpeakerMemory(memory_path=memory_path)

        # 1-word affirmation
        memory.record_attribution("Sam", "Yeah.")
        # Punctuation only
        memory.record_attribution("Sam", "...???")
        # Empty string
        memory.record_attribution("Sam", "")
        # Common backchannel
        memory.record_attribution("Sam", "Okay.")

        prof = memory.get_profile("Sam")
        assert prof is not None
        # Must not learn single-word openers
        assert len(prof.opener_phrases) == 0
        # Stopwords like 'yeah', 'okay' must not be added to common vocabulary
        assert "yeah" not in prof.common_vocabulary
        assert "okay" not in prof.common_vocabulary
        assert "" not in prof.common_vocabulary

        # Match score against empty turn must return 0.0 without division by zero
        score_empty = memory.match_speaker_style_score("Sam", "")
        assert score_empty["total"] == 0.0


def test_adversarial_malformed_memory_data() -> None:
    """
    Category: Malformed input.
    Memory files with missing profile fields, corrupted types, or partial JSON
    must load gracefully without crashing the pipeline.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / "corrupted_memory.json"
        corrupted_payload = {
            "schema_version": "1.0.0",
            "profiles": {
                "broken_user": {
                    "name": "Broken User",
                    # missing person_id, missing lists and dicts
                    "confirmed_turn_count": -5,
                    "avg_words_per_turn": "not_a_number",  # malformed type handled gracefully
                }
            },
        }
        # In this test we write valid JSON with broken schema contents
        memory_path.write_text(json.dumps(corrupted_payload), encoding="utf-8")

        memory = SpeakerMemory(memory_path=memory_path)
        prof = memory.get_profile("Broken User")
        assert prof is not None
        assert isinstance(prof.opener_phrases, list)
        assert isinstance(prof.common_vocabulary, dict)

        # Candidate scoring against corrupted profile must not crash
        transcript = "**Speaker 1** [00:00:01]: Hello everyone."
        roster = [("Broken User", "Org"), ("Valid User", "Org")]
        metadata: Dict[str, Any] = {"speaker_memory": memory, "attendees": ["Broken User", "Valid User"]}
        cands = score_candidates_for_speaker("Speaker 1", transcript, roster, metadata, total_clusters=2)
        assert len(cands) == 2


def test_adversarial_look_alike_speakers_distinguished() -> None:
    """
    Category: Exact production regression / Look-alike disambiguation.
    Two speakers who both use 'Good morning everyone' are distinguished by their
    domain-specific technical vocabulary.
    """
    memory = SpeakerMemory()
    memory._profiles["alice dev"] = SpeakerProfile(
        name="Alice Dev",
        person_id="person:alice",
        opener_phrases=["good morning everyone"],
        common_vocabulary={"kubernetes": 10, "ingress": 8, "docker": 6},
    )
    memory._profiles["bob finance"] = SpeakerProfile(
        name="Bob Finance",
        person_id="person:bob",
        opener_phrases=["good morning everyone"],
        common_vocabulary={"quarterly": 10, "revenue": 8, "ebitda": 6},
    )

    transcript = (
        "**Speaker 1** [00:00:01]: Good morning everyone. Let us review the quarterly revenue and ebitda margins."
    )
    roster = [("Alice Dev", "Tech"), ("Bob Finance", "Finance")]
    metadata: Dict[str, Any] = {"speaker_memory": memory, "attendees": ["Alice Dev", "Bob Finance"]}

    cands = score_candidates_for_speaker("Speaker 1", transcript, roster, metadata, total_clusters=2)
    top_cand = cands[0]
    assert top_cand.attendee_name == "Bob Finance"
    assert top_cand.confidence > next(c.confidence for c in cands if c.attendee_name == "Alice Dev")


def test_adversarial_concurrency_16_workers() -> None:
    """
    Category: Concurrency & race.
    16 parallel workers concurrently updating speaker profiles against the same file.
    Must finish without unhandled race exceptions or corrupt files.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / "concurrent_memory.json"
        memory = SpeakerMemory(memory_path=memory_path)

        def worker_task(worker_id: int) -> None:
            for i in range(5):
                name = f"Speaker_{worker_id % 4}"
                utterance = f"Thread {worker_id} iteration {i} running distributed sync with cluster nodes."
                memory.record_attribution(name, utterance, interlocutor=f"Speaker_{(worker_id + 1) % 4}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker_task, w) for w in range(16)]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

        # Reload from disk into fresh instance and verify JSON integrity
        reloaded = SpeakerMemory(memory_path=memory_path)
        for w in range(4):
            prof = reloaded.get_profile(f"Speaker_{w}")
            assert prof is not None
            assert prof.confirmed_turn_count > 0


def test_adversarial_idempotent_replay_bounds() -> None:
    """
    Category: Rollback & recovery / Idempotent bounds.
    Repeating the same utterance 50 times in a row must respect maximum bounds
    for opener phrases (<= 15) and vocabulary (<= 30 words), preventing memory leaks.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = Path(tmpdir) / "idempotent_memory.json"
        memory = SpeakerMemory(memory_path=memory_path)

        for _ in range(50):
            memory.record_attribution(
                "Dana",
                "Let's get started, team. We need to deploy the infrastructure pipeline and verify the cluster nodes.",
            )

        prof = memory.get_profile("Dana")
        assert prof is not None
        assert len(prof.opener_phrases) <= 15
        assert len(prof.common_vocabulary) <= 30
        assert prof.avg_words_per_turn > 0
