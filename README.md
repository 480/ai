# 480 agents

> Internal use only: this repository is currently intended for Imweb employees.

Install the five 480 agents into OpenCode, Claude Code, and Codex CLI to get a development agent set optimized for the plan -> implement -> review loop.

## What are the 480 agents?

- Development agents optimized for the plan -> implement -> review loop
- https://5k.gg/480ai

## Providers

- OpenCode: user-scope install, with `480-architect` enabled by default and optional desktop notifications through a local plugin hook
- Claude Code: user/project-scope install, with `480-architect` enabled when selected. The installer asks about the agent teams experimental flag and desktop notifications, then writes it into `settings.json` and its `env` block when the flag is enabled.
- Codex CLI: user/project-scope install. The root `AGENTS.md` 480ai managed block provides the architect main prompt, and the custom agent set contains only the four subagents. Review runs are normally parallel with `480-code-reviewer` and `480-code-reviewer2`; if `480-code-reviewer2` hits a delegation infrastructure blocker, the developer does not re-request `480-code-reviewer`, waits for `480-code-reviewer` to finish if it is still pending, then retries `480-code-reviewer2` alone exactly once before escalating. The rest of the delegation budget stays narrow. Install merges `features.multi_agent = true`, `agents.max_depth = 2`, and optional desktop notifications into `config.toml`. Install state and no-op delegation validation are available through `python3 -m app.manage_agents verify --target codex --scope user`.

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
