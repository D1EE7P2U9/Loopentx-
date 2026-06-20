"""Loopentx custom exceptions."""

from __future__ import annotations
from typing import Optional


class LoopentxError(Exception):
    """Base exception for all Loopentx errors."""


class StepError(LoopentxError):
    def __init__(self, step_id: str, cause: Optional[Exception] = None) -> None:
        self.step_id = step_id
        self.cause   = cause
        super().__init__(f"Step '{step_id}' failed: {cause}")


class SkillError(LoopentxError):
    def __init__(self, skill_name: str, cause: Optional[Exception] = None) -> None:
        self.skill_name = skill_name
        self.cause      = cause
        super().__init__(f"Skill '{skill_name}' failed after all retries: {cause}")


class SkillTimeoutError(LoopentxError):
    def __init__(self, skill_name: str, timeout: int) -> None:
        self.skill_name = skill_name
        self.timeout    = timeout
        super().__init__(f"Skill '{skill_name}' timed out after {timeout}s")


class PolicyViolationError(LoopentxError):
    def __init__(self, skill_name: str, capability: str, action: str) -> None:
        self.skill_name  = skill_name
        self.capability  = capability
        self.action      = action
        super().__init__(
            f"Policy violation in '{skill_name}': "
            f"attempted to {action} '{capability}' which is not declared in @policy"
        )


class SkillNotApprovedError(LoopentxError):
    def __init__(self, skill_name: str) -> None:
        self.skill_name = skill_name
        super().__init__(
            f"Skill '{skill_name}' is not approved. "
            f"Run `loopentx trust approve {skill_name}` or wait for shadow cycles to complete."
        )


class LoopExitConditionMet(Exception):
    """Raised internally when a loop's exit condition is satisfied."""


class EscalationTimeoutError(LoopentxError):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Escalation for run '{run_id}' timed out with no response.")


class BackendError(LoopentxError):
    pass


class ConfigurationError(LoopentxError):
    pass
