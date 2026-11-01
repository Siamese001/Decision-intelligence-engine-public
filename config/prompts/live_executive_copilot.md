# Live Executive Copilot Prompt: gpt-5.6-luna under env_di

Governs the live copilot reasoning engine during real-time client meetings.

## Invariants:
1. Model-neutral runtime binding via `dual_pipeline.model_registry`.
2. Parameter immunity enforced for reasoning models (`gpt-5.6-luna`, `gpt-6-astra`).
3. Low-latency evaluation (< 1.5s execution budget).
4. Output schema strictly structured as JSON. Machine-enforced field constraints:
   - snap_cue <= 8 words
   - suggested_script <= 12 words per sentence, 3 sentences max (under 35 words total; up to 90 words in detailed speaker-note synthesis mode)
   - speaker_summary_quotes <= 130 words (multi-sentence speaker stance, commitments, and objections)
   - risk_trap <= 8 words

## Runtime Prompt (loaded at startup from governance/decision_intelligence/LIVE_EXECUTIVE_COPILOT_PROMPT.md)

This file is a metadata stub. The authoritative prompt text is:
`governance/decision_intelligence/LIVE_EXECUTIVE_COPILOT_PROMPT.md`

It is loaded at runtime by `CANONICAL_PROMPT_REGISTRY.get_prompt("LIVE_COPILOT")`.
