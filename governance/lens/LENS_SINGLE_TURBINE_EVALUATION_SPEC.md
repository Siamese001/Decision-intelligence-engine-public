# Project LENS: Single-Turbine Evaluation Specification

## 1. Document Context & Authority
- **Authority**: Mandated by the Project LENS executive technical review on 09/18/2026 (`02_Jake_Amit_Miles_Slalom_09-18-2026_1243PM_0139PM_ET_executive_recap.md`, Action Item 2).
- **Core Intent**: Establish a formal, reproducible evaluation specification that separates image capture defects from model extraction errors, defines explicit denominators, and articulates the statistical limitations of single-turbine development data versus fleet-wide validation.
- **Audience**: PartnerAdvisory Engineering, PartnerAdvisory Leadership, and Client Technical/Operations Reviewers.

---

## 2. Problem Statement & Technical Tension
In the LENS pipeline, field technicians photograph wind turbine components (nacelles, electrical cabinets, switches, relays, generators) using mobile devices. The pipeline employs a multi-modal intake and dual-model vision inference mesh (Fast OCR + Deep Vision Transformer) to extract:
1. `manufacturer` (e.g. Hirschmann, Bussmann, General Electric, ABB)
2. `model_number` (e.g. RS20-0800T1T1T0AUMM)
3. `serial_number` (e.g. 943434001)

### The Tension
- **Strict Consensus Baseline**: Currently, automatic acceptance (Straight-Through Processing / STP) requires 100% exact character-for-character consensus across both models for all three fields. When models disagree on a single character or formatting nuance, the record trips into human-in-the-loop (HITL) review. This creates an unacceptably high review burden (~70% human review, ~30% STP).
- **Relaxed Consensus Proposal**: Relaxing consensus (e.g. bounding-box dominance for manufacturer, prefix anchoring for model numbers, Levenshtein fuzzy matching on serials, and zero-serial exception rules) increases automatic acceptance. However, if relaxed thresholds allow incorrect asset records to be automatically accepted, the operational cost shifts downstream into enterprise asset registry corruption.
- **Deliverable Purpose**: Provide a rigorous, empirical comparison on the existing single-turbine photograph dataset (~200 images) to quantify the exact trade-off: **How much review volume is reduced for each incremental error admitted?**

---

## 3. Two-Stage Failure Ontology: Segregation of Defects

A critical finding from the 09/18 alignment was that image capture defects were erroneously blended into model extraction failure metrics. This distorted engineering performance and client trust. The evaluation specification mandates strict two-stage segregation:

```
[ All Input Photographs: N_total ]
               │
               ▼
   [ STAGE 0: PRE-FLIGHT GATE ]
   • Exposure (40 <= Mean Luminance <= 220)
   • Sharpness (Laplacian Variance >= 100)
   • Alphanumeric Token Presence
               │
      ┌────────┴────────┐
      ▼                 ▼
[ CAPTURE DEFECT ]   [ LEGIBLE FRAME: N_legible ]
• ERR_BLUR                         │
• ERR_LIGHTING                     ▼
• ERR_NO_TEXT       [ STAGE 1 & 2: DUAL-MODEL EXTRACTION ]
(Quarantined;       • Model A (Fast OCR)
 0% penalty to      • Model B (Deep Vision)
 model accuracy)    • Consensus Mesh
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
          [ AUTO-ACCEPTED ]    [ SENT TO REVIEW ]
          (Zone 5A)            (Zone 5B / 5C)
                 │
           ┌─────┴─────┐
           ▼           ▼
       CORRECT     EXTRACTION ERROR
                   (False Acceptance)
```

### Stage 0: Capture Quality Defects (Environment & Technician)
- **Definition**: Photographic frames that fail physical or optical criteria regardless of model capability.
- **Failure Reason Codes**:
  - `ERR_BLUR`: Laplacian variance < threshold (hand motion, focal blur).
  - `ERR_LIGHTING`: Mean luminance outside acceptable range (pitch black cabinet, flash washout).
  - `ERR_NO_TEXT`: Zero detectable alphanumeric tokens in the frame (e.g., photo of FAA hazard light or blank nacelle wall).
- **Accounting Rule**: Capture defects are routed to the Quarantine Audit Vault. They are counted in $N_{total}$ but **excluded from the denominator of model extraction accuracy**. They represent field data collection issues, not AI extraction failures.

### Stage 1 & 2: Model Extraction Errors (Algorithmic & Inference)
- **Definition**: Legible frames where the dual-model mesh makes an erroneous extraction or reconciliation.
- **Failure Types**:
  - `MANUFACTURER_MISIDENTIFICATION`: Extracting a peripheral label (e.g., fuse manufacturer) instead of the primary component asset.
  - `MODEL_NUMBER_TRUNCATION`: Truncating or hallucinating model number suffixes.
  - `SERIAL_NUMBER_MISREAD`: Misreading OCR characters (e.g., reading "8" as "B", or "0" as "O").
  - `FALSE_ACCEPTANCE`: An incorrect field combination automatically accepted into Zone 5A without human verification.

---

## 4. Governed Metrics & Mathematical Denominators

To prevent ambiguous percentages, all reported evaluations must publish the following exact counts and formulas:

| Denominator / Count | Notation | Definition |
| :--- | :--- | :--- |
| Total Photographic Inputs | $N_{\text{total}}$ | Total raw photographs submitted in the test run. |
| Capture Quality Defects | $N_{\text{defect}}$ | Images failing Stage 0 pre-flight quality checks. |
| Legible Frames | $N_{\text{legible}}$ | $N_{\text{total}} - N_{\text{defect}}$: Valid frames entering model inference. |
| Automatic Acceptances (STP) | $N_{\text{auto}}$ | Records routed to Zone 5A (Straight-Through Automation). |
| Human Review Volume | $N_{\text{review}}$ | Records routed to Zone 5B (Assisted) or Zone 5C (Escalated). |
| Checked Correct Acceptances | $N_{\text{correct\_auto}}$ | Auto-accepted records verified 100% correct against ground truth. |
| Extraction Errors (False Accepts) | $N_{\text{error\_auto}}$ | Auto-accepted records containing $\ge 1$ incorrect field. |

### Derived Governing Metrics

1. **Automatic Acceptance Rate (STP %)**:
   $$\text{STP Rate} = \frac{N_{\text{auto}}}{N_{\text{legible}}} \times 100\%$$
2. **Checked Correctness Rate (Precision of Automation)**:
   $$\text{Checked Correctness} = \frac{N_{\text{correct\_auto}}}{N_{\text{auto}}} \times 100\%$$
3. **False Acceptance Rate (Downstream Error Burden)**:
   $$\text{Error Rate}_{\text{auto}} = \frac{N_{\text{error\_auto}}}{N_{\text{auto}}} \times 100\% = 100\% - \text{Checked Correctness}$$
4. **Human Review Volume Rate**:
   $$\text{Review Rate} = \frac{N_{\text{review}}}{N_{\text{legible}}} \times 100\%$$
5. **Capture Defect Rate**:
   $$\text{Defect Rate} = \frac{N_{\text{defect}}}{N_{\text{total}}} \times 100\%$$

---

## 5. Scope Boundaries: Single-Turbine vs. Fleet Validation

| Parameter | Single-Turbine Study (Current Scope) | Fleet Validation Gate (Future Prerequisite) |
| :--- | :--- | :--- |
| **Sample Denominator** | 1 Turbine (~200 component photos) | $\ge 50$ Turbines spanning multiple geographic sites |
| **Purpose** | Algorithmic tuning & trade-off visibility | Statistical reliability & production qualification |
| **Authority** | Internal development & client calibration | Client executive sign-off for production deployment |
| **Generalization Claim** | **Strictly Prohibited** (risk of sample overfitting) | Validated across diverse turbine makes, ages & lighting |
| **Deployment State** | Sandbox / evaluation only | Authorized for automated enterprise registry writes |
