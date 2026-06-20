"""Global configuration for Loopentx."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from loopentx.backends.base import BaseBackend

_config: Optional["LoopentxConfig"] = None


@dataclass
class LoopentxConfig:
    backend:                "BaseBackend"
    llm_provider:           str = "openai"
    llm_model:              str = "gpt-4o"
    llm_api_key:            Optional[str] = None
    llm_extra:              dict[str, Any] = field(default_factory=dict)
    default_shadow_cycles:  int = 0
    default_blast_radius:   str = "low"
    auto_approve_low_blast: bool = True
    worker_concurrency:     int = 10
    worker_poll_interval:   float = 1.0
    log_level:              str = "INFO"
    store_step_outputs:     bool = True


def configure(
    backend: "BaseBackend",
    llm_provider: str = "openai",
    llm_model:    str = "gpt-4o",
    llm_api_key:  Optional[str] = None,
    **kwargs: Any,
) -> LoopentxConfig:
    """Configure Loopentx. Call once at application startup.

    Example:
        from loopentx import configure
        from loopentx.backends import RedisBackend

        configure(
            backend=RedisBackend("redis://localhost:6379"),
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
        )
    """
    global _config
    _config = LoopentxConfig(
        backend=backend,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        **kwargs,
    )
    return _config


def get_config() -> LoopentxConfig:
    """Return the current global configuration."""
    if _config is None:
        raise RuntimeError(
            "Loopentx is not configured. "
            "Call loopentx.configure() before using the framework."
        )
    return _config
