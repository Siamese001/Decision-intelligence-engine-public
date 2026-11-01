# W1 Boundary Review

## Decision

W1 is **complete with migration blockers**. The boundary contract is now explicit and executable. The current graph remains unchanged; the audit records debt for a later, exact-scope, approval-bound migration.

## Boundary established

The W1 contract separates four lanes:

1. **Canonical NEE facts** — evidence-bound propositions in the NEE Knowledge Base.
2. **Dated observations** — append-only status, metric, decision, dependency, and readiness observations with explicit as-of and expiry semantics.
3. **Derived decision intelligence** — change briefs, decision inbox items, readiness gaps, dependency warnings, meeting briefs, POVs, hypotheses, metric proposals, and recommendations. These remain non-authoritative and must carry evidence, counterevidence, review state, expiry, and falsification criteria.
4. **Private overlays** — separate, explicitly scoped PartnerAdvisory/account or personal context. They may read client facts, but cannot write client facts or influence client-facing retrieval.

The audience registry is closed-world. A query that mixes a client audience with a private or PartnerAdvisory-restricted overlay must fail closed. Confidential records cannot be exposed to a client audience without explicit approved classification and provenance.

## Runtime proof

Command:

```text
python tools/audit_decision_intelligence_boundaries.py --graph .local/main/runtime-bge-m3-sherpa-strategic-20260729/generations/generation-combined-remediation-1d2b1a2f0c1e77c644b734c6acb3/graph.sqlite --output .local/w1-decision-intelligence-boundary-20260808/boundary-audit.json
```

Result: `FAIL` by design because this is a baseline audit and current graph debt is not silently treated as compliant.

| Check | Result |
|---|---:|
| Assertions audited | 1,686 |
| Derived assertions in canonical lane | 17 |
| Derived assertions in canonical retrieval lane | 17 |
| Canonical assertions missing extraction lineage | 76 |
| Client/private or restricted audience mixtures | 3 |
| Confidential records with client audience | 2 |
| Invalid audience labels | 0 |
| Unknown audience labels | 0 |
| Provider calls | 0 |
| Graph mutations | 0 |
| Release activation | false |

The audit artifact digest is:

```text
4956FAC6248DB581611CAC55617ABBD6289CCF041F07CE8DA2C3450C2A7714FC
```

The exact active graph SHA256 was unchanged across the audit:

```text
5CE434C5CB819B66BBD39278573FDA0645C7EDF0377E132B138BF7AC86F9F184
```

## Interpretation

The important W1 outcome is not that the baseline passes. It is that the system now has a written, closed-world boundary contract and a reproducible measurement of where the existing graph violates it. The current violations are migration candidates, not authorization to mutate.

## Gate to W2

W2 may define the normalized semantic spine only after preserving these constraints:

- canonical fact, dated observation, derived intelligence, and private overlay remain distinct;
- derived assertions are not promoted to canonical current retrieval evidence;
- private overlays remain outside the NEE Knowledge Base release path;
- every strategic answer can identify its source facts, observations, counterevidence, audience, review state, and expiry;
- any migration proposal is exact-scope, digest-bound, and separately approved.
