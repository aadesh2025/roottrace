"""The local symbol table strategy B is built on (`03` §S5, T4.3).

Uses `ast`, not Tree-sitter — see `ast_index.py`'s module docstring for why.
"""

from __future__ import annotations

import ast

import pytest

from roottrace_worker.pipeline.retrieve.ast_index import (
    analyze_calls,
    build_index,
    enclosing_function,
    find_class,
    find_function,
)

pytestmark = pytest.mark.unit

SOURCE = '''\
"""Module docstring."""

from __future__ import annotations

from decimal import Decimal

from clients.tax_client import TaxClient
from models.cart import Cart
from services import pricing


class CheckoutService:
    def __init__(self, tax_client: TaxClient) -> None:
        self.tax_client = tax_client

    def calculate_total(self, cart: Cart, user: User) -> Decimal:
        base_price = cart.subtotal()
        tax_amount = self.tax_client.get_rate(cart.region)
        return base_price + tax_amount


def module_function() -> None:
    pass
'''


def test_functions_and_methods_are_indexed_with_qualnames() -> None:
    index = build_index(SOURCE)
    assert index is not None
    names = {f.qualname for f in index.functions}
    assert names == {
        "CheckoutService.__init__",
        "CheckoutService.calculate_total",
        "module_function",
    }


def test_classes_are_indexed() -> None:
    index = build_index(SOURCE)
    assert index is not None
    assert [c.name for c in index.classes] == ["CheckoutService"]


def test_a_syntax_error_returns_none_not_a_crash() -> None:
    """One malformed file must degrade retrieval, never take the stage
    down."""
    assert build_index("def broken(:\n") is None


def test_an_empty_file_indexes_to_nothing() -> None:
    index = build_index("")
    assert index is not None
    assert index.functions == ()
    assert index.classes == ()


# ── Imports ──────────────────────────────────────────────────────────────


def test_from_imports_carry_both_names() -> None:
    index = build_index(SOURCE)
    assert index is not None
    tax_import = next(imp for imp in index.imports if imp.imported_name == "TaxClient")
    assert tax_import.module == "clients.tax_client"
    assert tax_import.original_name == "TaxClient"
    assert tax_import.level == 0


def test_an_aliased_import_keeps_the_original_name_for_the_module() -> None:
    index = build_index("from clients.tax_client import TaxClient as TC\n")
    assert index is not None
    imp = index.imports[0]
    assert imp.imported_name == "TC"  # what the code uses
    assert imp.original_name == "TaxClient"  # what the file actually exports


def test_a_bare_import_has_no_original_name() -> None:
    index = build_index("import logging\n")
    assert index is not None
    imp = index.imports[0]
    assert imp.imported_name == "logging"
    assert imp.module == "logging"
    assert imp.original_name is None


def test_a_relative_import_carries_its_level() -> None:
    index = build_index("from .tax_client import TaxClient\n")
    assert index is not None
    assert index.imports[0].level == 1


# ── Lookups ──────────────────────────────────────────────────────────────


def test_find_function_by_name() -> None:
    index = build_index(SOURCE)
    assert index is not None
    found = find_function(index, "calculate_total")
    assert found is not None
    assert found.qualname == "CheckoutService.calculate_total"


def test_find_function_disambiguates_by_line_when_a_name_repeats() -> None:
    source = (
        "class A:\n    def run(self):\n        pass\n\nclass B:\n    def run(self):\n        pass\n"
    )
    index = build_index(source)
    assert index is not None
    at_a = find_function(index, "run", line=2)
    at_b = find_function(index, "run", line=6)
    assert at_a is not None and at_a.qualname == "A.run"
    assert at_b is not None and at_b.qualname == "B.run"


def test_find_function_falls_back_to_the_first_match_when_the_line_matches_none() -> None:
    """A `line` hint that falls outside every candidate's range (a caller
    passing a stale or unrelated line number) must not raise — the first
    match by source order is a reasonable, deterministic fallback."""
    source = "def run():\n    pass\n\n\ndef run():\n    pass\n"
    index = build_index(source)
    assert index is not None
    found = find_function(index, "run", line=999)
    assert found is not None
    assert found.start_line == 1


def test_find_function_returns_none_for_an_unknown_name() -> None:
    index = build_index(SOURCE)
    assert index is not None
    assert find_function(index, "does_not_exist") is None


def test_find_class() -> None:
    index = build_index(SOURCE)
    assert index is not None
    assert find_class(index, "CheckoutService") is not None
    assert find_class(index, "Nope") is None


def test_enclosing_function_finds_the_innermost_match() -> None:
    index = build_index(SOURCE)
    assert index is not None
    line_of_get_rate_call = (
        SOURCE.splitlines().index("        tax_amount = self.tax_client.get_rate(cart.region)") + 1
    )
    enclosing = enclosing_function(index, line_of_get_rate_call)
    assert enclosing is not None
    assert enclosing.qualname == "CheckoutService.calculate_total"


def test_enclosing_function_is_none_outside_any_function() -> None:
    index = build_index(SOURCE)
    assert index is not None
    assert enclosing_function(index, 1) is None  # the module docstring


def test_a_nested_helper_is_not_indexed_separately() -> None:
    """A local helper is not a symbol anything outside its enclosing function
    can call, so it is not a second graph node — its calls are attributed to
    the enclosing function instead (covered by `test_calls_inside_a_nested_helper_are_attributed_to_the_enclosing_function`)."""
    source = "def outer():\n    def inner():\n        pass\n    inner()\n"
    index = build_index(source)
    assert index is not None
    assert [f.name for f in index.functions] == ["outer"]


# ── Call and type analysis ──────────────────────────────────────────────


def test_the_spec_worked_example() -> None:
    """`03` §S5's worked example: `calculate_total` calls `subtotal` and
    `get_rate`, and references `Cart`, `User`, `Decimal`."""
    index = build_index(SOURCE)
    assert index is not None
    func = find_function(index, "calculate_total")
    assert func is not None
    callees, type_names = analyze_calls(func.node)
    assert callees == ["subtotal", "get_rate"]
    assert type_names == ["Cart", "User", "Decimal"]


def test_calls_inside_a_nested_helper_are_attributed_to_the_enclosing_function() -> None:
    source = "def outer():\n    def inner():\n        helper_call()\n    inner()\n"
    index = build_index(source)
    assert index is not None
    func = find_function(index, "outer")
    assert func is not None
    callees, _ = analyze_calls(func.node)
    assert "helper_call" in callees
    assert "inner" in callees


def test_type_instantiation_is_recognised_by_pascal_case() -> None:
    tree = ast.parse("def f():\n    x = Decimal('1')\n    y = lowercase_call()\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    callees, type_names = analyze_calls(func)
    assert "Decimal" in type_names
    assert "lowercase_call" in callees
    assert "lowercase_call" not in type_names


def test_calls_are_deduplicated_preserving_first_occurrence_order() -> None:
    tree = ast.parse("def f():\n    b()\n    a()\n    b()\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    callees, _ = analyze_calls(func)
    assert callees == ["b", "a"]


def test_attribute_calls_use_the_final_attribute_name() -> None:
    tree = ast.parse("def f():\n    self.tax_client.get_rate(1)\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    callees, _ = analyze_calls(func)
    assert callees == ["get_rate"]


def test_common_noise_calls_are_excluded() -> None:
    tree = ast.parse("def f():\n    logger.info('x')\n    real_call()\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    callees, _ = analyze_calls(func)
    assert callees == ["real_call"]


def test_subscripted_annotations_yield_their_inner_type_names() -> None:
    tree = ast.parse("def f(x: Optional[TaxClient]) -> dict[str, Cart]:\n    pass\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    _, type_names = analyze_calls(func)
    assert "TaxClient" in type_names
    assert "Cart" in type_names


def test_a_nested_functions_own_signature_is_not_double_counted() -> None:
    """A regression against the earlier bug this file's `strategies.py`
    counterpart fixed during development: visiting `node.body` must not skip
    the *outer* function's own signature while separately avoiding
    double-counting a nested function's."""
    tree = ast.parse("def outer(x: Decimal) -> Cart:\n    def inner(y: TaxClient):\n        pass\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    _, type_names = analyze_calls(func)
    assert "Decimal" in type_names
    assert "Cart" in type_names


def test_an_async_function_is_indexed_like_a_sync_one() -> None:
    index = build_index("class Service:\n    async def fetch(self):\n        pass\n")
    assert index is not None
    assert [f.qualname for f in index.functions] == ["Service.fetch"]


def test_a_dotted_annotation_yields_its_final_segment() -> None:
    """`module.TaxClient` — a fully-qualified annotation rather than a bare
    name, which real code uses when a module is imported wholesale."""
    tree = ast.parse("def f(x: clients.TaxClient) -> None:\n    pass\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    _, type_names = analyze_calls(func)
    assert "TaxClient" in type_names


def test_a_kwargs_annotation_is_included() -> None:
    tree = ast.parse("def f(**kwargs: Cart) -> None:\n    pass\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    _, type_names = analyze_calls(func)
    assert "Cart" in type_names


def test_a_call_target_that_is_neither_a_name_nor_an_attribute_is_ignored() -> None:
    """`handlers[0]()` — calling the result of a subscript. Not a symbol
    `search_symbol` could ever be asked about, so it is silently skipped
    rather than raising or fabricating a name."""
    tree = ast.parse("def f(handlers):\n    handlers[0]()\n")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    callees, _ = analyze_calls(func)
    assert callees == []
