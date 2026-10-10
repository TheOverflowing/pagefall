"""Offline translation orchestration and persistence boundary regressions.

Provider calls are always replaced by local fixtures. Passing these tests
demonstrates state/serialization behavior, not literary quality or paid usage.
"""

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import xml.etree.ElementTree as ET
import zipfile

from fastapi import HTTPException
from novelist.exporters import export_book
from novelist.translation_jobs import run_translation
from novelist.translation_settings import DEFAULT_SETTINGS, PROVIDERS
from novelist.translation_store import TranslationStore, digest
from novelist import server
import test_workbench as workbench_fixtures


FAKE_KEY = "fixture-only-not-a-real-api-key"


def fixture_book(book_id="syosetu_n101ab"):
    return {"id": book_id, "title": "离线小说夹具", "author": "Fixture", "chapters": [
        {"id": "10", "title": "First", "text": "原文十。", "state": "done"},
        {"id": "2", "title": "Second", "text": "原文二。", "state": "review"},
        {"id": "3", "title": "Third", "text": "原文三。", "state": "reviewed"},
        {"id": "blocked", "title": "Blocked", "text": "访问提示，不是正文", "state": "blocked"},
        {"id": "pending", "title": "Pending", "text": "", "state": "pending"},
        {"id": "incomplete", "title": "Incomplete", "text": "未收全正文", "state": "review", "incomplete": True},
    ]}


def fixture_config(**overrides):
    return {**DEFAULT_SETTINGS, "base_url": PROVIDERS["deepseek"]["base_url"], **overrides}


def fixture_result(chapter_id, *, terms=None):
    return {"translated_text": "Fixture translation " + chapter_id, "translated_title": "Translated " + chapter_id,
            "summary": "Summary " + chapter_id, "glossary_updates": terms or [], "quality_issues": [],
            "usage": {"requests": 1, "total_tokens": 50}}


def workbench():
    return SimpleNamespace(pause=False, translation=None, phase="idle", message="")


class TranslationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="novelist-translation-integration-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = TranslationStore(self.root / "translations")
        self.book = fixture_book()
        self.config = fixture_config()
        self.workbench = workbench()

    async def translate(self, book=None, config=None, scope="all", translator=None, work=None, store=None):
        translator = translator or AsyncMock(side_effect=lambda chapter, *_args, **_kwargs: fixture_result(chapter["id"]))
        with patch("novelist.translation.translate_chapter", translator):
            await run_translation(work or self.workbench, store or self.store, book or self.book,
                                  config or self.config, FAKE_KEY, scope)
        return translator

    def files_text(self):
        return "\n".join(path.read_text() for path in self.root.rglob("*.json"))

    async def test_first_then_all_preserves_source_and_skips_already_completed_first(self):
        original = copy.deepcopy(self.book)
        first = await self.translate(scope="first")
        self.assertEqual([call.args[0]["id"] for call in first.call_args_list], ["10"])
        self.assertEqual(self.workbench.translation["done"], 1)
        remaining = await self.translate()
        self.assertEqual([call.args[0]["id"] for call in remaining.call_args_list], ["2", "3"])
        self.assertEqual(self.workbench.translation["done"], 3)
        self.assertEqual(self.workbench.translation["total"], 3)
        self.assertEqual(self.book, original)
        self.assertNotIn(FAKE_KEY, self.files_text())
        self.assertNotIn("api_key", self.files_text())
        self.assertNotIn("原文十。", self.files_text())

    async def test_summary_uses_same_complete_source_eligibility_as_jobs(self):
        await self.translate()
        result = self.store.summary(self.book)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["done"], 3)
        self.assertEqual(result["status"], "done")

    async def test_glossary_edit_marks_old_translation_stale_without_destroying_text(self):
        await self.translate(scope="first")
        original = self.store.chapter(self.book, self.book["chapters"][0], full=True)["text"]
        terms = [{"source": "名前", "target": "姓名", "kind": "term"}]
        self.store.update_glossary(self.book["id"], terms)
        row = self.store.chapter(self.book, self.book["chapters"][0], full=True)
        self.assertEqual(row["status"], "stale")
        self.assertEqual(row["text"], original)
        index = self.store.load(self.book["id"])
        revision = index["glossary_revision"]
        self.store.update_glossary(self.book["id"], index["glossary"])
        self.assertEqual(self.store.load(self.book["id"])["glossary_revision"], revision)
        self.assertEqual(self.store.summary(self.book)["stale"], 1)

    async def test_failed_retranslation_preserves_previous_text_and_source(self):
        await self.translate(scope="first")
        original = self.store.chapter(self.book, self.book["chapters"][0], full=True)["text"]
        self.book["chapters"][0]["text"] += "用户修订的原文。"
        source = copy.deepcopy(self.book)
        failing = AsyncMock(side_effect=RuntimeError("fixture provider internal failure"))
        await self.translate(scope="first", translator=failing)
        row = self.store.chapter(self.book, self.book["chapters"][0], full=True)
        self.assertEqual(row["text"], original)
        self.assertNotEqual(row["status"], "done")
        self.assertEqual(self.book, source)
        self.assertEqual(self.workbench.phase, "error")
        self.assertNotIn("fixture provider internal failure", self.files_text())

    async def test_provider_change_marks_previous_result_stale_and_keeps_it_until_replaced(self):
        await self.translate(scope="first")
        before = self.store.read_work(self.book["id"], "zh-Hans", "10")["result"]
        updated = fixture_config(provider="openrouter", model="author/model", base_url=PROVIDERS["openrouter"]["base_url"])
        self.store.create(self.book["id"], updated)
        self.assertEqual(self.store.chapter(self.book, self.book["chapters"][0])["status"], "stale")
        self.assertEqual(self.store.read_work(self.book["id"], "zh-Hans", "10")["result"], before)
        translated = await self.translate(config=updated, scope="first")
        self.assertEqual(translated.await_count, 1)
        self.assertEqual(self.store.chapter(self.book, self.book["chapters"][0])["status"], "done")

    async def test_automatic_terms_flow_forward_without_rewriting_existing_chapters(self):
        terms = [{"source": "原文", "target": "Original", "kind": "term"}]
        calls = []

        async def translating(chapter, source, target, glossary, context, config, key, **kwargs):
            calls.append((chapter["id"], copy.deepcopy(glossary), copy.deepcopy(context)))
            return fixture_result(chapter["id"], terms=terms if chapter["id"] == "10" else [])

        await self.translate(translator=translating)
        self.assertEqual(calls[0][1], [])
        self.assertEqual(calls[1][1][0]["source"], "原文")
        self.assertTrue(calls[1][1][0]["locked"])
        self.assertEqual(calls[1][2]["previous_summaries"], [{"chapter_id": "10", "summary": "Summary 10"}])
        self.assertEqual(self.store.load(self.book["id"])["glossary_revision"], 0)
        self.assertEqual(self.store.chapter(self.book, self.book["chapters"][0])["status"], "done")

    async def test_untrusted_value_error_cannot_persist_or_display_api_key(self):
        error = ValueError("transport failure included " + FAKE_KEY)
        await self.translate(scope="first", translator=AsyncMock(side_effect=error))
        self.assertNotIn(FAKE_KEY, self.files_text())
        self.assertNotIn(FAKE_KEY, self.workbench.message)
        self.assertNotIn(FAKE_KEY, json.dumps(self.workbench.translation))
        self.assertFalse(self.workbench.translation["running"])

    async def test_pause_saves_checkpoint_before_return_and_restart_reuses_completed_segments(self):
        book = fixture_book()
        book["chapters"] = [{"id": "10", "title": "Long", "text": "一" * 3200 + "\n\n" + "二" * 3200, "state": "done"}]
        requests = []

        async def transport(config, key, payload, request_id):
            self.assertEqual(key, FAKE_KEY)
            requests.append(request_id)
            if len(requests) == 1:
                self.workbench.pause = True
            source = json.loads(payload["messages"][1]["content"])
            output = {"title": "Translated long chapter", "segments": [{"id": segment["id"], "text": "译文分段 " + segment["id"]} for segment in source["source_segments"]],
                      "glossary_updates": [], "summary": "Safe fixture summary", "quality_notes": []}
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}], "usage": {"total_tokens": 100}}

        with patch("novelist.translation.request_completion", transport):
            await run_translation(self.workbench, self.store, book, self.config, FAKE_KEY, "all")
            self.assertEqual(self.workbench.phase, "paused")
            persisted = self.store.read_work(book["id"], "zh-Hans", "10")
            self.assertEqual(len(persisted["checkpoint"]["chunks"]), 1)
            self.assertEqual(len(requests), 1)
            restarted = workbench()
            await run_translation(restarted, TranslationStore(self.store.root), book, self.config, FAKE_KEY, "all")
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(set(requests)), 2)
        self.assertEqual(restarted.phase, "finished")
        self.assertEqual(self.store.chapter(book, book["chapters"][0])["status"], "done")
        self.assertNotIn(FAKE_KEY, self.files_text())

    async def test_crash_after_result_before_index_commit_reuses_completed_checkpoint(self):
        book = fixture_book()
        book["chapters"] = book["chapters"][:1]
        requests = []

        async def transport(config, key, payload, request_id):
            requests.append(request_id)
            source = json.loads(payload["messages"][1]["content"])
            output = {"title": "Translated chapter", "segments": [{"id": segment["id"], "text": "译文夹具正文"} for segment in source["source_segments"]],
                      "glossary_updates": [], "summary": "Safe fixture summary", "quality_notes": []}
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}

        class SimulatedCrash(BaseException):
            pass

        original_save = self.store.save

        def crash_before_index(value):
            if any(row.get("status") == "done" for row in value["chapters"].values()):
                raise SimulatedCrash()
            original_save(value)

        with patch("novelist.translation.request_completion", transport):
            with patch.object(self.store, "save", side_effect=crash_before_index):
                with self.assertRaises(SimulatedCrash):
                    await run_translation(self.workbench, self.store, book, self.config, FAKE_KEY, "all")
            self.assertEqual(len(requests), 1)
            self.assertTrue(self.store.read_work(book["id"], "zh-Hans", "10")["result"]["text"])
            restarted = workbench()
            await run_translation(restarted, TranslationStore(self.store.root), book, self.config, FAKE_KEY, "all")
        self.assertEqual(len(requests), 1, "Persisted completed chunks must not be sent again after restart")
        self.assertEqual(self.store.chapter(book, book["chapters"][0])["status"], "done")

    async def test_books_and_target_languages_keep_independent_translation_files(self):
        await self.translate(scope="first")
        another = fixture_book("jjwxc_101")
        another["chapters"][0]["text"] = "另一本书原文。"
        await self.translate(book=another, scope="first", translator=AsyncMock(return_value=fixture_result("other-book")))
        await self.translate(scope="first", config=fixture_config(target_language="en"), translator=AsyncMock(return_value=fixture_result("english")))
        self.assertEqual(self.store.chapter(self.book, self.book["chapters"][0], full=True, language="zh-Hans")["text"], "Fixture translation 10")
        self.assertEqual(self.store.chapter(self.book, self.book["chapters"][0], full=True, language="en")["text"], "Fixture translation english")
        self.assertEqual(self.store.chapter(another, another["chapters"][0], full=True)["text"], "Fixture translation other-book")
        self.assertEqual(self.store.selected(self.book["id"]), "en")
        self.assertEqual(self.store.selected(another["id"]), "zh-Hans")
        self.assertEqual(self.store.load(another["id"])["book_id"], another["id"])

    def test_book_chapter_and_target_paths_reject_traversal_before_writing(self):
        for book_id in ("../outside", "/absolute", "a/b", "a\\b", "", "a" * 81):
            with self.subTest(book_id=book_id), self.assertRaises(ValueError):
                self.store.create(book_id, self.config)
        for target in ("../outside", "en/other", "/absolute", "auto", "en\x00"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.store.create(self.book["id"], fixture_config(target_language=target))
        for chapter_id in ("../outside", "/absolute", "a/b", ""):
            with self.subTest(chapter_id=chapter_id), self.assertRaises(ValueError):
                self.store.write_work(self.book["id"], "en", chapter_id, {"result": {}})
        self.assertEqual(list(self.root.rglob("*.json")), [])

    async def test_translation_exports_keep_order_and_untranslated_placeholders(self):
        await self.translate(scope="first")
        original = copy.deepcopy(self.book)
        translated = self.store.export_book(self.book)
        self.assertEqual([row["id"] for row in translated["chapters"]], [row["id"] for row in self.book["chapters"]])
        self.assertEqual(translated["chapters"][0]["state"], "review")
        self.assertTrue(all(row["text"] == "" for row in translated["chapters"][1:]))
        for format in ("txt", "md", "epub"):
            path = export_book(translated, self.root / "exports", format)
            if format == "epub":
                with zipfile.ZipFile(path) as archive:
                    texts = [archive.read(name).decode() for name in archive.namelist() if name.endswith(".xhtml")]
                    content = "\n".join(texts)
            else:
                content = path.read_text()
            self.assertIn("Fixture translation 10", content)
            self.assertIn("本章正文缺失", content)
            self.assertNotIn("原文十。", content)
            self.assertNotIn("访问提示，不是正文", content)
            self.assertIn("Third", content)
        self.assertEqual(self.book, original)

    async def test_stale_translation_is_preserved_locally_but_not_exported_as_current(self):
        await self.translate()
        self.book["chapters"][2]["text"] += "修改。"
        exported = self.store.export_book(self.book)
        self.assertTrue(exported["chapters"][0]["text"])
        self.assertTrue(exported["chapters"][1]["text"])
        self.assertEqual(exported["chapters"][2]["text"], "")
        self.assertEqual(exported["chapters"][2]["state"], "blocked")
        self.assertTrue(self.store.chapter(self.book, self.book["chapters"][2], full=True)["text"])

    async def test_english_translation_epub_uses_target_language_metadata(self):
        await self.translate(scope="first", config=fixture_config(target_language="en"))
        translated = self.store.export_book(self.book)
        with zipfile.ZipFile(export_book(translated, self.root / "exports", "epub")) as archive:
            package = ET.fromstring(archive.read("EPUB/package.opf"))
            namespace = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
            self.assertEqual(package.find("opf:metadata/dc:language", namespace).text, "en")
            chapter = ET.fromstring(archive.read("EPUB/chapter_00001.xhtml"))
            self.assertEqual(chapter.attrib.get("lang"), "en")


async def http_json(path, *, method="GET", data=None, query=b"", origin=None):
    """Call actual middleware and routes without creating a network listener."""
    messages = []
    consumed = False

    async def receive():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": json.dumps(data or {}).encode() if method == "POST" else b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    headers = [(b"host", b"127.0.0.1:8765")]
    if method == "POST":
        headers.append((b"content-type", b"application/json"))
    if origin:
        headers.append((b"origin", origin.encode()))
    await server.app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                      "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                      "raw_path": path.encode(), "query_string": query, "root_path": "", "headers": headers,
                      "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8765)}, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), json.loads(body)


class TranslationRouteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        workbench_fixtures.WorkbenchTests.setUp(self)
        self.workbench.book = fixture_book()
        self.workbench.save()
        self.config = fixture_config(has_key=True)
        from novelist import translation_settings
        for name, result in (("load_settings", self.config), ("load_provider_key", FAKE_KEY), ("has_provider_key", True)):
            mocked = patch.object(translation_settings, name, return_value=result)
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)

    async def asyncTearDown(self):
        await workbench_fixtures.WorkbenchTests.asyncTearDown(self)

    async def test_settings_get_reports_flags_without_loading_or_serializing_key(self):
        status, headers, response = await http_json("/api/translation/settings")
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertTrue(response["has_key"])
        self.assertEqual({provider["id"] for provider in response["providers"]}, {"deepseek", "openrouter"})
        self.load_provider_key.assert_not_called()
        self.assertNotIn(FAKE_KEY, json.dumps(response))
        self.assertNotIn("api_key", response)

    async def test_export_route_defaults_to_clean_and_explicit_report_uses_separate_file(self):
        for format in ("txt", "md", "epub"):
            with self.subTest(format=format):
                data = {"book_id": self.workbench.book["id"], "format": format}
                status, _, result = await http_json("/api/export", method="POST", data=data)
                self.assertEqual(status, 200)
                self.assertTrue(result["clean"])
                self.assertGreater(result["summary"]["missing"], 0)
                clean_path = Path(result["path"])
                clean_bytes = clean_path.read_bytes()
                if format == "epub":
                    with zipfile.ZipFile(clean_path) as archive:
                        self.assertNotIn("EPUB/report.xhtml", archive.namelist())
                        content = "\n".join(archive.read(name).decode() for name in archive.namelist() if name.endswith(".xhtml"))
                else:
                    content = clean_path.read_text()
                self.assertNotIn("导出检查报告", content)
                self.assertNotIn("采集状态", content)
                self.assertIn("【本章正文缺失】", content)
                self.assertIn("原文十。", content)
                status, _, reported = await http_json("/api/export", method="POST", data={**data, "clean": False})
                self.assertEqual(status, 200)
                self.assertFalse(reported["clean"])
                self.assertNotEqual(reported["path"], str(clean_path))
                self.assertEqual(clean_path.read_bytes(), clean_bytes)
                if format == "epub":
                    with zipfile.ZipFile(reported["path"]) as archive:
                        self.assertIn("EPUB/report.xhtml", archive.namelist())
                else:
                    self.assertIn("导出检查报告", Path(reported["path"]).read_text())

    async def test_translation_export_route_clean_keeps_target_text_and_missing_placeholders(self):
        source = copy.deepcopy(self.workbench.book)
        translated = AsyncMock(side_effect=lambda chapter, *_args, **_kwargs: fixture_result(chapter["id"]))
        with patch("novelist.translation.translate_chapter", translated):
            await run_translation(self.workbench, server.translation_store(), self.workbench.book,
                                  fixture_config(target_language="en"), FAKE_KEY, "first")
        for format in ("txt", "md", "epub"):
            with self.subTest(format=format):
                status, _, result = await http_json("/api/export", method="POST", data={"book_id": source["id"], "variant": "translation", "format": format})
                self.assertEqual(status, 200)
                self.assertTrue(result["clean"])
                self.assertEqual(result["summary"]["captured"], 1)
                if format == "epub":
                    with zipfile.ZipFile(result["path"]) as archive:
                        self.assertIsNone(archive.testzip())
                        self.assertNotIn("EPUB/report.xhtml", archive.namelist())
                        for name in archive.namelist():
                            if name.endswith((".xml", ".opf", ".xhtml")):
                                ET.fromstring(archive.read(name))
                        package = ET.fromstring(archive.read("EPUB/package.opf"))
                        self.assertEqual(package.find("{http://www.idpf.org/2007/opf}metadata/{http://purl.org/dc/elements/1.1/}language").text, "en")
                        content = "\n".join(archive.read(name).decode() for name in archive.namelist() if name.endswith(".xhtml"))
                else:
                    content = Path(result["path"]).read_text()
                self.assertIn("Fixture translation 10", content)
                self.assertIn("Translated 10", content)
                self.assertIn("【本章正文缺失】", content)
                self.assertNotIn("原文十。", content)
                self.assertNotIn("导出检查报告", content)
                self.assertNotIn("采集状态", content)
                self.assertNotIn("AI 译本", content)
                self.assertNotIn(FAKE_KEY, content)
        self.assertEqual(self.workbench.book, source)

    async def test_export_route_rejects_non_boolean_clean_without_writing_files(self):
        for value in (None, 0, 1, "true", "false", [], {}):
            with self.subTest(value=value):
                status, _, _ = await http_json("/api/export", method="POST", data={"book_id": self.workbench.book["id"], "clean": value})
                self.assertEqual(status, 400)
        self.assertEqual(list(self.exports.iterdir()), [])

    async def test_settings_write_treats_key_as_write_only_and_origin_check_precedes_writes(self):
        from novelist import translation_settings
        with patch.object(translation_settings, "save_settings", return_value=self.config) as saved:
            status, _, response = await http_json("/api/translation/settings", method="POST", data={"api_key": FAKE_KEY})
            self.assertEqual(status, 200)
            self.assertNotIn(FAKE_KEY, json.dumps(response))
            self.assertEqual(saved.call_args.args[0], {"api_key": FAKE_KEY})
            saved.reset_mock()
            status, _, response = await http_json("/api/translation/settings", method="POST", data={"api_key": FAKE_KEY}, origin="https://external.example")
            self.assertEqual(status, 403)
            saved.assert_not_called()

    async def test_missing_key_stops_before_job_or_translation_files_exist(self):
        self.load_provider_key.return_value = None
        status, _, response = await http_json("/api/translate", method="POST", data={"book_id": self.workbench.book["id"], "scope": "first"})
        self.assertEqual(status, 400)
        self.assertIn("API Key", response["detail"])
        self.assertFalse(self.workbench.busy)
        self.assertFalse((self.data / "translations").exists())

    async def test_known_chinese_auto_rejects_chinese_before_loading_key_or_starting_job(self):
        for platform, identifier in (("jjwxc", "42"), ("gongzicp", "gongzicp_42"), ("fanqie", "fanqie_42"), (None, "9669851")):
            for target in ("zh", "zh-Hans", "zh-Hant", "zh-TW"):
                with self.subTest(platform=platform, target=target):
                    self.workbench.book = fixture_book(identifier)
                    if platform is not None:
                        self.workbench.book["platform"] = platform
                    self.load_settings.return_value = fixture_config(has_key=True)
                    self.load_provider_key.reset_mock()
                    with patch("novelist.translation_jobs.run_translation", new_callable=AsyncMock) as job:
                        status, _, response = await http_json("/api/translate", method="POST", data={"book_id": identifier, "target_language": target})
                    self.assertEqual(status, 400)
                    self.assertIn("源语言和目标语言相同", response["detail"])
                    self.load_provider_key.assert_not_called()
                    job.assert_not_called()
                    self.assertFalse(self.workbench.busy)
        self.assertFalse((self.data / "translations").exists())

    async def test_known_chinese_auto_to_english_resolves_source_to_chinese(self):
        for platform, identifier in (("jjwxc", "42"), ("gongzicp", "gongzicp_42"), ("fanqie", "fanqie_42"), (None, "9669851")):
            with self.subTest(platform=platform):
                self.workbench.book = fixture_book(identifier)
                if platform is not None:
                    self.workbench.book["platform"] = platform
                with patch("novelist.translation_jobs.run_translation", new_callable=AsyncMock) as job:
                    status, _, _ = await http_json("/api/translate", method="POST", data={"book_id": identifier, "source_language": "auto", "target_language": "en", "scope": "first"})
                    self.assertEqual(status, 200)
                    await self.workbench.task
                job.assert_awaited_once()
                self.assertEqual(job.await_args.args[3]["source_language"], "zh")
                self.assertEqual(job.await_args.args[3]["target_language"], "en")
                self.assertEqual(job.await_args.args[5], "first")

    async def test_unknown_platform_auto_remains_auto_including_nonnumeric_legacy_ids(self):
        for platform, identifier in ((None, "unknown_42"), ("syosetu", "n101ab"), ("other_platform", "42")):
            with self.subTest(platform=platform, identifier=identifier):
                self.workbench.book = fixture_book(identifier)
                if platform is not None:
                    self.workbench.book["platform"] = platform
                with patch("novelist.translation_jobs.run_translation", new_callable=AsyncMock) as job:
                    status, _, _ = await http_json("/api/translate", method="POST", data={"book_id": identifier, "source_language": "auto", "target_language": "zh-Hans"})
                    self.assertEqual(status, 200)
                    await self.workbench.task
                job.assert_awaited_once()
                self.assertEqual(job.await_args.args[3]["source_language"], "auto")

    async def test_explicit_source_language_overrides_platform_inference(self):
        self.workbench.book = {**fixture_book("42"), "platform": "jjwxc"}
        with patch("novelist.translation_jobs.run_translation", new_callable=AsyncMock) as job:
            status, _, _ = await http_json("/api/translate", method="POST", data={"book_id": "42", "source_language": "ja", "target_language": "zh-Hans"})
            self.assertEqual(status, 200)
            await self.workbench.task
        job.assert_awaited_once()
        self.assertEqual(job.await_args.args[3]["source_language"], "ja")

    async def test_same_primary_language_variants_are_rejected_before_key_access(self):
        for source, target in (("zh-Hant", "zh-Hans"), ("zh", "zh-Hant"), ("en-US", "en-GB"), ("ja-JP", "ja")):
            with self.subTest(source=source, target=target):
                self.load_provider_key.reset_mock()
                with patch("novelist.translation_jobs.run_translation", new_callable=AsyncMock) as job:
                    status, _, _ = await http_json("/api/translate", method="POST", data={"book_id": self.workbench.book["id"], "source_language": source, "target_language": target})
                self.assertEqual(status, 400)
                self.load_provider_key.assert_not_called()
                job.assert_not_called()

    async def test_invalid_scope_book_language_or_empty_source_does_not_start_paid_work(self):
        for data in (
            {"book_id": self.workbench.book["id"], "scope": "typo"},
            {"book_id": "../escape", "scope": "all"},
            {"book_id": "missing-book", "scope": "all"},
            {"book_id": self.workbench.book["id"], "source_language": "en", "target_language": "en"},
            {"book_id": self.workbench.book["id"], "target_language": "../escape"},
        ):
            with self.subTest(data=data):
                self.load_provider_key.reset_mock()
                status, _, _ = await http_json("/api/translate", method="POST", data=data)
                self.assertIn(status, (400, 404))
                self.load_provider_key.assert_not_called()
                self.assertFalse(self.workbench.busy)
        self.workbench.book["chapters"] = [{"id": "1", "title": "Gate", "text": "Login notice", "state": "blocked"}]
        status, _, _ = await http_json("/api/translate", method="POST", data={"book_id": self.workbench.book["id"]})
        self.assertEqual(status, 400)
        self.assertFalse((self.data / "translations").exists())

    async def test_target_book_route_writes_only_requested_book_and_public_detail_is_scoped(self):
        other = fixture_book("fanqie_42")
        self.workbench.save_book(other)
        original_current = copy.deepcopy(self.workbench.book)
        translated = AsyncMock(side_effect=lambda chapter, *_args, **_kwargs: fixture_result(chapter["id"]))
        with patch("novelist.translation.translate_chapter", translated):
            status, _, response = await http_json("/api/translate", method="POST", data={"book_id": other["id"], "scope": "first"})
            self.assertEqual(status, 200)
            self.assertTrue(response["ok"])
            await self.workbench.task
        self.assertEqual(translated.await_count, 1)
        self.assertEqual(self.workbench.book, original_current)
        store = server.translation_store()
        self.assertIsNone(store.load(original_current["id"]))
        self.assertIsNotNone(store.load(other["id"]))
        status, _, response = await http_json("/api/chapter", query=b"book_id=fanqie_42&id=10")
        self.assertEqual(status, 200)
        self.assertEqual(response["book_id"], "fanqie_42")
        self.assertEqual(response["translation"]["text"], "Fixture translation 10")
        self.assertEqual(response["text"], other["chapters"][0]["text"])
        status, _, response = await http_json("/api/book", query=b"id=fanqie_42")
        self.assertEqual(status, 200)
        self.assertEqual(response["translation"]["done"], 1)
        self.assertNotIn("text", response["chapters"][0]["translation"])
        self.assertNotIn(FAKE_KEY, json.dumps(response))

    async def test_active_translation_blocks_source_and_glossary_edits_and_pause_requests_checkpoint(self):
        gate = asyncio.Event()
        self.workbench.task = asyncio.create_task(gate.wait())
        self.workbench.translation.update(running=True, book_id=self.workbench.book["id"])
        original = copy.deepcopy(self.workbench.book)
        for route, data in (
            ("/api/chapter", {"book_id": original["id"], "id": "10", "text": "Edited"}),
            ("/api/translation/glossary", {"book_id": original["id"], "terms": []}),
        ):
            status, _, _ = await http_json(route, method="POST", data=data)
            self.assertEqual(status, 409)
        self.assertEqual(self.workbench.book, original)
        status, _, _ = await http_json("/api/translate/pause", method="POST", data={})
        self.assertEqual(status, 200)
        self.assertTrue(self.workbench.pause)
        self.assertTrue(self.workbench.translation["running"], "Pause must wait for the in-flight checkpoint")
        gate.set()
        await self.workbench.task

    async def test_glossary_creation_rechecks_idle_after_waiting_for_settings(self):
        entered, release = threading.Event(), threading.Event()

        def delayed_settings():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture settings wait timed out")
            return dict(self.config)

        self.load_settings.side_effect = delayed_settings
        request = workbench_fixtures.FakeRequest({"book_id": self.workbench.book["id"], "terms": [{"source": "原文", "target": "Original"}]})
        saving = asyncio.create_task(server.save_translation_glossary(request))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            # A translation starts while the first settings read is suspended.
            store = server.translation_store()
            store.create(self.workbench.book["id"], self.config)
            gate = asyncio.Event()
            self.workbench.task = asyncio.create_task(gate.wait())
            self.workbench.translation.update(running=True, book_id=self.workbench.book["id"])
            release.set()
            with self.assertRaises(HTTPException) as raised:
                await saving
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(store.glossary(self.workbench.book["id"])["terms"], [])
            gate.set()
            await self.workbench.task
        finally:
            release.set()
            if not saving.done():
                saving.cancel()
            await asyncio.gather(saving, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
