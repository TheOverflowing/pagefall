"""Repeated downloads use independent snapshots and preserve readable old copies."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from novelist import server
from novelist.browser import CaptureError
from novelist.translation_store import digest
import test_workbench as fixtures


class BookVersionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkbenchTests.setUp(self)
        self.original = fixtures.book(fixtures.chapter(1, 'review', '旧的 OCR 正文。', method='ocr'))
        self.workbench.book = copy.deepcopy(self.original)
        self.workbench.save()
        self.workbench.reader.scan_result = fixtures.book(fixtures.chapter(1))
        self.proofread_patch = patch.object(server, 'apply_proofreading', return_value=False)
        self.proofread_patch.start()
        self.addCleanup(self.proofread_patch.stop)

    async def asyncTearDown(self):
        await fixtures.WorkbenchTests.asyncTearDown(self)

    def refresh_entry(self):
        entry = self.workbench.download_entry(self.original['url'], refresh=True)
        self.workbench.queue = [entry]
        self.workbench.active_download = entry
        return entry

    async def test_ocr_download_can_be_downloaded_again_as_api_without_losing_old_version(self):
        entry = self.refresh_entry()
        async def capture(*_):
            return {'text': '新的接口正文。', 'raw_text': '新的接口正文。', 'review': False, 'method': 'jjwxc_api'}
        self.workbench.reader.on_capture = capture
        await self.workbench.download(entry)
        self.assertEqual(self.workbench.book['chapters'][0]['text'], '新的接口正文。')
        versions = server.book_versions().list('42')
        self.assertEqual([version['source'] for version in versions], ['api', 'ocr'])
        self.assertEqual(server.book_versions().get('42', versions[1]['id'])['chapters'][0]['text'], '旧的 OCR 正文。')
        chapter_history = server.chapter_versions().list('42', '1')
        self.assertEqual(len(chapter_history), 2)
        self.assertEqual(entry['completed'], ['1'])

    async def test_incomplete_refresh_keeps_saved_text_and_proofreading(self):
        self.workbench.book['chapters'][0]['proofreading'] = {'preserved': True}
        self.workbench.save()
        entry = self.refresh_entry()
        async def capture(*_):
            return {'text': '不完整', 'incomplete': True, 'review': True, 'method': 'ocr'}
        self.workbench.reader.on_capture = capture
        await self.workbench.download(entry)
        chapter = self.workbench.book['chapters'][0]
        self.assertEqual(chapter['text'], '旧的 OCR 正文。')
        self.assertEqual(chapter['proofreading'], {'preserved': True})
        self.assertEqual(chapter['state'], 'review')
        self.assertIn('1', entry['failures'])
        self.assertEqual(entry['completed'], [])
        self.assertEqual(server.book_versions().list('42')[0]['refresh_failed'], 1)
        self.assertEqual((await server.get_chapter('1', '42'))['text'], '旧的 OCR 正文。')

    async def test_locked_refresh_preserves_existing_readable_body(self):
        self.workbench.reader.scan_result['chapters'][0].update(locked=True, state='blocked')
        entry = self.refresh_entry()
        await self.workbench.download(entry)
        self.assertEqual(self.workbench.reader.capture_calls, [])
        self.assertEqual(self.workbench.book['chapters'][0]['text'], '旧的 OCR 正文。')
        self.assertEqual(self.workbench.book['chapters'][0]['state'], 'review')
        self.assertTrue(server.complete_local_text(self.workbench.book['chapters'][0]))

    async def test_pause_restart_keeps_same_run_and_skips_already_refreshed_chapters(self):
        self.workbench.book['chapters'].append(fixtures.chapter(2, 'review', '第二章旧正文。', method='ocr'))
        self.workbench.save()
        self.workbench.reader.scan_result = fixtures.book(fixtures.chapter(1), fixtures.chapter(2))
        entry = self.refresh_entry()
        async def first(chapter, *_):
            self.workbench.pause = True
            return {'text': '第一章新版。', 'review': False, 'method': 'jjwxc_api'}
        self.workbench.reader.on_capture = first
        await self.workbench.download(entry)
        self.assertEqual(entry['completed'], ['1'])
        self.assertEqual(len(server.book_versions().list('42')), 2)
        resumed = server.Workbench()
        self.assertEqual(resumed.queue[0]['run_id'], entry['run_id'])
        self.assertTrue(resumed.queue[0]['refresh'])
        self.assertEqual(resumed.queue[0]['completed'], ['1'])
        resumed.reader.scan_result = self.workbench.reader.scan_result
        resumed.active_download = resumed.queue[0]
        await resumed.download(resumed.queue[0])
        self.assertEqual([item[0] for item in resumed.reader.capture_calls], ['2'])
        self.assertEqual(len(server.book_versions().list('42')), 2)
        snapshot = server.book_versions().get('42', entry['run_id'])
        self.assertEqual(snapshot['chapters'][0]['text'], '第一章新版。')
        self.assertEqual(snapshot['chapters'][1]['method'], 'text')

    async def test_repeated_default_download_automatically_refreshes_complete_saved_book(self):
        self.workbench.queue_paused = True
        self.workbench.enqueue(self.original['url'])
        self.assertTrue(self.workbench.queue[0]['refresh'])
        self.assertEqual(self.workbench.download_progress(self.workbench.book), {'refresh': True, 'done': 0, 'total': 1})

    async def test_delete_snapshot_does_not_change_current_or_other_version_and_does_not_reimport(self):
        initial = await server.list_book_versions('42')
        old_id = initial['versions'][0]['id']
        second = server.book_versions().record({**self.original, 'title': '第二次下载'})
        await server.delete_book_version(fixtures.FakeRequest({'book_id': '42', 'version_id': old_id}))
        versions = (await server.list_book_versions('42'))['versions']
        self.assertEqual([version['id'] for version in versions], [second['id']])
        self.assertEqual(self.workbench.book['chapters'][0]['text'], '旧的 OCR 正文。')
        self.assertTrue(list((self.data / 'book-versions' / '.trash' / '42').glob('*')))
        await server.delete_book_version(fixtures.FakeRequest({'book_id': '42', 'version_id': second['id']}))
        self.assertEqual((await server.list_book_versions('42'))['versions'], [])
        self.assertIsNone(server.book_versions().record(self.original, version_id=second['id']))
        with self.assertRaises(HTTPException) as missing:
            await server.get_book_version('42', old_id)
        self.assertEqual(missing.exception.status_code, 404)

    async def test_snapshot_has_readonly_reader_and_clean_exports(self):
        version_id = (await server.list_book_versions('42'))['versions'][0]['id']
        self.workbench.book['chapters'][0]['text'] = '当前正文已经修改。'
        chapter = await server.get_book_version_chapter('42', version_id, '1')
        self.assertEqual(chapter['text'], '旧的 OCR 正文。')
        self.assertTrue(chapter['read_only'])
        self.assertEqual(chapter['reading_source'], 'edition')
        public = await server.get_book_version('42', version_id)
        self.assertNotIn('text', public['chapters'][0])
        self.assertTrue(public['chapters'][0]['readable_locally'])
        for format in ['txt', 'md', 'epub']:
            output = await server.export_book_version(fixtures.FakeRequest({'book_id': '42', 'version_id': version_id, 'format': format}))
            self.assertTrue(Path(output['path']).is_file())
            if format != 'epub':
                content = Path(output['path']).read_text()
                self.assertIn('旧的 OCR 正文。', content)
                self.assertNotIn('当前正文已经修改。', content)
                self.assertNotIn('检查报告', content)

    async def test_snapshot_translation_remains_readable_and_exportable_after_current_translation_changes(self):
        chapter = self.workbench.book['chapters'][0]
        store = server.translation_store()
        index = store.create('42', {'provider': 'deepseek', 'model': 'fixture', 'source_language': 'zh', 'target_language': 'en'})
        index['chapters']['1'] = {'status': 'done', 'source_hash': digest(chapter['text']),
                                  'source_title': chapter['title'], 'source_dependency': store.source_dependencies(self.workbench.book)['1'],
                                  'glossary_revision': 0, 'translated_characters': 17}
        store.save(index)
        store.write_work('42', 'en', '1', {'result': {'text': 'Old translation.', 'translated_title': 'Chapter one'}})
        version_id = (await server.list_book_versions('42'))['versions'][0]['id']
        store.write_work('42', 'en', '1', {'result': {'text': 'Changed translation.'}})
        saved_chapter = await server.get_book_version_chapter('42', version_id, '1')
        self.assertEqual(saved_chapter['translation']['text'], 'Old translation.')
        self.assertTrue((await server.get_book_version('42', version_id))['translation']['has_text'])
        output = await server.export_book_version(fixtures.FakeRequest({'book_id': '42', 'version_id': version_id,
                                                                      'format': 'txt', 'variant': 'translation'}))
        self.assertIn('Old translation.', Path(output['path']).read_text())
        self.assertNotIn('Changed translation.', Path(output['path']).read_text())

    async def test_chapter_version_delete_keeps_current_text_and_remains_deleted_on_list(self):
        version_id = (await server.list_chapter_versions('42', '1'))['versions'][0]['id']
        result = await server.delete_chapter_version(fixtures.FakeRequest({'book_id': '42', 'chapter_id': '1', 'version_id': version_id}))
        self.assertEqual(result['versions'], [])
        self.assertEqual((await server.list_chapter_versions('42', '1'))['versions'], [])
        self.assertEqual(self.workbench.book['chapters'][0]['text'], '旧的 OCR 正文。')

    async def test_capture_error_does_not_replace_saved_text(self):
        entry = self.refresh_entry()
        async def fail(*_):
            raise CaptureError('需要重新连接账号。', 'blocked', True)
        self.workbench.reader.on_capture = fail
        await self.workbench.download(entry)
        self.assertEqual(self.workbench.phase, 'paused')
        self.assertEqual(self.workbench.book['chapters'][0]['text'], '旧的 OCR 正文。')
        self.assertEqual(entry['completed'], [])

    async def test_two_versions_export_original_and_translation_without_overwriting_each_other_or_current(self):
        store = server.translation_store()
        snapshots = []
        for number in (1, 2):
            chapter = self.workbench.book['chapters'][0]
            chapter['text'] = f'第 {number} 个版本的正文。'
            index = store.create('42', {'provider': 'deepseek', 'model': 'fixture', 'source_language': 'zh', 'target_language': 'en'})
            index['chapters']['1'] = {'status': 'done', 'source_hash': digest(chapter['text']),
                                      'source_title': chapter['title'], 'source_dependency': store.source_dependencies(self.workbench.book)['1'],
                                      'glossary_revision': 0, 'translated_characters': 20}
            store.save(index)
            translated = f'Translation version {number}.'
            store.write_work('42', 'en', '1', {'result': {'text': translated, 'translated_title': 'Chapter one'}})
            snapshot = server.book_versions().record(self.workbench.book, translations=self.data / 'translations')
            snapshots.append((snapshot['id'], chapter['text'], translated))
        self.workbench.book['chapters'][0]['text'] = '当前工作稿正文。'
        store.write_work('42', 'en', '1', {'result': {'text': 'Current working translation.'}})
        outputs = []
        for version_id, original, translated in snapshots:
            for variant, expected in (('source', original), ('translation', translated)):
                output = await server.export_book_version(fixtures.FakeRequest({'book_id': '42', 'version_id': version_id,
                                                                                'format': 'txt', 'variant': variant}))
                outputs.append((output, expected))
        current = await server.export(fixtures.FakeRequest({'book_id': '42', 'format': 'txt'}))
        self.assertIn('当前工作稿正文。', Path(current['path']).read_text())
        self.assertEqual(len({output['path'] for output, _ in outputs}), 4)
        for output, expected in outputs:
            self.assertNotEqual(output['path'], current['path'])
            self.assertIn(expected, Path(output['path']).read_text())
            self.assertEqual(output['url'], '/exports/' + Path(output['path']).relative_to(self.exports).as_posix())

    async def test_archived_export_stays_available_while_current_book_is_being_updated(self):
        version_id = (await server.list_book_versions('42'))['versions'][0]['id']
        self.workbench.chapter_refresh_id = '42'
        with self.assertRaises(HTTPException) as busy:
            await server.export(fixtures.FakeRequest({'book_id': '42', 'format': 'txt'}))
        self.assertEqual(busy.exception.status_code, 409)
        output = await server.export_book_version(fixtures.FakeRequest({'book_id': '42', 'version_id': version_id, 'format': 'txt'}))
        self.assertIn('旧的 OCR 正文。', Path(output['path']).read_text())

    async def test_deleted_or_unknown_export_version_returns_not_found_without_current_fallback(self):
        version_id = (await server.list_book_versions('42'))['versions'][0]['id']
        await server.delete_book_version(fixtures.FakeRequest({'book_id': '42', 'version_id': version_id}))
        for selected in (version_id, 'missing-version'):
            with self.subTest(version_id=selected), self.assertRaises(HTTPException) as missing:
                await server.export_book_version(fixtures.FakeRequest({'book_id': '42', 'version_id': selected, 'format': 'txt'}))
            self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(list(self.exports.iterdir()), [])
