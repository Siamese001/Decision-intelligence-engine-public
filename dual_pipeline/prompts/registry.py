"""
dual_pipeline/prompts/registry.py

Centralized Single Source of Truth (SSOT) for prompt templates and evaluation contracts.
Loads, validates, and caches canonical prompt contracts from governance/decision_intelligence/
with SHA-256 integrity verification.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Optional

# Root path of the repository
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_GOVERNANCE_DIR = _REPO_ROOT / "governance" / "decision_intelligence"
_CONFIG_PROMPTS_DIR = _REPO_ROOT / "config" / "prompts"
_CONFIG_STAGES_DIR = _CONFIG_PROMPTS_DIR / "synthesis" / "stages"


PROMPT_FILE_MAP: Dict[str, Path] = {
    # Judge Evaluation Prompts
    "JUDGE_FORENSIC": _GOVERNANCE_DIR / "FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md",
    "JUDGE_RECAP": _GOVERNANCE_DIR / "EXECUTIVE_RECAP_JUDGE_PROMPT.md",
    "JUDGE_GRAPH": _GOVERNANCE_DIR / "GRAPH_EXTRACTION_JUDGE_PROMPT.md",
    "EXECUTIVE_EXOSKELETON_REVIEW": _GOVERNANCE_DIR / "EXECUTIVE_GRAPH_EXOSKELETON_REVIEW_PROMPT.md",
    "EXECUTIVE_STRATEGY_AUDIT": _GOVERNANCE_DIR / "EXECUTIVE_GRAPH_STRATEGY_REVIEW_PROMPT_1_2M.md",
    # Synthesis Modality SSOT Contracts
    "RAW_TRANSCRIPT_SYNTHESIS": _CONFIG_PROMPTS_DIR / "synthesis" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_8.md",
    "RAW_TRANSCRIPT_SYNTHESIS_V5_7": _CONFIG_PROMPTS_DIR / "synthesis" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md",
    "TEAMS_RAW_TRANSCRIPT_SYNTHESIS": _GOVERNANCE_DIR / "TEAMS_RAW_TRANSCRIPT_SYNTHESIS_PROMPT.md",
    "AUDIO_STREAM_TRANSCRIPT_SYNTHESIS": _GOVERNANCE_DIR / "AUDIO_STREAM_TRANSCRIPT_SYNTHESIS_PROMPT.md",
    "ARTIFACT_INDEPENDENCE_FIREWALL": _GOVERNANCE_DIR / "ARTIFACT_INDEPENDENCE_AND_CAREER_FIREWALL_PROMPT.md",
    "AGENT_REASONING_SCRATCHPAD": _GOVERNANCE_DIR / "AGENT_REASONING_SCRATCHPAD_PROMPT.md",
    "HOLISTIC_RELEASE_JUDGE": _GOVERNANCE_DIR / "HOLISTIC_RELEASE_JUDGE_PROMPT.md",
    "HARDENED_SVP_JUDGE": _GOVERNANCE_DIR / "HARDENED_SVP_JUDGE_PROMPT.md",
    "REPO_TESTING_GAPS_REVIEW": _CONFIG_PROMPTS_DIR / "testing" / "REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md",

    # Stage Synthesis Prompts
    "STAGE_01_FORENSIC": _CONFIG_STAGES_DIR / "01_forensic_normalizer.md",
    "STAGE_01_TEAMS_RAW": _CONFIG_STAGES_DIR / "01_teams_raw_normalizer.md",
    "STAGE_02_RECAP": _CONFIG_STAGES_DIR / "02_executive_recap.md",
    "STAGE_02_ARCHITECTURE_DEEP_DIVE_OVERRIDE": _CONFIG_STAGES_DIR / "02_architecture_deep_dive_override.md",
    "STAGE_03_GRAPH": _CONFIG_STAGES_DIR / "03_graph_extractor.md",
    "STAGE_03_ARCHITECTURE_DEEP_DIVE_OVERRIDE": _CONFIG_STAGES_DIR / "03_architecture_deep_dive_override.md",
    "STAGE_03_DOCUMENT_GRAPH": _CONFIG_STAGES_DIR / "03_document_extractor.md",

    # Pipeline Intelligence & Agentic Prompts
    "AGENTIC_EXTRACTION": _GOVERNANCE_DIR / "AGENTIC_EXTRACTION_PROMPT.md",
    "LIVE_COPILOT": _CONFIG_PROMPTS_DIR / "live_executive_copilot.md",
    "SPEAKER_DISAMBIGUATION": _GOVERNANCE_DIR / "SPEAKER_DISAMBIGUATION_PROMPT.md",
    "DYNAMIC_PROJECT_ROUTER": _GOVERNANCE_DIR / "DYNAMIC_PROJECT_ROUTER_PROMPT.md",
}



class PromptRegistry:
    """Centralized prompt template loader and SHA-256 integrity validator."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or _GOVERNANCE_DIR
        self._cache: Dict[str, str] = {}
        self._digest_cache: Dict[str, str] = {}

    def _resolve_path(self, prompt_key: str) -> Path:
        """Resolve prompt key to filesystem path."""
        if prompt_key in PROMPT_FILE_MAP:
            return PROMPT_FILE_MAP[prompt_key]
        # Fallback: check directly under base_dir
        candidate = self.base_dir / f"{prompt_key}.md"
        if candidate.exists():
            return candidate
        raise KeyError(f"Unknown prompt contract key: {prompt_key}. Registered keys: {list(PROMPT_FILE_MAP.keys())}")

    def get_prompt(self, prompt_key: str, reload: bool = False) -> str:
        """Load and cache prompt content with whitespace normalization."""
        if not reload and prompt_key in self._cache:
            return self._cache[prompt_key]

        path = self._resolve_path(prompt_key)
        if not path.exists():
            raise FileNotFoundError(f"Canonical prompt file not found at {path}")

        content = path.read_text(encoding="utf-8").strip()
        self._cache[prompt_key] = content
        self._digest_cache[prompt_key] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return content

    def get_contract(self, output_id: int) -> OutputContract:
        """Get declarative OutputContract for Output 1, 2, or 3."""
        from .contracts import OUTPUT1_CONTRACT, OUTPUT2_CONTRACT, OUTPUT3_CONTRACT
        contract_map = {1: OUTPUT1_CONTRACT, 2: OUTPUT2_CONTRACT, 3: OUTPUT3_CONTRACT}
        if output_id not in contract_map:
            raise KeyError(f"Unknown output_id: {output_id}. Must be 1, 2, or 3.")
        return contract_map[output_id]

    def compile_stage_prompt(
        self,
        output_id: int,
        archetype: Any,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Compile archetype-adaptive stage generation prompt from canonical contract."""
        from .contracts import MeetingArchetype
        arch = MeetingArchetype.from_string(archetype) if not isinstance(archetype, MeetingArchetype) else archetype
        contract = self.get_contract(output_id)
        return contract.compile_stage_prompt(arch, extra_context=extra_context)

    def compile_judge_prompt(self, output_id: int, archetype: Any) -> str:
        """Compile archetype-adaptive judge evaluation prompt from canonical contract."""
        from .contracts import MeetingArchetype
        arch = MeetingArchetype.from_string(archetype) if not isinstance(archetype, MeetingArchetype) else archetype
        contract = self.get_contract(output_id)
        return contract.compile_judge_prompt(arch)

    def get_prompt_digest(self, prompt_key: str) -> str:
        """Compute or retrieve cached SHA-256 digest of prompt contract."""
        if prompt_key not in self._digest_cache:
            self.get_prompt(prompt_key)
        return self._digest_cache[prompt_key]

    def get_manifest(self) -> Dict[str, str]:
        """Generate manifest mapping all registered prompt keys to SHA-256 digests."""
        manifest = {}
        for key in PROMPT_FILE_MAP:
            try:
                manifest[key] = self.get_prompt_digest(key)
            except FileNotFoundError:
                manifest[key] = "FILE_NOT_FOUND"
        return manifest

    def verify_manifest(self) -> bool:
        """Verify that all registered prompt contracts exist and have non-empty content."""
        for key, path in PROMPT_FILE_MAP.items():
            if not path.exists():
                return False
            content = self.get_prompt(key)
            if not content:
                return False
        return True


# Global singleton instance for pipeline consumers
CANONICAL_PROMPT_REGISTRY = PromptRegistry()

