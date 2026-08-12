"""The one place the transport is chosen (`08` §7.1).

> The **only** place `RT_GITHUB_MODE` is read is the gateway factory. A grep
> for `github_mode` outside `roottrace_worker/github/factory.py` is a build
> failure — this is enforced by a lint rule, because parity that depends on
> discipline will not survive ten weeks.

`test_transport_parity.py` is that rule. It greps the tree and fails if the
attribute appears anywhere else.

Settings arrive as an object, not as a string, so the *attribute access* is
what is confined here rather than the plumbing. A caller that had to read
`settings.github_mode` to pass it in would have reintroduced the branch it was
supposed to be spared.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from roottrace_worker.github.errors import TransportUnavailable
from roottrace_worker.github.fixture import FixtureTransport
from roottrace_worker.github.gateway import GitHubGateway


class GithubSettings(Protocol):
    """The slice of `Settings` this factory needs.

    A Protocol so `roottrace_worker` does not import `roottrace_api` to get a
    type. The real `Settings` satisfies it structurally, and so does a test
    double, without either knowing about the other.
    """

    @property
    def github_mode(self) -> str: ...

    @property
    def github_fixture_path(self) -> str: ...


def build_gateway(settings: GithubSettings) -> GitHubGateway:
    """Build the gateway this deployment is configured for."""
    mode = settings.github_mode

    if mode == "fixture":
        return FixtureTransport(Path(settings.github_fixture_path))

    if mode == "replay":
        # Not built. `08` §7.4 is explicit that a transport is not complete
        # until it passes every contract case, and there are no cassettes to
        # replay until a real repository has been recorded against.
        raise TransportUnavailable(
            "the replay transport is not implemented yet; it records cassettes "
            "from a real repository, which V1 has none of. Use GITHUB_MODE=fixture."
        )

    if mode == "live":
        # V2. The boot invariant already refuses `live` at the evaluation
        # tier, and an evaluation deployment holds no App private key, so it
        # could not authenticate even if this returned something. Raising here
        # makes that a loud failure rather than a subtle one.
        raise TransportUnavailable(
            "the live transport is V2. An evaluation-tier deployment holds no "
            "GitHub App key and structurally cannot reach a customer repository."
        )

    raise TransportUnavailable(f"unknown GITHUB_MODE: {mode!r}")
