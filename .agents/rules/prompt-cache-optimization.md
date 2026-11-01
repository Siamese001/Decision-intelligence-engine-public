---
trigger: glob
globs: "{dual_pipeline/l2_policy_core/**,dual_pipeline/query_core/**}"
---

# Prompt Cache Optimization Governance

This rule governs the construction and execution of agentic loops and system prompts to maximize OpenAI Prompt Caching (`prompt_cache_diagnostics`).

## Core Principles

1. **Prefix Stability Mandate**
   All LLM API calls must construct their message array with an absolute static prefix. The order must be strictly preserved across all turns:
   - System prompt
   - Tools/Schemas definitions
   - Static few-shot examples
   - Dynamic user messages/context

2. **No Volatile Injections in Prefix**
   Do not inject dynamic variables like current timestamps (`2026-10-02T04:12`), randomly generated task IDs, or varying context variables into the system prompt. Any volatile context must be placed strictly in the dynamic suffix (e.g., the final user message or context message).

3. **Stable Tool Schema Contract**
   When utilizing tools or functions, the list of available tools must remain stable throughout a session. Adding or removing tools mid-session invalidates the cached prefix ("tools_changed" cache miss reason).

4. **Telemetry Capture**
   All pipeline systems and autonomous agent wrappers must log `cache_missed_tokens` and `comparison_reusable_tokens` from OpenAI's `prompt_cache_diagnostics` response payload to enable diagnostic debugging.
