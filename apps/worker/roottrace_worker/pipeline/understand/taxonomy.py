"""The exception taxonomy (`03` §S4).

Nine families, each with a cause class and — the part that matters — a
**retrieval hint**. The hint is not documentation. It is injected into the
prompt at T5.2 and it steers the deterministic plan in `plan.py` today, so
classifying into the wrong family sends S5 to fetch the wrong code.

The rule that earns its place here is the null/undefined one:

> When a value is unexpectedly None, the defect is usually in whatever
> PRODUCED that value, not in the code that consumed it. (`A2` §3)

Every frame in a `NoneType` traceback points at the consumer. The producer is
never in the stack. That single hint is the difference between patching the
symptom and patching the bug.

**Classification is over the exception alone** — type and message — and never
over breadcrumbs. Two of the twenty-five fixtures are knowable only from their
breadcrumbs (`race-01` is a lost update, `resource-01` is unbounded growth),
and both are deliberately left misclassified rather than pattern-matched on
their fixture text. Fitting the classifier to the corpus would raise the
measured score while making the pipeline no better on the twenty-sixth error,
which is precisely the failure `A1` §9 warns about. The LLM extractor reads
breadcrumbs and fixes these at T5.2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from roottrace_worker.pipeline.understand.contracts import ExceptionFamily


@dataclass(frozen=True, slots=True)
class FamilyProfile:
    cause_class: str
    retrieval_hint: str


#: `03` §S4's table, verbatim in substance. Injected into the prompt as the
#: domain layer at T5.2, so the wording is the contract with the model.
PROFILES: dict[ExceptionFamily, FamilyProfile] = {
    ExceptionFamily.NULL_UNDEFINED: FamilyProfile(
        cause_class="Missing guard on optional value, upstream returned null",
        retrieval_hint="Fetch the producer of the null value, not just the consumer",
    ),
    ExceptionFamily.TYPE_MISMATCH: FamilyProfile(
        cause_class="Contract drift between modules",
        retrieval_hint="Fetch both sides of the boundary",
    ),
    ExceptionFamily.KEY_INDEX: FamilyProfile(
        cause_class="Shape assumption violated",
        retrieval_hint="Fetch where the structure is built",
    ),
    ExceptionFamily.INTEGRATION: FamilyProfile(
        cause_class="External failure, missing retry/fallback",
        retrieval_hint="Fetch the client wrapper and its config",
    ),
    ExceptionFamily.DATA_DB: FamilyProfile(
        cause_class="Constraint or migration mismatch",
        retrieval_hint="Fetch the model and recent migrations",
    ),
    ExceptionFamily.CONCURRENCY: FamilyProfile(
        cause_class="Shared mutable state, missing lock",
        retrieval_hint="Fetch all writers to the shared resource",
    ),
    ExceptionFamily.RESOURCE: FamilyProfile(
        cause_class="Leak or unbounded growth",
        retrieval_hint="Fetch the allocation site and its lifecycle",
    ),
    ExceptionFamily.AUTH: FamilyProfile(
        cause_class="Missing/expired credential, scope mismatch",
        retrieval_hint="Fetch the auth middleware",
    ),
    ExceptionFamily.SERIALIZATION: FamilyProfile(
        cause_class="Malformed input, schema drift",
        retrieval_hint="Fetch the schema definition and the parser",
    ),
    ExceptionFamily.UNCLASSIFIED: FamilyProfile(
        cause_class="Unknown",
        retrieval_hint="Fetch the failing frame and its immediate callers",
    ),
}

#: A null value named in the message, in any of the runtimes V1 and V2 cover.
#: Checked before the type table because the *type* of a null-propagation bug
#: is almost always `TypeError` or `AttributeError`, which belong to two other
#: families when no null is involved. `03` §S4 lists `TypeError: NoneType` and
#: `TypeError: undefined is not a function` under Null/undefined for exactly
#: this reason.
_NULL_IN_MESSAGE = re.compile(
    r"""
    \bNoneType\b
  | \bNullPointer\w*
  | (?<!\w)undefined\s+is\s+not\b
  | \bof\s+undefined\b
  | \bof\s+null\b
  | \bis\s+(?:not\s+)?(?:None|null|undefined)\b
    """,
    re.VERBOSE,
)

#: Exact exception type names. Checked in dictionary order is not enough — the
#: lookup is exact, so order is irrelevant here and the substring rules below
#: are what need ordering.
_BY_TYPE: dict[str, ExceptionFamily] = {
    "AttributeError": ExceptionFamily.KEY_INDEX,
    "IndexError": ExceptionFamily.KEY_INDEX,
    "KeyError": ExceptionFamily.KEY_INDEX,
    "LookupError": ExceptionFamily.KEY_INDEX,
    "ClassCastException": ExceptionFamily.TYPE_MISMATCH,
    "TypeError": ExceptionFamily.TYPE_MISMATCH,
    "ValueError": ExceptionFamily.TYPE_MISMATCH,
    "BrokenPipeError": ExceptionFamily.INTEGRATION,
    "ConnectionError": ExceptionFamily.INTEGRATION,
    "ConnectionRefusedError": ExceptionFamily.INTEGRATION,
    "ConnectionResetError": ExceptionFamily.INTEGRATION,
    "HTTPError": ExceptionFamily.INTEGRATION,
    "SSLError": ExceptionFamily.INTEGRATION,
    "TimeoutError": ExceptionFamily.INTEGRATION,
    "DataError": ExceptionFamily.DATA_DB,
    "DoesNotExist": ExceptionFamily.DATA_DB,
    "IntegrityError": ExceptionFamily.DATA_DB,
    "OperationalError": ExceptionFamily.DATA_DB,
    "ProgrammingError": ExceptionFamily.DATA_DB,
    "DeadlockDetected": ExceptionFamily.CONCURRENCY,
    "MemoryError": ExceptionFamily.RESOURCE,
    "OSError": ExceptionFamily.RESOURCE,
    "IOError": ExceptionFamily.RESOURCE,
    "PermissionError": ExceptionFamily.AUTH,
    "JSONDecodeError": ExceptionFamily.SERIALIZATION,
    "UnicodeDecodeError": ExceptionFamily.SERIALIZATION,
    "ValidationError": ExceptionFamily.SERIALIZATION,
}

#: Applied to the *lower-cased type name* when the exact lookup misses, in
#: order. Services raise their own exception classes far more often than the
#: builtins — the corpus alone has `UpstreamTimeout`, `UpstreamUnavailable`
#: and `RateLimited` — and a taxonomy that only knows the standard library
#: would return `unclassified` for most of a real application's errors.
_BY_TYPE_SUBSTRING: tuple[tuple[str, ExceptionFamily], ...] = (
    # `03` §S4 lists `NullPointerException` under Null/undefined. Its message
    # names no null — the *type* is the whole signal — so the message check
    # above cannot catch it.
    ("nullpointer", ExceptionFamily.NULL_UNDEFINED),
    ("nullreference", ExceptionFamily.NULL_UNDEFINED),
    ("deadlock", ExceptionFamily.CONCURRENCY),
    ("racecondition", ExceptionFamily.CONCURRENCY),
    ("unauthor", ExceptionFamily.AUTH),
    ("forbidden", ExceptionFamily.AUTH),
    ("permission", ExceptionFamily.AUTH),
    ("credential", ExceptionFamily.AUTH),
    ("timeout", ExceptionFamily.INTEGRATION),
    ("unavailable", ExceptionFamily.INTEGRATION),
    ("ratelimit", ExceptionFamily.INTEGRATION),
    ("throttl", ExceptionFamily.INTEGRATION),
    ("upstream", ExceptionFamily.INTEGRATION),
    ("connection", ExceptionFamily.INTEGRATION),
    ("serializ", ExceptionFamily.SERIALIZATION),
    ("deserializ", ExceptionFamily.SERIALIZATION),
    ("decodeerror", ExceptionFamily.SERIALIZATION),
    ("parseerror", ExceptionFamily.SERIALIZATION),
    ("validationerror", ExceptionFamily.SERIALIZATION),
    ("integrity", ExceptionFamily.DATA_DB),
    ("notfound", ExceptionFamily.DATA_DB),
    ("doesnotexist", ExceptionFamily.DATA_DB),
)


def classify(exception_type: str | None, message: str | None = None) -> ExceptionFamily:
    """The family of an exception, from its type and message alone.

    The null check runs first and overrides the type table. `TypeError` is
    `type_mismatch` when a `Decimal` meets a `str`, and `null_undefined` when
    it meets a `NoneType` — same type, different bug, different retrieval.
    """
    if message and _NULL_IN_MESSAGE.search(message):
        return ExceptionFamily.NULL_UNDEFINED

    if not exception_type:
        return ExceptionFamily.UNCLASSIFIED

    if (family := _BY_TYPE.get(exception_type)) is not None:
        return family

    lowered = exception_type.lower()
    for needle, family in _BY_TYPE_SUBSTRING:
        if needle in lowered:
            return family

    return ExceptionFamily.UNCLASSIFIED


def retrieval_hint(family: ExceptionFamily) -> str:
    return PROFILES[family].retrieval_hint


def cause_class(family: ExceptionFamily) -> str:
    return PROFILES[family].cause_class
