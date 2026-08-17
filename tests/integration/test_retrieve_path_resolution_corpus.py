"""T4.2 acceptance — frame path resolution, cascade steps 3-4, over the
25-case corpus (`15` §6).

> **Accept:** All four cascade steps are individually exercised and return
> the documented confidence. Monorepo `root_path` and `service_map`
> resolution works.

The per-step and monorepo-scoping exercise is in
`apps/worker/tests/test_retrieve_path_resolution.py`, in the plain-`unit`
style the rest of the codebase uses for algorithm coverage. This file is the
corpus-level proof: `test_understand_corpus.py` measures S4 in isolation (no
repository access, `03` §8.1) and scores frame paths at 24/25, with
`config-02` as the one documented, expected miss — a well-formed path that
does not resolve to a real file, and that only a tree is able to tell. This
file completes the cascade with the tree, exactly as S5's fetch loop (T4.3)
will, and the corpus reaches **25/25**.

Uses the real `FixtureTransport` against `fixtures/synthetic-repo`, so this
is proof against real git object data, not a constructed tree — the same
distinction `08` §7.4's GC1 makes for `fetch_file` and `fetch_tree`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from fixtures.triggers.cases import CASE_IDS
from roottrace_worker.github.fixture import FixtureTransport
from roottrace_worker.github.types import RepoRef, RepoTree
from roottrace_worker.pipeline.retrieve import resolve_frame_path
from roottrace_worker.pipeline.understand import PathMapping

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"
CORPUS = REPO_ROOT / "fixtures" / "error-corpus"

FIXTURE_REPO_REF = RepoRef(owner="acme", name="checkout-api")


def case(case_id: str) -> dict[str, Any]:
    return json.loads((CORPUS / f"{case_id}.case.json").read_text(encoding="utf-8"))


def payload(case_id: str) -> dict[str, Any]:
    return json.loads((CORPUS / f"{case_id}.json").read_text(encoding="utf-8"))


def project_mappings() -> tuple[PathMapping, ...]:
    metadata = json.loads((FIXTURE_REPO / ".roottrace-fixture.json").read_text(encoding="utf-8"))
    return tuple(
        PathMapping(mapping["from"], mapping["to"]) for mapping in metadata["path_mappings"]
    )


MAPPINGS = project_mappings()


@pytest.fixture(scope="module")
def fixture_tree() -> RepoTree:
    gateway = FixtureTransport(FIXTURE_REPO)
    return asyncio.run(gateway.fetch_tree(FIXTURE_REPO_REF, gateway.default_branch))


def resolved_paths(case_id: str, tree: RepoTree) -> list[str | None]:
    frames = payload(case_id)["events"][0]["error"]["stack_frames"]
    return [
        resolve_frame_path(frame["file"], tree, mappings=MAPPINGS).repo_path
        for frame in frames
        if frame.get("in_app")
    ]


def test_all_25_cases_resolve_completely(fixture_tree: RepoTree) -> None:
    """The number `15` §6 exists to move: with cascade steps 3-4, every
    corpus frame resolves to the file the payload was captured from."""
    wrong = {
        case_id: resolved_paths(case_id, fixture_tree)
        for case_id in CASE_IDS
        if resolved_paths(case_id, fixture_tree) != case(case_id)["expected"]["frame_repo_paths"]
    }
    assert wrong == {}, wrong


def test_config_02_is_the_case_this_ticket_fixes(fixture_tree: RepoTree) -> None:
    """`config-02` reports `/workspace/services/services/export.py`. S4 alone
    strips the documented prefix and gets a well-formed path that is not a
    real file (`test_understand_corpus.py::test_the_case_that_needs_the_tree_is_still_honest`).
    With the tree, suffix matching corrects it."""
    frames = payload("config-02")["events"][0]["error"]["stack_frames"]
    raw = next(frame["file"] for frame in frames if frame.get("in_app"))
    result = resolve_frame_path(raw, fixture_tree, mappings=MAPPINGS)
    assert result.repo_path == "services/export.py"
    assert result.method == "suffix_match"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_resolved_path_exists_in_the_repository(case_id: str, fixture_tree: RepoTree) -> None:
    """The check that cannot drift: every path this resolver returns is
    checked against the real tree by construction, but this asserts it
    against the filesystem too, independent of the resolver's own logic."""
    for repo_path in resolved_paths(case_id, fixture_tree):
        if repo_path is None:
            continue
        assert (FIXTURE_REPO / repo_path).is_file(), f"{case_id}: {repo_path} is not a file"
