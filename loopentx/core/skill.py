"""The @skill decorator — durable, retryable, policy-scoped functions."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable, Coroutine, Optional

import structlog

from loopentx.core.context import LoopContext
from loopentx.core.models import RunRecord, RunStatus
from loopentx.core.exceptions import SkillError
from loopentx.core.config import get_config

log = structlog.get_logger()


class SkillDefinition:
    """Internal wrapper for a @skill-decorated function."""

    def __init__(
        self,
        fn:                Callable[..., Coroutine[Any, Any, Any]],
        retries:           int = 3,
        timeout:           Optional[int] = None,
        on_failure:        Optional[Callable] = None,
        concurrency_limit: Optional[int] = None,
        description:       Optional[str] = None,
    ) -> None:
        self.fn                = fn
        self.name              = fn.__name__
        self.retries           = retries
        self.timeout           = timeout
        self.on_failure        = on_failure
        self.concurrency_limit = concurrency_limit
        self.description       = description or fn.__doc__
        self.policy_context: Optional[Any] = None

        self._sem: Optional[asyncio.Semaphore] = (
            asyncio.Semaphore(concurrency_limit) if concurrency_limit else None
        )

    async def execute(self, ctx: LoopContext, **kwargs: Any) -> Any:
        config  = get_config()
        backend = config.backend

        run = RunRecord(
            id=ctx.run_id, skill_name=self.name,
            trigger="invoke", status=RunStatus.RUNNING,
            input=kwargs, is_shadow=ctx.is_shadow,
            started_at=time.time(),
        )
        await backend.save_run(run)

        last_exc: Optional[Exception] = None

        for attempt in range(self.retries + 1):
            try:
                if attempt > 0:
                    await asyncio.sleep(min(2 ** attempt, 60))
                    log.info("skill.retry", skill=self.name, attempt=attempt)

                if self._sem:
                    async with self._sem:
                        result = await self._run(ctx, **kwargs)
                else:
                    result = await self._run(ctx, **kwargs)

                run.status       = RunStatus.COMPLETED
                run.output       = result
                run.completed_at = time.time()
                run.duration_ms  = int((run.completed_at - run.started_at) * 1000)
                await backend.save_run(run)
                await backend.record_trust_outcome(self.name, success=True)

                log.info("skill.completed", skill=self.name, run_id=ctx.run_id,
                         duration_ms=run.duration_ms)
                return result

            except Exception as exc:
                last_exc = exc
                log.warning("skill.attempt_failed", skill=self.name,
                            attempt=attempt, error=str(exc))

        run.status       = RunStatus.FAILED
        run.error        = str(last_exc)
        run.completed_at = time.time()
        run.duration_ms  = int((run.completed_at - run.started_at) * 1000)
        await backend.save_run(run)
        await backend.record_trust_outcome(self.name, success=False)

        if self.on_failure:
            try:
                await self.on_failure(error=last_exc, run=run, ctx=ctx)
            except Exception as fe:
                log.error("skill.on_failure_error", error=str(fe))

        raise SkillError(skill_name=self.name, cause=last_exc) from last_exc

    async def _run(self, ctx: LoopContext, **kwargs: Any) -> Any:
        if self.timeout:
            return await asyncio.wait_for(self.fn(ctx, **kwargs), timeout=self.timeout)
        return await self.fn(ctx, **kwargs)


def skill(
    retries:           int = 3,
    timeout:           Optional[int] = None,
    on_failure:        Optional[Callable] = None,
    concurrency_limit: Optional[int] = None,
    description:       Optional[str] = None,
) -> Callable:
    """Decorator to define a durable, retryable Loopentx skill.

    Each ctx.step() call inside a skill is checkpointed. If the process
    restarts mid-execution, completed steps are not re-run.

    Args:
        retries:           Retry attempts on failure. Default 3.
        timeout:           Max execution time in seconds.
        on_failure:        Async callback when all retries exhausted.
                           Receives (error, run, ctx) keyword arguments.
        concurrency_limit: Max concurrent executions of this skill.
        description:       Human-readable description.

    Example:
        @skill(retries=3, timeout=60)
        async def analyse_report(ctx, report_id: str):
            data    = await ctx.step("fetch",     fetch_report, report_id)
            summary = await ctx.step("summarise", call_llm, data)
            await ctx.step("store", save_summary, summary)
            return summary
    """
    def decorator(fn: Callable) -> Callable:
        skill_def = SkillDefinition(
            fn=fn, retries=retries, timeout=timeout,
            on_failure=on_failure, concurrency_limit=concurrency_limit,
            description=description,
        )

        @functools.wraps(fn)
        async def wrapper(**kwargs: Any) -> Any:
            from ulid import ULID
            cfg     = get_config()
            run_id  = str(ULID())
            ctx     = LoopContext(
                run_id=run_id, skill_name=fn.__name__,
                backend=cfg.backend,
                policy_context=skill_def.policy_context,
            )
            return await skill_def.execute(ctx, **kwargs)

        wrapper._loopentx_skill = skill_def   # type: ignore[attr-defined]
        wrapper._loopentx_kind  = "skill"     # type: ignore[attr-defined]
        return wrapper

    return decorator
