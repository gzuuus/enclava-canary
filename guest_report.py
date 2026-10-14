#!/usr/bin/env python3
"""Reference client for the canary's guest-report route — the nightly's
assertion shape: fetch with a fresh nonce, verify the echo + no-store +
pinned schema, assert fields, print the report. Exit 0 iff all checks pass.

  python3 guest_report.py https://<origin> [--assert sidecar.state=unlocked] ...

Assertion values: `true`/`false`/`null` compare as literals, integer-looking
values as ints, everything else as strings. Dotted paths address nested fields
(`sidecar.claims_verified`). The report itself is a CLAIM from the workload —
attested truth stays on proof-bundle + TEE /status (see README).
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
import urllib.request

# Top-level field contract, pinned alongside test_server.SchemaPin — changing
# either is a contract change for the nightly.
REPORT_FIELDS = {
    "nonce", "generated_at", "vantage", "uname", "init_ready", "mounts",
    "block_devices", "mapper_devices", "sev", "sidecar",
}

_MISSING = object()


def fetch_report(origin: str, timeout: float = 15.0):
    """Returns (report, checks). Never raises: failures become failed checks."""
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        req = urllib.request.Request(f"{origin.rstrip('/')}/guest-report?nonce={nonce}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            headers, body = dict(r.headers), json.loads(r.read().decode())
    except Exception as e:
        check("fetch", False, f"{type(e).__name__}: {e}")
        return None, checks

    check("nonce_echo", body.get("nonce") == nonce)
    check("no_store", headers.get("Cache-Control") == "no-store",
          headers.get("Cache-Control", ""))
    check("schema", set(body) == REPORT_FIELDS,
          f"missing={sorted(REPORT_FIELDS - set(body))} extra={sorted(set(body) - REPORT_FIELDS)}")
    return body, checks


def resolve(body, path: str):
    cur = body
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def expected_value(raw: str):
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("origin", help="app origin, e.g. https://<app>.<org>.dev.enclava.work")
    ap.add_argument("--assert", action="append", default=[], dest="assertions",
                    metavar="PATH=VALUE",
                    help="field assertion (dotted path; true/false/null/int/string)")
    args = ap.parse_args(argv)

    body, checks = fetch_report(args.origin)

    for spec in args.assertions:
        path, sep, raw = spec.partition("=")
        if not sep:
            print(f"malformed assertion (want PATH=VALUE): {spec}", file=sys.stderr)
            return 2
        actual = _MISSING if body is None else resolve(body, path)
        want = expected_value(raw)
        if actual is _MISSING:
            checks.append({"check": f"assert:{spec}", "ok": False, "detail": "field missing"})
        else:
            ok = actual == want or (want is None and actual is None) or str(actual) == raw
            checks.append({"check": f"assert:{spec}", "ok": ok,
                           "detail": f"actual={actual!r}"})

    print(json.dumps({"report": body, "checks": checks}, indent=2))
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
