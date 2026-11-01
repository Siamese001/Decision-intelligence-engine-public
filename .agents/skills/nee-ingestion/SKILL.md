---
name: nee-ingestion
description: NEE knowledge graph ingestion pipeline from directives through intake stages (U0/L1/L0/C0/X1-X3/L4), preprocessing DAG, and release gates. Use for source ingestion.
---

# NEE Ingestion Pipeline

This skill guides the full ingestion lifecycle for the NEE knowledge graph.

## When to use

- Running the **Energy Intelligence Agent** (`energy-utilities-best-practices` sidecar) or **Agentic Frontier Architecture Agent** (`agentic-repo-best-practices-scout` sidecar): **MUST** first read `/Users/amitayer/.gemini/config/sidecars/<sidecar-dir>/sidecar.json` and execute [`automations/prompts/weekly_market_intelligence_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/weekly_market_intelligence_agent.md) or [`automations/prompts/agentic_repo_best_practices_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/agentic_repo_best_practices_agent.md) end-to-end (`Step 0.1` isolated worktree creation via `tools/pr_governance.py start` -> wave plan in `docs/plans/<branch-slug>.md` -> atomic HITL -> branch commit, `tools/pr_governance.py rebase`, and governed PR merge to `main`).
- User supplies source material (documents, meeting notes, HTML, PDF, DOCX, Markdown, meeting audio)
- User asks to "ingest", "process", "run", or "reconcile" material
- User approves graph mutations from a prior analysis

## Pre-requisites

1. Verify environment is active: `python --version` (virtual environment active)
2. Run preflight: `python nee.py preflight`
3. Classify source material per [`policy/pre-intake-classification-policy.json`](file:///Users/amitayer/Git/decision-intelligence-engine/policy/pre-intake-classification-policy.json)

## Lifecycle Steps

### 1. Open a Directive

```bash
python nee.py intake-directive-open --package <path-to-package>
```

Binds the directive to exact contract-valid package bytes. The directive persists across follow-up turns until a terminal outcome is recorded.

### 2. Attach Additional Packages (if needed)

```bash
python nee.py intake-directive-attach --directive-id <id> --package <path>
```

Empty attach is a no-op. Adding nonempty bytes invalidates prior approval.

### 3. Source Adapter Selection

The canonical Evidence Envelope JSON format (`urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0`) is the sole intake mechanism into the knowledge graph:

| Source Type | Adapter Module | Prepare Tool | Synthesis Contract |
|------------|---------------|--------------|-------------------|
| Evidence Envelope JSON | `dual_pipeline.intake` | [`prepare_evidence_envelope_intake.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/prepare_evidence_envelope_intake.py) | — |
| Raw Meeting Transcripts / Audio | `dual_pipeline.intake` | [`prepare_evidence_envelope_intake.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/prepare_evidence_envelope_intake.py) | [`RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md`](file:///Users/amitayer/Git/decision-intelligence-engine/config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md) |
| Word Documents (.docx) | [`docx_source.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/docx_source.py) | `dual_pipeline.docx_source` | — |
| PDF Documents (.pdf) | [`pdf_source.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/pdf_source.py) | `dual_pipeline.pdf_source` | — |
| HTML Pages (.html) | [`html_source.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/html_source.py) | `dual_pipeline.html_source` | — |

#### Raw Transcript Preprocessing Contract (Agentic Pipeline)
When processing raw meeting transcripts or minutes:
1. Ingest directly via canonical CLI entry point or governed wrapper:
   ```bash
   nee ingest-meeting \
     --input "${INPUT}" \
     --project "${PROJECT}" \
     --meeting "${TITLE}" \
     --date "${DATE}" \
     ${ATTENDEES:+--attendees "${ATTENDEES}"}
   ```
   Or: `./scripts/ingest_meeting.sh --input "${INPUT}" --project "${PROJECT}" --meeting "${TITLE}" --date "${DATE}"`
2. The pipeline routes through `U0` $\to$ `L1` $\to$ `L0` $\to$ `C0` $\to$ `PA` $\to$ `L2` $\to$ `X1-X3` and generates the three canonical outputs:
   - `01_<meeting_name>_<date>_<time>_hitl_resolved_transcript.md`
   - `02_<meeting_name>_<date>_<time>_executive_recap.md`
   - `03_<meeting_name>_<date>_<time>_canonical_signal_projection.json`
3. Packaging, structural hierarchy checks, and GraphDB publication with BGE-M3 retrieval evaluation execute automatically (gating on operator approval when required).
*(For full details, see the [`nee-transcript-synthesis`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-transcript-synthesis/SKILL.md) skill).*

#### Mandatory Deliverable Links in Completion Responses
Ingestion completions must include clickable markdown file links (`file:///...`) for each generated deliverable per [.agents/rules/engineering-output.md](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/engineering-output.md) §Ingestion Outcome Links.

These links are outcome results under `.agents/rules/engineering-output.md`, not prohibited process inventory. All paths must be resolvable, and awaiting-approval isolation must be truthfully reported without claiming active graph mutation.

#### Active Ingestion & Approval Policy Invariant
- **Default Execution**: Ingestion runs default to active graph intent, sealing deliverables and requesting operator approval when exact pre-bound clearance is absent.
- **Package-Only Runs (`--package-only`)**: If `--package-only` is explicitly specified, the pipeline prepares and seals deliverables (`PACKAGE_SEALED`) and generates an `active-ingestion-approval-request`.
- **Mandatory HITL Presentation**: The agent MUST NOT terminate after a package-only run without presenting the mutation approval request to the operator via `ask_user_question` (or the preview exit hook). Sealing without operator presentation is a governance failure (`hitl-decisions` Rule 9).

#### Dry-Run Policy
- **Optional & Offline**: `--dry-run` is strictly optional and not a required preflight step. When invoked, it executes 100% offline with zero outbound network calls, running identical loader preflight checks as a real run.

#### Governed Ingestion Execution Lifecycle and Worktree Isolation
Ingestion is a governed knowledge graph operation with automated branch isolation:
- **Worktree Isolation Invariant**: All ingestion runs isolate candidate deliverables inside dedicated git worktrees (`.worktrees/ingest_doc_*` or `.worktrees/ingest_meeting_*`) on conventional branches (`ingest/doc-*`, `ingest/meeting-*`), enforced fail-closed by `--enforce-worktree=True`. Code, configuration, or schema changes required during intake MUST be committed inside an isolated worktree branch via `tools/pr_governance.py start <branch>` and NEVER applied directly on `main`.
- **Ingestion Completion Gate**: Ingestion ends at operator approval via `ask_user_question` and active pair activation (`nee intake-approval-resume` / `nee intake-publish`). While graph activation updates the active pair SSOT (`.local/dual_pipeline/active_pair.json`), source artifacts and deliverables remain preserved within their isolated worktree branch.
- **Direct Governed Execution**: Run ingestion directly via the governed CLI (`nee ingest-meeting`, `nee ingest-document`). The pipeline automatically provisions and isolates worktrees, writes candidate packages, requests operator approval, and activates the newly published generation in the single active-pair SSOT.

If no compatible adapter exists, either implement one or block the directive:

```bash
python nee.py intake-directive-block --directive-id <id> --reason "NO_COMPATIBLE_ADAPTER"
```

### 4. Key Master Constraints

- **C-01 Source Binding**: Every entity, statement, and update must link to verifiable source locators.
- **C-02 Closed Enum Kind**: Statements must use only 6 valid kinds: `summary`, `decision`, `risk`, `dependency`, `technical`, `strategic`.
- **C-03 / C-04 Entity Spine**: Standard prefixes (`person:`, `org:`, `system:`, etc.) conforming to W2 Semantic Spine.
- **C-07 W3 Temporal Hygiene**: Decisions and risks must declare `observed_at` and `stale_after` (`MM/DD/YYYY`).
- **C-16 Prior Context Protocol**: Query active graph before synthesis; use `CONFIRM` for existing entities, `CREATE` only for novel ones.
- **C-17 Hard Evidence Ceiling**: `evidence_only_statement_ids` must not exceed $\max(3, 2 \times \text{promoted\_mutation\_count})$.
- **C-18 Idempotent Signal IDs**: Deterministic hashing of `(from_id, to_id, relationship)` to prevent duplicate relationship edges.

### 5. Approve the Directive

```bash
python nee.py intake-directive-approve --directive-id <id>
```

Or present the scoped proposal via interactive `ask_user_question` prompt. Approval authorizes the exact directive and proposed delta scope (`INV-INGESTION-ACTIVE-COMPLETION`).

### 6. Execute Intake Stages

The intake engine runs these stages in order:

| Stage | Name | Provider-free? |
|-------|------|---------------|
| U0 | Ingress / Source extraction | Yes |
| L1 | Identity resolution | Yes |
| L0 | Deterministic routing | Yes |
| C0.1 | Route assignment | Yes |
| C0.2 | Assertion disposition | Yes |
| C0.3 | Graph change set | Yes |
| X1 | External validation | Yes |
| X2 | HITL review (if required) | Yes |
| X3 | Graph write | Yes |
| L4 | Post-write audit | Yes |

When conditional ingestion reasoning is enabled, L2D runs after C0 and may select a provider call. If so, PA binds a minimized packet and L2 emits only CANDIDATE, QUARANTINE, REJECTED_NOOP, or HITL_REQUIRED.

### 7. Prepare, Evaluate, and Activate Release

Follow the release lifecycle:
1. Prepare: `python nee.py release-prepare --directive-id <id>`
2. Evaluate: `python nee.py retrieval-release-evaluate ...` (under pinned BGE-M3)
3. Activate: `python nee.py release-activate --release-id <id>`
4. Completion Verification: `python nee.py intake-completion-gate --directive-id <id>`
5. Backlog Recovery: `python tools/reconcile_ingestion_coverage.py --runtime-root <path> --sources-dir <path> --output-manifest <manifest.json>` followed by bounded recovery under authentic scoped operator authorization (`INV-INGESTION-ACTIVE-COMPLETION`).


### 8. Mandatory GraphDB Mutation Breakdown Table

Every completion report communicating a GraphDB mutation (or idempotent no-op) MUST present the granular delta breakdown table:

| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |
| :--- | :--- | :--- | :--- | :--- |
| `node` | ... | ... | `+0` | Explicit accounting of entities created, updated, or safely reconciled via identity resolution |
| `source` | ... | ... | `+N` | Ingested source record ID and title |
| `source_version` | ... | ... | `+N` | Source version bound to package digest |
| `extraction_run` | ... | ... | `+N` | Intake execution run ID |
| `evidence_item` | ... | ... | `+N` | Source-bound evidence items with locators |
| `assertion` | ... | ... | `+N` | Candidate/canonical assertions inserted or updated |
| `assertion_evidence` | ... | ... | `+N` | Provenance bindings between assertions and evidence items |
| `search_document` | ... | ... | `+N` | BM25 retrieval search records |
| `search_fts` | ... | ... | `+N` | FTS5 virtual table entries |
| `audit_event` | ... | ... | `+N` | Stage audit events |

When assertions are inserted or modified, follow with the itemized assertions table (`Assertion ID`, `Subject Node`, `Predicate`, `Literal / Claim Text`, `Status`), and explicitly record before/after `graph_sha256` transitions and idempotency verification.

## Zero-Op Ingestion

If every exact intake run proposes zero graph operations, do not manufacture an empty release:

```bash
python nee.py intake-completion-noop --directive-id <id>
```

## Related Files

- Intake engine: [`intake_engine.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/intake_engine.py)
- Transcript pipeline: [`transcript_pipeline.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_pipeline.py)
- Package preparation: [`prepare_evidence_envelope_intake.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/prepare_evidence_envelope_intake.py)
- Classification policy: [`pre-intake-classification-policy.json`](file:///Users/amitayer/Git/decision-intelligence-engine/policy/pre-intake-classification-policy.json)
