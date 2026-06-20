"""
Heartbeat Loop — Boris's Pattern
"I don't prompt Claude anymore. I write loops, the loops do the work."

Runs every 30 minutes. Checks service health. Acts if something looks wrong.
You write it once. You step back.

Run:
    loopentx worker start --app examples.heartbeat.loop --config examples/heartbeat/loopentx_config.py
"""

from __future__ import annotations
import random
from dataclasses import dataclass

from loopentx import loop, skill
from loopentx.trust import policy


# ── Simulated external services ───────────────────────────────────────────────

@dataclass
class HealthState:
    status:   str           # "normal" | "degraded" | "critical"
    services: list[str]
    details:  dict


async def check_service_health() -> HealthState:
    """Simulate fetching health from a monitoring API."""
    error_rate = random.uniform(0.0, 0.2)
    services   = ["api", "auth", "payments"]
    if error_rate > 0.15:
        return HealthState("critical", ["payments"], {"error_rate": error_rate})
    elif error_rate > 0.07:
        return HealthState("degraded", ["api"], {"error_rate": error_rate})
    return HealthState("normal", [], {"error_rate": error_rate})


async def post_slack(channel: str, message: str) -> None:
    print(f"\n[SLACK #{channel}]\n{message}\n")


async def page_oncall(message: str) -> None:
    print(f"\n[PAGERDUTY] {message}\n")


# ── Skill: triage an incident ─────────────────────────────────────────────────

@policy(
    can_read=["metrics_api"],
    can_write=["slack", "pagerduty"],
    blast_radius="medium",
    shadow_cycles=2,
)
@skill(retries=3, timeout=60)
async def triage_incident(ctx, services: list[str], details: dict) -> str:
    """Fetch details, analyse root cause, notify the team."""
    analysis = await ctx.step("analyse", _fake_llm_analysis, services, details)
    await ctx.step("notify-slack",    post_slack, "incidents", analysis)

    if details.get("error_rate", 0) > 0.15:
        await ctx.step("page-oncall", page_oncall, f"CRITICAL: {analysis}")

    return analysis


async def _fake_llm_analysis(services: list[str], details: dict) -> str:
    return (
        f"Services affected: {', '.join(services)}. "
        f"Error rate: {details.get('error_rate', 0):.1%}. "
        f"Likely cause: traffic spike or recent deploy regression."
    )


# ── Loop: heartbeat ───────────────────────────────────────────────────────────

@loop(every="30m", memory=True)
async def heartbeat(ctx) -> dict:
    """Monitor health every 30 minutes. Act if degraded."""
    health = await ctx.step("check-health", check_service_health)

    decision = await ctx.think(
        "Given this health state, what action should I take?",
        context=f"Status: {health.status}, Services: {health.services}",
        choose_from=["triage", "monitor", "skip"],
    )

    print(f"[heartbeat] Status={health.status} → decision={decision}")

    if decision in ("triage", "monitor"):
        await ctx.invoke(
            triage_incident,
            services=health.services,
            details=health.details,
        )

    # Push to loop memory so we can review trends
    await ctx.memory.push_history({
        "status":   health.status,
        "decision": decision,
        "services": health.services,
    })

    return {"status": health.status, "decision": decision}
