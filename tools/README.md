# NEE gate utility

## Copilot intake draft finalization

`prepare_copilot_intake.py` turns one Copilot-generated meeting draft into one
contract-valid `copilot-intake` artifact. It binds the exact preserved source
export, operator-authorized classification and audiences, and controlled
archive location; computes both required SHA-256 values; validates the
closed-world contract; validates every source locator against the preserved
export; and refuses to overwrite an existing output.

```powershell
.\.venv\Scripts\python.exe .\tools\prepare_copilot_intake.py `
  --draft "C:\NEE\input\meeting-draft.json" `
  --source "C:\NEE\archive\meeting-evidence.txt" `
  --source-location "controlled/archive/meeting-evidence.txt" `
  --classification confidential `
  --audience internal_consulting `
  --audience nee-engineering `
  --output "C:\NEE\input\meeting-final.json"
```

The source prompt and its current adapter limitations are documented in
`documentation/INGESTION_GRAPHRAG_PROMPT_COPILOT.md`.

When required metadata is absent, add `--hitl-output` to create a narrow,
non-mutating request. Both meeting finalizers use
`policy/pre-intake-classification-policy.json` to include a deterministic,
provider-free initial classification and audience proposal, its source-bound
digest, and the signal categories supporting the recommendation. The proposal
is advisory: the finalizer still creates no package until an operator approves
or overrides the proposed values.

```powershell
.\.venv\Scripts\python.exe .\tools\prepare_copilot_intake.py `
  --draft "C:\NEE\input\meeting-draft.json" `
  --source "C:\NEE\archive\meeting-evidence.txt" `
  --output "C:\NEE\output\meeting-package.json" `
  --hitl-output "C:\NEE\output\meeting-pre-intake-hitl.json"
```

## Google Meet / Gemini notes pre-intake HITL and finalization

When operator-controlled metadata is absent, produce a narrow HITL request
instead of guessing a classification or audience:

```powershell
.\.venv\Scripts\python.exe .\tools\prepare_google_meet_gemini_intake.py `
  --draft "C:\NEE\input\google-meet-draft.json" `
  --source "C:\NEE\archive\gemini-notes.txt" `
  --output "C:\NEE\output\google-meet-package.json" `
  --hitl-output "C:\NEE\output\google-meet-pre-intake-hitl.json"
```

Both meeting finalizers emit `HITL_REQUIRED` and create no package until the
operator supplies only the missing source location, classification, and
audience scope. A declared classification is never lowered, declared audiences
are never broadened, and unmapped audience scope remains an operator decision.
Candidate staging does not authorize a graph mutation; that requires a separate
exact-scope release approval.

To inspect the same proposal independently of a finalizer:

```powershell
.\.venv\Scripts\python.exe .\tools\classify_ingestion_source.py `
  --draft "C:\NEE\input\meeting-draft.json" `
  --source "C:\NEE\archive\meeting-evidence.txt" `
  --output "C:\NEE\output\pre-intake-classification.json"
```

The output is create-once, contains no matched secret or personal-data values,
and grants no package, directive, or graph-mutation authority.

## BGE-M3 production preflight

`run_bge_m3_preflight.py` is the standalone equivalent of
`nee production-preflight`. It writes a sealed JSON receipt covering the pinned
Python dependencies, `pip check`, CPU execution, exact BGE-M3 manifest, offline hybrid
encode, approved seed graph, clean runtime/report paths, and tracked-secret
scan. Add `--strict-production` plus all authorization and qualification input
arguments to require resource/model-bound production authorization. Without
that strict authorization, a passing receipt remains infrastructure-only and
sets `production_execution_ready` to `false`.

The shipped `requirements/production-trust-policy.json` is deliberately
`UNCONFIGURED`. Strict mode remains blocked until the deployment
identity/security owner pins the exact issuer, audience, and public-key SHA-256
and changes the reviewed policy status to `CONFIGURED`. Caller-supplied trust
values alone are rejected.

```powershell
$runtimePython = (Resolve-Path ".\.local\venvs\runtime-cu128\Scripts\python.exe").Path
& $runtimePython .\tools\run_bge_m3_preflight.py `
  --runtime-root "C:\NEE\dual-pipeline-runtime" `
  --graph ".\graph\nee_graph.sqlite" `
  --model-path "C:\NEE\models\bge-m3\<revision>" `
  --model-revision "<40-hex-revision>" `
  --model-manifest "C:\NEE\models\bge-m3\bge-m3-manifest.json" `
  --report "C:\NEE\evidence\bge-m3-preflight.json"
```

Use an isolated `runtime-cu128` environment whose closure exactly matches the
runtime lock. A development `.venv` containing extra tooling is intentionally
rejected by this preflight.

Provision the exact official model snapshot first by following
`documentation/BGE_M3_RUNTIME_QUALIFICATION.md`. Expected identity is revision
`5617a9f61b028005a4858fdac845db406aefb181`, 30 files, 4,587,317,404 bytes, and
manifest digest
`a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e`.

## Production and release-action authorization requests

`build_production_qualification_request.py` emits the exact package, decision,
normalized-question, release-approval, and model-manifest bindings that the
deployment identity boundary must sign. It does not create a JWT. Production
intake, review, query, and release preparation enforce those same resources at
the point of use.

After a release is prepared, build a separate action request for each normal
transition:

```powershell
.\.venv\Scripts\python.exe .\tools\build_release_action_request.py `
  --receipt "C:\NEE\runtime\releases\release-<generation>.json" `
  --action activate --runtime-root "C:\NEE\runtime" `
  --output "C:\NEE\auth\activate-request.json"

.\.venv\Scripts\python.exe .\tools\build_release_action_request.py `
  --receipt "C:\NEE\runtime\releases\release-<generation>.json" `
  --action rollback --output "C:\NEE\auth\rollback-request.json"
```

The identity boundary issues distinct short-lived JWTs whose
`nee_authorized_resources` exactly match each request. Request and receipt
builders use create-once output semantics and refuse overwrite.

For a BGE-M3 activation, the action-request builder reads the sealed retrieval
release evaluation from the runtime and refuses to create a request until its
receipt, baseline/candidate pair, policy, and PASS gates bind to the prepared
release. Its digest is added as `retrieval_release_evaluation` to the exact
activation resource set. Rollback does not require that evaluation.

`nee_pre_mutation_gate.py` is the fail-closed control utility activated by
`PD-45-PRE-MUTATION-GRAPH-CARD-GATES` and extended by
`PD-47.1-THREE-TABLE-INLINE-OUTPUT-GATE` and
`PD-47.1.1-ZERO-DELTA-NA-RATIONALE`.

## Control sequence

1. Prepare Gate G1 and Gate C1 proposal JSON outside the baseline package.
2. Put machine-readable graph-delta rows and the fixed ten graph-metric rows in
   the graph proposal; put the complete affected-card rows in the card proposal.
3. Generate the G1 decision surface with `render-inline-output`.
4. Validate that the decision surface contains exactly the three required tables
   and no additional inline material.
5. Obtain an approval record bound to the baseline, proposal digests, and exact
   G1 inline-output hash.
6. Validate approval before the first canonical write.
7. Implement on a copy of the baseline and verify changed-file scope.
8. Put the actual three-table rows in the Gate R1 conformance record and generate
   the R1 decision surface.
9. Package only through the gated `package` command. Under policy 47.1.1, packaging
   requires both exact inline-output files and their bound hashes.

## Exact inline decision surface

The generated output contains only these sections, in this order:

1. `Graph Delta and Retrieval Impact`
2. `Mandatory Graph Metrics`
3. `Card Additions and Retrieval Impact`

No narrative, extra table, code block, digest list, approval command, source
assessment, or reconciliation detail is permitted inside the gate body. Those
items are filed separately.

The graph table must reconcile to component counts and include at least sources,
nodes, aliases, edges, claims, validations, and node-to-card mappings. Every
all-zero scalar or vector delta renders `NA` in the retrieval-rationale column.
Any non-zero delta using `NA`, `N/A`, blank, or generic rationale fails closed. The metric
table must contain the ten fixed metrics in the approved order and threshold.
The card table must reconcile to every affected card and route, or contain the
single controlled no-change row.

## Commands

```bash
python tools/nee_pre_mutation_gate.py digest \
  governance/proposals/G1_EXAMPLE.json

python tools/nee_pre_mutation_gate.py render-inline-output \
  --stage G1 \
  --graph-proposal governance/proposals/G1_EXAMPLE.json \
  --card-proposal governance/proposals/C1_EXAMPLE.json \
  --output /review/G1_INLINE_GATE_OUTPUT.md

python tools/nee_pre_mutation_gate.py validate-inline-output \
  --stage G1 \
  --graph-proposal governance/proposals/G1_EXAMPLE.json \
  --card-proposal governance/proposals/C1_EXAMPLE.json \
  --inline-output /review/G1_INLINE_GATE_OUTPUT.md

python tools/nee_pre_mutation_gate.py validate-approval \
  --approval approvals/APPROVAL.json \
  --baseline-zip /path/to/baseline.zip \
  --graph-proposal governance/proposals/G1_EXAMPLE.json \
  --card-proposal governance/proposals/C1_EXAMPLE.json \
  --g1-inline-output /review/G1_INLINE_GATE_OUTPUT.md

python tools/nee_pre_mutation_gate.py verify-scope \
  --approval approvals/APPROVAL.json \
  --baseline-dir /path/to/baseline \
  --work-dir /path/to/work

python tools/nee_pre_mutation_gate.py render-inline-output \
  --stage R1 \
  --conformance governance/IMPLEMENTATION_CONFORMANCE.json \
  --output /review/R1_INLINE_GATE_OUTPUT.md

python tools/nee_pre_mutation_gate.py package \
  --approval approvals/APPROVAL.json \
  --baseline-zip /path/to/baseline.zip \
  --baseline-dir /path/to/baseline \
  --graph-proposal governance/proposals/G1_EXAMPLE.json \
  --card-proposal governance/proposals/C1_EXAMPLE.json \
  --conformance governance/IMPLEMENTATION_CONFORMANCE.json \
  --g1-inline-output /review/G1_INLINE_GATE_OUTPUT.md \
  --r1-inline-output /review/R1_INLINE_GATE_OUTPUT.md \
  --source-dir /path/to/successor \
  --output-zip /path/to/successor.zip
```

## Fail-closed behavior

The utility exits with status `2` when, among other failures:

- a proposal, approval, conformance, or required inline-output file is missing;
- the baseline ZIP or a proposal digest differs from the approval;
- the inline body is not the exact generated three-table output;
- a required graph component or affected card is omitted;
- graph count arithmetic or proposal reconciliation fails;
- an all-zero graph delta does not render `NA`, or a non-zero delta uses `NA`, `N/A`, blank, or generic rationale;
- any fixed metric is missing, reordered, renamed, uses a changed threshold, or
  fails without a pre-approved exception;
- the G1 inline output is not approval-bound;
- the R1 inline output is not conformance-bound;
- a file outside the approved path list changes;
- the graph or a non-policy card changes despite a zero-delta approval;
- implementation drift or unapproved semantic changes are reported; or
- the packaging gate has not passed.

It deliberately does not infer that two different deltas or two visually similar
outputs are “close enough.” The generated Markdown must match byte-for-byte after
line-ending normalization.

## Tests

```bash
python -m unittest -v tests/test_nee_pre_mutation_gate.py
```

The test suite covers approval and scope controls plus the three-table output,
metric-row, arithmetic, affected-card, approval-binding, and fail-closed paths.

## Semantic cache L0 routing stress test

`stress_test_semantic_cache_l0.py` provides high-concurrency stress testing and
adversarial validation for the BGE-M3 semantic cache in L0 routing:

```bash
NEE_RUN_BGE_TESTS=1 python tools/stress_test_semantic_cache_l0.py \
  --concurrency 16 \
  --burst-queries 300 \
  --depth 60 \
  --output scratch/semantic_cache_l0_stress_results.json
```

It validates five key operational vectors:
1. High-concurrency reader/writer lock contention under SQLite WAL mode (zero lock errors).
2. Cache depth scaling and burst lookup throughput (p50/p95/p99 latency tracking).
3. L0 multi-tenant route context and security namespace isolation (zero cross-tenant leaks).
4. Adversarial edge cases and fail-closed resilience (boundary vectors, payload corruption, non-finite inputs).
5. L0 telemetry route decision recording and learning event chain validation.
