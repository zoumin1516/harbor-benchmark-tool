from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from harbor_bench.scenarios.charts import run_charts_worker
from harbor_bench.scenarios.core_api import run_core_worker
from harbor_bench.scenarios.registry import run_registry_blob_worker, run_registry_manifest_worker
from harbor_bench.stats import LatencyRecorder


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


async def _run_workers(coro_factory, concurrency: int, **kwargs) -> LatencyRecorder:
    recorder = LatencyRecorder()
    stop_at = time.monotonic() + kwargs.pop("duration")
    kwargs["stop_at"] = stop_at
    kwargs["recorder"] = recorder

    async def one() -> None:
        await coro_factory(**kwargs)

    tasks = [asyncio.create_task(one()) for _ in range(concurrency)]
    await asyncio.gather(*tasks)
    return recorder


async def amain(args: argparse.Namespace) -> dict:
    duration = float(args.duration)
    conc = int(args.concurrency)
    verify = not args.insecure

    results: dict = {"duration_s": duration, "concurrency": conc, "scenarios": {}}

    if "core" in args.scenarios:
        rec = await _run_workers(
            run_core_worker,
            conc,
            duration=duration,
            base_url=args.url,
            user=args.user,
            password=args.password,
            project=args.project,
            repository=args.repository,
            verify_ssl=verify,
            rps=args.rps,
        )
        results["scenarios"]["core"] = rec.report(duration)

    if "registry-manifest" in args.scenarios:
        rec = await _run_workers(
            run_registry_manifest_worker,
            conc,
            duration=duration,
            base_url=args.url,
            registry_host=args.registry_url,
            user=args.user,
            password=args.password,
            repository=f"{args.project}/{args.repository}",
            tag=args.tag,
            verify_ssl=verify,
            rps=args.rps,
            do_get_manifest=args.registry_get_manifest,
        )
        results["scenarios"]["registry-manifest"] = rec.report(duration)

    if "registry-blob" in args.scenarios:
        rec = await _run_workers(
            run_registry_blob_worker,
            conc,
            duration=duration,
            base_url=args.url,
            registry_host=args.registry_url,
            user=args.user,
            password=args.password,
            repository=f"{args.project}/{args.repository}",
            tag=args.tag,
            blob_digest=args.blob_digest,
            verify_ssl=verify,
            rps=args.rps,
            max_blob_bytes=int(args.blob_read_bytes),
        )
        results["scenarios"]["registry-blob"] = rec.report(duration)

    if "charts" in args.scenarios:
        rec = await _run_workers(
            run_charts_worker,
            conc,
            duration=duration,
            base_url=args.url,
            user=args.user,
            password=args.password,
            chart_project=args.chart_project,
            verify_ssl=verify,
            rps=args.rps,
            chart_filename=args.chart_filename,
        )
        results["scenarios"]["charts"] = rec.report(duration)

    return results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Harbor 压测：Core API / Registry manifest&blob / ChartMuseum（异步 httpx）",
    )
    p.add_argument(
        "--url",
        default=_env("HARBOR_URL", "https://localhost"),
        help="Harbor 外部访问 URL（环境变量 HARBOR_URL）",
    )
    p.add_argument("--user", default=_env("HARBOR_USER", "admin"), help="HARBOR_USER")
    p.add_argument(
        "--password",
        default=_env("HARBOR_PASSWORD"),
        required=False,
        help="HARBOR_PASSWORD（未设置则必须显式传入）",
    )
    p.add_argument("--project", default=_env("HARBOR_PROJECT", "library"), help="项目名")
    p.add_argument(
        "--repository",
        default=_env("HARBOR_REPOSITORY", "hello-world"),
        help="仓库名（不含 project 前缀）",
    )
    p.add_argument("--tag", default=_env("HARBOR_TAG", "latest"), help="镜像 tag 或 digest")
    p.add_argument(
        "--registry-url",
        default=_env("HARBOR_REGISTRY_URL"),
        help="Registry 根 URL，默认与 --url 相同（ingress 分离时填 registry 域名）",
    )
    p.add_argument(
        "--blob-digest",
        default=_env("HARBOR_BLOB_DIGEST"),
        help="registry-blob 场景固定拉取的 blob digest；不填则从 manifest 自动解析一层",
    )
    p.add_argument(
        "--blob-read-bytes",
        type=int,
        default=int(_env("HARBOR_BLOB_READ_BYTES", "1048576") or "1048576"),
        help="每个 blob 请求最多读取字节数，控制带宽",
    )
    p.add_argument(
        "--chart-project",
        default=_env("HARBOR_CHART_PROJECT", "library"),
        help="Helm chart 所在 Harbor 项目",
    )
    p.add_argument(
        "--chart-filename",
        default=_env("HARBOR_CHART_FILENAME"),
        help="可选：chart tgz 文件名，如 myapp-1.0.0.tgz",
    )
    p.add_argument(
        "--scenarios",
        default=_env("HARBOR_SCENARIOS", "core,registry-manifest"),
        help="逗号分隔：core,registry-manifest,registry-blob,charts",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=float(_env("HARBOR_DURATION", "30") or "30"),
        help=(
            "每个场景的压测时长（秒，可小数）。到点后各场景 worker 停止循环；"
            "输出中的 QPS = 该场景 requests_total / duration（见 README）。"
        ),
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=int(_env("HARBOR_CONCURRENCY", "20") or "20"),
        help=(
            "每个场景同时启动的并发协程数（各场景独立一组，互不影响）。"
            "表示「同时有多少个 worker 在跑」，不是「每秒请求数」；"
            "实际 QPS 由服务端响应、网络与可选 --rps 限速共同决定。"
        ),
    )
    p.add_argument(
        "--rps",
        type=float,
        default=float(_env("HARBOR_RPS", "0") or "0"),
        help="每并发内限速（0 表示不限速，全速循环）",
    )
    p.add_argument(
        "--registry-get-manifest",
        action="store_true",
        help="registry-manifest 场景除 HEAD 外再 GET 完整 manifest",
    )
    p.add_argument("--insecure", action="store_true", help="跳过 TLS 校验")
    p.add_argument("--json", action="store_true", help="仅输出 JSON 结果")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.password:
        parser.error("请设置 --password 或环境变量 HARBOR_PASSWORD")

    args.scenarios = {s.strip() for s in args.scenarios.split(",") if s.strip()}
    valid = {"core", "registry-manifest", "registry-blob", "charts"}
    bad = args.scenarios - valid
    if bad:
        parser.error(f"未知场景: {bad}，可选: {valid}")

    out = asyncio.run(amain(args))

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print("=== Harbor 压测结果 ===")
    print(f"时长: {out['duration_s']}s  并发: {out['concurrency']}")
    for name, rep in out["scenarios"].items():
        print(f"\n--- {name} ---")
        print(f"  请求: {rep['requests_total']}  成功: {rep['success']}  失败: {rep['errors']}")
        print(f"  错误率: {rep['error_rate_pct']}%  QPS: {rep['qps']}")
        lat = rep["latency_ms"]
        print(
            f"  延迟(ms) min/mean/p50/p95/p99/max: "
            f"{lat['min']}/{lat['mean']}/{lat['p50']}/{lat['p95']}/{lat['p99']}/{lat['max']}"
        )
    print(
        "\n说明: CPU/内存/连接数/DB/Redis 请结合 Prometheus（harbor exporter）或 "
        "kubelet / node 监控对照文档中的观察指标。"
    )


if __name__ == "__main__":
    main()
