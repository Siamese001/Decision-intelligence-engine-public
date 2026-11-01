---
trigger: model_decision
---
# Conversational Communication Style: Teams & Verbal Updates

Governs tone and delivery for informal, team-facing communications (Teams, Slack, talking points).

## Core Principles
1. **Natural First-Person Voice**: Write from operator perspective (`I caught up with...`, `I stepped in to...`). Avoid sterile third-person phrasing unless in formal audit reports.
2. **Typable & Read-Aloud Format (Zero Bold & Zero Em-Dashes)**: Bullets read naturally as if typed or spoken aloud. Strictly omit all raw Markdown bold syntax (`**...**`), backticks, `#` headings, stiff bold headers (`Meeting Outcome: [Text]`), em-dashes (`\u2014`), en-dashes (`\u2013`), and double-hyphen spacers (` -- `) so the text can be pasted directly into Microsoft Teams or Slack without raw formatting artifacts.
3. **Strategic Essence without Corporate Fluff**: Retain hard facts, names, decisions, technical trade-offs, and commitments. Deliver concise updates suitable for executive Teams chat.
4. **Audience Adaptation**: For internal leadership, lead with executive outcome and team dynamics, followed by coaching/action plan.
5. **Engineering Turn Separation**: Ordinary engineering turn completion reports are governed by [.agents/rules/engineering-output.md](engineering-output.md), requiring concise target 120 word (max 300 word) reporting of inputs, observed effects, and limitations rather than conversational narrative.
