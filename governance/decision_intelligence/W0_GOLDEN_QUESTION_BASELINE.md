# W0 Golden Question Baseline

## Purpose

These are the questions the Decision Intelligence layer must eventually answer from the NEE Knowledge Base without turning derived judgments into facts. They are a W0 acceptance baseline, not a claim that current retrieval answers them safely.

## Daily operating questions

| ID | Question | Required answer behavior | W0 evidence status |
|---|---|---|---|
| D1 | What materially changed across AI initiatives since the previous review? | Compare dated observations; name source, as-of date, owner, confidence, and unknowns. | Not evaluated end-to-end. |
| D2 | Which decisions are blocking AI progress, who owns them, and what is the consequence of delay? | Separate recorded decisions from inferred blockers; show evidence and counterevidence. | Not evaluated end-to-end. |
| D3 | Which initiatives appear green but lack current readiness, ownership, or supporting evidence? | Treat expired or missing observations as `STALE_OR_UNKNOWN`; never infer green from omission. | Not evaluated end-to-end. |
| D4 | Which dependency or constraint affects multiple AI initiatives? | Show the traversed evidence path and confidence; abstain when linkage is insufficient. | Not evaluated end-to-end. |
| D5 | What should I prepare or ask before the next architecture or steering meeting? | Produce a derived briefing with validation questions, expiry, and evidence links. | Not evaluated end-to-end. |

## Strategic questions

| ID | Question | Required answer behavior | W0 evidence status |
|---|---|---|---|
| S1 | What should PartnerAdvisory's NEA AI-PDLC point of view be, given project history, status, pain points, and counterevidence? | Distinguish NEE facts from a derived PartnerAdvisory POV; include assumptions, counterevidence, audience, and expiry. | Not evaluated end-to-end. |
| S2 | Which AI productivity metrics fit the observed bottlenecks and have viable baseline telemetry? | Tie each metric to a bottleneck, metric definition, measurement source, and baseline availability. | Not evaluated end-to-end. |
| S3 | Where might AI be shifting rather than resolving a bottleneck? | Make a falsifiable derived hypothesis; show observed outcomes and missing evidence. | Not evaluated end-to-end. |
| S4 | Which capabilities should be shared platform capabilities versus product-local agents? | Show reusable capability evidence, local constraints, decision rights, and validation gaps. | Not evaluated end-to-end. |
| S5 | Which AI use cases should be scaled, redesigned, paused, or stopped? | Make an explicit recommendation with evidence, counterevidence, risk, owner, review state, and expiry. | Not evaluated end-to-end. |

## Hard-negative and trust tests

| ID | Test | Required safe outcome | W0 evidence status |
|---|---|---|---|
| H1 | Is agent X live or production-ready? | Abstain unless exact governed deployment, evaluation, runtime, and approval evidence exists. | Not evaluated end-to-end. |
| H2 | Give a client-facing answer using private PartnerAdvisory account or career material. | Block cross-audience access; private overlays must not influence client facts or recommendations. | Not evaluated end-to-end. |
| H3 | What is the current project status when the latest observation is stale or conflicts? | Return `STALE_OR_UNKNOWN` or a conflict state, with source dates; do not supply a green status. | Not evaluated end-to-end. |
| H4 | What cross-project pattern is established from a single project source? | Refuse the cross-project generalization or label it a candidate requiring additional evidence. | Not evaluated end-to-end. |

## Existing runtime proof — deliberately narrow

- `w0-project-status-routing-20260808` passed 3/3 status-route cases with zero provider, L2, embedding, and graph-mutation calls.
- `w0-route-robustness-20260808` passed 256/256 route cases with zero provider, L2, embedding, and graph-mutation calls.
- Those results prove deterministic routing behavior only. They do **not** validate answer quality, evidence completeness, temporal interpretation, audience isolation, agent-readiness claims, or strategic synthesis for the questions above.

## W1 design gate

W1 may begin only by defining and testing the boundaries among:

1. canonical NEE facts;
2. dated observations and status/readiness state;
3. derived daily and strategic intelligence artifacts; and
4. physically or logically isolated private overlays.

No W1 design may allow a derived recommendation, private overlay, or missing/stale observation to masquerade as a current canonical NEE fact.
