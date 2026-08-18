"""Tier routing config parsing (`06` §2.2, T5.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from roottrace_worker.ai.routing import (
    InvalidModelConfigError,
    load_model_routing,
    parse_model_routing,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_CONFIG = REPO_ROOT / "infra" / "config" / "models.yaml"


def _document(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tiers": {
            "fast": [{"provider": "anthropic", "model": "claude-haiku-4-5"}],
            "reasoning-a": [{"provider": "anthropic", "model": "claude-sonnet-5"}],
            "reasoning-b": [{"provider": "openai", "model": "gpt-5"}],
            "embed": [{"provider": "voyage", "model": "voyage-code-3", "dimensions": 1536}],
        },
        "failover": {
            "trigger_on": ["rate_limit", "timeout", "server_error", "content_filter"],
            "max_provider_attempts": 2,
            "backoff": {"base_ms": 1000, "factor": 2, "jitter": True, "max_ms": 16000},
        },
    }
    base.update(overrides)
    return base


def test_the_real_models_yaml_parses() -> None:
    routing = load_model_routing(REAL_CONFIG)
    assert routing.entries_for("fast")[0].provider == "anthropic"
    assert routing.failover.max_provider_attempts == 2


def test_reasoning_a_and_reasoning_b_lead_with_different_providers() -> None:
    """`06` §2.3: the critic's independence is architectural — this is the
    one assertion that would catch someone accidentally reordering
    `models.yaml` and losing it."""
    routing = load_model_routing(REAL_CONFIG)
    assert (
        routing.entries_for("reasoning-a")[0].provider
        != routing.entries_for("reasoning-b")[0].provider
    )


def test_a_well_formed_document_parses() -> None:
    routing = parse_model_routing(_document())
    assert routing.entries_for("fast")[0].model == "claude-haiku-4-5"
    assert routing.failover.backoff.base_ms == 1000


def test_default_max_tokens_and_timeout_apply_when_omitted() -> None:
    routing = parse_model_routing(_document())
    entry = routing.entries_for("fast")[0]
    assert entry.max_tokens == 4096
    assert entry.timeout_s == 60


def test_a_missing_required_tier_is_rejected() -> None:
    doc = _document()
    del doc["tiers"]["embed"]
    with pytest.raises(InvalidModelConfigError, match="embed"):
        parse_model_routing(doc)


def test_an_empty_tier_list_is_rejected() -> None:
    doc = _document()
    doc["tiers"]["fast"] = []
    with pytest.raises(InvalidModelConfigError):
        parse_model_routing(doc)


def test_an_entry_missing_provider_is_rejected() -> None:
    doc = _document()
    doc["tiers"]["fast"] = [{"model": "x"}]
    with pytest.raises(InvalidModelConfigError, match="provider"):
        parse_model_routing(doc)


def test_missing_tiers_key_is_rejected() -> None:
    with pytest.raises(InvalidModelConfigError, match="tiers"):
        parse_model_routing({"failover": _document()["failover"]})


def test_missing_failover_key_is_rejected() -> None:
    doc = _document()
    del doc["failover"]
    with pytest.raises(InvalidModelConfigError, match="failover"):
        parse_model_routing(doc)


def test_requesting_an_unconfigured_tier_raises() -> None:
    doc = _document()
    routing = parse_model_routing(doc)
    with pytest.raises(InvalidModelConfigError):
        routing.entries_for("nonexistent-tier")  # type: ignore[arg-type]
