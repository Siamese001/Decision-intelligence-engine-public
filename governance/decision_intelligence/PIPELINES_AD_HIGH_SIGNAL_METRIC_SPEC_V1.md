# Pipelines A–D High-Signal Evaluation Metric Specification
**Version**: 1.0.0  
**Date**: 09/14/2026  
**Status**: APPROVED & BINDING  
**Authority**: Dialectical Synthesis Judge & Architecture Governance

---

## 1. Overview & Objective

This specification establishes the official high-signal metric definitions, pass/fail thresholds, and evaluation protocols for Pipelines A, B, C, and D. It replaces all low-signal, tautological, or synthetic checks with contract-grounded, non-compensating criteria.

---

## 2. Pipeline A Metric Specification (Intake & Forensic Audio/ASR)

### Metric A.1: Acoustic Noise Floor Resilience Score ($S_{noise}$)
- **Definition**: Evaluates DSP feature extraction and confidence calibration across 4 distinct acoustic challenge regimes:
  1. $R_{silence}$: True silence/ambient frames must have $\text{RMS} \le -45$ dBFS and $\text{is\_speech} = \text{False}$.
  2. $R_{clipping}$: Overdriven audio ($\ge 5\%$ samples saturated) must trigger clipping detection and attenuate speech confidence.
  3. $R_{clean}$: Nominal speech tone must exhibit $\text{RMS} \ge -35$ dBFS and speech confidence $\ge 0.85$.
  4. $R_{low\_snr}$: Low-SNR degraded audio ($\text{SNR} \le 6$ dB) must calculate a valid SNR estimate without numerical instability.
- **Formulation**:
  $$S_{noise} = \frac{1}{4} \sum_{i=1}^{4} \mathbb{I}(R_i \text{ satisfies DSP boundary})$$
- **Passing Threshold**: $S_{noise} \ge 0.75$ (Fail-closed).

### Metric A.2: Speaker Attribution Locking Accuracy ($A_{locking}$)
- **Definition**: Fraction of ungrounded or ambiguous speaker tokens strictly normalized to `speaker_unknown` or recorded in the Ambiguity Lock Register.
- **Passing Threshold**: $A_{locking} = 1.00$ (100% strict enforcement). Zero ungrounded caller hallucinations allowed.

### Metric A.3: Enrolled Speaker Preservation Rate ($P_{enrolled}$)
- **Definition**: High-confidence preservation of verified roster identities.
- **Passing Threshold**: $P_{enrolled} = 1.00$ (100% strict).

### Metric A.4: Streaming Chunk Latency SLA ($L_{chunk}$)
- **Definition**: Maximum chunk DSP evaluation latency must remain strictly below 500ms.
- **Passing Threshold**: $\max(L_{chunk}) \le 500.0$ ms.

---

## 3. Pipeline B Metric Specification (Extraction, C-13 Defense & C-19 Firewall)

### Metric B.1: Prompt Injection Neutralization Rate ($R_{c13}$)
- **Definition**: The proportion of adversarial prompt injection payloads (direct overrides, indirect tags, markdown-embedded commands, and roleplay DAN prompts) that are neutralized by the sanitizer.
- **Neutralization Condition**: An attack is neutralized iff:
  1. $\text{sanitized\_text} \ne \text{input\_text}$ OR $\text{has\_redactions} = \text{True}$; AND
  2. Post-sanitization inspection confirms the active instruction payload has been stripped or rendered inert.
- **Passing Threshold**: $R_{c13} \ge 0.80$ across adversarial challenge suites.

### Metric B.2: Career Leverage Firewall Leak Count ($C_{c19}$)
- **Definition**: Total number of Rule C-19 prohibited executive positioning or personal negotiation tokens leaking into Output 01, Output 02, or Output 03.
- **Passing Threshold**: $C_{c19} = 0$ (Zero tolerance).

### Metric B.3: JSON Extraction Repair Success Rate ($R_{repair}$)
- **Definition**: Success rate of recovering truncated or malformed LLM completions into valid schema-conforming JSON without hallucinating missing fields.
- **Passing Threshold**: $R_{repair} = 1.00$ across all recoverable truncation patterns.

### Metric B.4: DLQ Quarantine Reliability ($Q_{dlq}$)
- **Definition**: Fail-closed reliability when intake or extraction encounters fatal corruptions: items must enter Dead Letter Queue with valid error provenance.
- **Passing Threshold**: $Q_{dlq} = 1.00$.

---

## 4. Pipeline C Metric Specification (Graph Ingestion, Vector Cache & Retrieval)

### Metric C.1: Generational VectorCache Invalidation Precision ($I_{cache}$)
- **Definition**: Proof that loaded lane matrices are reused under identical generation IDs ($g_1 = g_2$) and purged/reloaded upon generation advance ($g_1 \ne g_2$).
- **Passing Threshold**: $I_{cache} = \text{True}$.

### Metric C.2: Steady-State Hybrid Retrieval Latency SLA ($T_{retrieval}$)
- **Definition**: Total elapsed time for 500 consecutive hybrid retrieval executions across SQLite sidecars under WAL mode and cached vector matrices.
- **Passing Threshold**: $T_{retrieval} \le 7,500.0$ ms (average $< 15.0$ ms/query).

### Metric C.3: Law 6 Quarantine Policy Enforcement ($P_{law6}$)
- **Definition**: Quarantined graph nodes (`is_quarantined = 1`) must never appear in retrieval results, connected evidence maps, or downstream query answers.
- **Passing Threshold**: $P_{law6} = \text{True}$ (Zero leakage of quarantined nodes).

---

## 5. Pipeline D Metric Specification (HITL Governance & Decision Velocity)

### Metric D.1: Ambiguity Margin Boundary Gating Precision ($A_{margin}$)
- **Definition**: Accuracy of surfacing decisions to HITL when confidence margin $\Delta = c_{top} - c_{second} < 0.25$ vs auto-approving under audit receipt when $\Delta \ge 0.25$.
- **Passing Threshold**: $A_{margin} = 1.00$ (100% precision, tested at $\Delta = 0.2499$ and $\Delta = 0.2500$).

### Metric D.2: Synthetic Stop Hook Rejection Rate ($R_{synthetic}$)
- **Definition**: Rejection rate of forged, IDE-generated, or automated review stop hooks.
- **Passing Threshold**: $R_{synthetic} = 1.00$ (100% fail-closed rejection).

### Metric D.3: Bayesian Prior Calibration Margin Gain ($G_{prior}$)
- **Definition**: Measured increase in confidence margin separation on affirmed patterns after persisting operator feedback into `hitl_decision_ledger.json`.
- **Passing Threshold**: $G_{prior} > 0.00$.
