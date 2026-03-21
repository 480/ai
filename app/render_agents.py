#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from .agent_bundle import REPO_ROOT, AgentSpec, load_bundle
    from .install_targets import ProviderModelSelection, all_providers, get_provider
except ImportError:
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
        "OpenCode용 체크인 산출물과 설치 동작을 정리한 문서입니다.",
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
            "## 설치 이름과 경로",
            "",
            "설치 파일 이름은 체크인 산출물과 동일하며 항상 `~/.config/opencode/agents/`에 복사됩니다.",
            "`기본 추천` 설치는 `providers/opencode/agents/`의 체크인 산출물을 그대로 사용합니다.",
            "`고급` 설치는 선택한 모델 조합으로 임시 산출물을 렌더링한 뒤 같은 설치 경로에 복사합니다.",
            "",
            "## 기본 동작",
            "",
            "- 기본값으로 `480-architect`를 활성화하며 설치 시 `default_agent`를 설정합니다.",
            "- `--no-activate-default` 또는 `BOOTSTRAP_ACTIVATE_DEFAULT=0`을 주면 `default_agent`를 바꾸지 않습니다.",
            "- 제거는 bootstrap 상태에 활성화 기록이 있고 현재 설정이 아직 `480-architect`일 때만 이전 기본값을 복원합니다.",
            "",
            "## Source of truth",
            "",
            "- 공통 agent 정의: `bundles/common/agents.json`.",
            "- 공통 instruction 본문: `bundles/common/instructions/`.",
            "- provider별 설치 경로와 모델 선택 스키마: `app/providers.py`.",
            "- provider 산출물 렌더링: `app/render_agents.py`.",
            "- 설치/제거 entrypoint: `app/manage_agents.py`.",
            "- 상태 저장과 복원: `app/installer_core.py`.",
            "- 사용자 안내: `README.md`.",
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
        "Claude Code용 체크인 산출물과 설치 동작을 정리한 문서입니다.",
        "",
        "## 이름 매핑",
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
            "## 설치 이름과 경로",
            "",
            "설치 파일은 위 Claude 전용 이름으로 `~/.claude/agents/` 또는 `<project>/.claude/agents/`에 복사됩니다.",
            "`기본 추천` 설치는 `providers/claude/agents/`의 체크인 산출물을 그대로 사용합니다.",
            "`고급` 설치는 선택한 모델 조합으로 임시 산출물을 렌더링한 뒤 같은 설치 경로에 복사합니다.",
            "",
            "## 기본 동작",
            "",
            "- 기본 활성화는 선택 사항이며 `--activate-default`를 줄 때만 `agent`를 `480-architect`로 설정합니다.",
            "- 제거는 현재 `agent`가 아직 `480-architect`일 때만 이전 값을 복원합니다.",
            "",
            "## 팀 동작",
            "",
            "- Claude Code의 agent team 기능이 켜진 환경에서는 `480-architect`가 팀 리더로 기본 3인 팀(`480-developer`, `480-code-reviewer`, `480-code-reviewer2`)을 조율합니다.",
            "- `480-code-scanner`는 저장소 스캔이 실제로 필요할 때만 선택적으로 추가합니다.",
            "- 설치 중 agent teams 실험 플래그 활성화 여부를 물어보고, 동의하면 `settings.json`의 `env`에 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`을 merge합니다.",
            "- 제거는 이 teams 실험 플래그 env 설정을 건드리지 않습니다.",
            "- 팀 기능이 비활성화되었거나 미지원이면 `480-architect`가 기존 single-orchestrator fallback으로 같은 Task Brief 기반 흐름을 직접 진행합니다.",
            "",
            "## Source of truth",
            "",
            "- 공통 agent 정의: `bundles/common/agents.json`.",
            "- 기본 instruction 본문: `bundles/common/instructions/`.",
            "- Claude provider 전용 override 본문(있다면): `providers/claude/instructions/`.",
            "- provider별 설치 경로와 모델 선택 스키마: `app/providers.py`.",
            "- provider 산출물 렌더링: `app/render_agents.py`.",
            "- 설치/제거 entrypoint: `app/manage_agents.py`.",
            "- 상태 저장과 복원: `app/installer_core.py`.",
            "- 사용자 안내: `README.md`.",
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
        "Codex CLI용 체크인 산출물과 설치 동작을 정리한 문서입니다.",
        "",
        "## Main Prompt",
        "",
        "Codex는 루트 `AGENTS.md`의 480ai 관리 블록을 architect 메인 프롬프트로 사용합니다.",
        "관리 블록 소스는 Codex 전용 architect instruction 본문(`providers/codex/instructions/480-architect.md`)이며 architect custom agent는 따로 만들지 않습니다.",
        "",
        "## 이름 매핑",
        "",
    ]

    for spec in subagents:
        codex_name = codex_name_map[spec.identifier]
        lines.append(f"- `{spec.identifier}` -> `{codex_name}` (`providers/codex/agents/{codex_name}.toml`)")

    lines.extend(["", "## Custom agents", "", "Codex custom agent는 아래 4개 서브에이전트만 제공합니다.", ""])
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
            "## 설치 이름과 경로",
            "",
            "설치 파일은 `~/.codex/agents/` 또는 `<project>/.codex/agents/`에 복사됩니다.",
            "user 범위는 `~/.codex/AGENTS.md`, project 범위는 저장소 루트 `AGENTS.md`에 480ai 관리 블록을 추가합니다.",
            "Codex 설정은 공식 계약에 맞춰 `~/.codex/config.toml` 또는 `<project>/.codex/config.toml`에 최소 merge만 적용합니다.",
            "설치 시 기존 설정은 보존한 채 `features.multi_agent = true`와 `agents.max_depth = 2`만 반영합니다.",
            "Codex CLI는 각 TOML의 `name` 필드를 custom agent 이름으로 사용합니다.",
            "루트 `AGENTS.md` 480ai 관리 블록은 architect 메인 프롬프트 본문을 그대로 사용합니다.",
            "기존 사용자 내용은 보존한 채 480ai 관리 블록만 덧붙입니다.",
            "재설치 시에는 기존 480ai 관리 블록을 교체하여 중복을 만들지 않습니다.",
            "제거 시에는 480ai 관리 블록만 삭제합니다.",
            "",
            "## Codex delegation model",
            "",
            "- Codex는 native subagent workflow를 사용합니다. architect는 `480-developer`를 spawn하고, developer는 필요할 때만 reviewer/scanner 서브에이전트를 씁니다.",
            "- 기본 delegation depth는 2단계입니다: architect(depth 0) -> developer(depth 1) -> reviewer/scanner(depth 2).",
            "- `480-developer`는 구현 후 `480-code-reviewer`, `480-code-reviewer2`를 Codex 서브에이전트로 병렬 호출해 둘 다 승인받아야 합니다.",
            "",
            "다음처럼 Codex CLI 프롬프트에서 바로 호출할 수 있습니다.",
            "문서와 예시는 Codex의 실제 자연어 호출 패턴을 기준으로 작성합니다.",
            "",
            "```text",
            "Plan the next work for docs/480ai/example-topic/001-example-task.md.",
            "Have 480-developer implement docs/480ai/example-topic/001-example-task.md.",
            "Have 480-developer spawn 480-code-reviewer and 480-code-reviewer2 in parallel, wait for both approvals, and return a completion report.",
            "```",
            "`기본 추천` 설치는 `providers/codex/agents/`의 체크인 산출물을 그대로 사용합니다.",
            "`고급` 설치는 선택한 모델 조합으로 임시 산출물을 렌더링한 뒤 같은 설치 경로에 복사합니다.",
            "",
            "## 범위 메모",
            "",
            "Codex CLI 설치기는 custom agent와 480ai 관리 AGENTS 블록만 관리합니다.",
            "사용자 작성 내용이나 480ai 관리 블록 밖의 AGENTS.md 내용은 건드리지 않습니다.",
            "",
            "## Source of truth",
            "",
            "- 공통 agent 정의: `bundles/common/agents.json`.",
            "- 공통 instruction 본문: `bundles/common/instructions/`.",
            "- Codex provider 전용 override 본문(있다면): `providers/codex/instructions/`.",
            "- provider별 설치 경로와 모델 선택 스키마: `app/providers.py`.",
            "- provider 산출물 렌더링: `app/render_agents.py`.",
            "- 설치/제거 entrypoint: `app/manage_agents.py`.",
            "- 상태 저장과 복원: `app/installer_core.py`.",
            "- 사용자 안내: `README.md`.",
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
