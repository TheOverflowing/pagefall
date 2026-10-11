"""Exercise saved task state using a fake reader; never open a live browser."""

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from novelist import server
from novelist.browser import CaptureError
from novelist.exporters import export_book, summarize


def chapter(number, state="pending", text="", **fields):
    return {
        "id": str(number), "title": f"第 {number} 章",
        "url": f"https://www.jjwxc.net/onebook.php?novelid=42&chapterid={number}",
        "state": state, "text": text, "review": state == "review",
        "expected_words": 100, "vip": False, **fields,
    }


def book(*chapters):
    return {
        "id": "42", "title": "测试小说", "author": "测试作者",
        "url": "https://www.jjwxc.net/onebook.php?novelid=42",
        "chapters": list(chapters),
    }


class FakeReader:
    def __init__(self, profile):
        self.profile = profile
        self.opened = False
        self.capture_calls = []
        self.open_calls = []
        self.scan_calls = []
        self.on_capture = None
        self.on_open = None
        self.scan_result = None
        self.on_scan = None
        self.library_result = server.empty_library()
        self.on_library = None
        self.library_calls = 0
        self.headless = True
        self.login_calls = 0
        self.background_calls = 0

    async def capture(self, chapter, folder, mode):
        self.capture_calls.append((chapter["id"], folder, mode))
        if self.on_capture:
            return await self.on_capture(chapter, folder, mode)
        return {"text": f"第 {chapter['id']} 章的测试正文。", "raw_text": "原始识别正文。", "method": "text", "review": False, "screenshots": [], "error": ""}

    async def open(self, url):
        self.open_calls.append(url)
        if self.on_open:
            return await self.on_open(url)
        self.opened = True

    async def scan(self, url):
        self.scan_calls.append(url)
        if self.on_scan:
            return await self.on_scan(url)
        return copy.deepcopy(self.scan_result)

    async def library(self):
        self.library_calls += 1
        if self.on_library:
            return await self.on_library()
        return copy.deepcopy(self.library_result)

    async def show_login(self):
        self.login_calls += 1
        self.opened = True
        self.headless = False

    async def background(self):
        self.background_calls += 1
        self.headless = True


class FakeRequest:
    def __init__(self, data):
        self.data = data

    async def json(self):
        return self.data


class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"
        self.books = self.data / "books"
        self.exports = Path(self.temporary.name) / "exports"
        self.books.mkdir(parents=True)
        self.exports.mkdir()
        for target, value in (("DATA", self.data), ("BOOKS", self.books), ("EXPORTS", self.exports), ("Reader", FakeReader), ("public_app_basic_metadata", lambda identifier: {"tags": ["百合", "甜文"], "classification_version": server.CLASSIFICATION_VERSION})):
            patcher = patch.object(server, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.workbench = server.Workbench()
        patcher = patch.object(server, "workbench", self.workbench)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        self.workbench.closing = True
        tasks = [task for task in (self.workbench.task, self.workbench.library_task, self.workbench.tags_task, self.workbench.account_task) if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)

    def stored_book(self):
        return json.loads((self.books / "42" / "book.json").read_text(encoding="utf-8"))

    def prepare_queue_reader(self):
        async def scan(url):
            identifier = parse_qs(urlparse(url).query)["novelid"][0]
            result = book(chapter(1))
            result.update(id=identifier, title=f"测试作品 {identifier}", url=url)
            result["chapters"][0]["url"] = url + "&chapterid=1"
            return result

        async def no_delay():
            return

        self.workbench.reader.on_scan = scan
        self.workbench._wait_pause = no_delay

    async def drain_queue(self):
        async def drain():
            while self.workbench.busy or (self.workbench.queue and not self.workbench.queue_paused and not self.workbench.account_waiting):
                if self.workbench.task:
                    await self.workbench.task
                await asyncio.sleep(0)
        await asyncio.wait_for(drain(), 2)

    async def test_resume_skips_every_captured_state_without_losing_review(self):
        self.workbench.book = book(
            chapter(1, "done", "已保存的正文"),
            chapter(2, "review", "待校对的识别正文", method="ocr"),
            chapter(3, "reviewed", "人工校对后的正文"),
            chapter(4),
        )
        await self.workbench.capture("auto", 0)
        self.assertEqual([call[0] for call in self.workbench.reader.capture_calls], ["4"])
        saved = self.stored_book()
        self.assertEqual([item["state"] for item in saved["chapters"]], ["done", "review", "reviewed", "done"])
        self.assertEqual(saved["chapters"][2]["text"], "人工校对后的正文")
        self.assertEqual(summarize(saved), {"total": 4, "captured": 4, "missing": 0, "review": 2, "complete": False, "auto_processed": 1})
        self.assertEqual(self.workbench.phase, "finished")
        self.assertIn("待校对 2 章", self.workbench.message)

    async def test_login_gate_stops_next_chapter_and_exports_missing_report(self):
        self.workbench.book = book(chapter(1), chapter(2))

        async def gated_capture(*_):
            raise CaptureError("请先登录晋江", "blocked", True)

        self.workbench.reader.on_capture = gated_capture
        await self.workbench.capture("ocr", 0)
        self.assertEqual([call[0] for call in self.workbench.reader.capture_calls], ["1"])
        self.assertEqual(self.workbench.phase, "paused")
        saved = self.stored_book()
        self.assertEqual([item["state"] for item in saved["chapters"]], ["blocked", "pending"])
        self.assertEqual(summarize(saved)["missing"], 2)
        result = export_book(saved, self.exports, "txt").read_text(encoding="utf-8")
        self.assertIn("未完成（存在缺失章节）", result)
        self.assertIn("请先登录晋江", result)
        self.assertNotIn("状态：完整", result)

    async def test_transport_failure_preserves_progress_and_does_not_claim_complete(self):
        self.workbench.book = book(chapter(1, "done", "先前的有效正文"), chapter(2), chapter(3))

        async def failed_capture(*_):
            raise TimeoutError("simulated network timeout")

        self.workbench.reader.on_capture = failed_capture
        await self.workbench.capture("ocr", 0)
        saved = self.stored_book()
        self.assertEqual([call[0] for call in self.workbench.reader.capture_calls], ["2"])
        self.assertEqual([item["state"] for item in saved["chapters"]], ["done", "failed", "pending"])
        self.assertIn("超时", saved["chapters"][1]["error"])
        self.assertEqual(summarize(saved)["captured"], 1)
        self.assertFalse(summarize(saved)["complete"])
        self.assertEqual(self.workbench.phase, "paused")

    async def test_pause_during_capture_saves_current_chapter_and_never_starts_next(self):
        self.workbench.book = book(chapter(1), chapter(2))
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_capture(*_):
            started.set()
            await release.wait()
            return {"text": "当前这一章已保存", "method": "ocr", "review": True, "error": "需要校对"}

        self.workbench.reader.on_capture = delayed_capture
        self.workbench.launch(self.workbench.capture("ocr", 10), "capturing")
        await asyncio.wait_for(started.wait(), 1)
        self.assertTrue(self.workbench.busy)
        self.assertEqual(self.stored_book()["chapters"][0]["state"], "capturing")
        self.workbench.pause = True
        release.set()
        await asyncio.wait_for(self.workbench.task, 1)
        self.assertFalse(self.workbench.busy)
        self.assertEqual([call[0] for call in self.workbench.reader.capture_calls], ["1"])
        saved = self.stored_book()
        self.assertEqual([item["state"] for item in saved["chapters"]], ["review", "pending"])
        self.assertEqual(saved["chapters"][0]["text"], "当前这一章已保存")
        self.assertEqual(self.workbench.phase, "paused")

    async def test_immediate_pause_before_capture_task_starts_is_not_lost(self):
        self.workbench.book = book(chapter(1), chapter(2))
        self.workbench.resume_current("ocr", 0)
        self.workbench.pause_all()
        await asyncio.wait_for(self.workbench.task, 1)
        self.assertEqual(self.workbench.reader.capture_calls, [])
        self.assertEqual(self.workbench.phase, "paused")
        self.assertTrue(self.workbench.queue_paused)
        self.assertEqual([item["state"] for item in self.stored_book()["chapters"]], ["pending", "pending"])

    async def test_restart_resets_only_interrupted_capture_to_pending(self):
        self.workbench.book = book(chapter(1, "reviewed", "已校对"), chapter(2, "capturing"), chapter(3, "blocked", error="已锁定"))
        self.workbench.save()
        recovered = server.Workbench()
        self.assertEqual([item["state"] for item in recovered.book["chapters"]], ["reviewed", "pending", "blocked"])
        self.assertEqual(recovered.book["chapters"][0]["text"], "已校对")
        self.assertEqual(recovered.book["chapters"][2]["error"], "已锁定")
        self.assertFalse(recovered.busy)
        self.assertEqual(recovered.reader.profile, self.data / "browser")
        recovered.save()
        self.assertEqual(self.stored_book()["chapters"][1]["state"], "pending")

    async def test_rescan_merges_saved_content_but_preserves_current_metadata(self):
        self.workbench.book = book(chapter(1, "reviewed", "保留人工校对", method="ocr", raw_text="原始文本", source_updated_at="2026-10-01 12:00:00"), chapter(2, "capturing", locked=True, source_updated_at="2026-10-01 13:00:00"))
        self.workbench.save()
        new_url = "https://my.jjwxc.net/onebook_vip.php?novelid=42&chapterid=1"
        latest = book(chapter(1, title="更新后的标题", url=new_url, expected_words=9000, vip=True, source_updated_at="2026-10-09 12:01:02"), chapter(2, locked=False, source_updated_at=""), chapter(3))
        latest["title"] = "更新后的书名"
        self.workbench.reader.scan_result = latest
        await self.workbench.scan(latest["url"])
        saved = self.stored_book()
        self.assertEqual(saved["title"], "更新后的书名")
        self.assertEqual([item["id"] for item in saved["chapters"]], ["1", "2", "3"])
        first = saved["chapters"][0]
        for field in ("title", "url", "expected_words", "vip", "source_updated_at"):
            self.assertEqual(first[field], latest["chapters"][0][field])
        self.assertEqual(first["state"], "reviewed")
        self.assertEqual(first["text"], "保留人工校对")
        self.assertEqual(first["raw_text"], "原始文本")
        self.assertEqual(saved["chapters"][1]["state"], "pending")
        self.assertFalse(saved["chapters"][1]["locked"], "Fresh directory metadata must release a previously locked chapter")
        self.assertEqual(saved["chapters"][1]["source_updated_at"], "")
        self.assertEqual(self.workbench.phase, "ready")

    async def test_launch_clears_busy_after_exception_and_rejects_overlapping_work(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def failed_open(_):
            started.set()
            await release.wait()
            raise ValueError("模拟阅读窗口打开失败")

        self.workbench.reader.on_open = failed_open
        self.workbench.launch(self.workbench.open("first"), "opening")
        await asyncio.wait_for(started.wait(), 1)
        self.assertTrue(self.workbench.busy)
        rejected = self.workbench.open("overlapping")
        with self.assertRaises(HTTPException) as context:
            self.workbench.launch(rejected, "opening")
        self.assertEqual(context.exception.status_code, 409)
        self.assertIsNone(rejected.cr_frame, "Rejected coroutine must be closed without leaking a warning")
        release.set()
        await asyncio.wait_for(self.workbench.task, 1)
        self.assertFalse(self.workbench.busy)
        self.assertEqual(self.workbench.phase, "error")
        self.assertEqual(self.workbench.message, "模拟阅读窗口打开失败")
        self.workbench.reader.on_open = None
        self.workbench.launch(self.workbench.open("retry"), "opening")
        await asyncio.wait_for(self.workbench.task, 1)
        self.assertFalse(self.workbench.busy)
        self.assertTrue(self.workbench.reader.opened)
        self.assertEqual(self.workbench.phase, "ready")
        self.assertEqual(self.workbench.reader.open_calls, ["first", "retry"])

    async def test_known_locked_chapter_remains_missing_without_browser_request(self):
        self.workbench.book = book(chapter(1, locked=True, url=""), chapter(2))
        await self.workbench.capture("ocr", 0)
        self.assertEqual([call[0] for call in self.workbench.reader.capture_calls], ["2"])
        saved = self.stored_book()
        self.assertEqual(saved["chapters"][0]["state"], "blocked")
        self.assertIn("已锁定", saved["chapters"][0]["error"])
        self.assertEqual(summarize(saved)["missing"], 1)
        self.assertFalse(summarize(saved)["complete"])
        self.assertIn("缺失 1 章", self.workbench.message)

    async def test_rescan_preserves_chapter_missing_from_latest_directory(self):
        self.workbench.book = book(chapter(1, "done", "第一章已保存"), chapter(2, "reviewed", "人工校对不能丢失"))
        self.workbench.save()
        self.workbench.reader.scan_result = book(chapter(1))
        await self.workbench.scan(self.workbench.book["url"])
        saved = self.stored_book()
        self.assertEqual(saved["toc_chapter_count"], 1)
        self.assertEqual(len(saved["chapters"]), 2)
        self.assertEqual(saved["chapters"][1]["text"], "人工校对不能丢失")
        self.assertTrue(saved["chapters"][1]["absent_from_toc"])
        self.assertFalse(summarize(saved)["complete"])

    async def test_incomplete_capture_is_retained_for_review_but_not_counted_complete(self):
        self.workbench.book = book(chapter(1))
        async def short_capture(*_):
            return {"text": "短缺的片段", "method": "ocr", "review": True, "incomplete": True, "error": "正文不完整"}
        self.workbench.reader.on_capture = short_capture
        await self.workbench.capture("ocr", 0)
        saved = self.stored_book()
        self.assertEqual(saved["chapters"][0]["text"], "短缺的片段")
        self.assertEqual(saved["chapters"][0]["state"], "failed")
        self.assertEqual(summarize(saved)["missing"], 1)
        self.assertFalse(summarize(saved)["complete"])

    async def test_library_cache_filters_private_fields_and_preserves_books_on_error(self):
        self.workbench.reader.library_result = {
            "account": {"state": "connected", "nickname": "测试读者", "cookie": "must-not-copy"},
            "books": [{"id": "42", "title": "收藏作品", "author": "作者", "url": "https://www.jjwxc.net/onebook.php?novelid=42&token=must-not-copy", "text": "不应放入目录的正文"}],
            "expected_count": 1, "complete": True, "message": "同步完成", "session": "must-not-copy",
        }
        await self.workbench.refresh_library()
        cached = json.loads((self.data / "library.json").read_text())
        self.assertTrue(cached["complete"])
        self.assertEqual(cached["account"]["state"], "connected")
        self.assertTrue(cached["account"]["verified_at"])
        self.assertNotIn("must-not-copy", json.dumps(cached))
        self.assertNotIn("不应放入目录的正文", json.dumps(cached, ensure_ascii=False))

        async def network_failure():
            raise TimeoutError("network timeout")

        self.workbench.reader.on_library = network_failure
        await self.workbench.refresh_library()
        self.assertEqual(self.workbench.library_cache["account"]["state"], "error")
        self.assertEqual(self.workbench.library_cache["account"]["nickname"], "测试读者")
        self.assertEqual(self.workbench.library_cache["books"], cached["books"])
        self.assertTrue(self.workbench.library_cache["complete"])
        self.assertEqual(self.workbench.library_cache["account"]["message"], "收藏刷新失败，请重试。")
        recovered = server.Workbench()
        self.assertEqual(recovered.library_cache, self.workbench.library_cache)

    async def test_saved_connection_error_does_not_claim_a_reading_window_is_open(self):
        recovered = server.clean_library({"account": {"state": "error"}, "books": [{"id": "42"}],
                                          "message": "阅读窗口已关闭。重新打开拾页后可继续。"})
        self.assertEqual(recovered["account"]["message"], "收藏刷新失败，请重试。")
        self.assertEqual(server.human_error(RuntimeError("Target page, context or browser has been closed")), "连接已中断，请重试。")
        self.assertEqual(server.human_error(TimeoutError("network timeout")), "请求超时，请稍后重试。")

    async def test_library_refresh_looks_up_tags_for_shelf_books(self):
        self.workbench.reader.library_result = {"account": {"state": "connected", "nickname": "读者"}, "books": [{"id": "987654321", "title": "收藏作品"}], "complete": True}
        await self.workbench.refresh_library()
        await self.workbench.tags_task
        self.assertEqual(self.workbench.library_state()["books"][0]["tags"], ["百合", "甜文"])
        self.assertEqual(json.loads((self.data / "library-tags.json").read_text())["987654321"], ["百合", "甜文"])
        self.assertEqual(server.Workbench().library_tags["987654321"], ["百合", "甜文"])

    async def test_old_shelf_tags_are_preserved_while_classification_is_added_once(self):
        server.write_json(self.data / "library-tags.json", {"42": ["甜文"]})
        restored = server.Workbench()
        restored.library_cache = server.clean_library({"account": {"state": "connected"}, "books": [{"id": "42"}]})
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", return_value={"tags": ["甜文"], "classification": {"orientation": "百合", "perspective": "互攻", "word_count": 874059}, "classification_version": server.CLASSIFICATION_VERSION}) as fetch:
            restored.launch_tag_lookup()
            await restored.tags_task
            state = restored.library_state()
            self.assertEqual(state["books"][0]["tags"], ["甜文"])
            self.assertEqual(state["books"][0]["classification"]["orientation"], "百合")
            self.assertEqual(state["classification_count"], 1)
            self.assertFalse(state["classification_loading"])
            restored = server.Workbench()
            restored.library_cache = server.clean_library({"books": [{"id": "42"}]})
            restored.launch_tag_lookup()
            self.assertIsNone(restored.tags_task)
            fetch.assert_called_once_with("42")

    async def test_shelf_classification_reuses_existing_details_without_request(self):
        self.workbench.library_cache = server.clean_library({"books": [{"id": "42"}]})
        metadata = {"source": "jjwxc", "genre": "原创-言情-架空历史-奇幻", "perspective": "女主", "status": "连载", "word_count": "10,198", "tags": ["正剧"], "classification_version": server.CLASSIFICATION_VERSION}
        with patch.object(server.catalog, "cached", return_value={"metadata": metadata}), patch.object(server, "public_app_basic_metadata") as fetch:
            await self.workbench.lookup_tags()
            fetch.assert_not_called()
        classification = self.workbench.library_state()["books"][0]["classification"]
        self.assertEqual(classification["orientation"], "言情")
        self.assertEqual(classification["word_count"], 10198)
        self.assertEqual(json.loads((self.data / "library-classifications.json").read_text())["42"], {**classification, "_checked_version": server.CLASSIFICATION_VERSION, "_basic_checked": True})

    async def test_restart_only_reads_missing_shelf_metadata(self):
        shelf = server.clean_library({"books": [{"id": "42"}, {"id": "43"}]})
        server.write_json(self.data / "library.json", shelf)
        server.write_json(self.data / "library-tags.json", {"42": ["甜文"]})
        server.write_json(self.data / "library-classifications.json", {"42": {"orientation": "百合", "_checked_version": server.CLASSIFICATION_VERSION}})
        restored = server.Workbench()
        with patch.object(server.catalog, "cached", return_value=None) as details, patch.object(server, "public_app_basic_metadata", return_value={}) as basic, patch.object(server, "public_author_publications") as dates:
            restored.launch_tag_lookup()
            await restored.tags_task
            details.assert_called_once_with("43")
            basic.assert_called_once_with("43")
            dates.assert_not_called()
        self.assertEqual(restored.library_state()["classification_count"], 1)
        self.assertEqual(restored.library_state()["books"][0]["classification"]["orientation"], "百合")

    async def test_checked_unknown_fields_are_cached_without_repeat_requests(self):
        self.workbench.library_cache = server.clean_library({"books": [{"id": "42"}]})
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", return_value={"tags": [], "classification": {}, "classification_version": server.CLASSIFICATION_VERSION}) as fetch:
            self.workbench.launch_tag_lookup()
            await self.workbench.tags_task
            self.workbench.launch_tag_lookup()
            fetch.assert_called_once_with("42")
        self.assertEqual(self.workbench.library_state()["books"][0]["classification"], {})
        self.assertEqual(self.workbench.library_state()["classification_count"], 1)

    async def test_incremental_classification_progress_survives_cancel(self):
        self.workbench.library_cache = server.clean_library({"books": [{"id": "42"}, {"id": "43"}]})
        release = threading.Event()
        def lookup(identifier):
            if identifier == "43":
                release.wait(2)
                return {}
            return {"tags": ["甜文"], "classification": {"orientation": "百合"}, "classification_version": server.CLASSIFICATION_VERSION}
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", side_effect=lookup):
            self.workbench.launch_tag_lookup()
            async def first_book_loaded():
                while self.workbench.library_state()["classification_count"] == 0:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(first_book_loaded(), 1)
            state = self.workbench.library_state()
            self.assertTrue(state["classification_loading"])
            self.assertEqual(state["classification_count"], 1)
            self.workbench.tags_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.workbench.tags_task
            release.set()
        restored = server.Workbench()
        self.assertEqual(restored.library_classifications, {"42": {"orientation": "百合"}})
        self.assertEqual(restored.classification_checked, {"42"})
        restored.library_cache = self.workbench.library_cache
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", return_value={"tags": [], "classification": {}, "classification_version": server.CLASSIFICATION_VERSION}) as fetch:
            restored.launch_tag_lookup()
            await restored.tags_task
            fetch.assert_called_once_with("43")
        self.assertEqual(restored.library_state()["classification_count"], 2)

    async def test_old_flat_classification_cache_gets_new_fields_once_and_keeps_categories(self):
        old = {"orientation": "百合", "perspective": "互攻", "word_count": 874059}
        server.write_json(self.data / "library-classifications.json", {"42": old})
        server.write_json(self.data / "library-tags.json", {"42": ["甜文"]})
        restored = server.Workbench()
        restored.library_cache = server.clean_library({"books": [{"id": "42"}]})
        self.assertEqual(restored.library_state()["classification_count"], 0)
        extra = {"tags": ["甜文"], "classification": {"published_at": "2026-03-30", "favorite_count": 0}, "classification_version": server.CLASSIFICATION_VERSION}
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", return_value=extra) as fetch:
            restored.launch_tag_lookup()
            await restored.tags_task
            state = restored.library_state()
            self.assertEqual(state["books"][0]["classification"], {**old, "published_at": "2026-03-30", "favorite_count": 0})
            self.assertEqual(state["classification_count"], 1)
            restarted = server.Workbench()
            restarted.library_cache = restored.library_cache
            restarted.launch_tag_lookup()
            self.assertIsNone(restarted.tags_task)
            fetch.assert_called_once_with("42")

    async def test_unknown_publication_after_success_is_not_refetched_on_restart(self):
        self.workbench.library_cache = server.clean_library({"books": [{"id": "42"}]})
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", return_value={"tags": [], "classification": {"favorite_count": 0}, "classification_version": server.CLASSIFICATION_VERSION}) as fetch:
            self.workbench.launch_tag_lookup()
            await self.workbench.tags_task
            restarted = server.Workbench()
            restarted.library_cache = self.workbench.library_cache
            restarted.launch_tag_lookup()
            self.assertIsNone(restarted.tags_task)
            fetch.assert_called_once_with("42")
        self.assertNotIn("published_at", restarted.library_state()["books"][0]["classification"])
        self.assertEqual(restarted.library_state()["classification_count"], 1)

    async def test_publication_failure_saves_basic_fields_and_restart_only_reads_dates(self):
        self.workbench.library_cache = server.clean_library({"books": [{"id": "42"}]})
        extra = {"tags": ["甜文"], "author_id": "7", "classification": {"orientation": "百合", "favorite_count": 0}}
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", return_value=extra), patch.object(server, "public_author_publications", return_value=None):
            await self.workbench.lookup_tags()
        saved = json.loads((self.data / "library-classifications.json").read_text())["42"]
        self.assertTrue(saved["_basic_checked"])
        self.assertEqual(saved["_author_id"], "7")
        self.assertEqual(saved["favorite_count"], 0)
        self.assertNotIn("_checked_version", saved)
        restored = server.Workbench()
        restored.library_cache = self.workbench.library_cache
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata") as basic, patch.object(server, "public_author_publications", return_value={"42": "2026-03-30"}) as dates:
            await restored.lookup_tags()
            basic.assert_not_called()
            dates.assert_called_once_with("7", self.data / "author-publications", "42")
        self.assertEqual(restored.library_state()["classification_count"], 1)
        self.assertEqual(restored.library_state()["books"][0]["classification"]["published_at"], "2026-03-30")
        warm = server.Workbench()
        warm.library_cache = restored.library_cache
        with patch.object(server, "public_app_basic_metadata") as basic, patch.object(server, "public_author_publications") as dates:
            warm.launch_tag_lookup()
            self.assertIsNone(warm.tags_task)
            basic.assert_not_called();dates.assert_not_called()

    async def test_three_unavailable_author_pages_do_not_block_other_books(self):
        self.workbench.library_cache = server.clean_library({"books": [{"id": str(n)} for n in range(42, 46)]})
        def basic(identifier):
            return {"tags": [], "author_id": identifier, "classification": {"orientation": "百合"}}
        def dates(author, directory, identifier):
            return {"45": "2025-01-01"} if identifier == "45" else None
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", side_effect=basic) as fetch, patch.object(server, "public_author_publications", side_effect=dates):
            await self.workbench.lookup_tags()
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(self.workbench.classification_checked, {"45"})
        self.assertEqual(self.workbench.classification_basic, {"42", "43", "44", "45"})

    async def test_author_tls_failures_pause_only_dates_and_finish_basic_facets(self):
        self.workbench.library_cache = server.clean_library({"books": [{"id": str(n)} for n in range(42, 62)]})
        def basic(identifier):
            return {"tags": ["甜文"], "author_id": identifier, "classification": {"orientation": "百合", "favorite_count": 0}}
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", side_effect=basic) as fetch, patch.object(server, "public_author_publications", side_effect=server.PublicMetadataInterrupted("network")) as dates:
            await self.workbench.lookup_tags()
        self.assertEqual(fetch.call_count, 20)
        self.assertLessEqual(dates.call_count, 5)
        state = self.workbench.library_state()
        self.assertEqual(state["classification_basic_count"], 20)
        self.assertEqual(state["classification_count"], 0)
        self.assertTrue(all(book["classification"]["orientation"] == "百合" and book["classification"]["favorite_count"] == 0 for book in state["books"]))

    async def test_lookup_uses_three_workers_and_rate_limit_stops_the_queue(self):
        self.workbench.library_cache = server.clean_library({"books": [{"id": str(n)} for n in range(42, 62)]})
        lock = threading.Lock(); release = threading.Event(); active = 0; maximum = 0
        def basic(identifier):
            nonlocal active, maximum
            with lock:
                active += 1; maximum = max(maximum, active)
                if active == 3:release.set()
            release.wait(1)
            with lock:active -= 1
            raise server.PublicMetadataInterrupted("rate_limit")
        with patch.object(server.catalog, "cached", return_value=None), patch.object(server, "public_app_basic_metadata", side_effect=basic) as fetch:
            await self.workbench.lookup_tags()
        self.assertEqual(maximum, 3)
        self.assertEqual(fetch.call_count, 3)

    async def test_partial_library_merges_cache_and_expired_session_keeps_books(self):
        self.workbench.library_cache = server.clean_library({"account": {"state": "connected", "nickname": "读者"}, "books": [{"id": "41", "title": "旧收藏"}], "complete": True})
        self.workbench.reader.library_result = {"account": {"state": "connected", "nickname": "读者"}, "books": [{"id": "42", "title": "另一页收藏"}], "expected_count": 4, "complete": False, "message": "后续页未完成"}
        await self.workbench.refresh_library()
        self.assertEqual([item["id"] for item in self.workbench.library_cache["books"]], ["41", "42"])
        self.assertFalse(self.workbench.library_cache["complete"])
        self.workbench.reader.library_result = {"account": {"state": "expired"}, "books": [], "complete": False, "message": "请重新连接账号"}
        await self.workbench.refresh_library()
        self.assertEqual(self.workbench.library_cache["account"]["state"], "expired")
        self.assertEqual(len(self.workbench.library_cache["books"]), 2)

    async def test_library_refresh_can_run_during_capture_without_changing_active_book(self):
        self.workbench.book = book(chapter(1))
        started, release = asyncio.Event(), asyncio.Event()

        async def waiting_capture(*_):
            started.set()
            await release.wait()
            return {"text": "保存当前章节", "review": False}

        self.workbench.reader.on_capture = waiting_capture
        self.workbench.launch(self.workbench.capture("ocr", 0), "capturing")
        await asyncio.wait_for(started.wait(), 1)
        self.workbench.reader.library_result = {"account": {"state": "connected"}, "books": [{"id": "99"}], "complete": True}
        self.workbench.launch_library_refresh()
        self.assertTrue(self.workbench.library_syncing)
        await self.workbench.library_task
        self.assertTrue(self.workbench.busy)
        self.assertEqual(self.workbench.book["id"], "42")
        self.assertEqual(self.workbench.library_cache["books"][0]["id"], "99")
        with self.assertRaises(HTTPException) as context:
            self.workbench.launch_account()
        self.assertEqual(context.exception.status_code, 409)
        release.set()
        await self.workbench.task

    async def test_account_connection_holds_queue_until_verified_and_backgrounded(self):
        self.prepare_queue_reader()
        self.workbench.launch_account()
        await self.workbench.account_task
        self.assertTrue(self.workbench.account_waiting)
        self.assertFalse(self.workbench.reader.headless)
        self.workbench.enqueue("42")
        await asyncio.sleep(0)
        self.assertFalse(self.workbench.busy)
        self.workbench.reader.library_result = {"account": {"state": "connected", "nickname": "测试读者"}, "books": [{"id": "42"}], "complete": True}
        self.workbench.launch_account(finish=True)
        await self.workbench.account_task
        await asyncio.sleep(0)
        await self.drain_queue()
        self.assertFalse(self.workbench.account_waiting)
        self.assertTrue(self.workbench.reader.headless)
        self.assertEqual(self.workbench.reader.background_calls, 1)
        self.assertEqual(self.workbench.reader.login_calls, 1)
        self.assertEqual(summarize(self.workbench.book)["captured"], 1)

    async def test_cancel_account_connection_releases_queue_and_closes_visible_browser(self):
        self.prepare_queue_reader()
        self.workbench.launch_account()
        await self.workbench.account_task
        self.workbench.enqueue("42")
        self.workbench.launch_account(cancel=True)
        await self.workbench.account_task
        await asyncio.sleep(0)
        await self.drain_queue()
        self.assertFalse(self.workbench.account_waiting)
        self.assertTrue(self.workbench.reader.headless)
        self.assertEqual(self.workbench.reader.background_calls, 1)
        self.assertEqual(summarize(self.workbench.book)["captured"], 1)

    async def test_connection_import_blocks_reader_work_until_cookie_save_commits(self):
        self.workbench.library_cache = server.clean_library({
            "account": {"state": "connected"}, "books": [{"id": "91"}], "complete": True,
        })
        self.workbench.reader.library_result = {
            "account": {"state": "connected"}, "books": [], "expected_count": 0, "complete": True,
        }
        started, release = threading.Event(), threading.Event()

        def save(_cookies):
            started.set()
            if not release.wait(2):
                raise RuntimeError("Fixture release timed out")

        fake = [{"domain": ".jjwxc.net", "name": "fixture", "value": "synthetic-value", "path": "/"}]
        with patch.object(server, "save_cookies_sync", side_effect=save):
            task = asyncio.create_task(self.workbench.import_session(fake))
            try:
                while not started.is_set():
                    await asyncio.sleep(0.001)
                self.assertTrue(self.workbench.account_busy)
                self.assertTrue(self.workbench.state()["busy"])
                self.workbench.launch_library_refresh()
                self.assertIsNone(self.workbench.library_task)
                with self.assertRaises(HTTPException) as context:
                    self.workbench.launch(self.workbench.scan("42"), "scanning")
                self.assertEqual(context.exception.status_code, 409)
                with self.assertRaises(HTTPException):
                    await self.workbench.import_session(fake)
                with self.assertRaises(HTTPException):
                    self.workbench.launch_account()
            finally:
                release.set()
                result = await task
                await self.workbench.account_task
        self.assertTrue(result["ok"])
        self.assertNotIn("synthetic-value", json.dumps(result))
        self.assertFalse(self.workbench.account_busy)
        self.assertFalse(self.workbench.account_waiting)
        self.assertEqual(self.workbench.reader.library_calls, 1)
        self.assertEqual(self.workbench.reader.background_calls, 1)
        self.assertEqual(self.workbench.library_cache["books"], [])
        metadata = json.loads((self.data / "connection-update.json").read_text())
        self.assertEqual(set(metadata), {"provider", "nonce", "updated_at"})
        self.assertEqual(metadata["provider"], "chrome")
        self.assertNotIn("synthetic-value", json.dumps(metadata))

    async def test_connection_import_refuses_live_download_and_sanitizes_storage_errors(self):
        release = asyncio.Event()
        self.workbench.launch(release.wait(), "capturing")
        fake = [{"domain": ".jjwxc.net", "name": "fixture", "value": "synthetic-private-value", "path": "/"}]
        with patch.object(server, "save_cookies_sync") as save:
            with self.assertRaises(HTTPException) as context:
                await self.workbench.import_session(fake)
            self.assertEqual(context.exception.status_code, 409)
            save.assert_not_called()
        release.set()
        await self.workbench.task
        with patch.object(server, "save_cookies_sync", side_effect=RuntimeError("synthetic-private-value")):
            with self.assertRaises(HTTPException) as context:
                await self.workbench.import_session(fake)
            self.assertEqual(context.exception.status_code, 500)
            self.assertNotIn("synthetic-private-value", context.exception.detail)
        self.assertFalse(self.workbench.account_busy)
        self.assertFalse((self.data / "connection-update.json").exists())

    async def test_expired_import_holds_queue_and_can_retry_with_new_chrome_session(self):
        self.prepare_queue_reader()
        fake = [{"domain": ".jjwxc.net", "name": "fixture", "value": "synthetic-value", "path": "/"}]
        self.workbench.reader.library_result = {"account": {"state": "expired"}, "books": [], "complete": False}
        with patch.object(server, "save_cookies_sync"):
            await self.workbench.import_session(fake)
            await self.workbench.account_task
        self.workbench.enqueue("42")
        await asyncio.sleep(0)
        self.assertTrue(self.workbench.account_waiting)
        self.assertFalse(self.workbench.busy)
        self.assertEqual(self.workbench.reader.capture_calls, [])
        self.workbench.reader.library_result = {"account": {"state": "connected"}, "books": [], "expected_count": 0, "complete": True}
        with patch.object(server, "save_cookies_sync"):
            await self.workbench.import_session(fake)
            await self.workbench.account_task
        await asyncio.sleep(0)
        await self.drain_queue()
        self.assertFalse(self.workbench.account_waiting)
        self.assertEqual(summarize(self.workbench.book)["captured"], 1)

    async def test_explicit_login_partial_sync_does_not_merge_previous_account_books(self):
        self.workbench.library_cache = server.clean_library({
            "account": {"state": "connected"}, "books": [{"id": "91"}], "complete": True,
        })
        self.workbench.launch_account()
        await self.workbench.account_task
        self.workbench.reader.library_result = {
            "account": {"state": "connected"}, "books": [{"id": "92"}],
            "complete": False, "expected_count": 2,
        }
        self.workbench.launch_account(finish=True)
        await self.workbench.account_task
        self.assertEqual([item["id"] for item in self.workbench.library_cache["books"]], ["92"])
        self.assertFalse(self.workbench.library_cache["complete"])
        self.assertFalse(self.workbench.account_waiting)

    async def test_blocked_platform_notice_cannot_be_marked_as_reviewed_text(self):
        notice = "本章是 App 专属福利番外，网页端无法显示。"
        self.workbench.book = book(chapter(1, "blocked", notice, raw_text=notice))
        self.workbench.save()
        with self.assertRaises(HTTPException) as context:
            await server.update_chapter(FakeRequest({"book_id": "42", "id": "1", "text": notice}))
        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(self.workbench.book["chapters"][0]["state"], "blocked")
        self.assertEqual(summarize(self.workbench.book)["captured"], 0)
        self.assertEqual(summarize(self.workbench.book)["missing"], 1)

    async def test_fifo_queue_deduplicates_and_persists_inflight_work(self):
        self.prepare_queue_reader()
        first_started, release = asyncio.Event(), asyncio.Event()
        captured = []

        async def capture(chapter, folder, mode):
            identifier = folder.parent.parent.name
            captured.append(identifier)
            if identifier == "41":
                first_started.set()
                await release.wait()
            return {"text": "排队采集的正文", "review": False}

        self.workbench.reader.on_capture = capture
        self.assertTrue(self.workbench.enqueue("41"))
        await asyncio.wait_for(first_started.wait(), 1)
        self.assertFalse(self.workbench.enqueue("41"))
        self.assertTrue(self.workbench.enqueue("42"))
        self.assertFalse(self.workbench.enqueue("42"))
        persisted = json.loads((self.data / "queue.json").read_text())
        self.assertEqual([item["id"] for item in persisted["items"]], ["41", "42"])
        self.assertEqual(self.workbench.book["id"], "41")
        self.assertTrue(self.workbench.state()["downloads"][0]["active"])
        release.set()
        await self.drain_queue()
        self.assertEqual(captured, ["41", "42"])
        self.assertEqual(self.workbench.queue, [])
        self.assertEqual(json.loads((self.data / "queue.json").read_text())["items"], [])
        self.assertEqual({item["id"] for item in self.workbench.downloads()}, {"41", "42"})

    async def test_pause_halts_entire_queue_and_resume_preserves_saved_chapter(self):
        self.prepare_queue_reader()
        started, release = asyncio.Event(), asyncio.Event()
        captured = []

        async def capture(chapter, folder, mode):
            identifier = folder.parent.parent.name
            captured.append(identifier)
            if identifier == "41":
                started.set()
                await release.wait()
            return {"text": "已完成的一章", "review": False}

        self.workbench.reader.on_capture = capture
        self.workbench.enqueue("41")
        await asyncio.wait_for(started.wait(), 1)
        self.workbench.enqueue("42")
        self.workbench.pause_all()
        release.set()
        await self.workbench.task
        await asyncio.sleep(0)
        self.assertTrue(self.workbench.queue_paused)
        self.assertEqual(captured, ["41"])
        recovered = server.Workbench()
        self.assertTrue(recovered.queue_paused)
        self.assertEqual([item["id"] for item in recovered.queue], ["41", "42"])
        self.workbench.resume_queue()
        await self.drain_queue()
        self.assertEqual(captured, ["41", "42"], "The saved first chapter must not be recaptured")
        self.assertFalse(self.workbench.queue_paused)

    async def test_queue_failure_retains_failed_item_and_stops_later_books(self):
        self.prepare_queue_reader()

        async def failed_scan(_):
            raise TimeoutError("directory timeout")

        self.workbench.reader.on_scan = failed_scan
        self.workbench.queue_paused = True
        self.workbench.enqueue("41")
        self.workbench.enqueue("42")
        self.workbench.resume_queue()
        await self.workbench.task
        await asyncio.sleep(0)
        self.assertFalse(self.workbench.busy)
        self.assertTrue(self.workbench.queue_paused)
        self.assertEqual([item["id"] for item in self.workbench.queue], ["41", "42"])
        self.assertEqual(len(self.workbench.reader.scan_calls), 1)

    async def test_selected_book_edit_and_export_do_not_replace_active_capture(self):
        self.workbench.book = book(chapter(1, "review", "当前作品正文"))
        self.workbench.save()
        other = book(chapter(1, "review", "另一作品正文", raw_text="另一作品原始文本"))
        other.update(id="99", title="另一作品", url="https://www.jjwxc.net/onebook.php?novelid=99")
        self.workbench.save_book(other)
        started, release = asyncio.Event(), asyncio.Event()

        async def ongoing_operation():
            started.set()
            await release.wait()

        self.workbench.launch(ongoing_operation(), "capturing")
        await asyncio.wait_for(started.wait(), 1)
        selected = await server.get_book("99")
        self.assertNotIn("text", selected["chapters"][0])
        self.assertNotIn("raw_text", selected["chapters"][0])
        details = await server.get_chapter("1", book_id="99")
        self.assertEqual(details["book_id"], "99")
        self.assertEqual(details["text"], "另一作品正文")
        await server.update_chapter(FakeRequest({"book_id": "99", "id": "1", "text": "校对另一作品"}))
        exported = await server.export(FakeRequest({"book_id": "99", "format": "txt"}))
        self.assertIn("校对另一作品", Path(exported["path"]).read_text())
        self.assertEqual(self.workbench.book["id"], "42")
        self.assertEqual(self.workbench.book["chapters"][0]["text"], "当前作品正文")
        with self.assertRaises(HTTPException) as context:
            await server.update_chapter(FakeRequest({"book_id": "42", "id": "1", "text": "不应改动"}))
        self.assertEqual(context.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            await server.export(FakeRequest({"book_id": "42", "format": "txt"}))
        with self.assertRaises(HTTPException) as context:
            await server.get_book("../42")
        self.assertEqual(context.exception.status_code, 400)
        release.set()
        await self.workbench.task


if __name__ == "__main__":
    unittest.main()
