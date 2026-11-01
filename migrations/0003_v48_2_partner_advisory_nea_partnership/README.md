# Migration 0003 — PartnerAdvisory–NEA 2026 partnership briefing

This migration is bound to approval `AD-V48.2-partner_advisory-NEA-PARTNERSHIP-2026-001` and the exact G1/C1/O1 digests in `approvals/v48.2/`. It performs an exact fail-closed database replacement only when the target matches the approved v48.1 baseline hash; a second forward run is an idempotent no-op. Rollback restores the packaged byte-exact v48.1 graph snapshot.

- Baseline graph: `5a376919027b37b1b3c566074a8923b18fe842f70d38a5459534e3eaa52f8b0a`
- Candidate graph: `b6b10223b7c6f25eed890df932f0dca50dc0ed47b5d57cb30f3f2f1df01f6ea6`
- Semantic delta: 75 additive operations; no existing semantic rows or canonical card bytes change.
- Candidate posture: 14 assertions remain source-attributed, currentness-unknown, and excluded from authoritative retrieval.
