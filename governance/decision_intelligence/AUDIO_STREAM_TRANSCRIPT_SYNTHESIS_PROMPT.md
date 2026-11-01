# AUDIO STREAM & ASR RAW TRANSCRIPT SYNTHESIS CONTRACT (SSOT)
## Protocol: `AUDIO_STREAM_TRANSCRIPT_SYNTHESIS:1.0.0`
## Canonical Authority: `governance/decision_intelligence/AUDIO_STREAM_TRANSCRIPT_SYNTHESIS_PROMPT.md`

> **Scope**: Authoritative Single Source of Truth (SSOT) prompt contract governing the synthesis of
> audio recordings, single-channel Whisper captures, and MLX speech-to-text streams into the
> governed three-artifact delivery triad (Outputs 01, 02, and 03).

---

## 1. INGESTION MODALITY: AUDIO RECORDING & ASR TRANSCRIPTION

Whisper / MLX speech-to-text outputs often originate from single-channel microphones, room audio, or mixed streams:
- Chunks may be un-diarized or pre-tagged with a default speaker label.
- Speakers transition naturally without explicit acoustic speaker markers.

### Core Processing Invariants:
1. **Linguistic Multi-Speaker Deconvolution**:
   - The synthesizer must identify speaker transitions using conversational signals:
     - **Direct Address & Greetings**: E.g. *"Hey Mandy! Hello!"* indicates a transition to or acknowledgment of Mandy;
     - **Self-Identification**: E.g. *"I'm Amit Ayer, and I'm here to implement..."* definitively marks an Amit Ayer turn;
     - **Third-Party Referencing**: E.g. *"If we send him an update that it's 22.4, Amit is going to lose it"* proves the speaker is NOT Amit Ayer;
     - **Question / Answer Pairs**: Scoping questions vs domain responses;
     - **Organizational Voice**: PartnerAdvisory architecture/delivery voice vs Client business/requirements voice.
2. **Strict Prohibition of Single-Speaker Collapse**:
   - In a meeting with multiple declared attendees, attributing 100% (or >90%) of turns to a single speaker while asserting *"no other spoken turns were isolated"* is a **BLOCKING DEFECT**.
   - If the raw text has turns collapsed under a single name (due to recorder channel defaults), the synthesizer must perform forensic re-attribution to recover discrete turns for all participating attendees.
3. **Knowledge Graph Attendee Grounding**:
   - Cross-reference all attendees against the Knowledge Graph and entity catalog before finalizing Output 01.
   - Never classify PartnerAdvisory personnel (Jen Pan, Amit Ayer) as Client/Partner.
   - Never truncate client leaders (Mandy Hopmann, Product Manager, Apex Global / NEA).

---

## 2. THE THREE-ARTIFACT DELIVERY CONTRACT

### Output 01: Forensic Attributed Transcript
`01_<meeting_name>_<date>_<time>_hitl_resolved_transcript.md`

Must feature:
- Company-grouped participants with full names, titles, and corporate affiliations.
- Verbatim spoken dialogue with discrete, deconvolved speaker turns for each attendee.
- Ambiguity Lock Register capturing acoustic artifacts, phonetic disfluencies, and domain acronyms.

### Output 02: Executive Recap & Action Brief
`02_<meeting_name>_<date>_<time>_executive_recap.md`

Must feature:
- Email subject, date, time, and company-grouped attendees.
- Meeting Objective and Key Discussion Points.
- Next Steps table with specific DRI owners and deadlines.
- Copilot-style Appendix of Detailed Stakeholder Perspectives & Contribution Breakdown, detailing what each attendee contributed.

### Output 03: Canonical Signal Projection
`03_<meeting_name>_<date>_<time>_canonical_signal_projection.json`

Valid JSON adhering to `evidence-envelope-intake.schema.json` with multi-speaker evidence statements and canonical W2 graph mutations.
