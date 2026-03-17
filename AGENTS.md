# Agents

This repository ships five OpenCode agents.

## Primary

- `architect`
  - file: `agents/architect.md`
  - model: `openai/gpt-5.4`
  - reasoning: `xhigh`
  - role: planning, scoping, and orchestrating the implementation/review loop

## Subagents

- `developer`
  - file: `agents/developer.md`
  - model: `openai/gpt-5.4`
  - reasoning: `medium`
  - role: implementation

- `code-reviewer`
  - file: `agents/code-reviewer.md`
  - model: `openai/gpt-5.4`
  - reasoning: `high`
  - role: primary code review

- `code-reviewer2`
  - file: `agents/code-reviewer2.md`
  - model: `google/antigravity-gemini-3-flash`
  - reasoning: `high`
  - role: secondary code review

- `code-scanner`
  - file: `agents/code-scanner.md`
  - model: `openai/gpt-5.3-codex-spark`
  - reasoning: `xhigh`
  - role: repository scanning and stack discovery

## Installed names

After install, these files are copied into `~/.config/opencode/agents/` with the same filenames.

## Default behavior

- The installer sets `default_agent` to `architect`.
- `uninstall.sh` restores the previous default agent only if the current OpenCode config still points to `architect`.

## Source of truth

- Agent payloads live in `agents/`.
- Install behavior lives in `scripts/manage_agents.py`.
- User-facing install docs live in `README.md`.
