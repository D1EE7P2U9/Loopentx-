"""
Research Loop — Andrej's Pattern
"Remove yourself as the bottleneck. Arrange it once and hit go."

Runs until confidence threshold is met (or max 50 iterations).
No human in the chain. Loop accumulates findings across runs.

Run:
    loopentx worker start --app examples.research.loop --config examples/research/loopentx_config.py
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field

from loopentx import loop, skill
from loopentx.core.context import LoopContext


# ── Simulated search / synthesis ──────────────────────────────────────────────

@dataclass
class SearchResult:
    query:      str
    findings:   list[str]
    confidence: float


@dataclass
class Synthesis:
    summary:    str
    confidence: float
    gaps:       list[str]


async def search_web(query: str, prior: list) -> SearchResult:
    """Simulate a web search (real: use Tavily, Exa, etc.)"""
    new_info = [
        f"Finding {random.randint(100, 999)}: insight about '{query}'",
        f"Finding {random.randint(100, 999)}: counter-evidence about '{query}'",
    ]
    return SearchResult(
        query=query,
        findings=prior + new_info,
        confidence=min(0.3 + len(prior) * 0.06, 0.95),
    )


async def synthesise(findings: list[str], iteration: int) -> Synthesis:
    """Simulate LLM synthesis of accumulated findings."""
    conf = min(0.25 + iteration * 0.08, 0.92)
    gaps = [] if conf > 0.80 else [f"Need more data on sub-topic {iteration + 1}"]
    return Synthesis(
        summary=f"Synthesis after {iteration} iterations: {len(findings)} findings analysed.",
        confidence=conf,
        gaps=gaps,
    )


# ── Exit condition ────────────────────────────────────────────────────────────

async def confident_enough(ctx: LoopContext) -> bool:
    """Stop when confidence > 0.85 or we've stored enough findings."""
    conf = await ctx.memory.get("confidence", default=0.0)
    return float(conf) > 0.85


# ── Loop ──────────────────────────────────────────────────────────────────────

@loop(
    every="5m",              # check every 5 minutes in real use
    until=confident_enough,  # stop when we know enough
    max_iterations=50,       # hard safety ceiling
    memory=True,
)
async def research_loop(ctx, topic: str = "agentic AI frameworks") -> dict:
    """Self-directing research loop. Runs until confident, not until told to stop."""
    iteration = ctx.iteration
    prior     = await ctx.memory.get_list("findings")

    # Step 1: search (checkpointed — not re-run on retry)
    result = await ctx.step(
        f"search-{iteration}",
        search_web,
        topic,
        prior,
    )

    # Step 2: synthesise findings
    synthesis = await ctx.step(
        f"synthesise-{iteration}",
        synthesise,
        result.findings,
        iteration,
    )

    # Update loop memory
    for f in result.findings:
        await ctx.memory.append("findings", f)
    await ctx.memory.set("confidence", synthesis.confidence)
    await ctx.memory.set("latest_summary", synthesis.summary)
    await ctx.memory.push_history({
        "iteration":  iteration,
        "confidence": synthesis.confidence,
        "gaps":       synthesis.gaps,
    })

    print(
        f"[research_loop] iter={iteration} "
        f"confidence={synthesis.confidence:.2f} "
        f"findings={len(result.findings)}"
    )

    if synthesis.confidence > 0.85:
        print(f"\n✓ Research complete after {iteration} iterations.")
        print(f"  Summary: {synthesis.summary}")

    return {
        "iteration":  iteration,
        "confidence": synthesis.confidence,
        "summary":    synthesis.summary,
    }
