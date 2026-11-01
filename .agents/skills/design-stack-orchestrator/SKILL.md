---
name: design-stack-orchestrator
description: Route design, storytelling, and visual delivery across standalone HTML walkthroughs, presentation bundles, and design governance. Use for portable deliverable packages.
---

# Design Stack Orchestrator

Select the lowest-friction, highest-quality design path that preserves editability, governance, and user intent. Do not force work into Codex or a blocked SaaS connector when an installed design skill can produce a better portable artifact directly.

## Workflow

1. Extract the request into the route inputs in `references/decision-matrix.md`.
2. Run `scripts/score_routes.py` when the choice is not obvious.
3. Prefer a direct-write path only when enterprise-approved authentication or an authenticated local plugin path exists.
4. Otherwise choose the strongest editable substitute.
5. Invoke the most specific installed execution skill for the selected route.
6. Run `design-governance-qa` before release when the artifact is client-facing, executive-facing, published, or source-backed.
7. Use `design-handoff-bundle` when the destination app cannot be written directly or when a durable implementation package is useful.

## Routing rules

Use these defaults unless the user explicitly chooses a medium:

- Miro live board: use `miro-board-writer` only with organization-approved Miro authorization. If unavailable, use `widescreen-svg-studio` for polished frames or `excalidraw-diagram` for editable whiteboard content, then create a handoff bundle.
- Figma or FigJam: use `figma-canvas-writer`; prefer its local/private plugin path under the user's authenticated Figma session. Never bypass tenant plugin policy.
- Executive presentation: use `slides`.
- Polished executive diagram or infographic: use `ai-sdlc-visual-storytelling` when the content is AI/agentic SDLC; otherwise use `widescreen-svg-studio`.
- Enterprise technical diagram requiring precise editable XML: use `drawio-diagram`.
- Architecture-as-code or version-controlled technical truth: use `mermaid-architecture` or `pretty-mermaid` for lighter diagrams.
- Whiteboard-style editable brainstorming/process view: use `excalidraw-diagram`.
- Interactive architecture walkthrough: use `interactive-architecture`.
- Guided explanatory walkthrough: use `interactive-walkthrough`.
- Executive one-pager, prototype, or story-driven web artifact: use `frontend-storytelling`.
- Animated portable vector: use `svg-animations`.

## Enterprise constraints

- Treat missing MCP as a routing constraint, not as permission to create personal tokens or bypass SSO, app approval, plugin governance, or tenant policy.
- Prefer authenticated organization sessions, approved OAuth apps, private organization plugins, or portable artifacts.
- Never request a personal developer token unless the organization explicitly permits it.
- Never embed secrets in prompts, manifests, source files, URLs, or generated artifacts.
- When both direct-write and portable paths are possible, prefer the path with easier review, rollback, and ownership.
- Minimize unnecessary Codex use. Use Codex only when actual code execution or a code-hosted deployment is materially required.

## Output contract

Before execution, state the selected route in one short sentence when the choice is non-obvious. Include:

- target medium
- execution skill
- direct-write or portable-handoff mode
- authentication/governance dependency, if any
- fallback route if the primary path is blocked

Then execute the selected skill rather than returning only a recommendation.

## Resources

- `references/decision-matrix.md`: routing criteria and fallback hierarchy.
- `references/handoff-contract.md`: common artifact contract between design skills.
- `scripts/score_routes.py`: deterministic route scorer for ambiguous requests.
