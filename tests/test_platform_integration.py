"""Offline checks at the platform, persistence, HTTP and engine boundaries."""

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from novelist import engine, platforms, server
from novelist.browser import CaptureError, Reader
from novelist.exporters import export_book, summarize


def chapter(identifier, ordinal, *, state="pending", text="", **extra):
    return {"id": identifier, "title": f"原创章节 {ordinal}", "ordinal": ordinal,
            "url": f"https://www.gongzicp.com/read-{identifier}.html", "state": state,
            "text": text, "raw_text": text, "review": state == "review", **extra}


def book(*chapters, platform="gongzicp"):
    url = {"gongzicp": "https://www.gongzicp.com/novel-42.html", "fanqie": "https://fanqienovel.com/page/42", "jjwxc": "https://www.jjwxc.net/onebook.php?novelid=42"}[platform]
    return {"id": platforms.book_identity(url), "url": url, "platform": platform,
            "platform_name": platform, "title": "同名原创小说", "author": "测试作者", "chapters": list(chapters)}


class FakeReader:
    def __init__(self, _profile):
        self.opened = False
        self.headless = True
        self.scan_result = None

    async def scan(self, _url):
        return copy.deepcopy(self.scan_result)


async def http_get(path, query=b""):
    """Exercise real ASGI middleware/routing without starting a server process."""
    sent = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await server.app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                      "http_version": "1.1", "method": "GET", "scheme": "http",
                      "path": path, "raw_path": path.encode(), "query_string": query,
                      "root_path": "", "headers": [(b"host", b"127.0.0.1:8765")],
                      "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8765)}, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body


class PlatformPersistenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.books = self.data / "books"
        self.exports = Path(self.temp.name) / "exports"
        self.books.mkdir(parents=True)
        self.exports.mkdir()
        for name, value in (("DATA", self.data), ("BOOKS", self.books), ("EXPORTS", self.exports), ("Reader", FakeReader)):
            context = patch.object(server, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.workbench = server.Workbench()
        context = patch.object(server, "workbench", self.workbench)
        context.start()
        self.addCleanup(context.stop)

    async def asyncTearDown(self):
        self.workbench.closing = True
        tasks = [task for task in (self.workbench.task, self.workbench.account_task, self.workbench.library_task) if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)

    async def test_same_numeric_book_id_on_three_platforms_remains_separate_in_queue_and_storage(self):
        self.workbench.queue_paused = True
        examples = [book(chapter("1", 1, state="done", text="不同平台的原创正文"), platform=site) for site in ("jjwxc", "gongzicp", "fanqie")]
        for sample in examples:
            self.assertTrue(self.workbench.enqueue(sample["url"]))
            self.workbench.save_book(sample)
        self.assertEqual([item["id"] for item in self.workbench.queue], ["42", "gongzicp_42", "fanqie_42"])
        files = [export_book(sample, self.exports, "txt") for sample in examples]
        self.assertEqual(len(set(files)), 3)
        for sample in examples:
            status, _headers, payload = await http_get("/api/book", f"id={sample['id']}".encode())
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload)["platform"], sample["platform"])
        status, _headers, payload = await http_get("/api/downloads")
        self.assertEqual(status, 200)
        downloaded = {item["id"]: item for item in json.loads(payload)["books"]}
        self.assertEqual({item["platform"] for item in downloaded.values()}, {"jjwxc", "gongzicp", "fanqie"})
        self.assertIsNotNone(downloaded["42"]["cover"])
        self.assertIsNone(downloaded["gongzicp_42"]["cover"])

    async def test_rescan_keeps_catalog_order_and_refreshes_ordinals_without_losing_reviewed_text(self):
        previous = book(chapter("90", 1, state="reviewed", text="保留九十号章节正文"), chapter("20", 2, state="reviewed", text="保留二十号章节正文"))
        self.workbench.save_book(previous)
        self.workbench.reader.scan_result = book(chapter("20", 1), chapter("15", 2), chapter("90", 3))
        await self.workbench.scan(previous["url"])
        self.assertEqual([row["id"] for row in self.workbench.book["chapters"]], ["20", "15", "90"])
        self.assertEqual([row["ordinal"] for row in self.workbench.book["chapters"]], [1, 2, 3])
        self.assertEqual(self.workbench.book["chapters"][0]["text"], "保留二十号章节正文")
        self.assertEqual(self.workbench.book["chapters"][2]["state"], "reviewed")
        public = server.public_book(self.workbench.book)
        self.assertEqual([row["ordinal"] for row in public["chapters"]], [1, 2, 3])
        self.assertNotIn("text", public["chapters"][0])

    async def test_fresh_blocked_state_wins_over_old_unread_or_failed_state(self):
        for old_state, old_text in (("pending", ""), ("capturing", ""), ("failed", "之前误识别的平台提示")):
            with self.subTest(old_state=old_state):
                previous = book(chapter("90", 1, state=old_state, text=old_text, error="旧错误"))
                self.workbench.save_book(previous)
                self.workbench.reader.scan_result = book(chapter("90", 1, state="blocked", locked=True, error="当前章节需要订阅确认"))
                await self.workbench.scan(previous["url"])
                saved = self.workbench.book["chapters"][0]
                self.assertEqual(saved["state"], "blocked")
                self.assertEqual(saved["error"], "当前章节需要订阅确认")
                self.assertEqual(saved["text"], old_text, "Old content remains available for audit")
                self.assertEqual(summarize(self.workbench.book)["captured"], 0)

    async def test_new_access_barrier_does_not_delete_a_previously_reviewed_copy(self):
        previous = book(chapter("90", 1, state="reviewed", text="此前已经保存并校对的正文"))
        self.workbench.save_book(previous)
        self.workbench.reader.scan_result = book(chapter("90", 1, state="blocked", locked=True, error="当前章节已锁定"))
        await self.workbench.scan(previous["url"])
        self.assertEqual(self.workbench.book["chapters"][0]["state"], "reviewed")
        self.assertEqual(self.workbench.book["chapters"][0]["text"], previous["chapters"][0]["text"])

    async def test_removed_blocked_or_failed_chapter_is_not_promoted_to_captured(self):
        for old_state in ("blocked", "failed"):
            with self.subTest(old_state=old_state):
                previous = book(chapter("90", 1, state=old_state, text="旧平台提示或不完整识别片段"))
                self.workbench.save_book(previous)
                self.workbench.reader.scan_result = book(chapter("20", 1))
                await self.workbench.scan(previous["url"])
                preserved = next(row for row in self.workbench.book["chapters"] if row["id"] == "90")
                self.assertTrue(preserved["absent_from_toc"])
                self.assertEqual(preserved["state"], old_state)
                self.assertEqual(summarize(self.workbench.book)["captured"], 0)

    async def test_transient_profile_failure_keeps_nickname_but_new_account_never_inherits_it(self):
        for replacing in (False, True):
            with self.subTest(replacing=replacing):
                self.workbench.library_cache = server.clean_library({"account": {"state": "connected", "nickname": "旧账号完整昵称"}, "books": [], "complete": True})
                self.workbench.replace_library_on_sync = replacing
                self.workbench.reader.library = AsyncMock(return_value={"account": {"state": "connected", "nickname": ""}, "books": [], "complete": True, "expected_count": 0})
                await self.workbench.refresh_library()
                self.assertEqual(self.workbench.library_cache["account"]["nickname"], "" if replacing else "旧账号完整昵称")

    async def test_chapter_route_exposes_display_ordinal_and_keeps_namespaced_book_id(self):
        sample = book(chapter("7137980122702807592", 2, state="review", text="原始章节正文"), platform="fanqie")
        self.workbench.save_book(sample)
        status, _headers, payload = await http_get("/api/chapter", b"id=7137980122702807592&book_id=fanqie_42")
        self.assertEqual(status, 200)
        result = json.loads(payload)
        self.assertEqual(result["book_id"], "fanqie_42")
        self.assertEqual(result["id"], "7137980122702807592")
        self.assertEqual(result["ordinal"], 2)

    async def test_resume_current_uses_the_platform_minimum_delay(self):
        self.workbench.book = book(chapter("90", 1))
        self.workbench.capture = AsyncMock()
        self.workbench.resume_current("auto", 3)
        await self.workbench.task
        self.workbench.capture.assert_awaited_once_with("auto", 10)

    async def test_jinjiang_ids_covers_nicknames_and_private_serialization_stay_compatible(self):
        sample = book(chapter("1", 1, state="reviewed", text="晋江原有校对正文", screenshots=["/private/source.png"]), platform="jjwxc")
        sample["cookie"] = "DO_NOT_SERIALIZE"
        self.workbench.save_book(sample)
        status, headers, payload = await http_get("/api/book", b"id=42")
        result = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertEqual(result["id"], "42")
        self.assertTrue(result["cover"].endswith("book_id=42"))
        self.assertNotIn("DO_NOT_SERIALIZE", payload.decode())
        self.assertNotIn("screenshots", result["chapters"][0])
        cleaned = server.clean_library({"account": {"state": "connected", "nickname": "完整原创昵称"}, "books": [{"id": "42", "url": sample["url"]}], "complete": True})
        self.assertEqual(cleaned["account"]["nickname"], "完整原创昵称")
        self.assertEqual(cleaned["books"][0]["cover"], result["cover"])

    async def test_cover_route_preserves_cache_header_and_rejects_namespaced_ids(self):
        fake = AsyncMock(side_effect=ValueError("only numeric Jinjiang IDs"))
        with patch.object(server.cover_cache, "get", fake):
            status, _headers, _payload = await http_get("/api/cover", b"book_id=fanqie_42")
        self.assertEqual(status, 400)
        with patch.object(server.cover_cache, "get", AsyncMock(return_value=None)):
            status, headers, _payload = await http_get("/api/cover", b"book_id=42")
        self.assertEqual(status, 404)
        self.assertEqual(headers[b"cache-control"], b"no-store")


    async def test_cover_retryable_failure_is_not_cached_as_missing(self):
        with patch.object(server.cover_cache, "get", AsyncMock(side_effect=server.CoverRetryableError(7))):
            status, headers, _payload = await http_get("/api/cover", b"book_id=42&retry=1")
        self.assertEqual(status, 503)
        self.assertEqual(headers[b"retry-after"], b"7")
        self.assertEqual(headers[b"cache-control"], b"no-store")


class EngineBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        manifest = Path(self.temp.name) / "rules.json"
        manifest.write_text(json.dumps({"rules": [{"id": "Fixture", "name": "Fixture", "hosts": ["books.example.org"]}]}))
        context = patch.object(platforms, "UPSTREAM_MANIFEST", manifest)
        context.start()
        self.addCleanup(context.stop)
        bundle = Path(self.temp.name) / "engine.js"
        bundle.write_text("// Offline wrapper fixture")
        context = patch.object(engine, "BUNDLE", bundle)
        context.start()
        self.addCleanup(context.stop)
        self.url = "https://books.example.org/book/42"
        self.page = type("Page", (), {})()
        self.page.url = self.url
        self.page.add_script_tag = AsyncMock()
        self.page.wait_for_function = AsyncMock()
        self.page.evaluate = AsyncMock()

    def raw_scan(self, chapters=None):
        return {"rule_id": "Fixture", "title": "兼容规则原创样本", "author": "测试作者", "chapters": chapters or [
            {"id": "c_" + "a" * 24, "title": "首章", "url": "https://books.example.org/chapter/90", "order": 100},
            {"id": "c_" + "b" * 24, "title": "次章", "url": "https://books.example.org/chapter/20", "vip": True, "paid": False},
        ]}

    async def test_wrapper_keeps_engine_order_namespaces_and_unconfirmed_vip_barriers(self):
        self.page.evaluate.return_value = {"ok": True, "value": self.raw_scan()}
        result = await engine.scan(self.page, self.url)
        self.assertTrue(result["id"].startswith("upstream_fixture_"))
        self.assertEqual([row["ordinal"] for row in result["chapters"]], [1, 2])
        self.assertEqual(result["chapters"][0]["id"], "c_" + "a" * 24)
        self.assertEqual(result["chapters"][1]["state"], "blocked")
        self.assertTrue(result["chapters"][1]["locked"])

    async def test_cross_site_chapter_and_duplicate_ids_reject_entire_manifest(self):
        for rows in ([{"id": "c_" + "a" * 24, "url": "https://evil.example/chapter/1"}], [self.raw_scan()["chapters"][0]] * 2):
            with self.subTest(rows=len(rows)):
                self.page.evaluate.return_value = {"ok": True, "value": self.raw_scan(rows)}
                with self.assertRaises(CaptureError):
                    await engine.scan(self.page, self.url)

    async def test_rule_failures_are_sanitized_and_cancellation_is_preserved(self):
        self.page.evaluate.side_effect = RuntimeError("synthetic-secret-should-not-escape")
        with self.assertRaises(CaptureError) as result:
            await engine.invoke(self.page, "scan", {})
        self.assertNotIn("synthetic-secret", str(result.exception))
        self.page.evaluate.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await engine.invoke(self.page, "scan", {})

    async def test_read_requires_review_and_rejects_images_pua_or_rendered_gates(self):
        chapter = {"book_url": self.url, "rule_id": "Fixture", "id": "c_" + "a" * 24}
        prose = "这是为了验证兼容读取边界而创作的测试正文，既不是小说摘录，也没有隐藏数据。"
        self.page.evaluate.return_value = {"ok": True, "value": {"text": prose, "needs_ocr": False, "images": 0}}
        result = await engine.read(self.page, chapter)
        self.assertEqual(result["text"], prose)
        self.assertTrue(result["review"])
        for fields in ({"needs_ocr": True}, {"images": 1}, {"text": prose + "\uE123"}, {"gate": "needs_login"}):
            with self.subTest(fields=list(fields)):
                self.page.evaluate.return_value = {"ok": True, "value": {"text": prose, **fields}}
                with self.assertRaises(CaptureError):
                    await engine.read(self.page, chapter)

    async def test_recreated_reader_context_rescans_engine_book_before_resume(self):
        reader = Reader(Path(self.temp.name) / "unused-profile")
        reader._engine_book_url = self.url
        await reader.close()
        # An account/library operation has since created another open context,
        # but its main page has not loaded this book or the injected rule bundle.
        reader.page = type("Page", (), {"url": "about:blank", "is_closed": lambda self: False})()

        async def scan(url):
            reader.page.url = url
            reader._engine_book_url = url

        async def read(page, chapter):
            self.assertEqual(page.url, chapter["book_url"])
            return {"text": "重建浏览器后重新读取的原创测试正文。", "review": True}

        reader.scan = AsyncMock(side_effect=scan)
        row = {"id": "c_" + "a" * 24, "book_url": self.url, "rule_id": "Fixture"}
        with patch.object(engine, "read", AsyncMock(side_effect=read)):
            await reader.capture(row, Path(self.temp.name) / "unused-source", mode="auto")
        reader.scan.assert_awaited_once_with(self.url)


if __name__ == "__main__":
    unittest.main()
