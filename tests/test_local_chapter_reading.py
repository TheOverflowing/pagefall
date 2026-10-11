"""Existing chapter text remains readable while background operations are running."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from novelist import server
from novelist.browser import CaptureError
from novelist.catalog import Catalog
from novelist.chapter_versions import ChapterVersions
import test_workbench as fixtures


class LocalChapterReadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkbenchTests.setUp(self)
        self.catalog = Catalog(self.data / "catalogs")
        patcher = patch.object(server, "catalog", self.catalog)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workbench.book = fixtures.book(fixtures.chapter(1, "blocked", locked=True))
        self.workbench.save()
        self.catalog.write(self.catalog.directory / "42.json", copy.deepcopy(self.workbench.book))

    async def asyncTearDown(self):
        await fixtures.WorkbenchTests.asyncTearDown(self)

    def request(self):
        return fixtures.FakeRequest({"book_id": "42", "chapter_id": "1"})

    def cache(self, text="已保存的在线正文。", **fields):
        reading = {"id": "1", "book_id": "42", "title": "第一章", "text": text,
                   "url": self.workbench.book["chapters"][0]["url"], "method": "api", **fields}
        self.catalog.write(self.data / "reading" / "42" / "1.json", reading)
        return reading

    def archive(self, text="先前保存的完整正文。"):
        return self.catalog.versions.record("42", {"id": "1", "title": "第一章", "text": text}, kind="source")

    async def test_download_with_blocked_state_is_still_readable_without_unlocking_or_mutation(self):
        chapter = self.workbench.book["chapters"][0]
        chapter["text"] = "锁定前保存的正文。"
        before = copy.deepcopy(chapter)
        self.workbench.chapter_refresh_id = "42"
        with patch.object(self.catalog, "read", new_callable=AsyncMock) as network:
            result = await server.read_online_chapter(self.request())
        network.assert_not_awaited()
        self.assertEqual(result["text"], before["text"])
        self.assertEqual(result["state"], "blocked")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["reading_source"], "download")
        self.assertEqual(chapter, before)
        self.assertTrue(server.public_book(self.workbench.book)["chapters"][0]["readable_locally"])

    async def test_complete_download_keeps_details_and_is_readable_during_background_writes(self):
        chapter = self.workbench.book["chapters"][0]
        chapter.update(state="done", text="完整的正文。", raw_text="原始正文。")
        for task in ("proofreading", "translation"):
            with self.subTest(task=task):
                getattr(self.workbench, task).update(book_id="42", running=True)
                result = await server.read_online_chapter(self.request())
                self.assertTrue(result["read_only"])
                self.assertEqual(result["raw_text"], chapter["raw_text"])
                self.assertIn("translation", result)
                self.assertEqual(result["book_id"], "42")
                getattr(self.workbench, task)["running"] = False
        self.assertFalse((await server.get_chapter("1", book_id="42"))["read_only"])

    async def test_online_cache_is_readable_during_book_download_and_is_not_editable(self):
        self.cache()
        self.workbench.chapter_refresh_id = "42"
        with patch.object(self.catalog, "read", new_callable=AsyncMock) as network:
            online = await server.read_online_chapter(self.request())
            direct = await server.get_chapter("1", book_id="42")
        network.assert_not_awaited()
        self.assertEqual(online, direct)
        self.assertEqual(online["state"], "done")
        self.assertEqual(online["reading_source"], "cache")
        self.assertTrue(online["read_only"])
        self.assertTrue(server.public_book(self.workbench.book)["chapters"][0]["readable_locally"])

    async def test_archive_fallback_uses_latest_complete_snapshot_without_restoring_it(self):
        chapter = self.workbench.book["chapters"][0]
        chapter.update(text="不完整的最新抓取", incomplete=True)
        self.archive("旧版完整正文。")
        latest = self.archive("最近存档的完整正文。")
        before = copy.deepcopy(self.workbench.book)
        book_file = (self.books / "42" / "book.json").read_text()
        self.workbench.chapter_refresh_id = "42"
        with patch.object(self.catalog, "read", new_callable=AsyncMock) as network:
            result = await server.read_online_chapter(self.request())
        network.assert_not_awaited()
        self.assertEqual(result["text"], latest["text"])
        self.assertEqual(result["id"], "1")
        self.assertEqual(result["version_id"], latest["id"])
        self.assertEqual(result["reading_source"], "archive")
        self.assertTrue(result["read_only"])
        self.assertEqual(self.workbench.book, before)
        self.assertEqual((self.books / "42" / "book.json").read_text(), book_file)
        self.assertIsNone(self.catalog.cached_read("42", "1"))

    async def test_archive_only_chapter_can_be_read_and_browsed_after_directory_disappears(self):
        latest = self.archive()
        self.workbench.book = None
        (self.books / "42" / "book.json").unlink()
        (self.catalog.directory / "42.json").unlink()
        result = await server.get_chapter("1", book_id="42")
        self.assertEqual(result["text"], latest["text"])
        self.assertEqual(result["reading_source"], "archive")
        self.assertEqual((await server.list_chapter_versions("42", "1"))["versions"][0]["id"], latest["id"])

    async def test_incomplete_reading_cache_does_not_hide_complete_archive(self):
        self.cache("部分内容。", incomplete=True)
        latest = self.archive()
        result = await server.read_online_chapter(self.request())
        self.assertEqual(result["text"], latest["text"])
        self.assertEqual(result["reading_source"], "archive")

    async def test_locked_chapter_without_any_local_text_still_uses_existing_access_gate(self):
        self.assertFalse(server.public_book(self.workbench.book)["chapters"][0]["readable_locally"])
        with patch.object(self.catalog, "read", new_callable=AsyncMock, side_effect=CaptureError("本章已锁定。", "blocked", False)) as network:
            with self.assertRaises(HTTPException) as raised:
                await server.read_online_chapter(self.request())
        self.assertEqual(raised.exception.status_code, 400)
        network.assert_awaited_once()
        self.assertIsNone(self.catalog.cached_read("42", "1"))

    async def test_public_availability_uses_filenames_without_loading_archive_text(self):
        self.assertFalse(server.public_book(self.workbench.book)["chapters"][0]["readable_locally"])
        self.archive()
        with patch.object(ChapterVersions, "list", side_effect=AssertionError("TOC loaded all versions")), patch.object(ChapterVersions, "get", side_effect=AssertionError("TOC loaded a body")), patch.object(self.catalog, "cached_read", side_effect=AssertionError("TOC loaded online body")):
            first = server.public_book(self.workbench.book)
            second = server.public_book(self.workbench.book)
        self.assertTrue(first["chapters"][0]["readable_locally"])
        self.assertTrue(second["chapters"][0]["readable_locally"])
        (self.data / "chapter-versions" / "42" / "1.json").unlink()
        self.assertFalse(server.public_book(self.workbench.book)["chapters"][0]["readable_locally"])

    async def test_refresh_archives_complete_old_body_before_replacing_abnormal_state(self):
        chapter = self.workbench.book["chapters"][0]
        for state in ("blocked", "capturing"):
            with self.subTest(state=state):
                old_text = state + " 状态下保留的完整正文。"
                chapter.update(state=state, text=old_text, incomplete=False)

                async def capture(*args, **kwargs):
                    self.assertEqual(chapter["state"], state)
                    existing = self.catalog.versions.list("42", "1")[0]
                    self.assertEqual(existing["kind"], "existing")
                    self.assertEqual(self.catalog.versions.get("42", "1", existing["id"])["text"], old_text)
                    return {"id": "1", "book_id": "42", "title": "第一章", "text": "更新后的完整正文。", "method": "api"}

                with patch.object(self.catalog, "read", side_effect=capture):
                    await self.workbench.refresh_saved_chapter(self.workbench.book, "1")
                self.assertEqual(chapter["text"], "更新后的完整正文。")
        chapter.update(state="blocked", text="不完整的抓取片段。", incomplete=True)
        previous = self.catalog.versions.list("42", "1")
        self.assertIsNone(server.archive_chapter("42", chapter))
        self.assertEqual(self.catalog.versions.list("42", "1"), previous)
