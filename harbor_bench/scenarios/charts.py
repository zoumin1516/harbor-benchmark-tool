from __future__ import annotations

import time

import httpx

from harbor_bench.auth import basic_header
from harbor_bench.stats import LatencyRecorder, RateTimer


async def run_charts_worker(
    base_url: str,
    user: str,
    password: str,
    chart_project: str,
    stop_at: float,
    recorder: LatencyRecorder,
    verify_ssl: bool,
    rps: float | None,
    chart_filename: str | None,
) -> None:
    """ChartMuseum：GET /chartrepo/<project>/index.yaml，可选 GET charts/<tgz 文件名>。"""
    base = base_url.rstrip("/")
    proj = chart_project.strip("/")
    auth = basic_header(user, password)
    timer = RateTimer(rps)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=50)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0),
        verify=verify_ssl,
        limits=limits,
    ) as client:
        while time.monotonic() < stop_at:
            await timer.wait_tick()
            t0 = time.perf_counter()
            ok = True
            try:
                r = await client.get(
                    f"{base}/chartrepo/{proj}/index.yaml",
                    headers={"Authorization": auth},
                )
                if r.status_code >= 400:
                    ok = False
                elif chart_filename:
                    r2 = await client.get(
                        f"{base}/chartrepo/{proj}/charts/{chart_filename}",
                        headers={"Authorization": auth},
                    )
                    ok = r2.status_code < 400
            except Exception:
                ok = False
            recorder.record(time.perf_counter() - t0, ok)
