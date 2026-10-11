"""User-requested spacing is a minimum between chapter starts, not an extra wait."""
import asyncio
import unittest
from unittest.mock import patch

from novelist import server
from novelist.browser import CaptureError
import test_workbench as fixtures


class CapturePacingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkbenchTests.setUp(self)
        self.workbench.book = fixtures.book(fixtures.chapter(1), fixtures.chapter(2))
        self.clock = 100.0
        self.starts = []
        self.waits = []

    async def asyncTearDown(self):
        await fixtures.WorkbenchTests.asyncTearDown(self)

    async def capture(self, duration, delay, pause_during_wait=False, method="jjwxc_api", fail_first=False):
        async def read(*_):
            self.starts.append(self.clock)
            self.clock += duration
            if fail_first and len(self.starts) == 1:
                raise CaptureError("本次未取得正文。", "failed", False)
            return {"text": "测试正文。", "raw_text": "测试正文。", "review": False, "method": method}

        async def wait(operation, timeout):
            operation.close()
            self.waits.append(timeout)
            if pause_during_wait:
                self.workbench.pause = True
                return
            self.clock += timeout
            raise asyncio.TimeoutError()

        self.workbench.reader.on_capture = read
        with patch.object(server, "monotonic", side_effect=lambda: self.clock), patch.object(server.asyncio, "wait_for", side_effect=wait):
            await self.workbench.capture("auto", delay)

    async def test_fast_capture_waits_only_for_remaining_interval(self):
        await self.capture(duration=1, delay=3)
        self.assertEqual(self.starts, [100, 103])
        self.assertEqual(self.waits, [2])
        self.assertEqual(self.workbench.phase, "finished")

    async def test_slow_capture_does_not_wait_again(self):
        await self.capture(duration=4, delay=3)
        self.assertEqual(self.starts, [100, 104])
        self.assertEqual(self.waits, [])

    async def test_larger_platform_interval_is_preserved(self):
        await self.capture(duration=4, delay=10)
        self.assertEqual(self.starts, [100, 110])
        self.assertEqual(self.waits, [6])

    async def test_pause_interrupts_remaining_wait_without_starting_next_chapter(self):
        await self.capture(duration=1, delay=3, pause_during_wait=True)
        self.assertEqual(self.starts, [100])
        self.assertEqual(self.workbench.book["chapters"][0]["state"], "done")
        self.assertEqual(self.workbench.book["chapters"][1]["state"], "pending")
        self.assertEqual(self.workbench.phase, "paused")

    async def test_direct_api_uses_shorter_configured_interval(self):
        await self.capture(duration=0.2, delay=1)
        self.assertEqual(self.starts, [100, 101])
        self.assertAlmostEqual(self.waits[0], 0.8)

    async def test_web_fallback_keeps_three_second_floor(self):
        await self.capture(duration=0.2, delay=1, method="jjwxc_upstream")
        self.assertEqual(self.starts, [100, 103])
        self.assertAlmostEqual(self.waits[0], 2.8)

    async def test_ocr_fallback_keeps_three_second_floor(self):
        await self.capture(duration=0.2, delay=1, method="ocr")
        self.assertEqual(self.starts, [100, 103])
        self.assertAlmostEqual(self.waits[0], 2.8)

    async def test_failed_refresh_does_not_use_old_saved_api_method_to_reduce_interval(self):
        self.workbench.book["chapters"][0].update(text="原来的接口正文。", state="done", method="jjwxc_api")
        entry = self.workbench.download_entry(self.workbench.book["url"], refresh=True)
        self.workbench.queue = [entry]
        self.workbench.active_download = entry
        await self.capture(duration=0.2, delay=1, fail_first=True)
        self.assertEqual(self.starts, [100, 103])
        self.assertEqual(self.workbench.book["chapters"][0]["text"], "原来的接口正文。")

    async def test_short_api_interval_remains_interruptible(self):
        await self.capture(duration=0.2, delay=1, pause_during_wait=True)
        self.assertEqual(self.starts, [100])
        self.assertEqual(self.workbench.phase, "paused")
