#!/bin/sh
set -u

SRBMINER_URL="${SRBMINER_URL:-https://github.com/doktor83/SRBMiner-Multi/releases/download/3.3.7/SRBMiner-Multi-3-3-7-Linux.tar.gz}"
SRB_API_PORT="${SRB_API_PORT:-21550}"
SRB_API_URL="${SRB_API_URL:-http://127.0.0.1:${SRB_API_PORT}/api}"
SRB_WATCHDOG_WARMUP_SECONDS="${SRB_WATCHDOG_WARMUP_SECONDS:-180}"
SRB_WATCHDOG_STALE_SECONDS="${SRB_WATCHDOG_STALE_SECONDS:-420}"
SRB_WATCHDOG_CHECK_INTERVAL="${SRB_WATCHDOG_CHECK_INTERVAL:-30}"
SRB_WATCHDOG_API_TIMEOUT_SECONDS="${SRB_WATCHDOG_API_TIMEOUT_SECONDS:-5}"
SRB_WATCHDOG_MIN_HASHRATE_TH_S="${SRB_WATCHDOG_MIN_HASHRATE_TH_S:-0.1}"
SRB_WATCHDOG_RESTART_DELAY="${SRB_WATCHDOG_RESTART_DELAY:-10}"
SRB_SHUTDOWN_TERM_SECONDS="${SRB_SHUTDOWN_TERM_SECONDS:-8}"
SRB_SHUTDOWN_KILL_SECONDS="${SRB_SHUTDOWN_KILL_SECONDS:-4}"

CACHE_BASE="${MINER_CACHE_DIR:-/app/miners}"
SRBMINER_DIR="${SRBMINER_DIR:-$CACHE_BASE/srbminer-3.3.5}"
SRBMINER_BIN="$SRBMINER_DIR/SRBMiner-MULTI"
child_pid=""
child_pgid=""

log() {
  printf '%s %s\n' "[$(date -Iseconds)]" "$*"
}

download_file() {
  url="$1"
  out="$2"
  if python3 - "$url" "$out" <<'PY'
import shutil
import sys
import os
import urllib.request

url, out = sys.argv[1], sys.argv[2]
timeout = int(os.environ.get("MINER_DOWNLOAD_TIMEOUT_SECONDS", "180"))
req = urllib.request.Request(url, headers={"User-Agent": "pearl-fleet-srb-wrapper/1"})
with urllib.request.urlopen(req, timeout=timeout) as resp, open(out + ".tmp", "wb") as fh:
    shutil.copyfileobj(resp, fh)
os.replace(out + ".tmp", out)
PY
  then
    return 0
  fi
  log "[srb-wrapper] python download failed, trying curl/wget: $url"
  rm -f "$out.tmp"
  if command -v curl >/dev/null 2>&1; then
    curl_ipv4_flag=""
    if [ "${CONFIG_CURL_IPV4_ONLY:-1}" = "1" ]; then
      curl_ipv4_flag="-4"
    fi
    if curl $curl_ipv4_flag --http1.1 -fL --retry "${MINER_DOWNLOAD_RETRIES:-3}" --retry-delay 2 \
      --connect-timeout "${CONFIG_CURL_CONNECT_TIMEOUT_SECONDS:-10}" \
      --max-time "${MINER_DOWNLOAD_TIMEOUT_SECONDS:-180}" \
      -A "pearl-fleet-srb-wrapper/1" \
      -o "$out.tmp" "$url" && [ -s "$out.tmp" ]; then
      mv "$out.tmp" "$out"
      return 0
    fi
  fi
  rm -f "$out.tmp"
  if command -v wget >/dev/null 2>&1; then
    if wget -q --tries="${MINER_DOWNLOAD_RETRIES:-3}" \
      --timeout="${CONFIG_CURL_CONNECT_TIMEOUT_SECONDS:-10}" \
      --user-agent="pearl-fleet-srb-wrapper/1" \
      -O "$out.tmp" "$url" && [ -s "$out.tmp" ]; then
      mv "$out.tmp" "$out"
      return 0
    fi
  fi
  return 1
}

extract_archive() {
  archive="$1"
  target="$2"
  python3 - "$archive" "$target" <<'PY'
import shutil
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
target = Path(sys.argv[2])
if target.exists():
    shutil.rmtree(target)
target.mkdir(parents=True, exist_ok=True)
with tarfile.open(archive) as tf:
    tf.extractall(target)
PY
}

ensure_srbminer() {
  if [ -x "$SRBMINER_BIN" ]; then
    return 0
  fi
  mkdir -p "$SRBMINER_DIR"
  archive="$SRBMINER_DIR/SRBMiner-Multi.tar.gz"
  extract_dir="$SRBMINER_DIR/extract"
  log "[srb-wrapper] downloading $SRBMINER_URL"
  download_file "$SRBMINER_URL" "$archive"
  extract_archive "$archive" "$extract_dir"
  found="$(find "$extract_dir" -type f -name SRBMiner-MULTI | head -n 1)"
  if [ -z "$found" ]; then
    log "[srb-wrapper] SRBMiner-MULTI not found after extract"
    return 1
  fi
  cp "$found" "$SRBMINER_BIN"
  chmod 0755 "$SRBMINER_BIN"
}

read_api() {
  LAST_ACCEPTED="$1" LAST_REJECTED="$2" python3 - "$SRB_API_URL" <<'PY'
import json
import os
import sys
import urllib.request

url = sys.argv[1]
timeout = int(os.environ.get("SRB_WATCHDOG_API_TIMEOUT_SECONDS", "5"))
min_hashrate = float(os.environ.get("SRB_WATCHDOG_MIN_HASHRATE_TH_S", "0.1"))
last_accepted = int(os.environ.get("LAST_ACCEPTED", "-1"))
last_rejected = int(os.environ.get("LAST_REJECTED", "-1"))
with urllib.request.urlopen(url, timeout=timeout) as resp:
    data = json.load(resp)
algorithms = data.get("algorithms") or []
if not algorithms:
    raise SystemExit("missing algorithms")
algo = algorithms[0] if isinstance(algorithms[0], dict) else {}
hashrate = algo.get("hashrate") if isinstance(algo.get("hashrate"), dict) else {}
gpu_hashrate = hashrate.get("gpu") if isinstance(hashrate.get("gpu"), dict) else {}
shares = algo.get("shares") if isinstance(algo.get("shares"), dict) else {}
hashrate_th_s = float(gpu_hashrate.get("total") or 0) / 1_000_000_000_000.0
accepted = int(shares.get("accepted") or 0)
rejected = int(shares.get("rejected") or 0)
mining_time = int(data.get("mining_time") or 0)
active = hashrate_th_s > min_hashrate or accepted > last_accepted or rejected > last_rejected
print(f"{hashrate_th_s:.6f} {accepted} {rejected} {mining_time} {1 if active else 0}")
PY
}

stop_child() {
  if [ -z "$child_pid" ]; then
    return 0
  fi
  if [ -n "$child_pgid" ]; then
    kill -TERM "-$child_pgid" 2>/dev/null || true
  else
    kill "$child_pid" 2>/dev/null || true
  fi

  deadline="$(( $(date +%s) + SRB_SHUTDOWN_TERM_SECONDS ))"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! kill -0 "$child_pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done

  if [ -n "$child_pgid" ]; then
    kill -KILL "-$child_pgid" 2>/dev/null || true
  else
    kill -9 "$child_pid" 2>/dev/null || true
  fi

  deadline="$(( $(date +%s) + SRB_SHUTDOWN_KILL_SECONDS ))"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! kill -0 "$child_pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  wait "$child_pid" 2>/dev/null || true
  child_pid=""
  child_pgid=""
}

on_signal() {
  stop_child
  exit 143
}

trap on_signal INT TERM

start_child() {
  ensure_srbminer || return 1
  if command -v setsid >/dev/null 2>&1; then
    setsid "$SRBMINER_BIN" "$@" &
    child_pgid="$!"
  else
    "$SRBMINER_BIN" "$@" &
    child_pgid=""
  fi
  child_pid="$!"
  log "[srb-wrapper] started pid=$child_pid pgid=${child_pgid:-direct} api=$SRB_API_URL"
}

interruptible_sleep() {
  remaining="$1"
  while [ "$remaining" -gt 0 ]; do
    sleep 1
    remaining="$((remaining - 1))"
  done
}

monitor_child() {
  start_ts="$(date +%s)"
  last_activity="$start_ts"
  last_accepted="-1"
  last_rejected="-1"

  while kill -0 "$child_pid" 2>/dev/null; do
    interruptible_sleep "$SRB_WATCHDOG_CHECK_INTERVAL"
    now="$(date +%s)"
    if api_line="$(read_api "$last_accepted" "$last_rejected" 2>&1)"; then
      set -- $api_line
      hashrate="$1"
      accepted="$2"
      rejected="$3"
      mining_time="$4"
      active="$5"
      log "[srb-api] Total: ${hashrate} TH/s accepted=${accepted} rejected=${rejected} mining_time=${mining_time}s"
      last_accepted="$accepted"
      last_rejected="$rejected"
      if [ "$active" = "1" ]; then
        last_activity="$now"
      fi
    else
      log "[srb-api] api_error: $api_line"
    fi

    age="$((now - last_activity))"
    runtime="$((now - start_ts))"
    if [ "$runtime" -gt "$SRB_WATCHDOG_WARMUP_SECONDS" ] && [ "$age" -gt "$SRB_WATCHDOG_STALE_SECONDS" ]; then
      log "[srb-watchdog] stale_api age=${age}s; restarting SRBMiner"
      stop_child
      wait "$child_pid" 2>/dev/null || true
      return 78
    fi
  done

  wait "$child_pid"
  return "$?"
}

while :; do
  if start_child "$@"; then
    monitor_child "$@"
    status="$?"
    log "[srb-wrapper] child monitor exited status=$status; restarting in ${SRB_WATCHDOG_RESTART_DELAY}s"
  else
    log "[srb-wrapper] start failed; retrying in ${SRB_WATCHDOG_RESTART_DELAY}s"
  fi
  sleep "$SRB_WATCHDOG_RESTART_DELAY"
done
