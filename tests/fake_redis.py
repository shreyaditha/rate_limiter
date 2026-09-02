"""
In-memory Redis subset that executes the sliding-window Lua semantics in Python.

Used by unit/integration tests so they do not need a live Redis process.
The production path still uses EVAL against real Redis — this replica exists
only to lock the algorithm (evict → count → increment) without racey multi-calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeRedis:
    zsets: dict[str, list[tuple[float, str]]] = field(default_factory=dict)
    fail: bool = False

    async def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("redis down")
        return True

    async def aclose(self) -> None:
        return None

    async def eval(self, script: str, numkeys: int, *args) -> list[int]:
        if self.fail:
            raise ConnectionError("redis down")
        # Mirrors SLIDING_WINDOW_LUA in app.limiter.sliding_window.
        key = str(args[0])
        now = int(args[1])
        window = int(args[2])
        max_requests = int(args[3])
        request_id = str(args[4])
        cutoff = now - window

        members = [(s, m) for s, m in self.zsets.get(key, []) if s > cutoff]
        current = len(members)

        if current >= max_requests:
            reset_at = now + window
            if members:
                reset_at = int(min(s for s, _ in members) + window)
            self.zsets[key] = members
            return [0, current, 0, reset_at]

        members.append((float(now), request_id))
        self.zsets[key] = members
        new_count = current + 1
        remaining = max_requests - new_count
        reset_at = int(min(s for s, _ in members) + window)
        return [1, new_count, remaining, reset_at]
