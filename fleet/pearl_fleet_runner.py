#!/usr/bin/env python3
import glob
import hashlib
import json
import os
import queue
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


DEFAULTS = {
    "PEARL_PLATFORM": "",
    "WORKER_PREFIX": "auto",
    "WORKER_ID_LEN": "8",
    "MINER_PROFILE": "pearlpool",
    "MINER_CACHE_DIR": "/app/miners",
    "FLEET_STATE_DIR": "/app/state",
    "CONFIG_SYNC_ENABLED": "1",
    "CONFIG_POLL_SECONDS": "300",
    "CONFIG_POLL_JITTER_SECONDS": "30",
    "WATCHDOG_ENABLED": "1",
    "WATCHDOG_CHECK_INTERVAL": "30",
    "WATCHDOG_STARTUP_GRACE": "180",
    "WATCHDOG_STALE_SECONDS": "300",
    "WATCHDOG_RESTART_DELAY": "10",
    "WATCHDOG_MAX_RESTARTS": "0",
    "MINER_DRY_RUN": "0",
    "ALWAYS_DOWNLOAD_MINER": "0",
    "FLEET_LOCAL_ENV_FILES": "/workspace/fleet.env /runpod-volume/fleet.env /app/fleet.env",
    "FLEET_OVERRIDE_FILES": "/workspace/fleet.override.env /runpod-volume/fleet.override.env /app/fleet.override.env",
    "FLEET_LOCAL_REGISTRY_FILES": "/workspace/miners.json /runpod-volume/miners.json /app/miners.json",
    "FLEET_LOCAL_PLATFORM_FILES": "/workspace/platforms.json /runpod-volume/platforms.json /app/platforms.json",
    "PLATFORM_REGISTRY_URL": "",
    "HEARTBEAT_ENABLED": "auto",
    "HEARTBEAT_URL": "",
    "HEARTBEAT_INTERVAL_SECONDS": "60",
    "HEARTBEAT_TIMEOUT_SECONDS": "5",
    "HEARTBEAT_TOKEN": "",
}

ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
TOKEN_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")
URL_CACHE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def log(message):
    print(message, flush=True)


def warn(message):
    print(message, file=sys.stderr, flush=True)


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def nonempty_env():
    return {k: v for k, v in os.environ.items() if v != ""}


def merge(base, incoming):
    out = dict(base)
    for key, value in incoming.items():
        if value is not None:
            out[str(key)] = str(value)
    return out


def parse_env_text(text, source):
    parsed = {}
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        match = ENV_LINE_RE.match(stripped)
        if not match:
            warn(f"[config] skipping invalid line in {source}: {line}")
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        parsed[key] = value
    return parsed


def parse_env_file(path):
    p = Path(path)
    if not p.is_file():
        return {}
    log(f"[config] loading {p}")
    return parse_env_text(p.read_text(encoding="utf-8", errors="replace"), str(p))


def load_env_files(paths_text):
    result = {}
    for part in str(paths_text or "").split():
        result = merge(result, parse_env_file(part))
    return result


def cache_busted_url(url, identity=None):
    sep = "&" if "?" in url else "?"
    minute = int(time.time() // 60)
    query = f"_pearl_min={minute}"
    if identity:
        query += f"&id={urllib.parse.quote(identity)}"
    return f"{url}{sep}{query}"


def state_path(cfg, name):
    root = Path(cfg.get("FLEET_STATE_DIR", DEFAULTS["FLEET_STATE_DIR"]))
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def safe_cache_name(prefix, url, suffix):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    hostish = URL_CACHE_RE.sub("-", urllib.parse.urlparse(url).netloc or "url").strip("-")
    return f"{prefix}-{hostish}-{digest}.{suffix}"


def fetch_text(url, timeout=20, identity=None):
    req = urllib.request.Request(
        cache_busted_url(url, identity),
        headers={"User-Agent": "pearl-fleet-runner/1"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_env_with_cache(url, cfg, cache_name, identity=None):
    if not url:
        return {}
    cache = state_path(cfg, cache_name)
    try:
        log(f"[config] fetching {url}")
        text = fetch_text(url, identity=identity)
        cache.write_text(text, encoding="utf-8")
        return parse_env_text(text, url)
    except Exception as exc:
        warn(f"[config] fetch failed {url}: {exc}")
        if cache.is_file():
            warn(f"[config] using cached {cache}")
            return parse_env_text(cache.read_text(encoding="utf-8", errors="replace"), str(cache))
        return {}


def fetch_json_with_cache(url, cfg, cache_name, identity=None):
    if not url:
        return None
    cache = state_path(cfg, cache_name)
    try:
        log(f"[registry] fetching {url}")
        text = fetch_text(url, identity=identity)
        data = json.loads(text)
        cache.write_text(text, encoding="utf-8")
        return data
    except Exception as exc:
        warn(f"[registry] fetch failed {url}: {exc}")
        if cache.is_file():
            warn(f"[registry] using cached {cache}")
            return json.loads(cache.read_text(encoding="utf-8", errors="replace"))
        return None


def normalize_part(value):
    value = str(value or "").lower()
    for piece in ("nvidia ", "geforce "):
        value = value.replace(piece, "")
    value = value.replace("laptop gpu", "laptop").replace("ti super", "tisuper")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def short_id(value, length):
    return normalize_part(value)[: int(length or 8)].strip("-") or "worker"


def detect_gpu_model():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        first = out.splitlines()[0].strip()
        if first:
            return first
    except Exception:
        pass
    for path in glob.glob("/proc/driver/nvidia/gpus/*/information"):
        try:
            for line in Path(path).read_text(errors="ignore").splitlines():
                if ":" in line and ("Model" in line or "GPU" in line):
                    return line.split(":", 1)[1].strip()
        except Exception:
            continue
    return "gpu"


def platform_entries(platforms):
    if isinstance(platforms, dict) and isinstance(platforms.get("platforms"), dict):
        return platforms["platforms"]
    return {}


def load_platform_registry(cfg, identity=None):
    registry = None
    for path in str(cfg.get("FLEET_LOCAL_PLATFORM_FILES", DEFAULTS["FLEET_LOCAL_PLATFORM_FILES"])).split():
        p = Path(path)
        if p.is_file():
            log(f"[platforms] loading {p}")
            registry = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            break
    if registry is None:
        registry = {"platforms": {}}

    remote_url = cfg.get("PLATFORM_REGISTRY_URL", "")
    if remote_url and not truthy(cfg.get("DISABLE_REMOTE_CONFIG")):
        cache_identity = identity.get("WORKER_NAME") if isinstance(identity, dict) else None
        remote = fetch_json_with_cache(remote_url, cfg, "platforms.current.json", cache_identity)
        if remote:
            registry = remote
    return registry


def config_or_env(cfg, key):
    return first_nonempty(cfg.get(key), os.environ.get(key))


def first_by_names(cfg, names):
    for key in names or []:
        value = config_or_env(cfg, key)
        if value:
            return value
    return ""


def detect_platform(cfg, platforms=None):
    if cfg.get("PEARL_PLATFORM"):
        return normalize_part(cfg["PEARL_PLATFORM"])
    for name, spec in platform_entries(platforms).items():
        if name == "generic":
            continue
        if first_by_names(cfg, spec.get("detect_env", [])):
            return normalize_part(name)
    return "generic"


def first_nonempty(*values):
    for value in values:
        if value:
            return str(value)
    return ""


def build_identity(cfg, platforms=None):
    platform = detect_platform(cfg, platforms)
    spec = platform_entries(platforms).get(platform, {})
    generic = platform_entries(platforms).get("generic", {})
    gpu_model = normalize_part(detect_gpu_model())
    instance = first_nonempty(
        cfg.get("INSTANCE_ID"),
        first_by_names(cfg, spec.get("instance_id_env", [])),
        first_by_names(cfg, generic.get("instance_id_env", [])),
        os.environ.get("HOSTNAME"),
        socket.gethostname(),
    )
    machine = first_nonempty(
        cfg.get("MACHINE_ID"),
        first_by_names(cfg, spec.get("machine_id_env", [])),
        first_by_names(cfg, generic.get("machine_id_env", [])),
    )
    offer = first_nonempty(
        cfg.get("OFFER_ID"),
        first_by_names(cfg, spec.get("offer_id_env", [])),
        first_by_names(cfg, generic.get("offer_id_env", [])),
    )
    length = cfg.get("WORKER_ID_LEN", "8")
    profile = normalize_profile(cfg.get("MINER_PROFILE", DEFAULTS["MINER_PROFILE"]))
    prefix = cfg.get("WORKER_PREFIX", "auto")
    if prefix == "auto":
        prefix = spec.get("worker_prefix") or platform

    instance_short = short_id(instance, length)
    machine_short = short_id(machine, length) if machine else ""
    offer_short = short_id(offer, length) if offer else ""
    worker_name = cfg.get("WORKER_NAME", "")
    if not worker_name:
        template = spec.get("worker_template", "")
        vars_map = {
            "WORKER_PREFIX": prefix,
            "MINER_PROFILE": profile,
            "GPU_MODEL": gpu_model,
            "INSTANCE_ID": instance,
            "INSTANCE_ID_SHORT": instance_short,
            "MACHINE_ID": machine,
            "MACHINE_ID_SHORT": machine_short,
            "OFFER_ID": offer,
            "OFFER_ID_SHORT": offer_short,
        }
        if template:
            worker_name = normalize_part(render_template(template, vars_map, required=False))
            worker_name = re.sub(r"-(m|o)-", "-", worker_name)
            worker_name = re.sub(r"-(m|o)$", "", worker_name).strip("-")
        if not worker_name:
            parts = [prefix, profile, gpu_model]
            if offer_short:
                parts.append(f"o{offer_short}")
            if machine_short:
                parts.append(f"m{machine_short}")
            parts.append(f"i{instance_short}")
            worker_name = "-".join(parts)

    identity = {
        "PEARL_PLATFORM": platform,
        "GPU_MODEL": gpu_model,
        "RAW_INSTANCE_ID": instance,
        "RAW_MACHINE_ID": machine,
        "RAW_OFFER_ID": offer,
        "WORKER_NAME": worker_name,
        "MINER_PROFILE": profile,
    }
    return identity


def normalize_profile(profile):
    profile = str(profile or "").lower()
    aliases = {
        "pearlhash": "pearlpool",
        "official": "pearlpool",
        "pearlhash-official": "pearlpool",
        "tw": "tw-pool",
        "tw-cuda13": "tw-pool",
        "tw-cuda12": "tw-pool-cuda12",
        "lucky": "luckypool",
        "alpha": "alphapool",
    }
    return aliases.get(profile, profile)


def render_template(value, vars_map, required=True):
    def replace(match):
        key = match.group(1)
        default = match.group(3)
        if key in vars_map and str(vars_map[key]) != "":
            return str(vars_map[key])
        if default is not None:
            return default
        if required:
            raise KeyError(key)
        return ""

    return TOKEN_RE.sub(replace, str(value))


def render_list(values, vars_map):
    rendered = []
    for value in values:
        item = render_template(value, vars_map)
        if item != "":
            rendered.append(item)
    return rendered


def url_hash(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def download_binary(url, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "pearl-fleet-runner/1"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    tmp.replace(output)
    output.chmod(0o755)


def verify_sha256(path, expected):
    if not expected:
        return
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")


def extract_archive(archive, target):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
        return
    if name.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar")):
        with tarfile.open(archive) as tf:
            tf.extractall(target)
        return
    raise RuntimeError(f"unsupported archive type: {archive}")


def find_binary(payload_dir, binary):
    candidates = []
    binary = str(binary)
    if "/" in binary:
        candidates.extend(glob.glob(str(payload_dir / binary), recursive=True))
    candidates.extend(glob.glob(str(payload_dir / "**" / binary), recursive=True))
    candidates = [Path(p) for p in candidates if Path(p).is_file()]
    if not candidates:
        raise RuntimeError(f"binary {binary} not found under {payload_dir}")
    path = candidates[0]
    path.chmod(0o755)
    return path


def prepare_miner(profile, entry, vars_map, cfg):
    cache_root = Path(cfg.get("MINER_CACHE_DIR", DEFAULTS["MINER_CACHE_DIR"]))
    url = render_template(entry["url"], vars_map)
    digest = url_hash(url)
    miner_dir = cache_root / profile / digest
    archive_type = entry.get("archive", "file")
    binary_name = entry.get("binary", profile)
    always = truthy(cfg.get("ALWAYS_DOWNLOAD_MINER"))
    sha = render_template(entry.get("sha256", ""), vars_map, required=False)

    if archive_type == "file":
        out = miner_dir / binary_name
        if always or not out.is_file():
            log(f"[download] {profile}: {url}")
            download_binary(url, out)
            verify_sha256(out, sha)
        return out

    payload_dir = miner_dir / "payload"
    marker = miner_dir / ".ready"
    archive_suffix = entry.get("archive_suffix", "")
    if not archive_suffix:
        archive_suffix = Path(urllib.parse.urlparse(url).path).name or "miner.archive"
    archive_path = miner_dir / archive_suffix

    if always or not marker.is_file():
        miner_dir.mkdir(parents=True, exist_ok=True)
        log(f"[download] {profile}: {url}")
        download_binary(url, archive_path)
        verify_sha256(archive_path, sha)
        extract_archive(archive_path, payload_dir)
        marker.write_text(str(time.time()), encoding="utf-8")
    return find_binary(payload_dir, binary_name)


def registry_profiles(registry):
    if "profiles" in registry and isinstance(registry["profiles"], dict):
        return registry["profiles"]
    return registry


def load_registry(cfg, identity):
    registry = None
    for path in str(cfg.get("FLEET_LOCAL_REGISTRY_FILES", DEFAULTS["FLEET_LOCAL_REGISTRY_FILES"])).split():
        p = Path(path)
        if p.is_file():
            log(f"[registry] loading {p}")
            registry = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            break
    if registry is None:
        registry = {"profiles": {}}

    remote_url = cfg.get("MINER_REGISTRY_URL", "")
    if remote_url and not truthy(cfg.get("DISABLE_REMOTE_CONFIG")):
        remote = fetch_json_with_cache(remote_url, cfg, "miners.current.json", identity.get("WORKER_NAME"))
        if remote:
            registry = remote
    return registry


def early_override_env():
    result = {}
    for path in DEFAULTS["FLEET_OVERRIDE_FILES"].split():
        result = merge(result, parse_env_file(path))
    return result


def fetch_target_envs(cfg, identity):
    if truthy(cfg.get("DISABLE_REMOTE_CONFIG")):
        return {}
    result = {}
    template = cfg.get("FLEET_TARGET_CONFIG_URL", "")
    vars_map = merge(cfg, identity)
    if template:
        try:
            url = render_template(template, vars_map)
            result = merge(result, fetch_optional_env(url, identity["WORKER_NAME"]))
        except KeyError as exc:
            warn(f"[config] target URL missing variable {exc}; skipping")
    base = cfg.get("FLEET_TARGETS_BASE_URL", "").rstrip("/")
    if base:
        for key in ("WORKER_NAME", "RAW_INSTANCE_ID"):
            name = normalize_part(identity.get(key, ""))
            if not name:
                continue
            url = f"{base}/{urllib.parse.quote(name)}.env"
            fetched = fetch_optional_env(url, identity["WORKER_NAME"])
            if fetched:
                result = merge(result, fetched)
                break
    return result


def fetch_optional_env(url, identity=None):
    try:
        log(f"[config] fetching optional target {url}")
        return parse_env_text(fetch_text(url, identity=identity), url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log(f"[config] no target override at {url}")
            return {}
        warn(f"[config] optional target fetch failed {url}: {exc}")
        return {}
    except Exception as exc:
        warn(f"[config] optional target fetch failed {url}: {exc}")
        return {}


def load_config():
    env = nonempty_env()
    early = early_override_env()
    disabled = truthy(env.get("DISABLE_REMOTE_CONFIG") or early.get("DISABLE_REMOTE_CONFIG"))
    cfg = dict(DEFAULTS)

    bootstrap_url = early.get("PEARL_BOOTSTRAP_URL") or env.get("PEARL_BOOTSTRAP_URL")
    if bootstrap_url and not disabled:
        cfg = merge(cfg, fetch_env_with_cache(bootstrap_url, cfg, "bootstrap.current.env"))

    config_url = (
        early.get("PEARL_CONFIG_URL")
        or env.get("PEARL_CONFIG_URL")
        or early.get("MINER_CONFIG_URL")
        or env.get("MINER_CONFIG_URL")
        or cfg.get("PEARL_CONFIG_URL")
        or cfg.get("MINER_CONFIG_URL")
    )
    if config_url and not disabled:
        cfg = merge(cfg, fetch_env_with_cache(config_url, cfg, "fleet.current.env"))

    local_env_files = env.get("FLEET_LOCAL_ENV_FILES") or early.get("FLEET_LOCAL_ENV_FILES") or cfg.get("FLEET_LOCAL_ENV_FILES", DEFAULTS["FLEET_LOCAL_ENV_FILES"])
    cfg = merge(cfg, load_env_files(local_env_files))
    cfg = merge(cfg, env)
    cfg = merge(cfg, early)
    cfg["MINER_PROFILE"] = normalize_profile(cfg.get("MINER_PROFILE", DEFAULTS["MINER_PROFILE"]))
    platforms = load_platform_registry(cfg)
    identity = build_identity(cfg, platforms)

    target = fetch_target_envs(cfg, identity)
    if target:
        cfg = merge(cfg, target)
        local_env_files = env.get("FLEET_LOCAL_ENV_FILES") or early.get("FLEET_LOCAL_ENV_FILES") or cfg.get("FLEET_LOCAL_ENV_FILES", DEFAULTS["FLEET_LOCAL_ENV_FILES"])
        cfg = merge(cfg, load_env_files(local_env_files))
        cfg = merge(cfg, env)
        cfg = merge(cfg, early)
        cfg["MINER_PROFILE"] = normalize_profile(cfg.get("MINER_PROFILE", DEFAULTS["MINER_PROFILE"]))
        platforms = load_platform_registry(cfg)
        identity = build_identity(cfg, platforms)

    cfg = merge(cfg, identity)
    return cfg


def config_fingerprint(cfg, registry, platforms=None):
    relevant = {
        key: value
        for key, value in cfg.items()
        if key.startswith(("MINER_", "PEARL", "WATCHDOG", "FLEET", "GPU_", "WORKER", "ALPHA", "LUCKY", "TW_", "PLATFORM"))
    }
    payload = json.dumps({"cfg": relevant, "registry": registry, "platforms": platforms or {}}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def redacted_command(cmd, cfg):
    secrets = {
        cfg.get("MINER_WALLET", ""),
        cfg.get("MINER_USER", ""),
        cfg.get("ALPHA_PASSWORD", ""),
        cfg.get("MINER_PASSWORD", ""),
    }
    redacted = []
    for item in cmd:
        if item and item in secrets:
            redacted.append("REDACTED")
        else:
            redacted.append(item)
    return " ".join(shlex_quote(x) for x in redacted)


def shlex_quote(value):
    value = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:=,+@%-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def build_command(cfg, registry):
    profiles = registry_profiles(registry)
    profile = normalize_profile(cfg.get("MINER_PROFILE", DEFAULTS["MINER_PROFILE"]))
    if profile not in profiles:
        raise RuntimeError(f"unknown MINER_PROFILE={profile}; available={','.join(sorted(profiles))}")
    entry = profiles[profile]
    vars_map = merge(entry.get("env", {}), cfg)
    for _ in range(2):
        vars_map = {
            key: render_template(value, vars_map, required=False)
            for key, value in vars_map.items()
        }
    wallet = vars_map.get("MINER_WALLET") or vars_map.get("MINER_USER")
    if not wallet:
        raise RuntimeError("MINER_WALLET is required")
    vars_map["MINER_WALLET"] = wallet
    binary = prepare_miner(profile, entry, vars_map, cfg)
    args = render_list(entry.get("args", []), merge(vars_map, {"MINER_BINARY": str(binary)}))
    return [str(binary)] + args, entry


def maybe_apply_tuning(cfg):
    commands = []
    if cfg.get("GPU_PERSISTENCE_MODE"):
        commands.append(["nvidia-smi", "-pm", cfg["GPU_PERSISTENCE_MODE"]])
    if cfg.get("GPU_POWER_LIMIT"):
        commands.append(["nvidia-smi", "-pl", cfg["GPU_POWER_LIMIT"]])
    if cfg.get("GPU_LOCK_CORE_CLOCKS"):
        value = cfg["GPU_LOCK_CORE_CLOCKS"]
        if "," not in value:
            value = f"{value},{value}"
        commands.append(["nvidia-smi", "-lgc", value])
    if cfg.get("GPU_LOCK_MEMORY_CLOCKS"):
        value = cfg["GPU_LOCK_MEMORY_CLOCKS"]
        if "," not in value:
            value = f"{value},{value}"
        commands.append(["nvidia-smi", "-lmc", value])
    for cmd in commands:
        try:
            log(f"[gpu-tune] {' '.join(cmd)}")
            subprocess.run(cmd, check=False, timeout=10)
        except Exception as exc:
            warn(f"[gpu-tune] warning: {exc}")


def safe_int(value, default):
    try:
        return int(value)
    except Exception:
        return default


def heartbeat_is_enabled(cfg):
    mode = str(cfg.get("HEARTBEAT_ENABLED", "auto")).strip().lower()
    if mode == "auto":
        return bool(cfg.get("HEARTBEAT_URL"))
    return truthy(mode)


def build_heartbeat_payload(cfg, event, status, metrics=None):
    cost = {}
    for key in ("COST_USD_HOUR", "RENT_USD_HOUR", "PRICE_USD_HOUR", "VAST_DPH_TOTAL"):
        if cfg.get(key):
            cost[key] = cfg[key]
    return {
        "schema_version": 1,
        "sent_at": int(time.time()),
        "event": event,
        "status": status,
        "worker_name": cfg.get("WORKER_NAME", ""),
        "platform": cfg.get("PEARL_PLATFORM", ""),
        "profile": cfg.get("MINER_PROFILE", ""),
        "gpu_model": cfg.get("GPU_MODEL", ""),
        "config_version": cfg.get("CONFIG_VERSION", ""),
        "identity": {
            "instance_id": cfg.get("RAW_INSTANCE_ID", ""),
            "machine_id": cfg.get("RAW_MACHINE_ID", ""),
            "offer_id": cfg.get("RAW_OFFER_ID", ""),
            "hostname": socket.gethostname(),
        },
        "cost": cost,
        "metrics": metrics or {},
    }


def post_heartbeat(cfg, event, status="ok", metrics=None):
    if not heartbeat_is_enabled(cfg):
        return
    url = cfg.get("HEARTBEAT_URL", "")
    if not url:
        warn("[heartbeat] HEARTBEAT_ENABLED is set but HEARTBEAT_URL is empty")
        return
    payload = json.dumps(build_heartbeat_payload(cfg, event, status, metrics), sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "pearl-fleet-runner/1",
    }
    token = cfg.get("HEARTBEAT_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    timeout = safe_int(cfg.get("HEARTBEAT_TIMEOUT_SECONDS"), 5)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except Exception as exc:
        warn(f"[heartbeat] post failed {url}: {exc}")


class LogState:
    def __init__(self):
        self.last_activity = time.time()
        self.last_hashrate = None
        self.last_hashrate_th_s = None
        self.last_hashrate_raw = None
        self.accepted = 0
        self.rejected = 0
        self.error_seen = False


def compile_regex(pattern):
    if not pattern:
        return None
    return re.compile(pattern, re.IGNORECASE)


def strip_ansi(value):
    return ANSI_RE.sub("", str(value or ""))


def normalize_hashrate_th(value, unit="TH/s"):
    number = float(str(value).replace(",", ""))
    raw_unit = str(unit or "TH/s").strip().lower()
    compact = raw_unit.replace(" ", "").replace("hash", "h").replace("sol", "h")
    compact = compact.replace("/sec", "/s")
    if compact in {"ph/s", "phs", "ph"}:
        return number * 1000.0
    if compact in {"th/s", "ths", "th", "t"}:
        return number
    if compact in {"gh/s", "ghs", "gh", "g"}:
        return number / 1000.0
    if compact in {"mh/s", "mhs", "mh", "m"}:
        return number / 1_000_000.0
    if compact in {"kh/s", "khs", "kh", "k"}:
        return number / 1_000_000_000.0
    if compact in {"h/s", "hs", "h"}:
        return number / 1_000_000_000_000.0
    return number


def regex_rule_items(primary, fallback=None):
    raw = primary if primary not in (None, "", []) else fallback
    if raw in (None, "", []):
        return []
    if isinstance(raw, list):
        return raw
    return [raw]


def compile_pattern_rules(primary, fallback=None):
    rules = []
    for item in regex_rule_items(primary, fallback):
        if isinstance(item, dict):
            pattern = item.get("regex") or item.get("pattern")
            if not pattern:
                continue
            rule = dict(item)
            rule["compiled"] = re.compile(pattern, re.IGNORECASE)
            rules.append(rule)
        else:
            rules.append({"compiled": re.compile(str(item), re.IGNORECASE)})
    return rules


def apply_count_rules(line, rules, current):
    updated = current
    matched = False
    for rule in rules:
        match = rule["compiled"].search(line)
        if not match:
            continue
        matched = True
        group = int(rule.get("group", 1))
        if match.lastindex and group <= match.lastindex:
            try:
                updated = max(updated, int(match.group(group)))
            except Exception:
                updated += 1
        else:
            updated += 1
    return matched, updated


def apply_hashrate_rules(line, rules, default_unit="TH/s"):
    for rule in rules:
        match = rule["compiled"].search(line)
        if not match:
            continue
        value_group = int(rule.get("value_group", rule.get("group", 1)))
        unit_group = rule.get("unit_group")
        value = match.group(value_group) if match.lastindex and value_group <= match.lastindex else match.group(0)
        unit = rule.get("unit") or default_unit
        if unit_group is not None:
            unit_group = int(unit_group)
            if match.lastindex and unit_group <= match.lastindex:
                unit = match.group(unit_group)
        th_s = normalize_hashrate_th(value, unit)
        return th_s, value, unit
    return None, None, None


def reader_thread(proc, q):
    try:
        for line in proc.stdout:
            q.put(line.rstrip("\n"))
    finally:
        q.put(None)


def terminate_process(proc, timeout=15):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)


def miner_subprocess_env(cmd):
    env = os.environ.copy()
    if not cmd:
        return env
    binary_dir = str(Path(cmd[0]).resolve().parent)
    current = env.get("LD_LIBRARY_PATH", "")
    parts = [binary_dir]
    if current:
        parts.append(current)
    env["LD_LIBRARY_PATH"] = ":".join(parts)
    return env


def miner_metrics(state, proc, attempt, fingerprint, extra=None):
    metrics = {
        "attempt": attempt,
        "fingerprint": fingerprint,
        "pid": proc.pid if proc else None,
        "accepted": state.accepted if state else 0,
        "rejected": state.rejected if state else 0,
        "last_hashrate": state.last_hashrate if state else None,
        "last_hashrate_th_s": state.last_hashrate_th_s if state else None,
        "last_hashrate_raw": state.last_hashrate_raw if state else None,
        "last_activity_age_seconds": int(time.time() - state.last_activity) if state else None,
    }
    if proc and proc.poll() is not None:
        metrics["exit_code"] = proc.returncode
    if extra:
        metrics.update(extra)
    return metrics


def run_once(cfg, registry, fingerprint, attempt):
    cmd, entry = build_command(cfg, registry)
    watchdog = entry.get("watchdog", {})
    watchdog = merge(
        {
            "mode": "process",
            "warmup_seconds": cfg.get("WATCHDOG_STARTUP_GRACE", "180"),
            "stale_seconds": cfg.get("WATCHDOG_STALE_SECONDS", "300"),
            "check_interval": cfg.get("WATCHDOG_CHECK_INTERVAL", "30"),
        },
        watchdog,
    )
    accepted_rules = compile_pattern_rules(watchdog.get("accepted_regexes"), watchdog.get("accepted_regex", ""))
    rejected_rules = compile_pattern_rules(watchdog.get("rejected_regexes"), watchdog.get("rejected_regex", ""))
    accepted_count_rules = compile_pattern_rules(watchdog.get("accepted_count_regexes"), watchdog.get("accepted_count_regex", ""))
    rejected_count_rules = compile_pattern_rules(watchdog.get("rejected_count_regexes"), watchdog.get("rejected_count_regex", ""))
    hashrate_rules = compile_pattern_rules(watchdog.get("hashrate_regexes"), watchdog.get("hashrate_regex", ""))
    error_rules = compile_pattern_rules(watchdog.get("error_regexes"), watchdog.get("error_regex", ""))
    hashrate_unit = watchdog.get("hashrate_unit", "TH/s")

    log(f"[miner] profile={cfg['MINER_PROFILE']} worker={cfg['WORKER_NAME']} attempt={attempt}")
    log(f"[miner] command={redacted_command(cmd, cfg)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(Path(cmd[0]).resolve().parent),
        env=miner_subprocess_env(cmd),
    )
    q = queue.Queue()
    threading.Thread(target=reader_thread, args=(proc, q), daemon=True).start()
    state = LogState()
    post_heartbeat(cfg, "miner_start", "running", miner_metrics(state, proc, attempt, fingerprint))
    start = time.time()
    next_poll = time.time() + poll_interval(cfg)
    next_heartbeat = time.time() + safe_int(cfg.get("HEARTBEAT_INTERVAL_SECONDS"), 60)

    while True:
        try:
            line = q.get(timeout=1)
        except queue.Empty:
            line = None
        if line is not None:
            if line:
                log(line)
                clean_line = strip_ansi(line)
                matched, value = apply_count_rules(clean_line, accepted_count_rules, state.accepted)
                if matched:
                    state.accepted = value
                    state.last_activity = time.time()
                matched, value = apply_count_rules(clean_line, rejected_count_rules, state.rejected)
                if matched:
                    state.rejected = value
                    state.last_activity = time.time()
                matched, value = apply_count_rules(clean_line, accepted_rules, state.accepted)
                if matched:
                    state.accepted = value
                    state.last_activity = time.time()
                matched, value = apply_count_rules(clean_line, rejected_rules, state.rejected)
                if matched:
                    state.rejected = value
                    state.last_activity = time.time()
                th_s, raw_value, raw_unit = apply_hashrate_rules(clean_line, hashrate_rules, hashrate_unit)
                if th_s is not None:
                    state.last_hashrate_th_s = round(th_s, 1)
                    state.last_hashrate = f"{th_s:.1f} TH/s"
                    state.last_hashrate_raw = f"{raw_value} {raw_unit}".strip()
                    state.last_activity = time.time()
                if any(rule["compiled"].search(clean_line) for rule in error_rules):
                    state.error_seen = True
                    warn("[watchdog] error regex matched; restarting miner")
                    terminate_process(proc)
                    status = proc.wait()
                    post_heartbeat(
                        cfg,
                        "watchdog_restart",
                        "error_regex",
                        miner_metrics(state, proc, attempt, fingerprint, {"exit_code": status}),
                    )
                    return status

        if proc.poll() is not None:
            post_heartbeat(
                cfg,
                "miner_exit",
                "exited",
                miner_metrics(state, proc, attempt, fingerprint, {"exit_code": proc.returncode}),
            )
            return proc.returncode

        now = time.time()
        if now >= next_heartbeat:
            next_heartbeat = now + safe_int(cfg.get("HEARTBEAT_INTERVAL_SECONDS"), 60)
            post_heartbeat(cfg, "miner_running", "running", miner_metrics(state, proc, attempt, fingerprint))

        if truthy(cfg.get("CONFIG_SYNC_ENABLED")) and now >= next_poll:
            next_poll = now + poll_interval(cfg)
            try:
                new_cfg = load_config()
                new_platforms = load_platform_registry(new_cfg)
                new_registry = load_registry(new_cfg, build_identity(new_cfg, new_platforms))
                new_fp = config_fingerprint(new_cfg, new_registry, new_platforms)
                if new_fp != fingerprint:
                    log("[config] changed; restarting miner with new config")
                    terminate_process(proc)
                    post_heartbeat(
                        cfg,
                        "config_changed",
                        "restart",
                        miner_metrics(state, proc, attempt, fingerprint, {"new_fingerprint": new_fp}),
                    )
                    return 99
            except Exception as exc:
                warn(f"[config] sync check failed; keeping current miner: {exc}")

        if not truthy(cfg.get("WATCHDOG_ENABLED")):
            continue
        mode = str(watchdog.get("mode", "process"))
        if mode in {"log", "api"} and now - start > int(watchdog.get("warmup_seconds", 180)):
            stale = int(watchdog.get("stale_seconds", cfg.get("WATCHDOG_STALE_SECONDS", 300)))
            if now - state.last_activity > stale:
                warn(f"[watchdog] no log activity for {int(now - state.last_activity)}s; restarting miner")
                terminate_process(proc)
                status = proc.wait()
                post_heartbeat(
                    cfg,
                    "watchdog_restart",
                    "stale_log",
                    miner_metrics(state, proc, attempt, fingerprint, {"exit_code": status}),
                )
                return status


def poll_interval(cfg):
    base = int(cfg.get("CONFIG_POLL_SECONDS", DEFAULTS["CONFIG_POLL_SECONDS"]))
    jitter = int(cfg.get("CONFIG_POLL_JITTER_SECONDS", DEFAULTS["CONFIG_POLL_JITTER_SECONDS"]))
    if jitter <= 0:
        return base
    return max(10, base + random.randint(-jitter, jitter))


def dry_run(cfg, registry, fingerprint):
    profiles = registry_profiles(registry)
    profile = cfg["MINER_PROFILE"]
    entry = profiles.get(profile)
    log("Pearl fleet dry run")
    log(f"platform={cfg['PEARL_PLATFORM']}")
    log(f"profile={profile}")
    log(f"worker={cfg['WORKER_NAME']}")
    log(f"gpu={cfg['GPU_MODEL']}")
    log(f"instance={cfg['RAW_INSTANCE_ID']}")
    log(f"machine={cfg['RAW_MACHINE_ID']}")
    log(f"config_version={cfg.get('CONFIG_VERSION', '')}")
    log(f"fingerprint={fingerprint}")
    log(f"registry_profiles={','.join(sorted(profiles))}")
    if entry:
        vars_map = merge(entry.get("env", {}), cfg)
        log(f"miner_url={render_template(entry.get('url', ''), vars_map, required=False)}")


def main():
    attempt = 0
    while True:
        cfg = load_config()
        platforms = load_platform_registry(cfg)
        registry = load_registry(cfg, build_identity(cfg, platforms))
        fp = config_fingerprint(cfg, registry, platforms)
        if truthy(cfg.get("MINER_DRY_RUN")):
            dry_run(cfg, registry, fp)
            return 0
        maybe_apply_tuning(cfg)
        attempt += 1
        try:
            status = run_once(cfg, registry, fp, attempt)
        except Exception as exc:
            warn(f"[runner] error: {exc}")
            post_heartbeat(
                cfg,
                "runner_error",
                "error",
                {"attempt": attempt, "fingerprint": fp, "error": str(exc)[:300]},
            )
            status = 1
        log(f"[watchdog] miner exited status={status}")
        if not truthy(cfg.get("WATCHDOG_ENABLED")):
            return status
        max_restarts = int(cfg.get("WATCHDOG_MAX_RESTARTS", "0"))
        if max_restarts and attempt >= max_restarts:
            return status
        time.sleep(int(cfg.get("WATCHDOG_RESTART_DELAY", DEFAULTS["WATCHDOG_RESTART_DELAY"])))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    raise SystemExit(main())
