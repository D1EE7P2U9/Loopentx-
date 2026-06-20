"""In-memory backend — for testing and local development."""

from __future__ import annotations

import time
from typing import Any, Optional

from loopentx.backends.base import BaseBackend
from loopentx.core.models import (
    RunRecord, StepRecord, SkillRegistration, TrustRecord,
    ShadowOutput, LoopMemoryRecord, EscalationRecord, StepStatus,
)
from loopentx.core.events import LoopentxEvent


class MemoryBackend(BaseBackend):
    """In-memory backend. Data is NOT persisted between restarts.

    Use RedisBackend for production.

    Example:
        from loopentx import configure
        from loopentx.backends import MemoryBackend
        configure(backend=MemoryBackend())
    """

    def __init__(self) -> None:
        self._runs:        dict[str, RunRecord]          = {}
        self._steps:       dict[str, StepRecord]         = {}
        self._skills:      dict[str, SkillRegistration]  = {}
        self._trust:       dict[str, TrustRecord]        = {}
        self._shadows:     dict[str, ShadowOutput]       = {}
        self._memory:      dict[str, LoopMemoryRecord]   = {}
        self._escalations: dict[str, EscalationRecord]   = {}
        self._events:      list[LoopentxEvent]           = []

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass

    # ── Runs ──────────────────────────────────────────────────────────────
    async def save_run(self, run: RunRecord) -> None:
        self._runs[run.id] = run

    async def get_run(self, run_id: str) -> Optional[RunRecord]:
        return self._runs.get(run_id)

    async def get_runs(
        self,
        skill_name: Optional[str] = None,
        since:      Optional[float] = None,
        limit:      int = 100,
    ) -> list[RunRecord]:
        runs = list(self._runs.values())
        if skill_name:
            runs = [r for r in runs if r.skill_name == skill_name]
        if since:
            runs = [r for r in runs if r.started_at >= since]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    # ── Steps ─────────────────────────────────────────────────────────────
    async def save_step(self, step: StepRecord) -> None:
        self._steps[step.id] = step

    async def get_step_result(self, step_id: str) -> Optional[Any]:
        step = self._steps.get(step_id)
        return step.output if step and step.status == StepStatus.COMPLETED else None

    # ── Skill registration ────────────────────────────────────────────────
    async def save_skill_registration(self, reg: SkillRegistration) -> None:
        self._skills[reg.name] = reg

    async def get_skill_registration(self, name: str) -> Optional[SkillRegistration]:
        return self._skills.get(name)

    async def list_skill_registrations(self) -> list[SkillRegistration]:
        return list(self._skills.values())

    async def set_skill_active(self, name: str, active: bool) -> None:
        if name in self._skills:
            self._skills[name].is_active = active

    # ── Trust ─────────────────────────────────────────────────────────────
    async def save_trust_record(self, trust: TrustRecord) -> None:
        self._trust[trust.skill_name] = trust

    async def get_trust_record(self, name: str) -> Optional[TrustRecord]:
        return self._trust.get(name)

    async def record_trust_outcome(self, name: str, success: bool) -> None:
        trust = self._trust.get(name) or TrustRecord(skill_name=name)
        trust.successful_runs += int(success)
        trust.failed_runs     += int(not success)
        trust.total_runs      += 1
        trust.last_updated_at  = time.time()
        self._trust[name] = trust

    # ── Shadow outputs ────────────────────────────────────────────────────
    async def save_shadow_output(
        self, run_id: str, step_id: str,
        output: Optional[Any] = None, error: Optional[str] = None,
    ) -> None:
        run        = self._runs.get(run_id)
        skill_name = run.skill_name if run else "unknown"
        key        = f"{run_id}:{step_id}"
        self._shadows[key] = ShadowOutput(
            run_id=run_id, skill_name=skill_name,
            step_id=step_id, output=output, error=error,
        )

    async def get_shadow_outputs(self, skill_name: str) -> list[ShadowOutput]:
        return [o for o in self._shadows.values() if o.skill_name == skill_name]

    # ── Loop memory ───────────────────────────────────────────────────────
    async def save_loop_memory(self, record: LoopMemoryRecord) -> None:
        self._memory[record.loop_name] = record

    async def get_loop_memory(self, loop_name: str) -> Optional[LoopMemoryRecord]:
        return self._memory.get(loop_name)

    # ── Escalations ───────────────────────────────────────────────────────
    async def save_escalation(self, esc: EscalationRecord) -> None:
        self._escalations[esc.id] = esc

    async def get_escalation(self, esc_id: str) -> Optional[EscalationRecord]:
        return self._escalations.get(esc_id)

    async def list_pending_escalations(self) -> list[EscalationRecord]:
        from loopentx.core.models import EscalationStatus
        return [e for e in self._escalations.values()
                if e.status == EscalationStatus.PENDING]

    # ── Events ────────────────────────────────────────────────────────────
    async def publish_event(self, evt: LoopentxEvent) -> None:
        self._events.append(evt)

    async def poll_events(self) -> list[LoopentxEvent]:
        events, self._events = self._events[:], []
        return events

    # ── Test helpers ──────────────────────────────────────────────────────
    def reset(self) -> None:
        """Clear all stored data. Use between tests."""
        self._runs.clear(); self._steps.clear(); self._skills.clear()
        self._trust.clear(); self._shadows.clear(); self._memory.clear()
        self._escalations.clear(); self._events.clear()
