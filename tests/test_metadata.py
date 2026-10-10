"""Cover cache fixtures use original generated pixels, not remote images."""

import asyncio
import gzip
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.request import Request

from PIL import Image

from novelist.metadata import CoverCache, CoverRetryableError, _ScopedRedirect, _decode_body, _image_extension, _image_target, cover_candidates, extract_cover_url, normalize_book_id, normalize_cover_url


def png_bytes():
    output = BytesIO()
    Image.new("RGB", (40, 60), color=(27, 71, 81)).save(output, format="PNG")
    return output.getvalue()


PAGE_URL = "https://www.jjwxc.net/onebook.php?novelid=42"
COVER_URL = "https://i4-static.jjwxc.net/authorimagespace.php?path=public&imageName=fixture.jpg"
HTML = f'<html><img src="{COVER_URL}" class="noveldefaultimage" width="200" height="280"></html>'.encode()


class CoverParsingTests(unittest.TestCase):
    def test_explicit_cover_wins_over_social_image_and_navigation_logo(self):
        markup = f'<meta property="og:image" content="https://static.jjwxc.net/social.png"><img src="https://static.jjwxc.net/logo.gif"><img class="noveldefaultimage other" src="{COVER_URL}">'
        self.assertEqual(extract_cover_url(markup, PAGE_URL), COVER_URL)
        self.assertEqual(normalize_cover_url("//i4-static.jjwxc.net/a.jpg#fragment"), "https://i4-static.jjwxc.net/a.jpg")
        self.assertIsNone(extract_cover_url('<img src="https://static.jjwxc.net/logo.gif">', PAGE_URL))

    def test_only_numeric_ids_and_official_image_hosts_are_allowed(self):
        self.assertEqual(normalize_book_id("00042"), "42")
        for value in ("../42", "0", "42?x=1", "1" * 13, None, 42):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_book_id(value)
        for value in ("https://evil.example/a.png", "https://i4-static.jjwxc.net.evil.example/a.png", "https://my.jjwxc.net/backend/userinfo.php", "https://user:pass@i4-static.jjwxc.net/a.png", "https://i4-static.jjwxc.net:8443/a.png", "data:image/png;base64,a", "file:///tmp/image.png"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_cover_url(value)

    def test_redirect_is_rejected_before_external_request(self):
        handler = _ScopedRedirect(normalize_cover_url)
        request = Request(COVER_URL)
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/external.jpg")
        redirect = handler.redirect_request(request, None, 302, "Found", {}, "https://i1-static.jjwxc.net/cover.jpg")
        self.assertEqual(redirect.full_url, "https://i1-static.jjwxc.net/cover.jpg")

    def test_gzip_response_is_decoded_with_a_strict_expansion_limit(self):
        self.assertEqual(_decode_body(gzip.compress(HTML), "gzip", 4096), HTML)
        with self.assertRaises(ValueError):
            _decode_body(gzip.compress(b"a" * 10000), "gzip", 1024)
        with self.assertRaises(ValueError):
            _decode_body(gzip.compress(HTML)[:-3], "gzip", 4096)

    def test_decoded_raster_handles_official_png_mislabeled_as_jpeg(self):
        self.assertEqual(_image_extension(png_bytes(), "image/png"), ".png")
        self.assertEqual(_image_extension(png_bytes(), "image/jpeg"), ".png")
        self.assertEqual(_image_extension(png_bytes(), "application/octet-stream"), ".png")
        with self.assertRaises(Exception):
            _image_extension(b"<svg></svg>", "image/png")
        with self.assertRaises(ValueError):
            _image_extension(png_bytes(), "text/html")

    def test_verified_external_author_image_precedes_official_title_cover(self):
        source = "https://pic1.imgdb.cn/i/original.png"
        self.assertEqual(cover_candidates(f'<img class="noveldefaultimage" src="{source}">', PAGE_URL, "42"), [source, "https://i9-static.jjwxc.net/novelimage.php?novelid=42"])
        self.assertEqual(normalize_cover_url("https://bbs-static.jjwxc.net/upload/cover.jpeg"), "https://bbs-static.jjwxc.net/upload/cover.jpeg")
        with self.assertRaises(ValueError):
            normalize_cover_url("http://pic1.imgdb.cn/i/original.png")

    def test_external_dns_and_redirect_must_stay_on_public_verified_hosts(self):
        address = "https://pic1.imgdb.cn/i/original.png"
        for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "198.18.0.2"):
            with self.subTest(ip=ip), patch("novelist.metadata.socket.getaddrinfo", return_value=[(2, 1, 6, "", (ip, 443))]), self.assertRaises(ValueError):
                _image_target(address)
        with patch("novelist.metadata.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            self.assertEqual(_image_target(address), address)
        for local, allowed in (("198.18.0.2", True), ("127.0.0.1", False), ("10.0.0.1", False)):
            records = [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", (local, 443))]
            with self.subTest(proxy_pair=local), patch("novelist.metadata.socket.getaddrinfo", return_value=records):
                if allowed:
                    self.assertEqual(_image_target(address), address)
                else:
                    with self.assertRaises(ValueError):
                        _image_target(address)
        with self.assertRaises(ValueError):
            _image_target("https://unverified.example/cover.png")


class CoverCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.cache = CoverCache(self.folder, request_interval=0, retry_delay=0)

    async def response(self, url, kind, referer):
        self.assertIn("onebook.php?novelid=", referer)
        await asyncio.sleep(0.01)
        return (HTML, "text/html", "utf-8") if kind == "page" else (png_bytes(), "image/png", "utf-8")

    async def test_only_requested_book_is_loaded_and_concurrent_requests_share_one_fetch(self):
        self.cache._request = AsyncMock(side_effect=self.response)
        self.assertEqual(list(self.folder.iterdir()), [])
        results = await asyncio.gather(*(self.cache.get("42") for _ in range(12)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(results[0].name, "42.png")
        self.assertEqual(results[0].read_bytes(), png_bytes())
        self.assertEqual(self.cache._request.await_count, 2)
        self.assertEqual(await self.cache.get("42"), results[0])
        self.assertEqual(self.cache._request.await_count, 2)
        self.assertFalse(any(path.suffix == ".tmp" for path in self.folder.iterdir()))

    async def test_missing_or_failed_cover_has_persistent_negative_cache(self):
        self.cache._request = AsyncMock(return_value=(b"<html>No cover</html>", "text/html", "utf-8"))
        self.assertIsNone(await self.cache.get("42"))
        self.assertIsNone(await self.cache.get("42"))
        self.assertEqual(self.cache._request.await_count, 1)
        another = CoverCache(self.folder)
        another._request = AsyncMock(side_effect=AssertionError("Cache must prevent network access"))
        self.assertIsNone(await another.get("42"))
        another._request.assert_not_awaited()
        self.assertEqual(json.loads((self.folder / "42.json").read_text())["reason"], "missing")

    async def test_failed_refresh_keeps_previous_valid_cover_atomically(self):
        previous = self.folder / "42.png"
        previous.write_bytes(png_bytes())
        old = time.time() - 10 * 86400
        os.utime(previous, (old, old))
        self.cache._request = AsyncMock(side_effect=OSError("simulated network failure"))
        self.assertEqual(await self.cache.get("42"), previous)
        self.assertEqual(previous.read_bytes(), png_bytes())

    async def test_bad_author_image_falls_back_to_official_cover(self):
        external = "https://pic1.imgdb.cn/i/original.png"

        async def response(url, kind, referer):
            if kind == "page":
                return f'<img class="noveldefaultimage" src="{external}">'.encode(), "text/html", "utf-8"
            if url == external:
                raise ValueError("Image unavailable")
            return png_bytes(), "image/jpeg", "utf-8"

        self.cache._request = AsyncMock(side_effect=response)
        result = await self.cache.get("42")
        self.assertEqual(result.name, "42.png")
        self.assertEqual(result.read_bytes(), png_bytes())
        self.assertEqual(self.cache._request.await_count, 3)

    async def test_explicit_refresh_can_replace_a_cached_fallback_with_author_image(self):
        previous = self.folder / "42.png"
        previous.write_bytes(png_bytes())
        output = BytesIO()
        Image.new("RGB", (40, 60), color="red").save(output, format="JPEG")
        author_image = output.getvalue()

        async def response(url, kind, referer):
            return (HTML, "text/html", "utf-8") if kind == "page" else (author_image, "image/jpeg", "utf-8")

        self.cache._request = AsyncMock(side_effect=response)
        result = await self.cache.get("42", refresh=True)
        self.assertEqual(result.name, "42.jpg")
        self.assertEqual(result.read_bytes(), author_image)
        self.assertFalse(previous.exists())

    async def test_transient_failure_retries_then_is_retryable_not_a_cached_missing_cover(self):
        self.cache._request = AsyncMock(side_effect=OSError("temporary connection failure"))
        with self.assertRaises(CoverRetryableError):
            await self.cache.get("42")
        self.assertEqual(self.cache._request.await_count, 2)
        marker = json.loads((self.folder / "42.json").read_text())
        self.assertEqual(marker["reason"], "temporary")
        self.assertLess(marker["retry_after"] - time.time(), 16)
        with self.assertRaises(CoverRetryableError):
            await self.cache.get("42")
        self.assertEqual(self.cache._request.await_count, 2)
        self.cache._request = AsyncMock(side_effect=self.response)
        self.assertIsNotNone(await self.cache.get("42", refresh=True))

    async def test_legacy_negative_cache_is_rechecked_after_parser_fix(self):
        (self.folder / "42.json").write_text(json.dumps({"retry_after": time.time() + 900}))
        self.cache._request = AsyncMock(side_effect=self.response)
        self.assertIsNotNone(await self.cache.get("42"))
        self.assertEqual(self.cache._request.await_count, 2)

    async def test_saturated_queue_is_retryable_and_does_not_cache_false_missing_cover(self):
        started, release = asyncio.Event(), asyncio.Event()
        self.cache.max_pending = 1

        async def response(url, kind, referer):
            started.set()
            await release.wait()
            return await self.response(url, kind, referer)

        self.cache._request = response
        first = asyncio.create_task(self.cache.get("42"))
        await started.wait()
        try:
            with self.assertRaises(CoverRetryableError) as error:
                await self.cache.get("43")
            self.assertEqual(error.exception.retry_after, 2)
            self.assertFalse((self.folder / "43.json").exists())
        finally:
            release.set()
            await first

    async def test_cover_fetches_have_bounded_book_concurrency(self):
        active = maximum = 0

        async def response(url, kind, referer):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                return await self.response(url, kind, referer)
            finally:
                active -= 1

        self.cache._request = response
        results = await asyncio.gather(*(self.cache.get(str(i)) for i in range(1, 8)))
        self.assertTrue(all(path and path.is_file() for path in results))
        self.assertLessEqual(maximum, 2)


if __name__ == "__main__":
    unittest.main()
