"""Full Loopentx test suite — core, trust, backends, memory."""

from __future__ import annotations

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch

from loopentx import configure, loop, skill
from loopentx.backends.memory import MemoryBackend
from loopentx.core.context import LoopContext, _parse_duration
from loopentx.core.models import RunStatus, StepStatus, TrustLevel, LoopMemoryRecord
from loopentx.core.exceptions import StepError, SkillError, PolicyViolationError
from loopentx.core.events import event, LoopentxEvent
from loopentx.trust.policy import policy, PolicyContext
from loopentx.trust.scorer import TrustScorer, TrustScore
from loopentx.core.models import RunRecord, TrustRecord


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def backend():
    return MemoryBackend()


@pytest.fixture(autouse=True)
def setup(backend):
    configure(backend=backend)
    yield
    backend.reset()


# ══════════════════════════════════════════════════════════════════════════════
# Context & step checkpointing
# ══════════════════════════════════════════════════════════════════════════════

class TestLoopContext:

    @pytest.mark.asyncio
    async def test_step_executes_function(self, backend):
        ctx    = LoopContext(run_id="r1", skill_name="s", backend=backend)
        result = await ctx.step("my-step", AsyncMock(return_value=42))
        assert result == 42

    @pytest.mark.asyncio
    async def test_step_caches_result(self, backend):
        calls = 0

        async def expensive():
            nonlocal calls; calls += 1
            return "value"

        ctx = LoopContext(run_id="r2", skill_name="s", backend=backend)
        r1  = await ctx.step("step", expensive)
        r2  = await ctx.step("step", expensive)  # should hit cache
        assert r1 == r2 == "value"
        assert calls == 1

    @pytest.mark.asyncio
    async def test_step_raises_step_error_on_failure(self, backend):
        ctx = LoopContext(run_id="r3", skill_name="s", backend=backend)

        async def boom():
            raise ValueError("oops")

        with pytest.raises(StepError) as exc:
            await ctx.step("fail", boom)
        assert "fail" in str(exc.value)

    @pytest.mark.asyncio
    async def test_shadow_mode_flag(self, backend):
        ctx = LoopContext(run_id="r4", skill_name="s", backend=backend, shadow_mode=True)
        assert ctx.is_shadow is True

    @pytest.mark.asyncio
    async def test_invoke_non_skill_raises(self, backend):
        ctx = LoopContext(run_id="r5", skill_name="s", backend=backend)

        async def not_a_skill(): pass

        with pytest.raises(ValueError, match="not a Loopentx skill"):
            await ctx.invoke(not_a_skill)

    @pytest.mark.asyncio
    async def test_spawn_non_loop_raises(self, backend):
        ctx = LoopContext(run_id="r6", skill_name="s", backend=backend)

        async def not_a_loop(): pass

        with pytest.raises(ValueError, match="not a Loopentx loop"):
            await ctx.spawn(not_a_loop)

    def test_is_shadow_flag_default_false(self, backend):
        ctx = LoopContext(run_id="r7", skill_name="s", backend=backend)
        assert ctx.is_shadow is False


# ══════════════════════════════════════════════════════════════════════════════
# Duration parser
# ══════════════════════════════════════════════════════════════════════════════

def test_parse_duration_seconds():
    assert _parse_duration("30s") == 30

def test_parse_duration_minutes():
    assert _parse_duration("30m") == 1800

def test_parse_duration_hours():
    assert _parse_duration("2h") == 7200

def test_parse_duration_days():
    assert _parse_duration("1d") == 86400


# ══════════════════════════════════════════════════════════════════════════════
# @skill decorator
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillDecorator:

    @pytest.mark.asyncio
    async def test_basic_execution(self, backend):
        @skill(retries=0)
        async def simple(ctx, x: int) -> int:
            return await ctx.step("double", AsyncMock(return_value=x * 2))

        result = await simple(x=5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_records_run_on_completion(self, backend):
        @skill(retries=0)
        async def tracked(ctx) -> str:
            return "done"

        await tracked()
        runs = await backend.get_runs(skill_name="tracked")
        assert len(runs) == 1
        assert runs[0].status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_retries_on_failure(self, backend):
        count = 0

        @skill(retries=2)
        async def flaky(ctx) -> str:
            nonlocal count; count += 1
            if count < 3: raise RuntimeError("not yet")
            return "ok"

        with patch("asyncio.sleep", AsyncMock()):
            result = await flaky()
        assert result == "ok"
        assert count == 3

    @pytest.mark.asyncio
    async def test_records_failure_after_exhausted_retries(self, backend):
        @skill(retries=0)
        async def always_fails(ctx) -> None:
            raise RuntimeError("always")

        with pytest.raises(SkillError):
            await always_fails()

        runs = await backend.get_runs(skill_name="always_fails")
        assert runs[0].status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_on_failure_callback(self, backend):
        called = {}

        async def on_fail(error, run, ctx):
            called["error"] = error

        @skill(retries=0, on_failure=on_fail)
        async def fails(ctx) -> None:
            raise ValueError("err")

        with pytest.raises(SkillError):
            await fails()
        assert isinstance(called.get("error"), ValueError)

    @pytest.mark.asyncio
    async def test_timeout(self, backend):
        @skill(retries=0, timeout=1)
        async def slow(ctx) -> None:
            await asyncio.sleep(99)

        with pytest.raises((SkillError, asyncio.TimeoutError)):
            await slow()


# ══════════════════════════════════════════════════════════════════════════════
# @loop decorator
# ══════════════════════════════════════════════════════════════════════════════

class TestLoopDecorator:

    def test_invalid_cron_raises(self):
        with pytest.raises(ValueError, match="Invalid cron"):
            @loop(cron="not-valid")
            async def bad(ctx): pass

    def test_no_trigger_raises(self):
        with pytest.raises(ValueError, match="at least one of"):
            @loop()
            async def nothing(ctx): pass

    @pytest.mark.asyncio
    async def test_manual_execution(self, backend):
        @loop(every="1h")
        async def simple_loop(ctx) -> str:
            return "ran"

        result = await simple_loop()
        assert result == "ran"

    @pytest.mark.asyncio
    async def test_records_run(self, backend):
        @loop(every="1h")
        async def recorded(ctx) -> None:
            pass

        await recorded()
        runs = await backend.get_runs(skill_name="recorded")
        assert len(runs) == 1
        assert runs[0].trigger == "manual"

    @pytest.mark.asyncio
    async def test_loop_invokes_skill(self, backend):
        invoked = {}

        @skill(retries=0)
        async def child(ctx, msg: str) -> str:
            invoked["msg"] = msg
            return f"got:{msg}"

        @loop(every="1h")
        async def parent(ctx) -> None:
            await ctx.invoke(child, msg="hello")

        await parent()
        assert invoked.get("msg") == "hello"

    @pytest.mark.asyncio
    async def test_loop_spawns_child(self, backend):
        spawned = {}

        @loop(every="999h")
        async def child_loop(ctx, val: str = "") -> str:
            spawned["val"] = val
            return val

        @loop(every="1h")
        async def parent_loop(ctx) -> None:
            result = await ctx.spawn(child_loop, wait=True, val="world")
            assert result == "world"

        await parent_loop()
        assert spawned.get("val") == "world"

    @pytest.mark.asyncio
    async def test_gather_runs_concurrently(self, backend):
        results = []

        @loop(every="999h")
        async def worker(ctx, n: int = 0) -> int:
            results.append(n)
            return n

        @loop(every="1h")
        async def supervisor(ctx) -> None:
            out = await ctx.gather([
                ctx.spawn(worker, wait=True, n=i) for i in range(3)
            ])
            assert sorted(out) == [0, 1, 2]

        await supervisor()
        assert sorted(results) == [0, 1, 2]


# ══════════════════════════════════════════════════════════════════════════════
# Loop memory
# ══════════════════════════════════════════════════════════════════════════════

class TestLoopMemory:

    @pytest.mark.asyncio
    async def test_set_and_get(self, backend):
        @loop(every="1h", memory=True)
        async def mem_loop(ctx) -> None:
            await ctx.memory.set("key", "value")
            result = await ctx.memory.get("key")
            assert result == "value"

        await mem_loop()

    @pytest.mark.asyncio
    async def test_append_and_get_list(self, backend):
        @loop(every="1h", memory=True)
        async def list_loop(ctx) -> None:
            await ctx.memory.append("items", "a")
            await ctx.memory.append("items", "b")
            items = await ctx.memory.get_list("items")
            assert items == ["a", "b"]

        await list_loop()

    @pytest.mark.asyncio
    async def test_last_n(self, backend):
        @loop(every="1h", memory=True)
        async def history_loop(ctx) -> None:
            for i in range(10):
                await ctx.memory.push_history(i)
            last5 = await ctx.memory.last(5)
            assert last5 == [5, 6, 7, 8, 9]

        await history_loop()

    @pytest.mark.asyncio
    async def test_default_value(self, backend):
        @loop(every="1h", memory=True)
        async def default_loop(ctx) -> None:
            val = await ctx.memory.get("missing", default=42)
            assert val == 42

        await default_loop()

    @pytest.mark.asyncio
    async def test_memory_persists_across_calls(self, backend):
        """Memory written in one call is readable in the next."""
        @loop(every="1h", memory=True)
        async def persist_loop(ctx) -> None:
            count = await ctx.memory.get("count", default=0)
            await ctx.memory.set("count", count + 1)

        await persist_loop()
        await persist_loop()
        rec = await backend.get_loop_memory("persist_loop")
        assert rec is not None
        assert rec.entries["count"].value == 2


# ══════════════════════════════════════════════════════════════════════════════
# @policy decorator
# ══════════════════════════════════════════════════════════════════════════════

class TestPolicy:

    def test_assert_can_read_allowed(self):
        pc = PolicyContext("s", can_read=["db"], can_write=["slack"],
                          blast_radius=None, shadow_cycles=0, require_approval=False)
        pc.assert_can_read("db")     # explicit read
        pc.assert_can_read("slack")  # write implies read — no exception

    def test_assert_can_read_denied(self):
        pc = PolicyContext("s", can_read=["db"], can_write=[],
                          blast_radius=None, shadow_cycles=0, require_approval=False)
        with pytest.raises(PolicyViolationError) as exc:
            pc.assert_can_read("stripe_api")
        assert "read" in str(exc.value)

    def test_assert_can_write_denied(self):
        pc = PolicyContext("s", can_read=["slack"], can_write=[],
                          blast_radius=None, shadow_cycles=0, require_approval=False)
        with pytest.raises(PolicyViolationError) as exc:
            pc.assert_can_write("slack")
        assert "write" in str(exc.value)

    def test_write_action_heuristic(self):
        pc = PolicyContext("s", [], [], None, 0, False)

        async def post_slack(): pass
        async def send_email(): pass
        async def fetch_data(): pass
        async def notify_team(): pass

        assert pc.is_write_action(post_slack)  is True
        assert pc.is_write_action(send_email)  is True
        assert pc.is_write_action(notify_team) is True
        assert pc.is_write_action(fetch_data)  is False

    def test_high_blast_auto_requires_approval(self):
        @policy(can_write=["infra"], blast_radius="high")
        @skill(retries=0)
        async def dangerous(ctx): pass

        assert dangerous._loopentx_skill.policy_context.require_approval is True


# ══════════════════════════════════════════════════════════════════════════════
# Trust scorer
# ══════════════════════════════════════════════════════════════════════════════

class TestTrustScorer:

    @pytest.mark.asyncio
    async def test_no_runs_returns_zero_score(self, backend):
        scorer = TrustScorer()
        trust  = await scorer.evaluate("ghost_skill")
        assert trust.trust_score == 0.0
        assert trust.trust_level == TrustLevel.UNTRUSTED

    @pytest.mark.asyncio
    async def test_high_success_rate_increases_score(self, backend):
        now = time.time()
        for i in range(20):
            await backend.save_run(RunRecord(
                id=f"r{i}", skill_name="reliable", trigger="cron",
                status=RunStatus.COMPLETED,
                started_at=now - i * 3600,
                completed_at=now - i * 3600 + 5,
                duration_ms=5000,
            ))
        scorer = TrustScorer()
        trust  = await scorer.evaluate("reliable")
        assert trust.trust_score > 0.4

    @pytest.mark.asyncio
    async def test_human_approvals_boost_score(self, backend):
        now = time.time()
        for i in range(10):
            await backend.save_run(RunRecord(
                id=f"a{i}", skill_name="approved_skill", trigger="cron",
                status=RunStatus.COMPLETED,
                started_at=now - i * 3600,
                completed_at=now - i * 3600 + 5,
            ))
        await TrustScore.approve("approved_skill", approved_by="alice")
        await TrustScore.approve("approved_skill", approved_by="bob")
        scorer = TrustScorer()
        trust  = await scorer.evaluate("approved_skill")
        assert trust.trust_score > 0.3

    def test_explain_output(self):
        trust = TrustRecord(
            skill_name="s", total_runs=50, successful_runs=45,
            trust_score=0.72, trust_level=TrustLevel.TRUSTED,
        )
        explanation = TrustScorer().explain(trust)
        assert "0.72" in explanation
        assert "trusted" in explanation


# ══════════════════════════════════════════════════════════════════════════════
# MemoryBackend
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryBackend:

    @pytest.mark.asyncio
    async def test_save_and_get_run(self, backend):
        run = RunRecord(id="x1", skill_name="s", trigger="manual",
                        status=RunStatus.COMPLETED)
        await backend.save_run(run)
        r = await backend.get_run("x1")
        assert r is not None and r.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_filter_runs_by_skill(self, backend):
        for i in range(5):
            await backend.save_run(RunRecord(
                id=f"r{i}", skill_name="a" if i < 3 else "b", trigger="cron",
            ))
        assert len(await backend.get_runs(skill_name="a")) == 3
        assert len(await backend.get_runs(skill_name="b")) == 2

    @pytest.mark.asyncio
    async def test_step_checkpoint(self, backend):
        from loopentx.core.models import StepRecord, StepStatus
        step = StepRecord(id="s1", run_id="r1", skill_name="s",
                          step_id="step-a", status=StepStatus.COMPLETED,
                          output={"k": "v"})
        await backend.save_step(step)
        result = await backend.get_step_result("s1")
        assert result == {"k": "v"}

    @pytest.mark.asyncio
    async def test_step_result_none_if_running(self, backend):
        from loopentx.core.models import StepRecord, StepStatus
        step = StepRecord(id="s2", run_id="r1", skill_name="s",
                          step_id="step-b", status=StepStatus.RUNNING)
        await backend.save_step(step)
        assert await backend.get_step_result("s2") is None

    @pytest.mark.asyncio
    async def test_approve_skill(self, backend):
        from loopentx.core.models import SkillRegistration
        reg = SkillRegistration(name="p", kind="skill", is_active=False, is_shadow=True)
        await backend.save_skill_registration(reg)
        await backend.approve_skill("p", approved_by="alice")
        approved = await backend.get_skill_registration("p")
        assert approved.is_active is True
        assert approved.approved_by == "alice"

    @pytest.mark.asyncio
    async def test_trust_outcome_recording(self, backend):
        await backend.record_trust_outcome("s", success=True)
        await backend.record_trust_outcome("s", success=True)
        await backend.record_trust_outcome("s", success=False)
        trust = await backend.get_trust_record("s")
        assert trust.total_runs      == 3
        assert trust.successful_runs == 2
        assert trust.failed_runs     == 1

    @pytest.mark.asyncio
    async def test_event_publish_and_poll(self, backend):
        evt1 = LoopentxEvent(name="deploy.completed", data={"env": "prod"})
        evt2 = LoopentxEvent(name="incident.detected")
        await backend.publish_event(evt1)
        await backend.publish_event(evt2)
        events = await backend.poll_events()
        assert len(events) == 2
        assert {e.name for e in events} == {"deploy.completed", "incident.detected"}
        # queue empty after poll
        assert await backend.poll_events() == []

    @pytest.mark.asyncio
    async def test_loop_memory_save_and_load(self, backend):
        rec = LoopMemoryRecord(loop_name="my_loop")
        from loopentx.core.models import MemoryEntry
        rec.entries["k"] = MemoryEntry(key="k", value=99)
        await backend.save_loop_memory(rec)
        loaded = await backend.get_loop_memory("my_loop")
        assert loaded is not None
        assert loaded.entries["k"].value == 99

    @pytest.mark.asyncio
    async def test_reset_clears_all(self, backend):
        await backend.save_run(RunRecord(id="r1", skill_name="s", trigger="cron"))
        await backend.publish_event(LoopentxEvent(name="test"))
        backend.reset()
        assert await backend.get_runs() == []
        assert await backend.poll_events() == []


# ══════════════════════════════════════════════════════════════════════════════
# Events
# ══════════════════════════════════════════════════════════════════════════════

def test_event_creation():
    evt = event("deploy.completed", data={"env": "prod"}, source="ci")
    assert evt.name == "deploy.completed"
    assert evt.data == {"env": "prod"}
    assert evt.source == "ci"
    assert evt.id  # ULID generated
