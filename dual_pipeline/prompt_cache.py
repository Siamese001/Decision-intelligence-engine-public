"""SSOT for prompt caching: key derivation, provider minimums, and usage normalization.

This module provides deterministic, provider-neutral helpers for prompt caching:
- Standardized cache key formulation across workflows and providers.
- Normalization of cached token counts across OpenAI, Gemini, and Anthropic.
- Provider minimum prefix token thresholds and retention policies.
- Dormant Anthropic cache_control breakpoint helpers.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

# Standard minimum token thresholds required for provider prompt caching
PROVIDER_MIN_PREFIX_TOKENS: Mapping[str, int] = {
    "openai": 1024,
    "anthropic": 1024,
    "gemini": 1024,
    "gemini_flash": 1024,
    "gemini_pro": 2048,
    "gemini_3": 4096,
}

# Standard OpenAI cache retention policy (in-memory is default and recommended)
DEFAULT_CACHE_RETENTION = "in_memory"

# Sensitive pattern indicators that must never be present in cache keys
_FORBIDDEN_KEY_PATTERNS = [
    re.compile(r"(?:run|req|trace|uuid)[-_][0-9a-fA-F-]{8,}", re.I),
    re.compile(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}"),
    re.compile(r"(?:confidential|salary|compensation|rate_card|bonus|\$[\d,]+)", re.I),
]


def derive_cache_key(
    *,
    workflow: str,
    model: str,
    static_prefix: str,
    template_version: str = "v1",
) -> str:
    """Derive a deterministic, safe prompt_cache_key.

    Format: nee:{workflow}:{model}:{sha256(version + prefix)[:16]}
    Guarantees no timestamps, run IDs, or PII leak into the cache key.
    """
    for pattern in _FORBIDDEN_KEY_PATTERNS:
        if pattern.search(workflow) or pattern.search(model):
            raise ValueError(f"Cache key input violates security hygiene: workflow={workflow}, model={model}")

    clean_workflow = re.sub(r"[^a-zA-Z0-9_-]", "_", workflow.strip().lower())
    clean_model = re.sub(r"[^a-zA-Z0-9._-]", "_", model.strip().lower())

    prefix_content = f"{template_version}:{static_prefix}"
    prefix_sha = hashlib.sha256(prefix_content.encode("utf-8")).hexdigest()[:16]

    key = f"nee:{clean_workflow}:{clean_model}:{prefix_sha}"

    for pattern in _FORBIDDEN_KEY_PATTERNS:
        if pattern.search(key):
            raise ValueError(f"Derived cache key violates security hygiene: {key}")

    return key


def _extract_usage_container(response_or_usage: Any) -> Any:
    if response_or_usage is None:
        return None
    if hasattr(response_or_usage, "usage") and response_or_usage.usage is not None:
        return response_or_usage.usage
    if hasattr(response_or_usage, "usage_metadata") and response_or_usage.usage_metadata is not None:
        return response_or_usage.usage_metadata
    if isinstance(response_or_usage, Mapping):
        for k in ("usage", "usageMetadata", "usage_metadata"):
            if k in response_or_usage and response_or_usage[k] is not None:
                return response_or_usage[k]
    return response_or_usage


def _get_field(obj: Any, key: str) -> Any:
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, Mapping):
        return obj.get(key)
    return None


def _extract_cached_token_val(usage: Any) -> Any:
    for details_key in ("input_tokens_details", "prompt_tokens_details"):
        details = _get_field(usage, details_key)
        if details is not None:
            tok = _get_field(details, "cached_tokens")
            if tok is not None:
                return tok

    for key in (
        "cached_content_token_count",
        "cachedContentTokenCount",
        "cache_read_input_tokens",
        "cached_tokens",
    ):
        tok = _get_field(usage, key)
        if tok is not None:
            return tok

    return None


def normalize_cached_tokens(
    response_or_usage: Any,
    provider: str = "openai",
) -> int:
    """Normalize cached input token counts from any provider response or usage dict.

    Supported shapes:
    - OpenAI Responses API: usage.input_tokens_details.cached_tokens
    - OpenAI Chat API: usage.prompt_tokens_details.cached_tokens
    - Gemini REST / SDK: usageMetadata.cachedContentTokenCount or cached_content_token_count
    - Anthropic: usage.cache_read_input_tokens or cache_read_input_tokens
    """
    if response_or_usage is None:
        return 0

    usage = _extract_usage_container(response_or_usage)
    val = _extract_cached_token_val(usage)
    if val is None or isinstance(val, bool):
        return 0

    try:
        return max(0, int(val))
    except (ValueError, TypeError):
        return 0


def anthropic_cache_breakpoint(
    content: str,
    breakpoint_type: str = "ephemeral",
    ttl: str | None = None,
) -> dict[str, Any]:
    """Helper for Anthropic explicit cache breakpoints (supports 5m or 1h TTL)."""
    cache_control: dict[str, Any] = {"type": breakpoint_type}
    if ttl in ("5m", "1h"):
        cache_control["ttl"] = ttl
    return {
        "type": "text",
        "text": content,
        "cache_control": cache_control,
    }


def _append_text_or_breakpoint(
    target_list: list[dict[str, Any]], content: str, eff_ttl: str, used: int, max_bp: int
) -> int:
    if used < max_bp:
        target_list.append(anthropic_cache_breakpoint(content, ttl=eff_ttl))
        return used + 1
    target_list.append({"type": "text", "text": content})
    return used


def _format_anthropic_tools(
    tools: list[dict[str, Any]] | None, eff_ttl: str, used: int, max_bp: int
) -> tuple[list[dict[str, Any]], int]:
    if not tools:
        return [], used
    formatted = [dict(t) for t in tools]
    if used < max_bp:
        formatted[-1]["cache_control"] = {"type": "ephemeral", "ttl": eff_ttl}
        used += 1
    return formatted, used


def build_anthropic_multi_breakpoint_payload(
    *,
    system_blocks: list[str] | str,
    tools: list[dict[str, Any]] | None = None,
    examples_block: str | None = None,
    turn_prefix_block: str | None = None,
    user_query: str = "",
    ttl: str = "5m",
) -> dict[str, Any]:
    """Construct up to 4 Anthropic cache_control breakpoints across tools, system, examples, and turn prefix."""
    max_bp = 4
    eff_ttl = ttl if ttl in ("5m", "1h") else "5m"
    formatted_tools, used = _format_anthropic_tools(tools, eff_ttl, 0, max_bp)

    raw_sys = [system_blocks] if isinstance(system_blocks, str) else list(system_blocks)
    sys_payload: list[dict[str, Any]] = [{"type": "text", "text": b} for b in raw_sys[:-1]]
    if raw_sys:
        used = _append_text_or_breakpoint(sys_payload, raw_sys[-1], eff_ttl, used, max_bp)
    if examples_block:
        used = _append_text_or_breakpoint(sys_payload, examples_block, eff_ttl, used, max_bp)

    user_parts: list[dict[str, Any]] = []
    if turn_prefix_block:
        used = _append_text_or_breakpoint(user_parts, turn_prefix_block, eff_ttl, used, max_bp)
    if user_query:
        user_parts.append({"type": "text", "text": user_query})

    result: dict[str, Any] = {
        "system": sys_payload,
        "messages": [{"role": "user", "content": user_parts}],
        "breakpoints_count": used,
    }
    if formatted_tools:
        result["tools"] = formatted_tools
    return result


OPENAI_MIN_CACHE_PREFIX_TOKENS = PROVIDER_MIN_PREFIX_TOKENS["openai"]

VOLATILE_PREFIX_PATTERNS = [
    re.compile(r"Current Date/Time:\s*.*", re.IGNORECASE),
    re.compile(r"Execution Run ID:\s*.*", re.IGNORECASE),
    re.compile(r"Observed At:\s*.*", re.IGNORECASE),
    re.compile(r"<dynamic_context>.*?</dynamic_context>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<meeting_metadata>.*?</meeting_metadata>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<roster>.*?</roster>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<meeting_attendees_roster>.*?</meeting_attendees_roster>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<raw_transcript>.*?</raw_transcript>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<forensic_transcript>.*?</forensic_transcript>", re.DOTALL | re.IGNORECASE),
    re.compile(r"(?:run|req|trace|uuid)[-_][0-9a-fA-F-]{8,}", re.IGNORECASE),
]

PROMOTABLE_STATIC_PATTERNS = [
    re.compile(r"(<governance_context>[\s\S]*?</governance_context>)", re.IGNORECASE),
    re.compile(r"(<mandatory_structure>[\s\S]*?</mandatory_structure>)", re.IGNORECASE),
    re.compile(r"(<undiarized_execution_guidance>[\s\S]*?</undiarized_execution_guidance>)", re.IGNORECASE),
    re.compile(r"(<incremental_graph_directive>[\s\S]*?</incremental_graph_directive>)", re.IGNORECASE),
    re.compile(
        r"(INSTRUCTIONS FOR ATTENDEES \(Rule C-16 & Gate 3\.11\):[\s\S]*?"
        r"(?=<meeting_attendees_roster>|\Z))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(INSTRUCTION:\s*1\.\s*For any entity listed in <known_graph_entities>[\s\S]*?"
        r"(?=<known_graph_entities>|\Z))",
        re.IGNORECASE,
    ),
]


class PromptPrefixQuarantine:
    """Enforces strict quarantine of static instructions (>= 1,024 tokens) from dynamic runtime context."""

    def __init__(self, profiler: Any = None) -> None:
        if profiler is None:
            from dual_pipeline.token_optimization.profiler import TokenProfiler
            profiler = TokenProfiler()
        self.profiler = profiler

    def compute_prefix_hash(self, text: str) -> str:
        normalized = self.profiler.strip_redundant_whitespace(text)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def sanitize_prefix(self, text: str) -> tuple[str, list[str]]:
        clean_text = self.profiler.strip_redundant_whitespace(text)
        extracted: list[str] = []
        for pattern in VOLATILE_PREFIX_PATTERNS:
            found = pattern.findall(clean_text)
            if found:
                for match in found:
                    extracted.append(match.strip())
                clean_text = pattern.sub("", clean_text)
        return self.profiler.strip_redundant_whitespace(clean_text), extracted

    def promote_static_blocks(self, user_content: str) -> tuple[str, list[str]]:
        promoted: list[str] = []
        cleaned = user_content
        for pattern in PROMOTABLE_STATIC_PATTERNS:
            found = pattern.findall(cleaned)
            if found:
                for match in found:
                    promoted.append(match.strip())
                cleaned = pattern.sub("", cleaned)
        return self.profiler.strip_redundant_whitespace(cleaned), promoted

    def quarantine_blocks(
        self,
        static_blocks: Any,
        dynamic_blocks: Any,
        min_prefix_tokens: int = OPENAI_MIN_CACHE_PREFIX_TOKENS,
    ) -> tuple[str, str, str, bool]:
        joined_static = "\n\n".join(b.strip() for b in static_blocks if b and b.strip())
        sanitized_prefix, extracted_dynamic = self.sanitize_prefix(joined_static)
        dynamic_parts = [b.strip() for b in dynamic_blocks if b and b.strip()]
        if extracted_dynamic:
            dynamic_parts.extend(extracted_dynamic)
        dynamic_suffix = "\n\n".join(dynamic_parts)
        prefix_sha = self.compute_prefix_hash(sanitized_prefix)
        prefix_tokens = self.profiler.count_tokens(sanitized_prefix)
        return sanitized_prefix, dynamic_suffix, prefix_sha, prefix_tokens >= min_prefix_tokens

    def build_quarantined_messages(
        self,
        system_prompt: str,
        user_content: str,
        static_blocks: Any = (),
        dynamic_blocks: Any = (),
        min_prefix_tokens: int = OPENAI_MIN_CACHE_PREFIX_TOKENS,
    ) -> tuple[list[dict[str, str]], Any, str]:
        from dual_pipeline.token_optimization.schemas import PromptComponents
        clean_user, promoted_static = self.promote_static_blocks(user_content)
        all_static = [system_prompt] + list(static_blocks) + promoted_static
        all_dynamic = [clean_user] + list(dynamic_blocks)
        static_prefix, dynamic_suffix, prefix_sha, is_cacheable = self.quarantine_blocks(
            static_blocks=all_static,
            dynamic_blocks=all_dynamic,
            min_prefix_tokens=min_prefix_tokens,
        )
        components = PromptComponents(
            static_system_prefix=static_prefix,
            dynamic_system_suffix="",
            user_payload=dynamic_suffix,
            estimated_prefix_tokens=self.profiler.count_tokens(static_prefix),
            is_cacheable_prefix=is_cacheable,
        )
        messages = [
            {"role": "system", "content": static_prefix},
            {"role": "user", "content": dynamic_suffix},
        ]
        return messages, components, prefix_sha


__all__ = [
    "DEFAULT_CACHE_RETENTION",
    "OPENAI_MIN_CACHE_PREFIX_TOKENS",
    "PROMOTABLE_STATIC_PATTERNS",
    "PROVIDER_MIN_PREFIX_TOKENS",
    "PromptPrefixQuarantine",
    "VOLATILE_PREFIX_PATTERNS",
    "anthropic_cache_breakpoint",
    "build_anthropic_multi_breakpoint_payload",
    "derive_cache_key",
    "normalize_cached_tokens",
]
