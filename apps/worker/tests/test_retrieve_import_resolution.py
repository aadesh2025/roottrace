"""Resolving an import to a repository path, tree-verified (`03` §S5 strategy
B's "imports" bullet, T4.3)."""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.retrieve.import_resolution import resolve_import

pytestmark = pytest.mark.unit

TREE = frozenset(
    {
        "clients/tax_client.py",
        "clients/__init__.py",
        "services/__init__.py",
        "services/pricing.py",
        "services/checkout.py",
        "config/__init__.py",
        "config/regions.py",
    }
)


def test_a_direct_module_import_resolves() -> None:
    assert (
        resolve_import(
            TREE,
            importing_file="services/checkout.py",
            module="clients.tax_client",
            level=0,
            original_name="TaxClient",
        )
        == "clients/tax_client.py"
    )


def test_a_submodule_imported_from_its_package_resolves_to_the_submodule() -> None:
    """`from services import pricing` — genuinely ambiguous from the
    statement alone (is `pricing` a symbol in `services/__init__.py` or a
    submodule?). The submodule candidate is tried first and, since it exists
    in the tree, wins."""
    assert (
        resolve_import(
            TREE,
            importing_file="services/checkout.py",
            module="services",
            level=0,
            original_name="pricing",
        )
        == "services/pricing.py"
    )


def test_a_symbol_imported_from_a_package_init_resolves_there() -> None:
    """The submodule candidate does not exist this time, so the package's
    `__init__.py` — where the symbol must actually live — is used instead."""
    assert (
        resolve_import(
            TREE,
            importing_file="services/checkout.py",
            module="config",
            level=0,
            original_name="something_defined_in_init",
        )
        == "config/__init__.py"
    )


def test_a_stdlib_import_does_not_resolve() -> None:
    assert (
        resolve_import(
            TREE,
            importing_file="services/checkout.py",
            module="decimal",
            level=0,
            original_name="Decimal",
        )
        is None
    )


def test_a_bare_import_with_no_original_name() -> None:
    assert (
        resolve_import(
            TREE,
            importing_file="services/checkout.py",
            module="clients.tax_client",
            level=0,
            original_name=None,
        )
        == "clients/tax_client.py"
    )


def test_a_relative_import_climbs_from_the_importing_files_own_package() -> None:
    """`from .tax_client import TaxClient` inside `clients/errors.py` —
    `level=1` means "this package", i.e. `clients/`."""
    assert (
        resolve_import(
            TREE,
            importing_file="clients/errors.py",
            module="tax_client",
            level=1,
            original_name="TaxClient",
        )
        == "clients/tax_client.py"
    )


def test_relative_import_levels_climb_the_correct_number_of_packages() -> None:
    """`level=1` is the current package, `level=2` its parent, `level=3` its
    grandparent — Python's own semantics for `from .`/`from ..`/`from ...`.
    One marker file per level, so a climb landing one directory too high or
    too low fails loudly rather than passing by coincidence."""
    tree = frozenset(
        {
            "services/sub/deep/module.py",
            "services/sub/deep/here1.py",
            "services/sub/here2.py",
            "services/here3.py",
        }
    )
    importing_file = "services/sub/deep/module.py"
    assert (
        resolve_import(
            tree, importing_file=importing_file, module=None, level=1, original_name="here1"
        )
        == "services/sub/deep/here1.py"
    )
    assert (
        resolve_import(
            tree, importing_file=importing_file, module=None, level=2, original_name="here2"
        )
        == "services/sub/here2.py"
    )
    assert (
        resolve_import(
            tree, importing_file=importing_file, module=None, level=3, original_name="here3"
        )
        == "services/here3.py"
    )


def test_a_relative_import_that_climbs_past_the_repository_root_resolves_to_none() -> None:
    """`level` deep enough to climb above the repo root has nowhere valid to
    land — a malformed or pathological import, not a file this system can
    name."""
    assert (
        resolve_import(
            TREE,
            importing_file="services/checkout.py",
            module=None,
            level=5,
            original_name="anything",
        )
        is None
    )


def test_an_unresolvable_candidate_returns_none() -> None:
    assert (
        resolve_import(
            TREE,
            importing_file="services/checkout.py",
            module="nonexistent.module",
            level=0,
            original_name="Thing",
        )
        is None
    )
