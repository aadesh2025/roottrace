"""Prompt versioning (`06` §3.3, `A2` §10, T5.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from roottrace_worker.ai.prompts.registry import (
    InvalidPromptRegistryError,
    PromptRegistry,
    load_prompt_registry,
)

pytestmark = pytest.mark.unit

REAL_REGISTRY = (
    Path(__file__).resolve().parents[1] / "roottrace_worker" / "ai" / "prompts" / "registry.yaml"
)


def test_the_real_registry_loads_every_current_stage() -> None:
    registry = load_prompt_registry(REAL_REGISTRY)
    for stage in (
        "system",
        "understand",
        "reason",
        "patch",
        "critique",
        "repair",
        "pr_description",
        "schema_repair",
    ):
        version = registry.get(stage)
        assert version.text
        assert version.stage == stage


def test_prompt_version_property_combines_stage_and_version() -> None:
    registry = load_prompt_registry(REAL_REGISTRY)
    version = registry.get("understand")
    assert version.prompt_version == "understand/v3"


def test_a_second_get_for_the_same_stage_returns_the_cached_object() -> None:
    """Reads happen once, at first access — a prompt file changing on disk
    mid-run must not change behaviour mid-investigation."""
    registry = load_prompt_registry(REAL_REGISTRY)
    first = registry.get("reason")
    second = registry.get("reason")
    assert first is second


def test_a_stage_not_in_current_raises() -> None:
    registry = PromptRegistry({"understand": "v3"})
    with pytest.raises(InvalidPromptRegistryError, match="nonexistent"):
        registry.get("nonexistent")


def test_a_missing_md_file_raises(tmp_path: Path) -> None:
    registry = PromptRegistry({"ghost": "v1"}, prompts_dir=tmp_path)
    with pytest.raises(InvalidPromptRegistryError, match=r"ghost/v1\.md"):
        registry.get("ghost")


def test_a_document_missing_current_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("history: {}\n", encoding="utf-8")
    with pytest.raises(InvalidPromptRegistryError, match="current"):
        load_prompt_registry(path)


def test_an_empty_current_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("current: {}\n", encoding="utf-8")
    with pytest.raises(InvalidPromptRegistryError, match="current"):
        load_prompt_registry(path)


def test_a_non_string_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("current:\n  understand: 3\n", encoding="utf-8")
    with pytest.raises(InvalidPromptRegistryError, match="understand"):
        load_prompt_registry(path)


def test_a_document_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(InvalidPromptRegistryError, match="mapping"):
        load_prompt_registry(path)


def test_loaded_files_have_had_registry_normalisation_applied(tmp_path: Path) -> None:
    (tmp_path / "understand").mkdir()
    (tmp_path / "understand" / "v1.md").write_text("hello   \n\n\n", encoding="utf-8")
    registry = PromptRegistry({"understand": "v1"}, prompts_dir=tmp_path)
    assert registry.get("understand").text == "hello\n"
