"""Adversarial and property-based test suite for organic signal scrubber.

Meets the mandatory requirements of .agents/rules/e2e-and-edge-case-coverage.md
and Decision H-8 (Hypothesis property-based verification):
1. Boundary payloads (empty input, single punctuation, 0-length turns, unicode spaces).
2. Malformed input (malformed headers, non-ASCII emoji, mixed newlines, truncated syntax).
3. Concurrency & race (16-thread concurrent execution stress).
4. Invariant bypass attempts (sub-word filler collision prevention: 'summary', 'human', 'brother').
5. Exact production regressions (exact excerpts from 5 merged PRs).
6. Property-based invariants via Hypothesis.
"""

from concurrent.futures import ThreadPoolExecutor
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dual_pipeline.transcript_stages_core.organic_signal_scrubber import (
    clean_turn_text,
    collapse_stutters_and_repetitions,
    is_passive_backchannel,
    is_question_turn,
    repair_dangling_connectors,
    scrub_organic_signal,
    strip_vocalized_fillers,
)


# --- Category 1: Boundary Payloads ---
def test_boundary_payloads_empty_and_whitespace():
    assert scrub_organic_signal("") == ""
    assert scrub_organic_signal("   \n\t  \n  ") == ""
    assert clean_turn_text("") == ""
    assert clean_turn_text("   ") == ""
    assert strip_vocalized_fillers("") == ""
    assert repair_dangling_connectors("") == ""
    assert collapse_stutters_and_repetitions("") == ""


def test_boundary_payloads_single_token_and_punct():
    assert clean_turn_text(".") == "."
    assert clean_turn_text("...") == "..."
    assert clean_turn_text("?") == "?"
    assert clean_turn_text("um") == ""
    assert clean_turn_text("Uh") == ""
    assert clean_turn_text("ok") == "Ok"


# --- Category 2: Malformed Input ---
def test_malformed_headers_and_mixed_newlines():
    malformed = (
        "[invalid-time] Speaker Text without colon\r\n"
        "***Broken Speaker*** [00:01:00] Text\r\n"
        "[00:02:00] ::: Double Colon\r\n"
        "[00:03:00] Speaker: Valid turn with emoji 🚀 and um filler.\r\n"
    )
    result = scrub_organic_signal(malformed)
    assert "Valid turn with emoji 🚀 and filler." in result


# --- Category 3: Concurrency & Race ---
def test_concurrency_multithreaded_stress():
    sample = (
        "[00:00:10] Speaker A: Um, we have an architecture question. Any questions?\n"
        "[00:00:15] Speaker B: Yeah.\n"
        "[00:00:16] Speaker C: OK.\n"
        "[00:00:20] Speaker A: It -it -it -that is the plan.\n"
    )

    def _worker(idx: int) -> str:
        return scrub_organic_signal(f"<!-- Worker {idx} -->\n{sample}")

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(_worker, i) for i in range(64)]
        results = [f.result() for f in futures]

    assert len(results) == 64
    for res in results:
        assert "That is the plan." in res
        assert "Speaker B: Yeah." in res
        assert "Speaker C: OK." not in res


# --- Category 4: Invariant Bypass Prevention (Sub-word Collisions) ---
def test_invariant_bypass_subword_filler_preservation():
    """Ensure sub-words containing filler tokens (e.g. 'summary', 'human', 'brother') are never corrupted."""
    text = "The summary for the human instrument was reviewed by our brother."
    assert strip_vocalized_fillers(text) == text

    turn = "[00:01:00] Lead: Summary for human brotherhood and triumph."
    assert "Summary for human brotherhood and triumph." in scrub_organic_signal(turn)


# --- Category 5: Exact Production Regressions ---
def test_exact_production_regressions_5_prs():
    # 1. PR ed8b5f8a7
    pr1 = (
        "[00:00:48] Amit Ayer: OK.\n"
        "[00:05:53] Alex Sanchez: Yeah.\n"
        "[00:08:22] Eduardo Juan More: It -it -it -that I think is part of phase one, is -is that at least...\n"
        "[00:07:19] Alex Sanchez: But also, they all kind of fall under the same thing. And so .\n"
        "[00:07:27] Alex Sanchez: different. different.\n"
    )
    res1 = scrub_organic_signal(pr1)
    assert "[00:00:48]" not in res1
    assert "[00:05:53]" not in res1
    assert "That I think is part of phase one, is that at least..." in res1
    assert "same thing." in res1
    assert "Different." in res1

    # 2. PR 6ef5bca4e
    pr2 = "[00:06:13] Speaker: make make the changes"
    res2 = scrub_organic_signal(pr2)
    assert "Make the changes" in res2

    # 3. PR e41415e8e
    pr3 = (
        "[00:05:06] Speaker: um because\n"
        "[00:05:45] Speaker: so we we\n"
        "[00:05:50] Speaker: just just that only the the text\n"
    )
    res3 = scrub_organic_signal(pr3)
    assert "Because" in res3
    assert "So we" in res3
    assert "Just that only the text" in res3

    # 4. PR a5f72b1e7
    pr4 = (
        "[00:00:08] Amit: Good afternoon, everybody.\n"
        "[00:00:09] Marcus: Hey, Dan.\n"
        "[00:00:10] Marcus: Thanks for joining.\n"
        "[00:00:12] Dan: Yeah.\n"
        "[00:00:30] Marcus: Cool.\n"
        "[00:00:35] Marcus: Real substance starts here.\n"
    )
    res4 = scrub_organic_signal(pr4)
    assert "Good afternoon" not in res4
    assert "Thanks for joining" not in res4
    assert "Real substance starts here." in res4

    # 5. PR d97c5bf95
    pr5 = (
        "[00:01:35] Speaker: like performance or .\n"
        "[00:19:15] Speaker: Um, no you're not missing anything\n"
    )
    res5 = scrub_organic_signal(pr5)
    assert "Like performance." in res5
    assert "No, you're not missing anything" in res5

    # 6. Exact Production Regression: Kannan Welcome Interviews (Bold Header Blindspot)
    pr6 = (
        "# Forensic Transcript\n\n"
        "**[00:22:34] Kannan Chandraprakash:** Try to listen.\n\n"
        "**[00:25:23] Amit Ayer:** Yeah.\n\n"
        "**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator |\n"
        "|---|---|---|\n"
        "| 1 | Reference to 00:25:23 | [00:25:23] |\n"
    )
    res6 = scrub_organic_signal(pr6)
    assert "**[00:25:23] Amit Ayer:**" not in res6
    assert "**[00:22:34] Kannan Chandraprakash:** Try to listen." in res6
    assert "**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV." in res6
    assert "| 1 | Reference to 00:25:23 | [00:25:23] |" in res6


def test_adversarial_bold_header_consecutive_and_boundary():
    """Verify consecutive passive backchannels, empty content, and boundary turns in bold syntax."""
    adversarial_dialogue = (
        "**[00:01:00] Speaker A:** What is the rollout timeline?\n"
        "**[00:01:05] Speaker B:** Yeah.\n"
        "**[00:01:10] Speaker C:** Sure.\n"
        "**[00:01:15] Speaker A:** The timeline spans six weeks.\n"
        "**[00:01:20] Speaker B:** OK.\n"
        "**[00:01:22] Speaker C:** Yeah.\n"
        "**[00:01:25] Speaker A:** End of meeting.\n"
    )
    res = scrub_organic_signal(adversarial_dialogue)
    # The first backchannel after the question is preserved as affirmative answer
    assert "**[00:01:05] Speaker B:** Yeah." in res
    # Subsequent consecutive backchannel is pruned
    assert "[00:01:10]" not in res
    # Substantive turn preserved
    assert "**[00:01:15] Speaker A:** The timeline spans six weeks." in res
    # Consecutive unprompted backchannels both pruned
    assert "[00:01:20]" not in res
    assert "[00:01:22]" not in res
    assert "**[00:01:25] Speaker A:** End of meeting." in res


def test_forensic_healer_e2e_kannan_regression():
    """Verify end-to-end integration between heal_output_1_forensic and check_organic_signal_hygiene."""
    from dual_pipeline.transcript_qa_core.forensic_healer import heal_output_1_forensic
    from dual_pipeline.llm_judge_core.forensic_checks import check_organic_signal_hygiene

    raw_kannan = (
        "# Forensic Transcript\n\n"
        "**[00:22:34] Kannan Chandraprakash:** Try to listen.\n\n"
        "**[00:25:23] Amit Ayer:** Yeah.\n\n"
        "**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator |\n"
        "|---|---|---|\n"
        "| 1 | Reference to 00:25:23 | [00:25:23] |\n"
    )
    healed = heal_output_1_forensic(raw_kannan)
    assert "**[00:25:23] Amit Ayer:**" not in healed
    assert "**[00:22:34] Kannan Chandraprakash:** Try to listen." in healed
    assert "**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV." in healed
    assert "| 1 | Reference to 00:25:23 | [00:25:23] |" in healed

    passed, defect_codes, metrics = check_organic_signal_hygiene(healed)
    assert passed is True
    assert len(defect_codes) == 0
    assert metrics["backchannel_count"] == 0
    assert metrics["total_disfluencies"] == 0


# --- Category 6: Hypothesis Property Invariants ---
@settings(max_examples=100, deadline=1000)
@given(st.text())
def test_hypothesis_never_crashes_on_arbitrary_string(sample: str):
    """Scrubber must never raise uncaught exception or return None on arbitrary text."""
    res = scrub_organic_signal(sample)
    assert isinstance(res, str)
    turn_res = clean_turn_text(sample)
    assert isinstance(turn_res, str)


def test_forensic_normalizer_never_merges_different_speakers():
    from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
    raw = """### [00:00:02]

**Amit Ayer:** We need to talk about the role.

### [00:00:05]

**Megan Orzell:** the

### [00:00:07]

**Megan Orzell:** so

### [00:00:10]

**Amit Ayer:** What are the next steps?
"""
    stitched = ForensicNormalizer._stitch_acoustic_micro_turns(raw)
    # Megan Orzell's micro-turns "the" and "so" must NOT be merged into Amit Ayer!
    assert "Amit Ayer:** We need to talk about the role. the" not in stitched
    assert "Amit Ayer:** We need to talk about the role. so" not in stitched
    assert "**Megan Orzell:** the so" in stitched or ("**Megan Orzell:**" in stitched and "Amit Ayer" in stitched)


def test_forensic_normalizer_respects_30s_turn_duration_ceiling():
    from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
    # Same speaker speaking across >30 seconds with small gaps
    raw = """### [00:00:02]

**Amit Ayer:** First sentence starting at 2 seconds.

### [00:00:15]

**Amit Ayer:** Second sentence at 15 seconds.

### [00:00:38]

**Amit Ayer:** Third sentence at 38 seconds exceeding 30s total duration.
"""
    stitched = ForensicNormalizer._stitch_acoustic_micro_turns(raw)
    # The turn at 00:00:38 must NOT be stitched into [00:00:02] because duration > 30s
    assert "### [00:00:38]" in stitched
    assert "### [00:00:02]" in stitched

