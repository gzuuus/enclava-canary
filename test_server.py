#!/usr/bin/env python3
"""Tests for the guest-report route: nonce validation, the pinned field
schema (the nightly asserts these fields), and the HTTP contract."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import server
import guest_report

VALID_NONCE = base64.urlsafe_b64encode(b"x" * server.REPORT_NONCE_BYTES).decode().rstrip("=")


class NonceValidation(unittest.TestCase):
    def test_accepts_urlsafe_unpadded_and_padded(self):
        self.assertEqual(server.parse_report_nonce(f"nonce={VALID_NONCE}"), VALID_NONCE)
        padded = VALID_NONCE + "=" * ((4 - len(VALID_NONCE) % 4) % 4)
        self.assertEqual(server.parse_report_nonce(f"nonce={padded}"), padded)

    def test_rejects_missing_or_duplicate(self):
        self.assertIsNone(server.parse_report_nonce(""))
        self.assertIsNone(server.parse_report_nonce("a=1"))
        self.assertIsNone(server.parse_report_nonce(f"nonce={VALID_NONCE}&nonce={VALID_NONCE}"))

    def test_rejects_wrong_length_and_bad_encoding(self):
        short = base64.urlsafe_b64encode(b"too short").decode().rstrip("=")
        self.assertIsNone(server.parse_report_nonce(f"nonce={short}"))
        self.assertIsNone(server.parse_report_nonce("nonce=!!!not-base64!!!"))
        self.assertIsNone(server.parse_report_nonce("nonce=" + "A" * (server.REPORT_NONCE_MAX_CHARS + 1)))


class SchemaPin(unittest.TestCase):
    """The nightly asserts these fields by name — changes here are contract
    changes and must land with the suite updated."""

    def test_top_level_fields(self):
        payload = server.guest_report_payload(VALID_NONCE)
        self.assertEqual(
            set(payload),
            {"nonce", "generated_at", "vantage", "uname", "init_ready", "mounts",
             "block_devices", "mapper_devices", "sev", "sidecar"},
        )
        # Two-sided pin: the client's field contract matches the server's.
        self.assertEqual(set(payload), guest_report.REPORT_FIELDS)
        self.assertEqual(payload["nonce"], VALID_NONCE)
        self.assertEqual(payload["vantage"], "workload-container")
        self.assertEqual(set(payload["uname"]), {"sys", "release", "machine"})
        self.assertEqual(set(payload["mounts"]), {"/state", "/app/data"})
        self.assertEqual(set(payload["sev"]), {"dev_present", "attestation_type", "runtime_class"})
        self.assertEqual(
            set(payload["sidecar"]),
            {"reachable", "claims_verified", "config_ready", "state", "mode", "error"},
        )
        for dev in payload["block_devices"]:
            self.assertEqual(set(dev), {"name", "size_sectors"})

    def test_output_is_bounded(self):
        payload = server.guest_report_payload(VALID_NONCE)
        self.assertLessEqual(len(payload["block_devices"]), 32)
        self.assertLess(len(json.dumps(payload)), 16 * 1024)


class Route(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        def parse(code, headers, raw):
            try:
                body = json.loads(raw.decode())
            except ValueError:
                body = raw.decode()
            return code, headers, body
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return parse(r.status, dict(r.headers), r.read())
        except urllib.error.HTTPError as e:
            return parse(e.code, dict(e.headers), e.read())

    def test_valid_nonce_serves_report(self):
        status, headers, body = self.get(f"/guest-report?nonce={VALID_NONCE}")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(body["nonce"], VALID_NONCE)

    def test_invalid_nonce_is_400(self):
        status, _, body = self.get("/guest-report?nonce=bad")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "nonce_invalid"})

    def test_unknown_path_still_404s(self):
        status, _, _ = self.get("/guest-report-evil")
        self.assertEqual(status, 404)


class Helper(unittest.TestCase):
    """The nightly assertion shape: guest_report.py against a live route."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        cls.origin = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_passes_on_matching_assertions(self):
        body, checks = guest_report.fetch_report(self.origin)
        self.assertIsNotNone(body)
        self.assertTrue(all(c["ok"] for c in checks), checks)

    def run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return guest_report.main(argv)

    def test_fails_on_wrong_assertion(self):
        self.assertEqual(self.run_main([self.origin, "--assert", "vantage=host-container"]), 1)

    def test_passes_on_true_assertion(self):
        self.assertEqual(self.run_main([self.origin, "--assert", "uname.sys=Linux"]), 0)


if __name__ == "__main__":
    unittest.main()
