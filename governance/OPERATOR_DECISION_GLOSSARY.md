# Operator Decision Governance Glossary

This document serves as the repository Single Source of Truth (SSOT) defining the terminology used for human-in-the-loop interactions, ambiguity evaluation, and decision persistence.

---

## 1. The Disambiguation of "HITL"

Historically, the acronym **"HITL"** (Human-in-the-Loop) was overloaded across four conflicting operational domains:
1. An ambiguity margin calculation (`requires_hitl` or `hitl_required`).
2. A binary tool permission gate (`HITL_REQUIRED_TOOLS` or `Decision.ASK_USER`).
3. An unattended execution halting status (`HITL_REQUIRED`).
4. An interactive tool name or invocation vehicle (`ask_question`, `ask_user`).

This conflation caused automated checkpoints to confuse permission checks with option selection, allowed scheduled runs to silently swallow required decisions, and obscured why interactive prompts were failing to fire.

To resolve this ambiguity, the terminology is decomposed into **six non-overlapping architectural concepts**.

---

## 2. Canonical Vocabulary Table

| Canonical Term | Concept | Plain-English Definition | Replaced Legacy Terms | Operational Scope & Boundary |
| :--- | :--- | :--- | :--- | :--- |
| **`ask_user_question`** | **Channel** | The **only** interactive mechanism for presenting multiple-choice questions to the operator. | `ask_question`, `ask_user`, `ask_question_payload` | Interactive UI sessions only. Never called in headless, unattended, or scheduled runs. |
| **`needs_operator_decision`** | **Ambiguity Rule** | The mathematical evaluation rule requiring adjudication when the top-ranked and second-ranked options have a confidence margin $\le 20\%$ (resolves autonomously when $> 20\%$). | `requires_hitl`, `hitl_required`, margin checks | Evaluated during candidate ranking; triggers `ask_user_question` in interactive mode. |
| **`TOOL_APPROVAL_REQUIRED`** | **Tool Guard** | A binary yes/no confirmation gate required before executing expensive, destructive, or external tools. | `HITL_REQUIRED_TOOLS`, `Decision.ASK_USER` | Tool invocation gate only. Never formulates multiple-choice options or writes decision records. |
| **`OPERATOR_INPUT_REQUIRED`** | **Run Outcome** | The terminal outcome emitted when a scheduled, daemon, or unattended run halts because human input is required. | `HITL_REQUIRED` terminal outcome | Terminal outcome for non-interactive runners when `needs_operator_decision` evaluates to true. |
| **`decision_record`** | **Persistent Store** | An immutable record of an operator's verified response stored in the persistent decision ledger. | ledger entries, resolutions, adjudications | Written exclusively via `decision_record_writer` with file locking and Merkle hash verification. |
| **`approval_signature_valid`** | **Approval Proof** | Cryptographic Ed25519 signature proof attesting that an approval or decision originated from an authentic operator. | `hitl_receipt_bound` | Verified independently by governance gates; caller-asserted booleans are strictly discarded. |

---

## 3. Strict Separation of Architectural Concerns

### A. Channel vs. Ambiguity Rule
- `ask_user_question` is a **communication channel**, not a policy. It does not decide *when* to ask; it only formats and delivers the interactive modal to the operator.
- `needs_operator_decision` is the **policy rule**. It evaluates whether $\Delta = c_{\text{top}} - c_{\text{second}} \le 20\%$. If true, the system MUST invoke `ask_user_question` (in interactive mode) or halt with `OPERATOR_INPUT_REQUIRED` (in headless mode). When $\Delta > 20\%$, autonomous resolution is permitted.

### B. Tool Approval vs. Decision Record
- `TOOL_APPROVAL_REQUIRED` is a binary security check (e.g. allowing `gcloud` or graph mutations). It carries no multiple-choice alternatives, no Bayesian priors, and no confidence margin.
- `decision_record` stores substantive choices between distinct options. Tool approvals MUST NEVER be written into the decision ledger as if they were option choices.

### C. Interactive Asking vs. Unattended Execution
- Headless, scheduled, or daemon executions CANNOT present interactive prompts. When an unattended task encounters `needs_operator_decision == True`, it MUST immediately halt and record `OPERATOR_INPUT_REQUIRED`.

---

## 4. Code and Governance References

- Python SSOT: [`config/operator_decision_vocabulary.py`](../config/operator_decision_vocabulary.py)
- Operating Rules: [`AGENTS.md`](../AGENTS.md) § "HITL decisions are atomic, optionated, and confidence-scored"
- Rule Enforcement: [`.agents/rules/hitl-decisions.md`](../.agents/rules/hitl-decisions.md)
- Linter: [`tools/lint_operator_decision_terms.py`](../tools/lint_operator_decision_terms.py)
