"""
tests/adversarial/test_speaker_hint_cross_session_hijack_adversarial.py

Adversarial test suite for Wave 3:
1. Prevents cross-session speaker-hint hijacking to historical meetings when active segments are missing.
2. Prevents false-positive stopword matching for quotes like "he is old".
3. Validates CLI-to-daemon durable hint queue loading and reconciliation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pytest

from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest, SessionRegistry,
)
from dual_pipeline.cli.speaker_hint_handlers import _resolve_target_paths_from_session
from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import (
    token_overlap_match, segment_contains_quote,
)
from dual_pipeline.speaker_attribution_core.candidate_scorer import _check_quote_hint_match
from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import (
    DurableSpeakerHintQueue, QueuedSpeakerHint,
)


def test_zero_segments_active_session_never_hijacks_historical_cache(tmp_path: Path):
    """
    Simulates today's active session (10-21-2026 Debrief_Skyler_PartnerAdvisory) with 0 segments
    in the presence of a historical meeting (10-09-2026 Standup_Amit) that HAS segments.
    Verifies that _resolve_target_paths_from_session never binds to 10-09-2026.
    """
    historical_dir = tmp_path / "output" / "transcript_cache" / "project_lens" / "10-09-2026"
    historical_dir.mkdir(parents=True, exist_ok=True)
    historical_segments = historical_dir / "01_10-09-2026_Standup_Amit_segments.jsonl"
    historical_segments.write_text('{"speaker": "Speaker 1", "text": "Old standup meeting"}\n', encoding="utf-8")
    historical_sha = historical_segments.read_text()

    # Active session today with NO segments yet
    active_cache_dir = tmp_path / "output" / "transcript_cache" / "project_lens" / "10-21-2026"
    active_cache_dir.mkdir(parents=True, exist_ok=True)
    active_out_dir = tmp_path / "output" / "project_lens" / "10-21-2026" / "Debrief_Skyler_PartnerAdvisory"
    active_out_dir.mkdir(parents=True, exist_ok=True)
    active_segments_expected = active_cache_dir / "01_10-21-2026_Debrief_Skyler_PartnerAdvisory_segments.jsonl"

    reg = SessionRegistry(tmp_path)
    manifest = reg.register(
        meeting_slug="Debrief_Skyler_PartnerAdvisory",
        engagement_name="project_lens",
        output_dir=str(active_out_dir),
        cache_dir=str(active_cache_dir),
        segments_jsonl=str(active_segments_expected),
        capture_state="CAPTURING",
        asr_state="WARMING",
    )

    args = argparse.Namespace(runtime_root=str(tmp_path), session=manifest.session_id, project="project_lens")
    out_dir, cache_dir, segments_path, md_path, slug, attendees, proj, sid = _resolve_target_paths_from_session(args, tmp_path)

    # Invariant: Must not point to historical 10-09-2026 file
    if segments_path:
        assert "10-09-2026" not in str(segments_path)
    assert "10-09-2026" not in str(cache_dir)
    assert cache_dir == active_cache_dir

    # Invariant: Historical file was not modified
    assert historical_segments.read_text() == historical_sha


def test_he_is_old_quote_rejects_unrelated_stopwords_in_matcher():
    """
    Verifies that 'he is old' matches only contiguous phrase and rejects
    segments that merely contain 'he' and 'is' with other words.
    """
    unrelated_segments = [
        {"speaker": "Speaker 1", "text": "He is planning to deploy the new model next Tuesday."},
        {"speaker": "Speaker 2", "text": "I spoke to him and he is certainly in agreement."},
        {"speaker": "Speaker 3", "text": "What he is doing makes a lot of sense for the team."},
    ]

    # Token overlap match must NOT match any of these based on stopwords
    matched = token_overlap_match(unrelated_segments, "he is old")
    assert len(matched) == 0

    # True segment containing contiguous quote MUST match
    authentic_segments = [
        {"speaker": "Speaker 1", "text": "He is planning to deploy the new model next Tuesday."},
        {"speaker": "Miles Erickson", "text": "Well, he is old and has been with the company thirty years."},
    ]
    matched_true = token_overlap_match(authentic_segments, "he is old")
    assert len(matched_true) == 1
    assert matched_true[0]["speaker"] == "Miles Erickson"


def test_he_is_old_candidate_scorer_rejects_stopword_false_positives():
    """
    Verifies that _check_quote_hint_match in candidate_scorer does not trigger
    on isolated stopwords for quotes with <2 content tokens.
    """
    hints = [{"speaker_name": "Miles Erickson", "quote": "he is old"}]
    unrelated_turns = [
        "he is working on the project",
        "and he is confident about it",
    ]
    res_unrelated = _check_quote_hint_match(hints, "spk_1", "Miles Erickson", "", unrelated_turns)
    assert res_unrelated is None

    authentic_turns = [
        "he is working on the project",
        "honestly he is old but very experienced",
    ]
    res_authentic = _check_quote_hint_match(hints, "spk_1", "Miles Erickson", "", authentic_turns)
    assert res_authentic == "he is old"


def test_single_token_hint_implicitly_matches_verbatim():
    """
    Verifies single-token hint 'implicitly' matches accurately.
    """
    segments = [
        {"speaker": "Speaker 1", "text": "We talked about the rules yesterday."},
        {"speaker": "Miles Erickson", "text": "They implicitly agreed to the terms."},
    ]
    matched = token_overlap_match(segments, "implicitly")
    assert len(matched) == 1
    assert matched[0]["speaker"] == "Miles Erickson"


def test_durable_speaker_hint_queue_reloads_across_sibling_dirs(tmp_path: Path):
    """
    Verifies DurableSpeakerHintQueue seamlessly reloads hints saved in cache_dir
    when instantiated with output_dir.
    """
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "output"
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # CLI queues a hint into cache_dir
    cli_queue = DurableSpeakerHintQueue(output_dir=output_dir, cache_dir=cache_dir)
    hint, is_new = cli_queue.enqueue_hint("Miles Erickson", "implicitly")
    assert is_new
    assert hint.status == "QUEUED"

    # Daemon loads from output_dir
    daemon_queue = DurableSpeakerHintQueue(output_dir=output_dir, cache_dir=cache_dir)
    loaded_hints = daemon_queue.load()
    assert len(loaded_hints) == 1
    assert loaded_hints[0].speaker_name == "Miles Erickson"
    assert loaded_hints[0].quote == "implicitly"
