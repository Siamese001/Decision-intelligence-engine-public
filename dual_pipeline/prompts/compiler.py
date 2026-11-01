"""
dual_pipeline/prompts/compiler.py

Unified Prompt & Rubric Compiler Engine (SSOT).
Deterministically compiles stage generation prompts, judge evaluation rubrics,
and governance artifacts from declarative OutputContracts.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contracts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
    OutputContract,
)
from .partials import (
    ANTI_HALLUCINATION_RULES,
    CAREER_FIREWALL_C19_RULE,
    CRITIQUE_DIFF_ENVELOPE_SCHEMA,
    TEMPORAL_SSOT_RULES,
    UNIVERSAL_SYSTEM_PREAMBLE,
    XML_DATA_ISOLATION_INSTRUCTION,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOVERNANCE_DIR = REPO_ROOT / "governance" / "decision_intelligence"
CONFIG_STAGES_DIR = REPO_ROOT / "config" / "prompts" / "synthesis" / "stages"


class PromptCompiler:
    """Compiles declarative OutputContracts into executable prompts and rubrics."""

    def __init__(self) -> None:
        self.contracts: Dict[int, OutputContract] = {
            1: OUTPUT1_CONTRACT,
            2: OUTPUT2_CONTRACT,
            3: OUTPUT3_CONTRACT,
        }

    def compile_stage_prompt(
        self,
        output_id: int,
        archetype: MeetingArchetype = MeetingArchetype.GENERAL,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Compile an archetype-adaptive stage generation prompt."""
        if output_id not in self.contracts:
            raise ValueError(f"Unknown output_id: {output_id}. Supported: 1, 2, 3")
        contract = self.contracts[output_id]
        return contract.compile_stage_prompt(archetype, extra_context=extra_context)

    def compile_judge_prompt(
        self,
        output_id: int,
        archetype: MeetingArchetype = MeetingArchetype.GENERAL,
    ) -> str:
        """Compile an archetype-adaptive judge evaluation prompt."""
        if output_id not in self.contracts:
            raise ValueError(f"Unknown output_id: {output_id}. Supported: 1, 2, 3")
        contract = self.contracts[output_id]
        return contract.compile_judge_prompt(archetype)

    def compile_judge_rubric_markdown(self, output_id: int, archetype: MeetingArchetype) -> str:
        """Generate structured markdown rubric for judge prompt from rubric dimensions."""
        contract = self.contracts[output_id]
        lines = [
            f"# Output {contract.output_id}: {contract.name} — Evaluation Rubric",
            f"Meeting Archetype: `{archetype.value}`\n",
            UNIVERSAL_SYSTEM_PREAMBLE,
            "\n## Evaluation Dimensions (100 Points Total)\n",
        ]
        for i, dim in enumerate(contract.rubric_dimensions, 1):
            lines.append(f"### Dimension {i}: {dim.name} ({dim.max_points} Points)")
            lines.append(f"**Key**: `{dim.key}`")
            lines.append(f"**Description**: {dim.description}")
            if dim.criteria:
                lines.append("**Criteria**:")
                for c in dim.criteria:
                    lines.append(f"- {c}")
            lines.append("")

        lines.extend([
            "## Universal Governance & Security Floors",
            ANTI_HALLUCINATION_RULES,
            TEMPORAL_SSOT_RULES,
            CAREER_FIREWALL_C19_RULE,
            XML_DATA_ISOLATION_INSTRUCTION,
            "\n## Required JSON Response Format",
            "You MUST emit valid JSON conforming to this schema:",
            "```json",
            "{",
            f'  "meeting_archetype": "{archetype.value}",',
        ])
        for dim in contract.rubric_dimensions:
            lines.append(f'  "{dim.key}": <integer 0-{dim.max_points}>,')
        lines.extend([
            '  "composite_score": <integer 0-100>,',
            '  "verdict": "PASS" | "FAIL",',
            '  "forensic_rationale": "<dense paragraph explaining scores with transcript citations>",',
            '  "actionable_remediation_diff": "<specific edits required to pass>",',
            '  "critique_diff_envelope": ' + CRITIQUE_DIFF_ENVELOPE_SCHEMA,
            "}",
            "```",
        ])
        return "\n".join(lines).strip()

    def build_canonical_manifest(self) -> Dict[Path, str]:
        """Map canonical repository files to their SSOT content via CANONICAL_PROMPT_REGISTRY."""
        from .registry import CANONICAL_PROMPT_REGISTRY
        return {
            CONFIG_STAGES_DIR / "01_forensic_normalizer.md": CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_01_FORENSIC"),
            CONFIG_STAGES_DIR / "02_executive_recap.md": CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_02_RECAP"),
            CONFIG_STAGES_DIR / "03_graph_extractor.md": CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_GRAPH"),
        }

    def verify_ssot_drift(self) -> List[str]:
        """Verify that filesystem markdown files and judges do not drift from contract specifications."""
        from tools.lint_contract_parity import run_contract_parity_checks
        passed, parity_defects = run_contract_parity_checks()
        defects = list(parity_defects)

        from .registry import CANONICAL_PROMPT_REGISTRY
        if not CANONICAL_PROMPT_REGISTRY.verify_manifest():
            defects.append("CANONICAL_PROMPT_REGISTRY manifest verification failed: missing or empty prompts.")
        return defects


CANONICAL_PROMPT_COMPILER = PromptCompiler()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compiler for Prompt & Judge SSOT Architecture")
    parser.add_argument("--verify", action="store_true", help="Verify repository prompt files match contracts")
    parser.add_argument("--build", action="store_true", help="Build and write compiled prompts to repository")
    args = parser.parse_args()

    compiler = PromptCompiler()
    if args.verify:
        defects = compiler.verify_ssot_drift()
        if defects:
            print(f"[FAIL] Found {len(defects)} SSOT drift defect(s):")
            for d in defects:
                print(f"  - {d}")
            return 1
        print("[PASS] Zero SSOT prompt drift detected. All canonical prompts match compiled contracts.")
        return 0

    if args.build:
        manifest = compiler.build_canonical_manifest()
        for path, content in manifest.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.strip() + "\n", encoding="utf-8")
            print(f"[BUILT] {path.relative_to(REPO_ROOT)}")
        print("[SUCCESS] All canonical prompts built from OutputContracts.")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
