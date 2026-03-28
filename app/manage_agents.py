#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

if __package__:
    from .agent_bundle import load_bundle, target_agent_names
    from . import installer_core
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
else:  # pragma: no cover
    from agent_bundle import load_bundle, target_agent_names
    import installer_core
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
DEFAULT_VERIFY_TARGET = "codex"
DEFAULT_VERIFY_SCOPE = "user"
INSTALL_TARGET_ENV = "BOOTSTRAP_TARGET"
INSTALL_SCOPE_ENV = "BOOTSTRAP_SCOPE"
INSTALL_ACTIVATE_DEFAULT_ENV = "BOOTSTRAP_ACTIVATE_DEFAULT"
INSTALL_DESKTOP_NOTIFY_ENV = "BOOTSTRAP_DESKTOP_NOTIFY"
INSTALL_MODEL_MODE_ENV = "BOOTSTRAP_MODEL_MODE"
INSTALL_ROLE_MODEL_CHOICES_ENV = "BOOTSTRAP_ROLE_MODEL_CHOICES"
DEFAULT_MODEL_SELECTION_MODE = "recommended"


class InstallTuiUnavailableError(RuntimeError):
    pass


class TuiNavigateBack:
    pass


TUI_NAVIGATE_BACK = TuiNavigateBack()


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
    desktop_notifications: bool | None = None
    model_selection: ProviderModelSelection | None = None


@dataclass(frozen=True)
class InstallOptions:
    providers: tuple[ProviderInstallRequest, ...]


CODEX_NOOP_VALIDATION_PROMPT = (
    "Spawn `480-developer` for a no-op validation only. Do not edit files. "
    "Have the child inspect its current role and report only JSON with: "
    '{"developer_role":"...","redelegated":false,"notes":"..."}.'
)


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
        note = f"Config directory detected: {config_dir}" if config_dir.exists() else ""
        choices.append(Choice(value=provider.identifier, label=provider.label, note=note))
    return tuple(choices)


def interactive_install_unavailable_message() -> str:
    provider_binaries = ", ".join(INTERACTIVE_PROVIDER_BINARIES)
    return "\n".join(
        (
            f"No supported CLI binaries were found on PATH: {provider_binaries}.",
            "Interactive install only shows detected providers.",
            "Even if a config directory exists, the provider will not appear unless its CLI binary is on PATH.",
            "Install the CLI first and run again, or use the non-interactive `--target <provider>` or `BOOTSTRAP_TARGET=<provider>` option.",
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
        Choice(value="user", label="user", note="Install for the current user"),
        Choice(
            value="project",
            label="project",
            note="Install in the current repository",
            disabled="project" not in supported,
        ),
    )


def model_mode_choices_for_target(target: str) -> tuple[Choice, ...]:
    supported_modes = set(model_selection_schema_for_target(target).supported_modes)
    choices: list[Choice] = []
    if "recommended" in supported_modes:
        choices.append(Choice(value="recommended", label="Recommended", note="Apply the provider-recommended profile"))
    if "advanced" in supported_modes:
        choices.append(Choice(value="advanced", label="Advanced", note="Select curated per-role options"))
    return tuple(choices)


def teams_flag_default_for_target(target: str) -> bool | None:
    if target != "claude":
        return None
    return False


def desktop_notifications_default_for_target(target: str) -> bool | None:
    _ = target
    return False


def model_choice_entries_from_env(raw: str) -> list[str]:
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not entries:
        raise SystemExit(f"{INSTALL_ROLE_MODEL_CHOICES_ENV} must not be empty when provided.")
    return entries


def normalize_legacy_role_model_option_key(*, target: str, role_id: str, option_key: str) -> str:
    if target == "codex" and (role_id, option_key) in {
        ("480-code-scanner", "gpt-5.4-mini-high"),
        ("480-code-scanner", "gpt-5.4-mini-medium"),
    }:
        return "gpt-5.4-low"
    return option_key


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
        option_key = normalize_legacy_role_model_option_key(target=target, role_id=role_id, option_key=option_key)
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
    try:
        state = read_json_object(target.paths.state_file)
    except SystemExit:
        return None
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

    try:
        state = read_json_object(target.paths.state_file)
    except SystemExit:
        default_activation = provider.default_activation_default
        return bool(default_activation) if default_activation is not None else False
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
            default_marker = " (default)" if index == default_index else ""
            disabled_marker = " - unsupported" if choice.disabled else ""
            note_marker = f" — {choice.note}" if choice.note else ""
            print_line(output, f"  {index}) {choice.label}{default_marker}{disabled_marker}{note_marker}")

        output.write(f"Select [{default_index}]: ")
        output.flush()
        response = input_stream.readline()
        if response == "":
            raise SystemExit("Could not read install input.")

        answer = response.strip()
        selected = choice_map.get(str(default_index) if answer == "" else answer)
        if selected is None:
            print_line(output, "Invalid input. Please choose a number again.")
            continue
        if selected.disabled:
            print_line(output, f"{selected.label} scope is not yet supported for this target.")
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
            Choice(value="yes", label="Yes"),
            Choice(value="no", label="No"),
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
    allow_back: bool = False,
) -> None | TuiNavigateBack:
    import curses

    left_key = getattr(curses, "KEY_LEFT", None)
    scroll_offset = 0

    while True:
        rendered_lines, max_body_rows = tui_rendered_body_lines(screen, lines)
        max_scroll_offset = max(0, len(rendered_lines) - max_body_rows)
        footer_parts = [footer]
        if allow_back:
            footer_parts.append("Left Arrow: Back")
        if max_scroll_offset > 0:
            footer_parts.append("Up/Down Arrow or j/k: Scroll")
        scrollable_footer = " | ".join(footer_parts)
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
        if allow_back and left_key is not None and key == left_key:
            return TUI_NAVIGATE_BACK
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
        lines: list[str] = [
            "Select one or more providers.",
            "Press Space to select or deselect.",
            "",
        ]
        for index, choice in enumerate(choices):
            cursor = ">" if index == highlighted_index else " "
            checked = "x" if choice.value in selected_values else " "
            disabled_marker = " (unsupported)" if choice.disabled else ""
            note_marker = f" - {choice.note}" if choice.note else ""
            lines.append(f"{cursor} [{checked}] {choice.label}{disabled_marker}{note_marker}")

        tui_render_screen(
            screen,
            title=title,
            lines=lines,
            footer="Space: Select/Deselect | Enter: Next | Up/Down Arrow or j/k: Move",
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
                error = f"{highlighted_choice.label} is not available yet."
                continue
            if highlighted_choice.value in selected_values:
                selected_values.remove(highlighted_choice.value)
            else:
                selected_values.add(highlighted_choice.value)
            error = None
            continue
        if key in (curses.KEY_ENTER, 10, 13):
            if not selected_values:
                error = "Select at least one provider."
                continue
            return tuple(choice.value for choice in choices if choice.value in selected_values)


def tui_prompt_single_choice(
    screen: Any,
    *,
    title: str,
    choices: tuple[Choice, ...],
    default_value: str,
    allow_back: bool = False,
) -> str | TuiNavigateBack:
    import curses

    left_key = getattr(curses, "KEY_LEFT", None)
    highlighted_index = next(
        (index for index, choice in enumerate(choices) if choice.value == default_value and not choice.disabled),
        0,
    )
    error: str | None = None

    while True:
        lines: list[str] = []
        for index, choice in enumerate(choices):
            cursor = ">" if index == highlighted_index else " "
            default_marker = " (default)" if choice.value == default_value else ""
            disabled_marker = " (unsupported)" if choice.disabled else ""
            note_marker = f" - {choice.note}" if choice.note else ""
            lines.append(f"{cursor} {choice.label}{default_marker}{disabled_marker}{note_marker}")

        tui_render_screen(
            screen,
            title=title,
            lines=lines,
            footer=(
                "Enter: Next | Left Arrow: Back | Up/Down Arrow or j/k: Move"
                if allow_back
                else "Enter: Next | Up/Down Arrow or j/k: Move"
            ),
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
        if allow_back and left_key is not None and key == left_key:
            return TUI_NAVIGATE_BACK
        if key in (curses.KEY_ENTER, 10, 13):
            selected = choices[highlighted_index]
            if selected.disabled:
                error = f"{selected.label} is not available yet."
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
    lines = ["Review the selected install configuration.", ""]
    for request in requests:
        provider = get_provider(request.target)
        lines.append(f"- {provider.label}: scope={request.scope}")
        if request.activate_default is not None:
            activation = "yes" if request.activate_default else "no"
            lines.append(f"  default activation: {activation}")
        if request.enable_teams is not None:
            teams_enabled = "yes" if request.enable_teams else "no"
            lines.append(f"  agent teams: {teams_enabled}")
        if request.desktop_notifications is not None:
            desktop_notifications = "yes" if request.desktop_notifications else "no"
            lines.append(f"  desktop notifications: {desktop_notifications}")
        model_mode = request.model_selection.mode if request.model_selection is not None else "recommended"
        lines.append(f"  model mode: {model_mode}")
        if request.model_selection is not None and request.model_selection.mode == "advanced":
            lines.append("  model selection by role:")
            for spec in load_bundle():
                option = resolved_advanced_role_model_option(request, provider=provider, spec=spec)
                lines.append(f"    {spec.display_name}: {option.key} ({option.label})")
    return lines


def prompt_install_options_tui() -> InstallOptions:
    target_choices = required_interactive_provider_choices()
    role_specs = tuple(load_bundle())
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

        selected_targets = tuple(
            tui_prompt_multi_select(
                screen,
                title="Choose providers to install for 480/ai",
                choices=target_choices,
                default_values=tuple(choice.value for choice in target_choices),
            )
        )
        state_by_target: dict[str, dict[str, Any]] = {}

        def ensure_target_state(target: str) -> dict[str, Any]:
            state = state_by_target.get(target)
            if state is None:
                provider = get_provider(target)
                state = {
                    "scope": DEFAULT_SCOPE,
                    "activate_default": default_activation_for_target(target),
                    "enable_teams": teams_flag_default_for_target(target),
                    "desktop_notifications": desktop_notifications_default_for_target(target),
                    "model_mode": (
                        "advanced"
                        if DEFAULT_MODEL_SELECTION_MODE not in provider.supported_model_selection_modes()
                        else DEFAULT_MODEL_SELECTION_MODE
                    ),
                    "role_options": {},
                }
                state_by_target[target] = state
            return state

        def steps_for_target(target: str) -> list[str]:
            state = ensure_target_state(target)
            steps = ["scope"]
            if state["activate_default"] is not None:
                steps.append("activate_default")
            if state["enable_teams"] is not None:
                steps.append("enable_teams")
            if state["desktop_notifications"] is not None:
                steps.append("desktop_notifications")
            steps.append("model_mode")
            if state["model_mode"] == "advanced":
                steps.extend(f"role:{spec.identifier}" for spec in role_specs)
            return steps

        target_index = 0
        step_index = 0

        while True:
            while True:
                target = selected_targets[target_index]
                provider = get_provider(target)
                state = ensure_target_state(target)
                steps = steps_for_target(target)
                current_step = steps[step_index]

                if current_step == "scope":
                    selection = tui_prompt_single_choice(
                        screen,
                        title=f"Choose the install scope for {provider.label}.",
                        choices=scope_choices_for_target(target),
                        default_value=state["scope"],
                        allow_back=True,
                    )
                    if selection is TUI_NAVIGATE_BACK:
                        if target_index == 0:
                            selected_targets = tuple(
                                tui_prompt_multi_select(
                                    screen,
                                    title="Choose providers to install for 480/ai",
                                    choices=target_choices,
                                    default_values=selected_targets,
                                )
                            )
                            for selected_target in selected_targets:
                                ensure_target_state(selected_target)
                            target_index = 0
                            step_index = 0
                            continue
                        target_index -= 1
                        step_index = len(steps_for_target(selected_targets[target_index])) - 1
                        continue
                    state["scope"] = selection
                elif current_step == "activate_default":
                    selection = tui_prompt_single_choice(
                        screen,
                        title=f"Activate the default agent for {provider.label} now?",
                        choices=(
                            Choice(value="yes", label="Yes"),
                            Choice(value="no", label="No"),
                        ),
                        default_value="yes" if state["activate_default"] else "no",
                        allow_back=True,
                    )
                    if selection is TUI_NAVIGATE_BACK:
                        step_index -= 1
                        continue
                    state["activate_default"] = selection == "yes"
                elif current_step == "enable_teams":
                    selection = tui_prompt_single_choice(
                        screen,
                        title=f"Enable the {provider.label} agent teams experimental flag?",
                        choices=(
                            Choice(value="yes", label="Yes", note="Required for team-centered workflows"),
                            Choice(value="no", label="No", note="Install only and keep existing behavior"),
                        ),
                        default_value="yes" if state["enable_teams"] else "no",
                        allow_back=True,
                    )
                    if selection is TUI_NAVIGATE_BACK:
                        step_index -= 1
                        continue
                    state["enable_teams"] = selection == "yes"
                elif current_step == "desktop_notifications":
                    selection = tui_prompt_single_choice(
                        screen,
                        title=f"Install desktop notifications for {provider.label}?",
                        choices=(
                            Choice(value="yes", label="Yes", note="Show desktop alerts when work completes"),
                            Choice(value="no", label="No", note="Skip notification setup"),
                        ),
                        default_value="yes" if state["desktop_notifications"] else "no",
                        allow_back=True,
                    )
                    if selection is TUI_NAVIGATE_BACK:
                        step_index -= 1
                        continue
                    state["desktop_notifications"] = selection == "yes"
                elif current_step == "model_mode":
                    selection = tui_prompt_single_choice(
                        screen,
                        title=f"Choose the model mode for {provider.label}.",
                        choices=model_mode_choices_for_target(target),
                        default_value=state["model_mode"],
                        allow_back=True,
                    )
                    if selection is TUI_NAVIGATE_BACK:
                        step_index -= 1
                        continue
                    state["model_mode"] = selection
                else:
                    role_id = current_step.split(":", 1)[1]
                    spec = next(spec for spec in role_specs if spec.identifier == role_id)
                    choices = tuple(
                        Choice(value=option.key, label=option.label, note=option.note)
                        for option in provider.advanced_role_model_options(spec.identifier)
                    )
                    default_option_key = state["role_options"].get(role_id)
                    if default_option_key is None:
                        default_option_key = provider.default_advanced_role_model_option(spec).key
                    selection = tui_prompt_single_choice(
                        screen,
                        title=f"Choose the model for {provider.label} / {spec.display_name}.",
                        choices=choices,
                        default_value=default_option_key,
                        allow_back=True,
                    )
                    if selection is TUI_NAVIGATE_BACK:
                        step_index -= 1
                        continue
                    state["role_options"][role_id] = selection

                steps = steps_for_target(target)
                if step_index >= len(steps):
                    step_index = len(steps) - 1
                if step_index + 1 < len(steps):
                    step_index += 1
                    continue
                if target_index + 1 < len(selected_targets):
                    target_index += 1
                    step_index = 0
                    continue
                break

            requests = []
            for target in selected_targets:
                state = ensure_target_state(target)
                model_selection = None
                if state["model_mode"] == "advanced":
                    model_selection = advanced_model_selection_for_target(target, state["role_options"])
                requests.append(
                    ProviderInstallRequest(
                        target=target,
                        scope=state["scope"],
                        activate_default=state["activate_default"],
                        enable_teams=state["enable_teams"],
                        desktop_notifications=state["desktop_notifications"],
                        model_selection=model_selection,
                    )
                )

            summary_lines = build_install_summary_lines(tuple(requests))
            review_result = tui_prompt_review(
                screen,
                title="Install summary",
                lines=summary_lines,
                footer="Enter: Start install",
                allow_back=True,
            )
            if review_result is TUI_NAVIGATE_BACK:
                target_index = len(selected_targets) - 1
                step_index = len(steps_for_target(selected_targets[target_index])) - 1
                continue
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
    print_line(output, "Choose the 480/ai install target.")
    print_line(output)

    target = prompt_choice(
        output=output,
        input_stream=input_stream,
        title="Choose the CLI to install.",
        choices=target_choices,
        default_value=default_target,
    )
    scope = prompt_choice(
        output=output,
        input_stream=input_stream,
        title="Choose the install scope.",
        choices=scope_choices_for_target(target),
        default_value=DEFAULT_SCOPE,
    )

    activate_default = default_activation_for_target(target)
    if activate_default is not None:
        activate_default = prompt_bool_choice(
            output=output,
            input_stream=input_stream,
            title="Activate the default agent now?",
            default=activate_default,
        )

    enable_teams = teams_flag_default_for_target(target)
    if enable_teams is not None:
        enable_teams = prompt_bool_choice(
            output=output,
            input_stream=input_stream,
            title="Enable the Claude Code agent teams experimental flag?",
            default=enable_teams,
        )

    desktop_notifications = desktop_notifications_default_for_target(target)
    if desktop_notifications is not None:
        desktop_notifications = prompt_bool_choice(
            output=output,
            input_stream=input_stream,
            title="Install desktop notifications now?",
            default=desktop_notifications,
        )

    model_mode = prompt_choice(
        output=output,
        input_stream=input_stream,
        title="Choose the model mode.",
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
                    desktop_notifications=desktop_notifications,
                ),
            )
        )

    provider = get_provider(target)
    role_options: dict[str, str] = {}
    print_line(output, "Advanced mode: choose curated models by role.")
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
            title=f"Choose the model for {spec.display_name}.",
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
                desktop_notifications=desktop_notifications,
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
            args.desktop_notifications,
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
            INSTALL_DESKTOP_NOTIFY_ENV,
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

    desktop_notifications = args.desktop_notifications
    if desktop_notifications is None and INSTALL_DESKTOP_NOTIFY_ENV in env:
        desktop_notifications = parse_optional_bool(
            env[INSTALL_DESKTOP_NOTIFY_ENV],
            env_name=INSTALL_DESKTOP_NOTIFY_ENV,
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
                    desktop_notifications=desktop_notifications,
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
                    desktop_notifications=desktop_notifications,
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


def resolve_verify_options_from_inputs(
    *,
    args: argparse.Namespace,
    env: dict[str, str],
) -> tuple[str, str]:
    _ = env
    target = args.target or DEFAULT_VERIFY_TARGET
    scope = args.scope or DEFAULT_VERIFY_SCOPE
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
        if request.desktop_notifications is not None:
            install_kwargs["desktop_notifications"] = request.desktop_notifications
        if request.model_selection is not None:
            install_kwargs["model_selection"] = request.model_selection
        install(**install_kwargs)


def _codex_expected_agent_contents(target: InstallTarget) -> dict[str, object]:
    try:
        model_selection = load_persisted_model_selection(target)
        with tempfile.TemporaryDirectory() as tmp:
            expected_agents_dir = write_provider_outputs(
                target.name,
                repo_root=Path(tmp),
                model_selection=model_selection,
            )
            return {
                "status": "ok",
                "contents": {
                    path.name: path.read_text(encoding="utf-8")
                    for path in sorted(expected_agents_dir.glob(f"*{target.agent_file_extension}"))
                },
            }
    except SystemExit as exc:
        return {"status": "mismatch", "contents": {}, "error": str(exc)}


def _verify_codex_agent_outputs(target: InstallTarget) -> dict[str, object]:
    installed_dir = target.paths.installed_agents_dir
    expected = _codex_expected_agent_contents(target)
    expected_contents = expected["contents"]
    actual_paths = {
        path.name: path
        for path in sorted(installed_dir.glob(f"*{target.agent_file_extension}"))
    } if installed_dir.exists() else {}
    managed_actual_paths = {name: path for name, path in actual_paths.items() if name in expected_contents}

    missing = sorted(name for name in expected_contents if name not in managed_actual_paths)
    unexpected: list[str] = []
    mismatched = sorted(
        name
        for name in expected_contents.keys() & managed_actual_paths.keys()
        if managed_actual_paths[name].read_text(encoding="utf-8") != expected_contents[name]
    )
    unmanaged_files = sorted(name for name in actual_paths if name not in expected_contents)

    status = "ok" if expected["status"] == "ok" and not (missing or mismatched) else "mismatch"
    return {
        "status": status,
        "installed_dir": str(installed_dir),
        "missing": missing,
        "unexpected": unexpected,
        "mismatched": mismatched,
        "unmanaged_files": unmanaged_files,
        "expected_count": len(expected_contents),
        "installed_count": len(actual_paths),
        "error": expected.get("error"),
    }


def _verify_codex_managed_guidance(target: InstallTarget) -> dict[str, object]:
    guidance_path = target.paths.config_dir / "AGENTS.md"
    expected_guidance = render_codex_managed_guidance(load_bundle())
    if not guidance_path.exists():
        return {
            "status": "mismatch",
            "path": str(guidance_path),
            "present": False,
            "block_match": False,
        }

    contents = guidance_path.read_text(encoding="utf-8")
    block_span = installer_core.codex_managed_guidance_block_span(contents)
    if block_span is None:
        return {
            "status": "mismatch",
            "path": str(guidance_path),
            "present": True,
            "block_match": False,
        }

    start, end = block_span
    actual_guidance = contents[
        start + len(installer_core.CODEX_MANAGED_AGENTS_START) : end - len(installer_core.CODEX_MANAGED_AGENTS_END)
    ].strip("\n")
    block_match = actual_guidance == expected_guidance
    return {
        "status": "ok" if block_match else "mismatch",
        "path": str(guidance_path),
        "present": True,
        "block_match": block_match,
        "expected_block_start": installer_core.CODEX_MANAGED_AGENTS_START,
        "expected_block_end": installer_core.CODEX_MANAGED_AGENTS_END,
    }


def _verify_codex_config(target: InstallTarget) -> dict[str, object]:
    config_path = target.paths.config_file
    if config_path is None:
        return {"status": "mismatch", "path": None, "present": False, "missing": ["config_path"]}
    if not config_path.exists():
        return {"status": "mismatch", "path": str(config_path), "present": False, "missing": ["config_file"]}

    try:
        config = installer_core.read_toml_object(config_path)
    except SystemExit as exc:
        return {
            "status": "mismatch",
            "path": str(config_path),
            "present": True,
            "mismatches": ["invalid-toml"],
            "error": str(exc),
        }

    mismatches: list[str] = []
    for table_name, key_name, _rendered_value, parsed_value in installer_core.CODEX_REQUIRED_SETTINGS:
        table = config.get(table_name)
        if not isinstance(table, dict):
            mismatches.append(f"{table_name}.{key_name}:missing-table")
            continue
        if table.get(key_name) != parsed_value:
            mismatches.append(f"{table_name}.{key_name}:expected={parsed_value!r},actual={table.get(key_name)!r}")

    return {
        "status": "ok" if not mismatches else "mismatch",
        "path": str(config_path),
        "present": True,
        "mismatches": mismatches,
    }


def _verify_codex_cleanup(target: InstallTarget) -> dict[str, object]:
    legacy_files: list[str] = []
    for directory in (target.paths.installed_agents_dir, target.paths.backup_dir):
        if not directory.exists():
            continue
        for path in directory.glob(f"*{target.agent_file_extension}"):
            if path.name in {"480-architect.toml", "480.toml"}:
                legacy_files.append(str(path))
    legacy_files.sort()
    return {
        "status": "ok" if not legacy_files else "mismatch",
        "paths": [str(target.paths.installed_agents_dir), str(target.paths.backup_dir)],
        "legacy_files": legacy_files,
    }


def _run_codex_noop_validation(repo_root: Path) -> dict[str, object]:
    command = [
        "codex",
        "exec",
        "--json",
        "--cd",
        str(repo_root),
        "--dangerously-bypass-approvals-and-sandbox",
        CODEX_NOOP_VALIDATION_PROMPT,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        return {
            "status": "blocked",
            "command": command,
            "error": f"codex binary not found: {exc}",
        }

    if completed.returncode != 0:
        return {
            "status": "blocked",
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }

    parsed_message: dict[str, object] | None = None
    raw_message: str | None = None
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        raw_message = item.get("text") if isinstance(item.get("text"), str) else None
        if raw_message is None:
            continue
        try:
            parsed = json.loads(raw_message)
        except json.JSONDecodeError:
            parsed = {"raw": raw_message}
        if isinstance(parsed, dict):
            parsed_message = parsed

    if parsed_message is None:
        return {
            "status": "blocked",
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "error": "missing structured agent message",
        }

    developer_role = parsed_message.get("developer_role")
    redelegated = parsed_message.get("redelegated")
    notes = parsed_message.get("notes")
    status = "ok" if _codex_noop_validation_reports_developer_role(developer_role) and redelegated is False else "blocked"
    return {
        "status": status,
        "command": command,
        "returncode": completed.returncode,
        "developer_role": developer_role,
        "redelegated": redelegated,
        "notes": notes,
        "raw_message": raw_message,
    }


def _codex_noop_validation_reports_developer_role(role_value: object) -> bool:
    if not isinstance(role_value, str):
        return False
    normalized = role_value.strip()
    return "480-developer" in normalized


def _classify_verify_results(results: dict[str, dict[str, object]]) -> str:
    if results["install_state"]["status"] != "ok" or results["cleanup_result"]["status"] != "ok":
        return "install_issue"
    exec_path_result = results["exec_path_result"]
    if exec_path_result["status"] != "ok":
        if exec_path_result.get("error") or exec_path_result.get("returncode") not in (None, 0):
            return "platform_blocker"
        return "exec_path_limitation"
    return "success"


def _build_general_session_validation() -> dict[str, object]:
    return {
        "status": "not_run",
        "developer_role": None,
        "redelegated": None,
        "notes": "Separate Codex session validation is documented but not run by automated verify.",
        "raw_message": None,
    }


def verify(target: str = DEFAULT_VERIFY_TARGET, scope: str = DEFAULT_VERIFY_SCOPE) -> dict[str, object]:
    if target != "codex":
        raise SystemExit("verify currently supports only target codex.")
    if scope != "user":
        raise SystemExit("verify currently supports only scope user.")

    resolved_target = resolve_target(target, scope)
    install_state = {
        "agent_outputs": _verify_codex_agent_outputs(resolved_target),
        "config": _verify_codex_config(resolved_target),
        "guidance": _verify_codex_managed_guidance(resolved_target),
    }
    install_state["status"] = "ok" if all(section["status"] == "ok" for section in install_state.values() if isinstance(section, dict)) else "mismatch"

    cleanup_result = _verify_codex_cleanup(resolved_target)
    exec_path_result = _run_codex_noop_validation(REPO_ROOT)
    general_session_validation = _build_general_session_validation()

    results = {
        "install_state": install_state,
        "cleanup_result": cleanup_result,
        "general_session_validation": general_session_validation,
        "exec_path_result": exec_path_result,
    }
    results["final_classification"] = _classify_verify_results(results)
    return results


def install(
    target: str = DEFAULT_TARGET,
    scope: str = DEFAULT_SCOPE,
    force: bool = False,
    activate_default: bool | None = None,
    enable_teams: bool | None = None,
    desktop_notifications: bool | None = None,
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
            enable_desktop_notifications=desktop_notifications,
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
            enable_desktop_notifications=desktop_notifications,
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
    parser.add_argument("action", choices=("install", "uninstall", "verify"))
    parser.add_argument("--target")
    parser.add_argument("--scope")
    activation_group = parser.add_mutually_exclusive_group()
    activation_group.add_argument("--activate-default", dest="activate_default", action="store_true")
    activation_group.add_argument("--no-activate-default", dest="activate_default", action="store_false")
    desktop_notify_group = parser.add_mutually_exclusive_group()
    desktop_notify_group.add_argument("--desktop-notify", dest="desktop_notifications", action="store_true")
    desktop_notify_group.add_argument("--no-desktop-notify", dest="desktop_notifications", action="store_false")
    parser.add_argument("--model-mode", choices=("recommended", "advanced"))
    parser.add_argument("--role-model-choice", action="append")
    parser.set_defaults(activate_default=None)
    parser.set_defaults(desktop_notifications=None)
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
    elif args.action == "uninstall":
        target, scope = resolve_uninstall_options_from_inputs(args=args, env=env)
        uninstall(target=target, scope=scope)
    else:
        target, scope = resolve_verify_options_from_inputs(args=args, env=env)
        result = verify(target=target, scope=scope)
        print_line(sys.stdout, json.dumps(result, ensure_ascii=False))
        return 0 if result["final_classification"] in {"success", "exec_path_limitation"} else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
