from __future__ import annotations

import builtins
import importlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib  # type: ignore[reportMissingImports]
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from app import agent_bundle
from app import installer_core
from app import manage_agents
from app import render_agents
from app.install_targets import (
    InstallPaths,
    InstallTarget,
    all_providers,
    bootstrap_state_paths,
    get_provider,
    project_bootstrap_state_paths,
    resolve_project_root,
    resolve_install_target,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
MANAGE_AGENTS_MODULE = "app.manage_agents"
AGENTS = [
    "480-architect",
    "480-developer",
    "480-code-reviewer",
    "480-code-reviewer2",
    "480-code-scanner",
]
CLAUDE_AGENTS = [
    "480-architect",
    "480-developer",
    "480-code-reviewer",
    "480-code-reviewer2",
    "480-code-scanner",
]
LEGACY_CLAUDE_AGENTS = {
    "ai-architect": "480-architect",
    "ai-developer": "480-developer",
    "ai-code-reviewer": "480-code-reviewer",
    "ai-code-reviewer-secondary": "480-code-reviewer2",
    "ai-code-scanner": "480-code-scanner",
}
CODEX_CANONICAL_AGENTS = [
    "480-developer",
    "480-code-reviewer",
    "480-code-reviewer2",
    "480-code-scanner",
]
CODEX_AGENTS = [
    "480-developer",
    "480-code-reviewer",
    "480-code-reviewer2",
    "480-code-scanner",
]


def provider_agents_source_dir(target: str) -> Path:
    return REPO_ROOT / "providers" / target / "agents"


def provider_index_path(target: str) -> Path:
    return REPO_ROOT / "providers" / target / "AGENTS.md"


class TTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class TTYStringIOWithFileno(TTYStringIO):
    def fileno(self) -> int:
        return 0


class FakeCursesScreen:
    def __init__(self, keys: list[int], *, height: int = 24, width: int = 80):
        self._keys = iter(keys)
        self.height = height
        self.width = width
        self.keypad_enabled = False
        self.frames: list[dict[int, str]] = []
        self._buffer: dict[int, str] = {}

    def keypad(self, enabled: bool) -> None:
        self.keypad_enabled = enabled

    def getch(self) -> int:
        return next(self._keys)

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def erase(self) -> None:
        self._buffer = {}

    def addnstr(self, y: int, _x: int, text: str, max_chars: int, *_attrs) -> None:
        self._buffer[y] = text[:max_chars]

    def refresh(self) -> None:
        self.frames.append(dict(self._buffer))


class FakeCursesModule:
    KEY_UP = 259
    KEY_DOWN = 258
    KEY_LEFT = 260
    KEY_ENTER = 343
    A_BOLD = 1
    A_DIM = 2

    class error(Exception):
        pass

    def __init__(self, screen: FakeCursesScreen | None = None):
        self.screen = screen

    def wrapper(self, func):
        if self.screen is None:
            raise AssertionError("Fake curses screen is required.")
        return func(self.screen)

    def curs_set(self, _value: int) -> None:
        return None

    def setupterm(self, *, fd: int) -> None:
        return None


class InstallationTests(unittest.TestCase):
    def assert_screen_title_contains(self, titles: list[str], *parts: str) -> None:
        self.assertTrue(
            any(all(part in title for part in parts) for title in titles),
            f"No rendered title contained all parts {parts!r}: {titles!r}",
        )

    def assert_summary_contains_provider(
        self,
        summary_lines: list[str],
        *,
        provider_label: str,
        scope: str,
        activate_default: bool | None,
        desktop_notifications: bool | None = None,
        model_mode: str,
    ) -> None:
        provider_block = self.summary_provider_block(summary_lines, provider_label)
        summary = "\n".join(provider_block)
        self.assertIn(f"- {provider_label}:", provider_block[0])
        self.assertIn(f"scope={scope}", provider_block[0])
        if activate_default is not None:
            self.assertIn(f"default activation: {'yes' if activate_default else 'no'}", summary)
        else:
            self.assertNotIn("default activation:", summary)
        if desktop_notifications is not None:
            self.assertIn(f"desktop notifications: {'yes' if desktop_notifications else 'no'}", summary)
        else:
            self.assertNotIn("desktop notifications:", summary)
        self.assertIn(f"model mode: {model_mode}", summary)

    def summary_provider_block(self, summary_lines: list[str], provider_label: str) -> list[str]:
        block_start: int | None = None
        for index, line in enumerate(summary_lines):
            if line.startswith(f"- {provider_label}:"):
                block_start = index
                break

        self.assertIsNotNone(block_start, f"Provider block not found for {provider_label!r}: {summary_lines!r}")
        assert block_start is not None

        block_end = len(summary_lines)
        for index in range(block_start + 1, len(summary_lines)):
            if summary_lines[index].startswith("- "):
                block_end = index
                break
        return summary_lines[block_start:block_end]

    def readme_section(self, heading: str, readme: str) -> str:
        match = re.search(rf"## {re.escape(heading)}\n(?P<body>.*?)(?:\n## |\Z)", readme, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group("body")

    def readme_section_any(self, headings: tuple[str, ...], readme: str) -> str:
        for heading in headings:
            match = re.search(rf"## {re.escape(heading)}\n(?P<body>.*?)(?:\n## |\Z)", readme, re.DOTALL)
            if match is not None:
                return match.group("body")
        self.fail(f"README section not found: {headings}")

    def assert_claude_team_contract(self, text: str, *, named_team_members: bool = True) -> None:
        self.assertRegex(text, r"480-architect")
        self.assertRegex(text, r"(agent team|team)")
        if named_team_members:
            self.assertRegex(text, r"480-developer")
            self.assertRegex(text, r"480-code-reviewer")
            self.assertRegex(text, r"480-code-reviewer2")
        self.assertRegex(text, r"(480-code-scanner|scanner)")
        self.assertRegex(text, r"(only when|optionally)")
        self.assertRegex(text, r"(fallback|single-orchestrator)")
        self.assertRegex(text, r"(disabled|unsupported)")

    def assert_claude_teams_install_contract(
        self,
        text: str,
        *,
        mention_uninstall: bool,
        mention_env_key: bool,
    ) -> None:
        self.assertRegex(text, r"(install)")
        self.assertRegex(text, r"(agent teams|experimental flag)")
        self.assertRegex(text, r"(ask|question|whether)")
        self.assertRegex(text, r"settings\.json")
        self.assertRegex(text, r"`?env`?")
        if mention_env_key:
            self.assertRegex(text, r"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
        self.assertRegex(text, r"(merge|writes it into)")
        if mention_uninstall:
            self.assertRegex(text, r"(uninstall)")
            self.assertRegex(text, r"(unchanged|preserve|do not change|untouched)")

    def assert_claude_teams_prompt_shown(self, text: str) -> None:
        self.assertRegex(text, r"Claude Code")
        self.assertRegex(text, r"agent teams")
        self.assertRegex(text, r"experimental flag")

    def assert_codex_lifecycle_contract(
        self,
        text: str,
        *,
        ownership_line: str,
        active_work_line: str,
        close_line: str,
    ) -> None:
        self.assertIn(ownership_line, text)
        self.assertIn(active_work_line, text)
        self.assertIn(close_line, text)
        self.assertNotIn(
            "Codex manages child thread lifecycle itself. Do not add explicit close enforcement unless a separate platform contract requires it.",
            text,
        )
        self.assertNotIn(
            "Let Codex manage child thread lifecycle unless a platform contract explicitly requires otherwise.",
            text,
        )
        self.assertNotIn(
            "Let Codex manage reviewer/scanner child thread lifecycle unless a platform contract explicitly requires otherwise.",
            text,
        )
        self.assertIn(
            "Treat a spawn response with no `agent_id`, or any non-structured spawn response, as `spawn_failure`.",
            text,
        )

    def assert_codex_close_contract(self, text: str, *, parent_label: str) -> None:
        self.assertIn(f"Parent close responsibility stays with the parent {parent_label}.", text)
        self.assertRegex(text, r"Only when .* current loop is truly finished")
        self.assertRegex(text, r"latest result is completed")
        self.assertRegex(text, r"no follow-up, retry, or result wait remains")
        self.assertRegex(text, r"Do not (?:treat this .* as closable while|close .+ that still has)")
        self.assertIn("follow-up, retry, or result wait work", text)
        self.assertIn(
            "Treat a spawn response with no `agent_id`, or any non-structured spawn response, as `spawn_failure`.",
            text,
        )

    def assert_claude_parent_lifecycle_contract(
        self,
        text: str,
        *,
        ownership_line: str,
        active_work_line: str,
        close_line: str,
    ) -> None:
        self.assertIn(ownership_line, text)
        self.assertIn(active_work_line, text)
        self.assertIn(close_line, text)
        self.assertNotIn("Let Codex manage child thread lifecycle unless a platform contract explicitly requires otherwise.", text)
        self.assertNotIn(
            "Codex manages child thread lifecycle itself. Do not add explicit close enforcement unless a separate platform contract requires it.",
            text,
        )

    def assert_codex_reviewer_stays_in_thread(self, text: str) -> None:
        self.assertIn("Review in-thread and do not spawn additional subagents from this reviewer.", text)
        self.assertNotIn("If you spawn `480-code-scanner`", text)

    def assert_codex_reviewer_feedback_contract(self, text: str) -> None:
        self.assertIn("Approval:", text)
        self.assertIn("`Approved.`", text)
        self.assertIn("If it does not need fixing, respond with `Approved.` only.", text)
        self.assertNotIn("No changes requested.", text)
        self.assertNotIn("LGTM.", text)
        self.assertIn("One flat bullet per required change, and nothing else.", text)
        self.assertIn(
            "Exact format per bullet: `- What: <change>. Why: <reason>. Where: <file/function/line>.`",
            text,
        )
        self.assertIn("Return exactly these six lines and nothing else:", text)
        self.assertIn("`status: blocked`", text)
        self.assertIn("`blocker_type: <spawn_failure|thread_limit|usage_limit|other>`", text)
        self.assertIn("`stage: <spawn|wait|review>`", text)
        self.assertIn("`reason: <short reason>`", text)
        self.assertIn("`attempts: <number>`", text)
        self.assertIn("`evidence: <short evidence>`", text)

    def assert_reviewer_throughput_contract(self, text: str) -> None:
        self.assertIn("The user's time is expensive.", text)
        self.assertIn(
            "converging quickly to either required changes or approval, and avoid creating avoidable back-and-forth.",
            text,
        )
        self.assertIn(
            "Avoid creating review churn from minor operational friction or speculative concerns.",
            text,
        )

    def assert_scanner_output_path_contract(self, text: str) -> None:
        self.assertIn("`docs/480ai/ARCHITECTURE.md`", text)
        self.assertNotIn("called ARCHITECTURE.md at the root of the repo", text)
        self.assertRegex(text, r"Do not modify any files except .*docs/480ai/ARCHITECTURE\.md")
        self.assertIn("The user's time is expensive.", text)
        self.assertRegex(text, r"remove avoidable stack(?:(?: and)? tooling|/tooling) questions early")
        self.assertIn(
            "Absorb small uncertainties with evidence-based judgments and explicit assumptions when that is sufficient.",
            text,
        )

    def assert_developer_role_identity_contract(self, text: str, *, codex_style: bool) -> None:
        if codex_style:
            self.assertIn(
                "You are already the active `480-developer` child session for the current task",
                text,
            )
            self.assertIn(
                "If inherited context conflicts with this role (for example, architect-style instructions or text telling you to spawn `480-developer`)",
                text,
            )
            self.assertIn(
                "Do not spawn, delegate to, or ask another `480-developer` to implement the same task.",
                text,
            )
            self.assertIn(
                "The only allowed child delegation from this session is support work such as `480-code-reviewer`, `480-code-reviewer2`, or `480-code-scanner` within the current task.",
                text,
            )
        else:
            self.assertIn(
                "You are already the active @480-developer child session for the current task",
                text,
            )
            self.assertIn(
                "If inherited context conflicts with this role (for example, architect-style instructions or text telling you to spawn @480-developer)",
                text,
            )
            self.assertIn(
                "Do not spawn, delegate to, or ask another @480-developer to implement the same task.",
                text,
            )

        self.assertIn("The user's time is expensive.", text)
        self.assertIn("inside this developer loop", text)
        self.assertIn("Do not treat routine status requests, progress reports, or check-ins as a reason to pause", text)
        self.assertTrue(
            "Do not treat progress as completion or stop the implementation/review loop." in text
            or "Do not treat a progress update as a completion report or stop the implementation or review loop."
            in text
        )

    def assert_codex_developer_review_parse_contract(self, text: str) -> None:
        self.assertIn(
            "Parse reviewer responses using the reviewer contract, in this order, instead of assuming long free-form feedback:",
            text,
        )
        self.assertIn("Approval: treat exactly `Approved.` as approval.", text)
        self.assertIn(
            "Change requests: treat one or more flat bullets in the form `- What: <change>. Why: <reason>. Where: <file/function/line>.` as required changes.",
            text,
        )
        self.assertIn(
            "Infrastructure blocker: treat exactly the six-line minimal report with `status: blocked`, `blocker_type`, `stage`, `reason`, `attempts`, and `evidence` as a delegation infrastructure blocker.",
            text,
        )
        self.assertIn(
            "Do not treat a blocker report as approval, and do not infer approval from any response shape other than the explicit `Approved.` approval string.",
            text,
        )
        self.assertIn("Iterate until BOTH reviewers approve with the explicit `Approved.` approval string.", text)
        self.assertNotIn("Any reviewer response without change requests counts as approval.", text)

    def assert_architect_autopilot_worktree_contract(self, text: str) -> None:
        self.assertIn(
            "The user's time is expensive. Once the required pre-implementation approvals are satisfied, the default responsibility is to carry the approved scope through to completion rather than handing routine coordination back to the user.",
            text,
        )
        self.assertIn(
            "After the plan is approved, stay on autopilot and execute the approved plan to completion without asking the user for additional between-task approval.",
            text,
        )
        self.assertIn(
            "Absorb routine exceptions, minor operational friction, and ordinary mid-task judgment calls inside the agent loop whenever that can be done safely and within the approved scope.",
            text,
        )
        self.assertIn(
            "Once work inside the approved scope has started, keep that work moving to completion even if the user later asks for a mid-task status update.",
            text,
        )
        self.assertIn(
            "Status updates do not reset autopilot or create a new approval gate.",
            text,
        )
        self.assertIn(
            "Treat status reports, progress summaries, and mid-task check-ins as reporting only. They do not pause execution, reopen the agreed scope, or create a new approval gate.",
            text,
        )
        self.assertIn(
            "Plan and delegate with a dedicated worktree and task branch as the default operating model when the environment supports it.",
            text,
        )
        self.assertIn(
            "Do not merge branches or delete a completed worktree unless the user explicitly asks for that git operation.",
            text,
        )
        self.assertIn(
            "Return to the user when the approved plan is complete, or when a pause condition requires user input. Do not treat routine progress reporting as a reason to stop execution and hand control back early.",
            text,
        )

    def subprocess_env(self, home: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(REPO_ROOT) if not existing_pythonpath else f"{REPO_ROOT}:{existing_pythonpath}"
        return env

    @contextmanager
    def patched_manage_agents_home(self, home: Path):
        with ExitStack() as stack:
            stack.enter_context(mock.patch("pathlib.Path.home", return_value=home))
            yield

    @contextmanager
    def patched_render_outputs_root(self, root: Path):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(render_agents, "REPO_ROOT", root))
            yield

    @contextmanager
    def patched_detected_interactive_providers(self, *targets: str):
        choices = tuple(choice for choice in manage_agents.TARGET_CHOICES if choice.value in set(targets))
        with mock.patch.object(manage_agents, "detected_provider_choices", return_value=choices):
            yield

    def run_command(self, home: Path, action: str, *extra_args: str, cwd: Path | None = None) -> None:
        subprocess.run(
            ["python3", "-m", MANAGE_AGENTS_MODULE, action, *extra_args],
            check=True,
            cwd=REPO_ROOT if cwd is None else cwd,
            env=self.subprocess_env(home),
        )

    def run_command_capture(
        self,
        home: Path,
        action: str,
        *extra_args: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "-m", MANAGE_AGENTS_MODULE, action, *extra_args],
            check=False,
            cwd=REPO_ROOT if cwd is None else cwd,
            env=self.subprocess_env(home),
            text=True,
            capture_output=True,
        )

    def run_module_command_capture(
        self,
        home: Path,
        module: str,
        *module_args: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "-m", module, *module_args],
            check=False,
            cwd=REPO_ROOT if cwd is None else cwd,
            env=self.subprocess_env(home),
            text=True,
            capture_output=True,
        )

    def run_python_capture(
        self,
        home: Path,
        script: str,
        *,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "-c", script],
            check=False,
            cwd=REPO_ROOT if cwd is None else cwd,
            env=self.subprocess_env(home),
            text=True,
            capture_output=True,
        )

    def write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def advanced_selection(self, target: str, **overrides: str):
        return manage_agents.advanced_model_selection_for_target(target, overrides)

    def make_repo_project(self, home: Path) -> tuple[Path, Path]:
        project_root = home / "work" / "demo-repo"
        nested_dir = project_root / "packages" / "app"
        nested_dir.mkdir(parents=True, exist_ok=True)
        (project_root / ".git").mkdir(exist_ok=True)
        return project_root, nested_dir

    def seed_legacy_claude_install(
        self,
        home: Path,
        *,
        scope: str = "user",
        project_root: Path | None = None,
        activated_agent: str = "ai-architect",
        previous_agent: str = "custom-agent",
    ) -> tuple[Path, Path]:
        if scope == "user":
            config_dir = home / ".claude"
            state_path = home / ".claude" / ".480ai-bootstrap" / "state.json"
        else:
            assert project_root is not None
            config_dir = project_root / ".claude"
            state_path = project_bootstrap_state_paths("claude", "project", project_root, home=home).state_file

        config_path = config_dir / "settings.json"
        agents_dir = config_dir / "agents"
        self.write_json(config_path, {"agent": activated_agent, "theme": "dark"})

        managed: dict[str, bool] = {}
        managed_file_metadata: dict[str, dict[str, int] | None] = {}
        pending_cleanup: dict[str, bool] = {}
        for legacy_name, current_name in LEGACY_CLAUDE_AGENTS.items():
            source = provider_agents_source_dir("claude") / f"{current_name}.md"
            destination = agents_dir / f"{legacy_name}.md"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            managed[legacy_name] = True
            metadata = installer_core.file_metadata(destination)
            assert metadata is not None
            managed_file_metadata[legacy_name] = metadata
            pending_cleanup[legacy_name] = False

        self.write_json(
            state_path,
            {
                "version": 1,
                "managed_agents": list(LEGACY_CLAUDE_AGENTS),
                "backups": {},
                "managed": managed,
                "managed_file_metadata": managed_file_metadata,
                "pending_cleanup": pending_cleanup,
                "previous_default_agent": {"present": True, "value": previous_agent},
                "default_activation_enabled": True,
            },
        )
        return config_path, state_path

    def test_provider_registry_exposes_equal_level_provider_metadata(self) -> None:
        providers = {provider.identifier: provider for provider in all_providers()}

        self.assertEqual(set(providers), {"opencode", "claude", "codex"})
        self.assertEqual(providers["opencode"].cli_binary_name, "opencode")
        self.assertEqual(providers["claude"].cli_binary_name, "claude")
        self.assertEqual(providers["codex"].cli_binary_name, "codex")
        self.assertEqual(providers["opencode"].artifacts.agents_dirname, "providers/opencode/agents")
        self.assertEqual(providers["claude"].artifacts.agents_dirname, "providers/claude/agents")
        self.assertEqual(providers["codex"].artifacts.agents_dirname, "providers/codex/agents")
        self.assertEqual(providers["opencode"].supported_scopes, ("user",))
        self.assertEqual(providers["claude"].supported_scopes, ("user", "project"))
        self.assertEqual(providers["codex"].supported_scopes, ("user", "project"))
        self.assertEqual(providers["opencode"].default_activation_default, True)
        self.assertEqual(providers["claude"].default_activation_default, False)
        self.assertIsNone(providers["codex"].default_activation_default)

    def test_provider_model_profiles_expose_recommended_defaults_by_provider(self) -> None:
        specs = {spec.identifier: spec for spec in agent_bundle.load_bundle()}

        opencode = get_provider("opencode")
        claude = get_provider("claude")
        codex = get_provider("codex")

        self.assertEqual(opencode.supported_model_selection_modes(), ("recommended", "advanced"))
        self.assertEqual(claude.supported_model_selection_modes(), ("recommended", "advanced"))
        self.assertEqual(codex.supported_model_selection_modes(), ("recommended", "advanced"))

        opencode_architect = opencode.recommended_role_model_config(specs["480-architect"])
        self.assertEqual(opencode_architect.model, "openai/gpt-5.4")
        self.assertEqual(opencode_architect.effort, "xhigh")

        claude_reviewer = claude.recommended_role_model_config(specs["480-code-reviewer"])
        self.assertEqual(claude_reviewer.model, "claude-opus-4-6")
        self.assertEqual(claude_reviewer.effort, "low")

        claude_reviewer2 = claude.recommended_role_model_config(specs["480-code-reviewer2"])
        self.assertEqual(claude_reviewer2.model, "claude-sonnet-4-6")
        self.assertEqual(claude_reviewer2.effort, "low")

        codex_developer = codex.recommended_role_model_config(specs["480-developer"])
        self.assertEqual(codex_developer.model, "gpt-5.4-mini")
        self.assertEqual(codex_developer.effort, "medium")

        codex_scanner = codex.recommended_role_model_config(specs["480-code-scanner"])
        self.assertEqual(codex_scanner.model, "gpt-5.3-codex-spark")
        self.assertEqual(codex_scanner.effort, "low")

        codex_reviewer = codex.recommended_role_model_config(specs["480-code-reviewer"])
        self.assertEqual(codex_reviewer.model, "gpt-5.4")
        self.assertEqual(codex_reviewer.effort, "high")

        codex_reviewer2 = codex.recommended_role_model_config(specs["480-code-reviewer2"])
        self.assertEqual(codex_reviewer2.model, "gpt-5.4")
        self.assertEqual(codex_reviewer2.effort, "medium")

    def test_provider_model_profiles_define_advanced_curated_options_for_every_role(self) -> None:
        role_ids = {spec.identifier for spec in agent_bundle.load_bundle()}

        for provider in all_providers():
            for role_id in role_ids:
                options = provider.advanced_role_model_options(role_id)
                self.assertGreaterEqual(len(options), 2)
                self.assertEqual(len({option.key for option in options}), len(options))
                for option in options:
                    self.assertTrue(option.label)
                    self.assertTrue(option.config.model)
                    self.assertTrue(option.config.effort)

    def test_manage_agents_exposes_provider_model_schema_for_future_installer_ux(self) -> None:
        schema = manage_agents.model_selection_schema_for_target("claude")

        self.assertEqual(schema.supported_modes, ("recommended", "advanced"))
        self.assertIn("480-architect", schema.advanced)
        self.assertGreaterEqual(len(schema.advanced["480-architect"]), 2)

    def test_provider_defaults_advanced_choice_to_recommended_match_when_available(self) -> None:
        specs = {spec.identifier: spec for spec in agent_bundle.load_bundle()}

        self.assertEqual(
            get_provider("opencode").default_advanced_role_model_option(specs["480-architect"]).key,
            "gpt-5.4-xhigh",
        )
        self.assertEqual(
            get_provider("claude").default_advanced_role_model_option(specs["480-code-reviewer"]).key,
            "opus-low",
        )
        self.assertEqual(
            get_provider("codex").default_advanced_role_model_option(specs["480-developer"]).key,
            "gpt-5.4-medium",
        )
        self.assertEqual(
            get_provider("codex").default_advanced_role_model_option(specs["480-code-reviewer"]).key,
            "gpt-5.4-high",
        )
        self.assertEqual(
            get_provider("codex").default_advanced_role_model_option(specs["480-code-reviewer2"]).key,
            "gpt-5.4-medium",
        )
        reviewer2_option_keys = {
            option.key for option in get_provider("codex").advanced_role_model_options("480-code-reviewer2")
        }
        self.assertIn("gpt-5.4-medium", reviewer2_option_keys)
        self.assertIn("gpt-5.2-medium", reviewer2_option_keys)

    def test_target_agent_names_use_provider_registry_for_all_targets(self) -> None:
        self.assertEqual(agent_bundle.target_agent_names("opencode"), AGENTS)
        self.assertEqual(agent_bundle.target_agent_names("claude"), CLAUDE_AGENTS)
        self.assertEqual(agent_bundle.target_agent_names("codex"), CODEX_AGENTS)

    def test_opencode_user_target_resolver_matches_existing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            target = resolve_install_target("opencode", "user", home=home)

            self.assertEqual(target.name, "opencode")
            self.assertEqual(target.scope, "user")
            self.assertEqual(target.paths.config_dir, home / ".config" / "opencode")
            self.assertEqual(target.paths.config_file, home / ".config" / "opencode" / "opencode.json")
            self.assertEqual(target.paths.installed_agents_dir, home / ".config" / "opencode" / "agents")
            self.assertEqual(target.paths.state_dir, home / ".config" / "opencode" / ".480ai-bootstrap")
            self.assertEqual(
                target.paths.backup_dir,
                home / ".config" / "opencode" / ".480ai-bootstrap" / "backups",
            )
            self.assertEqual(
                target.paths.state_file,
                home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json",
            )
            activation = target.default_activation
            self.assertIsNotNone(activation)
            assert activation is not None
            self.assertEqual(activation.config_key, "default_agent")
            self.assertEqual(activation.managed_value, "480-architect")

    def test_claude_user_target_resolver_matches_expected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            target = resolve_install_target("claude", "user", home=home)

            self.assertEqual(target.name, "claude")
            self.assertEqual(target.label, "Claude Code")
            self.assertEqual(target.scope, "user")
            self.assertEqual(target.paths.config_dir, home / ".claude")
            self.assertEqual(target.paths.config_file, home / ".claude" / "settings.json")
            self.assertEqual(target.paths.installed_agents_dir, home / ".claude" / "agents")
            self.assertEqual(target.paths.state_dir, home / ".claude" / ".480ai-bootstrap")
            activation = target.default_activation
            self.assertIsNotNone(activation)
            assert activation is not None
            self.assertEqual(activation.config_key, "agent")
            self.assertEqual(activation.managed_value, "480-architect")

    def test_claude_project_target_resolver_uses_project_local_settings_and_external_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)

            with mock.patch("pathlib.Path.home", return_value=home):
                previous_cwd = Path.cwd()
                os.chdir(project_root)
                try:
                    target = resolve_install_target("claude", "project", home=home)
                finally:
                    os.chdir(previous_cwd)

            self.assertEqual(target.paths.config_dir.resolve(), (project_root / ".claude").resolve())
            assert target.paths.config_file is not None
            self.assertEqual(
                target.paths.config_file.resolve(),
                (project_root / ".claude" / "settings.json").resolve(),
            )
            self.assertEqual(
                target.paths.installed_agents_dir.resolve(),
                (project_root / ".claude" / "agents").resolve(),
            )
            self.assertTrue(str(target.paths.state_dir).startswith(str(home / ".config" / "480ai")))
            self.assertFalse(str(target.paths.state_dir).startswith(str(project_root)))

    def test_resolve_project_root_uses_git_root_for_nested_repo_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root, nested_dir = self.make_repo_project(home)

            self.assertEqual(resolve_project_root(nested_dir), project_root.resolve())

    def test_project_bootstrap_state_paths_are_deterministic_and_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)

            first = project_bootstrap_state_paths("claude", "project", project_root, home=home)
            second = project_bootstrap_state_paths("claude", "project", project_root, home=home)
            other_target = project_bootstrap_state_paths("codex", "project", project_root, home=home)

            self.assertEqual(first, second)
            self.assertNotEqual(first.state_dir, other_target.state_dir)
            self.assertTrue(str(first.state_dir).startswith(str(home / ".config" / "480ai")))
            self.assertFalse(str(first.state_dir).startswith(str(project_root)))
            self.assertEqual(first.backup_dir, first.state_dir / "backups")
            self.assertEqual(first.state_file, first.state_dir / "state.json")

    def test_install_and_uninstall_skip_default_activation_when_target_disables_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            config_dir = home / ".config" / "custom-target"
            config_path = config_dir / "config.json"
            installed_agents_dir = config_dir / "agents"
            state_dir = home / ".config" / "480ai" / "bootstrap-state" / "custom-target"
            self.write_json(config_path, {"default_agent": "keep-me", "provider": {"x": 1}})

            target = InstallTarget(
                name="custom-target",
                label="Custom Target",
                scope="project",
                paths=InstallPaths(
                    config_dir=config_dir,
                    config_file=config_path,
                    installed_agents_dir=installed_agents_dir,
                    state=bootstrap_state_paths(state_dir),
                ),
                default_activation=None,
            )

            installer_core.install(target, provider_agents_source_dir("opencode"), AGENTS)

            config_after_install = self.read_json(config_path)
            state_after_install = self.read_json(target.paths.state_file)
            self.assertEqual(config_after_install, {"default_agent": "keep-me", "provider": {"x": 1}})
            self.assertNotIn("previous_default_agent", state_after_install)

            installer_core.uninstall(target, provider_agents_source_dir("opencode"), AGENTS)

            self.assertEqual(self.read_json(config_path), {"default_agent": "keep-me", "provider": {"x": 1}})
            self.assertFalse(target.paths.state_file.exists())

    def test_installer_core_import_does_not_require_toml_support(self) -> None:
        original_module = sys.modules.get("app.installer_core")
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in {"tomllib", "tomli"}:
                raise ModuleNotFoundError(f"blocked {name}")
            return real_import(name, globals, locals, fromlist, level)

        try:
            sys.modules.pop("app.installer_core", None)
            with mock.patch("builtins.__import__", side_effect=fake_import):
                module = importlib.import_module("app.installer_core")
            self.assertTrue(hasattr(module, "read_toml_object"))
        finally:
            sys.modules.pop("app.installer_core", None)
            if original_module is not None:
                sys.modules["app.installer_core"] = original_module

    def test_load_toml_module_preserves_internal_module_not_found_error(self) -> None:
        inner_error = ModuleNotFoundError("blocked inner dependency")
        inner_error.name = "tomli._parser"

        with mock.patch.object(installer_core, "import_module", side_effect=inner_error):
            with self.assertRaises(ModuleNotFoundError) as context:
                installer_core.load_toml_module()

        self.assertIs(context.exception, inner_error)

    def test_load_toml_module_uses_vendored_fallback_when_stdlib_and_tomli_are_missing(self) -> None:
        def fake_import_module(name):
            if name in {"tomllib", "tomli"}:
                raise ModuleNotFoundError(f"No module named '{name}'", name=name)
            raise AssertionError(f"unexpected import_module call: {name}")

        with mock.patch.object(installer_core, "import_module", side_effect=fake_import_module):
            module = installer_core.load_toml_module()

        self.assertIs(module, importlib.import_module("app._vendor_tomllib"))

    def test_manage_agents_package_import_does_not_fallback_on_internal_import_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            result = self.run_python_capture(
                home,
                """
import builtins
import importlib
import sys

real_import = builtins.__import__
absolute_fallbacks = []

def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    if (level == 1 and name == "install_targets") or name == "app.install_targets":
        raise ModuleNotFoundError("blocked app.install_targets")
    if level == 0 and name in {"agent_bundle", "install_targets", "installer_core", "render_agents"}:
        absolute_fallbacks.append(name)
        raise AssertionError(f"unexpected absolute fallback: {name}")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = fake_import
for module_name in (
    "app.manage_agents",
    "app.install_targets",
    "app.agent_bundle",
    "app.render_agents",
    "app.installer_core",
):
    sys.modules.pop(module_name, None)

try:
    importlib.import_module("app.manage_agents")
except ModuleNotFoundError as exc:
    if absolute_fallbacks:
        print(absolute_fallbacks)
        raise SystemExit(2)
    if "blocked app.install_targets" not in str(exc):
        raise
else:
    raise SystemExit(3)
""",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_opencode_install_succeeds_without_toml_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            result = self.run_python_capture(
                home,
                """
from app import manage_agents
from app import installer_core

def fake_import_module(name):
    if name in {"tomllib", "tomli"}:
        raise ModuleNotFoundError(f"blocked {name}")
    raise AssertionError(f"unexpected import_module call: {name}")

installer_core.import_module = fake_import_module
manage_agents.install(target="opencode", scope="user")
""",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / ".config" / "opencode" / "agents" / "480-architect.md").exists())

    def test_codex_install_succeeds_with_vendored_toml_support_when_stdlib_toml_modules_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".codex" / "config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text('model = "gpt-5.4"\n[profiles.default]\napproval_policy = "never"\n', encoding="utf-8")
            result = self.run_python_capture(
                home,
                """
from app import manage_agents
from app import installer_core

def fake_import_module(name):
    if name in {"tomllib", "tomli"}:
        raise ModuleNotFoundError(f"No module named '{name}'", name=name)
    raise AssertionError(f"unexpected import_module call: {name}")

installer_core.import_module = fake_import_module
manage_agents.install(target="codex", scope="user")
""",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            merged = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["model"], "gpt-5.4")
            self.assertEqual(merged["profiles"]["default"]["approval_policy"], "never")
            self.assertTrue(merged["features"]["multi_agent"])
            self.assertEqual(merged["agents"]["max_depth"], 2)
            self.assertTrue((home / ".codex" / "agents" / "480-developer.toml").exists())

    def test_claude_user_install_without_activate_default_preserves_agent_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            self.write_json(config_path, {"agent": "keep-me", "theme": "dark"})

            self.run_command(home, "install", "--target", "claude", "--scope", "user")

            self.assertEqual(self.read_json(config_path), {"agent": "keep-me", "theme": "dark"})
            for name in CLAUDE_AGENTS:
                installed = home / ".claude" / "agents" / f"{name}.md"
                source = provider_agents_source_dir("claude") / f"{name}.md"
                self.assertEqual(installed.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "user")

            self.assertEqual(self.read_json(config_path), {"agent": "keep-me", "theme": "dark"})
            self.assertFalse((home / ".claude" / ".480ai-bootstrap" / "state.json").exists())

    def test_claude_user_install_with_activate_default_restores_previous_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            self.write_json(config_path, {"agent": "custom-agent", "theme": "light"})

            self.run_command(
                home,
                "install",
                "--target",
                "claude",
                "--scope",
                "user",
                "--activate-default",
            )

            self.assertEqual(self.read_json(config_path), {"agent": "480-architect", "theme": "light"})

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "user")

            self.assertEqual(self.read_json(config_path), {"agent": "custom-agent", "theme": "light"})

    def test_claude_user_round_trip_removes_bootstrap_created_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            agents_dir = home / ".claude" / "agents"

            self.run_command(
                home,
                "install",
                "--target",
                "claude",
                "--scope",
                "user",
                "--activate-default",
            )

            self.assertTrue(config_path.exists())

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "user")

            self.assertFalse(config_path.exists())
            self.assertFalse(agents_dir.exists())

    def test_claude_user_install_with_teams_enabled_safely_merges_env_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            self.write_json(
                config_path,
                {
                    "agent": "keep-me",
                    "theme": "dark",
                    "env": {"EXISTING_FLAG": "keep"},
                },
            )

            with self.patched_manage_agents_home(home):
                manage_agents.install(target="claude", scope="user", activate_default=False, enable_teams=True)

                self.assertEqual(
                    self.read_json(config_path),
                    {
                        "agent": "keep-me",
                        "theme": "dark",
                        "env": {
                            "EXISTING_FLAG": "keep",
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                        },
                    },
                )

                manage_agents.uninstall(target="claude", scope="user")

            self.assertEqual(
                self.read_json(config_path),
                {
                    "agent": "keep-me",
                    "theme": "dark",
                    "env": {
                        "EXISTING_FLAG": "keep",
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                    },
                },
            )

    def test_claude_user_install_with_teams_disabled_does_not_add_env_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            self.write_json(config_path, {"agent": "keep-me", "theme": "dark"})

            with self.patched_manage_agents_home(home):
                manage_agents.install(target="claude", scope="user", activate_default=False, enable_teams=False)

            self.assertEqual(self.read_json(config_path), {"agent": "keep-me", "theme": "dark"})

    def test_claude_project_install_with_teams_enabled_merges_env_setting_and_keeps_state_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)
            config_path = project_root / ".claude" / "settings.json"
            self.write_json(
                config_path,
                {
                    "agent": "team-agent",
                    "theme": "dark",
                    "env": {"EXISTING_FLAG": "keep"},
                },
            )

            with self.patched_manage_agents_home(home):
                previous_cwd = Path.cwd()
                try:
                    os.chdir(project_root)
                    manage_agents.install(target="claude", scope="project", activate_default=False, enable_teams=True)

                    self.assertEqual(
                        self.read_json(config_path),
                        {
                            "agent": "team-agent",
                            "theme": "dark",
                            "env": {
                                "EXISTING_FLAG": "keep",
                                "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                            },
                        },
                    )

                    state_path = project_bootstrap_state_paths("claude", "project", project_root, home=home).state_file
                    self.assertTrue(state_path.exists())
                    self.assertFalse((project_root / ".claude" / ".480ai-bootstrap" / "state.json").exists())

                    manage_agents.uninstall(target="claude", scope="project")
                    self.assertFalse(state_path.exists())
                finally:
                    os.chdir(previous_cwd)

            self.assertEqual(
                self.read_json(config_path),
                {
                    "agent": "team-agent",
                    "theme": "dark",
                    "env": {
                        "EXISTING_FLAG": "keep",
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                    },
                },
            )

    def test_claude_project_install_with_teams_disabled_does_not_add_env_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)
            config_path = project_root / ".claude" / "settings.json"
            self.write_json(config_path, {"agent": "team-agent", "theme": "dark"})

            with self.patched_manage_agents_home(home):
                previous_cwd = Path.cwd()
                try:
                    os.chdir(project_root)
                    manage_agents.install(target="claude", scope="project", activate_default=False, enable_teams=False)
                finally:
                    os.chdir(previous_cwd)

            self.assertEqual(self.read_json(config_path), {"agent": "team-agent", "theme": "dark"})

    def test_claude_user_install_with_desktop_notifications_manages_hook_and_restores_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            hook_path = home / ".claude" / ".480ai" / "desktop-notify-hook.py"
            self.write_json(config_path, {"agent": "keep-me", "hooks": {"Stop": []}})

            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="claude",
                    scope="user",
                    activate_default=False,
                    enable_teams=False,
                    desktop_notifications=True,
                )

                config = self.read_json(config_path)
                self.assertEqual(config["agent"], "keep-me")
                self.assertEqual(config["hooks"]["Stop"], [])
                self.assertEqual(
                    config["hooks"]["Notification"],
                    [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{hook_path} claude",
                                }
                            ]
                        }
                    ],
                )
                self.assertTrue(hook_path.exists())

                manage_agents.uninstall(target="claude", scope="user")

            self.assertEqual(self.read_json(config_path), {"agent": "keep-me", "hooks": {"Stop": []}})
            self.assertFalse(hook_path.exists())

    def test_claude_user_install_with_existing_notification_hook_preserves_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            hook_path = home / ".claude" / ".480ai" / "desktop-notify-hook.py"
            existing_hook = {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{hook_path} claude",
                    }
                ]
            }
            self.write_json(
                config_path,
                {
                    "agent": "keep-me",
                    "hooks": {
                        "Stop": [],
                        "Notification": [existing_hook],
                    },
                },
            )

            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="claude",
                    scope="user",
                    activate_default=False,
                    enable_teams=False,
                    desktop_notifications=True,
                )

                self.assertEqual(
                    self.read_json(config_path),
                    {
                        "agent": "keep-me",
                        "hooks": {
                            "Stop": [],
                            "Notification": [existing_hook],
                        },
                    },
                )
                self.assertTrue(hook_path.exists())

                manage_agents.uninstall(target="claude", scope="user")

            self.assertEqual(
                self.read_json(config_path),
                {
                    "agent": "keep-me",
                    "hooks": {
                        "Stop": [],
                        "Notification": [existing_hook],
                    },
                },
            )
            self.assertFalse(hook_path.exists())

    def test_claude_existing_state_without_notification_metadata_backfills_and_restores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            state_path = home / ".claude" / ".480ai-bootstrap" / "state.json"
            hook_path = home / ".claude" / ".480ai" / "desktop-notify-hook.py"
            self.write_json(config_path, {"agent": "keep-me", "hooks": {"Stop": []}})

            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="claude",
                    scope="user",
                    activate_default=False,
                    enable_teams=False,
                    desktop_notifications=False,
                )

                state = self.read_json(state_path)
                managed_config = state.get("managed_config")
                assert isinstance(managed_config, dict)
                managed_config.pop("claude_notification", None)
                self.write_json(state_path, state)

                manage_agents.install(
                    target="claude",
                    scope="user",
                    activate_default=False,
                    enable_teams=False,
                    desktop_notifications=True,
                )

                self.assertTrue(hook_path.exists())
                manage_agents.uninstall(target="claude", scope="user")

            self.assertEqual(self.read_json(config_path), {"agent": "keep-me", "hooks": {"Stop": []}})
            self.assertFalse(hook_path.exists())

    def test_claude_reinstall_with_explicit_notification_opt_out_removes_managed_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            hook_path = home / ".claude" / ".480ai" / "desktop-notify-hook.py"
            self.write_json(config_path, {"agent": "keep-me", "hooks": {"Stop": []}})

            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="claude",
                    scope="user",
                    activate_default=False,
                    enable_teams=False,
                    desktop_notifications=True,
                )

                self.assertEqual(
                    self.read_json(config_path)["hooks"]["Notification"],
                    [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{hook_path} claude",
                                }
                            ]
                        }
                    ],
                )

                manage_agents.install(
                    target="claude",
                    scope="user",
                    activate_default=False,
                    enable_teams=False,
                    desktop_notifications=False,
                )

            self.assertEqual(self.read_json(config_path), {"agent": "keep-me", "hooks": {"Stop": []}})
            self.assertFalse(hook_path.exists())

    def test_claude_reinstall_without_activate_default_preserves_previous_restore_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            self.write_json(config_path, {"agent": "custom-agent", "theme": "light"})

            self.run_command(
                home,
                "install",
                "--target",
                "claude",
                "--scope",
                "user",
                "--activate-default",
            )
            self.run_command(home, "install", "--target", "claude", "--scope", "user")
            self.run_command(home, "uninstall", "--target", "claude", "--scope", "user")

            self.assertEqual(self.read_json(config_path), {"agent": "custom-agent", "theme": "light"})

    def test_claude_user_uninstall_cleans_managed_filename_and_reinstall_refreshes_latest_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".claude" / "settings.json"
            state_path = home / ".claude" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".claude" / ".480ai-bootstrap" / "backups" / "480-developer.md"
            developer_path = home / ".claude" / "agents" / "480-developer.md"
            managed_source = (provider_agents_source_dir("claude") / "480-developer.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"agent": "keep-me", "theme": "dark"})

            self.run_command(home, "install", "--target", "claude", "--scope", "user")

            developer_path.write_text("user modified developer\n", encoding="utf-8")

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "user")

            self.assertEqual(self.read_json(config_path), {"agent": "keep-me", "theme": "dark"})
            self.assertFalse(developer_path.exists())
            self.assertFalse(state_path.exists())
            self.assertFalse(backup_path.exists())

            developer_path.parent.mkdir(parents=True, exist_ok=True)
            developer_path.write_text("stale blocked install developer\n", encoding="utf-8")

            self.run_command(home, "install", "--target", "claude", "--scope", "user")

            self.assertEqual(self.read_json(config_path), {"agent": "keep-me", "theme": "dark"})
            self.assertEqual(developer_path.read_text(encoding="utf-8"), managed_source)

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "user")

            self.assertFalse(developer_path.exists())
            self.assertFalse(backup_path.exists())
            self.assertFalse(state_path.exists())

    def test_claude_project_install_uses_repo_local_settings_and_external_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)
            config_path = project_root / ".claude" / "settings.json"
            self.write_json(config_path, {"agent": "team-agent", "hooks": {"Stop": []}})

            self.run_command(
                home,
                "install",
                "--target",
                "claude",
                "--scope",
                "project",
                cwd=project_root,
            )

            self.assertEqual(self.read_json(config_path), {"agent": "team-agent", "hooks": {"Stop": []}})
            state_path = project_bootstrap_state_paths("claude", "project", project_root, home=home).state_file
            self.assertTrue(state_path.exists())
            self.assertFalse((project_root / ".claude" / ".480ai-bootstrap" / "state.json").exists())

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "project", cwd=project_root)

            self.assertEqual(self.read_json(config_path), {"agent": "team-agent", "hooks": {"Stop": []}})
            self.assertFalse(state_path.exists())

    def test_codex_user_target_resolver_matches_expected_paths_and_disables_default_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            target = resolve_install_target("codex", "user", home=home)

            self.assertEqual(target.name, "codex")
            self.assertEqual(target.label, "Codex CLI")
            self.assertEqual(target.scope, "user")
            self.assertEqual(target.paths.config_dir, home / ".codex")
            self.assertEqual(target.paths.config_file, home / ".codex" / "config.toml")
            self.assertEqual(target.paths.installed_agents_dir, home / ".codex" / "agents")
            self.assertEqual(target.paths.state_dir, home / ".codex" / ".480ai-bootstrap")
            self.assertEqual(target.agent_file_extension, ".toml")
            self.assertIsNone(target.default_activation)

    def test_codex_project_target_resolver_uses_repo_local_agents_and_external_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)

            with mock.patch("pathlib.Path.home", return_value=home):
                previous_cwd = Path.cwd()
                os.chdir(project_root)
                try:
                    target = resolve_install_target("codex", "project", home=home)
                finally:
                    os.chdir(previous_cwd)

            self.assertEqual(target.paths.config_dir.resolve(), (project_root / ".codex").resolve())
            assert target.paths.config_file is not None
            self.assertEqual(target.paths.config_file.resolve(), (project_root / ".codex" / "config.toml").resolve())
            self.assertEqual(
                target.paths.installed_agents_dir.resolve(),
                (project_root / ".codex" / "agents").resolve(),
            )
            self.assertTrue(str(target.paths.state_dir).startswith(str(home / ".config" / "480ai")))
            self.assertFalse(str(target.paths.state_dir).startswith(str(project_root)))

    def test_claude_project_target_resolver_normalizes_nested_repo_cwd_to_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root, nested_dir = self.make_repo_project(home)

            with mock.patch("pathlib.Path.home", return_value=home):
                previous_cwd = Path.cwd()
                os.chdir(nested_dir)
                try:
                    target = resolve_install_target("claude", "project", home=home)
                finally:
                    os.chdir(previous_cwd)

            expected_state = project_bootstrap_state_paths("claude", "project", project_root, home=home)
            nested_state = project_bootstrap_state_paths("claude", "project", nested_dir, home=home)
            self.assertEqual(target.paths.config_dir.resolve(), (project_root / ".claude").resolve())
            self.assertEqual(target.paths.state_dir, expected_state.state_dir)
            self.assertNotEqual(target.paths.state_dir, nested_state.state_dir)

    def test_codex_project_target_resolver_normalizes_nested_repo_cwd_to_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root, nested_dir = self.make_repo_project(home)

            with mock.patch("pathlib.Path.home", return_value=home):
                previous_cwd = Path.cwd()
                os.chdir(nested_dir)
                try:
                    target = resolve_install_target("codex", "project", home=home)
                finally:
                    os.chdir(previous_cwd)

            expected_state = project_bootstrap_state_paths("codex", "project", project_root, home=home)
            nested_state = project_bootstrap_state_paths("codex", "project", nested_dir, home=home)
            self.assertEqual(target.paths.config_dir.resolve(), (project_root / ".codex").resolve())
            self.assertEqual(target.paths.state_dir, expected_state.state_dir)
            self.assertNotEqual(target.paths.state_dir, nested_state.state_dir)

    def test_codex_user_install_uninstall_and_reinstall_preserve_guidance_without_duplicate_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guidance_path = home / ".codex" / "AGENTS.md"
            config_path = home / ".codex" / "config.toml"
            agents_dir = home / ".codex" / "agents"
            guidance_path.parent.mkdir(parents=True, exist_ok=True)
            original_guidance = "keep user guidance\n"
            guidance_path.write_text(original_guidance, encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            state_path = home / ".codex" / ".480ai-bootstrap" / "state.json"
            state = self.read_json(state_path)
            self.assertNotIn("previous_default_agent", state)
            self.assertNotIn("default_activation_enabled", state)
            parsed_config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed_config["features"]["multi_agent"], True)
            self.assertEqual(parsed_config["agents"]["max_depth"], 2)
            installed_guidance = guidance_path.read_text(encoding="utf-8")
            self.assertIn(original_guidance, installed_guidance)
            self.assertIn(installer_core.CODEX_MANAGED_AGENTS_START, installed_guidance)
            self.assertIn(installer_core.CODEX_MANAGED_AGENTS_END, installed_guidance)
            self.assertEqual(installed_guidance.count(installer_core.CODEX_MANAGED_AGENTS_START), 1)
            self.assertIn(render_agents.render_codex_managed_guidance(agent_bundle.load_bundle()), installed_guidance)

            for name in CODEX_AGENTS:
                installed = home / ".codex" / "agents" / f"{name}.toml"
                source = provider_agents_source_dir("codex") / f"{name}.toml"
                self.assertEqual(installed.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertEqual(guidance_path.read_text(encoding="utf-8"), original_guidance)
            self.assertFalse(state_path.exists())
            for name in CODEX_AGENTS:
                self.assertFalse((home / ".codex" / "agents" / f"{name}.toml").exists())
            self.assertFalse(config_path.exists())
            self.assertFalse(agents_dir.exists())

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            reinstalled_guidance = guidance_path.read_text(encoding="utf-8")
            self.assertIn(original_guidance, reinstalled_guidance)
            self.assertEqual(reinstalled_guidance.count(installer_core.CODEX_MANAGED_AGENTS_START), 1)
            self.assertEqual(reinstalled_guidance.count(installer_core.CODEX_MANAGED_AGENTS_END), 1)

    def test_codex_user_install_cleans_legacy_architect_agent_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_agents_dir = home / ".codex" / "agents"
            legacy_agents_dir.mkdir(parents=True, exist_ok=True)
            (legacy_agents_dir / "480-architect.toml").write_text("legacy architect\n", encoding="utf-8")
            (legacy_agents_dir / "480.toml").write_text("legacy root\n", encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            self.assertFalse((legacy_agents_dir / "480-architect.toml").exists())
            self.assertFalse((legacy_agents_dir / "480.toml").exists())

    def test_codex_user_uninstall_cleans_legacy_architect_agent_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_agents_dir = home / ".codex" / "agents"

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            (legacy_agents_dir / "480-architect.toml").write_text("legacy architect\n", encoding="utf-8")
            (legacy_agents_dir / "480.toml").write_text("legacy root\n", encoding="utf-8")

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertFalse((legacy_agents_dir / "480-architect.toml").exists())
            self.assertFalse((legacy_agents_dir / "480.toml").exists())

    def test_codex_user_uninstall_without_state_still_cleans_legacy_architect_agent_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_agents_dir = home / ".codex" / "agents"
            legacy_agents_dir.mkdir(parents=True, exist_ok=True)
            (legacy_agents_dir / "480-architect.toml").write_text("legacy architect\n", encoding="utf-8")
            (legacy_agents_dir / "480.toml").write_text("legacy root\n", encoding="utf-8")

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertFalse((legacy_agents_dir / "480-architect.toml").exists())
            self.assertFalse((legacy_agents_dir / "480.toml").exists())

    def test_codex_user_uninstall_without_state_rejects_symlinked_agents_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            outside_dir = home / "outside"
            outside_dir.mkdir()
            (outside_dir / "480-architect.toml").write_text("outside architect\n", encoding="utf-8")

            codex_dir = home / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            agents_path = codex_dir / "agents"
            agents_path.symlink_to(outside_dir, target_is_directory=True)

            result = self.run_command_capture(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to manage symlinked path", result.stderr)
            self.assertEqual((outside_dir / "480-architect.toml").read_text(encoding="utf-8"), "outside architect\n")

    def test_codex_verify_reports_install_state_and_noop_delegation_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                self.run_command(home, "install", "--target", "codex", "--scope", "user")

                agent_message = json.dumps(
                    {
                        "developer_role": "The current session is the `480-developer` role. It implements one task at a time from the given Task Brief and reports results to the parent session after iterating through review subagents when needed.",
                        "redelegated": False,
                        "notes": "Current role is 480-developer.",
                    }
                )
                fake_stdout = "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                        json.dumps({"type": "turn.started"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_4",
                                    "type": "agent_message",
                                    "text": agent_message,
                                },
                            }
                        ),
                        json.dumps({"type": "turn.completed"}),
                    ]
                )

                def fake_run(command, **kwargs):
                    self.assertEqual(command[:3], ["codex", "exec", "--json"])
                    self.assertIn("--cd", command)
                    self.assertIn(str(REPO_ROOT), command)
                    self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
                    self.assertIn(manage_agents.CODEX_NOOP_VALIDATION_PROMPT, command)
                    self.assertTrue(kwargs["text"])
                    self.assertTrue(kwargs["capture_output"])
                    return subprocess.CompletedProcess(command, 0, stdout=fake_stdout, stderr="")

                with mock.patch.object(manage_agents.subprocess, "run", side_effect=fake_run) as run_mock:
                    result = manage_agents.verify(target="codex", scope="user")

            run_mock.assert_called_once()
            self.assertEqual(result["final_classification"], "success")
            self.assertEqual(result["install_state"]["status"], "ok")
            self.assertEqual(result["cleanup_result"]["status"], "ok")
            self.assertEqual(result["general_session_validation"]["status"], "not_run")
            self.assertIsNone(result["general_session_validation"]["developer_role"])
            self.assertIsNone(result["general_session_validation"]["redelegated"])
            self.assertIn("not run by automated verify", result["general_session_validation"]["notes"])
            self.assertEqual(result["exec_path_result"]["status"], "ok")
            self.assertIn("agent_outputs", result["install_state"])
            self.assertIn("config", result["install_state"])
            self.assertIn("guidance", result["install_state"])

    def test_codex_verify_keeps_exec_path_limit_from_becoming_install_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                self.run_command(home, "install", "--target", "codex", "--scope", "user")

                agent_message = json.dumps(
                    {
                        "developer_role": "The current session is the software architect agent.",
                        "redelegated": False,
                        "notes": "Current role is architect.",
                    }
                )
                fake_stdout = "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                        json.dumps({"type": "turn.started"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_4",
                                    "type": "agent_message",
                                    "text": agent_message,
                                },
                            }
                        ),
                        json.dumps({"type": "turn.completed"}),
                    ]
                )

                with mock.patch.object(
                    manage_agents.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(["codex"], 0, stdout=fake_stdout, stderr=""),
                ):
                    result = manage_agents.verify(target="codex", scope="user")

            self.assertEqual(result["install_state"]["status"], "ok")
            self.assertEqual(result["cleanup_result"]["status"], "ok")
            self.assertEqual(result["exec_path_result"]["status"], "blocked")
            self.assertEqual(result["general_session_validation"]["status"], "not_run")
            self.assertEqual(result["final_classification"], "exec_path_limitation")

    def test_codex_verify_keeps_hard_exec_path_failures_as_platform_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                self.run_command(home, "install", "--target", "codex", "--scope", "user")

                def fake_run(command, **kwargs):
                    raise FileNotFoundError("codex binary not found")

                with mock.patch.object(manage_agents.subprocess, "run", side_effect=fake_run):
                    result = manage_agents.verify(target="codex", scope="user")

            self.assertEqual(result["install_state"]["status"], "ok")
            self.assertEqual(result["cleanup_result"]["status"], "ok")
            self.assertEqual(result["exec_path_result"]["status"], "blocked")
            self.assertIn("codex binary not found", result["exec_path_result"]["error"])
            self.assertEqual(result["final_classification"], "platform_blocker")

    def test_codex_verify_honors_persisted_advanced_model_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="codex",
                    scope="user",
                    model_selection=self.advanced_selection("codex", **{"480-developer": "spark-medium"}),
                )

                agent_message = json.dumps(
                    {
                        "developer_role": "The current session is the `480-developer` role. It implements one task at a time from the given Task Brief and reports results to the parent session after iterating through review subagents when needed.",
                        "redelegated": False,
                        "notes": "Current role is 480-developer.",
                    }
                )
                fake_stdout = "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                        json.dumps({"type": "turn.started"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_4",
                                    "type": "agent_message",
                                    "text": agent_message,
                                },
                            }
                        ),
                        json.dumps({"type": "turn.completed"}),
                    ]
                )

                with mock.patch.object(
                    manage_agents.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(["codex"], 0, stdout=fake_stdout, stderr=""),
                ):
                    result = manage_agents.verify(target="codex", scope="user")

            self.assertEqual(result["final_classification"], "success")
            self.assertEqual(result["install_state"]["status"], "ok")
            self.assertEqual(result["install_state"]["agent_outputs"]["status"], "ok")

    def test_codex_verify_flags_legacy_backup_files_in_cleanup_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                self.run_command(home, "install", "--target", "codex", "--scope", "user")

                backup_dir = home / ".codex" / ".480ai-bootstrap" / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                (backup_dir / "480-architect.toml").write_text("legacy architect backup\n", encoding="utf-8")
                (backup_dir / "480.toml").write_text("legacy root backup\n", encoding="utf-8")

                agent_message = json.dumps(
                    {
                        "developer_role": "The current session is the `480-developer` role. It implements one task at a time from the given Task Brief and reports results to the parent session after iterating through review subagents when needed.",
                        "redelegated": False,
                        "notes": "Current role is 480-developer.",
                    }
                )
                fake_stdout = "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                        json.dumps({"type": "turn.started"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_4",
                                    "type": "agent_message",
                                    "text": agent_message,
                                },
                            }
                        ),
                        json.dumps({"type": "turn.completed"}),
                    ]
                )

                with mock.patch.object(
                    manage_agents.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(["codex"], 0, stdout=fake_stdout, stderr=""),
                ):
                    result = manage_agents.verify(target="codex", scope="user")

            self.assertEqual(result["final_classification"], "install_issue")
            self.assertEqual(result["cleanup_result"]["status"], "mismatch")
            self.assertEqual(len(result["cleanup_result"]["legacy_files"]), 2)

    def test_codex_verify_ignores_unrelated_files_in_installed_agents_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                self.run_command(home, "install", "--target", "codex", "--scope", "user")

                unrelated_agent_path = home / ".codex" / "agents" / "custom-agent.toml"
                unrelated_agent_path.write_text("unmanaged agent\n", encoding="utf-8")

                agent_message = json.dumps(
                    {
                        "developer_role": "The current session is the `480-developer` role. It implements one task at a time from the given Task Brief and reports results to the parent session after iterating through review subagents when needed.",
                        "redelegated": False,
                        "notes": "Current role is 480-developer.",
                    }
                )
                fake_stdout = "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                        json.dumps({"type": "turn.started"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_4",
                                    "type": "agent_message",
                                    "text": agent_message,
                                },
                            }
                        ),
                        json.dumps({"type": "turn.completed"}),
                    ]
                )

                with mock.patch.object(
                    manage_agents.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(["codex"], 0, stdout=fake_stdout, stderr=""),
                ):
                    result = manage_agents.verify(target="codex", scope="user")

            self.assertEqual(result["final_classification"], "success")
            self.assertEqual(result["install_state"]["status"], "ok")
            self.assertIn("custom-agent.toml", result["install_state"]["agent_outputs"]["unmanaged_files"])
            self.assertEqual(result["install_state"]["agent_outputs"]["status"], "ok")

    def test_codex_verify_returns_structured_mismatch_for_malformed_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                self.run_command(home, "install", "--target", "codex", "--scope", "user")

                config_path = home / ".codex" / "config.toml"
                config_path.write_text("invalid = [\n", encoding="utf-8")

                agent_message = json.dumps(
                    {
                        "developer_role": "The current session is the `480-developer` role. It implements one task at a time from the given Task Brief and reports results to the parent session after iterating through review subagents when needed.",
                        "redelegated": False,
                        "notes": "Current role is 480-developer.",
                    }
                )
                fake_stdout = "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                        json.dumps({"type": "turn.started"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_4",
                                    "type": "agent_message",
                                    "text": agent_message,
                                },
                            }
                        ),
                        json.dumps({"type": "turn.completed"}),
                    ]
                )

                with mock.patch.object(
                    manage_agents.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(["codex"], 0, stdout=fake_stdout, stderr=""),
                ):
                    result = manage_agents.verify(target="codex", scope="user")

            self.assertEqual(result["final_classification"], "install_issue")
            self.assertEqual(result["install_state"]["status"], "mismatch")
            self.assertEqual(result["install_state"]["config"]["status"], "mismatch")
            self.assertEqual(result["install_state"]["config"]["mismatches"], ["invalid-toml"])

    def test_codex_verify_returns_structured_mismatch_for_invalid_persisted_model_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                self.run_command(home, "install", "--target", "codex", "--scope", "user")

                state_path = home / ".codex" / ".480ai-bootstrap" / "state.json"
                self.write_json(
                    state_path,
                    {
                        "model_selection": {
                            "mode": "advanced",
                            "role_options": {
                                "480-developer": 123,
                            },
                        }
                    },
                )

                agent_message = json.dumps(
                    {
                        "developer_role": "The current session is the `480-developer` role. It implements one task at a time from the given Task Brief and reports results to the parent session after iterating through review subagents when needed.",
                        "redelegated": False,
                        "notes": "Current role is 480-developer.",
                    }
                )
                fake_stdout = "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                        json.dumps({"type": "turn.started"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_4",
                                    "type": "agent_message",
                                    "text": agent_message,
                                },
                            }
                        ),
                        json.dumps({"type": "turn.completed"}),
                    ]
                )

                with mock.patch.object(
                    manage_agents.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(["codex"], 0, stdout=fake_stdout, stderr=""),
                ):
                    result = manage_agents.verify(target="codex", scope="user")

            self.assertEqual(result["final_classification"], "install_issue")
            self.assertEqual(result["install_state"]["status"], "mismatch")
            self.assertEqual(result["install_state"]["agent_outputs"]["status"], "mismatch")
            self.assertIn("Invalid model_selection", result["install_state"]["agent_outputs"]["error"])

    def test_codex_verify_rejects_unsupported_target_and_scope(self) -> None:
        with self.assertRaises(SystemExit) as target_exc:
            manage_agents.verify(target="opencode", scope="user")

        self.assertEqual(str(target_exc.exception), "verify currently supports only target codex.")

        with self.assertRaises(SystemExit) as scope_exc:
            manage_agents.verify(target="codex", scope="project")

        self.assertEqual(str(scope_exc.exception), "verify currently supports only scope user.")

    def test_codex_user_round_trip_restores_existing_config_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".codex" / "config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            original = 'model = "gpt-5.4"\n\n[features]\nmulti_agent = false\n\n[agents]\nmax_depth = 7\n'
            config_path.write_text(original, encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            merged = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(merged["features"]["multi_agent"])
            self.assertEqual(merged["agents"]["max_depth"], 2)

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertEqual(config_path.read_text(encoding="utf-8"), original)

    def test_codex_user_uninstall_preserves_user_modified_managed_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".codex" / "config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text('[features]\nmulti_agent = false\n\n[agents]\nmax_depth = 7\n', encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            config_path.write_text('[features]\nmulti_agent = false\n\n[agents]\nmax_depth = 5\n', encoding="utf-8")

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            preserved = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(preserved["features"]["multi_agent"])
            self.assertEqual(preserved["agents"]["max_depth"], 5)

    def test_codex_user_install_merges_minimal_subagent_settings_into_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".codex" / "config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                'model = "gpt-5.4"\n\n[features]\nunified_exec = false\n\n[agents]\nmax_threads = 4\n',
                encoding="utf-8",
            )

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            merged = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["model"], "gpt-5.4")
            self.assertFalse(merged["features"]["unified_exec"])
            self.assertTrue(merged["features"]["multi_agent"])
            self.assertEqual(merged["agents"]["max_threads"], 100)
            self.assertEqual(merged["agents"]["max_depth"], 2)

    def test_codex_user_install_with_desktop_notifications_restores_previous_notify_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".codex" / "config.toml"
            hook_path = home / ".codex" / ".480ai" / "desktop-notify-hook.py"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                'notify = ["/Users/example/original-notify", "codex"]\n',
                encoding="utf-8",
            )

            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="codex",
                    scope="user",
                    activate_default=False,
                    desktop_notifications=True,
                )

                installed = tomllib.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(installed["notify"], [str(hook_path), "codex"])
                self.assertTrue(hook_path.exists())

                manage_agents.uninstall(target="codex", scope="user")

            restored = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["notify"], ["/Users/example/original-notify", "codex"])
            self.assertFalse(hook_path.exists())

    def test_codex_reinstall_with_explicit_notification_opt_out_removes_managed_notify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".codex" / "config.toml"
            hook_path = home / ".codex" / ".480ai" / "desktop-notify-hook.py"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                'notify = ["/Users/example/original-notify", "codex"]\n',
                encoding="utf-8",
            )

            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="codex",
                    scope="user",
                    activate_default=False,
                    desktop_notifications=True,
                )

                installed = tomllib.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(installed["notify"], [str(hook_path), "codex"])
                self.assertTrue(hook_path.exists())

                manage_agents.install(
                    target="codex",
                    scope="user",
                    activate_default=False,
                    desktop_notifications=False,
                )

            restored = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["notify"], ["/Users/example/original-notify", "codex"])
            self.assertFalse(hook_path.exists())

    def test_opencode_user_install_with_desktop_notifications_manages_plugin_and_restores_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            hook_path = home / ".config" / "opencode" / ".480ai" / "desktop-notify-hook.py"
            plugin_path = home / ".config" / "opencode" / "plugins" / "480ai-desktop-notify.js"
            self.write_json(config_path, {"theme": "dark", "default_agent": "480-architect"})

            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="opencode",
                    scope="user",
                    activate_default=True,
                    desktop_notifications=True,
                )

                installed = self.read_json(config_path)
                self.assertEqual(installed["default_agent"], "480-architect")
                self.assertTrue(hook_path.exists())
                self.assertTrue(plugin_path.exists())
                self.assertIn(str(hook_path), plugin_path.read_text(encoding="utf-8"))

                manage_agents.uninstall(target="opencode", scope="user")

            restored = self.read_json(config_path)
            self.assertEqual(restored, {"theme": "dark", "default_agent": "480-architect"})
            self.assertFalse(hook_path.exists())
            self.assertFalse(plugin_path.exists())

    def test_opencode_reinstall_with_explicit_notification_opt_out_removes_managed_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            hook_path = home / ".config" / "opencode" / ".480ai" / "desktop-notify-hook.py"
            plugin_path = home / ".config" / "opencode" / "plugins" / "480ai-desktop-notify.js"
            self.write_json(config_path, {"theme": "dark", "default_agent": "480-architect"})

            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="opencode",
                    scope="user",
                    activate_default=True,
                    desktop_notifications=True,
                )

                self.assertTrue(hook_path.exists())
                self.assertTrue(plugin_path.exists())

                manage_agents.install(
                    target="opencode",
                    scope="user",
                    activate_default=True,
                    desktop_notifications=False,
                )

            restored = self.read_json(config_path)
            self.assertEqual(restored, {"theme": "dark", "default_agent": "480-architect"})
            self.assertFalse(hook_path.exists())
            self.assertFalse(plugin_path.exists())

    def test_codex_user_install_updates_existing_dotted_subagent_settings_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".codex" / "config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                'features.multi_agent = false\nagents.max_depth = 1\nmodel = "gpt-5.4"\n',
                encoding="utf-8",
            )

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            config_text = config_path.read_text(encoding="utf-8")
            merged = tomllib.loads(config_text)
            self.assertTrue(merged["features"]["multi_agent"])
            self.assertEqual(merged["agents"]["max_depth"], 2)
            self.assertEqual(merged["agents"]["max_threads"], 100)
            self.assertEqual(merged["model"], "gpt-5.4")
            self.assertEqual(config_text.count("features.multi_agent"), 1)
            self.assertEqual(config_text.count("agents.max_depth"), 1)
            self.assertEqual(config_text.count("agents.max_threads"), 1)

    def test_codex_user_install_replaces_managed_block_in_place_and_preserves_user_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guidance_path = home / ".codex" / "AGENTS.md"
            guidance_path.parent.mkdir(parents=True, exist_ok=True)
            guidance_path.write_text("before guidance\n", encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            first_install = guidance_path.read_text(encoding="utf-8").rstrip("\n")
            user_tail = "after guidance\nsecond line\n"
            guidance_path.write_text(f"{first_install}\n\n{user_tail}", encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            reinstalled_guidance = guidance_path.read_text(encoding="utf-8")
            managed_start = reinstalled_guidance.index(installer_core.CODEX_MANAGED_AGENTS_START)
            managed_end = reinstalled_guidance.index(installer_core.CODEX_MANAGED_AGENTS_END)
            self.assertEqual(reinstalled_guidance.count(installer_core.CODEX_MANAGED_AGENTS_START), 1)
            self.assertLess(managed_start, reinstalled_guidance.index("after guidance"))
            self.assertLess(managed_end, reinstalled_guidance.index("after guidance"))
            self.assertEqual(
                reinstalled_guidance,
                (
                    "before guidance\n\n"
                    f"{installer_core.CODEX_MANAGED_AGENTS_START}\n"
                    f"{render_agents.render_codex_managed_guidance(agent_bundle.load_bundle())}\n"
                    f"{installer_core.CODEX_MANAGED_AGENTS_END}\n\n"
                    f"{user_tail}"
                ),
            )

    def test_codex_user_uninstall_preserves_preexisting_empty_guidance_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guidance_path = home / ".codex" / "AGENTS.md"
            guidance_path.parent.mkdir(parents=True, exist_ok=True)
            guidance_path.write_text("", encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")
            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertTrue(guidance_path.exists())
            self.assertEqual(guidance_path.read_text(encoding="utf-8"), "")

    def test_codex_user_uninstall_deletes_install_created_guidance_file_without_user_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guidance_path = home / ".codex" / "AGENTS.md"

            self.assertFalse(guidance_path.exists())

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            self.assertTrue(guidance_path.exists())
            self.assertIn(
                installer_core.CODEX_MANAGED_AGENTS_START,
                guidance_path.read_text(encoding="utf-8"),
            )

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertFalse(guidance_path.exists())

    def test_codex_user_uninstall_from_block_only_file_removes_leading_newline_before_user_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guidance_path = home / ".codex" / "AGENTS.md"

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            guidance_path.write_text(
                guidance_path.read_text(encoding="utf-8") + "after guidance\n",
                encoding="utf-8",
            )

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertTrue(guidance_path.exists())
            self.assertEqual(guidance_path.read_text(encoding="utf-8"), "after guidance\n")

    def test_codex_user_uninstall_removes_only_block_added_blank_lines_between_user_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guidance_path = home / ".codex" / "AGENTS.md"
            guidance_path.parent.mkdir(parents=True, exist_ok=True)
            guidance_path.write_text("before guidance\n", encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            installed_guidance = guidance_path.read_text(encoding="utf-8").rstrip("\n")
            guidance_path.write_text(f"{installed_guidance}\n\nafter guidance\n", encoding="utf-8")

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertEqual(guidance_path.read_text(encoding="utf-8"), "before guidance\n\nafter guidance\n")

    def test_codex_user_uninstall_removes_install_added_newline_and_restores_single_user_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guidance_path = home / ".codex" / "AGENTS.md"
            guidance_path.parent.mkdir(parents=True, exist_ok=True)
            guidance_path.write_text("before guidance\n", encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            installed_guidance = guidance_path.read_text(encoding="utf-8").rstrip("\n")
            guidance_path.write_text(f"{installed_guidance}\nafter guidance\n", encoding="utf-8")

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "user")

            self.assertEqual(guidance_path.read_text(encoding="utf-8"), "before guidance\nafter guidance\n")

    def test_strip_codex_managed_guidance_block_restores_single_newline_between_user_sections(self) -> None:
        managed_block = "\n".join(
            [
                installer_core.CODEX_MANAGED_AGENTS_START,
                "managed guidance",
                installer_core.CODEX_MANAGED_AGENTS_END,
            ]
        )

        preserved = installer_core.strip_codex_managed_guidance_block(f"before guidance\n{managed_block}\nafter guidance\n")

        self.assertEqual(preserved, "before guidance\nafter guidance\n")

    def test_strip_codex_managed_guidance_block_preserves_multiple_newlines_between_user_sections(self) -> None:
        managed_block = "\n".join(
            [
                installer_core.CODEX_MANAGED_AGENTS_START,
                "managed guidance",
                installer_core.CODEX_MANAGED_AGENTS_END,
            ]
        )

        preserved = installer_core.strip_codex_managed_guidance_block(
            f"before guidance\n\n\n{managed_block}\n\n\nafter guidance\n"
        )

        self.assertEqual(preserved, "before guidance\n\n\nafter guidance\n")

    def test_codex_project_install_uninstall_and_reinstall_preserve_repo_guidance_without_duplicate_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)
            repo_agents_index = project_root / "AGENTS.md"
            original_guidance = "repo guidance\n"
            repo_agents_index.write_text(original_guidance, encoding="utf-8")

            self.run_command(home, "install", "--target", "codex", "--scope", "project", cwd=project_root)

            state_path = project_bootstrap_state_paths("codex", "project", project_root, home=home).state_file
            self.assertTrue(state_path.exists())
            self.assertFalse((project_root / ".codex" / ".480ai-bootstrap" / "state.json").exists())
            installed_guidance = repo_agents_index.read_text(encoding="utf-8")
            self.assertIn(original_guidance, installed_guidance)
            self.assertEqual(installed_guidance.count(installer_core.CODEX_MANAGED_AGENTS_START), 1)
            self.assertIn(render_agents.render_codex_managed_guidance(agent_bundle.load_bundle()), installed_guidance)

            for name in CODEX_AGENTS:
                installed = project_root / ".codex" / "agents" / f"{name}.toml"
                source = provider_agents_source_dir("codex") / f"{name}.toml"
                self.assertEqual(installed.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "project", cwd=project_root)

            self.assertEqual(repo_agents_index.read_text(encoding="utf-8"), original_guidance)
            self.assertFalse(state_path.exists())

            self.run_command(home, "install", "--target", "codex", "--scope", "project", cwd=project_root)

            reinstalled_guidance = repo_agents_index.read_text(encoding="utf-8")
            self.assertIn(original_guidance, reinstalled_guidance)
            self.assertEqual(reinstalled_guidance.count(installer_core.CODEX_MANAGED_AGENTS_START), 1)

    def test_claude_project_install_from_repo_subdir_uninstalls_from_repo_root_with_same_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root, nested_dir = self.make_repo_project(home)
            config_path = project_root / ".claude" / "settings.json"
            self.write_json(config_path, {"agent": "team-agent", "hooks": {"Stop": []}})

            expected_state = project_bootstrap_state_paths("claude", "project", project_root, home=home).state_file
            nested_state = project_bootstrap_state_paths("claude", "project", nested_dir, home=home).state_file

            self.run_command(
                home,
                "install",
                "--target",
                "claude",
                "--scope",
                "project",
                cwd=nested_dir,
            )

            self.assertTrue(expected_state.exists())
            self.assertFalse(nested_state.exists())
            self.assertFalse((nested_dir / ".claude").exists())
            for name in CLAUDE_AGENTS:
                self.assertTrue((project_root / ".claude" / "agents" / f"{name}.md").exists())

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "project", cwd=project_root)

            self.assertFalse(expected_state.exists())
            for name in CLAUDE_AGENTS:
                self.assertFalse((project_root / ".claude" / "agents" / f"{name}.md").exists())
            self.assertEqual(self.read_json(config_path), {"agent": "team-agent", "hooks": {"Stop": []}})

    def test_claude_install_migrates_legacy_ai_state_files_and_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path, state_path = self.seed_legacy_claude_install(home)

            self.run_command(home, "install", "--target", "claude", "--scope", "user")

            self.assertEqual(self.read_json(config_path), {"agent": "480-architect", "theme": "dark"})
            state = self.read_json(state_path)
            self.assertEqual(state["managed_agents"], CLAUDE_AGENTS)
            self.assertEqual(set(state["managed"]), set(CLAUDE_AGENTS))
            self.assertEqual(set(state["managed_file_metadata"]), set(CLAUDE_AGENTS))
            self.assertEqual(set(state["pending_cleanup"]), set(CLAUDE_AGENTS))
            self.assertEqual(state["previous_default_agent"], {"present": True, "value": "custom-agent"})
            for legacy_name, current_name in LEGACY_CLAUDE_AGENTS.items():
                self.assertFalse((home / ".claude" / "agents" / f"{legacy_name}.md").exists())
                self.assertTrue((home / ".claude" / "agents" / f"{current_name}.md").exists())

    def test_claude_uninstall_migrates_legacy_ai_state_and_restores_previous_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path, state_path = self.seed_legacy_claude_install(home)

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "user")

            self.assertEqual(self.read_json(config_path), {"agent": "custom-agent", "theme": "dark"})
            self.assertFalse(state_path.exists())
            for legacy_name, current_name in LEGACY_CLAUDE_AGENTS.items():
                self.assertFalse((home / ".claude" / "agents" / f"{legacy_name}.md").exists())
                self.assertFalse((home / ".claude" / "agents" / f"{current_name}.md").exists())

    def test_claude_migrate_legacy_state_prefers_existing_current_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = resolve_install_target("claude", "user", home=home)
            state = installer_core.migrate_legacy_state(
                target,
                {
                    "version": 1,
                    "managed_agents": list(LEGACY_CLAUDE_AGENTS),
                    "backups": {
                        "480-architect": "backups/480-architect.md",
                        "ai-architect": "backups/ai-architect.md",
                    },
                    "managed": {"480-architect": False, "ai-architect": True},
                    "managed_file_metadata": {"480-architect": None, "ai-architect": None},
                    "pending_cleanup": {"480-architect": True, "ai-architect": False},
                },
                CLAUDE_AGENTS,
            )

            self.assertEqual(state["backups"]["480-architect"], "backups/480-architect.md")
            self.assertFalse(state["managed"]["480-architect"])
            self.assertTrue(state["pending_cleanup"]["480-architect"])
            self.assertEqual(state["managed_agents"], CLAUDE_AGENTS)

    def test_claude_migrate_legacy_state_preserves_invalid_current_backup_when_legacy_key_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = resolve_install_target("claude", "user", home=home)
            state = installer_core.migrate_legacy_state(
                target,
                {
                    "version": 1,
                    "managed_agents": list(LEGACY_CLAUDE_AGENTS),
                    "backups": {
                        "480-architect": "../../bad",
                        "ai-architect": "backups/ai-architect.md",
                    },
                    "managed": {"480-architect": False, "ai-architect": True},
                    "managed_file_metadata": {"480-architect": None, "ai-architect": None},
                    "pending_cleanup": {"480-architect": True, "ai-architect": False},
                },
                CLAUDE_AGENTS,
            )

            self.assertEqual(state["backups"]["480-architect"], "../../bad")

    def test_claude_migrate_legacy_state_rewrites_backup_path_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = resolve_install_target("claude", "user", home=home)
            state = installer_core.migrate_legacy_state(
                target,
                {
                    "version": 1,
                    "managed_agents": list(LEGACY_CLAUDE_AGENTS),
                    "backups": {"ai-architect": "backups/ai-architect.md"},
                    "managed": {name: True for name in LEGACY_CLAUDE_AGENTS},
                    "managed_file_metadata": {name: None for name in LEGACY_CLAUDE_AGENTS},
                    "pending_cleanup": {name: False for name in LEGACY_CLAUDE_AGENTS},
                },
                CLAUDE_AGENTS,
            )

            self.assertEqual(state["backups"], {"480-architect": "backups/480-architect.md"})

    def test_claude_install_recovers_legacy_invalid_backup_path_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _config_path, state_path = self.seed_legacy_claude_install(home)
            backup_path = home / ".claude" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            state = self.read_json(state_path)
            state["backups"] = {"ai-architect": "../../outside.md"}
            self.write_json(state_path, state)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text("stale legacy backup\n", encoding="utf-8")

            result = self.run_command_capture(home, "install", "--target", "claude", "--scope", "user")

            self.assertEqual(result.returncode, 0)
            recovered_state = self.read_json(state_path)
            self.assertEqual(recovered_state["managed_agents"], CLAUDE_AGENTS)
            self.assertEqual(recovered_state["backups"], {"480-architect": "backups/480-architect.md"})
            self.assertTrue(backup_path.exists())

    def test_claude_install_recovers_non_string_legacy_backup_entry_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _config_path, state_path = self.seed_legacy_claude_install(home)
            backup_path = home / ".claude" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            state = self.read_json(state_path)
            state["backups"] = {"ai-architect": 123}
            self.write_json(state_path, state)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text("stale legacy backup\n", encoding="utf-8")

            result = self.run_command_capture(home, "install", "--target", "claude", "--scope", "user")

            self.assertEqual(result.returncode, 0)
            recovered_state = self.read_json(state_path)
            self.assertEqual(recovered_state["managed_agents"], CLAUDE_AGENTS)
            self.assertEqual(recovered_state["backups"], {"480-architect": "backups/480-architect.md"})
            self.assertTrue(backup_path.exists())

    def test_claude_migrate_legacy_conflict_moves_stale_file_to_current_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path, state_path = self.seed_legacy_claude_install(home)
            target = resolve_install_target("claude", "user", home=home)
            state = installer_core.load_state(target, CLAUDE_AGENTS)
            config = self.read_json(config_path)

            legacy_path = home / ".claude" / "agents" / "ai-architect.md"
            current_path = home / ".claude" / "agents" / "480-architect.md"
            legacy_path.write_text("legacy architect\n", encoding="utf-8")
            current_path.write_text("current architect\n", encoding="utf-8")

            changed = installer_core.migrate_legacy_paths_and_activation(target, state, config)

            self.assertTrue(changed)
            self.assertFalse(legacy_path.exists())
            self.assertEqual(current_path.read_text(encoding="utf-8"), "current architect\n")
            backup_path = home / ".claude" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            self.assertEqual(backup_path.read_text(encoding="utf-8"), "legacy architect\n")
            self.assertEqual(state["backups"]["480-architect"], "backups/480-architect.md")

    def test_claude_install_preserves_legacy_conflict_failure_for_manual_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _config_path, state_path = self.seed_legacy_claude_install(home)
            legacy_backup = home / ".claude" / ".480ai-bootstrap" / "backups" / "ai-architect.md"
            current_backup = home / ".claude" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            state = self.read_json(state_path)
            state["backups"] = {"ai-architect": "backups/ai-architect.md"}
            self.write_json(state_path, state)
            legacy_backup.parent.mkdir(parents=True, exist_ok=True)
            legacy_backup.write_text("legacy backup\n", encoding="utf-8")
            current_backup.write_text("current backup\n", encoding="utf-8")

            result = self.run_command_capture(home, "install", "--target", "claude", "--scope", "user")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Conflicting legacy Claude backup files", result.stderr)
            self.assertTrue(state_path.exists())
            self.assertEqual(legacy_backup.read_text(encoding="utf-8"), "legacy backup\n")
            self.assertEqual(current_backup.read_text(encoding="utf-8"), "current backup\n")

    def test_claude_invalid_state_recovery_preserves_legacy_backup_conflict_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _config_path, state_path = self.seed_legacy_claude_install(home)
            legacy_backup = home / ".claude" / ".480ai-bootstrap" / "backups" / "ai-architect.md"
            current_backup = home / ".claude" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            state = self.read_json(state_path)
            state["managed_agents"] = ["broken-agent"]
            state["backups"] = {"ai-architect": "backups/ai-architect.md"}
            self.write_json(state_path, state)
            legacy_backup.parent.mkdir(parents=True, exist_ok=True)
            legacy_backup.write_text("legacy backup\n", encoding="utf-8")
            current_backup.write_text("current backup\n", encoding="utf-8")

            result = self.run_command_capture(home, "install", "--target", "claude", "--scope", "user")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Conflicting legacy Claude backup files", result.stderr)
            self.assertEqual(self.read_json(state_path)["managed_agents"], ["broken-agent"])
            self.assertEqual(legacy_backup.read_text(encoding="utf-8"), "legacy backup\n")
            self.assertEqual(current_backup.read_text(encoding="utf-8"), "current backup\n")

    def test_claude_project_install_and_uninstall_migrate_legacy_ai_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)
            (project_root / ".git").mkdir(exist_ok=True)
            config_path, state_path = self.seed_legacy_claude_install(home, scope="project", project_root=project_root)

            self.run_command(home, "install", "--target", "claude", "--scope", "project", cwd=project_root)

            self.assertEqual(self.read_json(config_path), {"agent": "480-architect", "theme": "dark"})
            self.assertTrue(state_path.exists())
            for legacy_name, current_name in LEGACY_CLAUDE_AGENTS.items():
                self.assertFalse((project_root / ".claude" / "agents" / f"{legacy_name}.md").exists())
                self.assertTrue((project_root / ".claude" / "agents" / f"{current_name}.md").exists())

            self.run_command(home, "uninstall", "--target", "claude", "--scope", "project", cwd=project_root)

            self.assertEqual(self.read_json(config_path), {"agent": "custom-agent", "theme": "dark"})
            self.assertFalse(state_path.exists())
            for legacy_name, current_name in LEGACY_CLAUDE_AGENTS.items():
                self.assertFalse((project_root / ".claude" / "agents" / f"{legacy_name}.md").exists())
                self.assertFalse((project_root / ".claude" / "agents" / f"{current_name}.md").exists())

    def test_codex_project_install_from_repo_subdir_uninstalls_from_repo_root_with_same_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root, nested_dir = self.make_repo_project(home)
            repo_agents_index = project_root / "AGENTS.md"
            repo_agents_index.write_text("repo guidance\n", encoding="utf-8")

            expected_state = project_bootstrap_state_paths("codex", "project", project_root, home=home).state_file
            nested_state = project_bootstrap_state_paths("codex", "project", nested_dir, home=home).state_file

            self.run_command(home, "install", "--target", "codex", "--scope", "project", cwd=nested_dir)

            self.assertTrue(expected_state.exists())
            self.assertFalse(nested_state.exists())
            self.assertFalse((nested_dir / ".codex").exists())
            for name in CODEX_AGENTS:
                self.assertTrue((project_root / ".codex" / "agents" / f"{name}.toml").exists())

            self.run_command(home, "uninstall", "--target", "codex", "--scope", "project", cwd=project_root)

            self.assertFalse(expected_state.exists())
            for name in CODEX_AGENTS:
                self.assertFalse((project_root / ".codex" / "agents" / f"{name}.toml").exists())
            self.assertEqual(repo_agents_index.read_text(encoding="utf-8"), "repo guidance\n")

    def test_install_applies_advanced_model_selection_to_provider_outputs(self) -> None:
        cases = (
            (
                "opencode",
                Path(".config") / "opencode" / "agents" / "480-architect.md",
                Path(".config") / "opencode" / ".480ai-bootstrap" / "state.json",
                self.advanced_selection("opencode", **{"480-architect": "gemini-flash-high"}),
                "480-architect",
                ("model: google/gemini-3-flash-preview", "reasoningEffort: high"),
            ),
            (
                "claude",
                Path(".claude") / "agents" / "480-architect.md",
                Path(".claude") / ".480ai-bootstrap" / "state.json",
                self.advanced_selection("claude", **{"480-architect": "sonnet-max"}),
                "480-architect",
                ("model: claude-sonnet-4-6", "effort: max"),
            ),
            (
                "codex",
                Path(".codex") / "agents" / "480-developer.toml",
                Path(".codex") / ".480ai-bootstrap" / "state.json",
                self.advanced_selection("codex", **{"480-developer": "spark-medium"}),
                "480-developer",
                ('model = "gpt-5.3-codex-spark"', 'model_reasoning_effort = "medium"'),
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                for target, relative_path, state_relative_path, model_selection, selected_role, expected_markers in cases:
                    with self.subTest(target=target):
                        manage_agents.install(
                            target=target,
                            scope="user",
                            activate_default=False,
                            model_selection=model_selection,
                        )
                        installed = home / relative_path
                        contents = installed.read_text(encoding="utf-8")
                        source = provider_agents_source_dir(target) / installed.name
                        state = self.read_json(home / state_relative_path)
                        self.assertEqual(state["model_selection"]["mode"], "advanced")
                        self.assertEqual(
                            state["model_selection"]["role_options"][selected_role],
                            model_selection.role_options[selected_role],
                        )
                        self.assertNotEqual(contents, source.read_text(encoding="utf-8"))
                        for marker in expected_markers:
                            self.assertIn(marker, contents)
                        manage_agents.uninstall(target=target, scope="user")
                        self.assertFalse(installed.exists())

    def test_noninteractive_reinstall_preserves_persisted_advanced_model_selection(self) -> None:
        cases = (
            (
                "opencode",
                Path(".config") / "opencode" / "agents" / "480-architect.md",
                self.advanced_selection("opencode", **{"480-architect": "gemini-flash-high"}),
                ("model: google/gemini-3-flash-preview", "reasoningEffort: high"),
            ),
            (
                "claude",
                Path(".claude") / "agents" / "480-architect.md",
                self.advanced_selection("claude", **{"480-architect": "sonnet-max"}),
                ("model: claude-sonnet-4-6", "effort: max"),
            ),
            (
                "codex",
                Path(".codex") / "agents" / "480-developer.toml",
                self.advanced_selection("codex", **{"480-developer": "spark-medium"}),
                ('model = "gpt-5.3-codex-spark"', 'model_reasoning_effort = "medium"'),
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                for target, relative_path, model_selection, expected_markers in cases:
                    with self.subTest(target=target):
                        manage_agents.install(
                            target=target,
                            scope="user",
                            activate_default=False,
                            model_selection=model_selection,
                        )

                        self.run_command(home, "install", "--target", target, "--scope", "user")

                        contents = (home / relative_path).read_text(encoding="utf-8")
                        for marker in expected_markers:
                            self.assertIn(marker, contents)

                        manage_agents.uninstall(target=target, scope="user")

    def test_reinstall_reapplies_managed_default_activation_and_keeps_uninstall_restore_contract(self) -> None:
        cases = (
            ("opencode", Path(".config") / "opencode" / "opencode.json", "default_agent"),
            ("claude", Path(".claude") / "settings.json", "agent"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for target, relative_path, config_key in cases:
                with self.subTest(target=target):
                    config_path = home / relative_path
                    self.write_json(config_path, {config_key: "custom-agent", "theme": "light"})

                    self.run_command(
                        home,
                        "install",
                        "--target",
                        target,
                        "--scope",
                        "user",
                        "--activate-default",
                    )

                    self.write_json(config_path, {config_key: "drifted-agent", "theme": "light"})

                    self.run_command(home, "install", "--target", target, "--scope", "user")

                    self.assertEqual(
                        self.read_json(config_path),
                        {config_key: "480-architect", "theme": "light"},
                    )

                    self.run_command(home, "uninstall", "--target", target, "--scope", "user")

                    self.assertEqual(
                        self.read_json(config_path),
                        {config_key: "custom-agent", "theme": "light"},
                    )

    def test_codex_reinstall_refreshes_managed_guidance_block_and_preserves_user_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            guidance_path = home / ".codex" / "AGENTS.md"
            guidance_path.parent.mkdir(parents=True, exist_ok=True)
            guidance_path.write_text(
                "before guidance\n\n"
                f"{installer_core.CODEX_MANAGED_AGENTS_START}\n"
                "stale managed guidance\n"
                f"{installer_core.CODEX_MANAGED_AGENTS_END}\n\n"
                "after guidance\n",
                encoding="utf-8",
            )

            self.run_command(home, "install", "--target", "codex", "--scope", "user")

            self.assertEqual(
                guidance_path.read_text(encoding="utf-8"),
                "before guidance\n\n"
                f"{installer_core.CODEX_MANAGED_AGENTS_START}\n"
                f"{render_agents.render_codex_managed_guidance(agent_bundle.load_bundle())}\n"
                f"{installer_core.CODEX_MANAGED_AGENTS_END}\n\n"
                "after guidance\n",
            )

    def test_cli_accepts_explicit_default_target_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            self.write_json(config_path, {"default_agent": "480-developer"})

            result = subprocess.run(
                ["python3", "-m", MANAGE_AGENTS_MODULE, "install", "--target", "opencode", "--scope", "user"],
                check=False,
                cwd=REPO_ROOT,
                env=self.subprocess_env(home),
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("Installed 480ai OpenCode agents.", result.stdout)
            self.assertEqual(self.read_json(config_path)["default_agent"], "480-architect")

    def test_python_module_manage_agents_install_and_uninstall_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})

            install_result = self.run_module_command_capture(
                home,
                "app.manage_agents",
                "install",
            )

            self.assertEqual(install_result.returncode, 0)
            self.assertIn("Installed 480ai OpenCode agents.", install_result.stdout)
            self.assertEqual(
                self.read_json(config_path),
                {"default_agent": "480-architect", "provider": {"x": 1}},
            )
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.assertTrue(state_path.exists())
            for name in AGENTS:
                installed = home / ".config" / "opencode" / "agents" / f"{name}.md"
                source = provider_agents_source_dir("opencode") / f"{name}.md"
                self.assertEqual(installed.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

            uninstall_result = self.run_module_command_capture(
                home,
                "app.manage_agents",
                "uninstall",
            )

            self.assertEqual(uninstall_result.returncode, 0)
            self.assertIn("Uninstalled 480ai OpenCode agents.", uninstall_result.stdout)
            self.assertEqual(
                self.read_json(config_path),
                {"default_agent": "480-developer", "provider": {"x": 1}},
            )
            self.assertFalse(state_path.exists())
            for name in AGENTS:
                self.assertFalse((home / ".config" / "opencode" / "agents" / f"{name}.md").exists())

    def test_python_module_render_agents_check_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            result = self.run_module_command_capture(home, "app.render_agents", "check")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("Agent outputs are up to date.", result.stdout)

    def test_python_module_render_agents_write_then_check_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_result = self.run_module_command_capture(home, "app.render_agents", "write")
            check_result = self.run_module_command_capture(home, "app.render_agents", "check")

        self.assertEqual(write_result.returncode, 0)
        self.assertEqual(write_result.stdout, "")
        self.assertEqual(write_result.stderr, "")
        self.assertEqual(check_result.returncode, 0)
        self.assertEqual(check_result.stderr, "")
        self.assertIn("Agent outputs are up to date.", check_result.stdout)

    def test_install_main_interactively_uses_claude_default_activation_no(self) -> None:
        stdin = TTYStringIO("2\n\n\n\n\n\n")
        stdout = TTYStringIO()

        with (
            self.patched_detected_interactive_providers("opencode", "claude", "codex"),
            mock.patch.object(manage_agents.sys, "stdin", stdin),
            mock.patch.object(manage_agents.sys, "stdout", stdout),
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(manage_agents, "install") as install_mock,
        ):
            result = manage_agents.main(["manage_agents.py", "install"])

        self.assertEqual(result, 0)
        install_mock.assert_called_once_with(
            target="claude",
            scope="user",
            activate_default=False,
            enable_teams=False,
            desktop_notifications=False,
        )

    def test_supports_install_tui_rejects_missing_terminal_capabilities(self) -> None:
        stdin = TTYStringIOWithFileno()
        stdout = TTYStringIOWithFileno()

        with mock.patch.dict(os.environ, {"TERM": "dumb"}, clear=True):
            self.assertFalse(manage_agents.supports_install_tui(input_stream=stdin, output=stdout))

    def test_prompt_install_options_uses_basic_prompt_when_tui_is_unavailable(self) -> None:
        stdin = TTYStringIO()
        stdout = TTYStringIO()
        expected = manage_agents.InstallOptions(
            providers=(
                manage_agents.ProviderInstallRequest(
                    target="opencode",
                    scope="user",
                    activate_default=True,
                ),
            )
        )

        with (
            mock.patch.object(manage_agents, "supports_install_tui", return_value=False),
            mock.patch.object(manage_agents, "prompt_install_options_basic", return_value=expected) as basic_mock,
            mock.patch.object(manage_agents, "prompt_install_options_tui") as tui_mock,
        ):
            actual = manage_agents.prompt_install_options(input_stream=stdin, output=stdout)

        self.assertEqual(actual, expected)
        basic_mock.assert_called_once_with(input_stream=stdin, output=stdout)
        tui_mock.assert_not_called()

    def test_prompt_install_options_uses_tui_when_supported(self) -> None:
        stdin = TTYStringIOWithFileno()
        stdout = TTYStringIOWithFileno()
        expected = manage_agents.InstallOptions(
            providers=(
                manage_agents.ProviderInstallRequest(
                    target="claude",
                    scope="project",
                    activate_default=False,
                ),
            )
        )

        with (
            mock.patch.object(manage_agents, "supports_install_tui", return_value=True),
            mock.patch.object(manage_agents, "prompt_install_options_tui", return_value=expected) as tui_mock,
            mock.patch.object(manage_agents, "prompt_install_options_basic") as basic_mock,
        ):
            actual = manage_agents.prompt_install_options(input_stream=stdin, output=stdout)

        self.assertEqual(actual, expected)
        tui_mock.assert_called_once_with()
        basic_mock.assert_not_called()

    def test_prompt_install_options_falls_back_when_tui_is_unavailable(self) -> None:
        stdin = TTYStringIOWithFileno()
        stdout = TTYStringIOWithFileno()
        expected = manage_agents.InstallOptions(
            providers=(
                manage_agents.ProviderInstallRequest(
                    target="opencode",
                    scope="user",
                    activate_default=True,
                ),
            )
        )

        with (
            mock.patch.object(manage_agents, "supports_install_tui", return_value=True),
            mock.patch.object(
                manage_agents,
                "prompt_install_options_tui",
                side_effect=manage_agents.InstallTuiUnavailableError("init failed"),
            ),
            mock.patch.object(manage_agents, "prompt_install_options_basic", return_value=expected) as basic_mock,
        ):
            actual = manage_agents.prompt_install_options(input_stream=stdin, output=stdout)

        self.assertEqual(actual, expected)
        basic_mock.assert_called_once_with(input_stream=stdin, output=stdout)

    def test_prompt_install_options_does_not_hide_tui_runtime_errors(self) -> None:
        stdin = TTYStringIOWithFileno()
        stdout = TTYStringIOWithFileno()

        with (
            mock.patch.object(manage_agents, "supports_install_tui", return_value=True),
            mock.patch.object(manage_agents, "prompt_install_options_tui", side_effect=RuntimeError("boom")),
            mock.patch.object(manage_agents, "prompt_install_options_basic") as basic_mock,
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                manage_agents.prompt_install_options(input_stream=stdin, output=stdout)

        basic_mock.assert_not_called()

    def test_install_main_without_tty_or_inputs_uses_default_noninteractive_path(self) -> None:
        stdin = io.StringIO()
        stdout = io.StringIO()

        with (
            mock.patch.object(manage_agents.sys, "stdin", stdin),
            mock.patch.object(manage_agents.sys, "stdout", stdout),
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(manage_agents, "install") as install_mock,
            mock.patch.object(manage_agents, "prompt_install_options") as prompt_mock,
        ):
            result = manage_agents.main(["manage_agents.py", "install"])

        self.assertEqual(result, 0)
        prompt_mock.assert_not_called()
        install_mock.assert_called_once_with(
            target="opencode",
            scope="user",
            activate_default=None,
        )

    def test_prompt_install_options_blocks_unsupported_opencode_project_and_allows_opt_out(self) -> None:
        stdin = TTYStringIO("1\n2\n\n2\n\n\n")
        stdout = TTYStringIO()

        with self.patched_detected_interactive_providers("opencode", "claude", "codex"):
            install_options = manage_agents.prompt_install_options(
                input_stream=stdin,
                output=stdout,
            )

        request = install_options.providers[0]
        self.assertEqual((request.target, request.scope, request.activate_default), ("opencode", "user", False))
        self.assertIsNone(request.model_selection)
        output = stdout.getvalue()
        self.assertIn("project - unsupported", output)
        self.assertIn("project scope is not yet supported for this target.", output)
        self.assertIn("Activate the default agent now?", output)
        self.assertIn("1) Yes", output)
        self.assertIn("2) No", output)
        self.assertNotIn("agent teams experimental flag", output)
        self.assertIn("Choose the model mode.", output)

    def test_prompt_install_options_hides_activation_for_codex(self) -> None:
        stdin = TTYStringIO("3\n2\n\n\n")
        stdout = TTYStringIO()

        with self.patched_detected_interactive_providers("opencode", "claude", "codex"):
            install_options = manage_agents.prompt_install_options(
                input_stream=stdin,
                output=stdout,
            )

        request = install_options.providers[0]
        self.assertEqual((request.target, request.scope, request.activate_default), ("codex", "project", None))
        self.assertIsNone(request.model_selection)
        self.assertNotIn("Activate the default agent now?", stdout.getvalue())
        self.assertNotIn("agent teams experimental flag", stdout.getvalue())

    def test_prompt_install_options_shows_teams_prompt_only_for_claude(self) -> None:
        stdin = TTYStringIO("2\n\n\n\n\n\n")
        stdout = TTYStringIO()

        with self.patched_detected_interactive_providers("opencode", "claude", "codex"):
            install_options = manage_agents.prompt_install_options(
                input_stream=stdin,
                output=stdout,
            )

        request = install_options.providers[0]
        self.assertEqual(request.target, "claude")
        self.assertFalse(request.enable_teams)
        self.assert_claude_teams_prompt_shown(stdout.getvalue())

    def test_prompt_install_options_collects_advanced_role_model_choices(self) -> None:
        stdin = TTYStringIO("2\n\n\n\n\n2\n2\n\n\n\n\n")
        stdout = TTYStringIO()

        with self.patched_detected_interactive_providers("opencode", "claude", "codex"):
            install_options = manage_agents.prompt_install_options(
                input_stream=stdin,
                output=stdout,
            )

        request = install_options.providers[0]
        self.assertEqual((request.target, request.scope, request.activate_default), ("claude", "user", False))
        self.assertFalse(request.enable_teams)
        assert request.model_selection is not None
        self.assertEqual(request.model_selection.mode, "advanced")
        self.assertEqual(request.model_selection.role_options["480-architect"], "sonnet-max")
        self.assertEqual(request.model_selection.role_options["480-developer"], "sonnet-medium")
        output = stdout.getvalue()
        self.assertIn("Advanced mode: choose curated models by role.", output)
        self.assertIn("Choose the model for 480-architect.", output)
        self.assertIn("Choose the model for 480-code-scanner.", output)

    def test_tui_prompt_multi_select_supports_multiple_providers(self) -> None:
        fake_curses = FakeCursesModule(screen=FakeCursesScreen([10]))
        rendered_screens: list[list[str]] = []

        with (
            mock.patch.dict(sys.modules, {"curses": fake_curses}),
            mock.patch.object(
                manage_agents,
                "tui_render_screen",
                side_effect=lambda *args, **kwargs: rendered_screens.append(list(kwargs["lines"])),
            ),
        ):
            selected = manage_agents.tui_prompt_multi_select(
                fake_curses.screen,
                title="provider selection",
                choices=manage_agents.TARGET_CHOICES,
                default_values=tuple(choice.value for choice in manage_agents.TARGET_CHOICES),
            )

        self.assertEqual(selected, tuple(choice.value for choice in manage_agents.TARGET_CHOICES))
        first_render = rendered_screens[0]
        self.assertTrue(any("OpenCode" in line and "[x]" in line for line in first_render))
        self.assertTrue(any("Claude Code" in line and "[x]" in line for line in first_render))
        self.assertTrue(any("Codex CLI" in line and "[x]" in line for line in first_render))

    def test_tui_prompt_multi_select_blocks_empty_selection_until_provider_is_selected(self) -> None:
        fake_curses = FakeCursesModule(screen=FakeCursesScreen([ord(" "), 10, ord(" "), 10]))
        rendered_errors: list[str | None] = []

        with (
            mock.patch.dict(sys.modules, {"curses": fake_curses}),
            mock.patch.object(manage_agents, "tui_render_screen", side_effect=lambda *args, **kwargs: rendered_errors.append(kwargs.get("error"))),
        ):
            selected = manage_agents.tui_prompt_multi_select(
                fake_curses.screen,
                title="provider selection",
                choices=manage_agents.TARGET_CHOICES,
                default_values=("opencode",),
            )

        self.assertEqual(selected, ("opencode",))
        self.assertTrue(any(error for error in rendered_errors))
        self.assertIn("Select at least one provider.", rendered_errors)
        self.assertIsNone(rendered_errors[-1])

    def test_prompt_install_options_tui_asks_each_provider_in_order_and_renders_summary(self) -> None:
        keys = [10] * 16
        fake_curses = FakeCursesModule(screen=FakeCursesScreen(keys))
        rendered_screens: list[tuple[str, list[str], str]] = []

        def record_screen(*_args, **kwargs):
            rendered_screens.append((kwargs["title"], list(kwargs["lines"]), kwargs["footer"]))
            return (len(kwargs["lines"]), 20)

        with (
            self.patched_detected_interactive_providers("opencode", "claude", "codex"),
            mock.patch.dict(sys.modules, {"curses": fake_curses}),
            mock.patch.object(manage_agents, "tui_render_screen", side_effect=record_screen),
        ):
            install_options = manage_agents.prompt_install_options_tui()

        assert fake_curses.screen is not None
        self.assertTrue(fake_curses.screen.keypad_enabled)
        self.assertEqual([request.target for request in install_options.providers], ["opencode", "claude", "codex"])

        provider_title, provider_lines, provider_footer = rendered_screens[0]
        self.assertIn("provider", provider_title)
        self.assertIn("Select one or more providers.", provider_lines)
        self.assertIn("Press Space to select or deselect.", provider_lines)
        checked_provider_lines = [line for line in provider_lines if "[" in line and "]" in line]
        self.assertTrue(any("OpenCode" in line and "[x]" in line for line in checked_provider_lines))
        self.assertTrue(any("Claude Code" in line and "[x]" in line for line in checked_provider_lines))
        self.assertTrue(any("Codex CLI" in line and "[x]" in line for line in checked_provider_lines))
        self.assertEqual(
            provider_footer,
            "Space: Select/Deselect | Enter: Next | Up/Down Arrow or j/k: Move",
        )

    def test_prompt_install_options_tui_scope_back_moves_to_previous_provider_last_step(self) -> None:
        keys = [
            FakeCursesModule.KEY_DOWN,
            FakeCursesModule.KEY_DOWN,
            ord(" "),
            10,
            10,
            10,
            10,
            10,
            FakeCursesModule.KEY_LEFT,
            FakeCursesModule.KEY_DOWN,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
        ]
        fake_curses = FakeCursesModule(screen=FakeCursesScreen(keys))

        with (
            self.patched_detected_interactive_providers("opencode", "claude", "codex"),
            mock.patch.dict(sys.modules, {"curses": fake_curses}),
        ):
            install_options = manage_agents.prompt_install_options_tui()

        self.assertEqual([request.target for request in install_options.providers], ["opencode", "claude"])
        self.assertIsNotNone(install_options.providers[0].model_selection)
        assert install_options.providers[0].model_selection is not None
        self.assertEqual(install_options.providers[0].model_selection.mode, "advanced")
        self.assertIsNone(install_options.providers[1].model_selection)

    def test_prompt_install_options_tui_first_scope_back_keeps_selected_providers(self) -> None:
        keys = [
            FakeCursesModule.KEY_DOWN,
            FakeCursesModule.KEY_DOWN,
            ord(" "),
            10,
            FakeCursesModule.KEY_LEFT,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
        ] + [10] * 40
        fake_curses = FakeCursesModule(screen=FakeCursesScreen(keys))

        with (
            self.patched_detected_interactive_providers("opencode", "claude", "codex"),
            mock.patch.dict(sys.modules, {"curses": fake_curses}),
        ):
            install_options = manage_agents.prompt_install_options_tui()

        self.assertEqual([request.target for request in install_options.providers], ["opencode", "claude"])
        self.assertEqual(install_options.providers[0].scope, "user")
        self.assertEqual(install_options.providers[1].scope, "user")
        self.assertFalse(install_options.providers[1].activate_default)
        self.assertFalse(install_options.providers[1].enable_teams)

    def test_prompt_install_options_tui_back_from_advanced_role_can_switch_to_recommended(self) -> None:
        keys = [
            10,
            10,
            10,
            10,
            FakeCursesModule.KEY_DOWN,
            10,
            FakeCursesModule.KEY_LEFT,
            FakeCursesModule.KEY_UP,
            10,
            10,
        ]
        fake_curses = FakeCursesModule(screen=FakeCursesScreen(keys))

        with (
            self.patched_detected_interactive_providers("opencode"),
            mock.patch.dict(sys.modules, {"curses": fake_curses}),
        ):
            install_options = manage_agents.prompt_install_options_tui()

        self.assertEqual([request.target for request in install_options.providers], ["opencode"])
        self.assertIsNone(install_options.providers[0].model_selection)

    def test_prompt_install_options_tui_summary_back_returns_to_last_step_and_keeps_state(self) -> None:
        keys = [
            10,
            10,
            10,
            10,
            10,
            FakeCursesModule.KEY_LEFT,
            FakeCursesModule.KEY_DOWN,
            10,
            *([10] * len(agent_bundle.load_bundle())),
            10,
        ]
        fake_curses = FakeCursesModule(screen=FakeCursesScreen(keys))
        rendered_screens: list[tuple[str, list[str], str]] = []

        def record_screen(*_args, **kwargs):
            rendered_screens.append((kwargs["title"], list(kwargs["lines"]), kwargs["footer"]))
            return (len(kwargs["lines"]), 20)

        with (
            self.patched_detected_interactive_providers("opencode"),
            mock.patch.dict(sys.modules, {"curses": fake_curses}),
            mock.patch.object(manage_agents, "tui_render_screen", side_effect=record_screen),
        ):
            install_options = manage_agents.prompt_install_options_tui()

        self.assertEqual([request.target for request in install_options.providers], ["opencode"])
        self.assertEqual(install_options.providers[0].scope, "user")
        self.assertTrue(install_options.providers[0].activate_default)
        self.assertIsNotNone(install_options.providers[0].model_selection)
        assert install_options.providers[0].model_selection is not None
        self.assertEqual(install_options.providers[0].model_selection.mode, "advanced")

        summary_titles = [title for title, _lines, _footer in rendered_screens if title == "Install summary"]
        self.assertGreaterEqual(len(summary_titles), 2)
        summary_footers = [footer for title, _lines, footer in rendered_screens if title == "Install summary"]
        self.assertIn("Enter: Start install | Left Arrow: Back", summary_footers)

    def test_prompt_install_options_tui_does_not_preload_advanced_defaults_for_recommended_only_provider(self) -> None:
        fake_provider = SimpleNamespace(
            label="Fake Provider",
            supported_scopes=("user",),
            default_activation_default=None,
            supported_model_selection_modes=lambda: ("recommended",),
            default_advanced_role_model_option=mock.Mock(side_effect=AssertionError("advanced defaults should be lazy")),
        )
        fake_choices = (manage_agents.Choice(value="fake", label="Fake Provider"),)
        fake_bundle = (SimpleNamespace(identifier="480-architect", display_name="480 Architect"),)
        fake_curses = FakeCursesModule(screen=FakeCursesScreen([10, 10, 10, 10, 10, 10]))

        with (
            mock.patch.dict(sys.modules, {"curses": fake_curses}),
            mock.patch.object(manage_agents, "required_interactive_provider_choices", return_value=fake_choices),
            mock.patch.object(manage_agents, "interactive_default_target", return_value="fake"),
            mock.patch.object(manage_agents, "get_provider", return_value=fake_provider),
            mock.patch.object(manage_agents, "load_bundle", return_value=fake_bundle),
            mock.patch.object(
                manage_agents,
                "model_selection_schema_for_target",
                return_value=SimpleNamespace(supported_modes=("recommended",)),
            ),
        ):
            install_options = manage_agents.prompt_install_options_tui()

        self.assertEqual([request.target for request in install_options.providers], ["fake"])
        self.assertIsNone(install_options.providers[0].model_selection)
        fake_provider.default_advanced_role_model_option.assert_not_called()

    def test_detected_provider_choices_filters_by_cli_binary_and_keeps_config_dir_as_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir(parents=True, exist_ok=True)

            def fake_which(binary: str) -> str | None:
                return {"claude": "/usr/bin/claude", "codex": "/usr/bin/codex"}.get(binary)

            with (
                mock.patch("pathlib.Path.home", return_value=home),
                mock.patch.object(manage_agents.shutil, "which", side_effect=fake_which),
            ):
                choices = manage_agents.detected_provider_choices()

        self.assertEqual([choice.value for choice in choices], ["claude", "codex"])
        self.assertIn("Config directory detected", choices[0].note)
        self.assertIn(str(home / ".claude"), choices[0].note)
        self.assertEqual(choices[1].note, "")

    def test_prompt_install_options_only_shows_detected_providers_in_basic_mode(self) -> None:
        stdin = TTYStringIO("1\n\n\n\n\n\n")
        stdout = TTYStringIO()
        detected_choices = (
            manage_agents.Choice(value="claude", label="Claude Code"),
            manage_agents.Choice(value="codex", label="Codex CLI"),
        )

        with mock.patch.object(manage_agents, "detected_provider_choices", return_value=detected_choices):
            install_options = manage_agents.prompt_install_options(input_stream=stdin, output=stdout)

        request = install_options.providers[0]
        self.assertEqual((request.target, request.scope), ("claude", "user"))
        output = stdout.getvalue()
        self.assertIn("Claude Code", output)
        self.assertIn("Codex CLI", output)
        self.assertNotIn("OpenCode", output)

    def test_prompt_install_options_stops_with_clear_message_when_no_provider_is_detected(self) -> None:
        stdin = TTYStringIO()
        stdout = TTYStringIO()

        with mock.patch.object(manage_agents, "detected_provider_choices", return_value=()):
            with self.assertRaises(SystemExit) as exc:
                manage_agents.prompt_install_options(input_stream=stdin, output=stdout)

        message = str(exc.exception)
        self.assertIn("PATH", message)
        self.assertIn("opencode, claude, codex", message)
        self.assertIn("--target <provider>", message)

    def test_install_main_keeps_explicit_target_contract_without_detection(self) -> None:
        stdin = TTYStringIO()
        stdout = TTYStringIO()

        with (
            mock.patch.object(manage_agents.sys, "stdin", stdin),
            mock.patch.object(manage_agents.sys, "stdout", stdout),
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(manage_agents, "detected_provider_choices", return_value=()),
            mock.patch.object(manage_agents, "install") as install_mock,
            mock.patch.object(manage_agents, "prompt_install_options") as prompt_mock,
        ):
            result = manage_agents.main(["manage_agents.py", "install", "--target", "codex", "--scope", "project"])

        self.assertEqual(result, 0)
        prompt_mock.assert_not_called()
        install_mock.assert_called_once_with(target="codex", scope="project", activate_default=None)

    def test_build_install_summary_lines_lists_each_provider_selection(self) -> None:
        install_options = manage_agents.InstallOptions(
            providers=(
                manage_agents.ProviderInstallRequest(
                    target="opencode",
                    scope="user",
                    activate_default=True,
                    desktop_notifications=True,
                ),
                manage_agents.ProviderInstallRequest(
                    target="codex",
                    scope="project",
                    activate_default=None,
                    desktop_notifications=False,
                    model_selection=self.advanced_selection("codex", **{"480-architect": "spark-high"}),
                ),
                manage_agents.ProviderInstallRequest(
                    target="claude",
                    scope="user",
                    activate_default=False,
                    enable_teams=False,
                    desktop_notifications=True,
                ),
            )
        )

        lines = manage_agents.build_install_summary_lines(install_options.providers)

        self.assert_summary_contains_provider(
            lines,
            provider_label="OpenCode",
            scope="user",
            activate_default=True,
            desktop_notifications=True,
            model_mode="recommended",
        )
        self.assert_summary_contains_provider(
            lines,
            provider_label="Codex CLI",
            scope="project",
            activate_default=None,
            desktop_notifications=False,
            model_mode="advanced",
        )
        claude_block = "\n".join(self.summary_provider_block(lines, "Claude Code"))
        self.assertRegex(claude_block, r"agent teams:\s*no")
        self.assertIn("desktop notifications: yes", claude_block)
        codex_block = "\n".join(self.summary_provider_block(lines, "Codex CLI"))
        self.assertIn("480-architect: spark-high", codex_block)
        self.assertIn("480-developer: gpt-5.4-medium", codex_block)

    def test_tui_prompt_review_allows_scrolling_to_end_of_long_install_summary(self) -> None:
        install_options = manage_agents.InstallOptions(
            providers=(
                manage_agents.ProviderInstallRequest(
                    target="opencode",
                    scope="user",
                    activate_default=True,
                ),
                manage_agents.ProviderInstallRequest(
                    target="claude",
                    scope="project",
                    activate_default=False,
                    model_selection=self.advanced_selection("claude", **{"480-architect": "sonnet-max"}),
                ),
                manage_agents.ProviderInstallRequest(
                    target="codex",
                    scope="project",
                    activate_default=None,
                    model_selection=self.advanced_selection("codex", **{"480-architect": "spark-high"}),
                ),
            )
        )
        summary_lines = manage_agents.build_install_summary_lines(install_options.providers)
        layout_probe = FakeCursesScreen([], height=8, width=80)
        rendered_lines, max_body_rows = manage_agents.tui_rendered_body_lines(layout_probe, summary_lines)
        max_scroll_offset = max(0, len(rendered_lines) - max_body_rows)
        fake_curses = FakeCursesModule(
            screen=FakeCursesScreen(
                [FakeCursesModule.KEY_DOWN] * (max_scroll_offset + 3) + [10],
                height=8,
                width=80,
            )
        )

        with mock.patch.dict(sys.modules, {"curses": fake_curses}):
            manage_agents.tui_prompt_review(
                fake_curses.screen,
                title="Install summary",
                lines=summary_lines,
                footer="Enter: start install",
            )

        assert fake_curses.screen is not None
        first_frame = fake_curses.screen.frames[0]
        last_frame = fake_curses.screen.frames[-1]
        visible_first = [first_frame.get(row, "") for row in range(2, 2 + max_body_rows)]
        visible_last = [last_frame.get(row, "") for row in range(2, 2 + max_body_rows)]
        self.assertEqual(visible_first, rendered_lines[:max_body_rows])
        self.assertEqual(visible_last, rendered_lines[max_scroll_offset : max_scroll_offset + max_body_rows])
        self.assertIn("Claude Code", "\n".join(summary_lines))
        self.assertIn("Codex CLI", "\n".join(summary_lines))

    def test_install_main_runs_multiple_provider_requests_sequentially(self) -> None:
        install_options = manage_agents.InstallOptions(
            providers=(
                manage_agents.ProviderInstallRequest(
                    target="opencode",
                    scope="user",
                    activate_default=True,
                ),
                manage_agents.ProviderInstallRequest(
                    target="claude",
                    scope="project",
                    activate_default=False,
                    enable_teams=True,
                    desktop_notifications=True,
                    model_selection=self.advanced_selection("claude", **{"480-architect": "sonnet-max"}),
                ),
            )
        )

        with (
            mock.patch.object(manage_agents, "should_prompt_install", return_value=True),
            mock.patch.object(manage_agents, "prompt_install_options", return_value=install_options),
            mock.patch.object(manage_agents, "install") as install_mock,
        ):
            result = manage_agents.main(["manage_agents.py", "install"])

        self.assertEqual(result, 0)
        self.assertEqual(install_mock.call_count, 2)
        first_call = install_mock.call_args_list[0].kwargs
        second_call = install_mock.call_args_list[1].kwargs
        self.assertEqual(first_call, {"target": "opencode", "scope": "user", "activate_default": True})
        self.assertEqual(second_call["target"], "claude")
        self.assertEqual(second_call["scope"], "project")
        self.assertEqual(second_call["activate_default"], False)
        self.assertTrue(second_call["enable_teams"])
        self.assertTrue(second_call["desktop_notifications"])
        self.assertEqual(second_call["model_selection"].role_options["480-architect"], "sonnet-max")

    def test_install_main_uses_bootstrap_env_without_prompting(self) -> None:
        stdin = TTYStringIO()
        stdout = TTYStringIO()

        with (
            mock.patch.object(manage_agents.sys, "stdin", stdin),
            mock.patch.object(manage_agents.sys, "stdout", stdout),
            mock.patch.dict(
                os.environ,
                {
                    "BOOTSTRAP_TARGET": "claude",
                    "BOOTSTRAP_SCOPE": "project",
                    "BOOTSTRAP_ACTIVATE_DEFAULT": "yes",
                    "BOOTSTRAP_DESKTOP_NOTIFY": "yes",
                },
                clear=True,
            ),
            mock.patch.object(manage_agents, "install") as install_mock,
            mock.patch.object(manage_agents, "prompt_install_options") as prompt_mock,
        ):
            result = manage_agents.main(["manage_agents.py", "install"])

        self.assertEqual(result, 0)
        prompt_mock.assert_not_called()
        install_mock.assert_called_once_with(
            target="claude",
            scope="project",
            activate_default=True,
            desktop_notifications=True,
        )

    def test_install_main_uses_advanced_model_env_without_prompting(self) -> None:
        stdin = TTYStringIO()
        stdout = TTYStringIO()

        with (
            mock.patch.object(manage_agents.sys, "stdin", stdin),
            mock.patch.object(manage_agents.sys, "stdout", stdout),
            mock.patch.dict(
                os.environ,
                {
                    "BOOTSTRAP_TARGET": "codex",
                    "BOOTSTRAP_SCOPE": "user",
                    "BOOTSTRAP_MODEL_MODE": "advanced",
                    "BOOTSTRAP_ROLE_MODEL_CHOICES": "480-architect=spark-high",
                },
                clear=True,
            ),
            mock.patch.object(manage_agents, "install") as install_mock,
            mock.patch.object(manage_agents, "prompt_install_options") as prompt_mock,
        ):
            result = manage_agents.main(["manage_agents.py", "install"])

        self.assertEqual(result, 0)
        prompt_mock.assert_not_called()
        install_mock.assert_called_once()
        call_kwargs = install_mock.call_args.kwargs
        self.assertEqual(call_kwargs["target"], "codex")
        self.assertEqual(call_kwargs["scope"], "user")
        self.assertIsNone(call_kwargs["activate_default"])
        model_selection = call_kwargs["model_selection"]
        self.assertEqual(model_selection.mode, "advanced")
        self.assertEqual(model_selection.role_options["480-architect"], "spark-high")
        self.assertEqual(model_selection.role_options["480-developer"], "gpt-5.4-medium")

    def test_parse_role_model_choice_entries_preserves_reviewer2_mini_high_and_migrates_removed_scanner_mini_keys(self) -> None:
        parsed = manage_agents.parse_role_model_choice_entries(
            [
                "480-code-scanner=gpt-5.4-mini-high",
                "480-code-reviewer2=gpt-5.4-mini-high",
            ],
            target="codex",
        )

        self.assertEqual(parsed["480-code-scanner"], "gpt-5.4-low")
        self.assertEqual(parsed["480-code-reviewer2"], "gpt-5.4-mini-high")

        parsed_scanner_medium = manage_agents.parse_role_model_choice_entries(
            ["480-code-scanner=gpt-5.4-mini-medium"],
            target="codex",
        )

        self.assertEqual(parsed_scanner_medium["480-code-scanner"], "gpt-5.4-low")

    def test_parse_role_model_choice_entries_rejects_nonexistent_codex_legacy_mini_role_combo(self) -> None:
        with self.assertRaises(SystemExit) as exc:
            manage_agents.parse_role_model_choice_entries(
                ["480-code-reviewer2=gpt-5.4-mini-medium"],
                target="codex",
            )

        self.assertEqual(
            str(exc.exception),
            "Unsupported advanced Codex CLI model option for 480-code-reviewer2: gpt-5.4-mini-medium",
        )

    def test_noninteractive_reinstall_preserves_reviewer2_mini_high_and_migrates_removed_scanner_mini_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.patched_manage_agents_home(home):
                manage_agents.install(
                    target="codex",
                    scope="user",
                    activate_default=False,
                    model_selection=self.advanced_selection("codex", **{"480-developer": "spark-medium"}),
                )

                state_path = home / ".codex" / ".480ai-bootstrap" / "state.json"
                state = self.read_json(state_path)
                state["model_selection"]["role_options"]["480-code-scanner"] = "gpt-5.4-mini-high"
                state["model_selection"]["role_options"]["480-code-reviewer2"] = "gpt-5.4-mini-high"
                self.write_json(state_path, state)

                self.run_command(home, "install", "--target", "codex", "--scope", "user")

                scanner_contents = (home / ".codex" / "agents" / "480-code-scanner.toml").read_text(encoding="utf-8")
                reviewer2_contents = (home / ".codex" / "agents" / "480-code-reviewer2.toml").read_text(encoding="utf-8")
                migrated_state = self.read_json(state_path)

                self.assertIn('model = "gpt-5.4"', scanner_contents)
                self.assertIn('model_reasoning_effort = "low"', scanner_contents)
                self.assertIn('model = "gpt-5.4-mini"', reviewer2_contents)
                self.assertIn('model_reasoning_effort = "high"', reviewer2_contents)
                self.assertEqual(migrated_state["model_selection"]["role_options"]["480-code-scanner"], "gpt-5.4-low")
                self.assertEqual(migrated_state["model_selection"]["role_options"]["480-code-reviewer2"], "gpt-5.4-mini-high")

    def test_uninstall_main_uses_bootstrap_env(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "BOOTSTRAP_TARGET": "codex",
                    "BOOTSTRAP_SCOPE": "project",
                },
                clear=True,
            ),
            mock.patch.object(manage_agents, "uninstall") as uninstall_mock,
        ):
            result = manage_agents.main(["manage_agents.py", "uninstall"])

        self.assertEqual(result, 0)
        uninstall_mock.assert_called_once_with(target="codex", scope="project")

    def test_verify_main_serializes_verify_output(self) -> None:
        fake_result = {
            "install_state": {"status": "ok"},
            "cleanup_result": {"status": "ok"},
            "general_session_validation": {"status": "ok"},
            "exec_path_result": {"status": "ok"},
            "final_classification": "success",
        }
        stdout = io.StringIO()

        with (
            mock.patch.object(manage_agents, "verify", return_value=fake_result) as verify_mock,
            redirect_stdout(stdout),
        ):
            result = manage_agents.main(["manage_agents.py", "verify", "--target", "codex", "--scope", "user"])

        self.assertEqual(result, 0)
        verify_mock.assert_called_once_with(target="codex", scope="user")
        self.assertEqual(json.loads(stdout.getvalue()), fake_result)

    def test_verify_main_ignores_bootstrap_env_and_uses_codex_user_defaults(self) -> None:
        fake_result = {
            "install_state": {"status": "ok"},
            "cleanup_result": {"status": "ok"},
            "general_session_validation": {"status": "ok"},
            "exec_path_result": {"status": "ok"},
            "final_classification": "success",
        }
        stdout = io.StringIO()

        with (
            mock.patch.dict(
                os.environ,
                {
                    "BOOTSTRAP_TARGET": "opencode",
                    "BOOTSTRAP_SCOPE": "project",
                },
                clear=True,
            ),
            mock.patch.object(manage_agents, "verify", return_value=fake_result) as verify_mock,
            redirect_stdout(stdout),
        ):
            result = manage_agents.main(["manage_agents.py", "verify"])

        self.assertEqual(result, 0)
        verify_mock.assert_called_once_with(target="codex", scope="user")
        self.assertEqual(json.loads(stdout.getvalue()), fake_result)

    def test_install_sh_forwards_noninteractive_cli_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = os.environ.copy()
            env["HOME"] = str(home)

            result = subprocess.run(
                [
                    "sh",
                    str(REPO_ROOT / "install.sh"),
                    "--target",
                    "claude",
                    "--scope",
                    "user",
                    "--activate-default",
                ],
                check=False,
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("Installed 480ai Claude Code agents.", result.stdout)
            self.assertEqual(
                self.read_json(home / ".claude" / "settings.json"),
                {"agent": "480-architect"},
            )

    def test_uninstall_sh_honors_bootstrap_env_target_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_root = home / "work" / "demo-repo"
            project_root.mkdir(parents=True, exist_ok=True)

            self.run_command(home, "install", "--target", "codex", "--scope", "project", cwd=project_root)

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["BOOTSTRAP_TARGET"] = "codex"
            env["BOOTSTRAP_SCOPE"] = "project"
            result = subprocess.run(
                ["sh", str(REPO_ROOT / "uninstall.sh")],
                check=False,
                cwd=project_root,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("Uninstalled 480ai Codex CLI agents.", result.stdout)
            state_path = project_bootstrap_state_paths("codex", "project", project_root, home=home).state_file
            self.assertFalse(state_path.exists())
            for name in CODEX_AGENTS:
                self.assertFalse((project_root / ".codex" / "agents" / f"{name}.toml").exists())

    def test_install_is_idempotent_and_preserves_unrelated_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            self.write_json(config_path, {"model": "test-model", "default_agent": "other"})

            self.run_command(home, "install")
            self.run_command(home, "install")

            config = self.read_json(config_path)
            self.assertEqual(config["model"], "test-model")
            self.assertEqual(config["default_agent"], "480-architect")

            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.assertTrue(state_path.exists())

            for name in AGENTS:
                installed = home / ".config" / "opencode" / "agents" / f"{name}.md"
                source = provider_agents_source_dir("opencode") / f"{name}.md"
                self.assertEqual(installed.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

    def test_opencode_round_trip_removes_bootstrap_created_config_and_agents_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"

            self.run_command(home, "install")

            self.assertTrue(config_path.exists())
            self.assertTrue(agents_dir.exists())

            self.run_command(home, "uninstall")

            self.assertFalse(config_path.exists())
            self.assertFalse(agents_dir.exists())

    def test_repeated_install_then_uninstall_removes_managed_file_and_bootstrap_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            self.write_json(config_path, {"model": "test-model", "default_agent": "480-developer"})
            agents_dir.mkdir(parents=True, exist_ok=True)
            original_architect = "original architect agent\n"
            (agents_dir / "480-architect.md").write_text(original_architect, encoding="utf-8")

            self.run_command(home, "install")
            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            config = self.read_json(config_path)
            self.assertEqual(config["model"], "test-model")
            self.assertEqual(config["default_agent"], "480-developer")
            self.assertFalse((agents_dir / "480-architect.md").exists())
            self.assertFalse(state_path.exists())
            self.assertFalse(backup_path.exists())
            for name in [agent for agent in AGENTS if agent != "480-architect"]:
                self.assertFalse((agents_dir / f"{name}.md").exists())

    def test_install_recovers_invalid_managed_agents_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-architect", "provider": {"x": 1}})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("user architect before recovery\n", encoding="utf-8")
            self.write_json(
                state_path,
                {
                    "version": 1,
                    "managed_agents": ["architect"],
                    "backups": {},
                    "managed": {name: True for name in AGENTS},
                    "managed_file_metadata": {name: None for name in AGENTS},
                    "pending_cleanup": {name: False for name in AGENTS},
                },
            )
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text("stale managed backup\n", encoding="utf-8")

            result = self.run_command_capture(home, "install")

            self.assertEqual(result.returncode, 0)
            state = self.read_json(state_path)
            self.assertEqual(state["managed_agents"], AGENTS)
            self.assertEqual(state["backups"], {"480-architect": "backups/480-architect.md"})
            self.assertEqual(backup_path.read_text(encoding="utf-8"), "user architect before recovery\n")
            self.assertEqual(self.read_json(config_path), {"default_agent": "480-architect", "provider": {"x": 1}})

    def test_install_recovers_malformed_json_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text('{"broken":\n', encoding="utf-8")

            result = self.run_command_capture(home, "install")

            self.assertEqual(result.returncode, 0)
            state = self.read_json(state_path)
            self.assertEqual(state["managed_agents"], AGENTS)
            self.assertEqual(self.read_json(config_path), {"default_agent": "480-architect", "provider": {"x": 1}})

    def test_install_recovers_non_object_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text("[]\n", encoding="utf-8")

            result = self.run_command_capture(home, "install")

            self.assertEqual(result.returncode, 0)
            state = self.read_json(state_path)
            self.assertEqual(state["managed_agents"], AGENTS)
            self.assertEqual(self.read_json(config_path), {"default_agent": "480-architect", "provider": {"x": 1}})

    def test_recovery_path_keeps_invalid_state_file_until_final_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            target = resolve_install_target("opencode", "user", home=home)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = target.paths.state_file
            invalid_state_contents = "[]\n"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(invalid_state_contents, encoding="utf-8")

            with mock.patch("app.installer_core.write_target_config", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    installer_core.install(target, provider_agents_source_dir("opencode"), AGENTS)

            self.assertEqual(state_path.read_text(encoding="utf-8"), invalid_state_contents)

    def test_recovery_with_legacy_migration_change_keeps_invalid_state_file_until_final_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            _config_path, state_path = self.seed_legacy_claude_install(home)
            target = resolve_install_target("claude", "user", home=home)
            state = self.read_json(state_path)
            state["managed_agents"] = ["broken-agent"]
            self.write_json(state_path, state)
            invalid_state_contents = state_path.read_text(encoding="utf-8")

            with mock.patch("app.installer_core.write_state", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    installer_core.install(target, provider_agents_source_dir("claude"), CLAUDE_AGENTS)

            self.assertEqual(state_path.read_text(encoding="utf-8"), invalid_state_contents)

    def test_codex_install_recovers_invalid_bootstrap_state_and_preserves_user_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".codex" / "config.toml"
            guidance_path = home / ".codex" / "AGENTS.md"
            state_path = home / ".codex" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".codex" / ".480ai-bootstrap" / "backups" / "480-developer.toml"
            developer_path = home / ".codex" / "agents" / "480-developer.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text('model = "gpt-5.4"\n', encoding="utf-8")
            guidance_path.write_text("keep user guidance\n", encoding="utf-8")
            developer_path.parent.mkdir(parents=True, exist_ok=True)
            developer_path.write_text("user developer before recovery\n", encoding="utf-8")
            self.write_json(
                state_path,
                {
                    "version": 1,
                    "managed_agents": ["broken-agent"],
                    "backups": {},
                    "managed": {name: True for name in CODEX_AGENTS},
                    "managed_file_metadata": {name: None for name in CODEX_AGENTS},
                    "pending_cleanup": {name: False for name in CODEX_AGENTS},
                },
            )
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text("stale codex backup\n", encoding="utf-8")

            result = self.run_command_capture(home, "install", "--target", "codex", "--scope", "user")

            self.assertEqual(result.returncode, 0)
            state = self.read_json(state_path)
            self.assertEqual(state["managed_agents"], CODEX_AGENTS)
            self.assertEqual(state["backups"], {"480-developer": "backups/480-developer.toml"})
            self.assertEqual(backup_path.read_text(encoding="utf-8"), "user developer before recovery\n")
            self.assertIn("keep user guidance\n", guidance_path.read_text(encoding="utf-8"))
            parsed_config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed_config["model"], "gpt-5.4")
            self.assertTrue(parsed_config["features"]["multi_agent"])
            self.assertEqual(parsed_config["agents"]["max_depth"], 2)

    def test_uninstall_removes_user_modified_managed_agent_and_preserves_new_default_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})

            self.run_command(home, "install")

            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            architect_path.write_text("user modified architect\n", encoding="utf-8")
            self.write_json(config_path, {"default_agent": "custom-agent", "provider": {"x": 1}})

            self.run_command(home, "uninstall")

            config = self.read_json(config_path)
            self.assertEqual(config["default_agent"], "custom-agent")
            self.assertEqual(config["provider"], {"x": 1})
            self.assertFalse(architect_path.exists())

    def test_install_fails_before_overwriting_agents_when_config_is_not_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            existing_architect = "existing architect agent\n"
            architect_path = agents_dir / "480-architect.md"
            architect_path.write_text(existing_architect, encoding="utf-8")
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("[]\n", encoding="utf-8")

            result = self.run_command_capture(home, "install")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Expected JSON object", result.stderr)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), existing_architect)
            self.assertFalse((home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json").exists())

    def test_retry_after_failed_final_install_state_write_recovers_without_fake_backup_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            architect_source = (provider_agents_source_dir("opencode") / "480-architect.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"default_agent": "480-developer"})

            with self.patched_manage_agents_home(home):
                target = manage_agents.resolve_target()
                real_write_state = installer_core.write_state
                calls = 0

                def fail_final_write(patched_target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("boom")
                    real_write_state(patched_target, state)

                with mock.patch(
                    "app.installer_core.write_state",
                    side_effect=lambda patched_target, state: fail_final_write(patched_target, state),
                ):
                    with self.assertRaises(RuntimeError):
                        manage_agents.install()

            self.assertEqual(architect_path.read_text(encoding="utf-8"), architect_source)

            result = self.run_command_capture(home, "install")

            self.assertEqual(result.returncode, 0)
            self.assertFalse(backup_path.exists())
            self.assertEqual(architect_path.read_text(encoding="utf-8"), architect_source)
            self.assertIsNotNone(self.read_json(state_path)["managed_file_metadata"]["480-architect"])

    def test_advanced_install_state_write_failure_keeps_model_selection_for_safe_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})

            with self.patched_manage_agents_home(home):
                real_write_state = installer_core.write_state
                calls = 0

                def fail_final_write(patched_target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("boom")
                    real_write_state(patched_target, state)

                with mock.patch(
                    "app.installer_core.write_state",
                    side_effect=lambda patched_target, state: fail_final_write(patched_target, state),
                ):
                    with self.assertRaises(RuntimeError):
                        manage_agents.install(
                            model_selection=self.advanced_selection(
                                "opencode",
                                **{"480-architect": "gemini-flash-high"},
                            )
                        )

                self.assertIn("google/gemini-3-flash-preview", architect_path.read_text(encoding="utf-8"))
                state = self.read_json(state_path)
                self.assertEqual(state["model_selection"]["mode"], "advanced")
                self.assertEqual(
                    state["model_selection"]["role_options"]["480-architect"],
                    "gemini-flash-high",
                )

                manage_agents.uninstall()

            self.assertFalse(architect_path.exists())
            self.assertFalse(state_path.exists())

    def test_uninstall_invalid_config_fails_before_touching_managed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})

            self.run_command(home, "install")

            managed_contents = architect_path.read_text(encoding="utf-8")
            config_path.write_text("[]\n", encoding="utf-8")

            result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Expected JSON object", result.stderr)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), managed_contents)
            self.assertTrue(state_path.exists())

    def test_uninstall_removes_modified_managed_file_and_bootstrap_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})
            agents_dir.mkdir(parents=True, exist_ok=True)
            original_architect = "original architect agent\n"
            (agents_dir / "480-architect.md").write_text(original_architect, encoding="utf-8")

            self.run_command(home, "install")

            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            architect_path.write_text("user modified architect\n", encoding="utf-8")

            self.run_command(home, "uninstall")

            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            self.assertFalse(state_path.exists())
            self.assertFalse(backup_path.exists())
            self.assertEqual(
                self.read_json(config_path),
                {"default_agent": "480-developer", "provider": {"x": 1}},
            )
            self.assertFalse(architect_path.exists())

    def test_uninstall_removes_preexisting_matching_managed_agent_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            architect_source = (provider_agents_source_dir("opencode") / "480-architect.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"default_agent": "480-developer"})
            agents_dir.mkdir(parents=True, exist_ok=True)
            architect_path = agents_dir / "480-architect.md"
            architect_path.write_text(architect_source, encoding="utf-8")

            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertFalse(architect_path.exists())

    def test_uninstall_allows_reinstall_without_manual_cleanup_after_modified_managed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            self.run_command(home, "install")
            architect_path.write_text("user modified architect\n", encoding="utf-8")
            self.run_command(home, "uninstall")

            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("new user architect\n", encoding="utf-8")
            install_result = self.run_command_capture(home, "install")
            uninstall_result = self.run_command_capture(home, "uninstall")

            self.assertEqual(install_result.returncode, 0)
            self.assertEqual(uninstall_result.returncode, 0)
            self.assertNotIn("live file and backup both exist", uninstall_result.stdout)
            self.assertFalse(architect_path.exists())
            self.assertFalse(backup_path.exists())
            self.assertFalse(state_path.exists())

    def test_retry_after_failed_uninstall_still_cleans_recreated_managed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = installer_core.write_state
                calls = 0

                def fail_after_first_write(target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(target, state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("app.installer_core.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("new user architect\n", encoding="utf-8")
            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertFalse(architect_path.exists())

    def test_retry_after_failed_uninstall_does_not_require_restoring_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = installer_core.write_state
                calls = 0

                def fail_after_first_write(target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(target, state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("app.installer_core.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertFalse(architect_path.exists())

    def test_uninstall_removes_backup_after_failed_final_install_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                real_write_state = installer_core.write_state
                calls = 0

                def fail_final_write(target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("boom")
                    real_write_state(target, state)

                with mock.patch("app.installer_core.write_state", side_effect=fail_final_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.install()

            state = self.read_json(state_path)
            self.assertTrue(state["managed"]["480-architect"])
            self.assertFalse(state["pending_cleanup"]["480-architect"])

            self.run_command(home, "uninstall")

            self.assertFalse(backup_path.exists())
            self.assertFalse(architect_path.exists())
            self.assertFalse(state_path.exists())

    def test_uninstall_rejects_state_without_pending_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = installer_core.write_state
                calls = 0

                def fail_after_first_write(target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(target, state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("app.installer_core.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            state = self.read_json(state_path)
            state.pop("pending_cleanup", None)
            self.write_json(state_path, state)

            result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing pending_cleanup", result.stderr)
            self.assertFalse(architect_path.exists())
            self.assertTrue(state_path.exists())

    def test_reinstall_after_failed_uninstall_overwrites_recreated_managed_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = installer_core.write_state
                calls = 0

                def fail_after_first_write(target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(target, state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("app.installer_core.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            architect_path.write_text("new user architect\n", encoding="utf-8")

            result = self.run_command_capture(home, "install")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                architect_path.read_text(encoding="utf-8"),
                (provider_agents_source_dir("opencode") / "480-architect.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "new user architect\n")
            self.assertTrue(state_path.exists())

    def test_reinstall_recovers_missing_backup_from_current_live_file_before_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})

            self.run_command(home, "install")

            state = self.read_json(state_path)
            state["backups"] = {"480-architect": "backups/480-architect.md"}
            self.write_json(state_path, state)
            backup_file.unlink(missing_ok=True)
            architect_path.write_text("recover me\n", encoding="utf-8")

            result = self.run_command_capture(home, "install")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "recover me\n")
            self.assertEqual(
                architect_path.read_text(encoding="utf-8"),
                (provider_agents_source_dir("opencode") / "480-architect.md").read_text(encoding="utf-8"),
            )

    def test_reinstall_preserves_canonical_backup_when_state_backup_entry_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            managed_architect = (provider_agents_source_dir("opencode") / "480-architect.md").read_text(
                encoding="utf-8"
            )
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            self.run_command(home, "install")
            architect_path.write_text("user modified architect\n", encoding="utf-8")
            self.run_command(home, "uninstall")

            state_path.parent.mkdir(parents=True, exist_ok=True)
            self.write_json(
                state_path,
                {
                    "version": 1,
                    "managed_agents": AGENTS,
                    "backups": {},
                    "managed": {name: False for name in AGENTS},
                    "managed_file_metadata": {name: None for name in AGENTS},
                    "pending_cleanup": {name: False for name in AGENTS},
                },
            )
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            backup_file.write_text("original architect\n", encoding="utf-8")

            state = self.read_json(state_path)
            state["backups"] = {}
            self.write_json(state_path, state)
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "original architect\n")

            result = self.run_command_capture(home, "install")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "original architect\n")
            self.assertEqual(architect_path.read_text(encoding="utf-8"), managed_architect)
            self.assertEqual(self.read_json(state_path)["backups"]["480-architect"], "backups/480-architect.md")

    def test_reinstall_preserves_canonical_backup_when_state_backup_entry_is_misaligned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            managed_architect = (provider_agents_source_dir("opencode") / "480-architect.md").read_text(
                encoding="utf-8"
            )
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            self.run_command(home, "install")
            architect_path.write_text("user modified architect\n", encoding="utf-8")
            self.run_command(home, "uninstall")

            state_path.parent.mkdir(parents=True, exist_ok=True)
            self.write_json(
                state_path,
                {
                    "version": 1,
                    "managed_agents": AGENTS,
                    "backups": {},
                    "managed": {name: False for name in AGENTS},
                    "managed_file_metadata": {name: None for name in AGENTS},
                    "pending_cleanup": {name: False for name in AGENTS},
                },
            )
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            backup_file.write_text("original architect\n", encoding="utf-8")

            state = self.read_json(state_path)
            state["backups"] = {"480-architect": "backups/stale-architect.md"}
            self.write_json(state_path, state)
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "original architect\n")

            result = self.run_command_capture(home, "install")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "original architect\n")
            self.assertEqual(architect_path.read_text(encoding="utf-8"), managed_architect)
            self.assertEqual(self.read_json(state_path)["backups"]["480-architect"], "backups/480-architect.md")

    def test_uninstall_removes_stale_backup_file_after_canonical_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_dir = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups"
            canonical_backup = backup_dir / "480-architect.md"
            stale_backup = backup_dir / "stale-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})

            self.run_command(home, "install")

            state = self.read_json(state_path)
            state["backups"] = {"480-architect": "backups/stale-architect.md"}
            self.write_json(state_path, state)
            backup_dir.mkdir(parents=True, exist_ok=True)
            canonical_backup.write_text("canonical backup\n", encoding="utf-8")
            stale_backup.write_text("stale backup\n", encoding="utf-8")

            self.run_command(home, "uninstall")

            self.assertFalse(architect_path.exists())
            self.assertFalse(canonical_backup.exists())
            self.assertFalse(stale_backup.exists())
            self.assertFalse(backup_dir.exists())
            self.assertFalse(state_path.parent.exists())

    def test_tampered_managed_state_cannot_preserve_recreated_managed_file_on_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = installer_core.write_state
                calls = 0

                def fail_after_first_write(target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(target, state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("app.installer_core.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            state = self.read_json(state_path)
            state["managed"] = {name: True for name in AGENTS}
            state["pending_cleanup"] = {name: False for name in AGENTS}
            self.write_json(state_path, state)

            architect_path.write_text("new user architect\n", encoding="utf-8")

            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertFalse(architect_path.exists())

    def test_tampered_state_with_matching_metadata_cannot_preserve_repo_identical_managed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            architect_source = (provider_agents_source_dir("opencode") / "480-architect.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = installer_core.write_state
                calls = 0

                def fail_after_first_write(target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(target, state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("app.installer_core.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            state = self.read_json(state_path)
            state["managed"] = {name: True for name in AGENTS}
            state["pending_cleanup"] = {name: False for name in AGENTS}
            self.assertFalse(backup_file.exists())
            architect_path.write_text(architect_source, encoding="utf-8")
            state["managed_file_metadata"] = {
                **state["managed_file_metadata"],
                "480-architect": installer_core.file_metadata(architect_path),
            }
            self.write_json(state_path, state)

            self.run_command(home, "install")
            self.run_command(home, "uninstall")

            self.assertFalse(architect_path.exists())

    def test_uninstall_removes_repo_identical_managed_file_after_failed_uninstall_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            backup_file = home / ".config" / "opencode" / ".480ai-bootstrap" / "backups" / "480-architect.md"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            architect_source = (provider_agents_source_dir("opencode") / "480-architect.md").read_text(encoding="utf-8")
            self.write_json(config_path, {"default_agent": "480-developer"})
            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text("original architect\n", encoding="utf-8")

            with self.patched_manage_agents_home(home):
                manage_agents.install()

                real_write_state = installer_core.write_state
                calls = 0

                def fail_after_first_write(target, state: dict) -> None:
                    nonlocal calls
                    calls += 1
                    real_write_state(target, state)
                    if calls == 1:
                        raise RuntimeError("boom")

                with mock.patch("app.installer_core.write_state", side_effect=fail_after_first_write):
                    with self.assertRaises(RuntimeError):
                        manage_agents.uninstall()

            architect_path.parent.mkdir(parents=True, exist_ok=True)
            architect_path.write_text(architect_source, encoding="utf-8")
            self.run_command(home, "install")
            result = self.run_command_capture(home, "uninstall")

            self.assertEqual(result.returncode, 0)
            self.assertNotIn("live file and backup both exist", result.stdout)
            self.assertFalse(architect_path.exists())
            self.assertFalse(state_path.exists())
            self.assertFalse(backup_file.exists())

    def test_write_json_preserves_existing_file_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opencode.json"
            original = {"default_agent": "480-developer"}
            replacement = {"default_agent": "480-architect"}
            installer_core.write_json(path, original)

            with mock.patch("app.installer_core.os.replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    installer_core.write_json(path, replacement)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_install_refuses_to_follow_symlinked_agent_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            agents_dir = home / ".config" / "opencode" / "agents"
            outside_path = home / "outside.md"
            architect_path = agents_dir / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer"})
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
            self.write_json(config_path, {"default_agent": "480-developer"})
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
            self.write_json(config_path, {"default_agent": "480-developer"})

            self.run_command(home, "install")

            state = self.read_json(state_path)
            state["managed_agents"] = ["480-architect", "../../outside"]
            self.write_json(state_path, state)

            result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid managed_agents", result.stderr)

    def test_uninstall_rejects_backup_path_outside_backup_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            outside_backup = home / "outside-backup.md"
            self.write_json(config_path, {"default_agent": "480-developer"})

            self.run_command(home, "install")

            outside_backup.write_text("outside backup\n", encoding="utf-8")
            state = self.read_json(state_path)
            state["backups"] = {"480-architect": "../../../../outside-backup.md"}
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
            self.write_json(config_path, {"default_agent": "480-developer"})

            self.run_command(home, "install")

            state = self.read_json(state_path)
            state["managed"] = {"480-architect": True}
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
            architect_path = home / ".config" / "opencode" / "agents" / "480-architect.md"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})

            self.run_command(home, "install")

            managed_contents = architect_path.read_text(encoding="utf-8")
            self.write_json(state_path, {})

            result = self.run_command_capture(home, "uninstall")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing managed_agents", result.stderr)
            self.assertEqual(architect_path.read_text(encoding="utf-8"), managed_contents)
            self.assertEqual(
                self.read_json(config_path),
                {"default_agent": "480-architect", "provider": {"x": 1}},
            )

    def test_uninstall_ignores_invalid_previous_default_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "opencode" / "opencode.json"
            state_path = home / ".config" / "opencode" / ".480ai-bootstrap" / "state.json"
            self.write_json(config_path, {"default_agent": "480-developer", "provider": {"x": 1}})

            self.run_command(home, "install")

            state = self.read_json(state_path)
            state["previous_default_agent"] = []
            self.write_json(state_path, state)

            result = self.run_command_capture(home, "uninstall")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                self.read_json(config_path),
                {"default_agent": "480-architect", "provider": {"x": 1}},
            )

    def test_remote_bootstrap_scripts_default_to_public_repo_without_auth(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        install_remote = (REPO_ROOT / "bootstrap" / "install-remote.sh").read_text(encoding="utf-8")
        uninstall_remote = (REPO_ROOT / "bootstrap" / "uninstall-remote.sh").read_text(encoding="utf-8")
        install_section = self.readme_section("Install", readme)
        uninstall_section = self.readme_section("Uninstall", readme)

        self.assertIn("curl", install_section)
        self.assertIn("raw.githubusercontent.com/480/ai/main/bootstrap/install-remote.sh", install_section)
        self.assertIn('sh -c "$(curl -fsSL https://raw.githubusercontent.com/480/ai/main/bootstrap/install-remote.sh)"', install_section)
        self.assertNotIn("tmpdir=", install_section)
        self.assertNotIn("cleanup()", install_section)
        self.assertNotIn("trap cleanup EXIT INT TERM HUP", install_section)
        self.assertNotIn("codeload.github.com/480/ai/tar.gz/main", install_section)
        self.assertNotIn('sh "$tmpdir/install.sh"', install_section)
        self.assertNotIn('rm -rf "$tmpdir"', install_section)
        self.assertNotRegex(install_section, r"(?<![A-Za-z-])(?:bootstrap/)?uninstall-remote\.sh")
        self.assertNotIn("git clone https://github.com/480/ai.git", install_section)
        self.assertNotIn("clone-first", install_section)
        self.assertNotIn("private `480/ai`", install_section)
        self.assertNotIn("gh auth token", install_section)
        self.assertNotIn("GITHUB_TOKEN", install_section)

        self.assertIn("curl", uninstall_section)
        self.assertIn("raw.githubusercontent.com/480/ai/main/bootstrap/uninstall-remote.sh", uninstall_section)
        self.assertIn('curl -fsSL "https://raw.githubusercontent.com/480/ai/main/bootstrap/uninstall-remote.sh" | sh', uninstall_section)
        self.assertNotIn("tmpdir=", uninstall_section)
        self.assertNotIn("cleanup()", uninstall_section)
        self.assertNotIn("trap cleanup EXIT INT TERM HUP", uninstall_section)
        self.assertNotIn("codeload.github.com/480/ai/tar.gz/main", uninstall_section)
        self.assertNotIn('sh "$tmpdir/uninstall.sh"', uninstall_section)
        self.assertNotIn('rm -rf "$tmpdir"', uninstall_section)
        self.assertNotRegex(uninstall_section, r"(?<![A-Za-z-])(?:bootstrap/)?install-remote\.sh")
        self.assertNotIn('sh -c "$(curl -fsSL https://raw.githubusercontent.com/480/ai/main/bootstrap/install-remote.sh)"', uninstall_section)
        self.assertIn('REPO="${BOOTSTRAP_REPO:-480/ai}"', install_remote)
        self.assertIn('REPO="${BOOTSTRAP_REPO:-480/ai}"', uninstall_remote)
        self.assertIn('archive_url="https://codeload.github.com/$REPO/tar.gz/$REF"', install_remote)
        self.assertIn('archive_url="https://codeload.github.com/$REPO/tar.gz/$REF"', uninstall_remote)
        self.assertIn('sh "$checkout_dir/install.sh" "$@"', install_remote)
        self.assertIn('sh "$checkout_dir/uninstall.sh" "$@"', uninstall_remote)
        self.assertNotIn("Authorization: Bearer", install_remote)
        self.assertNotIn("Authorization: Bearer", uninstall_remote)
        self.assertNotIn("GITHUB_TOKEN", install_remote)
        self.assertNotIn("GITHUB_TOKEN", uninstall_remote)
        self.assertIn("Check your network connection, repository name, and ref.", install_remote)
        self.assertIn("Check your network connection, repository name, and ref.", uninstall_remote)
        self.assertIn("Check the repository name and ref.", install_remote)
        self.assertIn("Check the repository name and ref.", uninstall_remote)

    def test_readme_covers_slim_bootstrap_flow_and_core_references(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        install_section = self.readme_section("Install", readme)

        self.assertIn("## Install", readme)
        self.assertIn("## Uninstall", readme)
        self.assertRegex(install_section, r"multiple\s+providers")

        supported_targets = self.readme_section_any(("Supported providers", "Providers"), readme)

        opencode_line = re.search(r"^.*OpenCode.*$", supported_targets, re.MULTILINE)
        self.assertIsNotNone(opencode_line)
        assert opencode_line is not None
        self.assertIn("user", opencode_line.group(0))
        self.assertNotIn("project", opencode_line.group(0))
        self.assertIn("480-architect", opencode_line.group(0))

        claude_line = re.search(r"^.*Claude Code.*$", supported_targets, re.MULTILINE)
        self.assertIsNotNone(claude_line)
        assert claude_line is not None
        self.assertIn("user", claude_line.group(0))
        self.assertIn("project", claude_line.group(0))
        self.assertIn("480-architect", claude_line.group(0))
        self.assert_claude_teams_install_contract(
            claude_line.group(0),
            mention_uninstall=False,
            mention_env_key=False,
        )

        codex_line = re.search(r"^.*Codex CLI.*$", supported_targets, re.MULTILINE)
        self.assertIsNotNone(codex_line)
        assert codex_line is not None
        self.assertIn("user", codex_line.group(0))
        self.assertIn("project", codex_line.group(0))
        self.assertIn("AGENTS.md", codex_line.group(0))
        self.assertIn("config.toml", codex_line.group(0))
        self.assertIn("agents.max_depth = 2", codex_line.group(0))
        self.assertNotRegex(codex_line.group(0), r"480-architect.*enabled|enabled.*480-architect")

    def test_opencode_index_and_architecture_docs_match_current_bootstrap_behavior(self) -> None:
        opencode_index = provider_index_path("opencode").read_text(encoding="utf-8")
        claude_index = provider_index_path("claude").read_text(encoding="utf-8")
        codex_index = provider_index_path("codex").read_text(encoding="utf-8")
        common_architect = (REPO_ROOT / "bundles" / "common" / "instructions" / "480-architect.md").read_text(
            encoding="utf-8"
        )
        codex_architect = (REPO_ROOT / "providers" / "codex" / "instructions" / "480-architect.md").read_text(
            encoding="utf-8"
        )
        opencode_architect = (provider_agents_source_dir("opencode") / "480-architect.md").read_text(encoding="utf-8")
        claude_architect_source = (REPO_ROOT / "providers" / "claude" / "instructions" / "480-architect.md").read_text(
            encoding="utf-8"
        )
        claude_architect = (provider_agents_source_dir("claude") / "480-architect.md").read_text(encoding="utf-8")
        codex_managed_guidance = render_agents.render_codex_managed_guidance(agent_bundle.load_bundle())

        self.assertIn("Enable `480-architect` by default and set `default_agent` during install.", opencode_index)
        self.assertIn("`--no-activate-default` or `BOOTSTRAP_ACTIVATE_DEFAULT=0`", opencode_index)
        self.assertIn("current setting is still `480-architect`", opencode_index)
        self.assertIn("Advanced installs render temporary artifacts from the selected model combination", opencode_index)
        self.assert_claude_team_contract(claude_index)
        self.assert_claude_teams_install_contract(
            claude_index,
            mention_uninstall=True,
            mention_env_key=True,
        )
        self.assertIn("Codex uses the 480ai managed block in the root `AGENTS.md` as the architect main prompt.", codex_index)
        self.assertIn("`providers/codex/instructions/480-architect.md`", codex_index)
        self.assertIn("there is no separate architect custom agent", codex_index)
        self.assertIn("Codex custom agents provide only the four subagents below.", codex_index)
        self.assertIn("`~/.codex/config.toml` or `<project>/.codex/config.toml`", codex_index)
        self.assertIn("Install preserves existing settings and only applies `features.multi_agent = true` and `agents.max_depth = 2`.", codex_index)
        self.assertIn("This architect workflow is for the root Codex session only", codex_index)
        self.assertIn("Codex install/uninstall also clean up legacy `480-architect.toml` and `480.toml` leftovers when present.", codex_index)
        self.assertIn("The default delegation depth is 2:", codex_index)
        self.assertIn("The default reviewer flow is parallel", codex_index)
        self.assertIn(
            "If `480-code-reviewer2` returns a delegation infrastructure blocker, do not re-request `480-code-reviewer`; wait for `480-code-reviewer` to finish if it is still pending, then retry `480-code-reviewer2` alone exactly once before surfacing the blocker upstream.",
            codex_index,
        )
        self.assertIn("Reviewers review in-thread", codex_index)
        self.assertIn("Keep the concurrent agent budget narrow", codex_index)
        self.assertIn("dedicated worktree and task branch", codex_index)
        self.assertIn("When possible, the architect plans and delegates with a dedicated worktree and task branch as the default operating model.", codex_index)
        self.assert_codex_lifecycle_contract(
            codex_index,
            ownership_line="The current parent session owns each child lifecycle end-to-end: spawn, follow-up, retry, result collection, wait, and explicit close.",
            active_work_line="Do not treat the active workflow as complete while any child still has pending follow-up, retry, result collection, or wait work owned by that parent session.",
            close_line="Close a child only after its latest loop is complete and the parent session has no remaining follow-up, retry, result collection, or wait responsibility for it.",
        )
        self.assertIn("When waiting on a Codex child agent, prefer longer waits over short polling loops.", codex_index)
        self.assertIn(
            "Do not repeat user-facing `still waiting` messages when there is no meaningful state change.",
            codex_index,
        )
        self.assertIn(
            "User-facing wait updates should only report blockers, completion, real state changes, or long delays that help decision-making.",
            codex_index,
        )
        self.assertIn(
            "Use follow-up status checks sparingly and do not make them the default waiting pattern.",
            codex_index,
        )
        self.assertIn("Workspace resolution should prefer the Task Brief path and explicit absolute repo/worktree paths", codex_index)
        self.assertIn("Treat a spawn response with no `agent_id`, or any non-structured spawn response, as `spawn_failure`.", codex_index)
        self.assertIn("Classify `spawn_failure`, thread limit failures, and usage limit failures as delegation infrastructure blockers, not implementation blockers.", codex_index)
        self.assertIn("If the blocker remains after one retry in the same session, return only a structured blocker report to the current parent session/thread.", codex_index)
        self.assertIn("Low-risk fallback: if one reviewer has approved and the other reviewer is blocked only by delegation infrastructure, the architect may run an independent diff review when the changed files are limited to prompts, docs, config metadata, or tests.", codex_index)
        self.assertIn("Do not waive any explicit change request from either reviewer.", codex_index)
        self.assertIn("Do not make `new session` or `exception allowed` the default path for users.", codex_index)
        self.assertIn("Existing user content is preserved and only the 480ai managed block is appended.", codex_index)
        self.assertIn("Uninstall removes only the 480ai managed block.", codex_index)
        self.assertIn("Architect rules apply only to the root session, and subagents follow their own custom agent instructions.", codex_index)
        self.assertIn(
            "Plan the next work for docs/480ai/example-topic/001-example-task.md.",
            codex_index,
        )
        self.assertIn(
            "Have 480-developer implement docs/480ai/example-topic/001-example-task.md.",
            codex_index,
        )
        self.assertIn(
            "Have 480-developer request review from 480-code-reviewer and 480-code-reviewer2 in parallel, then return a completion report after both approvals.",
            codex_index,
        )
        expected_gitignore_contract = (
            "Ensure `docs/480ai/` is ignored in the working repo's `.gitignore` before writing Task Brief files there; "
            "handle that housekeeping in the workflow instead of asking the user about it."
        )
        expected_short_signoff_contract = (
            "Ask the user to reply with a short, explicit approval word in their current language "
            "(for example, `approved`)."
        )
        for architect_doc in (common_architect, codex_architect, opencode_architect, codex_managed_guidance):
            self.assertIn(expected_gitignore_contract, architect_doc)
            self.assertIn(expected_short_signoff_contract, architect_doc)
            self.assert_architect_autopilot_worktree_contract(architect_doc)
            self.assertNotIn("tell the user to add that path to the repo's `.gitignore`", architect_doc)
        self.assertIn(expected_gitignore_contract, claude_architect_source)
        self.assertIn(expected_short_signoff_contract, claude_architect_source)
        self.assert_architect_autopilot_worktree_contract(claude_architect_source)
        self.assertIn(expected_gitignore_contract, claude_architect)
        self.assertIn(expected_short_signoff_contract, claude_architect)
        self.assert_architect_autopilot_worktree_contract(claude_architect)
        self.assert_claude_parent_lifecycle_contract(
            claude_architect_source,
            ownership_line="In team mode, the parent session owns each delegated child lifecycle end-to-end.",
            active_work_line="Do not treat a workflow, task, or plan step as complete while any spawned child still requires result collection, waiting, follow-up, or closure from you.",
            close_line="After you spawn a child, keep the task active until you have collected the child's result, waited through any required follow-up, and explicitly closed or otherwise released finished child sessions.",
        )
        self.assert_claude_parent_lifecycle_contract(
            claude_architect,
            ownership_line="In team mode, the parent session owns each delegated child lifecycle end-to-end.",
            active_work_line="Do not treat a workflow, task, or plan step as complete while any spawned child still requires result collection, waiting, follow-up, or closure from you.",
            close_line="After you spawn a child, keep the task active until you have collected the child's result, waited through any required follow-up, and explicitly closed or otherwise released finished child sessions.",
        )
        self.assertIn("Codex native delegation contract", codex_managed_guidance)
        self.assertIn("`480-developer` (depth 1) -> reviewer/scanner subagents only when needed (depth 2)", codex_managed_guidance)
        self.assertIn("Keep the concurrent agent budget narrow.", codex_managed_guidance)
        self.assertIn(
            "The parent session owns each child lifecycle end-to-end: spawn, follow-up, retry, result collection, wait, and explicit close.",
            codex_managed_guidance,
        )
        self.assertIn(
            "Do not treat an active workflow as finished, or return a completed result, while any spawned child still has pending follow-up, retry, result collection, or wait work owned by the parent.",
            codex_managed_guidance,
        )
        self.assertIn(
            "Close a child only after its latest loop is complete and the parent has no remaining follow-up, retry, result collection, or wait responsibility for that child.",
            codex_managed_guidance,
        )
        self.assertIn("Treat a spawn response with no `agent_id`, or any non-structured spawn response, as `spawn_failure`.", codex_managed_guidance)
        self.assertIn("Any explicit change request from either reviewer is a real review finding and is never waived by this fallback.", codex_managed_guidance)
        self.assertIn("prefer longer waits over short polling loops", codex_managed_guidance)
        self.assertIn("Do not send user-facing \"still waiting\"", codex_managed_guidance)
        self.assertIn("User-facing wait updates should be change-based", codex_managed_guidance)
        self.assertIn("Use follow-up status checks sparingly", codex_managed_guidance)
        self.assertIn("prefer the repo or worktree implied by the Task Brief path", codex_managed_guidance)
        self.assertNotIn(
            "Let Codex manage child thread lifecycle unless a platform contract explicitly requires otherwise.",
            codex_managed_guidance,
        )
        self.assertNotIn(
            "Codex manages child thread lifecycle itself. Do not add explicit close enforcement unless a separate platform contract requires it.",
            codex_managed_guidance,
        )

        common_developer = (REPO_ROOT / "bundles" / "common" / "instructions" / "480-developer.md").read_text(
            encoding="utf-8"
        )
        self.assert_developer_role_identity_contract(common_developer, codex_style=False)
        self.assert_developer_role_identity_contract(
            (provider_agents_source_dir("opencode") / "480-developer.md").read_text(encoding="utf-8"),
            codex_style=False,
        )
        self.assert_developer_role_identity_contract(
            (provider_agents_source_dir("claude") / "480-developer.md").read_text(encoding="utf-8"),
            codex_style=False,
        )
        self.assertIn(
            "Resolve workspace context from the Task Brief path and any explicit absolute repository or worktree path first.",
            (provider_agents_source_dir("opencode") / "480-developer.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "Resolve workspace context from the Task Brief path and any explicit absolute repository or worktree path first.",
            (provider_agents_source_dir("claude") / "480-developer.md").read_text(encoding="utf-8"),
        )
        self.assert_developer_role_identity_contract(
            (REPO_ROOT / "providers" / "codex" / "instructions" / "480-developer.md").read_text(encoding="utf-8"),
            codex_style=True,
        )
        self.assert_codex_developer_review_parse_contract(
            (REPO_ROOT / "providers" / "codex" / "instructions" / "480-developer.md").read_text(encoding="utf-8")
        )

        codex_developer = tomllib.loads((provider_agents_source_dir("codex") / "480-developer.toml").read_text(encoding="utf-8"))
        self.assert_codex_lifecycle_contract(
            codex_developer["developer_instructions"],
            ownership_line="This parent developer session owns each reviewer or scanner child lifecycle end-to-end: spawn, follow-up, retry, result collection, wait, and explicit close.",
            active_work_line="Do not treat the current task as complete, or return a completion report, while any reviewer or scanner child still has pending follow-up, retry, result collection, or wait work owned by this session.",
            close_line="Close a reviewer or scanner child only after its latest loop is complete and this session has no remaining follow-up, retry, result collection, or wait responsibility for that child.",
        )
        self.assert_developer_role_identity_contract(codex_developer["developer_instructions"], codex_style=True)
        self.assert_codex_developer_review_parse_contract(codex_developer["developer_instructions"])
        self.assertIn(
            "return only a structured blocker report to the current parent session or thread with `status`, `blocker_type`, `stage`, `reason`, `attempts`, and `evidence`.",
            codex_developer["developer_instructions"],
        )
        self.assertIn(
            "The parent architect may continue without pausing only if that low-risk fallback is applicable and an independent diff review finds no required changes.",
            codex_developer["developer_instructions"],
        )
        self.assertIn(
            "Any explicit change request from either reviewer is a real review finding and is never waived by this fallback.",
            codex_developer["developer_instructions"],
        )
        self.assertIn(
            "Do not treat a progress update as a completion report or stop the implementation or review loop.",
            codex_developer["developer_instructions"],
        )
        self.assertIn(
            "If `480-code-reviewer2` returns a delegation infrastructure blocker, do not re-request `480-code-reviewer`; wait for `480-code-reviewer` to finish if it is still pending, then retry `480-code-reviewer2` alone exactly once before surfacing the blocker upstream.",
            codex_developer["developer_instructions"],
        )
        claude_developer = (provider_agents_source_dir("claude") / "480-developer.md").read_text(encoding="utf-8")
        self.assertNotIn(
            "Let Codex manage child thread lifecycle unless a platform contract explicitly requires otherwise.",
            claude_developer,
        )
        self.assertNotIn(
            "Codex manages child thread lifecycle itself. Do not add explicit close enforcement unless a separate platform contract requires it.",
            claude_developer,
        )

        codex_reviewer = tomllib.loads((provider_agents_source_dir("codex") / "480-code-reviewer.toml").read_text(encoding="utf-8"))
        self.assert_codex_close_contract(
            codex_reviewer["developer_instructions"],
            parent_label="`480-developer` subagent",
        )
        self.assertIn(
            "A direct change request is a real review finding and must not be described as an infrastructure blocker.",
            codex_reviewer["developer_instructions"],
        )
        self.assert_codex_reviewer_stays_in_thread(codex_reviewer["developer_instructions"])

        codex_reviewer2 = tomllib.loads((provider_agents_source_dir("codex") / "480-code-reviewer2.toml").read_text(encoding="utf-8"))
        self.assert_codex_close_contract(
            codex_reviewer2["developer_instructions"],
            parent_label="`480-developer` subagent",
        )
        self.assertIn(
            "A direct change request is a real review finding and must not be described as an infrastructure blocker.",
            codex_reviewer2["developer_instructions"],
        )
        self.assert_codex_reviewer_stays_in_thread(codex_reviewer2["developer_instructions"])

        codex_scanner = tomllib.loads((provider_agents_source_dir("codex") / "480-code-scanner.toml").read_text(encoding="utf-8"))
        self.assertIn(
            "Parent close responsibility stays with the current parent session or thread.",
            codex_scanner["developer_instructions"],
        )
        self.assertIn("current loop is truly finished", codex_scanner["developer_instructions"])
        self.assertIn("latest result is completed", codex_scanner["developer_instructions"])
        self.assertIn("no follow-up, retry, or result wait remains", codex_scanner["developer_instructions"])
        self.assertIn("Do not treat this scanner child thread as closable while follow-up, retry, or result wait work is still pending.", codex_scanner["developer_instructions"])
        self.assertIn(
            "return only a structured blocker report to the current parent session or thread instead of proposing `new session` or `exception allowed` as the default path.",
            codex_scanner["developer_instructions"],
        )
        self.assertIn("treat that target as the primary workspace hint", codex_scanner["developer_instructions"])
        self.assertIn("If a Codex spawn response is missing `agent_id` or is not a structured response, treat it as `spawn_failure`.", codex_scanner["developer_instructions"])
        self.assert_scanner_output_path_contract(codex_scanner["developer_instructions"])

    def test_repo_gitignore_tracks_only_current_planning_dir(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("docs/480ai/", gitignore)
        self.assertNotIn("docs/coding-team/", gitignore)

    def test_code_scanner_reasoning_is_high_everywhere(self) -> None:
        common_scanner = (REPO_ROOT / "bundles" / "common" / "instructions" / "480-code-scanner.md").read_text(
            encoding="utf-8"
        )
        code_scanner_agent = (provider_agents_source_dir("opencode") / "480-code-scanner.md").read_text(encoding="utf-8")
        agents_index = provider_index_path("opencode").read_text(encoding="utf-8")

        self.assert_scanner_output_path_contract(common_scanner)
        self.assertIn("model: openai/gpt-5.4-nano", code_scanner_agent)
        self.assertIn("reasoningEffort: high", code_scanner_agent)
        self.assertNotIn("reasoningEffort: xhigh", code_scanner_agent)
        self.assert_scanner_output_path_contract(code_scanner_agent)
        self.assert_scanner_output_path_contract(
            (provider_agents_source_dir("claude") / "480-code-scanner.md").read_text(encoding="utf-8")
        )
        self.assertIn("- `480-code-scanner`\n  - file: `providers/opencode/agents/480-code-scanner.md`\n  - model: `openai/gpt-5.4-nano`\n  - reasoning: `high`", agents_index)

    def test_codex_agents_index_lists_synced_default_models(self) -> None:
        agents_index = (REPO_ROOT / "providers" / "codex" / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn(
            "- `480-developer`\n  - maps from: `480-developer`\n  - file: `providers/codex/agents/480-developer.toml`\n  - model: `gpt-5.4-mini`\n  - reasoning: `medium`",
            agents_index,
        )
        self.assertIn(
            "- `480-code-reviewer`\n  - maps from: `480-code-reviewer`\n  - file: `providers/codex/agents/480-code-reviewer.toml`\n  - model: `gpt-5.4`\n  - reasoning: `high`",
            agents_index,
        )
        self.assertIn(
            "- `480-code-reviewer2`\n  - maps from: `480-code-reviewer2`\n  - file: `providers/codex/agents/480-code-reviewer2.toml`\n  - model: `gpt-5.4`\n  - reasoning: `medium`",
            agents_index,
        )
        self.assertIn(
            "- `480-code-scanner`\n  - maps from: `480-code-scanner`\n  - file: `providers/codex/agents/480-code-scanner.toml`\n  - model: `gpt-5.3-codex-spark`\n  - reasoning: `low`",
            agents_index,
        )

    def test_common_bundle_renders_checked_in_target_outputs(self) -> None:
        specs = agent_bundle.load_bundle()
        claude_name_map = render_agents._claude_name_map(specs)
        codex_name_map = render_agents._codex_name_map(specs)

        self.assertEqual([spec.identifier for spec in specs], AGENTS)
        self.assertEqual(list(claude_name_map.values()), CLAUDE_AGENTS)
        self.assertEqual(agent_bundle.target_agent_names("codex"), CODEX_AGENTS)
        self.assertEqual(
            [codex_name_map[spec.identifier] for spec in specs if spec.mode == "subagent"],
            CODEX_CANONICAL_AGENTS,
        )

        for spec in specs:
            agent_path = provider_agents_source_dir("opencode") / f"{spec.identifier}.md"
            self.assertEqual(
                agent_path.read_text(encoding="utf-8"),
                render_agents.render_opencode_agent(spec),
            )
            claude_path = provider_agents_source_dir("claude") / f"{claude_name_map[spec.identifier]}.md"
            self.assertEqual(
                claude_path.read_text(encoding="utf-8"),
                render_agents.render_claude_agent(spec, claude_name_map),
            )
            if spec.mode == "subagent":
                codex_name = codex_name_map[spec.identifier]
                codex_path = provider_agents_source_dir("codex") / f"{codex_name}.toml"
                codex_contents = codex_path.read_text(encoding="utf-8")
                self.assertEqual(codex_contents, render_agents.render_codex_agent(spec, codex_name_map))

                parsed = tomllib.loads(codex_contents)
                codex_metadata = spec.metadata_for_target("codex")
                codex_model_profile = get_provider("codex").recommended_role_model_config(spec)
                expected_instructions = render_agents._replace_agent_names(
                    spec.instruction_source_for_target("codex").read_text(encoding="utf-8"),
                    codex_name_map,
                    mention_prefix="",
                )
                if not expected_instructions.endswith("\n"):
                    expected_instructions += "\n"
                self.assertEqual(parsed["name"], codex_name)
                self.assertEqual(parsed["description"], spec.description)
                self.assertEqual(parsed["model"], codex_model_profile.model)
                self.assertEqual(parsed["model_reasoning_effort"], codex_model_profile.effort)
                self.assertEqual(parsed["sandbox_mode"], codex_metadata["sandbox_mode"])
                self.assertNotIn("nickname_candidates", parsed)
                self.assertEqual(parsed["developer_instructions"], expected_instructions)
                self.assertNotIn("@", parsed["developer_instructions"])

        self.assertEqual(
            provider_index_path("opencode").read_text(encoding="utf-8"),
            render_agents.render_agents_index(specs),
        )
        self.assertEqual(
            provider_index_path("claude").read_text(encoding="utf-8"),
            render_agents.render_claude_agents_index(specs),
        )
        self.assertEqual(
            provider_index_path("codex").read_text(encoding="utf-8"),
            render_agents.render_codex_agents_index(specs),
        )
        self.assertEqual(
            render_agents.render_codex_managed_guidance(specs),
            render_agents._replace_agent_names(
                next(spec for spec in specs if spec.identifier == "480-architect").instruction_source_for_target("codex").read_text(encoding="utf-8"),
                codex_name_map,
                mention_prefix="",
            ).rstrip("\n"),
        )

    def test_render_codex_agent_uses_target_instruction_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_instructions = root / "default.md"
            codex_instructions = root / "codex.md"
            default_instructions.write_text("Default instructions\n", encoding="utf-8")
            codex_instructions.write_text("Codex-only instructions for 480-developer\n", encoding="utf-8")

            spec = agent_bundle.AgentSpec(
                identifier="480-developer",
                display_name="480-developer",
                description="Developer",
                role="implementation",
                mode="subagent",
                model="openai/gpt-5.4",
                reasoning="medium",
                instruction_source=default_instructions,
                target_metadata={
                    "codex": {
                        "name": "480-developer",
                        "instruction_source": "codex.md",
                        "sandbox_mode": "workspace-write",
                    }
                },
            )

            with mock.patch.object(agent_bundle, "REPO_ROOT", root):
                rendered = render_agents.render_codex_agent(spec, {"480-developer": "480-developer"})

            parsed = tomllib.loads(rendered)
            self.assertIn("Codex-only instructions for 480-developer", parsed["developer_instructions"])
            self.assertNotIn("Default instructions", parsed["developer_instructions"])

    def test_render_codex_agent_applies_name_map_without_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            architect_instructions = root / "architect.md"
            developer_instructions = root / "developer.md"
            reviewer_instructions = root / "reviewer.md"
            architect_instructions.write_text(
                "Ask @480-developer to sync with `480-code-reviewer`.\n",
                encoding="utf-8",
            )
            developer_instructions.write_text("Developer body\n", encoding="utf-8")
            reviewer_instructions.write_text("Reviewer body\n", encoding="utf-8")

            specs = (
                agent_bundle.AgentSpec(
                    identifier="480-architect",
                    display_name="480-architect",
                    description="Architect",
                    role="planning",
                    mode="primary",
                    model="openai/gpt-5.4",
                    reasoning="high",
                    instruction_source=architect_instructions,
                    target_metadata={
                        "codex": {
                            "name": "architect-role",
                            "model": "gpt-5.4",
                            "model_reasoning_effort": "high",
                            "sandbox_mode": "workspace-write",
                        }
                    },
                ),
                agent_bundle.AgentSpec(
                    identifier="480-developer",
                    display_name="480-developer",
                    description="Developer",
                    role="implementation",
                    mode="subagent",
                    model="openai/gpt-5.4",
                    reasoning="medium",
                    instruction_source=developer_instructions,
                    target_metadata={
                        "codex": {
                            "name": "implementer-role",
                            "model": "gpt-5.4",
                            "model_reasoning_effort": "medium",
                            "sandbox_mode": "workspace-write",
                        }
                    },
                ),
                agent_bundle.AgentSpec(
                    identifier="480-code-reviewer",
                    display_name="480-code-reviewer",
                    description="Reviewer",
                    role="review",
                    mode="subagent",
                    model="openai/gpt-5.4",
                    reasoning="high",
                    instruction_source=reviewer_instructions,
                    target_metadata={
                        "codex": {
                            "name": "reviewer-role",
                            "model": "gpt-5.4",
                            "model_reasoning_effort": "high",
                            "sandbox_mode": "read-only",
                        }
                    },
                ),
            )

            codex_name_map = render_agents._codex_name_map(specs)
            rendered = render_agents.render_codex_agent(specs[0], codex_name_map)
            parsed = tomllib.loads(rendered)

            self.assertEqual(
                parsed["developer_instructions"],
                "Ask implementer-role to sync with `reviewer-role`.\n",
            )

    def test_render_claude_agent_preserves_at_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instructions = root / "reviewer.md"
            instructions.write_text(
                "Send feedback to @480-developer and escalate to @480-architect.\n",
                encoding="utf-8",
            )

            spec = agent_bundle.AgentSpec(
                identifier="480-code-reviewer",
                display_name="480-code-reviewer",
                description="Reviewer",
                role="review",
                mode="subagent",
                model="openai/gpt-5.4",
                reasoning="high",
                instruction_source=instructions,
                target_metadata={
                    "claude": {
                        "name": "ai-reviewer",
                        "tools": ["Read", "Bash"],
                    }
                },
            )
            claude_name_map = {
                "480-architect": "480-architect",
                "480-developer": "480-developer",
                "480-code-reviewer": "ai-reviewer",
            }

            rendered = render_agents.render_claude_agent(spec, claude_name_map)

            self.assertIn("@480-developer", rendered)
            self.assertIn("@480-architect", rendered)

    def test_render_claude_agent_uses_target_instruction_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_instructions = root / "default.md"
            claude_instructions = root / "claude.md"
            default_instructions.write_text("Default instructions\n", encoding="utf-8")
            claude_instructions.write_text("Claude-only instructions for @480-developer\n", encoding="utf-8")

            spec = agent_bundle.AgentSpec(
                identifier="480-architect",
                display_name="480-architect",
                description="Architect",
                role="planning",
                mode="primary",
                model="openai/gpt-5.4",
                reasoning="xhigh",
                instruction_source=default_instructions,
                target_metadata={
                    "claude": {
                        "name": "480-architect",
                        "instruction_source": "claude.md",
                        "tools": ["Read", "Bash"],
                    }
                },
            )

            with mock.patch.object(agent_bundle, "REPO_ROOT", root):
                rendered = render_agents.render_claude_agent(
                    spec,
                    {
                        "480-architect": "480-architect",
                        "480-developer": "480-developer",
                    },
                )

            self.assertIn("Claude-only instructions for @480-developer", rendered)
            self.assertNotIn("Default instructions", rendered)

    def test_target_instruction_override_applies_only_to_claude_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_instructions = root / "default.md"
            claude_instructions = root / "claude.md"
            default_instructions.write_text("Default architect flow with @480-developer\n", encoding="utf-8")
            claude_instructions.write_text("Claude team flow with @480-developer and fallback\n", encoding="utf-8")

            spec = agent_bundle.AgentSpec(
                identifier="480-architect",
                display_name="480-architect",
                description="Architect",
                role="planning",
                mode="primary",
                model="openai/gpt-5.4",
                reasoning="xhigh",
                instruction_source=default_instructions,
                target_metadata={
                    "opencode": {
                        "temperature": 0.1,
                        "tools": {"write": True, "edit": True, "bash": True},
                    },
                    "claude": {
                        "name": "480-architect",
                        "instruction_source": "claude.md",
                        "tools": ["Agent(480-developer)", "Read", "Bash"],
                    },
                    "codex": {
                        "name": "480",
                        "sandbox_mode": "workspace-write",
                    },
                },
            )
            developer_spec = agent_bundle.AgentSpec(
                identifier="480-developer",
                display_name="480-developer",
                description="Developer",
                role="implementation",
                mode="subagent",
                model="openai/gpt-5.4",
                reasoning="medium",
                instruction_source=default_instructions,
                target_metadata={
                    "opencode": {
                        "temperature": 0.1,
                        "tools": {"write": True, "edit": True, "bash": True},
                    },
                    "claude": {
                        "name": "480-developer",
                        "tools": ["Read", "Bash"],
                    },
                    "codex": {
                        "name": "480-developer",
                        "sandbox_mode": "workspace-write",
                    },
                },
            )

            with mock.patch.object(agent_bundle, "REPO_ROOT", root):
                claude_rendered = render_agents.render_claude_agent(
                    spec,
                    {"480-architect": "480-architect", "480-developer": "480-developer"},
                )
                opencode_rendered = render_agents.render_opencode_agent(spec)
                codex_guidance = render_agents.render_codex_managed_guidance((spec, developer_spec))

            self.assertIn("Claude team flow with @480-developer and fallback", claude_rendered)
            self.assertNotIn("Default architect flow", claude_rendered)
            self.assertIn("Default architect flow with @480-developer", opencode_rendered)
            self.assertNotIn("Claude team flow", opencode_rendered)
            self.assertIn("Default architect flow with 480-developer", codex_guidance)
            self.assertNotIn("Claude team flow", codex_guidance)

    def test_render_codex_target_agent_names_include_compatibility_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instructions = root / "developer.md"
            instructions.write_text("Developer body\n", encoding="utf-8")

            spec = agent_bundle.AgentSpec(
                identifier="480-developer",
                display_name="480-developer",
                description="Developer",
                role="implementation",
                mode="subagent",
                model="openai/gpt-5.4",
                reasoning="medium",
                instruction_source=instructions,
                target_metadata={
                    "codex": {
                        "name": "implementer-role",
                        "compatibility_names": ["implementer-role-legacy"],
                        "sandbox_mode": "workspace-write",
                    }
                },
            )

            self.assertEqual(
                render_agents._codex_agent_output_names(spec, {"480-developer": "implementer-role"}),
                ["implementer-role", "implementer-role-legacy"],
            )

    def test_rendered_provider_outputs_use_provider_model_profiles(self) -> None:
        specs = {spec.identifier: spec for spec in agent_bundle.load_bundle()}

        claude_name_map = render_agents._claude_name_map(tuple(specs.values()))
        claude_reviewer = render_agents.render_claude_agent(specs["480-code-reviewer"], claude_name_map)
        self.assertIn("model: claude-opus-4-6", claude_reviewer)
        self.assertIn("effort: low", claude_reviewer)

        claude_reviewer2 = render_agents.render_claude_agent(specs["480-code-reviewer2"], claude_name_map)
        self.assertIn("model: claude-sonnet-4-6", claude_reviewer2)
        self.assertIn("effort: low", claude_reviewer2)

        opencode_reviewer2 = render_agents.render_opencode_agent(specs["480-code-reviewer2"])
        self.assertIn("model: google/gemini-3-flash-preview", opencode_reviewer2)
        self.assertIn("reasoningEffort: high", opencode_reviewer2)

        codex_name_map = render_agents._codex_name_map(tuple(specs.values()))
        codex_reviewer2 = tomllib.loads(
            render_agents.render_codex_agent(specs["480-code-reviewer2"], codex_name_map)
        )
        self.assertEqual(codex_reviewer2["model"], "gpt-5.4")
        self.assertEqual(codex_reviewer2["model_reasoning_effort"], "medium")

    def test_codex_reviewer_contract_and_model_alignment_are_pinned_in_sources_and_rendered_outputs(self) -> None:
        specs = {spec.identifier: spec for spec in agent_bundle.load_bundle()}
        codex_name_map = render_agents._codex_name_map(tuple(specs.values()))

        for agent_id, expected_model, expected_effort in (
            ("480-code-reviewer", "gpt-5.4", "high"),
            ("480-code-reviewer2", "gpt-5.4", "medium"),
        ):
            common_source_instruction = (
                REPO_ROOT / "bundles" / "common" / "instructions" / f"{agent_id}.md"
            ).read_text(encoding="utf-8")
            self.assert_reviewer_throughput_contract(common_source_instruction)

            source_instruction = (
                REPO_ROOT / "providers" / "codex" / "instructions" / f"{agent_id}.md"
            ).read_text(encoding="utf-8")
            self.assert_reviewer_throughput_contract(source_instruction)
            self.assert_codex_reviewer_feedback_contract(source_instruction)

            checked_in_toml = (
                provider_agents_source_dir("codex") / f"{agent_id}.toml"
            ).read_text(encoding="utf-8")
            checked_in_agent = tomllib.loads(checked_in_toml)
            self.assertEqual(checked_in_agent["model"], expected_model)
            self.assertEqual(checked_in_agent["model_reasoning_effort"], expected_effort)
            self.assert_reviewer_throughput_contract(checked_in_agent["developer_instructions"])
            self.assert_codex_reviewer_feedback_contract(checked_in_agent["developer_instructions"])

            rendered_agent = tomllib.loads(
                render_agents.render_codex_agent(specs[agent_id], codex_name_map)
            )
            self.assertEqual(rendered_agent["model"], expected_model)
            self.assertEqual(rendered_agent["model_reasoning_effort"], expected_effort)
            self.assertEqual(rendered_agent["developer_instructions"], checked_in_agent["developer_instructions"])
            self.assert_reviewer_throughput_contract(rendered_agent["developer_instructions"])
            self.assert_codex_reviewer_feedback_contract(rendered_agent["developer_instructions"])

    def test_check_outputs_reports_missing_and_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.patched_render_outputs_root(root):
                render_agents.write_outputs()

                missing_path = root / "providers" / "claude" / "agents" / "480-architect.md"
                missing_path.unlink()
                extra_agent = root / "providers" / "opencode" / "agents" / "stale.md"
                extra_agent.write_text("stale\n", encoding="utf-8")
                extra_codex = root / "providers" / "codex" / "agents" / "stale.toml"
                extra_codex.write_text("stale\n", encoding="utf-8")

                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = render_agents.check_outputs()

                self.assertEqual(result, 1)
                self.assertIn("Agent outputs are out of date:", stderr.getvalue())
                self.assertIn("providers/claude/agents/480-architect.md", stderr.getvalue())
                self.assertIn("providers/opencode/agents/stale.md", stderr.getvalue())
                self.assertIn("providers/codex/agents/stale.toml", stderr.getvalue())

    def test_write_outputs_restores_missing_extra_and_modified_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.patched_render_outputs_root(root):
                render_agents.write_outputs()

                managed_path = root / "providers" / "opencode" / "agents" / "480-architect.md"
                managed_path.write_text("modified\n", encoding="utf-8")
                missing_path = root / "providers" / "claude" / "agents" / "480-architect.md"
                missing_path.unlink()
                extra_path = root / "providers" / "codex" / "agents" / "stale.toml"
                extra_path.write_text("stale\n", encoding="utf-8")

                render_agents.write_outputs()

                specs = agent_bundle.load_bundle()
                claude_name_map = render_agents._claude_name_map(specs)
                architect_spec = next(spec for spec in specs if spec.identifier == "480-architect")
                self.assertEqual(
                    managed_path.read_text(encoding="utf-8"),
                    render_agents.render_opencode_agent(architect_spec),
                )
                self.assertEqual(
                    missing_path.read_text(encoding="utf-8"),
                    render_agents.render_claude_agent(architect_spec, claude_name_map),
                )
                self.assertFalse(extra_path.exists())
                self.assertEqual(render_agents.check_outputs(), 0)


if __name__ == "__main__":
    unittest.main()
