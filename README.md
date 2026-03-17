# 480/ai bootstrap

This repo packages the current five-agent OpenCode setup as a private-repo-friendly install bundle.

## What this repo contains

- OpenCode agent payloads under `agents/`
- Install and uninstall entrypoints: `install.sh`, `uninstall.sh`
- Private-repo-friendly remote bootstrap scripts: `bootstrap/install-remote.sh`, `bootstrap/uninstall-remote.sh`
- Installer implementation: `scripts/manage_agents.py`
- Regression tests: `tests/test_installation.py`
- Coding-team notes: `docs/coding-team/`

## Included agents

- `architect`
- `developer`
- `code-reviewer`
- `code-reviewer2`
- `code-scanner`

The installer also sets `default_agent` to `architect` in `~/.config/opencode/opencode.json`.

See `AGENTS.md` for role, model, and reasoning details.

## Install

These commands require authenticated access to the private `480/ai` repository.

With `gh` login:

```bash
(tmp="$(mktemp)" && curl -fsSL -H "Accept: application/vnd.github.raw" -H "Authorization: Bearer $(gh auth token)" -o "$tmp" "https://api.github.com/repos/480/ai/contents/bootstrap/install-remote.sh?ref=main" && sh "$tmp"; status=$?; rm -f "$tmp"; exit "$status")
```

With `GITHUB_TOKEN` already exported:

```bash
(tmp="$(mktemp)" && curl -fsSL -H "Accept: application/vnd.github.raw" -H "Authorization: Bearer ${GITHUB_TOKEN:?export GITHUB_TOKEN first}" -o "$tmp" "https://api.github.com/repos/480/ai/contents/bootstrap/install-remote.sh?ref=main" && sh "$tmp"; status=$?; rm -f "$tmp"; exit "$status")
```

The remote bootstrap script downloads the repo to a temporary directory and then runs the normal `./install.sh` flow.

## Uninstall

With `gh` login:

```bash
(tmp="$(mktemp)" && curl -fsSL -H "Accept: application/vnd.github.raw" -H "Authorization: Bearer $(gh auth token)" -o "$tmp" "https://api.github.com/repos/480/ai/contents/bootstrap/uninstall-remote.sh?ref=main" && sh "$tmp"; status=$?; rm -f "$tmp"; exit "$status")
```

With `GITHUB_TOKEN` already exported:

```bash
(tmp="$(mktemp)" && curl -fsSL -H "Accept: application/vnd.github.raw" -H "Authorization: Bearer ${GITHUB_TOKEN:?export GITHUB_TOKEN first}" -o "$tmp" "https://api.github.com/repos/480/ai/contents/bootstrap/uninstall-remote.sh?ref=main" && sh "$tmp"; status=$?; rm -f "$tmp"; exit "$status")
```

The remote bootstrap script downloads the repo to a temporary directory and then runs the normal `./uninstall.sh` flow.

## Update

Re-run the install one-liner for the current branch or ref you want to apply.

With `gh` login:

```bash
(tmp="$(mktemp)" && curl -fsSL -H "Accept: application/vnd.github.raw" -H "Authorization: Bearer $(gh auth token)" -o "$tmp" "https://api.github.com/repos/480/ai/contents/bootstrap/install-remote.sh?ref=main" && sh "$tmp"; status=$?; rm -f "$tmp"; exit "$status")
```

With `GITHUB_TOKEN` already exported:

```bash
(tmp="$(mktemp)" && curl -fsSL -H "Accept: application/vnd.github.raw" -H "Authorization: Bearer ${GITHUB_TOKEN:?export GITHUB_TOKEN first}" -o "$tmp" "https://api.github.com/repos/480/ai/contents/bootstrap/install-remote.sh?ref=main" && sh "$tmp"; status=$?; rm -f "$tmp"; exit "$status")
```

Re-running install is safe when the recorded bootstrap state is still trustworthy. If a prior install or uninstall was interrupted and the state is ambiguous or corrupted, the scripts fail conservatively before guessing about backups or ownership.

## Conservative failure contract

- The bootstrap scripts prefer an early safe failure over automatic recovery when state is ambiguous.
- If both a live file and backup exist, or if bootstrap state/config is corrupted, install/uninstall stops before making destructive guesses.
- In those cases you may need to inspect `~/.config/opencode/agents/`, `~/.config/opencode/opencode.json`, and `~/.config/opencode/.480ai-bootstrap/` and resolve the conflict manually.
- Retry only after the state is clearly valid again, or after you have manually cleaned up the ambiguous files.

## Validate

```bash
python3 -m unittest -v
```

## Repository layout

- `agents/` - source-of-truth agent files bundled by the installer
- `bootstrap/` - remote bootstrap helpers for curl install and uninstall
- `docs/coding-team/` - planning/task-brief notes that belong in version control
- `scripts/` - install/uninstall implementation
- `tests/` - regression coverage for installer behavior

## Installed locations

- Agents: `~/.config/opencode/agents/*.md`
- OpenCode config: `~/.config/opencode/opencode.json`
- Install state: `~/.config/opencode/.480ai-bootstrap/`

## Notes

- This repo is the source of truth for the five agent files under `agents/`.
- The installer does not publish packages or depend on a registry.
- `default_agent` is restored on uninstall only when the current config still points to `architect`.
- New coding-team task briefs should live under `docs/coding-team/`, not `misc/`.
- `misc/` is intentionally ignored and should not be committed.
