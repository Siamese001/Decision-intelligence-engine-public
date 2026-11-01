# MICROSOFT TEAMS RAW TRANSCRIPT SYNTHESIS CONTRACT (SSOT)
## Protocol: `TEAMS_RAW_TRANSCRIPT_SYNTHESIS:1.0.0`
## Canonical Authority: `governance/decision_intelligence/TEAMS_RAW_TRANSCRIPT_SYNTHESIS_PROMPT.md`

> **Scope**: Authoritative Single Source of Truth (SSOT) prompt contract governing the synthesis of
> Microsoft Teams native transcripts (`.docx`, `.txt`, `.vtt`, WebVTT, CoPilot exports) into the
> governed three-artifact delivery triad (Outputs 01, 02, and 03).

---

## 1. INGESTION MODALITY: MICROSOFT TEAMS NATIVE TRANSCRIPT

Microsoft Teams transcripts contain native speaker attribution and chronological turn markers:
- Turn Format: `Speaker Name  HH:MM`, `Speaker Name [HH:MM:SS]`, or `<v Speaker Name>`.
- Spoken text follows each speaker marker.
- Attendees are known participants within the enterprise tenant or invited guest partners.

### Core Processing Invariants:
1. **Preserve Native Attributions**: Never overwrite, strip, or merge distinct native speaker turns. Every turn must retain its identified speaker.
2. **Knowledge Graph Attendee Grounding**: All speaker names and participants must be cross-referenced against the Knowledge Graph and verified enterprise directory:
   - **PartnerAdvisory Attendees**: Never classify PartnerAdvisory personnel (e.g., Jen Pan, Amit Ayer, Tyler McCord, Stephan Marceau, Dan Okray, Alex Sanchez, Yan Gobeil) as "Client / Partner" or "Unknown".
   - **Client / Partner Stakeholders**: Never truncate full names or omit titles for verified stakeholders (e.g., resolve Mandy Hopmann / Mandy Bambenek Hopmann, Product Manager, Apex Global / NEA).
3. **Artifact Independence Firewall (Rule C-19)**:
   - Output 01 (Forensic Transcript) and Output 02 (Executive Recap) are 100% team-safe for joint PartnerAdvisory + Client consumption.
   - Private career strategy, personal negotiation leverage, and informal power dynamics belong exclusively in the local encrypted shadow graph (`INTERNAL_ADVISORY`), never in Outputs 01 or 02.

---

## 2. THE THREE-ARTIFACT DELIVERY CONTRACT

### Output 01: Forensic Source-of-Truth Transcript
`01_<meeting_name>_<date>_<time>_hitl_resolved_transcript.md`

Must adhere strictly to this structure:
```markdown
# [Meeting Title] -- HITL-Resolved Transcript

**Date:** MM/DD/YYYY  
**Time:** HH:MM AM/PM - HH:MM AM/PM ET (Duration: ~XXm)  
**Platform Reference:** Microsoft Teams  
**Participants:**
- [Company Name]: [Full Name] ([Title/Role]), ...

---

## Forensic Spoken Transcript

**[HH:MM] Full Speaker Name:** Cleaned verbatim dialogue...

---

## Ambiguity Lock Register

| Timestamp | Raw Acoustic / ASR Token | Forensic Interpretation / Context | Status | Affected Statement IDs |
| :--- | :--- | :--- | :--- | :--- |
| **00:08** | [token] | [interpretation] | LOCKED | ev-01 |
```

### Output 02: Executive Recap & Action Brief
`02_<meeting_name>_<date>_<time>_executive_recap.md`

Must adhere strictly to this structure:
```markdown
# Subject: [Meeting Title] -- Executive Strategic & Technical Brief | MM/DD/YYYY

Date: MM/DD/YYYY  
Time: HH:MM AM/PM - HH:MM AM/PM ET (Duration: ~XXm)  

Attendees:
- [Company 1]: [Name] ([Title/Role]), ...
- [Company 2]: [Name] ([Title/Role]), ...

# Meeting Objective
[Clear concise objective statement]

# Key Discussion Points & Findings
[Executive opportunity setup, operational context, technical landscape, scope boundaries]

# Next Steps & Action Items
| # | Action Item / Deliverable | Target Workstream | DRI Owner | Due Date | Spoken Locator |
| :- | :--- | :--- | :--- | :--- | :--- |
| 1 | [Deliverable] | [Team] | [Owner] | MM/DD/YYYY | [HH:MM] |

# Confirmed Decisions & Open Blockers
- Executive conclusion: ...
- Decision owner: ...
- Decision status: Confirmed | Open

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
[SSOT Gold Standard per-attendee notes detailing what each attendee contributed, raised, or committed using granular thematic sections or bullets]

# [Speaker Name] - [Organization], [Role]
[Thematic Focus Title]: [Substantive narrative detailing statements, technical reasoning, context, and takeaways]
[Thematic Focus Title 2]: [Substantive narrative on second major theme or line of inquiry]
```

### Output 03: Canonical Signal Projection
`03_<meeting_name>_<date>_<time>_canonical_signal_projection.json`

Valid JSON adhering to `evidence-envelope-intake.schema.json` with multi-speaker evidence statements and canonical W2 graph mutations.
