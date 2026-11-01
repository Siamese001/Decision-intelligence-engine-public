# Project LENS: Pinned SSOT Definitions & Architecture
**Quick Reference for Teams Channel**

---

### 📌 1. Contract Milestones: M1 vs M2 Deconfliction

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       SOW 1 ACCEPTANCE MILESTONES                           │
├──────────────────────────────────────┬──────────────────────────────────────┤
│      MILESTONE 2: THE READING TEST   │   MILESTONE 1: SELF-AWARENESS TEST   │
│      Target: >= 90% Extraction       │   Target: >= 85% Auto-Approval       │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ • Pre-Bouncer: Raw OCR characters    │ • Post-Bouncer: Straight-through STP │
│ • Evaluated on: Ruled plates ONLY    │ • Evaluated on: High confidence (Cf) │
│ • Fields: Mfr, Model #, Serial #     │ • Guarantees: When LENS auto-approves│
│ • Unruled plates: Contractually      │   it is 100% correct >= 85% of time  │
│   excluded (SOW Sec 9, line 132)     │ • Human review trips = 0% penalty    │
└──────────────────────────────────────┴──────────────────────────────────────┘
```

---

### 📌 2. The Rule: "The Blueprint" vs "The Bouncer"

```
                ┌──────────────────────────────────────┐
                │          BUSINESS RULE ROLE          │
                └──────────────────┬───────────────────┘
                                   │
         ┌─────────────────────────┴─────────────────────────┐
         ▼                                                   ▼
┌─────────────────────────────────┐         ┌─────────────────────────────────┐
│     ROLE A: "THE BLUEPRINT"     │         │      ROLE B: "THE BOUNCER"      │
│      (Governs Milestone 2)      │         │      (Governs Milestone 1)      │
├─────────────────────────────────┤         ├─────────────────────────────────┤
│ • The grading rubric for text.  │         │ • The gatekeeper for automation.│
│ • If no rule exists for a plate,│         │ • If no rule exists, Bouncer    │
│   it CANNOT be graded.          │         │   clamps confidence to Cf = 0.  │
│ • SOW ties rules to Apex Global SME.│         │ • Forces 100% of unruled plates │
│ • Missing rule = EXCLUDED from  │         │   to human review. Never auto-  │
│   M2 calculation.               │         │   approves bad data to Maximo!  │
└─────────────────────────────────┘         └─────────────────────────────────┘
```

---

### 📌 3. Tri-Tier Routing & Confidence Thresholds ($\tau$)

```
[ Field Photos ] ──> [ Pre-Flight Gate ] ──> [ Dual-VLM Consensus ]
                                                     │
                                 Composite Score Cf  │
                     ┌───────────────────────────────┼───────────────────────────────┐
                     ▼                               ▼                               ▼
       ┌───────────────────────────┐   ┌───────────────────────────┐   ┌───────────────────────────┐
       │   ZONE 5A: STRAIGHT-THRU  │   │    ZONE 5B: ATLAS 3-PANE  │   │    ZONE 5C: QUARANTINE    │
       │        (AUTOMATION)       │   │       (ASSISTED HITL)     │   │     (ESCALATED REVIEW)    │
       ├───────────────────────────┤   ├───────────────────────────┤   ├───────────────────────────┤
       │ • Score: Cf >= 88%        │   │ • Score: 70% <= Cf < 88%  │   │ • Score: Cf < 70%         │
       │ • Zero operator clicks    │   │ • Red char diffs (e.g.0/O)│   │ • Unruled equipment       │
       │ • Straight into Maximo    │   │ • 1-click confirm (~2 sec)│   │ • Damaged / occluded tag  │
       │ • Governs Milestone 1     │   │ • Cuts 70% keystrokes     │   │ • Full manual data entry  │
       └───────────────────────────┘   └───────────────────────────┘   └───────────────────────────┘
```

---

### 📌 4. Statistical Power: Why 105 Assets (Not 800)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SOW 1 FLEET SAMPLING TARGET MATRIX                       │
├───────────────────────┬─────────────────────────┬───────────────────────────┤
│    CURRENT BASELINE   │    TIER 2 UAT ASK       │   COMBINED SOW 1 FLEET    │
│      (Turbines 1 & 2) │    (Out-of-Sample)      │   (Statistically Bounded) │
├───────────────────────┼─────────────────────────┼───────────────────────────┤
│ • 85 assets           │ • 20 assets             │ • 105 total assets        │
│ • 255 photos          │ • 60 photos             │ • 315 total photos        │
│ • 2 turbines          │ • 3 to 5 turbines       │ • 5 to 7 turbines         │
│ • DEFF = 7.22         │ • DEFF = 1.45           │ • DEFF = 3.25             │
│ • Neff = 11.8 assets  │ • Neff = 13.8 assets    │ • Neff = 32.3 assets      │
│ • Margin: +/- 6.4%    │ • Field time: ~3.5 hrs  │ • Margin: +/- 5.7% (95% CI│
├───────────────────────┴─────────────────────────┴───────────────────────────┤
│ ★ 800 assets (+/- 2% margin) is an FDA clinical trial lower bound (50 days).│
│ ★ SOW 1 is an empirical point estimate (p >= 90%), NOT a lower-bound trial. │
│ ★ 105 assets across 5-7 turbines is universal enterprise UAT gold standard. │
└─────────────────────────────────────────────────────────────────────────────┘
```
