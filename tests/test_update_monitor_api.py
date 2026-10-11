"""Monitor integration with local bodies, archives, edits and API summaries."""
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from novelist import server
from novelist.catalog import Catalog
from novelist.update_monitor import UpdateMonitor, text_hash
import test_workbench as fixtures


class UpdateMonitorAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkbenchTests.setUp(self)
        self.catalog = Catalog(self.data / "catalogs")
        patcher = patch.object(server, "catalog", self.catalog)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workbench.updates = UpdateMonitor(self.data, self.workbench, self.catalog, AsyncMock(return_value={"books": [], "complete": True}))
        self.workbench.book = fixtures.book(fixtures.chapter(1, "reviewed", "我手动校对的正文。", raw_text="初始正文。"))
        self.workbench.save()
        self.catalog.write(self.catalog.directory / "42.json", copy.deepcopy(self.workbench.book))

    async def asyncTearDown(self):
        await fixtures.WorkbenchTests.asyncTearDown(self)

    def source(self, text="作者更新后的正文。", method="api"):
        return {"id": "1", "book_id": "42", "title": "第一章", "url": self.workbench.book["chapters"][0]["url"], "text": text, "method": method, "source_updated_at": "2026-10-10 17:00:00"}

    async def capture(self, book, chapter_id, refresh=False, previous_source_hash=None):
        value = self.source()
        self.catalog.versions.record("42", value, kind="source")
        self.catalog.write(self.data / "reading" / "42" / "1.json", value)
        return value

    async def test_background_change_archives_manual_body_and_keeps_current_book_selection(self):
        marker_before = (self.data / "current.json").read_text()
        current = self.workbench.book
        with patch.object(self.catalog, "read", side_effect=self.capture):
            await self.workbench.refresh_saved_chapter(current, "1", previous_source_hash=text_hash("初始正文。"))
        self.assertIs(self.workbench.book, current)
        self.assertEqual((self.data / "current.json").read_text(), marker_before)
        self.assertEqual(current["chapters"][0]["text"], "作者更新后的正文。")
        versions = self.catalog.versions.list("42", "1")
        self.assertEqual([entry["kind"] for entry in versions], ["source", "existing"])
        self.assertEqual(self.catalog.versions.get("42", "1", versions[-1]["id"])["text"], "我手动校对的正文。")

    async def test_unchanged_source_preserves_manual_edits(self):
        with patch.object(self.catalog, "read", new_callable=AsyncMock, return_value=self.source("初始正文。")) as read:
            await self.workbench.refresh_saved_chapter(self.workbench.book, "1", previous_source_hash=text_hash("初始正文。"))
        self.assertEqual(self.workbench.book["chapters"][0]["text"], "我手动校对的正文。")
        self.assertEqual(self.workbench.book["chapters"][0]["state"], "reviewed")
        self.assertEqual(read.call_args.kwargs["previous_source_hash"], text_hash("初始正文。"))

    async def test_newer_download_is_baseline_instead_of_older_online_copy(self):
        chapter = self.workbench.book["chapters"][0]
        chapter["text_source_updated_at"] = "2026-10-10 18:00:00"
        self.catalog.write(self.data / "reading" / "42" / "1.json", self.source("更早在线读过的正文。"))
        self.workbench.monitor_books()
        self.assertIs(self.workbench.monitor_snapshot("42", "1"), chapter)

    async def test_background_ocr_is_kept_for_review(self):
        with patch.object(self.catalog, "read", new_callable=AsyncMock, return_value=self.source(method="ocr")):
            await self.workbench.refresh_saved_chapter(self.workbench.book, "1", previous_source_hash=text_hash("初始正文。"))
        self.assertEqual(self.workbench.book["chapters"][0]["state"], "review")
        self.assertTrue(self.workbench.book["chapters"][0]["review"])

    async def test_stale_open_editor_gets_conflict_without_losing_new_body(self):
        with self.assertRaises(HTTPException) as raised:
            await server.update_chapter(fixtures.FakeRequest({"book_id": "42", "id": "1", "text": "旧编辑器的保存", "base_text": "已过期的正文"}))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.workbench.book["chapters"][0]["text"], "我手动校对的正文。")
        await server.update_chapter(fixtures.FakeRequest({"book_id": "42", "id": "1", "text": "再次校对。", "base_text": "我手动校对的正文。"}))
        self.assertEqual(self.workbench.book["chapters"][0]["text"], "再次校对。")

    async def test_reading_cache_and_download_scope_excludes_unsaved_books_and_drafts(self):
        another = fixtures.book(fixtures.chapter(1))
        another.update(id="43", url="https://www.jjwxc.net/onebook.php?novelid=43")
        self.workbench.save_book(another)
        reading = {**self.source(), "book_id": "44"}
        self.catalog.write(self.data / "reading" / "44" / "1.json", reading)
        self.catalog.write(self.catalog.directory / "44.json", {**another, "id": "44"})
        self.workbench.save_book({**another, "id": "draft-one", "url": "", "chapters": [fixtures.chapter(1, "done", "自写内容")]})
        self.assertEqual({book["id"] for book in self.workbench.monitor_books()}, {"42", "44"})

    async def test_online_only_book_keeps_reader_route_and_markers_are_public(self):
        self.workbench.updates.data["books"]["42"] = {"title": "测试作品", "chapters": {"1": {"update_status": "changed", "update_detected_at": "2026-10-10T00:00:00Z"}}}
        remote = fixtures.book(fixtures.chapter(1))
        public = server.public_book(remote)
        self.assertEqual(public["chapters"][0]["update_status"], "changed")
        self.assertEqual(public["chapters"][0]["state"], "pending")
        self.assertEqual(self.workbench.state()["updates"]["changed_count"], 1)
        result = await server.chapter_update_read(fixtures.FakeRequest({"book_id": "42", "chapter_id": "1"}))
        self.assertTrue(result["ok"])
        self.assertNotIn("update_status", server.public_book(remote)["chapters"][0])

    async def test_settings_and_scheduled_check_response_match_native_contract(self):
        result = await server.updates_settings(fixtures.FakeRequest({"enabled": False}))
        self.assertTrue(result["ok"])
        self.assertFalse((await server.updates_status())["enabled"])
        self.assertTrue((await server.updates_check())["ok"])
        self.assertTrue(self.workbench.updates.force)
        self.assertFalse(self.workbench.updates.data["enabled"])
        self.assertEqual(json.loads((self.data / "updates.json").read_text())["enabled"], False)

    async def test_new_saved_chapter_is_appended_without_dropping_older_local_chapters(self):
        remote = fixtures.book(fixtures.chapter(2))
        result = {**self.source(), "id": "2"}
        with patch.object(self.catalog, "read", new_callable=AsyncMock, return_value=result):
            await self.workbench.refresh_saved_chapter(remote, "2")
        self.assertEqual([chapter["id"] for chapter in self.workbench.book["chapters"]], ["1", "2"])
        self.assertEqual(self.workbench.book["chapters"][0]["text"], "我手动校对的正文。")

    async def test_first_auto_subscription_creates_download_with_only_latest_body_and_keeps_selection(self):
        remote = fixtures.book(fixtures.chapter(1), fixtures.chapter(2), fixtures.chapter(3))
        remote.update(id="43", title="新追更作品")
        remote.pop("author")
        result = {**self.source(), "id": "3", "book_id": "43"}
        current = self.workbench.book
        marker = (self.data / "current.json").read_text()
        with patch.object(self.catalog, "read", new_callable=AsyncMock, return_value=result):
            await self.workbench.refresh_saved_chapter(remote, "3", save_new_book=True)
        saved = json.loads((self.books / "43" / "book.json").read_text())
        self.assertEqual(saved["author"], "")
        self.assertEqual([chapter["state"] for chapter in saved["chapters"]], ["pending", "pending", "done"])
        self.assertEqual(sum(bool(chapter.get("text")) for chapter in saved["chapters"]), 1)
        self.assertIs(self.workbench.book, current)
        self.assertEqual((self.data / "current.json").read_text(), marker)
