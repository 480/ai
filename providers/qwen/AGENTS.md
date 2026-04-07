# Qwen Code Agents

Documentation for the checked-in Qwen Code artifacts and install behavior.

## Name mapping

- `480-architect` -> `480-architect` (`providers/qwen/agents/480-architect.md`)
- `480-developer` -> `480-developer` (`providers/qwen/agents/480-developer.md`)
- `480-code-reviewer` -> `480-code-reviewer` (`providers/qwen/agents/480-code-reviewer.md`)
- `480-code-reviewer2` -> `480-code-reviewer2` (`providers/qwen/agents/480-code-reviewer2.md`)
- `480-code-scanner` -> `480-code-scanner` (`providers/qwen/agents/480-code-scanner.md`)

## Primary

- `480-architect`
  - maps from: `480-architect`
  - file: `providers/qwen/agents/480-architect.md`
  - model: `qwen-coder-plus`

## Subagents

- `480-developer`
  - maps from: `480-developer`
  - file: `providers/qwen/agents/480-developer.md`
  - model: `qwen-coder-plus`

- `480-code-reviewer`
  - maps from: `480-code-reviewer`
  - file: `providers/qwen/agents/480-code-reviewer.md`
  - model: `qwen-coder-plus`

- `480-code-reviewer2`
  - maps from: `480-code-reviewer2`
  - file: `providers/qwen/agents/480-code-reviewer2.md`
  - model: `qwen-coder-plus`

- `480-code-scanner`
  - maps from: `480-code-scanner`
  - file: `providers/qwen/agents/480-code-scanner.md`
  - model: `qwen-coder-flash`

## Install names and paths

Install files use the Qwen Code-specific names above and are copied to `~/.qwen/agents/` or `<project>/.qwen/agents/`.
Recommended installs use the checked-in artifacts in `providers/qwen/agents/` as-is.
Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.

## Default behavior

- Default activation is optional and only sets `default_agent` to `480-architect` when `--activate-default` is used.
- Uninstall restores the previous value only when the current `default_agent` is still `480-architect`.

## Source of truth

- Common agent definitions: `bundles/common/agents.json`.
- Default instruction bodies: `bundles/common/instructions/`.
- Qwen provider-specific override bodies, if any: `providers/qwen/instructions/`.
- Provider install paths and model-selection schema: `app/providers.py`.
- Provider artifact rendering: `app/render_agents.py`.
- Install/uninstall entrypoint: `app/manage_agents.py`.
- State storage and restore: `app/installer_core.py`.
- User guidance: `README.md`.
