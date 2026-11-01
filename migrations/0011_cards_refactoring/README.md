# Migration 0011: governed facets and card views

This migration is additive and is applied only through
`dual_pipeline.card_refactor_release`.

It creates:

- `facet_derivation_rule`;
- `object_facet_assignment`;
- `facet_coverage_exception`; and
- `card_view_definition`.

The canonical forward DDL is `dual_pipeline.facets.FACET_SCHEMA_SQL`; its
SHA-256 is written to `schema_migration` and bound into every proposal.
Duplicating that DDL in a second SQL file is intentionally avoided.

## Read-only proposal

```powershell
.\.venv\Scripts\python.exe tools\cards_refactor.py plan `
  --output .local\main\cards-refactoring-proposal.json
```

The planner resolves the graph from `.local/main/runtime/active_pair.json` and
fails if the bytes do not match the active graph hash.

## Application gates

`apply_card_refactor_plan` requires:

1. an untampered, baseline-current plan;
2. an active approved `governance_approval`;
3. `baseline_graph_sha256` equal to the plan baseline;
4. `proposal_sha256` equal to the plan digest;
5. an exact scope containing the plan ID and complete allowed-action set;
6. replacement facet coverage before route retirement;
7. unchanged legacy claim/card and node-card-map digests; and
8. passing SQLite foreign-key and integrity checks.

Any failure rolls back schema, assignments, views, audits, and route
retirements together.

## Rollback

Before activation, discard the staged generation. After activation, use the
normal release rollback to restore the previous graph/index pair. The migration
never edits `database/schema_v54.sql` or `graph/nee_graph.sqlite` in place.
