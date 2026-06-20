"""Event system for event-triggered loops."""

from __future__ import annotations

import time
from typing import Any, Optional
from pydantic import BaseModel, Field
from ulid import ULID


class LoopentxEvent(BaseModel):
    id:        str = Field(default_factory=lambda: str(ULID()))
    name:      str
    data:      dict[str, Any] = Field(default_factory=dict)
    source:    Optional[str] = None
    timestamp: float = Field(default_factory=time.time)


def event(
    name:   str,
    data:   Optional[dict[str, Any]] = None,
    source: Optional[str] = None,
) -> LoopentxEvent:
    """Create and return a LoopentxEvent.

    Publish it via the backend to trigger event-driven loops.

    Example:
        from loopentx import event, get_config

        cfg = get_config()
        evt = event("deploy.completed", data={"env": "prod", "version": "2.4.1"})
        await cfg.backend.publish_event(evt)
    """
    return LoopentxEvent(name=name, data=data or {}, source=source)
