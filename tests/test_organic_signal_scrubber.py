"""Unit and regression tests for context-aware organic signal scrubber.

Verifies elimination of non-lexical vocalized fillers, stutters, and passive backchannels
across real production excerpts from PRs ed8b5f8a7, 6ef5bca4e, e41415e8e, a5f72b1e7, and d97c5bf95.
"""

import pytest
from dual_pipeline.transcript_stages_core.organic_signal_scrubber import (
    clean_turn_text,
    collapse_stutters_and_repetitions,
    is_passive_backchannel,
    is_question_turn,
    repair_dangling_connectors,
    scrub_organic_signal,
    strip_vocalized_fillers,
)


def test_vocalized_fillers_stripped_inline_and_leading():
    raw1 = "Um, no you're not missing anything"
    assert strip_vocalized_fillers(raw1) == "No, you're not missing anything"

    raw2 = "I guess it's more of a, um, maybe, rather standard platform for your past, like, um..."
    assert strip_vocalized_fillers(raw2) == "I guess it's more of a, maybe, rather standard platform for your past, like..."

    raw3 = "Yeah, okay, I... uh... it's ready to be demoed"
    assert strip_vocalized_fillers(raw3) == "Yeah, okay, I... it's ready to be demoed"

    raw4 = "Ugh, this pipeline is failing"
    assert strip_vocalized_fillers(raw4) == "This pipeline is failing"


def test_hyphen_stutters_and_repeated_words_collapsed():
    # PR ed8b5f8a7 excerpt
    raw_stutter = "It -it -it -that I think is part of phase one, is -is that at least"
    collapsed = collapse_stutters_and_repetitions(raw_stutter)
    assert "That I think is part of phase one" in collapsed
    assert "is that at least" in collapsed

    # PR 6ef5bca4e & e41415e8e word duplications
    assert collapse_stutters_and_repetitions("make make the changes") == "make the changes"
    assert collapse_stutters_and_repetitions("so we we can verify") == "so we can verify"
    assert collapse_stutters_and_repetitions("just just that only the the text") == "just that only the text"
    assert collapse_stutters_and_repetitions("different. different.") == "different."


def test_dangling_connectors_repaired():
    # PR ed8b5f8a7 & d97c5bf95 dangling connectors
    assert repair_dangling_connectors("fall under the same thing. And so .") == "fall under the same thing."
    assert repair_dangling_connectors("like performance or .") == "like performance."
    assert repair_dangling_connectors("Process that it's now in acceptance. Or") == "Process that it's now in acceptance."


def test_question_detection():
    assert is_question_turn("Any questions on this?") is True
    assert is_question_turn("So is that what you had in mind basically?") is True
    assert is_question_turn("Does that make sense?") is True
    assert is_question_turn("Here is the updated architectural diagram.") is False


def test_passive_backchannels_pruned_unless_answering_question():
    transcript = """[00:00:48] Amit Ayer: OK.
[00:00:50] Alex Sanchez: Let's begin the review of the notification architecture.
[00:05:53] Alex Sanchez: Yeah.
[00:05:55] Eduardo Juan More: The architecture covers three tiers. Any questions on this?
[00:05:58] Amit Ayer: Yeah.
[00:06:00] Alex Sanchez: We need to ensure low latency.
"""
    cleaned = scrub_organic_signal(transcript)
    # Standalone OK at 00:00:48 must be pruned
    assert "[00:00:48]" not in cleaned
    # Passive Yeah at 00:05:53 must be pruned
    assert "[00:05:53]" not in cleaned
    # Question at 00:05:55 must be preserved
    assert "Any questions on this?" in cleaned
    # Affirmative answer Yeah at 00:05:58 MUST be preserved
    assert "[00:05:58] Amit Ayer: Yeah." in cleaned


def test_opening_phatic_fluff_pruned():
    # PR a5f72b1e7 opening
    transcript = """[00:00:08] Amit Ayer: Good afternoon, everybody.
[00:00:09] Marcus Balk: Hey, Dan.
[00:00:10] Marcus Balk: Thanks for joining.
[00:00:12] Dan: Yeah.
[00:00:30] Marcus Balk: Cool.
[00:00:35] Marcus Balk: Today we want to walk through the Sherpa demo requirements.
"""
    cleaned = scrub_organic_signal(transcript)
    assert "Good afternoon" not in cleaned
    assert "Thanks for joining" not in cleaned
    assert "Cool." not in cleaned
    assert "Sherpa demo requirements" in cleaned


def test_preservation_of_technical_metrics_and_epistemic_hedges():
    raw = "[00:02:15] Lead Architect: I think the 1.2M trajectory needs AWS S3 integration, maybe with Kafka."
    cleaned = scrub_organic_signal(raw)
    assert "I think" in cleaned
    assert "1.2M trajectory" in cleaned
    assert "AWS S3" in cleaned
    assert "Kafka" in cleaned


def test_markdown_bold_turn_headers_prune_passive_backchannels():
    transcript = """**[00:22:34] Kannan Chandraprakash:** Try to listen carefully.

**[00:25:23] Amit Ayer:** Yeah.

**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV.

## Ambiguity Lock Register
| # | Category | Timestamp | Speaker | Observed Ambiguity | Resolution |
|---|---|---|---|---|---|
| 1 | Test | 00:25:23 | Amit Ayer | Passive acknowledgment | Pruned |
"""
    cleaned = scrub_organic_signal(transcript)
    assert "**[00:25:23] Amit Ayer:** Yeah." not in cleaned
    assert "**[00:22:34] Kannan Chandraprakash:** Try to listen carefully." in cleaned
    assert "**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV." in cleaned
    assert "## Ambiguity Lock Register" in cleaned
    assert "| 1 | Test |" in cleaned


def test_multiline_heading_turn_blocks_scrubbed():
    transcript = """### [00:01:00]

**Alex Sanchez:** Today we are reviewing the architecture.

### [00:01:50]

**Michael Maione:** Yeah, no worries. Yeah, I said over, uh, like right next to Barrows's office.

### [00:02:09]

**Michael Maione:** Yeah.

### [00:02:10]

**Amit Ayer:** Okay.

### [00:03:06]

**Mike Homorody:** I know this is probably probably a hands-on meeting.

### [00:11:30]

**Amit Ayer:** Um... But I think what Avik could have done differently.

### [00:35:22]

**Michael Maione:** Um...

## Ambiguity Lock Register
| Item # | Identified Ambiguity |
|---|---|
| 1 | Test |
"""
    cleaned = scrub_organic_signal(transcript)
    assert "**Michael Maione:** Yeah." not in cleaned
    assert "**Amit Ayer:** Okay." not in cleaned
    assert "### [00:35:22]" not in cleaned
    assert "like right next to Barrows's office." in cleaned
    assert ", uh," not in cleaned
    assert "probably a hands-on meeting" in cleaned
    assert "probably probably" not in cleaned
    assert "But I think what Avik" in cleaned
    assert "Um..." not in cleaned
    assert "## Ambiguity Lock Register" in cleaned


def test_bold_markdown_turn_headers_pruning_and_preservation():
    # Production regression from Kannan Welcome Interviews
    transcript = """# Forensic Transcript

**[00:22:34] Kannan Chandraprakash:** Try to listen.

**[00:25:23] Amit Ayer:** Yeah.

**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV. Any questions on this?

**[00:25:50] Amit Ayer:** Yeah.

**[00:25:52] Kannan Chandraprakash:** Great.

## Ambiguity Lock Register

| Item # | Identified Ambiguity | Source Locator |
|---|---|---|
| 1 | Test table row with um filler | [00:25:23] |
"""
    cleaned = scrub_organic_signal(transcript)

    # Standalone passive backchannel turn at 00:25:23 must be pruned
    assert "**[00:25:23] Amit Ayer:**" not in cleaned
    # Non-question turn preceding it must be preserved with bold format
    assert "**[00:22:34] Kannan Chandraprakash:** Try to listen." in cleaned
    # Question turn preserved
    assert "**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV. Any questions on this?" in cleaned
    # Question response at 00:25:50 must be preserved in bold format
    assert "**[00:25:50] Amit Ayer:** Yeah." in cleaned
    # Markdown section heading and table untouched
    assert "## Ambiguity Lock Register" in cleaned
    assert "| 1 | Test table row with um filler | [00:25:23] |" in cleaned


def test_mixed_turn_header_styles_preserved():
    transcript = """**Alex Sanchez:** [00:01:00] First turn with trailing timestamp.
**[00:01:05]** **Alex Sanchez:** Second turn with split bold.
**[00:01:10] Alex Sanchez:** Third turn with unified bold.
[00:01:15] Alex Sanchez: Fourth turn with standard syntax.
"""
    cleaned = scrub_organic_signal(transcript)
    assert "**Alex Sanchez:** [00:01:00] First turn with trailing timestamp." in cleaned
    assert "**[00:01:05]** **Alex Sanchez:** Second turn with split bold." in cleaned
    assert "**[00:01:10] Alex Sanchez:** Third turn with unified bold." in cleaned
    assert "[00:01:15] Alex Sanchez: Fourth turn with standard syntax." in cleaned


def test_two_line_heading_turns_scrubbing_and_backchannel_pruning():
    transcript = """### [00:00:02]

**Amit Ayer:** Good afternoon, everybody. I um think to to make progress we need AWS.

### [00:00:08]

**Megan Orzell:** Yeah.

### [00:00:10]

**Amit Ayer:** Do you have any questions on the open role?

### [00:00:15]

**Megan Orzell:** Yeah.

### [00:00:18]

**Amit Ayer:** Great. We will proceed.
"""
    cleaned = scrub_organic_signal(transcript)
    assert "to make progress" in cleaned
    assert "to to" not in cleaned
    assert " um " not in cleaned
    assert "### [00:00:08]" not in cleaned
    assert "### [00:00:02]" in cleaned
    assert "### [00:00:15]" in cleaned
    assert "**Megan Orzell:** Yeah." in cleaned


def test_inline_bold_format_scrubbing():
    transcript = """**Speaker 1** [00:00:02]: Well um I think to to proceed with Kafka.
**Speaker 2** [00:00:05]: Yeah.
**Speaker 1** [00:00:08]: Should we schedule the follow-up?
**Speaker 2** [00:00:10]: Yeah.
"""
    cleaned = scrub_organic_signal(transcript)
    assert "to to" not in cleaned
    assert " um " not in cleaned
    assert "to proceed with Kafka" in cleaned
    assert "[00:00:05]" not in cleaned
    assert "[00:00:10]" in cleaned

