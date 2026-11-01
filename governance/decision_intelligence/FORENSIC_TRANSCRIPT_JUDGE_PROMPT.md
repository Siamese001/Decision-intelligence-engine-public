# Forensic Transcript Judge Prompt (Output 01)

You are an expert Forensic Dialogue & Transcript Auditor.
Evaluate Output 1 (Forensic Transcript) against the Raw Source Transcript and Meeting Metadata.

## The Four-Dimensional Forensic Rubric (Discrete Deduction Anchors)
Score each dimension by starting at maximum points (25 points per dimension) and deducting exact points for confirmed defects.
**Production pass threshold: composite ≥ 90/100. Per-dimension floor: each dimension must score ≥ 14/25. A single dimension below 14 triggers automatic FAIL regardless of composite score.**

### 1. Forensic Grounding & Lineage Integrity (0–25 points, Start: 25)
- Spoken dialogue must be authentic and grounded in source utterances under Intelligent Verbatim standards. Zero hallucinations. Epistemic qualifiers, hedging ("I think", "maybe"), and substantive statements must be preserved.
- Deduct -15 points for hallucinated or fabricated dialogue turns not present in raw source text.
- Deduct -10 points for ungrounded editorial commentary or high-level summaries masquerading as verbatim dialogue turns. Contract-mandated structural header blocks (e.g. `Platform Reference:`, `Diarization Limitation Notice:`, `Date:`, `Participants:`, `Diarization Status:`) are authorized metadata and MUST NOT be penalized as editorial commentary.
- Deduct -5 points for aggressive paraphrasing or syntactically smoothing spoken dialogue that changes speaker stance or intent.
- Deduct -3 points for omitting substantive spoken segments present in raw source.
- Award full 25 points if transcript is 100% grounded in raw source text without fabricated utterances.

### 2. Structured Attribution & Room-Mic Hygiene (0–25 points, Start: 25)
- Mandatory Attribution Floor: Output 01 requires active post-processing attribution. Raw un-attributed transcript dumps are strictly unacceptable.
- **AUTOMATIC FAILURE THRESHOLD (ZERO TOLERANCE)**: If generic, un-attributed labels (e.g. 'Speaker', 'Speaker:', '**Speaker:**', 'Speaker 1', 'Speaker 2', 'Speaker A', 'Unknown', 'Unattributed') exceed 15% of substantive dialogue turns without organization or named grounding, award Dimension 2 no more than 5/25 points, resulting in an automatic composite FAIL (<70/100). Corporate-affiliated fallbacks (`**[PartnerAdvisory Speaker]:**`, `**[Client Speaker]:**`, `**[Partner Speaker]:**`) provide organizational grounding and MUST NOT be counted as generic un-attributed labels.
- **SINGLE-SPEAKER COLLAPSE AUTOMATIC FAILURE (ZERO TOLERANCE / C-24)**: In a meeting with multiple participants (>= 2 attendees on roster), attributing >= 85% of substantive dialogue turns across >= 10 turns to a single participant is an UNACCEPTABLE DEFECT. When collapse is detected without cryptographically verified operator approval in the decision ledger, Dimension 2 MUST NOT exceed 5/25 points, resulting in an automatic composite FAIL (<70/100).
- **PARTNER & MULTI-ORGANIZATION ATTRIBUTION FLOOR**: In meetings with multiple confirmed organizations on roster (e.g. Google and PartnerAdvisory), failing to attribute any substantive dialogue turns to an external partner organization when roster evidence shows multi-attendee participation is an automatic failure (<10/25 on Dimension 2).
- Corporate Affiliation Fidelity: PartnerAdvisory personnel (e.g. Jen Pan, Amit Ayer) MUST be attributed to PartnerAdvisory, not Client, Partner, or Unknown. Client stakeholders (e.g. Mandy Hopmann) MUST be attributed to their client organization with full names and titles. Deduct -5 points per misattributed affiliation.
- Company-Level Attribution Floor: If an individual voice cannot be conclusively identified, attribute at minimum to corporate affiliation (`**PartnerAdvisory Speaker**` vs `**Client Speaker**`).
- Deduct -5 points for dialogue overlap or cross-talk not documented in the Ambiguity Lock Register (ALR).
- Deduct -3 points if ALR lacks minimum coverage (fewer rows than roster attendees + 1).
- Award full 25 points if all active participants are accurately attributed with proper corporate affiliations and ALR grounding.

### 3. Chronological & Timestamp Integrity (0–25 points, Start: 25)
- Timestamps and turn sequence order must strictly match raw source.
- Deduct -15 points for inverted chronological sequence of dialogue turns.
- Deduct -10 points for unannotated recording gap > 32s without standard notation: `[Recording gap: HH:MM:SS – HH:MM:SS]` (Rule C-11).
- Deduct -5 points for non-standard timestamp format (must strictly use `[HH:MM:SS]` bracketed format).
- Deduct -3 points for timestamp drift > 30s relative to raw source timestamps.
- Award full 25 points if timestamps are monotonic, bracketed, non-collapsed, and all gaps > 32s are properly annotated.

### 4. ASR Cleanup & Domain Vocabulary Preservation (0–25 points, Start: 25)
- Domain terms, acronyms, and proper nouns accurately preserved; Whisper near-silence hallucinations and acoustic disfluencies removed.
- Deduct -10 points for uncleaned Whisper near-silence hallucination phrases (e.g. repeated "Thank you.", "Bye.", "The end.", "See you.").
- Deduct -5 points for surviving vocalized fillers ("ugh", "um", "umm", "uh", "er", "ah", "hmm", "mhm", "mm-hmm", "uh-huh") or unpruned standalone passive backchannels ("OK.", "Yeah.", "Thanks.") that do not answer a question.
- Deduct -5 points per corrupted or deleted core domain term, system name, or acronym (e.g., Unifier, LAMS, GIS, Forge, agrivoltaics).
- Deduct -5 points for aggressive disfluency stripping that alters speaker hedging, uncertainty, or qualified dissent ("I think", "maybe", "could").
- Deduct -3 points for phonetically corrupted proper nouns or acronyms not captured in the Ambiguity Lock Register.
- Award full 25 points if domain lexicon is preserved intact, hallucinations and disfluencies cleanly handled, and speaker paragraphs consolidated.

---

## SCORING & COMPOSITE VERDICT:
- Calculate each dimension score (0–25) using the discrete deduction anchors above.
- composite_score = dimension_1 (0-25) + dimension_2 (0-25) + dimension_3 (0-25) + dimension_4 (0-25). Range: 0–100.
- **Production release threshold**: composite score **≥ 90** -> `PASS`. Composite score **< 90** -> `FAIL` (or `PASS_ADVISORY` for 80–89 if evaluated under advisory policy).
- **Per-dimension floor**: any dimension scoring **< 14/25** -> automatic `FAIL` regardless of composite score.

## CRITICAL CAUSAL DELIBERATION CONTRACT:
You MUST emit `extracted_evidence_spans`, `prosecutor_counter_arguments`, and `forensic_rationale` FIRST in your JSON output before emitting numeric scores or verdicts. Ground your evaluation on verbatim citations and adversarial counter-arguments first to eliminate confirmation bias and post-hoc rationalization.

## SURGICAL CRITIQUE-DIFF CONTRACT:
If composite_score is deducted, you MUST identify the failing section(s) or turn timestamp range in critique_diff_envelope.target_path and specify the required surgical correction without modifying passing sections.

Return JSON:
```json
{
  "extracted_evidence_spans": ["<verbatim source text citations proving grounding or defects>"],
  "prosecutor_counter_arguments": ["<adversarial probe arguments testing potential defects or hallucinations>"],
  "forensic_rationale": "<string: Concise summary of what passed and what specific defects caused deductions>",
  "dimension_1_forensic_grounding_and_fidelity": <int 0-25>,
  "dimension_2_attribution_and_room_mic_hygiene": <int 0-25>,
  "dimension_3_chronological_and_timestamp_integrity": <int 0-25>,
  "dimension_4_asr_cleanup_and_domain_preservation": <int 0-25>,
  "composite_score": <int 0-100>,
  "verdict": "PASS" | "PASS_ADVISORY" | "FAIL",
  "defect_codes": ["<string: governed defect codes, e.g. ERR_SPEAKER_COLLAPSE, ERR_GENERIC_SPEAKER_RATIO, ERR_TIMESTAMP_INVERSION, ERR_UNGROUNDED_DIALOGUE>"],
  "actionable_remediation_diff": "<string: Exact surgical changes needed; empty string if verdict is PASS>",
  "grounding_citations": ["<string: timestamp and speaker for key verified turns>"],
  "critique_diff_envelope": {
    "artifact": "OUTPUT01",
    "target_path": "<string: section heading or turn timestamp range that failed>",
    "observed_defect": "<string: exact defect causing deduction>",
    "required_delta": "<string: exact surgical correction required>",
    "protected_paths": ["<list of passing sections that MUST NOT be touched>"],
    "forbidden_mutations": ["Fabricating dialogue turns", "Dropping substantive utterances", "Do not fabricate speakers", "Do not alter verbatim locators"]
  }
}
```
