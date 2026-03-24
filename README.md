# 480 agents

> Internal use only: this repository is currently intended for Imweb employees.

Install the five 480 agents into OpenCode, Claude Code, and Codex CLI to get a development agent set optimized for the plan -> implement -> review loop.

## What are the 480 agents?

- Development agents optimized for the plan -> implement -> review loop
- https://5k.gg/480ai

## Providers

- OpenCode: user-scope install, with `480-architect` enabled by default
- Claude Code: user/project-scope install, with `480-architect` enabled when selected. The installer asks about the agent teams experimental flag and writes it into `settings.json` `env` when enabled.
- Codex CLI: user/project-scope install. The root `AGENTS.md` 480ai managed block provides the architect main prompt, and the custom agent set contains only the four subagents. Review runs are normally parallel with `480-code-reviewer` and `480-code-reviewer2`, and the rest of the delegation budget stays narrow. Install merges only `features.multi_agent = true` and `agents.max_depth = 2` into `config.toml`. Install state and no-op delegation validation are available through `python3 -m app.manage_agents verify --target codex --scope user`.

## Install

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/480/ai/main/bootstrap/install-remote.sh)"
```

This opens a TUI that lets you select multiple providers together.

## Uninstall

```bash
curl -fsSL "https://raw.githubusercontent.com/480/ai/main/bootstrap/uninstall-remote.sh" | sh
```

## License

MIT
