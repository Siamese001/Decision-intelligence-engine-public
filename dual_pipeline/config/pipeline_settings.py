"""
dual_pipeline/config/pipeline_settings.py

Wave 2: Bifurcated Fault Tolerance Configuration.
Explicitly segregates deterministic payload failures from transient provider network faults.
"""

from __future__ import annotations

from dual_pipeline.parameters import get_pipeline_settings_parameters

_SETTINGS = get_pipeline_settings_parameters()

# Payload & Remediation Retries:
# Capped strictly at 1 to prevent recursive 120k token burns and context duplication.
# If an LLM cannot produce valid payload after 1 remediation attempt, fail closed immediately.
MAX_PAYLOAD_RETRIES: int = int(_SETTINGS["max_payload_retries"])
MAX_JUDGE_RETRIES_DEFAULT: int = int(_SETTINGS["max_judge_retries_default"])

# Network & Transient Provider Fault Retries:
# Exponential backoff with jitter for HTTP 429 (rate limits), 503 (unavailable), and socket timeouts.
MAX_NETWORK_RETRIES: int = int(_SETTINGS["max_network_retries"])
NETWORK_BASE_DELAY_SECONDS: float = float(_SETTINGS["network_base_delay_seconds"])
NETWORK_MAX_DELAY_SECONDS: float = float(_SETTINGS["network_max_delay_seconds"])

# Wave 4: Multi-format Attachment Mutation Automation.
# Disabled by default; requires explicit enablement in metadata or config.
ENABLE_ATTACHMENT_MUTATION_AUTOMATION: bool = bool(_SETTINGS["enable_attachment_mutation_automation"])


def is_attachment_mutation_automation_enabled(metadata: dict | None = None) -> bool:
    """Check whether attachment mutation automation is enabled in metadata or config."""
    if metadata and "enable_attachment_mutation_automation" in metadata:
        return bool(metadata["enable_attachment_mutation_automation"])
    return ENABLE_ATTACHMENT_MUTATION_AUTOMATION

