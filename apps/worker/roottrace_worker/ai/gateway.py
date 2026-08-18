"""`LLMGateway.complete` (`06` §2.4) — the one seam every pipeline stage
calls through. Nothing above this module talks to a provider SDK directly.

**Every provider call that returns a response writes its own `llm_calls`
row**, immediately — a native attempt, a repair call, and (if the output-
side injection check forces a retry) a retry call are each a real, billable
round-trip and each gets recorded, not batched into one row per
`complete()` invocation. A provider attempt that fails before returning
anything (rate-limited, timed out) writes no row: nothing was billed, so
there is nothing to attribute. Deterministic salvage (`06` §4.1's attempt 3)
makes no provider call and therefore writes no row of its own either — it
re-validates the repair call's `raw_text`.

The `LLMResult` handed back to the caller reflects whichever call's output
was ultimately accepted; `06` §8.3's per-investigation cost profiling reads
the full set of `llm_calls` rows, not a single `LLMResult`, when it needs
the total."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from roottrace_worker.ai import cache as cache_module
from roottrace_worker.ai import circuit_breaker, structured
from roottrace_worker.ai.contracts import CompletionRequest, LLMCallRecord, LLMResult, Tier
from roottrace_worker.ai.cost import compute_cost_micro_usd
from roottrace_worker.ai.errors import (
    AllProvidersExhaustedError,
    ProviderError,
    SchemaValidationFailedError,
    SuspiciousContentRejectedError,
)
from roottrace_worker.ai.providers.base import Provider, ProviderRequest, ProviderResponse
from roottrace_worker.ai.redaction import scan_and_redact
from roottrace_worker.ai.retry import compute_backoff_ms, should_fail_over
from roottrace_worker.ai.routing import ModelRouting
from roottrace_worker.ai.storage import ObjectStore

#: `04` §8's `attempt` numbering: the native call is 1, the repair call is
#: 2. Salvage (attempt 3 in `06` §4.1's diagram) makes no call and writes
#: no row, so no `attempt` value is reserved for it here.
_NATIVE_ATTEMPT = 1
_REPAIR_ATTEMPT = 2


class LLMCallsRepositoryLike(Protocol):
    async def insert(self, record: LLMCallRecord) -> str: ...


class CacheRedisLike(cache_module.RedisLike, Protocol): ...


class BreakerRedisLike(circuit_breaker.RedisLike, Protocol): ...


@dataclass(frozen=True, slots=True)
class _DispatchResult:
    provider: str
    model: str
    response: ProviderResponse
    failover_from: str | None
    latency_ms: int


class LLMGateway:
    def __init__(
        self,
        *,
        providers: dict[str, Provider],
        routing: ModelRouting,
        storage: ObjectStore,
        db: LLMCallsRepositoryLike,
        cache_redis: CacheRedisLike | None = None,
        breaker_redis: BreakerRedisLike | None = None,
        daily_cap_micro_usd: int = circuit_breaker.DEFAULT_DAILY_CAP_MICRO_USD,
        monthly_cap_micro_usd: int = circuit_breaker.DEFAULT_MONTHLY_CAP_MICRO_USD,
        reservation_estimate_micro_usd: int = circuit_breaker.DEFAULT_RESERVATION_ESTIMATE_MICRO_USD,
        cache_ttl_seconds: int = cache_module.DEFAULT_CACHE_TTL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers
        self._routing = routing
        self._storage = storage
        self._db = db
        self._cache_redis = cache_redis
        self._breaker_redis = breaker_redis
        self._daily_cap = daily_cap_micro_usd
        self._monthly_cap = monthly_cap_micro_usd
        self._reservation_estimate = reservation_estimate_micro_usd
        self._cache_ttl = cache_ttl_seconds
        self._sleep = sleep
        self._clock = clock

    async def complete[M: BaseModel](self, request: CompletionRequest[M]) -> LLMResult[M]:
        schema = request.output_model.model_json_schema()
        schema_name = request.output_model.__name__
        redacted_system, _ = scan_and_redact(request.prompt.system)
        redacted_user, _ = scan_and_redact(request.prompt.user)
        prompt_hash = _hash_prompt(
            prompt_version=request.prompt_version, system=redacted_system, user=redacted_user
        )

        if request.deterministic and self._cache_redis is not None:
            cached = await cache_module.get_cached(self._cache_redis, prompt_hash)
            if cached is not None:
                return await self._result_from_cache(
                    request, cached, prompt_hash, schema_name=schema_name
                )

        reservation = None
        if self._breaker_redis is not None:
            reservation = await circuit_breaker.reserve(
                self._breaker_redis,
                project_id=request.project_id,
                yyyymmdd=request.now.date().isoformat(),
                estimate_micro_usd=self._reservation_estimate,
                daily_cap_micro_usd=self._daily_cap,
                monthly_cap_micro_usd=self._monthly_cap,
            )

        actual_cost = 0
        try:
            result = await self._complete_uncached(
                request,
                schema=schema,
                schema_name=schema_name,
                prompt_hash=prompt_hash,
                redacted_system=redacted_system,
                redacted_user=redacted_user,
            )
            actual_cost = result.cost_micro_usd
            return result
        finally:
            if reservation is not None and self._breaker_redis is not None:
                await circuit_breaker.reconcile(
                    self._breaker_redis, reservation, actual_micro_usd=actual_cost
                )

    async def _complete_uncached[M: BaseModel](
        self,
        request: CompletionRequest[M],
        *,
        schema: dict[str, object],
        schema_name: str,
        prompt_hash: str,
        redacted_system: str,
        redacted_user: str,
    ) -> LLMResult[M]:
        start = self._clock()

        native = await self._dispatch_tier(
            request.tier,
            system=redacted_system,
            user=redacted_user,
            schema=schema,
            schema_name=schema_name,
        )
        native_parse = structured.parse_and_validate(native.response.raw_text, request.output_model)
        await self._record_call(
            request,
            native,
            prompt_hash=prompt_hash,
            attempt=_NATIVE_ATTEMPT,
            schema_repair_used=False,
            raw_text=native.response.raw_text,
        )

        accepted = native
        parsed = native_parse
        schema_repair_used = False

        if not native_parse.ok:
            repair_system, repair_user = structured.build_repair_prompt(
                original_raw_text=native.response.raw_text,
                validator_error=native_parse.error or "unknown validation error",
                schema_name=schema_name,
            )
            repair = await self._dispatch_tier(
                "fast",
                system=repair_system,
                user=repair_user,
                schema=schema,
                schema_name=schema_name,
            )
            repair_parse = structured.parse_and_validate(
                repair.response.raw_text, request.output_model
            )
            await self._record_call(
                request,
                repair,
                prompt_hash=prompt_hash,
                attempt=_REPAIR_ATTEMPT,
                schema_repair_used=True,
                raw_text=repair.response.raw_text,
            )
            accepted, parsed, schema_repair_used = repair, repair_parse, True

            if not repair_parse.ok:
                salvaged = structured.salvage(repair.response.raw_text)
                salvage_parse = (
                    structured.parse_and_validate(salvaged, request.output_model)
                    if salvaged is not None
                    else structured.ParseResult[M](output=None, error="nothing salvageable")
                )
                parsed = salvage_parse
                if not salvage_parse.ok:
                    raise SchemaValidationFailedError(schema_name, salvage_parse.error or "unknown")

        if parsed.output is None:  # pragma: no cover — unreachable, every branch above either
            # sets a validated output or raises first; kept as a hard stop rather than an
            # `assert` (stripped under `-O`) in case a future branch breaks that invariant.
            raise SchemaValidationFailedError(schema_name, "no output produced")

        raw_text_for_check = accepted.response.raw_text
        if request.prompt.flagged_injection_patterns and any(
            pattern in raw_text_for_check for pattern in request.prompt.flagged_injection_patterns
        ):
            raise SuspiciousContentRejectedError(request.prompt.flagged_injection_patterns[0])

        latency_ms = int((self._clock() - start) * 1000)
        cost = compute_cost_micro_usd(
            provider=accepted.provider,
            model=accepted.model,
            tokens_in=accepted.response.tokens_in,
            tokens_out=accepted.response.tokens_out,
        )

        if request.deterministic and self._cache_redis is not None:
            await cache_module.put_cached(
                self._cache_redis,
                prompt_hash,
                cache_module.CachedCompletion(
                    provider=accepted.provider,
                    model=accepted.model,
                    raw_text=accepted.response.raw_text,
                    tokens_in=accepted.response.tokens_in,
                    tokens_out=accepted.response.tokens_out,
                ),
                ttl_seconds=self._cache_ttl,
            )

        return LLMResult(
            output=parsed.output,
            provider=accepted.provider,
            model=accepted.model,
            tier=request.tier,
            tokens_in=accepted.response.tokens_in,
            tokens_out=accepted.response.tokens_out,
            cached_tokens_in=accepted.response.cached_tokens_in,
            cost_micro_usd=cost,
            latency_ms=latency_ms,
            attempt=_REPAIR_ATTEMPT if schema_repair_used else _NATIVE_ATTEMPT,
            prompt_version=request.prompt_version,
            prompt_hash=prompt_hash,
            schema_repair_used=schema_repair_used,
            suspicious_content_detected=bool(request.prompt.flagged_injection_patterns),
            failover_from=accepted.failover_from,
        )

    async def _dispatch_tier(
        self, tier: Tier, *, system: str, user: str, schema: dict[str, object], schema_name: str
    ) -> _DispatchResult:
        entries = self._routing.entries_for(tier)
        attempts: list[ProviderError] = []
        failed_over_from: str | None = None

        for entry in entries:
            provider = self._providers.get(entry.provider)
            if provider is None:
                attempts.append(
                    ProviderError(entry.provider, "server_error", "no such provider configured")
                )
                continue

            for provider_attempt in range(self._routing.failover.max_provider_attempts):
                call_start = self._clock()
                try:
                    response = await provider.complete(
                        ProviderRequest(
                            model=entry.model,
                            system=system,
                            user=user,
                            json_schema=schema,
                            schema_name=schema_name,
                            max_tokens=entry.max_tokens,
                            timeout_s=entry.timeout_s,
                        )
                    )
                    return _DispatchResult(
                        provider=entry.provider,
                        model=entry.model,
                        response=response,
                        failover_from=failed_over_from,
                        latency_ms=int((self._clock() - call_start) * 1000),
                    )
                except ProviderError as exc:
                    attempts.append(exc)
                    if not should_fail_over(
                        exc.trigger, configured_triggers=self._routing.failover.trigger_on
                    ):
                        raise
                    is_last_attempt_for_provider = (
                        provider_attempt == self._routing.failover.max_provider_attempts - 1
                    )
                    if not is_last_attempt_for_provider:
                        backoff_ms = compute_backoff_ms(
                            provider_attempt, self._routing.failover.backoff
                        )
                        await self._sleep(backoff_ms / 1000)
                    else:
                        failed_over_from = entry.provider

        raise AllProvidersExhaustedError(tier, attempts)

    async def _record_call(
        self,
        request: CompletionRequest[Any],
        dispatched: _DispatchResult,
        *,
        prompt_hash: str,
        attempt: int,
        schema_repair_used: bool,
        raw_text: str,
    ) -> None:
        prompt_url = await self._storage.put(
            f"{prompt_hash}/attempt-{attempt}/prompt.json", raw_text
        )
        response_url = await self._storage.put(
            f"{prompt_hash}/attempt-{attempt}/response.json", raw_text
        )
        cost = compute_cost_micro_usd(
            provider=dispatched.provider,
            model=dispatched.model,
            tokens_in=dispatched.response.tokens_in,
            tokens_out=dispatched.response.tokens_out,
        )
        tier: Tier = "fast" if attempt == _REPAIR_ATTEMPT else request.tier
        await self._db.insert(
            LLMCallRecord(
                investigation_id=request.investigation_id,
                project_id=request.project_id,
                pipeline_step_id=request.pipeline_step_id,
                stage=request.stage,
                tier=tier,
                provider=dispatched.provider,
                model=dispatched.model,
                prompt_version=request.prompt_version,
                prompt_url=prompt_url,
                response_url=response_url,
                prompt_hash=prompt_hash,
                tokens_in=dispatched.response.tokens_in,
                tokens_out=dispatched.response.tokens_out,
                cached_tokens_in=dispatched.response.cached_tokens_in,
                cost_micro_usd=cost,
                latency_ms=dispatched.latency_ms,
                attempt=attempt,
                failover_from=dispatched.failover_from,
                schema_repair_used=schema_repair_used,
                suspicious_content_detected=False,
            )
        )

    async def _result_from_cache[M: BaseModel](
        self,
        request: CompletionRequest[M],
        cached: cache_module.CachedCompletion,
        prompt_hash: str,
        *,
        schema_name: str,
    ) -> LLMResult[M]:
        parsed = structured.parse_and_validate(cached.raw_text, request.output_model)
        if not parsed.ok or parsed.output is None:
            raise SchemaValidationFailedError(
                schema_name, parsed.error or "cached completion no longer validates"
            )
        return LLMResult(
            output=parsed.output,
            provider=cached.provider,
            model=cached.model,
            tier=request.tier,
            tokens_in=cached.tokens_in,
            tokens_out=cached.tokens_out,
            cached_tokens_in=0,
            # A cache hit costs nothing new — the tokens were already billed
            # and recorded the first time this exact input was seen.
            cost_micro_usd=0,
            latency_ms=0,
            attempt=0,
            prompt_version=request.prompt_version,
            prompt_hash=prompt_hash,
            schema_repair_used=False,
            suspicious_content_detected=False,
        )


def _hash_prompt(*, prompt_version: str, system: str, user: str) -> str:
    digest = hashlib.sha256()
    digest.update(prompt_version.encode("utf-8"))
    digest.update(b"\0")
    digest.update(system.encode("utf-8"))
    digest.update(b"\0")
    digest.update(user.encode("utf-8"))
    return digest.hexdigest()
