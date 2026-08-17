"""S5's four implemented strategies (`03` §S5, T4.3), against a small
in-memory repository built for precise control — see `test_retrieve_strategies_corpus.py`
for proof against the real fixture corpus."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _fake_gateway import FakeGateway

from roottrace_worker.github.types import (
    Actor,
    BlameRange,
    Commit,
    CompareResult,
    FileChange,
    RepoRef,
    SymbolHit,
)
from roottrace_worker.pipeline.retrieve.strategies import (
    MAX_CALLER_CANDIDATES,
    _expand_callers,
    _fetch_definition_window,
    gather,
    strategy_a_frame_direct,
    strategy_b_call_graph,
    strategy_c_vector_semantic,
    strategy_d_git_history,
    strategy_e_test_discovery,
)
from roottrace_worker.pipeline.understand.contracts import (
    EntryPoint,
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    FailurePoint,
    Frame,
    RetrievalPlan,
)

pytestmark = pytest.mark.unit

REPO = RepoRef(owner="acme", name="checkout-api")
REF = "main"

CHECKOUT_SOURCE = (
    "from __future__ import annotations\n\n"
    "from clients.tax_client import TaxClient\n"
    "from models.cart import Cart\n\n\n"
    "class CheckoutService:\n"
    "    def __init__(self, tax_client: TaxClient) -> None:\n"
    "        self.tax_client = tax_client\n\n"
    "    def calculate_total(self, cart: Cart) -> object:\n"
    "        base_price = cart.subtotal()\n"
    "        tax_amount = self.tax_client.get_rate(cart.region)\n"
    "        return base_price + tax_amount\n"
)

TAX_CLIENT_SOURCE = (
    "class TaxClient:\n    def get_rate(self, region: str) -> object:\n        return None\n"
)

CART_SOURCE = "class Cart:\n    def subtotal(self) -> object:\n        return None\n"

ROUTES_SOURCE = (
    "from services.checkout import CheckoutService\n\n\n"
    "def create_checkout(payload):\n"
    "    service = CheckoutService(None)\n"
    "    total = service.calculate_total(payload['cart'])\n"
    "    return total\n"
)

QUOTE_SOURCE = (
    "from services.checkout import CheckoutService\n\n\n"
    "def quote(cart):\n"
    "    return CheckoutService(None).calculate_total(cart)\n"
)

DOC_MENTION_SOURCE = (
    "# calculate_total is documented elsewhere; no call here.\ndef unrelated():\n    pass\n"
)

TEST_SOURCE = (
    "from services.checkout import CheckoutService\n\n\n"
    "def test_calculate_total():\n"
    "    assert CheckoutService(None).calculate_total(None) is not None\n"
)


def repo_files() -> dict[str, str]:
    return {
        "services/checkout.py": CHECKOUT_SOURCE,
        "clients/tax_client.py": TAX_CLIENT_SOURCE,
        "models/cart.py": CART_SOURCE,
        "api/routes/checkout.py": ROUTES_SOURCE,
        "services/quote.py": QUOTE_SOURCE,
        "docs/notes.py": DOC_MENTION_SOURCE,
        "tests/test_checkout.py": TEST_SOURCE,
    }


def understanding_for(gateway_files: dict[str, str]) -> ErrorUnderstanding:
    del gateway_files
    return ErrorUnderstanding(
        language="python",
        framework="fastapi",
        exception=ExceptionInfo(
            type="TypeError",
            family=ExceptionFamily.NULL_UNDEFINED,
            message_normalized="unsupported operand type(s) for +: '<type>' and '<type>'",
            is_user_facing=True,
        ),
        frames=(
            Frame(
                index=0,
                raw_path="/app/services/checkout.py",
                repo_path="services/checkout.py",
                line=11,
                function="calculate_total",
                in_app=True,
                confidence=0.95,
            ),
        ),
        entry_point=EntryPoint(type="http_route", method="POST", pattern="/checkout"),
        failure_point=FailurePoint(
            repo_path="services/checkout.py", function="calculate_total", line=11
        ),
        implicated_symbols=("calculate_total",),
        retrieval_plan=RetrievalPlan(must_fetch=("services/checkout.py",)),
        extraction_confidence=0.5,
    )


# ── Strategy A ───────────────────────────────────────────────────────────


async def test_strategy_a_fetches_each_in_app_frame_file() -> None:
    gateway = FakeGateway(files=repo_files())
    frames = (
        Frame(
            index=0,
            raw_path="x",
            repo_path="services/checkout.py",
            line=11,
            function="calculate_total",
            in_app=True,
            confidence=0.95,
        ),
    )
    tree = await gateway.fetch_tree(REPO, REF)
    items = await strategy_a_frame_direct(gateway, REPO, REF, tree, frames)
    assert len(items) == 1
    assert items[0].repo_path == "services/checkout.py"
    assert items[0].strategy == "frame_direct"
    assert items[0].truncated is False  # file is under 400 lines


async def test_strategy_a_ignores_frames_that_are_not_in_app() -> None:
    gateway = FakeGateway(files=repo_files())
    frames = (Frame(index=0, raw_path="x", repo_path=None, line=1, in_app=False, confidence=0.0),)
    tree = await gateway.fetch_tree(REPO, REF)
    items = await strategy_a_frame_direct(gateway, REPO, REF, tree, frames)
    assert items == ()


async def test_strategy_a_merges_two_frames_in_the_same_file_into_one_entry() -> None:
    """`null-prop-04`'s shape: two frames, one file. One entry, not two."""
    gateway = FakeGateway(files=repo_files())
    frames = (
        Frame(
            index=0,
            raw_path="x",
            repo_path="services/checkout.py",
            line=9,
            function="calculate_total",
            in_app=True,
            confidence=0.95,
        ),
        Frame(
            index=1,
            raw_path="x",
            repo_path="services/checkout.py",
            line=11,
            function="calculate_total",
            in_app=True,
            confidence=0.95,
        ),
    )
    tree = await gateway.fetch_tree(REPO, REF)
    items = await strategy_a_frame_direct(gateway, REPO, REF, tree, frames)
    assert len(items) == 1


async def test_strategy_a_skips_a_frame_whose_file_cannot_be_fetched() -> None:
    gateway = FakeGateway(files=repo_files())
    frames = (
        Frame(
            index=0,
            raw_path="x",
            repo_path="does/not/exist.py",
            line=1,
            function="f",
            in_app=True,
            confidence=0.95,
        ),
    )
    tree = await gateway.fetch_tree(REPO, REF)
    items = await strategy_a_frame_direct(gateway, REPO, REF, tree, frames)
    assert items == ()


async def test_strategy_a_reports_classes_and_functions_defined_in_the_window() -> None:
    gateway = FakeGateway(files=repo_files())
    frames = (
        Frame(
            index=0,
            raw_path="x",
            repo_path="clients/tax_client.py",
            line=2,
            function="get_rate",
            in_app=True,
            confidence=0.95,
        ),
    )
    tree = await gateway.fetch_tree(REPO, REF)
    items = await strategy_a_frame_direct(gateway, REPO, REF, tree, frames)
    assert "TaxClient" in items[0].symbols_defined
    assert "TaxClient.get_rate" in items[0].symbols_defined


# ── Strategy B ───────────────────────────────────────────────────────────


async def test_strategy_b_finds_the_callee_across_files() -> None:
    """The corpus-motivating case in miniature: `get_rate` is called by
    `calculate_total` but defined in a different, unfetched file."""
    gateway = FakeGateway(files=repo_files())
    understanding = understanding_for(repo_files())
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )

    assert "clients/tax_client.py" in {f.repo_path for f in result.files}
    edge = next(e for e in result.edges if e.kind == "calls" and "get_rate" in e.target)
    assert edge.source == "services/checkout.py::calculate_total"


async def test_strategy_b_finds_the_type_definition() -> None:
    gateway = FakeGateway(files=repo_files())
    understanding = understanding_for(repo_files())
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )

    assert "models/cart.py" in {f.repo_path for f in result.files}
    assert any(node.id == "models/cart.py::Cart" and node.kind == "class" for node in result.nodes)


async def test_strategy_b_confirms_callers_and_excludes_a_docstring_mention() -> None:
    """`docs/notes.py` mentions `calculate_total` in a comment and defines no
    function that calls it — `ast`-confirmation must exclude it.
    `api/routes/checkout.py`, `services/quote.py`, and `tests/test_checkout.py`
    all really call it and must all appear."""
    gateway = FakeGateway(files=repo_files())
    understanding = understanding_for(repo_files())
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )

    caller_paths = {
        f.repo_path
        for f in result.files
        if f.repo_path != "clients/tax_client.py" and f.repo_path != "models/cart.py"
    }
    assert "api/routes/checkout.py" in caller_paths
    assert "services/quote.py" in caller_paths
    assert "tests/test_checkout.py" in caller_paths
    assert "docs/notes.py" not in {f.repo_path for f in result.files}

    caller_edges = [e for e in result.edges if e.target == "services/checkout.py::calculate_total"]
    assert len(caller_edges) == 3


async def test_strategy_b_with_no_failure_point_returns_empty() -> None:
    gateway = FakeGateway(files=repo_files())
    understanding = understanding_for(repo_files()).model_copy(update={"failure_point": None})
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert result.files == ()
    assert result.nodes == ()


async def test_strategy_b_the_failure_point_is_always_the_first_node() -> None:
    gateway = FakeGateway(files=repo_files())
    understanding = understanding_for(repo_files())
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert result.nodes[0].is_failure_point is True
    assert result.nodes[0].id == "services/checkout.py::calculate_total"


async def test_strategy_b_caps_the_number_of_caller_candidates_fetched() -> None:
    files = repo_files()
    for n in range(MAX_CALLER_CANDIDATES + 5):
        files[f"callers/caller_{n}.py"] = (
            "from services.checkout import CheckoutService\n\n\n"
            f"def call_site_{n}():\n"
            "    return CheckoutService(None).calculate_total(None)\n"
        )
    gateway = FakeGateway(files=files)
    understanding = understanding_for(files)
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    caller_files = [f for f in result.files if f.repo_path.startswith("callers/")]
    assert len(caller_files) <= MAX_CALLER_CANDIDATES


async def test_strategy_b_a_missing_failure_point_file_returns_empty_gracefully() -> None:
    gateway = FakeGateway(files={})
    understanding = understanding_for({})
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert result.files == ()


# ── Strategy C ───────────────────────────────────────────────────────────


async def test_strategy_c_always_returns_empty() -> None:
    """`03` §S5: the index is empty in V1; the code path exists and returns
    empty."""
    assert await strategy_c_vector_semantic(("tax rate calculation",)) == ()
    assert await strategy_c_vector_semantic(()) == ()


# ── Strategy D ───────────────────────────────────────────────────────────


def commit(sha: str, when: datetime) -> Commit:
    return Commit(sha=sha, message="m", author=Actor(name="a", email="a@x.io"), date=when)


async def test_strategy_d_returns_the_blame_commit() -> None:
    target = commit("8a3f1c2e" + "0" * 32, datetime(2026, 7, 25, tzinfo=UTC))
    gateway = FakeGateway(
        files=repo_files(),
        blames={
            "services/checkout.py": [
                BlameRange(path="services/checkout.py", start_line=1, end_line=20, commit=target)
            ]
        },
    )
    result = await strategy_d_git_history(
        gateway,
        REPO,
        REF,
        must_fetch_paths=("services/checkout.py",),
        failure_point=FailurePoint(
            repo_path="services/checkout.py", function="calculate_total", line=11
        ),
    )
    assert result.blame_commit is not None
    assert result.blame_commit.sha == target.sha


async def test_strategy_d_deduplicates_commits_across_files_and_sorts_by_recency() -> None:
    old = commit("1" * 40, datetime(2026, 1, 1, tzinfo=UTC))
    new = commit("2" * 40, datetime(2026, 6, 1, tzinfo=UTC))
    gateway = FakeGateway(
        files=repo_files(),
        commits_by_path={
            "services/checkout.py": [new, old],
            "clients/tax_client.py": [old],  # same commit, must not duplicate
        },
    )
    result = await strategy_d_git_history(
        gateway,
        REPO,
        REF,
        must_fetch_paths=("services/checkout.py", "clients/tax_client.py"),
        failure_point=None,
    )
    assert [c.sha for c in result.recent_commits] == [new.sha, old.sha]


async def test_strategy_d_release_diff_is_none_without_a_previous_ref() -> None:
    gateway = FakeGateway(files=repo_files())
    result = await strategy_d_git_history(
        gateway, REPO, REF, must_fetch_paths=(), failure_point=None
    )
    assert result.release_diff is None


async def test_strategy_d_release_diff_is_populated_when_a_previous_ref_is_given() -> None:
    diff = CompareResult(
        base="v1", head="v2", commits=(), files=(FileChange(path="a.py", status="modified"),)
    )
    gateway = FakeGateway(files=repo_files(), compares={("v1", "v2"): diff})
    result = await strategy_d_git_history(
        gateway, REPO, "v2", must_fetch_paths=(), failure_point=None, previous_ref="v1"
    )
    assert result.release_diff == diff


async def test_strategy_d_with_no_failure_point_has_no_blame() -> None:
    gateway = FakeGateway(files=repo_files())
    result = await strategy_d_git_history(
        gateway, REPO, REF, must_fetch_paths=(), failure_point=None
    )
    assert result.blame_commit is None


# ── Strategy E ───────────────────────────────────────────────────────────


async def test_strategy_e_finds_the_conventionally_named_test() -> None:
    gateway = FakeGateway(files=repo_files())
    tree = await gateway.fetch_tree(REPO, REF)
    matches = await strategy_e_test_discovery(
        gateway,
        REPO,
        REF,
        tree,
        must_fetch_paths=("services/checkout.py",),
        implicated_symbols=(),
    )
    assert any(m.repo_path == "tests/test_checkout.py" for m in matches)


async def test_strategy_e_finds_a_test_by_symbol_grep() -> None:
    gateway = FakeGateway(files=repo_files())
    tree = await gateway.fetch_tree(REPO, REF)
    matches = await strategy_e_test_discovery(
        gateway,
        REPO,
        REF,
        tree,
        must_fetch_paths=(),
        implicated_symbols=("calculate_total",),
    )
    covered = {m.repo_path: m.covers for m in matches}
    assert "calculate_total" in covered.get("tests/test_checkout.py", ())


async def test_strategy_e_finds_nothing_for_an_untested_file() -> None:
    gateway = FakeGateway(files=repo_files())
    tree = await gateway.fetch_tree(REPO, REF)
    matches = await strategy_e_test_discovery(
        gateway,
        REPO,
        REF,
        tree,
        must_fetch_paths=("docs/notes.py",),
        implicated_symbols=(),
    )
    assert matches == ()


# ── gather ───────────────────────────────────────────────────────────────


async def test_gather_combines_all_implemented_strategies() -> None:
    gateway = FakeGateway(files=repo_files())
    tree = await gateway.fetch_tree(REPO, REF)
    understanding = understanding_for(repo_files())

    candidates = await gather(gateway, REPO, REF, tree, understanding)

    paths = {f.repo_path for f in candidates.files}
    assert "services/checkout.py" in paths  # strategy A
    assert "clients/tax_client.py" in paths  # strategy B, callee
    assert "api/routes/checkout.py" in paths  # strategy B, caller
    assert candidates.history is not None
    assert any(t.repo_path == "tests/test_checkout.py" for t in candidates.tests)  # strategy E


async def test_gather_does_not_deduplicate_across_strategies() -> None:
    """Deliberately not this module's job — see the module docstring.
    `services/checkout.py` is the failure point (strategy A) and, if it also
    called itself recursively, would appear again from strategy B; here the
    overlap check is just that A's own output is present exactly once and
    T4.4 is where cross-strategy dedup happens."""
    gateway = FakeGateway(files=repo_files())
    tree = await gateway.fetch_tree(REPO, REF)
    understanding = understanding_for(repo_files())
    candidates = await gather(gateway, REPO, REF, tree, understanding)
    checkout_entries = [f for f in candidates.files if f.repo_path == "services/checkout.py"]
    assert len(checkout_entries) == 1  # only strategy A retrieved it in this fixture


# ── Edge cases: races between "the tree said it was there" and "it wasn't" ──
#
# A live transport can have a file listed in a tree that a moment later 404s
# (deleted between the two calls, or an eventually-consistent index) — every
# strategy has to survive that without raising, and `phantom_paths`/
# `phantom_hits` on `FakeGateway` exist to construct exactly that race.


async def test_strategy_a_survives_a_frame_that_resolves_but_then_404s() -> None:
    gateway = FakeGateway(files=repo_files(), phantom_paths=("services/ghost.py",))
    tree = await gateway.fetch_tree(REPO, REF)
    frames = (
        Frame(
            index=0,
            raw_path="/app/services/ghost.py",
            repo_path="services/ghost.py",
            line=1,
            function="f",
            in_app=True,
            confidence=0.95,
        ),
    )
    items = await strategy_a_frame_direct(gateway, REPO, REF, tree, frames)
    assert items == ()


async def test_strategy_a_skips_a_frame_with_no_line_number() -> None:
    gateway = FakeGateway(files=repo_files())
    tree = await gateway.fetch_tree(REPO, REF)
    frames = (
        Frame(
            index=0,
            raw_path="x",
            repo_path="services/checkout.py",
            line=None,
            in_app=True,
            confidence=0.95,
        ),
    )
    items = await strategy_a_frame_direct(gateway, REPO, REF, tree, frames)
    # Still fetched (the file resolves) — just with no line to build a
    # window around, so the whole file (or a None-centered window) is used
    # rather than crashing on an absent line number.
    assert len(items) == 1


async def test_strategy_b_survives_a_failure_point_that_resolves_but_then_404s() -> None:
    gateway = FakeGateway(files=repo_files(), phantom_paths=("services/ghost.py",))
    tree = await gateway.fetch_tree(REPO, REF)
    understanding = understanding_for(repo_files()).model_copy(
        update={
            "frames": (
                Frame(
                    index=0,
                    raw_path="/app/services/ghost.py",
                    repo_path="services/ghost.py",
                    line=1,
                    function="f",
                    in_app=True,
                    confidence=0.95,
                ),
            ),
            "failure_point": FailurePoint(repo_path="services/ghost.py", function="f", line=1),
        }
    )
    result = await strategy_b_call_graph(gateway, REPO, REF, tree, understanding)
    assert result.files == ()


async def test_strategy_b_a_failure_point_file_with_a_syntax_error_returns_empty() -> None:
    files = repo_files()
    files["services/checkout.py"] = "def broken(:\n"
    gateway = FakeGateway(files=files)
    understanding = understanding_for(files)
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert result.files == ()


async def test_strategy_b_a_failure_point_function_not_actually_in_the_file_returns_empty() -> None:
    """S4's plan named a function that does not exist in the file it
    resolved to — a stale or wrong `function` name. Strategy B must not
    guess; it stops expansion rather than analysing the wrong function."""
    gateway = FakeGateway(files=repo_files())
    understanding = understanding_for(repo_files()).model_copy(
        update={
            "failure_point": FailurePoint(
                repo_path="services/checkout.py", function="does_not_exist", line=1
            )
        }
    )
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert result.files == ()


async def test_strategy_b_a_callee_resolved_via_search_that_then_404s_is_skipped() -> None:
    """The callee resolves (a `search_symbol` hit says it is defined at
    `services/ghost.py`) but the file itself is gone by the time it is
    fetched — `_fetch_definition_window` returning `None` must not raise."""
    files = repo_files()
    files["services/checkout.py"] = "def calculate_total():\n    phantom_callee()\n"
    gateway = FakeGateway(
        files=files,
        phantom_hits=(
            SymbolHit(path="services/ghost.py", line=1, symbol="phantom_callee", kind="function"),
        ),
    )
    understanding = understanding_for(files)
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert "services/ghost.py" not in {f.repo_path for f in result.files}
    assert not any("phantom_callee" in node.id for node in result.nodes)


async def test_strategy_b_finds_a_type_defined_in_the_same_file_as_the_failure_point() -> None:
    """Local resolution (same file) for a class, exercised specifically —
    the callee case is already covered by `test_strategy_b_finds_the_callee_across_files`,
    which resolves in a *different* file."""
    files = {
        "services/checkout.py": (
            "class LocalHelper:\n    pass\n\n\ndef calculate_total(x: LocalHelper):\n    return x\n"
        ),
    }
    gateway = FakeGateway(files=files)
    understanding = understanding_for(files)
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert any(node.id == "services/checkout.py::LocalHelper" for node in result.nodes)
    # Same file as the failure point, already implicitly fetched by the
    # graph node existing — no separate RetrievedFile duplicate is expected
    # for a same-file resolution.


async def test_strategy_b_an_import_that_does_not_resolve_falls_through_to_search() -> None:
    """A name is imported, but the module it names is not in the tree (a
    third-party or stdlib import that happens to share a name with something
    findable elsewhere in the repo) — resolution must continue to the
    repository-wide search rather than giving up."""
    files = {
        "services/checkout.py": (
            "from nowhere.at.all import subtotal\n\n\n"
            "def calculate_total():\n    return subtotal()\n"
        ),
        "models/cart.py": "def subtotal():\n    return 1\n",
    }
    gateway = FakeGateway(files=files)
    understanding = understanding_for(files)
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert "models/cart.py" in {f.repo_path for f in result.files}


async def test_fetch_definition_window_returns_none_when_the_file_404s() -> None:
    gateway = FakeGateway(files={}, phantom_paths=("services/ghost.py",))
    result = await _fetch_definition_window(gateway, REPO, REF, "services/ghost.py", "f")
    assert result is None


async def test_fetch_definition_window_survives_a_syntax_error_in_the_target_file() -> None:
    gateway = FakeGateway(files={"broken.py": "def broken(:\n"})
    result = await _fetch_definition_window(gateway, REPO, REF, "broken.py", "broken")
    assert result is not None
    assert result.symbols_defined == ()  # nothing indexable, whole file taken as-is


async def test_fetch_definition_window_falls_back_to_a_class_when_no_function_matches() -> None:
    gateway = FakeGateway(files={"models/thing.py": "class Thing:\n    pass\n"})
    result = await _fetch_definition_window(gateway, REPO, REF, "models/thing.py", "Thing")
    assert result is not None
    assert "Thing" in result.symbols_defined


async def test_expand_callers_with_no_function_name_does_nothing() -> None:
    """Direct unit test of the narrowing check strategy B's own guard makes
    unreachable through the public entry point — `strategy_b_call_graph`
    never calls this helper without a resolved `function`, so this proves
    the guard itself is correct in isolation."""
    files: list[object] = []
    nodes: list[object] = []
    edges: list[object] = []
    gateway = FakeGateway(files=repo_files())
    await _expand_callers(
        gateway,
        REPO,
        REF,
        FailurePoint(repo_path="services/checkout.py", function=None, line=1),
        failure_repo_path="services/checkout.py",
        files=files,  # type: ignore[arg-type]
        nodes=nodes,  # type: ignore[arg-type]
        edges=edges,  # type: ignore[arg-type]
        failure_id="x",
    )
    assert files == []


async def test_strategy_b_a_caller_candidate_that_404s_is_skipped() -> None:
    gateway = FakeGateway(
        files=repo_files(),
        phantom_hits=(
            SymbolHit(path="services/ghost.py", line=1, symbol="calculate_total", kind="reference"),
        ),
    )
    understanding = understanding_for(repo_files())
    result = await strategy_b_call_graph(
        gateway, REPO, REF, await gateway.fetch_tree(REPO, REF), understanding
    )
    assert "services/ghost.py" not in {f.repo_path for f in result.files}


# ── Strategy D — RefNotFound on compare ─────────────────────────────────


async def test_strategy_d_release_diff_is_none_when_the_previous_ref_is_not_found() -> None:
    gateway = FakeGateway(files=repo_files())  # no `compares` entries at all
    result = await strategy_d_git_history(
        gateway, REPO, REF, must_fetch_paths=(), failure_point=None, previous_ref="v0"
    )
    assert result.release_diff is None


# ── Strategy E — a must_fetch path with no .py extension ────────────────


async def test_strategy_e_a_must_fetch_path_without_a_py_extension_is_handled() -> None:
    """Guards the `stem.endswith(".py")` branch — a path this stage has no
    reason to assume is always `.py`, even though V1's corpus only ever
    produces one."""
    gateway = FakeGateway(files=repo_files())
    tree = await gateway.fetch_tree(REPO, REF)
    matches = await strategy_e_test_discovery(
        gateway, REPO, REF, tree, must_fetch_paths=("README",), implicated_symbols=()
    )
    assert matches == ()


async def test_strategy_e_a_matched_test_file_that_404s_is_skipped() -> None:
    gateway = FakeGateway(files=repo_files(), phantom_paths=("tests/test_checkout.py",))
    # The real tests/test_checkout.py in repo_files() would shadow the
    # phantom, so remove it from `files` for this test specifically.
    files = repo_files()
    del files["tests/test_checkout.py"]
    gateway = FakeGateway(files=files, phantom_paths=("tests/test_checkout.py",))
    tree = await gateway.fetch_tree(REPO, REF)
    matches = await strategy_e_test_discovery(
        gateway,
        REPO,
        REF,
        tree,
        must_fetch_paths=("services/checkout.py",),
        implicated_symbols=(),
    )
    assert matches == ()
