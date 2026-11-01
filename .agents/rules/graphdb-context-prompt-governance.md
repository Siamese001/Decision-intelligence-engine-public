---
trigger: model_decision
---
# GraphDB Context Prompt & Multi-Channel Output Governance

Governs any operator request to generate a prompt, meta-prompt, or context-pack query that traverses `graph.sqlite` (or produces onboarding / deep-context deliverables for Word, Microsoft Outlook, and Microsoft Teams).

## 1. Trigger Scope
Activates whenever the operator asks to:
- Generate a prompt or prompt template to query GraphDB (`graph.sqlite`) for an onboarding care package, performance review, workstream context brief, or stakeholder/project deep dive.
- Execute a GraphDB context prompt to produce Word (`.docx`), Microsoft Outlook, or Microsoft Teams copy-paste outputs.
- Draft an ad-hoc question, status update, or reply for a team or stakeholder intended for Microsoft Teams, Slack, or Microsoft Outlook copy-paste.

## 2. Five Non-Negotiable Governance Pillars

### Pillar A: Exhaustive GraphDB Traversal & Target-Scoped Roster Hygiene (`FAIL-CLOSED`)
1. **Canonical & Evidence Traversal**:
   - Traverse `source`, `node`, `assertion`, `evidence_item`, and `search_document`.
   - Resolve all split-identity or alias nodes for the target person or workstream, and order all meeting sources chronologically (`ORDER BY s.source_date ASC, s.source_name ASC`), inspecting `assertion.scope_text`, `object_literal`, and `evidence_item.direct_quote`.
2. **Target-Relative Workstream Isolation (`Zero Cross-Workstream Bleed`)**:
   - Check `qualifiers_json` for `"boundary_type": "disjoint_workstream_exclusion"` and `"excluded_workstream"` **relative to the requested target workstream**.
   - Exclude unrelated charters, departed consultants, and offboarded personnel who have zero delivery role on the requested workstream. Never hardcode a single project's roster exclusions, acronyms, or architecture into prompts for another workstream.
3. **No Unrequested 30-Day Tactical Roadmaps**:
   - Omit unrequested 30-day tactical execution plans unless explicitly requested by the operator.

### Pillar B: Strict Anti-AI Filter & Human Voice (`ZERO EM-DASHES`)
1. **Hard Punctuation Ban**:
   - **NEVER** output an em-dash (`\u2014`), en-dash (`\u2013`), or double-hyphen spacer (` -- `) in generated prompts, Word documents, Outlook emails, or Teams messages.
   - Replace every dash pause with a comma, colon, parentheses, or period. Standard hyphens (`-`) are restricted strictly to compound modifiers (`cross-account`, `real-time`, `end-to-end`, `in-house`).
2. **Lexical Blocklist (Zero AI Slop)**:
   - Banned words: `delve`, `testament`, `tapestry`, `crucial`, `pivotal`, `foster`, `underscore`, `moreover`, `furthermore`, `synergy`, `holistic`, `paradigm`, `seamless`, `beacon`, `landscape`, `elevate`, `leverage` (as verb).
   - Banned transitions: `"It is important to note"`, `"In conclusion"`, `"Let's dive in"`, `"Here is a breakdown"`.
   - Write in a direct, pragmatic human operator voice using active verbs and natural contractions (`don't`, `won't`, `it's`, `we've`).

### Pillar C: Mandatory Section Introductions & Plain-English Grounding
1. **Every Section Requires an Introduction**:
   - Every section MUST open with a 3 to 5 sentence `Section Introduction` paragraph explaining the context and why it matters before any bullets or tables appear.
2. **Plain-English Acronym Primer First**:
   - Never drop target domain acronyms cold. Define every acronym relevant to the target workstream in plain human language in Section 1 before diving into technical architecture or evaluation details.

### Pillar D: Diplomatic Framing & Structured Evidence Tables
1. **Zero Bus-Throwing**:
   - Surface all candid meeting pain points, verbatim client quotes, and delivery lessons from `graph.sqlite` WITHOUT blaming existing pod engineers or delivery leads.
   - Credit foundational engineering or delivery lift first, framing friction as structural gaps paired with actionable operating playbooks or objective evidence.
2. **Structured 3-Column Readability Table**:
   - Render pain points, lessons learned, or verbatim evidence in a clean 3-column table tailored to the prompt's objective (e.g., `Pain Point & Anti-Pattern | Graph-Verified Context | Operating Playbook` for onboarding, or `Date & Source | Deliverable / Risk Area | Verbatim Graph Evidence` for performance reviews).

### Pillar E: Multi-Channel Copy-Paste Ready Outputs (Word, Outlook, Teams)
1. **Microsoft Word (`.docx`) + RTF Clipboard**:
   - Narrow `0.5"` (`720 dxa`) margins, `10800 dxa` table width, `19pt` title, `15pt` section headings, `12pt` bold navy first column on `#EDF2F7` fill, `11.5pt` body/cells, `<w:cantSplit/>` on all rows, `<w:keepNext/>` across sections, AppleScript close-and-reopen in Word, and `pbcopy -Prefer rtf`.
2. **Microsoft Outlook Copy-Paste Block**:
   - Includes `Subject:`, `To:`, `Cc:`, warm first-person opening, scannable bold sections, zero raw Markdown symbols (`**`, `#`, backticks), and zero em-dashes.
3. **Microsoft Teams Copy-Paste Block**:
   - First-person conversational operator voice, short 1 to 2 sentence paragraphs, simple hyphen (`-`) bullets that read aloud naturally, zero wide tables, zero raw Markdown bold syntax (`**...**`), zero backticks, zero `#` headings, and zero em-dashes.

### Pillar F: Project-End Performance Evaluation (`SBAI+` Dual-Source Standard)
Whenever the prompt or deliverable targets a stakeholder performance review or project-end evaluation:
1. **Dual-Source Retrieval (GraphDB + Meeting Transcripts)**:
   - Query `graph.sqlite` (`node`, `assertion`, `evidence_item`, `source`, `search_document`) AND trace every target `source_id` to its resolved meeting artifacts (`01_hitl_resolved_transcript.md`, `02_executive_recap.md`, `03_canonical_signal_projection.json`) via `EvidenceLocatorService` to capture behavioral nuances, facilitation quality, and verbatim dialogue not stored in graph triples.
2. **Six Mandatory Evaluation Calibration Controls**:
   - **Tenure Window & Inherited-State Baseline**: Anchor exact onboarding and offboarding/cutoff dates from graph assertions and earliest/latest transcript timestamps; separate inherited precursor debt (e.g., zero-overlap handoffs) from owned execution during active tenure.
   - **Role-Calibrated Rubric**: Evaluate strictly against the target's actual charter role (e.g., Delivery Lead / Engagement PM: SOW/milestone governance, scope defense, client risk escalation, cross-team dependencies, and pod facilitation vs. hands-on coding).
   - **First-Party vs. Third-Party Evidence Separation**: Distinguish (a) direct observable speaker turns by the evaluatee, (b) leadership/peer feedback in debriefs or 1:1s, and (c) shared pod outcomes.
   - **Commitment Follow-Through (Say-Do Tracking)**: Trace action items assigned to the evaluatee in `02_executive_recap.md` and `03_canonical_signal_projection.json` across consecutive meetings to verify closure.
   - **Ramp Velocity & Trajectory Curve**: Compare early-tenure vs. late-tenure meeting performance to measure ramp speed and shift toward proactive governance.
   - **Fail-Closed "Not Observed" Guardrail**: Explicitly label off-camera competencies (e.g., offline financials or unrecorded emails) as `Not Observed in Graph/Transcripts` rather than guessing.
3. **SBAI+ Evaluation Format**:
   - Structure each evaluated competency with explicit `Situation`, `Behavior`, `Action`, `Impact`, and `+` (`Plus / Continue & Stretch`) blocks.
