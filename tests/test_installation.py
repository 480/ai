from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from unittest import mock
from pathlib import Path

from scripts import manage_agents


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "manage_agents.py"
AGENTS = [
    "architect",
    "developer",
    "code-reviewer",
    "code-reviewer2",
    "code-scanner",
]


class InstallationTests(unittest.TestCase):
    @contextmanager
    def patched_manage_agents_home(self, home: Path):
        config_dir = home / ".config" / "opencode"
        state_dir = config_dir / ".480ai-bootstrap"

        with ExitStack() as stack:
            stack.enter_context(mock.patch("pathlib.Path.home", return_value=home))
            stack.enter_context(mock.patch.object(manage_agents, "CONFIG_DIR", config_dir))
            stack.enter_context(mock.patch.object(manage_agents, "INSTALLED_AGENTS_DIR", config_dir / "agents"))
            stack.enter_context(mock.patch.object(manage_agents, "STATE_DIR", state_dir))
            stack.enter_context(mock.patch.object(manage_agents, "BACKUP_DIR", state_dir / "backups"))
            stack.enter_context(mock.patch.object(manage_agents, "STATE_FILE", state_dir / "state.json"))
            yield

    def run_command(self, home: Path, action: str) -> None:
        env = os.environ.copy()
        env["HOME"] = str(home)
        subprocess.run(
            ["python3", str(SCRIPT), action],
            check=True,
            cwd=REPO_ROOT,
            env=env,
        )

    def run_command_capture(self, home: Path, action: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        return subprocess.run(
            ["python3", str(SCRIPT), action],
            check=False,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

    def write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_install_is_idempotent_and_preserves_unrelated_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            self.write_json(config_path, {"model": "test-model", "default_agent": "other"})

            self.run_command(home, "install")
            self.run_command(home, "install")

            config = self.read_json(config_path)
            self.assertEqual(config["model"], "test-model")
            self.assertEqual(config["default_agent"], "architect")

            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.assertTrue(state_path.exists())

            for name in AGENTS:
                installed = home / ".config" / "opencode" / "agents" / f"{name}.md"
                source = REPO_ROOT / "agents" / f"{name}.md"
                self.assertEqual(installed.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

    def test_repeated_install_then_uninstall_preserves_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "architect.md"
            self.write_json(config_path, {"model": "test-model", "default_agent": "developer"})
            agents_dir.mkdir(parents=True, exist_ok=True)
            original_architect = "original architect agent\n"
            (agents_dir / "architect.md").write_text(original_architect, encoding="utf-8")

            self.run_command(home, "install")
            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            config = self.read_json(config_path)
            self.assertEqual(config["model"], "test-model")
            self.assertEqual(config["default_agent"], "developer")
            self.assertNotEqual((agents_dir / "architect.md").read_text(encoding="utf-8"), original_architect)
            self.assertEqual(backup_path.read_text(encoding="utf-8"), original_architect)
            self.assertTrue(state_path.exists())
            for name in [agent for agent in AGENTS if agent != "architect"]:
                self.assertFalse((agents_dir / f"{name}.md").exists())

            (agents_dir / "architect.md").unlink()
            self.run_command(home, "uninstall")

            self.assertEqual((agents_dir / "architect.md").read_text(encoding="utf-8"), original_architect)
            self.assertFalse(state_path.exists())
            self.assertFalse(backup_path.exists())

    def test_uninstall_leaves_user_modified_agent_and_new_default_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            self.write_json(config_path, {"default_agent": "developer", "provider": {"x": 1}})

            self.run_command(home, "install")

            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            architect_path.write_text("user modified architect\n", encoding="utf-8")
            self.write_json(config_path, {"default_agent": "custom-agent", "provider": {"x": 1}})

            self.run_command(home, "uninstall")

            config = self.read_json(config_path)
            self.assertEqual(config["default_agent"], "custom-agent")
            self.assertEqual(config["provider"], {"x": 1})
            self.assertEqual(architect_path.read_text(encoding="utf-8"), "user modified architect\n")

    def test_install_fails_before_overwriting_agents_when_config_is_not_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            existing_architect = "existing architect agent\n"
            architect_path = agents_dir / "architect.md"
            architect_path.write_text(existing_architect, encoding="utf-8")
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("[]\n", encoding="utf-8")

            result = self.run_command_capture(home, "install")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Expected JSON object", result.stderr)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), existing_architect)
            self.assertFalse((home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json").exists())

    def test_retry_after_failed_final_install_state_write_fails_before_fake_backup_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            architect_source = (REPO_ROOT / "agents" / "architect.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"default_agent": "developer"})

            with self.patched_manage_agents_home(home):
                real_write_state = manage_agents.write_state
                calls = 0

                def fail_final_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("boom")
                    real_write_state(state)

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_final_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.install()

            self.assertEqual(architect_path.read_text(encoding="utf-8"), architect_source)

            result = self.run_command_capture(home, "install")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("prior install state is incomplete or corrupted", result.stderr)
            self.assertFalse(backup_path.exists())
            self.assertEqual(architect_path.read_text(encoding="utf-8"), architect_source)
            self.assertEqual(self.read_json(state_path)["managed_file_metadata"]["architect"], None)

    def test_uninstall_invalid_config_fails_before_touching_managed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer", "provider": {"x": 1}})

            self.run_command(home, "install")

            managed_contents = architect_path.read_text(encoding="utf-8")
            config_path.write_text("[]\n", encoding="utf-8")

            result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Expected JSON object", result.stderr)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), managed_contents)
            self.assertTrue(state_path.exists())

    def test_uninstall_keeps_state_until_modified_file_conflict_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            self.write_json(config_path, {"default_agent": "developer", "provider": {"x": 1}})
            agents_dir.mkdir(parents=True, exist_ok=True)
            original_architect = "original architect agent\n"
            (agents_dir / "architect.md").write_text(original_architect, encoding="utf-8")

            self.run_command(home, "install")

            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            architect_path.write_text("user modified architect\n", encoding="utf-8")

            self.run_command(home, "uninstall")

            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "architect.md"
            self.assertTrue(state_path.exists())
            self.assertTrue(backup_path.exists())

            architect_path.unlink()
            self.run_command(home, "uninstall")

            self.assertFalse(state_path.exists())
            self.assertFalse(backup_path.exists())
            self.assertEqual(
                self.read_json(config_path),
                {"default_agent": "developer", "provider": {"x": 1}},
            )
            self.assertEqual(architect_path.read_text(encoding="utf-8"), original_architect)

    def test_uninstall_preserves_preexisting_matching_agent_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            architect_source = (REPO_ROOT / "agents" / "architect.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"default_agent": "developer"})
            agents_dir.mkdir(parents=True, exist_ok=True)
            architect_path = agents_dir / "architect.md"
            architect_path.write_text(architect_source, encoding="utf-8")

            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertTrue(architect_path.exists())
            self.assertEqual(architect_path.read_text(encoding="utf-8"), architect_source)

    def test_partial_uninstall_then_reinstall_and_uninstall_preserves_live_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            self.run_command(home, "install")
            architect_path.write_text("user modified architect\n", encoding="utf-8")
            self.run_command(home, "uninstall")

            architect_path.write_text("new user architect\n", encoding="utf-8")
            install_result = self.run_command_capture(home, "install")
            uninstall_result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(install_result.returncode, 0)
            self.assertIn("live file and backup both exist", install_result.stderr)
            self.assertEqual(uninstall_result.returncode, 0)
            self.assertIn("live file and backup both exist", uninstall_result.stdout)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), "new user architect\n")
            self.assertEqual(backup_path.read_text(encoding="utf-8"), "original architect\n")
            self.assertTrue(state_path.exists())

    def test_retry_after_failed_uninstall_preserves_new_user_file_without_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer"})

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = manage_agents.write_state
                calls = 0

                def fail_after_first_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            architect_path.write_text("new user architect\n", encoding="utf-8")
            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertNotEqual(architect_path.read_text(encoding="utf-8"), "new user architect\n")
            architect_path.unlink()
            self.run_command(home, "uninstall")

            self.assertEqual(architect_path.read_text(encoding="utf-8"), "new user architect\n")

    def test_retry_after_failed_uninstall_preserves_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = manage_agents.write_state
                calls = 0

                def fail_after_first_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertNotEqual(architect_path.read_text(encoding="utf-8"), "original architect\n")
            architect_path.unlink()
            self.run_command(home, "uninstall")

            self.assertEqual(architect_path.read_text(encoding="utf-8"), "original architect\n")

    def test_uninstall_restores_backup_after_failed_final_install_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                real_write_state = manage_agents.write_state
                calls = 0

                def fail_final_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("boom")
                    real_write_state(state)

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_final_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.install()

            state = self.read_json(state_path)
            self.assertTrue(state["managed"]["architect"])
            self.assertFalse(state["pending_cleanup"]["architect"])

            self.run_command(home, "uninstall")

            self.assertEqual(backup_path.read_text(encoding="utf-8"), "original architect\n")
            self.assertTrue(state_path.exists())

            architect_path.unlink()
            self.run_command(home, "uninstall")

            self.assertEqual(architect_path.read_text(encoding="utf-8"), "original architect\n")
            self.assertFalse(state_path.exists())

    def test_legacy_state_without_pending_cleanup_recovers_after_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = manage_agents.write_state
                calls = 0

                def fail_after_first_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            state = self.read_json(state_path)
            state.pop("pending_cleanup", None)
            self.write_json(state_path, state)

            architect_path.unlink()
            self.run_command(home, "uninstall")

            self.assertEqual(architect_path.read_text(encoding="utf-8"), "original architect\n")
            self.assertFalse(state_path.exists())

    def test_reinstall_refuses_to_overwrite_recreated_file_while_original_backup_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = manage_agents.write_state
                calls = 0

                def fail_after_first_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            architect_path.write_text("new user architect\n", encoding="utf-8")

            result = self.run_command_capture(home, "install")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to overwrite", result.stderr)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), "new user architect\n")
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "original architect\n")
            self.assertTrue(state_path.exists())

    def test_tampered_managed_state_cannot_suppress_backup_of_recreated_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer"})

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = manage_agents.write_state
                calls = 0

                def fail_after_first_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            state = self.read_json(state_path)
            state["managed"] = {name: True for name in AGENTS}
            state["pending_cleanup"] = {name: False for name in AGENTS}
            self.write_json(state_path, state)

            architect_path.write_text("new user architect\n", encoding="utf-8")

            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertNotEqual(architect_path.read_text(encoding="utf-8"), "new user architect\n")
            architect_path.unlink()
            self.run_command(home, "uninstall")

            self.assertEqual(architect_path.read_text(encoding="utf-8"), "new user architect\n")

    def test_tampered_state_with_matching_metadata_cannot_delete_recreated_repo_identical_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            architect_source = (REPO_ROOT / "agents" / "architect.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"default_agent": "developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = manage_agents.write_state
                calls = 0

                def fail_after_first_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            state = self.read_json(state_path)
            state["managed"] = {name: True for name in AGENTS}
            state["pending_cleanup"] = {name: False for name in AGENTS}
            self.assertTrue(backup_file.exists())
            architect_path.unlink()
            architect_path.write_text(architect_source, encoding="utf-8")
            state["managed_file_metadata"] = {
                **state["managed_file_metadata"],
                "architect": manage_agents.file_metadata(architect_path),
            }
            self.write_json(state_path, state)

            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertTrue(architect_path.exists())
            self.assertEqual(architect_path.read_text(encoding="utf-8"), architect_source)

    def test_partial_uninstall_repo_identical_live_file_stays_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            architect_source = (REPO_ROOT / "agents" / "architect.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"default_agent": "developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = manage_agents.write_state
                calls = 0

                def fail_after_first_write(state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("scripts.manage_agents.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            architect_path.unlink()
            architect_path.write_text(architect_source, encoding="utf-8")
            self.run_command(home, "install")
            result = self.run_command_capture(home, "uninstall")

            self.assertEqual(result.returncode, 0)
            self.assertIn("live file and backup both exist", result.stdout)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), architect_source)
            self.assertTrue(state_path.exists())
            self.assertTrue(backup_file.exists())

    def test_write_json_preserves_existing_file_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opencode.json"
            original = {"default_agent": "developer"}
            replacement = {"default_agent": "architect"}
            manage_agents.write_json(path, original)

            with mock.patch("scripts.manage_agents.os.replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    manage_agents.write_json(path, replacement)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_install_refuses_to_follow_symlinked_agent_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            outside_path = home / "outside.md"
            architect_path = agents_dir / "architect.md"
            self.write_json(config_path, {"default_agent": "developer"})
            agents_dir.mkdir(parents=True, exist_ok=True)
            outside_path.write_text("outside\n", encoding="utf-8")
            architect_path.symlink_to(outside_path)

            result = self.run_command_capture(home, "install")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to manage symlinked path", result.stderr)
            self.assertEqual(outside_path.read_text(encoding="utf-8"), "outside\n")

    def test_install_refuses_symlinked_agents_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            opencode_dir = config_path.parent
            outside_dir = home / "outside-agents"
            self.write_json(config_path, {"default_agent": "developer"})
            outside_dir.mkdir(parents=True, exist_ok=True)
            (outside_dir / "sentinel.txt").write_text("keep\n", encoding="utf-8")

            agents_path = opencode_dir / "agents"
            agents_path.symlink_to(outside_dir, target_is_directory=True)

            result = self.run_command_capture(home, "install")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to manage symlinked path", result.stderr)
            self.assertEqual((outside_dir / "sentinel.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertEqual(sorted(path.name for path in outside_dir.iterdir()), ["sentinel.txt"])

    def test_uninstall_rejects_tampered_managed_agents_in_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.write_json(config_path, {"default_agent": "developer"})

            self.run_command(home, "install")

            state = self.read_json(state_path)
            state["managed_agents"] = ["architect", "../../outside"]
            self.write_json(state_path, state)

            result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid managed_agents", result.stderr)

    def test_uninstall_rejects_backup_path_outside_backup_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            outside_backup = home / "outside-backup.md"
            self.write_json(config_path, {"default_agent": "developer"})

            self.run_command(home, "install")

            outside_backup.write_text("outside backup\n", encoding="utf-8")
            state = self.read_json(state_path)
            state["backups"] = {"architect": "../../../../outside-backup.md"}
            self.write_json(state_path, state)

            result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid backup path", result.stderr)
            self.assertEqual(outside_backup.read_text(encoding="utf-8"), "outside backup\n")
            self.assertTrue(architect_path.exists())

    def test_uninstall_rejects_tampered_managed_and_pending_cleanup_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.write_json(config_path, {"default_agent": "developer"})

            self.run_command(home, "install")

            state = self.read_json(state_path)
            state["managed"] = {"architect": True}
            self.write_json(state_path, state)

            managed_result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(managed_result.returncode, 0)
            self.assertIn("Invalid managed", managed_result.stderr)

            state = self.read_json(state_path)
            state["managed"] = {name: True for name in AGENTS}
            state["pending_cleanup"] = {**{name: False for name in AGENTS}, "../../outside": True}
            self.write_json(state_path, state)

            pending_result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(pending_result.returncode, 0)
            self.assertIn("Invalid pending_cleanup", pending_result.stderr)

    def test_uninstall_rejects_sparse_state_without_touching_live_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "architect.md"
            self.write_json(config_path, {"default_agent": "developer", "provider": {"x": 1}})

            self.run_command(home, "install")

            managed_contents = architect_path.read_text(encoding="utf-8")
            self.write_json(state_path, {})

            result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing managed_agents", result.stderr)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), managed_contents)
            self.assertEqual(
                self.read_json(config_path),
                {"default_agent": "architect", "provider": {"x": 1}},
            )

    def test_uninstall_ignores_invalid_previous_default_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.write_json(config_path, {"default_agent": "developer", "provider": {"x": 1}})

            self.run_command(home, "install")

            state = self.read_json(state_path)
            state["previous_default_agent"] = []
            self.write_json(state_path, state)

            result = self.run_command_capture(home, "uninstall")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                self.read_json(config_path),
                {"default_agent": "architect", "provider": {"x": 1}},
            )


if __name__ == "__main__":
    unittest.main()
