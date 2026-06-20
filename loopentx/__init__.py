"""
Loopentx — Write the loop once. Step back. Loopentx runs it forever.

Four layers:
  Loop        → scheduled / event-triggered autonomous execution
  Skill       → durable, checkpointed, retryable functions
  Orchestrator→ scheduling, concurrency, history, hot-deploy
  Trust       → policy, shadow mode, trust scoring, escalation
"""

from loopentx.core.loop import loop
from loopentx.core.skill import skill
from loopentx.core.context import LoopContext
from loopentx.core.orchestrator import Orchestrator
from loopentx.core.config import configure, get_config
from loopentx.core.events import event, LoopentxEvent
from loopentx.trust.policy import policy

__all__ = [
    "loop",
    "skill",
    "policy",
    "event",
    "configure",
    "get_config",
    "LoopContext",
    "LoopentxEvent",
    "Orchestrator",
]

__version__ = "0.1.0"
