# 480ai bootstrap

This repo packages the current five-agent OpenCode setup as a private-repo-friendly install bundle.

## Included agents

- `architect`
- `developer`
- `code-reviewer`
- `code-reviewerer`
- `repo-scout`

The installer also sets `default_agent` to `architect` in `~/.config/opencode/opencode.json`.

## Install

```bash
git clone https://github.com/480/480ai.git
cd 480ai
./install.sh
```

## Update

```bash
git pull
./install.sh
```

Re-running install is safe when the recorded bootstrap state is still trustworthy. If a prior install or uninstall was interrupted and the state is ambiguous or corrupted, the scripts fail conservatively before guessing about backups or ownership.

## Uninstall

```bash
./uninstall.sh
```

Uninstall removes only the agents managed by this repo. If one of those agent files existed before install, the installer keeps a backup and uninstall restores it when possible.

## Conservative failure contract

- The bootstrap scripts prefer an early safe failure over automatic recovery when state is ambiguous.
- If both a live file and backup exist, or if bootstrap state/config is corrupted, install/uninstall stops before making destructive guesses.
- In those cases you may need to inspect `~/.config/opencode/agents/`, `~/.config/opencode/opencode.json`, and `~/.config/opencode/.480ai-bootstrap/` and resolve the conflict manually.
- Retry only after the state is clearly valid again, or after you have manually cleaned up the ambiguous files.

## Validate

```bash
python3 -m unittest -v
```

## Installed locations

- Agents: `~/.config/opencode/agents/*.md`
- OpenCode config: `~/.config/opencode/opencode.json`
- Install state: `~/.config/opencode/.480ai-bootstrap/`

## Notes

- This repo is the source of truth for the five agent files under `agents/`.
- The installer does not publish packages or depend on a registry.
- `default_agent` is restored on uninstall only when the current config still points to `architect`.
