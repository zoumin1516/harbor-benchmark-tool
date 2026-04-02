from __future__ import annotations

import json
import time
from typing import Any

import httpx

from harbor_bench.auth import fetch_registry_token
from harbor_bench.stats import LatencyRecorder, RateTimer


def _parse_blob_digests_from_manifest(body: bytes, content_type: str) -> list[str]:
    """从 Docker/OCI manifest 中提取可拉取的 blob digest（config + layers）。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    try:
        data: Any = json.loads(body.decode("utf-8"))
    except Exception:
        return []
    digests: list[str] = []

    if "schemaVersion" in data and data.get("schemaVersion") == 1:
        fs = data.get("fsLayers") or []
        for layer in fs:
            d = layer.get("blobSum")
            if d and d.startswith("sha256:"):
                digests.append(d)
        return digests

    if ct.endswith("manifest.list.v2+json") or ct.endswith("image.index.v1+json"):
        for m in data.get("manifests") or []:
            d = m.get("digest")
            if d:
                digests.append(str(d))
        return digests

    cfg = data.get("config")
    if isinstance(cfg, dict) and cfg.get("digest"):
        digests.append(str(cfg["digest"]))
    for layer in data.get("layers") or []:
        d = layer.get("digest") if isinstance(layer, dict) else None
        if d:
            digests.append(str(d))
    return digests


def _is_manifest_list(ct: str) -> bool:
    c = (ct or "").split(";")[0].strip().lower()
    return c.endswith("manifest.list.v2+json") or c.endswith("image.index.v1+json")


async def _resolve_blob_digest_for_pull(
    client: httpx.AsyncClient,
    reg: str,
    repo_path: str,
    auth_h: dict[str, str],
    tag_or_digest: str,
    accept: str,
) -> str | None:
    """
    解析出可用于 GET .../blobs/<digest> 的 blob digest（处理 manifest list → 子 manifest）。
    """
    url = f"{reg}/v2/{repo_path}/manifests/{tag_or_digest}"
    rm = await client.get(url, headers={**auth_h, "Accept": accept})
    if rm.status_code >= 400:
        return None
    ct = rm.headers.get("content-type", "")
    body = rm.content
    if _is_manifest_list(ct):
        try:
            data: Any = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        manifests = data.get("manifests") or []
        if not manifests:
            return None
        child_digest = manifests[0].get("digest")
        if not child_digest:
            return None
        child_url = f"{reg}/v2/{repo_path}/manifests/{child_digest}"
        rm2 = await client.get(child_url, headers={**auth_h, "Accept": accept})
        if rm2.status_code >= 400:
            return None
        body = rm2.content
        ct = rm2.headers.get("content-type", "")
    digests = _parse_blob_digests_from_manifest(body, ct)
    if not digests:
        return None
    return digests[-1]


async def run_registry_manifest_worker(
    base_url: str,
    registry_host: str | None,
    user: str,
    password: str,
    repository: str,
    tag: str,
    stop_at: float,
    recorder: LatencyRecorder,
    verify_ssl: bool,
    rps: float | None,
    do_get_manifest: bool,
) -> None:
    """
    Registry manifest 压测：HEAD（及可选 GET）/v2/<repo>/manifests/<ref>
    registry_host: 若与 Harbor 入口不同（如直连 registry 域名），可单独指定；否则用 base_url
    """
    base = base_url.rstrip("/")
    reg = (registry_host or base).rstrip("/")
    repo_path = repository.strip("/")
    accept = (
        "application/vnd.oci.image.index.v1+json,"
        "application/vnd.docker.distribution.manifest.list.v2+json,"
        "application/vnd.oci.image.manifest.v1+json,"
        "application/vnd.docker.distribution.manifest.v2+json"
    )
    timer = RateTimer(rps)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0),
        verify=verify_ssl,
        limits=limits,
    ) as client:
        token = await fetch_registry_token(client, base, user, password, repo_path, "pull")
        auth_h = {"Authorization": f"Bearer {token}"}

        while time.monotonic() < stop_at:
            await timer.wait_tick()
            t0 = time.perf_counter()
            ok = True
            try:
                url = f"{reg}/v2/{repo_path}/manifests/{tag}"
                r = await client.head(url, headers={**auth_h, "Accept": accept})
                if r.status_code >= 400:
                    ok = False
                elif do_get_manifest:
                    r2 = await client.get(url, headers={**auth_h, "Accept": accept})
                    ok = r2.status_code < 400
            except Exception:
                ok = False
            recorder.record(time.perf_counter() - t0, ok)


async def run_registry_blob_worker(
    base_url: str,
    registry_host: str | None,
    user: str,
    password: str,
    repository: str,
    tag: str,
    blob_digest: str | None,
    stop_at: float,
    recorder: LatencyRecorder,
    verify_ssl: bool,
    rps: float | None,
    max_blob_bytes: int,
) -> None:
    """
    Blob 下载压测：先 GET manifest 解析 digest（若未指定 blob_digest），再 GET blob。
    max_blob_bytes: 仅读取响应前 N 字节以减轻带宽（仍建立完整连接与首包延迟）。
    """
    base = base_url.rstrip("/")
    reg = (registry_host or base).rstrip("/")
    repo_path = repository.strip("/")
    accept = (
        "application/vnd.oci.image.index.v1+json,"
        "application/vnd.docker.distribution.manifest.list.v2+json,"
        "application/vnd.oci.image.manifest.v1+json,"
        "application/vnd.docker.distribution.manifest.v2+json"
    )
    timer = RateTimer(rps)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0),
        verify=verify_ssl,
        limits=limits,
    ) as client:
        token = await fetch_registry_token(client, base, user, password, repo_path, "pull")
        auth_h = {"Authorization": f"Bearer {token}"}
        manifest_url = f"{reg}/v2/{repo_path}/manifests/{tag}"

        cached_digest: str | None = blob_digest

        while time.monotonic() < stop_at:
            await timer.wait_tick()
            t0 = time.perf_counter()
            ok = True
            try:
                digest = cached_digest
                if not digest:
                    digest = await _resolve_blob_digest_for_pull(
                        client, reg, repo_path, auth_h, tag, accept
                    )
                    if digest:
                        cached_digest = digest
                if not digest:
                    ok = False
                else:
                    blob_url = f"{reg}/v2/{repo_path}/blobs/{digest}"
                    async with client.stream(
                        "GET",
                        blob_url,
                        headers=auth_h,
                    ) as br:
                        if br.status_code >= 400:
                            ok = False
                        else:
                            n = 0
                            async for chunk in br.aiter_bytes():
                                n += len(chunk)
                                if n >= max_blob_bytes:
                                    break
            except Exception:
                ok = False
            recorder.record(time.perf_counter() - t0, ok)
