"""
Supervisor Loop — Steipete's Pattern
"Design loops that prompt your agents."

A parent loop breaks a goal into tasks, spawns child loops for each,
and synthesises the results. Loops supervising loops.

Run:
    loopentx worker start --app examples.supervisor.loop --config examples/supervisor/loopentx_config.py
"""

from __future__ import annotations
import asyncio
from dataclasses import dataclass

from loopentx import loop, skill
from loopentx.trust import policy


# ── Types ─────────────────────────────────────────────────────────────────────

@dataclass
class Task:
    id:          str
    description: str
    priority:    str  # "high" | "medium" | "low"


@dataclass
class TaskResult:
    task_id:  str
    output:   str
    success:  bool


# ── Simulated helpers ─────────────────────────────────────────────────────────

async def decompose_goal(goal: str) -> list[Task]:
    """Break a goal into sub-tasks (real: call an LLM planner)."""
    return [
        Task("t1", f"Research current state of: {goal}",    "high"),
        Task("t2", f"Identify key gaps in: {goal}",         "high"),
        Task("t3", f"Propose solutions for: {goal}",        "medium"),
        Task("t4", f"Draft summary report for: {goal}",     "low"),
    ]


async def execute_task(task: Task) -> TaskResult:
    """Execute a single task (real: call an LLM agent)."""
    await asyncio.sleep(0.1)  # simulate work
    return TaskResult(
        task_id=task.id,
        output=f"Completed '{task.description}' [priority={task.priority}]",
        success=True,
    )


async def compile_report(results: list[TaskResult]) -> str:
    """Compile task results into a final report."""
    lines = [f"Weekly Report — {len(results)} tasks completed\n"]
    for r in results:
        status = "✓" if r.success else "✗"
        lines.append(f"  {status} [{r.task_id}] {r.output}")
    return "\n".join(lines)


async def send_report(report: str, recipient: str) -> None:
    print(f"\n[EMAIL → {recipient}]\n{report}\n")


# ── Worker skill (child) ──────────────────────────────────────────────────────

@policy(can_read=["task_db"], blast_radius="low")
@skill(retries=2, timeout=120)
async def worker_skill(ctx, task_id: str, description: str, priority: str) -> TaskResult:
    """Execute a single task. Invoked by the supervisor."""
    task   = Task(id=task_id, description=description, priority=priority)
    result = await ctx.step("execute", execute_task, task)
    return result


# ── Worker loop (child) ───────────────────────────────────────────────────────

@loop(every="999h")  # only triggered via ctx.spawn(), not on a schedule
async def worker_loop(ctx, task_id: str = "", description: str = "", priority: str = "") -> TaskResult:
    """A per-task worker loop spawned by the supervisor."""
    task   = Task(id=task_id, description=description, priority=priority)
    result = await ctx.step("execute", execute_task, task)
    print(f"  [worker_loop:{task_id}] {result.output}")
    return result


# ── Supervisor loop (parent) ──────────────────────────────────────────────────

@policy(can_write=["email"], blast_radius="low")
@loop(cron="0 9 * * 1", memory=True)  # every Monday 9am
async def supervisor_loop(ctx, goal: str = "agentic framework ecosystem") -> str:
    """Break the weekly goal into tasks, spawn workers, compile the report."""

    # Step 1: plan
    tasks = await ctx.step("decompose", decompose_goal, goal)
    print(f"[supervisor] Spawning {len(tasks)} worker loops for: {goal}")

    # Step 2: spawn all workers in parallel, wait for results
    results = await ctx.gather([
        ctx.spawn(
            worker_loop,
            wait=True,
            task_id=t.id,
            description=t.description,
            priority=t.priority,
        )
        for t in tasks
    ])

    # Step 3: compile
    report = await ctx.step("compile", compile_report, results)

    # Step 4: send
    await ctx.step("send", send_report, report, "team@company.com")

    # Update memory
    await ctx.memory.set("last_report", report)
    await ctx.memory.push_history({"goal": goal, "tasks": len(tasks)})

    print(f"[supervisor] Done. Report sent.")
    return report
