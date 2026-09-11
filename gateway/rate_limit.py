"""In-process sliding-window rate limiter for LLM-cost-bearing endpoints
(/chat, /chat/stream, /broadcast, and the equivalent WebSocket message
types).

Not distributed and not persisted across restarts - that's fine for this
project's single-process deployment model, where the goal is just to stop
a runaway client (or prompt-injection loop) from exhausting LLM spend or
piling up unbounded sessions, not to defend a multi-instance service.
"""

import time
from collections import defaultdict, deque
from typing import Deque, Dict

from env_config import env_int

RATE_LIMIT_MAX_REQUESTS = env_int("RATE_LIMIT_MAX_REQUESTS", 30)
RATE_LIMIT_WINDOW_SECONDS = env_int("RATE_LIMIT_WINDOW_SECONDS", 60)

_hits: Dict[str, Deque[float]] = defaultdict(deque)


def check_rate_limit(key: str) -> bool:
    """Records a hit for `key` and returns whether it's still within the
    configured rate limit (False means the caller should reject)."""
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    hits = _hits[key]

    while hits and hits[0] < window_start:
        hits.popleft()

    if len(hits) >= RATE_LIMIT_MAX_REQUESTS:
        return False

    hits.append(now)
    return True
