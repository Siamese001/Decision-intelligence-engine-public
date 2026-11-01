#!/usr/bin/env python3
"""Adversarial Debate to Harden LLM Judge & QA Gates.

Single-Turn Dialectical Reasoning Rollout under OpenAI gpt-5.6-sol (reasoning_effort="medium"):
Executes 5 adversarial personas (Executive Sponsor, Graph Ontologist, Forensic Auditor,
Technical Architect, Pipeline Orchestrator) and a Chief Architect synthesis in one unified
high-coherence rollout with parameter immunity (temperature omitted).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from config.credentials import CredentialManager

# Add workspace root to sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

CredentialManager.ensure_ssot_env_loaded()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("adversarial_debate")

RUBRIC_PATH = WORKSPACE_ROOT / "governance" / "decision_intelligence" / "TRANSCRIPT_SYNTHESIS_EVALUATION_RUBRIC_V1.md"
PIPELINE_PATH = WORKSPACE_ROOT / "scripts" / "run_project_atlas_pipeline.py"
OUTPUT_PATH = WORKSPACE_ROOT / "output" / "project_atlas" / "08-28-2026" / "hardening_recommendations.md"

def load_context() -> str:
    rubric_content = RUBRIC_PATH.read_text(encoding="utf-8") if RUBRIC_PATH.exists() else "# Rubric not found"

    # Extract just the QA gate logic (audit_output_01, 02, 03) from pipeline
    if PIPELINE_PATH.exists():
        pipeline_content = PIPELINE_PATH.read_text(encoding="utf-8")
        lines = pipeline_content.splitlines()
        qa_lines = []
        in_qa = False
        for line in lines:
            if line.startswith("def audit_output_01("):
                in_qa = True
            if in_qa:
                qa_lines.append(line)
            if in_qa and line.startswith("def run_llm_judge("):
                break
        qa_code = "\n".join(qa_lines)
    else:
        qa_code = "# QA gate logic not found"

    return f"=== RUBRIC ===\n{rubric_content}\n\n=== QA GATE LOGIC ===\n{qa_code}\n"


UNIFIED_DIALECTICAL_PROMPT = """You are the Chief Architect and Deliberation Director orchestrating an exhaustive adversarial critique and synthesis across 5 specialized perspectives to harden our LLM Judge Rubric and QA Gate Python logic.

You must internally simulate an adversarial debate across five distinct expert lenses, and then synthesize their critiques into an actionable, definitive hardening plan:

### Perspective 1: The Executive Sponsor Lens
Focus: Output 2 (Executive Strategic Brief).
Despise IT fluff, meeting minutes, and passive verbs. Demand clear ROI, business altitude, and actionable scoping.
Critique: Do the rubric and QA gates truly guarantee that Output 2 answers 'what does this mean for the business and how do we execute it?' What strict rules must be added?

### Perspective 2: The Graph Ontologist Lens
Focus: Output 3 (Canonical Signal Projection JSON).
Demand strict adherence to the W2 Semantic Spine, valid directional predicates, zero orphan nodes, and proper topological bridging.
Critique: Do the rubric and QA gates guarantee Output 3 is structurally pristine and queryable? What programmatic gates or rubric dimensions must be hardened?

### Perspective 3: The Forensic Auditor Lens
Focus: Hallucination Prevention and Epistemic Modality across all outputs.
Demand 100% grounding, exact verbatim locators for evidence, and strict separation between proposed ideas vs ratified decisions.
Critique: Do the rubric and QA gates strictly prevent unauthorized facts from slipping in? What forensic limits must be added?

### Perspective 4: The Technical Architect Lens
Focus: Technical Substance and Architecture Fidelity in Output 2.
Demand that systems (e.g. Unifier, SSO, P6) are explicitly named, data flows are accurate, and technical blockers are specified.
Critique: Do they prevent generic filler? What technical gating constraints must be added?

### Perspective 5: The Pipeline Orchestrator Lens
Focus: Programmatic Determinism and JSON Schema Validation.
Demand rigid Python assertions in QA gates, graceful edge-case handling, schema compliance, and temporal decay enforcement.
Critique: Are assertions too soft? What deterministic regex, parsing, or type checks must be added to audit functions?

### Chief Architect Final Synthesis & Actionable Hardening Plan
Synthesize these 5 perspectives into a definitive Markdown report:
1. **Ultimate Questions:** What ultimately defines success for Output 2 and Output 3?
2. **Rubric Updates:** Specific additions/changes required for `TRANSCRIPT_SYNTHESIS_EVALUATION_RUBRIC_V1.md`. Focus on uncompromising evaluation metrics.
3. **QA Gate Updates:** Specific Python assertions and deterministic checks required for `scripts/run_project_atlas_pipeline.py`.
4. **Dialectical Consensus & Calibrated Confidence Scores:** Summary of agreed priorities and calibrated confidence ratings.
"""


def _generate_mock_synthesis(model_id: str, reasoning_effort: str) -> str:
    return f"""# Dialectical Hardening Synthesis: LLM Judge Rubric & QA Gates
*Model:* `{model_id}` | *Reasoning Effort:* `{reasoning_effort}` | *Mode:* Single-Turn Dialectical Rollout

## Perspective Summaries (5-Lens Adversarial Debate)
1. **Executive Sponsor:** Output 2 must eliminate passive verbs (e.g. 'consider', 'discuss'), enforce executive altitude, and provide exact ROI and assigned milestone owners.
2. **Graph Ontologist:** Output 3 must validate all entity prefixes against W2 Semantic Spine, forbid orphan nodes, and verify bidirectional relationship coherence.
3. **Forensic Auditor:** Strict quotation verification; locators must bind to verifiable transcript offsets; hallucinated decisions fail closed.
4. **Technical Architect:** Require concrete system integration topology (SSO, ERP, Unifier) rather than generic abstractions.
5. **Pipeline Orchestrator:** Programmatic regex assertion on `MM/DD/YYYY` dates, table columns, and JSON schema fail-closed checks.

## 1. Ultimate Questions Defining Success
- **Output 2:** Does this briefing deliver executive-grade clarity with zero passive action verbs and assigned single-threaded owners?
- **Output 3:** Does this projection represent a 100% grounded, topologically valid graph delta conforming to the W2 Semantic Spine?

## 2. Rubric Updates (`TRANSCRIPT_SYNTHESIS_EVALUATION_RUBRIC_V1.md`)
- Add non-compensating Gate for Passive Action Verbs (score = 0.0 if passive verbs detected in next steps table).
- Add Gate for Verbatim Locator Grounding (100% locator coverage mandatory).
- Add Gate for Topological Bridge Integrity (zero orphan nodes permitted).

## 3. QA Gate Updates (`scripts/run_project_atlas_pipeline.py`)
- Assert regex `^\\d{{2}}/\\d{{2}}/\\d{{4}}$` on all date fields.
- Assert non-empty `owner` string without placeholders ('TBD', 'TBC', 'Unassigned').
- Enforce schema validation against `contracts/dual_pipeline/evidence-envelope-intake.schema.json`.

## 4. Dialectical Consensus & Calibrated Confidence Scores
- **Option A (Adopt Unified Single-Turn Dialectical Frame):** Confidence 92% (HIGH) — (Recommended) High cohesion, zero broker queue latency.
- **Option B (Maintain 6-Call Multi-Turn Agent Loop):** Confidence 45% (LOW) — Fragile, high token consumption, slow wall-clock latency.
- **Ambiguity Margin:** $\\Delta = 0.92 - 0.45 = 0.47 \\ge 0.25$ -> **AUTONOMOUS_APPROVED**.
"""


def run_adversarial_debate(
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    dry_run: bool = False,
    client: Optional[Any] = None,
    output_path: Optional[Path] = None,
) -> str:
    """Executes single-turn dialectical reasoning debate."""
    from dual_pipeline.model_registry import ModelRegistry, ModelTier
    def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FAST_ROUTING)
    eff_model = model or def_m
    eff_effort = reasoning_effort or def_e or "medium"

    output_file = output_path or OUTPUT_PATH
    context = load_context()

    api_key = CredentialManager.get_openai_api_key(allow_mock=dry_run)
    if not dry_run and not api_key:
        raise RuntimeError(
            "Zero mocks invariant violated: Live OpenAI API key required for adversarial debate, "
            "but none was found in environment. Explicitly pass dry_run=True for offline mock mode."
        )

    if dry_run:
        logger.info(f"Executing deterministic dialectical rollout (dry_run=True) under {eff_model} ({eff_effort})")
        final_md = _generate_mock_synthesis(eff_model, eff_effort)
    else:
        logger.info(f"Running Single-Turn Dialectical Reasoning Rollout on {eff_model} ({eff_effort})")
        if client is None:
            client, _ = CredentialManager.get_openai_client(allow_mock=False)

        # Parameter immunity: Omit temperature for reasoning models
        response = client.chat.completions.create(
            model=eff_model,
            reasoning_effort=eff_effort,
            messages=[
                {"role": "system", "content": UNIFIED_DIALECTICAL_PROMPT},
                {"role": "user", "content": f"Here is the context of the artifacts:\n\n{context}"},
            ],
        )
        final_md = response.choices[0].message.content or ""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(final_md, encoding="utf-8")
    logger.info(f"Hardening recommendations written to {output_file}")
    return final_md


def main() -> None:
    from dual_pipeline.model_registry import ModelRegistry, ModelTier
    def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FAST_ROUTING)

    parser = argparse.ArgumentParser(description=f"Run Adversarial Debate under {def_m}")
    parser.add_argument("--model", default=def_m, help=f"Model ID (default: {def_m})")
    parser.add_argument("--reasoning-effort", default=def_e or "medium", help=f"Reasoning effort (default: {def_e or 'medium'})")
    parser.add_argument("--dry-run", action="store_true", help="Execute deterministic mock rollout without live API calls")
    parser.add_argument("--output", type=Path, default=None, help="Custom output path")
    args = parser.parse_args()

    run_adversarial_debate(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        dry_run=args.dry_run,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
