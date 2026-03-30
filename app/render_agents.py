#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__:
    from .agent_bundle import REPO_ROOT, AgentSpec, load_bundle
    from .install_targets import ProviderModelSelection, all_providers, get_provider
else:  # pragma: no cover
    from agent_bundle import REPO_ROOT, AgentSpec, load_bundle
    from install_targets import ProviderModelSelection, all_providers, get_provider


def provider_agents_dir(target: str, repo_root: Path | None = None) -> Path:
    resolved_root = REPO_ROOT if repo_root is None else repo_root
    return get_provider(target).artifacts.agents_dir(resolved_root)


def provider_index_path(target: str, repo_root: Path | None = None) -> Path:
    resolved_root = REPO_ROOT if repo_root is None else repo_root
    return get_provider(target).artifacts.index_path(resolved_root)


def _provider_name_map(target: str, specs: tuple[AgentSpec, ...]) -> dict[str, str]:
    provider = get_provider(target)
    return {spec.identifier: provider.bundle_agent_name(spec) for spec in specs}


def _claude_name_map(specs: tuple[AgentSpec, ...]) -> dict[str, str]:
    return _provider_name_map("claude", specs)


def _codex_name_map(specs: tuple[AgentSpec, ...]) -> dict[str, str]:
    return _provider_name_map("codex", specs)


def _codex_custom_specs(specs: tuple[AgentSpec, ...]) -> list[AgentSpec]:
    return [spec for spec in specs if spec.mode == "subagent"]


def _codex_primary_spec(specs: tuple[AgentSpec, ...]) -> AgentSpec:
    for spec in specs:
        if spec.mode == "primary":
            return spec
    raise ValueError("Missing Codex primary spec.")


def _replace_agent_names(body: str, name_map: dict[str, str], *, mention_prefix: str = "@") -> str:
    for source_name, target_name in sorted(name_map.items(), key=lambda item: len(item[0]), reverse=True):
        body = body.replace(f"@{source_name}", f"{mention_prefix}{target_name}")
        body = body.replace(f"`{source_name}`", f"`{target_name}`")
    return body


def _render_toml_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid TOML string for '{field_name}'.")
    return json.dumps(value)


def _render_toml_string_array(value: object, *, field_name: str) -> str:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"Invalid TOML string array for '{field_name}'.")
    return "[" + ", ".join(json.dumps(item) for item in value) + "]"


def _render_toml_multiline_literal(value: str, *, field_name: str) -> str:
    if "'''" in value:
        raise ValueError(f"Unsupported triple single quote in '{field_name}'.")
    return "'''\n" + value + "'''"


def _render_tools(metadata: dict[str, object]) -> list[str]:
    tools = metadata.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("Missing tools metadata for OpenCode target.")
    lines = []
    for key in ("write", "edit", "bash"):
        value = tools.get(key)
        if not isinstance(value, bool):
            raise ValueError(f"Invalid tool flag '{key}'.")
        lines.append(f"  {key}: {'true' if value else 'false'}")
    return lines


def _model_profile_for_provider(
    target: str,
    spec: AgentSpec,
    model_selection: ProviderModelSelection | None = None,
):
    return get_provider(target).resolve_role_model_config(spec, model_selection=model_selection)


def render_opencode_agent(spec: AgentSpec, model_selection: ProviderModelSelection | None = None) -> str:
    provider = get_provider("opencode")
    metadata = spec.opencode_metadata
    temperature = metadata.get("temperature")
    if not isinstance(temperature, (int, float)):
        raise ValueError(f"Invalid OpenCode temperature for {spec.identifier}.")

    model_profile = provider.resolve_role_model_config(spec, model_selection=model_selection)

    body = spec.instruction_source_for_target("opencode").read_text(encoding="utf-8")
    if not body.endswith("\n"):
        body += "\n"

    front_matter = [
        "---",
        f"description: {spec.description}",
        f"mode: {spec.mode}",
        f"model: {model_profile.model}",
        f"reasoningEffort: {model_profile.effort}",
        f"temperature: {temperature}",
        "tools:",
        *_render_tools(metadata),
        "---",
    ]
    return "\n".join(front_matter) + "\n" + body


def render_claude_agent(
    spec: AgentSpec,
    claude_name_map: dict[str, str],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    provider = get_provider("claude")
    metadata = spec.metadata_for_target("claude")
    name = metadata.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"Invalid Claude agent name for {spec.identifier}.")

    tools = metadata.get("tools")
    if not isinstance(tools, list) or not tools or not all(isinstance(tool, str) and tool for tool in tools):
        raise ValueError(f"Invalid Claude tools for {spec.identifier}.")

    model_profile = provider.resolve_role_model_config(spec, model_selection=model_selection)

    body = _replace_agent_names(spec.instruction_source_for_target("claude").read_text(encoding="utf-8"), claude_name_map)
    if not body.endswith("\n"):
        body += "\n"

    mapping_line = f"Claude Code agent name: @{name} maps to role `{spec.identifier}`."
    front_matter = [
        "---",
        f"name: {name}",
        f"description: {spec.description}",
        f"tools: {', '.join(tools)}",
        f"model: {model_profile.model}",
        f"effort: {model_profile.effort}",
        "---",
    ]
    return "\n".join(front_matter) + "\n" + mapping_line + "\n\n" + body


def render_codex_agent(
    spec: AgentSpec,
    codex_name_map: dict[str, str],
    model_selection: ProviderModelSelection | None = None,
    rendered_name: str | None = None,
) -> str:
    provider = get_provider("codex")
    metadata = spec.metadata_for_target("codex")
    name = metadata.get("name") if rendered_name is None else rendered_name
    sandbox_mode = metadata.get("sandbox_mode")
    model_profile = provider.resolve_role_model_config(spec, model_selection=model_selection)

    body = _replace_agent_names(
        spec.instruction_source_for_target("codex").read_text(encoding="utf-8"),
        codex_name_map,
        mention_prefix="",
    )
    if not body.endswith("\n"):
        body += "\n"

    lines = [
        f"name = {_render_toml_string(name, field_name='name')}",
        f"description = {_render_toml_string(spec.description, field_name='description')}",
        f"model = {_render_toml_string(model_profile.model, field_name='model')}",
        f"model_reasoning_effort = {_render_toml_string(model_profile.effort, field_name='model_reasoning_effort')}",
        f"sandbox_mode = {_render_toml_string(sandbox_mode, field_name='sandbox_mode')}",
    ]
    lines.append(
        f"developer_instructions = {_render_toml_multiline_literal(body, field_name='developer_instructions')}"
    )
    return "\n".join(lines) + "\n"


def render_codex_managed_guidance(specs: tuple[AgentSpec, ...]) -> str:
    return _replace_agent_names(
        _codex_primary_spec(specs).instruction_source_for_target("codex").read_text(encoding="utf-8"),
        _codex_name_map(specs),
        mention_prefix="",
    ).rstrip("\n")


def _codex_compatibility_names(spec: AgentSpec) -> list[str]:
    provider = get_provider("codex")
    return provider.compatibility_agent_names(spec)


def _codex_agent_output_names(spec: AgentSpec, codex_name_map: dict[str, str]) -> list[str]:
    return [codex_name_map[spec.identifier], *_codex_compatibility_names(spec)]


def render_agents_index(
    specs: tuple[AgentSpec, ...],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    primary = [spec for spec in specs if spec.mode == "primary"]
    subagents = [spec for spec in specs if spec.mode == "subagent"]

    lines = [
        "# Agents",
        "",
        "Documentation for the checked-in OpenCode artifacts and install behavior.",
        "",
        "## Primary",
        "",
    ]

    for spec in primary:
        lines.extend(
            [
                f"- `{spec.display_name}`",
                f"  - file: `providers/opencode/agents/{spec.identifier}.md`",
                f"  - model: `{_model_profile_for_provider('opencode', spec, model_selection).model}`",
                f"  - reasoning: `{_model_profile_for_provider('opencode', spec, model_selection).effort}`",
                f"  - role: {spec.role}",
                "",
            ]
        )

    lines.extend(["## Subagents", ""])
    for spec in subagents:
        lines.extend(
            [
                f"- `{spec.display_name}`",
                f"  - file: `providers/opencode/agents/{spec.identifier}.md`",
                f"  - model: `{_model_profile_for_provider('opencode', spec, model_selection).model}`",
                f"  - reasoning: `{_model_profile_for_provider('opencode', spec, model_selection).effort}`",
                f"  - role: {spec.role}",
                "",
            ]
        )

    lines.extend(
        [
            "## Install names and paths",
            "",
            "Install file names match the checked-in artifacts and are always copied to `~/.config/opencode/agents/`.",
            "Recommended installs use the checked-in artifacts in `providers/opencode/agents/` as-is.",
            "Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.",
            "",
            "## Default behavior",
            "",
            "- Enable `480-architect` by default and set `default_agent` during install.",
            "- `--no-activate-default` or `BOOTSTRAP_ACTIVATE_DEFAULT=0` leaves `default_agent` unchanged.",
            "- Uninstall restores the previous default only when bootstrap state recorded an activation and the current setting is still `480-architect`.",
            "",
            "## Source of truth",
            "",
            "- Common agent definitions: `bundles/common/agents.json`.",
            "- Common instruction bodies: `bundles/common/instructions/`.",
            "- Provider install paths and model-selection schema: `app/providers.py`.",
            "- Provider artifact rendering: `app/render_agents.py`.",
            "- Install/uninstall entrypoint: `app/manage_agents.py`.",
            "- State storage and restore: `app/installer_core.py`.",
            "- User guidance: `README.md`.",
            "",
        ]
    )

    return "\n".join(lines)


def render_claude_agents_index(
    specs: tuple[AgentSpec, ...],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    claude_name_map = _claude_name_map(specs)
    primary = [spec for spec in specs if spec.mode == "primary"]
    subagents = [spec for spec in specs if spec.mode == "subagent"]

    lines = [
        "# Claude Agents",
        "",
        "Documentation for the checked-in Claude Code artifacts and install behavior.",
        "",
        "## Name mapping",
        "",
    ]

    for spec in specs:
        claude_name = claude_name_map[spec.identifier]
        lines.append(f"- `{spec.identifier}` -> `{claude_name}` (`providers/claude/agents/{claude_name}.md`)")

    lines.extend(["", "## Primary", ""])
    for spec in primary:
        model_profile = _model_profile_for_provider("claude", spec, model_selection)
        lines.extend(
            [
                f"- `{claude_name_map[spec.identifier]}`",
                f"  - maps from: `{spec.identifier}`",
                f"  - file: `providers/claude/agents/{claude_name_map[spec.identifier]}.md`",
                f"  - model: `{model_profile.model}`",
                f"  - effort: `{model_profile.effort}`",
                "",
            ]
        )

    lines.extend(["## Subagents", ""])
    for spec in subagents:
        model_profile = _model_profile_for_provider("claude", spec, model_selection)
        lines.extend(
            [
                f"- `{claude_name_map[spec.identifier]}`",
                f"  - maps from: `{spec.identifier}`",
                f"  - file: `providers/claude/agents/{claude_name_map[spec.identifier]}.md`",
                f"  - model: `{model_profile.model}`",
                f"  - effort: `{model_profile.effort}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Install names and paths",
            "",
            "Install files use the Claude-specific names above and are copied to `~/.claude/agents/` or `<project>/.claude/agents/`.",
            "Recommended installs use the checked-in artifacts in `providers/claude/agents/` as-is.",
            "Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.",
            "",
            "## Default behavior",
            "",
            "- Default activation is optional and only sets `agent` to `480-architect` when `--activate-default` is used.",
            "- Uninstall restores the previous value only when the current `agent` is still `480-architect`.",
            "",
            "## Team behavior",
            "",
            "- In environments where Claude Code agent teams are enabled, `480-architect` coordinates the default three-person team (`480-developer`, `480-code-reviewer`, `480-code-reviewer2`).",
            "- Add `480-code-scanner` only when repository scanning is actually needed.",
            "- During install, the installer asks whether to enable the agent teams experimental flag and merges `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` into the `env` section of `settings.json` when enabled.",
            "- Uninstall leaves the teams experimental flag env setting untouched.",
            "- When team support is disabled or unsupported, `480-architect` follows the same Task Brief-based flow directly as the single-orchestrator fallback.",
            "",
            "## Source of truth",
            "",
            "- Common agent definitions: `bundles/common/agents.json`.",
            "- Default instruction bodies: `bundles/common/instructions/`.",
            "- Claude provider-specific override bodies, if any: `providers/claude/instructions/`.",
            "- Provider install paths and model-selection schema: `app/providers.py`.",
            "- Provider artifact rendering: `app/render_agents.py`.",
            "- Install/uninstall entrypoint: `app/manage_agents.py`.",
            "- State storage and restore: `app/installer_core.py`.",
            "- User guidance: `README.md`.",
            "",
        ]
    )

    return "\n".join(lines)


def render_codex_agents_index(
    specs: tuple[AgentSpec, ...],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    codex_name_map = _codex_name_map(specs)
    subagents = _codex_custom_specs(specs)

    lines = [
        "# Codex CLI Agents",
        "",
        "Documentation for the checked-in Codex CLI artifacts and install behavior.",
        "",
        "## Main Prompt",
        "",
        "Codex uses the 480ai managed block in the root `AGENTS.md` as the architect main prompt.",
        "The managed block source is the Codex-specific architect instruction body (`providers/codex/instructions/480-architect.md`), and there is no separate architect custom agent.",
        "",
        "## Name mapping",
        "",
    ]

    for spec in subagents:
        codex_name = codex_name_map[spec.identifier]
        lines.append(f"- `{spec.identifier}` -> `{codex_name}` (`providers/codex/agents/{codex_name}.toml`)")

    lines.extend(["", "## Custom agents", "", "Codex custom agents provide only the four subagents below.", ""])
    for spec in subagents:
        model_profile = _model_profile_for_provider("codex", spec, model_selection)
        metadata = spec.metadata_for_target("codex")
        lines.extend(
            [
                f"- `{codex_name_map[spec.identifier]}`",
                f"  - maps from: `{spec.identifier}`",
                f"  - file: `providers/codex/agents/{codex_name_map[spec.identifier]}.toml`",
                f"  - model: `{model_profile.model}`",
                f"  - reasoning: `{model_profile.effort}`",
                f"  - sandbox: `{metadata['sandbox_mode']}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Install names and paths",
            "",
            "Install files are copied to `~/.codex/agents/` or `<project>/.codex/agents/`.",
            "User scope adds the 480ai managed block to `~/.codex/AGENTS.md`; project scope adds it to the repository root `AGENTS.md`.",
            "Codex config follows the official contract and applies only minimal merges to `~/.codex/config.toml` or `<project>/.codex/config.toml`.",
            "Install preserves existing settings and only applies `features.multi_agent = true` and `agents.max_depth = 2`.",
            "Codex CLI uses the `name` field in each TOML as the custom agent name.",
            "The root `AGENTS.md` 480ai managed block uses the architect main prompt body verbatim.",
            "This architect workflow is for the root Codex session only, and the `480-developer`/reviewer/scanner subagents follow their own custom agent instructions.",
            "Existing user content is preserved and only the 480ai managed block is appended.",
            "Reinstall replaces the existing 480ai managed block rather than duplicating it.",
            "Uninstall removes only the 480ai managed block.",
            "Codex install/uninstall also clean up legacy `480-architect.toml` and `480.toml` leftovers when present.",
            "",
            "## Codex delegation model",
            "",
            "- Codex uses a native subagent workflow. The architect spawns `480-developer`, and the developer uses reviewer/scanner subagents only when needed.",
            "- The default delegation depth is 2: architect(depth 0) -> developer(depth 1) -> reviewer/scanner(depth 2).",
            "- The default reviewer flow is parallel: call `480-code-reviewer` and `480-code-reviewer2` together.",
            "- If `480-code-reviewer2` returns a delegation infrastructure blocker, do not re-request `480-code-reviewer`; wait for `480-code-reviewer` to finish if it is still pending, then retry `480-code-reviewer2` alone exactly once before surfacing the blocker upstream.",
            "- Reviewers review in-thread. `480-code-reviewer` and `480-code-reviewer2` do not spawn additional subagents.",
            "- Keep the concurrent agent budget narrow. Outside the review step, the default path activates only one child agent at a time.",
            "- When possible, the architect plans and delegates with a dedicated worktree and task branch as the default operating model.",
            "- Merge or completed worktree deletion only happens when the user explicitly requests it.",
            "- The current parent session owns each child lifecycle end-to-end: spawn, follow-up, retry, result collection, wait, and explicit close.",
            "- Do not treat the active workflow as complete while any child still has pending follow-up, retry, result collection, or wait work owned by that parent session.",
            "- Close a child only after its latest loop is complete and the parent session has no remaining follow-up, retry, result collection, or wait responsibility for it.",
            "- When waiting on a Codex child agent, prefer longer waits over short polling loops.",
            "- Do not repeat user-facing `still waiting` messages when there is no meaningful state change.",
            "- User-facing wait updates should only report blockers, completion, real state changes, or long delays that help decision-making.",
            "- Use follow-up status checks sparingly and do not make them the default waiting pattern.",
            "- Workspace resolution should prefer the Task Brief path and explicit absolute repo/worktree paths, falling back to the current working directory only when there is no stronger hint.",
            "- Treat a spawn response with no `agent_id`, or any non-structured spawn response, as `spawn_failure`.",
            "- Classify `spawn_failure`, thread limit failures, and usage limit failures as delegation infrastructure blockers, not implementation blockers.",
            "- If the blocker remains after one retry in the same session, return only a structured blocker report to the current parent session/thread.",
            "- Low-risk fallback: if one reviewer has approved and the other reviewer is blocked only by delegation infrastructure, the architect may run an independent diff review when the changed files are limited to prompts, docs, config metadata, or tests. Continue only if that review finds no required changes. Do not waive any explicit change request from either reviewer.",
            "- Do not make `new session` or `exception allowed` the default path for users.",
            "",
            "You can call this directly from a Codex CLI prompt like this:",
            "The document and examples use Codex's actual natural-language call pattern.",
            "",
            "```text",
            "Plan the next work for docs/480ai/example-topic/001-example-task.md.",
            "Have 480-developer implement docs/480ai/example-topic/001-example-task.md.",
            "Have 480-developer request review from 480-code-reviewer and 480-code-reviewer2 in parallel, then return a completion report after both approvals.",
            "```",
            "Recommended installs use the checked-in artifacts in `providers/codex/agents/` as-is.",
            "Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.",
            "",
            "## Scope notes",
            "",
            "The Codex CLI installer manages only the custom agents and the 480ai managed AGENTS block.",
            "Architect rules apply only to the root session, and subagents follow their own custom agent instructions.",
            "Do not touch user-written content or any AGENTS.md content outside the 480ai managed block.",
            "",
            "## Source of truth",
            "",
            "- Common agent definitions: `bundles/common/agents.json`.",
            "- Common instruction bodies: `bundles/common/instructions/`.",
            "- Codex provider-specific override bodies, if any: `providers/codex/instructions/`.",
            "- Provider install paths and model-selection schema: `app/providers.py`.",
            "- Provider artifact rendering: `app/render_agents.py`.",
            "- Install/uninstall entrypoint: `app/manage_agents.py`.",
            "- State storage and restore: `app/installer_core.py`.",
            "- User guidance: `README.md`.",
            "",
        ]
    )

    return "\n".join(lines)


def _render_provider_agent(
    target: str,
    spec: AgentSpec,
    name_map: dict[str, str],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    if target == "opencode":
        return render_opencode_agent(spec, model_selection=model_selection)
    if target == "claude":
        return render_claude_agent(spec, name_map, model_selection=model_selection)
    if target == "codex":
        return render_codex_agent(spec, name_map, model_selection=model_selection)
    raise ValueError(f"Unsupported provider renderer: {target}")


def _render_provider_index(
    target: str,
    specs: tuple[AgentSpec, ...],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    if target == "opencode":
        return render_agents_index(specs, model_selection=model_selection)
    if target == "claude":
        return render_claude_agents_index(specs, model_selection=model_selection)
    if target == "codex":
        return render_codex_agents_index(specs, model_selection=model_selection)
    raise ValueError(f"Unsupported provider index renderer: {target}")


def _expected_provider_outputs(
    target: str,
    specs: tuple[AgentSpec, ...],
    name_map: dict[str, str],
    *,
    repo_root: Path | None = None,
    model_selection: ProviderModelSelection | None = None,
) -> dict[Path, str]:
    provider = get_provider(target)
    agents_dir = provider_agents_dir(target, repo_root=repo_root)
    expected_outputs: dict[Path, str] = {}
    for spec in specs:
        if target == "codex" and spec.mode == "primary":
            continue
        output_names = [name_map[spec.identifier]]
        if target == "codex":
            output_names = _codex_agent_output_names(spec, name_map)
        for output_name in output_names:
            contents = _render_provider_agent(
                target,
                spec,
                name_map,
                model_selection=model_selection,
            )
            if target == "codex":
                contents = render_codex_agent(
                    spec,
                    name_map,
                    model_selection=model_selection,
                    rendered_name=output_name,
                )
            expected_outputs[agents_dir / f"{output_name}{provider.artifacts.agent_file_extension}"] = contents
    return expected_outputs


def _actual_managed_paths(directory: Path, *, suffix: str) -> set[Path]:
    if not directory.exists():
        return set()
    return {path for path in directory.iterdir() if path.is_file() and path.suffix == suffix}


def _check_directory_outputs(expected_outputs: dict[Path, str], *, directory: Path, suffix: str) -> list[Path]:
    mismatches: list[Path] = []
    expected_paths = set(expected_outputs)
    actual_paths = _actual_managed_paths(directory, suffix=suffix)

    for path in sorted(expected_paths - actual_paths):
        mismatches.append(path)
    for path in sorted(actual_paths - expected_paths):
        mismatches.append(path)
    for path in sorted(expected_paths & actual_paths):
        if path.read_text(encoding="utf-8") != expected_outputs[path]:
            mismatches.append(path)
    return mismatches


def _write_directory_outputs(expected_outputs: dict[Path, str], *, directory: Path, suffix: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    expected_paths = set(expected_outputs)
    for path in _actual_managed_paths(directory, suffix=suffix) - expected_paths:
        path.unlink()
    for path, contents in expected_outputs.items():
        path.write_text(contents, encoding="utf-8")


def write_outputs() -> None:
    specs = load_bundle()
    for provider in all_providers():
        name_map = _provider_name_map(provider.identifier, specs)
        _write_directory_outputs(
            _expected_provider_outputs(provider.identifier, specs, name_map),
            directory=provider_agents_dir(provider.identifier),
            suffix=provider.artifacts.agent_file_extension,
        )
        provider_index_path(provider.identifier).write_text(
            _render_provider_index(provider.identifier, specs),
            encoding="utf-8",
        )


def check_outputs() -> int:
    specs = load_bundle()
    mismatches: list[Path] = []
    for provider in all_providers():
        name_map = _provider_name_map(provider.identifier, specs)
        mismatches.extend(
            _check_directory_outputs(
                _expected_provider_outputs(provider.identifier, specs, name_map),
                directory=provider_agents_dir(provider.identifier),
                suffix=provider.artifacts.agent_file_extension,
            )
        )
        index_path = provider_index_path(provider.identifier)
        expected_index = _render_provider_index(provider.identifier, specs)
        if not index_path.exists() or index_path.read_text(encoding="utf-8") != expected_index:
            mismatches.append(index_path)

    if not mismatches:
        print("Agent outputs are up to date.")
        return 0

    print("Agent outputs are out of date:", file=sys.stderr)
    for path in mismatches:
        print(path.relative_to(REPO_ROOT), file=sys.stderr)
    return 1


def write_provider_outputs(
    target: str,
    *,
    repo_root: Path,
    model_selection: ProviderModelSelection | None = None,
) -> Path:
    specs = load_bundle()
    provider = get_provider(target)
    name_map = _provider_name_map(target, specs)
    agents_dir = provider_agents_dir(target, repo_root=repo_root)
    _write_directory_outputs(
        _expected_provider_outputs(
            target,
            specs,
            name_map,
            repo_root=repo_root,
            model_selection=model_selection,
        ),
        directory=agents_dir,
        suffix=provider.artifacts.agent_file_extension,
    )
    provider_index_path(target, repo_root=repo_root).write_text(
        _render_provider_index(target, specs, model_selection=model_selection),
        encoding="utf-8",
    )
    return agents_dir


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"check", "write"}:
        print("Usage: render_agents.py [check|write]", file=sys.stderr)
        return 1
    if argv[1] == "write":
        write_outputs()
        return 0
    return check_outputs()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
