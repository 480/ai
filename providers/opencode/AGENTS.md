# Agents

Documentation for the checked-in OpenCode artifacts and install behavior.

## Primary

- `480-architect`
  - file: `providers/opencode/agents/480-architect.md`
  - model: `openai/gpt-5.4`
  - reasoning: `xhigh`
  - role: planning, scoping, and orchestrating the implementation/review loop

## Subagents

- `480-developer`
  - file: `providers/opencode/agents/480-developer.md`
  - model: `openai/gpt-5.4`
  - reasoning: `medium`
  - role: implementation

- `480-code-reviewer`
  - file: `providers/opencode/agents/480-code-reviewer.md`
  - model: `openai/gpt-5.4`
  - reasoning: `high`
  - role: primary code review

- `480-code-reviewer2`
  - file: `providers/opencode/agents/480-code-reviewer2.md`
  - model: `google/gemini-3-flash-preview`
  - reasoning: `high`
  - role: secondary code review

- `480-code-scanner`
  - file: `providers/opencode/agents/480-code-scanner.md`
  - model: `openai/gpt-5.4-nano`
  - reasoning: `high`
  - role: repository scanning and stack discovery

## Install names and paths

Install file names match the checked-in artifacts and are always copied to `~/.config/opencode/agents/`.
Recommended installs use the checked-in artifacts in `providers/opencode/agents/` as-is.
Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.

## Default behavior

- Enable `480-architect` by default and set `default_agent` during install.
- `--no-activate-default` or `BOOTSTRAP_ACTIVATE_DEFAULT=0` leaves `default_agent` unchanged.
- Uninstall restores the previous default only when bootstrap state recorded an activation and the current setting is still `480-architect`.

## Source of truth

- Common agent definitions: `bundles/common/agents.json`.
- Common instruction bodies: `bundles/common/instructions/`.
- Provider install paths and model-selection schema: `app/providers.py`.
- Provider artifact rendering: `app/render_agents.py`.
- Install/uninstall entrypoint: `app/manage_agents.py`.
- State storage and restore: `app/installer_core.py`.
- User guidance: `README.md`.
