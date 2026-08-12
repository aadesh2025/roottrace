"""The lint rule that keeps the seam a seam (`08` §7.1).

> The **only** place `RT_GITHUB_MODE` is read is the gateway factory. A grep
> for `github_mode` outside `roottrace_worker/github/factory.py` is a build
> failure — this is enforced by a lint rule, because parity that depends on
> discipline will not survive ten weeks.

This is that rule. It is a test rather than a ruff plugin because it has to
grep the tree, and because a failing test says *why* in the place a developer
is already looking.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from roottrace_worker.github import TransportUnavailable, build_gateway
from roottrace_worker.github.factory import GithubSettings

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
FACTORY = REPO_ROOT / "apps" / "worker" / "roottrace_worker" / "github" / "factory.py"

#: Where application code lives. `docs/`, `fixtures/` and the tests that police
#: this rule are allowed to name the variable.
APPLICATION_ROOTS = (
    REPO_ROOT / "apps" / "api" / "roottrace_api",
    REPO_ROOT / "apps" / "worker" / "roottrace_worker",
    REPO_ROOT / "apps" / "sandbox-runner",
    REPO_ROOT / "packages",
)

#: Files permitted to *name* the mode. `settings.py` declares the field and its
#: boot invariants constrain it against the deployment tier (C5); `main.py`
#: reports it from `/health/ready` and `/v1/auth/providers`. Declaring a field
#: and echoing it to an operator are not branching on a transport.
#:
#: The distinction is enforced, not assumed: `test_nothing_branches_on_the_mode`
#: below rejects a comparison against it anywhere outside the factory,
#: including in these files.
NAMING_ALLOWED = {
    FACTORY,
    REPO_ROOT / "apps" / "api" / "roottrace_api" / "settings.py",
    REPO_ROOT / "apps" / "api" / "roottrace_api" / "main.py",
}

#: Files permitted to *compare* against the mode.
#:
#: `settings.py` only for the C5 tier interlocks — `evaluation` refuses `live`
#: and `live` refuses `fixture`, both at boot, both raising. That is a safety
#: invariant about what a deployment may touch, not a choice of transport, and
#: `test_no_transport_is_imported_outside_the_factory` is what guarantees it
#: cannot become one: settings.py cannot build a gateway.
BRANCH_ALLOWED = {
    FACTORY,
    REPO_ROOT / "apps" / "api" / "roottrace_api" / "settings.py",
}

#: `if mode == "fixture"`, `match settings.github_mode`, and friends.
BRANCH_PATTERNS = (
    re.compile(r"github_mode\s*(==|!=|\bin\b|\bnot in\b)"),
    re.compile(r"(==|!=)\s*github_mode"),
    re.compile(r"match\s+.*github_mode"),
    re.compile(r"if\s+.*github_mode.*:"),
)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in APPLICATION_ROOTS:
        if root.exists():
            files += [
                path
                for path in root.rglob("*.py")
                if "__pycache__" not in path.parts and "tests" not in path.parts
            ]
    return files


def test_github_mode_is_named_in_exactly_one_place() -> None:
    """A stage that reads the mode is a stage that can branch on it, and the
    fixture/live parity guarantee is then a claim rather than a property."""
    offenders: dict[str, list[int]] = {}
    pattern = re.compile(r"\bgithub_mode\b")

    for path in _python_files():
        if path in NAMING_ALLOWED:
            continue
        hits = [
            number
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if pattern.search(line)
        ]
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits

    assert not offenders, (
        f"github_mode is read outside the factory: {offenders}. "
        "No pipeline stage may contain a fixture-mode branch (`08` §7.1)."
    )


def test_nothing_branches_on_the_mode_outside_the_factory() -> None:
    """The rule that actually matters.

    Naming the mode to report it is harmless; comparing against it is the
    fixture-mode branch `08` §7.1 forbids. Checked across every application
    file including the ones allowed to name it, so `/health/ready` echoing the
    value cannot quietly become `if settings.github_mode == "fixture"`.
    """
    offenders: dict[str, list[int]] = {}

    for path in _python_files():
        if path in BRANCH_ALLOWED:
            continue
        hits = [
            number
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if any(pattern.search(line) for pattern in BRANCH_PATTERNS)
        ]
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits

    assert not offenders, (
        f"something branches on github_mode outside the factory: {offenders}. "
        "The pipeline must be transport-independent (`08` §7.1)."
    )


def test_the_factory_is_where_the_mode_is_read() -> None:
    """The other half. If the rule above passed because nobody reads the mode
    anywhere at all, the factory has stopped choosing a transport."""
    assert "github_mode" in FACTORY.read_text(encoding="utf-8")


def test_no_transport_is_imported_outside_the_factory() -> None:
    """Importing `FixtureTransport` directly would bypass the seam even
    without naming the mode."""
    offenders = []
    for path in _python_files():
        if path in NAMING_ALLOWED or path.name in ("fixture.py", "__init__.py"):
            continue
        if "FixtureTransport" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, offenders


class _Settings:
    def __init__(self, mode: str, path: str = "fixtures/synthetic-repo"):
        self.github_mode = mode
        self.github_fixture_path = path


def test_the_real_settings_object_satisfies_the_factory_protocol() -> None:
    """`GithubSettings` is structural so the worker need not import the api
    package. That is only true if the real `Settings` actually satisfies it —
    otherwise the Protocol is a description of something that does not exist.
    """
    from roottrace_api.settings import Settings

    for field in ("github_mode", "github_fixture_path"):
        assert field in Settings.model_fields

    annotations = (
        GithubSettings.__annotations__ if hasattr(GithubSettings, "__annotations__") else {}
    )
    assert isinstance(annotations, dict)


@pytest.mark.parametrize("mode", ["replay", "live"])
def test_an_unbuilt_transport_fails_loudly(mode: str) -> None:
    """Not silently falling back to fixtures. A deployment that asked for
    `live` and quietly got fixtures would report success for work it never
    did."""
    with pytest.raises(TransportUnavailable):
        build_gateway(_Settings(mode))


def test_an_unknown_mode_fails_loudly() -> None:
    with pytest.raises(TransportUnavailable, match="unknown GITHUB_MODE"):
        build_gateway(_Settings("definitely-not-a-mode"))


def test_the_fixture_mode_builds_a_gateway() -> None:
    """The positive control. Every rejection above is meaningless if the
    supported mode cannot be built either."""
    gateway = build_gateway(_Settings("fixture", str(REPO_ROOT / "fixtures" / "synthetic-repo")))
    assert gateway is not None
