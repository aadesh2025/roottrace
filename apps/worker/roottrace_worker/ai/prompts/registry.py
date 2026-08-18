"""Prompt versioning (`06` §3.3, `A2` §10).

A prompt version is never edited in place — every `.md` file under this
package is immutable once shipped; a revision creates `v{n+1}.md` next to
it. `registry.yaml` names which version is *current* per stage; rolling
back a regression is editing that one file, not a deploy (`06` §3.3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from roottrace_worker.ai.prompts.assembly import normalise_prompt_file

PROMPTS_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = PROMPTS_DIR / "registry.yaml"


class InvalidPromptRegistryError(Exception):
    """`registry.yaml` failed to parse into something `A2` §10 describes,
    or named a stage/version whose `.md` file does not exist on disk."""


@dataclass(frozen=True, slots=True)
class PromptVersion:
    stage: str
    version: str
    text: str

    @property
    def prompt_version(self) -> str:
        """`04` §8's `llm_calls.prompt_version` — `{stage}/{version}`, so a
        historical run traces to the exact file that produced it (`06`
        §3.3) without a second lookup."""
        return f"{self.stage}/{self.version}"


class PromptRegistry:
    """Loads `registry.yaml` once and serves `.md` content by stage name.
    Reads happen at construction, not per-call — a prompt file changing on
    disk mid-run (a bad deploy) should not change behaviour mid-investigation."""

    def __init__(self, current: dict[str, str], *, prompts_dir: Path = PROMPTS_DIR) -> None:
        self._current = current
        self._dir = prompts_dir
        self._cache: dict[str, PromptVersion] = {}

    def get(self, stage: str) -> PromptVersion:
        cached = self._cache.get(stage)
        if cached is not None:
            return cached

        version = self._current.get(stage)
        if version is None:
            raise InvalidPromptRegistryError(f"no current version configured for stage {stage!r}")

        path = self._dir / stage / f"{version}.md"
        if not path.is_file():
            raise InvalidPromptRegistryError(f"{stage}/{version}.md does not exist at {path}")

        text = normalise_prompt_file(path.read_text(encoding="utf-8"))
        result = PromptVersion(stage=stage, version=version, text=text)
        self._cache[stage] = result
        return result


def _parse_registry(document: dict[str, Any]) -> dict[str, str]:
    current = document.get("current")
    if not isinstance(current, dict) or not current:
        raise InvalidPromptRegistryError("missing or empty top-level 'current' mapping")
    parsed: dict[str, str] = {}
    for stage, version in current.items():
        if not isinstance(stage, str) or not isinstance(version, str):
            raise InvalidPromptRegistryError(f"'current.{stage}' must be a string version")
        parsed[stage] = version
    return parsed


def load_prompt_registry(path: Path | str = DEFAULT_REGISTRY_PATH) -> PromptRegistry:
    text = Path(path).read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise InvalidPromptRegistryError(f"{path}: did not parse to a mapping")
    current = _parse_registry(document)
    return PromptRegistry(current, prompts_dir=Path(path).resolve().parent)
