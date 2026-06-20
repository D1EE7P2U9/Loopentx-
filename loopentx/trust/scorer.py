"""Trust scorer — evaluates skill reliability and promotes trust levels."""

from __future__ import annotations

import time
from typing import Optional

import structlog

from loopentx.core.models import TrustRecord, TrustLevel, RunStatus
from loopentx.core.config import get_config

log = structlog.get_logger()

THRESHOLDS = {
    TrustLevel.AUTONOMOUS:   0.90,
    TrustLevel.TRUSTED:      0.65,
    TrustLevel.PROVISIONAL:  0.30,
    TrustLevel.UNTRUSTED:    0.0,
}

WEIGHTS = {
    "success_rate":        0.45,
    "human_approval_rate": 0.30,
    "volume_bonus":        0.10,
    "recency_factor":      0.15,
}


class TrustScorer:
    """Calculates and updates trust scores for loops and skills.

    Score components:
      success_rate (45%)       — ratio of completed to total live runs
      human_approval_rate (30%)— ratio of approvals to human reviews
      volume_bonus (10%)       — logarithmic reward for proven track record
      recency_factor (15%)     — penalises dormant skills

    Score → Level:
      0.00–0.29  UNTRUSTED    shadow + human review required
      0.30–0.64  PROVISIONAL  runs live, changes monitored
      0.65–0.89  TRUSTED      autonomous, changes need review
      0.90–1.00  AUTONOMOUS   fully autonomous, changes auto-approved
    """

    async def evaluate(self, skill_name: str) -> TrustRecord:
        cfg   = get_config()
        since = time.time() - (30 * 86400)
        runs  = await cfg.backend.get_runs(skill_name=skill_name, since=since)

        existing = await cfg.backend.get_trust_record(skill_name)
        trust    = existing or TrustRecord(skill_name=skill_name)

        if not runs:
            trust.last_evaluated_at = time.time()
            return trust

        live   = [r for r in runs if not r.is_shadow]
        total  = len(live)
        ok     = sum(1 for r in live if r.status == RunStatus.COMPLETED)
        failed = sum(1 for r in live if r.status == RunStatus.FAILED)
        shadow = sum(1 for r in runs if r.is_shadow)

        trust.total_runs     = total
        trust.successful_runs= ok
        trust.failed_runs    = failed
        trust.shadow_runs    = shadow

        durations = [r.duration_ms for r in runs if r.duration_ms]
        trust.avg_duration_ms = sum(durations) / len(durations) if durations else 0.0

        success_rate = ok / total if total > 0 else 0.0

        approvals   = trust.human_approvals
        rejections  = trust.human_rejections
        reviews     = approvals + rejections
        approval_rate = approvals / reviews if reviews > 0 else 0.5

        volume_bonus = min(total / 50.0, 1.0)

        most_recent   = max((r.completed_at or 0.0) for r in runs)
        days_inactive = (time.time() - most_recent) / 86400 if most_recent else 30
        recency       = max(0.0, 1.0 - days_inactive / 14.0)

        score = (
            success_rate   * WEIGHTS["success_rate"]
            + approval_rate * WEIGHTS["human_approval_rate"]
            + volume_bonus  * WEIGHTS["volume_bonus"]
            + recency       * WEIGHTS["recency_factor"]
        )

        trust.trust_score        = round(min(score, 1.0), 4)
        trust.trust_level        = self._to_level(trust.trust_score)
        trust.last_evaluated_at  = time.time()
        trust.last_updated_at    = time.time()

        log.info("trust.evaluated", skill=skill_name, score=trust.trust_score,
                 level=trust.trust_level, success_rate=round(success_rate, 3))
        return trust

    def _to_level(self, score: float) -> TrustLevel:
        for level, threshold in THRESHOLDS.items():
            if score >= threshold:
                return level
        return TrustLevel.UNTRUSTED

    def explain(self, trust: TrustRecord) -> str:
        return "\n".join([
            f"Trust: '{trust.skill_name}' → {trust.trust_score:.2f} ({trust.trust_level.value})",
            f"  Runs (30d):       {trust.total_runs}",
            f"  Successful:       {trust.successful_runs}",
            f"  Failed:           {trust.failed_runs}",
            f"  Shadow runs:      {trust.shadow_runs}",
            f"  Human approvals:  {trust.human_approvals}",
            f"  Human rejections: {trust.human_rejections}",
            f"  Avg duration:     {trust.avg_duration_ms:.0f}ms",
        ])


class TrustScore:
    """Convenience helpers for reading and updating trust records."""

    @staticmethod
    async def get(skill_name: str) -> Optional[TrustRecord]:
        return await get_config().backend.get_trust_record(skill_name)

    @staticmethod
    async def approve(skill_name: str, approved_by: str = "human") -> None:
        backend = get_config().backend
        trust   = await backend.get_trust_record(skill_name) or TrustRecord(skill_name=skill_name)
        trust.human_approvals  += 1
        trust.last_updated_at   = time.time()
        await backend.save_trust_record(trust)

    @staticmethod
    async def reject(skill_name: str, rejected_by: str = "human") -> None:
        backend = get_config().backend
        trust   = await backend.get_trust_record(skill_name) or TrustRecord(skill_name=skill_name)
        trust.human_rejections += 1
        trust.last_updated_at   = time.time()
        await backend.save_trust_record(trust)
