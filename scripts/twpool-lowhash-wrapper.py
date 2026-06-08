#!/usr/bin/env python3
import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


HASH_RE = r"([0-9]+(?:[.][0-9]+)?)\s*([KMGT]?H/s)\s+window\s*\|\s*([0-9]+(?:[.][0-9]+)?)\s*([KMGT]?H/s)\s+avg"
CURRENT_PROC = None


def log(message):
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] {message}", flush=True)


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return int(default)


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def url_hash(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def download_with_cli(url, out, timeout=180):
    tmp = str(out) + ".tmp"
    if os.path.exists(tmp):
        os.unlink(tmp)
    retries = str(env_int("MINER_DOWNLOAD_RETRIES", 3))
    connect_timeout = str(env_int("CONFIG_CURL_CONNECT_TIMEOUT_SECONDS", 10))
    if shutil.which("curl"):
        cmd = [
            "curl",
            "--http1.1",
            "-fL",
            "--retry",
            retries,
            "--retry-delay",
            "2",
            "--connect-timeout",
            connect_timeout,
            "--max-time",
            str(timeout),
            "-A",
            "pearl-fleet-twpool-wrapper/1",
            "-o",
            tmp,
            url,
        ]
        if truthy(os.environ.get("CONFIG_CURL_IPV4_ONLY", "1")):
            cmd.insert(1, "-4")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, out)
            return True
        log(f"[twpool-wrapper] curl download failed: {proc.stderr.decode('utf-8', errors='replace').strip()[:300]}")
    if shutil.which("wget"):
        cmd = [
            "wget",
            "-q",
            "--tries",
            retries,
            "--timeout",
            connect_timeout,
            "--user-agent",
            "pearl-fleet-twpool-wrapper/1",
            "-O",
            tmp,
            url,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, out)
            return True
        log(f"[twpool-wrapper] wget download failed: {proc.stderr.decode('utf-8', errors='replace').strip()[:300]}")
    return False


def download_file(url, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "pearl-fleet-twpool-wrapper/1"})
    timeout = env_int("MINER_DOWNLOAD_TIMEOUT_SECONDS", 180)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(str(out) + ".tmp", "wb") as fh:
            shutil.copyfileobj(resp, fh)
        os.replace(str(out) + ".tmp", out)
    except Exception as exc:
        log(f"[twpool-wrapper] urllib download failed: {exc}")
        if not download_with_cli(url, out, timeout=timeout):
            raise


def extract_archive(archive, target):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(target)


def find_binary(payload_dir, binary_name):
    candidates = [Path(p) for p in glob.glob(str(payload_dir / "**" / binary_name), recursive=True)]
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        raise RuntimeError(f"{binary_name} not found under {payload_dir}")
    candidates[0].chmod(0o755)
    return candidates[0]


def parse_wrapper_args(args):
    wrapper = {
        "miner_url": os.environ.get("TWPOOL_MINER_URL", ""),
        "binary_name": os.environ.get("TWPOOL_MINER_BINARY", "pearl-gpu-miner"),
    }
    miner_args = []
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item == "--":
            miner_args.extend(args[idx + 1 :])
            break
        if item == "--twpool-miner-url" and idx + 1 < len(args):
            wrapper["miner_url"] = args[idx + 1]
            idx += 2
            continue
        if item.startswith("--twpool-miner-url="):
            wrapper["miner_url"] = item.split("=", 1)[1]
            idx += 1
            continue
        if item == "--twpool-miner-binary" and idx + 1 < len(args):
            wrapper["binary_name"] = args[idx + 1]
            idx += 2
            continue
        if item.startswith("--twpool-miner-binary="):
            wrapper["binary_name"] = item.split("=", 1)[1]
            idx += 1
            continue
        miner_args.append(item)
        idx += 1
    if not wrapper["miner_url"]:
        raise RuntimeError("--twpool-miner-url is required; set it in miners.json")
    return wrapper, miner_args


def ensure_miner(miner_url, binary_name):
    cache_base = Path(os.environ.get("MINER_CACHE_DIR", "/app/miners"))
    miner_dir = cache_base / f"twpool-runtime-{url_hash(miner_url)}"
    payload_dir = miner_dir / "payload"
    binary = payload_dir / binary_name
    if binary.is_file() and os.access(binary, os.X_OK) and not truthy(os.environ.get("ALWAYS_DOWNLOAD_MINER")):
        return binary

    archive_name = Path(urllib.parse.urlparse(miner_url).path).name or "twpool.tar.gz"
    archive_path = miner_dir / archive_name
    log(f"[twpool-wrapper] downloading {miner_url}")
    download_file(miner_url, archive_path)
    extract_archive(archive_path, payload_dir)
    return find_binary(payload_dir, binary_name)


def arg_value(args, name):
    for idx, value in enumerate(args):
        if value == name and idx + 1 < len(args):
            return args[idx + 1]
        prefix = name + "="
        if value.startswith(prefix):
            return value[len(prefix):]
    return ""


def normalize_th_s(value, unit):
    value = float(value)
    unit = (unit or "TH/s").upper()
    if unit.startswith("KH"):
        return value / 1_000_000_000.0
    if unit.startswith("MH"):
        return value / 1_000_000.0
    if unit.startswith("GH"):
        return value / 1_000.0
    if unit.startswith("TH"):
        return value
    return value


def heartbeat_enabled():
    mode = os.environ.get("TWPOOL_HEARTBEAT_ENABLED", os.environ.get("HEARTBEAT_ENABLED", "auto"))
    if str(mode).strip().lower() == "auto":
        return bool(os.environ.get("HEARTBEAT_URL"))
    return truthy(mode)


def heartbeat_payload(event, status, metrics):
    worker = os.environ.get("WORKER_NAME") or arg_value(sys.argv[1:], "--worker")
    cost = {}
    for key in ("COST_USD_HOUR", "RENT_USD_HOUR", "PRICE_USD_HOUR", "VAST_DPH_TOTAL"):
        if os.environ.get(key):
            cost[key] = os.environ[key]
    return {
        "schema_version": 1,
        "sent_at": int(time.time()),
        "event": event,
        "status": status,
        "worker_name": worker,
        "platform": os.environ.get("PEARL_PLATFORM", ""),
        "profile": os.environ.get("MINER_PROFILE", ""),
        "gpu_model": os.environ.get("GPU_MODEL", ""),
        "config_version": os.environ.get("CONFIG_VERSION", ""),
        "identity": {
            "instance_id": os.environ.get("RAW_INSTANCE_ID", ""),
            "machine_id": os.environ.get("RAW_MACHINE_ID", ""),
            "offer_id": os.environ.get("RAW_OFFER_ID", ""),
            "hostname": os.uname().nodename if hasattr(os, "uname") else "",
        },
        "cost": cost,
        "metrics": metrics,
    }


def post_heartbeat(event, status, metrics):
    url = os.environ.get("HEARTBEAT_URL", "")
    if not url or not heartbeat_enabled():
        return
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("HEARTBEAT_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(heartbeat_payload(event, status, metrics), sort_keys=True).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        log(f"[twpool-heartbeat] post_error: {exc}")
        if shutil.which("curl"):
            cmd = [
                "curl",
                "-fsS",
                "--max-time",
                "10",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
            ]
            if token:
                cmd += ["-H", f"Authorization: Bearer {token}"]
            cmd += ["--data-binary", "@-", url]
            try:
                subprocess.run(cmd, input=body, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except Exception as curl_exc:
                log(f"[twpool-heartbeat] curl_post_error: {curl_exc}")


def terminate_process(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def request_container_reboot(reason, metrics):
    log(f"[twpool-watchdog] reboot_requested reason={reason}")
    post_heartbeat("twpool_low_hashrate_reboot", "rebooting", metrics)
    time.sleep(env_int("TWPOOL_LOW_HASH_REBOOT_HEARTBEAT_GRACE_SECONDS", "2"))

    command = os.environ.get("TWPOOL_LOW_HASH_REBOOT_COMMAND", "").strip()
    if command:
        log(f"[twpool-watchdog] running reboot command: {command}")
        try:
            subprocess.Popen(command, shell=True)
        finally:
            os._exit(88)

    method = os.environ.get("TWPOOL_LOW_HASH_REBOOT_METHOD", "pid1").strip().lower()
    if method in {"pid1", "container"}:
        term_seconds = env_int("TWPOOL_LOW_HASH_REBOOT_TERM_SECONDS", "10")
        try:
            os.kill(1, signal.SIGTERM)
            log("[twpool-watchdog] sent SIGTERM to pid 1")
        except Exception as exc:
            log(f"[twpool-watchdog] failed SIGTERM pid 1: {exc}")
        time.sleep(term_seconds)
        try:
            os.kill(1, signal.SIGKILL)
            log("[twpool-watchdog] sent SIGKILL to pid 1")
        except Exception as exc:
            log(f"[twpool-watchdog] failed SIGKILL pid 1: {exc}")
        os._exit(88)

    if method == "exit":
        os._exit(88)

    log(f"[twpool-watchdog] unknown reboot method={method}; falling back to wrapper exit")
    os._exit(88)


def child_env(binary):
    env = os.environ.copy()
    binary_dir = str(binary.resolve().parent)
    current = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = binary_dir if not current else f"{binary_dir}:{current}"
    return env


def monitor_child(binary, args, restart_state):
    import re
    global CURRENT_PROC

    cmd = [str(binary)] + args
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(binary.resolve().parent),
        env=child_env(binary),
    )
    CURRENT_PROC = proc
    log(f"[twpool-wrapper] started pid={proc.pid}")
    start_ts = time.time()
    warmup = env_int("TWPOOL_LOW_HASH_WARMUP_SECONDS", "600")
    ratio = env_float("TWPOOL_LOW_HASH_RATIO", "0.55")
    bad_limit = env_int("TWPOOL_LOW_HASH_BAD_SAMPLES", "3")
    min_baseline = env_float("TWPOOL_LOW_HASH_MIN_BASELINE_TH_S", "50")
    cooldown = env_int("TWPOOL_LOW_HASH_RESTART_COOLDOWN_SECONDS", "900")
    heartbeat_interval = env_int("TWPOOL_HEARTBEAT_INTERVAL_SECONDS", os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60"))
    restart_delay = env_int("TWPOOL_RESTART_DELAY", os.environ.get("WATCHDOG_RESTART_DELAY", "10"))
    low_hash_enabled = truthy(os.environ.get("TWPOOL_LOW_HASH_ENABLED", "1"))
    reboot_enabled = truthy(os.environ.get("TWPOOL_LOW_HASH_REBOOT_ENABLED", "0"))
    reboot_after_restarts = env_int("TWPOOL_LOW_HASH_REBOOT_AFTER_RESTARTS", "0")
    recovery_seconds = env_int("TWPOOL_LOW_HASH_RECOVERY_SECONDS", "180")
    reboot_cooldown = env_int("TWPOOL_LOW_HASH_REBOOT_COOLDOWN_SECONDS", "1800")

    hash_re = re.compile(HASH_RE, re.IGNORECASE)
    shares_re = re.compile(r"shares:\s*([0-9]+)\s+accepted", re.IGNORECASE)
    baseline = float(restart_state.get("baseline") or 0.0)
    bad_samples = 0
    accepted = 0
    last_window = None
    last_avg = None
    next_heartbeat = time.time() + min(heartbeat_interval, 30)

    for line in proc.stdout:
        line = line.rstrip("\n")
        print(line, flush=True)
        now = time.time()

        shares_match = shares_re.search(line)
        if shares_match:
            accepted = int(shares_match.group(1))

        match = hash_re.search(line)
        if match:
            window_th = normalize_th_s(match.group(1), match.group(2))
            avg_th = normalize_th_s(match.group(3), match.group(4))
            last_window = window_th
            last_avg = avg_th
            runtime = now - start_ts
            if runtime >= warmup and window_th >= min_baseline:
                baseline = max(baseline, window_th)
                restart_state["baseline"] = baseline

            if baseline >= min_baseline and runtime >= warmup and low_hash_enabled:
                threshold = baseline * ratio
                if window_th < threshold:
                    bad_samples += 1
                else:
                    bad_samples = 0
                    restart_state["low_hash_restarts"] = 0
                if bad_samples >= bad_limit and now - restart_state["last_low_hash_restart"] >= cooldown:
                    metrics = build_metrics(proc, accepted, last_window, last_avg, baseline, ratio, bad_samples)
                    low_hash_restarts = int(restart_state.get("low_hash_restarts") or 0)
                    if reboot_enabled and now - float(restart_state.get("last_low_hash_reboot") or 0) >= reboot_cooldown:
                        restart_state["last_low_hash_reboot"] = now
                        request_container_reboot(
                            f"low_hashrate_direct window={window_th:.1f} baseline={baseline:.1f}",
                            metrics,
                        )
                    else:
                        log(
                            "[twpool-watchdog] low_hashrate "
                            f"window={window_th:.1f} TH/s avg={avg_th:.1f} TH/s "
                            f"baseline={baseline:.1f} TH/s ratio={window_th / baseline:.3f}; restarting miner"
                        )
                        post_heartbeat("twpool_low_hashrate_restart", "restarting", metrics)
                        restart_state["last_low_hash_restart"] = now
                        restart_state["low_hash_restarts"] = low_hash_restarts + 1
                        restart_state["last_recovery_start"] = now
                        terminate_process(proc)
                        CURRENT_PROC = None
                        return 78
            elif baseline < min_baseline and runtime >= warmup and window_th >= min_baseline:
                baseline = window_th
                restart_state["baseline"] = baseline

            if (
                reboot_enabled
                and low_hash_enabled
                and int(restart_state.get("low_hash_restarts") or 0) >= reboot_after_restarts
                and baseline >= min_baseline
                and runtime >= recovery_seconds
                and window_th < baseline * ratio
                and now - float(restart_state.get("last_low_hash_reboot") or 0) >= reboot_cooldown
            ):
                metrics = build_metrics(proc, accepted, last_window, last_avg, baseline, ratio, bad_samples)
                restart_state["last_low_hash_reboot"] = now
                request_container_reboot(
                    f"low_hashrate_recovery_failed window={window_th:.1f} baseline={baseline:.1f}",
                    metrics,
                )

        if last_window is not None and now >= next_heartbeat:
            next_heartbeat = now + heartbeat_interval
            post_heartbeat(
                "twpool_metrics",
                "running",
                build_metrics(proc, accepted, last_window, last_avg, baseline, ratio, bad_samples),
            )

    status = proc.wait()
    CURRENT_PROC = None
    log(f"[twpool-wrapper] child exited status={status}; restarting in {restart_delay}s")
    return status


def build_metrics(proc, accepted, window_th, avg_th, baseline, ratio, bad_samples):
    window_value = round(float(window_th or 0), 1)
    avg_value = round(float(avg_th or 0), 1)
    return {
        "pid": proc.pid if proc else None,
        "accepted": accepted,
        "last_hashrate": f"{window_value:.1f} TH/s",
        "last_hashrate_th_s": window_value,
        "last_hashrate_raw": f"{window_value:.1f} TH/s window",
        "twpool_window_hashrate_th_s": window_value,
        "twpool_avg_hashrate_th_s": avg_value,
        "twpool_hashrate_basis": "window",
        "twpool_low_hash_baseline_th_s": round(float(baseline or 0), 1),
        "twpool_low_hash_ratio": ratio,
        "twpool_low_hash_bad_samples": bad_samples,
    }


def main():
    stop = {"value": False}
    restart_state = {
        "baseline": 0.0,
        "last_low_hash_restart": 0.0,
        "last_low_hash_reboot": 0.0,
        "low_hash_restarts": 0,
        "last_recovery_start": 0.0,
    }

    def on_signal(signum, _frame):
        stop["value"] = True
        if CURRENT_PROC is not None:
            terminate_process(CURRENT_PROC)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    restart_delay = env_int("TWPOOL_RESTART_DELAY", os.environ.get("WATCHDOG_RESTART_DELAY", "10"))

    while not stop["value"]:
        try:
            wrapper, miner_args = parse_wrapper_args(sys.argv[1:])
            binary = ensure_miner(wrapper["miner_url"], wrapper["binary_name"])
            status = monitor_child(binary, miner_args, restart_state)
            if stop["value"]:
                return 143
            if status == 78:
                time.sleep(restart_delay)
            else:
                time.sleep(restart_delay)
        except KeyboardInterrupt:
            return 143
        except Exception as exc:
            log(f"[twpool-wrapper] start_or_monitor_error: {exc}; retrying in {restart_delay}s")
            time.sleep(restart_delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
