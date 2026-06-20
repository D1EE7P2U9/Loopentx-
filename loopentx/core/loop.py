"""The @loop decorator — the core Loopentx primitive."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable, Coroutine, Optional

import structlog
from croniter import croniter

from loopentx.core.context import LoopContext, _parse_duration
from loopentx.core.models import RunRecord, RunStatus
from loopentx.core.exceptions import LoopExitConditionMet
from loopentx.core.config import get_config

log = structlog.get_logger()


class LoopDefinition:
    """Internal wrapper for a @loop-decorated function."""

    def __init__(
        self,
        fn:             Callable[..., Coroutine[Any, Any, Any]],
        cron:           Optional[str]      = None,
        every:          Optional[str]      = None,
        event:          Optional[str]      = None,
        until:          Optional[Callable] = None,
        max_iterations: Optional[int]      = None,
        memory:         bool               = False,
        max_concurrency:int                = 1,
        description:    Optional[str]      = None,
    ) -> None:
        self.fn             = fn
        self.name           = fn.__name__
        self.cron           = cron
        self.every          = every
        self.event          = event
        self.until          = until
        self.max_iterations = max_iterations
        self.memory         = memory
        self.max_concurrency= max_concurrency
        self.description    = description or fn.__doc__

        if cron and not croniter.is_valid(cron):
            raise ValueError(f"Invalid cron expression: {cron!r}")

        self._every_s: Optional[int] = _parse_duration(every) if every else None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._iteration = 0

    def next_cron_time(self) -> Optional[float]:
        if not self.cron:
            return None
        return croniter(self.cron).get_next(float)

    async def execute(
        self,
        trigger:    str = "manual",
        event_data: Optional[dict] = None,
    ) -> Any:
        """Execute one iteration of the loop."""
        cfg     = get_config()
        backend = cfg.backend
        from ulid import ULID

        self._iteration += 1
        run_id = str(ULID())

        ctx = LoopContext(
            run_id=run_id,
            skill_name=self.name,
            backend=backend,
            iteration=self._iteration,
            enable_memory=self.memory,
        )

        # Check exit condition before running
        if self.until and await self._check_exit(ctx):
            log.info("loop.exit_condition_met", loop=self.name, iteration=self._iteration)
            return None

        if self.max_iterations and self._iteration > self.max_iterations:
            log.info("loop.max_iterations_reached", loop=self.name)
            return None

        run = RunRecord(
            id=run_id, skill_name=self.name, trigger=trigger,
            status=RunStatus.RUNNING, input=event_data,
            iteration=self._iteration, started_at=time.time(),
        )
        await backend.save_run(run)

        try:
            result = await self.fn(ctx, **(event_data or {}))

            run.status       = RunStatus.COMPLETED
            run.output       = result
            run.completed_at = time.time()
            run.duration_ms  = int((run.completed_at - run.started_at) * 1000)
            await backend.save_run(run)

            log.info("loop.completed", loop=self.name, run_id=run_id,
                     iteration=self._iteration, duration_ms=run.duration_ms)
            return result

        except LoopExitConditionMet:
            run.status = RunStatus.COMPLETED
            run.completed_at = time.time()
            await backend.save_run(run)
            return None

        except Exception as exc:
            run.status       = RunStatus.FAILED
            run.error        = str(exc)
            run.completed_at = time.time()
            run.duration_ms  = int((run.completed_at - run.started_at) * 1000)
            await backend.save_run(run)
            log.error("loop.failed", loop=self.name, run_id=run_id, error=str(exc))
            raise

    async def _check_exit(self, ctx: LoopContext) -> bool:
        if not self.until:
            return False
        try:
            result = self.until(ctx)
            if asyncio.iscoroutine(result):
                return bool(await result)
            return bool(result)
        except Exception as exc:
            log.warning("loop.exit_condition_error", loop=self.name, error=str(exc))
            return False

    async def start_cron(self) -> None:
        """Run the cron scheduler for this loop indefinitely."""
        self._running = True
        log.info("loop.cron_started", loop=self.name, cron=self.cron)
        while self._running:
            now      = time.time()
            next_run = self.next_cron_time()
            if next_run is None:
                break
            wait = max(0.0, next_run - now)
            await asyncio.sleep(wait)
            if not self._running:
                break
            try:
                await self.execute(trigger="cron")
            except Exception:
                pass  # logged inside execute()

    async def start_interval(self) -> None:
        """Run on a fixed interval (every=) indefinitely."""
        assert self._every_s is not None
        self._running = True
        log.info("loop.interval_started", loop=self.name, every_s=self._every_s)
        while self._running:
            try:
                await self.execute(trigger="interval")
            except Exception:
                pass
            await asyncio.sleep(self._every_s)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()


def loop(
    cron:           Optional[str]      = None,
    every:          Optional[str]      = None,
    event:          Optional[str]      = None,
    until:          Optional[Callable] = None,
    max_iterations: Optional[int]      = None,
    memory:         bool               = False,
    max_concurrency:int                = 1,
    description:    Optional[str]      = None,
) -> Callable:
    """Decorator to define a Loopentx loop.

    A loop is the core primitive: it runs on a schedule or event,
    uses ctx.think() to decide what to do next, and invokes skills.
    You write it once — Loopentx runs it forever.

    At least one of cron=, every=, or event= is required.

    Args:
        cron:           Cron expression (e.g. "*/30 * * * *").
        every:          Interval string (e.g. "30m", "2h", "1d").
        event:          Event name that triggers this loop.
        until:          Callable(ctx) → bool. Loop stops when True.
        max_iterations: Hard ceiling on iteration count.
        memory:         If True, enable loop-native persistent memory.
        max_concurrency:Max concurrent executions. Default 1.
        description:    Human-readable description.

    Examples:
        # Heartbeat — Boris's pattern
        @loop(every="1h", memory=True)
        async def monitor(ctx):
            state    = await ctx.step("check", check_state)
            decision = await ctx.think("Action needed?", context=state,
                                       choose_from=["act", "skip"])
            if decision == "act":
                await ctx.invoke(triage, data=state)

        # Research — Andrej's pattern
        @loop(until=lambda ctx: False, max_iterations=50, memory=True)
        async def research(ctx, topic: str):
            ...

        # Supervisor — Steipete's pattern
        @loop(cron="0 9 * * 1")
        async def weekly_supervisor(ctx):
            tasks   = await ctx.step("plan", decompose, goal)
            results = await ctx.gather([ctx.spawn(worker, task=t) for t in tasks])
    """
    if cron is None and every is None and event is None:
        raise ValueError("@loop requires at least one of: cron=, every=, event=")

    def decorator(fn: Callable) -> Callable:
        loop_def = LoopDefinition(
            fn=fn, cron=cron, every=every, event=event,
            until=until, max_iterations=max_iterations, memory=memory,
            max_concurrency=max_concurrency, description=description,
        )

        @functools.wraps(fn)
        async def wrapper(**kwargs: Any) -> Any:
            return await loop_def.execute(trigger="manual", event_data=kwargs)

        wrapper._loopentx_loop = loop_def  # type: ignore[attr-defined]
        wrapper._loopentx_kind = "loop"    # type: ignore[attr-defined]
        return wrapper

    return decorator
