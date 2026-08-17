"""Reading an error message: normalisation, and the symbols hiding in it.

**This normaliser is not S2's and must never be used for fingerprinting.**
They look similar and answer different questions. S2's (`03` §S2) reduces a
message to a *hash input*, so it flattens every quoted string to `<str>` —
`'NoneType'`, `'coupon_code'` and `'eu-north'` all collapse to the same token,
which is correct for grouping and useless for reasoning. S4's keeps the
identifiers, because they are the strongest retrieval signal in the whole
payload: `'coupon_code'` is the key S5 must find the writer of, and
`apply_discount` is the function whose signature changed.

What S4 does replace is types and magnitudes, which is what `03` §S4's output
contract shows:

    unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'
    → unsupported operand type(s) for +: '<type>' and '<type>'

Distinguishing a quoted *type* from a quoted *value* is the only subtle part.
`'dict'` is a type and `'user'` is a dictionary key, though both are lowercase
identifiers in quotes. The test is membership of the builtin type names, or a
dotted path, or CamelCase — not "is it quoted".
"""

from __future__ import annotations

import re

#: Builtin and near-builtin type names. A quoted one of these is a type.
#: `NoneType` is not a builtin name you can call, but it is what CPython puts
#: in the message, which is the only thing that matters here.
_BUILTIN_TYPES = frozenset(
    {
        "NoneType",
        "bool",
        "bytearray",
        "bytes",
        "complex",
        "dict",
        "float",
        "frozenset",
        "function",
        "int",
        "list",
        "memoryview",
        "object",
        "range",
        "set",
        "slice",
        "str",
        "tuple",
        "type",
        "undefined",
    }
)

#: `decimal.Decimal`, `models.cart.Cart` — a dotted path ending in a segment
#: that starts with a capital, or a bare CamelCase name. Deliberately does not
#: match a bare lowercase identifier: `'user'` is a key, not a class.
_LOOKS_LIKE_TYPE = re.compile(r"^(?:[A-Za-z_]\w*\.)*[A-Z]\w*$")

_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"")

#: Three or more digits, matching S2's threshold. Below that the number is
#: usually part of the meaning (`for +: 2 arguments`) rather than a value.
_LONG_NUMBER = re.compile(r"\b\d{3,}\b")

#: `apply_discount() missing 1 required positional argument`
_CALLED_FUNCTION = re.compile(r"\b([A-Za-z_]\w*)\s*\(\s*\)")

#: `'NoneType' object has no attribute 'percent_off'`
_MISSING_ATTRIBUTE = re.compile(r"has no attribute\s+'([^']+)'")

#: `missing 1 required positional argument: 'region'`
_MISSING_ARGUMENT = re.compile(r"required (?:positional |keyword-only )?arguments?:\s*(.+)$")

#: A bare quoted identifier, which is what a `KeyError` message is in its
#: entirety: `KeyError: 'coupon_code'`.
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")


def _is_type_name(token: str) -> bool:
    return token in _BUILTIN_TYPES or bool(_LOOKS_LIKE_TYPE.match(token))


def normalize_message(message: str | None) -> str:
    """Replace types and magnitudes; keep identifiers.

    Idempotent: `<type>` and `<num>` contain no digits and are not quoted
    identifiers, so a second pass changes nothing. Asserted in the tests,
    because a normaliser whose output depends on how many times it ran would
    make the S4 output depend on whether the stage had been retried.
    """
    if not message:
        return ""

    def replace(match: re.Match[str]) -> str:
        token = match.group(1) if match.group(1) is not None else match.group(2)
        quote = "'" if match.group(1) is not None else '"'
        if _is_type_name(token):
            return f"{quote}<type>{quote}"
        return match.group(0)

    normalised = _QUOTED.sub(replace, message)
    normalised = _LONG_NUMBER.sub("<num>", normalised)
    return " ".join(normalised.split())


def symbols_in_message(message: str | None) -> tuple[str, ...]:
    """Identifiers the message names, in the order they would help retrieval.

    Every one of these is a real, checkable string: a function that was called
    with the wrong arity, an attribute that did not exist, a key that was
    missing. S5 searches for them and S6 must cite them, so a name invented
    here would surface as an unresolvable symbol two stages later — which is
    why nothing is inferred, only extracted.
    """
    if not message:
        return ()

    found: list[str] = []

    def add(token: str) -> None:
        token = token.strip().strip("'\"")
        if token and _IDENTIFIER.match(token) and not _is_type_name(token) and token not in found:
            found.append(token)

    for match in _CALLED_FUNCTION.finditer(message):
        add(match.group(1))
    for match in _MISSING_ATTRIBUTE.finditer(message):
        add(match.group(1))
    for match in _MISSING_ARGUMENT.finditer(message):
        for part in match.group(1).split(","):
            add(part)

    stripped = message.strip()
    if (quoted := _QUOTED.fullmatch(stripped)) is not None:
        add(quoted.group(1) if quoted.group(1) is not None else quoted.group(2))

    return tuple(found)


#: Text shaped like an instruction rather than like data (`A2` §2 rule 5).
#: Recorded on the understanding and never acted on. This is detection at the
#: *ingest-to-prompt* boundary; the fencing itself is T5.2's job.
_INJECTION_PATTERNS = re.compile(
    r"""
    ignore\s+(?:all\s+|any\s+|the\s+)?previous
  | disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above)
  | you\s+are\s+now\b
  | new\s+instructions?\s*:
  | system\s+prompt
  | </?untrusted_context>
  | \bBEGIN\s+SYSTEM\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def looks_like_an_instruction(text: str | None) -> bool:
    """Whether untrusted text is trying to be read as an instruction."""
    return bool(text) and bool(_INJECTION_PATTERNS.search(text or ""))
