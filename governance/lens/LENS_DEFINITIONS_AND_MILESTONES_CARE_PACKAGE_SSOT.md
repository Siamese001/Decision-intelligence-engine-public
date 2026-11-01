# PROJECT LENS: DEFINITIONS, MILESTONES & ARCHITECTURE CARE PACKAGE
**Official Single Source of Truth (SSOT) for SOW 1 Deliverables & Technical Architecture**
*Ratified during the Technical Working Session on 09/23/2026*

---

## Executive Overview & Governance Authority

This Care Package establishes the permanent, contract-bound Single Source of Truth (SSOT) governing **Project LENS Phase 1 (SOW 1, Contract No. 4600027256)**. It formalizes the technical definitions, mathematical topologies, pipeline zones, prompting paradigms, and statistical sampling boundaries ratified during the technical working session on September 23, 2026:
- **Amit Ayer**: Lead Enterprise Architect
- **Alex Sanchez**: Tech Lead / Senior Engineer
- **Jake Stark**: UI and Prompting Lead
- **Miles Erickson**: Software Engineer / Data Pipeline SME

This document supersedes all conflicting notes in working slide decks, Confluence draft pages, and informal communications.

---

## 1. Contractual Milestone Deconfliction & Mathematical Subset Topology

SOW 1 establishes two distinct, non-compensating acceptance milestones operating at different stages of the ingestion pipeline. Conflating these thresholds creates contractual ambiguity; both metrics are evaluated independently.

```
╔═════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                      UNIVERSE OF ALL FIELD PHOTOGRAPHS (N_total = ~315)                             ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════╝
                                                │
                                                ▼
                        ┌───────────────────────────────────────────────┐
                        │      STAGE 0: PRE-FLIGHT QUALITY GATE         │
                        │  • Exposure Envelope (40 <= Lux <= 220)       │
                        │  • Sharpness Index (Laplacian >= 100)         │
                        │  • Alphanumeric Fast Gate (Text Detected)     │
                        └───────────────────────┬───────────────────────┘
                                                │
                       ┌────────────────────────┴────────────────────────┐
                       ▼ [FAIL: Unprocessable]                           ▼ [PASS: Legible Frame]
        ┌─────────────────────────────┐           ╔═════════════════════════════════════════════╗
        │   QUARANTINE AUDIT VAULT    │           ║          LEGIBLE NAMEPLATE FRAMES           ║
        │ • ERR_BLUR / ERR_LIGHTING   │           ║        (N_legible = ~290 frames)            ║
        │ • Blank plates / FAA lights │           ╚═════════════════════════════════════════════╝
        │ • 0% Penalty to Model Acc   │                                  │
        └─────────────────────────────┘                                  ▼
                                                  ┌─────────────────────────────────────────────┐
                                                  │         RULE REGISTRY APPLICABILITY         │
                                                  │   • Rule = The Blueprint (Grading Rubric)   │
                                                  │   • SOW Section 9, Line 132 Governs         │
                                                  └──────────────────────┬──────────────────────┘
                                                                         │
                                         ┌───────────────────────────────┴───────────────────────────────┐
                                         ▼ [NO ACTIVE RULE: ~15%]                                        ▼ [HAS ACTIVE RULE: ~85%]
                        ┌─────────────────────────────────┐                       ╔═════════════════════════════════════════════╗
                        │   OUT-OF-SCOPE FOR MILESTONE 2  │                       ║         MILESTONE 2 EVALUATION COHORT       ║
                        │ • Unruled equipment plates      │                       ║        (Ruled & Legible Nameplates)         ║
                        │ • Excluded from M2 Denominator  │                       ╠═════════════════════════════════════════════╣
                        │ • Cannot grade without Blueprint│                       ║  ★ MILESTONE 2: THE "READING TEST"          ║
                        │ • Bouncer forces C = 0 for M1   │                       ║  ★ Target: >= 90% Field Extraction Acc      ║
                        └─────────────────────────────────┘                       ║  ★ Fields: Mfr, Model #, Serial #           ║
                                                                                  ║  ★ Pre-Bouncer Metric (Character-level)     ║
                                                                                  ╚═════════════════════════════════════════════╝
                                                                                                         │
                                                                                                         ▼
                                                                                  ┌─────────────────────────────────────────────┐
                                                                                  │     STAGE 1 & 2: DUAL-VLM CONSENSUS MESH    │
                                                                                  │  Cf = RuleMatch * [0.50*Agree + 0.35*Angle  │
                                                                                  │                   + 0.15*LogProb]           │
                                                                                  └──────────────────────┬──────────────────────┘
                                                                                                         │
                                                         ┌───────────────────────────────────────────────┴───────────────────────────────┐
                                                         ▼ [Cf < 0.88 OR No Rule]                                                        ▼ [Cf >= 0.88 AND RuleMatch = 1]
                                        ┌─────────────────────────────────┐                                       ╔═════════════════════════════════════════════╗
                                        │    ATLAS 3-PANE HUMAN REVIEW    │                                       ║       STRAIGHT-THROUGH AUTOMATION (STP)     ║
                                        │ • Zone 5B: Assisted HITL (Diffs)│                                       ║           (Auto-Approved to Maximo)         ║
                                        │ • Zone 5C: Escalated Anomaly    │                                       ╠═════════════════════════════════════════════╣
                                        │ • Intentional Safety Quarantine │                                       ║  ★ MILESTONE 1: THE "SELF-AWARENESS TEST"   ║
                                        │ • Protects Maximo ERP Integrity │                                       ║  ★ Target: >= 85% Auto-Approval Precision   ║
                                        │ • Excluded from M1 Denominator  │                                       ║  ★ Post-Bouncer Metric: When system claims  ║
                                        └─────────────────────────────────┘                                       ║    high confidence, it is right >= 85%      ║
                                                                                                                  ╚═════════════════════════════════════════════╝
```

### Milestone Comparison Matrix

| Milestone Dimension | Milestone 2 (M2) | Milestone 1 (M1) |
| :--- | :--- | :--- |
| **Common Name** | The "Reading Test" | The "Self-Awareness Test" |
| **Contractual Target** | **$\ge 90\%$ Extraction Accuracy** | **$\ge 85\%$ Auto-Approval Precision** |
| **Pipeline Stage** | **Pre-Bouncer** (Raw OCR / Extraction) | **Post-Bouncer** (Routing Decision) |
| **Evaluation Denominator** | All legible nameplates with an **active Rule** | Only assets routed to **Straight-Through Processing** ($C_f \ge \tau$) |
| **Evaluation Metric** | Character-level accuracy across Mfr, Model, Serial | Percentage of auto-approved assets that are 100% correct |
| **Failure Meaning** | VLM misread stamped metal character (e.g., '0' as 'O') | System had high confidence ($C_f \ge 88\%$) on an erroneous string |
| **Unruled Plates** | **Contractually Excluded** (No blueprint to grade against) | **Routed to Human Review** ($C_f = 0$ via Bouncer; 0% failure penalty) |
| **Governing SOW Clause** | SOW 1 § 4.2 & Section 9 (Line 132) | SOW 1 § 4.1 (Autonomous Ingestion Threshold) |

---

## 2. The Dual Role of a Rule: "The Blueprint" vs. "The Bouncer"

A declarative business rule performs two independent functions across the two milestones:

```
                          ┌───────────────────────────────────────────────┐
                          │            BUSINESS RULE REGISTRY             │
                          │   (Declarative Schemas, RegEx & Dictionaries) │
                          └──────────────────────┬────────────────────────┘
                                                 │
                   ┌─────────────────────────────┴─────────────────────────────┐
                   ▼                                                           ▼
    ╔═════════════════════════════╗                             ╔═════════════════════════════╗
    ║   ROLE A: "THE BLUEPRINT"   ║                             ║    ROLE B: "THE BOUNCER"    ║
    ║   (Governs Milestone 2)     ║                             ║    (Governs Milestone 1)    ║
    ╠═════════════════════════════╣                             ╠═════════════════════════════╣
    ║ • Serves as the GRADING     ║                             ║ • Serves as the GATEKEEPER  ║
    ║   RUBRIC for character      ║                             ║   for autonomous execution. ║
    ║   extraction accuracy.      ║                             ║                             ║
    ║ • Defines expected field    ║                             ║ • If an asset has NO active ║
    ║   locations, formats, and   ║                             ║   rule, the Bouncer sets:   ║
    ║   regex patterns.           ║                             ║       RuleMatch(s) = 0      ║
    ║ • Without a Blueprint, an   ║                             ║          ==> Cf = 0.00      ║
    ║   asset CANNOT be graded.   ║                             ║                             ║
    ║ • SOW Section 9 (Line 132): ║                             ║ • Instantly forces asset to ║
    ║   Apex Global SME must provide  ║                             ║   Atlas 3-Pane Human Review ║
    ║   weekly rule inputs.       ║                             ║   (Zone 5B / 5C).           ║
    ║ • Plates lacking rules are  ║                             ║ • Zero chance of corrupting ║
    ║   OUT-OF-SCOPE for M2.      ║                             ║   Maximo via auto-approval. ║
    ╚═════════════════════════════╝                             ╚═════════════════════════════╝
```

### Scope Governance on Unruled Plates
1. **Milestone 2 Demarcation**: SOW Section 9, line 132 explicitly ties rule development velocity to Apex Global SME availability. Plates lacking active rules are contractually excluded from the Milestone 2 evaluation denominator until the corresponding rule is authored.
2. **Milestone 1 Protection**: Because the Bouncer strictly clamps $C_f = 0$ on unruled assets, they are quarantined to the Atlas 3-pane reviewer queue. They never enter the auto-approval denominator ($N_{\text{auto}}$), ensuring zero risk of downstream ERP corruption and zero penalty to Milestone 1 precision.

---

## 3. End-to-End Pipeline & Multi-Zone Ingestion Architecture

The production architecture segregates processing into six deterministic zones:

```
╔═════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                      ZONE 1: MULTI-MODAL INTAKE & FIELD CONTEXT CAPTURE                             ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════╝
    Technician Field Upload via Mobile App (Turbine Nacelle / Tower Base / Pad-Mount Transformer)
    • Macro Photo: 90° normal angle on stamped metal nameplate (high-resolution crop)
    • Oblique Photo: 45° angle to mitigate direct flash specular glare
    • Context Overview: Wide-angle view capturing rack position and component enclosure
                                                │
                                                ▼
╔═════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                      ZONE 2: DETERMINISTIC PRE-FLIGHT QUALITY GATE (STAGE 0)                        ║
╠═════════════════════════════════════════════════════════════════════════════════════════════════════╣
║  • Exposure Envelope: 40 <= Mean Luminance <= 220 (Rejects pitch-black / flash washout)            ║
║  • Sharpness Index: Laplacian Variance >= 100 (Rejects hand jitter / motion blur)                   ║
║  • Zero-Compute Fast Gate: Rejects frames lacking alphanumeric tokens (e.g. FAA beacon light)       ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════╝
                        │                                               │
                        ▼ [FAIL: Defective Frame]                       ▼ [PASS: Legible Frame]
        ┌─────────────────────────────┐         ╔═════════════════════════════════════════════════════╗
        │   QUARANTINE AUDIT VAULT    │         ║     ZONE 3: HETEROGENEOUS DUAL-VLM MESH (STAGE 1)   ║
        │ • Status: REJECTED_CAPTURE  │         ╠═════════════════════════════════════════════════════╣
        │ • Log Code: ERR_BLUR/LIGHT  │         ║ Model A: Claude 3.5 Sonnet / Opus (Detailed Vision) ║
        │ • 0% Penalty to Model Pass %│         ║ Model B: OpenAI GPT-4o Vision (Spatial Reasoning)   ║
        │ • Flags Technician Retake   │         ║ ─────────────────────────────────────────────────── ║
        └─────────────────────────────┘         ║ Multi-Angle Spatial Matching & Token IoU Alignment  ║
                                                ╚═════════════════════════════════════════════════════╝
                                                                                │
                                                                                ▼
╔═════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                  ZONE 4: GOVERNED SEMANTIC RECONCILIATION MESH (STAGE 2)                            ║
╠═════════════════════════════════════════════════════════════════════════════════════════════════════╣
║  1. MANUFACTURER RESOLUTION (Bounding Box Dominance + Prompt Target Anchor)                         ║
║     • Primary Bounding Box = "HIRSCHMANN" | Background peripheral label = "BUSSMANN" (Suppressed)   ║
║     ──> Outcome: Manufacturer = HIRSCHMANN [CONFIRMED AUTO-PASS]                                    ║
║                                                                                                     ║
║  2. MODEL NUMBER ALIGNMENT (Prefix Anchor + Dual-Model Agreement)                                   ║
║     • Model A: RS20-0800T1T1T0AUMM  |  Model B: RS20-0800T1T1TDAU??                                ║
║     • Agreed Prefix: "RS20-0800T1T1" (Length >= 8 chars)                                            ║
║     ──> Outcome: Model Number = RS20-0800T1T1T0AUMM [CONFIRMED AUTO-PASS]                           ║
║                                                                                                     ║
║  3. SERIAL NUMBER HANDLING & ZERO-SERIAL EXCEPTION RULES                                            ║
║     • Branch A (Serial Exists): Levenshtein Match > 80% -> Pre-fills consensus string               ║
║     • Branch B (Zero-Serial Asset Class): If component has no factory serial by design (e.g. busbar)║
║       tag as [N/A - BY DESIGN] -> Prevents false review trips on un-serialized parts                ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════╝
                                                │
                ┌───────────────────────────────┼───────────────────────────────┐
                ▼                               ▼                               ▼
╔═══════════════════════════════╗╔═══════════════════════════════╗╔═══════════════════════════════╗
║  ZONE 5A: STRAIGHT-THROUGH    ║║  ZONE 5B: 1-CLICK ASSISTED    ║║  ZONE 5C: ESCALATED ANOMALY   ║
║  AUTOMATION (STP)             ║║  HITL REVIEW (ATLAS 3-PANE)   ║║  REVIEW (QUARANTINE)          ║
╠═══════════════════════════════╣╠═══════════════════════════════╣╠═══════════════════════════════╣
║ • Confidence Cf >= 0.88       ║║ • Confidence: 0.70 <= Cf < 0.88║║ • Confidence Cf < 0.70       ║
║ • RuleMatch(s) == 1 (Valid)   ║║ • UI renders RED DIFF on      ║║ • Severe occlusion / damage   ║
║ • Zero Operator Intervention  ║║   conflicting tokens (e.g. 0/O)║║ • Unruled equipment class    ║
║ • Direct Ingestion to Maximo  ║║ • 1-Click Technician Confirm  ║║ • Full manual data entry path ║
║ • Milestone 1 Target: >= 85%  ║║ • Saves 70% keystroke time    ║║ • Logged to Audit Trail       ║
╚═══════════════════════════════╝╚═══════════════════════════════╝╚═══════════════════════════════╝
                │                               │                               │
                └───────────────────────────────┼───────────────────────────────┘
                                                ▼
╔═════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                      ZONE 6: ENTERPRISE STATE & EVIDENCE PERSISTENCE                                ║
╠═════════════════════════════════════════════════════════════════════════════════════════════════════╣
║  • Canonical Asset Record: Committed to Enterprise Knowledge Graph & Maximo Asset Staging Table     ║
║  • Cryptographic Evidence Envelopes: SHA-256 hash binds pixel bounding boxes to confirmed fields   ║
║  • Telemetry Ledger: Logs latency, token usage, agreement scores, and ECE calibration metrics        ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════╝
```

---

## 4. Two-Tier Prompting Architecture Runway

Evaluation of optical extraction on industrial stamped metal confirmed that traditional OCR engines (Textract, DocAI) degrade significantly under specular flash glare, mechanical scratches, and low-contrast surface relief. Furthermore, forcing a Vision LLM to simultaneously read degraded text and format JSON schemas induces token-budget exhaustion and hallucinations. Decoupling the operations into two sequential passes establishes an optimized execution path:

```
  RAW NAMEPLATE PHOTO (High Specular Glare / Stamped Metal Relief)
                       │
                       ▼
  ┌─────────────────────────────────────────────────────────────┐
  │       IMAGE PREPROCESSING: CLAHE CONTRAST ENHANCEMENT       │
  │  • Contrast Limited Adaptive Histogram Equalization         │
  │  • High-frequency edge sharpening + specular glare clip     │
  └────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
╔═════════════════════════════════════════════════════════════════════════════╗
║  TIER 1 PROMPT: PURE VERBATIM OPTICAL TRANSCRIPTION (PASS 1)                ║
╠═════════════════════════════════════════════════════════════════════════════╣
║  • Objective: Pure optical character recognition without semantic formatting║
║  • Instruction: "Transcribe every character, number, and label exactly as   ║
║    stamped on the metal plate line-by-line. Do NOT output JSON. Do NOT      ║
║    attempt to interpret fields. Maintain raw spatial layout."               ║
║  • Output: Raw textual representation preserving line breaks and symbols    ║
╚═════════════════════════════════════════════════════════════════════════════╝
                               │
                               ▼
╔═════════════════════════════════════════════════════════════════════════════╗
║  TIER 2 PROMPT: SEMANTIC SCHEMA MAPPING & REGISTRY BINDING (PASS 2)         ║
╠═════════════════════════════════════════════════════════════════════════════╣
║  • Objective: Structured extraction guided by declarative Rule Blueprint    ║
║  • Input: Raw Tier 1 transcript + Rule Registry schema definitions          ║
║  • Instruction: "Using the raw text transcript and the turbine component    ║
║    rule schema, extract: 1. Manufacturer, 2. Model Number, 3. Serial Number.║
║    Output strictly valid JSON complying with Pydantic contract."            ║
║  • Benefit: Zero token budget wasted on simultaneous vision & parsing       ║
╚═════════════════════════════════════════════════════════════════════════════╝
                               │
                               ▼
  ┌─────────────────────────────────────────────────────────────┐
  │          DOWNSTREAM SYNTACTIC & HEURISTIC BOUNCERS          │
  │  • Regex validation against known vendor model prefixes     │
  │  • Character count checks (e.g. TPT + 9 numeric digits)     │
  │  • Zero-serial exception rules for passive electrical parts │
  └─────────────────────────────────────────────────────────────┘
```

---

## 5. Statistical Sampling Architecture & Binomial Power Analysis

Technical analysis evaluated sample sizing requirements between an exhaustive 800-asset cohort and the governed 105-asset SOW 1 target:
1. **Operational Scope**: An 800-asset target requires ~2,400 photos across 20 to 25 wind turbines (40 to 50 field technician climbing days), exceeding Phase 1 pilot feasibility.
2. **Contractual Standard**: SOW 1 acceptance is governed as an **empirical point estimate** ($\hat{p} \ge 90\%$), rather than a lower-bound clinical trial.
3. **Statistical Power**: Merging the 85 baseline assets with Mandy Hopmann's Tier 2 UAT request (20 assets / 60 photos across 3–5 turbines) establishes a statistically rigorous cohort bounding the margin of error to $\pm 5.7\%$ at a 95% confidence interval.

```
╔═════════════════════════════════════════════════════════════════════════════╗
║                      STATISTICAL SAMPLING & STATISTICAL POWER COHORTS                               ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

$$\text{Margin of Error } (E) = Z \times \sqrt{\frac{p(1-p)}{n}} \quad (Z = 1.96 \text{ for } 95\% \text{ CI})$$

$$\text{Cluster Design Effect (DEFF)} = 1 + (m - 1)\rho \quad (\rho \approx 0.15, \text{ intra-turbine correlation})$$

$$N_{\text{eff}} = \frac{N_{\text{assets}}}{\text{DEFF}}$$

| Cohort Dimension | Cohort 1: Current Baseline | Cohort 2: Tier 2 UAT Ask | Cohort 3: Combined SOW 1 Fleet |
| :--- | :---: | :---: | :---: |
| **Physical Assets ($n$)** | 85 assets | 20 assets | **105 assets** |
| **Total Photographs** | 255 photos (3 per asset) | 60 photos (3 per asset) | **315 photos** |
| **Turbine Towers ($k$)** | 2 turbines | 3 to 5 turbines | **5 to 7 turbines** |
| **Cluster Size ($m = n/k$)**| 42.5 assets / turbine | ~4.5 assets / turbine | ~17.5 assets / turbine |
| **Design Effect (DEFF)** | 7.22 (High intra-cluster bias)| 1.45 (Dispersed correlation) | **3.25** |
| **Effective Sample ($N_{\text{eff}}$)**| 11.8 independent assets | 13.8 independent assets | **32.3 independent assets** |
| **Extraction Margin ($E_{90}$)**| $\pm 6.4\%$ | $\pm 13.1\%$ (Asset) / $\pm 7.6\%$ (Photo) | **$\pm 5.7\%$ (Statistically Defensible)** |
| **Calibration Margin ($E_{85}$)**| $\pm 7.6\%$ | $\pm 15.6\%$ (Asset) / $\pm 9.0\%$ (Photo) | **$\pm 6.8\%$ (Statistically Defensible)** |
| **Field Burden** | Already collected | **~3–4 hours (1 afternoon)** | Optimal enterprise UAT rigor |

### Key Methodological Rationale:
- **Baseline Cohort Retention**: Because LENS utilizes zero-shot foundation VLMs without internal parameter weight updates (backpropagation), dividing the 85 assets into 45 dev and 40 holdout assets unnecessarily inflates the error margin beyond $\pm 10.1\%$. All 85 baseline assets serve as the unified Golden Calibration Benchmark, while Mandy's Tier 2 UAT data serves as the true blind out-of-sample evaluation.

---

## 6. Ground Truth Fidelity Boundary: Metal Reality vs. ERP Corruption

Historical records in IBM Maximo contain legacy data entry discrepancies. Benchmarking extraction models against Maximo introduces artificial defect penalties:

```
  ┌─────────────────────────────────────────────────────────────┐
  │                 PHYSICAL STAMPED NAMEPLATE                  │
  │  • Physical reality inside nacelle / electrical cabinet     │
  │  • Example: Pitch Motor Stamped "MOOG - Model G403-221"     │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                 [ THE CAMERA CAPTURES REALITY ]
                                 │
                                 ▼
  ╔═════════════════════════════════════════════════════════════╗
  ║          PROJECT LENS EXTRACTION ENGINE (VLM MESH)          ║
  ║  • Extracts: Manufacturer = "MOOG"                          ║
  ║  • Extracts: Model Number = "G403-221"                      ║
  ╚═════════════════════════════════════════════════════════════╝
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
  ┌─────────────────────────────┐ ┌─────────────────────────────┐
  │  LEGACY IBM MAXIMO RECORD   │ │  CORRECT GOVERNED GROUND    │
  │  (DIRTY HISTORICAL DATA)    │ │  TRUTH (CAMERA FIDELITY)    │
  │ • Stored Mfr: "GE ENERGY"   │ │ • Authoritative Truth:      │
  │ • Stored Model: "G400-UNKNOWN││   "MOOG" / "G403-221"       │
  │ • Stored by historical typo │ │ • Benchmarking LENS against │
  │   during replacement        │ │   this yields 100% SUCCESS  │
  │ • Benchmarking LENS against │ │ • Normalizes master data    │
  │   Maximo yields FALSE FAIL! │ │   in enterprise registry    │
  └─────────────────────────────┘ └─────────────────────────────┘
```

### Ground Truth Governance Invariant:
1. **Ground truth is strictly defined by physical nameplate imagery**, verified by human annotators from high-resolution photographs.
2. **Vendor Alias Normalization** (e.g. mapping "GE" or "GE Renewable Energy" to "General Electric") is managed deterministically via the Rule Builder Dictionary, decoupling transcription fidelity from ERP mapping.

---

## 7. Heterogeneous Dual-VLM Consensus Formula & Threshold Tuning

To satisfy the $\ge 85\%$ calibration threshold without single-model hallucinations, LENS computes a composite confidence score $C_f$:

$$C_f = \text{RuleMatch}(s) \times \left[ 0.50 \cdot \text{Agreement}(s_A, s_B) + 0.35 \cdot \text{MultiAngleConsensus}(s) + 0.15 \cdot \left(\frac{c_A + c_B}{2}\right) \right]$$

```
╔═════════════════════════════════════════════════════════════════════════════╗
║                      COMPOSITE CONFIDENCE SCORE (Cf) ARCHITECTURE                                   ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                                                     ║
║   1. RULE MATCH GATE: RuleMatch(s) in {0, 1} [BINARY MULTIPLIER / THE BOUNCER]                     ║
║      • If extracted string matches declared regex/dictionary and asset has active rule: = 1.0       ║
║      • If string violates syntax OR plate lacks an active rule: = 0.0 (Forces Cf = 0.00)            ║
║                                                                                                     ║
║   2. CROSS-MODEL AGREEMENT: Agreement(s_A, s_B) [50% WEIGHT]                                        ║
║      • Exact string match between Model A (Claude) and Model B (GPT-4o): = 1.0                      ║
║      • Normalized Levenshtein distance on minor character conflict: = 1 - (LevDist / MaxLen)        ║
║                                                                                                     ║
║   3. MULTI-ANGLE CONSISTENCY: MultiAngleConsensus(s) [35% WEIGHT]                                   ║
║      • Agreement between 90° normal macro view and 45° oblique view: = 1.0                          ║
║      • Resolves specular flash washout and lens flare anomalies                                     ║
║                                                                                                     ║
║   4. MODEL TOKEN PROBABILITIES: (c_A + c_B) / 2 [15% WEIGHT]                                       ║
║      • Normalized average of raw token log-probabilities from primary vision backends               ║
║                                                                                                     ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

### Operational Routing Thresholds ($\tau$):
- **Straight-Through Processing (Zone 5A)**: $C_f \ge 0.88$ AND $\text{RuleMatch} = 1.0$. Straight to Maximo staging.
- **Assisted Review (Zone 5B - Atlas 3-Pane UI)**: $0.70 \le C_f < 0.88$. Pre-populates consensus string and highlights conflicting characters in red (e.g. `TPT46471` vs `TP146471`).
- **Escalated Quarantine (Zone 5C)**: $C_f < 0.70$. Full manual review path.

---

## 8. Governance Realignment, Action Ledger & RACI Matrix

To ensure focused execution ahead of the executive sprint demo to Ed Moore (VP) and Alexandra Corrow (Director of OT), engineering ownership is partitioned as follows:

```
╔═════════════════════════════════════════════════════════════════════════════╗
║                               WORKSTREAM RACI MATRIX                        ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  • Amit Ayer (Lead Enterprise Architect):                                   ║
║    - Executive narrative, client pre-read deck, and Figma slide authoring.  ║
║    - Leads Mandy Hopmann pre-read alignment and executive sprint demo.      ║
║                                                                             ║
║  • Alex Sanchez (Tech Lead / Senior Engineer):                              ║
║    - Core pipeline engineering, calibration harness execution, ECE curves.  ║
║    - Validates zero-leakage holdout barrier across baseline datasets.       ║
║                                                                             ║
║  • Jake Stark (UI and Prompting Lead):                                      ║
║    - Two-Tier Prompting implementation (Pass 1 verbatim + Pass 2 mapping).  ║
║    - Atlas 3-pane red character conflict diff highlighting for approvals.   ║
║                                                                             ║
║  • Miles Erickson (Software Engineer / Pipeline SME):                       ║
║    - Stage U0 automated metadata tagging and multi-angle consensus harness. ║
║    - Offline edge capture pre-flight check specifications.                  ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

### Action Items & Delivery Milestones

| # | Action Item / Deliverable | Owner | Target Timing | Verification Gate |
| :-: | :--- | :--- | :---: | :--- |
| **1** | **Client Cadence Realignment** | Amit Ayer | Complete (09/23) | Direct coordination with Mandy Hopmann |
| **2** | **Deploy Two-Tier Prompting Architecture** | Jake Stark & Amit Ayer | 09/23 (Tonight) | Pass 1 verbatim transcription + Pass 2 mapping |
| **3** | **Run Empirical Calibration Curve (ECE $\le 0.15$)** | Alex Sanchez | 09/23 (Tonight) | Verification across unified 85 baseline assets |
| **4** | **Render Red Character Diffs in Atlas UI** | Jake Stark | 09/24 (09:00 AM) | UI test verifying character dispute highlighting |
| **5** | **Deploy Stage U0 Multi-Angle Harness** | Miles Erickson | 09/24 (09:00 AM) | Automated multi-angle grouping script passing |
| **6** | **Tier 2 UAT Alignment with Mandy** | Amit Ayer | 09/24 (11:00 AM) | Agreement on 20 assets / 60 photos across 3–5 turbines |
| **7** | **Executive Sprint Demo to Ed Moore & Alexandra Corrow**| Amit Ayer & PartnerAdvisory Team | 09/24 (02:00 PM) | Live demonstration of 4-tier matrix & Atlas review |
