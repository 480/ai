#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

try:
    from .agent_bundle import load_bundle, target_agent_names
    from .install_targets import (
        InstallTarget,
        ProviderModelSelection,
        ProviderModelSelectionSchema,
        all_providers,
        get_provider,
        resolve_install_target,
    )
    from .installer_core import default_activation_enabled
    from .installer_core import install as run_install
    from .installer_core import read_json_object
    from .installer_core import uninstall as run_uninstall
    from .render_agents import render_codex_managed_guidance, write_provider_outputs
except ImportError:
    from agent_bundle import load_bundle, target_agent_names
    from install_targets import (
        InstallTarget,
        ProviderModelSelection,
        ProviderModelSelectionSchema,
        all_providers,
        get_provider,
        resolve_install_target,
    )
    from installer_core import default_activation_enabled
    from installer_core import install as run_install
    from installer_core import read_json_object
    from installer_core import uninstall as run_uninstall
    from render_agents import render_codex_managed_guidance, write_provider_outputs


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = "opencode"
DEFAULT_SCOPE = "user"
INSTALL_TARGET_ENV = "BOOTSTRAP_TARGET"
INSTALL_SCOPE_ENV = "BOOTSTRAP_SCOPE"
INSTALL_ACTIVATE_DEFAULT_ENV = "BOOTSTRAP_ACTIVATE_DEFAULT"
INSTALL_MODEL_MODE_ENV = "BOOTSTRAP_MODEL_MODE"
INSTALL_ROLE_MODEL_CHOICES_ENV = "BOOTSTRAP_ROLE_MODEL_CHOICES"
DEFAULT_MODEL_SELECTION_MODE = "recommended"


class InstallTuiUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    note: str = ""
    disabled: bool = False


@dataclass(frozen=True)
class ProviderInstallRequest:
    target: str
    scope: str
    activate_default: bool | None
    enable_teams: bool | None = None
    model_selection: ProviderModelSelection | None = None


@dataclass(frozen=True)
class InstallOptions:
    providers: tuple[ProviderInstallRequest, ...]


TARGET_CHOICES = tuple(Choice(value=provider.identifier, label=provider.label) for provider in all_providers())
INTERACTIVE_PROVIDER_BINARIES = tuple(provider.cli_binary_name for provider in all_providers())


def agent_names_for_target(target: str) -> list[str]:
    return target_agent_names(target)


def source_agents_dir_for_target(target: str) -> Path:
    return get_provider(target).source_agents_dir(REPO_ROOT)


def detected_provider_choices() -> tuple[Choice, ...]:
    choices: list[Choice] = []
    for provider in all_providers():
        if shutil.which(provider.cli_binary_name) is None:
            continue
        config_dir = resolve_install_target(provider.identifier, "user").paths.config_dir
        note = f"설정 디렉터리 감지: {config_dir}" if config_dir.exists() else ""
        choices.append(Choice(value=provider.identifier, label=provider.label, note=note))
    return tuple(choices)


def interactive_install_unavailable_message() -> str:
    provider_binaries = ", ".join(INTERACTIVE_PROVIDER_BINARIES)
    return "\n".join(
        (
            f"PATH에서 지원되는 CLI 바이너리를 찾지 못했습니다: {provider_binaries}.",
            "interactive install은 감지된 provider만 표시합니다.",
            "설정 디렉터리가 있어도 CLI 바이너리가 PATH에 없으면 선택지에 나타나지 않습니다.",
            "CLI를 먼저 설치한 뒤 다시 실행하거나, 비대화식으로 `--target <provider>` 또는 `BOOTSTRAP_TARGET=<provider>`를 지정하세요.",
        )
    )


def required_interactive_provider_choices() -> tuple[Choice, ...]:
    choices = detected_provider_choices()
    if choices:
        return choices
    raise SystemExit(interactive_install_unavailable_message())


def interactive_default_target(choices: tuple[Choice, ...]) -> str:
    for choice in choices:
        if choice.value == DEFAULT_TARGET:
            return DEFAULT_TARGET
    return choices[0].value


def resolve_target(target: str = DEFAULT_TARGET, scope: str = DEFAULT_SCOPE) -> InstallTarget:
    return resolve_install_target(target=target, scope=scope)


def parse_optional_bool(raw: str, *, env_name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise SystemExit(
        f"{env_name} must be one of: 1, 0, true, false, yes, no, on, off."
    )


def default_activation_for_target(target: str) -> bool | None:
    return get_provider(target).default_activation_default


def model_selection_schema_for_target(target: str) -> ProviderModelSelectionSchema:
    return get_provider(target).model_selection_schema


def advanced_model_selection_for_target(
    target: str,
    role_options: dict[str, str] | None = None,
) -> ProviderModelSelection:
    provider = get_provider(target)
    selected_options = {} if role_options is None else dict(role_options)
    resolved_options: dict[str, str] = {}
    for spec in load_bundle():
        role_id = spec.identifier
        option_key = selected_options.get(role_id)
        if option_key is None:
            option_key = provider.default_advanced_role_model_option(spec).key
        else:
            provider.advanced_role_model_option(role_id, option_key)
        resolved_options[role_id] = option_key
    return ProviderModelSelection(mode="advanced", role_options=resolved_options)


def scope_choices_for_target(target: str) -> tuple[Choice, ...]:
    supported = set(get_provider(target).supported_scopes)
    return (
        Choice(value="user", label="user", note="현재 사용자에 설치"),
        Choice(
            value="project",
            label="project",
            note="현재 저장소에 설치",
            disabled="project" not in supported,
        ),
    )


def model_mode_choices_for_target(target: str) -> tuple[Choice, ...]:
    supported_modes = set(model_selection_schema_for_target(target).supported_modes)
    choices: list[Choice] = []
    if "recommended" in supported_modes:
        choices.append(Choice(value="recommended", label="기본 추천", note="provider 추천 프로필 적용"))
    if "advanced" in supported_modes:
        choices.append(Choice(value="advanced", label="고급", note="role별 curated 메뉴 선택"))
    return tuple(choices)


def teams_flag_default_for_target(target: str) -> bool | None:
    if target != "claude":
        return None
    return False


def model_choice_entries_from_env(raw: str) -> list[str]:
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not entries:
        raise SystemExit(f"{INSTALL_ROLE_MODEL_CHOICES_ENV} must not be empty when provided.")
    return entries


def parse_role_model_choice_entries(entries: list[str], *, target: str) -> dict[str, str]:
    provider = get_provider(target)
    parsed: dict[str, str] = {}
    known_roles = {spec.identifier for spec in load_bundle()}
    for entry in entries:
        role_id, separator, option_key = entry.partition("=")
        if separator != "=" or not role_id or not option_key:
            raise SystemExit(
                "Role model choices must use the format '<role-id>=<option-key>'."
            )
        if role_id in parsed:
            raise SystemExit(f"Duplicate role model choice for {role_id}.")
        if role_id not in known_roles:
            raise SystemExit(f"Unsupported role model choice target: {role_id}")
        try:
            provider.advanced_role_model_option(role_id, option_key)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        parsed[role_id] = option_key
    return parsed


def serialize_model_selection(model_selection: ProviderModelSelection | None) -> dict[str, object] | None:
    if model_selection is None:
        return None
    return {
        "mode": model_selection.mode,
        "role_options": dict(model_selection.role_options),
    }


def load_persisted_model_selection(target: InstallTarget) -> ProviderModelSelection | None:
    if not target.paths.state_file.exists():
        return None
    state = read_json_object(target.paths.state_file)
    raw_selection = state.get("model_selection")
    if raw_selection is None:
        return None
    if not isinstance(raw_selection, dict):
        raise SystemExit(f"Invalid model_selection in {target.paths.state_file}.")

    mode = raw_selection.get("mode")
    if not isinstance(mode, str) or not mode:
        raise SystemExit(f"Invalid model_selection mode in {target.paths.state_file}.")
    if mode == "recommended":
        return None

    provider = get_provider(target.name)
    if mode not in provider.supported_model_selection_modes():
        raise SystemExit(f"Unsupported model_selection mode in {target.paths.state_file}: {mode}")

    role_options = raw_selection.get("role_options")
    if not isinstance(role_options, dict) or not all(
        isinstance(role_id, str) and role_id and isinstance(option_key, str) and option_key
        for role_id, option_key in role_options.items()
    ):
        raise SystemExit(f"Invalid model_selection role_options in {target.paths.state_file}.")

    parsed_role_options = parse_role_model_choice_entries(
        [f"{role_id}={option_key}" for role_id, option_key in role_options.items()],
        target=target.name,
    )
    return advanced_model_selection_for_target(target.name, parsed_role_options)


def install_reuses_existing_model_selection(
    *,
    args: argparse.Namespace,
    env: dict[str, str],
) -> bool:
    return (
        args.model_mode is None
        and args.role_model_choice is None
        and INSTALL_MODEL_MODE_ENV not in env
        and INSTALL_ROLE_MODEL_CHOICES_ENV not in env
    )


def load_persisted_default_activation(target: InstallTarget, *, provider: Any) -> bool:
    if provider.default_activation is None:
        return False
    if not target.paths.state_file.exists():
        default_activation = provider.default_activation_default
        return bool(default_activation) if default_activation is not None else False

    state = read_json_object(target.paths.state_file)
    return default_activation_enabled(state)


def print_line(output: TextIO, message: str = "") -> None:
    output.write(f"{message}\n")
    output.flush()


def supports_install_tui(*, input_stream: TextIO, output: TextIO) -> bool:
    if not input_stream.isatty() or not output.isatty():
        return False

    term = os.environ.get("TERM", "").strip().lower()
    if term in {"", "dumb", "unknown"}:
        return False

    try:
        input_stream.fileno()
        output_stream_fd = output.fileno()
    except (AttributeError, OSError, ValueError):
        return False

    try:
        import curses
    except ImportError:
        return False

    try:
        curses.setupterm(fd=output_stream_fd)
    except (curses.error, OSError):
        return False
    return True


def prompt_choice(
    *,
    output: TextIO,
    input_stream: TextIO,
    title: str,
    choices: tuple[Choice, ...],
    default_value: str,
) -> str:
    choice_map = {str(index): choice for index, choice in enumerate(choices, start=1)}
    default_index = next(
        index for index, choice in enumerate(choices, start=1) if choice.value == default_value
    )

    while True:
        print_line(output, title)
        for index, choice in enumerate(choices, start=1):
            default_marker = " (기본값)" if index == default_index else ""
            disabled_marker = " - 지원하지 않음" if choice.disabled else ""
            note_marker = f" — {choice.note}" if choice.note else ""
            print_line(output, f"  {index}) {choice.label}{default_marker}{disabled_marker}{note_marker}")

        output.write(f"선택 [{default_index}]: ")
        output.flush()
        response = input_stream.readline()
        if response == "":
            raise SystemExit("설치 입력을 읽지 못했습니다.")

        answer = response.strip()
        selected = choice_map.get(str(default_index) if answer == "" else answer)
        if selected is None:
            print_line(output, "잘못된 입력입니다. 번호를 다시 선택해 주세요.")
            continue
        if selected.disabled:
            print_line(output, f"{selected.label} 범위는 이 대상에서 아직 지원하지 않습니다.")
            continue
        print_line(output)
        return selected.value


def prompt_bool_choice(
    *,
    output: TextIO,
    input_stream: TextIO,
    title: str,
    default: bool,
) -> bool:
    default_value = "yes" if default else "no"
    selected = prompt_choice(
        output=output,
        input_stream=input_stream,
        title=title,
        choices=(
            Choice(value="yes", label="예"),
            Choice(value="no", label="아니오"),
        ),
        default_value=default_value,
    )
    return selected == "yes"


def tui_line_chunks(text: str, width: int) -> list[str]:
    if width <= 1:
        return [""]
    chunks = textwrap.wrap(text, width=width, replace_whitespace=False) or [""]
    return [chunk[:width] for chunk in chunks]


def tui_rendered_body_lines(screen: Any, lines: list[str]) -> tuple[list[str], int]:
    height, width = screen.getmaxyx()
    content_width = max(10, width - 4)
    rendered_lines: list[str] = []
    for line in lines:
        rendered_lines.extend(tui_line_chunks(line, content_width))
    return rendered_lines, max(1, height - 4)


def tui_render_screen(
    screen: Any,
    *,
    title: str,
    lines: list[str],
    footer: str,
    error: str | None = None,
    scroll_offset: int = 0,
) -> tuple[int, int]:
    import curses

    height, width = screen.getmaxyx()
    rendered_lines, max_body_rows = tui_rendered_body_lines(screen, lines)

    screen.erase()
    try:
        screen.addnstr(0, 0, title, width - 1, curses.A_BOLD)
    except curses.error:
        pass

    visible_lines = rendered_lines[scroll_offset : scroll_offset + max_body_rows]
    for row_index, line in enumerate(visible_lines, start=2):
        try:
            screen.addnstr(row_index, 0, line, width - 1)
        except curses.error:
            pass

    if error:
        try:
            screen.addnstr(height - 2, 0, error, width - 1, curses.A_BOLD)
        except curses.error:
            pass
    try:
        screen.addnstr(height - 1, 0, footer, width - 1, curses.A_DIM)
    except curses.error:
        pass
    screen.refresh()
    return len(rendered_lines), max_body_rows


def tui_prompt_review(
    screen: Any,
    *,
    title: str,
    lines: list[str],
    footer: str,
) -> None:
    import curses

    scroll_offset = 0

    while True:
        rendered_lines, max_body_rows = tui_rendered_body_lines(screen, lines)
        max_scroll_offset = max(0, len(rendered_lines) - max_body_rows)
        scrollable_footer = footer if max_scroll_offset == 0 else f"{footer} | 화살표/jk: 스크롤"
        total_lines, max_body_rows = tui_render_screen(
            screen,
            title=title,
            lines=lines,
            footer=scrollable_footer,
            scroll_offset=scroll_offset,
        )
        max_scroll_offset = max(0, total_lines - max_body_rows)

        key = screen.getch()
        if key in (curses.KEY_ENTER, 10, 13):
            return
        if key in (curses.KEY_UP, ord("k")) and scroll_offset > 0:
            scroll_offset -= 1
            continue
        if key in (curses.KEY_DOWN, ord("j")) and scroll_offset < max_scroll_offset:
            scroll_offset += 1
            continue


def tui_prompt_multi_select(
    screen: Any,
    *,
    title: str,
    choices: tuple[Choice, ...],
    default_values: tuple[str, ...],
) -> tuple[str, ...]:
    import curses

    selected_values = {choice.value for choice in choices if choice.value in default_values and not choice.disabled}
    highlighted_index = next(
        (index for index, choice in enumerate(choices) if choice.value in selected_values),
        0,
    )
    error: str | None = None

    while True:
        lines: list[str] = ["여러 provider를 선택할 수 있습니다.", ""]
        for index, choice in enumerate(choices):
            cursor = ">" if index == highlighted_index else " "
            checked = "x" if choice.value in selected_values else " "
            disabled_marker = " (지원하지 않음)" if choice.disabled else ""
            note_marker = f" - {choice.note}" if choice.note else ""
            lines.append(f"{cursor} [{checked}] {choice.label}{disabled_marker}{note_marker}")

        tui_render_screen(
            screen,
            title=title,
            lines=lines,
            footer="Space: 선택 전환 | Enter: 다음 | 화살표/jk: 이동",
            error=error,
        )

        key = screen.getch()
        if key in (curses.KEY_UP, ord("k")):
            highlighted_index = (highlighted_index - 1) % len(choices)
            error = None
            continue
        if key in (curses.KEY_DOWN, ord("j")):
            highlighted_index = (highlighted_index + 1) % len(choices)
            error = None
            continue
        if key == ord(" "):
            highlighted_choice = choices[highlighted_index]
            if highlighted_choice.disabled:
                error = f"{highlighted_choice.label}는 아직 선택할 수 없습니다."
                continue
            if highlighted_choice.value in selected_values:
                selected_values.remove(highlighted_choice.value)
            else:
                selected_values.add(highlighted_choice.value)
            error = None
            continue
        if key in (curses.KEY_ENTER, 10, 13):
            if not selected_values:
                error = "최소 한 개의 provider를 선택해야 합니다."
                continue
            return tuple(choice.value for choice in choices if choice.value in selected_values)


def tui_prompt_single_choice(
    screen: Any,
    *,
    title: str,
    choices: tuple[Choice, ...],
    default_value: str,
) -> str:
    import curses

    highlighted_index = next(
        (index for index, choice in enumerate(choices) if choice.value == default_value and not choice.disabled),
        0,
    )
    error: str | None = None

    while True:
        lines: list[str] = []
        for index, choice in enumerate(choices):
            cursor = ">" if index == highlighted_index else " "
            default_marker = " (기본값)" if choice.value == default_value else ""
            disabled_marker = " (지원하지 않음)" if choice.disabled else ""
            note_marker = f" - {choice.note}" if choice.note else ""
            lines.append(f"{cursor} {choice.label}{default_marker}{disabled_marker}{note_marker}")

        tui_render_screen(
            screen,
            title=title,
            lines=lines,
            footer="Enter: 선택 | 화살표/jk: 이동",
            error=error,
        )

        key = screen.getch()
        if key in (curses.KEY_UP, ord("k")):
            highlighted_index = (highlighted_index - 1) % len(choices)
            error = None
            continue
        if key in (curses.KEY_DOWN, ord("j")):
            highlighted_index = (highlighted_index + 1) % len(choices)
            error = None
            continue
        if key in (curses.KEY_ENTER, 10, 13):
            selected = choices[highlighted_index]
            if selected.disabled:
                error = f"{selected.label}는 아직 선택할 수 없습니다."
                continue
            return selected.value


def resolved_advanced_role_model_option(
    request: ProviderInstallRequest,
    *,
    provider: Any,
    spec: Any,
) -> Any:
    if request.model_selection is None:
        raise ValueError("Advanced role model option resolution requires model_selection.")
    option_key = request.model_selection.role_options.get(spec.identifier)
    if option_key is None:
        return provider.default_advanced_role_model_option(spec)
    return provider.advanced_role_model_option(spec.identifier, option_key)


def build_install_summary_lines(requests: tuple[ProviderInstallRequest, ...]) -> list[str]:
    lines = ["선택한 설치 구성을 확인하세요.", ""]
    for request in requests:
        provider = get_provider(request.target)
        lines.append(f"- {provider.label}: scope={request.scope}")
        if request.activate_default is not None:
            activation = "yes" if request.activate_default else "no"
            lines.append(f"  기본 활성화: {activation}")
        if request.enable_teams is not None:
            teams_enabled = "yes" if request.enable_teams else "no"
            lines.append(f"  agent teams: {teams_enabled}")
        model_mode = request.model_selection.mode if request.model_selection is not None else "recommended"
        lines.append(f"  모델 모드: {model_mode}")
        if request.model_selection is not None and request.model_selection.mode == "advanced":
            lines.append("  role별 모델 선택:")
            for spec in load_bundle():
                option = resolved_advanced_role_model_option(request, provider=provider, spec=spec)
                lines.append(f"    {spec.display_name}: {option.key} ({option.label})")
    return lines


def prompt_install_options_tui() -> InstallOptions:
    target_choices = required_interactive_provider_choices()
    default_target = interactive_default_target(target_choices)
    try:
        import curses
    except ImportError as exc:
        raise InstallTuiUnavailableError("Install TUI is unavailable.") from exc

    tui_started = False

    def run(screen: Any) -> InstallOptions:
        nonlocal tui_started
        tui_started = True
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)

        selected_targets = tui_prompt_multi_select(
            screen,
            title="480/ai 설치 provider 선택",
            choices=target_choices,
            default_values=(default_target,),
        )

        requests: list[ProviderInstallRequest] = []
        for target in selected_targets:
            provider = get_provider(target)
            scope = tui_prompt_single_choice(
                screen,
                title=f"{provider.label} 설치 범위를 선택하세요.",
                choices=scope_choices_for_target(target),
                default_value=DEFAULT_SCOPE,
            )

            activate_default = default_activation_for_target(target)
            if activate_default is not None:
                activate_default = (
                    tui_prompt_single_choice(
                        screen,
                        title=f"{provider.label} 기본 에이전트를 바로 활성화할까요?",
                        choices=(
                            Choice(value="yes", label="예"),
                            Choice(value="no", label="아니오"),
                        ),
                        default_value="yes" if activate_default else "no",
                    )
                    == "yes"
                )

            enable_teams = teams_flag_default_for_target(target)
            if enable_teams is not None:
                enable_teams = (
                    tui_prompt_single_choice(
                        screen,
                        title=f"{provider.label} agent teams 실험 플래그를 활성화할까요?",
                        choices=(
                            Choice(value="yes", label="예", note="팀 중심 워크플로우를 사용할 때 필요"),
                            Choice(value="no", label="아니오", note="설치만 진행하고 기존 동작 유지"),
                        ),
                        default_value="yes" if enable_teams else "no",
                    )
                    == "yes"
                )
            else:
                enable_teams = None

            model_mode = tui_prompt_single_choice(
                screen,
                title=f"{provider.label} 모델 모드를 선택하세요.",
                choices=model_mode_choices_for_target(target),
                default_value=DEFAULT_MODEL_SELECTION_MODE,
            )

            model_selection: ProviderModelSelection | None = None
            if model_mode == "advanced":
                role_options: dict[str, str] = {}
                for spec in load_bundle():
                    default_option = provider.default_advanced_role_model_option(spec)
                    choices = tuple(
                        Choice(value=option.key, label=option.label, note=option.note)
                        for option in provider.advanced_role_model_options(spec.identifier)
                    )
                    role_options[spec.identifier] = tui_prompt_single_choice(
                        screen,
                        title=f"{provider.label} / {spec.display_name} 모델을 선택하세요.",
                        choices=choices,
                        default_value=default_option.key,
                    )
                model_selection = advanced_model_selection_for_target(target, role_options)

            requests.append(
                ProviderInstallRequest(
                    target=target,
                    scope=scope,
                    activate_default=activate_default,
                    enable_teams=enable_teams,
                    model_selection=model_selection,
                )
            )

        summary_lines = build_install_summary_lines(tuple(requests))
        tui_prompt_review(
            screen,
            title="설치 요약",
            lines=summary_lines,
            footer="Enter: 설치 시작",
        )
        return InstallOptions(providers=tuple(requests))

    try:
        return curses.wrapper(run)
    except curses.error as exc:
        if not tui_started:
            raise InstallTuiUnavailableError("Install TUI could not initialize.") from exc
        raise


def prompt_install_options_basic(
    *,
    input_stream: TextIO,
    output: TextIO,
) -> InstallOptions:
    target_choices = required_interactive_provider_choices()
    default_target = interactive_default_target(target_choices)
    print_line(output, "480/ai 설치 대상을 선택하세요.")
    print_line(output)

    target = prompt_choice(
        output=output,
        input_stream=input_stream,
        title="설치할 CLI를 선택하세요.",
        choices=target_choices,
        default_value=default_target,
    )
    scope = prompt_choice(
        output=output,
        input_stream=input_stream,
        title="설치 범위를 선택하세요.",
        choices=scope_choices_for_target(target),
        default_value=DEFAULT_SCOPE,
    )

    activate_default = default_activation_for_target(target)
    if activate_default is not None:
        activate_default = prompt_bool_choice(
            output=output,
            input_stream=input_stream,
            title="기본 에이전트를 바로 활성화할까요?",
            default=activate_default,
        )

    enable_teams = teams_flag_default_for_target(target)
    if enable_teams is not None:
        enable_teams = prompt_bool_choice(
            output=output,
            input_stream=input_stream,
            title="Claude Code agent teams 실험 플래그를 활성화할까요?",
            default=enable_teams,
        )

    model_mode = prompt_choice(
        output=output,
        input_stream=input_stream,
        title="모델 모드를 선택하세요.",
        choices=model_mode_choices_for_target(target),
        default_value=DEFAULT_MODEL_SELECTION_MODE,
    )
    if model_mode != "advanced":
        return InstallOptions(
            providers=(
                ProviderInstallRequest(
                    target=target,
                    scope=scope,
                    activate_default=activate_default,
                    enable_teams=enable_teams,
                ),
            )
        )

    provider = get_provider(target)
    role_options: dict[str, str] = {}
    print_line(output, "고급 모드: role별 curated 모델을 선택하세요.")
    print_line(output)
    for spec in load_bundle():
        default_option = provider.default_advanced_role_model_option(spec)
        choices = tuple(
            Choice(value=option.key, label=option.label, note=option.note)
            for option in provider.advanced_role_model_options(spec.identifier)
        )
        role_options[spec.identifier] = prompt_choice(
            output=output,
            input_stream=input_stream,
            title=f"{spec.display_name} 모델을 선택하세요.",
            choices=choices,
            default_value=default_option.key,
        )
    return InstallOptions(
        providers=(
            ProviderInstallRequest(
                target=target,
                scope=scope,
                activate_default=activate_default,
                enable_teams=enable_teams,
                model_selection=advanced_model_selection_for_target(target, role_options),
            ),
        )
    )


def should_prompt_install(
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    stdin: TextIO,
    stdout: TextIO,
) -> bool:
    explicit_cli = any(
        value is not None
        for value in (
            args.target,
            args.scope,
            args.activate_default,
            args.model_mode,
            args.role_model_choice,
        )
    )
    explicit_env = any(
        name in env
        for name in (
            INSTALL_TARGET_ENV,
            INSTALL_SCOPE_ENV,
            INSTALL_ACTIVATE_DEFAULT_ENV,
            INSTALL_MODEL_MODE_ENV,
            INSTALL_ROLE_MODEL_CHOICES_ENV,
        )
    )
    if explicit_cli or explicit_env:
        return False
    return stdin.isatty() and stdout.isatty()


def resolve_install_options_from_inputs(
    *,
    args: argparse.Namespace,
    env: dict[str, str],
) -> InstallOptions:
    target = args.target or env.get(INSTALL_TARGET_ENV) or DEFAULT_TARGET
    scope = args.scope or env.get(INSTALL_SCOPE_ENV) or DEFAULT_SCOPE

    activate_default = args.activate_default
    if activate_default is None and INSTALL_ACTIVATE_DEFAULT_ENV in env:
        activate_default = parse_optional_bool(
            env[INSTALL_ACTIVATE_DEFAULT_ENV],
            env_name=INSTALL_ACTIVATE_DEFAULT_ENV,
        )

    model_mode = args.model_mode or env.get(INSTALL_MODEL_MODE_ENV)
    if model_mode is not None and model_mode not in model_selection_schema_for_target(target).supported_modes:
        raise SystemExit(f"Unsupported model selection mode for {target}: {model_mode}")

    raw_role_model_choices = args.role_model_choice
    if raw_role_model_choices is None and INSTALL_ROLE_MODEL_CHOICES_ENV in env:
        raw_role_model_choices = model_choice_entries_from_env(env[INSTALL_ROLE_MODEL_CHOICES_ENV])
    if raw_role_model_choices is not None and model_mode is None:
        model_mode = "advanced"

    if model_mode == "recommended" or model_mode is None:
        if raw_role_model_choices:
            raise SystemExit("Role model choices require BOOTSTRAP_MODEL_MODE=advanced or --model-mode advanced.")
        return InstallOptions(
            providers=(
                ProviderInstallRequest(
                    target=target,
                    scope=scope,
                    activate_default=activate_default,
                    model_selection=(
                        load_persisted_model_selection(resolve_target(target, scope))
                        if install_reuses_existing_model_selection(args=args, env=env)
                        else None
                    ),
                ),
            )
        )

    if model_mode != "advanced":
        raise SystemExit(f"Unsupported model selection mode for {target}: {model_mode}")

    parsed_choices = parse_role_model_choice_entries(raw_role_model_choices or [], target=target)
    return InstallOptions(
        providers=(
            ProviderInstallRequest(
                target=target,
                scope=scope,
                activate_default=activate_default,
                model_selection=advanced_model_selection_for_target(target, parsed_choices),
            ),
        )
    )


def resolve_uninstall_options_from_inputs(
    *,
    args: argparse.Namespace,
    env: dict[str, str],
) -> tuple[str, str]:
    target = args.target or env.get(INSTALL_TARGET_ENV) or DEFAULT_TARGET
    scope = args.scope or env.get(INSTALL_SCOPE_ENV) or DEFAULT_SCOPE
    return target, scope


def prompt_install_options(
    *,
    input_stream: TextIO,
    output: TextIO,
) -> InstallOptions:
    if supports_install_tui(input_stream=input_stream, output=output):
        try:
            return prompt_install_options_tui()
        except InstallTuiUnavailableError:
            pass
    return prompt_install_options_basic(input_stream=input_stream, output=output)


def run_install_requests(requests: tuple[ProviderInstallRequest, ...]) -> None:
    for request in requests:
        install_kwargs = {
            "target": request.target,
            "scope": request.scope,
            "activate_default": request.activate_default,
        }
        if request.enable_teams is not None:
            install_kwargs["enable_teams"] = request.enable_teams
        if request.model_selection is not None:
            install_kwargs["model_selection"] = request.model_selection
        install(**install_kwargs)


def install(
    target: str = DEFAULT_TARGET,
    scope: str = DEFAULT_SCOPE,
    force: bool = False,
    activate_default: bool | None = None,
    enable_teams: bool | None = None,
    model_selection: ProviderModelSelection | None = None,
) -> None:
    resolved_target = resolve_target(target, scope)
    provider = get_provider(target)
    codex_managed_guidance = render_codex_managed_guidance(load_bundle()) if target == "codex" else None
    if activate_default is None:
        should_activate_default = load_persisted_default_activation(resolved_target, provider=provider)
    else:
        should_activate_default = activate_default
    if model_selection is None:
        source_agents_dir = source_agents_dir_for_target(target)
        run_install(
            resolved_target,
            source_agents_dir,
            agent_names_for_target(target),
            force=force,
            manage_default_activation=should_activate_default,
            state_updates={"model_selection": None},
            enable_claude_teams=bool(enable_teams),
            codex_managed_guidance=codex_managed_guidance,
        )
        return

    with tempfile.TemporaryDirectory() as tmp:
        source_agents_dir = write_provider_outputs(
            target,
            repo_root=Path(tmp),
            model_selection=model_selection,
        )
        run_install(
            resolved_target,
            source_agents_dir,
            agent_names_for_target(target),
            force=force,
            manage_default_activation=should_activate_default,
            state_updates={"model_selection": serialize_model_selection(model_selection)},
            enable_claude_teams=bool(enable_teams),
            codex_managed_guidance=codex_managed_guidance,
        )


def uninstall(target: str = DEFAULT_TARGET, scope: str = DEFAULT_SCOPE) -> None:
    resolved_target = resolve_target(target, scope)
    model_selection = load_persisted_model_selection(resolved_target)
    if model_selection is None:
        run_uninstall(
            resolved_target,
            source_agents_dir_for_target(target),
            agent_names_for_target(target),
        )
        return

    with tempfile.TemporaryDirectory() as tmp:
        source_agents_dir = write_provider_outputs(
            target,
            repo_root=Path(tmp),
            model_selection=model_selection,
        )
        run_uninstall(
            resolved_target,
            source_agents_dir,
            agent_names_for_target(target),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manage_agents.py")
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--target")
    parser.add_argument("--scope")
    activation_group = parser.add_mutually_exclusive_group()
    activation_group.add_argument("--activate-default", dest="activate_default", action="store_true")
    activation_group.add_argument("--no-activate-default", dest="activate_default", action="store_false")
    parser.add_argument("--model-mode", choices=("recommended", "advanced"))
    parser.add_argument("--role-model-choice", action="append")
    parser.set_defaults(activate_default=None)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    env = dict(os.environ)

    if args.action == "install":
        if should_prompt_install(args=args, env=env, stdin=sys.stdin, stdout=sys.stdout):
            install_options = prompt_install_options(
                input_stream=sys.stdin,
                output=sys.stdout,
            )
        else:
            install_options = resolve_install_options_from_inputs(args=args, env=env)
        run_install_requests(install_options.providers)
    else:
        target, scope = resolve_uninstall_options_from_inputs(args=args, env=env)
        uninstall(target=target, scope=scope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
