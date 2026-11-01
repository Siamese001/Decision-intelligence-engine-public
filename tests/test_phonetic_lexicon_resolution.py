"""Tests for Wave 3: Phonetic Lexicon Biasing and Silence Hallucination Quarantine.

Verifies:
1. Speaker identity resolution for phonetic distortions ('Alston Cassio' -> 'Alex Sanchez').
2. Dialogue text normalization for domain and architecture terms ('Sloan' -> 'PartnerAdvisory', 'F3' -> 'AWS S3', 'one-turban' -> 'one turbine').
3. Chunk transcriber hallucination quarantine for standalone silence artifacts ('Thank you.', 'The end.').
"""
from __future__ import annotations

import pytest

from dual_pipeline.identity_resolver_core.resolver import IdentityResolver
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _filter_hallucinations,
    _is_standalone_silence_hallucination,
)


class TestPhoneticLexiconResolution:
    """Wave 3 verification suite for phonetic repairs and silence filtering."""

    @pytest.fixture
    def resolver(self) -> IdentityResolver:
        return IdentityResolver()

    def test_resolve_speaker_alston_cassio_alias(self, resolver: IdentityResolver) -> None:
        """Alston Cassio resolves to Alex Sanchez (PartnerAdvisory)."""
        res = resolver.resolve_speaker("Alston Cassio")
        assert res.status == "RESOLVED"
        assert res.canonical_name == "Alex Sanchez"
        assert res.organization == "PartnerAdvisory"
        assert res.confidence == 1.0

    def test_resolve_speaker_alston_first_name(self, resolver: IdentityResolver) -> None:
        """Alston resolves to Alex Sanchez (PartnerAdvisory)."""
        res = resolver.resolve_speaker("Alston")
        assert res.status == "RESOLVED"
        assert res.canonical_name == "Alex Sanchez"

    def test_resolve_speaker_cassio_alias(self, resolver: IdentityResolver) -> None:
        """Cassio resolves to Alex Sanchez (PartnerAdvisory)."""
        res = resolver.resolve_speaker("Cassio")
        assert res.status == "RESOLVED"
        assert res.canonical_name == "Alex Sanchez"

    def test_normalize_phonetic_dialogue_attendee_distortion(self, resolver: IdentityResolver) -> None:
        """Phonetic distortion 'I'm Alston Cassio' normalizes to 'I'm Alex Sanchez'."""
        lines = [(1, "Alex Sanchez", "I'm Alston Cassio.")]
        norm, repairs, _ = resolver.normalize_phonetic_dialogue(lines)

        assert len(norm) == 1
        assert norm[0][2] == "I'm Alex Sanchez."
        assert len(repairs) >= 1
        assert repairs[0].canonical_text == "Alex Sanchez"
        assert repairs[0].category == "attendee_identity"
        assert repairs[0].confidence >= 0.95

    def test_normalize_phonetic_dialogue_organization_and_architecture(self, resolver: IdentityResolver) -> None:
        """Normalizes 'Sloan' -> 'PartnerAdvisory' and 'F3' -> 'AWS S3'."""
        lines = [
            (10, "Jake Stark", "We have Sloan working on the pipeline."),
            (11, "Alex Sanchez", "The raw images land in F3 storage."),
        ]
        norm, repairs, _ = resolver.normalize_phonetic_dialogue(lines)

        assert norm[0][2] == "We have PartnerAdvisory working on the pipeline."
        assert norm[1][2] == "The raw images land in AWS S3 storage."
        categories = {r.category for r in repairs}
        assert "organization_lexicon" in categories
        assert "architecture_lexicon" in categories

    def test_normalize_phonetic_dialogue_turbine_denominator(self, resolver: IdentityResolver) -> None:
        """Normalizes 'one-turban' and 'One-Turban' to 'one turbine'."""
        lines = [
            (20, "Amit Ayer", "Evaluating accuracy on a One-Turban sample is flawed."),
            (21, "Jake Stark", "Right, not just a one-turban batch."),
        ]
        norm, repairs, _ = resolver.normalize_phonetic_dialogue(lines)

        assert norm[0][2] == "Evaluating accuracy on a one turbine sample is flawed."
        assert norm[1][2] == "Right, not just a one turbine batch."
        assert any(r.category == "domain_lexicon" for r in repairs)

    def test_standalone_silence_hallucination_detection(self) -> None:
        """Detects formulaic standalone hallucinations on silent chunks."""
        assert _is_standalone_silence_hallucination("Thank you.") is True
        assert _is_standalone_silence_hallucination("Thank you") is True
        assert _is_standalone_silence_hallucination("Thanks.") is True
        assert _is_standalone_silence_hallucination("The end.") is True
        assert _is_standalone_silence_hallucination("Thank you very much.") is True
        assert _is_standalone_silence_hallucination("You.") is True
        assert _is_standalone_silence_hallucination("Bye.") is True

        # Non-standalone substantive dialogue is NOT quarantined
        assert _is_standalone_silence_hallucination("Thank you for walking us through the architecture.") is False
        assert _is_standalone_silence_hallucination("Thanks, Jake, that makes sense.") is False

    def test_filter_hallucinations_quarantines_standalone_silence(self) -> None:
        """Filters out standalone silence hallucinations while keeping substantive sentences."""
        assert _filter_hallucinations("Thank you.") == ""
        assert _filter_hallucinations("The end.") == ""
        assert _filter_hallucinations("Thanks.") == ""

        substantive = "Thank you for joining the architecture sync today."
        assert _filter_hallucinations(substantive) == substantive
