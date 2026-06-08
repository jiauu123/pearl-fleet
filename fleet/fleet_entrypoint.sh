#!/usr/bin/env bash
set -euo pipefail

ACCESS_PASSWORD="${FLEET_ACCESS_PASSWORD:-${RUNPOD_ACCESS_PASSWORD:-${WEB_TERMINAL_PASSWORD:-${SSH_PASSWORD:-}}}}"
SSH_ENABLED="${SSH_ENABLED:-1}"
WEB_TERMINAL_ENABLED="${WEB_TERMINAL_ENABLED:-1}"
WEB_TERMINAL_USER="${WEB_TERMINAL_USER:-admin}"
WEB_TERMINAL_PASSWORD="${WEB_TERMINAL_PASSWORD:-${ACCESS_PASSWORD}}"
WEB_TERMINAL_MAX_CLIENTS="${WEB_TERMINAL_MAX_CLIENTS:-2}"
HEALTH_SERVER_ENABLED="${HEALTH_SERVER_ENABLED:-1}"
HEALTH_SERVER_HOST="${HEALTH_SERVER_HOST:-[::]}"
HEALTH_SERVER_PORT="${HEALTH_SERVER_PORT:-8888}"
FLEET_NETWORK_FIX_ENABLED="${FLEET_NETWORK_FIX_ENABLED:-1}"

run_network_fix() {
  if [[ "${FLEET_NETWORK_FIX_ENABLED}" != "1" ]]; then
    return 0
  fi
  if [[ ! -x /usr/local/bin/fleet-network-fix.sh ]]; then
    echo "[network] fleet-network-fix.sh missing; skipping" >&2
    return 0
  fi
  /usr/local/bin/fleet-network-fix.sh --boot --repair >/tmp/fleet-network-fix.boot.log 2>&1 || {
    echo "[network] repair failed; continuing, log=/tmp/fleet-network-fix.boot.log" >&2
  }
}

if [[ "${MINER_DRY_RUN:-0}" == "1" ]]; then
  run_network_fix
  exec python3 /usr/local/bin/pearl_fleet_runner.py "$@"
fi

start_sshd() {
  if [[ "${SSH_ENABLED}" != "1" ]]; then
    return 0
  fi
  if ! command -v sshd >/dev/null 2>&1; then
    echo "[ssh] sshd not installed; skipping" >&2
    return 0
  fi

  mkdir -p /run/sshd
  if [[ -n "${ACCESS_PASSWORD}" ]]; then
    echo "root:${ACCESS_PASSWORD}" | chpasswd
    sed -i \
      -e 's/^#\?PermitRootLogin .*/PermitRootLogin yes/' \
      -e 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' \
      -e 's/^#\?KbdInteractiveAuthentication .*/KbdInteractiveAuthentication yes/' \
      /etc/ssh/sshd_config
  fi
  /usr/sbin/sshd
  echo "[ssh] started on port 22 user=root"
}

start_health_server() {
  if [[ "${HEALTH_SERVER_ENABLED}" != "1" ]]; then
    return 0
  fi
  if ! command -v busybox >/dev/null 2>&1; then
    return 0
  fi
  mkdir -p /tmp/pearl-health
  cat >/tmp/pearl-health/index.html <<EOF
ok
service=pearl-fleet
EOF
  busybox httpd -f -p "${HEALTH_SERVER_HOST}:${HEALTH_SERVER_PORT}" -h /tmp/pearl-health &
  echo "[health] listening on ${HEALTH_SERVER_HOST}:${HEALTH_SERVER_PORT}"
}

start_ttyd() {
  if [[ "${WEB_TERMINAL_ENABLED}" != "1" ]]; then
    start_health_server
    return 0
  fi
  if [[ -z "${WEB_TERMINAL_PASSWORD}" ]]; then
    echo "[ttyd] password empty; falling back to health server"
    start_health_server
    return 0
  fi
  if ! command -v ttyd >/dev/null 2>&1; then
    echo "[ttyd] ttyd not installed; falling back to health server"
    start_health_server
    return 0
  fi

  ttyd \
    -6 \
    -i :: \
    -p "${HEALTH_SERVER_PORT}" \
    -c "${WEB_TERMINAL_USER}:${WEB_TERMINAL_PASSWORD}" \
    -m "${WEB_TERMINAL_MAX_CLIENTS}" \
    -w /app \
    /bin/bash &
  echo "[ttyd] listening on ${HEALTH_SERVER_PORT} user=${WEB_TERMINAL_USER}"
}

run_network_fix
start_sshd
start_ttyd

exec python3 /usr/local/bin/pearl_fleet_runner.py "$@"
