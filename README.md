# Harbor Benchmark Tool

面向 [Harbor](https://goharbor.io/) 的 HTTP 压测工具，使用 **Python 3** 与 **httpx** 异步客户端，覆盖 **Core API**、**Registry（manifest / blob）** 与 **ChartMuseum** 等典型读路径，输出 QPS、延迟分位数与错误率，便于与监控（Prometheus、kubelet 等）对照做容量与基线评估。

## 功能概览

| 场景 | 说明 |
|------|------|
| `core` | 串联调用：`GET /api/v2.0/projects` → 指定项目的仓库列表 → 指定仓库的 artifact 列表（模拟控制台/API 元数据读路径） |
| `registry-manifest` | Registry V2：`HEAD`（可选 `GET`）`/v2/<project>/<repo>/manifests/<tag>`，含 JWT 鉴权 |
| `registry-blob` | 解析 manifest（支持 **multi-arch**：先取 index 再取子 manifest），对某一 layer/config **流式下载**，可通过 `--blob-read-bytes` 限制读取量以控制带宽 |
| `charts` | `GET /chartrepo/<project>/index.yaml`，可选下载指定 `charts/<文件名>.tgz` |

以下能力**不在本工具范围内**（需配合外部手段或自行扩展）：

- **Push** 全链路（建议用 `crane` / `oras` 等配合脚本）
- **JobService** 异步任务队列（复制、扫描等）的专用 API 压测
- **PostgreSQL / Redis** 直连压测（建议使用 `pgbench`、`redis-benchmark` 或与业务窗口对齐观测）

## 环境要求

- Python **3.9+**（推荐 3.10 及以上）
- 能访问 Harbor 外部 URL；账号需对目标项目/仓库有相应 **pull**（及 Chart 拉取）权限

## 安装

```bash
cd harbor-benchmark-tool
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

在项目根目录执行（将 `harbor_bench` 所在目录加入 `PYTHONPATH`，或直接在项目根运行）：

```bash
cd harbor-benchmark-tool
python3 -m harbor_bench --help
```

## 快速开始

```bash
export HARBOR_URL="https://harbor.example.com"
export HARBOR_USER="admin"
export HARBOR_PASSWORD="your-password"

# 默认场景：core + registry-manifest，约 30s、并发 20
python3 -m harbor_bench

# 自签名证书
python3 -m harbor_bench --insecure

# 指定镜像与场景
python3 -m harbor_bench \
  --project library \
  --repository nginx \
  --tag latest \
  --scenarios core,registry-manifest,registry-blob

# 机器可读输出（JSON）
python3 -m harbor_bench --json --duration 60 --concurrency 50
```

### Registry 与 Harbor 入口分离

当镜像拉取走独立域名（例如 `registry.example.com`）时：

```bash
python3 -m harbor_bench \
  --url https://harbor.example.com \
  --registry-url https://registry.example.com \
  --scenarios registry-manifest
```

JWT 仍通过 Harbor 的 `/service/token` 获取（`--url` 指向 Harbor 门户地址）。

### ChartMuseum 场景

```bash
export HARBOR_CHART_PROJECT="library"
export HARBOR_CHART_FILENAME="myapp-1.0.0.tgz"

python3 -m harbor_bench --scenarios charts --chart-project library --chart-filename myapp-1.0.0.tgz
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--url` | Harbor 外部访问根 URL（默认 `https://localhost`） |
| `--user` / `--password` | 基本认证用户与密码；密码也可仅用环境变量 |
| `--project` | 项目名（Core / Registry 仓库路径为 `project/repo`） |
| `--repository` | 仓库名，**不含** project 前缀 |
| `--tag` | 镜像 tag 或 digest |
| `--registry-url` | Registry 根 URL；不填则与 `--url` 相同 |
| `--blob-digest` | `registry-blob` 固定拉取的 blob digest；不填则从 manifest 自动解析一层 |
| `--blob-read-bytes` | 每个 blob 请求最多读取的字节数（默认 `1048576`），用于省带宽 |
| `--chart-project` | Helm chart 所在 Harbor 项目 |
| `--chart-filename` | 可选，chart 包文件名，如 `myapp-1.0.0.tgz` |
| `--scenarios` | 逗号分隔：`core`、`registry-manifest`、`registry-blob`、`charts` |
| `--duration` | 压测时长（秒，浮点） |
| `--concurrency` | 并发协程数（每个场景独立一组并发） |
| `--rps` | 每个并发内的请求限速；`0` 表示不限速 |
| `--registry-get-manifest` | `registry-manifest` 在 `HEAD` 之外再 `GET` 完整 manifest |
| `--insecure` | 跳过 TLS 证书校验 |
| `--json` | 仅打印 JSON 结果到标准输出 |

查看全部帮助：

```bash
python3 -m harbor_bench --help
```

## 环境变量

下列变量与多数命令行参数一一对应；**命令行优先于环境变量**（实现见 `harbor_bench/main.py`）。

| 变量 | 对应配置 |
|------|-----------|
| `HARBOR_URL` | `--url` |
| `HARBOR_USER` | `--user` |
| `HARBOR_PASSWORD` | `--password` |
| `HARBOR_PROJECT` | `--project` |
| `HARBOR_REPOSITORY` | `--repository` |
| `HARBOR_TAG` | `--tag` |
| `HARBOR_REGISTRY_URL` | `--registry-url` |
| `HARBOR_BLOB_DIGEST` | `--blob-digest` |
| `HARBOR_BLOB_READ_BYTES` | `--blob-read-bytes` |
| `HARBOR_CHART_PROJECT` | `--chart-project` |
| `HARBOR_CHART_FILENAME` | `--chart-filename` |
| `HARBOR_SCENARIOS` | `--scenarios` |
| `HARBOR_DURATION` | `--duration` |
| `HARBOR_CONCURRENCY` | `--concurrency` |
| `HARBOR_RPS` | `--rps` |

## 输出说明

### 文本模式（默认）

每个场景输出：

- 总请求数、成功数、失败数  
- 错误率（%）、QPS  
- 延迟（毫秒）：min、mean、p50、p95、p99、max  

### JSON 模式（`--json`）

顶层字段示例：

- `duration_s`：压测时长  
- `concurrency`：并发数  
- `scenarios`：各场景名 → `requests_total`、`success`、`errors`、`error_rate_pct`、`qps`、`latency_ms`（含 `min` / `mean` / `p50` / `p95` / `p99` / `max`）  

便于接入 CI、报表或二次分析。

## 与监控指标对照

客户端侧本工具只统计 **HTTP 延迟与错误**。文档中常见的 **Core CPU/内存、Registry 吞吐、DB 连接与慢查询、Redis 延迟与内存** 等，请在压测同一时间窗口内通过例如：

- Kubernetes / Prometheus（Harbor 与中间件 exporter）  
- 节点与 Pod 资源使用率  
- PostgreSQL、Redis 监控面板  

进行关联分析。

## 项目结构

```
harbor-benchmark-tool/
├── README.md
├── requirements.txt
└── harbor_bench/
    ├── __init__.py
    ├── __main__.py          # python -m harbor_bench
    ├── main.py              # CLI 入口与场景编排
    ├── auth.py              # Basic Auth、Registry JWT
    ├── stats.py             # 延迟记录与分位数
    └── scenarios/
        ├── core_api.py
        ├── registry.py
        └── charts.py
```

## 常见问题

**1. 报错 401 / 403**  
检查用户密码、机器人账户权限，以及项目是否对 `pull` / chart 读开放。

**2. `registry-blob` 失败或 404**  
确认 `--project` / `--repository` / `--tag` 在 Registry 路径中为 `project/repo`；multi-arch 镜像已做子 manifest 解析，若仍失败可显式传入 `--blob-digest`。

**3. `core` 中 artifact 接口失败**  
若仓库不存在会得到非 2xx，属预期；请换成真实存在的 `project` 与 `repository`。

## 许可证

未随仓库指定默认许可证时，请以你所在组织的合规要求为准；如需开源发布，请自行补充 `LICENSE` 文件。
