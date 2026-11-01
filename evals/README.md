# Dual-pipeline evaluations

Evaluation suites are sealed `eval-suite` artifacts. They bind the exact seed
graph and every ingestion package used by a case, then run each case in its own
isolated runtime under the supplied evaluation root.

Run the deterministic vertical slice from the repository root:

```powershell
nee eval-run --suite evals/suites/dual-deterministic-e1.json --runtime-root .local/evals --graph graph/nee_graph.sqlite
```

The command writes one immutable result and a concise report under
`<runtime-root>/evaluations/<evaluation-id>/`. It never changes the seed graph.
The Copilot ingestion case does create and activate a graph/index generation,
but only inside its isolated case runtime.

`eval-compare` accepts only sealed results bound to the same suite digest,
graph SHA-256, and pipeline. It never rewrites a baseline.

Run the connected graph retrieval suite:

```powershell
nee eval-run `
  --suite evals/suites/graph-retrieval-e1.json `
  --runtime-root .local/graph-retrieval-evals `
  --graph graph/nee_graph.sqlite
```

This network-free suite evaluates BM25-assisted fusion and real typed
multi-hop traversal. It checks required and forbidden records, deep path
assertions, excluded navigation relationships, branch/hub control, opportunity
and candidate context, supporting-evidence completeness, stage-ledger
completion, zero provider calls, and zero graph mutation. The exact-lookup
case is the control: BM25 must run while graph traversal remains off.

## Automatic gates

Evaluation now runs at two automatic boundaries:

| Boundary | Evaluation | Evidence location | Failure behavior |
| --- | --- | --- | --- |
| Pre-push | Sealed, network-free `candidate-bounded` workflow suite | `.local/automatic-evals/workflow-evaluations/<evaluation-id>/` | Push stops when any hard gate fails or measurement is invalid |
| BGE-M3 release | Receipt-bound active-versus-candidate E3 retrieval suite | `<runtime-root>/retrieval_evaluations/` | Activation and completion stop; the active pair remains unchanged |

The pre-commit hook remains a fast static/contract gate. The pre-push hook
executes the real deterministic workflow evaluator, then the retrieval
evaluator tests and the automatic batch-release lifecycle test. Its workflow
result contains counters and digests only, makes zero external provider calls,
and performs zero graph mutations.

Run the same automatic pre-push evaluation directly:

```powershell
.\.venv\Scripts\python.exe tools\run_automatic_evaluation_gates.py
```

An automatic BGE-M3 release suite is stored under
`<runtime-root>/retrieval_evaluation_suites/automatic/`. A caller may still
supply a reviewed explicit suite; the pipeline copies it under
`<runtime-root>/retrieval_evaluation_suites/explicit/` before evaluation.
Passing results occupy the canonical evaluation path. Failed attempts are
retained separately under `<runtime-root>/retrieval_evaluations/failed/` so
they remain auditable without making a corrected retry impossible.
