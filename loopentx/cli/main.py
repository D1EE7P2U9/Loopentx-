"""Loopentx CLI — deploy, inspect, trust, runs, escalations, memory, worker."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config(config_path: str) -> None:
    path = Path(config_path)
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location("loopentx_config", path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)   # type: ignore[attr-defined]


def _load_app(app: str) -> object:
    try:
        return importlib.import_module(app)
    except Exception as e:
        console.print(f"[red]Failed to load app module '{app}': {e}[/red]")
        sys.exit(1)


# ── Root ──────────────────────────────────────────────────────────────────────

@click.group()
@click.version_option()
def cli() -> None:
    """Loopentx — write the loop once. Step back."""


# ── Worker ────────────────────────────────────────────────────────────────────

@cli.group()
def worker() -> None:
    """Manage the Loopentx worker."""


@worker.command("start")
@click.option("--app",    "-a", required=True, help="Module path (e.g. myapp.loops)")
@click.option("--config", "-c", default="loopentx_config.py")
def worker_start(app: str, config: str) -> None:
    """Start the worker and begin processing loops and events."""
    console.print(Panel.fit("[bold green]Starting Loopentx Worker[/bold green]"))
    _load_config(config)
    module = _load_app(app)

    from loopentx.core.orchestrator import Orchestrator
    orch = Orchestrator()

    for name in dir(module):
        obj  = getattr(module, name)
        kind = getattr(obj, "_loopentx_kind", None)
        if kind in ("skill", "loop"):
            orch.register(obj)

    console.print(f"[green]Registered {len(orch._skills)} skill(s), "
                  f"{len(orch._loops)} loop(s)[/green]")

    try:
        asyncio.run(orch.start())
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped.[/yellow]")


# ── Deploy ────────────────────────────────────────────────────────────────────

@cli.group()
def deploy() -> None:
    """Deploy loops and skills."""


@deploy.command("skill")
@click.argument("skill_name")
@click.option("--app",    "-a", required=True)
@click.option("--config", "-c", default="loopentx_config.py")
def deploy_skill(skill_name: str, app: str, config: str) -> None:
    """Register a skill with the orchestrator."""
    async def _run() -> None:
        _load_config(config)
        module = _load_app(app)
        fn     = getattr(module, skill_name, None)
        if fn is None or not hasattr(fn, "_loopentx_skill"):
            console.print(f"[red]Skill '{skill_name}' not found in {app}[/red]")
            sys.exit(1)

        from loopentx.core.config import get_config
        from loopentx.core.models import SkillRegistration
        cfg = get_config()
        pc  = fn._loopentx_skill.policy_context
        reg = pc.to_registration(skill_name) if pc else SkillRegistration(
            name=skill_name, kind="skill", is_active=True)
        await cfg.backend.save_skill_registration(reg)

        console.print(f"[green]✓ Deployed:[/green] {skill_name}")
        if reg.is_shadow:
            console.print(f"  [yellow]Shadow mode:[/yellow] {reg.shadow_cycles} cycles required")
        elif reg.require_approval:
            console.print(f"  [yellow]Awaiting approval[/yellow]")
        else:
            console.print(f"  [green]Status:[/green] Active")
    asyncio.run(_run())


@deploy.command("loop")
@click.argument("loop_name")
@click.option("--app",    "-a", required=True)
@click.option("--config", "-c", default="loopentx_config.py")
def deploy_loop(loop_name: str, app: str, config: str) -> None:
    """Register a loop with the orchestrator."""
    async def _run() -> None:
        _load_config(config)
        module = _load_app(app)
        fn     = getattr(module, loop_name, None)
        if fn is None or not hasattr(fn, "_loopentx_loop"):
            console.print(f"[red]Loop '{loop_name}' not found in {app}[/red]")
            sys.exit(1)
        from loopentx.core.config import get_config
        from loopentx.core.models import SkillRegistration
        ld  = fn._loopentx_loop
        cfg = get_config()
        reg = SkillRegistration(
            name=loop_name, kind="loop", is_active=True,
            cron=ld.cron, description=ld.description,
        )
        await cfg.backend.save_skill_registration(reg)
        console.print(f"[green]✓ Deployed loop:[/green] {loop_name}")
    asyncio.run(_run())


# ── Inspect ───────────────────────────────────────────────────────────────────

@cli.group()
def inspect() -> None:
    """Inspect loops, skills, and runs."""


@inspect.command("skill")
@click.argument("skill_name")
@click.option("--config", "-c", default="loopentx_config.py")
def inspect_skill(skill_name: str, config: str) -> None:
    """Show policy, trust score, and recent runs for a skill."""
    async def _run() -> None:
        _load_config(config)
        from loopentx.core.config import get_config
        from loopentx.trust.scorer import TrustScorer
        cfg   = get_config()
        reg   = await cfg.backend.get_skill_registration(skill_name)
        trust = await cfg.backend.get_trust_record(skill_name)
        runs  = await cfg.backend.get_runs(skill_name=skill_name, limit=10)

        if not reg:
            console.print(f"[red]Skill '{skill_name}' not registered.[/red]")
            return

        console.print(Panel(
            f"[bold]Status:[/bold] {'🟢 Active' if reg.is_active else '🟡 Inactive'}\n"
            f"[bold]Kind:[/bold] {reg.kind}\n"
            f"[bold]Blast radius:[/bold] {reg.blast_radius.value}\n"
            f"[bold]Can read:[/bold] {', '.join(reg.can_read) or 'none'}\n"
            f"[bold]Can write:[/bold] {', '.join(reg.can_write) or 'none'}\n"
            f"[bold]Shadow cycles remaining:[/bold] {reg.shadow_cycles_remaining}",
            title=f"[bold cyan]{skill_name}[/bold cyan]",
        ))

        if trust:
            scorer = TrustScorer()
            console.print(Panel(scorer.explain(trust), title="Trust"))
        else:
            console.print("[dim]No trust data yet.[/dim]")

        if runs:
            table = Table(box=box.SIMPLE, title="Recent runs")
            table.add_column("Run ID"); table.add_column("Status")
            table.add_column("Duration"); table.add_column("Shadow")
            for run in runs:
                c = {"completed": "green", "failed": "red"}.get(run.status.value, "white")
                table.add_row(
                    run.id[:10] + "…", f"[{c}]{run.status.value}[/{c}]",
                    f"{run.duration_ms}ms" if run.duration_ms else "-",
                    "yes" if run.is_shadow else "no",
                )
            console.print(table)
    asyncio.run(_run())


# ── Runs ──────────────────────────────────────────────────────────────────────

@cli.group()
def runs() -> None:
    """Query run history."""


@runs.command("list")
@click.option("--skill",  "-s", default=None)
@click.option("--last",   "-l", default=None, help="e.g. 7d, 24h")
@click.option("--limit",  "-n", default=20)
@click.option("--config", "-c", default="loopentx_config.py")
def runs_list(skill: Optional[str], last: Optional[str], limit: int, config: str) -> None:
    """List recent runs."""
    async def _run() -> None:
        import time as _time
        _load_config(config)
        from loopentx.core.config import get_config
        cfg   = get_config()
        since = None
        if last:
            u  = last[-1]
            v  = int(last[:-1])
            since = _time.time() - v * {"h": 3600, "d": 86400, "w": 604800}.get(u, 3600)
        run_list = await cfg.backend.get_runs(skill_name=skill, since=since, limit=limit)
        table = Table(box=box.SIMPLE)
        table.add_column("Run ID"); table.add_column("Skill")
        table.add_column("Status"); table.add_column("Trigger"); table.add_column("Iter")
        for r in run_list:
            c = {"completed": "green", "failed": "red"}.get(r.status.value, "white")
            table.add_row(
                r.id[:10] + "…", r.skill_name,
                f"[{c}]{r.status.value}[/{c}]", r.trigger, str(r.iteration),
            )
        console.print(table)
        console.print(f"[dim]{len(run_list)} run(s)[/dim]")
    asyncio.run(_run())


# ── Trust ─────────────────────────────────────────────────────────────────────

@cli.group()
def trust() -> None:
    """Manage skill trust scores and approvals."""


@trust.command("list")
@click.option("--config", "-c", default="loopentx_config.py")
def trust_list(config: str) -> None:
    """Show trust scores for all registered skills."""
    async def _run() -> None:
        _load_config(config)
        from loopentx.core.config import get_config
        cfg    = get_config()
        skills = await cfg.backend.list_skill_registrations()
        table  = Table(box=box.SIMPLE, title="Trust scores")
        table.add_column("Name"); table.add_column("Kind")
        table.add_column("Level"); table.add_column("Score")
        table.add_column("Runs"); table.add_column("Status")
        for reg in skills:
            tr     = await cfg.backend.get_trust_record(reg.name)
            score  = f"{tr.trust_score:.2f}" if tr else "-"
            level  = tr.trust_level.value if tr else "-"
            runs   = str(tr.total_runs) if tr else "0"
            status = "🟢 active" if reg.is_active else "🟡 pending"
            table.add_row(reg.name, reg.kind, level, score, runs, status)
        console.print(table)
    asyncio.run(_run())


@trust.command("approve")
@click.argument("skill_name")
@click.option("--by",     default="human")
@click.option("--config", "-c", default="loopentx_config.py")
def trust_approve(skill_name: str, by: str, config: str) -> None:
    """Approve a skill for live execution."""
    async def _run() -> None:
        _load_config(config)
        from loopentx.core.config import get_config
        from loopentx.trust.scorer import TrustScore
        cfg = get_config()
        await cfg.backend.approve_skill(skill_name, approved_by=by)
        await TrustScore.approve(skill_name, approved_by=by)
        console.print(f"[green]✓ Approved:[/green] {skill_name} (by {by})")
    asyncio.run(_run())


@trust.command("reject")
@click.argument("skill_name")
@click.option("--by",     default="human")
@click.option("--config", "-c", default="loopentx_config.py")
def trust_reject(skill_name: str, by: str, config: str) -> None:
    """Reject and pause a skill."""
    async def _run() -> None:
        _load_config(config)
        from loopentx.core.config import get_config
        from loopentx.trust.scorer import TrustScore
        cfg = get_config()
        await cfg.backend.set_skill_active(skill_name, active=False)
        await TrustScore.reject(skill_name, rejected_by=by)
        console.print(f"[red]✗ Rejected:[/red] {skill_name} — skill paused")
    asyncio.run(_run())


# ── Memory ────────────────────────────────────────────────────────────────────

@cli.group()
def memory() -> None:
    """Inspect and manage loop memory."""


@memory.command("show")
@click.argument("loop_name")
@click.option("--config", "-c", default="loopentx_config.py")
def memory_show(loop_name: str, config: str) -> None:
    """Show current memory for a loop."""
    async def _run() -> None:
        _load_config(config)
        from loopentx.core.config import get_config
        cfg = get_config()
        rec = await cfg.backend.get_loop_memory(loop_name)
        if not rec:
            console.print(f"[dim]No memory stored for '{loop_name}'.[/dim]")
            return
        console.print(Panel(
            "\n".join(
                f"[bold]{k}:[/bold] {v.value}" for k, v in rec.entries.items()
            ) or "(empty)",
            title=f"Memory: {loop_name}",
        ))
        for k, v in rec.lists.items():
            console.print(f"  [bold]{k}[/bold] ({len(v)} items): {v[-3:]}")
    asyncio.run(_run())


@memory.command("clear")
@click.argument("loop_name")
@click.option("--config", "-c", default="loopentx_config.py")
def memory_clear(loop_name: str, config: str) -> None:
    """Clear all memory for a loop."""
    async def _run() -> None:
        _load_config(config)
        from loopentx.core.config import get_config
        from loopentx.core.models import LoopMemoryRecord
        cfg = get_config()
        await cfg.backend.save_loop_memory(LoopMemoryRecord(loop_name=loop_name))
        console.print(f"[yellow]Cleared memory for '{loop_name}'.[/yellow]")
    asyncio.run(_run())


# ── Escalations ───────────────────────────────────────────────────────────────

@cli.group()
def escalations() -> None:
    """Manage loop escalations."""


@escalations.command("list")
@click.option("--config", "-c", default="loopentx_config.py")
def escalations_list(config: str) -> None:
    """List pending escalations awaiting human response."""
    async def _run() -> None:
        _load_config(config)
        from loopentx.core.config import get_config
        cfg    = get_config()
        pending = await cfg.backend.list_pending_escalations()
        if not pending:
            console.print("[dim]No pending escalations.[/dim]")
            return
        table = Table(box=box.SIMPLE, title="Pending escalations")
        table.add_column("ID"); table.add_column("Skill")
        table.add_column("Message"); table.add_column("Fallback")
        for e in pending:
            table.add_row(e.id[:10] + "…", e.skill_name, e.message[:60], e.fallback)
        console.print(table)
    asyncio.run(_run())


@escalations.command("respond")
@click.argument("escalation_id")
@click.argument("response")
@click.option("--config", "-c", default="loopentx_config.py")
def escalations_respond(escalation_id: str, response: str, config: str) -> None:
    """Provide a human response to an escalation."""
    async def _run() -> None:
        _load_config(config)
        from loopentx.core.orchestrator import Orchestrator
        orch = Orchestrator()
        await orch.respond_to_escalation(escalation_id, response)
        console.print(f"[green]✓ Response sent:[/green] {response}")
    asyncio.run(_run())


if __name__ == "__main__":
    cli()
