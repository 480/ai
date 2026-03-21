from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BootstrapStatePaths:
    state_dir: Path
    backup_dir: Path
    state_file: Path


@dataclass(frozen=True)
class DefaultActivation:
    config_key: str
    managed_value: str


@dataclass(frozen=True)
class InstallPaths:
    config_dir: Path
    config_file: Path | None
    installed_agents_dir: Path
    state: BootstrapStatePaths

    @property
    def state_dir(self) -> Path:
        return self.state.state_dir

    @property
    def backup_dir(self) -> Path:
        return self.state.backup_dir

    @property
    def state_file(self) -> Path:
        return self.state.state_file


@dataclass(frozen=True)
class InstallTarget:
    name: str
    label: str
    scope: str
    paths: InstallPaths
    default_activation: DefaultActivation | None = None
    agent_file_extension: str = ".md"


@dataclass(frozen=True)
class ProviderArtifacts:
    agents_dirname: str
    index_filename: str
    agent_file_extension: str

    def agents_dir(self, repo_root: Path) -> Path:
        return repo_root / self.agents_dirname

    def index_path(self, repo_root: Path) -> Path:
        return repo_root / self.index_filename


@dataclass(frozen=True)
class ProviderRoleModelConfig:
    model: str
    effort: str


@dataclass(frozen=True)
class ProviderRoleModelOption:
    key: str
    label: str
    config: ProviderRoleModelConfig
    note: str = ""


@dataclass(frozen=True)
class ProviderRecommendedModelProfile:
    source: str
    roles: dict[str, ProviderRoleModelConfig] = field(default_factory=dict)
    overrides: dict[str, ProviderRoleModelConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderModelSelectionSchema:
    supported_modes: tuple[str, ...]
    recommended: ProviderRecommendedModelProfile
    advanced: dict[str, tuple[ProviderRoleModelOption, ...]]


@dataclass(frozen=True)
class ProviderModelSelection:
    mode: str
    role_options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSpec:
    identifier: str
    label: str
    cli_binary_name: str
    supported_scopes: tuple[str, ...]
    user_config_dir_parts: tuple[str, ...]
    project_config_dirname: str | None
    config_filename: str | None
    default_activation: DefaultActivation | None
    default_activation_default: bool | None
    artifacts: ProviderArtifacts
    model_selection_schema: ProviderModelSelectionSchema
    bundle_name_key: str | None = None
    project_state_location: str = "external"
    installed_agents_subdir: str = "agents"

    def bundle_agent_name(self, spec: object) -> str:
        if self.bundle_name_key is None:
            identifier = getattr(spec, "identifier", None)
            if not isinstance(identifier, str) or not identifier:
                raise ValueError(f"Invalid provider agent identifier for {self.identifier}.")
            return identifier

        metadata_for_target = getattr(spec, "metadata_for_target", None)
        if not callable(metadata_for_target):
            raise ValueError(f"Invalid provider metadata access for {self.identifier}.")
        metadata = metadata_for_target(self.identifier)
        if not isinstance(metadata, dict):
            raise ValueError(f"Missing {self.identifier} target metadata.")
        name = metadata.get(self.bundle_name_key)
        if not isinstance(name, str) or not name:
            raise ValueError(f"Invalid {self.label} agent name.")
        return name

    def compatibility_agent_names(self, spec: object) -> list[str]:
        metadata_for_target = getattr(spec, "metadata_for_target", None)
        if not callable(metadata_for_target):
            raise ValueError(f"Invalid provider metadata access for {self.identifier}.")
        metadata = metadata_for_target(self.identifier)
        if not isinstance(metadata, dict):
            raise ValueError(f"Missing {self.identifier} target metadata.")
        compatibility_names = metadata.get("compatibility_names")
        if compatibility_names is None:
            return []
        if not isinstance(compatibility_names, list) or not all(
            isinstance(name, str) and name for name in compatibility_names
        ):
            raise ValueError(f"Invalid {self.label} compatibility agent names.")
        return compatibility_names.copy()

    def resolve_install_target(self, scope: str, home: Path | None = None) -> InstallTarget:
        resolved_home = Path.home() if home is None else home
        if scope not in self.supported_scopes:
            raise SystemExit(f"Unsupported install scope for {self.identifier}: {scope}")

        if scope == "user":
            config_dir = resolved_home.joinpath(*self.user_config_dir_parts)
            state = bootstrap_state_paths(config_dir / ".480ai-bootstrap")
        else:
            project_root = resolve_project_root()
            if self.project_config_dirname is None:
                raise SystemExit(f"Unsupported install scope for {self.identifier}: {scope}")
            config_dir = project_root / self.project_config_dirname
            if self.project_state_location == "external":
                state = project_bootstrap_state_paths(self.identifier, scope, project_root, home=resolved_home)
            elif self.project_state_location == "local":
                state = bootstrap_state_paths(config_dir / ".480ai-bootstrap")
            else:
                raise SystemExit(f"Unsupported project state location for {self.identifier}: {self.project_state_location}")

        config_file = None if self.config_filename is None else config_dir / self.config_filename
        return InstallTarget(
            name=self.identifier,
            label=self.label,
            scope=scope,
            paths=InstallPaths(
                config_dir=config_dir,
                config_file=config_file,
                installed_agents_dir=config_dir / self.installed_agents_subdir,
                state=state,
            ),
            default_activation=self.default_activation,
            agent_file_extension=self.artifacts.agent_file_extension,
        )

    def source_agents_dir(self, repo_root: Path) -> Path:
        return self.artifacts.agents_dir(repo_root)

    def supported_model_selection_modes(self) -> tuple[str, ...]:
        return self.model_selection_schema.supported_modes

    def recommended_role_model_config(self, spec: object) -> ProviderRoleModelConfig:
        identifier = getattr(spec, "identifier", None)
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"Invalid provider agent identifier for {self.identifier}.")

        override = self.model_selection_schema.recommended.overrides.get(identifier)
        if override is not None:
            return override

        if self.model_selection_schema.recommended.source == "bundle":
            model = getattr(spec, "model", None)
            effort = getattr(spec, "reasoning", None)
            if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
                raise ValueError(f"Invalid bundle-backed model profile for {identifier}.")
            return ProviderRoleModelConfig(model=model, effort=effort)

        if self.model_selection_schema.recommended.source == "fixed":
            config = self.model_selection_schema.recommended.roles.get(identifier)
            if config is None:
                raise ValueError(f"Missing recommended {self.label} model profile for {identifier}.")
            return config

        raise ValueError(
            f"Unsupported recommended model source for {self.identifier}: {self.model_selection_schema.recommended.source}"
        )

    def advanced_role_model_options(self, role_id: str) -> tuple[ProviderRoleModelOption, ...]:
        options = self.model_selection_schema.advanced.get(role_id)
        if not options:
            raise ValueError(f"Missing advanced {self.label} model options for {role_id}.")
        return options

    def advanced_role_model_option(self, role_id: str, option_key: str) -> ProviderRoleModelOption:
        for option in self.advanced_role_model_options(role_id):
            if option.key == option_key:
                return option
        raise ValueError(f"Unsupported advanced {self.label} model option for {role_id}: {option_key}")

    def default_advanced_role_model_option(self, spec: object) -> ProviderRoleModelOption:
        identifier = getattr(spec, "identifier", None)
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"Invalid provider agent identifier for {self.identifier}.")

        recommended = self.recommended_role_model_config(spec)
        options = self.advanced_role_model_options(identifier)
        for option in options:
            if option.config == recommended:
                return option
        return options[0]

    def resolve_role_model_config(
        self,
        spec: object,
        model_selection: ProviderModelSelection | None = None,
    ) -> ProviderRoleModelConfig:
        if model_selection is None or model_selection.mode == "recommended":
            return self.recommended_role_model_config(spec)
        if model_selection.mode != "advanced":
            raise ValueError(f"Unsupported model selection mode for {self.identifier}: {model_selection.mode}")

        identifier = getattr(spec, "identifier", None)
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"Invalid provider agent identifier for {self.identifier}.")

        option_key = model_selection.role_options.get(identifier)
        if option_key is None:
            return self.default_advanced_role_model_option(spec).config
        return self.advanced_role_model_option(identifier, option_key).config


def _role_config(model: str, effort: str) -> ProviderRoleModelConfig:
    return ProviderRoleModelConfig(model=model, effort=effort)


def _role_option(
    key: str,
    label: str,
    *,
    model: str,
    effort: str,
    note: str = "",
) -> ProviderRoleModelOption:
    return ProviderRoleModelOption(
        key=key,
        label=label,
        config=_role_config(model, effort),
        note=note,
    )


OPENCODE_MODEL_SELECTION_SCHEMA = ProviderModelSelectionSchema(
    supported_modes=("recommended", "advanced"),
    recommended=ProviderRecommendedModelProfile(source="bundle"),
    advanced={
        "480-architect": (
            _role_option("gpt-5.4-xhigh", "GPT-5.4 / xhigh", model="openai/gpt-5.4", effort="xhigh"),
            _role_option("gpt-5.4-high", "GPT-5.4 / high", model="openai/gpt-5.4", effort="high"),
            _role_option(
                "gemini-flash-high",
                "Gemini Flash / high",
                model="google/gemini-3-flash-preview",
                effort="high",
            ),
        ),
        "480-developer": (
            _role_option("gpt-5.4-medium", "GPT-5.4 / medium", model="openai/gpt-5.4", effort="medium"),
            _role_option("gpt-5.4-low", "GPT-5.4 / low", model="openai/gpt-5.4", effort="low"),
            _role_option(
                "gemini-flash-medium",
                "Gemini Flash / medium",
                model="google/gemini-3-flash-preview",
                effort="medium",
            ),
        ),
        "480-code-reviewer": (
            _role_option("gpt-5.4-high", "GPT-5.4 / high", model="openai/gpt-5.4", effort="high"),
            _role_option("gpt-5.4-medium", "GPT-5.4 / medium", model="openai/gpt-5.4", effort="medium"),
            _role_option(
                "gemini-flash-low",
                "Gemini Flash / low",
                model="google/gemini-3-flash-preview",
                effort="low",
            ),
        ),
        "480-code-reviewer2": (
            _role_option(
                "gemini-flash-high",
                "Gemini Flash / high",
                model="google/gemini-3-flash-preview",
                effort="high",
            ),
            _role_option("gpt-5.4-medium", "GPT-5.4 / medium", model="openai/gpt-5.4", effort="medium"),
            _role_option(
                "gpt-5.4-nano-low",
                "GPT-5.4 Nano / low",
                model="openai/gpt-5.4-nano",
                effort="low",
            ),
        ),
        "480-code-scanner": (
            _role_option(
                "gpt-5.4-nano-high",
                "GPT-5.4 Nano / high",
                model="openai/gpt-5.4-nano",
                effort="high",
            ),
            _role_option(
                "gpt-5.4-nano-medium",
                "GPT-5.4 Nano / medium",
                model="openai/gpt-5.4-nano",
                effort="medium",
            ),
            _role_option(
                "gemini-flash-low",
                "Gemini Flash / low",
                model="google/gemini-3-flash-preview",
                effort="low",
            ),
        ),
    },
)


CLAUDE_MODEL_SELECTION_SCHEMA = ProviderModelSelectionSchema(
    supported_modes=("recommended", "advanced"),
    recommended=ProviderRecommendedModelProfile(
        source="fixed",
        roles={
            "480-architect": _role_config("claude-opus-4-6", "max"),
            "480-developer": _role_config("claude-sonnet-4-6", "medium"),
            "480-code-scanner": _role_config("haiku", "low"),
            "480-code-reviewer": _role_config("claude-opus-4-6", "low"),
            "480-code-reviewer2": _role_config("claude-sonnet-4-6", "low"),
        },
    ),
    advanced={
        "480-architect": (
            _role_option("opus-max", "Opus 4.6 / max", model="claude-opus-4-6", effort="max"),
            _role_option("sonnet-max", "Sonnet 4.6 / max", model="claude-sonnet-4-6", effort="max"),
            _role_option(
                "sonnet-medium",
                "Sonnet 4.6 / medium",
                model="claude-sonnet-4-6",
                effort="medium",
            ),
        ),
        "480-developer": (
            _role_option(
                "sonnet-medium",
                "Sonnet 4.6 / medium",
                model="claude-sonnet-4-6",
                effort="medium",
            ),
            _role_option("sonnet-low", "Sonnet 4.6 / low", model="claude-sonnet-4-6", effort="low"),
            _role_option("opus-medium", "Opus 4.6 / medium", model="claude-opus-4-6", effort="medium"),
        ),
        "480-code-reviewer": (
            _role_option("opus-low", "Opus 4.6 / low", model="claude-opus-4-6", effort="low"),
            _role_option("sonnet-low", "Sonnet 4.6 / low", model="claude-sonnet-4-6", effort="low"),
            _role_option("opus-medium", "Opus 4.6 / medium", model="claude-opus-4-6", effort="medium"),
        ),
        "480-code-reviewer2": (
            _role_option(
                "sonnet-low",
                "Sonnet 4.6 / low",
                model="claude-sonnet-4-6",
                effort="low",
            ),
            _role_option("haiku-low", "Haiku / low", model="haiku", effort="low"),
            _role_option(
                "sonnet-medium",
                "Sonnet 4.6 / medium",
                model="claude-sonnet-4-6",
                effort="medium",
            ),
        ),
        "480-code-scanner": (
            _role_option("haiku-low", "Haiku / low", model="haiku", effort="low"),
            _role_option("sonnet-low", "Sonnet 4.6 / low", model="claude-sonnet-4-6", effort="low"),
        ),
    },
)


CODEX_MODEL_SELECTION_SCHEMA = ProviderModelSelectionSchema(
    supported_modes=("recommended", "advanced"),
    recommended=ProviderRecommendedModelProfile(
        source="fixed",
        roles={
            "480-architect": _role_config("gpt-5.4", "xhigh"),
            "480-developer": _role_config("gpt-5.4", "medium"),
            "480-code-scanner": _role_config("gpt-5.4-mini", "low"),
            "480-code-reviewer": _role_config("gpt-5.4", "high"),
            "480-code-reviewer2": _role_config("gpt-5.4-mini", "medium"),
        },
    ),
    advanced={
        "480-architect": (
            _role_option("gpt-5.4-xhigh", "GPT-5.4 / xhigh", model="gpt-5.4", effort="xhigh"),
            _role_option("gpt-5.4-high", "GPT-5.4 / high", model="gpt-5.4", effort="high"),
            _role_option(
                "spark-high",
                "Codex Spark / high",
                model="gpt-5.3-codex-spark",
                effort="high",
            ),
        ),
        "480-developer": (
            _role_option("gpt-5.4-medium", "GPT-5.4 / medium", model="gpt-5.4", effort="medium"),
            _role_option(
                "spark-medium",
                "Codex Spark / medium",
                model="gpt-5.3-codex-spark",
                effort="medium",
            ),
            _role_option("gpt-5.4-low", "GPT-5.4 / low", model="gpt-5.4", effort="low"),
        ),
        "480-code-reviewer": (
            _role_option("gpt-5.4-high", "GPT-5.4 / high", model="gpt-5.4", effort="high"),
            _role_option(
                "spark-medium",
                "Codex Spark / medium",
                model="gpt-5.3-codex-spark",
                effort="medium",
            ),
            _role_option("gpt-5.4-medium", "GPT-5.4 / medium", model="gpt-5.4", effort="medium"),
        ),
        "480-code-reviewer2": (
            _role_option(
                "spark-medium",
                "Codex Spark / medium",
                model="gpt-5.3-codex-spark",
                effort="medium",
            ),
            _role_option(
                "gpt-5.4-mini-high",
                "GPT-5.4 Mini / high",
                model="gpt-5.4-mini",
                effort="high",
            ),
            _role_option("gpt-5.4-low", "GPT-5.4 / low", model="gpt-5.4", effort="low"),
        ),
        "480-code-scanner": (
            _role_option(
                "gpt-5.4-mini-high",
                "GPT-5.4 Mini / high",
                model="gpt-5.4-mini",
                effort="high",
            ),
            _role_option(
                "spark-low",
                "Codex Spark / low",
                model="gpt-5.3-codex-spark",
                effort="low",
            ),
            _role_option(
                "gpt-5.4-mini-medium",
                "GPT-5.4 Mini / medium",
                model="gpt-5.4-mini",
                effort="medium",
            ),
        ),
    },
)


def bootstrap_state_paths(state_dir: Path) -> BootstrapStatePaths:
    return BootstrapStatePaths(
        state_dir=state_dir,
        backup_dir=state_dir / "backups",
        state_file=state_dir / "state.json",
    )


def resolve_project_root(current_dir: Path | None = None) -> Path:
    resolved_current_dir = (Path.cwd() if current_dir is None else current_dir).expanduser().resolve()
    for candidate in (resolved_current_dir, *resolved_current_dir.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved_current_dir


def project_state_key(project_root: Path) -> str:
    resolved_project_root = project_root.expanduser().resolve()
    digest = hashlib.sha256(str(resolved_project_root).encode("utf-8")).hexdigest()[:16]
    name = resolved_project_root.name or "project"
    return f"{name}-{digest}"


def project_bootstrap_state_paths(
    target: str,
    scope: str,
    project_root: Path,
    home: Path | None = None,
) -> BootstrapStatePaths:
    resolved_home = Path.home() if home is None else home
    state_dir = (
        resolved_home
        / ".config"
        / "480ai"
        / "bootstrap-state"
        / target
        / scope
        / project_state_key(project_root)
    )
    return bootstrap_state_paths(state_dir)


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        identifier="opencode",
        label="OpenCode",
        cli_binary_name="opencode",
        supported_scopes=("user",),
        user_config_dir_parts=(".config", "opencode"),
        project_config_dirname=None,
        config_filename="opencode.json",
        default_activation=DefaultActivation(
            config_key="default_agent",
            managed_value="480-architect",
        ),
        default_activation_default=True,
        artifacts=ProviderArtifacts(
            agents_dirname="providers/opencode/agents",
            index_filename="providers/opencode/AGENTS.md",
            agent_file_extension=".md",
        ),
        model_selection_schema=OPENCODE_MODEL_SELECTION_SCHEMA,
    ),
    ProviderSpec(
        identifier="claude",
        label="Claude Code",
        cli_binary_name="claude",
        supported_scopes=("user", "project"),
        user_config_dir_parts=(".claude",),
        project_config_dirname=".claude",
        config_filename="settings.json",
        default_activation=DefaultActivation(
            config_key="agent",
            managed_value="480-architect",
        ),
        default_activation_default=False,
        artifacts=ProviderArtifacts(
            agents_dirname="providers/claude/agents",
            index_filename="providers/claude/AGENTS.md",
            agent_file_extension=".md",
        ),
        model_selection_schema=CLAUDE_MODEL_SELECTION_SCHEMA,
        bundle_name_key="name",
    ),
    ProviderSpec(
        identifier="codex",
        label="Codex CLI",
        cli_binary_name="codex",
        supported_scopes=("user", "project"),
        user_config_dir_parts=(".codex",),
        project_config_dirname=".codex",
        config_filename="config.toml",
        default_activation=None,
        default_activation_default=None,
        artifacts=ProviderArtifacts(
            agents_dirname="providers/codex/agents",
            index_filename="providers/codex/AGENTS.md",
            agent_file_extension=".toml",
        ),
        model_selection_schema=CODEX_MODEL_SELECTION_SCHEMA,
        bundle_name_key="name",
    ),
)


PROVIDER_MAP = {provider.identifier: provider for provider in PROVIDERS}


def all_providers() -> tuple[ProviderSpec, ...]:
    return PROVIDERS


def get_provider(target: str) -> ProviderSpec:
    provider = PROVIDER_MAP.get(target)
    if provider is None:
        raise SystemExit(f"Unsupported install target: {target}")
    return provider


def resolve_install_target(target: str, scope: str, home: Path | None = None) -> InstallTarget:
    return get_provider(target).resolve_install_target(scope, home=home)
