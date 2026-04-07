# Gemini CLI Agents

Documentation for the checked-in Gemini CLI artifacts and install behavior.

## Name mapping

- `480-architect` -> `480-architect` (`providers/gemini/agents/480-architect.md`)
- `480-developer` -> `480-developer` (`providers/gemini/agents/480-developer.md`)
- `480-code-reviewer` -> `480-code-reviewer` (`providers/gemini/agents/480-code-reviewer.md`)
- `480-code-reviewer2` -> `480-code-reviewer2` (`providers/gemini/agents/480-code-reviewer2.md`)
- `480-code-scanner` -> `480-code-scanner` (`providers/gemini/agents/480-code-scanner.md`)

## Primary

- `480-architect`
  - maps from: `480-architect`
  - file: `providers/gemini/agents/480-architect.md`
  - model: `inherit`

## Subagents

- `480-developer`
  - maps from: `480-developer`
  - file: `providers/gemini/agents/480-developer.md`
  - model: `inherit`

- `480-code-reviewer`
  - maps from: `480-code-reviewer`
  - file: `providers/gemini/agents/480-code-reviewer.md`
  - model: `inherit`

- `480-code-reviewer2`
  - maps from: `480-code-reviewer2`
  - file: `providers/gemini/agents/480-code-reviewer2.md`
  - model: `inherit`

- `480-code-scanner`
  - maps from: `480-code-scanner`
  - file: `providers/gemini/agents/480-code-scanner.md`
  - model: `inherit`

## Install names and paths

Install files use the Gemini CLI-specific names above and are copied to `~/.gemini/agents/` or `<project>/.gemini/agents/`.
Recommended installs use the checked-in artifacts in `providers/gemini/agents/` as-is.
Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.

## Default behavior

- Default activation is optional and only sets `default_agent` to `480-architect` when `--activate-default` is used.
- Uninstall restores the previous value only when the current `default_agent` is still `480-architect`.

## Subagent support

- Gemini CLI subagents are experimental and require `{"experimental": {{"enableSubagents": true}}}` in `settings.json`.
- Agents are discovered from `.gemini/agents/` (project) and `~/.gemini/agents/` (user) directories.
- The main Gemini CLI automatically routes tasks to subagents based on their `description` field.

## Source of truth

- Common agent definitions: `bundles/common/agents.json`.
- Default instruction bodies: `bundles/common/instructions/`.
- Gemini provider-specific override bodies, if any: `providers/gemini/instructions/`.
- Provider install paths and model-selection schema: `app/providers.py`.
- Provider artifact rendering: `app/render_agents.py`.
- Install/uninstall entrypoint: `app/manage_agents.py`.
- State storage and restore: `app/installer_core.py`.
- User guidance: `README.md`.
