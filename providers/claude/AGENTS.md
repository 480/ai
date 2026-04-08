# Claude Agents

Documentation for the checked-in Claude Code artifacts and install behavior.

## Name mapping

- `480-architect` -> `480-architect` (`providers/claude/agents/480-architect.md`)
- `480-developer` -> `480-developer` (`providers/claude/agents/480-developer.md`)
- `480-code-reviewer` -> `480-code-reviewer` (`providers/claude/agents/480-code-reviewer.md`)
- `480-code-reviewer2` -> `480-code-reviewer2` (`providers/claude/agents/480-code-reviewer2.md`)
- `480-code-scanner` -> `480-code-scanner` (`providers/claude/agents/480-code-scanner.md`)

## Primary

- `480-architect`
  - maps from: `480-architect`
  - file: `providers/claude/agents/480-architect.md`
  - model: `claude-opus-4-6`
  - effort: `max`

## Subagents

- `480-developer`
  - maps from: `480-developer`
  - file: `providers/claude/agents/480-developer.md`
  - model: `claude-sonnet-4-6`
  - effort: `medium`

- `480-code-reviewer`
  - maps from: `480-code-reviewer`
  - file: `providers/claude/agents/480-code-reviewer.md`
  - model: `claude-opus-4-6`
  - effort: `low`

- `480-code-reviewer2`
  - maps from: `480-code-reviewer2`
  - file: `providers/claude/agents/480-code-reviewer2.md`
  - model: `claude-sonnet-4-6`
  - effort: `low`

- `480-code-scanner`
  - maps from: `480-code-scanner`
  - file: `providers/claude/agents/480-code-scanner.md`
  - model: `haiku`
  - effort: `low`

## Install names and paths

Install files use the Claude-specific names above and are copied to `~/.claude/agents/` or `<project>/.claude/agents/`.
Recommended installs use the checked-in artifacts in `providers/claude/agents/` as-is.
Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.

## Default behavior

- Default activation is optional and only sets `agent` to `480-architect` when `--activate-default` is used.
- Uninstall restores the previous value only when the current `agent` is still `480-architect`.

## Team behavior

- In environments where Claude Code agent teams are enabled, `480-architect` coordinates the default three-person team (`480-developer`, `480-code-reviewer`, `480-code-reviewer2`).
- Add `480-code-scanner` only when repository scanning is actually needed.
- During install, the installer asks whether to enable the agent teams experimental flag and merges `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` into the `env` section of `settings.json` when enabled.
- Uninstall leaves the teams experimental flag env setting untouched.
- When team support is disabled or unsupported, `480-architect` follows the same Task Brief-based flow directly as the single-orchestrator fallback.

## Source of truth

- Common agent definitions: `bundles/common/agents.json`.
- Default instruction bodies: `bundles/common/instructions/`.
- Claude provider-specific override bodies, if any: `providers/claude/instructions/`.
- Provider install paths and model-selection schema: `app/providers.py`.
- Provider artifact rendering: `app/render_agents.py`.
- Install/uninstall entrypoint: `app/manage_agents.py`.
- State storage and restore: `app/installer_core.py`.
- User guidance: `README.md`.
