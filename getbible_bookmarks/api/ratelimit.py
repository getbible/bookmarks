"""In-memory token buckets keyed by contributor or client address."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    capacity: float
    refill_per_second: float
    max_keys: int = 10_000
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def acquire(self, key: str, cost: float = 1.0, *, now: float | None = None) -> float:
        """Consume ``cost`` tokens; return 0 when allowed, else seconds until allowed."""
        moment = time.monotonic() if now is None else now
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self.max_keys:
                self._prune(moment)
            bucket = _Bucket(tokens=self.capacity, updated=moment)
            self._buckets[key] = bucket
        elapsed = max(0.0, moment - bucket.updated)
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_second)
        bucket.updated = moment
        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return 0.0
        deficit = cost - bucket.tokens
        return deficit / self.refill_per_second if self.refill_per_second > 0 else float("inf")

    def _prune(self, moment: float) -> None:
        full_after = self.capacity / self.refill_per_second if self.refill_per_second > 0 else 0
        stale = [
            key for key, bucket in self._buckets.items() if moment - bucket.updated > full_after
        ]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) >= self.max_keys:
            oldest = sorted(self._buckets, key=lambda key: self._buckets[key].updated)
            for key in oldest[: len(oldest) // 2]:
                del self._buckets[key]


__all__ = ["RateLimiter"]
