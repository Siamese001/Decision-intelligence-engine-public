# Transcript Synthesis Evaluation Rubric (V2.0 — Hardened)
**Contract Reference:** `governance/decision_intelligence/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md`  
**Governing Standard:** NEE Master Constraints C-01 through C-21  
**Target Artifacts:** Artifact 01 (Immutable Source Transcript), Artifact 02 (Executive Strategic Brief / Recap), Artifact 03 (Canonical Signal Projection JSON)  
**Evaluator Specification:** Single impartial high-reasoning LLM judge (`gpt-5.6-luna`, `reasoning_effort: high`) operating only after deterministic QA gates pass  
**Authoritative Principle:** Deterministic source, schema, provenance, ontology, lifecycle, topology, idempotency, and write-gateway failures cannot be overridden by an LLM score.
**Active Constraint Additions (C-19 through C-21):**
- **C-19 Career Leverage & Artifact Independence Firewall**: Output 01 and Output 02 must remain 100% team-safe (PartnerAdvisory + NEA). Personal career strategy, executive positioning, and compensation vectors are strictly quarantined to the encrypted `INTERNAL_ADVISORY` shadow graph. Any leak into Outputs 01/02 is a blocking defect.
- **C-20 Anti-Echo / Marginal Value Law**: Conditional sections must deliver ≥ 80% net-new information not already present in the core narrative. Restating already-captured findings without added analytical depth fails this gate.
- **C-21 Persona-Centric Appendix Depth (Copilot Gold Standard)**: The Appendix is an independent, person-centric analytical lens exempt from echo-avoidance. Tiered validation floors enforced: PRIMARY (>30% turns) ≥ 150 words / 3 themes (targeting 500–850 words / 5–7 themes on extended sessions); CO_LEAD (15–30%) ≥ 80 words / 2 themes (targeting 200–350 words); PARTICIPANT (5–15%) ≥ 40 words / 2 themes (targeting 80–200 words); CONTRIBUTOR (<5%) ≥ 15 words / 1 theme.

---

## 1. Overview & Evaluation Architecture

This rubric is the authoritative evaluation contract for synthesizing raw meeting transcripts and attached domain artifacts into:

1. **Artifact 02 — Executive Strategic Brief / Recap:** A grounded decision instrument for senior decision-makers, engineering leaders, architects, and delivery owners.
2. **Artifact 03 — Canonical Signal Projection JSON:** A pristine, provenance-preserving, queryable graph delta suitable for controlled write-gateway evaluation.

A valid-looking document or JSON file is insufficient.

### 1.1 Ultimate Success Criteria

#### Output 2 — Executive-Grade Strategic Brief

A senior decision-maker must be able to answer, from the brief alone:

1. What is happening?
2. Why does it matter economically, operationally, technically, and now?
3. What evidence supports that conclusion?
4. What decision must leadership make?
5. What recommendation is being made?
6. Which alternatives are rejected or deferred?
7. What is the consequence of delay or non-decision?
8. What bounded execution path follows?
9. Who is accountable, by when, subject to which dependencies, and measured by what criterion?
10. Which statements are facts, proposals, inferences, open questions, or ratified commitments?

The required synthesis chain is:

> evidence → business meaning → decision → bounded execution → measurable outcome

If the source does not establish a financial value, owner, date, decision, or commitment, the brief must state that limitation explicitly and create a validation, assignment, or decision action. It must never manufacture certainty.

#### Output 3 — Pristine Knowledge Graph JSON

Output 3 succeeds only when it is simultaneously:

- valid against the authoritative schema;
- grounded in immutable source evidence;
- epistemically and attribution-safe;
- normalized to the canonical ontology;
- temporally and lifecycle coherent;
- referentially complete;
- directionally valid;
- free of orphan nodes and unsupported edges;
- idempotent and overwrite-safe against the base graph;
- within mutation and payload budgets; and
- demonstrably queryable through representative graph traversals.

Every writeable node, assertion, and edge must have:

- a canonical identity;
- a valid lifecycle state;
- resolvable endpoints;
- exact provenance;
- an explicit modality and epistemic status;
- a defensible place in the graph topology; and
- deterministic overwrite behavior.

No LLM score may override a deterministic graph-integrity failure.

---

## 1.2 Evaluation Sequence

The evaluator and release pipeline must execute in this order:

1. Validate the immutable source manifest and raw source binding.
2. Validate structured attribution metadata.
3. Classify the meeting archetype for each output.
4. Validate Artifact 02 deterministic structure and claim provenance.
5. Validate Artifact 03 strict JSON, schema, ontology, provenance, lifecycle, topology, and mutation integrity.
6. Validate cross-artifact consistency.
7. Run archetype-specific deterministic completeness checks.
8. Apply hard failures and score caps.
9. Only then apply LLM quality scoring.
10. Compute the structured verdict.
11. Permit write-gateway release only if both deterministic and LLM gates pass.

The evaluator must fail closed. An exception, parse failure, unresolved source chain, or incomplete validation result is a failure, not an advisory.

---

## 1.3 Canonical Archetype Registry

The following five archetypes are the only permitted meeting classifications:

```text
STRATEGIC_DISCOVERY
ARCHITECTURE_DEEP_DIVE
TACTICAL_ALIGNMENT
GOVERNANCE_REVIEW
LESSONS_LEARNED
```

All five archetypes are valid for both Output 2 and Output 3.

Classification must include:

- `selected_archetype`;
- `confidence`;
- `supporting_evidence_ids`; and
- `mandatory_checks`.

### Archetype Definitions and Mandatory Checks

| Archetype | Primary Character | Output 2 Mandatory Checks | Output 3 Mandatory Checks |
|---|---|---|---|
| `STRATEGIC_DISCOVERY` | Broad business catalysts, cross-silo discovery, partnership wedges, and strategic options | Business value case, executive decision request, bounded wedge, value-validation action, next decision gate | Cross-domain bridge, strategic dependency, decision leverage, evidence-backed business-to-technical topology |
| `ARCHITECTURE_DEEP_DIVE` | Technical dependencies, integration contracts, security, APIs, schemas, and implementation constraints | System relationship detail, source-of-truth treatment, acceptance test, integration decision, dependency owner | Complete system/dependency chain, valid predicate domains, interface or mechanism status, technical evidence |
| `TACTICAL_ALIGNMENT` | Immediate deliverables, blockers, ownership, and milestone alignment | Every active blocker has an accountable owner and target timing; ratified actions are separated from discussion | Action-to-owner and action-to-workstream/project closure; blocker state and due-date integrity |
| `GOVERNANCE_REVIEW` | Protocol compliance, ratification, sign-off, risk re-weighting, and state transitions | Decision status, ratification evidence, superseded alternatives, governance owner, effective date | Explicit lifecycle transitions, ratification evidence, `SUPERSEDES` relationships, no contradictory active states |
| `LESSONS_LEARNED` | Root causes, systemic failure patterns, controls, and verification | Root cause, corrective control, accountable owner, verification metric, prevention boundary | Root-cause-to-control topology, risk treatment, verification metric, evidence-backed causal claims |

The evaluator must not select an archetype based only on meeting title or participant list. Classification must be supported by evidence IDs.

---

## 1.4 Canonical Registry Contract

The prompt, Pydantic models, QA gates, evaluator, and write gateway must import the same registry. No component may maintain an independent list of prefixes, predicates, states, evidence kinds, or mutation categories.

### 1.4.1 Entity Prefix Registry

The authoritative graph entity prefixes are:

```text
person
org
system
workstream
project
metric
role
policy
decision
risk
dependency
action
```

Entity IDs must conform to:

```text
<prefix>:<lowercase-slug>
```

The canonical grammar is:

```text
^[a-z]+:[a-z0-9][a-z0-9._-]*$
```

Examples:

```text
person:jordan-lee
org:PartnerAdvisory
system:unifier
system:oracle-p6
workstream:developer-workflow
project:transmission-expansion
metric:cycle-time
role:program-owner
policy:ss0-access
decision:mvp-scope
risk:manual-resequencing
dependency:iam-approval
action:publish-gap-list
```

The following are not graph entity IDs and must use their own canonical grammars:

```text
claim-<identifier>
ev-<identifier>
rel-<encoded-from>--<encoded-to>--<predicate>
```

Conversational artifacts such as meeting invitations, room microphones, slide decks, calendar events, travel arrangements, and social activities must not be instantiated as graph entities.

### 1.4.2 Evidence Kind Registry

The only permitted evidence kinds are:

```text
summary
decision
risk
dependency
technical
strategic
```

The following must not be used as evidence kinds:

```text
fact
proposal
commitment
action
inference
observation
```

Those concepts belong in modality or epistemic fields.

### 1.4.3 Modality Registry

Every material claim must contain one modality:

```text
DISCUSSION
PROPOSED
RATIFIED
COMMITTED
BLOCKED
SUPERSEDED
```

Modality describes the status of the statement in the meeting. It does not describe whether the statement is true.

### 1.4.4 Epistemic Status Registry

Every material claim must contain one epistemic status:

```text
DIRECT_FACT
DOCUMENTED_FACT
MODEL_INFERENCE
OPEN_QUESTION
```

Epistemic status describes the basis and certainty of the statement.

A `MODEL_INFERENCE` must never be silently written as a canonical fact. It must either:

- be marked non-writeable;
- be marked `REVIEW_REQUIRED`; or
- contain an explicit inference rule and supporting evidence.

### 1.4.5 Lifecycle State Registry

The only permitted graph lifecycle states are:

```text
PROPOSED
RATIFIED
SUPERSEDED
BLOCKED
ACTIVE
```

Lifecycle state is distinct from modality. For example:

- a proposed risk may be `PROPOSED` and `DISCUSSION`;
- a committed action may be `ACTIVE` and `COMMITTED`;
- an obsolete decision may be `SUPERSEDED` and `SUPERSEDED`.

### 1.4.6 Predicate Registry

The canonical directional predicates are:

```text
OWNS
MEMBER_OF
BLOCKED_BY
DEPENDS_ON
INTEGRATES_WITH
EVALUATES
ACCELERATES
IMPACTED_BY
SUPERSEDES
```

`SUPERSEDES` is mandatory and must be implemented as a canonical directional predicate. A new state or decision that retires an older fact must use `SUPERSEDES` rather than merely appending a contradictory assertion.

Predicate domains and ranges must be enforced centrally. At minimum:

| Predicate | Valid Source Types | Valid Target Types |
|---|---|---|
| `OWNS` | `person`, `org`, `role` | `action`, `project`, `system`, `workstream`, `decision` |
| `MEMBER_OF` | `person`, `role` | `org`, `workstream` |
| `BLOCKED_BY` | `project`, `workstream`, `action`, `system`, `decision` | `risk`, `dependency`, `decision`, `system` |
| `DEPENDS_ON` | `project`, `workstream`, `action`, `system`, `decision` | `project`, `system`, `dependency`, `decision` |
| `INTEGRATES_WITH` | `system` | `system` |
| `EVALUATES` | `person`, `org`, `role`, `system`, `workstream` | `metric`, `project`, `system`, `decision` |
| `ACCELERATES` | `project`, `workstream`, `system`, `org`, `action` | `project`, `workstream`, `system`, `metric` |
| `IMPACTED_BY` | `project`, `workstream`, `system`, `metric`, `decision` | `risk`, `dependency`, `decision` |
| `SUPERSEDES` | `decision`, `project`, `workstream`, `system`, `risk`, `dependency` | `decision`, `project`, `workstream`, `system`, `risk`, `dependency` |

The registry must reject unsupported domain/range combinations.

### 1.4.7 Mutation Category Registry

Mutation categories must be centrally defined. At minimum:

```text
entity_upsert
decision_update
proposal_update
risk_update
dependency_update
action_update
relationship_upsert
supersession_update
```

The C-17 total mutation ceiling is `25` across all categories, not `25` per category.

---

## 1.5 Common Claim Ledger

Output 2 and Output 3 must use the same normalized claim ledger.

Canonical claim shape:

```json
{
  "claim_id": "claim-014",
  "kind": "technical",
  "modality": "PROPOSED",
  "epistemic_status": "MODEL_INFERENCE",
  "text": "The P6-to-Unifier mapping requires validation before the target workflow can be approved.",
  "evidence_ids": ["ev-031"],
  "entities": ["system:oracle-p6", "system:unifier"],
  "owner": null,
  "due_date": null
}
```

Every material claim, action, decision, owner, date, metric, system relationship, financial value, technical constraint, and lifecycle transition must have one or more evidence IDs.

The claim ledger must be normalized before either output is scored. The evaluator must reject claims that:

- have no evidence IDs;
- reference nonexistent evidence;
- contain unsupported owners or dates;
- silently upgrade inference to fact;
- convert discussion into commitment;
- convert proposal into ratification; or
- violate attribution restrictions.

---

# 2. Output 2 Scoring Dimensions: Executive Strategic and Technical Brief

Output 2 is scored across four dimensions for a composite maximum of `100` points.

Each dimension is scored from `0–25`.

The brief must contain the following unique, ordered level-two sections:

1. `Meeting Objective`
2. `Key Discussion Points & Findings`
3. `Business Meaning and Value Case`
4. `Executive Decision`
5. `Bounded Execution Wedge`
6. `Next Steps & Action Items`
7. `Open Decisions / Blockers`

Headings alone do not satisfy the requirement. Each section must contain substantive, evidence-linked content or an explicit grounded sentinel.

Permitted sentinels include:

```text
Value was not quantified in the source.
No decision recorded.
No new action commitments were ratified.
MVP status: NOT RATIFIED.
Target timing was not established in the source.
```

---

## 2.0 Output 2 Archetype and Format Adaptivity

The evaluator must classify the meeting using the canonical registry before scoring.

The brief must bridge executive and technical audiences. It must explain:

- the business problem;
- the operational and technical reality;
- the evidence supporting the interpretation;
- the decision or unresolved decision;
- the bounded execution path;
- ownership, timing, dependencies, and success criteria.

A polished chronological summary is insufficient.

---

## Dimension 1: Executive Altitude, Business Meaning, and Value Case — 0–25 Points

Measures whether the brief translates the meeting into an executive-grade business case rather than merely listing discussion topics.

The mandatory `Business Meaning and Value Case` section must contain:

```text
Business problem:
Value at stake:
Baseline:
Target:
Value mechanism:
Confidence:
Consequence of delay:
Evidence:
```

Rules:

- Quantify revenue, cost, capital, time, risk, compliance, or capacity whenever the source supports the quantification.
- If the source does not quantify value, write `Unquantified in source`.
- Do not invent ROI, savings, exposure, headcount, dates, or financial consequences.
- Every material number must have evidence and must preserve its meaning and scope.
- A number appearing in the transcript does not automatically establish its baseline, business impact, or financial interpretation.
- An unquantified value case must include a value-validation action.
- The brief must explain why the matter is important now.
- The consequence of delay must be sourced or explicitly marked as unresolved.

### 21–25 Points — Exemplary

The brief:

- clearly states what is happening and why it matters now;
- connects economic, operational, technical, and timeline pressures;
- identifies the value mechanism rather than merely naming a benefit;
- distinguishes source-established value from model interpretation;
- quantifies supported stakes accurately;
- explicitly states unquantified areas;
- explains the consequence of delay;
- links the value case to the executive decision and bounded execution path; and
- provides evidence for all material claims.

### 16–20 Points — Good

The brief provides strong business context and explains the primary objective and urgency. The value mechanism is substantially clear, but one or more supported quantitative stakes, baseline details, or consequences of delay are underdeveloped.

### 10–15 Points — Marginal

The brief primarily summarizes participants, topics, or updates. Business consequences are present but muted, generic, weakly evidenced, or disconnected from the proposed decision and execution path.

### 0–9 Points — Unacceptable

The brief is effectively administrative meeting minutes. It does not establish the business problem, value mechanism, urgency, consequence of delay, or evidence-backed strategic importance.

---

## Dimension 2: Technical Substance, System Relationships, and Architectural Grounding — 0–25 Points

Measures whether an engineering lead or enterprise architect can understand the exact technical reality discussed.

System-name presence alone earns no substantial technical credit. Every named system must connect to at least one of:

- a business consequence;
- a data-flow dependency;
- an integration constraint;
- a failure mode;
- a scope boundary;
- a source-of-truth issue;
- an execution milestone; or
- a validated open question.

The brief must distinguish technical claims using:

```text
OBSERVED_CURRENT_STATE
RATIFIED_TARGET_STATE
PROPOSED_OPTION
OPEN_QUESTION
MODEL_INFERENCE
```

For every asserted system relationship, where applicable, the brief must identify:

```text
Source system:
Target system:
Business object or data domain:
Direction:
Interface or mechanism:
Current or target state:
Source of truth:
Identity/key:
Cadence or latency:
Security/IAM constraint:
Failure or reconciliation behavior:
Owner/dependency:
Evidence:
```

The evaluator must not infer an API, integration, source-of-record relationship, ownership relationship, or target architecture merely because two systems were mentioned in the same meeting.

If the mechanism is unknown, the brief must state that it is unknown and create a validation action.

### 21–25 Points — Exemplary

The brief accurately names and relates the relevant systems, tools, data domains, interfaces, constraints, workarounds, and failure modes. It explains how data flows or fails to flow and distinguishes observed current state from proposed or ratified target state.

It connects technical realities to business consequences and bounded scope. Unknown mechanisms are explicitly labeled and assigned to validation rather than presented as facts.

### 16–20 Points — Good

The brief identifies the major systems and integration challenges and explains their business relevance. Minor gaps exist in data lineage, interface mechanism, ownership, source-of-truth treatment, or failure-mode detail.

### 10–15 Points — Marginal

The brief references systems generically or lists system names without explaining their relationships, data domains, architectural constraints, or business consequences. It may imply unsupported integrations or source-of-record relationships.

### 0–9 Points — Unacceptable

The brief has no meaningful technical grounding, misrepresents the technical state, or reduces architecture and integration discussion to generic administrative language.

---

## Dimension 3: Strategic Synthesis, Decision Quality, and Evidence Discipline — 0–25 Points

Measures whether the brief converts dialogue and evidence into coherent implications, a decision request, and a defensible recommendation.

The mandatory `Executive Decision` section must contain:

```text
Executive conclusion:
Decision required:
Recommended choice:
Alternatives rejected or deferred:
Reason:
Decision owner:
Decision deadline:
Consequence of no decision:
Decision status:
Evidence:
```

Rules:

- Discussion is not a decision.
- A proposal is not a commitment.
- A speaker’s opinion is not an organizational decision.
- A ratified decision requires explicit ratification evidence.
- If no decision was recorded, the brief must state:

  `No decision recorded. The following decision remains required: ...`

- A decision without an owner or deadline must be marked unresolved.
- The brief must distinguish facts, proposals, inferences, open questions, and commitments.
- The recommendation must not be represented as approved unless the source contains ratification evidence.
- Alternatives may be deferred rather than rejected when the source does not establish rejection.

### 21–25 Points — Exemplary

The brief connects disparate statements into strategic vectors and makes the implications explicit. It identifies the required leadership decision, recommendation, alternatives, decision owner, timing, consequence of delay, and status.

It preserves epistemic boundaries, uses evidence at claim level, and correctly distinguishes a proposed wedge from a ratified commitment.

### 16–20 Points — Good

The brief synthesizes the main implications and provides a useful decision framing. Minor gaps exist in alternatives, decision timing, evidence specificity, or the distinction between proposal and commitment.

### 10–15 Points — Marginal

The brief repeats dialogue or quotations with limited analysis. The decision request is vague, implicit, unsupported, or confused with the meeting discussion.

### 0–9 Points — Unacceptable

The brief is a transcript paraphrase with no meaningful synthesis, decision framing, recommendation, or evidence discipline.

---

## Dimension 4: Immediate Delivery Scope & Release Boundaries, Action Completeness, and Plain-English Governance — 0–25 Points

Measures whether the brief defines a bounded, measurable execution path in plain, transparent layman English without inventing scope, ownership, timing, or commitments, and without consulting jargon or pseudo-intellectual word salad.

For strategic and architecture-oriented meetings, the mandatory `Immediate Delivery Scope & Release Boundaries` (formerly Bounded Execution Wedge) section must be written in transparent, plain English and contain:

```text
Target user / Beneficiary:
Proposed capability / What we are delivering:
In scope / Included in this release:
Outside scope / Excluded from this release:
Systems touched:
Systems explicitly not replaced:
Required dependencies:
Known constraints:
First deliverable:
Acceptance test:
Exit criterion:
Accountable owner:
Decision gate:
Success metric:
Evidence:
```

**Plain-English & Layman Scoping Mandate:**
- Words like "wedge", "bounded execution wedge", "manifest-controlled", "governed execution wedge", or defensive hedging ("not an invented target") are strictly penalized.
- Academic word salad ("evidenced rejection behavior", "aspirational operating concept", "configuration persistence versus data portability") across the recap document incurs an automatic score cap and deduction.

A valid scope must identify:

- the user;
- the capability;
- the boundary;
- the systems or data involved;
- the acceptance test;
- the decision gate;
- the owner or explicit unresolved-owner status; and
- the success metric.

The following are insufficiently scoped:

```text
Explore integration opportunities.
Align stakeholders.
Move forward with the platform.
Continue discussions.
```

If no MVP or wedge was ratified, the brief must state:

```text
MVP status: NOT RATIFIED
Next required decision: ...
```

### Action Register Contract

The `Next Steps & Action Items` section must contain the exact table schema:

```text
# | Action Item | Accountable Owner | Responsible Party | Target Timing | Evidence IDs | Status
```

Every committed action requires:

```text
Action verb:
Concrete object:
Deliverable or outcome:
Accountable owner:
Responsible party, if supported:
Target date or explicit unresolved-date status:
Dependency:
Acceptance evidence:
Source evidence:
Modality:
```

Action quality, not action count, is scored.

`Review` is valid only when it specifies a concrete output, such as:

```text
Review the P6-to-Unifier mapping and publish the gap list.
```

Generic actions do not pass:

```text
Schedule follow-up.
Coordinate with the team.
Continue discussions.
Move forward.
```

A meeting with zero new commitments is valid when the brief states:

```text
No new action commitments were ratified.
```

No universal five-action floor applies.

### Archetype-Specific Action Completeness

| Archetype | Minimum Completeness Requirement |
|---|---|
| `STRATEGIC_DISCOVERY` | Decision request, bounded wedge, value-validation action where needed, and next decision gate |
| `ARCHITECTURE_DEEP_DIVE` | Integration decision, technical acceptance test, dependency owner, and risk treatment |
| `TACTICAL_ALIGNMENT` | Every active blocker has an accountable owner and target timing |
| `GOVERNANCE_REVIEW` | Every state transition has ratification or supersession evidence |
| `LESSONS_LEARNED` | Every material root cause has a corrective control and verification metric |

### 21–25 Points — Exemplary

The brief defines a bounded wedge with explicit user, capability, scope boundary, systems, dependencies, acceptance test, decision gate, owner, and success metric.

Actions are concrete, evidence-linked, modality-safe, and assigned only where supported. Dates are precise or explicitly unresolved. The brief preserves tentative offers as proposals and does not promote room-mic discussion into commitments.

### 16–20 Points — Good

The execution path and actions are substantially actionable. Minor gaps exist in acceptance criteria, dependencies, success measures, ownership, or decision-gate precision.

### 10–15 Points — Marginal

The brief contains vague actions, passive or modal constructions, unsupported owners, unresolved commitments, or an incomplete scope boundary. It may convert suggestions into obligations.

### 0–9 Points — Unacceptable

The brief invents mandates, promotes administrative trivia, fabricates owners or commitments, lacks an execution boundary, or contains generic consulting platitudes instead of executable actions.

---

# 3. Output 3 Scoring Dimensions: Canonical Signal Projection JSON

Output 3 is scored across five dimensions for a composite maximum of `100` points.

Each dimension is scored from `0–20`.

The required score bands for every Output 3 dimension are:

| Score | Meaning |
|---:|---|
| 17–20 | Exemplary |
| 14–16 | Good |
| 8–13 | Marginal |
| 0–7 | Unacceptable |

Any Output 3 deterministic integrity failure is a hard failure regardless of the composite score.

---

## 3.0 Output 3 Archetype Classification

The evaluator must classify the meeting using the canonical five-archetype registry.

The classification must include:

```json
{
  "selected_archetype": "ARCHITECTURE_DEEP_DIVE",
  "confidence": 0.92,
  "supporting_evidence_ids": ["ev-011", "ev-018"],
  "mandatory_checks": [
    "system_dependency_chain",
    "integration_mechanism_status",
    "technical_acceptance_test"
  ]
}
```

The evaluator must not use a separate Output 3 archetype registry.

---

## Dimension 1: Signal Amplification and Next Best Action Intelligence — 0–20 Points

Measures whether the graph captures high-signal intelligence, non-obvious dependencies, decision levers, and forward-looking actions appropriate to the meeting archetype.

### 17–20 Points — Exemplary

The graph:

- captures the most important strategic, technical, or operational signal;
- identifies evidence-backed dependencies beyond direct conversational repetition;
- creates useful next-best-action relationships;
- preserves uncertainty and modality;
- represents the archetype-specific topology;
- avoids generic filler nodes and edges; and
- does not manufacture connections solely to improve graph connectedness.

Examples:

- Strategic discovery: transmission access constraints linked to supported siting or capacity implications.
- Architecture deep dive: system integration prerequisites linked to explicit technical constraints and acceptance tests.
- Tactical alignment: blockers linked to accountable owners, workstreams, and target timing.
- Governance review: state transitions linked to ratification or supersession evidence.
- Lessons learned: root causes linked to controls and verification metrics.

### 14–16 Points — Good

The graph captures the principal signals and dependencies and contains useful next steps. Secondary indirect impacts or archetype-specific relationships may be omitted.

### 8–13 Points — Marginal

The graph captures only direct statements and produces generic actions such as scheduling follow-ups. Signal leverage and cross-domain implications are limited.

### 0–7 Points — Unacceptable

The graph is a dead log, contains no forward-looking intelligence, misses critical dependencies, or emits unsupported strategic conclusions.

---

## Dimension 2: Entity Resolution and Ontological Precision — 0–20 Points

Measures adherence to the canonical ontology, identity grammar, alias resolution, and graph hygiene.

### 17–20 Points — Exemplary

The graph:

- uses only registry-approved entity prefixes and ID grammar;
- resolves aliases to canonical identities;
- avoids duplicate entities;
- validates every relationship endpoint, owner, decision, risk, dependency, action, and assertion reference;
- excludes transitory conversational artifacts;
- preserves canonical case and slug normalization; and
- contains no spurious node inflation.

### 14–16 Points — Good

Entity modeling is substantially correct with only minor naming inconsistency on non-critical secondary entities.

### 8–13 Points — Marginal

Some IDs lack canonical prefixes, aliases remain unresolved, or minor conversational artifacts are instantiated as graph entities.

### 0–7 Points — Unacceptable

The graph contains illegal prefixes, free-text node names, pervasive duplication, unresolved aliases, or widespread ontology violations.

---

## Dimension 3: Temporal Dynamics and Lifecycle Coherence — 0–20 Points

Measures date validity, temporal freshness, lifecycle transitions, decay, and supersession.

All contractually required dates must use:

```text
MM/DD/YYYY
```

The following are invalid where the contract requires canonical dates:

```text
2027-01-15
January 15, 2027
15/01/2027
```

Relevant records must contain the required temporal fields:

| Record Type | Required Fields |
|---|---|
| Decision update | `observed_at`, `stale_after` |
| Proposal update | `observed_at`, `stale_after` |
| Risk update | `observed_at`, `stale_after` |
| Action update | `due_date` |
| Lifecycle transition | `observed_at`, prior state, new state, evidence |

Temporal rules:

- `observed_at <= stale_after`;
- `observed_at <= due_date` where applicable;
- stale active assertions must be marked stale, superseded, blocked, or otherwise excluded from active state;
- the evaluator must use the caller-supplied `as_of` date;
- the gate must not call `date.today()` internally;
- decisions and risks should default to an explicit decay horizon, normally observed date plus 90 days or an explicitly sourced milestone;
- lifecycle transitions require evidence;
- contradictory active facts require `SUPERSEDES` or an explicit unresolved state.

### 17–20 Points — Exemplary

All dates are canonical and valid. Required decay horizons and lifecycle states are present. State transitions are evidence-backed. Superseded facts are explicitly retired, and no contradictory active assertions accumulate.

### 14–16 Points — Good

Temporal formatting is valid and decay horizons are generally present. Minor lifecycle defaults or non-critical transition details are incomplete.

### 8–13 Points — Marginal

Some dates or decay parameters are missing, state handling is inconsistent, or minor formatting drift exists.

### 0–7 Points — Unacceptable

The graph lacks observation dates, uses invalid date strings, contains active expired facts, or has no coherent lifecycle tracking.

---

## Dimension 4: Evidence Envelope Grounding and Attribution Traceability — 0–20 Points

Measures exact source provenance, evidence-kind validity, attribution fidelity, modality safety, and grounding in Artifact 01.

Every writeable entity, assertion, and relationship must have at least one evidence reference.

Evidence records must contain, where applicable:

```json
{
  "evidence_id": "ev-031",
  "source_id": "transcript-001",
  "artifact_sha256": "…",
  "start_offset": 91822,
  "end_offset": 91944,
  "exact_text": "…",
  "attribution_mode": "DIRECT"
}
```

The locator must identify exact bytes in the immutable source. Substring presence is insufficient.

The six evidence kinds are closed:

```text
summary
decision
risk
dependency
technical
strategic
```

Attribution modes are:

```text
DIRECT
SHARED_ROOM_MIC
DOCUMENT
UNRESOLVED
```

For `SHARED_ROOM_MIC` evidence:

- individual speaker attribution is prohibited unless independently verified;
- the speaker must be absent or represented as `collective:room-mic`;
- individual ownership, commitment, or opinion must not be inferred.

Explicit evidence is required for:

- `RATIFIED` decisions;
- `COMMITTED` actions;
- individual ownership;
- exact due dates;
- financial values;
- technical integration assertions.

### 17–20 Points — Exemplary

All material assertions have exact locators, valid evidence kinds, accurate attribution modes, valid modality, and appropriate epistemic status. No unsupported nodes or edges are injected.

### 14–16 Points — Good

Evidence is substantially complete and exact. Minor quote truncation or non-critical attribution detail does not alter meaning or authority.

### 8–13 Points — Marginal

Some locators, classifications, or attribution details are incomplete or weakly grounded, but the core graph remains recoverable.

### 0–7 Points — Unacceptable

The graph contains fabricated assertions, invalid evidence kinds, unverifiable locators, unsupported financial or technical claims, invented commitments, or attribution-lock violations.

Any unsupported material claim or attribution-lock violation is an automatic `FAIL`, not merely a score of `0–7`.

---

## Dimension 5: Graph Topology, Referential Integrity, and Traversal Utility — 0–20 Points

Measures whether the graph delta is connected, directional, queryable, referentially complete, idempotent, and safe for gateway evaluation.

Requirements:

- Every relationship has valid endpoint IDs.
- Every endpoint exists in the base graph or current upsert set.
- Every writeable node has evidence.
- Every non-anchor node has a directed path to a context anchor.
- Context anchors include `project:`, `system:`, `org:`, or `workstream:`.
- Unsupported edges are prohibited.
- Self-loops are prohibited unless explicitly allowed by the registry.
- Duplicate normalized triples are prohibited.
- Signal IDs must be deterministic and collision-safe.
- Mutation and payload budgets must be respected.
- Graph state must be safe for overwrite rather than append-only duplication.

### 17–20 Points — Exemplary

The graph is fully connected, directionally valid, evidence-backed, idempotent, and highly useful for Cypher, GQL, and semantic retrieval. Archetype-specific traversals complete without dangling endpoints, orphan nodes, or contradictory active states.

### 14–16 Points — Good

The graph is connected and queryable with valid predicates. Minor topology redundancy or star-topology skew exists without orphan nodes or unsupported edges.

### 8–13 Points — Marginal

The graph contains limited redundancy, weak query paths, or one or two recoverable structural defects that do not create unsupported graph meaning.

### 0–7 Points — Unacceptable

The graph contains dangling endpoints, multiple orphan nodes, unsupported edges, illegal predicates, disconnected components, invalid signal IDs, mutation-budget violations, or failed query smoke tests.

Any unsupported Output 3 entity, edge, dangling endpoint, orphan node, or failed query smoke test is an automatic `FAIL`.

---

# 4. Deterministic QA Gates and Hard-Failure Rules

The pipeline must be fail-closed and use explicit failure collection rather than Python `assert`.

Recommended signatures:

```python
def audit_output_01(
    f1_path: Path,
    source_path: Path,
    source_manifest: dict[str, Any],
) -> dict[str, Any]:
    ...

def audit_output_02(
    f2_path: Path,
    f1_path: Path,
    claim_ledger: dict[str, Any],
    attribution_manifest: dict[str, Any],
) -> dict[str, Any]:
    ...

def audit_output_03(
    f3_path: Path,
    source_manifest: dict[str, Any],
    base_graph: dict[str, Any],
    as_of: date,
    archetype: str,
) -> dict[str, Any]:
    ...
```

Use:

```python
def require(
    condition: bool,
    failures: list[dict[str, Any]],
    code: str,
    detail: str,
):
    if not condition:
        failures.append({
            "code": code,
            "detail": detail,
        })
```

Any unexpected exception must return a structured `FAIL`.

---

## 4.1 Output 1 Gates

### Gate 1.1 — Immutable Source Binding

Hash raw source bytes, not normalized text.

```python
source_bytes = source_path.read_bytes()
actual_sha256 = hashlib.sha256(source_bytes).hexdigest()

expected_sha256 = source_manifest.get("raw_sha256")

require(
    type(expected_sha256) is str
    and re.fullmatch(r"[0-9a-f]{64}", expected_sha256, re.I) is not None,
    failures,
    "SOURCE_MANIFEST_INVALID",
    "Missing or malformed expected raw-source SHA-256",
)

require(
    actual_sha256.lower() == expected_sha256.lower(),
    failures,
    "SOURCE_HASH_MISMATCH",
    f"Expected {expected_sha256}, computed {actual_sha256}",
)

require(
    len(source_bytes) == source_manifest.get("raw_byte_length"),
    failures,
    "SOURCE_LENGTH_MISMATCH",
    "Raw source byte length differs from manifest",
)
```

The manifest must preserve source encoding, newline policy, byte length, and hash.

### Gate 1.2 — Structured Attribution Metadata

Attribution must come from source metadata, not generated-text pattern matching.

Required metadata shape:

```json
{
  "device_id": "JB-E1419-VC",
  "attribution_mode": "SHARED_ROOM_MIC",
  "individual_attribution_allowed": false,
  "individually_verified": []
}
```

Permitted attribution modes:

```text
DIRECT
SHARED_ROOM_MIC
DOCUMENT
UNRESOLVED
```

For every source segment:

```python
require(
    segment["attribution_mode"] in {
        "DIRECT",
        "SHARED_ROOM_MIC",
        "DOCUMENT",
        "UNRESOLVED",
    },
    failures,
    "INVALID_ATTRIBUTION_MODE",
    "Unsupported attribution mode",
)

if segment["attribution_mode"] == "SHARED_ROOM_MIC":
    require(
        segment.get("speaker_id") in {None, "collective:room-mic"},
        failures,
        "ROOM_MIC_INDIVIDUAL_ATTRIBUTION",
        "Shared room-mic segment has an individual speaker",
    )
```

### Gate 1.3 — Canonical Date Parsing

```python
DATE_RE = re.compile(r"[0-9]{2}/[0-9]{2}/[0-9]{4}")

def parse_mmddyyyy(value: object) -> date:
    if type(value) is not str or DATE_RE.fullmatch(value) is None:
        raise ValueError(f"Invalid MM/DD/YYYY value: {value!r}")

    parsed = datetime.strptime(value, "%m/%d/%Y").date()

    if parsed.strftime("%m/%d/%Y") != value:
        raise ValueError(f"Noncanonical or invalid date: {value}")

    return parsed
```

Reject ISO dates and all other date formats wherever the contract requires `MM/DD/YYYY`.

### Gate 1.4 — Source-Derived Vocabulary

A term is mandatory only when:

1. it appears in the immutable source; and
2. it is relevant to the selected archetype.

Vocabulary presence is never sufficient for technical fidelity. Every material term must have a role, relationship, constraint, consequence, or evidence reference.

---

## 4.2 Output 2 Gates

### Gate 2.1 — Required Section Parsing

Parse unique, ordered level-two headings. Do not merely search for heading strings.

```python
required_sections = [
    "Meeting Objective",
    "Key Discussion Points & Findings",
    "Business Meaning and Value Case",
    "Executive Decision",
    "Bounded Execution Wedge",
    "Next Steps & Action Items",
    "Open Decisions / Blockers",
]
```

Each section must contain substantive content or an approved explicit sentinel.

An empty section or heading-only section fails:

```text
EMPTY_REQUIRED_SECTION
```

### Gate 2.2 — Typed Action Register

The action section must contain:

```text
# | Action Item | Accountable Owner | Responsible Party | Target Timing | Evidence IDs | Status
```

Reject:

- rows outside the action section;
- malformed columns;
- duplicate or nonsequential action numbers;
- empty evidence IDs;
- unsupported owners;
- unsupported dates;
- missing modality or status.

Zero action rows are permitted only when the section explicitly states:

```text
No new action commitments were ratified.
```

### Gate 2.3 — Positive Action Semantics

Use a positive execution lexicon rather than a blacklist.

Example approved verbs:

```text
assign
approve
build
confirm
define
deliver
document
escalate
map
measure
obtain
publish
resolve
test
validate
draft
compare
identify
```

Every action must begin with an approved active verb, contain a concrete object, and identify a deliverable or outcome.

Modal or passive constructions must fail when they obscure commitment:

```text
will
shall
should
could
would
may
might
can
is
are
was
were
be
been
being
to
```

`Review` may be used only when the output is explicit:

```text
Review the P6-to-Unifier mapping and publish the gap list.
```

### Gate 2.4 — Canonical Owner Validation

Owners must be drawn from the owner registry created from Artifact 01 and the attribution manifest.

Reject arbitrary unsupported owners such as:

```text
Leadership
The team
Business
Alice and stakeholders
```

Organizational or role owners are valid only when represented by canonical `org:` or `role:` identities and supported by evidence.

If the source does not assign an owner:

- do not fabricate one;
- mark the owner unresolved; and
- create an owner-assignment action if appropriate.

### Gate 2.5 — Timing Validation

Committed actions require a canonical date or an explicit unresolved-date status.

Permitted unresolved timing values:

```text
DATE NOT ESTABLISHED
PENDING FORMAL AGREEMENT
MILESTONE NOT RATIFIED
```

The following are invalid for committed actions:

```text
Soon
Ongoing
Next steps
In the coming weeks
As needed
```

### Gate 2.6 — Claim-Level Evidence

Every material claim must reference evidence IDs that resolve to exact source evidence.

This applies to:

- actions;
- decisions;
- owners;
- dates;
- metrics;
- financial claims;
- technical constraints;
- system relationships;
- recommendations;
- value cases;
- consequences of delay.

Empty or generic locators fail.

### Gate 2.7 — Executive Answer Checks

The following fields must exist, be substantive, and be evidence-linked:

```text
Business problem
Value at stake
Confidence
Consequence of delay
Decision required
Decision status
Target user
In scope
Out of scope
Acceptance test
Success metric
```

The source may establish that a value, owner, date, or decision is unknown. In that case, the field must state the limitation and identify the required validation or assignment action.

### Gate 2.8 — Archetype Completeness

The evaluator must apply the mandatory checks for the selected archetype. No universal action-count floor applies.

### Gate 2.9 — Cross-Artifact Consistency

Compare normalized claim IDs, entity IDs, modalities, lifecycle states, owners, dates, and system identities across Outputs 2 and 3.

Hard-fail examples:

- Output 2 says an integration is blocked while Output 3 emits it as an active fact.
- Output 2 labels a proposal as tentative while Output 3 emits a `RATIFIED` decision.
- Output 2 assigns an individual owner prohibited by attribution metadata.
- Output 2 and Output 3 use different dates, owners, or system identities without an explicit correction or supersession state.
- Output 2 claims a decision was ratified while Output 3 lacks ratification evidence.

---

## 4.3 Output 3 Gates

### Gate 3.1 — Strict JSON Parsing

Reject duplicate keys, non-finite values, scalar roots, and unknown fields.

```python
def reject_duplicate_keys(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value

    return result

def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")

graph_data = json.loads(
    graph_raw,
    object_pairs_hook=reject_duplicate_keys,
    parse_constant=reject_constant,
)

if type(graph_data) is not dict:
    raise ValueError("Graph root must be a JSON object")
```

Pydantic must use:

```python
model_config = ConfigDict(
    strict=True,
    extra="forbid",
    validate_assignment=True,
)
```

Any parse or schema-validation exception returns `FAIL`.

### Gate 3.2 — Exact Ontology Enforcement

Validate every entity ID, relationship endpoint, owner ID, decision ID, risk ID, dependency ID, action ID, and supersession reference.

Reject:

- illegal prefixes;
- invalid slugs;
- free-text identifiers;
- unresolved aliases;
- duplicate canonical entities;
- entity IDs representing transitory meeting artifacts.

### Gate 3.3 — Closed Evidence Kinds

Validate:

```python
ALLOWED_KINDS = {
    "summary",
    "decision",
    "risk",
    "dependency",
    "technical",
    "strategic",
}
```

Reject all other values.

### Gate 3.4 — Exact Evidence Provenance

Do not validate provenance using substring search.

Require:

```json
{
  "source_id": "transcript-001",
  "artifact_sha256": "…",
  "start_offset": 91822,
  "end_offset": 91944,
  "exact_text": "…"
}
```

Then validate exact byte equality:

```python
source_bytes = source_path.read_bytes()
start = locator["start_offset"]
end = locator["end_offset"]
quote = locator["exact_text"].encode("utf-8")

require(
    0 <= start < end <= len(source_bytes),
    failures,
    "LOCATOR_RANGE_INVALID",
    statement_id,
)

require(
    source_bytes[start:end] == quote,
    failures,
    "LOCATOR_QUOTE_MISMATCH",
    statement_id,
)
```

An empty locator, empty quote, or generic text fragment fails.

### Gate 3.5 — Attribution and Modality Validation

For shared room-mic evidence:

```python
if evidence["attribution_mode"] == "SHARED_ROOM_MIC":
    require(
        evidence.get("speaker_id") in {None, "collective:room-mic"},
        failures,
        "ROOM_MIC_INDIVIDUAL_ATTRIBUTION",
        evidence_id,
    )
```

Without explicit evidence, downgrade rather than promote:

- `RATIFIED` → `PROPOSED` or `DISCUSSION`;
- `COMMITTED` → `PROPOSED` or `DISCUSSION`;
- individual owner → unresolved owner;
- exact due date → unresolved date;
- technical integration → open question or review-required inference.

### Gate 3.6 — Dates, Decay, and Lifecycle

Validate:

```python
observed_at <= stale_after
observed_at <= due_date
```

where applicable.

Required fields:

| Mutation | Required Fields |
|---|---|
| `decision_update` | `observed_at`, `stale_after`, `lifecycle_state` |
| `proposal_update` | `observed_at`, `stale_after`, `lifecycle_state` |
| `risk_update` | `observed_at`, `stale_after`, `lifecycle_state` |
| `action_update` | `due_date`, `lifecycle_state` |
| `supersession_update` | prior state, new state, observed date, evidence |

Allowed lifecycle states are:

```text
PROPOSED
RATIFIED
SUPERSEDED
BLOCKED
ACTIVE
```

Reject:

- invalid states;
- invalid transitions;
- expired active assertions;
- contradictory active facts without supersession;
- missing `SUPERSEDES` edges when an assertion is explicitly retired.

### Gate 3.7 — Predicate Domains and Directions

For each relationship:

1. confirm the predicate is in the canonical registry;
2. parse source and target prefixes;
3. validate source domain;
4. validate target range;
5. validate evidence;
6. validate lifecycle state.

Unsupported source/target combinations fail.

### Gate 3.8 — Referential Integrity and Topology

Construct the candidate graph from:

- the base graph;
- entity upserts;
- decision, risk, dependency, and action records;
- relationship signals;
- supersession signals.

Require:

```python
known_ids = existing_ids | upsert_ids

for rel in relationships:
    require(rel["from_id"] in known_ids, ...)
    require(rel["to_id"] in known_ids, ...)
```

Reject:

- dangling endpoints;
- unsupported self-loops;
- orphan nodes;
- disconnected unsupported components;
- edges without evidence;
- unsupported graph bridges.

Every non-anchor entity must have a directed path to at least one context anchor:

```text
project:
system:
org:
workstream:
```

Archetype-specific topology must be present:

- Strategic discovery: evidence-backed business-to-technical or cross-domain bridge.
- Architecture deep dive: system, dependency, constraint, and acceptance-test chain.
- Tactical alignment: action-to-owner and action-to-workstream/project closure.
- Governance review: state transition and supersession structure.
- Lessons learned: root cause, control, risk, and verification-metric chain.

Connectivity must never be manufactured solely to make the graph pass. Inferred bridges must be marked:

```text
epistemic_status: MODEL_INFERENCE
write_status: REVIEW_REQUIRED
inference_rule_id: ...
supporting_evidence_ids: [...]
```

### Gate 3.9 — Deterministic Signal IDs and Idempotency

Use one canonical signal-ID encoder:

```python
def canonical_signal_id(
    from_id: str,
    to_id: str,
    predicate: str,
) -> str:
    return (
        f"rel-{encode_component(from_id)}"
        f"--{encode_component(to_id)}"
        f"--{predicate}"
    )
```

Require:

```python
expected = canonical_signal_id(
    rel["from_id"],
    rel["to_id"],
    rel["relationship"],
)

require(
    rel["signal_id"] == expected,
    failures,
    "SIGNAL_ID_MISMATCH",
    rel["signal_id"],
)
```

Also check:

- duplicate signal IDs;
- duplicate normalized triples;
- duplicate IDs across mutation categories;
- collision with existing graph signals;
- expected base-graph version;
- stale write version;
- overwrite-not-append behavior.

A delta alone cannot establish overwrite safety. The gate must receive the current graph snapshot or write-set version.

### Gate 3.10 — Mutation and Payload Budgets

Retain the C-17 mutation limit:

```text
Maximum total mutations: 25
```

Also enforce:

- maximum serialized payload bytes;
- maximum property count;
- maximum evidence references per mutation;
- maximum nested-array length;
- maximum text length;
- maximum total nodes;
- maximum total edges.

A small number of oversized mutations must not evade the budget.

### Gate 3.11 — Query Smoke Tests

Materialize the candidate graph in an isolated test graph and execute representative traversals:

- action → owner;
- workstream → blocker;
- system → integration/dependency chain;
- assertion → evidence;
- superseded fact → replacement fact;
- context anchor → connected entities.

Fail if:

- a query returns unresolved endpoints;
- evidence cannot be traversed;
- owner or blocker paths are absent;
- traversal exceeds bounded depth;
- disconnected components exist;
- canonical aliases cannot be resolved;
- lifecycle state contradicts the query result.

---

# 5. Hard Score Caps, Automatic Failures, and Verdict Precedence

## 5.1 Hard Score Caps

The following caps apply before final verdict calculation:

| Failure | Maximum Result |
|---|---|
| Missing value case and no value-validation plan | `PASS_ADVISORY` maximum |
| Missing bounded execution scope | Composite maximum `74` |
| Missing executive decision request | Composite maximum `74` |
| Missing success measure | Composite maximum `89` |
| Missing required Output 2 section without valid sentinel | `FAIL` |
| Missing archetype-specific completeness requirement | `PASS_ADVISORY` maximum |
| Action register malformed or unsupported owner/date | `FAIL` where material; otherwise composite maximum `74` |
| Unsupported material claim | Automatic `FAIL` |
| Invented owner, date, amount, or commitment | Automatic `FAIL` |
| Attribution-lock violation | Automatic `FAIL` |
| Unsupported Output 3 entity or edge | Automatic `FAIL` |
| Source-chain failure | Automatic `FAIL` |
| Invalid locator or unverifiable quote | Automatic `FAIL` |
| Dangling endpoint | Automatic `FAIL` |
| Orphan node | Automatic `FAIL` |
| Lifecycle contradiction | Automatic `FAIL` |
| Failed query smoke test | Automatic `FAIL` |
| Mutation version conflict | Automatic `FAIL` |

A composite score cannot compensate for a critical grounding or integrity defect.

## 5.2 Output 2 Minimum Dimension Threshold

Every Output 2 dimension must score at least:

```text
18/25
```

A dimension below `18` produces `FAIL`, regardless of composite score.

## 5.3 Output 3 Minimum Dimension Threshold

Every Output 3 dimension must score at least:

```text
14/20
```

A dimension below `14` produces `FAIL`, regardless of composite score.

## 5.4 Verdict Precedence

Verdict values are structured and limited to:

```text
PASS
PASS_ADVISORY
FAIL
```

Precedence is:

1. Any automatic hard-gate failure → `FAIL`.
2. Any deterministic gate failure → `FAIL`.
3. Any score below the minimum dimension threshold → `FAIL`.
4. Any hard score cap → apply the cap.
5. **Output 2 (Executive Strategic Brief / Recap) & Output 3 (Canonical Signal Projection) Mandatory Passing Threshold (>= 90/100)**:
   - Composite score `90–100` with all dimension thresholds satisfied → `PASS`.
   - Any composite score `< 90` → `FAIL`. (Strict 90/100 passing threshold; `PASS_ADVISORY` is not permitted below 90 for Output 2 or Output 3; authoritative write is blocked).
6. **Output 1 (Forensic Transcript) Passing Threshold (>= 80/100)**:
   - Composite score `90–100` with all thresholds satisfied → `PASS`.
   - Composite score `80–89` with all thresholds satisfied → `PASS_ADVISORY`.
   - Composite score `< 80` → `FAIL`.

`PASS_ADVISORY` must never authorize an authoritative production write when the gateway requires production certification.

---

# 6. Composite Score Bands and Release Status

The composite score is calculated separately for Output 2 (Executive Recap) and Output 3 (Canonical Signal Projection).

### Output 2 (Executive Strategic Brief / Recap) & Output 3 (Canonical Signal Projection) — Mandatory 90/100 Passing Threshold

| Composite Score | Release Status | Verdict | Operational Action |
|---:|---|---|---|
| 90–100 | Production Certified | `PASS` | Eligible for production release if all deterministic and cross-artifact gates pass |
| 0–89 | Forensic Failure | `FAIL` | Release blocked; below mandatory 90/100 threshold; authoritative write blocked |

### Output 1 (Forensic Transcript) — 80/100 Threshold

| Composite Score | Release Status | Verdict | Operational Action |
|---:|---|---|---|
| 90–100 | Production Certified | `PASS` | Eligible for production release if all deterministic and cross-artifact gates pass |
| 80–89 | Advisory Pass | `PASS_ADVISORY` | Advisory release only; authoritative write remains blocked unless gateway policy explicitly permits it |
| 0–79 | Below Threshold | `FAIL` | Release blocked; regenerate or route for HITL disambiguation |

A score of `90` or greater does not produce `PASS` if:

- any dimension is below its minimum threshold;
- any hard score cap applies;
- any deterministic gate fails;
- any cross-artifact consistency check fails; or
- any automatic failure condition is present.

---

# 7. Final Release Rule

The final release decision is deterministic:

```python
output_2_llm_score_pass = (
    output_2_composite_score >= 90
    and output_2_verdict == "PASS"
)

output_2_pass = (
    output_2_deterministic_gates_pass
    and output_2_llm_score_pass
)

output_3_llm_score_pass = (
    output_3_composite_score >= 90
    and output_3_verdict in ("PASS", "PASS_ADVISORY")
)

output_3_pass = (
    output_3_deterministic_gates_pass
    and output_3_llm_score_pass
)

final_pass = (
    output_1_pass
    and output_2_pass
    and output_3_pass
    and cross_artifact_consistency_pass
)
```

The following always force `FAIL`:

- source-chain break;
- empty or unverifiable locator;
- unsupported material claim;
- invented owner, date, amount, or commitment;
- attribution-lock violation;
- invalid entity or predicate;
- dangling endpoint;
- orphan node;
- lifecycle contradiction;
- failed query smoke test;
- mutation version conflict;
- duplicate signal ID;
- unsupported graph edge;
- invalid evidence kind;
- invalid date format;
- unsupported active state.

The LLM judge assesses:

- executive quality;
- synthesis depth;
- decision usefulness;
- business meaning;
- technical explanation;
- signal leverage;
- archetype fit.

The LLM judge must not independently certify:

- source integrity;
- exact provenance;
- ontology validity;
- attribution safety;
- lifecycle validity;
- referential integrity;
- graph topology;
- idempotency;
- mutation budgets;
- write-gateway safety.

---

# 8. Evaluator Output Schema

The evaluator must output strictly valid JSON using only the structured verdict values `PASS`, `PASS_ADVISORY`, and `FAIL`.

```json
{
  "rubric_version": "2.0",
  "evaluator_model": "gpt-5.6-luna",
  "reasoning_effort": "high",
  "deterministic_gate_status": {
    "output_1": "PASS",
    "output_2": "PASS",
    "output_3": "PASS",
    "cross_artifact_consistency": "PASS",
    "hard_gate_failures": [],
    "score_caps_applied": []
  },
  "output_2_evaluation": {
    "meeting_archetype": {
      "selected_archetype": "STRATEGIC_DISCOVERY",
      "confidence": 0.94,
      "supporting_evidence_ids": [
        "ev-004",
        "ev-017"
      ],
      "mandatory_checks": [
        "business_value_case",
        "executive_decision_request",
        "bounded_execution_wedge",
        "value_validation_action",
        "next_decision_gate"
      ]
    },
    "dimension_1_executive_altitude_and_value_case": 0,
    "dimension_2_technical_substance_and_architecture": 0,
    "dimension_3_strategic_synthesis_and_decision_quality": 0,
    "dimension_4_bounded_execution_and_action_completeness": 0,
    "composite_score": 0,
    "minimum_dimension_threshold": 18,
    "hard_score_caps": [],
    "automatic_failures": [],
    "verdict": "PASS",
    "forensic_rationale": "Dimension-by-dimension forensic assessment of business meaning, technical grounding, synthesis, decision quality, scope, action completeness, and modality.",
    "actionable_remediation_diff": "Specific text and structural changes required if the score is below 90 or a hard cap applies."
  },
  "output_3_evaluation": {
    "meeting_archetype": {
      "selected_archetype": "ARCHITECTURE_DEEP_DIVE",
      "confidence": 0.91,
      "supporting_evidence_ids": [
        "ev-011",
        "ev-018"
      ],
      "mandatory_checks": [
        "system_dependency_chain",
        "integration_mechanism_status",
        "technical_acceptance_test"
      ]
    },
    "dimension_1_signal_amplification_and_nba": 0,
    "dimension_2_entity_resolution_and_ontology": 0,
    "dimension_3_temporal_dynamics_and_lifecycle": 0,
    "dimension_4_evidence_grounding_and_attribution": 0,
    "dimension_5_graph_topology_and_traversal": 0,
    "composite_score": 0,
    "minimum_dimension_threshold": 14,
    "hard_score_caps": [],
    "automatic_failures": [],
    "verdict": "PASS",
    "forensic_rationale": "Archetype-calibrated assessment of graph signal, entity hygiene, temporal state, exact provenance, attribution, referential integrity, and traversal utility.",
    "actionable_remediation_diff": "Specific JSON, provenance, ontology, lifecycle, topology, or mutation adjustments required if the score is below 90 or a hard gate applies."
  },
  "overall_rubric_summary": "Synthesized evaluation summary covering executive decision usefulness, business value clarity, technical grounding, graph signal leverage, deterministic integrity, and release readiness.",
  "overall_verdict": "PASS",
  "write_authorization": "PRODUCTION_CERTIFIED"
}
```

Permitted `write_authorization` values are:

```text
PRODUCTION_CERTIFIED
ADVISORY_ONLY
BLOCKED
```

The evaluator must set:

- `PRODUCTION_CERTIFIED` only for overall `PASS` with all deterministic gates passed;
- `ADVISORY_ONLY` for overall `PASS_ADVISORY`;
- `BLOCKED` for overall `FAIL`.

No prose such as `"PASS (Advisory)"` is permitted in structured verdict fields.
