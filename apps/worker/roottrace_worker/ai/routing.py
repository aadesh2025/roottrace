"""Tier routing (`06` §2.2) — loads `infra/config/models.yaml` (or wherever
`RT_LLM_CONFIG_PATH` points, `A3`) into an ordered provider list per tier.

**A config edit, not a code change.** Nothing here hardcodes a model name;
`gateway.py` asks this module for the ordered list for a tier and walks it,
so a provider outage, a price change, or a new model release never touches
Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from roottrace_worker.ai.contracts import FailoverTrigger, Tier

DEFAULT_CONFIG_PATH = Path("infra/config/models.yaml")


class InvalidModelConfigError(Exception):
    """`models.yaml` failed to parse into something `06` §2.2 describes —
    fails loudly at boot rather than at the first pipeline run that needs a
    tier the malformed section left out."""


@dataclass(frozen=True, slots=True)
class TierEntry:
    """One line of a tier's ordered provider list."""

    provider: str
    model: str
    max_tokens: int
    timeout_s: float
    dimensions: int | None = None


@dataclass(frozen=True, slots=True)
class BackoffConfig:
    base_ms: int
    factor: float
    jitter: bool
    max_ms: int


@dataclass(frozen=True, slots=True)
class FailoverConfig:
    trigger_on: tuple[FailoverTrigger, ...]
    max_provider_attempts: int
    backoff: BackoffConfig


@dataclass(frozen=True, slots=True)
class ModelRouting:
    """The whole of `models.yaml`, parsed."""

    tiers: dict[Tier, tuple[TierEntry, ...]]
    failover: FailoverConfig

    def entries_for(self, tier: Tier) -> tuple[TierEntry, ...]:
        entries = self.tiers.get(tier)
        if not entries:
            raise InvalidModelConfigError(f"tier {tier!r} has no configured providers")
        return entries


_REQUIRED_TIERS: tuple[Tier, ...] = ("fast", "reasoning-a", "reasoning-b", "embed")


def _parse_entry(raw: Any, *, tier: str) -> TierEntry:
    if not isinstance(raw, dict):
        raise InvalidModelConfigError(f"tier {tier!r}: each entry must be a mapping")
    try:
        provider = str(raw["provider"])
        model = str(raw["model"])
    except KeyError as exc:
        raise InvalidModelConfigError(f"tier {tier!r}: entry missing {exc}") from exc
    return TierEntry(
        provider=provider,
        model=model,
        max_tokens=int(raw.get("max_tokens", 4096)),
        timeout_s=float(raw.get("timeout_s", 60)),
        dimensions=int(raw["dimensions"]) if "dimensions" in raw else None,
    )


def parse_model_routing(document: dict[str, Any]) -> ModelRouting:
    """Pure parse — no filesystem access, so unit tests exercise malformed
    config directly without writing a temp file."""
    raw_tiers = document.get("tiers")
    if not isinstance(raw_tiers, dict):
        raise InvalidModelConfigError("missing top-level 'tiers' mapping")

    tiers: dict[Tier, tuple[TierEntry, ...]] = {}
    for tier_name, raw_entries in raw_tiers.items():
        if not isinstance(raw_entries, list) or not raw_entries:
            raise InvalidModelConfigError(f"tier {tier_name!r} must be a non-empty list")
        tiers[tier_name] = tuple(_parse_entry(entry, tier=tier_name) for entry in raw_entries)

    missing = [tier for tier in _REQUIRED_TIERS if tier not in tiers]
    if missing:
        raise InvalidModelConfigError(f"missing required tier(s): {', '.join(missing)}")

    raw_failover = document.get("failover")
    if not isinstance(raw_failover, dict):
        raise InvalidModelConfigError("missing top-level 'failover' mapping")
    raw_backoff = raw_failover.get("backoff")
    if not isinstance(raw_backoff, dict):
        raise InvalidModelConfigError("'failover.backoff' must be a mapping")

    failover = FailoverConfig(
        trigger_on=tuple(raw_failover.get("trigger_on", ())),
        max_provider_attempts=int(raw_failover.get("max_provider_attempts", 2)),
        backoff=BackoffConfig(
            base_ms=int(raw_backoff.get("base_ms", 1000)),
            factor=float(raw_backoff.get("factor", 2)),
            jitter=bool(raw_backoff.get("jitter", True)),
            max_ms=int(raw_backoff.get("max_ms", 16000)),
        ),
    )
    return ModelRouting(tiers=tiers, failover=failover)


def load_model_routing(path: Path | str = DEFAULT_CONFIG_PATH) -> ModelRouting:
    text = Path(path).read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise InvalidModelConfigError(f"{path}: did not parse to a mapping")
    return parse_model_routing(document)
