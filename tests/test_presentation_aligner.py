"""
tests/test_presentation_aligner.py — Unit tests for transcript-presentation alignment engine.
"""
from __future__ import annotations

from dual_pipeline.pptx_source import PPTXPresentation, PPTXSlide
from dual_pipeline.presentation_aligner import (
    PresentationAlignment,
    _extract_slide_numbers_from_turn,
    align_transcript_presentation,
    inject_slide_annotations,
    parse_transcript_turns,
)


def _make_dummy_presentation() -> PPTXPresentation:
    """Build a lightweight dummy PPTXPresentation for testing."""
    s1 = PPTXSlide(
        slide_number=1,
        slide_target="ppt/slides/slide1.xml",
        title="Executive Strategic Vision",
        paragraphs=["Welcome and kick off to Florida 2027."],
        tables=[],
        speaker_notes="Set the overall strategic posture.",
    )
    s2 = PPTXSlide(
        slide_number=2,
        slide_target="ppt/slides/slide2.xml",
        title="Operational Run Rate",
        paragraphs=["Florida annualized revenue run rate reached 84 million.", "Growth exceeded 30%."],
        tables=[],
        speaker_notes="",
    )
    s3 = PPTXSlide(
        slide_number=3,
        slide_target="ppt/slides/slide3.xml",
        title="Account Planning Playbook",
        paragraphs=["Account teams will follow the standardized operating cadence."],
        tables=[],
        speaker_notes="",
    )
    slides = [s1, s2, s3]
    return PPTXPresentation(
        title="Florida 2027 Strategy",
        slides=slides,
        raw_sha256="d" * 64,
        extracted_markdown="# Florida 2027 Strategy\n\n## Slide 1\n\n## Slide 2\n\n## Slide 3\n",
        extracted_markdown_sha256="e" * 64,
        slide_count=3,
    )


def test_parse_transcript_turns():
    """Verify turn parser extracts speaker names, timestamps, and text while filtering headers."""
    sample_transcript = """# Enterprise Account Planning Kickoff
**Date:** 09/11/2026
**Time:** 10:00 AM - 10:45 AM ET
**Platform Reference:** Microsoft Teams
---

**Maud Boisacq:** [00:01:15] Welcome everyone to our Florida planning kickoff session.
Please turn to slide 1 for our strategic vision.

**Chris Bongiovanni:** [00:02:30] Thanks Maud. As we look at slide 2, our operational run rate has hit 84 million.

**Lucy Bickford:** [00:04:00] We also need to consider slide 3 for the account cadence.
"""
    turns = parse_transcript_turns(sample_transcript)
    assert len(turns) == 3
    assert turns[0]["speaker"] == "Maud Boisacq"
    assert turns[0]["timestamp"] == "00:01:15"
    assert "slide 1" in turns[0]["text"]

    assert turns[1]["speaker"] == "Chris Bongiovanni"
    assert turns[1]["timestamp"] == "00:02:30"
    assert "slide 2" in turns[1]["text"]

    assert turns[2]["speaker"] == "Lucy Bickford"
    assert turns[2]["timestamp"] == "00:04:00"
    assert "slide 3" in turns[2]["text"]


def test_extract_slide_numbers_from_turn():
    """Verify explicit slide reference extractor handles single, multiple, and range mentions."""
    max_slides = 50

    # Single slide
    assert _extract_slide_numbers_from_turn("Let's look at slide 4 please.", max_slides) == [4]

    # Multiple slides (and / comma)
    assert _extract_slide_numbers_from_turn("Slides 4 and 5 cover this topic.", max_slides) == [4, 5]

    # Slide range (through / to / -)
    assert _extract_slide_numbers_from_turn("Review slide 8 through 12 in detail.", max_slides) == [8, 9, 10, 11, 12]

    # Out of bounds ignored
    assert _extract_slide_numbers_from_turn("Check slide 99.", max_slides) == []


def test_align_transcript_presentation():
    """Verify presentation alignment aggregates mentions, speaker coverage, and evidence packs."""
    pres = _make_dummy_presentation()
    transcript = """
**Maud Boisacq:** [00:01:00] Please refer to slide 1 for the executive vision.
**Chris Bongiovanni:** [00:02:00] Moving to slide 2, our operational run rate is strong.
**Murray Gordon:** [00:03:00] I have a question about general market growth.
"""
    alignment = align_transcript_presentation(transcript, pres)

    assert isinstance(alignment, PresentationAlignment)
    assert alignment.presentation_title == "Florida 2027 Strategy"
    assert alignment.slide_count == 3
    assert alignment.total_turns == 3
    assert len(alignment.mentions) == 2

    # Speaker coverage
    assert alignment.speaker_slide_coverage["Maud Boisacq"] == [1]
    assert alignment.speaker_slide_coverage["Chris Bongiovanni"] == [2]

    # Unreferenced slides
    assert alignment.unreferenced_slides == [3]

    # Evidence pack
    pack = alignment.get_slide_evidence_pack()
    assert len(pack) >= 2
    locators = {p["locator"] for p in pack}
    assert "slide:01" in locators
    assert "slide:02" in locators


def test_inject_slide_annotations():
    """Verify inject_slide_annotations embeds formatted callout blocks into referenced turns."""
    pres = _make_dummy_presentation()
    transcript = """# Kickoff
**Date:** 09/11/2026
---

**Maud Boisacq:** [00:01:00] Please refer to slide 1 for our strategic vision.
**Murray Gordon:** [00:02:00] Great overview.
"""
    alignment = align_transcript_presentation(transcript, pres)
    annotated = inject_slide_annotations(transcript, alignment, pres)

    # Maud's turn should have slide annotation
    assert "> *[Referenced Presentation Deck: Slide 1: Executive Strategic Vision]*" in annotated
    # Murray's turn should not have slide annotation
    assert "Slide 2" not in annotated
    assert "Slide 3" not in annotated


def test_inject_slide_annotations_preserves_roster_and_footer():
    """Verify inject_slide_annotations preserves complete indented participant roster and footer tables."""
    pres = _make_dummy_presentation()
    transcript = """# Enterprise Account Planning Kickoff
**Date:** 09/11/2026
### Participants & Roster:
- **PartnerAdvisory:**
  - Beau Williamson (General Manager / Market Leader)
  - Maud Boisacq (Operations Lead)
---

## Transcript

**Maud Boisacq:** [00:01:00] Please refer to slide 1 for our strategic vision.
**Murray Gordon:** [00:02:00] Great overview.

---

## Ambiguity Lock Register

| Item # | Identified Ambiguity | Final Resolved Record |
|---|---|---|
| AMB-01 | Speaker-1 Attribution | Resolved to Beau Williamson |

---

## Evidence Locks / Semantic Constraints

- LOCK-01: Revenue planning is strictly net revenue.
"""
    alignment = align_transcript_presentation(transcript, pres)
    annotated = inject_slide_annotations(transcript, alignment, pres)

    # 1. Slide callout injected
    assert "> *[Referenced Presentation Deck: Slide 1: Executive Strategic Vision]*" in annotated

    # 2. Complete header roster preserved
    assert "- **PartnerAdvisory:**" in annotated
    assert "  - Beau Williamson (General Manager / Market Leader)" in annotated
    assert "  - Maud Boisacq (Operations Lead)" in annotated

    # 3. Complete footer tables preserved
    assert "## Ambiguity Lock Register" in annotated
    assert "AMB-01" in annotated
    assert "## Evidence Locks / Semantic Constraints" in annotated
    assert "LOCK-01: Revenue planning is strictly net revenue." in annotated
