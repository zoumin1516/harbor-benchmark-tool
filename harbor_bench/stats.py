from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import List


@dataclass
class LatencyRecorder:
    """线程安全的延迟记录器，用于计算分位数与 QPS。"""

    _latencies_ms: List[float] = field(default_factory=list)
    _ok: int = 0
    _err: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, elapsed_s: float, ok: bool) -> None:
        ms = elapsed_s * 1000.0
        with self._lock:
            self._latencies_ms.append(ms)
            if ok:
                self._ok += 1
            else:
                self._err += 1

    def merge(self, other: "LatencyRecorder") -> None:
        with self._lock:
            self._latencies_ms.extend(other._latencies_ms)
            self._ok += other._ok
            self._err += other._err

    def snapshot_sorted(self) -> tuple[List[float], int, int]:
        with self._lock:
            return sorted(self._latencies_ms), self._ok, self._err

    def report(self, duration_s: float) -> dict:
        latencies, ok, err = self.snapshot_sorted()
        total = ok + err
        qps = total / duration_s if duration_s > 0 else 0.0
        err_rate = (err / total * 100.0) if total else 0.0

        def percentile(p: float) -> float | None:
            if not latencies:
                return None
            k = (len(latencies) - 1) * (p / 100.0)
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return latencies[int(k)]
            return latencies[f] + (latencies[c] - latencies[f]) * (k - f)

        return {
            "requests_total": total,
            "success": ok,
            "errors": err,
            "error_rate_pct": round(err_rate, 4),
            "qps": round(qps, 4),
            "latency_ms": {
                "min": round(min(latencies), 3) if latencies else None,
                "max": round(max(latencies), 3) if latencies else None,
                "mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
                "p50": round(percentile(50) or 0, 3) if latencies else None,
                "p95": round(percentile(95) or 0, 3) if latencies else None,
                "p99": round(percentile(99) or 0, 3) if latencies else None,
            },
        }


class RateTimer:
    """简单节拍器：按固定间隔触发（用于控制 RPS）。"""

    def __init__(self, rps: float | None) -> None:
        self.rps = rps
        self._interval = 1.0 / rps if rps and rps > 0 else None
        self._next = time.monotonic()

    async def wait_tick(self) -> None:
        if self._interval is None:
            return
        import asyncio

        now = time.monotonic()
        sleep_for = self._next - now
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        self._next = max(self._next + self._interval, time.monotonic())
