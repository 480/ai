# Repository scout report

## Detected stack

- Languages: Python and shell (`scripts/manage_agents.py`, `tests/test_installation.py`, `install.sh`, `uninstall.sh`, `bootstrap/install-remote.sh`, `bootstrap/uninstall-remote.sh`).
- Frameworks and libraries: Python standard library only (`json`, `os`, `pathlib`, `unittest`) in runtime and tests; no external framework layer (`scripts/manage_agents.py`, `tests/test_installation.py`).
- Build and packaging: no package manifest or build tool (no `pyproject.toml`, `requirements*.txt`, `package.json`, `Cargo.toml`, etc. found). Install entrypoints are direct scripts (`install.sh`, `uninstall.sh`) and remote bootstrap scripts (`bootstrap/install-remote.sh`, `bootstrap/uninstall-remote.sh`).
- Deployment/runtime: repo is installed into OpenCode user config paths under `~/.config/opencode` (runtime copy into `~/.config/opencode/agents/`, state in `~/.config/opencode/.480ai-bootstrap/`, config in `~/.config/opencode/opencode.json`) with download flow via GitHub tarball in bootstrap scripts.

## Conventions

- Formatting and linting: no formatter/lint/type-checker config found (`pyproject.toml`, `ruff.toml`, `.pre-commit-config.yaml`, `Makefile`, `tox.ini` absent).
- Type checking: no explicit type checker config found; scripts still use explicit annotations and typed containers (`dict[str, int]`, return annotations).
- Testing: Python `unittest` suite in `tests/test_installation.py` with long-form behavioral tests and temp dirs; tests assert installer state and safety invariants.
- Documentation: architecture/source-of-truth guidance in `AGENTS.md`, agent instructions in `agents/*.md`, and operational docs in `README.md`; task plans under `docs/coding-team/`.

## Linting and testing commands

- `python3 -m unittest -v` (documented in `README.md:88`, runs `tests/test_installation.py`).
- No single "pre-check" command found for lint/typecheck (no config evidence in repo).

## Project structure hotspots

- `scripts/manage_agents.py`: highest-change core installer/uninstaller with state bookkeeping, backup handling, and config mutation.
- `agents/*.md`: authoritative prompt payloads for all OpenCode agents; changes here directly alter agent behavior.
- `AGENTS.md`: maps role/model metadata and clarifies install source-of-truth and default-agent behavior.
- `install.sh` / `uninstall.sh`: thin script entrypoints that execute installer logic.
- `bootstrap/install-remote.sh` / `bootstrap/uninstall-remote.sh`: remote bootstrap entrypoints that fetch repo tarball then call local entrypoints.
- `tests/test_installation.py`: comprehensive installer behavior safety tests, likely to fail fast if semantics change.
- `README.md`: installation command surface and failure policy docs.
- Boundaries: source agents in `agents/`, execution in `scripts/`, operations in `bootstrap/`, test suite in `tests/`, plans in `docs/coding-team/`.

## Do and don't patterns

- Do: fail-fast behavior for guardrail conditions using explicit `SystemExit` and descriptive errors in installer logic (`scripts/manage_agents.py`).
- Do: safe install/uninstall state model (`state.json`) with backup/restore paths (`STATE_DIR`, `BACKUP_DIR`) and metadata checks (`scripts/manage_agents.py`).
- Do: strict path safety checks (reject symlinked paths) before mutating files (`scripts/manage_agents.py`).
- Do: keep user config changes scoped (`.config/opencode/opencode.json`) and restore default agent only when it was `architect` at uninstall time (`scripts/manage_agents.py`).
- Don't: no framework-level dependency injection/container patterns, ORM, logging libraries, or app frameworks are present; logic is procedural utility-style Python.
- Don't: no CI/build automation pipeline is defined in-repo (`.github/workflows`, `Makefile`, `tox.ini` not present).
- Don't: no external packaging/build tooling is used for this repo; it is operational scripts + installed prompt assets.

## Agent architecture and prompt-modification path

- Repository defines five agents and default behavior in `AGENTS.md` (`architect`, `developer`, `code-reviewer`, `code-reviewer2`, `code-scanner`).
- Prompt/behavior definitions are in `agents/architect.md`, `agents/developer.md`, `agents/code-reviewer.md`, `agents/code-reviewer2.md`, `agents/code-scanner.md`.
- Installed prompt path at runtime is handled by `scripts/manage_agents.py` (copies `agents/*.md` into `~/.config/opencode/agents/*.md`, writes `default_agent: architect` into `~/.config/opencode/opencode.json`).
- To change language-response behavior, edit the relevant agent markdown instruction blocks first, then propagate by changing installer inputs and reinstalling (`install.sh` or bootstrap flow).
