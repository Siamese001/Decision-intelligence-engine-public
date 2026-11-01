---
trigger: always_on
---

# HITL Decision Governance: Atomic Presentation, Options, and Confidence Assignment

Governs Human-in-the-Loop (HITL) decisions across Ingestion, Decision Intelligence, Knowledge Graph, and Release Lifecycle.
Canonical Terminology & Separation of Concerns: See [Operator Decision Glossary](../../governance/OPERATOR_DECISION_GLOSSARY.md) and [`config/operator_decision_vocabulary.py`](../../config/operator_decision_vocabulary.py).

## Core Principles

1. **Atomic Presentation (One at a Time)**:
   - Decisions must be presented strictly one at a time. Never batch, bundle, or aggregate multiple review items into a single turn or multi-question form.
   - Each decision must be independently resolved before introducing the next.

2. **Interactive Clickable Options**:
   - You must use the `ask_user_question` tool to present options interactively.
   - Provide concrete, mutually exclusive options. Plain text markdown lists for options are strictly forbidden. Default option marked `(Recommended)` with rationale.

3. **Assigned Confidence Levels & Meaningful Uncertainty Governance**:
   - Every option must declare an explicit confidence level: tier (`HIGH` $\ge 85\%$, `MEDIUM` 50%–84%, `LOW` <50%) and quantitative score: `(Confidence: TIER (N%))` or clean conviction indicator.
   - Grounded in verified graph evidence, source provenance, and Beta posterior distributions.
   - **Prohibition of Meaningless Intervals**: When historical sample count $n=0$ or empirical evidence is uncalibrated, bracketed credible intervals (`[5%–95%, n=0]`) MUST NOT be rendered in user-facing option labels. Zero-sample intervals provide zero information and violate Zero-Jargon Layman Communication. Bracketed credible intervals (`[lo%–hi%, n=K]`) are permitted ONLY when historically mature ($n \ge 30$, interval width $< 0.20$) under `hitl.uncertainty_display` (`sparse_only`).

4. **Persistent Feedback Storage & Prior Calibration (Schema 1.1.0)**:
   - Resolved operator choices recorded in `hitl_decision_ledger.json` and SQLite store with Merkle-linked hashing.
   - Enriched Telemetry Fields (Schema 1.1.0):
     - `outcome_type`: `AFFIRMED` (recommended option accepted), `OVERRIDDEN` (recommended option rejected in favor of alternative), or `AUTONOMOUS` (decisive margin bypass).
     - `margin_at_decision`: The $\Delta = c_{\text{top}} - c_{\text{second}}$ value at presentation time.
     - `confidence_interval_at_decision`: $[lo, hi]$ bounds for the selected option.
     - `sample_count_at_presentation`: Number of historical records informing calibration.
   - **Bounded Bayesian Updates**: Historical answers act as empirical priors (Dirichlet conjugate with Laplace smoothing $\alpha = 1$, clamped to $[\epsilon, 1 - \epsilon]$ with $\epsilon = 0.05$ per `system-learning-and-feedback.md`).

5. **Ambiguity Gating (SSOT Margin Rule)**:
   - Margin $\Delta = c_{\text{top}} - c_{\text{second}}$.
   - **Surfacing Trigger**: Surface a decision to the operator whenever `needs_operator_decision(c_top, c_second)` returns True (i.e. $\Delta \le \text{threshold}$, governed SSOT in `dual_pipeline.parameters`, pinned to 20% (`0.20`) inclusive).
   - **Autonomous Proceed Gate**: When $\Delta$ strictly exceeds the 20% SSOT threshold (e.g. top option 90%, second option 50%, spread 40% $> 20\%$) and top conviction meets the 65% floor, the top-ranked option is decisively resolved; do NOT call `ask_user_question` for non-Tier-0 decisions—proceed autonomously and record an `AUTONOMOUS` decision receipt in `hitl_decision_ledger.json`.
   - **Non-Waivable PR Merge Gate (`PR_MERGE_AUTHORIZATION`)**: Merging or promoting a branch into `main` is a `TIER_0_IRREVERSIBLE` decision that ALWAYS requires explicit human operator authorization via `ask_user_question` recorded in `hitl_decision_ledger.json`, regardless of confidence levels or margin spread.

6. **Synthetic Stop Hook Rejection & Plan-Only Firewall**:
   - Synthetic auto-approvals emitted by IDE stop hooks are strictly rejected by `validate_approval_origin()` unless the immediately preceding human message explicitly authorized implementation.
   - When `PLAN ONLY` is requested, execution without a subsequent human instruction is strictly forbidden.

7. **Anti-Autonomous Speaker Attribution Invariant**:
   - Once a meeting enters a diarization-error, speaker-attribution-ambiguity, or single-speaker-collapse state (where turn share exceeds 85.0% across >=10 turns in a multi-attendee session), the assistant MUST NOT autonomously alter any authoritative transcript or attribution artifact in that meeting's lineage.
   - Prohibited operations include autonomously relabeling speakers, inserting/deleting/merging/splitting turns, rewriting attribution evidence, and fabricating verification statements.
   - Modifying or overwriting `01_*` or `output/*` files using `write_to_file` or `replace_file_content` following an attribution failure without prior operator approval is strictly prohibited and fails closed.
   - The assistant MUST halt authoritative processing and request authenticated human review via `ask_user_question`.
8. **Cryptographic Ambiguity Lock Register (ALR) Receipt Requirement**:
   - Any claim of `(Operator Verified)`, `[OPERATOR VERIFIED]`, or operator confirmation in Output 01 Ambiguity Lock Register or downstream attribution tables MUST be backed by a valid, verifiable Ed25519 signature over RFC 8785 (JCS) canonical payload bytes in `hitl_decision_ledger.json`.
   - Unauthenticated claims fail Gate 1.7 closed with `UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM`.
   - Caller-asserted `hitl_receipt_bound: True` booleans are strictly discarded; the verifier alone computes this status from cryptographic evidence.

9. **Active Ingestion Approval Continuation & Anti-Fabrication Invariant**:
   - Ingestion approval is a continuation of the same run, never an optional manual rerun step.
   - When exact scoped authorization is absent, ingestion persists an `active-ingestion-approval-request` and presents the exact delta via `ask_user_question` (or host adapter), entering `AWAITING_APPROVAL`.
   - Authentic operator resolution resumes the same run through publication and activation without re-prompting or duplicate extraction.
   - Default options, mock reviewer environment variables (e.g. `REVIEWER=auto`), generated rationales, empty callbacks, or synthetic approval strings cannot authorize a graph mutation. An unanswered request leaves the run safely pending (`AWAITING_APPROVAL`). Declined requests record `DECLINED` / `CANCELLED`.

10. **Prohibition of Destructive Shell Lock Remediation**:
    - Proposing or executing raw shell lock deletions (e.g. `rm -f *.lock`, `rm -f .../index.lock`) or destructive git resets (`git reset HEAD`) to remediate worktree locks is strictly prohibited.
    - All worktree lock recovery and interrupted checkout remediation MUST route strictly through the governed repair tool:
      `.venv/bin/python tools/pr_governance.py repair-index <worktree>`
    - Any HITL option proposing raw shell lock deletion fails closed via `dual_pipeline.hitl_governance_core.question_validator`.

11. **Decisive Recommendation, Zero-Fence-Sitting & Post-Wave Transition Gate (`INV-DECISIVE-RECOMMENDATION`)**:
    - Assistants MUST NEVER emit disjunctive advice or non-committal compound options in chat prose (such as *"Ready for next direction: we can address X, or proceed with Y"*, *"rebase onto main or archive"*).
    - Whenever a wave commit, live replay, or partial-pass synthesis run concludes with competing next-step workflows without a decisive margin ($\Delta \le 20\%$), the assistant MUST immediately invoke `ask_user_question` with structured, confidence-calibrated options rather than offering choices in prose. When $\Delta > 20\%$ on a non-Tier-0 choice, execute the top-ranked option autonomously.

12. **Post-Edit Commit, Rebase & Promotion Authorization Gate**:
    - Whenever an assistant turn mutates repository files (source code, configs, rules, or deliverables), the assistant MUST NOT end its turn with explanatory prose alone.
    - The assistant MUST invoke `ask_user_question` to present the recommended branch commit, rebase onto `main`, and local `main` PR promotion option (`PR_MERGE_AUTHORIZATION`), backed by calibrated confidence levels.
    - Enforced mechanically by `scripts/hitl_post_invocation_guard.py`, which intercepts turns with unprompted file mutations and injects a forced continuation prompt.


---

## Speaker Assignment Evidence Context

- Every speaker assignment choice must show the target turn with the two preceding and two following transcript turns, when those turns exist in the same source. Show explicit boundary counts when fewer turns exist; preserve transcript wording and speaker labels.
- **Mandatory Multi-Row Formatting**: Turn markers (`BEFORE 2`, `BEFORE 1`, `TARGET`, `AFTER 1`, `AFTER 2`) must never share a line or collapse into a single continuous row. Each turn must occupy its own distinct row separated by `\n\n` paragraph breaks or Markdown list items.
- A speaker assignment must identify the target turn and bind the approval to the exact visible context digest. Do not apply an approval when context is missing, stale, or spans sources.
- Render `UNRESOLVED` in bold (`**UNRESOLVED — <Speaker>**`) or distinct visual styling wherever the target turn has no resolved speaker. Keep the unresolved state explicit; “cannot tell” records no attribution change.
- Grouped review must remain atomic per cluster, with one context window for each target turn. Uncertain individual turns must be presented one at a time.

## Decision Structure
Use `ask_user_question` with structured parameters:
- `question`: "HITL Decision: [Title]\n\n**Candidate ID**: `<id>`\n**Context**: [Description]"
- `options`:
  - "(Recommended) [Option A] (Confidence: HIGH (92%)) — [Action, Evidence, Risk]"
  - "[Option B] (Confidence: MEDIUM (60%)) — [Action, Evidence, Risk]"

---

## Planning-Mode HITL Override
The planning-mode instruction "Do not use the `ask_user_question` tool" does NOT apply to design decisions driving implementation choices.

A question is a **blocking design decision** (requires `ask_user_question` before Proceed) when ALL of:
1. It determines a code path, API surface, protocol, or data contract.
2. It cannot be deferred without silently choosing a default that may be wrong.
3. The confidence margin $\Delta = c_{\text{top}} - c_{\text{second}}$ satisfies `needs_operator_decision(c_top, c_second)` ($\le 20\%$ inclusive) between the top two options.

A question is a **deferrable clarification** (may be embedded in plan prose) when:
- It is a naming or labeling preference with no structural impact, OR
- $\Delta$ strictly exceeds the SSOT threshold so the agent can autonomously proceed with the top option and record an audit receipt.

No implementation plan artifact may present a `Proceed` button while unscored design decisions remain inline as prose.
