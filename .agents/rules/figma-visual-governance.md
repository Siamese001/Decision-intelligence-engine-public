---
trigger: glob
globs: "{governance/figma/**,evals/figma/**,*.png}"
---

# Figma 4K Visual Governance Rules

Governs visual generation, executive slides, technical diagrams, and release qualification under `Figma/Figma_4K_Visual_Governance_v1.0.0_20260904`.

## The Seven Non-Negotiable Invariants

1. **Exact 3840×2160 Canvas (16:9)**:
   - Exported as exact 3840×2160 PNG at 1× scale. Non-blank luminance variance $\ge 8.0$. Fully editable native Figma layers; flat bitmap paste strictly rejected.

2. **Inter Typography Mandate**:
   - Font family must be **Inter only** (`font_family: "Inter"`, no fallback). Title Hard Floor: 88 px ($\le 2$ rendered lines). Essential Body Floor: 42 px. Secondary Body Floor: 38 px. No shrink-to-fit (`no_shrink_to_fit: true`).

3. **Exclusive Full-Width Title Row**:
   - Frame width $\ge 3648$ px ($x \approx 96$ px to $3744$ px). Title occupies exclusive row ($y \in [48, 310]$ px). Zero manual line breaks. No badges/logos intersecting title row.

4. **100% Source Truth Traceability**:
   - Every visible text node must have an entry in `source_manifest.json` (`SOURCE_EXACT`, `SOURCE_PARAPHRASE`, `USER_APPROVED_NEW`, `STRUCTURAL_LABEL`). `UNSUPPORTED` is a blocking defect.

5. **Executive Dominance & Argument Structure**:
   - One analytical argument (`CONCLUSION -> EVIDENCE -> IMPLICATION/DECISION`). Dominant exhibit occupies $\ge 45\%$ usable area. Max 1 attribution block, 1 quote block, 4 peer cards, 2 hero message zones.

6. **Zero Material Regression on Revisions**:
   - Revisions bind to exact predecessor SHA-256 (`PRESERVE / IMPROVE / DO NOT CHANGE`). Side-by-side review across 26 dimensions in `policy/regression.json`. A single `MATERIAL_REGRESSION` rejects candidate.

7. **Fail-Closed Machine Release Authority**:
   - Only `python -m runtime.govern release --receipt RELEASE.json` can authorize promotion (`DELIVERY_READY`). Diagnostic commands return `DIAGNOSTIC_ONLY`. Self-attestation forbidden (`builder_run_id != reviewer_run_id`).

## Reference Archetypes & Repair Order
- Executive slides select one archetype: `DIAGNOSTIC_MATRIX`, `RANKED_COMPARISON`, `OPERATING_MODEL`, `ROADMAP`, `DECISION_TREE`, `VALUE_BRIDGE`.
- Canonical repair hierarchy: 1. `REMOVE_DUPLICATION` -> 2. `REMOVE_LOW_SIGNAL_WORDING` -> 3. `SYNTHESIZE_OR_SHORTEN` -> 4. `REFLOW` -> 5. `ENLARGE_REGION` -> 6. `REBALANCE` -> 7. `SIMPLIFY_EXHIBIT` -> 8. `REJECT` (never shrink font below floors).
