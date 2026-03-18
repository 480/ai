#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import shutil
import tempfile
import sys
from pathlib import Path


AGENT_NAMES = [
    "architect",
    "developer",
    "code-reviewer",
    "code-reviewer2",
    "code-scanner",
]
REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_AGENTS_DIR = REPO_ROOT / "agents"
CONFIG_DIR = Path.home() / ".config" / "opencode"
INSTALLED_AGENTS_DIR = CONFIG_DIR / "agents"
STATE_DIR = CONFIG_DIR / ".480ai-bootstrap"
BACKUP_DIR = STATE_DIR / "backups"
STATE_FILE = STATE_DIR / "state.json"
EXPECTED_BACKUP_RELATIVE_PATHS = {
    name: str((BACKUP_DIR / f"{name}.md").relative_to(STATE_DIR)) for name in AGENT_NAMES
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


def source_agent_path(name: str) -> Path:
    return SOURCE_AGENTS_DIR / f"{name}.md"


def installed_agent_path(name: str) -> Path:
    return INSTALLED_AGENTS_DIR / f"{name}.md"


def backup_path(name: str) -> Path:
    return BACKUP_DIR / f"{name}.md"


def default_state() -> dict:
    return {
        "version": 1,
        "managed_agents": AGENT_NAMES.copy(),
        "backups": {},
        "managed": {},
        "managed_file_metadata": {},
        "pending_cleanup": {},
    }


def validate_state(state: dict) -> dict:
    required_fields = (
        "managed_agents",
        "backups",
        "managed",
        "managed_file_metadata",
    )
    for field_name in required_fields:
        if field_name not in state:
            raise SystemExit(f"Missing {field_name} in {STATE_FILE}.")

    managed_agents = state["managed_agents"]
    if managed_agents != AGENT_NAMES:
        raise SystemExit(f"Invalid managed_agents in {STATE_FILE}.")

    backups = state["backups"]
    if not isinstance(backups, dict):
        raise SystemExit(f"Expected JSON object at {STATE_FILE} for backups.")

    for name, backup_relative in backups.items():
        if name not in AGENT_NAMES:
            raise SystemExit(f"Invalid backup entry for unmanaged agent '{name}' in {STATE_FILE}.")
        if backup_relative != EXPECTED_BACKUP_RELATIVE_PATHS[name]:
            raise SystemExit(f"Invalid backup path for '{name}' in {STATE_FILE}.")

    for field_name in ("managed", "pending_cleanup"):
        if field_name not in state:
            continue
        field_value = state[field_name]
        if not isinstance(field_value, dict):
            raise SystemExit(f"Expected JSON object at {STATE_FILE} for {field_name}.")
        if set(field_value) != set(AGENT_NAMES):
            raise SystemExit(f"Invalid {field_name} in {STATE_FILE}.")
        for name, managed_value in field_value.items():
            if not isinstance(managed_value, bool):
                raise SystemExit(f"Invalid {field_name} value for '{name}' in {STATE_FILE}.")

    managed_file_metadata = state["managed_file_metadata"]
    if not isinstance(managed_file_metadata, dict):
        raise SystemExit(f"Expected JSON object at {STATE_FILE} for managed_file_metadata.")
    if set(managed_file_metadata) != set(AGENT_NAMES):
        raise SystemExit(f"Invalid managed_file_metadata in {STATE_FILE}.")
    for name, metadata in managed_file_metadata.items():
        if metadata is None:
            continue
        if not isinstance(metadata, dict):
            raise SystemExit(f"Invalid managed_file_metadata value for '{name}' in {STATE_FILE}.")
        expected_keys = {"device", "inode", "mtime_ns", "size"}
        if set(metadata) != expected_keys:
            raise SystemExit(f"Invalid managed_file_metadata value for '{name}' in {STATE_FILE}.")
        for key, value in metadata.items():
            if not isinstance(value, int):
                raise SystemExit(f"Invalid managed_file_metadata value for '{name}' in {STATE_FILE}.")

    return state


def state_backup_path(state: dict, name: str) -> Path | None:
    backup_relative = state.get("backups", {}).get(name)
    if not backup_relative:
        return None
    return STATE_DIR / backup_relative


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


def has_live_backup(state: dict, name: str) -> bool:
    backup = state_backup_path(state, name)
    if backup is None:
        return False
    return path_exists(backup)


def is_managed_in_place(state: dict, name: str) -> bool:
    managed = state.get("managed")
    if isinstance(managed, dict):
        return bool(managed.get(name, False))
    return True


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
    state: dict, name: str, current_bytes: bytes | None, desired_bytes: bytes, force: bool = False
) -> bool:
    if force:
        return True
    if not is_managed_in_place(state, name):
        return False
    if state.get("pending_cleanup", {}).get(name):
        return False
    # If the file matches our last known metadata, it's safe to update even if bytes differ
    if has_matching_managed_file_metadata(state, name, installed_agent_path(name)):
        return True
    return current_bytes == desired_bytes


def is_safe_managed_install_target(
    state: dict, name: str, current_bytes: bytes | None, desired_bytes: bytes, force: bool = False
) -> bool:
    if force:
        return True
    if has_live_backup(state, name):
        # If we have a backup, we only overwrite if the file is still "our" managed file
        return has_matching_managed_file_metadata(state, name, installed_agent_path(name))
    return is_safe_managed_install_target_without_backup_conflict(state, name, current_bytes, desired_bytes, force)


def is_ambiguous_interrupted_install_retry(state: dict, name: str, current_bytes: bytes | None, desired_bytes: bytes, force: bool = False) -> bool:
    if force:
        return False
    if current_bytes != desired_bytes:
        # If it's a managed update, it's not an ambiguous interruption
        if has_matching_managed_file_metadata(state, name, installed_agent_path(name)):
            return False
        return False
    if has_live_backup(state, name):
        return False
    if not is_managed_in_place(state, name):
        return False
    if state.get("pending_cleanup", {}).get(name):
        return False
    return not has_matching_managed_file_metadata(state, name, installed_agent_path(name))


def write_state(state: dict) -> None:
    write_json(STATE_FILE, state)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return default_state()
    state = read_json_object(STATE_FILE)
    validate_state(state)
    state.setdefault("version", 1)
    state["managed_agents"] = AGENT_NAMES.copy()
    state["backups"] = dict(state["backups"])
    state["managed"] = dict(state["managed"])
    state["managed_file_metadata"] = dict(state["managed_file_metadata"])
    state["pending_cleanup"] = dict(state.get("pending_cleanup", {}))
    return state


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


def ensure_source_agents_exist() -> None:
    missing = [name for name in AGENT_NAMES if not source_agent_path(name).exists()]
    if missing:
        raise SystemExit(f"Missing repo-managed agent files: {', '.join(missing)}")


def install(force: bool = False) -> None:
    ensure_source_agents_exist()
    config_path = CONFIG_DIR / "opencode.json"
    ensure_path_hierarchy_safe(config_path)
    ensure_path_hierarchy_safe(INSTALLED_AGENTS_DIR)
    ensure_path_hierarchy_safe(BACKUP_DIR)
    ensure_path_hierarchy_safe(STATE_FILE)
    config = read_json_object(config_path)
    INSTALLED_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()

    for name in AGENT_NAMES:
        source = source_agent_path(name)
        destination = installed_agent_path(name)
        ensure_not_symlink(destination)
        desired_bytes = source.read_bytes()
        current_bytes = read_bytes(destination)
        live_backup = state_backup_path(state, name)
        backup_bytes = read_bytes(live_backup) if live_backup is not None else None

        if is_ambiguous_interrupted_install_retry(state, name, current_bytes, desired_bytes, force):
            raise SystemExit(
                f"Refusing to continue install for {destination}; prior install state is incomplete or corrupted. "
                "Resolve manually or uninstall before retrying."
            )

        if not force and current_bytes is not None and has_live_backup(state, name) and \
           not has_matching_managed_file_metadata(state, name, destination) and current_bytes != desired_bytes:
            raise SystemExit(f"Refusing to overwrite {destination}; live file was modified and backup exists.")

        if current_bytes is not None and (
            not is_safe_managed_install_target(state, name, current_bytes, desired_bytes, force)
            or (name in state["backups"] and not has_live_backup(state, name))
        ):
            if (
                not force
                and live_backup is not None
                and current_bytes != desired_bytes
                and current_bytes != backup_bytes
                and not has_matching_managed_file_metadata(state, name, destination)
            ):
                raise SystemExit(
                    f"Refusing to overwrite {destination}; uninstall state still tracks a different backup."
                )
            backup = backup_path(name)
            ensure_path_hierarchy_safe(backup)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not (
                state.get("pending_cleanup", {}).get(name)
                and current_bytes == desired_bytes
                and not has_live_backup(state, name)
            ):
                if not has_live_backup(state, name):
                    backup.write_bytes(current_bytes)
                    state["backups"][name] = str(backup.relative_to(STATE_DIR))

    if "previous_default_agent" not in state:
        state["previous_default_agent"] = {
            "present": "default_agent" in config,
            "value": config.get("default_agent"),
        }

    for name in AGENT_NAMES:
        state["managed"][name] = True
        state["managed_file_metadata"][name] = None
        state["pending_cleanup"][name] = False

    write_state(state)

    for name in AGENT_NAMES:
        source = source_agent_path(name)
        destination = installed_agent_path(name)
        ensure_not_symlink(destination)
        desired_bytes = source.read_bytes()
        current_bytes = read_bytes(destination)

        if current_bytes != desired_bytes:
            shutil.copy2(source, destination)
        state["managed_file_metadata"][name] = file_metadata(destination)
    config["default_agent"] = "architect"
    write_json(config_path, config)

    write_state(state)
    print("Installed 480ai OpenCode agents.")


def uninstall() -> None:
    ensure_source_agents_exist()
    if not STATE_FILE.exists():
        print("No 480ai install state found. Nothing to uninstall.")
        return

    config_path = CONFIG_DIR / "opencode.json"
    ensure_path_hierarchy_safe(config_path)
    config = read_json_object(config_path)

    ensure_path_hierarchy_safe(INSTALLED_AGENTS_DIR)
    ensure_path_hierarchy_safe(BACKUP_DIR)
    ensure_path_hierarchy_safe(STATE_FILE)
    state = load_state()

    for name in state.get("managed_agents", AGENT_NAMES):
        source = source_agent_path(name)
        destination = installed_agent_path(name)
        ensure_not_symlink(destination)
        expected_bytes = source.read_bytes()
        current_bytes = read_bytes(destination)
        backup_relative = state.get("backups", {}).get(name)

        if backup_relative:
            backup = STATE_DIR / backup_relative
            ensure_path_hierarchy_safe(backup)
            if current_bytes is None:
                state["managed"][name] = False
                state["managed_file_metadata"][name] = None
                state["pending_cleanup"][name] = False
                write_state(state)
                if path_exists(backup):
                    ensure_path_hierarchy_safe(destination)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, destination)
                state["backups"].pop(name, None)
                write_state(state)
                if path_exists(backup):
                    backup.unlink()
            else:
                state["managed"][name] = False
                state["managed_file_metadata"][name] = None
                state["pending_cleanup"][name] = True
                write_state(state)
                print(f"Leaving {destination} in place; live file and backup both exist.")
        else:
            if destination.exists() and current_bytes == expected_bytes:
                state["managed"][name] = False
                state["managed_file_metadata"][name] = None
                state["pending_cleanup"][name] = True
                write_state(state)
                destination.unlink()
                state["pending_cleanup"][name] = False
                write_state(state)
            elif destination.exists():
                state["managed"][name] = False
                state["managed_file_metadata"][name] = None
                state["pending_cleanup"][name] = True
                write_state(state)
                print(f"Leaving {destination} in place; file no longer matches repo-managed content.")
            else:
                state["managed"][name] = False
                state["managed_file_metadata"][name] = None
                state["pending_cleanup"][name] = False
                write_state(state)

    previous_default = validated_previous_default_agent(state)
    if config.get("default_agent") == "architect" and previous_default is not None:
        if previous_default.get("present"):
            config["default_agent"] = previous_default.get("value")
        else:
            config.pop("default_agent", None)
        write_json(config_path, config)

    write_state(state)

    cleanup_deferred = False
    for name in state.get("managed_agents", AGENT_NAMES):
        destination = installed_agent_path(name)
        ensure_not_symlink(destination)
        backup_relative = state.get("backups", {}).get(name)
        source = source_agent_path(name)
        expected_bytes = source.read_bytes()
        current_bytes = read_bytes(destination)

        if backup_relative:
            backup = STATE_DIR / backup_relative
            ensure_path_hierarchy_safe(backup)
            if path_exists(backup):
                cleanup_deferred = True
                break
        elif state.get("pending_cleanup", {}).get(name) and destination.exists() and current_bytes != expected_bytes:
            cleanup_deferred = True
            break

    if not cleanup_deferred:
        if BACKUP_DIR.exists() and not any(BACKUP_DIR.iterdir()):
            BACKUP_DIR.rmdir()

        if STATE_FILE.exists():
            STATE_FILE.unlink()
        if STATE_DIR.exists() and not any(STATE_DIR.iterdir()):
            STATE_DIR.rmdir()

    print("Uninstalled 480ai OpenCode agents.")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"install", "uninstall"}:
        print("Usage: manage_agents.py [install|uninstall]", file=sys.stderr)
        return 1

    if argv[1] == "install":
        install()
    else:
        uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
