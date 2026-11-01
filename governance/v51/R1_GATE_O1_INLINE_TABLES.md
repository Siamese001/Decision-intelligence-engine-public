# Gate O1 — NEE V51.0 R1 Conformance

| Graph component | Baseline | Actual | Delta | Change | Retrieval rationale |
|---|---:|---:|---:|---|---|
| Sources | 164 | 169 | 5 | 5 exact row operations | Adds exact source attribution and non-inference boundaries for roster, identity, strategic-gap, and June 29 note retrieval. |
| Source versions | 164 | 169 | 5 | 5 exact row operations | Binds every new source to immutable content hashes and date/authority metadata. |
| Evidence items | 1586 | 1592 | 6 | 6 exact row operations | Provides direct locators for every new identity, role-context, and strategic-synthesis assertion. |
| Nodes (total / active) | 641 / 637 | 651 / 646 | 10 / 9 | 24 exact row operations | Adds missing canonical people and the strategic-gap concept while retiring only the duplicate Amanda node. |
| Node states | 641 | 651 | 10 | 11 exact row operations | Makes current, historical, merged, and retrieval eligibility states explicit. |
| Legacy aliases | 393 | 426 | 33 | 33 exact row operations | Enables exact lookup across PartnerAdvisory and client-domain emails plus confirmed name variants. |
| Canonical aliases (total / active) | 388 / 388 | 421 / 421 | 33 / 33 | 33 exact row operations | Provides normalized one-person resolution without domain-based duplication. |
| Alias lineage maps | 393 | 426 | 33 | 33 exact row operations | Preserves deterministic lineage from legacy alias rows to canonical aliases. |
| Edges (total / active) | 1049 / 1022 | 1066 / 1038 | 17 / 16 | 23 exact row operations | Connects people to PartnerAdvisory and dated workstreams, resolves Damyn, redirects Amanda, and links AI-PDLC to the strategic gap. |
| Claims (total / active) | 524 / 480 | 544 / 499 | 20 / 19 | 21 exact row operations | Adds atomic, source-bounded retrieval statements and supersedes the Damyn conflict claim. |
| Canonical assertions | 1606 | 1643 | 37 | 44 exact row operations | Makes every new/updated meaning-bearing record evidence-gated, temporal, audience-scoped, and approval-bound. |
| Assertion-evidence links | 1624 | 1668 | 44 | 44 exact row operations | Prevents unsupported canonical retrieval and records non-independent note evidence correctly. |
| Edge-assertion lineage maps | 1049 | 1066 | 17 | 17 exact row operations | Preserves exact legacy-edge to canonical-assertion lineage. |
| Claim-assertion lineage maps | 524 | 544 | 20 | 20 exact row operations | Preserves exact legacy-claim to canonical-assertion lineage. |
| Validations (total / open) | 174 / 143 | 176 / 143 | 2 / 0 | 26 exact row operations | Closes resolved identity questions, narrows partially answered questions, and adds only the two genuinely new role/assignment-timing follow-ups. |
| Node-card maps | 1581 | 1624 | 43 | 45 exact row operations | Routes new entities and sources to the existing NEE_00–16/22/99 spine without adding a card. |
| Canonical routes (total / active) | 3711 / 3711 | 3812 / 3811 | 101 / 100 | 102 exact row operations | Provides primary/support retrieval homes and deactivates the retired Amanda node route. |
| Search documents (total / eligible) | 2175 / 1258 | 2222 / 1304 | 47 / 46 | 68 exact row operations | Indexes canonical names, aliases, dated workstream context, and explicit non-inference boundaries while blocking historical/merged rows. |
| Identity-resolution candidates | 9 | 9 | 0 | 1 exact row operations | Closes the Amanda duplicate candidate without auto-merging any unresolved partial name. |
| Entity merge events | 0 | 1 | 1 | 1 exact row operations | Records the explicit Amanda merge as an auditable completed identity event. |
| Entity redirects | 0 | 1 | 1 | 1 exact row operations | Preserves lookup continuity from the retired Amanda node to the canonical survivor. |

| Metric | Pass threshold | Result | Status | Comment |
|---|---|---|---|---|
| Operational orphan rate | ≤5% and no increase | 0.0000% → 0.0000% (+0.0000 pp) | **PASS** | All new people, sources, and the strategic-gap node have explicit routes and evidence-backed relationships. |
| Duplicate node count | 0 new high-impact duplicates | 6 candidate groups → 5 candidate groups (-1) | **PASS** | The Amanda Knarr duplicate candidate is resolved by an explicit merge; no new duplicate group is introduced. |
| Duplicate edge rate | ≤1% unexplained and no increase | 0.0000% → 0.0000% (+0.0000 pp) | **PASS** | Existing affiliations are strengthened or superseded instead of duplicated; new edges use distinct dated scopes. |
| Rare edge-type rate | ≤25% or no increase | 0.0000% → 0.0000% (+0.0000 pp) | **PASS** | Only existing controlled relationship families are used. |
| Claim completeness | 100% | 100.0000% → 100.0000% (+0.0000 pp) | **PASS** | Every proposed claim has subject, source, evidence, canonical assertion, route, confidence, currentness, and lineage. |
| Source metadata completeness | ≥95%; 100% for material sources | 100.0000% → 100.0000% (+0.0000 pp) | **PASS** | All five new material sources have source versions, hashes, scope, authority, posture, and date status. |
| Temporal completeness | ≥90% and no decrease | 100.0000% → 100.0000% (+0.0000 pp) | **PASS** | All current or historical assertions have explicit valid-time and date-status treatment. |
| High-impact identity collisions | 0 unresolved | 0 unresolved → 0 unresolved (+0) | **PASS** | Amanda is merged with redirect lineage; Gessica T and other partials remain blocked rather than promoted. |
| P0/P1 SLA breach rate | 0% | 0.0000% → 0.0000% (+0.0000 pp) | **PASS** | Two P1 identity/affiliation questions close; all remaining open P0/P1 items retain owners and due dates. |
| Decision-safe answer regression | 0 regression on approved test set | 0 failures / 21 cases → 0 failures / 21 cases (+0 failures) | **PASS** | All 21 behavioral cases pass; fixed v50 authority-count expectations are reconciled through exact scratch-delta validation. |

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_01 | Enterprise agentic consistency gap and strategic-partner opportunity | body+frontmatter+route | User strategic synthesis and existing v50 PartnerAdvisory-led AI-PDLC framing. | Improves executive strategy retrieval by separating enterprise synthesis from Chakra attribution. |
| NEE_02 | Incremental June 29 NEA implementation/dependency details | body+frontmatter+route | June 29 NEA companion notes plus the formal packet lineage boundary. | Adds exact workstream-level blockers and implementation evidence while preserving formal-status ranking. |
| NEE_04 | Roster/Q3 capacity inference boundary and new people | body+frontmatter+route | User-confirmed roster identity boundary; existing Q3 capacity validations remain open. | Prevents roster emails from being misretrieved as current role, allocation, or capacity evidence. |
| NEE_05 | Chakra-facing AI-PDLC goal and sequencing | body+frontmatter+route | User-confirmed AI-PDLC goal and non-Chakra attribution boundary. | Creates an exact goal statement with Q3/Q4 sequencing and non-implementation boundary. |
| NEE_06 | Federated shared-minimum standards principle | body+frontmatter+route | User-confirmed federated-standard interpretation of the NEE IT–NEA gap. | Allows retrieval of shared controls versus local ways-of-working tradeoffs. |
| NEE_07 | Gap-to-AI-PDLC response and concrete examples | body+frontmatter+route | AI-PDLC source patterns plus user-confirmed gap and June 29 concrete examples. | Maps each consistency gap to a specific AI-PDLC control and dated example. |
| NEE_10 | Identity closures, new people, and remaining questions | body+frontmatter+route | Explicit identity, employment, merge, and open-question confirmations. | Resolves name variants and redirects while exposing only genuinely open role/authority questions. |
| NEE_11 | Reusable June 29 patterns with evidence limits | body+frontmatter+route | Dated June 29 cross-workstream patterns with non-generalization guardrails. | Indexes reusable patterns by source date and prevents premature generalization. |
| NEE_12 | Clay Palumbo resolution and former-status treatment | body+frontmatter+route | Explicit Clay Palumbo identity/status confirmation. | Returns Clay Palumbo for Sherpa feedback/lead-transition queries without identity ambiguity. |
| NEE_13 | EDO speaker/workstream matrix and unresolved-role list | body+frontmatter+route | June 29 EDO speaker notes and explicit role/authority boundaries. | Answers who has dated EDO workstream context and who remains unresolved. |
| NEE_15 | Dual-domain alias and no-attendance rule | body+frontmatter+route | User-confirmed alias and inference-boundary rule. | Enables exact email/name resolution and blocks domain-based duplicate identities or attendance inference. |
| NEE_16 | Five-source lineage and non-independence boundaries | body+frontmatter+route | Exact new source versions, input hashes, and companion-source lineage. | Makes source authority, hash, and same-event/non-independent lineage directly retrievable. |
| NEE_99 | Roster identity boundary plus automatic approved-current activation after R1/O1 | control_policy | User-confirmed Clay resolution and active roster identity boundary encoded as approval-contingent Appendix A5 / PD-51 policy. | Keeps identity guardrails and release selection behavior aligned with explicit user decisions while preserving approval and quality gates. |
