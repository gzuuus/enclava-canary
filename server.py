#!/usr/bin/env python3
"""enclava-canary — minimal healthy workload for custom-image deploy tests.

Serves a polished page that surfaces the in-pod attestation-proxy's verified
claims (SEV-SNP attestation info + ownership status), proving the app really
runs inside a confidential VM. `/health` is a bare 200 for anyone curling it;
the platform's readiness probe is a TCP-socket connect on the EXPOSE port.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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


class H(BaseHTTPRequestHandler):
    server_version = "enclava-canary"

    def do_GET(self):
        if self.path == "/health":
            self._send(200, b"ok", "text/plain")
        elif self.path == "/":
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

    def log_message(self, *a):  # ponytail: enclava relays logs encrypted; keep stderr quiet.
        pass


if __name__ == "__main__":
    print(f"enclava-canary listening on 0.0.0.0:{PORT} (proxy={PROXY})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
