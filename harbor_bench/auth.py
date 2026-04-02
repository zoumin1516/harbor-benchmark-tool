from __future__ import annotations

import base64
from typing import Optional
from urllib.parse import quote

import httpx


def basic_header(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


async def fetch_registry_token(
    client: httpx.AsyncClient,
    base_url: str,
    user: str,
    password: str,
    repository: str,
    actions: str = "pull",
) -> str:
    """
    向 Harbor /service/token 申请 registry JWT。
    repository 格式: project/repo 例如 library/nginx
    actions: pull 或 pull,push
    """
    scope = f"repository:{repository}:{actions}"
    url = (
        f"{base_url.rstrip('/')}/service/token"
        f"?service=harbor-registry&scope={quote(scope, safe='')}"
    )
    r = await client.get(url, headers={"Authorization": basic_header(user, password)})
    r.raise_for_status()
    data = r.json()
    token = data.get("token")
    if not token:
        raise RuntimeError(f"token 响应缺少 token 字段: {data}")
    return str(token)
