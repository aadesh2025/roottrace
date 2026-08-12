"""Typed configuration containers.

`TypedRegistry` is the shared helper behind `type-mismatch-03`: it is generic in
its value type, but `merge` accepts any registry and copies values across, so a
`TypedRegistry[Decimal]` can be handed the contents of a `TypedRegistry[str]`
without anything complaining until arithmetic is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class TypedRegistry(Generic[T]):
    name: str
    values: dict[str, T] = field(default_factory=dict)

    def get(self, key: str) -> T:
        return self.values[key]

    def put(self, key: str, value: T) -> None:
        self.values[key] = value

    def merge(self, other: "TypedRegistry") -> None:
        # No variance check. `other` is deliberately unparameterised, so the
        # type checker has nothing to complain about and the values land in a
        # registry that promises a different type.
        self.values.update(other.values)


@dataclass
class FeatureFlags:
    enable_async_export: bool = False
    enable_tax_cache: bool = False
