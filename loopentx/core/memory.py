"""LoopMemory — persistent state across loop iterations."""

from __future__ import annotations

import time
from typing import Any, Optional, TypeVar

from loopentx.core.models import LoopMemoryRecord, MemoryEntry

T = TypeVar("T")


class LoopMemory:
    """Persistent memory for a loop, shared across all iterations.

    Memory is loaded from the backend at the start of each run and saved
    after every mutating operation. The loop accumulates knowledge over
    time without any manual state management.

    Example:
        @loop(every="1h", memory=True)
        async def research_loop(ctx, topic: str):
            prior = ctx.memory.get("findings", default=[])
            new   = await ctx.step("search", search, topic, prior)
            ctx.memory.append("findings", new)
            ctx.memory.set("confidence", new.confidence)
    """

    def __init__(self, loop_name: str, backend: Any) -> None:
        self._loop_name = loop_name
        self._backend   = backend
        self._record: Optional[LoopMemoryRecord] = None

    async def _load(self) -> LoopMemoryRecord:
        if self._record is None:
            self._record = await self._backend.get_loop_memory(self._loop_name)
            if self._record is None:
                self._record = LoopMemoryRecord(loop_name=self._loop_name)
        return self._record

    async def _save(self) -> None:
        if self._record:
            self._record.updated_at = time.time()
            await self._backend.save_loop_memory(self._record)

    # ── Read ──────────────────────────────────────────────────────────────

    async def get(self, key: str, default: Any = None) -> Any:
        """Get a value by key. Returns default if not set."""
        record = await self._load()
        entry  = record.entries.get(key)
        return entry.value if entry else default

    async def last(self, n: int = 5) -> list[Any]:
        """Return the last n entries from the 'history' list."""
        record = await self._load()
        history = record.lists.get("history", [])
        return history[-n:]

    async def get_list(self, key: str) -> list[Any]:
        """Return the full list stored at key."""
        record = await self._load()
        return record.lists.get(key, [])

    async def keys(self) -> list[str]:
        """Return all scalar key names."""
        record = await self._load()
        return list(record.entries.keys())

    async def all(self) -> dict[str, Any]:
        """Return all scalar entries as a plain dict."""
        record = await self._load()
        return {k: v.value for k, v in record.entries.items()}

    # ── Write ─────────────────────────────────────────────────────────────

    async def set(self, key: str, value: Any) -> None:
        """Set a scalar value."""
        record = await self._load()
        record.entries[key] = MemoryEntry(key=key, value=value)
        await self._save()

    async def append(self, key: str, item: Any) -> None:
        """Append an item to a list stored at key."""
        record = await self._load()
        if key not in record.lists:
            record.lists[key] = []
        record.lists[key].append(item)
        await self._save()

    async def push_history(self, item: Any) -> None:
        """Append to the canonical 'history' list for use with last(n)."""
        await self.append("history", item)

    async def delete(self, key: str) -> None:
        """Delete a scalar key."""
        record = await self._load()
        record.entries.pop(key, None)
        await self._save()

    async def clear_list(self, key: str) -> None:
        """Clear a list."""
        record = await self._load()
        record.lists.pop(key, None)
        await self._save()

    async def clear_all(self) -> None:
        """Reset all memory for this loop."""
        self._record = LoopMemoryRecord(loop_name=self._loop_name)
        await self._save()
