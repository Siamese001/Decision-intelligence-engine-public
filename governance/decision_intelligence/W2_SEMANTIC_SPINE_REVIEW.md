# W2 Semantic Spine Review

## Decision

W2 is **complete with migration blockers**. The normalized semantic spine is defined and every current predicate and node type has an explicit disposition. The active graph remains unchanged; no legacy edge was rewritten.

## What the spine establishes

- Typed domains for initiatives, capabilities, organizations, delivery artifacts, governance, commercial context, evidence, editorial artifacts, and future decision-intelligence entities.
- A closed mapping for all 20 current predicates.
- Explicit treatment of `HAS_CLAIM_TEXT` as provenance-only.
- Explicit quarantine of generic `RELATED_TO`.
- Explicit split requirement for compound `OWNS_OR_LEADS` into `owns` or `leads` only after semantic review.
- Target relations for observations, decisions, pain points, readiness, metrics, agents, tools, deployment, evaluation, mitigation, evidence linkage, and supersession.

## Runtime proof

Command:

```text
python tools/audit_decision_intelligence_semantic_spine.py --graph .local/main/runtime-bge-m3-sherpa-strategic-20260729/generations/generation-combined-remediation-1d2b1a2f0c1e77c644b734c6acb3/graph.sqlite --output .local/w2-decision-intelligence-semantic-spine-20260808/semantic-spine-audit.json
```

Result: `FAIL` by design because the existing graph still contains semantic migration debt.

| Check | Result |
|---|---:|
| Nodes audited | 693 across 32 node types |
| Assertions audited | 1,686 across 20 predicates |
| Current predicates with explicit mapping | 20/20 |
| Unknown node types | 0 |
| Unknown predicates | 0 |
| Compound predicates needing split review | 1 (`OWNS_OR_LEADS`) |
| Generic/provenance predicates requiring special handling | 2 (`RELATED_TO`, `HAS_CLAIM_TEXT`) |
| Legacy/unparsed assertions | 459 |
| Target node types still absent | 12 |
| Provider calls | 0 |
| Graph mutations | 0 |
| Release activation | false |

The audit artifact digest is:

```text
41D1B15A867786F72B7ED61789B4A37FA61743028FE5B28F6EEF4075007A64BE
```

The active graph SHA256 remained:

```text
5CE434C5CB819B66BBD39278573FDA0645C7EDF0377E132B138BF7AC86F9F184
```

## Wave status at the end of W2

| Wave | Scope | Status | Evidence / open work |
|---|---|---|---|
| W0 | Freeze runtime, graph, policy, audit, and golden-question baseline | Complete with blockers | W0 manifest; development runtime only; 233 strict label blockers. |
| W1 | Establish fact, observation, derived-intelligence, and private-overlay boundaries | Complete with blockers | W1 boundary audit; 17 derived assertions in canonical retrieval lane; 3 mixed audience records. |
| W2 | Define normalized semantic spine and legacy mapping dispositions | Complete with blockers | This review; 20/20 predicates mapped, 459 legacy/unparsed assertions, 12 target node types absent. |
| W3 | Add immutable temporal observations and typed project/status semantics | Open | Must implement `observed_at`, `source_as_of`, `stale_after`, supersession, and `STALE_OR_UNKNOWN` behavior. |
| W4 | Build daily intelligence projections | Open | Change brief, decision inbox, readiness gap, dependency warning, and meeting brief. |
| W5 | Add bounded agentic readiness model | Open | Agent/version/deployment/tool/evaluation chain; telemetry remains external. |
| W6 | Build strategic evidence and synthesis workspace | Open | Evidence packets, counterevidence, audience, expiry, and falsification fields. |
| W7 | Prove the signature AI-PDLC and productivity-metric use cases | Open | End-to-end answer evaluations and hard-negative tests. |

## Gate to W3

W3 may proceed with the semantic spine as a design contract. Any migration of legacy predicates or assertions requires a separately generated, digest-bound proposal and the existing graph-mutation lifecycle. W3 must not collapse status text into mutable project properties or treat a missing observation as a healthy status.
