"""Native Messaging tests. Storage and live application checks are mocked."""

import base64
import errno
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from novelist import native_host as host
from novelist.session import normalize_cookies


def fixture_cookie(**changes):
    value = {"name": "test_login", "value": "FAKE_VALUE_NEVER_ECHO", "domain": ".jjwxc.net", "path": "/", "secure": True, "httpOnly": True, "session": True, "sameSite": "lax"}
    value.update(changes)
    return value


def frame(message):
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return struct.pack("=I", len(data)) + data


class FramingTests(unittest.TestCase):
    def test_round_trip_and_clean_eof(self):
        stream = io.BytesIO()
        host.write_message(stream, {"ok": True, "message": "连接就绪"})
        stream.seek(0)
        self.assertEqual(host.read_message(stream), {"ok": True, "message": "连接就绪"})
        self.assertIsNone(host.read_message(stream))

    def test_rejects_oversized_frame_before_reading_payload(self):
        stream = io.BytesIO(struct.pack("=I", host.MAX_MESSAGE_BYTES + 1) + b"not-read")
        with self.assertRaises(host.ProtocolError):
            host.read_message(stream)
        self.assertEqual(stream.tell(), 4)

    def test_malformed_or_truncated_frames(self):
        for payload in (
            b"\x01\x00", struct.pack("=I", 0), struct.pack("=I", 5) + b"{}",
            struct.pack("=I", 1) + b"\xff", struct.pack("=I", 1) + b"{", frame([]),
        ):
            with self.subTest(payload=payload), self.assertRaises(host.ProtocolError):
                host.read_message(io.BytesIO(payload))

    def test_main_outputs_only_framed_response(self):
        output = io.BytesIO()
        self.assertEqual(host.main(io.BytesIO(frame({"action": "ping"})), output, [host.ALLOWED_ORIGIN]), 0)
        output.seek(0)
        self.assertTrue(host.read_message(output)["ok"])
        self.assertIsNone(host.read_message(output))

    def test_main_framed_error_does_not_echo_invalid_input(self):
        output = io.BytesIO()
        raw = b"secret-invalid-json"
        self.assertEqual(host.main(io.BytesIO(struct.pack("=I", len(raw)) + raw), output, [host.ALLOWED_ORIGIN]), 1)
        self.assertNotIn(raw, output.getvalue())
        output.seek(0)
        self.assertFalse(host.read_message(output)["ok"])


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.bridge = patch.object(host, "import_backend_session").start()
        self.addCleanup(patch.stopall)

    def test_ping_does_not_access_application_or_storage(self):
        self.assertTrue(host.handle_message({"action": "ping"})["ok"])
        self.bridge.assert_not_called()

    def test_success_saves_without_returning_auth_data(self):
        cookie = fixture_cookie()
        response = host.handle_message({"action": "connect", "cookies": [cookie]})
        self.assertTrue(response["ok"])
        self.bridge.assert_called_once_with([cookie])
        self.assertEqual(set(response), {"ok", "message"})
        self.assertNotIn(cookie["value"], json.dumps(response))
        self.assertNotIn(cookie["name"], json.dumps(response))

    def test_backend_guard_failure_is_reported_without_local_fallback(self):
        self.bridge.side_effect = host.ProtocolError("请先暂停当前任务。")
        response = host.handle_message({"action": "connect", "cookies": [fixture_cookie()]})
        self.assertFalse(response["ok"])
        self.assertIn("暂停", response["message"])

    def test_rejects_domains_outside_jinjiang_before_any_storage_or_state_access(self):
        for domain in ("example.com", ".jjwxc.net.evil.example", "notjjwxc.net", "bad..jjwxc.net", "jjwxc.net/", "user@jjwxc.net", "..jjwxc.net"):
            with self.subTest(domain=domain):
                self.assertFalse(host.handle_message({"action": "connect", "cookies": [fixture_cookie(domain=domain)]})["ok"])
        self.bridge.assert_not_called()

    def test_accepts_only_jinjiang_apex_and_subdomains(self):
        for domain in ("jjwxc.net", ".jjwxc.com", "my.jjwxc.net", ".wap.jjwxc.com"):
            with self.subTest(domain=domain):
                self.assertEqual(host.validate_cookies([fixture_cookie(domain=domain)])[0]["domain"], domain)

    def test_rejects_malformed_messages_cookie_shapes_and_limits(self):
        cases = [None, [], {"action": "unknown"}, {"action": "connect"}, {"action": "connect", "cookies": {}}, {"action": "connect", "cookies": []}]
        for cookies in ([None], [fixture_cookie(path="relative")], [fixture_cookie(value=4)], [fixture_cookie(expirationDate=float("nan"))], [fixture_cookie(expirationDate=True)], [fixture_cookie(secure="yes")], [fixture_cookie(sameSite="bad")], [fixture_cookie()] * (host.MAX_COOKIES + 1)):
            cases.append({"action": "connect", "cookies": cookies})
        for index, message in enumerate(cases):
            with self.subTest(case=index):
                self.assertFalse(host.handle_message(message)["ok"])
        self.bridge.assert_not_called()

    def test_storage_exception_cannot_leak_cookie_values(self):
        self.bridge.side_effect = RuntimeError("FAKE_VALUE_NEVER_ECHO")
        response = host.handle_message({"action": "connect", "cookies": [fixture_cookie()]})
        self.assertFalse(response["ok"])
        self.assertNotIn("FAKE_VALUE_NEVER_ECHO", json.dumps(response))


class OriginAndBackendTests(unittest.TestCase):
    def test_rejects_missing_wrong_or_additional_origin_arguments_before_payload_read(self):
        for arguments in ([], ["chrome-extension://other/"], [host.ALLOWED_ORIGIN.rstrip("/")], [host.ALLOWED_ORIGIN, "extra"]):
            source, output = io.BytesIO(frame({"action": "connect", "cookies": [fixture_cookie()]})), io.BytesIO()
            with self.subTest(arguments=arguments), patch.object(host, "handle_message") as handler:
                self.assertEqual(host.main(source, output, arguments), 1)
                self.assertEqual(source.tell(), 0)
                handler.assert_not_called()
                self.assertNotIn(b"FAKE_VALUE_NEVER_ECHO", output.getvalue())

    def test_connection_refused_requires_running_app(self):
        opener = MagicMock()
        opener.open.side_effect = URLError(ConnectionRefusedError(errno.ECONNREFUSED, "test fixture refused"))
        with patch.object(host, "build_opener", return_value=opener), self.assertRaisesRegex(host.ProtocolError, "先打开拾页"):
            host.import_backend_session([fixture_cookie()])

    def test_network_timeout_fails_closed(self):
        opener = MagicMock()
        opener.open.side_effect = URLError(TimeoutError("fixture timeout"))
        with patch.object(host, "build_opener", return_value=opener), self.assertRaises(host.ProtocolError):
            host.import_backend_session([fixture_cookie()])

    def test_http_error_body_is_never_read_or_echoed(self):
        for code in (400, 404, 409, 500):
            secret_body = io.BytesIO(b"FAKE_VALUE_NEVER_ECHO")
            opener = MagicMock()
            opener.open.side_effect = HTTPError(host.BACKEND_IMPORT_URL, code, "FAKE_VALUE_NEVER_ECHO", {}, secret_body)
            with self.subTest(code=code), patch.object(host, "build_opener", return_value=opener), self.assertRaises(host.ProtocolError) as error:
                host.import_backend_session([fixture_cookie()])
            self.assertNotIn("FAKE_VALUE_NEVER_ECHO", str(error.exception))
            self.assertEqual(secret_body.tell(), 0)

    def test_backend_success_uses_only_loopback_json_post(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"ok":true,"message":"ignored"}'
        with patch.object(host, "build_opener", return_value=opener):
            host.import_backend_session([fixture_cookie()])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8765/api/account/import")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {"cookies": [fixture_cookie()]})

    def test_backend_unexpected_or_oversized_responses_fail_closed(self):
        for response in (b'null', b'{"ok":false}', b'{"ok":1}', b'FAKE_VALUE_NEVER_ECHO', b'x' * (host.MAX_RESPONSE_BYTES + 1)):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = response
            with self.subTest(size=len(response)), patch.object(host, "build_opener", return_value=opener), self.assertRaises(host.ProtocolError) as error:
                host.import_backend_session([fixture_cookie()])
            self.assertNotIn("FAKE_VALUE_NEVER_ECHO", str(error.exception))

    def test_extension_permissions_and_fixed_public_id(self):
        manifest = json.loads((Path(__file__).resolve().parent.parent / "chrome-extension" / "manifest.json").read_text())
        self.assertEqual(set(manifest["permissions"]), {"cookies", "nativeMessaging"})
        self.assertEqual(set(manifest["host_permissions"]), {"*://*.jjwxc.net/*", "*://*.jjwxc.com/*"})
        digest = hashlib.sha256(base64.b64decode(manifest["key"], validate=True)).hexdigest()[:32]
        identifier = "".join(chr(ord("a") + int(nibble, 16)) for nibble in digest)
        self.assertEqual(identifier, "ebiomijpkjmejjdbpokfcjmdjlopedkk")
        self.assertEqual(host.ALLOWED_ORIGIN, f"chrome-extension://{identifier}/")


class SessionNormalizationTests(unittest.TestCase):
    def test_chrome_and_playwright_shapes_normalize(self):
        chrome = fixture_cookie(domain=".JJWXC.NET", session=False, expirationDate=9999999999, sameSite="no_restriction")
        result = normalize_cookies([chrome])[0]
        self.assertEqual(result["domain"], ".jjwxc.net")
        self.assertEqual(result["expires"], 9999999999)
        self.assertEqual(result["sameSite"], "None")
        self.assertEqual(normalize_cookies([result]), [result])

    def test_expired_and_partitioned_cookies_are_not_flattened(self):
        self.assertEqual(normalize_cookies([fixture_cookie(session=False, expirationDate=0), fixture_cookie(partitionKey={})]), [])

    def test_rejects_invalid_domains_flags_expiry_controls_and_limits(self):
        invalid = [fixture_cookie(domain="..jjwxc.net"), fixture_cookie(domain="bad..jjwxc.net"), fixture_cookie(domain="jjwxc.net.evil.test"), fixture_cookie(secure="false"), fixture_cookie(httpOnly=1), fixture_cookie(session="yes"), fixture_cookie(expirationDate=True), fixture_cookie(expirationDate=float("inf")), fixture_cookie(expires=-2), fixture_cookie(sameSite="invalid"), fixture_cookie(name="a\nb"), fixture_cookie(value="a\rb"), fixture_cookie(path="/a\0b"), fixture_cookie(path="/" * 4097)]
        for cookie in invalid:
            with self.subTest(keys=list(cookie)), self.assertRaises(ValueError):
                normalize_cookies([cookie])
        with self.assertRaises(ValueError):
            normalize_cookies([fixture_cookie()] * (host.MAX_COOKIES + 1))


class InstallerTests(unittest.TestCase):
    def test_installer_is_scoped_atomic_and_private_in_temporary_home(self):
        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location("fixture_install_bridge", root / "scripts/install_chrome_bridge.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory(prefix="novelist-connector-test-") as folder:
            temporary_root = Path(folder) / "project with ' quote"
            for relative in (".venv/bin", ".build", "chrome-extension", "novelist"):
                (temporary_root / relative).mkdir(parents=True, exist_ok=True)
            (temporary_root / ".venv/bin/python").touch()
            (temporary_root / ".build/session-store").touch()
            (temporary_root / "chrome-extension/manifest.json").write_text((root / "chrome-extension/manifest.json").read_text())
            for filename in ("native_host.py", "session.py"):
                (temporary_root / "novelist" / filename).write_bytes((root / "novelist" / filename).read_bytes())
            temporary_home = Path(folder) / "fixture-home"
            destination = temporary_home / "Library/Application Support/Google/Chrome/NativeMessagingHosts/local.novelist.connect.json"
            destination.parent.mkdir(parents=True)
            destination.write_text("old fixture")
            destination.chmod(0o644)
            with patch.object(installer, "ROOT", temporary_root), patch.object(installer.sys, "platform", "darwin"), patch.object(installer.Path, "home", return_value=temporary_home), patch("sys.stdout", io.StringIO()):
                installer.main()
            manifest = json.loads(destination.read_text())
            self.assertEqual(manifest["allowed_origins"], [host.ALLOWED_ORIGIN])
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            wrapper = temporary_home / "Library/Application Support/Novelist/ChromeBridge/chrome-native-host"
            self.assertEqual(manifest["path"], str(wrapper))
            self.assertEqual(stat.S_IMODE(wrapper.stat().st_mode), 0o700)
            self.assertIn('"$@"', wrapper.read_text())
            self.assertNotIn(str(temporary_root), wrapper.read_text())
            self.assertEqual([file.name for file in destination.parent.iterdir()], [destination.name])
            # A packaged host still runs after the project folder is unavailable.
            temporary_root.rename(temporary_root.with_name("project-moved"))
            result = subprocess.run([str(wrapper), host.ALLOWED_ORIGIN], input=frame({"action": "ping"}), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/", timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b"")
            self.assertTrue(host.read_message(io.BytesIO(result.stdout))["ok"])


if __name__ == "__main__":
    unittest.main()
