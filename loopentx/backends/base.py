"""Abstract base class for Loopentx storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from loopentx.core.models import (
    RunRecord, StepRecord, SkillRegistration,
    TrustRecord, ShadowOutput, LoopMemoryRecord, EscalationRecord,
)
from loopentx.core.events import LoopentxEvent


class BaseBackend(ABC):
    """Abstract storage interface. Implement to add a new backend.

    Built-in: MemoryBackend (tests), RedisBackend (production).
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    # ── Runs ──────────────────────────────────────────────────────────────
    @abstractmethod
    async def save_run(self, run: RunRecord) -> None: ...

    @abstractmethod
    async def get_run(self, run_id: str) -> Optional[RunRecord]: ...

    @abstractmethod
    async def get_runs(
        self,
        skill_name: Optional[str] = None,
        since:      Optional[float] = None,
        limit:      int = 100,
    ) -> list[RunRecord]: ...

    # ── Steps ─────────────────────────────────────────────────────────────
    @abstractmethod
    async def save_step(self, step: StepRecord) -> None: ...

    @abstractmethod
    async def get_step_result(self, step_id: str) -> Optional[Any]: ...

    # ── Skill registration ────────────────────────────────────────────────
    @abstractmethod
    async def save_skill_registration(self, reg: SkillRegistration) -> None: ...

    @abstractmethod
    async def get_skill_registration(self, skill_name: str) -> Optional[SkillRegistration]: ...

    @abstractmethod
    async def list_skill_registrations(self) -> list[SkillRegistration]: ...

    @abstractmethod
    async def set_skill_active(self, skill_name: str, active: bool) -> None: ...

    async def approve_skill(self, skill_name: str, approved_by: str = "system") -> None:
        import time
        reg = await self.get_skill_registration(skill_name)
        if reg:
            reg.is_active               = True
            reg.is_shadow               = False
            reg.shadow_cycles_remaining = 0
            reg.approved_at             = time.time()
            reg.approved_by             = approved_by
            await self.save_skill_registration(reg)

    # ── Trust ─────────────────────────────────────────────────────────────
    @abstractmethod
    async def save_trust_record(self, trust: TrustRecord) -> None: ...

    @abstractmethod
    async def get_trust_record(self, skill_name: str) -> Optional[TrustRecord]: ...

    @abstractmethod
    async def record_trust_outcome(self, skill_name: str, success: bool) -> None: ...

    # ── Shadow outputs ────────────────────────────────────────────────────
    @abstractmethod
    async def save_shadow_output(
        self, run_id: str, step_id: str,
        output: Optional[Any] = None, error: Optional[str] = None,
    ) -> None: ...

    @abstractmethod
    async def get_shadow_outputs(self, skill_name: str) -> list[ShadowOutput]: ...

    # ── Loop memory ───────────────────────────────────────────────────────
    @abstractmethod
    async def save_loop_memory(self, record: LoopMemoryRecord) -> None: ...

    @abstractmethod
    async def get_loop_memory(self, loop_name: str) -> Optional[LoopMemoryRecord]: ...

    # ── Escalations ───────────────────────────────────────────────────────
    @abstractmethod
    async def save_escalation(self, esc: EscalationRecord) -> None: ...

    @abstractmethod
    async def get_escalation(self, esc_id: str) -> Optional[EscalationRecord]: ...

    @abstractmethod
    async def list_pending_escalations(self) -> list[EscalationRecord]: ...

    # ── Events ────────────────────────────────────────────────────────────
    @abstractmethod
    async def publish_event(self, evt: LoopentxEvent) -> None: ...

    @abstractmethod
    async def poll_events(self) -> list[LoopentxEvent]: ...
