# Loopentx

> *Write the loop once. Step back. Loopentx runs it forever.*

[![PyPI version](https://badge.fury.io/py/loopentx.svg)](https://badge.fury.io/py/loopentx)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://github.com/D1EE7P2U9/Loopentx-/actions/workflows/ci.yml/badge.svg)](https://github.com/D1EE7P2U9/Loopentx-/actions)

---

There's a shift happening in how developers work with AI.

- *"You shouldn't be prompting coding agents anymore. You should be designing loops that prompt your agents."* — [@steipete](https://x.com/steipete)
- *"I don't prompt Claude anymore. I write loops, the loops do the work."* — [@0xwhrrari](https://x.com/0xwhrrari)
- *"Remove yourself as the bottleneck. Arrange it once and hit go."* — [@karpathy](https://x.com/karpathy)

**Loopentx is the Python framework that makes this concrete.**

Not a prompt library. Not an agent wrapper. The infrastructure layer that lets you write a loop once — with durability, policy, trust, and memory built in — and trust it to run without you.

---

## The problem with existing frameworks

Every agent framework today still puts you in the loop by design.

LangGraph needs you to define the graph. CrewAI needs you to define agents and tasks. Inngest needs you to write the skills. These are excellent tools — but they all assume a human is nearby, watching, ready to intervene.

Loopentx is built around a different assumption: **you set it up once, then you're done.**

And it adds the layer every other framework is missing: **trust** — so when your loops run at 3am, you know they're doing the right thing, not just running.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        YOU (developer)                       │
│              Write the loop once. Step back.                │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    LOOPENTX FRAMEWORK                        │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                     LOOP LAYER                         │  │
│  │  @loop · cron/event · ctx.think() · memory · until=   │  │
│  └───────────────────────────┬────────────────────────────┘  │
│                              │                               │
│  ┌───────────────────────────▼────────────────────────────┐  │
│  │                     SKILL LAYER                        │  │
│  │  @skill · ctx.step() · retries · ctx.spawn() · hooks  │  │
│  └───────────────────────────┬────────────────────────────┘  │
│                              │                               │
│  ┌───────────────────────────▼────────────────────────────┐  │
│  │                  ORCHESTRATOR LAYER                    │  │
│  │   Scheduling · concurrency · history · hot-deploy      │  │
│  └───────────────────────────┬────────────────────────────┘  │
│                              │                               │
│  ┌───────────────────────────▼────────────────────────────┐  │
│  │            TRUST + POLICY LAYER  ✦ unique              │  │
│  │  @policy · shadow mode · trust scoring · escalation    │  │
│  └───────────────────────────┬────────────────────────────┘  │
└──────────────────────────────┼──────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
    LLM providers        External tools        Observability
  OpenAI · Anthropic    APIs · DBs · Slack    Runs · traces
```

Four layers. Most frameworks give you two. The trust layer is what makes the difference between a loop you watch and a loop you trust.

---

## Quickstart

```bash
pip install loopentx
```

```python
from loopentx import loop, skill, configure
from loopentx.trust import policy
from loopentx.backends import RedisBackend

configure(backend=RedisBackend("redis://localhost:6379"))

# A skill — durable, checkpointed, policy-scoped
@policy(can_read=["metrics_api"], can_write=["slack"], shadow_cycles=3)
@skill(retries=3, timeout=120)
async def triage_incident(ctx, services: list[str]):
    metrics  = await ctx.step("fetch",     fetch_metrics, services)
    analysis = await ctx.step("analyze",   ctx.think, "What's the root cause?", context=metrics)
    await ctx.step("notify", post_slack, analysis)
    return analysis

# A loop — runs forever, decides what to do, invokes skills
@loop(every="30m", memory=True)
async def health_check(ctx):
    health = await ctx.step("assess", check_health)

    decision = await ctx.think(
        "Is this health status worth waking someone up for?",
        context=health,
        choose_from=["act", "monitor", "skip"]
    )

    if decision == "act":
        await ctx.invoke(triage_incident, services=health.affected)
```

```bash
loopentx worker start --app myapp.loops
```

That's it. You're done. The loop runs every 30 minutes. If something's wrong, it triages it. If the process dies mid-execution, it resumes from the last checkpoint. If the skill misbehaves, the trust layer catches it.

---

## Core primitives

### `@loop` — the entry point

A loop is the unit of autonomous work. It runs on a schedule or event, uses an LLM to decide what to do next, and invokes skills to do it.

```python
@loop(
    every="1h",                        # interval: "30m", "2h", "1d"
    cron="0 9 * * 1",                  # or cron expression
    event="deploy.completed",          # or event trigger
    until=lambda ctx: ctx.memory.get("done"),  # exit condition
    max_iterations=100,                # safety ceiling
    memory=True,                       # persist state across runs
)
async def my_loop(ctx, **event_data):
    ...
```

### `ctx.think()` — the LLM decision point

Every loop has a `think()` call. This is where the agent makes a decision. It's explicit and named — you always know where the LLM is in the loop.

```python
decision = await ctx.think(
    "Given what we know, what should we do next?",
    context=ctx.memory.last(5),
    choose_from=["continue", "escalate", "done"],
)
```

### `ctx.step()` — checkpointed execution

Every step is persisted. If the process restarts, completed steps are replayed from cache — not re-executed. LLM calls are never repeated. Tokens are never wasted.

```python
result  = await ctx.step("fetch-data", fetch_from_api, url)
summary = await ctx.step("summarize",  call_llm, result)
```

### `ctx.memory` — loop-native persistence

Each loop has memory that persists across runs automatically.

```python
ctx.memory.set("last_result", result)
ctx.memory.get("last_result")
ctx.memory.last(n=5)           # last n run outputs
ctx.memory.append("log", item) # grow a list over time
```

### `ctx.spawn()` — child loops

A loop can spawn another loop as a subtask. Loops supervising loops, natively.

```python
# Fire and forget — parent continues immediately
await ctx.spawn(summarise_loop, data=chunk, wait=False)

# Wait for result — parent blocks until child completes
result = await ctx.spawn(deep_research_loop, topic=topic, wait=True)

# Gather multiple children in parallel
results = await ctx.gather([
    ctx.spawn(worker_loop, task=t, wait=True) for t in tasks
])
```

### `ctx.escalate()` — optional human checkpoint

Humans aren't in the loop by default. But the loop can decide to pull one in.

```python
if decision == "uncertain":
    response = await ctx.escalate(
        "Loop hit an edge case — error rate 25% for 3 cycles. What should I do?",
        timeout="2h",      # if no response in 2h, use fallback
        fallback="pause",  # pause | continue | abort
    )
```

### `@policy` — the trust layer

Declare what a skill is allowed to do. Enforced at runtime, not just documented.

```python
@policy(
    can_read=["db", "metrics_api"],  # read access
    can_write=["slack", "email"],    # write/action access
    blast_radius="medium",           # low | medium | high | critical
    shadow_cycles=5,                 # dry runs before going live
    require_approval=False,          # auto-approve if blast_radius=low
)
@skill(retries=3)
async def my_skill(ctx, ...):
    ...
```

---

## The three loop patterns

Loopentx is designed around the three patterns that come up again and again in real agentic systems.

### Pattern 1 — The heartbeat loop

*Boris's model: "I write loops, the loops do the work."*

Runs on a schedule. Checks state. Acts if needed. Never needs prompting.

```python
@loop(every="1h", memory=True)
async def monitor_loop(ctx):
    state    = await ctx.step("check", fetch_state)
    decision = await ctx.think("Is action needed?", context=state,
                               choose_from=["act", "skip"])
    if decision == "act":
        await ctx.invoke(handle_anomaly, state=state)
        ctx.memory.append("actions_taken", state)
```

### Pattern 2 — The research loop

*Andrej's model: "Maximize token throughput. Remove yourself as the bottleneck."*

Runs until a goal is reached, not until a human says stop. Self-improving across iterations.

```python
def confident_enough(ctx) -> bool:
    return ctx.memory.get("confidence", 0) > 0.85

@loop(until=confident_enough, max_iterations=50, memory=True)
async def research_loop(ctx, topic: str):
    prior     = ctx.memory.get("findings", [])
    findings  = await ctx.step("search", search_and_read, topic, prior)
    synthesis = await ctx.step("synthesize", ctx.think,
                               "How confident are we? What's missing?",
                               context=findings)
    ctx.memory.set("confidence", synthesis.confidence)
    ctx.memory.append("findings", synthesis.result)
```

### Pattern 3 — The supervisor loop

*Steipete's model: "Design loops that prompt your agents."*

A parent loop breaks work into subtasks, spawns child loops, and collects results.

```python
@loop(cron="0 9 * * 1")  # Every Monday 9am
async def supervisor_loop(ctx):
    tasks   = await ctx.step("plan", decompose_goal, weekly_goal)
    results = await ctx.gather([
        ctx.spawn(worker_loop, task=t, wait=True) for t in tasks
    ])
    report  = await ctx.step("report", synthesize_results, results)
    await ctx.step("send", email_report, report)
```

---

## What makes Loopentx different

|                                        | LangGraph | CrewAI | Inngest | Agentex | **Loopentx** |
|----------------------------------------|-----------|--------|---------|---------|--------------|
| Core primitive                         | Graph     | Agent crew | Function | Skill | **Loop** |
| Philosophy                             | You define the graph | You define agents | You define functions | You define policy | **You define the loop, then leave** |
| Step checkpointing                     | Partial   | ❌     | ✅      | ✅      | **✅**       |
| Loop memory                            | ❌        | ❌     | ❌      | ❌      | **✅**       |
| Exit conditions                        | Manual    | Manual | Manual  | Manual  | **Native primitive** |
| Child loops                            | ❌        | ❌     | ✅      | ✅      | **✅**       |
| `ctx.think()` — explicit LLM decision  | ❌        | ❌     | ❌      | ❌      | **✅**       |
| Trust scoring                          | ❌        | ❌     | ❌      | ✅      | **✅**       |
| Shadow mode                            | ❌        | ❌     | ❌      | ✅      | **✅**       |
| Capability scoping                     | ❌        | ❌     | ❌      | ✅      | **✅**       |
| Human escalation (opt-in)              | ❌        | ⚠️    | ❌      | ⚠️     | **✅**       |
| Python-first                           | ✅        | ✅     | ❌      | ✅      | **✅**       |

The key distinction: every other framework puts a human in the design loop. Loopentx puts you in the *setup* loop and takes you out of the *execution* loop.

---

## How agents run on their own

This is the core question. Here's exactly what happens after you run `loopentx worker start`:

```
1. You wrote the loop. You started the worker. You're done.

2. Loopentx scheduler fires the loop at the configured time/event.

3. The loop runs ctx.think() → LLM evaluates state and decides what to do.
   (You are not consulted. The LLM decides.)

4. The loop calls ctx.step() for each action.
   Each step is checkpointed — if the process dies here, it resumes.
   LLM calls inside steps are never repeated.

5. If a step fails → automatic retry with exponential backoff.
   If all retries fail → on_failure hook fires, run is logged.

6. If the loop spawns a child → child runs with its own checkpointing.
   Parent waits (if wait=True) or continues independently.

7. Exit condition is evaluated after each iteration.
   If met → loop stops. Result is stored. Notification sent if configured.
   If not met → loop sleeps until next scheduled time.

8. Trust layer runs in background:
   - Tracks success/failure rate per skill
   - Shadow mode intercepts write actions until cycles complete
   - Trust score updates hourly
   - Skills below threshold flagged for human review

9. You check the dashboard in the morning.
   You see what ran, what succeeded, what failed, what the LLM decided.
   You own the loop. You just don't have to watch it.
```

---

## CLI reference

```bash
# Worker
loopentx worker start --app myapp.loops   # start the execution worker
loopentx worker status                    # check worker health

# Deploy
loopentx deploy loop my_loop --app myapp.loops
loopentx deploy skill my_skill --app myapp.loops

# Inspect
loopentx inspect loop my_loop             # status, memory, run history
loopentx inspect skill my_skill           # trust score, policy, recent runs

# Runs
loopentx runs list --last 7d              # recent runs
loopentx runs inspect <run-id>            # full step trace
loopentx runs replay <run-id>             # replay a failed run

# Trust
loopentx trust list                       # trust scores for all skills
loopentx trust approve my_skill           # approve for live execution
loopentx trust reject my_skill            # pause and reject

# Memory
loopentx memory show my_loop              # view loop memory
loopentx memory clear my_loop             # reset loop memory
```

---

## Integrations

Loopentx is designed to sit alongside your existing stack, not replace it.

**With LangGraph** — use LangGraph to define agent graph structure; use Loopentx to make each graph execution durable and policy-scoped:

```python
@policy(can_write=["db"], blast_radius="medium")
@skill(retries=3)
async def run_langgraph_agent(ctx, state: dict):
    result = await ctx.step("execute", my_lg_graph.invoke, state)
    return result
```

**With CrewAI** — use CrewAI for multi-agent conversation; use Loopentx to schedule and govern the crew execution:

```python
@loop(cron="0 8 * * *", memory=True)
async def run_daily_crew(ctx):
    result = await ctx.step("crew", my_crew.kickoff, {"topic": ctx.memory.get("topic")})
    ctx.memory.set("last_output", result)
```

**With OpenAI / Anthropic directly** — `ctx.think()` calls your configured LLM provider. Swap providers in config, loops stay unchanged.

---

## Installation

```bash
# Core
pip install loopentx

# With Redis backend (production)
pip install loopentx[redis]

# With Postgres backend
pip install loopentx[postgres]

# With OpenAI
pip install loopentx[openai]

# With Anthropic
pip install loopentx[anthropic]

# Full dev install
pip install loopentx[dev]
```

---

## Examples

See [`examples/`](./examples/) for complete working code:

| Example                   | Pattern         | Demonstrates                              |
|---------------------------|-----------------|-------------------------------------------|
| `examples/heartbeat/`     | Heartbeat loop  | Monitoring, cron, ctx.think()             |
| `examples/research/`      | Research loop   | until=, memory, confidence scoring        |
| `examples/supervisor/`    | Supervisor loop | ctx.spawn(), ctx.gather(), child loops    |
| `examples/health_monitor/`| Combined        | All four layers, shadow mode, trust       |

---

## Configuration

```python
# loopentx_config.py
from loopentx import configure
from loopentx.backends import RedisBackend

configure(
    backend=RedisBackend(url="redis://localhost:6379"),
    llm_provider="anthropic",        # "openai" | "anthropic" | "custom"
    llm_model="claude-sonnet-4-6",
    llm_api_key="sk-ant-...",        # or set ANTHROPIC_API_KEY env var

    # Trust defaults
    default_shadow_cycles=0,
    auto_approve_low_blast=True,

    # Worker
    worker_concurrency=10,
    worker_poll_interval=1.0,
)
```

---

## Roadmap

- [x] `@loop` with cron, event, and interval triggers
- [x] `ctx.think()` — explicit LLM decision point
- [x] `ctx.step()` — step checkpointing
- [x] `ctx.memory` — loop-native persistence
- [x] `ctx.spawn()` — child loops and ctx.gather()
- [x] `ctx.escalate()` — optional human checkpoint
- [x] `@skill` with retries, timeout, on_failure
- [x] `@policy` — capability scoping, shadow mode, blast radius
- [x] Trust scoring — UNTRUSTED → AUTONOMOUS pipeline
- [x] Memory backend (in-memory + Redis)
- [x] CLI — deploy, inspect, runs, trust, worker
- [ ] Postgres backend
- [ ] Web dashboard (run history, trust scores, memory viewer)
- [ ] Temporal adapter
- [ ] Loop authoring agent (agent writes its own loops, trust pipeline validates)
- [ ] Evaluation suite integrations

---

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). The project is young — issues, PRs, and ideas are all welcome.

```bash
git clone https://github.com/D1EE7P2U9/Loopentx-
cd Loopentx-
pip install -e ".[dev]"
pytest tests/
```

---

## Why "Loopentx"?

Loop + agent + execution. The framework for loops that run without you.

---

## License

MIT — see [LICENSE](./LICENSE)

