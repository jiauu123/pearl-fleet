#!/usr/bin/env python3
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DATA_DIR = Path(os.environ.get("HEARTBEAT_DATA_DIR", "/var/lib/pearl-fleet"))
BIND = os.environ.get("HEARTBEAT_BIND", "127.0.0.1")
PORT = int(os.environ.get("HEARTBEAT_PORT", "8787"))
TOKEN = os.environ.get("HEARTBEAT_TOKEN", "")
MAX_BODY_BYTES = int(os.environ.get("HEARTBEAT_MAX_BODY_BYTES", "65536"))


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_name(value):
    value = str(value or "unknown").lower()
    value = re.sub(r"[^a-z0-9_.-]+", "-", value).strip("-")
    return value[:160] or "unknown"


def write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=True, sort_keys=True))
        fh.write("\n")


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default


def merge_payload(existing, incoming):
    if not isinstance(existing, dict):
        return incoming
    if not isinstance(incoming, dict):
        return existing

    merged = dict(existing)
    same_profile = existing.get("profile") == incoming.get("profile")
    for key, value in incoming.items():
        if key in {"metrics", "identity", "cost"} and isinstance(value, dict):
            if key == "metrics" and not same_profile:
                merged[key] = value
                continue
            base = merged.get(key) if isinstance(merged.get(key), dict) else {}
            new_value = dict(base)
            new_value.update(value)
            merged[key] = new_value
            continue
        if value in ("", None) and key in merged:
            continue
        merged[key] = value
    return merged


class Handler(BaseHTTPRequestHandler):
    server_version = "PearlFleetHeartbeat/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}", flush=True)

    def send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def check_auth(self):
        if not TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("empty request body")
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        body = self.rfile.read(length)
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("json body must be an object")
        return data

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json(200, {"ok": True, "time": now_iso()})
            return
        if path == "/api/workers":
            self.send_json(200, load_json(DATA_DIR / "heartbeats" / "latest.json", {}))
            return
        if path.startswith("/api/workers/"):
            worker = safe_name(unquote(path.rsplit("/", 1)[-1]))
            data = load_json(DATA_DIR / "heartbeats" / "workers" / f"{worker}.json", None)
            if data is None:
                self.send_json(404, {"ok": False, "error": "worker not found"})
                return
            self.send_json(200, data)
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/heartbeat":
            self.send_json(404, {"ok": False, "error": "not found"})
            return
        if not self.check_auth():
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            data = self.read_json_body()
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
            return

        data["received_at"] = int(time.time())
        data["received_at_iso"] = now_iso()
        data["remote_addr"] = self.client_address[0]
        worker = safe_name(data.get("worker_name") or data.get("worker") or data.get("identity", {}).get("instance_id"))

        root = DATA_DIR / "heartbeats"
        latest_path = root / "latest.json"
        latest = load_json(latest_path, {})
        existing = latest.get(worker)
        data = merge_payload(existing, data)
        latest[worker] = data

        write_json_atomic(root / "workers" / f"{worker}.json", data)
        write_json_atomic(latest_path, latest)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        append_jsonl(root / "events" / f"{day}.jsonl", data)

        print(f"heartbeat worker={worker} event={data.get('event', '')} status={data.get('status', '')}", flush=True)
        self.send_json(200, {"ok": True, "worker": worker})


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"pearl fleet heartbeat receiver listening on {BIND}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
