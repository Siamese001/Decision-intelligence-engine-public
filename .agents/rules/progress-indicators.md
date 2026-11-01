---
trigger: always_on
---

# Progress Indicators Rule

Mandates continuous visual progress tracking to prevent opaque or hung execution.

## Requirements
1. **Mandatory Progress Bars**: Multi-module or >3s runs must render a live progress bar (counts, %, elapsed, ETA, pass/fail/error/skip metrics). Single-character dot feeds are forbidden. Multi-module test suites must invoke `tools/run_tests_with_progress.py`.
2. **In-Band IDE Fenced Tracking Mandate**: Live Progress Bars, gate matrices, and pipeline telemetry MUST be rendered in-band within IDE execution streams (stdout/stderr or fenced ```text` code blocks). Creating or rewriting ephemeral markdown files in the artifact registry (`brain/<conversation-id>/`) or workspace filesystem (e.g. `live_pipeline_progress.md`) for live progress animation is strictly prohibited, creates disk I/O and file-watcher churn, pollutes the artifact registry, and fails closed.
3. **Stream Flushing & Non-TTY Adaptation**: All in-band progress updates MUST explicitly flush their destination stream (`file.flush()`) on every notification. In non-interactive or non-TTY subshells, progress must format as discrete, timestamped single lines with percentage and elapsed time to prevent carriage-return (`\r`) terminal corruption.
4. **Active Model Progress Bars**: Any stage or provider invocation with blocking execution exceeding 15 seconds (such as frontier LLM judge evaluations) MUST maintain an active background progress bar emitting elapsed time and active status at least every 10–15 seconds to eliminate silent or frozen process states.
5. **Anti-Sleep Animation Prohibition**: Progress indicators must be directly wired to concrete runtime execution events and gate completions. Simulating progress via synthetic post-hoc delay or sleep loops (`time.sleep`) after execution has already completed is strictly prohibited.
6. **Minimal Conversational Progress Ceiling**: Conversational progress updates emitted in chat during active execution must be concise, normally at most 20 words per update (`INV-OUTPUT-PROGRESS-MAX-WORDS`), conveying a concrete finding, blocker, or next verification step per [.agents/rules/engineering-output.md](engineering-output.md). Tool calls must not be replayed in conversational prose.
