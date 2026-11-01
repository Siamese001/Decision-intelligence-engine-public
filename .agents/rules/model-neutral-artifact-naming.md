---
trigger: always_on
---

# Model-Agnostic Artifact Naming & LLM Neutrality Rules

Governs file naming, report generation, artifact emission, and documentation across all agents, sub-agents, and pipelines. Grounded in Architectural Invariant 7.

## 1. Core Principles
1. **Domain-Bound Artifact Naming**: Files named strictly by domain function, architectural scope, or lifecycle role (e.g. `reports/wave1_alignment_receipt.json`).
2. **Strict LLM Model Name Prohibition in Filesystem Paths**: Prohibited tokens in filenames/paths: `gpt*`, `astra`, `luna`, `sol`, `terra`, `claude*`, `gemini*`, `llama*`, `haiku`, `sonnet`, `o1`, `o3`.
3. **Runtime Telemetry Isolation**: Model identities belong exclusively in structured audit metadata (`model_id`), never in filesystem paths.

## 2. Universal Agent System Prompt Directive
```text
CRITICAL INVARIANT: MODEL-AGNOSTIC FILE & ARTIFACT NAMING
You MUST name all files, reports, specifications, architectural documents,
evaluation receipts, and persistent artifacts strictly according to their DOMAIN
FUNCTION, ARCHITECTURAL SCOPE, or GOVERNANCE LIFECYCLE ROLE.
Under NO circumstances shall you prepend, append, or embed LLM model names,
provider identifiers, or reasoning tier tags into filenames or directory paths.
The generating model is ephemeral runtime telemetry belonging inside metadata.
```
