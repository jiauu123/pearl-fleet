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
tdklyx/pearl-fleet:v1.2.0
ghcr.io/selkk-lab/pearl-fleet:v1.2.0
```

Ubuntu 24.04 / glibc 2.39 variant for miners that fail with
`GLIBC_2.39 not found`:

```text
tdklyx/pearl-fleet:ubuntu24-v2
ghcr.io/selkk-lab/pearl-fleet:ubuntu24-v2
```

Historical tags `v1` and `ubuntu24-v1` do not include the v1.2.0 startup
network repair.

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
MINER_WALLET=YOUR_PRL_WALLET
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
MINER_WALLET=YOUR_PRL_WALLET
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
MINER_WALLET=YOUR_PRL_WALLET
```

Recommended env:

```env
MINER_WALLET=YOUR_PRL_WALLET
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
  -e MINER_WALLET=YOUR_PRL_WALLET \
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
    header Cache-Control "no-cache"
    file_server
  }
}
```

Then point workers at the VPS:

```env
PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env
MINER_WALLET=YOUR_PRL_WALLET
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

Config sync runs in a background thread after the miner starts. It does not
block miner stdout parsing, heartbeat, or watchdog checks. The runner uses
`If-None-Match` and `If-Modified-Since` when a server provides `ETag` or
`Last-Modified`. If a server does not support conditional requests, the runner
falls back to local content hashing and only restarts the miner when the final
config fingerprint changes.

Optional hash sidecar files can avoid full downloads on simple static VPS
hosting:

```bash
cd /pearl-fleet
sha256sum bootstrap.env | awk '{print $1}' > bootstrap.env.sha256
sha256sum fleet.env | awk '{print $1}' > fleet.env.sha256
sha256sum miners.json | awk '{print $1}' > miners.json.sha256
sha256sum platforms.json | awk '{print $1}' > platforms.json.sha256
```

Then enable sidecar checks:

```env
CONFIG_HASH_CHECK_ENABLED=1
CONFIG_HASH_URL_SUFFIX=.sha256
```

## Environment Variables

### Required Mining Settings

| Variable | Default | Use |
| --- | --- | --- |
| `MINER_WALLET` | empty | Required wallet address. Public config intentionally does not set it. |
| `MINER_USER` | empty | Legacy alias for `MINER_WALLET`. |
| `MINER_PROFILE` | `pearlpool` | Miner/pool profile. Supported values are listed below. |
| `WORKER_PREFIX` | `auto` | Worker name prefix. `auto` lets platform rules choose a prefix. |

### Pool And Miner Overrides

| Variable | Applies To | Use |
| --- | --- | --- |
| `PEARLHASH_POOL_HOST` | `pearlpool` | Host and port for pearlpool style miner, for example `84.32.220.219:9000`. |
| `MINER_POOL_URL` | `luckypool`, `alphapool`, `srbminer`, `srbminer-herominers` | Pool URL or host:port. Luckypool/Alphapool use `stratum+tcp://...`; SRBMiner profiles use plain `host:port`. |
| `SRB_API_PORT` | SRBMiner profiles | Local SRBMiner API port. |
| `ALPHA_DIFFICULTY` | `alphapool` | Static difficulty value used to render `ALPHA_PASSWORD`. |
| `ALPHA_PASSWORD` | `alphapool` | Pool password, default renders as `x;d=${ALPHA_DIFFICULTY}`. |
| `PEARLFORTUNE_PROXY` | `pearlfortune` | Pearlfortune proxy endpoint, for example `jp.pearlfortune.org:443`. |

Examples:

```env
# Pearlpool Asia node
MINER_PROFILE=pearlpool
PEARLHASH_POOL_HOST=84.32.220.219:9000

# SRBMiner on Luckypool
MINER_PROFILE=srbminer
MINER_POOL_URL=pearl-eu2.luckypool.io:3360

# SRBMiner on Herominers Asia
MINER_PROFILE=srbminer-herominers
MINER_POOL_URL=kr.pearl.herominers.com:1200
```

Useful Herominers endpoints:

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

### Remote Config

| Variable | Default | Use |
| --- | --- | --- |
| `PEARL_BOOTSTRAP_URL` | GitHub Raw public bootstrap | First remote env file. Override it to use your VPS/domain. |
| `PEARL_CONFIG_URL` | from bootstrap | Global fleet env URL. |
| `MINER_CONFIG_URL` | empty | Legacy alias for `PEARL_CONFIG_URL`. |
| `MINER_REGISTRY_URL` | from bootstrap | Remote `miners.json`. |
| `PLATFORM_REGISTRY_URL` | empty | Remote `platforms.json`. |
| `FLEET_TARGETS_BASE_URL` | empty | Base URL for per-worker files such as `targets/<worker>.env`. |
| `FLEET_TARGET_CONFIG_URL` | empty | Exact per-worker override URL template. Supports variables like `${WORKER_NAME}`. |
| `DISABLE_REMOTE_CONFIG` | `0` | Set `1` to use only local files and platform env. |

### Sync And HTTP Cache

| Variable | Default | Use |
| --- | --- | --- |
| `CONFIG_SYNC_ENABLED` | `1` | Background config sync after miner start. |
| `CONFIG_POLL_SECONDS` | `300` | Base sync interval. |
| `CONFIG_POLL_JITTER_SECONDS` | `30` | Random jitter range. Current behavior is `base +/- jitter`. |
| `CONFIG_HTTP_TIMEOUT_SECONDS` | `20` | Timeout for each remote config request. |
| `CONFIG_HTTP_CONDITIONAL_REQUESTS` | `1` | Use `If-None-Match` / `If-Modified-Since` when cached headers exist. |
| `CONFIG_HASH_CHECK_ENABLED` | `0` | Check optional hash sidecar URL before downloading full files. |
| `CONFIG_HASH_URL_SUFFIX` | `.sha256` | Suffix appended to config URLs for hash sidecars. |
| `CONFIG_HTTP_CACHE_BUST` | `0` | Add `_pearl_min` cache-busting query. Usually leave disabled when conditional requests are enabled. |
| `CONFIG_CURL_FALLBACK_ENABLED` | `1` | Use `curl/wget` when Python `urllib` cannot fetch remote config. |
| `CONFIG_CURL_IPV4_ONLY` | `1` | Force IPv4 for curl fallback to avoid broken IPv6 paths. |
| `CONFIG_CURL_RETRIES` | `3` | Retry count for config `curl/wget` fallback. |
| `CONFIG_CURL_CONNECT_TIMEOUT_SECONDS` | `10` | Connect timeout for `curl/wget` fallback. |
| `MINER_DOWNLOAD_TIMEOUT_SECONDS` | `180` | Total timeout for miner and wrapper downloads. |
| `MINER_DOWNLOAD_RETRIES` | `3` | Retry count for miner and wrapper downloads. |

### Network Diagnosis And Repair

New images run this before starting the fleet runner:

```bash
/usr/local/bin/fleet-network-fix.sh --boot --repair
```

It diagnoses DNS, TCP, HTTPS, Python urllib, and curl access, then repairs
`/etc/resolv.conf`, enables IPv4 preference, and writes logs to:

```text
/var/log/pearl-fleet-network-fix.log
```

Variables:

| Variable | Default | Use |
| --- | --- | --- |
| `FLEET_NETWORK_FIX_ENABLED` | `1` | Run network repair automatically at image startup. |
| `FLEET_DNS_SERVERS` | `1.1.1.1 8.8.8.8` | DNS servers written to `/etc/resolv.conf`. |
| `FLEET_DNS_OPTIONS` | `timeout:2 attempts:3 rotate` | Resolver options written to `/etc/resolv.conf`. |
| `FLEET_NETWORK_CHECK_HOSTS` | common config/GitHub/pool hosts | Hosts checked during diagnosis. |
| `FLEET_NETWORK_CHECK_URLS` | common config and miner URLs | URLs checked during diagnosis. |
| `FLEET_HOSTS_FALLBACKS` | empty | Optional `/etc/hosts` fallback in `host=ip host2=ip2` format. Use only when the IP is known. |

For existing machines that cannot switch image but still have SSH, upload the
script first, then run:

```bash
chmod 0755 /usr/local/bin/fleet-network-fix.sh
/usr/local/bin/fleet-network-fix.sh --install --repair --restart-runner
tail -n 120 /var/log/pearl-fleet-network-fix.log
```

`--install` installs best-effort boot persistence by patching the Fleet
entrypoint, writing `/etc/rc.local`, writing
`/etc/cron.d/pearl-fleet-network-fix`, and creating a systemd oneshot unit when
systemd is available. If the machine cannot reach your domain, do not fetch this
script with `curl`; upload it with SSH/SCP.

### Local Files And Cache

| Variable | Default | Use |
| --- | --- | --- |
| `FLEET_LOCAL_ENV_FILES` | `/workspace/fleet.env /runpod-volume/fleet.env /app/fleet.env` | Local env files loaded after remote global config. |
| `FLEET_OVERRIDE_FILES` | `/workspace/fleet.override.env /runpod-volume/fleet.override.env /app/fleet.override.env` | Highest-priority local overrides. |
| `FLEET_LOCAL_REGISTRY_FILES` | `/workspace/miners.json /runpod-volume/miners.json /app/miners.json` | Local fallback miner registries. |
| `FLEET_LOCAL_PLATFORM_FILES` | `/workspace/platforms.json /runpod-volume/platforms.json /app/platforms.json` | Local fallback platform registries. |
| `FLEET_STATE_DIR` | `/app/state` | Cache and state directory. Use `/workspace/...` for persistence. |
| `MINER_CACHE_DIR` | `/app/miners` | Downloaded miner cache directory. Use `/workspace/...` for persistence. |
| `ALWAYS_DOWNLOAD_MINER` | `0` | Set `1` to force re-download miner binaries. |
| `MINER_DRY_RUN` | `0` | Set `1` to render config and exit without starting a miner. |

### Watchdog

| Variable | Default | Use |
| --- | --- | --- |
| `WATCHDOG_ENABLED` | `1` | Enable restart logic. |
| `WATCHDOG_STARTUP_GRACE` | `180` | Default warmup if a profile does not override it. |
| `WATCHDOG_STALE_SECONDS` | `300` | Default stale activity window if a profile does not override it. |
| `WATCHDOG_RESTART_DELAY` | `10` | Delay between restart attempts. |
| `WATCHDOG_MAX_RESTARTS` | `0` | `0` means unlimited restarts. |

Profile-level watchdog values in `miners.json` override these defaults. Current
SRBMiner profiles use a remote wrapper script selected by `miners.json`. The
wrapper downloads SRBMiner, starts its local API, polls
`http://127.0.0.1:${SRB_API_PORT}/api`, and restarts SRBMiner if API hashrate
and share counters stay stale. The outer fleet runner only watches wrapper log
lines, so this behavior can be changed by updating `miners.json` and the wrapper
script without rebuilding the image.

On shutdown, the SRBMiner wrapper terminates the whole SRBMiner process group,
waits `SRB_SHUTDOWN_TERM_SECONDS`, then force-kills the process group and waits
up to `SRB_SHUTDOWN_KILL_SECONDS`. This prevents stale SRBMiner processes from
surviving profile switches.

| Variable | Default | Use |
| --- | --- | --- |
| `SRB_SHUTDOWN_TERM_SECONDS` | `8` | Time to wait after TERM before force kill. |
| `SRB_SHUTDOWN_KILL_SECONDS` | `4` | Time to wait after KILL before the wrapper exits. |

Current `tw-pool` and `tw-pool-cuda12` profiles also use a remote wrapper. It
still downloads the original tw-pool miner, but treats `window` hashrate as the
primary heartbeat hashrate and reports `avg` as an extra metric:

| Variable | Default | Use |
| --- | --- | --- |
| `TWPOOL_WRAPPER_URL` | GitHub Raw wrapper | Wrapper script URL; private fleets can point this at their own domain. |
| `TWPOOL_MINER_URL` | Set by profile | Original tw-pool miner tar.gz URL. |
| `TWPOOL_LOW_HASH_ENABLED` | `1` | Enable relative low-hashrate restart for twpool. |
| `TWPOOL_LOW_HASH_WARMUP_SECONDS` | `600` | Wait this long before establishing a baseline. |
| `TWPOOL_LOW_HASH_RATIO` | `0.55` | Count a bad sample when current `window` is below this ratio of baseline. |
| `TWPOOL_LOW_HASH_BAD_SAMPLES` | `3` | Restart the miner after this many consecutive bad samples. |
| `TWPOOL_LOW_HASH_MIN_BASELINE_TH_S` | `50` | Do not use relative restart until baseline reaches this value. |
| `TWPOOL_LOW_HASH_RESTART_COOLDOWN_SECONDS` | `900` | Minimum interval between low-hashrate restarts. |
| `TWPOOL_HEARTBEAT_INTERVAL_SECONDS` | `30` | Wrapper extended heartbeat interval. |

twpool heartbeat metrics:

- `last_hashrate_th_s` / `last_hashrate`: current `window` hashrate, used for automated acceptance and low-hashrate decisions.
- `twpool_window_hashrate_th_s`: the same `window` hashrate under an explicit field name.
- `twpool_avg_hashrate_th_s`: tw-pool `avg` hashrate, useful for debugging only.
- `twpool_low_hash_baseline_th_s`: current wrapper baseline for relative low-hashrate checks.

### Access And Heartbeat

| Variable | Default | Use |
| --- | --- | --- |
| `FLEET_ACCESS_PASSWORD` | empty | Shared password for SSH and ttyd if more specific variables are empty. |
| `RUNPOD_ACCESS_PASSWORD` | empty | RunPod-friendly alias for access password. |
| `SSH_PASSWORD` | empty | SSH-only password fallback. |
| `SSH_ENABLED` | `1` | Start sshd on port 22. |
| `WEB_TERMINAL_ENABLED` | `1` | Start ttyd on port 8888 when a password is available. |
| `WEB_TERMINAL_USER` | `admin` | ttyd username. |
| `WEB_TERMINAL_PASSWORD` | access password | ttyd password. |
| `WEB_TERMINAL_MAX_CLIENTS` | `2` | ttyd max clients. |
| `HEALTH_SERVER_ENABLED` | `1` | Serve a small health page when ttyd is disabled/unavailable. |
| `HEALTH_SERVER_PORT` | `8888` | ttyd or health server port. |
| `HEARTBEAT_ENABLED` | `auto` | `auto` enables heartbeat only when `HEARTBEAT_URL` is set. |
| `HEARTBEAT_URL` | empty | Receiver endpoint, usually `https://your-domain.example/api/heartbeat`. |
| `HEARTBEAT_INTERVAL_SECONDS` | `60` | Miner running heartbeat interval. |
| `HEARTBEAT_TIMEOUT_SECONDS` | `5` | Heartbeat POST timeout. |
| `HEARTBEAT_TOKEN` | empty | Optional bearer token for heartbeat receiver. |

### GPU Tuning

| Variable | Default | Use |
| --- | --- | --- |
| `GPU_PERSISTENCE_MODE` | empty | Passed to `nvidia-smi -pm`. |
| `GPU_POWER_LIMIT` | empty | Passed to `nvidia-smi -pl`. |
| `GPU_LOCK_CORE_CLOCKS` | empty | Passed to `nvidia-smi -lgc`. Single value becomes `value,value`. |
| `GPU_LOCK_MEMORY_CLOCKS` | empty | Passed to `nvidia-smi -lmc`. Single value becomes `value,value`. |

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
  -e MINER_WALLET=YOUR_PRL_WALLET \
  tdklyx/pearl-fleet:v1
```

Use a private VPS config:

```bash
docker run --rm \
  -e MINER_DRY_RUN=1 \
  -e MINER_WALLET=YOUR_PRL_WALLET \
  -e PEARL_BOOTSTRAP_URL=https://your-domain.example/bootstrap.env \
  tdklyx/pearl-fleet:v1
```

Build from source:

```bash
docker build -f fleet/Dockerfile.nocuda -t tdklyx/pearl-fleet:v1 fleet
docker build -f fleet/Dockerfile.ubuntu24 -t tdklyx/pearl-fleet:ubuntu24-v1 fleet
docker build -f fleet/Dockerfile.nocuda -t tdklyx/pearl-fleet:v1.2.0 fleet
docker build -f fleet/Dockerfile.ubuntu24 -t tdklyx/pearl-fleet:ubuntu24-v2 fleet
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

## Changelog

### v1.2.0 / ubuntu24-v2 - 2026-06-09

- Added startup network diagnosis and repair: rewrites `/etc/resolv.conf`,
  enables IPv4 preference, and logs DNS, TCP, HTTPS, Python urllib, and curl
  checks.
- Added `curl/wget` fallback for remote config, `miners.json`, wrapper scripts,
  and miner package downloads. Curl fallback defaults to IPv4 to reduce bad IPv6
  and Python urllib timeout failures that previously caused stale local registry
  fallback.
- Added `fleet-network-fix.sh --install --repair --restart-runner` for existing
  SSH-accessible machines that cannot switch images.
- Documented network repair variables and legacy-machine usage.

### v1.1.0 - 2026-06-04

- Switched SRBMiner profiles to SRBMiner-Multi 3.3.3 through an API wrapper that
  monitors local API hashrate and share counters before restarting SRBMiner.
- Updated worker naming templates to keep useful full platform IDs while
  removing profile names from worker names. Examples:
  `runpod-rtx-4090-<pod_id>`, `vast-rtx-4090-<instance_id>`,
  `clore-rtx-4090-<server_id>`, `nosana-rtx-4090-<job_id>`, and
  `salad-rtx-4090-<machine_id>`.
- Added Clore server-id priority via `CLORE_SERVER_ID`; kept `CLORE_MACHINE_ID`
  as fallback.
- Kept legacy migration helpers out of the public release. Use the Fleet image
  for new machines and maintain old-image migration scripts in private ops if
  needed.

### ubuntu24-v1 - 2026-06-07

- Added an Ubuntu 24.04 no-CUDA image variant with glibc 2.39 for miner
  binaries that require `GLIBC_2.39`.
- Kept the same Fleet entrypoint, bundled runner, default bootstrap URL, SSH,
  ttyd, watchdog, and heartbeat behavior as `v1`.
- Intended for profiles or test machines that need newer glibc. Keep using
  `v1` for miners that run correctly on Ubuntu 22.04 / glibc 2.35.
