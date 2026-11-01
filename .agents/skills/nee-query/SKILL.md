---
name: nee-query
description: NEE knowledge graph query operations, query routing, hybrid retrieval (BM25 + semantic), semantic cache, and decision queries. Use for graph lookups.
---

# NEE Query Pipeline

This skill guides query operations against the NEE knowledge graph.

## User-facing answer

Enforce the Two-Part Seamless Query & Executive Notes contract (`INV-QUERY-CLEAN-PROSE`):

1. **Easy-to-Read External-Ready Executive Notes (No Dense Prose, Footnotes, or Acronyms)**:
   - Format answers as clean, scannable executive notes ready to share with external parties, with one clear takeaway per bullet rather than dense paragraphs.
   - **Zero Parenthetical Stuffing & Zero Unexplained Acronyms**: Do not pack examples, lists, or caveats inside parentheses; use clean em-dash clauses or separate sub-bullets. Expand technical acronyms into plain English so notes read naturally at a glance.
   - **Uncapped Query Completeness**: Knowledge queries (`nee-query`) are strictly exempt from the 120/300-word engineering turn completion ceiling (`INV-OUTPUT-MAX-WORDS`). Never truncate or artificially compress a query response or its lineage table unless the user explicitly specifies a length limit in the prompt.
   - **Zero Inline Footnote & Zero Database Token Invariant**: Narrative text must be immediately copy-ready for external parties. Strictly ban inline footnote markers (`[1]`, `[2]`), raw assertion hashes (`ast_ing_*`, `asrt_claim_*`, `assertion-*`), `snake_case` node identifiers (`workstream_*`, `sow_*`, `model_*`), and inline database file URLs (`file:///.../graph.sqlite`) from note text.

2. **Bottom Evidence & Graph Lineage Footer (Consolidated Traversal Summary)**:
   - Append a dedicated `### Evidence & Graph Lineage` markdown section at the very bottom.
   - **Graph Traversal Telemetry Callout**: Emit a single compact summary line reporting total traversal metrics: `**Graph Traversal Summary:** <N> assertions evaluated across <M> entities and <K> distinct sources (<C> overall confidence).`
   - **Deduplicated Source & Domain Coverage Table**: Present a compact table (`| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |`) rolling up verified evidence by source and domain rather than repeating 30+ sentence-level rows.
   - Never cite superseded or unpromoted candidate rows as `CONFIRMED` or `HIGH`.
   - Decouple all technical plumbing and database identifiers out of narrative notes into this footer.

Do not expose the agent scratchpad, model/provider metadata, tool logs, or process narration. Distinguish role requirements supplied in an image from verified evidence about candidates; do not claim delivery maturity or availability without supporting records. An ordinary query answer uses readable executive notes rather than a Copilot code fence.

## When to use

- User asks a question about NEE knowledge (people, projects, relationships, status)
- User requests analysis, synthesis, gap identification, or decision support
- User asks "who", "what", "when", "where", "how", or "why" questions
- User requests an executive briefing or decision evaluation

## Governed CLI Query

The query router ([`dual_pipeline.query_router`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/query_router.py)) determines the appropriate query path:

```bash
python nee.py ask --question "your question here" --materiality medium --query-type current_state
```

### Route Types

| Route | Description | Example |
|-------|------------|---------|
| `exact_lookup` | Precise entity or fact retrieval | "What is Sarah's title?" |
| `current_state` | Current-state snapshot | "What projects is the team working on?" |
| `current_state_corroboration` | Corroborating assertions across sources | "Verify project Chakra's delivery commitments" |
| `relationship_explanation` | Relationship and network queries | "Who reports to Sarah?" |
| `gap_risk_inconsistency` | Gap and risk analysis | "What delivery risks exist in Q3?" |
| `opportunity_nba` | Next-best-action recommendations | "What should we prioritize next?" |
| `cross_workstream_synthesis` | Cross-domain synthesis | "Summarize account health across workstreams" |
| `candidate_promotion_review` | Candidate promotion evaluation | "Is this candidate assertion ready for promotion?" |

### Prompt Templates

Templates are organized under [`config/prompts/`](file:///Users/amitayer/Git/decision-intelligence-engine/config/prompts/):

| Template Dir | Route | Purpose |
|-------------|-------|---------|
| `config/prompts/exact_lookup/` | `exact_lookup` | Precise retrieval prompts |
| `config/prompts/current_state/` | `current_state` | State snapshot prompts |
| `config/prompts/relationship/` | `relationship` | Network query prompts |
| `config/prompts/gap_risk/` | `gap_risk` | Risk analysis prompts |
| `config/prompts/opportunity_nba/` | `opportunity_nba` | NBA prompts |
| `config/prompts/synthesis/` | `synthesis` | Cross-domain synthesis |
| `config/prompts/challenger/` | `challenger` | Contrarian analysis |
| `config/prompts/promotion_review/` | `promotion_review` | Promotion eval |
| `config/prompts/shared/` | all | Shared prompt components |

## Retrieval Architecture

The query pipeline uses hybrid retrieval:

1. **BM25** — keyword-based retrieval from the graph/index ([`graph_retrieval.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/graph_retrieval.py))
2. **Semantic** — embedding-based retrieval via `HybridRetriever` ([`index.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/index.py))
3. **Semantic Cache** — cached embeddings for repeat/similar queries ([`semantic_cache.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/semantic_cache.py))

### Checking Runtime State

```bash
python nee.py runtime-evidence inventory
python nee.py active-pair
```

### L1 Query Evaluation

```bash
python nee.py l1-query-evaluate --query "your question"
```

## Decision Intelligence Queries

For strategic decision evaluation, use the decision engine or HTTP service:

```bash
# Direct CLI invocation
python nee.py decision-query --query "your strategic question"
```

*(For full coverage of the Decision Intelligence HTTP daemon, typed decision framing, option evaluation, and strategic reasoning waves, see the [`nee-decision-intelligence`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-decision-intelligence/SKILL.md) skill).*

## Key Constraints

- **Anti-Fallback & Strict GraphDB Sourcing (`INV-GROUNDING-NO-OUTPUT-FILESYSTEM-BYPASS`)**: **NEVER** use filesystem commands (`ls`, `tree`, `stat`, `cat`, `view_file`, `grep`, `find`, `rg`) or inline directory scans on `output/` to discover historical meeting transcripts or investigate query drift. All knowledge lookups and RCA **must** route strictly through GraphDB traversal via `nee ask`, `query_router.py`, or `ReadOnlyGraphSnapshot`. The system enforces this via `ProhibitedCommandGuard` and exit code 2.
- **Mandatory Entity Traversal (`INV-QUERY-ENTITY-ADVISORY-TRAVERSAL`)**: Requests for executive agendas, talking points, or workstream status must traverse GraphDB canonical assertions before emitting delivery payloads, rejecting `:memory:` fallbacks and unanchored claims.
- **Fail-Closed Temporal Source Existence (`INV-QUERY-TEMPORAL-SOURCE-EXISTENCE`)**: When a query targets a specific date/time meeting (`today`, `MM/DD/YYYY`, `3:30 PM ET`), verify via `verify_temporal_source_existence()` that a matching `source.source_date` exists in `graph.sqlite`. If absent, halt and explicitly state **"`<Meeting>` is not in the Knowledge Graph"** while inspecting `.local/dual_pipeline/dlq/` and `.worktrees/` (`INV-QUERY-UNPROMOTED-WORKTREE-DLQ-CHECK`). Never silently substitute prior meetings.
- **Deliverable Alias Binding (`INV-QUERY-DELIVERABLE-ALIAS-BINDING`)**: Operator references to `"output(s) 1/2/3"` bind strictly to pipeline artifacts `01_*_hitl_resolved_transcript.md`, `02_*_executive_intelligence_report.md`, and `03_*_canonical_signal_projection.json`, never prior chat replies.
- **Read-only by default**: Queries never mutate the graph.
- **Evidence-bound claims**: All retrieval claims must be backed by `runtime_evidence inventory` output.
- **L2 provider calls**: Only when L2D decision selects them; deterministic fallbacks must remain preserved if provider call fails.
- **No synthetic embeddings**: All embeddings must use real, qualified models (e.g. BGE-M3).

## Governed Answer Ledger & Closed-Loop Learning

Query answers are captured in a durable, append-only SQLite WAL store with cryptographic RFC 8785 Merkle hash chains:

1. **Answer Recording**:
   - Every completed query writes an `answer_record:1.0.0` artifact to [`dual_pipeline/answer_ledger.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/answer_ledger.py) (`pipeline="query"`).
   - Preserves cited assertion IDs, graph SHA, query route, and query text.
   - **Independence Invariant**: The query and copilot pipelines write to the same ledger schema but never read or alter each other's execution state.

2. **Operator Feedback & Evaluation**:
   - Capture feedback via governed CLI:
     ```bash
     python nee.py answer-feedback --answer-id <id> --useful true --comment "Direct answer with citations"
     ```
   - Evaluate recorded answers against groundedness, durability, and usefulness metrics:
     ```bash
     python nee.py answer-eval --pipeline query --limit 50
     ```

3. **Closed-Loop Learning via UWG**:
   - Low retrieval accuracy or ungrounded assertions cluster into failure cohorts via `python tools/govern_learning_proposals.py answer-scan`.
   - `query_retrieval` proposals map to hybrid retrieval weights and **must pass all 11 BGE-M3 gates** before activation.
   - Commits route exclusively through the Universal Write Gateway (`learning_parameter_commit`) guaranteeing $+0$ graph mutations.

## Related Files

- Query engine: [`query.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/query.py)
- Query router: [`query_router.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/query_router.py)
- Hybrid index: [`index.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/index.py)
- Semantic cache: [`semantic_cache.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/semantic_cache.py)
- Decision API: [`decision_api.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/decision_api.py)
- Answer ledger: [`answer_ledger.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/answer_ledger.py)
- Answer evaluation: [`dual_pipeline/evals/answer_eval.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/evals/answer_eval.py)
- Learning loop core: [`dual_pipeline/learning_loop_core/`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/learning_loop_core/)
