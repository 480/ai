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


def _specs_for_target(target: str, specs: tuple[AgentSpec, ...]) -> tuple[AgentSpec, ...]:
    return tuple(spec for spec in specs if spec.supports_target(target))


def _provider_name_map(target: str, specs: tuple[AgentSpec, ...]) -> dict[str, str]:
    provider = get_provider(target)
    return {spec.identifier: provider.bundle_agent_name(spec) for spec in _specs_for_target(target, specs)}


def _claude_name_map(specs: tuple[AgentSpec, ...]) -> dict[str, str]:
    return _provider_name_map("claude", specs)


def _codex_name_map(specs: tuple[AgentSpec, ...]) -> dict[str, str]:
    return _provider_name_map("codex", specs)


def _gemini_tool_name_map(specs: tuple[AgentSpec, ...]) -> dict[str, str]:
    provider = get_provider("gemini")
    name_map: dict[str, str] = {}
    for spec in _specs_for_target("gemini", specs):
        metadata = spec.metadata_for_target("gemini")
        tool_name = metadata.get("tool_name")
        if tool_name is None:
            tool_name = provider.bundle_agent_name(spec)
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError(f"Invalid Gemini CLI tool_name for {spec.identifier}.")
        name_map[spec.identifier] = tool_name
    return name_map


def _codex_custom_specs(specs: tuple[AgentSpec, ...]) -> list[AgentSpec]:
    return [spec for spec in _specs_for_target("codex", specs) if spec.mode == "subagent"]


def _codex_primary_spec(specs: tuple[AgentSpec, ...]) -> AgentSpec:
    for spec in _specs_for_target("codex", specs):
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


def render_qwen_agent(
    spec: AgentSpec,
    qwen_name_map: dict[str, str],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    provider = get_provider("qwen")
    metadata = spec.metadata_for_target("qwen")
    name = metadata.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"Invalid Qwen Code agent name for {spec.identifier}.")

    tools = metadata.get("tools")
    if not isinstance(tools, list) or not tools or not all(isinstance(tool, str) and tool for tool in tools):
        raise ValueError(f"Invalid Qwen Code tools for {spec.identifier}.")

    model_profile = provider.resolve_role_model_config(spec, model_selection=model_selection)

    body = _replace_agent_names(spec.instruction_source_for_target("qwen").read_text(encoding="utf-8"), qwen_name_map)
    if not body.endswith("\n"):
        body += "\n"

    mapping_line = f"Qwen Code agent name: {name} maps to role `{spec.identifier}`."
    front_matter = [
        "---",
        f"name: {name}",
        f"description: {spec.description}",
    ]
    if model_profile.model != "inherit":
        front_matter.append(f"model: {model_profile.model}")
    tools_yaml = "\n".join(f"  - {t}" for t in tools)
    front_matter.extend(["tools:", tools_yaml, "---"])
    return "\n".join(front_matter) + "\n" + mapping_line + "\n\n" + body


def render_gemini_agent(
    spec: AgentSpec,
    gemini_name_map: dict[str, str],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    provider = get_provider("gemini")
    metadata = spec.metadata_for_target("gemini")
    tool_name = gemini_name_map.get(spec.identifier)
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError(f"Invalid Gemini CLI agent tool name for {spec.identifier}.")

    tools = metadata.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or not tools or not all(isinstance(tool, str) and tool for tool in tools):
            raise ValueError(f"Invalid Gemini CLI tools for {spec.identifier}.")

    model_profile = provider.resolve_role_model_config(spec, model_selection=model_selection)

    body = _replace_agent_names(spec.instruction_source_for_target("gemini").read_text(encoding="utf-8"), gemini_name_map)
    if not body.endswith("\n"):
        body += "\n"

    mapping_line = f"Gemini CLI agent name: {tool_name} maps to role `{spec.identifier}`."
    front_matter = [
        "---",
        f"name: {tool_name}",
        f"description: {spec.description}",
    ]
    if model_profile.model != "inherit":
        front_matter.append(f"model: {model_profile.model}")
    if tools:
        tools_yaml = "\n".join(f"  - {t}" for t in tools)
        front_matter.extend(["tools:", tools_yaml])
    front_matter.append("---")
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
        f"model_reasoning_effort = {_render_toml_string(model_profile.effort, field_name='model_reasoning_effort')}",
        f"sandbox_mode = {_render_toml_string(sandbox_mode, field_name='sandbox_mode')}",
    ]
    if model_selection is not None:
        lines.insert(2, f"model = {_render_toml_string(model_profile.model, field_name='model')}")
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
    specs = _specs_for_target("opencode", specs)
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
    specs = _specs_for_target("claude", specs)
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
            "Codex uses the 480ai managed block in the root `AGENTS.md` as the Software Orchestrator main prompt.",
            "The managed block source is the Codex-specific orchestrator instruction body (`providers/codex/instructions/480-orchestrator.md`), and the design architect is a separate design handoff subagent.",
        "",
        "## Name mapping",
        "",
    ]

    for spec in subagents:
        codex_name = codex_name_map[spec.identifier]
        lines.append(f"- `{spec.identifier}` -> `{codex_name}` (`providers/codex/agents/{codex_name}.toml`)")

    lines.extend(
        [
            "",
            "## Custom agents",
            "",
            "Codex custom agents provide the five subagents below.",
            "Checked-in Codex custom agent TOMLs omit `model`, so spawned sessions inherit the parent session's model by default.",
            "",
        ]
    )
    for spec in subagents:
        model_profile = _model_profile_for_provider("codex", spec, model_selection)
        metadata = spec.metadata_for_target("codex")
        lines.extend(
            [
                f"- `{codex_name_map[spec.identifier]}`",
                f"  - maps from: `{spec.identifier}`",
                f"  - file: `providers/codex/agents/{codex_name_map[spec.identifier]}.toml`",
                f"  - reasoning: `{model_profile.effort}`",
                f"  - sandbox: `{metadata['sandbox_mode']}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Install names and paths",
            "",
            "Install files are copied to the selected Codex user root `agents/` directory or `<project>/.codex/agents/`.",
            "Codex user scope defaults to `~/.codex`, and install/uninstall/verify may target an alternate user root such as `~/.codex-harness` with `--codex-user-root` or `BOOTSTRAP_CODEX_USER_ROOT`.",
            "User scope adds the 480ai managed block to `<codex-user-root>/AGENTS.md`; project scope adds it to the repository root `AGENTS.md`.",
            "Codex config follows the official contract and applies only minimal merges to `<codex-user-root>/config.toml` or `<project>/.codex/config.toml`.",
            "Install preserves existing settings and only applies `features.multi_agent = true`, `agents.max_depth = 1`, and `agents.max_threads = 200`.",
            "An empty interactive root input keeps the default `~/.codex` target.",
            "Alternate-root installs isolate managed agents, `AGENTS.md`, `config.toml`, bootstrap state, and optional desktop notification assets under that selected root only.",
            "Alternate-root verification reports install-state health for the selected root only and does not claim runtime profile activation.",
            "Codex CLI uses the `name` field in each TOML as the custom agent name.",
            "The root `AGENTS.md` 480ai managed block uses the Software Orchestrator main prompt body verbatim.",
            "This orchestrator workflow is for the root Codex session only. Spawned `480-design-architect`/developer/reviewer/scanner sessions must ignore those root-only rules and follow their own custom agent instructions.",
            "Existing user content is preserved and only the 480ai managed block is appended.",
            "Reinstall replaces the existing 480ai managed block rather than duplicating it.",
            "Uninstall removes only the 480ai managed block.",
            "Codex install/uninstall also clean up legacy `480-architect.toml` and `480.toml` leftovers when present.",
            "",
            "## Codex delegation model",
            "",
            "- Codex uses a native subagent workflow. The root Software Orchestrator spawns `480-design-architect`, `480-developer`, reviewer, and scanner subagents as needed.",
            "- The default delegation depth is 1: orchestrator(depth 0) -> subagents(depth 1). Subagents do not spawn additional subagents.",
            "- The root calls `480-design-architect` for every implementation task before Task Brief authoring, including code, tests, configuration, docs-only updates, generated-output synchronization, and bug fixes.",
            "- Pure non-implementation conversation, review, explanation, or status reporting does not need Design Input.",
            "- The root does not author design artifacts; it passes observed facts, constraints, and user intent to `480-design-architect`, then embeds the returned Design Contract or Minimal Transfer Analysis into the Task Brief under `Design Input`.",
            "- Bug fixes are not categorically skipped: `480-design-architect` decides whether a bug fix is MTA-backed maintenance, behavior-changing Design Contract work, or BLOCKED.",
            "- Design Contract or Minimal Transfer Analysis output is embedded into every implementation Task Brief under `Design Input`; v1 does not create separate design artifact files.",
            "- Root state machine: reviewer subagents are the verification gate inside `IMPLEMENTING`, not separate states.",
            "- Developer completion alone does not satisfy `DONE`; normal completion requires both `480-code-reviewer` and `480-code-reviewer2` to approve with exactly `Approved.`, required child sessions to be closed, and no follow-up, retry, or result wait to remain.",
            "- Review findings inside approved scope keep the workflow in `IMPLEMENTING` when the Review Escalation Gate classifies them as `within_scope`; findings classified to an escalation axis are loop telemetry and a retry guard on first appearance, and move the workflow back to `PLANNED` or `BLOCKED` only if the same axis appears again after one developer retry on that Task Brief.",
            "- Reviewer infrastructure blockers follow the existing retry and low-risk fallback rules and never count as reviewer approval.",
            "- The default reviewer flow is parallel: call `480-code-reviewer` and `480-code-reviewer2` together.",
            "",
            "## Review Escalation Gate",
            "",
            "- Before sending any reviewer-requested retry back to `480-developer`, the root runs a Review Escalation Gate that classifies the outcome for that Task Brief as `within_scope` or one of `[contract_semantics]`, `[risk_class]`, `[scope_surface]`, or `[global_change]`.",
            "- `within_scope` remains the baseline classification for ordinary in-scope retries.",
            "- Axis classification is loop telemetry and a retry guard, not an immediate stop condition on first appearance.",
            "- On the first axis-tagged finding for a Task Brief, the root records the escalation axis and allows one developer retry to absorb the review-driven follow-up.",
            "- Track escalation history per Task Brief. If the same escalation axis appears again after one developer retry, stop the loop, move back to `PLANNED` or `BLOCKED`, and ask the user for review instead of continuing reviewer/developer churn.",
            "- Pause reports to the user include the current approved scope, the new reviewer concern, why it exceeds scope, the recommended default `stay with the approved minimal fix`, the alternate `expand scope and re-plan`, and the single decision needed to continue.",
            "- The root orchestrator is the only role that may pause, re-plan, or ask the user for review. Reviewers and `480-developer` emit structured escalation signals only.",
            "",
            "## Review Loop Details",
            "",
            "- If `480-code-reviewer2` returns a delegation infrastructure blocker, do not re-request `480-code-reviewer`; wait for `480-code-reviewer` to finish if it is still pending, then retry `480-code-reviewer2` alone exactly once before surfacing the blocker upstream.",
            "- Reviewers review in-thread. `480-code-reviewer` and `480-code-reviewer2` do not spawn additional subagents.",
            "- Keep the concurrent agent budget narrow. Outside the review step, the default path activates only one child agent at a time.",
            "- When possible, the orchestrator plans and delegates with a dedicated worktree and task branch as the default operating model.",
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
            "- Low-risk fallback: if one reviewer has approved and the other reviewer is blocked only by delegation infrastructure, the orchestrator may run an independent diff review when the changed files are limited to prompts, docs, config metadata, or tests. Continue only if that review finds no required changes. Do not waive any explicit change request from either reviewer.",
            "- Do not make `new session` or `exception allowed` the default path for users.",
            "",
            "You can call this directly from a Codex CLI prompt like this:",
            "The document and examples use Codex's actual natural-language call pattern.",
            "",
            "```text",
            "Plan the next work for docs/480ai/example-topic/001-example-task.md.",
            "Have 480-design-architect produce Design Input for the implementation task and embed it in the Task Brief under `Design Input`.",
            "Have 480-developer implement docs/480ai/example-topic/001-example-task.md.",
            "Have 480-code-reviewer and 480-code-reviewer2 review in parallel, then return required changes or `Approved.`.",
            "If changes are requested, have 480-developer apply them, then re-run the parallel review.",
            "```",
            "Recommended installs use the checked-in artifacts in `providers/codex/agents/` as-is.",
            "Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.",
            "",
            "## Scope notes",
            "",
            "The Codex CLI installer manages only the custom agents and the 480ai managed AGENTS block.",
            "Root orchestrator rules apply only to the root session, and spawned subagents explicitly ignore those root-only rules in favor of their own custom agent instructions.",
            "Do not touch user-written content or any AGENTS.md content outside the 480ai managed block.",
            "",
            "## Source of truth",
            "",
            "- Common agent definitions: `bundles/common/agents.json`.",
            "- Common instruction bodies: `bundles/common/instructions/`.",
            "- Codex provider-specific instruction bodies: `providers/codex/instructions/`.",
            "- Provider install paths and model-selection schema: `app/providers.py`.",
            "- Provider artifact rendering: `app/render_agents.py`.",
            "- Install/uninstall entrypoint: `app/manage_agents.py`.",
            "- State storage and restore: `app/installer_core.py`.",
            "- User guidance: `README.md`.",
            "",
        ]
    )

    return "\n".join(lines)


def render_qwen_agents_index(
    specs: tuple[AgentSpec, ...],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    specs = _specs_for_target("qwen", specs)
    qwen_name_map = _provider_name_map("qwen", specs)
    primary = [spec for spec in specs if spec.mode == "primary"]
    subagents = [spec for spec in specs if spec.mode == "subagent"]

    lines = [
        "# Qwen Code Agents",
        "",
        "Documentation for the checked-in Qwen Code artifacts and install behavior.",
        "",
        "## Name mapping",
        "",
    ]

    for spec in specs:
        qwen_name = qwen_name_map[spec.identifier]
        lines.append(f"- `{spec.identifier}` -> `{qwen_name}` (`providers/qwen/agents/{qwen_name}.md`)")

    lines.extend(["", "## Primary", ""])
    for spec in primary:
        model_profile = _model_profile_for_provider("qwen", spec, model_selection)
        lines.extend(
            [
                f"- `{qwen_name_map[spec.identifier]}`",
                f"  - maps from: `{spec.identifier}`",
                f"  - file: `providers/qwen/agents/{qwen_name_map[spec.identifier]}.md`",
                f"  - model: `{model_profile.model}`",
                "",
            ]
        )

    lines.extend(["## Subagents", ""])
    for spec in subagents:
        model_profile = _model_profile_for_provider("qwen", spec, model_selection)
        lines.extend(
            [
                f"- `{qwen_name_map[spec.identifier]}`",
                f"  - maps from: `{spec.identifier}`",
                f"  - file: `providers/qwen/agents/{qwen_name_map[spec.identifier]}.md`",
                f"  - model: `{model_profile.model}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Install names and paths",
            "",
            "Install files use the Qwen Code-specific names above and are copied to `~/.qwen/agents/` or `<project>/.qwen/agents/`.",
            "Recommended installs use the checked-in artifacts in `providers/qwen/agents/` as-is.",
            "Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.",
            "",
            "## Default behavior",
            "",
            "- Default activation is optional and only sets `default_agent` to `480-architect` when `--activate-default` is used.",
            "- Uninstall restores the previous value only when the current `default_agent` is still `480-architect`.",
            "",
            "## Source of truth",
            "",
            "- Common agent definitions: `bundles/common/agents.json`.",
            "- Default instruction bodies: `bundles/common/instructions/`.",
            "- Qwen provider-specific override bodies, if any: `providers/qwen/instructions/`.",
            "- Provider install paths and model-selection schema: `app/providers.py`.",
            "- Provider artifact rendering: `app/render_agents.py`.",
            "- Install/uninstall entrypoint: `app/manage_agents.py`.",
            "- State storage and restore: `app/installer_core.py`.",
            "- User guidance: `README.md`.",
            "",
        ]
    )

    return "\n".join(lines)


def render_gemini_agents_index(
    specs: tuple[AgentSpec, ...],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    specs = _specs_for_target("gemini", specs)
    gemini_file_name_map = _provider_name_map("gemini", specs)
    gemini_tool_name_map = _gemini_tool_name_map(specs)
    primary = [spec for spec in specs if spec.mode == "primary"]
    subagents = [spec for spec in specs if spec.mode == "subagent"]

    lines = [
        "# Gemini CLI Agents",
        "",
        "Documentation for the checked-in Gemini CLI artifacts and install behavior.",
        "",
        "## Name mapping",
        "",
    ]

    for spec in specs:
        file_name = gemini_file_name_map[spec.identifier]
        tool_name = gemini_tool_name_map[spec.identifier]
        lines.append(f"- `{spec.identifier}` -> `{tool_name}` (`providers/gemini/agents/{file_name}.md`)")

    lines.extend(["", "## Primary", ""])
    for spec in primary:
        model_profile = _model_profile_for_provider("gemini", spec, model_selection)
        file_name = gemini_file_name_map[spec.identifier]
        tool_name = gemini_tool_name_map[spec.identifier]
        lines.extend(
            [
                f"- `{tool_name}`",
                f"  - maps from: `{spec.identifier}`",
                f"  - file: `providers/gemini/agents/{file_name}.md`",
                f"  - model: `{model_profile.model}`",
                "",
            ]
        )

    lines.extend(["## Subagents", ""])
    for spec in subagents:
        model_profile = _model_profile_for_provider("gemini", spec, model_selection)
        file_name = gemini_file_name_map[spec.identifier]
        tool_name = gemini_tool_name_map[spec.identifier]
        lines.extend(
            [
                f"- `{tool_name}`",
                f"  - maps from: `{spec.identifier}`",
                f"  - file: `providers/gemini/agents/{file_name}.md`",
                f"  - model: `{model_profile.model}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Install names and paths",
            "",
            "Install files use the Gemini CLI-specific names above and are copied to `~/.gemini/agents/` or `<project>/.gemini/agents/`.",
            "Recommended installs use the checked-in artifacts in `providers/gemini/agents/` as-is.",
            "Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.",
            "",
            "## Default behavior",
            "",
            "- Default activation is optional and enables the system prompt override (`GEMINI_SYSTEM_MD=1`) when `--activate-default` is used.",
            "- The override reads `.gemini/system.md` (project) or `~/.gemini/system.md` (user) depending on scope.",
            "- Uninstall restores `system.md` only when it still matches the managed contents.",
            "",
            "## Subagent support",
            "",
            "- Gemini CLI subagents are enabled by default; this installer ensures `{\"experimental\": {\"enableAgents\": true, \"enableSubagents\": true}}` in `settings.json` for compatibility across Gemini CLI versions.",
            "- Agents are discovered from `.gemini/agents/` (project) and `~/.gemini/agents/` (user) directories.",
            "- The main Gemini CLI automatically routes tasks to subagents based on their `description` field.",
            "",
            "## Source of truth",
            "",
            "- Common agent definitions: `bundles/common/agents.json`.",
            "- Default instruction bodies: `bundles/common/instructions/`.",
            "- Gemini provider-specific override bodies, if any: `providers/gemini/instructions/`.",
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
    if target == "qwen":
        return render_qwen_agent(spec, name_map, model_selection=model_selection)
    if target == "gemini":
        return render_gemini_agent(spec, name_map, model_selection=model_selection)
    raise ValueError(f"Unsupported provider renderer: {target}")


def _render_provider_index(
    target: str,
    specs: tuple[AgentSpec, ...],
    model_selection: ProviderModelSelection | None = None,
) -> str:
    target_specs = _specs_for_target(target, specs)
    if target == "opencode":
        return render_agents_index(target_specs, model_selection=model_selection)
    if target == "claude":
        return render_claude_agents_index(target_specs, model_selection=model_selection)
    if target == "codex":
        return render_codex_agents_index(target_specs, model_selection=model_selection)
    if target == "qwen":
        return render_qwen_agents_index(target_specs, model_selection=model_selection)
    if target == "gemini":
        return render_gemini_agents_index(target_specs, model_selection=model_selection)
    raise ValueError(f"Unsupported provider index renderer: {target}")


def _expected_provider_outputs(
    target: str,
    specs: tuple[AgentSpec, ...],
    output_name_map: dict[str, str],
    *,
    repo_root: Path | None = None,
    model_selection: ProviderModelSelection | None = None,
    render_name_map: dict[str, str] | None = None,
) -> dict[Path, str]:
    provider = get_provider(target)
    agents_dir = provider_agents_dir(target, repo_root=repo_root)
    expected_outputs: dict[Path, str] = {}
    if render_name_map is None:
        render_name_map = output_name_map
    for spec in specs:
        if not spec.supports_target(target):
            continue
        if target == "codex" and spec.mode == "primary":
            continue
        output_names = [output_name_map[spec.identifier]]
        if target == "codex":
            output_names = _codex_agent_output_names(spec, output_name_map)
        for output_name in output_names:
            contents = _render_provider_agent(
                target,
                spec,
                render_name_map,
                model_selection=model_selection,
            )
            if target == "codex":
                contents = render_codex_agent(
                    spec,
                    render_name_map,
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
        output_name_map = _provider_name_map(provider.identifier, specs)
        render_name_map = output_name_map
        if provider.identifier == "gemini":
            render_name_map = _gemini_tool_name_map(specs)
        _write_directory_outputs(
            _expected_provider_outputs(
                provider.identifier,
                specs,
                output_name_map,
                render_name_map=render_name_map,
            ),
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
        output_name_map = _provider_name_map(provider.identifier, specs)
        render_name_map = output_name_map
        if provider.identifier == "gemini":
            render_name_map = _gemini_tool_name_map(specs)
        mismatches.extend(
            _check_directory_outputs(
                _expected_provider_outputs(
                    provider.identifier,
                    specs,
                    output_name_map,
                    render_name_map=render_name_map,
                ),
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
    output_name_map = _provider_name_map(target, specs)
    render_name_map = output_name_map
    if target == "gemini":
        render_name_map = _gemini_tool_name_map(specs)
    agents_dir = provider_agents_dir(target, repo_root=repo_root)
    _write_directory_outputs(
        _expected_provider_outputs(
            target,
            specs,
            output_name_map,
            repo_root=repo_root,
            model_selection=model_selection,
            render_name_map=render_name_map,
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
