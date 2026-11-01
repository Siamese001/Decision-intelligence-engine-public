# W7 - AI Decision-Use-Case Qualification

## Outcome

W7 qualifies the three priority NEA AI questions as source-bound, candidate-only
decision contexts. It does not generate an approved POV, KPI commitment,
intervention, graph fact, release, or client-delivery authorization.

## Qualified portfolio

| Use case | Expected posture | Evidence behavior |
|---|---|---|
| AI-PDLC POV | `CANDIDATE_ANSWER_READY` | Source-bound support and counterevidence |
| AI productivity metrics | `CANDIDATE_ANSWER_WITH_GAPS` | Source-bound support with visible counterevidence gap |
| Agentic readiness | `CANDIDATE_ANSWER_READY` | Source-bound support and counterevidence |

## Hard-negative proof

| Case | Required behavior |
|---|---|
| Missing required project status | `BLOCKED_STALE_OR_UNKNOWN`, `NO_ANSWER` |
| Conflicting project status | `NO_ANSWER` |
| Untrusted strategic evidence | `NO_ANSWER` |
| Unknown answer section | Closed-world contract rejection |

The evaluator runs provider-free, preserves source assertion lineage, and proves
zero graph mutations and zero release activation. Source-bound retrieval can
consume these decision contexts directly; there is no separate W8–W10 review
or operations wave in this checkout.
