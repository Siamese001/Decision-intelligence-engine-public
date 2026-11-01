# V48 migrations

`0001_integrated_hardening` is the only V48.0 migration. It has forward, backfill, validation, and rollback entry points, all delegated to `migrate.py` so there is one implementation authority.

The migration is reversible through the bundled, hash-verified V47.2 database snapshot in `rollback/baseline/`. That snapshot is noncanonical and exists solely for tested rollback.

## 0004 — v49 external-evidence gap routing

Approval-bound additive candidate ingest; no schema, validation-state, identity, ontology, or card-byte change.

- `0006_v51_identity_role_standards`: approved V51 identity, aliases, role/workstream context, open-question reconciliation, enterprise consistency gap, and release-control implementation.

## V52.0

`0007_v52_uploaded_materials` applies the exact approved V52 source-ingest/status/TRM/Google graph snapshot and supports rollback to V51.0.
