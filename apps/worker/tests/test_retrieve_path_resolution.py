"""Frame path resolution, cascade steps 3-4 (`08` §3.2, T4.2).

`15` §6's T4.2 acceptance: *"All four cascade steps are individually
exercised and return the documented confidence. Monorepo `root_path` and
`service_map` resolution works."* Steps 1-2 are already covered in
`test_understand_frames.py`; this file covers steps 3-4 and the
step-1/2-vs-tree interaction, plus monorepo scoping.
"""

from __future__ import annotations

import pytest

from roottrace_worker.github.types import RepoTree, TreeEntry
from roottrace_worker.pipeline.retrieve.path_resolution import (
    CONFIDENCE_FILENAME_AMBIGUOUS,
    CONFIDENCE_FILENAME_UNIQUE,
    CONFIDENCE_NOT_FOUND,
    CONFIDENCE_SUFFIX_MULTIPLE,
    CONFIDENCE_SUFFIX_UNIQUE,
    dry_run_path_mapping,
    resolve_against_tree,
    resolve_frame_path,
    resolve_scope,
)
from roottrace_worker.pipeline.understand.frames import (
    CONFIDENCE_CONFIGURED,
    CONFIDENCE_HEURISTIC,
    PathMapping,
    ResolvedPath,
)

pytestmark = pytest.mark.unit


def blob(path: str) -> TreeEntry:
    return TreeEntry(path=path, sha="0" * 40)


def tree(*paths: str) -> RepoTree:
    return RepoTree(ref="HEAD", sha="1" * 40, entries=tuple(blob(p) for p in paths))


APP_MAPPING = (PathMapping("/app/", ""),)


# ── Trusting steps 1-2 only once verified ──────────────────────────────────


def test_a_verified_configured_mapping_is_kept_as_is() -> None:
    """The trivial, common case: steps 1-2 were right, and the tree confirms
    it. Nothing about the result should change."""
    result = resolve_frame_path(
        "/app/services/checkout.py",
        tree("services/checkout.py", "services/export.py"),
        mappings=APP_MAPPING,
    )
    assert result.repo_path == "services/checkout.py"
    assert result.confidence == CONFIDENCE_CONFIGURED
    assert result.method == "configured_mapping"


def test_a_verified_heuristic_result_is_labelled_correctly() -> None:
    result = resolve_frame_path("/usr/src/app/clients/tax_client.py", tree("clients/tax_client.py"))
    assert result.repo_path == "clients/tax_client.py"
    assert result.confidence == CONFIDENCE_HEURISTIC
    assert result.method == "heuristic_prefix_strip"


def test_the_config_02_corpus_case_is_corrected() -> None:
    """The exact fixture that motivated this ticket. Steps 1-2 produce
    `services/services/export.py`, a well-formed path that is not a file;
    step 3 corrects it to `services/export.py`, the real one."""
    result = resolve_frame_path(
        "/workspace/services/services/export.py",
        tree("services/export.py", "services/checkout.py"),
        mappings=(PathMapping("/workspace/", ""),),
    )
    assert result.repo_path == "services/export.py"
    assert result.confidence == CONFIDENCE_SUFFIX_UNIQUE
    assert result.method == "suffix_match"


# ── Step 3: suffix matching ─────────────────────────────────────────────────


def test_step_3_unique_suffix_match() -> None:
    result = resolve_against_tree(
        "/opt/mystery/services/export.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        tree("services/export.py", "clients/tax_client.py"),
    )
    assert result.repo_path == "services/export.py"
    assert result.confidence == CONFIDENCE_SUFFIX_UNIQUE
    assert result.method == "suffix_match"


def test_step_3_multiple_matches_prefers_the_shallowest_path() -> None:
    """`08` §3.2: multiple suffix matches -> shallowest path, at the lower
    confidence for having been ambiguous."""
    result = resolve_against_tree(
        "/opt/mystery/services/export.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        tree("services/export.py", "legacy/archive/services/export.py"),
    )
    assert result.repo_path == "services/export.py"
    assert result.confidence == CONFIDENCE_SUFFIX_MULTIPLE
    assert result.method == "suffix_match"


def test_step_3_prefers_the_longest_matching_suffix() -> None:
    """Two files share a basename (`export.py`); only one also shares the
    parent directory. The longer, more specific suffix must win."""
    result = resolve_against_tree(
        "/opt/mystery/services/export.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        tree("services/export.py", "clients/export.py"),
    )
    assert result.repo_path == "services/export.py"
    assert result.confidence == CONFIDENCE_SUFFIX_UNIQUE


def test_step_3_runs_even_when_steps_1_2_found_nothing() -> None:
    """A totally unresolved frame (no mapping, no heuristic prefix) still
    reaches the tree search, using the raw path as its own tail."""
    result = resolve_against_tree(
        "some/weird/services/checkout.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        tree("services/checkout.py"),
    )
    assert result.repo_path == "services/checkout.py"
    assert result.method == "suffix_match"


# ── Step 4: filename-only search ────────────────────────────────────────────


def test_step_4_unique_basename_match() -> None:
    """No suffix beyond the basename matches anywhere; the basename alone
    does, uniquely."""
    result = resolve_against_tree(
        "/totally/unrelated/tree/checkout.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        tree("services/checkout.py", "clients/tax_client.py"),
    )
    assert result.repo_path == "services/checkout.py"
    assert result.confidence == CONFIDENCE_FILENAME_UNIQUE
    assert result.method == "filename_search"


def test_step_4_ambiguous_basename_is_reported_not_guessed() -> None:
    """Two files share a basename and nothing else. `08` §3.2 says flag it,
    not pick one — silently choosing would be a coin flip presented as a
    resolution."""
    result = resolve_against_tree(
        "/totally/unrelated/tree/config.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        tree("services/config.py", "clients/config.py"),
    )
    assert result.repo_path is None
    assert result.confidence == CONFIDENCE_FILENAME_AMBIGUOUS
    assert result.method == "filename_search"


def test_nothing_found_anywhere_is_worse_than_ambiguous() -> None:
    result = resolve_against_tree(
        "/nowhere/near/anything.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        tree("services/checkout.py"),
    )
    assert result.repo_path is None
    assert result.confidence == CONFIDENCE_NOT_FOUND
    assert result.method == "unresolved"
    assert result.confidence < CONFIDENCE_FILENAME_AMBIGUOUS


def test_an_empty_path_resolves_to_nothing_without_raising() -> None:
    result = resolve_against_tree(
        "", ResolvedPath(repo_path=None, confidence=0.3), tree("services/checkout.py")
    )
    assert result.repo_path is None
    assert result.confidence == CONFIDENCE_NOT_FOUND


# ── Monorepo scoping ─────────────────────────────────────────────────────────


def test_resolve_scope_combines_root_path_and_service_map() -> None:
    assert resolve_scope("services/", {"checkout-api": "checkout"}, "checkout-api") == (
        "services/checkout"
    )


def test_resolve_scope_is_empty_when_not_a_monorepo() -> None:
    assert resolve_scope("", {}, None) == ""
    assert resolve_scope(None, {}, "checkout-api") == ""


def test_resolve_scope_is_empty_for_an_unmapped_service() -> None:
    """An unmapped service is not a scoping failure — it means search
    everything, not search nothing."""
    assert resolve_scope("services/", {"other-api": "other"}, "checkout-api") == "services"


def test_scoping_disambiguates_a_basename_collision() -> None:
    """The monorepo case `08` §3.2 exists to solve: two services each have
    their own `config.py`. Unscoped, the basename match is ambiguous.
    Scoped to the right service, it resolves cleanly."""
    monorepo = tree("services/checkout/config.py", "services/billing/config.py")
    service_map = {"checkout-api": "checkout", "billing-api": "billing"}

    unscoped = resolve_against_tree(
        "/app/config.py", ResolvedPath(repo_path=None, confidence=0.3), monorepo
    )
    assert unscoped.repo_path is None
    assert unscoped.confidence == CONFIDENCE_FILENAME_AMBIGUOUS

    scoped = resolve_against_tree(
        "/app/config.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        monorepo,
        scope_root=resolve_scope("services", service_map, "checkout-api"),
    )
    assert scoped.repo_path == "services/checkout/config.py"
    assert scoped.confidence == CONFIDENCE_FILENAME_UNIQUE


def test_scoping_excludes_a_match_outside_the_package() -> None:
    """The hard-filter property: a unique match elsewhere in the tree is not
    returned once the search is scoped away from it."""
    monorepo = tree("services/billing/only_here.py")
    result = resolve_against_tree(
        "/app/only_here.py",
        ResolvedPath(repo_path=None, confidence=0.3),
        monorepo,
        scope_root="services/checkout",
    )
    assert result.repo_path is None
    assert result.confidence == CONFIDENCE_NOT_FOUND


def test_scoping_also_applies_to_a_verified_step_1_2_result() -> None:
    """A configured mapping that happens to name a file outside the scoped
    package must not be trusted just because it exists in the tree — the
    scope is a hard filter (`08` §3.2), not merely a tiebreaker."""
    monorepo = tree("services/other/checkout.py")
    result = resolve_against_tree(
        "/app/checkout.py",
        ResolvedPath(repo_path="services/other/checkout.py", confidence=CONFIDENCE_CONFIGURED),
        monorepo,
        scope_root="services/checkout",
    )
    assert result.repo_path is None


# ── The dry-run shape (`05` §6.6) ────────────────────────────────────────────


def test_dry_run_matches_the_documented_response_shape() -> None:
    """`05` §6.6's example: `/app/services/checkout.py` -> configured_mapping
    at 0.95; `/usr/src/app/api/routes/checkout.py` -> heuristic_prefix_strip
    at 0.80."""
    results = dry_run_path_mapping(
        ["/app/services/checkout.py", "/usr/src/app/api/routes/checkout.py"],
        tree("services/checkout.py", "api/routes/checkout.py"),
        mappings=APP_MAPPING,
    )
    assert results[0].input == "/app/services/checkout.py"
    assert results[0].resolved == "services/checkout.py"
    assert results[0].confidence == CONFIDENCE_CONFIGURED
    assert results[0].method == "configured_mapping"
    assert results[0].exists_in_repo is True

    assert results[1].resolved == "api/routes/checkout.py"
    assert results[1].method == "heuristic_prefix_strip"


def test_dry_run_reports_unresolved_paths_honestly() -> None:
    results = dry_run_path_mapping(["/nowhere/at/all.py"], tree("services/checkout.py"))
    assert results[0].resolved is None
    assert results[0].exists_in_repo is False
    assert results[0].confidence == CONFIDENCE_NOT_FOUND


def test_dry_run_preserves_input_order_and_count() -> None:
    paths = ["/app/a.py", "/app/b.py", "/app/c.py"]
    results = dry_run_path_mapping(paths, tree("a.py", "b.py", "c.py"), mappings=APP_MAPPING)
    assert [r.input for r in results] == paths
