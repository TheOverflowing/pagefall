"""Defaults follow the direct API rate; explicit and persisted delays stay intact."""
import json
import unittest
from unittest.mock import patch

from novelist import server, jinjiang
import test_workbench as fixtures


class DownloadIntervalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkbenchTests.setUp(self)
        patcher = patch.object(jinjiang, 'API_REQUEST_INTERVAL', 1.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workbench.book = fixtures.book(fixtures.chapter(1))
        self.workbench.save()

    async def asyncTearDown(self):
        await fixtures.WorkbenchTests.asyncTearDown(self)

    async def test_default_and_explicit_api_delays_are_distinct(self):
        for requested, expected in ((None, 1.0), (0.2, 1.0), (3, 3), (10, 10)):
            with self.subTest(requested=requested):
                self.assertEqual(self.workbench.download_entry('42', delay=requested)['delay'], expected)

    async def test_other_modes_and_platforms_keep_existing_floors(self):
        for url, mode, expected in (('42', 'ocr', 3), ('https://fanqienovel.com/page/42', 'auto', 3),
                                    ('https://www.gongzicp.com/novel-42.html', 'auto', 10)):
            with self.subTest(url=url, mode=mode):
                self.assertEqual(self.workbench.download_entry(url, mode, 1)['delay'], expected)
                self.assertEqual(self.workbench.download_entry(url, mode)['delay'], expected)

    async def test_download_endpoint_uses_default_when_delay_omitted(self):
        self.workbench.queue_paused = True
        await server.download(fixtures.FakeRequest({'url': '42', 'mode': 'auto'}))
        self.assertEqual(self.workbench.queue[0]['delay'], 1.0)

    async def test_restart_preserves_new_default_and_legacy_explicit_three(self):
        self.workbench.queue = [self.workbench.download_entry('41'), self.workbench.download_entry('42', delay=3)]
        self.workbench.save_queue()
        stored = json.loads((self.data / 'queue.json').read_text())
        self.assertEqual([item['delay'] for item in stored['items']], [1.0, 3])
        recovered = server.Workbench()
        self.assertEqual([item['delay'] for item in recovered.queue], [1.0, 3])

    async def test_resume_without_delay_preserves_existing_explicit_setting(self):
        entry = self.workbench.download_entry('42', delay=10, refresh=True)
        entry['completed'] = ['1']
        self.workbench.queue = [entry]
        def close_operation(operation, *_args, **_kwargs):
            operation.close()
        with patch.object(self.workbench, 'launch', side_effect=close_operation):
            await server.start(fixtures.FakeRequest({'mode': 'auto'}))
        self.assertEqual(self.workbench.queue[0]['delay'], 10)
        self.assertEqual(self.workbench.queue[0]['run_id'], entry['run_id'])
        self.assertEqual(self.workbench.queue[0]['completed'], ['1'])

    async def test_start_without_existing_queue_uses_new_default(self):
        def close_operation(operation, *_args, **_kwargs):
            operation.close()
        with patch.object(self.workbench, 'launch', side_effect=close_operation):
            await server.start(fixtures.FakeRequest({'mode': 'auto'}))
        self.assertEqual(self.workbench.queue[0]['delay'], 1.0)
