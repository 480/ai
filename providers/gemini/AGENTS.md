# Gemini CLI Agents

Documentation for the checked-in Gemini CLI artifacts and install behavior.

## Name mapping

- `480-architect` -> `_480-architect` (`providers/gemini/agents/480-architect.md`)
- `480-developer` -> `_480-developer` (`providers/gemini/agents/480-developer.md`)
- `480-code-reviewer` -> `_480-code-reviewer` (`providers/gemini/agents/480-code-reviewer.md`)
- `480-code-reviewer2` -> `_480-code-reviewer2` (`providers/gemini/agents/480-code-reviewer2.md`)
- `480-code-scanner` -> `_480-code-scanner` (`providers/gemini/agents/480-code-scanner.md`)

## Primary

- `_480-architect`
  - maps from: `480-architect`
  - file: `providers/gemini/agents/480-architect.md`
  - model: `inherit`

## Subagents

- `_480-developer`
  - maps from: `480-developer`
  - file: `providers/gemini/agents/480-developer.md`
  - model: `inherit`

- `_480-code-reviewer`
  - maps from: `480-code-reviewer`
  - file: `providers/gemini/agents/480-code-reviewer.md`
  - model: `inherit`

- `_480-code-reviewer2`
  - maps from: `480-code-reviewer2`
  - file: `providers/gemini/agents/480-code-reviewer2.md`
  - model: `inherit`

- `_480-code-scanner`
  - maps from: `480-code-scanner`
  - file: `providers/gemini/agents/480-code-scanner.md`
  - model: `inherit`

## Install names and paths

Install files use the Gemini CLI-specific names above and are copied to `~/.gemini/agents/` or `<project>/.gemini/agents/`.
Recommended installs use the checked-in artifacts in `providers/gemini/agents/` as-is.
Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.

## Default behavior

- Default activation is optional and enables the system prompt override (`GEMINI_SYSTEM_MD=1`) when `--activate-default` is used.
- The override reads `.gemini/system.md` (project) or `~/.gemini/system.md` (user) depending on scope.
- Uninstall restores `system.md` only when it still matches the managed contents.

## Subagent support

- Gemini CLI subagents are enabled by default; this installer ensures `{"experimental": {"enableAgents": true, "enableSubagents": true}}` in `settings.json` for compatibility across Gemini CLI versions.
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
