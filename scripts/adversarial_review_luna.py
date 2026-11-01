import sys
import traceback
from pathlib import Path

from config.credentials import CredentialManager

ROOT = Path(__file__).resolve().parent.parent
CredentialManager.ensure_ssot_env_loaded()

client, _ = CredentialManager.get_openai_client(allow_mock=False)

system_prompt = """You are a Principal Enterprise Systems Architect, Chief Governance Officer, and Strategic Technology Executive conducting an unsparing, rigorous adversarial review of three architectural remediation pathways for a Decision Intelligence Engine and Knowledge Graph.

CONTEXT:
The system processes executive meeting recordings and transcripts into three primary deliverables:
- Output 01: Forensic attributed transcript
- Output 02: Email-ready executive brief / recap
- Output 03: Canonical signal projection JSON (schema 5.7) feeding the enterprise GraphDB (CANONICAL_GRAPH.db)

GOVERNANCE CONTRACTS:
1. Career Leverage Firewall (Rule C-19 / .agents/rules/career-leverage-firewall.md): Operator's private career strategy (executive compensation band), personal networking wedges, private political power maps, and career advancement vectors are STRICTLY PROHIBITED from Output 01, Output 02, and Output 03, and must route exclusively to the encrypted local INTERNAL_ADVISORY shadow graph.
2. Downstream Intake Contract: Output 03 must adhere to canonical schema 5.7 and evidence-envelope-intake schema. tools/validate_output_hierarchy.py enforces exact file naming and structure.
3. Two Distinct LLM Judges Exist in Code/Governance:
   - HARDENED_SVP_JUDGE_PROMPT (JudgeAgenticLeverage): Cutthroat gatekeeper for a rising SVP evaluating proposed networking wedges (So-What Test, Target Test, Social Calibration Test).
   - EXECUTIVE_GRAPH_STRATEGY_REVIEW_PROMPT_1_2M: 10 strategic intelligence dimensions evaluating whether an enterprise knowledge graph acts as an Executive Cognitive Exoskeleton (Business capability depth, latent linkage, financialized CapEx/OpEx synthesis, Conway's law, dual-audience mastery).

THREE PROPOSED REMEDIATION PATHWAYS:
- Option A (Preserve Firewall, Separate Enclave): Keep Output 03 purely canonical/client-safe for CANONICAL_GRAPH.db. Generate a companion enclave file (03_internal_advisory_leverage.json) for the $1.2M / SVP judge, persisting resulting nodes directly into INTERNAL_ADVISORY shadow graph tables (shadow_graph_nodes, shadow_graph_edges).
- Option B (Bipartite Output 3 with Stripping Prior to Intake): Output bipartite JSON (client_safe + agentic_leverage) in Output 03. GraphExtractionJudge automatically invokes HARDENED_SVP_JUDGE_PROMPT. Before sealing the intake package, strip agentic_leverage so prepare_package() receives only sanitized client_safe payload.
- Option C (Implement $1.2M Exoskeleton Dimension into Output 3 Judge): Incorporate the 10 dimensions from EXECUTIVE_GRAPH_STRATEGY_REVIEW_PROMPT_1_2M.md directly into GraphExtractionJudge as an elevated strategic evaluation standard for enterprise knowledge graphs (e.g. evaluating enterprise architectural depth and CapEx/OpEx linkage without leaking personal compensation data).

TASK:
Perform a deep adversarial review:
1. Attack each option (A, B, C) ruthlessly. Identify fatal failure modes, leakage vectors, schema breaks, governance conflicts, and maintenance liabilities for EACH option.
2. Formulate a Definitive Architectural Recommendation (recommend the best design, whether a single option or a disciplined synthesis).
3. Specify the exact contract boundaries, data schemas, evaluation rubric changes, and concrete implementation plan.
"""

user_prompt = "Provide the detailed adversarial review, recommendation, and execution plan."

output_file = ROOT / "docs" / "adversarial_review_output3_architecture.md"
output_file.parent.mkdir(parents=True, exist_ok=True)

try:
    from dual_pipeline.model_registry import ModelRegistry, ModelTier
    def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
    print(f"Calling {def_m} (reasoning_effort={def_e or 'high'})...", flush=True)
    resp = client.chat.completions.create(
        model=def_m,
        reasoning_effort=def_e or "high",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
    )
    content = resp.choices[0].message.content or ""
    output_file.write_text(content, encoding="utf-8")
    print(f"Success! Written {len(content)} characters to {output_file}", flush=True)
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
