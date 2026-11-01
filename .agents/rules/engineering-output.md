---
trigger: always_on
---

# Engineering Output & Concise Reporting Rules

Governs response format, concise reporting, behavior observation, and delivery boundaries for engineering turns. Grounded in `governance/policy_registry.yaml` (`INV-OUTPUT-MAX-WORDS`, `INV-OUTPUT-MAX-CHARS`, `INV-OUTPUT-PROGRESS-MAX-WORDS`).

---

## 1. Canonical Reporting Contract for Engineering Turns

For ordinary engineering completions (code modification, bug fixes, refactoring, test execution, wave progression):

1. **Concise Completion Budget**:
   - Target **approximately 120 words**; hard maximum **300 whitespace-delimited words** (`INV-OUTPUT-MAX-WORDS`).
   - Hard maximum **3,500 raw characters** (`INV-OUTPUT-MAX-CHARS`).
   - Lead directly with the executed action, input, and observed effect. Explicitly identify whether execution occurred locally, in an isolated sandbox, or in production.
   - Disclose material failures, incomplete scope, and the next required action.

2. **No Process Inventory vs. Required Deliverable Results**:
   - Strictly omit visible scratchpads, model telemetry, tool lists, cryptographic hashes, internal record IDs, all-wave implementation tables, procedural file inventories (e.g. lists of touched/read source code files), and generic compliance boilerplate.
   - **Translate Technical Mechanisms to Business Meaning**: Technical plumbing, internal functions, database internals, and code syntax must never appear in user-facing prose. Translate technical mechanisms into real-world business impacts (such as data reliability, execution speed, or billing separation) before presenting them to stakeholders.
   - **Plain-Language Precedence Over Raw Code Citations**: Plain language strictly takes precedence over raw code citations. When Rule 3 (evidence-grounded verification) mandates citing file contents or execution results, the agent MUST articulate the demonstrated behavior and verified outcome in plain English words. Raw file paths, snake_case identifiers, code tokens, and math symbols belong exclusively in durable internal files (e.g. logs, ledgers) and clickable deliverable links in the dedicated Deliverables section, NEVER in narrative prose.
   - **Deliverable Links Are Results, Not Process Inventory**: Generated outcome deliverable links (such as meeting transcripts, executive recaps, signal projections, and evaluation receipts) are primary execution results and MUST be explicitly linked using clickable file links (`file:///...`) when present, even under concise reporting.

3. **Success Requires Observed Behavior**:
   - Claims of success require running the normal user entry point with meaningful input and observing effects via an independent verification path.
   - Checking a function's self-returned success flag or in-memory boolean is insufficient proof of state change.
   - Fixtures may supply inputs but must never stub out or replace the behavior under evaluation.

4. **Claim Scoped to Demonstrated Effect**:
   - A helper test supports only a helper claim; a unit test supports only unit verification; an isolated sandbox run supports only a sandbox claim.
   - If a workflow was not executed, state that plainly. Planning turns report the plan; they must never claim functional runtime success.

5. **Freshness and Failure Retention**:
   - Observations bind strictly to current executed source code, configuration, target, and chronological run sequence.
   - A subsequent failure, interrupted execution, or unverified edit immediately invalidates prior success claims.
   - Unresolved failures must be preserved across all requested behaviors.

6. **Minimal Progress Updates**:
   - Conversational progress updates are capped at **20 words** per update (`INV-OUTPUT-PROGRESS-MAX-WORDS`).
   - Updates convey a concrete finding, blocker, or next verification step. Never replay tool calls or simulate progress via sleep loops.

7. **No Length Loopholes**:
   - Word and character counts apply to all final delivered text, including headings, labels, code snippets, tables, and collapsible blocks.
   - Routine completion reports must not be shifted into unsolicited file attachments or links.
   - Required outcome deliverable links (e.g. Output 01 transcript, Output 02 recap, Output 03 signal projection, and LLM judge evaluation for meeting ingestion, or corresponding modality outputs) are mandatory results and must be presented with accurate labels and resolvable paths within the concise budget.
   - Explicitly requested deliverables (such as implementation plans, architecture designs, or requested diagnostic dumps) are separate deliverables exempt from the completion cap.
   - Material failure disclosure takes precedence over brevity and is recorded as a governed exception.

8. **Factual and Internal Operational Records**:
   - Runtime receipts, gate verifications, and execution logs remain fully recorded in durable internal files (`reports/`, ledgers, SQLite stores).
   - Private deliberation is never collected. Concise diagnostics are provided only upon explicit operator request.

9. **Governed Deliverable Links Presentation**:
   - When an engineering turn executes source ingestion (meetings, documents, notes), the completion response MUST present clickable markdown links (`file:///...`) for each generated deliverable:
     - **Meeting Transcripts (3 Outputs + Judge)**:
       - Output 01 (Transcript): `[Output 01 (Transcript)](file:///path/to/01_..._hitl_resolved_transcript.md)`
       - Output 02 (Recap): `[Output 02 (Recap)](file:///path/to/02_..._executive_recap.md)`
       - Output 03 (Signal Projection): `[Output 03 (Signal Projection)](file:///path/to/03_..._canonical_signal_projection.json)`
       - LLM Judge Evaluation: `[LLM Judge Evaluation](file:///path/to/llm_judge_evaluation.json)` (verdict, composite score)
     - **Documents**: Output 03 (Signal Projection), source notes, and judge evaluation.
     - **Notes**: Output 03 (Signal Projection) and evaluation.
   - **Truthful Status & Isolation**: Every link must resolve to an existing file. Truthfully state the judge verdict and publication status (e.g. package-only vs active graph mutation). Never claim canonical graph publication when `--package-only` was used or mutation is pending. Missing or failed artifacts must never be advertised as passed.

10. **Stakeholder Plain-Language & Anti-Jargon Invariant (Universal Layman Rigor)**:
    - Across **all** user-facing outputs—including ordinary engineering turns, Root Cause Analyses (RCAs), system diagnostics, debugging explanations, executive briefings, progress updates, and interactive questions—use clear layman English paired with concrete operational rigor (exact numbers, verified counts, outcomes, and plain-English cause-and-effect explanations) and strictly enforce five negative constraints:
      1. **Zero Code & Query Syntax**: Strictly ban inline code, SQL statements (`SELECT ... FROM`), programming language expressions, function/method signatures, variables, and schema syntax from explanatory prose.
      2. **Zero Internal File & Table Names in Prose**: Ban raw repository file paths, script names (`.py`, `.sqlite`, `.json`), database table/column names, and directory structures from user-facing explanations. Reference deliverables and files solely by business function using plain-English clickable link labels (e.g., `[Database Count Checker](file:///...)`).
      3. **Zero Math Symbols**: Ban mathematical symbols, statistical formulas, Greek letters (such as $\kappa$, $\Delta$), and uncalibrated brackets.
      4. **Zero Architecture & Plumbing Acronyms**: Ban software and database plumbing acronyms (such as ACID, WAL, JSONL, AST, DDL, PRAGMA) without translating them into concrete everyday meaning (e.g., crash-proof data storage, instant document recovery, temporary scratchpad file).
      5. **Zero Programming Keywords, Snake-Case Tokens, & Git Plumbing**: Strictly ban language keywords (`def`, `class`, `import`, `return`, `self`), `snake_case` or `PascalCase` code identifiers, git plumbing tokens (`worktree`, `cherry-pick`, `rebase`, raw commit hashes), and library class names (`Pydantic`, `MutationGuard`) from explanatory text.
    - **Layman Explanations with Concrete Technical Rigor**: Never water down facts. Explain *why* and *how* the system behaved using clear everyday analogies and plain cause-and-effect logic backed by exact numbers (e.g., "+1 source document, +4 core entities, +19 verified facts, 96/100 quality score") so a non-programmer understands the exact technical reality without reading code tokens.
    - **Domain Terms Defined**: Allowed domain terms are strictly business, operational, and functional concepts (e.g., "seat license", "client billing", "document throughput", "audit trail", "crash recovery", "permanent knowledge library"). Under no circumstances do internal programming identifiers, function names, or software plumbing qualify as domain terms.
    - All technical capabilities and root causes must be articulated through real-world operational mechanics and business impacts: client delivery quality, cost savings, operational reliability, workflow efficiency, and executive confidence.

11. **Decisive Recommendation & Zero-Fence-Sitting Standard (INV-DECISIVE-RECOMMENDATION)**:
    - Assistants MUST NEVER emit disjunctive advice or non-committal compound options in chat prose (such as *"rebase onto main or archive"*, *"commit and rebase or prune"*).
    - When asked for disposition or status, assistants MUST deliver a single concrete recommendation grounded in empirical verification.
    - If uncertainty or competing options exist without a dominant margin ($\Delta \le 20\%$), the assistant MUST NOT guess in prose; it MUST immediately escalate via the interactive `ask_user_question` tool with structured, confidence-calibrated options.

---

## 2. Delivery Surface Classification & Enforcement Boundary

| Surface | Nature & Enforcer | Enforcement Authority |
| :--- | :--- | :--- |
| **Saved Summary / Walkthrough** | Saved markdown artifacts (`reports/walkthrough_*.md`) | **Machine-Enforced**: Verified by `tools/validate_implementation_summary.py` and CI readiness gates. |
| **Repository-Owned Response** | CLI commands (`nee` CLI, test runners, validation tools) | **Machine-Enforced**: Evaluated via automated return codes and stdin/stdout validators. |
| **IDE Answer (Chat Stream)** | Final assistant response delivered to operator in chat UI | **Instruction-Only**: Antigravity host hooks lack a final-text interception/redaction callback; governed by system instructions and prompt rules. |
| **Host Activity Cards** | IDE UI widgets (tool pills, thinking drawers, task status) | **Instruction-Only**: Host-internal UI generated entirely by the host client outside repository reach. |

---

## 3. Preserved Non-Engineering Routes

The concise engineering format applies strictly to engineering turns. Other governed routes retain their specific contracts:
- **Copilot Mode (`nee copilot`)**: Emits single fenced block of grounded speakable comments with zero surrounding prose.
- **Knowledge Queries (`nee query`)**: Governed by the Two-Part Seamless Query & Executive Notes Invariant (`INV-QUERY-CLEAN-PROSE`). Delivered as external-ready, scannable executive notes without dense paragraphs, parenthetical stuffing, unexplained acronyms, or inline footnote markers (`[1]`, `[2]`), making content immediately copy-ready for external sharing. Raw assertion hashes (`ast_*`, `asrt_*`, `ev_legacy_edge_*`), `snake_case` node tokens (`workstream_*`, `sow_*`), raw predicates (`HAS_CLAIM_TEXT`), and inline database file URLs are strictly prohibited in body text. Traversal evidence is consolidated into a dedicated `### Evidence & Graph Lineage` footer containing a **Graph Traversal Summary** telemetry callout (`**Graph Traversal Summary:** <N> assertions evaluated across <M> entities and <K> distinct sources (<C> overall confidence)`) and a deduplicated **Source & Domain Coverage Table** (`| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |`). Knowledge query responses and their lineage tables are strictly exempt from the 120/300-word engineering turn completion ceiling (`INV-OUTPUT-MAX-WORDS`) so query answers are never artificially compressed or truncated.
- **Entity Advisory & Executive Prep Queries (`INV-QUERY-ENTITY-ADVISORY-TRAVERSAL`, `INV-GROUNDING-NO-OUTPUT-FILESYSTEM-BYPASS`)**: Executive agenda, talking-point, and briefing turns must execute GraphDB traversal and emit grounded payloads certified under `dual_pipeline/grounding/delivery_boundary.py`. Running directory enumeration (`ls`, `tree`, `stat`) or file scans on `output/` is strictly prohibited.
- **HITL Governance**: Atomic presentation via `ask_user_question` with structured options, confidence scores, and SSOT ambiguity margin evaluation.
- **PR Queue / Branch Summaries**: Unified queue tables governed by `pr-queue-summary.md`.
- **Runtime Evidence & Isolation**: Sandboxing and evidence requirements remain non-negotiable across all routes.
