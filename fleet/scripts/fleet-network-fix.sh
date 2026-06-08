#!/bin/sh
set -u

LOG="${FLEET_NETWORK_FIX_LOG:-/var/log/pearl-fleet-network-fix.log}"
STATE_DIR="${FLEET_NETWORK_FIX_STATE_DIR:-/var/lib/pearl-fleet}"
DEFAULT_CHECK_HOSTS="raw.githubusercontent.com github.com objects.githubusercontent.com pearlhash.xyz pearl.tw-pool.com"
DEFAULT_CHECK_URLS="https://raw.githubusercontent.com/selkk-lab/pearl-fleet/main/config/bootstrap.env https://raw.githubusercontent.com/selkk-lab/pearl-fleet/main/config/miners.json https://github.com/egg5233/tw-pearl-miner/releases/download/v1.9.0/tw-pearl-miner-1.9.0-cuda12.tar.gz"
CHECK_HOSTS="${FLEET_NETWORK_CHECK_HOSTS:-$DEFAULT_CHECK_HOSTS}"
CHECK_URLS="${FLEET_NETWORK_CHECK_URLS:-$DEFAULT_CHECK_URLS}"
DNS_SERVERS="${FLEET_DNS_SERVERS:-1.1.1.1 8.8.8.8}"
DNS_OPTIONS="${FLEET_DNS_OPTIONS:-timeout:2 attempts:3 rotate}"
HOSTS_FALLBACKS="${FLEET_HOSTS_FALLBACKS:-}"
REPAIR=1
INSTALL=0
BOOT=0
PATCH_ENTRYPOINT="${FLEET_NETWORK_PATCH_ENTRYPOINT:-1}"
RESTART_RUNNER=0

usage() {
  cat <<'EOF'
Usage: fleet-network-fix.sh [--diagnose-only] [--repair] [--install] [--boot] [--restart-runner]

Environment:
  FLEET_DNS_SERVERS="1.1.1.1 8.8.8.8"
  FLEET_DNS_OPTIONS="timeout:2 attempts:3 rotate"
  FLEET_HOSTS_FALLBACKS="host=ip host2=ip2"
  FLEET_NETWORK_CHECK_HOSTS="raw.githubusercontent.com github.com ..."
  FLEET_NETWORK_CHECK_URLS="https://raw.githubusercontent.com/selkk-lab/pearl-fleet/main/config/bootstrap.env ..."
  FLEET_NETWORK_FIX_LOG="/var/log/pearl-fleet-network-fix.log"
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --diagnose-only)
      REPAIR=0
      INSTALL=0
      ;;
    --repair)
      REPAIR=1
      ;;
    --install)
      INSTALL=1
      ;;
    --boot)
      BOOT=1
      ;;
    --no-entrypoint-patch)
      PATCH_ENTRYPOINT=0
      ;;
    --restart-runner)
      RESTART_RUNNER=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '%s\n' "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

mkdir -p "$STATE_DIR" 2>/dev/null || true
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

log() {
  line="[$(date -Iseconds 2>/dev/null || date)] $*"
  printf '%s\n' "$line" >&2
  printf '%s\n' "$line" >>"$LOG" 2>/dev/null || true
}

have() {
  command -v "$1" >/dev/null 2>&1
}

url_host() {
  printf '%s\n' "$1" | sed -n 's#^[a-zA-Z][a-zA-Z0-9+.-]*://\([^/:]*\).*#\1#p'
}

append_dynamic_checks() {
  for url in "${PEARL_BOOTSTRAP_URL:-}" "${PEARL_CONFIG_URL:-}" "${MINER_CONFIG_URL:-}" "${MINER_REGISTRY_URL:-}" "${PLATFORM_REGISTRY_URL:-}"; do
    if [ -n "$url" ]; then
      CHECK_URLS="$url $CHECK_URLS"
      host="$(url_host "$url")"
      if [ -n "$host" ]; then
        CHECK_HOSTS="$host $CHECK_HOSTS"
      fi
    fi
  done
}

is_root() {
  [ "$(id -u 2>/dev/null || echo 1)" = "0" ]
}

backup_file() {
  path="$1"
  name="$(basename "$path")"
  if [ -e "$path" ] || [ -L "$path" ]; then
    cp -L "$path" "$STATE_DIR/${name}.bak.$(date +%s)" 2>>"$LOG" || true
  fi
}

write_resolv_conf() {
  if ! is_root; then
    log "[repair] not root; cannot rewrite /etc/resolv.conf"
    return 1
  fi
  if [ "${FLEET_DNS_FIX_ENABLED:-1}" != "1" ]; then
    log "[repair] DNS fix disabled"
    return 0
  fi

  tmp="$STATE_DIR/resolv.conf.new"
  : >"$tmp" 2>>"$LOG" || return 1
  for server in $DNS_SERVERS; do
    printf 'nameserver %s\n' "$server" >>"$tmp"
  done
  if [ -n "$DNS_OPTIONS" ]; then
    printf 'options %s\n' "$DNS_OPTIONS" >>"$tmp"
  fi

  backup_file /etc/resolv.conf
  if [ -L /etc/resolv.conf ]; then
    rm -f /etc/resolv.conf 2>>"$LOG" || true
  fi
  if cp "$tmp" /etc/resolv.conf 2>>"$LOG"; then
    chmod 0644 /etc/resolv.conf 2>>"$LOG" || true
    log "[repair] wrote /etc/resolv.conf using DNS: $DNS_SERVERS"
    return 0
  fi
  if cat "$tmp" >/etc/resolv.conf 2>>"$LOG"; then
    chmod 0644 /etc/resolv.conf 2>>"$LOG" || true
    log "[repair] wrote /etc/resolv.conf using DNS: $DNS_SERVERS"
    return 0
  fi
  log "[repair] failed to write /etc/resolv.conf"
  return 1
}

prefer_ipv4() {
  if ! is_root; then
    return 1
  fi
  if [ "${FLEET_IPV4_PREFER_ENABLED:-1}" != "1" ]; then
    return 0
  fi
  touch /etc/gai.conf 2>>"$LOG" || return 1
  if grep -q "pearl-fleet-network-fix prefer ipv4" /etc/gai.conf 2>/dev/null; then
    log "[repair] IPv4 preference already present"
    return 0
  fi
  backup_file /etc/gai.conf
  {
    printf '\n# pearl-fleet-network-fix prefer ipv4\n'
    printf 'precedence ::ffff:0:0/96  100\n'
  } >>/etc/gai.conf 2>>"$LOG" || return 1
  log "[repair] enabled IPv4 preference in /etc/gai.conf"
}

install_hosts_fallbacks() {
  if ! is_root; then
    return 1
  fi
  if [ -z "$HOSTS_FALLBACKS" ]; then
    log "[repair] no hosts fallbacks configured"
    return 0
  fi
  backup_file /etc/hosts
  tmp="$STATE_DIR/hosts.new"
  awk '
    /# pearl-fleet-network-fix begin/ { skip=1; next }
    /# pearl-fleet-network-fix end/ { skip=0; next }
    skip != 1 { print }
  ' /etc/hosts >"$tmp" 2>>"$LOG" || cp /etc/hosts "$tmp" 2>>"$LOG" || return 1
  {
    printf '# pearl-fleet-network-fix begin\n'
    for pair in $HOSTS_FALLBACKS; do
      host="${pair%%=*}"
      ip="${pair#*=}"
      if [ -n "$host" ] && [ -n "$ip" ] && [ "$host" != "$ip" ]; then
        printf '%s %s\n' "$ip" "$host"
      fi
    done
    printf '# pearl-fleet-network-fix end\n'
  } >>"$tmp"
  if cp "$tmp" /etc/hosts 2>>"$LOG"; then
    log "[repair] installed hosts fallbacks: $HOSTS_FALLBACKS"
    return 0
  fi
  log "[repair] failed to update /etc/hosts"
  return 1
}

probe_dns() {
  host="$1"
  if have getent; then
    out="$(getent hosts "$host" 2>&1 | head -n 3)"
    rc=$?
  elif have nslookup; then
    out="$(nslookup "$host" 2>&1 | head -n 8)"
    rc=$?
  else
    out="no getent/nslookup"
    rc=127
  fi
  if [ "$rc" -eq 0 ] && [ -n "$out" ]; then
    log "[dns] OK $host -> $(printf '%s' "$out" | tr '\n' ' ')"
  else
    log "[dns] FAIL $host -> $out"
  fi
}

probe_tcp() {
  host="$1"
  port="$2"
  if ! have python3; then
    return 0
  fi
  out="$(python3 - "$host" "$port" <<'PY' 2>&1
import socket, sys
host = sys.argv[1]
port = int(sys.argv[2])
try:
    s = socket.create_connection((host, port), timeout=8)
    s.close()
    print("OK")
except Exception as exc:
    print("FAIL", exc)
    raise SystemExit(1)
PY
)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    log "[tcp] OK $host:$port"
  else
    log "[tcp] FAIL $host:$port -> $out"
  fi
}

probe_url_curl() {
  url="$1"
  if ! have curl; then
    log "[url] curl missing for $url"
    return 0
  fi
  curl_ipv4_flag=""
  if [ "${CONFIG_CURL_IPV4_ONLY:-1}" = "1" ]; then
    curl_ipv4_flag="-4"
  fi
  out="$(curl $curl_ipv4_flag -I --http1.1 --connect-timeout 8 --max-time 25 -sS \
    -o /dev/null \
    -w 'http_code=%{http_code} remote_ip=%{remote_ip} redirect_url=%{redirect_url}' \
    "$url" 2>&1)"
  rc=$?
  flat="$(printf '%s' "$out" | tr '\n' ' ')"
  redirect="$(printf '%s\n' "$flat" | sed -n 's/.*redirect_url=\([^ ]*\).*/\1/p')"
  redirect_host="$(url_host "$redirect")"
  if [ -n "$redirect_host" ]; then
    flat="$(printf '%s\n' "$flat" | sed 's/ redirect_url=.*$/ redirect_host='"$redirect_host"'/')"
  else
    flat="$(printf '%s\n' "$flat" | sed 's/ redirect_url=.*$/ redirect_host=/')"
  fi
  if [ "$rc" -eq 0 ]; then
    case "$redirect $flat" in
      *safebrowse*|*WRONG_VERSION_NUMBER*|*wrong\ version*)
        log "[url] BLOCKED curl $url -> $flat"
        ;;
      *)
        log "[url] OK curl $url -> $flat"
        ;;
    esac
  else
    log "[url] FAIL curl $url -> $flat"
  fi
}

probe_url_python() {
  url="$1"
  if ! have python3; then
    return 0
  fi
  out="$(python3 - "$url" <<'PY' 2>&1
import sys, urllib.request
url = sys.argv[1]
try:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "pearl-fleet-network-fix/1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        print("OK", resp.status)
except Exception as exc:
    print("FAIL", exc)
    raise SystemExit(1)
PY
)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    log "[url] OK python $url -> $out"
  else
    log "[url] FAIL python $url -> $out"
  fi
}

diagnose() {
  label="$1"
  log "[diag] ===== $label ====="
  log "[diag] user=$(id 2>/dev/null || true) hostname=$(hostname 2>/dev/null || true)"
  log "[diag] date=$(date -Iseconds 2>/dev/null || date)"
  if [ -r /etc/resolv.conf ]; then
    log "[diag] resolv.conf: $(tr '\n' '|' </etc/resolv.conf | sed 's/|$//')"
  fi
  for host in $CHECK_HOSTS; do
    probe_dns "$host"
  done
  for host in $CHECK_HOSTS; do
    probe_tcp "$host" 443
  done
  for url in $CHECK_URLS; do
    probe_url_curl "$url"
    probe_url_python "$url"
  done
}

patch_entrypoint() {
  file="/usr/local/bin/fleet_entrypoint.sh"
  if [ "$PATCH_ENTRYPOINT" != "1" ]; then
    return 0
  fi
  if [ ! -f "$file" ]; then
    log "[install] no fleet entrypoint at $file"
    return 0
  fi
  if grep -q "pearl-fleet-network-fix hook begin" "$file" 2>/dev/null; then
    log "[install] fleet entrypoint hook already present"
    return 0
  fi
  backup_file "$file"
  tmp="$STATE_DIR/fleet_entrypoint.sh.new"
  awk '
    NR == 1 {
      print
      print ""
      print "# pearl-fleet-network-fix hook begin"
      print "if [ \"${FLEET_NETWORK_FIX_ENABLED:-1}\" = \"1\" ] && [ -x /usr/local/bin/fleet-network-fix.sh ]; then"
      print "  /usr/local/bin/fleet-network-fix.sh --boot --repair >/tmp/fleet-network-fix.boot.log 2>&1 || true"
      print "fi"
      print "# pearl-fleet-network-fix hook end"
      next
    }
    { print }
  ' "$file" >"$tmp" 2>>"$LOG" || return 1
  cp "$tmp" "$file" 2>>"$LOG" || return 1
  chmod 0755 "$file" 2>>"$LOG" || true
  log "[install] patched $file"
}

install_rc_local() {
  if [ ! -d /etc ]; then
    return 0
  fi
  if [ -f /etc/rc.local ] && grep -q "fleet-network-fix.sh" /etc/rc.local 2>/dev/null; then
    log "[install] rc.local hook already present"
    return 0
  fi
  backup_file /etc/rc.local
  cat >/etc/rc.local <<'EOF' 2>>"$LOG" || return 1
#!/bin/sh
/usr/local/bin/fleet-network-fix.sh --boot --repair >>/var/log/pearl-fleet-network-fix.log 2>&1 || true
exit 0
EOF
  chmod 0755 /etc/rc.local 2>>"$LOG" || true
  log "[install] installed /etc/rc.local fallback"
}

install_cron() {
  if [ ! -d /etc/cron.d ]; then
    mkdir -p /etc/cron.d 2>>"$LOG" || return 0
  fi
  cat >/etc/cron.d/pearl-fleet-network-fix <<'EOF' 2>>"$LOG" || return 1
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
@reboot root /usr/local/bin/fleet-network-fix.sh --boot --repair >>/var/log/pearl-fleet-network-fix.log 2>&1
EOF
  chmod 0644 /etc/cron.d/pearl-fleet-network-fix 2>>"$LOG" || true
  log "[install] installed cron @reboot fallback"
}

install_systemd() {
  if [ ! -d /etc/systemd/system ]; then
    return 0
  fi
  cat >/etc/systemd/system/pearl-fleet-network-fix.service <<'EOF' 2>>"$LOG" || return 1
[Unit]
Description=Pearl Fleet network repair
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/fleet-network-fix.sh --boot --repair
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  if have systemctl && [ -d /run/systemd/system ]; then
    systemctl daemon-reload >>"$LOG" 2>&1 || true
    systemctl enable pearl-fleet-network-fix.service >>"$LOG" 2>&1 || true
  fi
  log "[install] installed systemd unit fallback"
}

install_self() {
  if ! is_root; then
    log "[install] not root; cannot install persistence"
    return 1
  fi
  dst="/usr/local/bin/fleet-network-fix.sh"
  mkdir -p /usr/local/bin 2>>"$LOG" || true
  if [ "$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")" != "$dst" ] && [ -r "$0" ]; then
    cp "$0" "$dst" 2>>"$LOG" || true
  fi
  if [ -f "$dst" ]; then
    chmod 0755 "$dst" 2>>"$LOG" || true
    log "[install] installed $dst"
  else
    log "[install] $dst missing; download this script to that path for reboot persistence"
  fi
  patch_entrypoint || true
  install_rc_local || true
  install_cron || true
  install_systemd || true
}

restart_runner() {
  log "[restart] restarting fleet runner/miner processes"
  pkill -TERM -f 'pearl_fleet_runner.py' 2>/dev/null || true
  pkill -TERM -f 'managed-miner-wrapper.py' 2>/dev/null || true
  pkill -TERM -f 'twpool-lowhash-wrapper.py' 2>/dev/null || true
  pkill -TERM -f 'srbminer-api-wrapper.sh' 2>/dev/null || true
  sleep 3
  pkill -KILL -f 'pearl_fleet_runner.py' 2>/dev/null || true
  pkill -KILL -f 'managed-miner-wrapper.py' 2>/dev/null || true
  pkill -KILL -f 'twpool-lowhash-wrapper.py' 2>/dev/null || true
  pkill -KILL -f 'srbminer-api-wrapper.sh' 2>/dev/null || true
}

append_dynamic_checks
log "[start] fleet network fix boot=$BOOT repair=$REPAIR install=$INSTALL"
diagnose "before"
if [ "$REPAIR" = "1" ]; then
  write_resolv_conf || true
  prefer_ipv4 || true
  install_hosts_fallbacks || true
  diagnose "after"
fi
if [ "$INSTALL" = "1" ]; then
  install_self || true
fi
if [ "$RESTART_RUNNER" = "1" ]; then
  restart_runner || true
fi
log "[done] fleet network fix complete log=$LOG"
