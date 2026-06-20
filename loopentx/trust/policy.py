"""The @policy decorator — capability scoping, shadow mode, blast radius."""

from __future__ import annotations

import functools
import time
from typing import Any, Callable, Optional

import structlog

from loopentx.core.models import BlastRadius, SkillRegistration, TrustLevel
from loopentx.core.exceptions import PolicyViolationError, SkillNotApprovedError
from loopentx.core.config import get_config

log = structlog.get_logger()

_WRITE_PATTERNS = [
    "post_", "send_", "write_", "create_", "update_", "delete_",
    "publish_", "notify_", "alert_", "push_", "emit_", "dispatch_",
    "insert_", "patch_", "put_", "remove_", "drop_",
]


class PolicyContext:
    """Runtime policy enforcement attached to a skill."""

    def __init__(
        self,
        skill_name:       str,
        can_read:         list[str],
        can_write:        list[str],
        blast_radius:     BlastRadius,
        shadow_cycles:    int,
        require_approval: bool,
    ) -> None:
        self.skill_name       = skill_name
        self.can_read         = set(can_read)
        self.can_write        = set(can_write)
        self.blast_radius     = blast_radius
        self.shadow_cycles    = shadow_cycles
        self.require_approval = require_approval

    def assert_can_read(self, capability: str) -> None:
        if capability not in self.can_read and capability not in self.can_write:
            raise PolicyViolationError(self.skill_name, capability, "read")

    def assert_can_write(self, capability: str) -> None:
        if capability not in self.can_write:
            raise PolicyViolationError(self.skill_name, capability, "write")

    def is_write_action(self, fn: Callable) -> bool:
        name = fn.__name__.lower()
        return any(name.startswith(p) for p in _WRITE_PATTERNS)

    def to_registration(self, fn_name: str) -> SkillRegistration:
        needs_gate = self.shadow_cycles > 0 or self.require_approval
        return SkillRegistration(
            name=fn_name,
            kind="skill",
            can_read=list(self.can_read),
            can_write=list(self.can_write),
            blast_radius=self.blast_radius,
            shadow_cycles=self.shadow_cycles,
            shadow_cycles_remaining=self.shadow_cycles,
            require_approval=self.require_approval,
            is_active=not needs_gate,
            is_shadow=self.shadow_cycles > 0,
            trust_level=TrustLevel.UNTRUSTED if needs_gate else TrustLevel.PROVISIONAL,
        )


def policy(
    can_read:         Optional[list[str]] = None,
    can_write:        Optional[list[str]] = None,
    blast_radius:     str = "low",
    shadow_cycles:    int = 0,
    require_approval: bool = False,
) -> Callable:
    """Declare the trust policy for a skill.

    Must be applied ABOVE @skill in the decorator stack.

    Args:
        can_read:         Systems the skill may read from.
        can_write:        Systems the skill may write to (implies read).
        blast_radius:     Impact scope: "low" | "medium" | "high" | "critical".
                          high/critical automatically require human approval.
        shadow_cycles:    Dry-run cycles before going live. Write actions
                          are captured but not applied during shadow mode.
        require_approval: Require explicit `loopentx trust approve` before live.

    Example:
        @policy(
            can_read=["metrics_api"],
            can_write=["slack"],
            blast_radius="medium",
            shadow_cycles=3,
        )
        @skill(retries=3)
        async def triage(ctx, services: list[str]):
            ...
    """
    try:
        blast = BlastRadius(blast_radius)
    except ValueError:
        raise ValueError(
            f"Invalid blast_radius: {blast_radius!r}. "
            f"Must be one of: low, medium, high, critical"
        )

    _require = require_approval or blast in (BlastRadius.HIGH, BlastRadius.CRITICAL)

    def decorator(fn: Callable) -> Callable:
        policy_ctx = PolicyContext(
            skill_name=fn.__name__,
            can_read=can_read or [],
            can_write=can_write or [],
            blast_radius=blast,
            shadow_cycles=shadow_cycles,
            require_approval=_require,
        )

        if hasattr(fn, "_loopentx_skill"):
            fn._loopentx_skill.policy_context = policy_ctx

        @functools.wraps(fn)
        async def wrapper(**kwargs: Any) -> Any:
            if shadow_cycles > 0 or _require:
                cfg = get_config()
                reg = await cfg.backend.get_skill_registration(fn.__name__)
                if reg and not reg.is_active:
                    if reg.is_shadow and reg.shadow_cycles_remaining > 0:
                        return await _run_shadow(fn, policy_ctx, reg, **kwargs)
                    raise SkillNotApprovedError(fn.__name__)
            return await fn(**kwargs)

        wrapper._loopentx_policy = policy_ctx   # type: ignore[attr-defined]
        if hasattr(fn, "_loopentx_skill"):
            wrapper._loopentx_skill = fn._loopentx_skill  # type: ignore[attr-defined]
            wrapper._loopentx_skill.policy_context = policy_ctx
            wrapper._loopentx_kind = "skill"             # type: ignore[attr-defined]

        return wrapper

    return decorator


async def _run_shadow(
    fn:         Callable,
    policy_ctx: PolicyContext,
    reg:        Any,
    **kwargs:   Any,
) -> Any:
    from loopentx.core.context import LoopContext
    from loopentx.core.config import get_config
    from ulid import ULID

    cfg    = get_config()
    run_id = str(ULID())
    ctx    = LoopContext(
        run_id=run_id,
        skill_name=fn.__name__,
        backend=cfg.backend,
        policy_context=policy_ctx,
        shadow_mode=True,
    )

    if hasattr(fn, "_loopentx_skill"):
        result = await fn._loopentx_skill.execute(ctx, **kwargs)
    else:
        result = await fn(ctx, **kwargs)

    remaining = reg.shadow_cycles_remaining - 1
    if remaining <= 0:
        if policy_ctx.blast_radius == BlastRadius.LOW and not policy_ctx.require_approval:
            await cfg.backend.approve_skill(fn.__name__, approved_by="auto")
            log.info("policy.auto_approved", skill=fn.__name__)
        else:
            log.info("policy.shadow_complete_awaiting_approval",
                     skill=fn.__name__, blast=policy_ctx.blast_radius.value)
    else:
        reg.shadow_cycles_remaining = remaining
        await cfg.backend.save_skill_registration(reg)

    return result
