"""Redis backend for production use."""

from __future__ import annotations

import time
from typing import Any, Optional

import structlog

from loopentx.backends.base import BaseBackend
from loopentx.core.models import (
    RunRecord, StepRecord, SkillRegistration, TrustRecord,
    ShadowOutput, LoopMemoryRecord, EscalationRecord, StepStatus,
)
from loopentx.core.events import LoopentxEvent

log = structlog.get_logger()

TTL_RUN   = 30 * 86400   # 30 days
TTL_STEP  = 30 * 86400
TTL_EVENT = 86400


class RedisBackend(BaseBackend):
    """Redis-backed storage for Loopentx.

    Requires: pip install loopentx[redis]

    Example:
        from loopentx import configure
        from loopentx.backends import RedisBackend
        configure(backend=RedisBackend("redis://localhost:6379"))
    """

    def __init__(self, url: str = "redis://localhost:6379", prefix: str = "ltx") -> None:
        self.url    = url
        self.prefix = prefix
        self._r: Any = None

    def _k(self, *parts: str) -> str:
        return ":".join([self.prefix] + list(parts))

    async def connect(self) -> None:
        try:
            import redis.asyncio as aioredis
        except ImportError:
            raise ImportError("pip install loopentx[redis]")
        self._r = aioredis.from_url(self.url, decode_responses=True)
        await self._r.ping()
        log.info("redis_backend.connected", url=self.url)

    async def disconnect(self) -> None:
        if self._r:
            await self._r.aclose()

    # ── Runs ──────────────────────────────────────────────────────────────
    async def save_run(self, run: RunRecord) -> None:
        await self._r.setex(self._k("run", run.id), TTL_RUN, run.model_dump_json())
        await self._r.zadd(self._k("runs", "all"),  {run.id: run.started_at})
        await self._r.zadd(self._k("runs", "by", run.skill_name), {run.id: run.started_at})

    async def get_run(self, run_id: str) -> Optional[RunRecord]:
        d = await self._r.get(self._k("run", run_id))
        return RunRecord.model_validate_json(d) if d else None

    async def get_runs(
        self,
        skill_name: Optional[str] = None,
        since:      Optional[float] = None,
        limit:      int = 100,
    ) -> list[RunRecord]:
        idx = self._k("runs", "by", skill_name) if skill_name else self._k("runs", "all")
        ids = await self._r.zrangebyscore(idx, since or "-inf", "+inf", start=0, num=limit)
        runs = []
        for rid in ids:
            r = await self.get_run(rid)
            if r:
                runs.append(r)
        return sorted(runs, key=lambda r: r.started_at, reverse=True)

    # ── Steps ─────────────────────────────────────────────────────────────
    async def save_step(self, step: StepRecord) -> None:
        await self._r.setex(self._k("step", step.id), TTL_STEP, step.model_dump_json())

    async def get_step_result(self, step_id: str) -> Optional[Any]:
        d = await self._r.get(self._k("step", step_id))
        if not d:
            return None
        step = StepRecord.model_validate_json(d)
        return step.output if step.status == StepStatus.COMPLETED else None

    # ── Skill registration ────────────────────────────────────────────────
    async def save_skill_registration(self, reg: SkillRegistration) -> None:
        await self._r.set(self._k("skill", reg.name), reg.model_dump_json())
        await self._r.sadd(self._k("skills"), reg.name)

    async def get_skill_registration(self, name: str) -> Optional[SkillRegistration]:
        d = await self._r.get(self._k("skill", name))
        return SkillRegistration.model_validate_json(d) if d else None

    async def list_skill_registrations(self) -> list[SkillRegistration]:
        names = await self._r.smembers(self._k("skills"))
        result = []
        for n in names:
            r = await self.get_skill_registration(n)
            if r:
                result.append(r)
        return result

    async def set_skill_active(self, name: str, active: bool) -> None:
        reg = await self.get_skill_registration(name)
        if reg:
            reg.is_active = active
            await self.save_skill_registration(reg)

    # ── Trust ─────────────────────────────────────────────────────────────
    async def save_trust_record(self, trust: TrustRecord) -> None:
        await self._r.set(self._k("trust", trust.skill_name), trust.model_dump_json())

    async def get_trust_record(self, name: str) -> Optional[TrustRecord]:
        d = await self._r.get(self._k("trust", name))
        return TrustRecord.model_validate_json(d) if d else None

    async def record_trust_outcome(self, name: str, success: bool) -> None:
        trust = await self.get_trust_record(name) or TrustRecord(skill_name=name)
        trust.successful_runs += int(success)
        trust.failed_runs     += int(not success)
        trust.total_runs      += 1
        trust.last_updated_at  = time.time()
        await self.save_trust_record(trust)

    # ── Shadow outputs ────────────────────────────────────────────────────
    async def save_shadow_output(
        self, run_id: str, step_id: str,
        output: Optional[Any] = None, error: Optional[str] = None,
    ) -> None:
        run        = await self.get_run(run_id)
        skill_name = run.skill_name if run else "unknown"
        so         = ShadowOutput(run_id=run_id, skill_name=skill_name,
                                  step_id=step_id, output=output, error=error)
        key = self._k("shadow", run_id, step_id)
        await self._r.setex(key, TTL_RUN, so.model_dump_json())
        await self._r.sadd(self._k("shadows", skill_name), f"{run_id}:{step_id}")

    async def get_shadow_outputs(self, skill_name: str) -> list[ShadowOutput]:
        members = await self._r.smembers(self._k("shadows", skill_name))
        result  = []
        for m in members:
            run_id, step_id = m.rsplit(":", 1)
            d = await self._r.get(self._k("shadow", run_id, step_id))
            if d:
                result.append(ShadowOutput.model_validate_json(d))
        return result

    # ── Loop memory ───────────────────────────────────────────────────────
    async def save_loop_memory(self, record: LoopMemoryRecord) -> None:
        await self._r.set(self._k("memory", record.loop_name), record.model_dump_json())

    async def get_loop_memory(self, loop_name: str) -> Optional[LoopMemoryRecord]:
        d = await self._r.get(self._k("memory", loop_name))
        return LoopMemoryRecord.model_validate_json(d) if d else None

    # ── Escalations ───────────────────────────────────────────────────────
    async def save_escalation(self, esc: EscalationRecord) -> None:
        await self._r.setex(self._k("esc", esc.id), TTL_RUN, esc.model_dump_json())
        await self._r.sadd(self._k("escalations"), esc.id)

    async def get_escalation(self, esc_id: str) -> Optional[EscalationRecord]:
        d = await self._r.get(self._k("esc", esc_id))
        return EscalationRecord.model_validate_json(d) if d else None

    async def list_pending_escalations(self) -> list[EscalationRecord]:
        from loopentx.core.models import EscalationStatus
        ids    = await self._r.smembers(self._k("escalations"))
        result = []
        for eid in ids:
            e = await self.get_escalation(eid)
            if e and e.status == EscalationStatus.PENDING:
                result.append(e)
        return result

    # ── Events ────────────────────────────────────────────────────────────
    async def publish_event(self, evt: LoopentxEvent) -> None:
        k = self._k("events")
        await self._r.rpush(k, evt.model_dump_json())
        await self._r.expire(k, TTL_EVENT)

    async def poll_events(self) -> list[LoopentxEvent]:
        k = self._k("events")
        events = []
        for _ in range(50):
            d = await self._r.lpop(k)
            if not d:
                break
            events.append(LoopentxEvent.model_validate_json(d))
        return events
