"""Pydantic models for runs, steps, memory, trust, and skill records."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"
    SHADOW    = "shadow"
    PAUSED    = "paused"


class StepStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    SKIPPED   = "skipped"
    SHADOW    = "shadow"


class BlastRadius(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class TrustLevel(str, Enum):
    UNTRUSTED   = "untrusted"
    PROVISIONAL = "provisional"
    TRUSTED     = "trusted"
    AUTONOMOUS  = "autonomous"


class EscalationStatus(str, Enum):
    PENDING   = "pending"
    RESPONDED = "responded"
    TIMED_OUT = "timed_out"


# ── Step ──────────────────────────────────────────────────────────────────────

class StepRecord(BaseModel):
    id:           str
    run_id:       str
    skill_name:   str
    step_id:      str
    status:       StepStatus = StepStatus.PENDING
    input:        Optional[Any] = None
    output:       Optional[Any] = None
    error:        Optional[str] = None
    duration_ms:  Optional[int] = None
    retry_count:  int = 0
    is_shadow:    bool = False
    started_at:   float = Field(default_factory=time.time)
    completed_at: Optional[float] = None


# ── Run ───────────────────────────────────────────────────────────────────────

class RunRecord(BaseModel):
    id:           str
    skill_name:   str
    trigger:      str  # "cron" | "event" | "invoke" | "manual" | "spawn"
    status:       RunStatus = RunStatus.PENDING
    input:        Optional[dict[str, Any]] = None
    output:       Optional[Any] = None
    error:        Optional[str] = None
    steps:        list[StepRecord] = Field(default_factory=list)
    is_shadow:    bool = False
    shadow_cycle: Optional[int] = None
    parent_run_id:Optional[str] = None
    iteration:    int = 1
    started_at:   float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    duration_ms:  Optional[int] = None


# ── Loop memory ───────────────────────────────────────────────────────────────

class MemoryEntry(BaseModel):
    key:        str
    value:      Any
    updated_at: float = Field(default_factory=time.time)


class LoopMemoryRecord(BaseModel):
    loop_name:  str
    entries:    dict[str, MemoryEntry] = Field(default_factory=dict)
    lists:      dict[str, list[Any]]  = Field(default_factory=dict)
    updated_at: float = Field(default_factory=time.time)


# ── Skill registration ────────────────────────────────────────────────────────

class SkillRegistration(BaseModel):
    name:                    str
    kind:                    str  # "skill" | "loop"
    version:                 str = "1"
    description:             Optional[str] = None
    can_read:                list[str] = Field(default_factory=list)
    can_write:               list[str] = Field(default_factory=list)
    blast_radius:            BlastRadius = BlastRadius.LOW
    shadow_cycles:           int = 0
    shadow_cycles_remaining: int = 0
    require_approval:        bool = False
    retries:                 int = 3
    timeout:                 Optional[int] = None
    cron:                    Optional[str] = None
    event_trigger:           Optional[str] = None
    is_active:               bool = True
    is_shadow:               bool = False
    trust_level:             TrustLevel = TrustLevel.PROVISIONAL
    registered_at:           float = Field(default_factory=time.time)
    approved_at:             Optional[float] = None
    approved_by:             Optional[str] = None


# ── Trust ─────────────────────────────────────────────────────────────────────

class TrustRecord(BaseModel):
    skill_name:         str
    total_runs:         int   = 0
    successful_runs:    int   = 0
    failed_runs:        int   = 0
    shadow_runs:        int   = 0
    human_approvals:    int   = 0
    human_rejections:   int   = 0
    false_positive_rate:float = 0.0
    avg_duration_ms:    float = 0.0
    trust_score:        float = 0.0
    trust_level:        TrustLevel = TrustLevel.UNTRUSTED
    last_evaluated_at:  Optional[float] = None
    last_updated_at:    float = Field(default_factory=time.time)


# ── Shadow output ─────────────────────────────────────────────────────────────

class ShadowOutput(BaseModel):
    run_id:      str
    skill_name:  str
    step_id:     str
    output:      Optional[Any] = None
    error:       Optional[str] = None
    reviewed:    bool = False
    approved:    Optional[bool] = None
    reviewer:    Optional[str] = None
    captured_at: float = Field(default_factory=time.time)


# ── Escalation ────────────────────────────────────────────────────────────────

class EscalationRecord(BaseModel):
    id:          str
    run_id:      str
    skill_name:  str
    message:     str
    timeout_s:   int
    fallback:    str
    status:      EscalationStatus = EscalationStatus.PENDING
    response:    Optional[str] = None
    created_at:  float = Field(default_factory=time.time)
    resolved_at: Optional[float] = None
