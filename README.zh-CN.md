# Pearl Fleet

Pearl Fleet 是一个用于 Pearlhash 挖矿的通用镜像和远程配置 runner。目标是
一个镜像同时适配 RunPod、Vast、Clore、Nosana、Salad、普通 VPS，以及后续新增
的租机平台。

镜像不打包矿工二进制，也不打包完整 CUDA runtime。镜像只打包轻量运行环境、
SSH、ttyd、平台识别、远程 env 同步、矿工下载/解压、日志 watchdog 和可选
heartbeat。

## 镜像地址

推荐 no-CUDA 镜像：

```text
tdklyx/pearl-fleet:v1
ghcr.io/selkk-lab/pearl-fleet:v1
```

镜像默认读取这个 public GitHub bootstrap：

```text
https://raw.githubusercontent.com/selkk-lab/pearl-fleet/main/config/bootstrap.env
```

如果你有自己的 VPS 或域名，直接覆盖：

```env
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
```

这样后续切矿池、切矿工、灰度测试、单机测试，都可以改 VPS 配置，不需要改镜像。

## 快速使用

至少需要设置钱包地址。public GitHub 配置故意不包含钱包。

```env
MINER_WALLET=prl1replace_with_your_wallet
```

管理密码：

```env
FLEET_ACCESS_PASSWORD=change-me
WEB_TERMINAL_PASSWORD=change-me
```

平台模板里使用：

```text
Image: tdklyx/pearl-fleet:v1
Ports: 22/tcp, 8888/tcp
```

容器会在有密码时启动 SSH 22 和 ttyd 8888。

## 手动创建平台模板

当平台需要你手动创建 template 时，按这一节填。

### RunPod

创建新 template：

```text
Template name: pearl-fleet-v1
Container image: tdklyx/pearl-fleet:v1
Container disk: 10 GB 或更大
Volume: 可选，推荐 20 GB
Volume mount path: /workspace
Expose ports: 22/tcp, 8888/tcp
Docker entrypoint: 留空，使用镜像默认
Docker command: 留空
```

如果平台让你选择端口协议，`8888` 要填 `8888/tcp`，不要填成 HTTP-only。ttyd
走的是 TCP 暴露。

环境变量：

```env
MINER_WALLET=prl1replace_with_your_wallet
PEARL_BOOTSTRAP_URL=https://raw.githubusercontent.com/selkk-lab/pearl-fleet/main/config/bootstrap.env
FLEET_ACCESS_PASSWORD=change-me
WEB_TERMINAL_PASSWORD=change-me
WEB_TERMINAL_USER=admin
SSH_ENABLED=1
WEB_TERMINAL_ENABLED=1
```

如果你用自己的 VPS 配置服务，只替换：

```env
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
```

如果某个平台不执行镜像默认 entrypoint，就显式填：

```text
/usr/bin/tini -- /usr/local/bin/fleet_entrypoint.sh
```

### 通用租机平台

Vast、Clore、Nosana、Salad 以及类似平台，都按同一个模型：

```text
Image: tdklyx/pearl-fleet:v1
Ports: 22/tcp, 8888/tcp
Entrypoint: 默认，或者 /usr/bin/tini -- /usr/local/bin/fleet_entrypoint.sh
Command: 留空
Persistent volume: 如果平台支持，挂载到 /workspace
```

最小环境变量：

```env
MINER_WALLET=prl1replace_with_your_wallet
```

推荐环境变量：

```env
MINER_WALLET=prl1replace_with_your_wallet
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
FLEET_ACCESS_PASSWORD=change-me
WEB_TERMINAL_PASSWORD=change-me
SSH_ENABLED=1
WEB_TERMINAL_ENABLED=1
```

临时指定矿工 profile：

```env
MINER_PROFILE=luckypool
```

支持的 profile 名称看下面“当前矿工 profile”。

### 普通 Docker 主机

在已经安装 NVIDIA Container Toolkit 的 Docker 主机上直接运行：

```bash
docker run -d --name pearl-fleet \
  --gpus all \
  --restart unless-stopped \
  -p 2222:22 \
  -p 8888:8888 \
  -v pearl-fleet-workspace:/workspace \
  -e MINER_WALLET=prl1replace_with_your_wallet \
  -e PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env \
  -e FLEET_ACCESS_PASSWORD=change-me \
  -e WEB_TERMINAL_PASSWORD=change-me \
  tdklyx/pearl-fleet:v1
```

连接方式：

```text
SSH: root / FLEET_ACCESS_PASSWORD，主机端口 2222
ttyd: http://host:8888，使用 WEB_TERMINAL_USER / WEB_TERMINAL_PASSWORD
```

## 搭建自己的 VPS 配置服务

如果你需要实时控制配置、单台机器 override、heartbeat，就用自己的 VPS。推荐
目录：

```text
/pearl-fleet/bootstrap.env
/pearl-fleet/fleet.env
/pearl-fleet/miners.json
/pearl-fleet/platforms.json
/pearl-fleet/targets/
```

`bootstrap.env` 示例：

```env
PEARL_CONFIG_URL=https://your-domain.example/fleet.env
MINER_REGISTRY_URL=https://your-domain.example/miners.json
PLATFORM_REGISTRY_URL=https://your-domain.example/platforms.json
FLEET_TARGETS_BASE_URL=https://your-domain.example/targets
HEARTBEAT_URL=https://your-domain.example/api/heartbeat
```

Caddy 示例：

```caddyfile
your-domain.example {
  handle /api/* {
    reverse_proxy 127.0.0.1:8787
  }

  handle {
    root * /pearl-fleet
    header Cache-Control "no-cache"
    file_server
  }
}
```

机器环境变量指向你的 VPS：

```env
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
MINER_WALLET=prl1replace_with_your_wallet
```

后续如果换 VPS，只要 DNS 解析到新机器，并保持路径不变，已租机器不需要改环境变量。

## 配置文件说明

`config/bootstrap.env`：告诉 worker 去哪里拉全局配置、矿工 registry、平台
registry。

`config/fleet.env`：全局默认配置，比如默认矿工 profile、worker 前缀、同步间隔、
watchdog 开关。public 文件里不要写钱包、密码、token。

`config/miners.json`：矿工 profile。这里配置下载地址、解压方式、二进制名、启动
参数、算力正则、accepted/rejected 正则、错误正则。

`config/platforms.json`：平台识别和 worker 命名规则。

`targets/<worker_name>.env` 或 `targets/<instance_id>.env`：私有 VPS 上的单机
覆盖配置。删除这个文件或返回 404 后，机器会在下一次同步回到全局配置。

默认同步间隔：

```env
CONFIG_SYNC_ENABLED=1
CONFIG_POLL_SECONDS=300
CONFIG_POLL_JITTER_SECONDS=30
```

也就是大约 5 分钟同步一次。

miner 启动后，配置同步在后台线程里执行，不会阻塞 miner 日志解析、heartbeat 或
watchdog 检查。runner 会优先使用服务端的 `ETag` / `Last-Modified`，发送
`If-None-Match` / `If-Modified-Since`；如果服务端返回 304，就不下载完整文件。
如果服务端不支持条件请求，runner 会下载后做本地内容 hash，只有最终配置
fingerprint 变化才重启矿工。

如果你的 VPS 是简单静态文件服务，也可以维护可选 `.sha256` 文件来避免完整下载：

```bash
cd /pearl-fleet
sha256sum bootstrap.env | awk '{print $1}' > bootstrap.env.sha256
sha256sum fleet.env | awk '{print $1}' > fleet.env.sha256
sha256sum miners.json | awk '{print $1}' > miners.json.sha256
sha256sum platforms.json | awk '{print $1}' > platforms.json.sha256
```

然后启用：

```env
CONFIG_HASH_CHECK_ENABLED=1
CONFIG_HASH_URL_SUFFIX=.sha256
```

## 环境变量完整说明

### 必填和基础挖矿配置

| 变量 | 默认值 | 用法 |
| --- | --- | --- |
| `MINER_WALLET` | 空 | 必填钱包地址。public 配置故意不设置。 |
| `MINER_USER` | 空 | `MINER_WALLET` 的旧别名。 |
| `MINER_PROFILE` | `pearlpool` | 矿工/矿池 profile。支持值见下方 profile 列表。 |
| `WORKER_PREFIX` | `auto` | worker 名字前缀。`auto` 表示按平台规则自动选择。 |

### 矿池和矿工覆盖

| 变量 | 适用 profile | 用法 |
| --- | --- | --- |
| `PEARLHASH_POOL_HOST` | `pearlpool` | pearlpool 风格矿工的 host:port，例如 `84.32.220.219:9000`。 |
| `MINER_POOL_URL` | `luckypool`, `alphapool`, `srbminer`, `srbminer-herominers` | 矿池地址。Luckypool/Alphapool 使用 `stratum+tcp://...`；SRBMiner profile 使用纯 `host:port`。 |
| `SRB_API_PORT` | SRBMiner profiles | SRBMiner 本地 API 端口。 |
| `ALPHA_DIFFICULTY` | `alphapool` | 静态 difficulty，用于渲染 `ALPHA_PASSWORD`。 |
| `ALPHA_PASSWORD` | `alphapool` | 矿池 password，默认渲染为 `x;d=${ALPHA_DIFFICULTY}`。 |
| `PEARLFORTUNE_PROXY` | `pearlfortune` | Pearlfortune proxy，例如 `jp.pearlfortune.org:443`。 |

示例：

```env
# Pearlpool 亚洲节点
MINER_PROFILE=pearlpool
PEARLHASH_POOL_HOST=84.32.220.219:9000

# SRBMiner + Luckypool
MINER_PROFILE=srbminer
MINER_POOL_URL=pearl-eu2.luckypool.io:3360

# SRBMiner + Herominers 亚洲节点
MINER_PROFILE=srbminer-herominers
MINER_POOL_URL=kr.pearl.herominers.com:1200
```

常用 Herominers 节点：

```text
de.pearl.herominers.com:1200
fr.pearl.herominers.com:1200
es.pearl.herominers.com:1200
fi.pearl.herominers.com:1200
ru.pearl.herominers.com:1200
ca.pearl.herominers.com:1200
us.pearl.herominers.com:1200
us2.pearl.herominers.com:1200
us3.pearl.herominers.com:1200
br.pearl.herominers.com:1200
hk.pearl.herominers.com:1200
kr.pearl.herominers.com:1200
sg.pearl.herominers.com:1200
tr.pearl.herominers.com:1200
au.pearl.herominers.com:1200
```

### 远程配置源

| 变量 | 默认值 | 用法 |
| --- | --- | --- |
| `PEARL_BOOTSTRAP_URL` | GitHub Raw public bootstrap | 第一层远程 env。改成自己的 VPS/域名即可完全接管配置。 |
| `PEARL_CONFIG_URL` | 来自 bootstrap | 全局 `fleet.env` URL。 |
| `MINER_CONFIG_URL` | 空 | `PEARL_CONFIG_URL` 的旧别名。 |
| `MINER_REGISTRY_URL` | 来自 bootstrap | 远程 `miners.json`。 |
| `PLATFORM_REGISTRY_URL` | 空 | 远程 `platforms.json`。 |
| `FLEET_TARGETS_BASE_URL` | 空 | 单机 override 目录，例如 `targets/<worker>.env`。 |
| `FLEET_TARGET_CONFIG_URL` | 空 | 精确的单机 override URL 模板，支持 `${WORKER_NAME}` 等变量。 |
| `DISABLE_REMOTE_CONFIG` | `0` | 设为 `1` 后只使用本地文件和平台 env。 |

### 同步和 HTTP 缓存

| 变量 | 默认值 | 用法 |
| --- | --- | --- |
| `CONFIG_SYNC_ENABLED` | `1` | miner 启动后的后台配置同步。 |
| `CONFIG_POLL_SECONDS` | `300` | 同步基础间隔。 |
| `CONFIG_POLL_JITTER_SECONDS` | `30` | 随机抖动范围。当前行为是 `base +/- jitter`。 |
| `CONFIG_HTTP_TIMEOUT_SECONDS` | `20` | 每个远程配置请求的超时时间。 |
| `CONFIG_HTTP_CONDITIONAL_REQUESTS` | `1` | 有缓存头时使用 `If-None-Match` / `If-Modified-Since`。 |
| `CONFIG_HASH_CHECK_ENABLED` | `0` | 下载完整文件前先检查可选 hash sidecar。 |
| `CONFIG_HASH_URL_SUFFIX` | `.sha256` | hash sidecar 后缀。 |
| `CONFIG_HTTP_CACHE_BUST` | `0` | 添加 `_pearl_min` cache-busting query。启用条件请求时通常不要打开。 |

### 本地文件和缓存

| 变量 | 默认值 | 用法 |
| --- | --- | --- |
| `FLEET_LOCAL_ENV_FILES` | `/workspace/fleet.env /runpod-volume/fleet.env /app/fleet.env` | 远程全局配置后加载的本地 env。 |
| `FLEET_OVERRIDE_FILES` | `/workspace/fleet.override.env /runpod-volume/fleet.override.env /app/fleet.override.env` | 最高优先级本地 override。 |
| `FLEET_LOCAL_REGISTRY_FILES` | `/workspace/miners.json /runpod-volume/miners.json /app/miners.json` | 本地 miner registry fallback。 |
| `FLEET_LOCAL_PLATFORM_FILES` | `/workspace/platforms.json /runpod-volume/platforms.json /app/platforms.json` | 本地 platform registry fallback。 |
| `FLEET_STATE_DIR` | `/app/state` | 缓存和状态目录。需要持久化就设到 `/workspace/...`。 |
| `MINER_CACHE_DIR` | `/app/miners` | 下载的矿工缓存目录。需要持久化就设到 `/workspace/...`。 |
| `ALWAYS_DOWNLOAD_MINER` | `0` | 设为 `1` 强制重新下载矿工。 |
| `MINER_DRY_RUN` | `0` | 设为 `1` 只渲染配置，不启动矿工。 |

### Watchdog

| 变量 | 默认值 | 用法 |
| --- | --- | --- |
| `WATCHDOG_ENABLED` | `1` | 启用重启逻辑。 |
| `WATCHDOG_STARTUP_GRACE` | `180` | profile 没覆盖时的默认启动宽限期。 |
| `WATCHDOG_STALE_SECONDS` | `300` | profile 没覆盖时的默认活动超时。 |
| `WATCHDOG_RESTART_DELAY` | `10` | 重启间隔。 |
| `WATCHDOG_MAX_RESTARTS` | `0` | `0` 表示无限重启。 |

profile 里的 watchdog 会覆盖这些默认值。当前 SRBMiner profile 使用
`miners.json` 选择的远程 wrapper 脚本。wrapper 负责下载 SRBMiner、启动本地
API、轮询 `http://127.0.0.1:${SRB_API_PORT}/api`，并在 API 算力和 share
计数长时间不更新时重启 SRBMiner。外层 fleet runner 只看 wrapper 输出的日志
行，所以这套行为可以通过更新 `miners.json` 和 wrapper 脚本改变，不需要重建镜像。

### SSH、ttyd 和 Heartbeat

| 变量 | 默认值 | 用法 |
| --- | --- | --- |
| `FLEET_ACCESS_PASSWORD` | 空 | SSH 和 ttyd 共用密码，如果更具体变量为空。 |
| `RUNPOD_ACCESS_PASSWORD` | 空 | RunPod 友好的访问密码别名。 |
| `SSH_PASSWORD` | 空 | SSH-only 密码 fallback。 |
| `SSH_ENABLED` | `1` | 启动 22 端口 sshd。 |
| `WEB_TERMINAL_ENABLED` | `1` | 有密码时启动 8888 端口 ttyd。 |
| `WEB_TERMINAL_USER` | `admin` | ttyd 用户名。 |
| `WEB_TERMINAL_PASSWORD` | access password | ttyd 密码。 |
| `WEB_TERMINAL_MAX_CLIENTS` | `2` | ttyd 最大连接数。 |
| `HEALTH_SERVER_ENABLED` | `1` | ttyd 禁用或不可用时提供简单健康页。 |
| `HEALTH_SERVER_PORT` | `8888` | ttyd 或健康页端口。 |
| `HEARTBEAT_ENABLED` | `auto` | `auto` 表示设置了 `HEARTBEAT_URL` 才启用。 |
| `HEARTBEAT_URL` | 空 | 接收端地址，通常是 `https://your-domain.example/api/heartbeat`。 |
| `HEARTBEAT_INTERVAL_SECONDS` | `60` | miner running 心跳间隔。 |
| `HEARTBEAT_TIMEOUT_SECONDS` | `5` | heartbeat POST 超时。 |
| `HEARTBEAT_TOKEN` | 空 | 可选 bearer token。 |

### GPU 调参

| 变量 | 默认值 | 用法 |
| --- | --- | --- |
| `GPU_PERSISTENCE_MODE` | 空 | 传给 `nvidia-smi -pm`。 |
| `GPU_POWER_LIMIT` | 空 | 传给 `nvidia-smi -pl`。 |
| `GPU_LOCK_CORE_CLOCKS` | 空 | 传给 `nvidia-smi -lgc`。单值会变成 `value,value`。 |
| `GPU_LOCK_MEMORY_CLOCKS` | 空 | 传给 `nvidia-smi -lmc`。单值会变成 `value,value`。 |

## 当前矿工 profile

```text
pearlpool
tw-pool
tw-pool-cuda12
luckypool
alphapool
srbminer
srbminer-herominers
pearlfortune
```

新增矿池：通常只需要改 `miners.json` 里的 pool 地址和启动参数。

新增矿工：通常只需要在 `miners.json` 新增 profile：

- `url`
- `archive`
- `binary`
- `args`
- `watchdog`

大多数情况下不需要更新镜像。只有这些情况才需要重建镜像：

- 新矿工缺系统动态库，比如报 `xxx.so: cannot open shared object file`
- 新矿工需要镜像里没有的系统命令
- 新矿工压缩格式 runner 不支持
- 需要新的 watchdog 逻辑
- 需要打包额外 runtime

## Heartbeat

public 配置默认不启用 heartbeat。只要设置了 `HEARTBEAT_URL`，runner 会自动启用。

接收端会保存：

```text
/var/lib/pearl-fleet/heartbeats/latest.json
/var/lib/pearl-fleet/heartbeats/workers/<worker>.json
/var/lib/pearl-fleet/heartbeats/events/YYYYMMDD.jsonl
```

systemd 服务示例：

```bash
sudo mkdir -p /opt/pearl-fleet /var/lib/pearl-fleet
sudo cp fleet/heartbeat_receiver.py /opt/pearl-fleet/heartbeat_receiver.py

sudo tee /etc/systemd/system/pearl-fleet-heartbeat.service >/dev/null <<'EOF'
[Unit]
Description=Pearl fleet heartbeat receiver
After=network-online.target

[Service]
Environment=HEARTBEAT_BIND=127.0.0.1
Environment=HEARTBEAT_PORT=8787
Environment=HEARTBEAT_DATA_DIR=/var/lib/pearl-fleet
ExecStart=/usr/bin/python3 /opt/pearl-fleet/heartbeat_receiver.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now pearl-fleet-heartbeat
```

接口：

```text
GET /api/health
GET /api/workers
GET /api/workers/<worker_name>
POST /api/heartbeat
```

heartbeat 会包含 worker、平台、profile、GPU、配置版本、算力、accepted/rejected、
事件状态等信息。

## 本地 dry-run

不启动矿工，只验证配置：

```bash
docker run --rm \
  -e MINER_DRY_RUN=1 \
  -e MINER_WALLET=prl1replace_with_your_wallet \
  tdklyx/pearl-fleet:v1
```

使用自己的 VPS：

```bash
docker run --rm \
  -e MINER_DRY_RUN=1 \
  -e MINER_WALLET=prl1replace_with_your_wallet \
  -e PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env \
  tdklyx/pearl-fleet:v1
```

源码构建：

```bash
docker build -f fleet/Dockerfile.nocuda -t tdklyx/pearl-fleet:v1 fleet
```

## 安全说明

不要把平台 API key、SSH 密码、ttyd 密码、heartbeat token、私有运营域名写进
public GitHub 配置。

钱包地址不是 API key 那种密钥，但 public 默认配置仍然不写 `MINER_WALLET`，避免
别人不小心挖到你的钱包。

public config URL 对 worker 和平台机主都是可见的。如果你需要私有控制、灰度、
单机测试、heartbeat，应该使用自己的 HTTPS VPS/域名。

## 更新日志

### v1.1.0 - 2026-06-04

- SRBMiner profile 改为通过 API wrapper 使用 SRBMiner-Multi 3.3.3，由
  wrapper 监控本地 API 的算力和 share 计数，再决定是否重启 SRBMiner。
- 更新 worker 命名模板：保留有用的平台完整 ID，并从 worker 名里移除 profile。
  示例：`runpod-rtx-4090-<pod_id>`、`vast-rtx-4090-<instance_id>`、
  `clore-rtx-4090-<server_id>`、`nosana-rtx-4090-<job_id>`、
  `salad-rtx-4090-<machine_id>`。
- Clore 优先使用 `CLORE_SERVER_ID` 作为机器 ID，`CLORE_MACHINE_ID` 作为兜底。
- 旧镜像迁移 helper 不进入 public release。新机器使用 Fleet 镜像；旧镜像迁移脚本
  如有需要应放在私有运维配置里维护。
