from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import shlex
from importlib import import_module
from pathlib import Path

if __package__:
    from .install_targets import InstallTarget
else:  # pragma: no cover
    from install_targets import InstallTarget


REPO_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_NOTIFICATION_HOOK_TEMPLATE = r'''#!/usr/bin/env python3

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

TITLE_LIMIT = 72
MESSAGE_LIMIT = 220
SESSION_GLOB = "rollout-*{thread_id}.jsonl"


def collapse_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def truncate_text(value: object, limit: int) -> str:
    text = collapse_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def project_name(cwd: str) -> str:
    if not cwd:
        return ""
    return pathlib.Path(cwd).name


def nested_subagent_source(value: object) -> bool:
    if isinstance(value, dict):
        if "subagent" in value:
            return True
        return any(nested_subagent_source(item) for item in value.values())
    if isinstance(value, list):
        return any(nested_subagent_source(item) for item in value)
    return False


def payload_thread_id(payload: dict) -> str:
    for key in ("thread-id", "thread_id", "threadId", "session-id", "session_id", "sessionId"):
        thread_id = collapse_text(payload.get(key))
        if thread_id:
            return thread_id
    return ""


def codex_session_directories() -> tuple[pathlib.Path, ...]:
    codex_home = pathlib.Path.home() / ".codex"
    return (codex_home / "sessions", codex_home / "archived_sessions")


def load_session_meta(thread_id: str) -> dict | None:
    if not thread_id:
        return None

    pattern = SESSION_GLOB.format(thread_id=thread_id)
    for directory in codex_session_directories():
        if not directory.exists():
            continue

        for session_path in directory.rglob(pattern):
            try:
                first_line = session_path.read_text(encoding="utf-8").splitlines()[0]
                record = json.loads(first_line)
            except (IndexError, OSError, json.JSONDecodeError):
                continue

            if record.get("type") != "session_meta":
                continue

            payload = record.get("payload") or {}
            if collapse_text(payload.get("id")) == thread_id:
                return payload

    return None


def codex_subagent_payload(payload: dict) -> bool:
    source = payload.get("source")
    if nested_subagent_source(source):
        return True

    session_meta = load_session_meta(payload_thread_id(payload))
    if not session_meta:
        return False

    return nested_subagent_source(session_meta.get("source"))


def codex_notification(payload: dict) -> dict | None:
    if payload.get("type") != "agent-turn-complete":
        return None
    if codex_subagent_payload(payload):
        return None

    cwd = collapse_text(payload.get("cwd"))
    title = "Codex completed"
    project = project_name(cwd)
    if project:
        title += f" · {project}"

    message = collapse_text(payload.get("last-assistant-message"))
    if not message:
        inputs = payload.get("input-messages") or []
        if inputs:
            message = collapse_text(inputs[-1])
    if not message:
        message = "Response is ready."

    return {
        "title": title,
        "message": message,
        "group": f"codex:{cwd or 'global'}",
    }


def opencode_notification(payload: dict) -> dict | None:
    if payload.get("event") != "session.idle":
        return None

    cwd = collapse_text(payload.get("cwd"))
    title = "OpenCode completed"
    project = project_name(cwd)
    if project:
        title += f" · {project}"

    message = collapse_text(payload.get("summary")) or "Response is ready."
    return {
        "title": title,
        "message": message,
        "group": f"opencode:{cwd or 'global'}",
    }


def claude_notification(payload: dict) -> dict | None:
    if payload.get("hook_event_name") != "Notification":
        return None

    notification_type = collapse_text(payload.get("notification_type"))
    title = collapse_text(payload.get("title"))
    if not title:
        title_map = {
            "permission_prompt": "Permission needed",
            "idle_prompt": "Claude Code idle",
            "auth_success": "Authentication succeeded",
            "elicitation_dialog": "Input required",
        }
        title = title_map.get(notification_type, "Claude Code notification")

    message = collapse_text(payload.get("message")) or "Response is ready."
    cwd = collapse_text(payload.get("cwd"))
    return {
        "title": title,
        "message": message,
        "group": f"claude:{cwd or 'global'}:{notification_type or 'notification'}",
    }


def build_notification(source: str, payload: dict) -> dict | None:
    if source == "codex":
        return codex_notification(payload)
    if source == "opencode":
        return opencode_notification(payload)
    if source == "claude":
        return claude_notification(payload)
    return None


def send_with_terminal_notifier(title: str, message: str, group: str) -> bool:
    terminal_notifier = shutil.which("terminal-notifier")
    if not terminal_notifier:
        return False

    completed = subprocess.run(
        [
            terminal_notifier,
            "-title",
            truncate_text(title, TITLE_LIMIT),
            "-message",
            truncate_text(message, MESSAGE_LIMIT),
            "-group",
            group,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def send_with_osascript(title: str, message: str) -> None:
    script = (
        "on run argv\n"
        "display notification (item 2 of argv) with title (item 1 of argv)\n"
        "end run\n"
    )
    subprocess.run(
        [
            "osascript",
            "-e",
            script,
            truncate_text(title, TITLE_LIMIT),
            truncate_text(message, MESSAGE_LIMIT),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def load_payload() -> tuple[str, dict] | None:
    if len(sys.argv) < 2:
        return None

    source = collapse_text(sys.argv[1])
    raw_payload = sys.argv[2] if len(sys.argv) >= 3 else sys.stdin.read()
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return source, payload


def main() -> int:
    loaded = load_payload()
    if loaded is None:
        return 0

    source, payload = loaded
    notification = build_notification(source, payload)
    if not notification:
        return 0

    if send_with_terminal_notifier(
        notification["title"], notification["message"], notification["group"]
    ):
        return 0

    if shutil.which("osascript"):
        send_with_osascript(notification["title"], notification["message"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
OPENCODE_DESKTOP_NOTIFICATION_PLUGIN_TEMPLATE = r'''const NOTIFY_SCRIPT = "__NOTIFY_SCRIPT__";

const collapseText = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

const extractSummary = (parts) =>
  collapseText(
    parts
      .filter((part) => part?.type === "text" && !part?.ignored)
      .map((part) => part.text)
      .join(" "),
  );

const latestAssistantMessage = async (client, sessionID) => {
  const result = await client.session.messages({ sessionID });
  if (result.error || !result.data) {
    return null;
  }

  const messages = [...result.data].reverse();
  for (const entry of messages) {
    if (entry.info?.role !== "assistant") {
      continue;
    }

    return {
      id: entry.info.id,
      cwd: entry.info.path?.cwd ?? "",
      summary: extractSummary(entry.parts) || "Response is ready.",
    };
  }

  return null;
};

export const DesktopNotifyPlugin = async ({ client, $, directory }) => {
  const notifiedMessageIDs = new Map();

  return {
    event: async ({ event }) => {
      if (event.type !== "session.idle") {
        return;
      }

      try {
        const sessionID = event.properties.sessionID;
        const latest = await latestAssistantMessage(client, sessionID);
        if (!latest) {
          return;
        }

        if (notifiedMessageIDs.get(sessionID) === latest.id) {
          return;
        }

        notifiedMessageIDs.set(sessionID, latest.id);

        const payload = JSON.stringify({
          event: event.type,
          sessionID,
          cwd: latest.cwd || directory || "",
          summary: latest.summary,
        });

        await $`${NOTIFY_SCRIPT} opencode ${payload}`.quiet();
      } catch (error) {
        console.error(
          "[desktop-notify-plugin]",
          error instanceof Error ? error.message : String(error),
        );
      }
    },
  };
};
'''

DESKTOP_NOTIFICATION_STATE_KEY = "desktop_notifications"
DESKTOP_NOTIFICATION_STATE_FILES_KEY = "files"
DESKTOP_NOTIFICATION_HOOK_ARTIFACT_KEY = "hook_script"
DESKTOP_NOTIFICATION_OPENCODE_PLUGIN_ARTIFACT_KEY = "opencode_plugin"


def target_default_activation_state(target: InstallTarget) -> tuple[str, str] | None:
    activation = target.default_activation
    if activation is None:
        return None
    return activation.config_key, activation.managed_value


def legacy_agent_name_map(target: InstallTarget) -> dict[str, str]:
    if target.name != "claude" or target.agent_file_extension != ".md":
        return {}
    return {
        "ai-architect": "480-architect",
        "ai-developer": "480-developer",
        "ai-code-reviewer": "480-code-reviewer",
        "ai-code-reviewer-secondary": "480-code-reviewer2",
        "ai-code-scanner": "480-code-scanner",
    }


def legacy_default_activation_values(target: InstallTarget) -> set[str]:
    activation = target_default_activation_state(target)
    if activation is None:
        return set()
    _config_key, managed_value = activation
    legacy_names = legacy_agent_name_map(target)
    values = {managed_value}
    if managed_value == "480-architect":
        values.add("ai-architect")
    legacy_value = next((legacy for legacy, current in legacy_names.items() if current == managed_value), None)
    if legacy_value is not None:
        values.add(legacy_value)
    return values


def expected_backup_relative_paths(target: InstallTarget, agent_names: list[str]) -> dict[str, str]:
    return {
        name: str(backup_path(target, name).relative_to(target.paths.state_dir))
        for name in agent_names
    }


def expected_legacy_backup_relative_paths(target: InstallTarget) -> dict[str, str]:
    return {
        legacy_name: str(backup_path(target, legacy_name).relative_to(target.paths.state_dir))
        for legacy_name in legacy_agent_name_map(target)
    }


def read_json(path: Path) -> object:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON at {path}: {exc}") from exc


def read_json_object(path: Path) -> dict:
    data = read_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"Expected JSON object at {path}.")
    return data


def read_toml_object(path: Path) -> dict:
    if not path.exists():
        return {}
    toml_module = load_toml_module()
    try:
        data = toml_module.loads(path.read_text(encoding="utf-8"))
    except toml_module.TOMLDecodeError as exc:
        raise SystemExit(f"Invalid TOML at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Expected TOML table at {path}.")
    return data


def load_toml_module():
    for module_name in ("tomllib", "tomli"):
        try:
            return import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
    try:
        if __package__:
            from . import _vendor_tomllib
        else:  # pragma: no cover
            import _vendor_tomllib
    except ModuleNotFoundError as exc:
        vendor_module_name = "app._vendor_tomllib" if __package__ else "_vendor_tomllib"
        if exc.name != vendor_module_name:
            raise
        raise SystemExit("Could not load the bundled TOML support module.") from exc
    return _vendor_tomllib


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def write_text_atomic(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def managed_agent_filename(target: InstallTarget, name: str) -> str:
    return f"{name}{target.agent_file_extension}"


def source_agent_path(source_agents_dir: Path, target: InstallTarget, name: str) -> Path:
    return source_agents_dir / managed_agent_filename(target, name)


def installed_agent_path(target: InstallTarget, name: str) -> Path:
    return target.paths.installed_agents_dir / managed_agent_filename(target, name)


def backup_path(target: InstallTarget, name: str) -> Path:
    return target.paths.backup_dir / managed_agent_filename(target, name)


def read_target_config(target: InstallTarget) -> dict:
    config_path = target.paths.config_file
    if config_path is None:
        if target.default_activation is not None:
            raise SystemExit(f"Install target '{target.name}' is missing a config file path.")
        return {}
    ensure_path_hierarchy_safe(config_path)
    if target.name == "codex":
        return read_toml_object(config_path)
    return read_json_object(config_path)


def write_target_config(target: InstallTarget, config: dict) -> None:
    config_path = target.paths.config_file
    if config_path is None:
        if target.default_activation is not None:
            raise SystemExit(f"Install target '{target.name}' is missing a config file path.")
        return
    if target.name == "codex":
        raise SystemExit("Codex config writes must use merge_codex_subagent_settings.")
    write_json(config_path, config)


MANAGED_CONFIG_STATE_KEY = "managed_config"

CODEX_REQUIRED_SETTINGS = (
    ("features", "multi_agent", "true", True),
    ("agents", "max_depth", "2", 2),
    ("agents", "max_threads", "200", 200),
)


def _json_scalar(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    return str(value)


def _managed_config_state(state: dict) -> dict[str, object] | None:
    managed_config = state.get(MANAGED_CONFIG_STATE_KEY)
    if not isinstance(managed_config, dict):
        return None
    return managed_config


def managed_config_existed_before_install(state: dict) -> bool:
    managed_config = _managed_config_state(state)
    if managed_config is None:
        return True
    return managed_config.get("existed_before_install") is True


def capture_managed_config_state_on_install(target: InstallTarget, state: dict, config: dict) -> None:
    config_path = target.paths.config_file
    if config_path is None or MANAGED_CONFIG_STATE_KEY in state:
        return

    managed_config: dict[str, object] = {
        "existed_before_install": config_path.exists(),
    }
    if target.name == "codex":
        settings: dict[str, dict[str, object]] = {}
        for table_name, key_name, _rendered_value, _parsed_value in CODEX_REQUIRED_SETTINGS:
            full_key = f"{table_name}.{key_name}"
            table = config.get(table_name)
            if isinstance(table, dict) and key_name in table:
                settings[full_key] = {
                    "present": True,
                    "value": _json_value(table[key_name]),
                }
            else:
                settings[full_key] = {
                    "present": False,
                    "value": None,
                }
        managed_config["codex_required_settings"] = settings
        notify_value = config.get("notify")
        managed_config["codex_notify"] = {
            "present": "notify" in config,
            "value": _json_value(notify_value) if "notify" in config else None,
        }
    elif target.name == "claude":
        hooks = config.get("hooks")
        if isinstance(hooks, dict) and "Notification" in hooks:
            managed_config["claude_notification"] = {
                "present": True,
                "value": _json_value(hooks.get("Notification")),
            }
        else:
            managed_config["claude_notification"] = {
                "present": False,
                "value": None,
            }

    state[MANAGED_CONFIG_STATE_KEY] = managed_config


def maybe_remove_empty_managed_config(target: InstallTarget, state: dict, config: dict) -> bool:
    config_path = target.paths.config_file
    if config_path is None or managed_config_existed_before_install(state) or config:
        return False
    if config_path.exists():
        config_path.unlink()
        return True
    return False


def _render_toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return json.dumps(value)


def _render_toml_value(value: object) -> str:
    if isinstance(value, (bool, int, float, str)):
        return _render_toml_scalar(value)
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, dict):
        return json.dumps(value)
    return _render_toml_scalar(value)


def _nested_toml_value(data: dict, table_name: str, key_name: str) -> tuple[bool, object]:
    table = data.get(table_name)
    if not isinstance(table, dict) or key_name not in table:
        return False, None
    return True, table[key_name]


def _replace_toml_key_assignment(contents: str, key_path: str, rendered_value: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(?m)^(?P<prefix>\s*{re.escape(key_path)}\s*=\s*)(?P<value>[^#\n]*?)(?P<suffix>\s*(?:#.*)?)$")
    match = pattern.search(contents)
    if match is None:
        return contents, False
    if match.group("value").strip() == rendered_value:
        return contents, False
    replaced = pattern.sub(rf"\g<prefix>{rendered_value}\g<suffix>", contents, count=1)
    return replaced, True


def _merge_toml_root_key(contents: str, key_name: str, rendered_value: str) -> tuple[str, bool]:
    updated, changed = _replace_toml_key_assignment(contents, key_name, rendered_value)
    if changed:
        return updated, True

    line = f"{key_name} = {rendered_value}\n"
    if not contents:
        return line, True

    first_table_match = re.compile(r"(?m)^\[[^\n]+\]\s*(?:#.*)?$").search(contents)
    if first_table_match is None:
        return contents.rstrip("\n") + "\n" + line, True

    prefix = contents[: first_table_match.start()].rstrip("\n")
    suffix = contents[first_table_match.start() :].lstrip("\n")
    if prefix:
        prefix = prefix + "\n" + line.rstrip("\n")
    else:
        prefix = line.rstrip("\n")
    return prefix + "\n\n" + suffix, True


def _remove_toml_root_key(contents: str, key_name: str) -> tuple[str, bool]:
    return _remove_toml_key_assignment(contents, key_name)


def _remove_toml_key_assignment(contents: str, key_path: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key_path)}\s*=\s*[^#\n]*(?:\s*#.*)?$\n?")
    match = pattern.search(contents)
    if match is None:
        return contents, False
    return contents[: match.start()] + contents[match.end() :], True


def _table_header_pattern(table_name: str) -> re.Pattern[str]:
    return re.compile(rf"(?m)^\[{re.escape(table_name)}\]\s*(?:#.*)?$")


def _insert_toml_dotted_table_key(contents: str, table_name: str, key_name: str, rendered_value: str) -> tuple[str, bool]:
    dotted_table_pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(table_name)}\.[A-Za-z0-9_-]+\s*=\s*[^#\n]*(?:\s*#.*)?$"
    )
    matches = list(dotted_table_pattern.finditer(contents))
    if not matches:
        return contents, False

    last_match = matches[-1]
    insert_at = last_match.end()
    line = f"\n{table_name}.{key_name} = {rendered_value}"
    return contents[:insert_at] + line + contents[insert_at:], True


def _merge_toml_table_key(contents: str, table_name: str, key_name: str, rendered_value: str) -> tuple[str, bool]:
    dotted_key = f"{table_name}.{key_name}"
    updated, changed = _replace_toml_key_assignment(contents, dotted_key, rendered_value)
    if changed:
        return updated, True

    table_match = _table_header_pattern(table_name).search(contents)
    if table_match is None:
        updated, changed = _insert_toml_dotted_table_key(contents, table_name, key_name, rendered_value)
        if changed:
            return updated, True
        block = f"[{table_name}]\n{key_name} = {rendered_value}\n"
        if not contents:
            return block, True
        separator = "\n\n" if not contents.endswith("\n\n") else ""
        return contents.rstrip("\n") + separator + block, True

    section_start = table_match.end()
    next_table_match = re.compile(r"(?m)^\[[^\n]+\]\s*(?:#.*)?$").search(contents, section_start)
    section_end = len(contents) if next_table_match is None else next_table_match.start()
    section_body = contents[section_start:section_end]

    key_pattern = re.compile(rf"(?m)^(?P<prefix>\s*{re.escape(key_name)}\s*=\s*)(?P<value>[^#\n]*?)(?P<suffix>\s*(?:#.*)?)$")
    key_match = key_pattern.search(section_body)
    if key_match is not None:
        if key_match.group("value").strip() == rendered_value:
            return contents, False
        updated_section_body = key_pattern.sub(rf"\g<prefix>{rendered_value}\g<suffix>", section_body, count=1)
        return contents[:section_start] + updated_section_body + contents[section_end:], True

    if section_body:
        updated_section_body = section_body
        if not updated_section_body.endswith("\n"):
            updated_section_body += "\n"
        updated_section_body += f"{key_name} = {rendered_value}\n"
    else:
        updated_section_body = f"\n{key_name} = {rendered_value}\n"
    return contents[:section_start] + updated_section_body + contents[section_end:], True


def _remove_empty_toml_table(contents: str, table_name: str) -> tuple[str, bool]:
    table_match = _table_header_pattern(table_name).search(contents)
    if table_match is None:
        return contents, False

    header_start = table_match.start()
    section_start = table_match.end()
    next_table_match = re.compile(r"(?m)^\[[^\n]+\]\s*(?:#.*)?$").search(contents, section_start)
    section_end = len(contents) if next_table_match is None else next_table_match.start()
    section_body = contents[section_start:section_end]
    if section_body.strip():
        return contents, False

    before = contents[:header_start]
    after = contents[section_end:]
    if not before:
        return after.lstrip("\n"), True
    if not after:
        return before.rstrip("\n") + ("\n" if before else ""), True
    return before.rstrip("\n") + "\n\n" + after.lstrip("\n"), True


def _remove_toml_table_key(contents: str, table_name: str, key_name: str) -> tuple[str, bool]:
    dotted_key = f"{table_name}.{key_name}"
    updated, changed = _remove_toml_key_assignment(contents, dotted_key)
    if changed:
        return updated, True

    table_match = _table_header_pattern(table_name).search(contents)
    if table_match is None:
        return contents, False

    section_start = table_match.end()
    next_table_match = re.compile(r"(?m)^\[[^\n]+\]\s*(?:#.*)?$").search(contents, section_start)
    section_end = len(contents) if next_table_match is None else next_table_match.start()
    section_body = contents[section_start:section_end]

    key_pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key_name)}\s*=\s*[^#\n]*(?:\s*#.*)?$\n?")
    key_match = key_pattern.search(section_body)
    if key_match is None:
        return contents, False

    updated_section_body = section_body[: key_match.start()] + section_body[key_match.end() :]
    updated_contents = contents[:section_start] + updated_section_body + contents[section_end:]
    return _remove_empty_toml_table(updated_contents, table_name)


def desktop_notification_hook_path(target: InstallTarget) -> Path:
    return target.paths.config_dir / ".480ai" / "desktop-notify-hook.py"


def opencode_desktop_notification_plugin_path(target: InstallTarget) -> Path:
    return target.paths.config_dir / "plugins" / "480ai-desktop-notify.js"


def load_desktop_notification_hook_template() -> str:
    return DESKTOP_NOTIFICATION_HOOK_TEMPLATE


def render_opencode_desktop_notification_plugin(hook_path: Path) -> str:
    return OPENCODE_DESKTOP_NOTIFICATION_PLUGIN_TEMPLATE.replace("__NOTIFY_SCRIPT__", str(hook_path))


def _desktop_notification_state(state: dict) -> dict[str, object] | None:
    managed = state.get(DESKTOP_NOTIFICATION_STATE_KEY)
    if not isinstance(managed, dict):
        return None
    files = managed.get(DESKTOP_NOTIFICATION_STATE_FILES_KEY)
    if files is None:
        managed[DESKTOP_NOTIFICATION_STATE_FILES_KEY] = {}
        return managed
    if not isinstance(files, dict):
        managed[DESKTOP_NOTIFICATION_STATE_FILES_KEY] = {}
    return managed


def _desktop_notification_files_state(state: dict) -> dict[str, dict[str, object]] | None:
    managed = _desktop_notification_state(state)
    if managed is None:
        return None
    files = managed.get(DESKTOP_NOTIFICATION_STATE_FILES_KEY)
    if not isinstance(files, dict):
        return None
    return files


def desktop_notification_backup_path(target: InstallTarget, artifact_key: str) -> Path:
    return target.paths.state_dir / "desktop-notifications" / f"{artifact_key}.bak"


def capture_desktop_notification_asset_state(
    target: InstallTarget,
    state: dict,
    *,
    artifact_key: str,
    path: Path,
    desired_contents: str,
) -> bool:
    ensure_path_hierarchy_safe(path)
    files_state = _desktop_notification_files_state(state)
    if files_state is None:
        managed = state.setdefault(DESKTOP_NOTIFICATION_STATE_KEY, {})
        if not isinstance(managed, dict):
            raise SystemExit("Invalid desktop notification state.")
        managed[DESKTOP_NOTIFICATION_STATE_FILES_KEY] = {}
        files_state = managed[DESKTOP_NOTIFICATION_STATE_FILES_KEY]
        assert isinstance(files_state, dict)

    file_state = files_state.get(artifact_key)
    current_contents = path.read_text(encoding="utf-8") if path.exists() else None
    if not isinstance(file_state, dict):
        backup_relative: str | None = None
        if current_contents is not None and current_contents != desired_contents:
            backup = desktop_notification_backup_path(target, artifact_key)
            ensure_path_hierarchy_safe(backup)
            backup.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(backup, current_contents)
            backup_relative = str(backup.relative_to(target.paths.state_dir))

        files_state[artifact_key] = {
            "present_before_install": current_contents is not None,
            "backup": backup_relative,
        }

    if current_contents != desired_contents:
        write_text_atomic(path, desired_contents)
        if artifact_key == DESKTOP_NOTIFICATION_HOOK_ARTIFACT_KEY:
            path.chmod(0o755)
        return True
    if artifact_key == DESKTOP_NOTIFICATION_HOOK_ARTIFACT_KEY and path.exists():
        path.chmod(0o755)
    return False


def restore_desktop_notification_asset(
    target: InstallTarget,
    state: dict,
    *,
    artifact_key: str,
    path: Path,
    desired_contents: str,
) -> bool:
    ensure_path_hierarchy_safe(path)
    files_state = _desktop_notification_files_state(state)
    if files_state is None:
        return False

    file_state = files_state.get(artifact_key)
    if not isinstance(file_state, dict):
        return False

    if not path.exists():
        backup_relative = file_state.get("backup")
        if isinstance(backup_relative, str) and backup_relative:
            backup = target.paths.state_dir / backup_relative
            if backup.exists():
                backup.unlink()
        files_state.pop(artifact_key, None)
        return False

    current_contents = path.read_text(encoding="utf-8")
    if current_contents != desired_contents:
        return False

    backup_relative = file_state.get("backup")
    if file_state.get("present_before_install") is True:
        if isinstance(backup_relative, str) and backup_relative:
            backup = target.paths.state_dir / backup_relative
            if backup.exists():
                write_text_atomic(path, backup.read_text(encoding="utf-8"))
                if artifact_key == DESKTOP_NOTIFICATION_HOOK_ARTIFACT_KEY:
                    path.chmod(0o755)
                backup.unlink()
                files_state.pop(artifact_key, None)
                return True
        return False

    path.unlink()
    if isinstance(backup_relative, str) and backup_relative:
        backup = target.paths.state_dir / backup_relative
        if backup.exists():
            backup.unlink()
    files_state.pop(artifact_key, None)
    return True


def install_desktop_notification_assets(target: InstallTarget, state: dict) -> bool:
    hook_path = desktop_notification_hook_path(target)
    hook_contents = load_desktop_notification_hook_template()
    changed = capture_desktop_notification_asset_state(
        target,
        state,
        artifact_key=DESKTOP_NOTIFICATION_HOOK_ARTIFACT_KEY,
        path=hook_path,
        desired_contents=hook_contents,
    )

    if target.name == "opencode":
        plugin_path = opencode_desktop_notification_plugin_path(target)
        plugin_contents = render_opencode_desktop_notification_plugin(hook_path)
        changed = (
            capture_desktop_notification_asset_state(
                target,
                state,
                artifact_key=DESKTOP_NOTIFICATION_OPENCODE_PLUGIN_ARTIFACT_KEY,
                path=plugin_path,
                desired_contents=plugin_contents,
            )
            or changed
        )

    return changed


def restore_desktop_notification_assets(target: InstallTarget, state: dict) -> bool:
    hook_path = desktop_notification_hook_path(target)
    hook_contents = load_desktop_notification_hook_template()
    changed = restore_desktop_notification_asset(
        target,
        state,
        artifact_key=DESKTOP_NOTIFICATION_HOOK_ARTIFACT_KEY,
        path=hook_path,
        desired_contents=hook_contents,
    )

    if target.name == "opencode":
        plugin_path = opencode_desktop_notification_plugin_path(target)
        plugin_contents = render_opencode_desktop_notification_plugin(hook_path)
        changed = (
            restore_desktop_notification_asset(
                target,
                state,
                artifact_key=DESKTOP_NOTIFICATION_OPENCODE_PLUGIN_ARTIFACT_KEY,
                path=plugin_path,
                desired_contents=plugin_contents,
            )
            or changed
        )

    return changed


def codex_desktop_notification_notify_value(target: InstallTarget) -> list[str]:
    hook_path = desktop_notification_hook_path(target)
    return [str(hook_path), "codex"]


def merge_claude_desktop_notification_hook(
    target: InstallTarget,
    state: dict,
    config: dict,
    *,
    command: str,
    enabled: bool,
) -> bool:
    if not enabled or target.name != "claude":
        return False

    hook_entry = {
        "hooks": [
            {
                "type": "command",
                "command": command,
            }
        ]
    }

    managed_config = _managed_config_state(state)
    if managed_config is None:
        raise SystemExit("Claude config state is missing.")
    if not isinstance(managed_config.get("claude_notification"), dict):
        hooks = config.get("hooks")
        if isinstance(hooks, dict) and "Notification" in hooks:
            managed_config["claude_notification"] = {
                "present": True,
                "value": _json_value(hooks.get("Notification")),
            }
        else:
            managed_config["claude_notification"] = {
                "present": False,
                "value": None,
            }

    hooks = config.get("hooks")
    if hooks is None:
        config["hooks"] = {"Notification": [hook_entry]}
        return True
    if not isinstance(hooks, dict):
        config_path = target.paths.config_file
        if config_path is None:
            raise SystemExit("Claude notification hooks require a JSON object config file.")
        raise SystemExit(f"Expected JSON object at {config_path} for hooks.")

    notifications = hooks.get("Notification")
    if notifications is None:
        hooks["Notification"] = [hook_entry]
        return True
    if not isinstance(notifications, list):
        config_path = target.paths.config_file
        if config_path is None:
            raise SystemExit("Claude notification hooks require a Notification hook array.")
        raise SystemExit(f"Expected JSON array at {config_path} for hooks.Notification.")
    if hook_entry in notifications:
        return False

    notifications.append(hook_entry)
    return True


def restore_claude_desktop_notification_hook(
    target: InstallTarget,
    state: dict,
    config: dict,
    *,
    command: str,
) -> bool:
    if target.name != "claude":
        return False

    hook_entry = {
        "hooks": [
            {
                "type": "command",
                "command": command,
            }
        ]
    }

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False

    notifications = hooks.get("Notification")
    if not isinstance(notifications, list):
        return False

    managed_config = _managed_config_state(state)
    if managed_config is None:
        return False

    notification_state = managed_config.get("claude_notification")
    if not isinstance(notification_state, dict):
        return False

    original_present = notification_state.get("present") is True
    original_value = notification_state.get("value")
    if original_present and isinstance(original_value, list) and hook_entry in original_value:
        return False

    if original_present:
        if not isinstance(original_value, list):
            return False
        desired_notifications = original_value + [hook_entry]
    else:
        desired_notifications = [hook_entry]

    if notifications != desired_notifications:
        return False

    if original_present:
        hooks["Notification"] = original_value
    else:
        hooks.pop("Notification", None)
        if not hooks:
            config.pop("hooks", None)
    return True


def merge_codex_desktop_notification_hook(
    target: InstallTarget,
    state: dict,
    config: dict,
    *,
    enabled: bool,
) -> bool:
    if not enabled or target.name != "codex":
        return False

    config_path = target.paths.config_file
    if config_path is None:
        raise SystemExit("Install target 'codex' is missing a config file path.")
    ensure_path_hierarchy_safe(config_path)

    managed_config = _managed_config_state(state)
    if managed_config is None:
        raise SystemExit("Codex config state is missing.")

    existing_notify_state = managed_config.get("codex_notify")
    if not isinstance(existing_notify_state, dict):
        managed_config["codex_notify"] = {
            "present": "notify" in config,
            "value": _json_value(config.get("notify")) if "notify" in config else None,
        }

    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    desired_notify = _render_toml_value(codex_desktop_notification_notify_value(target))
    updated, changed = _merge_toml_root_key(existing, "notify", desired_notify)
    if updated.strip():
        toml_module = load_toml_module()
        try:
            toml_module.loads(updated)
        except toml_module.TOMLDecodeError as exc:
            raise SystemExit(f"Invalid TOML generated for {config_path}: {exc}") from exc
    if not changed:
        return False

    write_text_atomic(config_path, updated.rstrip("\n") + "\n")
    return True


def restore_codex_desktop_notification_hook(
    target: InstallTarget,
    state: dict,
    config: dict,
) -> bool:
    if target.name != "codex":
        return False

    config_path = target.paths.config_file
    if config_path is None or not config_path.exists():
        return False
    ensure_path_hierarchy_safe(config_path)

    managed_config = _managed_config_state(state)
    if managed_config is None:
        return False
    notify_state = managed_config.get("codex_notify")
    if not isinstance(notify_state, dict):
        return False

    current_notify = config.get("notify")
    desired_notify = codex_desktop_notification_notify_value(target)
    if current_notify != desired_notify:
        return False

    existing = config_path.read_text(encoding="utf-8")
    updated = existing
    changed = False
    if notify_state.get("present") is True:
        original_value = notify_state.get("value")
        updated, changed = _merge_toml_root_key(updated, "notify", _render_toml_value(original_value))
    else:
        updated, changed = _remove_toml_root_key(updated, "notify")

    if not changed:
        return False

    toml_module = load_toml_module()
    if updated.strip():
        try:
            toml_module.loads(updated)
        except toml_module.TOMLDecodeError as exc:
            raise SystemExit(f"Invalid TOML generated for {config_path}: {exc}") from exc

    if not managed_config_existed_before_install(state) and not updated.strip():
        config_path.unlink()
        return True

    write_text_atomic(config_path, updated.rstrip("\n") + "\n")
    return True


def merge_codex_subagent_settings(target: InstallTarget) -> bool:
    if target.name != "codex":
        return False

    config_path = target.paths.config_file
    if config_path is None:
        raise SystemExit("Install target 'codex' is missing a config file path.")

    toml_module = load_toml_module()
    ensure_path_hierarchy_safe(config_path)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if existing.strip():
        read_toml_object(config_path)

    updated = existing
    changed = False
    for table_name, key_name, rendered_value, _parsed_value in CODEX_REQUIRED_SETTINGS:
        updated, key_changed = _merge_toml_table_key(updated, table_name, key_name, rendered_value)
        changed = changed or key_changed

    if updated.strip():
        try:
            toml_module.loads(updated)
        except toml_module.TOMLDecodeError as exc:
            raise SystemExit(f"Invalid TOML generated for {config_path}: {exc}") from exc

    if not changed:
        return False

    write_text_atomic(config_path, updated.rstrip("\n") + "\n")
    return True


def restore_codex_subagent_settings(target: InstallTarget, state: dict, config: dict) -> bool:
    if target.name != "codex":
        return False

    config_path = target.paths.config_file
    if config_path is None or not config_path.exists():
        return False

    managed_config = _managed_config_state(state)
    if managed_config is None:
        return False
    settings_state = managed_config.get("codex_required_settings")
    if not isinstance(settings_state, dict):
        return False

    toml_module = load_toml_module()
    existing = config_path.read_text(encoding="utf-8")
    updated = existing
    changed = False

    for table_name, key_name, rendered_value, parsed_value in CODEX_REQUIRED_SETTINGS:
        full_key = f"{table_name}.{key_name}"
        setting_state = settings_state.get(full_key)
        if not isinstance(setting_state, dict):
            continue
        present, current_value = _nested_toml_value(config, table_name, key_name)
        if not present or current_value != parsed_value:
            continue

        if setting_state.get("present") is True:
            original_value = setting_state.get("value")
            updated, key_changed = _merge_toml_table_key(updated, table_name, key_name, _render_toml_scalar(original_value))
        else:
            updated, key_changed = _remove_toml_table_key(updated, table_name, key_name)
        changed = changed or key_changed

    if not changed:
        return False

    if updated.strip():
        try:
            toml_module.loads(updated)
        except toml_module.TOMLDecodeError as exc:
            raise SystemExit(f"Invalid TOML generated for {config_path}: {exc}") from exc

    if not managed_config_existed_before_install(state) and not updated.strip():
        config_path.unlink()
        return True

    write_text_atomic(config_path, updated.rstrip("\n") + "\n")
    return True


CLAUDE_TEAMS_ENV_KEY = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"


def merge_claude_teams_env_setting(target: InstallTarget, config: dict, *, enabled: bool) -> bool:
    if not enabled or target.name != "claude":
        return False

    env_config = config.get("env")
    if env_config is None:
        config["env"] = {CLAUDE_TEAMS_ENV_KEY: "1"}
        return True

    if not isinstance(env_config, dict):
        config_path = target.paths.config_file
        if config_path is None:
            raise SystemExit("Claude teams setting requires a JSON object config file.")
        raise SystemExit(f"Expected JSON object at {config_path} for env.")

    if env_config.get(CLAUDE_TEAMS_ENV_KEY) == "1":
        return False

    env_config[CLAUDE_TEAMS_ENV_KEY] = "1"
    return True


def default_state(agent_names: list[str]) -> dict:
    return {
        "version": 1,
        "managed_agents": agent_names.copy(),
        "backups": {},
        "managed": {name: False for name in agent_names},
        "managed_file_metadata": {name: None for name in agent_names},
        "pending_cleanup": {name: False for name in agent_names},
    }


def _remap_state_field_keys(field: object, name_map: dict[str, str]) -> object:
    if not isinstance(field, dict):
        return field
    canonical_keys = set(name_map.values())
    remapped: dict[object, object] = {}
    for key, value in field.items():
        if isinstance(key, str) and key in canonical_keys:
            remapped[key] = value
    for key, value in field.items():
        remapped_key = name_map.get(key, key) if isinstance(key, str) else key
        if remapped_key in remapped:
            continue
        remapped[remapped_key] = value
    return remapped


def migrate_legacy_state(target: InstallTarget, state: dict, agent_names: list[str]) -> dict:
    legacy_names = legacy_agent_name_map(target)
    if not legacy_names:
        return state

    migrated = dict(state)

    managed_agents = migrated.get("managed_agents")
    if isinstance(managed_agents, list):
        normalized_managed_agents = [
            legacy_names.get(name, name) if isinstance(name, str) else name for name in managed_agents
        ]
        if all(isinstance(name, str) for name in normalized_managed_agents) and set(normalized_managed_agents) <= set(
            agent_names
        ):
            migrated["managed_agents"] = [name for name in agent_names if name in normalized_managed_agents]
        else:
            migrated["managed_agents"] = normalized_managed_agents

    for field_name in ("managed", "managed_file_metadata", "pending_cleanup"):
        migrated[field_name] = _remap_state_field_keys(migrated.get(field_name), legacy_names)

    backups = migrated.get("backups")
    if isinstance(backups, dict):
        expected_backups = expected_backup_relative_paths(target, agent_names)
        expected_legacy_backups = expected_legacy_backup_relative_paths(target)
        remapped_backups: dict[object, object] = {}
        for name, backup_relative in backups.items():
            if not isinstance(name, str):
                remapped_backups[name] = backup_relative
                continue
            if name in expected_backups:
                remapped_backups[name] = backup_relative
                continue
            current_name = legacy_names.get(name, name)
            if current_name in remapped_backups:
                continue
            if isinstance(backup_relative, str) and name in expected_legacy_backups and backup_relative == expected_legacy_backups[name]:
                remapped_backups[current_name] = expected_backups[current_name]
                continue
            remapped_backups[current_name] = backup_relative
        migrated["backups"] = {
            name: backup_relative
            for name, backup_relative in remapped_backups.items()
        }

    previous_default = validated_previous_default_agent(migrated)
    if previous_default is not None and previous_default.get("present"):
        value = previous_default.get("value")
        if isinstance(value, str) and value in legacy_default_activation_values(target):
            activation = target.default_activation
            if activation is not None:
                migrated["previous_default_agent"] = {
                    "present": True,
                    "value": activation.managed_value,
                }

    return migrated


def state_managed_agents(target: InstallTarget, state: dict, agent_names: list[str]) -> list[str]:
    managed_agents = state.get("managed_agents")
    if managed_agents == agent_names:
        return agent_names
    raise SystemExit(f"Invalid managed_agents in {target.paths.state_file}.")


def validate_state(target: InstallTarget, state: dict, agent_names: list[str]) -> dict:
    required_fields = (
        "managed_agents",
        "backups",
        "managed",
        "managed_file_metadata",
        "pending_cleanup",
    )
    for field_name in required_fields:
        if field_name not in state:
            raise SystemExit(f"Missing {field_name} in {target.paths.state_file}.")

    managed_agents = state_managed_agents(target, state, agent_names)
    expected_backup_paths = expected_backup_relative_paths(target, agent_names)

    backups = state["backups"]
    if not isinstance(backups, dict):
        raise SystemExit(f"Expected JSON object at {target.paths.state_file} for backups.")

    for name, backup_relative in backups.items():
        if name not in managed_agents:
            raise SystemExit(
                f"Invalid backup entry for unmanaged agent '{name}' in {target.paths.state_file}."
            )
        if backup_relative != expected_backup_paths[name]:
            raise SystemExit(f"Invalid backup path for '{name}' in {target.paths.state_file}.")

    for field_name in ("managed", "pending_cleanup"):
        field_value = state[field_name]
        if not isinstance(field_value, dict):
            raise SystemExit(f"Expected JSON object at {target.paths.state_file} for {field_name}.")
        if set(field_value) != set(managed_agents):
            raise SystemExit(f"Invalid {field_name} in {target.paths.state_file}.")
        for name, managed_value in field_value.items():
            if not isinstance(managed_value, bool):
                raise SystemExit(f"Invalid {field_name} value for '{name}' in {target.paths.state_file}.")

    managed_file_metadata = state["managed_file_metadata"]
    if not isinstance(managed_file_metadata, dict):
        raise SystemExit(
            f"Expected JSON object at {target.paths.state_file} for managed_file_metadata."
        )
    if set(managed_file_metadata) != set(managed_agents):
        raise SystemExit(f"Invalid managed_file_metadata in {target.paths.state_file}.")
    for name, metadata in managed_file_metadata.items():
        if metadata is None:
            continue
        if not isinstance(metadata, dict):
            raise SystemExit(
                f"Invalid managed_file_metadata value for '{name}' in {target.paths.state_file}."
            )
        expected_keys = {"device", "inode", "mtime_ns", "size"}
        if set(metadata) != expected_keys:
            raise SystemExit(
                f"Invalid managed_file_metadata value for '{name}' in {target.paths.state_file}."
            )
        for value in metadata.values():
            if not isinstance(value, int):
                raise SystemExit(
                    f"Invalid managed_file_metadata value for '{name}' in {target.paths.state_file}."
                )

    return state


def state_backup_path(target: InstallTarget, state: dict, name: str) -> Path | None:
    backup_relative = state.get("backups", {}).get(name)
    if not backup_relative:
        return None
    return target.paths.state_dir / backup_relative


def canonical_state_backup_path(target: InstallTarget, name: str) -> Path:
    return backup_path(target, name)


def remove_safe_path(path: Path) -> None:
    ensure_path_hierarchy_safe(path)
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def normalize_install_state_backups(target: InstallTarget, state: dict, agent_names: list[str]) -> dict:
    backups = state.get("backups")
    if not isinstance(backups, dict):
        return state

    expected_backups = expected_backup_relative_paths(target, agent_names)
    normalized_backups: dict[object, object] = dict(backups)
    changed = False
    for name in agent_names:
        canonical_backup = canonical_state_backup_path(target, name)
        if not path_exists(canonical_backup):
            continue
        expected_backup = expected_backups[name]
        if normalized_backups.get(name) == expected_backup:
            continue
        normalized_backups[name] = expected_backup
        changed = True

    if not changed:
        return state

    normalized_state = dict(state)
    normalized_state["backups"] = normalized_backups
    return normalized_state


def read_bytes(path: Path) -> bytes | None:
    if not path_exists(path):
        return None
    return path.read_bytes()


def path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def ensure_not_symlink(path: Path) -> None:
    if path.is_symlink():
        raise SystemExit(f"Refusing to manage symlinked path: {path}")


def ensure_path_hierarchy_safe(path: Path) -> None:
    for candidate in (path, *path.parents):
        ensure_not_symlink(candidate)
        if candidate == Path.home():
            break


def has_live_backup(target: InstallTarget, state: dict, name: str, *, ignore_existing_backups: bool = False) -> bool:
    if ignore_existing_backups:
        return False
    backup = state_backup_path(target, state, name)
    if backup is not None and path_exists(backup):
        return True
    return path_exists(canonical_state_backup_path(target, name))


def is_managed_in_place(state: dict, name: str) -> bool:
    return bool(state["managed"].get(name, False))


def file_metadata(path: Path) -> dict[str, int] | None:
    if not path_exists(path):
        return None
    stats = path.stat()
    return {
        "device": stats.st_dev,
        "inode": stats.st_ino,
        "mtime_ns": stats.st_mtime_ns,
        "size": stats.st_size,
    }


def has_matching_managed_file_metadata(state: dict, name: str, destination: Path) -> bool:
    managed_file_metadata = state.get("managed_file_metadata")
    if not isinstance(managed_file_metadata, dict):
        return False
    return managed_file_metadata.get(name) == file_metadata(destination)


def is_safe_managed_install_target_without_backup_conflict(
    target: InstallTarget,
    state: dict,
    name: str,
    current_bytes: bytes | None,
    desired_bytes: bytes,
    force: bool = False,
) -> bool:
    if force:
        return True
    if not is_managed_in_place(state, name):
        return False
    if state["pending_cleanup"][name]:
        return False
    if has_matching_managed_file_metadata(state, name, installed_agent_path(target, name)):
        return True
    return current_bytes == desired_bytes


def is_safe_managed_install_target(
    target: InstallTarget,
    state: dict,
    name: str,
    current_bytes: bytes | None,
    desired_bytes: bytes,
    force: bool = False,
    ignore_existing_backups: bool = False,
) -> bool:
    if force:
        return True
    if has_live_backup(target, state, name, ignore_existing_backups=ignore_existing_backups):
        return has_matching_managed_file_metadata(state, name, installed_agent_path(target, name))
    return is_safe_managed_install_target_without_backup_conflict(
        target, state, name, current_bytes, desired_bytes, force
    )


def is_ambiguous_interrupted_install_retry(
    target: InstallTarget,
    state: dict,
    name: str,
    current_bytes: bytes | None,
    desired_bytes: bytes,
    force: bool = False,
) -> bool:
    if force:
        return False
    if current_bytes != desired_bytes:
        if has_matching_managed_file_metadata(state, name, installed_agent_path(target, name)):
            return False
        return False
    if has_live_backup(target, state, name):
        return False
    if not is_managed_in_place(state, name):
        return False
    if state["pending_cleanup"][name]:
        return False
    return not has_matching_managed_file_metadata(state, name, installed_agent_path(target, name))


def reconcile_recoverable_install_state(
    target: InstallTarget,
    state: dict,
    name: str,
    current_bytes: bytes | None,
    desired_bytes: bytes,
    *,
    ignore_existing_backups: bool = False,
) -> None:
    if ignore_existing_backups:
        state.get("backups", {}).pop(name, None)
        return

    backup = canonical_state_backup_path(target, name)
    expected_backup_relative = str(backup.relative_to(target.paths.state_dir))

    if state.get("backups", {}).get(name) != expected_backup_relative and path_exists(backup):
        state["backups"][name] = expected_backup_relative
        return

    backup = state_backup_path(target, state, name)
    if backup is None:
        if path_exists(canonical_state_backup_path(target, name)):
            state["backups"][name] = expected_backup_relative
        return

    if path_exists(backup):
        if state["backups"].get(name) != expected_backup_relative:
            state["backups"][name] = expected_backup_relative
        return

    if current_bytes is None or current_bytes == desired_bytes:
        state["backups"].pop(name, None)
        return

    ensure_path_hierarchy_safe(backup)
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(current_bytes)
    state["backups"][name] = str(backup.relative_to(target.paths.state_dir))


def write_state(target: InstallTarget, state: dict) -> None:
    write_json(target.paths.state_file, state)


INVALID_INSTALL_STATE_RECOVERY_KEY = "_invalid_install_state_recovery"


def cleanup_invalid_install_backups(target: InstallTarget, state: dict) -> None:
    if not target.paths.backup_dir.exists():
        return

    ensure_path_hierarchy_safe(target.paths.backup_dir)
    tracked_backups = {
        backup_relative
        for backup_relative in state.get("backups", {}).values()
        if isinstance(backup_relative, str)
    }
    for backup_entry in tuple(target.paths.backup_dir.iterdir()):
        if str(backup_entry.relative_to(target.paths.state_dir)) in tracked_backups:
            continue
        remove_safe_path(backup_entry)
    if not any(target.paths.backup_dir.iterdir()):
        target.paths.backup_dir.rmdir()


CODEX_MANAGED_AGENTS_START = "<!-- 480ai managed codex agents start -->"
CODEX_MANAGED_AGENTS_END = "<!-- 480ai managed codex agents end -->"
CODEX_GUIDANCE_STATE_KEY = "codex_guidance"


def codex_guidance_path(target: InstallTarget) -> Path | None:
    if target.name != "codex":
        return None
    if target.scope == "user":
        return target.paths.config_dir / "AGENTS.md"
    return target.paths.config_dir.parent / "AGENTS.md"


def strip_codex_managed_guidance_block(contents: str) -> str:
    return strip_codex_managed_guidance_block_with_install_padding(
        contents,
        added_before_block=0,
        added_after_block=0,
    )


def codex_managed_guidance_block_span(contents: str) -> tuple[int, int] | None:
    start = contents.find(CODEX_MANAGED_AGENTS_START)
    if start < 0:
        return None
    end = contents.find(CODEX_MANAGED_AGENTS_END, start)
    if end < 0:
        return None
    return start, end + len(CODEX_MANAGED_AGENTS_END)


def count_trailing_newlines(contents: str) -> int:
    return len(contents) - len(contents.rstrip("\n"))


def count_leading_newlines(contents: str) -> int:
    return len(contents) - len(contents.lstrip("\n"))


def join_codex_guidance_sections(before: str, after: str, *, min_newlines: int = 2) -> str:
    if not before or not after:
        return before + after
    existing_newlines = count_trailing_newlines(before) + count_leading_newlines(after)
    if existing_newlines >= min_newlines:
        return before + after
    return before + ("\n" * (min_newlines - existing_newlines)) + after


def codex_join_padding_added(before: str, after: str, *, min_newlines: int = 2) -> int:
    if not before or not after:
        return 0
    existing_newlines = count_trailing_newlines(before) + count_leading_newlines(after)
    return max(0, min_newlines - existing_newlines)


def set_codex_guidance_spacing_state(
    state: dict,
    *,
    added_before_block: int,
    added_after_block: int,
) -> None:
    guidance_state = state.setdefault(CODEX_GUIDANCE_STATE_KEY, {})
    if not isinstance(guidance_state, dict):
        guidance_state = {}
        state[CODEX_GUIDANCE_STATE_KEY] = guidance_state
    guidance_state["added_before_block"] = added_before_block
    guidance_state["added_after_block"] = added_after_block


def codex_guidance_spacing_state(state: dict) -> tuple[int, int]:
    guidance_state = state.get(CODEX_GUIDANCE_STATE_KEY)
    if not isinstance(guidance_state, dict):
        return 0, 0

    added_before_block = guidance_state.get("added_before_block")
    added_after_block = guidance_state.get("added_after_block")
    return (
        added_before_block if isinstance(added_before_block, int) and added_before_block >= 0 else 0,
        added_after_block if isinstance(added_after_block, int) and added_after_block >= 0 else 0,
    )


def strip_codex_managed_guidance_block_with_install_padding(
    contents: str,
    *,
    added_before_block: int,
    added_after_block: int,
) -> str:
    block_span = codex_managed_guidance_block_span(contents)
    if block_span is None:
        return contents

    start, end = block_span
    before = contents[:start]
    after = contents[end:]
    if not before:
        leading_after = count_leading_newlines(after)
        return after[min(leading_after, 1 + added_after_block) :]

    if not after:
        return before + after

    trailing_before = max(0, count_trailing_newlines(before) - added_before_block)
    leading_after = max(0, count_leading_newlines(after) - added_after_block)
    user_newlines = max(trailing_before, leading_after)
    return before.rstrip("\n") + ("\n" * user_newlines) + after.lstrip("\n")


def set_codex_guidance_state_on_install(target: InstallTarget, state: dict, guidance_path: Path) -> None:
    if target.name != "codex" or CODEX_GUIDANCE_STATE_KEY in state:
        return
    state[CODEX_GUIDANCE_STATE_KEY] = {
        "existed_before_install": guidance_path.exists(),
    }


def codex_guidance_existed_before_install(state: dict) -> bool:
    guidance_state = state.get(CODEX_GUIDANCE_STATE_KEY)
    if not isinstance(guidance_state, dict):
        return False
    return guidance_state.get("existed_before_install") is True


def write_codex_guidance_contents(guidance_path: Path, contents: str) -> None:
    if not contents:
        guidance_path.write_text("", encoding="utf-8")
        return
    guidance_path.write_text(contents.rstrip("\n") + "\n", encoding="utf-8")


def sync_codex_managed_guidance_install(target: InstallTarget, state: dict, managed_body: str | None) -> None:
    guidance_path = codex_guidance_path(target)
    if guidance_path is None:
        return

    ensure_path_hierarchy_safe(guidance_path)
    if managed_body is None:
        raise SystemExit("Missing Codex managed guidance source.")
    managed_body = managed_body.rstrip("\n")
    managed_block = "\n".join(
        [
            CODEX_MANAGED_AGENTS_START,
            managed_body,
            CODEX_MANAGED_AGENTS_END,
        ]
    )

    existing = guidance_path.read_text(encoding="utf-8") if guidance_path.exists() else ""
    block_span = codex_managed_guidance_block_span(existing)
    if block_span is None:
        added_before_block = codex_join_padding_added(existing, managed_block)
        added_after_block = 0
        contents = join_codex_guidance_sections(existing, managed_block)
    else:
        start, end = block_span
        before = existing[:start]
        after = existing[end:]
        added_before_block = codex_join_padding_added(before, managed_block)
        added_after_block = codex_join_padding_added(managed_block, after)
        contents = join_codex_guidance_sections(
            join_codex_guidance_sections(before, managed_block),
            after,
        )

    set_codex_guidance_spacing_state(
        state,
        added_before_block=added_before_block,
        added_after_block=added_after_block,
    )
    guidance_path.parent.mkdir(parents=True, exist_ok=True)
    write_codex_guidance_contents(guidance_path, contents)


def sync_codex_managed_guidance_uninstall(target: InstallTarget, state: dict) -> None:
    guidance_path = codex_guidance_path(target)
    if guidance_path is None or not guidance_path.exists():
        return

    ensure_path_hierarchy_safe(guidance_path)
    added_before_block, added_after_block = codex_guidance_spacing_state(state)
    preserved = strip_codex_managed_guidance_block_with_install_padding(
        guidance_path.read_text(encoding="utf-8"),
        added_before_block=added_before_block,
        added_after_block=added_after_block,
    )
    if preserved.strip("\n"):
        write_codex_guidance_contents(guidance_path, preserved)
        return

    if codex_guidance_existed_before_install(state):
        write_codex_guidance_contents(guidance_path, "")
        return

    guidance_path.unlink()


def load_state(target: InstallTarget, agent_names: list[str], *, recover_invalid_for_install: bool = False) -> dict:
    if not target.paths.state_file.exists():
        return default_state(agent_names)

    try:
        raw_state = read_json_object(target.paths.state_file)
    except SystemExit:
        if not recover_invalid_for_install:
            raise
        recovered_state = default_state(agent_names)
        recovered_state[INVALID_INSTALL_STATE_RECOVERY_KEY] = True
        return recovered_state

    state = migrate_legacy_state(target, raw_state, agent_names)
    state = normalize_install_state_backups(target, state, agent_names)

    try:
        validate_state(target, state, agent_names)
        managed_agents = state_managed_agents(target, state, agent_names)
    except SystemExit:
        if not recover_invalid_for_install:
            raise
        recovered_state = default_state(agent_names)
        recovered_state[INVALID_INSTALL_STATE_RECOVERY_KEY] = True
        return recovered_state

    state.setdefault("version", 1)
    state["managed_agents"] = managed_agents.copy()
    state["backups"] = dict(state["backups"])
    state["managed"] = dict(state["managed"])
    state["managed_file_metadata"] = dict(state["managed_file_metadata"])
    state["pending_cleanup"] = dict(state["pending_cleanup"])
    return state


def migrate_legacy_paths_and_activation(target: InstallTarget, state: dict, config: dict) -> bool:
    legacy_names = legacy_agent_name_map(target)
    if not legacy_names:
        return False

    changed = False
    for legacy_name, current_name in legacy_names.items():
        legacy_backup = backup_path(target, legacy_name)
        current_backup = backup_path(target, current_name)
        if path_exists(legacy_backup):
            ensure_not_symlink(legacy_backup)
            ensure_not_symlink(current_backup)
            legacy_backup_bytes = read_bytes(legacy_backup)
            current_backup_bytes = read_bytes(current_backup)
            if path_exists(current_backup):
                if legacy_backup_bytes == current_backup_bytes:
                    legacy_backup.unlink()
                    state["backups"][current_name] = str(current_backup.relative_to(target.paths.state_dir))
                    changed = True
                else:
                    raise SystemExit(
                        f"Conflicting legacy Claude backup files for {current_name}; resolve {legacy_backup} manually."
                    )
            else:
                current_backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(legacy_backup, current_backup)
                state["backups"][current_name] = str(current_backup.relative_to(target.paths.state_dir))
                changed = True

        legacy_installed = installed_agent_path(target, legacy_name)
        current_installed = installed_agent_path(target, current_name)
        if path_exists(legacy_installed):
            ensure_not_symlink(legacy_installed)
            ensure_not_symlink(current_installed)
            legacy_bytes = read_bytes(legacy_installed)
            current_bytes = read_bytes(current_installed)
            if path_exists(current_installed):
                if legacy_bytes == current_bytes:
                    legacy_installed.unlink()
                    changed = True
                else:
                    ensure_not_symlink(current_backup)
                    backup_bytes = read_bytes(current_backup)
                    if backup_bytes is None:
                        current_backup.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(legacy_installed, current_backup)
                        state["backups"][current_name] = str(current_backup.relative_to(target.paths.state_dir))
                        changed = True
                    elif backup_bytes == legacy_bytes:
                        legacy_installed.unlink()
                        state["backups"][current_name] = str(current_backup.relative_to(target.paths.state_dir))
                        changed = True
                    else:
                        raise SystemExit(
                            f"Conflicting legacy Claude install files for {current_name}; resolve {legacy_installed} manually."
                        )
            else:
                current_installed.parent.mkdir(parents=True, exist_ok=True)
                os.replace(legacy_installed, current_installed)
                changed = True

    activation = target.default_activation
    if activation is None:
        return changed

    legacy_values = legacy_default_activation_values(target)
    config_key = activation.config_key
    if config.get(config_key) in legacy_values:
        if config.get(config_key) != activation.managed_value:
            config[config_key] = activation.managed_value
            changed = True

    previous_default = validated_previous_default_agent(state)
    if previous_default is not None and previous_default.get("present"):
        value = previous_default.get("value")
        if isinstance(value, str) and value in legacy_values and value != activation.managed_value:
            state["previous_default_agent"] = {
                "present": True,
                "value": activation.managed_value,
            }
            changed = True

    return changed


def expected_managed_bytes(source_agents_dir: Path, target: InstallTarget, name: str) -> bytes:
    return source_agent_path(source_agents_dir, target, name).read_bytes()


def should_remove_without_backup(
    source_agents_dir: Path,
    target: InstallTarget,
    name: str,
    current_bytes: bytes | None,
) -> bool:
    if current_bytes is None:
        return False
    return current_bytes == expected_managed_bytes(source_agents_dir, target, name)


def validated_previous_default_agent(state: dict) -> dict[str, object] | None:
    if "previous_default_agent" not in state:
        return {"present": False, "value": None}
    previous_default = state.get("previous_default_agent")
    if not isinstance(previous_default, dict):
        return None

    present = previous_default.get("present")
    if not isinstance(present, bool):
        return None
    if not present:
        return {"present": False, "value": None}

    value = previous_default.get("value")
    if not isinstance(value, str):
        return None
    return {"present": True, "value": value}


def default_activation_enabled(state: dict) -> bool:
    value = state.get("default_activation_enabled")
    if isinstance(value, bool):
        return value
    return "previous_default_agent" in state


def ensure_source_agents_exist(target: InstallTarget, source_agents_dir: Path, agent_names: list[str]) -> None:
    missing = [name for name in agent_names if not source_agent_path(source_agents_dir, target, name).exists()]
    if missing:
        raise SystemExit(f"Missing repo-managed agent files: {', '.join(missing)}")


def cleanup_codex_legacy_agents(target: InstallTarget) -> None:
    if target.name != "codex":
        return
    for legacy_name in ("480-architect", "480"):
        legacy_path = installed_agent_path(target, legacy_name)
        ensure_path_hierarchy_safe(legacy_path)
        ensure_not_symlink(legacy_path)
        if path_exists(legacy_path):
            legacy_path.unlink()
        legacy_backup = backup_path(target, legacy_name)
        if path_exists(legacy_backup):
            ensure_path_hierarchy_safe(legacy_backup)
            ensure_not_symlink(legacy_backup)
            legacy_backup.unlink()


def install(
    target: InstallTarget,
    source_agents_dir: Path,
    agent_names: list[str],
    force: bool = False,
    manage_default_activation: bool = True,
    state_updates: dict[str, object] | None = None,
    enable_claude_teams: bool = False,
    enable_desktop_notifications: bool | None = None,
    codex_managed_guidance: str | None = None,
) -> None:
    ensure_source_agents_exist(target, source_agents_dir, agent_names)
    ensure_path_hierarchy_safe(target.paths.installed_agents_dir)
    ensure_path_hierarchy_safe(target.paths.backup_dir)
    ensure_path_hierarchy_safe(target.paths.state_file)
    config = read_target_config(target)
    target.paths.installed_agents_dir.mkdir(parents=True, exist_ok=True)
    target.paths.backup_dir.mkdir(parents=True, exist_ok=True)
    cleanup_codex_legacy_agents(target)

    state = load_state(target, agent_names, recover_invalid_for_install=True)
    should_cleanup_invalid_install_state = bool(state.pop(INVALID_INSTALL_STATE_RECOVERY_KEY, False))
    if state_updates:
        state.update(state_updates)
    capture_managed_config_state_on_install(target, state, config)
    if enable_desktop_notifications is True:
        install_desktop_notification_assets(target, state)
    guidance_path = codex_guidance_path(target)
    if guidance_path is not None:
        set_codex_guidance_state_on_install(target, state, guidance_path)
    if migrate_legacy_paths_and_activation(target, state, config):
        write_target_config(target, config)
        if not should_cleanup_invalid_install_state:
            write_state(target, state)

    for name in agent_names:
        source = source_agent_path(source_agents_dir, target, name)
        destination = installed_agent_path(target, name)
        ensure_not_symlink(destination)
        desired_bytes = source.read_bytes()
        current_bytes = read_bytes(destination)
        reconcile_recoverable_install_state(
            target,
            state,
            name,
            current_bytes,
            desired_bytes,
            ignore_existing_backups=should_cleanup_invalid_install_state,
        )

        if current_bytes is not None and (
            not is_safe_managed_install_target(
                target,
                state,
                name,
                current_bytes,
                desired_bytes,
                force,
                ignore_existing_backups=should_cleanup_invalid_install_state,
            )
            or (
                name in state["backups"]
                and not has_live_backup(
                    target,
                    state,
                    name,
                    ignore_existing_backups=should_cleanup_invalid_install_state,
                )
            )
        ):
            backup = backup_path(target, name)
            ensure_path_hierarchy_safe(backup)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not (
                state["pending_cleanup"][name]
                and current_bytes == desired_bytes
                and not has_live_backup(
                    target,
                    state,
                    name,
                    ignore_existing_backups=should_cleanup_invalid_install_state,
                )
            ):
                if not has_live_backup(
                    target,
                    state,
                    name,
                    ignore_existing_backups=should_cleanup_invalid_install_state,
                ):
                    backup.write_bytes(current_bytes)
                    state["backups"][name] = str(backup.relative_to(target.paths.state_dir))

    activation = target_default_activation_state(target)
    if activation is not None:
        if manage_default_activation:
            if "previous_default_agent" not in state:
                config_key, _managed_value = activation
                state["previous_default_agent"] = {
                    "present": config_key in config,
                    "value": config.get(config_key),
                }
            state["default_activation_enabled"] = True
        else:
            if "previous_default_agent" not in state:
                state["default_activation_enabled"] = False

    for name in agent_names:
        state["managed"][name] = True
        state["managed_file_metadata"][name] = None
        state["pending_cleanup"][name] = False

    if not should_cleanup_invalid_install_state:
        write_state(target, state)

    for name in agent_names:
        source = source_agent_path(source_agents_dir, target, name)
        destination = installed_agent_path(target, name)
        ensure_not_symlink(destination)
        desired_bytes = source.read_bytes()
        current_bytes = read_bytes(destination)

        if current_bytes != desired_bytes:
            shutil.copy2(source, destination)
        state["managed_file_metadata"][name] = file_metadata(destination)
    config_changed = merge_claude_teams_env_setting(target, config, enabled=enable_claude_teams)
    if activation is not None and manage_default_activation:
        config_key, managed_value = activation
        config[config_key] = managed_value
        config_changed = True

    if config_changed:
        write_target_config(target, config)

    merge_codex_subagent_settings(target)
    desktop_notification_changed = False
    if enable_desktop_notifications is True and target.name == "codex":
        desktop_notification_changed = merge_codex_desktop_notification_hook(target, state, config, enabled=True)
    if enable_desktop_notifications is True and target.name == "claude":
        desktop_notification_changed = merge_claude_desktop_notification_hook(
            target,
            state,
            config,
            command=f"{shlex.quote(str(desktop_notification_hook_path(target)))} claude",
            enabled=True,
        )
        if desktop_notification_changed:
            write_target_config(target, config)
    if enable_desktop_notifications is False:
        if target.name == "codex":
            desktop_notification_changed = restore_codex_desktop_notification_hook(target, state, config)
        elif target.name == "claude":
            desktop_notification_changed = restore_claude_desktop_notification_hook(
                target,
                state,
                config,
                command=f"{shlex.quote(str(desktop_notification_hook_path(target)))} claude",
            )
            if desktop_notification_changed:
                write_target_config(target, config)
        if restore_desktop_notification_assets(target, state):
            desktop_notification_changed = True

    sync_codex_managed_guidance_install(target, state, codex_managed_guidance)

    write_state(target, state)
    if should_cleanup_invalid_install_state:
        cleanup_invalid_install_backups(target, state)
    print(f"Installed 480ai {target.label} agents.")


def uninstall(target: InstallTarget, source_agents_dir: Path, agent_names: list[str]) -> None:
    ensure_source_agents_exist(target, source_agents_dir, agent_names)
    cleanup_codex_legacy_agents(target)
    if not target.paths.state_file.exists():
        print("No 480ai install state found. Nothing to uninstall.")
        return

    config = read_target_config(target)

    ensure_path_hierarchy_safe(target.paths.installed_agents_dir)
    ensure_path_hierarchy_safe(target.paths.backup_dir)
    ensure_path_hierarchy_safe(target.paths.state_file)
    state = load_state(target, agent_names)
    if migrate_legacy_paths_and_activation(target, state, config):
        write_target_config(target, config)
        write_state(target, state)

    if target.name == "codex":
        restore_codex_desktop_notification_hook(target, state, config)
    elif target.name == "claude":
        claude_desktop_changed = restore_claude_desktop_notification_hook(
            target,
            state,
            config,
            command=f"{shlex.quote(str(desktop_notification_hook_path(target)))} claude",
        )
        if claude_desktop_changed:
            write_target_config(target, config)

    for name in state.get("managed_agents", agent_names):
        destination = installed_agent_path(target, name)
        ensure_not_symlink(destination)
        if path_exists(destination):
            destination.unlink()

        tracked_backup = state_backup_path(target, state, name)
        canonical_backup = canonical_state_backup_path(target, name)
        for backup in (tracked_backup, canonical_backup):
            if backup is None or not path_exists(backup):
                continue
            ensure_path_hierarchy_safe(backup)
            backup.unlink()

        state["managed"][name] = False
        state["managed_file_metadata"][name] = None
        state["pending_cleanup"][name] = False
        state["backups"].pop(name, None)

    activation = target_default_activation_state(target)
    previous_default = validated_previous_default_agent(state)
    if activation is not None and default_activation_enabled(state):
        config_key, managed_value = activation
        if config.get(config_key) in legacy_default_activation_values(target) and previous_default is not None:
            if previous_default.get("present"):
                config[config_key] = previous_default.get("value")
            else:
                config.pop(config_key, None)
            if not maybe_remove_empty_managed_config(target, state, config):
                write_target_config(target, config)

    restore_codex_subagent_settings(target, state, config)

    sync_codex_managed_guidance_uninstall(target, state)
    restore_desktop_notification_assets(target, state)

    write_state(target, state)

    if target.paths.installed_agents_dir.exists() and not any(target.paths.installed_agents_dir.iterdir()):
        target.paths.installed_agents_dir.rmdir()

    if target.paths.backup_dir.exists():
        ensure_path_hierarchy_safe(target.paths.backup_dir)
        for backup_entry in tuple(target.paths.backup_dir.iterdir()):
            remove_safe_path(backup_entry)
        if not any(target.paths.backup_dir.iterdir()):
            target.paths.backup_dir.rmdir()

    if target.paths.state_file.exists():
        target.paths.state_file.unlink()
    if target.paths.state_dir.exists() and not any(target.paths.state_dir.iterdir()):
        target.paths.state_dir.rmdir()

    print(f"Uninstalled 480ai {target.label} agents.")
