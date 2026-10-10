"""Opt-in localhost-only Chromium cookie persistence experiment.

Run with NOVELIST_SESSION_TEST=1. Every profile is temporary; no application
profile, real account, real cookie, or external URL is opened or inspected.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright


COOKIE_VALUES = {
    "fixture_session": "fake-session-value",
    "fixture_persistent": "fake-persistent-value",
}


class FakeLoginHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/status":
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            result = {name: name in cookie and cookie[name].value == value for name, value in COOKIE_VALUES.items()}
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            # Neither this endpoint nor /stable ever recreates cookies. Restoring
            # the last tab therefore cannot accidentally fake persistence.
            body = b'<form action="/login" method="post"><button>Sign in to fixture</button></form>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path != "/login":
            self.send_error(404)
            return
        self.send_response(303)
        self.send_header("Location", "/stable")
        self.send_header("Set-Cookie", "fixture_session=fake-session-value; Path=/; HttpOnly; SameSite=Lax")
        self.send_header("Set-Cookie", "fixture_persistent=fake-persistent-value; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")
        self.send_header("Content-Length", "0")
        self.end_headers()


@unittest.skipUnless(os.environ.get("NOVELIST_SESSION_TEST") == "1", "Opt in to isolated headed-browser restart test with NOVELIST_SESSION_TEST=1")
class SessionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_headed_to_headless_session_and_expiring_cookies(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLoginHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        reports = []
        try:
            async with async_playwright() as playwright:
                for name, first_args, restart_args, reapply_in_memory in (
                    ("default_both", [], [], False),
                    ("restore_on_restart", [], ["--restore-last-session"], False),
                    ("restore_both", ["--restore-last-session"], ["--restore-last-session"], False),
                    ("in_memory_handoff", [], [], True),
                ):
                    with self.subTest(case=name), tempfile.TemporaryDirectory(prefix="novelist-fake-login-") as temp:
                        profile = str(Path(temp) / "profile")
                        context = None
                        try:
                            context = await playwright.chromium.launch_persistent_context(profile, headless=False, args=first_args)
                            version = context.browser.version
                            page = context.pages[0] if context.pages else await context.new_page()
                            await page.goto(origin + "/signin")
                            await page.get_by_role("button", name="Sign in to fixture").click()
                            await page.wait_for_url(origin + "/stable")
                            initial = {cookie["name"]: cookie for cookie in await context.cookies(origin)}
                            self.assertEqual(initial["fixture_session"]["expires"], -1)
                            self.assertGreater(initial["fixture_persistent"]["expires"], time.time())
                            self.assertTrue(all(initial[key]["httpOnly"] for key in COOKIE_VALUES))
                            await context.close()
                            context = None

                            context = await playwright.chromium.launch_persistent_context(profile, headless=True, args=restart_args)
                            if reapply_in_memory:
                                await context.add_cookies(list(initial.values()))
                            restored = {cookie["name"]: cookie for cookie in await context.cookies(origin)}
                            page = context.pages[0] if context.pages else await context.new_page()
                            response = await page.goto(origin + "/status")
                            sent_to_server = await response.json()
                            self.assertTrue(sent_to_server["fixture_persistent"])
                            if reapply_in_memory:
                                self.assertTrue(sent_to_server["fixture_session"])
                            self.assertEqual(set(restored), {key for key, present in sent_to_server.items() if present})
                            reports.append({
                                "case": name,
                                "chromium_version": version,
                                "session_cookie_survived": sent_to_server["fixture_session"],
                                "persistent_cookie_survived": sent_to_server["fixture_persistent"],
                                "restored_expiry": {key: value["expires"] for key, value in restored.items()},
                            })
                        finally:
                            if context is not None:
                                await context.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        print("\nISOLATED_COOKIE_PERSISTENCE=" + json.dumps(reports, sort_keys=True), flush=True)
        self.assertEqual(len(reports), 4)


if __name__ == "__main__":
    unittest.main()
