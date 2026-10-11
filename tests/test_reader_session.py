"""Session lifecycle fixtures: no browser profile, website or Keychain access."""

import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from novelist import browser


def cookie(value):
    return {"name": "fixture", "value": value, "domain": ".jjwxc.net", "path": "/", "expires": -1, "secure": True, "httpOnly": True, "sameSite": "Lax"}


class FakePage:
    def is_closed(self):
        return False


class FakeContext:
    def __init__(self, cookies):
        self.values = copy.deepcopy(cookies)
        self.closed = False

    async def cookies(self, _urls=None):
        return copy.deepcopy(self.values)

    async def clear_cookies(self, domain):
        self.values = [value for value in self.values if not domain.search(value["domain"])]

    async def add_cookies(self, cookies):
        by_key = {(c["domain"], c["path"], c["name"]): c for c in self.values}
        by_key.update({(c["domain"], c["path"], c["name"]): copy.deepcopy(c) for c in cookies})
        self.values = list(by_key.values())

    async def close(self):
        self.closed = True


class SessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def reader(self, nonce="old"):
        reader = browser.Reader(Path("unused-fixture-profile"), headless=True)
        reader.context = FakeContext([cookie("old-account")])
        reader.page = FakePage()
        reader._nonce = nonce
        return reader

    async def test_mode_switch_applies_new_import_before_saving_old_context(self):
        reader = self.reader()
        saved = []

        async def ensure():
            reader.context = FakeContext([])
            reader.page = FakePage()

        reader.ensure = ensure
        with patch.object(browser, "connection_nonce", return_value="new"), patch.object(browser, "load_cookies_sync", return_value=[cookie("new-account")]), patch.object(browser, "save_cookies_sync", side_effect=lambda values: saved.append(copy.deepcopy(values))):
            await reader._switch_mode(False)
        self.assertFalse(reader.headless)
        self.assertEqual(saved, [[cookie("new-account")]])
        self.assertEqual(reader.context.values, [cookie("new-account")])

    async def test_expired_new_import_clears_old_profile_instead_of_falling_back(self):
        reader = self.reader()
        with patch.object(browser, "connection_nonce", return_value="new"), patch.object(browser, "load_cookies_sync", return_value=[]):
            await reader._hydrate_session()
        self.assertEqual(reader.context.values, [])
        self.assertEqual(reader._nonce, "new")

    async def test_shutdown_keeps_current_session_but_cannot_overwrite_pending_import(self):
        for current_nonce, expected in (("old", 1), ("new", 0)):
            with self.subTest(current_nonce=current_nonce):
                reader = self.reader()
                context = reader.context
                with patch.object(browser, "connection_nonce", return_value=current_nonce), patch.object(browser, "save_cookies_sync") as save:
                    await reader.close()
                self.assertEqual(save.call_count, expected)
                self.assertTrue(context.closed)
                self.assertIsNone(reader.context)

    async def test_session_snapshot_keeps_path_scoped_jinjiang_cookies_only(self):
        reader = self.reader()
        backend_cookie = {**cookie("backend-session"), "path": "/backend", "domain": "my.jjwxc.net"}
        other_site = {**cookie("unrelated-cookie"), "domain": ".example.com"}
        reader.context.values.extend([backend_cookie, other_site])
        with patch.object(browser, "save_cookies_sync") as save:
            await reader._remember_session()
        self.assertEqual(save.call_args.args[0], [cookie("old-account"), backend_cookie])

    async def test_public_reader_never_loads_or_saves_account_session(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = browser.Reader(Path(directory), hydrate_session=False)
            page = FakePage()
            page.set_default_timeout = Mock()
            context = FakeContext([cookie("public-visit")])
            context.pages = [page]
            pw = SimpleNamespace(
                chromium=SimpleNamespace(launch_persistent_context=AsyncMock(return_value=context)),
                stop=AsyncMock(),
            )
            with patch.object(browser, "async_playwright", return_value=SimpleNamespace(start=AsyncMock(return_value=pw))), \
                 patch.object(browser, "connection_nonce") as nonce, \
                 patch.object(browser, "load_cookies_sync") as load, \
                 patch.object(browser, "save_cookies_sync") as save:
                await reader.ensure()
                await reader.ensure()
                await reader._hydrate_session()
                await reader._remember_session()
                await reader._switch_mode(False)
                await reader.close()
            nonce.assert_not_called()
            load.assert_not_called()
            save.assert_not_called()
            self.assertTrue(context.closed)

    async def test_public_navigation_skips_render_wait_but_checks_destination(self):
        url = "https://www.jjwxc.net/onebook.php?novelid=42"
        for hydrate in (False, True):
            with self.subTest(hydrate=hydrate):
                reader = browser.Reader(Path("unused-fixture-profile"), hydrate_session=hydrate)
                reader.ensure = AsyncMock()
                reader.page = SimpleNamespace(
                    url=url, goto=AsyncMock(return_value=SimpleNamespace(status=200)),
                    wait_for_timeout=AsyncMock(), evaluate=AsyncMock(),
                )
                await reader.goto(url)
                reader.page.goto.assert_awaited_once_with(url, wait_until="domcontentloaded", timeout=45000)
                self.assertEqual(reader.page.wait_for_timeout.await_count, int(hydrate))
                self.assertEqual(reader.page.evaluate.await_count, int(hydrate))
                reader.page.url = "https://unrelated.invalid/"
                with self.assertRaises(browser.CaptureError):
                    await reader.goto(url)


if __name__ == "__main__":
    unittest.main()
