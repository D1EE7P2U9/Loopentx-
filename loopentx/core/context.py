"""LoopContext — execution context passed to every loop and skill."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Coroutine, Optional, TypeVar

from loopentx.core.models import StepRecord, StepStatus, RunStatus
from loopentx.core.exceptions import StepError, EscalationTimeoutError
from loopentx.core.memory import LoopMemory

import structlog

log = structlog.get_logger()

T = TypeVar("T")

# Patterns that signal a write / side-effect action
_WRITE_PATTERNS = [
    "post_", "send_", "write_", "create_", "update_", "delete_",
    "publish_", "notify_", "alert_", "push_", "emit_", "dispatch_",
    "insert_", "patch_", "put_", "remove_", "drop_",
]


class LoopContext:
    """Execution context for a loop or skill run.

    Provides step checkpointing, LLM decisions, loop memory,
    child loop spawning, and optional human escalation.

    Example:
        @loop(every="1h", memory=True)
        async def my_loop(ctx):
            state    = await ctx.step("fetch", fetch_state)
            decision = await ctx.think("What next?", context=state,
                                       choose_from=["act", "skip"])
            if decision == "act":
                await ctx.invoke(my_skill, data=state)
            ctx.memory.push_history({"state": state, "decision": decision})
    """

    def __init__(
        self,
        run_id:         str,
        skill_name:     str,
        backend:        Any,
        policy_context: Optional[Any] = None,
        shadow_mode:    bool = False,
        iteration:      int  = 1,
        enable_memory:  bool = False,
    ) -> None:
        self.run_id         = run_id
        self.skill_name     = skill_name
        self._backend       = backend
        self._policy_ctx    = policy_context
        self._shadow_mode   = shadow_mode
        self._iteration     = iteration
        self._completed:    dict[str, Any] = {}

        self.memory = LoopMemory(skill_name, backend) if enable_memory else _NoopMemory()

    @property
    def is_shadow(self) -> bool:
        return self._shadow_mode

    @property
    def iteration(self) -> int:
        return self._iteration

    # ── Step checkpointing ────────────────────────────────────────────────

    async def step(
        self,
        step_id: str,
        fn:      Callable[..., Coroutine[Any, Any, T]],
        *args:   Any,
        **kwargs:Any,
    ) -> T:
        """Execute a durable, checkpointed step.

        If this step already completed in a previous run attempt, the
        cached result is returned immediately without re-executing fn.

        In shadow mode, write actions are intercepted and logged but
        not applied.
        """
        full_id = f"{self.run_id}:{step_id}"

        # In-process cache
        if step_id in self._completed:
            return self._completed[step_id]

        # Persistent checkpoint
        cached = await self._backend.get_step_result(full_id)
        if cached is not None:
            self._completed[step_id] = cached
            return cached

        # Shadow mode: intercept writes
        if self._shadow_mode and self._is_write(fn):
            return await self._shadow_step(step_id, fn, *args, **kwargs)

        # Execute
        rec = StepRecord(
            id=full_id, run_id=self.run_id, skill_name=self.skill_name,
            step_id=step_id, status=StepStatus.RUNNING, started_at=time.time(),
            is_shadow=self._shadow_mode,
        )
        await self._backend.save_step(rec)

        try:
            t0     = time.monotonic()
            result = await fn(*args, **kwargs)
            ms     = int((time.monotonic() - t0) * 1000)

            rec.status       = StepStatus.COMPLETED
            rec.output       = result
            rec.duration_ms  = ms
            rec.completed_at = time.time()
            await self._backend.save_step(rec)

            self._completed[step_id] = result
            return result

        except Exception as exc:
            rec.status       = StepStatus.FAILED
            rec.error        = str(exc)
            rec.completed_at = time.time()
            await self._backend.save_step(rec)
            raise StepError(step_id=step_id, cause=exc) from exc

    # ── LLM decision point ────────────────────────────────────────────────

    async def think(
        self,
        prompt:       str,
        context:      Any  = None,
        choose_from:  Optional[list[str]] = None,
        system:       Optional[str] = None,
    ) -> str:
        """Call the configured LLM and return its decision.

        This is the explicit, named decision point in every loop.
        Results are checkpointed like any other step.

        Args:
            prompt:      The question or instruction for the LLM.
            context:     Additional context to inject (serialised to str).
            choose_from: Constrain the LLM to one of these choices.
            system:      Optional system prompt override.

        Returns:
            The LLM's response as a string (or one of choose_from values).
        """
        from loopentx.llm.caller import call_llm

        step_id = f"think:{hash(prompt) & 0xFFFF:04x}"

        ctx_str = ""
        if context is not None:
            ctx_str = f"\n\nContext:\n{context}"

        choice_str = ""
        if choose_from:
            choice_str = (
                f"\n\nYou MUST respond with exactly one of these options "
                f"(no other text): {', '.join(choose_from)}"
            )

        full_prompt = f"{prompt}{ctx_str}{choice_str}"

        return await self.step(
            step_id,
            call_llm,
            full_prompt,
            system=system,
            choose_from=choose_from,
        )

    # ── Child loop / skill invocation ─────────────────────────────────────

    async def invoke(self, skill_fn: Any, **kwargs: Any) -> Any:
        """Invoke another skill as a synchronous child task.

        The child runs with its own run ID and full checkpointing.
        The parent waits for the child to complete.

        Args:
            skill_fn: A function decorated with @skill.
            **kwargs: Arguments forwarded to the skill.
        """
        if not hasattr(skill_fn, "_loopentx_skill"):
            raise ValueError(
                f"{skill_fn.__name__} is not a Loopentx skill. "
                "Decorate it with @skill first."
            )
        from ulid import ULID
        child_run_id = str(ULID())
        child_ctx = LoopContext(
            run_id=child_run_id,
            skill_name=skill_fn.__name__,
            backend=self._backend,
            policy_context=skill_fn._loopentx_skill.policy_context,
            shadow_mode=self._shadow_mode,
        )
        return await skill_fn._loopentx_skill.execute(child_ctx, **kwargs)

    async def spawn(
        self,
        loop_fn: Any,
        wait:    bool = True,
        **kwargs: Any,
    ) -> Any:
        """Spawn a child loop.

        Args:
            loop_fn: A function decorated with @loop.
            wait:    If True, block until the child completes.
                     If False, fire-and-forget.
            **kwargs: Arguments forwarded to the child loop.

        Returns:
            The child loop's return value if wait=True, else None.
        """
        if not hasattr(loop_fn, "_loopentx_loop"):
            raise ValueError(
                f"{loop_fn.__name__} is not a Loopentx loop. "
                "Decorate it with @loop first."
            )
        loop_def = loop_fn._loopentx_loop
        if wait:
            return await loop_def.execute(trigger="spawn", event_data=kwargs)
        else:
            asyncio.create_task(
                loop_def.execute(trigger="spawn", event_data=kwargs)
            )
            return None

    async def gather(self, coroutines: list[Any]) -> list[Any]:
        """Run multiple spawn() or step() calls concurrently.

        Example:
            results = await ctx.gather([
                ctx.spawn(worker, task=t, wait=True) for t in tasks
            ])
        """
        return list(await asyncio.gather(*coroutines))

    # ── Human escalation (opt-in) ─────────────────────────────────────────

    async def escalate(
        self,
        message:  str,
        timeout:  str  = "2h",
        fallback: str  = "pause",
    ) -> str:
        """Pause and request human input.

        The loop parks here until a human responds via CLI or API,
        or until timeout elapses (in which case fallback is applied).

        Args:
            message:  The question or situation to present to the human.
            timeout:  How long to wait: "30m", "2h", "1d".
            fallback: What to do on timeout: "pause" | "continue" | "abort".

        Returns:
            The human's response string, or the fallback action.
        """
        from loopentx.core.models import EscalationRecord, EscalationStatus
        from ulid import ULID
        import time

        timeout_s = _parse_duration(timeout)

        esc = EscalationRecord(
            id=str(ULID()),
            run_id=self.run_id,
            skill_name=self.skill_name,
            message=message,
            timeout_s=timeout_s,
            fallback=fallback,
        )
        await self._backend.save_escalation(esc)

        log.info(
            "loop.escalation_created",
            run_id=self.run_id,
            message=message,
            timeout=timeout,
        )

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            await asyncio.sleep(5)
            updated = await self._backend.get_escalation(esc.id)
            if updated and updated.status == EscalationStatus.RESPONDED:
                log.info("loop.escalation_resolved", response=updated.response)
                return updated.response or fallback

        log.warning("loop.escalation_timed_out", fallback=fallback)
        esc.status = EscalationStatus.TIMED_OUT
        await self._backend.save_escalation(esc)
        return fallback

    # ── Internals ─────────────────────────────────────────────────────────

    def _is_write(self, fn: Callable) -> bool:
        name = fn.__name__.lower()
        return any(name.startswith(p) for p in _WRITE_PATTERNS)

    async def _shadow_step(
        self,
        step_id: str,
        fn:      Callable[..., Coroutine[Any, Any, T]],
        *args:   Any,
        **kwargs: Any,
    ) -> T:
        """Run a write-side-effect step in shadow — capture but don't apply."""
        try:
            result = await fn(*args, **kwargs)
            await self._backend.save_shadow_output(
                run_id=self.run_id, step_id=step_id, output=result
            )
            log.info(
                "shadow.write_intercepted",
                skill=self.skill_name,
                step=step_id,
            )
            return result
        except Exception as exc:
            await self._backend.save_shadow_output(
                run_id=self.run_id, step_id=step_id, error=str(exc)
            )
            raise StepError(step_id=step_id, cause=exc) from exc


# ── Duration parser ────────────────────────────────────────────────────────────

def _parse_duration(s: str) -> int:
    """Parse a duration string like '30m', '2h', '1d' into seconds."""
    unit = s[-1].lower()
    val  = int(s[:-1])
    return {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 3600) * val


# ── Noop memory (when memory=False) ───────────────────────────────────────────

class _NoopMemory:
    """Returned as ctx.memory when memory=False. All ops are no-ops."""
    async def get(self, key: str, default: Any = None) -> Any: return default
    async def set(self, key: str, value: Any) -> None: pass
    async def append(self, key: str, item: Any) -> None: pass
    async def last(self, n: int = 5) -> list: return []
    async def get_list(self, key: str) -> list: return []
    async def push_history(self, item: Any) -> None: pass
    async def delete(self, key: str) -> None: pass
    async def clear_all(self) -> None: pass
    async def all(self) -> dict: return {}
    async def keys(self) -> list: return []
