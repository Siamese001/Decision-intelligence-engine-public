# Graph-labeling hardening migration

This additive schema introduces normalized label-state tables, a governed
audience registry, deterministic legacy mapping records, and the semantic
`approved` validation state.

The migration is not a standalone authorization. It must be:

1. bound to the exact active graph hash;
2. included in an approved graph-change set;
3. applied to a new immutable graph/index generation;
4. validated with the label-quality no-regression gate; and
5. activated through the normal W0/E0 release lifecycle.

It starts in `shadow` enforcement mode. A later approval-bound remediation
release may switch it to `enforced` only after all blocking label violations
are zero.
