# NEE dual-pipeline contracts

Initiative: `NEE-KG-DUAL-PIPELINE-01`  
Wave: `1 of 6`  
Contract family: `1.0.0`

This directory freezes the machine-readable contracts shared by governed
knowledge intake and complex knowledge query workflows. Wave 1 defines data
shapes and validation only. It does not implement intake execution, graph
writes, embedding generation, graph/index activation, provider calls, or
runtime policy changes.

## Compatibility policy

- Governed artifacts fail closed on unknown fields (`additionalProperties` is
  `false`).
- Compatible vendor or future fields may be carried only in the explicit
  `extensions` object and must use a reverse-DNS key.
- A breaking field or enum change requires a new major contract version.
- A backward-compatible optional field requires a minor version.
- Documentation-only corrections require a patch version.
- Consumers must reject unsupported `schema_version` or `contract_id` values.

## Canonical digests

Every governed fixture carries `canonical_digest`. The digest is SHA-256 over
RFC-8259 JSON serialized with UTF-8, sorted keys, and separators `,` and `:`
after removing the top-level `canonical_digest` field. This avoids a recursive
digest and makes replay independent of whitespace and property order.

Schema files are bound through `contract_inventory.json`, which records their
byte-level SHA-256 digests. The validation tool verifies both forms.

## Contract inventory

| Contract | Purpose |
|---|---|
| `copilot-intake` | Structured meeting metadata, speaker summaries, and actions |
| `architecture-diagram-intake` | Diagram components, relationships, evidence regions, and ambiguities |
| `batch-admission-manifest` | Exact-package, homogeneous candidate-only batch admission boundary |
| `markdown-source-intake` | Exact Markdown source text with selected, source-bound sections |
| `docx-source-intake` | Exact DOCX bytes with deterministic, selected section evidence |
| `html-source-intake` | Exact HTML bytes with deterministic visible-text, heading, and table evidence |
| `pdf-source-intake` | Exact PDF bytes with deterministic, selected page-range evidence |
| `normalized-candidate-bundle` | Source-bound discrete candidates and evidence |
| `ingestion-plan` | Proposal-only L1 intake evaluation plan |
| `query-plan` | Explicit L1 plan for complex governed queries |
| `l2-execution-decision` | Content-addressed deterministic post-retrieval decision to skip or invoke external L2 |
| `route-decision` | One deterministic route per candidate or query |
| `entity-resolution` | Evidence, alternatives, scores, conflicts, and one outcome per mention |
| `ingestion-decision-explanation` | Hash-bound plain-English decisions, reviewer choices, and exact graph effects derived from the technical ingestion ledger |
| `assertion-disposition` | Governed approved, candidate, quarantine, or rejected/no-op outcome |
| `hitl-decision` | Tiered review item and immutable human decision |
| `graph-change-set` | Exact proposed/staged/applied graph operations and before-state bindings |
| `retrieval-record` | Semantically complete retrieval unit bound to graph and lane |
| `connected-evidence-map` | BM25-seeded, typed multi-hop paths and the bounded evidence set selected for reasoning |
| `otel-event-envelope` | Sensitive-content-safe trace/span metadata |
| `learning-event` | Durable correlated decision-ledger event |
| `learning-change-proposal` | Approval-required learning proposal; never a live policy mutation |

The query plan carries the governed L2 policy and call caps. The separate L2
execution decision binds that deterministic policy to the sealed post-retrieval
evidence packet before any external reasoning provider is initialized.

## Validation

The validator uses only the Python standard library and implements the JSON
Schema 2020-12 keywords used by this contract family. It validates schema
identity, canonical digests, strict unknown-field behavior, cross-file
references, privacy constraints, correlation rules, lane/disposition rules,
and positive/negative fixtures.

Run from the repository root:

```powershell
python tools/validate_dual_pipeline_contracts.py
python tests/test_dual_pipeline_wave1.py
```

No command in Wave 1 opens the graph database for write, loads an embedding
model, changes configuration, or calls a provider.
