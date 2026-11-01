---
trigger: glob
globs: "{dual_pipeline/staleness_core/**,dual_pipeline/temporal_*.py}"
---

# Temporal Representation Single Source of Truth (SSOT) Rules

Governs datetime serialization, parsing, and display formats across the repository.

## 1. Three Governed Temporal Tiers
1. **Tier 1 (Database & Contract SSOT)**: **ISO-8601 UTC** (`YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SSZ`). Used in SQLite tables, vector sidecars, JSON evidence envelopes, OpenTelemetry events, and audit logs.
2. **Tier 2 (Human Artifact & Projection SSOT)**: **Slash-delimited `MM/DD/YYYY`** (e.g. `09/13/2026`). Used in headers, email subjects, executive recap tables (`02_executive_recap.md`), header metadata, and JSON projection properties (`observed_at`, `stale_after`, `due_date`). Enforced via Rule C-01, Master Constraint C-07, and Gate 3.
3. **Tier 3 (Filesystem Path SSOT)**: **Hyphenated `MM-DD-YYYY`** (e.g. `09-13-2026`). Used in meeting output directories (`output/<project>/<MM-DD-YYYY>/`) and deliverable filenames (`01_*_<date>_<time>_*.md`). Enforced via `tools/validate_output_hierarchy.py`.

## 2. Serialization & Parsing Utilities
- **Zero Ad-Hoc `strftime`**: Disallow custom unvalidated string formatting.
- **Central Normalization**: Route dates through `dual_pipeline.normalization.normalize_iso_date()` or `storage.identifiers.normalize_temporal_token()`.
- **Fail-Closed**: Unparseable date strings fail closed to HITL review rather than falling back to current system date.
