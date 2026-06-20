"""Orchestrator — runs loops, routes events, evaluates trust."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import structlog

from loopentx.core.config import get_config
from loopentx.core.events import LoopentxEvent

log = structlog.get_logger()


class Orchestrator:
    """The Loopentx execution engine.

    Manages loop and skill registration, cron/interval scheduling,
    event routing, concurrency, and the background trust evaluator.

    Example:
        from loopentx import configure, Orchestrator
        from loopentx.backends import RedisBackend
        from myapp.loops import health_check, research_loop

        configure(backend=RedisBackend())
        orch = Orchestrator()
        orch.register(health_check)
        orch.register(research_loop)
        asyncio.run(orch.start())
    """

    def __init__(self) -> None:
        self._skills:          dict[str, Any] = {}
        self._loops:           dict[str, Any] = {}
        self._cron_tasks:      dict[str, asyncio.Task] = {}
        self._event_listeners: dict[str, list[Any]] = {}
        self._running = False

    def register(self, fn: Any) -> None:
        """Register a @loop or @skill with the orchestrator."""
        kind = getattr(fn, "_loopentx_kind", None)

        if kind == "skill":
            skill_def = fn._loopentx_skill
            self._skills[skill_def.name] = fn
            log.info("orchestrator.skill_registered", name=skill_def.name)

        elif kind == "loop":
            loop_def = fn._loopentx_loop
            self._loops[loop_def.name] = fn
            if loop_def.event:
                self._event_listeners.setdefault(loop_def.event, []).append(fn)
            log.info("orchestrator.loop_registered", name=loop_def.name,
                     cron=loop_def.cron, every=loop_def.every, event=loop_def.event)
        else:
            raise ValueError(
                f"{fn.__name__} is not a Loopentx loop or skill. "
                "Decorate with @loop or @skill."
            )

    async def start(self) -> None:
        """Start the orchestrator. Runs until stop() is called."""
        self._running = True
        log.info("orchestrator.started",
                 skills=list(self._skills), loops=list(self._loops))

        tasks = []

        for name, loop_fn in self._loops.items():
            ld = loop_fn._loopentx_loop
            if ld.cron:
                task = asyncio.create_task(ld.start_cron(), name=f"cron:{name}")
                self._cron_tasks[name] = task
                tasks.append(task)
            elif ld.every:
                task = asyncio.create_task(ld.start_interval(), name=f"interval:{name}")
                self._cron_tasks[name] = task
                tasks.append(task)

        tasks.append(asyncio.create_task(self._event_loop(), name="event_loop"))
        tasks.append(asyncio.create_task(self._trust_loop(),  name="trust_eval"))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("orchestrator.stopped")

    async def stop(self) -> None:
        """Stop all loops and the orchestrator."""
        self._running = False
        for loop_fn in self._loops.values():
            await loop_fn._loopentx_loop.stop()
        for task in self._cron_tasks.values():
            if not task.done():
                task.cancel()

    async def trigger_event(self, evt: LoopentxEvent) -> None:
        """Publish an event to trigger matching loops."""
        cfg = get_config()
        await cfg.backend.publish_event(evt)

    async def respond_to_escalation(
        self,
        escalation_id: str,
        response:      str,
    ) -> None:
        """Provide a human response to a pending escalation."""
        from loopentx.core.models import EscalationStatus
        cfg = get_config()
        esc = await cfg.backend.get_escalation(escalation_id)
        if esc:
            esc.status      = EscalationStatus.RESPONDED
            esc.response    = response
            esc.resolved_at = time.time()
            await cfg.backend.save_escalation(esc)
            log.info("orchestrator.escalation_resolved", id=escalation_id)

    async def get_runs(
        self,
        skill_name: Optional[str] = None,
        since:      Optional[float] = None,
        limit:      int = 100,
    ) -> list:
        cfg = get_config()
        return await cfg.backend.get_runs(skill_name=skill_name, since=since, limit=limit)

    async def approve_skill(self, skill_name: str, approved_by: str = "human") -> None:
        cfg = get_config()
        await cfg.backend.approve_skill(skill_name, approved_by=approved_by)
        log.info("orchestrator.skill_approved", skill=skill_name, by=approved_by)

    async def _event_loop(self) -> None:
        cfg = get_config()
        while self._running:
            try:
                events = await cfg.backend.poll_events()
                for evt in events:
                    for loop_fn in self._event_listeners.get(evt.name, []):
                        asyncio.create_task(
                            loop_fn._loopentx_loop.execute(
                                trigger="event", event_data=evt.data
                            )
                        )
            except Exception as exc:
                log.error("orchestrator.event_loop_error", error=str(exc))
            await asyncio.sleep(get_config().worker_poll_interval)

    async def _trust_loop(self) -> None:
        from loopentx.trust.scorer import TrustScorer
        scorer = TrustScorer()
        while self._running:
            await asyncio.sleep(3600)  # every hour
            cfg = get_config()
            for name in list(self._skills) + list(self._loops):
                try:
                    trust = await scorer.evaluate(name)
                    await cfg.backend.save_trust_record(trust)
                    log.info("orchestrator.trust_evaluated",
                             skill=name, score=trust.trust_score,
                             level=trust.trust_level)
                except Exception as exc:
                    log.error("orchestrator.trust_eval_error",
                              skill=name, error=str(exc))
