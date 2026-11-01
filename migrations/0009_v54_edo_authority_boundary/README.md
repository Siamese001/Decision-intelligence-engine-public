# Migration 0009 — V54 EDO authority and boundary correction

This hash-bound snapshot migration moves an exact V53 canonical database to the approved V54 state.

- Baseline SHA-256: `5c326c4825c9ad3621a014361257f13d8fa4a9637196d6473281af06ae03d7a3`
- Final SHA-256: `a780950fffe48e3dbc3e6509f7af8cfd5afef2e2117e0b3437f2afeb11a8129f`
- Approval: `AD-V54-EDO-AUTHORITY-BOUNDARY-2026-001`
- Rollback snapshot: `rollback/baseline/nee_graph_v53.sqlite`

The migration is idempotent, validates database integrity, and fails closed on any unexpected input hash.
