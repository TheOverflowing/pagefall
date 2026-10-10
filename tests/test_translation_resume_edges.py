"""Title-change and process-restart boundaries with an offline transport."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from novelist.translation_jobs import run_translation
from novelist.translation_settings import DEFAULT_SETTINGS, PROVIDERS
from novelist.translation_store import TranslationStore


def workbench():
    return SimpleNamespace(pause=False, translation=None, phase="idle", message="")


class TranslationResumeEdgesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novelist-resume-edges-")
        self.addCleanup(self.temp.cleanup)
        self.store = TranslationStore(Path(self.temp.name) / "translations")
        self.book = {"id": "fixture_42", "title": "Offline book", "author": "Fixture", "chapters": [
            {"id": "1", "title": "Original chapter title", "text": "離線テストの原文です。", "state": "done"}
        ]}
        self.config = {**DEFAULT_SETTINGS, "source_language": "ja", "target_language": "en",
                       "base_url": PROVIDERS["deepseek"]["base_url"]}
        self.work = workbench()
        self.requests = []
        self.pause_on_first = False
        self.pause_on_request = None

    async def transport(self, config, key, payload, request_id):
        self.requests.append(request_id)
        request = json.loads(payload["messages"][1]["content"])
        if self.pause_on_first and len(self.requests) == 1 or len(self.requests) == self.pause_on_request:
            self.work.pause = True
        summary = hashlib.sha256(json.dumps(request['source_segments'], sort_keys=True).encode()).hexdigest()[:12]
        output = {"title": "Translated " + request["source_title"],
                  "segments": [{"id": segment["id"], "text": "Offline translated paragraph."} for segment in request["source_segments"]],
                  "glossary_updates": [], "summary": "Offline summary " + summary, "quality_notes": []}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}

    async def run_job(self, work=None, store=None):
        with patch("novelist.translation.request_completion", self.transport):
            await run_translation(work or self.work, store or self.store, self.book, self.config,
                                  "fixture-not-a-real-key", "all")

    async def test_changed_title_after_pause_replans_instead_of_permanent_checkpoint_mismatch(self):
        self.pause_on_first = True
        original_text = self.book["chapters"][0]["text"]
        await self.run_job()
        self.assertEqual(self.work.phase, "paused")
        saved = self.store.read_work(self.book["id"], "en", "1")
        old_checkpoint = copy.deepcopy(saved["checkpoint"])
        self.assertEqual(len(old_checkpoint["chunks"]), 1)
        self.book["chapters"][0]["title"] = "Author revised chapter title"
        resumed = workbench()
        await self.run_job(resumed)
        self.assertEqual(resumed.phase, "finished")
        self.assertEqual(len(self.requests), 2, "A changed source title changes the translation request")
        self.assertNotEqual(self.requests[0], self.requests[1])
        row = self.store.chapter(self.book, self.book["chapters"][0], full=True)
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["translated_title"], "Translated Author revised chapter title")
        self.assertEqual(self.book["chapters"][0]["text"], original_text)

    async def test_changed_title_marks_completed_translation_stale_until_replaced(self):
        await self.run_job()
        old = self.store.chapter(self.book, self.book["chapters"][0], full=True)
        self.book["chapters"][0]["title"] = "Updated title"
        stale = self.store.chapter(self.book, self.book["chapters"][0], full=True)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["text"], old["text"])
        self.assertEqual(stale["translated_title"], old["translated_title"])
        self.assertEqual(self.store.summary(self.book)["stale"], 1)
        await self.run_job(workbench())
        fresh = self.store.chapter(self.book, self.book["chapters"][0], full=True)
        self.assertEqual(fresh["status"], "done")
        self.assertEqual(fresh["translated_title"], "Translated Updated title")
        self.assertEqual(len(self.requests), 2)

    async def test_process_recovery_only_marks_inflight_state_and_preserves_checkpoint_for_reuse(self):
        self.pause_on_first = True
        await self.run_job()
        index = self.store.load(self.book["id"])
        index["chapters"]["1"]["status"] = "translating"
        self.store.save(index)
        path = self.store.chapter_path(self.book["id"], "en", "1")
        before = path.read_bytes()
        reopened = TranslationStore(self.store.root)
        with patch("novelist.translation.request_completion", side_effect=AssertionError("Recovery must not contact providers")):
            reopened.recover_interrupted()
        self.assertEqual(reopened.load(self.book["id"])["chapters"]["1"]["status"], "paused")
        self.assertEqual(path.read_bytes(), before)
        resumed = workbench()
        await self.run_job(resumed, reopened)
        self.assertEqual(resumed.phase, "finished")
        self.assertEqual(len(self.requests), 1, "Explicit resume reuses the persisted completed chunk")

    def add_later_chapters(self, count=1):
        for number in range(2, 2 + count):
            self.book['chapters'].append({'id': str(number), 'title': f'Chapter {number}',
                                          'text': f'第{number}章の離線テストです。', 'state': 'done'})

    async def test_upstream_text_edit_stales_later_results_and_refreshes_continuity(self):
        self.add_later_chapters()
        await self.run_job()
        old_work = self.store.read_work(self.book['id'], 'en', '2')
        original_second = self.store.chapter(self.book, self.book['chapters'][1], full=True)
        self.book['chapters'][0]['text'] += ' 前章の関係が修正されました。'
        original_source = copy.deepcopy(self.book)
        self.assertEqual(self.store.summary(self.book)['stale'], 2)
        stale_second = self.store.chapter(self.book, self.book['chapters'][1], full=True)
        self.assertEqual(stale_second['status'], 'stale')
        self.assertEqual(stale_second['text'], original_second['text'])
        await self.run_job(workbench())
        new_work = self.store.read_work(self.book['id'], 'en', '2')
        new_first = self.store.read_work(self.book['id'], 'en', '1')['result']
        self.assertEqual(len(self.requests), 4)
        self.assertNotEqual(new_work['plan']['source_dependency'], old_work['plan']['source_dependency'])
        self.assertNotEqual(new_work['initial_context'], old_work['initial_context'])
        self.assertEqual(new_work['initial_context']['continuity_summary'], new_first['summary'])
        self.assertEqual(self.store.summary(self.book)['done'], 2)
        self.assertEqual(self.book, original_source)

    async def test_upstream_edit_rebuilds_paused_later_chapter_context_and_checkpoint(self):
        self.add_later_chapters()
        self.pause_on_request = 2
        await self.run_job()
        self.assertEqual(self.work.phase, 'paused')
        old_work = self.store.read_work(self.book['id'], 'en', '2')
        self.assertEqual(len(old_work['checkpoint']['chunks']), 1)
        self.book['chapters'][0]['text'] += ' 新しい前章の内容です。'
        resumed = workbench()
        await self.run_job(resumed)
        new_work = self.store.read_work(self.book['id'], 'en', '2')
        self.assertEqual(resumed.phase, 'finished')
        self.assertEqual(len(self.requests), 4)
        self.assertNotEqual(new_work['initial_context'], old_work['initial_context'])
        self.assertNotEqual(new_work['checkpoint']['fingerprint'], old_work['checkpoint']['fingerprint'])
        self.assertEqual(self.store.summary(self.book)['done'], 2)

    async def test_unchanged_upstream_preserves_paused_later_chapter_checkpoint(self):
        self.add_later_chapters()
        self.pause_on_request = 2
        await self.run_job()
        old_work = self.store.read_work(self.book['id'], 'en', '2')
        resumed = workbench()
        await self.run_job(resumed)
        new_work = self.store.read_work(self.book['id'], 'en', '2')
        self.assertEqual(resumed.phase, 'finished')
        self.assertEqual(len(self.requests), 2, 'Unchanged context must not cause a repeated paid request')
        self.assertEqual(new_work['initial_context'], old_work['initial_context'])
        self.assertEqual(new_work['checkpoint'], old_work['checkpoint'])

    async def test_upstream_title_and_catalog_order_invalidate_later_translations(self):
        self.add_later_chapters(2)
        await self.run_job()
        original_title = self.book['chapters'][0]['title']
        self.book['chapters'][0]['title'] = 'Changed upstream title'
        self.assertEqual(self.store.summary(self.book)['stale'], 3)
        self.book['chapters'][0]['title'] = original_title
        self.assertEqual(self.store.summary(self.book)['done'], 3)
        self.book['chapters'][0], self.book['chapters'][1] = self.book['chapters'][1], self.book['chapters'][0]
        self.assertEqual(self.store.summary(self.book)['stale'], 3)
        self.assertEqual(len(self.requests), 3, 'Checking freshness never requests a translation')

    async def test_unread_insertions_do_not_change_valid_source_dependencies(self):
        self.add_later_chapters()
        await self.run_job()
        before = self.store.source_dependencies(self.book)
        self.book['chapters'].insert(1, {'id': 'gate', 'title': 'Gated', 'text': 'Platform notice', 'state': 'blocked'})
        self.book['chapters'].insert(2, {'id': 'partial', 'title': 'Incomplete', 'text': 'Incomplete source', 'state': 'review', 'incomplete': True})
        self.book['chapters'].insert(3, {'id': 'whitespace', 'title': 'Empty', 'text': ' \n ', 'state': 'done'})
        after = self.store.source_dependencies(self.book)
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual(self.store.summary(self.book)['done'], 2)
        self.assertEqual(self.store.summary(self.book)['total'], 2)
        await self.run_job(workbench())
        self.assertEqual(len(self.requests), 2)


if __name__ == "__main__":
    unittest.main()
