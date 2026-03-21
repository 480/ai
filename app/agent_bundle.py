from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from .install_targets import get_provider
except ImportError:
    from install_targets import get_provider


REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_PATH = REPO_ROOT / "bundles" / "common" / "agents.json"


@dataclass(frozen=True)
class AgentSpec:
    identifier: str
    display_name: str
    description: str
    role: str
    mode: str
    model: str
    reasoning: str
    instruction_source: Path
    target_metadata: dict[str, object]

    @property
    def opencode_metadata(self) -> dict[str, object]:
        metadata = self.target_metadata.get("opencode")
        if not isinstance(metadata, dict):
            raise ValueError(f"Missing opencode target metadata for {self.identifier}.")
        return metadata

    def metadata_for_target(self, target: str) -> dict[str, object]:
        metadata = self.target_metadata.get(target)
        if not isinstance(metadata, dict):
            raise ValueError(f"Missing {target} target metadata for {self.identifier}.")
        return metadata

    def instruction_source_for_target(self, target: str) -> Path:
        metadata = self.metadata_for_target(target)
        override = metadata.get("instruction_source")
        if override is None:
            return self.instruction_source
        if not isinstance(override, str) or not override:
            raise ValueError(f"Invalid '{target}.instruction_source' for {self.identifier} in {BUNDLE_PATH}.")

        instruction_source = REPO_ROOT / override
        if not instruction_source.exists():
            raise ValueError(f"Missing instruction source for {self.identifier} ({target}): {instruction_source}")
        return instruction_source


def _require_string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid '{key}' in {BUNDLE_PATH}.")
    return value


def _require_object(data: dict[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid '{key}' in {BUNDLE_PATH}.")
    return value


@lru_cache(maxsize=1)
def load_bundle() -> tuple[AgentSpec, ...]:
    manifest = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Expected JSON object at {BUNDLE_PATH}.")

    bundle_version = manifest.get("bundle_version")
    if bundle_version != 1:
        raise ValueError(f"Unsupported bundle_version in {BUNDLE_PATH}: {bundle_version!r}")

    roles = manifest.get("roles")
    if not isinstance(roles, list) or not roles:
        raise ValueError(f"Invalid 'roles' in {BUNDLE_PATH}.")

    seen_ids: set[str] = set()
    specs: list[AgentSpec] = []
    for role in roles:
        if not isinstance(role, dict):
            raise ValueError(f"Invalid role entry in {BUNDLE_PATH}.")

        identifier = _require_string(role, "id")
        if identifier in seen_ids:
            raise ValueError(f"Duplicate role id '{identifier}' in {BUNDLE_PATH}.")
        seen_ids.add(identifier)

        instruction_source = REPO_ROOT / _require_string(role, "instruction_source")
        if not instruction_source.exists():
            raise ValueError(f"Missing instruction source for {identifier}: {instruction_source}")

        specs.append(
            AgentSpec(
                identifier=identifier,
                display_name=_require_string(role, "display_name"),
                description=_require_string(role, "description"),
                role=_require_string(role, "role"),
                mode=_require_string(role, "mode"),
                model=_require_string(role, "model"),
                reasoning=_require_string(role, "reasoning"),
                instruction_source=instruction_source,
                target_metadata=_require_object(role, "target_metadata"),
            )
        )

    return tuple(specs)


def agent_names() -> list[str]:
    return [spec.identifier for spec in load_bundle()]


def target_agent_names(target: str) -> list[str]:
    provider = get_provider(target)
    agent_names: list[str] = []
    for spec in load_bundle():
        if target == "codex" and spec.mode == "primary":
            continue
        agent_names.append(provider.bundle_agent_name(spec))
        agent_names.extend(provider.compatibility_agent_names(spec))
    return agent_names
