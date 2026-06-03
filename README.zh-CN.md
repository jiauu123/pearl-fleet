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
    header Cache-Control "no-store"
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
