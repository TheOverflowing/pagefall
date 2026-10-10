"""Local correction lifecycle: reversible, resumable, and never a human approval."""
import asyncio
import copy
import unittest

from fastapi import HTTPException
from novelist import server
from novelist.exporters import summarize, proofread_current
import test_workbench as fixtures


class ProofreadingWorkbenchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkbenchTests.setUp(self)
        self.original = fixtures.book(
            fixtures.chapter(1, "review", "清晨，林溪走进书店。\n窗外的雨已经停了。", raw_text="原始结果保持不变。"),
            fixtures.chapter(2, "reviewed", "已经由读者校对的正文。"),
            fixtures.chapter(3, "blocked", "平台访问提示。"),
            fixtures.chapter(4, "failed", "不完整正文。", incomplete=True),
        )
        self.workbench.book = copy.deepcopy(self.original)
        self.workbench.save()

    async def asyncTearDown(self):
        await fixtures.WorkbenchTests.asyncTearDown(self)

    async def test_batch_keeps_original_and_skips_reviewed_or_unavailable_chapters(self):
        await self.workbench.proofread_book(self.workbench.book)
        chapters = self.workbench.book["chapters"]
        self.assertTrue(proofread_current(chapters[0]))
        self.assertEqual(chapters[0]["state"], "review")
        self.assertFalse(chapters[0]["proofreading"]["human_reviewed"])
        self.assertEqual(chapters[0]["raw_text"], self.original["chapters"][0]["raw_text"])
        self.assertEqual(chapters[1:], self.original["chapters"][1:])
        self.assertEqual(self.workbench.proofreading["done"], 1)
        self.assertTrue(server.public_book(self.workbench.book)["proofreading"]["can_undo"])

    async def test_repeat_is_idempotent_and_undo_restores_exact_prior_chapter(self):
        await self.workbench.proofread_book(self.workbench.book)
        first = copy.deepcopy(self.workbench.book["chapters"])
        await self.workbench.proofread_book(self.workbench.book)
        self.assertEqual(first, self.workbench.book["chapters"])
        await server.undo_proofread(fixtures.FakeRequest({"book_id": "42"}))
        self.assertEqual(self.workbench.book["chapters"], self.original["chapters"])

    async def test_human_edit_wins_over_undo_and_invalidates_automatic_result(self):
        await self.workbench.proofread_book(self.workbench.book)
        await server.update_chapter(fixtures.FakeRequest({"book_id": "42", "id": "1", "text": "读者亲自修改后的正文。"}))
        with self.assertRaises(HTTPException):
            await server.undo_proofread(fixtures.FakeRequest({"book_id": "42"}))
        chapter = self.workbench.book["chapters"][0]
        self.assertEqual(chapter["text"], "读者亲自修改后的正文。")
        self.assertEqual(chapter["state"], "reviewed")
        self.assertNotIn("proofreading", chapter)

    async def test_pause_persists_completed_chapters_and_resume_does_not_replace_undo(self):
        self.workbench.book["chapters"] = [fixtures.chapter(i, "review", "独立测试段落。") for i in range(1, 31)]
        operation = asyncio.create_task(self.workbench.proofread_book(self.workbench.book))
        while self.workbench.proofreading["done"] == 0:
            await asyncio.sleep(0)
        self.workbench.pause = True
        await operation
        count = self.workbench.proofreading["done"]
        self.assertGreater(count, 0)
        self.assertLess(count, 30)
        self.assertEqual(self.workbench.phase, "paused")
        snapshot = copy.deepcopy(self.workbench.book["chapters"][0]["proofread_undo"])
        await self.workbench.proofread_book(self.workbench.book)
        self.assertEqual(self.workbench.proofreading["done"], 30)
        self.assertEqual(self.workbench.book["chapters"][0]["proofread_undo"], snapshot)

    async def test_other_book_is_locked_during_batch_without_changing_current_book(self):
        self.workbench.proofreading.update(book_id="other", running=True)
        with self.assertRaises(HTTPException):
            server.require_book_idle({"id": "other"})
        self.assertEqual(self.workbench.book["id"], "42")

    async def test_modified_text_no_longer_counts_as_auto_processed(self):
        await self.workbench.proofread_book(self.workbench.book)
        self.assertEqual(summarize(self.workbench.book)["auto_processed"], 1)
        self.workbench.book["chapters"][0]["text"] += "发生变化。"
        self.assertEqual(summarize(self.workbench.book)["auto_processed"], 0)


if __name__ == "__main__":
    unittest.main()
