"""The exception taxonomy (`03` §S4).

The rule under test that matters most is the one that overrides the type
table: a `TypeError` is two different bugs depending on whether a null was
involved, and the two families send S5 to fetch different code.
"""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.understand.contracts import ExceptionFamily
from roottrace_worker.pipeline.understand.taxonomy import (
    PROFILES,
    cause_class,
    classify,
    retrieval_hint,
)

pytestmark = pytest.mark.unit


def test_every_family_has_a_profile() -> None:
    """A family with no retrieval hint would classify correctly and steer
    nothing, which is the only reason the classification exists."""
    for family in ExceptionFamily:
        assert family in PROFILES
        assert retrieval_hint(family)
        assert cause_class(family)


@pytest.mark.parametrize(
    ("exception_type", "message", "expected"),
    [
        ("KeyError", "'coupon_code'", ExceptionFamily.KEY_INDEX),
        ("IndexError", "list index out of range", ExceptionFamily.KEY_INDEX),
        ("AttributeError", "'dict' object has no attribute 'sku'", ExceptionFamily.KEY_INDEX),
        (
            "TypeError",
            "unsupported operand type(s) for +: 'Decimal' and 'str'",
            ExceptionFamily.TYPE_MISMATCH,
        ),
        ("ValueError", "quote drift for cart c_8821", ExceptionFamily.TYPE_MISMATCH),
        ("ConnectionError", "connection refused", ExceptionFamily.INTEGRATION),
        ("IntegrityError", "duplicate key value", ExceptionFamily.DATA_DB),
        ("MemoryError", "", ExceptionFamily.RESOURCE),
        ("PermissionError", "", ExceptionFamily.AUTH),
        ("JSONDecodeError", "Expecting value", ExceptionFamily.SERIALIZATION),
    ],
)
def test_the_type_table(exception_type: str, message: str, expected: ExceptionFamily) -> None:
    assert classify(exception_type, message) == expected


@pytest.mark.parametrize(
    ("exception_type", "message"),
    [
        ("TypeError", "unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'"),
        ("TypeError", "unsupported operand type(s) for +: 'NoneType' and 'int'"),
        ("AttributeError", "'NoneType' object has no attribute 'percent_off'"),
        ("TypeError", "undefined is not a function"),
        ("NullPointerException", "at com.acme.Checkout.total"),
        ("TypeError", "Cannot read properties of undefined (reading 'id')"),
    ],
)
def test_a_null_in_the_message_wins_over_the_type(exception_type: str, message: str) -> None:
    """`03` §S4 files `TypeError: NoneType` under Null/undefined, not under
    Type mismatch. The same `TypeError` with two real types is a mismatch —
    the message is the only thing that separates them."""
    assert classify(exception_type, message) == ExceptionFamily.NULL_UNDEFINED


def test_the_same_type_classifies_two_ways() -> None:
    """The whole point of reading the message, stated as one assertion."""
    null = classify("TypeError", "unsupported operand type(s) for +: 'Decimal' and 'NoneType'")
    mismatch = classify("TypeError", "unsupported operand type(s) for +: 'Decimal' and 'str'")
    assert null is ExceptionFamily.NULL_UNDEFINED
    assert mismatch is ExceptionFamily.TYPE_MISMATCH
    assert retrieval_hint(null) != retrieval_hint(mismatch)


@pytest.mark.parametrize(
    ("exception_type", "expected"),
    [
        ("UpstreamTimeout", ExceptionFamily.INTEGRATION),
        ("UpstreamUnavailable", ExceptionFamily.INTEGRATION),
        ("RateLimited", ExceptionFamily.INTEGRATION),
        ("ServiceThrottled", ExceptionFamily.INTEGRATION),
        ("DeadlockDetected", ExceptionFamily.CONCURRENCY),
        ("UnauthorizedError", ExceptionFamily.AUTH),
        ("SchemaValidationError", ExceptionFamily.SERIALIZATION),
        ("OrderDoesNotExist", ExceptionFamily.DATA_DB),
    ],
)
def test_application_defined_types_classify_by_name(
    exception_type: str, expected: ExceptionFamily
) -> None:
    """Services raise their own exception classes far more often than the
    builtins. A taxonomy that only knew the standard library would answer
    `unclassified` for most of a real application's errors."""
    assert classify(exception_type, "") == expected


@pytest.mark.parametrize("exception_type", ["", None, "WidgetFrobnicationFailure"])
def test_an_unrecognised_type_is_unclassified_rather_than_guessed(
    exception_type: str | None,
) -> None:
    """Defaulting to a family would attach a retrieval hint that sends S5 to
    fetch the wrong code, and would score as correct whenever the guess
    happened to match."""
    assert classify(exception_type, "something went wrong") == ExceptionFamily.UNCLASSIFIED


def test_classification_reads_no_breadcrumbs() -> None:
    """A guard against fitting the classifier to the corpus.

    `race-01` is a lost update whose exception is an ordinary `ValueError`;
    only its breadcrumbs reveal the concurrency. Scoring it correctly here
    would mean matching on its fixture text, which raises the measured number
    and improves nothing on the twenty-sixth error (`A1` §9). The extractor
    reads breadcrumbs at T5.2; this function must not.
    """
    assert classify("ValueError", "insufficient stock for sku-1") is ExceptionFamily.TYPE_MISMATCH
