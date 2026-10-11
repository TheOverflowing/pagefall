"""Version endpoints and mutation paths use only temporary fixture books."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from novelist import server
from novelist.catalog import Catalog
from novelist.exporters import proofread_current
from novelist.translation_store import digest
import test_workbench as fixtures
import test_proofreading as proofreading_fixtures


class ChapterVersionAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkbenchTests.setUp(self)
        self.catalog = Catalog(self.data / "catalogs")
        patcher = patch.object(server, "catalog", self.catalog)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workbench.book = fixtures.book(fixtures.chapter(1, "reviewed", "第一段。\n\n旧的第二段。"))
        self.workbench.save()
        self.catalog.write(self.catalog.directory / "42.json", copy.deepcopy(self.workbench.book))

    async def asyncTearDown(self):
        await fixtures.WorkbenchTests.asyncTearDown(self)

    def request(self, **fields):
        return fixtures.FakeRequest({"book_id": "42", "chapter_id": "1", **fields})

    def versions(self):
        return server.chapter_versions().list("42", "1")

    def online_value(self, text="第一段。\n\n新的第二段。"):
        return {"id": "1", "book_id": "42", "title": "第一章", "url": self.workbench.book["chapters"][0]["url"], "text": text, "method": "api", "source_updated_at": "2026-10-10 12:00:00"}

    async def test_existing_download_import_is_lazy_and_stable_after_reopening(self):
        self.assertEqual(self.versions(), [])
        result = await server.list_chapter_versions("42", "1")
        self.assertEqual(len(result["versions"]), 1)
        self.assertEqual(result["versions"][0]["kind"], "existing")
        self.assertNotIn("text", result["versions"][0])
        version_id = result["versions"][0]["id"]
        self.assertEqual((await server.get_chapter_version("42", "1", version_id))["text"], "第一段。\n\n旧的第二段。")
        self.assertEqual(await server.list_chapter_versions("42", "1"), result)
        self.workbench.book = None
        self.assertEqual(await server.list_chapter_versions("42", "1"), result)

    async def test_edit_and_diff_preserve_old_source_and_current_text(self):
        await server.update_chapter(fixtures.FakeRequest({"book_id": "42", "id": "1", "text": "第一段。\n\n手动改过的第二段。"}))
        versions = self.versions()
        self.assertEqual([item["kind"] for item in versions], ["edit", "existing"])
        diff = await server.compare_chapter_versions("42", "1", versions[1]["id"], versions[0]["id"])
        self.assertEqual((diff["added"], diff["deleted"]), (1, 1))
        self.assertIn({"kind": "delete", "text": "旧的第二段。"}, diff["rows"])
        self.assertIn({"kind": "insert", "text": "手动改过的第二段。"}, diff["rows"])
        await server.get_chapter_version("42", "1", versions[1]["id"])
        self.assertEqual(self.workbench.book["chapters"][0]["text"], "第一段。\n\n手动改过的第二段。")

    async def test_locked_download_remains_readable_without_catalog_request(self):
        self.workbench.book["chapters"][0]["locked"] = True
        with patch.object(self.catalog, "read", new_callable=AsyncMock) as read:
            result = await server.read_online_chapter(self.request())
        read.assert_not_awaited()
        self.assertEqual(result["text"], "第一段。\n\n旧的第二段。")

    async def test_reading_only_history_import_and_unknown_ids(self):
        self.workbench.book = None
        (self.books / "42" / "book.json").unlink()
        (self.catalog.directory / "42.json").unlink()
        value = self.online_value_without_book()
        self.catalog.write(self.data / "reading" / "42" / "1.json", value)
        self.assertEqual((await server.read_online_chapter(self.request()))["text"], value["text"])
        result = await server.list_chapter_versions("42", "1")
        self.assertEqual(len(result["versions"]), 1)
        self.assertEqual(result["versions"][0]["kind"], "existing")
        for book_id, chapter_id, status in (("../42", "1", 400), ("42", "../1", 400), ("42", "2", 404), ("43", "1", 404)):
            with self.subTest(book_id=book_id, chapter_id=chapter_id), self.assertRaises(HTTPException) as raised:
                await server.list_chapter_versions(book_id, chapter_id)
            self.assertEqual(raised.exception.status_code, status)
        with self.assertRaises(HTTPException) as raised:
            await server.refresh_chapter(self.request())
        self.assertEqual(raised.exception.status_code, 400)

    @staticmethod
    def online_value_without_book():
        return {"id": "1", "book_id": "42", "title": "第一章", "url": "https://www.jjwxc.net/onebook.php?novelid=42&chapterid=1", "text": "曾经在线读过的正文。", "method": "text"}

    async def test_refresh_updates_download_invalidates_proofreading_and_translation(self):
        chapter = self.workbench.book["chapters"][0]
        chapter.update(state="review", proofreading={"old": True}, proofread_undo={"old": True}, screenshots=["old.png"], lines=[{"text": "old"}])
        store = server.translation_store()
        index = store.create("42", {"provider": "deepseek", "model": "fixture", "source_language": "zh", "target_language": "en"})
        index["chapters"]["1"] = {"status": "done", "source_hash": digest(chapter["text"]), "source_title": chapter["title"], "source_dependency": store.source_dependencies(self.workbench.book)["1"], "glossary_revision": 0}
        store.save(index)
        self.assertEqual(store.chapter(self.workbench.book, chapter)["status"], "done")

        async def capture(book, chapter_id, refresh=False):
            self.assertTrue(refresh)
            value = self.online_value()
            self.catalog.versions.record("42", value, kind="source")
            return value

        with patch.object(self.catalog, "read", side_effect=capture):
            result = await server.refresh_chapter(self.request())
        self.assertEqual(result["text"], "第一段。\n\n新的第二段。")
        self.assertEqual(result["translation"]["status"], "stale")
        self.assertEqual(chapter["state"], "done")
        self.assertFalse(chapter["review"])
        for key in ("proofreading", "proofread_undo", "lines", "screenshots"):
            self.assertNotIn(key, chapter)
        self.assertEqual([item["kind"] for item in self.versions()], ["source", "existing"])
        saved = json.loads((self.books / "42" / "book.json").read_text())
        self.assertEqual(saved["chapters"][0]["text"], result["text"])
        self.assertFalse(self.workbench.busy)

    async def test_refresh_failure_preserves_download_and_reading_copy(self):
        old = copy.deepcopy(self.workbench.book)
        reading = self.online_value_without_book()
        self.catalog.write(self.data / "reading" / "42" / "1.json", reading)
        self.catalog.write(self.catalog.directory / "42.json", fixtures.book(fixtures.chapter(1, "blocked", locked=True)))
        with self.assertRaises(HTTPException) as raised:
            await server.refresh_chapter(self.request())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.workbench.book, old)
        self.assertEqual(self.catalog.cached_read("42", "1"), reading)
        self.assertFalse(self.workbench.busy)

    async def test_same_text_refresh_keeps_human_review_state(self):
        before = copy.deepcopy(self.workbench.book["chapters"][0])
        with patch.object(self.catalog, "read", new_callable=AsyncMock, return_value=self.online_value(before["text"])):
            result = await server.refresh_chapter(self.request())
        self.assertEqual(result["state"], "reviewed")
        self.assertEqual(self.workbench.book["chapters"][0], before)

    async def test_refreshed_ocr_remains_unreviewed(self):
        value = {**self.online_value(), "method": "ocr"}
        with patch.object(self.catalog, "read", new_callable=AsyncMock, return_value=value):
            result = await server.refresh_chapter(self.request())
        self.assertEqual(result["state"], "review")
        self.assertTrue(result["review"])
        self.assertEqual(result["method"], "ocr")

    async def test_refresh_claims_busy_before_network_and_blocks_other_writers(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def waiting_read(*args, **kwargs):
            entered.set()
            await release.wait()
            return self.online_value()

        with patch.object(self.catalog, "read", side_effect=waiting_read):
            updating = asyncio.create_task(server.refresh_chapter(self.request()))
            await entered.wait()
            try:
                self.assertTrue(self.workbench.busy)
                for operation in (server.refresh_chapter(self.request()), server.update_chapter(fixtures.FakeRequest({"book_id": "42", "id": "1", "text": "不能覆盖"})), server.proofread(self.request()), server.translate(self.request())):
                    with self.assertRaises(HTTPException) as raised:
                        await operation
                    self.assertEqual(raised.exception.status_code, 409)
            finally:
                release.set()
                await updating
        self.assertFalse(self.workbench.busy)

    async def test_existing_translation_or_proofreading_rejects_refresh(self):
        for state in (self.workbench.translation, self.workbench.proofreading):
            state.update(book_id="42", running=True)
            try:
                with self.assertRaises(HTTPException) as raised:
                    await server.refresh_chapter(self.request())
                self.assertEqual(raised.exception.status_code, 409)
            finally:
                state["running"] = False

    async def test_capture_archives_before_automatic_proofreading(self):
        self.workbench.book = fixtures.book(fixtures.chapter(1))

        def correction(chapter):
            chapter["text"] = "自动整理后的正文。"
            return True

        with patch.object(server, "apply_proofreading", side_effect=correction):
            await self.workbench.capture("auto", 0)
        versions = self.versions()
        self.assertEqual([item["kind"] for item in versions], ["proofread", "source"])
        self.assertEqual(server.chapter_versions().get("42", "1", versions[1]["id"])["text"], "第 1 章的测试正文。")

    async def test_automatic_proofreading_and_undo_are_distinct_versions(self):
        chapter = self.workbench.book["chapters"][0]
        value = proofreading_fixtures.chapter([
            proofreading_fixtures.line("这是一段为了测试换行整理而虚构的文字，", x=0.08, width=0.9),
            proofreading_fixtures.line("它的后半句仍然在原来的同一段中。", y=0.03, width=0.7),
        ])
        chapter.update({key: val for key, val in value.items() if key != "id"})
        before = copy.deepcopy(chapter)
        await self.workbench.proofread_book(self.workbench.book)
        self.assertTrue(proofread_current(chapter))
        self.assertNotEqual(chapter["text"], before["text"])
        await server.undo_proofread(self.request())
        self.assertEqual(chapter["text"], before["text"])
        self.assertEqual([item["kind"] for item in self.versions()], ["proofread_undo", "proofread", "existing"])

    async def test_unknown_versions_return_404(self):
        for operation in (server.get_chapter_version("42", "1", "missing"), server.compare_chapter_versions("42", "1", "missing", "other")):
            with self.assertRaises(HTTPException) as raised:
                await operation
            self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
