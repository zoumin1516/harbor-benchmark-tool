from __future__ import annotations

import time

import httpx

from harbor_bench.auth import basic_header
from harbor_bench.stats import LatencyRecorder, RateTimer


async def _one_round(
    client: httpx.AsyncClient,
    base: str,
    auth: str,
    project: str,
    repo: str,
) -> bool:
    """一轮 Core API：项目列表、仓库列表、artifact 列表（若存在）。"""
    h = {"Authorization": auth}
    try:
        r = await client.get(f"{base}/api/v2.0/projects", headers=h)
        if r.status_code >= 400:
            return False
        r = await client.get(
            f"{base}/api/v2.0/projects/{project}/repositories",
            headers=h,
            params={"page_size": 15},
        )
        if r.status_code >= 400:
            return False
        r = await client.get(
            f"{base}/api/v2.0/projects/{project}/repositories/{repo}/artifacts",
            headers=h,
            params={"page_size": 10},
        )
        # 404 表示仓库不存在，仍算「服务可用」层面的失败
        return r.status_code < 400
    except Exception:
        return False


async def run_core_worker(
    base_url: str,
    user: str,
    password: str,
    project: str,
    repository: str,
    stop_at: float,
    recorder: LatencyRecorder,
    verify_ssl: bool,
    rps: float | None,
) -> None:
    base = base_url.rstrip("/")
    auth = basic_header(user, password)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
    timer = RateTimer(rps)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),
        verify=verify_ssl,
        limits=limits,
    ) as client:
        while time.monotonic() < stop_at:
            await timer.wait_tick()
            t0 = time.perf_counter()
            ok = await _one_round(client, base, auth, project, repository)
            recorder.record(time.perf_counter() - t0, ok)
