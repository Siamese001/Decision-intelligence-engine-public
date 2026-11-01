"""
Advisory LLM Intent Summary Engine (Wave 6).
Provides optional, sanitized, hallucination-checked technical summaries of PR branches.

Invariants:
- OFF by default (--llm-summary).
- Redacts all secrets and credentials before egress.
- Drops any cited file path not present in the actual branch diff (anti-hallucination).
- Strictly advisory: NEVER an input to dispositions, review verdicts, or priority.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from tools.pr_governance_core.code_review_rules import DeterministicSignals
from tools.pr_governance_core.common import ROOT

logger = logging.getLogger(__name__)


def sanitize_for_llm_egress(text: str) -> str:
    """Sanitizes sensitive tokens, API keys, credentials, and internal secrets from LLM egress text."""
    # Redact Authorization / Bearer tokens
    redacted = re.sub(
        r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{12,}",
        "Bearer [REDACTED_TOKEN]",
        text,
    )
    # Redact Google / AWS / OpenAI style keys first (specific patterns)
    redacted = re.sub(r"AIza[0-9A-Za-z_\-]{20,45}", "[REDACTED_GOOGLE_KEY]", redacted)
    redacted = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[REDACTED_OPENAI_KEY]", redacted)
    redacted = re.sub(r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]", redacted)

    # Redact generic key/token/secret assignments (preserve already-redacted placeholders)
    redacted = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|secret|password|credential|auth)\s*[:=]\s*['\"])(?!\[REDACTED_)[^'\"\s]{8,}(['\"])",
        r"\1[REDACTED_CREDENTIAL]\2",
        redacted,
    )
    return redacted


def filter_hallucinated_file_citations(summary: str, actual_diff_files: Set[str]) -> str:
    """
    Enforces anti-hallucination defense: drops any file path citation in summary
    that is not present in actual_diff_files.
    """
    if not summary:
        return ""

    file_pattern = re.compile(r"\b([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9_\-]+)\b")

    def _replace_match(m: re.Match) -> str:
        candidate = m.group(1)
        # Skip pure version numbers or common technical shorthands
        if candidate.lower() in ("e.g.", "i.e.", "v1.0", "v2.0", "v3.0", "etc."):
            return candidate
        # Verify candidate exists in actual diff files or matches basename
        if candidate in actual_diff_files or any(f.endswith("/" + candidate) for f in actual_diff_files):
            return candidate
        # Dropped unverified file citation
        return "[unverified file reference removed]"

    return file_pattern.sub(_replace_match, summary)


def generate_advisory_llm_summary(
    branch: str,
    signals: DeterministicSignals,
    diff_files: List[str],
    commits: Optional[List[Dict[str, Any]]] = None,
    root: Path = ROOT,
) -> Optional[str]:
    """
    Generates an advisory intent summary for the branch.
    - Sanitizes egress of all credentials and secrets
    - Validates cited files against actual diff_files (drops hallucinated files)
    - Strictly advisory: never alters dispositions, verdicts, or priority.
    """
    try:
        commit_subjects = [c.get("subject", "") for c in (commits or []) if c.get("subject")]
        top_commits = commit_subjects[:5]

        # Check if provider is available in dual_pipeline or env
        summary_raw = ""
        try:
            from dual_pipeline.l2_providers_core.google_provider import GoogleProvider
            provider = GoogleProvider()
            if getattr(provider, "is_available", lambda: False)():
                egress_context = f"Branch: {branch}\nFiles: {len(diff_files)}\nCommits: {'; '.join(top_commits)}"
                sanitized_context = sanitize_for_llm_egress(egress_context)
                prompt = (
                    f"Summarize the technical intent of this branch in 1-2 concise sentences based on its commits and diff:\n{sanitized_context}"
                )
                summary_raw = provider.generate_text(prompt, max_tokens=100)
        except Exception:
            pass

        if not summary_raw:
            # High-fidelity deterministic advisory summary
            top_classes = sorted(
                signals.class_counts.items(), key=lambda x: x[1], reverse=True
            )
            class_desc = ", ".join(f"{c[0]} ({c[1]} files)" for c in top_classes[:2]) or "general"
            commit_desc = top_commits[0] if top_commits else "branch changes"
            summary_raw = f"Intent: {commit_desc}. Primary focus on {class_desc}."

        cleaned_summary = sanitize_for_llm_egress(summary_raw.strip())
        final_summary = filter_hallucinated_file_citations(cleaned_summary, set(diff_files))
        return final_summary
    except Exception as e:
        logger.warning("Failed to generate advisory LLM summary for %s: %s", branch, e)
        return None
