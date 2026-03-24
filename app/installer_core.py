from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from importlib import import_module
from pathlib import Path

if __package__:
    from .install_targets import InstallTarget
else:  # pragma: no cover
    from install_targets import InstallTarget


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
)


def _json_scalar(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
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
                    "value": _json_scalar(table[key_name]),
                }
            else:
                settings[full_key] = {
                    "present": False,
                    "value": None,
                }
        managed_config["codex_required_settings"] = settings

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


def _remove_toml_key_assignment(contents: str, key_path: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key_path)}\s*=\s*[^#\n]*(?:\s*#.*)?$\n?")
    match = pattern.search(contents)
    if match is None:
        return contents, False
    return contents[: match.start()] + contents[match.end() :], True


def _table_header_pattern(table_name: str) -> re.Pattern[str]:
    return re.compile(rf"(?m)^\[{re.escape(table_name)}\]\s*(?:#.*)?$")


def _merge_toml_table_key(contents: str, table_name: str, key_name: str, rendered_value: str) -> tuple[str, bool]:
    dotted_key = f"{table_name}.{key_name}"
    updated, changed = _replace_toml_key_assignment(contents, dotted_key, rendered_value)
    if changed:
        return updated, True

    table_match = _table_header_pattern(table_name).search(contents)
    if table_match is None:
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
