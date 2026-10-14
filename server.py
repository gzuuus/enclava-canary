#!/usr/bin/env python3
"""enclava-canary — minimal healthy workload for custom-image deploy tests.

Serves a polished page that surfaces the in-pod attestation-proxy's verified
claims (SEV-SNP attestation info + ownership status), proving the app really
runs inside a confidential VM. `/health` is a bare 200 for anyone curling it;
the platform's readiness probe is a TCP-socket connect on the EXPOSE port.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import socket
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PORT = int(os.environ.get("PORT", "8080"))
PROXY = os.environ.get("ATTESTATION_PROXY", "http://127.0.0.1:8081")
HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"
VISITS = Path(os.environ.get("VISITS_FILE", "/app/data/visits"))


def fetch(path: str, timeout: float = 2.0):
    """GET <proxy><path> as JSON. Never raises — returns {"_error": ...} so the
    page degrades gracefully (e.g. /status 404s in auto-unlock mode)."""
    try:
        with urllib.request.urlopen(f"{PROXY}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # ponytail: one net for all failures; demo only.
        return {"_error": f"{type(e).__name__}: {e}"}


def bump_visits() -> int | None:
    """Tiny visit counter proving the storage.paths bind at /app/data is
    writable by the workload. None when the mount is absent/unwritable — the
    page then shows "no /app/data mount", which is honest, not a crash."""
    try:
        VISITS.parent.mkdir(parents=True, exist_ok=True)
        n = (int(VISITS.read_text().strip()) + 1) if VISITS.exists() else 1
        VISITS.write_text(str(n))
        return n
    except Exception:
        return None


# --- guest-report route -------------------------------------------------
# GET /guest-report?nonce=<b64url-32B> — structured in-guest state for the
# e2e suite's headless rows (the SSH replacement). Fixed field list: no shell,
# no arbitrary paths. A CLAIM from the workload container (same vantage SSH
# had); the attested truth stays in the sidecar's /status and the proof bundle.
# Nonce semantics mirror the attestation-proxy's proof_nonce: exactly one
# `nonce` param, padding-tolerant base64, exactly 32 bytes decoded.

REPORT_NONCE_BYTES = 32
REPORT_NONCE_MAX_CHARS = 64


def parse_report_nonce(query: str) -> str | None:
    values = []
    for field in query.split("&"):
        if "=" in field:
            key, value = field.split("=", 1)
            if key == "nonce":
                values.append(unquote(value))
    if len(values) != 1:
        return None
    raw = values[0]
    if not raw or len(raw) > REPORT_NONCE_MAX_CHARS:
        return None
    stripped = raw.rstrip("=")
    if not stripped or "=" in stripped:  # mid-string padding is invalid
        return None
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except Exception:
        return None
    return raw if len(decoded) == REPORT_NONCE_BYTES else None


def _mount_source(path: str) -> str | None:
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == path:
                return parts[0]
    except OSError:
        pass
    return None


def _block_devices() -> list:
    out = []
    try:
        for dev in sorted(Path("/sys/block").glob("*")):
            try:
                out.append({"name": dev.name, "size_sectors": int((dev / "size").read_text().strip())})
            except (OSError, ValueError):
                continue
            if len(out) >= 32:  # bounded output
                break
    except OSError:
        pass
    return out


def guest_report_payload(nonce: str) -> dict:
    info = fetch("/v1/attestation/info")
    status = fetch("/status")
    u = platform.uname()
    try:
        mapper = sorted(p.name for p in Path("/dev/mapper").iterdir())
    except OSError:
        mapper = []  # absent in the workload container — namespace artifact
    return {
        "nonce": nonce,
        "generated_at": int(time.time()),
        "vantage": "workload-container",
        "uname": {"sys": u.system, "release": u.release, "machine": u.machine},
        "init_ready": Path("/run/enclava/init-ready").exists(),
        "mounts": {p: _mount_source(p) for p in ("/state", "/app/data")},
        "block_devices": _block_devices(),
        "mapper_devices": mapper,
        "sev": {
            "dev_present": any(Path("/dev").glob("sev*")),
            "attestation_type": info.get("attestation_type"),
            "runtime_class": info.get("runtime_class"),
        },
        "sidecar": {
            "reachable": "_error" not in status,
            "claims_verified": status.get("claims_verified"),
            "config_ready": status.get("config_ready"),
            "state": status.get("state"),
            "mode": status.get("mode"),
            "error": status.get("error"),
        },
    }


class H(BaseHTTPRequestHandler):
    server_version = "enclava-canary"

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self._send(200, b"ok", "text/plain")
        elif path == "/guest-report":
            nonce = parse_report_nonce(urlsplit(self.path).query)
            if nonce is None:
                self._send_json(400, {"error": "nonce_invalid"})
            else:
                self._send_json(200, guest_report_payload(nonce))
        elif path == "/":
            payload = {
                "attestation_info": fetch("/v1/attestation/info"),
                "ownership_status": fetch("/status"),
                "guest": {"hostname": socket.gethostname(), "visits": bump_visits()},
            }
            page = INDEX.read_text().replace("__ATTESTATION_JSON__", json.dumps(payload))
            self._send(200, page.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        body = json.dumps(payload).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # ponytail: enclava relays logs encrypted; keep stderr quiet.
        pass


if __name__ == "__main__":
    print(f"enclava-canary listening on 0.0.0.0:{PORT} (proxy={PROXY})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
