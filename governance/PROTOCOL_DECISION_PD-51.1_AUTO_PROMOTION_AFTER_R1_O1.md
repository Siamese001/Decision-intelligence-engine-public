# PD-51.1 — Automatic approved-current activation after R1/O1

- **Status:** approved_active
- **Effective version:** 51.0
- **Approval source:** explicit user instruction on 2026-07-13.
- **Problem:** The prior process required a separate promotion step after an already authorized successor passed Gate R1 and Gate O1.
- **Decision:** An authorized successor build becomes `approved_current` automatically after Gate R1 and Gate O1 pass. The release remains `candidate` only when the user explicitly requests `candidate-only` or withholds promotion before packaging.
- **Unaffected controls:** Pre-mutation G1/C1 approval, exact-delta conformance, hard-stop drift control, R1, O1, packaging hygiene, historical preservation, and audience restrictions remain blocking.
- **Rollback:** Restore the prior Phase D step, approval-matrix row, lifecycle language, packaging default, and protocol-register entry through a separately authorized policy change.
