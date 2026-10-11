"""Incremental update checks without live accounts, browser sessions or novels."""
import copy
from pathlib import Path
import tempfile
import unittest

from novelist.update_monitor import UpdateMonitor, text_hash


def chapter(identifier, text="正文一。", updated="2026-10-10 12:00:00", **fields):
    return {"id": str(identifier), "title": "第" + str(identifier) + "章", "text": text,
            "source_updated_at": updated, "expected_words": len(text), "method": "api", **fields}


def book(identifier="42", count=1):
    return {"id": identifier, "title": "测试作品", "url": "https://www.jjwxc.net/onebook.php?novelid=" + identifier,
            "chapters": [chapter(index) for index in range(1, count + 1)]}


class FakeHost:
    def __init__(self):
        self.saved = {}
        self.scope = {}
        self.subscribed = {"books": [], "complete": True}
        self.calls = []
        self.busy = False
        self.failures = set()

    def monitor_busy(self):
        return self.busy

    async def monitor_subscriptions(self, operation):
        return copy.deepcopy(self.subscribed)

    def monitor_books(self):
        return list(self.scope.values())

    def monitor_chapters(self, identifier):
        return {chapter_id for book_id, chapter_id in self.saved if book_id == identifier}

    def monitor_snapshot(self, book_id, chapter_id):
        return self.saved.get((book_id, chapter_id))

    async def refresh_saved_chapter(self, source, chapter_id, previous_source_hash=None, save_new_book=False):
        key = (source["id"], chapter_id)
        self.calls.append(key)
        if key in self.failures:
            raise ValueError("Locked or access pending")
        value = copy.deepcopy(next(item for item in source["chapters"] if item["id"] == chapter_id))
        if value.get("locked"):
            raise ValueError("Locked")
        self.saved[key] = value
        return value


class FakeCatalog:
    def __init__(self):
        self.books = {}
        self.calls = []

    async def watch_fetch(self, entry):
        self.calls.append(entry["id"])
        return copy.deepcopy(self.books[entry["id"]])


class UpdateMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.host, self.catalog = FakeHost(), FakeCatalog()
        self.monitor = UpdateMonitor(self.directory, self.host, self.catalog, None)
        self.now = 2000000000

    def save_book(self, value=None):
        value = value or book()
        self.host.scope[value["id"]] = copy.deepcopy(value)
        self.catalog.books[value["id"]] = copy.deepcopy(value)
        for current in value["chapters"]:
            self.host.saved[(value["id"], current["id"])] = copy.deepcopy(current)

    def subscribe(self, value):
        self.host.subscribed = {"books": [{**value, "auto_subscribed": True}], "complete": True}
        self.catalog.books[value["id"]] = copy.deepcopy(value)

    async def check(self, elapsed=0, force=True):
        await self.monitor.check_once(force=force, now=self.now + elapsed)

    async def test_unchanged_source_needs_no_body_request_and_restart_reuses_baseline(self):
        self.save_book()
        await self.check()
        self.assertEqual(self.host.calls, [])
        self.monitor = UpdateMonitor(self.directory, self.host, self.catalog, None)
        await self.check(901)
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.monitor.status()["revision"], 0)

    async def test_changed_body_marks_only_actual_change_and_acknowledgement_persists(self):
        self.save_book()
        await self.check()
        self.catalog.books["42"]["chapters"][0] = chapter(1, "作者修改的正文。", "2026-10-10 13:00:00")
        await self.check(901)
        status = self.monitor.status()
        self.assertEqual((status["changed_count"], status["revision"]), (1, 1))
        decorated = self.monitor.decorate(book())
        self.assertEqual(decorated["chapters"][0]["update_status"], "changed")
        self.monitor.mark_read("42", "1")
        self.monitor = UpdateMonitor(self.directory, self.host, self.catalog, None)
        self.assertEqual((self.monitor.status()["changed_count"], self.monitor.status()["revision"]), (0, 2))

    async def test_timestamp_only_change_does_not_mark_text_change(self):
        self.save_book()
        await self.check()
        self.catalog.books["42"]["chapters"][0]["source_updated_at"] = "2026-10-10 14:00:00"
        await self.check(901)
        await self.check(1802)
        self.assertEqual(self.host.calls, [("42", "1")])
        self.assertEqual(self.monitor.status()["changed_count"], 0)

    async def test_unknown_source_time_verified_once_then_baseline_survives_restart(self):
        self.save_book()
        self.host.saved[("42", "1")].pop("source_updated_at")
        await self.check()
        self.monitor = UpdateMonitor(self.directory, self.host, self.catalog, None)
        await self.check(901)
        self.assertEqual(self.host.calls, [("42", "1")])

    async def test_absent_source_time_has_periodic_body_rechecks(self):
        self.save_book(book())
        self.catalog.books["42"]["chapters"][0]["source_updated_at"] = None
        self.host.saved[("42", "1")]["source_updated_at"] = None
        await self.check()
        await self.check(901)
        await self.check(86401)
        self.assertEqual(self.host.calls, [("42", "1"), ("42", "1")])

    async def test_auto_subscription_starts_with_latest_only_then_saves_new_chapters(self):
        self.subscribe(book(count=80))
        await self.check()
        self.assertEqual(self.host.calls, [("42", "80")])
        self.catalog.books["42"]["chapters"].extend([chapter(81), chapter(82)])
        await self.check(61, force=False)
        self.assertEqual(self.host.calls, [("42", "80"), ("42", "81"), ("42", "82")])
        self.assertEqual(self.monitor.status()["changed_count"], 3)

    async def test_initial_locked_latest_stays_pending_until_unlock_even_if_subscription_sync_fails(self):
        value = book(count=3)
        value["chapters"][-1]["locked"] = True
        self.subscribe(value)
        await self.check()
        self.assertEqual(self.host.calls, [("42", "3"), ("42", "2")])
        self.assertEqual(self.monitor.data["books"]["42"]["pending"], ["3"])
        self.assertIn(("42", "2"), self.host.saved)
        self.monitor = UpdateMonitor(self.directory, self.host, self.catalog, None)
        self.host.subscribed = {"books": [], "complete": False}
        self.catalog.books["42"]["chapters"][-1]["locked"] = False
        await self.check(61)
        self.assertEqual(self.host.calls[-1], ("42", "3"))
        self.assertEqual(self.monitor.data["books"]["42"]["pending"], [])
        self.assertIn(("42", "3"), self.host.saved)
        self.assertEqual(self.monitor.status()["subscribed_count"], 1)

    async def test_partial_subscription_list_never_erases_verified_books(self):
        self.subscribe(book())
        await self.check()
        self.host.subscribed = {"books": [], "complete": False}
        await self.check(601)
        self.assertEqual(self.monitor.status()["subscribed_count"], 1)
        self.assertIsNotNone(self.monitor.status()["error"])

    async def test_subscribed_books_have_priority_and_ordinary_books_are_not_polled_every_minute(self):
        self.save_book(book("10"))
        self.subscribe(book("99"))
        await self.check()
        self.assertEqual(self.catalog.calls, ["99", "10"])
        await self.check(61, force=False)
        self.assertEqual(self.catalog.calls, ["99", "10", "99"])

    async def test_pending_new_chapter_survives_failure_restart_and_backoff(self):
        self.subscribe(book())
        await self.check()
        self.catalog.books["42"]["chapters"].append(chapter(2))
        self.host.failures.add(("42", "2"))
        await self.check(61)
        self.monitor = UpdateMonitor(self.directory, self.host, self.catalog, None)
        await self.check(62)
        self.assertEqual(self.host.calls.count(("42", "2")), 1)
        self.assertEqual(self.monitor.data["books"]["42"]["pending"], ["2"])
        self.host.failures.clear()
        await self.check(122)
        self.assertEqual(self.host.calls.count(("42", "2")), 2)
        self.assertEqual(self.monitor.data["books"]["42"]["pending"], [])

    async def test_locked_modified_chapter_preserves_baseline_and_old_body(self):
        self.save_book()
        await self.check()
        self.catalog.books["42"]["chapters"][0].update(locked=True, source_updated_at="2026-10-10 15:00:00", text="不会保存")
        await self.check(901)
        self.assertEqual(self.host.saved[("42", "1")]["text"], "正文一。")
        self.assertEqual(self.monitor.data["books"]["42"]["chapters"]["1"]["hash"], text_hash("正文一。"))
        self.assertEqual(self.monitor.status()["changed_count"], 0)

    async def test_empty_directory_does_not_replace_discovery_baseline(self):
        self.subscribe(book(count=2))
        await self.check()
        self.catalog.books["42"]["chapters"] = []
        await self.check(61)
        self.assertEqual(self.monitor.data["books"]["42"]["known_ids"], ["1", "2"])
        self.assertEqual(self.host.calls, [("42", "2")])

    async def test_busy_pauses_all_work_and_disabled_manual_check_does_not_enable_background(self):
        self.save_book()
        self.host.busy = True
        await self.check()
        self.assertEqual(self.catalog.calls, [])
        self.host.busy = False
        self.monitor.configure(False)
        await self.check(force=False)
        self.assertEqual(self.catalog.calls, [])
        await self.check()
        self.assertEqual(self.catalog.calls, ["42"])
        self.assertFalse(self.monitor.status()["enabled"])

    async def test_turning_off_subscription_stops_acquiring_unsaved_new_chapters(self):
        self.subscribe(book())
        await self.check()
        self.host.scope["42"] = book()
        self.host.subscribed = {"books": [], "complete": True}
        self.catalog.books["42"]["chapters"].append(chapter(2))
        await self.check(601)
        self.assertEqual(self.host.calls, [("42", "1")])
        self.assertFalse(self.monitor.status()["books"][0]["auto_subscribed"])

    async def test_unknown_old_bodies_are_batched_so_priority_books_can_be_checked_again(self):
        self.save_book(book("10", count=12))
        for current in self.host.saved.values():
            current.pop("source_updated_at")
        self.subscribe(book("99"))
        await self.check()
        self.assertEqual(len([key for key in self.host.calls if key[0] == "10"]), 3)
        self.assertEqual(self.monitor.data["books"]["10"]["next_check"], self.now + 5)
        self.catalog.books["99"]["chapters"].append(chapter(2))
        await self.check(61, force=False)
        self.assertEqual(self.host.calls[4], ("99", "2"))
        self.assertEqual(len([key for key in self.host.calls if key[0] == "10"]), 6)

    async def test_idle_tick_does_not_claim_a_new_check_time(self):
        self.save_book()
        await self.check()
        checked = self.monitor.status()["last_checked_at"]
        await self.check(5, force=False)
        self.assertEqual(self.monitor.status()["last_checked_at"], checked)
