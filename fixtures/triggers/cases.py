"""One trigger per fixture case.

Two kinds of reproduction, because not every defect is an exception:

- ``exception`` — running the code raises. The captured exception carries a
  real traceback, which T3.2 turns into the error payload.
- ``behaviour`` — the code returns the wrong answer without complaining. An
  off-by-one, a dropped element, a cache shared between requests. These are
  the defects a test suite is most likely to miss, so a corpus of only the
  first kind would be an easier corpus than reality.

``defect_in_repo`` separates the 23 real bugs from the 2 controls. For a
control the exception is genuine and the handling that produced it is already
correct — the fault is outside the repository, and the pipeline must say so
rather than invent a patch.
"""

from __future__ import annotations

import sys
import threading
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic-repo"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imported after the path insert: these are the synthetic repository's modules,
# not RootTrace's.
from clients.errors import RateLimited, UpstreamTimeout, UpstreamUnavailable  # noqa: E402
from clients.inventory_client import InventoryClient  # noqa: E402
from clients.payment_client import PaymentClient  # noqa: E402
from clients.tax_client import TaxClient  # noqa: E402
from config.regions import region_config  # noqa: E402
from config.settings import Settings  # noqa: E402
from models.cart import Cart, CartItem  # noqa: E402
from models.config import TypedRegistry  # noqa: E402
from models.user import DiscountProfile, User  # noqa: E402
from services import pricing  # noqa: E402
from services.cart import CartService  # noqa: E402
from services.checkout import CheckoutService  # noqa: E402
from services.export import ExportService  # noqa: E402
from services.inventory import InventoryService  # noqa: E402
from services.quote import QuoteService  # noqa: E402

Kind = Literal["exception", "behaviour"]


@dataclass
class Reproduction:
    case_id: str
    kind: Kind
    detail: str
    defect_in_repo: bool = True
    exception: BaseException | None = None

    @property
    def exception_type(self) -> str | None:
        return type(self.exception).__name__ if self.exception is not None else None


# ── Helpers ────────────────────────────────────────────────────────────────


def _cart(region: str = "eu-west") -> Cart:
    return Cart(
        id="c_8821",
        region=region,
        items=[
            CartItem(sku="sku-1", quantity=2, unit_price=Decimal("19.99")),
            CartItem(sku="sku-2", quantity=1, unit_price=Decimal("10.01")),
        ],
    )


def _user(with_discount: bool = True) -> User:
    profile = DiscountProfile(tier="gold", percent_off=10) if with_discount else None
    return User(id="u_9f2b1c", email="ada@example.com", plan="pro", discount_profile=profile)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "production",
        "tax_service_url": "http://tax.internal",
        "inventory_service_url": "http://inventory.internal",
        "payment_service_url": "http://payments.internal",
        "export_batch_size": 100,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _responding(client: object, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Point a client's transport at a stub.

    The upstream is simulated, never the repository. Nothing here patches the
    code under test — if a defect only reproduced against a patched version of
    itself, it would not be a defect.
    """
    inner = client._client  # type: ignore[attr-defined]
    client._client = httpx.Client(  # type: ignore[attr-defined]
        base_url=inner.base_url, transport=httpx.MockTransport(handler)
    )


def _capture(fn: Callable[[], object]) -> BaseException | None:
    try:
        fn()
    except BaseException as exc:
        return exc
    return None


def _expect_exception(case_id: str, fn: Callable[[], object], detail: str) -> Reproduction:
    exc = _capture(fn)
    if exc is None:
        raise AssertionError(f"{case_id}: expected the defect to raise, and it did not")
    return Reproduction(case_id=case_id, kind="exception", detail=detail, exception=exc)


# ── Null propagation ───────────────────────────────────────────────────────


def null_prop_01() -> Reproduction:
    """TaxClient.get_rate returns None on 5xx; calculate_total adds it."""
    tax = TaxClient("http://tax.internal")
    _responding(tax, lambda request: httpx.Response(503, json={"error": "unavailable"}))
    service = CheckoutService(tax, PaymentClient("http://payments.internal"))
    return _expect_exception(
        "null-prop-01",
        lambda: service.calculate_total(_cart(), _user()),
        "tax service 503 -> get_rate returns None -> Decimal + None",
    )


def null_prop_02() -> Reproduction:
    """get_item returns None for an absent SKU; increase_quantity uses it."""
    service = CartService()
    cart = _cart()
    return _expect_exception(
        "null-prop-02",
        lambda: service.increase_quantity(cart, "sku-not-in-cart"),
        "get_item returns None for an unknown SKU -> None.quantity",
    )


def null_prop_03() -> Reproduction:
    """tax_service_url is optional; tax_service_rate concatenates it."""
    settings = _settings(tax_service_url=None)
    return _expect_exception(
        "null-prop-03",
        lambda: pricing.tax_service_rate(settings, "eu-west"),
        "optional setting is None -> None + str",
    )


def null_prop_04() -> Reproduction:
    """discount_profile is None three frames above the arithmetic."""
    tax = TaxClient("http://tax.internal")
    _responding(tax, lambda request: httpx.Response(200, json={"rate": "0.20"}))
    service = CheckoutService(tax, PaymentClient("http://payments.internal"))
    return _expect_exception(
        "null-prop-04",
        lambda: service.discounted_subtotal(_cart(), _user(with_discount=False)),
        "user with no billing history -> discount_profile is None -> None.percent_off",
    )


# ── Type mismatch ──────────────────────────────────────────────────────────


def type_mismatch_01() -> Reproduction:
    """The provider sends amount as a string; charge_cart adds it to a Decimal."""
    tax = TaxClient("http://tax.internal")
    _responding(tax, lambda request: httpx.Response(200, json={"rate": "0.20"}))
    payment = PaymentClient("http://payments.internal")
    _responding(
        payment,
        lambda request: httpx.Response(200, json={"id": "ch_1", "amount": "49.99"}),
    )
    service = CheckoutService(tax, payment)
    return _expect_exception(
        "type-mismatch-01",
        lambda: service.charge_cart(_cart(), _user()),
        "payment amount arrives as str -> Decimal + str",
    )


def type_mismatch_02() -> Reproduction:
    """fetch_item returns a dict; describe treats it as a dataclass."""
    client = InventoryClient("http://inventory.internal")
    _responding(
        client,
        lambda request: httpx.Response(200, json={"sku": "sku-1", "description": "widget"}),
    )
    service = InventoryService(client)
    return _expect_exception(
        "type-mismatch-02",
        lambda: service.describe("sku-1"),
        "client returns dict -> attribute access on a dict",
    )


def type_mismatch_03() -> Reproduction:
    """A registry of strings merged into a registry of Decimals."""
    target: TypedRegistry[Decimal] = TypedRegistry(name="active")
    target.put("sku-1", Decimal("10.00"))
    incoming: TypedRegistry[str] = TypedRegistry(name="supplier")
    incoming.put("sku-2", "12.50")
    pricing.merge_price_book(target, incoming)
    return _expect_exception(
        "type-mismatch-03",
        lambda: pricing.total_with_rate(target.get("sku-2"), Decimal("0.2")),
        "unparameterised merge lets str into TypedRegistry[Decimal]",
    )


# ── Missing key ────────────────────────────────────────────────────────────


def key_error_01() -> Reproduction:
    """The retry probe sends no signature header."""
    from api.routes import webhooks

    return _expect_exception(
        "key-error-01",
        lambda: webhooks.verify_signature({"content-type": "application/json"}, b"{}"),
        "webhook probe carries no signature header -> direct index",
    )


def key_error_02() -> Reproduction:
    """coupon_code is optional in the client and indexed here."""
    from api.routes import cart as cart_route

    service = cart_route.get_cart_service()
    service.create("c_coupon", "us-east")
    return _expect_exception(
        "key-error-02",
        lambda: cart_route.apply_coupon("c_coupon", {"cart_id": "c_coupon"}),
        "optional body field accessed directly",
    )


def key_error_03() -> Reproduction:
    """A guest order has no user object at all."""
    service = ExportService(_settings())
    return _expect_exception(
        "key-error-03",
        lambda: service.render_row(
            {"id": "ord_3", "total": "5.00", "currency": "USD", "status": "paid"}
        ),
        "guest order has no `user` key -> nested index",
    )


# ── External dependency ────────────────────────────────────────────────────


def external_01() -> Reproduction:
    """Inventory times out; no retry, and the trace blames the handler."""

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("inventory did not respond", request=request)

    client = InventoryClient("http://inventory.internal")
    _responding(client, timeout)
    service = InventoryService(client)
    return _expect_exception(
        "external-01",
        lambda: service.reserve_for_cart(_cart()),
        "inventory timeout with no retry; only the breadcrumbs name the caller",
    )


def external_02() -> Reproduction:
    """The provider returns 429 with Retry-After, which is read and ignored."""
    payment = PaymentClient("http://payments.internal")
    _responding(
        payment,
        lambda request: httpx.Response(429, headers={"Retry-After": "2"}, json={}),
    )
    return _expect_exception(
        "external-02",
        lambda: payment.charge("c_8821", "49.99"),
        "payment 429 -> no backoff, the customer just fails",
    )


def external_03() -> Reproduction:
    """No circuit breaker: every call keeps paying the full failure cost.

    Reproduced behaviourally rather than as an exception, because the missing
    breaker *is* the defect and the typed error that comes back is the part
    that already works. Counting attempts is what shows it: a breaker would
    short-circuit after the first failure, and this reaches the dead upstream
    on every single call.
    """
    attempts = 0

    def unresolvable(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("name or service not known", request=request)

    client = InventoryClient("http://inventory.internal")
    _responding(client, unresolvable)

    calls = 5
    for _ in range(calls):
        _capture(lambda: client.reserve("sku-1", 1))

    if attempts < calls:
        raise AssertionError("external-03: something short-circuited, so a breaker exists")

    return Reproduction(
        case_id="external-03",
        kind="behaviour",
        detail=(
            f"{calls} calls against a dead upstream made {attempts} connection attempts; "
            "nothing opens after the first failure, so the checkout path serialises on it"
        ),
    )


# ── Concurrency ────────────────────────────────────────────────────────────


class _InterleavingStock(dict):  # type: ignore[type-arg]
    """The stock mapping, holding each of the first `readers` readers at a
    barrier so that all of them observe the same value before any writes back.

    This instruments the **data the service was handed**, not the service. Its
    `decrement` is untouched: it still reads, still checks, still writes, and
    the interleaving it exhibits here is one the real scheduler is free to
    produce at any moment. A defect that only reproduced against a modified
    version of itself would not be a defect.
    """

    def __init__(self, mapping: dict[str, int], readers: int):
        super().__init__(mapping)
        self._readers = readers
        self._barrier = threading.Barrier(readers)
        self._paused = 0
        self._lock = threading.Lock()

    def get(self, key: str, default: int | None = None) -> int | None:  # type: ignore[override]
        value = super().get(key, default)
        with self._lock:
            should_pause = self._paused < self._readers
            if should_pause:
                self._paused += 1
        if should_pause:
            # Released once every reader has arrived. Timed out rather than
            # infinite so a future change that stops calling `get` fails
            # loudly instead of hanging CI.
            self._barrier.wait(timeout=30)
        return value


def race_01() -> Reproduction:
    """Several threads all sell the last unit.

    **Deterministic, not probabilistic.** The first version raced the real
    scheduler with a shortened switch interval and 500 attempts. It reproduced
    reliably on a developer machine and never once on CI's runner — a flaky
    test, which is a bug by our own standard, and a particularly bad one here
    because a green run would have meant "this bug may not exist".

    Forcing the interleaving instead removes the timing entirely. Every seller
    reads the stock, all of them wait until each has read, and only then do
    they write. That `decrement` permits this at all is the defect: read,
    check and write are three steps with nothing holding them together.

    It is also why the bug survived review. CPython rarely switches threads
    inside those four bytecodes, so the window almost never opens in practice
    — and `tests/test_inventory.py` is single-threaded, so it never opens
    there at all.
    """
    service = InventoryService(InventoryClient("http://inventory.internal"))
    sellers = 4

    service.set_stock("sku-1", 1)
    service._stock = _InterleavingStock({"sku-1": 1}, readers=sellers)

    sold: list[int] = []
    bookkeeping = threading.Lock()

    def sell() -> None:
        try:
            remaining = service.decrement("sku-1", 1)
        except ValueError:
            return
        # The harness's own bookkeeping is synchronised. The code under test
        # is not, which is the whole point.
        with bookkeeping:
            sold.append(remaining)

    threads = [threading.Thread(target=sell) for _ in range(sellers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    if len(sold) <= 1:
        raise AssertionError(
            "race-01: only one seller succeeded, so read-check-write held together"
        )

    return Reproduction(
        case_id="race-01",
        kind="behaviour",
        detail=(
            f"{len(sold)} concurrent checkouts each sold the last unit of a stock of 1; "
            f"the ledger now reads {service.available('sku-1')}"
        ),
    )


def race_02() -> Reproduction:
    """Module-level cache leaks between requests."""
    pricing.clear_rate_cache()
    pricing.cache_rate("eu-west", Decimal("0.20"))
    leaked = pricing.cached_rate("eu-west")
    pricing.clear_rate_cache()
    if leaked is None:
        raise AssertionError("race-02: the cache did not retain state across callers")
    return Reproduction(
        case_id="race-02",
        kind="behaviour",
        detail=f"a rate cached while serving one request is visible to the next ({leaked})",
    )


# ── Boundary ───────────────────────────────────────────────────────────────


def boundary_01() -> Reproduction:
    """The first line item is unreachable through the paginated API."""
    service = CartService()
    cart = _cart()
    first_sku = cart.items[0].sku

    # Every offset the 1-based API accepts, across the whole cart.
    reachable = {
        item.sku
        for offset in range(1, len(cart.items) + 1)
        for item in service.page(cart, offset=offset, limit=len(cart.items))
    }
    if first_sku in reachable:
        raise AssertionError("boundary-01: the first row is reachable, so the offset is correct")

    return Reproduction(
        case_id="boundary-01",
        kind="behaviour",
        detail=(
            f"pagination is 1-based and slices as 0-based: {first_sku} is returned by no "
            f"valid page (reachable: {sorted(reachable)}), and the last page is short"
        ),
    )


def boundary_02() -> Reproduction:
    """Chunking drops the last element of every slice."""
    service = ExportService(_settings())
    rows = ["a", "b", "c", "d"]
    chunks = service.chunk(rows, 2)
    flattened = [row for chunk in chunks for row in chunk]
    if len(flattened) == len(rows):
        raise AssertionError("boundary-02: no rows were dropped")
    return Reproduction(
        case_id="boundary-02",
        kind="behaviour",
        detail=f"chunking 4 rows by 2 yielded {flattened} — one per chunk is lost",
    )


# ── Configuration ──────────────────────────────────────────────────────────


def config_01() -> Reproduction:
    """A region that exists everywhere except the config map."""
    return _expect_exception(
        "config-01",
        lambda: region_config("eu-north"),
        "region present in the load balancer and the signup form, absent here",
    )


def config_02() -> Reproduction:
    """EXPORT_BATCH_SIZE is set in production and absent in staging."""
    service = ExportService(_settings(environment="staging", export_batch_size=None))
    return _expect_exception(
        "config-02",
        service.batch_size,
        "env var absent in staging only -> None + int",
    )


# ── Regression ─────────────────────────────────────────────────────────────


def regression_01() -> Reproduction:
    """apply_discount grew a required parameter; this caller was missed."""
    tax = TaxClient("http://tax.internal")
    _responding(tax, lambda request: httpx.Response(200, json={"rate": "0.20"}))
    service = CheckoutService(tax, PaymentClient("http://payments.internal"))
    return _expect_exception(
        "regression-01",
        lambda: service.discounted_subtotal(_cart(), _user()),
        "v2.14.2 added a required `region` argument; discounted_subtotal still passes two",
    )


def regression_02() -> Reproduction:
    """The repair-loop case: quote silently under-quotes during an outage."""
    tax = TaxClient("http://tax.internal")
    _responding(tax, lambda request: httpx.Response(503, json={}))
    service = QuoteService(tax)
    cart = _cart()
    quoted = service.estimate_total(cart)
    if quoted != cart.subtotal():
        raise AssertionError("regression-02: the outage did not produce an untaxed quote")
    return Reproduction(
        case_id="regression-02",
        kind="behaviour",
        detail=(
            f"tax service 503 -> quote falls back to the untaxed subtotal ({quoted}). "
            "tests/test_quote.py asserts this contract, so the obvious fix for "
            "null-prop-01 breaks it and the repair loop has to route around it"
        ),
    )


def regression_03() -> Reproduction:
    """Behaviour changed in v2.14.2 without the signature changing."""
    service = CartService()
    cart = _cart()
    with_tax = service.subtotal_with_tax(cart, Decimal("0.20"))
    if with_tax != cart.subtotal():
        raise AssertionError("regression-03: subtotal_with_tax still applies the rate")
    return Reproduction(
        case_id="regression-03",
        kind="behaviour",
        detail=(
            f"subtotal_with_tax ignores its tax_rate argument and returns {with_tax}; "
            "the name and signature are unchanged since v2.14.1"
        ),
    )


# ── Resource ───────────────────────────────────────────────────────────────


def resource_01() -> Reproduction:
    """Export memory grows with the number of orders."""
    service = ExportService(_settings())

    def order(index: int) -> dict[str, object]:
        return {
            "id": f"ord_{index}",
            "user": {"email": f"user{index}@example.com"},
            "total": "10.00",
            "currency": "USD",
            "status": "paid",
        }

    def peak_for(count: int) -> int:
        tracemalloc.start()
        service.export_all([order(i) for i in range(count)])
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak

    small = peak_for(500)
    large = peak_for(5_000)
    if large <= small * 2:
        raise AssertionError("resource-01: memory did not grow with the export size")
    return Reproduction(
        case_id="resource-01",
        kind="behaviour",
        detail=(
            f"peak memory grows with the export: {small} bytes for 500 orders, "
            f"{large} for 5,000 — every row is held until the last one is rendered"
        ),
    )


# ── Controls ───────────────────────────────────────────────────────────────


def unfixable_01() -> Reproduction:
    """The tax service is down and the handling here is already correct.

    `reserve` raises a typed `UpstreamUnavailable` with the service named, and
    the caller propagates it. There is nothing to fix in this repository. The
    pipeline must terminate as `insufficient_context` and say so.
    """

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "scheduled maintenance"})

    client = InventoryClient("http://inventory.internal")
    _responding(client, unavailable)
    exc = _capture(lambda: client.reserve("sku-1", 1))
    if exc is None:
        raise AssertionError("unfixable-01: the outage did not surface")
    return Reproduction(
        case_id="unfixable-01",
        kind="exception",
        detail="upstream 503 during maintenance; the error path here is already correct",
        defect_in_repo=False,
        exception=exc,
    )


def unfixable_02() -> Reproduction:
    """A DNS failure in the platform, not a defect in the code.

    Distinct from `external-03`, which is about the *absence* of a circuit
    breaker on a hot path. `ping` is written defensively — the failure comes
    back typed and named — and the cluster's resolver is what is broken. No
    change to this repository fixes that.
    """

    def unresolvable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("temporary failure in name resolution", request=request)

    client = InventoryClient("http://inventory.internal")
    _responding(client, unresolvable)
    exc = _capture(client.ping)
    if exc is None:
        raise AssertionError("unfixable-02: the DNS failure did not surface")
    return Reproduction(
        case_id="unfixable-02",
        kind="exception",
        detail="cluster DNS failure; infrastructure, not code",
        defect_in_repo=False,
        exception=exc,
    )


# ── Registry ───────────────────────────────────────────────────────────────

TRIGGERS: dict[str, Callable[[], Reproduction]] = {
    "null-prop-01": null_prop_01,
    "null-prop-02": null_prop_02,
    "null-prop-03": null_prop_03,
    "null-prop-04": null_prop_04,
    "type-mismatch-01": type_mismatch_01,
    "type-mismatch-02": type_mismatch_02,
    "type-mismatch-03": type_mismatch_03,
    "key-error-01": key_error_01,
    "key-error-02": key_error_02,
    "key-error-03": key_error_03,
    "external-01": external_01,
    "external-02": external_02,
    "external-03": external_03,
    "race-01": race_01,
    "race-02": race_02,
    "boundary-01": boundary_01,
    "boundary-02": boundary_02,
    "config-01": config_01,
    "config-02": config_02,
    "regression-01": regression_01,
    "regression-02": regression_02,
    "regression-03": regression_03,
    "resource-01": resource_01,
    "unfixable-01": unfixable_01,
    "unfixable-02": unfixable_02,
}

CASE_IDS: tuple[str, ...] = tuple(TRIGGERS)

#: The exception each `exception`-kind case must produce. Asserted rather than
#: recorded: a trigger that starts raising something else has stopped
#: reproducing its case, and would otherwise pass silently.
EXPECTED_EXCEPTION: dict[str, str] = {
    "null-prop-01": "TypeError",
    "null-prop-02": "AttributeError",
    "null-prop-03": "TypeError",
    "null-prop-04": "AttributeError",
    "type-mismatch-01": "TypeError",
    "type-mismatch-02": "AttributeError",
    "type-mismatch-03": "TypeError",
    "key-error-01": "KeyError",
    "key-error-02": "KeyError",
    "key-error-03": "KeyError",
    "external-01": "UpstreamTimeout",
    "external-02": "RateLimited",
    "config-01": "KeyError",
    "config-02": "TypeError",
    "regression-01": "TypeError",
    "unfixable-01": "UpstreamUnavailable",
    "unfixable-02": "UpstreamUnavailable",
}


def reproduce(case_id: str) -> Reproduction:
    """Run one case's trigger against the synthetic repository."""
    try:
        trigger = TRIGGERS[case_id]
    except KeyError:
        raise KeyError(f"{case_id} is not a fixture case") from None
    return trigger()


def reproduce_all() -> dict[str, Reproduction]:
    return {case_id: reproduce(case_id) for case_id in CASE_IDS}


__all__ = [
    "CASE_IDS",
    "EXPECTED_EXCEPTION",
    "TRIGGERS",
    "RateLimited",
    "Reproduction",
    "UpstreamTimeout",
    "UpstreamUnavailable",
    "reproduce",
    "reproduce_all",
]
