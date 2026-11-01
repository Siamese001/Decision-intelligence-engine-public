# Migration 0001 — Integrated V48.0 hardening

This is the single digest-bound migration authorized for the V48.0 candidate. It retains internal dependency ordering but has no wave approval pauses.

## Phases

1. Reconstruct the eight legacy authority tables with enforceable primary keys, foreign keys, checks, and duplicate protection.
2. Create semantic registries, assertion/evidence/provenance, temporal, identity, routing, search, ingestion, audit, backup, and quality-control structures.
3. Insert the exact 14,012 approved rows from immutable JSONL rowsets.
4. Apply the eight exact approved validation due-date updates.
5. Replace three compatibility views with currentness-safe definitions.
6. Validate legacy preservation, exact rowset digests, cards, integrity, foreign keys, FTS parity, and currentness leakage.

The approved rebuild SQL required a narrowly scoped execution normalization: all dependent views are temporarily removed and recreated byte-for-byte, and the legacy priority CHECK uses a closed allowlist that includes the exact descriptive strings already present in V47.2. This changes no legacy value and admits no arbitrary new value.

## Commands

```bash
python migrations/migrate.py apply \
  --database /path/to/nee_graph.sqlite \
  --package-root /path/to/NEE_v48.0 \
  --backup /safe/path/nee_graph_v47.2.bak

python migrations/migrate.py validate \
  --database /path/to/nee_graph.sqlite \
  --package-root /path/to/NEE_v48.0

python migrations/migrate.py rollback \
  --database /path/to/nee_graph.sqlite \
  --package-root /path/to/NEE_v48.0
```

`apply` is idempotent. A second execution validates the existing V48 database and exits without writing.
