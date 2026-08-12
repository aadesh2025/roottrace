"""The transport contract (`08` §7.4) — GC1 through GC12.

One suite, parameterised over transports. *A transport is not complete until it
passes every case.* Only `fixture` exists in V1, so the suite runs once today
and once per transport the day another is built — the parameterisation is the
point, not a formality.

**GC1 and GC8 are the load-bearing pair.** If fixture and live return the same
bytes for the same file, and render the same PR body from the same
investigation, then every stage between them is transport-blind by
construction.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from roottrace_worker.github import (
    Actor,
    AlreadyExists,
    FileNotFound,
    GitHubGateway,
    NoDiff,
    PullRequestDraft,
    PullRequestRef,
    RefNotFound,
    RepoRef,
    TreeEntry,
    build_gateway,
)
from roottrace_worker.github.fixture import blob_sha

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"

REPO = RepoRef(owner="acme", name="checkout-api")
REF = "v2.14.3"

#: `replay` and `live` are listed and skipped rather than omitted. An omitted
#: transport is one nobody remembers to add; a skipped one is a visible gap
#: that names its ticket.
TRANSPORTS = [
    "fixture",
    pytest.param("replay", marks=pytest.mark.skip(reason="no cassettes until a real repo exists")),
    pytest.param("live", marks=pytest.mark.skip(reason="V2 — evaluation tier holds no App key")),
]


class _Settings:
    """Structurally a `Settings`, with no dependency on the api package."""

    def __init__(self, mode: str):
        self.github_mode = mode
        self.github_fixture_path = str(FIXTURE_REPO)


@pytest.fixture(params=TRANSPORTS)
def gateway(request: pytest.FixtureRequest) -> Iterator[GitHubGateway]:
    yield build_gateway(_Settings(request.param))


# ── GC1 — fetch_file at a known ref ────────────────────────────────────────


async def test_gc1_fetch_file_returns_content_and_a_git_sha(gateway: GitHubGateway) -> None:
    result = await gateway.fetch_file(REPO, "clients/tax_client.py", REF)

    assert result.path == "clients/tax_client.py"
    assert result.ref == REF
    assert "def get_rate" in result.content
    assert result.sha == blob_sha(result.content)


async def test_gc1_the_sha_is_the_one_git_computes(gateway: GitHubGateway) -> None:
    """The load-bearing half of the parity guarantee.

    GitHub reports the git blob id. A transport that invented plausible hex
    would satisfy every test we can write today and diverge the day `live`
    exists — so this is checked against the git object format itself, not
    against our own helper.
    """
    result = await gateway.fetch_file(REPO, "clients/tax_client.py", REF)

    payload = result.content.encode()
    expected = hashlib.sha1(
        b"blob " + str(len(payload)).encode() + b"\0" + payload, usedforsecurity=False
    ).hexdigest()
    assert result.sha == expected


GIT = shutil.which("git")


@pytest.mark.skipif(GIT is None, reason="git is not available to cross-check the object id")
async def test_gc1_git_itself_agrees(gateway: GitHubGateway) -> None:
    """Cross-checked against `git hash-object`, not just our own arithmetic.

    Reimplementing a hash and testing it against the same reimplementation
    proves only that the code is self-consistent. This is the test that would
    catch a subtly wrong header format — which would produce ids that look
    perfectly plausible and match nothing GitHub returns.
    """
    result = await gateway.fetch_file(REPO, "clients/tax_client.py", REF)

    def hash_object() -> str:
        completed = subprocess.run(  # noqa: S603 — absolute path, fixed argv, no shell
            [str(GIT), "hash-object", "--stdin"],
            input=result.content.encode(),
            capture_output=True,
            check=True,
        )
        return completed.stdout.decode().strip()

    assert result.sha == await asyncio.to_thread(hash_object)


# ── GC2 — missing path ─────────────────────────────────────────────────────


async def test_gc2_missing_path_raises_rather_than_returning_empty(
    gateway: GitHubGateway,
) -> None:
    """Empty content is indistinguishable from a legitimately empty file, and
    a stage that retrieved "nothing" would reason about a file that is not
    there."""
    with pytest.raises(FileNotFound):
        await gateway.fetch_file(REPO, "services/does_not_exist.py", REF)


async def test_gc2_a_path_escaping_the_repository_is_refused(gateway: GitHubGateway) -> None:
    """Frame paths and model-generated patches are untrusted input."""
    with pytest.raises(FileNotFound):
        await gateway.fetch_file(REPO, "../../../etc/passwd", REF)


# ── GC3 — fetch_tree ───────────────────────────────────────────────────────


async def test_gc3_tree_lists_the_repository_sorted(gateway: GitHubGateway) -> None:
    tree = await gateway.fetch_tree(REPO, REF)
    paths = list(tree.paths())

    assert paths == sorted(paths)
    assert "clients/tax_client.py" in paths
    assert "services/checkout.py" in paths


async def test_gc3_tree_excludes_build_artefacts_and_fixture_metadata(
    gateway: GitHubGateway,
) -> None:
    """`__pycache__` is not part of the repository under analysis, and
    `.roottrace-fixture.json` is our scaffolding — retrieval that surfaced
    either would be learning from something a real repository does not have."""
    paths = (await gateway.fetch_tree(REPO, REF)).paths()

    assert not [path for path in paths if "__pycache__" in path]
    assert ".roottrace-fixture.json" not in paths


async def test_gc3_every_tree_entry_carries_a_real_blob_sha(gateway: GitHubGateway) -> None:
    tree = await gateway.fetch_tree(REPO, REF)
    entry = next(item for item in tree.entries if item.path == "services/checkout.py")
    content = await gateway.fetch_file(REPO, entry.path, REF)
    assert entry.sha == content.sha


# ── GC4 — fetch_files ──────────────────────────────────────────────────────


async def test_gc4_batch_matches_individual_fetches_in_order(gateway: GitHubGateway) -> None:
    paths = ["services/checkout.py", "clients/tax_client.py", "services/quote.py"]

    batched = await gateway.fetch_files(REPO, paths, REF)
    individually = [await gateway.fetch_file(REPO, path, REF) for path in paths]

    assert batched == individually
    assert [item.path for item in batched] == paths


async def test_gc4_a_missing_path_in_a_batch_still_raises(gateway: GitHubGateway) -> None:
    """Silently dropping it would give retrieval a shorter list than it asked
    for, and nothing downstream would know."""
    with pytest.raises(FileNotFound):
        await gateway.fetch_files(REPO, ["services/checkout.py", "nope.py"], REF)


# ── GC5 — blame ────────────────────────────────────────────────────────────


async def test_gc5_blame_names_the_introducing_commit(gateway: GitHubGateway) -> None:
    """`18` §7 attributes the canonical defect at lines 38-43 to 8a3f1c2e, and
    the whole "introduced by" claim rests on blame agreeing."""
    ranges = await gateway.blame(REPO, "clients/tax_client.py", REF, (38, 43))

    assert ranges
    assert ranges[0].commit.short_sha == "8a3f1c2e"
    assert ranges[0].commit.author.email == "dana@acme.io"
    assert ranges[0].covers(41)


async def test_gc5_blame_is_filtered_to_the_requested_lines(gateway: GitHubGateway) -> None:
    ranges = await gateway.blame(REPO, "clients/tax_client.py", REF, (1, 10))

    assert ranges
    assert all(item.start_line <= 10 for item in ranges)
    assert {item.commit.short_sha for item in ranges} == {"3c1a9d2f"}


async def test_gc5_blame_on_an_unrecorded_file_is_empty_not_an_error(
    gateway: GitHubGateway,
) -> None:
    assert await gateway.blame(REPO, "models/order.py", REF, (1, 5)) == []


# ── GC6 — compare ──────────────────────────────────────────────────────────


async def test_gc6_compare_across_releases_lists_changed_files(gateway: GitHubGateway) -> None:
    """Release correlation is close to conclusive and costs one call: if the
    failing function was modified between the previous release and the one
    that errored, that is stronger evidence than most reasoning."""
    result = await gateway.compare(REPO, "v2.14.2", "v2.14.3")

    assert "clients/tax_client.py" in result.changed_paths()
    assert "services/checkout.py" in result.changed_paths()
    assert result.changed_paths() == tuple(sorted(result.changed_paths()))


async def test_gc6_compare_excludes_the_base_commit(gateway: GitHubGateway) -> None:
    """`base...head` is exclusive of the base. Including it would attribute
    the previous release's changes to this one."""
    result = await gateway.compare(REPO, "v2.14.2", "v2.14.3")
    assert "6d4b8e1f" not in {commit.short_sha for commit in result.commits}


async def test_gc6_an_unknown_ref_raises(gateway: GitHubGateway) -> None:
    with pytest.raises(RefNotFound):
        await gateway.compare(REPO, "v0.0.1", "v2.14.3")


# ── GC12 — ref resolution priority ─────────────────────────────────────────


async def test_gc12_release_tag_resolves_before_anything_else(
    gateway: GitHubGateway,
) -> None:
    result = await gateway.fetch_file(REPO, "services/checkout.py", "v2.14.1")
    assert result.ref == "v2.14.1"


async def test_gc12_default_branch_and_head_resolve(gateway: GitHubGateway) -> None:
    for ref in ("main", "HEAD"):
        assert await gateway.fetch_file(REPO, "services/checkout.py", ref)


async def test_gc12_an_unresolvable_ref_raises_rather_than_falling_back(
    gateway: GitHubGateway,
) -> None:
    """Falling back to HEAD would mean reasoning about code that never ran,
    and nothing downstream would be able to tell."""
    with pytest.raises(RefNotFound):
        await gateway.fetch_file(REPO, "services/checkout.py", "v9.9.9")


# ── GC7 — the write sequence ───────────────────────────────────────────────


async def test_gc7_blob_tree_commit_ref(gateway: GitHubGateway) -> None:
    """The whole write path without a clone (`08` §4)."""
    patched = "print('patched')\n"

    blob = await gateway.create_blob(REPO, patched)
    assert blob == blob_sha(patched)

    tree = await gateway.create_tree(REPO, "", [TreeEntry(path="services/checkout.py", sha=blob)])
    assert len(tree) == 40

    commit = await gateway.create_commit(
        REPO,
        "fix: guard the tax lookup",
        tree,
        [],
        Actor(name="RootTrace AI", email="bot@roottrace.ai"),
    )
    assert len(commit) == 40

    await gateway.create_ref(REPO, "roottrace/fix-null-prop-01", commit)


async def test_gc7_object_ids_are_deterministic(gateway: GitHubGateway) -> None:
    """The eval harness compares runs. A wall-clock commit timestamp would
    give the same patch a different sha every time."""
    author = Actor(name="RootTrace AI", email="bot@roottrace.ai")
    tree = await gateway.create_tree(REPO, "", [TreeEntry(path="a.py", sha=blob_sha("x"))])

    first = await gateway.create_commit(REPO, "same message", tree, [], author)
    second = await gateway.create_commit(REPO, "same message", tree, [], author)
    assert first == second


async def test_gc9_a_branch_collision_raises(gateway: GitHubGateway) -> None:
    """Silently moving a branch someone else created is how one patch lands on
    top of another."""
    commit = await gateway.create_commit(
        REPO, "m", await gateway.create_tree(REPO, "", []), [], Actor("a", "a@b.test")
    )
    await gateway.create_ref(REPO, "roottrace/dup", commit)

    with pytest.raises(AlreadyExists):
        await gateway.create_ref(REPO, "roottrace/dup", commit)


# ── GC8 — create_pull_request ──────────────────────────────────────────────


async def _open_pr(gateway: GitHubGateway, branch: str = "roottrace/fix-1") -> PullRequestRef:
    commit = await gateway.create_commit(
        REPO, "m", await gateway.create_tree(REPO, "", []), [], Actor("a", "a@b.test")
    )
    await gateway.create_ref(REPO, branch, commit)
    return await gateway.create_pull_request(
        REPO,
        PullRequestDraft(
            title="fix: guard the tax lookup",
            body="## Root cause\n\n`clients/tax_client.py:38-43`\n",
            head=branch,
            base="main",
            labels=("roottrace", "automated"),
        ),
    )


async def test_gc8_the_rendered_title_and_body_are_preserved_verbatim(
    gateway: GitHubGateway,
) -> None:
    """The other load-bearing half. The rendered description is stored and
    shown in the dashboard exactly as it would appear on GitHub, so fixture
    output is reviewable as the real artefact (`08` §7.5)."""
    pr = await _open_pr(gateway)

    assert pr.title == "fix: guard the tax lookup"
    assert pr.body == "## Root cause\n\n`clients/tax_client.py:38-43`\n"
    assert pr.base == "main"


async def test_gc8_a_fixture_pull_request_says_it_is_simulated(
    gateway: GitHubGateway,
) -> None:
    """Carried on the result rather than inferred from configuration. A
    `pull_request_records` row that cannot say whether it describes a real
    pull request is a row nobody can trust six months later."""
    pr = await _open_pr(gateway)
    assert pr.is_simulated is True


async def test_gc8_numbers_and_urls_look_like_the_real_thing(
    gateway: GitHubGateway,
) -> None:
    first = await _open_pr(gateway, "roottrace/fix-a")
    second = await _open_pr(gateway, "roottrace/fix-b")

    assert first.number == 1
    assert second.number == 2
    assert first.url == "https://github.com/acme/checkout-api/pull/1"


async def test_gc11_an_empty_diff_is_refused(gateway: GitHubGateway) -> None:
    """Opening a PR that changes nothing spends a reviewer's attention, which
    is the scarcest thing in the loop."""
    with pytest.raises(NoDiff):
        await gateway.create_pull_request(
            REPO, PullRequestDraft(title="t", body="b", head="main", base="main")
        )


async def test_a_pull_request_from_an_unknown_branch_is_refused(
    gateway: GitHubGateway,
) -> None:
    with pytest.raises(RefNotFound):
        await gateway.create_pull_request(
            REPO, PullRequestDraft(title="t", body="b", head="never/created", base="main")
        )


async def test_labels_are_added_without_duplicating(gateway: GitHubGateway) -> None:
    pr = await _open_pr(gateway)
    await gateway.add_labels(REPO, pr.number, ["automated", "needs-review"])

    updated = next(item for item in gateway.pull_requests if item.number == pr.number)  # type: ignore[attr-defined]
    assert updated.labels == ("roottrace", "automated", "needs-review")


# ── search_symbol ──────────────────────────────────────────────────────────


async def test_search_symbol_finds_the_definition(gateway: GitHubGateway) -> None:
    """Every definition, including the stub in the repository's own tests.

    The test double's signature is evidence of the intended contract, and
    filtering it here would be the transport deciding what retrieval is
    allowed to rank. Reporting what exists is this layer's job; choosing what
    is worth the token budget is S5's.
    """
    hits = await gateway.search_symbol(REPO, "get_rate")

    production = [hit for hit in hits if hit.path == "clients/tax_client.py"]
    assert production, [hit.path for hit in hits]
    assert production[0].line == 36
    assert production[0].kind == "function"
    assert "tests/conftest.py" in [hit.path for hit in hits]


async def test_search_symbol_does_not_match_a_longer_name(gateway: GitHubGateway) -> None:
    """`def calculate_total_v2` must not answer a search for
    `calculate_total`, or retrieval spends its budget on the wrong function."""
    hits = await gateway.search_symbol(REPO, "calculate_tot")
    assert hits == []
