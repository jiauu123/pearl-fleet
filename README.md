# Pearl Fleet

Pearl Fleet is a single container image and config-driven runner for Pearlhash
mining workers across rental GPU platforms such as RunPod, Vast, Clore, Nosana,
Salad, ordinary VPS hosts, and future platforms.

The image does not bundle miner binaries or CUDA runtime. It bundles a small
runtime layer, SSH, ttyd, platform detection, remote config loading, miner
download/extraction, a log watchdog, and optional heartbeat reporting.

## Images

Recommended no-CUDA image:

```text
tdklyx/pearl-fleet:v1
ghcr.io/selkk-lab/pearl-fleet:v1
```

The image defaults to this public bootstrap:

```text
https://raw.githubusercontent.com/selkk-lab/pearl-fleet/main/config/bootstrap.env
```

You can override it with your own VPS or domain:

```env
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
```

## Quick Start

Set at least a wallet address. The public GitHub config intentionally does not
contain a wallet.

```env
MINER_WALLET=prl1replace_with_your_wallet
```

Useful management env:

```env
FLEET_ACCESS_PASSWORD=change-me
WEB_TERMINAL_PASSWORD=change-me
```

If the platform supports custom image/env, use:

```text
Image: tdklyx/pearl-fleet:v1
Ports: 22/tcp, 8888/tcp
```

The container starts SSH on port 22 and ttyd on port 8888 when passwords are
provided.

## Manual Platform Template

Use this section when a rental platform asks you to create a template manually.

### RunPod

Create a new template with:

```text
Template name: pearl-fleet-v1
Container image: tdklyx/pearl-fleet:v1
Container disk: 10 GB or more
Volume: optional, 20 GB recommended
Volume mount path: /workspace
Expose ports: 22/tcp, 8888/tcp
Docker entrypoint: leave default
Docker command: empty
```

Do not set port 8888 as HTTP-only if the platform offers protocol choices.
Use `8888/tcp` for ttyd.

Environment:

```env
MINER_WALLET=prl1replace_with_your_wallet
PEARL_BOOTSTRAP_URL=https://raw.githubusercontent.com/selkk-lab/pearl-fleet/main/config/bootstrap.env
FLEET_ACCESS_PASSWORD=change-me
WEB_TERMINAL_PASSWORD=change-me
WEB_TERMINAL_USER=admin
SSH_ENABLED=1
WEB_TERMINAL_ENABLED=1
```

If you use your own VPS config server, replace only:

```env
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
```

If a platform ignores the image entrypoint, explicitly set:

```text
/usr/bin/tini -- /usr/local/bin/fleet_entrypoint.sh
```

### Generic Rental Platforms

For Vast, Clore, Nosana, Salad, and similar platforms, use the same image and
env model:

```text
Image: tdklyx/pearl-fleet:v1
Ports: 22/tcp, 8888/tcp
Entrypoint: default, or /usr/bin/tini -- /usr/local/bin/fleet_entrypoint.sh
Command: empty
Persistent volume: mount to /workspace if available
```

Minimum env:

```env
MINER_WALLET=prl1replace_with_your_wallet
```

Recommended env:

```env
MINER_WALLET=prl1replace_with_your_wallet
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
FLEET_ACCESS_PASSWORD=change-me
WEB_TERMINAL_PASSWORD=change-me
SSH_ENABLED=1
WEB_TERMINAL_ENABLED=1
```

Optional profile override:

```env
MINER_PROFILE=luckypool
```

Supported profile names are listed in the Miner Profiles section below.

### Plain Docker

Run directly on a Docker host with NVIDIA Container Toolkit:

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

Then connect:

```text
SSH: root / FLEET_ACCESS_PASSWORD on host port 2222
ttyd: http://host:8888 with WEB_TERMINAL_USER / WEB_TERMINAL_PASSWORD
```

## Private VPS Config Server

Use a private VPS when you want immediate config control, per-worker overrides,
or heartbeat collection. A minimal public static layout:

```text
/pearl-fleet/bootstrap.env
/pearl-fleet/fleet.env
/pearl-fleet/miners.json
/pearl-fleet/platforms.json
/pearl-fleet/targets/
```

Example `bootstrap.env`:

```env
PEARL_CONFIG_URL=https://your-domain.example/fleet.env
MINER_REGISTRY_URL=https://your-domain.example/miners.json
PLATFORM_REGISTRY_URL=https://your-domain.example/platforms.json
FLEET_TARGETS_BASE_URL=https://your-domain.example/targets
HEARTBEAT_URL=https://your-domain.example/api/heartbeat
```

Example Caddy config:

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

Then point workers at the VPS:

```env
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
MINER_WALLET=prl1replace_with_your_wallet
```

If you later move VPS, keep the same domain and file paths. DNS can point to
the new machine without changing worker env.

## Config Files

`config/bootstrap.env` points workers at mutable config files.

`config/fleet.env` selects the global default profile, worker prefix, sync
interval, and watchdog behavior. Do not put secrets in public copies.

`config/miners.json` defines miner profiles, download URLs, command arguments,
hashrate regexes, share regexes, and error regexes.

`config/platforms.json` defines platform detection and worker naming templates.

`targets/<worker_name>.env` or `targets/<instance_id>.env` can override one
worker on a private VPS. If the target file returns 404 or is deleted, the
worker returns to global config on the next sync.

Default sync interval:

```env
CONFIG_SYNC_ENABLED=1
CONFIG_POLL_SECONDS=300
CONFIG_POLL_JITTER_SECONDS=30
```

## Miner Profiles

Current profiles:

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

Add a new pool by adding or changing profile env/args in `miners.json`.

Add a new miner by adding a profile with:

- `url`
- `archive`
- `binary`
- `args`
- `watchdog`

You usually do not need to rebuild the image. Rebuild only when the miner needs
a missing system library, a new archive format, a new runtime, or a runner
feature that does not exist yet.

## Heartbeat

Heartbeat is disabled by default in public config. It is enabled automatically
when `HEARTBEAT_URL` is set.

The receiver stores:

```text
/var/lib/pearl-fleet/heartbeats/latest.json
/var/lib/pearl-fleet/heartbeats/workers/<worker>.json
/var/lib/pearl-fleet/heartbeats/events/YYYYMMDD.jsonl
```

Run receiver behind Caddy:

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

Useful endpoints:

```text
GET /api/health
GET /api/workers
GET /api/workers/<worker_name>
POST /api/heartbeat
```

## Local Dry Run

Dry run validates config without starting a miner:

```bash
docker run --rm \
  -e MINER_DRY_RUN=1 \
  -e MINER_WALLET=prl1replace_with_your_wallet \
  tdklyx/pearl-fleet:v1
```

Use a private VPS config:

```bash
docker run --rm \
  -e MINER_DRY_RUN=1 \
  -e MINER_WALLET=prl1replace_with_your_wallet \
  -e PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env \
  tdklyx/pearl-fleet:v1
```

Build from source:

```bash
docker build -f fleet/Dockerfile.nocuda -t tdklyx/pearl-fleet:v1 fleet
```

## Security Notes

Do not publish platform API keys, SSH passwords, ttyd passwords, heartbeat
tokens, or private operational domains in public GitHub config.

Wallet addresses are not secret in the same way as API keys, but the public
default config still leaves `MINER_WALLET` empty so users do not accidentally
mine to someone else's wallet.

Public config URLs are visible to workers and platform operators. Use your own
HTTPS VPS/domain for private rollout control, per-worker overrides, and
heartbeat.
