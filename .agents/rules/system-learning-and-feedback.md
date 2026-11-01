---
trigger: glob
globs: "{tools/govern_learning_proposals.py,dual_pipeline/learning_*.py,reports/learning_proposals/**,automations/prompts/**,tools/scheduled_task_core/**}"
---

# System Learning & Continuous Feedback Governance Rules

Governs closed-loop learning, failure cohorts, parameter adaptation, Bayesian priors, and `learning-change-proposal:1.0.0` lifecycle (`tools/govern_learning_proposals.py`).

## Core Principles

1. **Closed-Loop Feedback Contract (`learning-change-proposal:1.0.0`)**:
   - Updates must formulate a contract-valid proposal: `status: "proposed"`, `approval_required: true`, `auto_apply: false`. Autonomous self-modification is strictly prohibited.

2. **Failure Cohort Analysis & Evidence Grounding**:
   - Triggers from non-passing telemetry (`learning_event`), QA gate failures, and HITL overrides. Requires cluster of $\ge 2$ matching patterns within sliding window.

3. **Mandatory Dry-Run Simulation Gate**:
   - Cannot be approved/applied without affirmative dry-run simulation (`SimulationResult.success == True`) via `govern_learning_proposals.py simulate`.

4. **Cryptographic Approval Binding & Synthetic Firewall**:
   - Requires authentic human operator approval receipt. Synthetic auto-approvals rejected via `validate_approval_origin()`.

5. **Parameter Bounding & Rollback Guarantee**:
   - Updates validate against `ParameterRegistry`. Runtime application generates bidirectional rollback receipt for reversal via `govern_learning_proposals.py rollback`.

6. **Retrieval Clearance Preservation (11 BGE-M3 Retrieval Gates)**:
   - Proposals affecting retrieval weights must pass all 11 BGE-M3 retrieval gates (< 7,500ms latency) before activation.

7. **Bounded Bayesian Prior Calibration**:
   - Prior updating follows Dirichlet/Beta conjugate with Laplace smoothing ($\alpha = 1$), clamped to $[\epsilon, 1 - \epsilon]$ ($\epsilon = 0.05$) to prevent zero-probability degeneracy.

8. **Universal Write Gateway (UWG) Commit Lane (`learning_parameter_commit`)**:
   - Parameter and prior commits route exclusively through the Universal Write Gateway single front door.
   - Enforces cryptographic human approval verification, SSOT parameter bounds, and an immutable proof of zero graph mutation (`graph_sha256` unchanged, `+0` across all graph tables).
   - Generates dual receipts: an apply receipt (`status: "APPLIED"`) conforming to `uwg-learning-commit-receipt:1.0.0` and an exact bidirectional rollback receipt (`status: "PENDING_ROLLBACK"`).

9. **L4 Stage-Chain Sealing**:
   - Continuous telemetry records stages `LP0`–`LP4` for learning runs and `H0`–`H3` for HITL runs.
   - Stage `L4` calculates an immutable Merkle hash chain across all lifecycle events and persists cryptographic seal records into both `runs/<run_id>/run_seal.json` and the durable SQLite ledger.

## Governed Lifecycle (5-Phase Lifecycle)
`[1. INGEST] -> [2. CLUSTER] -> [3. PROPOSE] -> [4. SIMULATE] -> [5. APPROVE & APPLY VIA UWG]`
- Tools: `python tools/govern_learning_proposals.py {list,inspect,simulate,approve-and-apply,rollback,hitl-scan}`

## Governed Proposal Categories

### 1. `retrieval_weights`
Calibration of BM25 vs dense vs Colbert hybrid retrieval weights. Must pass all 11 BGE-M3 retrieval gates before activation.

### 2. `hitl_confidence`
Closed-loop calibration of Human-in-the-Loop decision scoring, prior strengths, and uncertainty quantification derived from operator override cohorts in `hitl_decision_ledger.json`.
- **Cohort Trigger**: $\ge 2$ overrides or $>60\%$ override rate within category sliding window.
- **Simulation Gate**: Brier score improvement $(\Delta_{\text{Brier}} < 0)$ replaying historical category decisions under proposed parameters.
- **SSOT Parameter Bounds**:
  | Parameter | Min | Max | Default |
  | :--- | :--- | :--- | :--- |
  | `hitl.bayesian_prior_strength` | 1.0 | 10.0 | 4.0 |
  | `hitl.human_feedback_weight` | 0.5 | 2.0 | 1.0 |
  | `hitl.autonomous_feedback_weight` | 0.01 | 0.20 | 0.05 |
  | `hitl.ambiguity_margin_threshold` | 0.05 | 0.30 | 0.20 |
- **CLI Commands**:
  - `python tools/govern_learning_proposals.py hitl-scan [--ledger-path <path>]`
  - `python tools/govern_learning_proposals.py simulate <proposal_id>`
  - `python tools/govern_learning_proposals.py approve-and-apply <proposal_id> --receipt <receipt_path>`
  - `python tools/govern_learning_proposals.py rollback <proposal_id>`

### 3. `copilot_context`
Closed-loop calibration of Copilot dynamic context parameters derived from context recall failure cohorts in `answer_eval_receipt.json`.
- **Cohort Trigger**: $\ge 2$ context recall misses or low context recall ($< 0.60$) within sliding window.
- **Simulation Gate**: Target context recall improvement without degrading guard metrics (groundedness, durability, staleness) and latency within `copilot.rigor.budget_ms`.
- **SSOT Parameter Bounds**:
  | Parameter | Min | Max | Default |
  | :--- | :--- | :--- | :--- |
  | `copilot.context.decay_tau_seconds` | 60.0 | 1800.0 | 300.0 |
  | `copilot.context.focus_seconds` | 5.0 | 120.0 | 15.0 |
  | `copilot.context.focus_weight` | 0.5 | 5.0 | 2.0 |
  | `copilot.context.salient_entities_top_k` | 2 | 20 | 5 |
  | `copilot.context.flow_items_max` | 3 | 30 | 10 |

### 4. `copilot_grounding`
Calibration of Copilot grounding, verification, and multi-hop neighborhood bounds derived from groundedness or conflict-miss cohorts.
- **Cohort Trigger**: $\ge 2$ ungrounded answer citations or conflict misses.
- **Simulation Gate**: Groundedness improvement with zero citation hallucination and verified tier latency $\le$ budget.
- **SSOT Parameter Bounds**:
  | Parameter | Min | Max | Default |
  | :--- | :--- | :--- | :--- |
  | `copilot.neighborhood_max_hops` | 1 | 2 | 1 |
  | `copilot.rigor.budget_ms` | 5.0 | 100.0 | 15.0 |
  | `copilot.rigor.min_salience_score` | 0.1 | 1.0 | 0.4 |
  | `copilot.rerank.candidate_pool_size` | 5 | 50 | 20 |

### 5. `query_retrieval`
Closed-loop calibration of Query hybrid retrieval weights mapped to `retrieval_weights`.
- **Gate Enforcement**: Must strictly pass all 11 BGE-M3 retrieval qualification gates before activation via UWG.

### Answer Evaluation Scanning CLI
- `python tools/govern_learning_proposals.py answer-scan [--eval-dir <dir>] [--min-occurrences <N>]`

