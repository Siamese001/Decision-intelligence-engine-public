
# NEE Project Instructions — Governed Knowledge, Version Update, and Query Protocol

> **SVP+ Agentic Engineering Reference Architecture (Amit Ayer)**
> Constitutional operating protocol demonstrating **engineering best practices, architectural foresight, and novel deterministic governance** across knowledge graph ingestion, dialectical reasoning, and cryptographic human-in-the-loop verification.

**Policy version:** 55.0  
**Effective date:** 2026-07-14  
**Canonical control file:** `cards/NEE_99_Project_Instructions.md`  
**Generated mirror:** `PROJECT_INSTRUCTIONS.md`

This policy governs how the NEE knowledge package is queried, updated, validated, evolved, and used. `NEE_99` is the canonical policy representation. The root file is a generated body-identical mirror. A mismatch is a release-blocking policy failure.

---

## 1. Scope and policy architecture

### 1.1 Scope

This policy governs:

- NEE-specific evidence, knowledge, synthesis, and private coaching use.
- Source intake, reconciliation, graph updates, card updates, versioning, and packaging.
- Read-only analysis, proposed-delta review, approval-bound implementation, and post-implementation conformance.
- Ad hoc package queries and machine-readable answer packets.
- Identity resolution, temporal/currentness handling, confidence, conflict, audience, and HITL.
- Graph ontology and canonical card-spine evolution.
- Approval, protocol changes, exceptions, QA obligations, and definitions of done.

Implementation-specific commands, current file inventories, database counts, and test scripts belong in the applicable technical documentation or QA specification. This policy defines the required control outcomes.

### 1.2 Policy layers

The policy is organized into three layers:

1. **Core policy:** evergreen rules in Sections 1–19.
2. **Current domain guardrails:** active initiative-specific controls in Appendix A.
3. **Protocol decision register:** effective-dated policy decisions in Appendix B.

Historical decisions remain preserved but do not override an active core rule unless the register explicitly marks them active and identifies the affected section.

### 1.3 Instruction precedence

When instructions conflict, use this order:

1. The user’s current explicit decision for the task, provided it does not require fabrication or unsafe use.
2. This canonical active policy in `NEE_99`.
3. Active, approved protocol decisions in Appendix B.
4. `NEE_00` authority, source-map, and synthesis controls.
5. Active canonical cards and approved domain guardrails.
6. Historical cards, release notes, prior packages, and intake notes.

A newer timestamp, upload, or edit does not by itself establish authority. Generic instructions such as “run,” “process,” “ingest,” “reconcile,” or “apply the project instructions” do not authorize semantic graph mutation, canonical card edits, migration execution, or versioned packaging. They authorize read-only analysis and proposal unless the user explicitly approves the specific delta or an exact, narrowly scoped control-plane change.

If two controlling instructions remain irreconcilable, apply the more conservative evidence and audience posture, record the ambiguity, and do not make an irreversible or externally visible change until it is resolved.

---

## 2. Core operating principles

1. **Evidence before assertion.** NEE-specific facts must be supported by the package or formally governed external evidence.
2. **Graph first, synthesis second.** The graph/SQLite layer is the primary structured knowledge layer. Cards are executive-readable synthesis.
3. **Approval before mutation.** Analyze and propose from a read-only baseline. Do not mutate the canonical graph, edit canonical cards, execute semantic migrations, regenerate derivatives that embed the proposed change, or create a successor versioned ZIP until the applicable proposed deltas are explicitly approved. Ingestion workflows require active intent: when exact approval is missing, prompt the operator interactively and resume execution through publication and activation upon approval; skipped publication or unattempted writes never count as completed ingestion.
4. **Facts, synthesis, and coaching stay distinct.** Canonical evidence, derived account synthesis, and private career coaching are separate logical layers.
5. **Stable by default, evolvable by evidence.** Reuse existing structures where they remain fit; change them when controlled evidence demonstrates a durable need.
6. **Append-only understanding by default.** Preserve prior assertions, conflicts, supersession, merge lineage, and decision history.
7. **Currentness is explicit.** A source can prove what it said at a date without proving that the proposition remains currently true.
8. **No identity guessing.** Similarity, proximity, role context, or model memory cannot establish a person, title, reporting line, ownership, or relationship.
9. **Proportional controls, fixed authorization boundary.** Analysis and QA effort may scale with risk, but the pre-mutation approval boundary does not disappear for a small semantic graph or card change.
10. **Explainable outputs.** Material answers and recommendations must be traceable to supporting evidence and the relevant subgraph.
11. **Fail closed on drift.** If implementation differs materially from the approved proposal, stop, issue a revised proposal, and obtain reapproval before applying the difference.
12. **Three-table decision surface.** Every G1/C1/R1 gate presentation must expose exactly the graph delta, graph metrics, and card additions tables inline; all supporting detail is filed separately and the executable Gate O1 must pass.
13. **No silent or retroactive policy change.** A structural or authorization decision cannot be justified by rewriting policy after implementation, and a later policy change cannot retroactively authorize an earlier unapproved mutation.
14. **Surface post-synthesis ambiguity.** After synthesizing new information, present every detected unresolved inconsistency, overlap, and open question as an explicit HITL clarification prompt; never silently choose, merge, rank, or close it.

---

## 3. Normative glossary

| Term | Definition and control consequence |
|---|---|
| **Canonical evidence** | Source-backed account knowledge represented with provenance, scope, time, authority, and use posture. It may be disputed or historical; canonical does not mean universally true. |
| **Derived synthesis** | An interpretation, pattern, summary, connection, or recommendation produced from canonical evidence. It must remain distinguishable from source fact. |
| **Private career coaching** | Recommendations for the user’s learning, contribution, relationship-building, meeting preparation, and career narrative. It is private by default and cannot upgrade account facts. |
| **Material** | Capable of changing an executive decision, client statement, account strategy, commercial posture, delivery action, reputational exposure, or significant resource allocation. |
| **High-signal** | Reusable or decision-relevant information with enough specificity to improve account understanding. High-signal does not automatically mean material. |
| **Current** | Supported as valid for the relevant proposition, scope, and decision date—not merely recently inserted or recently uploaded. |
| **Source-attributed** | Accurately reports what a named source stated for its date and scope without asserting current independent truth. |
| **Official** | Supported by the authority formally empowered for that claim type. |
| **Decision-grade** | Sufficiently authoritative, current, scoped, unconflicted, and audience-approved for the stated decision. |
| **Directional** | Useful for hypothesis, framing, or design discussion, but not sufficient for a final factual or commitment claim. |
| **Candidate** | Proposed mapping, identity, relationship, conclusion, or structure awaiting evidence or approval. Candidate status must remain visible. |
| **Validated** | Reviewed and accepted for the specific proposition and allowed use. Validation does not automatically extend to adjacent facts. |
| **Confirmed** | An explicit validation state, not a probability score. The confirmation scope must be recorded. |
| **Conflict** | Incompatible assertions about the same proposition, scope, and effective period. Differently scoped or dated facts may coexist without conflict. |
| **Executive-facing** | Material intended for PartnerAdvisory account leadership, PartnerAdvisory executives, joint leadership, or client executives. Private executive preparation is a separate audience. |
| **Client-facing** | Material intended to be communicated to NEE personnel outside the internal PartnerAdvisory working team. |
| **HITL** | Human review or approval required at a defined tier before a specified use or structural change. |
| **Synthesis clarification issue** | A detected unresolved inconsistency, overlap, or open question that becomes visible when new information is reconciled with the current package. An apparent inconsistency may resolve to conflict, scope differentiation, supersession, or error; an overlap may be duplicate, nested, complementary, or boundary-ambiguous. |
| **HITL clarification prompt** | A user-facing, evidence-linked question issued after synthesis for each synthesis clarification issue. It states the decision needed, affected scope and use, provisional posture, response options, and closure evidence. Silence does not resolve it. |
| **Canonical card home** | The approved executive synthesis location for a durable topic. It is not the exclusive storage location for all supporting evidence. |
| **Domain anchor** | A durable graph concept that organizes a coherent subdomain, rather than a routine entity, source, event, or assertion. |
| **Semantic graph mutation** | Any add, update, supersession, retirement, deletion, merge, route change, or state change to a canonical source, evidence item, node, edge, claim, alias, node-card mapping, validation, action, register, or other meaning-bearing graph record. |
| **Canonical card edit** | Any change to a canonical card body or meaning-bearing metadata, including an edit that leaves the card ID, card count, and active spine unchanged. |
| **Proposed graph delta** | A complete read-only description of the graph operations intended for implementation, including stable proposed identifiers, before/after values, evidence controls, routes, counts, and a digest. |
| **Proposed card delta** | A complete read-only section- or line-level diff for every canonical card intended to change, with rationale, supporting graph items, impact classification, and a digest. |
| **Approval envelope** | The exact baseline hash, input hashes, approved graph and card proposal digests, scope, limitations, approver, and approval time that constrain implementation. |
| **Implementation drift** | Any material difference between the approved proposal and the attempted or completed implementation, including an unlisted object, operation, route, card, or meaning-bearing edit. |
| **Review bundle** | An unversioned, noncanonical collection of proposal artifacts prepared outside the baseline package for approval. A review bundle is not a successor NEE release. |

---

## 4. Knowledge-layer boundaries

### 4.1 Canonical evidence layer

May contain sources, evidence items, entities, aliases, assertions, relationships, time, authority, conflict, validation, audience, and lineage. It must not contain unsupported coaching judgments or implied influence rankings.

### 4.2 Derived synthesis layer

May contain executive summaries, reusable patterns, cross-workstream connections, dependency analysis, answer packets, and recommendations. Every material output must identify its supporting evidence or state that it is a candidate hypothesis.

### 4.3 Private career-coaching layer

May recommend what the user should learn, contribute, ask, synthesize, or connect. It must:

- Be classified `private-coaching` unless the user explicitly authorizes a broader audience.
- Avoid psychographic profiling, protected characteristics, covert manipulation, or unsupported influence judgments.
- Rank actions and opportunities—not people—using account benefit, evidence quality, urgency, reversibility, and relationship risk.
- Never appear in normal client-facing exports or upgrade canonical account knowledge.

---

## 5. Change classes and required propagation

Classify every update before changing files. Change class determines the additional controls and propagation; it does not by itself grant mutation authority.

| Change class | Typical examples | Minimum required propagation and authorization |
|---|---|---|
| **C1 Source ingest** | New meeting note, deck, status artifact, or source pointer | Read-only source/evidence assessment; proposed graph delta; applicable assertions and routes; Gate G1 approval before canonical writes; Gate C1 if any canonical card may change; ingest record and proportional QA after approval |
| **C2 Factual correction** | Corrected name, date, scope, relationship, or status | Preserve prior assertion in the proposal; proposed correction/supersession; affected-output and validation impact; Gate G1 and, when applicable, Gate C1 approval before writes |
| **C3 Identity resolution** | Alias merge, candidate rejection, canonical-name closure | Candidate analysis, collision evidence, lineage, affected-reference proposal; Gate G1 approval for any canonical identity mutation; Gate C1 if card meaning changes; additional explicit approval for material or ambiguous merges |
| **C4 Synthesis revision** | New pattern, account thesis, risk, recommendation, or cross-workstream connection | Supporting subgraph and proposed card diff; complete post-synthesis HITL clarification register and prompt surface; Gate G1 for any graph change and Gate C1 for every canonical card edit; audience/HITL review |
| **C5 Structural change** | New ontology type, relationship vocabulary, domain anchor, card addition/split/merge/retirement | Gate G1 and/or Gate C1 plus structural decision record, alternatives, explicit approval, migration/redirect, affected artifacts, rollback, and enhanced QA |
| **C6 Protocol change** | Modification to this policy or a domain guardrail | Controlled protocol decision and explicit user approval; canonical/mirror synchronization; policy QA; account graph and non-policy card deltas remain zero unless separately proposed and approved |
| **C7 Validation-only change** | HITL decision, confidence/currentness update, due-date change | Proposed state transition and allowed-use impact; Gate G1 before canonical validation/state writes; Gate C1 if executive meaning changes |
| **C8 Control or packaging repair** | Broken reference, generated mirror drift, archive hygiene | Repair affected control only; demonstrate zero semantic graph and non-policy card delta. If meaning changes, reclassify and apply G1/C1 |
| **C9 Export-only regeneration** | Rebuild a derivative from unchanged canonical state | Verify canonical state is unchanged and the derivative does not embed an unapproved proposal; regenerate and verify lineage. No successor versioned ZIP may be created merely to bypass G1/C1 |

A user may explicitly constrain a release to one class. Do not expand the scope without material need. When a required downstream artifact is excluded by explicit scope, record the limitation and do not claim full-system synchronization.

All semantic graph mutations require Gate G1 approval. All canonical card edits require Gate C1 approval, including edits to existing cards where the active card spine and card count remain unchanged. Zero-change declarations are required when a gate is inapplicable.

---

## 6. Version-update workflow

Use the following sequence. The approval boundary is mandatory for every semantic graph mutation and canonical card edit.

### 6.1 Phase A — read-only analysis and proposal

1. Identify the latest **approved current** baseline and the user-requested scope.
2. Verify and record the baseline ZIP hash, baseline graph hash, policy hash, and all input artifact hashes.
3. Keep the baseline immutable. Inspect it through a read-only mount or an isolated copy whose canonical files are not written during analysis.
4. Classify the proposed work and identify all applicable approval, HITL, identity, authority, temporal, audience, and structural controls.
5. Inspect the evidence in a scratch workspace and reconcile identities, duplicates, scope, temporal overlap, corroboration, conflicts, currentness, and source authority.
6. Produce the complete **Gate G1 proposed graph delta**, including explicit zero-change declarations for applicable graph components.
7. Produce the complete **Gate C1 proposed card delta** for every canonical card expected to change, including an explicit declaration that no unlisted card will change.
8. Perform synthesis-impact, derivative-impact, migration-impact, packaging-impact, and rollback assessments.
8a. Generate and validate the exact Gate O1 three-table inline decision surface from the machine-readable G1 and C1 proposals. File every other assessment, reconciliation, digest, approval instruction, and detailed operation record outside the inline gate body.
8b. After synthesis and before approval is requested, generate a complete post-synthesis HITL clarification register and user-facing prompt set for every detected unresolved inconsistency, overlap, and open question. Deduplicate only semantically equivalent issues, link every retained issue to evidence and affected outputs, and record an explicit zero-issue declaration when none are detected.
9. Produce an unversioned review bundle outside the baseline package and stop.

At this point, do **not** update the canonical graph, card files, migrations, generated exports, manifest, changelog, QA report, or versioned package. Do not create a successor NEE ZIP.

### 6.2 Phase B — explicit approval

10. Obtain explicit user approval of the applicable graph and card proposal envelope after Gate O1 passes. The user does not need to repeat hashes or copy formulaic wording. An unambiguous direct approval such as `approve it`, `go ahead`, or equivalent is valid when it clearly refers to the single immediately preceding complete Gate O1 envelope; the implementation agent must bind the exact baseline, input, graph, card, and inline-output hashes in the approval record. Approval of only a summary, general approach, or approximate scope remains insufficient. If more than one envelope is pending or the reference is ambiguous, ask which one.
10a. Approval may be requested only after Gate O1 passes. Do not require the user to recite digest values; bind them automatically in the approval record.
11. Create an approval record that binds the baseline hash, input hashes, graph proposal digest, card proposal digest, approver, approval time, scope, limitations, audience/use, and decision source.
12. If either graph or card change is zero, bind the corresponding signed zero-change declaration to the approval record.
13. If the proposal changes before implementation, issue a revised proposal with new digests and obtain reapproval.

### 6.3 Phase C — approval-bound implementation

14. Only after valid approval exists, create a mutable implementation copy of the approved baseline.
14a. Before the first canonical write, revalidate the approved G1 inline output and its approval-bound SHA-256.
15. Apply only the approved identifiers, operations, values, routes, and card edits.
16. If implementation reveals a material out-of-envelope difference, stop before applying it, preserve the partial work as noncanonical scratch, and return to Gates G1/C1.
17. Regenerate only approved affected derivatives and metadata.
18. Record the actual graph delta, actual card diff, first canonical write time, and implementation-normalization details.

### 6.4 Phase D — conformance, packaging, and release-state activation

19. Run Gate R1 QA. Prove baseline immutability, approval chronology, exact-delta conformance, zero unapproved semantic-file changes, lineage, currentness, confidence, audience/HITL, schema, integrity, and packaging hygiene.
19a. Generate and validate the exact Gate O1 R1 three-table decision surface from the conformance record. Packaging is blocked unless the G1 and R1 inline outputs both validate and their hashes are bound to approval/conformance.
20. Create the successor versioned ZIP only after Gate R1 and Gate O1 pass.
21. For an authorized successor build, passing Gate R1 and Gate O1 automatically sets the release to `approved_current` and records the release-state activation. Keep the release `candidate` only when the user explicitly requests `candidate-only` or explicitly withholds promotion before packaging.
22. Preserve the prior approved-current package and the approval/conformance records.

For each materially changed component, report prior state, proposed state, approved state, implemented state, change, rationale, evidence, time posture, confidence dimensions, audience/HITL posture, affected routes, proposal digest, and conformance result. For unchanged components, a concise zero-change declaration is sufficient.

### 6.5 Protocol-only activation releases

A C6 instruction-only activation release may update `NEE_99`, its root mirror, approval/RCA records, and release-control documentation from an explicitly approved policy amendment without an account-knowledge Gate G1 or non-policy-card Gate C1 proposal, but only when:

- the account graph delta is exactly zero;
- every non-policy canonical card is byte-identical to the approved baseline;
- no derivative is regenerated from unapproved semantic content;
- the approval predates the first write;
- the release discloses that broader graph/card propagation is deferred; and
- the new rule is applied prospectively and is not used to ratify an earlier unapproved mutation.

---

## 7. Ad hoc query and retrieval protocol

### 7.1 Retrieval order

For NEE-specific questions, use this sequence:

1. Exact canonical entity search.
2. Normalized alias, unresolved-mention, and collision search.
3. Full-text or semantic candidate retrieval.
4. Typed graph expansion across relevant relationships and card routes.
5. Supporting source and evidence retrieval.
6. Time, scope, conflict, authority, audience, and HITL checks.
7. Card synthesis review.
8. Post-synthesis scan for every detected unresolved inconsistency, overlap, and open question; generate the required HITL clarification prompts.
9. Answer-packet assembly and prompt presentation.

Model memory may generate search hypotheses or explain general industry concepts. It may not establish an NEE-specific identity, role, ownership, current status, scope, architecture, commercial term, relationship, or fact.

### 7.2 Answer postures

Use one or more controlled postures:

| Posture | Meaning |
|---|---|
| `current_validated` | Supported as current for the relevant proposition, scope, and use |
| `source_attributed` | Accurately reports what a dated source said |
| `historical` | Supported for an earlier effective period |
| `directional` | Useful framing but not decision-grade |
| `candidate` | Proposed but unresolved |
| `disputed` | Competing assertions remain unresolved |
| `superseded` | Previously supported but replaced for the same scope/time purpose |
| `not_represented` | No canonical representation found in the searched package layers |
| `unresolved_identity` | A mention exists but cannot be safely mapped to a canonical identity |
| `search_incomplete` | Required evidence was inaccessible or the mandated search could not be completed |

Do not reduce these outcomes to a generic “not found.”

### 7.3 Minimum material-answer content

A material answer should include, as applicable:

- Direct answer and answer posture.
- Supporting entities, assertions, relationships, sources, and evidence.
- Source authority and effective date/currentness.
- Evidence strength, validation state, conflict state, and allowed audience.
- HITL tier and blocked use.
- Canonical card route.
- Missing evidence or unresolved identity.
- Recommended next action.
- Post-synthesis HITL clarification prompts for every detected unresolved inconsistency, overlap, and open question, or an explicit none-detected declaration.
- Brief derivation summary.

### 7.4 Absence and repair

Before concluding `not_represented`, search the applicable canonical layers rather than a permanent list of filenames. Exports are derivatives and do not override canonical evidence.

Propose a structural repair only when the missing information is material, reusable, repeated, required by an active decision, or explicitly requested. Include only applicable components—entity, alias, assertion, relationship, source/evidence, validation item, and card route—and explain omissions. Do not fabricate empty graph objects to satisfy a template.

---

## 8. Source authority, evidence, and currentness

### 8.1 Claim-type authority matrix

Authority is evaluated by claim type rather than one universal source order.

| Claim type | Primary authority | Supporting but normally insufficient alone |
|---|---|---|
| Contractual scope, fees, terms, commitments | Executed SOW, PO, change order, or approved commercial authority | Proposal, deck, meeting note, status report |
| Reported delivery status and RAG | Dated approved status artifact for its stated scope and as-of date | Meeting notes, planning board, informal recap |
| Current delivery reality | Current accountable owner plus appropriate current operational evidence | Older status artifacts or directional decks |
| Architecture and platform state | Approved architecture artifact or accountable technical owner with direct evidence | Proposals, whiteboards, inference from tool names |
| Identity and affiliation | Direct source, confirmed directory/contact evidence, or explicit scoped user confirmation | Contextual proximity, phonetic similarity, company assumption |
| Current title, role, reporting line, decision right | Current authoritative organizational source or accountable validation | Historical deck, email signature, inferred seniority |
| Ownership and accountability | Explicit RACI, charter, decision record, or accountable-owner confirmation | Attendance, mention frequency, whiteboard placement |
| Strategic intent and feedback | Direct statement, approved strategy artifact, or attributed meeting evidence | Secondary paraphrase or pattern inference |
| Public/current fact | Current authoritative public source | Model memory or stale public summary |
| General technical background | Primary technical documentation or research | Vendor marketing or unverified secondary summary |

User confirmation validates only the exact proposition confirmed. It does not automatically validate adjacent title, currentness, reporting line, decision right, architecture, scope, or commercial claims.

### 8.2 Evidence dimensions

Assess these separately:

- **Directness:** direct statement/evidence versus interpretation.
- **Authority:** whether the source is empowered for the claim type.
- **Freshness/currentness:** whether it covers the decision period.
- **Scope:** the exact organization, workstream, role, system, or period covered.
- **Independence:** whether apparent corroboration comes from genuinely independent sources.
- **Completeness:** whether material qualifiers are present.

### 8.3 Source-attributed reporting versus current assertion

The package may state what a dated source says when the source, date, scope, authority posture, and confidence dimensions are explicit. Reporting source content does not require revalidating the source merely to describe it.

A proposition presented as current, official, independently true, or decision-grade requires current authoritative evidence appropriate to that claim type and use.

Dated status artifacts control **reported status for their stated scope and as-of date**. They do not remain current indefinitely.

### 8.4 External research

External research may:

- Verify current public facts.
- Supply general technical or industry background.
- Compare tools, platforms, standards, or methods.

External research must be attributed and dated. It cannot silently establish private NEE facts. If formally ingested, classify it as external evidence with its authority, audience, and use limitations.

---

## 9. Reconciliation, history, and conflict

1. Preserve prior assertions by default; do not overwrite history merely to simplify the current view.
2. Identify the currently preferred assertion separately from the historical record.
3. Treat assertions as conflicts only when they address the same proposition, scope, and effective period.
4. Preserve corroborating and refuting evidence.
5. Merge duplicate entity identities into the canonical entity while preserving governed aliases, rejected candidates, and merge lineage.
6. Do not delete information merely to reduce counts.
7. Use deactivation, supersession, tombstones, redirects, and merge/split lineage for lifecycle changes.
8. Legal, privacy, or security deletion may override retention through an explicitly approved exception.
9. Cleanup means normalizing terminology, deduplicating equivalent concepts, preserving evidence, and documenting merges or removals.
10. Treat an apparent inconsistency as a reconciliation candidate until scope, time, authority, and evidence analysis establishes conflict, scope differentiation, supersession, or error.
11. Do not collapse overlap into duplication, shared ownership, or shared scope without evidence; preserve the boundary question when the relationship is unresolved.
12. After synthesis, route every unresolved inconsistency, overlap, and open question to the mandatory HITL clarification prompt surface.

---

## 10. Identity-resolution policy

### 10.1 Identity states

Use these states:

- `unresolved_mention`
- `candidate_match`
- `canonical_identity`
- `rejected_match`
- `merged_identity`

Stable internal placeholders may represent unresolved mentions without inventing a full name or affiliation.

### 10.2 No-guess rules

1. Use a canonical full name only when supported by the current evidence layer.
2. Never infer or expand a partial name, initials, affiliation, title, role, reporting line, decision right, or relationship from shorthand, whiteboard proximity, repeated mention, meeting attendance, centrality, semantic similarity, or model memory.
3. Similarity, fuzzy matching, phonetic matching, embeddings, and link prediction may retrieve candidates only.
4. Keep collisions separate until directly resolved.
5. Normal stakeholder outputs should display canonical names; hidden retrieval aliases may remain governed metadata.
6. If the user provides a stale or incorrect alias, answer with the canonical name when established and preserve the alias only when useful for retrieval or history.
7. Alias retention does not require normal display.
8. After a person is correctly resolved to a LinkedIn profile, the profile display name controls canonical personal-name spelling. A URL slug, search snippet, nickname/full-name similarity, or candidate profile does not establish spelling or identity. This rule does not establish identity equivalence, current role, affiliation, reporting line, ownership, decision rights, or currentness; preserve useful source variants as governed aliases or source mentions.

### 10.3 Active unresolved examples

These are current validation cases, not universal identity rules:

- The Josh mentioned in Speaker_Alpha’s feedback remains unresolved and is not mapped to Joshua Gary, logan Miller, Josh A, or another person without direct evidence.
- The Clay feedback target is resolved as Clay Palumbo, the former Project Beacon lead, who has left PartnerAdvisory. Exact departure date and historical decision rights remain unasserted.

---

## 11. Graph and ontology governance

### 11.1 Routine graph analysis

Evidence-backed identification of a source, evidence item, stakeholder, organization, meeting, event, system, initiative, workstream, decision, deliverable, risk, action, or relationship is a normal analysis operation when the concept is supported and uses an approved type. During Phase A it remains a proposed object in scratch, not a canonical graph mutation.

### 11.2 Gate G1 — proposed graph delta approval

Before any semantic graph mutation, produce a complete read-only proposed graph delta containing:

- baseline ZIP hash, baseline graph hash, policy hash, and input artifact hashes;
- every proposed source, evidence item, node, edge, claim, alias, node-card mapping, validation, action, register row, controlled-state change, update, supersession, retirement, or deletion;
- stable proposed identifiers and operation types: `add`, `update`, `supersede`, `retire`, `delete`, or `no_change`;
- exact before and proposed-after values for updates;
- source evidence, claim-type authority, temporal posture, confidence dimensions, materiality, audience, classification, HITL, and conflict state;
- identity-collision, duplicate, merge, and no-guess analysis;
- proposed card routes, downstream derivatives, migration impact, and rollback or forward-repair;
- per-component counts and explicit zero-change declarations; and
- a cryptographic digest over a canonical serialization of the complete proposal.

Then stop. No canonical database, graph export, migration file, card file, release-control file, or versioned package may be changed on the basis of that proposal until the user explicitly approves its digest.

The approval record must bind the proposal digest to the baseline hash and inputs. Approval of one graph proposal does not authorize additional objects, operations, routes, or meaning-bearing changes.

### Mandatory graph-change metric gate

Every Gate G1 proposal and Gate R1 conformance report for a semantic graph change must include this table. Use `N/A` only with a written reason. A failed row blocks implementation or release unless an explicit, pre-approved exception exists.

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---:|---:|---|---|
| Operational orphan rate |  | ≤5% and no increase |  | Identify and connect or justify new orphans |
| Duplicate node count |  | 0 new high-impact duplicates |  | Merge only through approved identity controls |
| Duplicate edge rate |  | ≤1% unexplained and no increase |  | Consolidate repeated relationships or separate evidence |
| Rare edge-type rate |  | ≤25% or no increase |  | Reuse approved vocabulary; justify new edge types |
| Claim completeness |  | 100% |  | Require subject, source, route, confidence, and HITL |
| Source metadata completeness |  | ≥95%; 100% for material sources |  | Add date, authority, scope, and posture |
| Temporal completeness |  | ≥90% and no decrease |  | Add effective/currentness posture for time-sensitive facts |
| High-impact identity collisions |  | 0 unresolved |  | Keep candidates separate and open validation |
| P0/P1 SLA breach rate |  | 0% |  | Assign one owner and due date; resolve or block use |
| Decision-safe answer regression |  | 0 regression on approved test set |  | Correct retrieval, ranking, wording, or unknown handling |

Report both baseline and proposed/actual values in `Result` using `before → after (delta)`. Comments must explain every failure, exception, or material movement, including the corrective action and accountable owner.

### Gate O1 — three-table inline output

Every G1/C1 proposal review and every R1 conformance review must expose exactly three Markdown tables inline, in this order:

1. `Graph Delta and Retrieval Impact`
2. `Mandatory Graph Metrics`
3. `Card Additions and Retrieval Impact`

The inline gate body must contain no other narrative, table, code block, digest list, approval command, source-assessment detail, reconciliation detail, or operation inventory. Supporting material must be filed separately. File cards or artifact links may accompany the response outside the gate body but may not replace any required table.

The exact required columns are:

| Table | Required columns | Completeness rule |
|---|---|---|
| Graph delta | `Graph component`; `Baseline`; `Proposed / actual`; `Delta`; `What changed`; `Retrieval rationale` | Include sources, nodes, aliases, edges, claims, validations, node-to-card mappings, and every other non-zero graph component. Counts must reconcile to the proposal/conformance and arithmetic must be correct. For an all-zero scalar or vector delta, the renderer must output exactly `NA`. For any row containing a non-zero delta, `NA`, `N/A`, blank, or generic language is invalid and the rationale must identify the retrieval mechanism, such as exact match, disambiguation, lineage traversal, routing, ranking, currentness, or decision safety. |
| Graph metrics | `Metric`; `Result`; `Pass threshold`; `Status`; `Comment / required improvement` | Include the exact ten approved metrics, order, and thresholds. Results must use `before → after (delta)`. `FAIL` blocks; `N/A` requires a reason; `EXCEPTION` requires a pre-approved exception ID. |
| Card additions | `Card`; `Addition`; `Change type`; `Defense / evidence`; `Retrieval improvement` | Include every affected card, including route-only changes. If there is no card or routing impact, include exactly one controlled `No affected cards` / `no_change` row. Rows must reconcile to C1 and proposed/actual node-card routes. |

The control utility must generate the tables from machine-readable proposal or conformance fields and validate the rendered Markdown byte-for-byte after line-ending normalization. It must fail closed on a missing, extra, reordered, renamed, unreconciled, arithmetically invalid, failed, or unbound row or table.

Graph-delta rationale enforcement is delta-aware and vector-aware. The renderer normalizes every all-zero graph row to the literal `NA`. A non-zero scalar or vector delta with `NA`, `N/A`, blank, or generic rationale fails Gate O1. This applies to both G1 and R1.

Gate O1 is blocking:

- Phase A is incomplete and approval may not be sought until the G1 output passes.
- Phase C may not begin unless the approved G1 output hash is bound to the approval record.
- Gate R1 and successor packaging fail unless both G1 and R1 outputs pass and the R1 hash is bound to conformance.
- A prose summary or filed artifact does not substitute for an inline table.

### 11.3 Escalated structural changes

The following controls apply in addition to Gate G1:

| Change | Required control |
|---|---|
| New instance of an approved type | G1 proposal, evidence, and normal ingest controls |
| New alias or candidate identity | G1 plus identity controls and collision review |
| New relationship assertion | G1 plus evidence, scope, time, and conflict controls |
| New ontology/type or relationship vocabulary | G1 plus structural decision record and explicit approval |
| New domain anchor | G1 plus structural necessity and integration analysis |
| New or changed canonical card | Gate C1; X/Y/Z additionally applies to spine changes |

Reuse existing entities and relationships when they represent the same concept. Do not force new evidence into an incorrect existing entity merely to avoid creating a legitimate instance.

### 11.4 Material routing

Every material or synthesis-relevant assertion must have a proposed canonical card route or an explicit reason it is intentionally unrouted. The approved and implemented route must match the proposal. Supporting evidence and operational metadata may inherit the route of their parent assertion, source, or domain. Avoid meaningless mappings created only to satisfy a count.

---

## 12. Canonical card spine and controlled evolution

### 12.1 Default posture

The approved card spine is **stable by default**, not immutable. Missing source detail, lineage gaps, or dense source packs should normally be handled through evidence, assertions, relationships, aliases, validation items, focused derivatives, and carefully proposed affected-card refactoring.

### 12.2 Permitted operations

A card may be edited, added, split, merged, renamed, re-scoped, deprecated, retired, superseded, or reactivated only through the applicable approval controls.

Card lifecycle states are:

- `proposed`
- `active`
- `deprecated`
- `retired`
- `superseded`

Never reuse a retired card ID for a different semantic purpose. A retired card may be reactivated only for its original semantic purpose.

### 12.3 Gate C1 — proposed card delta approval

Before editing any canonical card, produce a complete proposed card change set containing:

- every card expected to change;
- exact section-level or line-level before/after diff;
- rationale and supporting proposed or approved graph items;
- whether each edit changes interpretation, currentness, risk, action, allowed use, routing, classification, or only provenance;
- expected additions, removals, relocations, redirects, and frontmatter changes;
- confirmation that no unlisted canonical card will change;
- rollback or forward-repair instructions; and
- a cryptographic digest over a canonical serialization of the complete card proposal.

Then stop. Explicit user approval of the card proposal digest is required even when the card ID, card count, lifecycle state, and card spine remain unchanged. “Refactor affected cards,” “update the cards,” or similar generic language does not approve an unseen card diff.

The complete affected-card and route set must also appear in Gate O1’s third inline table. A route-only addition is a card impact for the inline decision surface even when no card-body bytes change.

### 12.4 X/Y/Z structural evolution gate

A spine modification requires Gate C1 and all three conditions:

#### X — Demonstrated structural necessity

Provide evidence that the current spine causes material retrieval ambiguity, duplicated synthesis, incompatible decision purposes, unclear canonical routing, or degraded executive readability. First evaluate existing-card rewrite, new sections, aliases, mappings, graph relationships, views, exports, and answer packets. Card length, source volume, or node count alone is insufficient.

#### Y — Durable strategic value

Show that the proposed structure represents a durable account domain with recurring executive or account-team use, coherent evidence coverage, meaningful differentiation, and expected relevance beyond one meeting, source pack, or reporting cycle.

#### Z — Controlled and reversible migration

Provide explicit user approval, a decision record comparing no change and existing-card refactoring, old-to-new routing, evidence and audience impact, redirects or successors, rollback or forward-repair, and old-versus-new retrieval evaluation.

Without X, Y, and Z, preserve the current active spine.

### 12.5 Current card-home summary

The current approved homes are listed in Appendix C. The canonical card register should be the machine-readable authority when available; this summary must be generated from or reconciled to it.

### 12.6 Retired ID rule

`NEE_17`–`NEE_21` remain inactive unless reactivation of their original semantic purpose passes Gate C1 and the X/Y/Z gate. They must never be assigned a new purpose.

---

## 13. Confidence, validation, materiality, and priority

### 13.1 Separate dimensions

Do not use one label to represent multiple concepts.

| Dimension | Controlled values |
|---|---|
| **Evidence strength** | `high`, `medium`, `low`, `unknown` |
| **Validation state** | `unreviewed`, `reviewed`, `confirmed`, `rejected`, `partially_confirmed` |
| **Currentness** | `current`, `as_of_dated`, `historical`, `stale`, `unknown` |
| **Conflict state** | `none_known`, `corroborated`, `disputed`, `superseded`, `scope_differentiated` |
| **Allowed use** | `private_only`, `internal_working`, `leadership_ready`, `client_ready`, `public_ready`, `blocked` |

A simplified confidence summary may be derived for display but cannot replace the underlying dimensions.

### 13.2 Materiality

Assess materiality independently from evidence strength:

- `critical`: could materially affect a client/executive decision, commitment, security, commercial position, or reputation.
- `high`: could materially affect account strategy, delivery, ownership, or significant resource decisions.
- `medium`: useful for planning or synthesis but not independently decision-determinative.
- `low`: contextual or supporting detail with limited downstream impact.

### 13.3 Validation priority

| Priority | Meaning |
|---|---|
| **P0** | Blocks an imminent high-impact external, executive, commercial, security, or irreversible use. |
| **P1** | Blocks a scheduled material decision, commitment, or near-term account action. |
| **P2** | Improves planning, synthesis, readiness, or reusable knowledge but does not block an imminent decision. |
| **P3** | Monitor or validate if the topic becomes active or repeated. |

Each priority item should separately record owner role, required evidence, blocked use, next action, linked event/milestone, due date, and allowed use before validation. P1 items should be grouped into coherent sprint or decision lanes rather than left as an undifferentiated backlog. Historical labels such as “before August rebaseline” belong in linked milestones, not in the permanent priority definition.

---

## 14. Audience, classification, and HITL

### 14.1 Audience taxonomy

Use the narrowest applicable audience:

- `private_user`
- `internal_working_team`
- `partner_advisory_account_leadership`
- `partner_advisory_executive`
- `joint_team`
- `client_executive`
- `external_public`

Movement to a broader audience requires the evidence, currentness, classification, and review posture appropriate to that audience.

### 14.2 Information classification

Use these minimum classifications:

- `public`
- `internal`
- `confidential_account`
- `restricted_commercial`
- `restricted_security`
- `personal`
- `private_coaching`

Classification controls minimum audience, redaction, allowed transformations, and required review. High confidence does not override classification.

### 14.3 HITL tiers

| Tier | Meaning |
|---|---|
| `blocked` | Use is prohibited until specified evidence or approval is obtained. |
| `approval_required` | Explicit approver authorization is required. |
| `review_required` | Human review is required before the stated use. |
| `review_recommended` | Review is advisable but not mandatory for the stated use. |
| `not_required` | Automated or direct use is permitted within the recorded audience and classification. |

Determine the tier from claim type, evidence authority, currentness, conflict, materiality, sensitivity, reversibility, and audience—not from confidence alone.

### 14.4 Default high-risk triggers

Normally require `blocked`, `approval_required`, or `review_required`:

- Unresolved source conflict affecting a material proposition.
- A material unresolved overlap or open question exposed by new synthesis.
- Commercial terms or commitments.
- Current role, reporting line, ownership, or decision rights without direct authority.
- Security incidents, restricted architecture, proprietary methods, or restricted demos.
- Client-facing claims that are not already `client_ready`.
- Irreversible ontology, identity-merge, or card-spine changes.
- Stale-source contamination of a current assertion.
- Private coaching content moving beyond `private_user`.

A current, authoritative, unconflicted, appropriately classified, leadership-ready assertion does not require mandatory review merely because it appears in private executive preparation.

### 14.5 Mandatory post-synthesis HITL clarification prompts

After any update, query, briefing, or artifact synthesizes new information against the current package, the same user-visible response or review bundle must include a section titled `HITL clarification prompts` immediately after the synthesis.

The section must enumerate every detected unresolved:

- **Inconsistency:** propositions, sources, dates, names, statuses, boundaries, or instructions that do not yet reconcile.
- **Overlap:** roles, ownership, scope, workstreams, capabilities, deliverables, sources, concepts, or card routes that may be duplicate, nested, complementary, or boundary-ambiguous.
- **Open question:** missing evidence or a decision needed to establish identity, authority, scope, currentness, ownership, use, action, or closure.

Each prompt must contain:

- stable prompt ID and category;
- one concise question to discuss or decide;
- the competing propositions or unresolved boundary, with source, date, and authority posture;
- affected entities, scope, effective period, cards, routes, answers, or artifacts;
- why clarification matters;
- current provisional posture, HITL tier, and blocked or allowed use;
- the recommended default pending clarification;
- selectable response options, including `other` and `needs_more_evidence`;
- validation owner, evidence needed, and priority; and
- the downstream change expected from each resolution, when known.

Apply these rules:

1. Present the synthesis first and the prompts immediately after it; do not withhold useful provisional synthesis merely because clarification is needed.
2. Mark affected propositions as provisional, disputed, candidate, scope-differentiated, or blocked. Do not silently resolve, merge, rank, or close them.
3. Surface all detected issues. Semantically duplicate prompts may be consolidated only when every affected proposition and source remains listed.
4. Prompting is mandatory even when the issue is nonmaterial; materiality determines HITL tier and blocked use, not whether the issue is surfaced.
5. A prompt closes only through explicit user or authorized-source clarification, or governed evidence. Silence, lack of objection, attendance, or an unrelated approval is not closure.
6. If no issue is detected after the required checks, state `No HITL clarification prompts detected` and record the dimensions checked. Do not invent questions.

---

## 15. Ownership and approval

### 15.1 Responsibility types

Distinguish:

- `action_owner`
- `accountable_owner`
- `evidence_owner`
- `validation_owner`
- `decision_owner`
- `contributor`
- `external_dependency`

Client stakeholders may own client decisions, evidence, or validation. They are not assigned as owners of internal PartnerAdvisory execution tasks unless the task is explicitly joint and represented as such.

Current person assignments are operational mappings, not permanent policy. Appendix D records the current snapshot.

### 15.2 Approval matrix

| Change or use | Default approval |
|---|---|
| Read-only evidence inspection, reconciliation, and proposal generation | No mutation approval required; baseline must remain immutable |
| Any semantic graph mutation, including routine instance ingest or validation-state change | Explicit user approval of the Gate G1 proposal digest |
| Any canonical card body or meaning-bearing metadata edit | Explicit user approval of the Gate C1 proposal digest |
| Candidate identity merge to canonical identity | Gate G1 plus explicit approval for material or ambiguous merges |
| New ontology/type, relationship vocabulary, or domain anchor | Gate G1 plus structural decision record and explicit approval |
| Card add/split/merge/retire/reactivate or semantic re-scope | Gate C1 plus X/Y/Z decision record and explicit approval |
| Protocol activation or waiver | Explicit user approval; instruction-only activation may use Section 6.5 when graph and non-policy card deltas are zero |
| Export-only regeneration from unchanged approved canonical state | No separate approval if zero semantic delta is proven and no unapproved proposal is embedded |
| Successor versioned ZIP creation | Gate R1 must pass; candidate status is not a substitute for G1/C1 approval |
| Release state after Gate R1 and Gate O1 | Automatically `approved_current` for an authorized successor build; use `candidate` only when the user explicitly requests `candidate-only` or withholds promotion |
| Client/external use of blocked or restricted material | Explicit authorized approval after evidence requirements are met |
| Destructive deletion or semantic ID reuse | Prohibited unless a legal/privacy/security exception explicitly authorizes deletion; semantic ID reuse remains prohibited |

### 15.3 Approval evidence

An approval record must identify:

- decision ID and exact proposition or change approved;
- approver and approval date/time;
- baseline ZIP and graph hashes;
- input artifact hashes;
- approved graph proposal digest or signed zero-change declaration;
- approved card proposal digest or signed zero-change declaration;
- scope, audience/use, limitations, and expiration if applicable;
- approval source or explicit statement; and
- any implementation-normalization allowance.

Silence, attendance, lack of objection, or a generic request to `run` the process is not approval. A direct natural-language approval after the complete Gate O1 decision surface is explicit approval when it unambiguously refers to one pending envelope; no copy-pasted statement or digest recitation is required. The implementation agent must bind the exact hashes. Approval remains invalid when the baseline, inputs, proposal, or inline-output digest no longer match.

---

## 16. Protocol changes and exceptions

### 16.1 Protocol-change control

A protocol change may be proposed from:

- An explicit user request.
- A root-cause analysis.
- A measured audit or evaluation finding.
- A changed regulatory, security, or operating requirement.

It may not become active without explicit user approval.

Every protocol decision must record:

- Decision ID and title.
- Status: `proposed`, `approved_active`, `superseded`, `retired`, or `rejected`.
- Proposed and approved dates.
- Effective policy/package version.
- Approval source.
- Problem evidence and affected rules.
- Alternatives considered, including no change.
- Selected decision and rationale.
- Risks, migration impact, tests, rollback or forward-repair.
- Superseded decision, if any.

A protocol decision is governed by the policy active immediately before activation. An explicitly approved instruction-only activation may use Section 6.5. Once active, the new rule applies prospectively. It cannot retroactively ratify an earlier unapproved graph or card mutation.

Do not rewrite policy after implementation to make the implementation appear compliant. If a protocol change affects authority, routing, packaging, QA, provenance, or card governance, propagate the approved decision to the affected control and synthesis artifacts in the next separately authorized full-system release. A user-scoped protocol-only release may defer that propagation, but it must disclose the limitation and must not claim full-system synchronization.

### 16.2 Controlled exceptions

A temporary exception must record:

- Rule affected.
- Reason and evidence.
- Narrow scope.
- Risk and compensating control.
- Approver.
- Effective and expiration dates.
- Remediation plan.

Exceptions cannot authorize fabrication, silent audience expansion, reuse of a semantic ID for a different purpose, unsupported upgrading of commercial, architecture, ownership, role, or current-state claims, or retroactive approval of an unapproved mutation. An exception to G1, C1, I1, O1, or R1 must be explicit, narrowly scoped, time-bounded, and approved before the affected write.

---

## 17. QA obligations

QA must verify policy outcomes appropriate to the change class. Core obligations include:

| Obligation | Required outcome |
|---|---|
| Policy synchronization | `NEE_99` body and root mirror are byte-identical; declared hash matches |
| Instruction precedence | No unresolved conflict among active controlling instructions |
| Baseline immutability | The approved baseline ZIP and baseline canonical file hashes remain unchanged |
| Proposal completeness | G1/C1 artifacts enumerate every applicable operation and contain stable digests or explicit zero-change declarations |
| Graph-change metric gate | Every semantic graph change includes the machine-validated G1 and R1 `before → after (delta)` metric table; every row passes or has an explicit pre-approved exception |
| Inline output gate | G1/C1 and R1 decision surfaces contain exactly the three generated tables in the required order; counts, metrics, and card rows reconcile; G1 approval and R1 conformance bind the exact output hashes |
| Approval chronology | Approval records are valid and predate the first canonical write |
| Approval binding | Baseline hash, input hashes, proposal digests, scope, and approver match the implementation |
| Graph conformance | Actual semantic graph delta matches the approved graph proposal or an explicitly approved normalization digest |
| Card conformance | Actual canonical card diff matches the approved card proposal; no unlisted card changed |
| Out-of-envelope enforcement | Material implementation drift causes a hard stop and revised approval, not a warning |
| Evidence lineage | Material assertions trace to governed evidence or remain explicitly candidate/unknown |
| Identity safety | Collisions remain separate; merges have evidence and approval where required |
| Temporal safety | Current claims have appropriate currentness; dated evidence is not silently treated as current |
| Conflict preservation | Competing assertions are retained and correctly scoped |
| Audience isolation | Restricted and private-coaching material cannot leak to broader outputs |
| HITL enforcement | Blocked or approval-required material is not used beyond its allowed audience |
| Post-synthesis HITL coverage | Every synthesis of new information has an evidence-linked prompt register covering every detected unresolved inconsistency, overlap, and open question, or a validated zero-issue declaration; prompt IDs reconcile to affected outputs and closure evidence |
| Card governance | Active spine matches the approved lifecycle state; structural changes have Gate C1 and X/Y/Z records |
| Routing quality | Material assertions have meaningful approved routes or an explicit intentional-unrouted reason |
| Change propagation | Only approved affected layers changed; omitted downstream work is disclosed |
| Packaging gate | No successor versioned ZIP was created before Gate R1 passed |
| Packaging hygiene | Raw artifacts are excluded by default; approved exceptions are separately classified and manifested |
| Version authority | Working, review-bundle, candidate, approved-current, superseded, and archived states are unambiguous |

Counts and file-presence checks are supporting controls, not proof of semantic correctness. Current commands, IDs, extensions, and implementation-specific tests belong in the QA specification.

---

## 18. Packaging, version lifecycle, and artifact policy

### 18.1 Version states

Use:

- `working`
- `review_bundle`
- `candidate`
- `approved_current`
- `superseded`
- `archived`
- `quarantined`

Queries default to the latest `approved_current` package unless the user explicitly requests a candidate, review bundle, quarantined artifact, or historical version.

A review bundle is not a versioned NEE release. Candidate status is an explicit release-state override; it does not substitute for pre-mutation approval.

### 18.2 Packaging defaults

1. Preserve the prior package.
2. Do not overwrite approved historical releases.
3. Keep Phase A proposal artifacts outside the baseline package and do not label them as a successor NEE version.
4. Do not create a successor versioned ZIP until applicable G1/C1 approvals exist, approved implementation is complete, and Gate R1 passes.
5. Exclude raw PDFs, PPTX, DOCX, XLSX, images, rendered whiteboards, transcript dumps, old audit folders, and prior snapshots from the active package by default.
6. A restricted evidence bundle or approved redacted extract may be packaged separately when independent review requires it. It must have explicit classification, audience, manifest, and approval.
7. Include only artifacts required by the authorized change scope.
8. State what changed, what did not, the approval decision and digests, and any known synchronization limitations.
9. Preserve or quarantine a failed or unapproved build under a distinct name; never silently relabel it as approved.
10. An authorized successor build becomes `approved_current` automatically after Gate R1 and Gate O1 pass. Package it as `candidate` only when the user explicitly requests `candidate-only` or withholds promotion.

### 18.3 Stable artifact capabilities

The project should maintain stable capabilities rather than permanent release-prefixed filenames:

- Proposed graph delta and digest.
- Proposed card delta and digest.
- Approval record.
- Implementation conformance record.
- Validation closure board.
- External/safe-use guardrails.
- Executive answer packet.
- Validation sprint plan.
- Source-request register.
- Confidence/currentness lifecycle register.
- Briefing-question catalog.
- Applicable owner/action briefs.

Historical v26 filenames may remain for compatibility but are not permanent policy obligations.

---

## 19. Required templates and definitions of done

### 19.1 Proposed graph delta contract

A Gate G1 proposal must support fields equivalent to:

- `proposal_id`
- `baseline_zip_sha256`
- `baseline_graph_sha256`
- `policy_sha256`
- `input_artifact_hashes`
- `generated_at`
- `operations`
- `object_type`
- `proposed_id`
- `operation_type`
- `before`
- `proposed_after`
- `supporting_evidence`
- `source_authority`
- `temporal_posture`
- `confidence_dimensions`
- `materiality`
- `classification`
- `allowed_audiences`
- `hitl`
- `identity_and_duplicate_analysis`
- `card_routes`
- `derivative_impact`
- `rollback_or_forward_repair`
- `component_counts`
- `inline_gate_output.graph_delta_rows`
- `inline_gate_output.graph_metric_rows`
- `zero_change_declarations`
- `canonical_serialization_sha256`

### 19.1A Three-table inline gate contract

The machine-readable and rendered decision surface must support fields equivalent to:

- `inline_gate_output.graph_delta_rows[]`
- `component_key`
- `graph_component`
- `baseline`
- `proposed_or_actual`
- `delta`
- `change`
- `retrieval_rationale`
- rendered `retrieval_rationale` is `NA` when the scalar or vector `delta` is all-zero; otherwise a mechanism-specific rationale is required
- `inline_gate_output.graph_metric_rows[]`
- `metric`
- `result`
- `pass_threshold`
- `status`
- `comment`
- `preapproved_exception_id` when applicable
- `inline_gate_output.card_rows[]`
- `card`
- `addition`
- `change_type`
- `defense_or_evidence`
- `retrieval_improvement`
- `g1_inline_output_sha256`
- `r1_inline_output_sha256`
- `inline_output_gate_result`

The rendered output must be generated from these fields by the control utility and must contain only the three required tables.

### 19.2 Proposed card delta contract

A Gate C1 proposal must support fields equivalent to:

- `proposal_id`
- `baseline_zip_sha256`
- `policy_sha256`
- `cards`
- `card_id`
- `path`
- `before_sha256`
- `proposed_after_sha256`
- `unified_or_section_diff`
- `rationale`
- `supporting_graph_items`
- `impact_types`
- `frontmatter_changes`
- `routing_changes`
- `rollback_or_forward_repair`
- `unlisted_cards_unchanged`
- `inline_gate_output.card_rows`
- `canonical_serialization_sha256`

### 19.3 Approval record contract

An approval record must support fields equivalent to:

- `decision_id`
- `status`
- `approver`
- `approved_at`
- `approval_source`
- `baseline_zip_sha256`
- `baseline_graph_sha256`
- `input_artifact_hashes`
- `graph_proposal_sha256`
- `card_proposal_sha256`
- `g1_inline_output_sha256`
- `inline_output_gate_required`
- `scope`
- `limitations`
- `allowed_audiences`
- `implementation_normalization_allowance`
- `expiration`
- `signature_or_statement_reference`

### 19.4 Implementation conformance record

A Gate R1 record must support fields equivalent to:

- `implementation_id`
- `approval_decision_id`
- `first_canonical_write_at`
- `baseline_immutability_result`
- `actual_graph_delta_sha256`
- `approved_graph_delta_sha256`
- `actual_card_diff_sha256`
- `approved_card_diff_sha256`
- `changed_file_inventory`
- `unapproved_semantic_changes`
- `drift_result`
- `schema_and_integrity_results`
- `lineage_currentness_audience_hitl_results`
- `g1_inline_output_sha256`
- `r1_inline_output_sha256`
- `inline_output_gate_result`
- `inline_gate_output`
- `packaging_gate_result`
- `release_state`

### 19.5 Structural decision record

For a new ontology type, relationship vocabulary, domain anchor, or card-spine change, record:

| Field | Required content |
|---|---|
| Change ID | Stable identifier |
| Current-state evidence | Exact policy, source, query, evaluation, or use-case evidence |
| Problem | Material limitation or missed capability |
| Objective served | Account, executive, synthesis, learning, or career value |
| Proposed design | Exact structural or policy change |
| Alternatives | At least no change and one credible alternative |
| Selection rationale | Decision criteria and trade-off |
| Risks/failure modes | What could become harder or unsafe |
| Migration impact | Affected evidence, routes, cards, audiences, and consumers |
| Reversibility | Rollback or forward-repair path |
| Approval | Required approver and decision evidence |
| Validation | Tests or evaluations demonstrating improvement |

### 19.6 Candidate repair record

Include only applicable fields:

- Missing concept or relationship.
- Evidence and user-provided context.
- Candidate entity/alias/assertion/relationship/source or validation item.
- Confidence dimensions, currentness, materiality, audience, and HITL.
- Existing homes considered.
- Card route or intentional-unrouted reason.
- Whether synthesis changes now or after validation.

### 19.7 Answer-packet contract

A machine-readable material answer should support fields equivalent to:

- `question`
- `new_information_synthesized`
- `answer`
- `answer_posture`
- `supporting_entities`
- `supporting_relationships`
- `supporting_assertions`
- `supporting_sources`
- `supporting_evidence`
- `source_authority`
- `evidence_strength`
- `validation_state`
- `currentness`
- `conflict_state`
- `missing_evidence`
- `validation_ids`
- `hitl_tier`
- `allowed_audiences`
- `classification`
- `card_routes`
- `derived_insights`
- `synthesis_clarification_issues`
- `hitl_clarification_prompts`
- `hitl_prompt_zero_declaration`
- `recommended_actions`
- `derivation_summary`

### 19.7A HITL clarification prompt contract

A machine-readable post-synthesis prompt register must support fields equivalent to:

- `prompt_id`
- `category`: `inconsistency`, `overlap`, or `open_question`
- `status`: `open`, `answered`, `evidence_requested`, `resolved`, `superseded`, or `not_applicable`
- `question`
- `propositions_or_boundary`
- `supporting_sources` and `supporting_evidence`
- `source_authority` and `effective_period`
- `affected_entities`, `affected_cards`, `affected_routes`, and `affected_outputs`
- `materiality`
- `hitl_tier`
- `blocked_or_allowed_use`
- `recommended_provisional_posture`
- `response_options`
- `validation_owner`
- `evidence_needed`
- `priority` and `due_date` when applicable
- `resolution`
- `closure_evidence`
- `downstream_change`
- `zero_issue_declaration` and `checked_dimensions` when no issue is detected

### 19.8 Definition of done — update

An update is complete when:

- The change class, baseline, inputs, and authorized scope are explicit.
- Applicable G1 and C1 proposals are complete, digest-bound, and explicitly approved before the first canonical write.
- Evidence, authority, time, identity, conflict, audience, and HITL are resolved or visibly unresolved.
- Every detected post-synthesis inconsistency, overlap, and open question appears in the HITL clarification register and user-facing prompt surface, or a validated zero-issue declaration is present; affected propositions remain provisional or blocked until closure.
- Required affected layers are updated; intentionally omitted layers are disclosed.
- Material assertions have meaningful approved routes.
- Structural changes have approval, migration/redirect, and evaluation.
- Actual graph and card deltas conform to the approval envelope.
- No unapproved semantic file changed.
- For semantic graph changes, the mandatory graph-change metric gate appears in both G1 and R1, reports baseline → proposed/actual values and deltas, and has no failed row without an explicit pre-approved exception.
- Gate O1 generates and validates exactly the three required inline tables, reconciles graph/card rows to the proposals or conformance record, and binds the G1 and R1 output hashes.
- Gate R1, Gate O1, and required QA pass before successor packaging.
- Candidate versus approved-current status is clear.
- The prior version is preserved.

### 19.9 Definition of done — query

A query is complete when:

- Required package layers were searched.
- The answer uses a controlled posture.
- Material claims expose evidence, currentness, conflict, audience, and HITL.
- Every detected post-synthesis inconsistency, overlap, and open question is presented as a HITL clarification prompt immediately after the synthesis, or the answer states that none were detected after the required checks.
- Missing information remains missing rather than being completed from memory.
- Derived recommendations are traceable to the supporting subgraph.

---

# Appendix A — Current domain guardrails

## A1. Adaptive AI-PDLC and Project Beacon

- Adaptive AI-PDLC decks are directional pattern language unless validated against NEE-specific evidence.
- They may inform `NEE_06`, `NEE_07`, `NEE_08`, `NEE_11`, and `NEE_16`.
- They may bridge carefully into `NEE_12` and `NEE_22` when the connection is evidence-backed and labeled.
- A2A means Agent-to-Agent protocol. For Project Beacon, it describes handoff and coordination among specialized agents inside a broader advisor or orchestration platform.
- A2A, orchestration, centrality, or repeated mention does not prove correctness, authority, ownership, or production readiness.

## A2. Rewire and NEA product-lane controls

- Rewire ownership remains open unless explicitly confirmed.
- Do not infer ownership from whiteboard proximity, NEE operating context, NEE IT corollary lanes, meeting attendance, or product-lane mandate context.
- NEA product lanes may use explicit lane entities and `HAS_COROLLARY_IN NEE IT` relationships for platform, controls, hardening, architecture, production readiness, and governance when supported.
- Broader Rewire product-line inventory remains a validation follow-up tied to an active milestone, not a permanent P1 definition.

## A3. Gemini Enterprise Enablement and REWIRE AM&WE

- The safe-use question is: **“What can I safely say about Gemini Enterprise Enablement?”**
- Avoid person-centered briefing phrasing unless the user asks for that perspective.
- Gemini proposal facts remain proposal-level unless executed scope, approved scope, or accountable-owner validation upgrades the exact proposition.
- REWIRE AM&WE PM/GM deck facts remain source-attributed as of 2026-04-07 unless current authoritative evidence upgrades currentness.

## A4. Sensitive-use controls

- Commercials require executed commercial authority or explicitly approved commercial validation.
- Public/current claims require current public verification.
- Security incidents require appropriate technical/security validation.
- Stakeholder titles, organizational lines, decision rights, and ownership require direct authoritative evidence.
- Proprietary architecture, restricted demos, and confidential methods remain restricted regardless of confidence.

## A5. NEA and EDO roster identity boundary

- Every named person in the user-supplied NEA Tech Leads and EDO roster emails is a PartnerAdvisory employee.
- A PartnerAdvisory address and an `@grid.example.com` or `@enterprise.example.com` address may be confirmed aliases on one canonical PartnerAdvisory person; the client-domain account does not establish separate identity or NEE/APEX employment.
- Roster placement is not evidence of attendance, title, role, workstream, allocation, authority, or decision rights.
- The six opaque APEX accounts supplied in the NEA email are excluded entirely unless the user explicitly reopens them.
- Partial names remain unresolved unless the user or an authoritative source explicitly confirms the mapping.

---

# Appendix B — Protocol decision register

| Decision ID | Status | Effective version | Decision |
|---|---|---:|---|
| `PD-25-IDENTITY` | `superseded` | 25 | Canonical-name display and alias-retention controls. Superseded by the consolidated Section 10 identity policy. |
| `PD-26-EXECUTION` | `approved_active` | 26 | P0/P1 items require owner role, evidence need, blocked/allowed use, next action, milestone, and due date. Stable capabilities replace permanent v26 filenames. |
| `PD-27-GEMINI-REWIRE` | `approved_active` | 27 | Gemini proposal and dated REWIRE AM&WE source controls; now maintained in Appendix A3. |
| `PD-31-SOURCE-ASSERTION` | `approved_active` | 31 | Source-attributed reporting is distinct from a current independent assertion; now promoted to Section 8.3. |
| `PD-32-FULL-NAME` | `superseded` | 32 | Full-name/no-guess controls and named unresolved cases; superseded by Section 10 while active cases remain in Section 10.3. |
| `PD-34-INSTRUCTION-REFACTOR` | `approved_active` | 34 | Establishes one canonical policy, body-identical mirror, explicit precedence, change classes, controlled spine evolution, layered evidence/synthesis/coaching, risk-based HITL, durable priorities, role-based ownership, protocol lifecycle, and proportional QA. Approved by the user’s explicit v34 instruction-only refactoring request. This release intentionally left all non-instruction artifacts byte-identical to the v33 baseline; broader propagation was deferred. |
| `PD-45-PRE-MUTATION-GRAPH-CARD-GATES` | `approved_active` | 45 | Requires read-only proposed graph and card deltas, explicit digest-bound user approval, approval-bound implementation, hard-stop drift control, and Gate R1 conformance before successor packaging. Candidate status cannot substitute for mutation approval. Activated by the user’s explicit 2026-07-11 instruction to implement the RCA and gating in v45. This instruction-only activation does not ratify the prior unapproved v45 semantic build; account graph and non-policy cards remain the v44 baseline pending separate G1/C1 approval. |
| `PD-47-MANDATORY-GRAPH-CHANGE-METRIC-GATE` | `approved_active` | 47 | Requires every semantic graph change to include the fixed before/after graph-health metric table in Gate G1 and Gate R1. Failed rows block implementation or release unless an explicit pre-approved exception exists; comments must state the improvement, corrective action, and accountable owner. |
| `PD-47.1-THREE-TABLE-INLINE-OUTPUT-GATE` | `approved_active` | 47.1 | Requires exactly three inline gate tables—graph delta with retrieval rationale, fixed graph metrics, and card additions with retrieval rationale—while filing all other detail separately. The control utility generates and validates the output, binds G1 to approval and R1 to conformance, and blocks packaging on any omission, extra content, reconciliation failure, metric failure, or hash mismatch. |
| `PD-47.1.1-ZERO-DELTA-NA-RATIONALE` | `approved_active` | 47.1.1 | Requires every all-zero graph-delta row to render `NA` in the retrieval-rationale column and rejects `NA`, `N/A`, blank, or generic rationale on any non-zero scalar or vector delta. The rule is enforced in G1 and R1 with regression tests. |
| `PD-47.2-LINKEDIN-NAME-SPELLING-AUTHORITY` | `approved_active` | 47.2 | After a person/profile match is correctly resolved, the LinkedIn profile display name controls canonical personal-name spelling. URL slugs, snippets, and name similarity do not prove spelling or identity; the rule does not establish role, affiliation, reporting line, ownership, decision rights, or currentness. Activation is contingent on exact digest-bound approval of the associated G1/C1 proposal. |
| `PD-51-ROSTER-IDENTITY-BOUNDARY` | `approved_active` | 51.0 | Confirms named NEA/EDO roster people as PartnerAdvisory employees, treats PartnerAdvisory and client-domain accounts as aliases on one canonical person, prohibits attendance/role/authority inference from roster placement, excludes the six opaque accounts unless reopened, resolves Clay Palumbo, and standardizes Cameron Mercer spelling. Activated only by exact digest-bound approval of the associated v51 G1/C1 proposal. |
| `PD-51.1-AUTO-PROMOTION-AFTER-R1-O1` | `approved_active` | 51.0 | Removes the separate promotion step. Every authorized successor build becomes `approved_current` automatically after Gate R1 and Gate O1 pass unless the user explicitly requests `candidate-only` or withholds promotion before packaging. This decision does not weaken pre-mutation approval, exact-delta conformance, or R1/O1 blocking controls. |
| `PD-53-NATURAL-LANGUAGE-APPROVAL-BINDING` | `approved_active` | 53.0 | Clarifies that, after a complete passed Gate O1 surface, an unambiguous direct approval such as `approve it` authorizes the single pending envelope. The implementation agent binds exact hashes; the user is not required to repeat formulaic digest wording. Ambiguous or generic run requests still fail closed. |
| `PD-55-SYNTHESIS-HITL-CLARIFICATION-PROMPTS` | `approved_active` | 55.0 | Requires every update, query, briefing, or artifact that synthesizes new information to present an evidence-linked HITL clarification prompt for each detected unresolved inconsistency, overlap, and open question immediately after the synthesis. The rule prohibits silent resolution, requires explicit zero-issue declarations, scales blocking by existing materiality/HITL controls, and adds QA and answer-packet contracts. |

Future decisions must use the metadata and approval requirements in Section 16.

---

# Appendix C — Current approved canonical card homes

| Card | Canonical home |
|---|---|
| `NEE_00` | Authority, source map, and synthesis protocol |
| `NEE_01` | Strategy and account thesis |
| `NEE_02` | Scope, status, risks, and project dossiers |
| `NEE_03` | SOWs, fees, terms, and commercial guardrails |
| `NEE_04` | NEA Q3 capacity and charters |
| `NEE_05` | Chakra synthesis and rebaseline |
| `NEE_06` | Co-delivery/co-hydrated operating model |
| `NEE_07` | AI SDLC, evals, survey, and controls |
| `NEE_08` | Monthly strategic engineering forum |
| `NEE_09` | Utility thought leadership, REWIRE, and AM&WE |
| `NEE_10` | Stakeholders, organization, champions, and credentialing |
| `NEE_11` | Cross-workstream synthesis and reuse |
| `NEE_12` | AI Hub, Sherpa, DevHub, and PGD lessons |
| `NEE_13` | EDO data-product and operating-model patterns |
| `NEE_14` | Trading, ETRM, and pricing models |
| `NEE_15` | Glossary, aliases, and identity resolution |
| `NEE_16` | Source provenance and cold-archive pointers |
| `NEE_22` | Project Beacon, A2A, and platform-advisor context |
| `NEE_99` | Project instructions and governed update/query protocol |

This appendix is a current summary, not an invariance rule. Any change requires Section 12 controls.

---

# Appendix D — Current operational assignment snapshot

**Effective date:** 2026-07-10  
**Status:** operational mapping; not evergreen policy

| Person | Current role-oriented responsibility |
|---|---|
| Dan | Account cadence and stakeholder follow-up |
| Amit Ayer | Synthesis, operating model, and rebaseline preparation |
| Cameron Mercer | Engineering model, pod-to-EM, and Q3 alignment |
| Andrew | AI evals, control framework, and technical quality |

Future updates should preserve the responsibility types in Section 15 and update person mappings separately when assignments change.

---

# Appendix E — Non-normative application examples

1. A dated status packet can support `source_attributed` status as of its date; it does not by itself support `current_validated` status months later.
2. A fuzzy match between “Josh” and a known Joshua can create a `candidate_match`; it cannot create a canonical identity.
3. A long card does not justify a split. Repeated mixed retrieval and incompatible executive purposes may satisfy structural necessity.
4. A provenance-only correction can be a C7 or C8 update and need not rewrite an executive card when synthesis is unchanged; a canonical validation-state write still requires Gate G1.
5. A source can strongly prove that a statement was made while the statement’s present-day truth remains stale or unknown.
6. “Run the project instructions on these attachments” means analyze the attachments and produce G1/C1 proposals. It does not authorize graph or card mutation.
7. A package labeled `candidate` may still be invalid if it was created before the proposed deltas were approved.
8. If an approved proposal lists ten card edits and implementation reveals an eleventh affected card, implementation stops and returns to Gate C1.
9. A protocol-only activation can change `NEE_99` and its mirror while leaving the account graph and all non-policy cards byte-identical. It cannot be used to bless a previously mutated package.
10. After a complete passed Phase A decision surface, `approve it and proceed` authorizes the single pending envelope. The implementation record—not the user—must bind the exact proposal and baseline hashes.

---
