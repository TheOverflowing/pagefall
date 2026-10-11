"""Persistent, incremental checks for locally saved and auto-subscribed books."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def signature(chapter):
    return [chapter.get("source_updated_at"), chapter.get("expected_words")]


def timestamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")


class UpdateMonitor:
    SUBSCRIBED_INTERVAL = 60
    SAVED_INTERVAL = 900
    SUBSCRIPTIONS_INTERVAL = 600
    BODY_INTERVAL = 86400
    BODY_BATCH = 3

    def __init__(self, directory, host, catalog, subscriptions):
        self.path = Path(directory) / "updates.json"
        self.host, self.catalog, self.subscriptions = host, catalog, subscriptions
        self.data = {"enabled": True, "books": {}, "subscriptions": {}, "subscriptions_checked": 0, "last_checked_at": None, "revision": 0}
        try:
            saved = json.loads(self.path.read_text())
            if isinstance(saved, dict) and isinstance(saved.get("books"), dict):
                self.data.update(saved)
        except (OSError, ValueError):
            pass
        self.checking = False
        self.task = None
        self.wake = asyncio.Event()
        self.force = False
        self.stopping = False

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def start(self):
        if self.task is None or self.task.done():
            self.stopping = False
            self.task = asyncio.create_task(self.run())

    async def close(self):
        self.stopping = True
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def configure(self, enabled):
        self.data["enabled"] = enabled
        self.save()
        self.wake.set()

    def request_check(self):
        self.force = True
        self.wake.set()

    def status(self):
        books = []
        for identifier, book in self.data["books"].items():
            if not book.get("active", True):
                continue
            books.append({"id": identifier, "title": book.get("title", identifier),
                          "auto_subscribed": identifier in self.data["subscriptions"],
                          "last_checked_at": book.get("last_checked_at"),
                          "changed_count": sum(bool(c.get("update_status")) for c in book.get("chapters", {}).values()),
                          "error": book.get("error")})
        books.sort(key=lambda book: (not book["auto_subscribed"], book["title"]))
        return {"enabled": self.data["enabled"], "checking": self.checking,
                "last_checked_at": self.data.get("last_checked_at"), "error": self.data.get("error"),
                "subscribed_count": len(self.data["subscriptions"]),
                "changed_count": sum(book["changed_count"] for book in books), "books": books, "revision": self.data["revision"]}

    def decorate(self, book):
        status = self.data["books"].get(book["id"], {})
        for chapter in book.get("chapters", []):
            marker = status.get("chapters", {}).get(chapter["id"], {})
            for key in ("update_status", "update_detected_at"):
                if marker.get(key):
                    chapter[key] = marker[key]
                else:
                    chapter.pop(key, None)
        book["auto_subscribed"] = book["id"] in self.data["subscriptions"]
        return book

    def mark_read(self, book_id, chapter_id):
        chapter = self.data["books"].get(book_id, {}).get("chapters", {}).get(chapter_id)
        if chapter and chapter.get("update_status"):
            chapter.pop("update_status", None)
            chapter.pop("update_detected_at", None)
            self.data["revision"] += 1
            self.save()

    async def run(self):
        while not self.stopping:
            if (self.data["enabled"] or self.force) and not self.host.monitor_busy():
                force, self.force = self.force, False
                try:
                    await self.check_once(force=force)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.data["error"] = "检查更新失败，请稍后重试。"
                    self.save()
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def check_once(self, force=False, now=None):
        if self.checking or (not self.data["enabled"] and not force) or self.host.monitor_busy():
            return
        now = time.time() if now is None else now
        self.checking = True
        checked = False
        started = time.monotonic()
        try:
            if force or now - self.data.get("subscriptions_checked", 0) >= self.SUBSCRIPTIONS_INTERVAL:
                try:
                    value = await self.host.monitor_subscriptions(self.subscriptions)
                    if value.get("complete") is True:
                        self.data["subscriptions"] = {book["id"]: book for book in value.get("books", []) if book.get("auto_subscribed") is True}
                        self.data.pop("error", None)
                    else:
                        self.data["error"] = "自动订阅列表未同步完成，请检查晋江账号连接。"
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.data["error"] = "自动订阅列表暂时无法同步，请检查晋江账号连接。"
                self.data["subscriptions_checked"] = now
                checked = True
                self.save()
            entries = {book["id"]: book for book in self.host.monitor_books()}
            entries.update(self.data["subscriptions"])
            for identifier, state in self.data["books"].items():
                state["active"] = identifier in entries
            for book in entries.values():
                state = self.data["books"].setdefault(book["id"], {"chapters": {}, "known_ids": [], "pending": []})
                state.update(title=book.get("title", book["id"]), url=book.get("url"), active=True)
            ordered = sorted(entries.values(), key=lambda book: (book["id"] not in self.data["subscriptions"], self.data["books"].get(book["id"], {}).get("next_check", 0)))
            for book in ordered:
                if self.host.monitor_busy() or (not self.data["enabled"] and not force):
                    break
                state = self.data["books"][book["id"]]
                if force or state.get("next_check", 0) <= now:
                    await self.check_book(book, state, now, force=force)
                    checked = True
                    if time.monotonic() - started >= 30:
                        break
            if checked:
                self.data["last_checked_at"] = timestamp(now)
            self.save()
        finally:
            self.checking = False

    async def check_book(self, book, state, now, force=False):
        subscribed = book["id"] in self.data["subscriptions"]
        interval = self.SUBSCRIBED_INTERVAL if subscribed else self.SAVED_INTERVAL
        try:
            remote = await self.catalog.watch_fetch(book)
            if remote.get("id") != book["id"] or not remote.get("chapters"):
                raise ValueError("Empty or mismatched chapter directory")
            chapters = {chapter["id"]: chapter for chapter in remote["chapters"]}
            old_ids = set(state["known_ids"])
            pending = list(state["pending"])
            if subscribed:
                if not state.get("subscription_started"):
                    pending.append(remote["chapters"][-1]["id"])
                    latest = next((chapter for chapter in reversed(remote["chapters"]) if not chapter.get("locked")), remote["chapters"][-1])
                    if not self.host.monitor_snapshot(book["id"], latest["id"]):
                        pending.append(latest["id"])
                    state["subscription_started"] = True
                else:
                    pending.extend(identifier for identifier in chapters if identifier not in old_ids)
            else:
                state.pop("subscription_started", None)
                pending = []
            state["known_ids"] = list(dict.fromkeys([*state["known_ids"], *chapters]))
            state["pending"] = list(dict.fromkeys(pending))
            # Persist discovery before fetching bodies so interruption cannot lose a new chapter.
            self.save()
            saved = self.host.monitor_chapters(book["id"])
            order = [*state["pending"], *(identifier for identifier in saved if identifier not in state["pending"])]
            failures = False
            deferred = False
            body_count = 0
            for identifier in order:
                if self.host.monitor_busy() or (not self.data["enabled"] and not force):
                    state["next_check"] = now
                    self.save()
                    return
                chapter = chapters.get(identifier)
                if not chapter:
                    continue
                baseline = state["chapters"].setdefault(identifier, {})
                snapshot = self.host.monitor_snapshot(book["id"], identifier) if "hash" not in baseline or identifier in state["pending"] else None
                if snapshot and "hash" not in baseline:
                    baseline["hash"] = text_hash(snapshot.get("raw_text") or snapshot["text"])
                    source_time = snapshot.get("text_source_updated_at", snapshot.get("source_updated_at"))
                    if source_time:
                        baseline["signature"] = [source_time, snapshot.get("expected_words")]
                        baseline["last_body_checked"] = now
                changed_directory = baseline.get("signature") != signature(chapter)
                body_due = not chapter.get("source_updated_at") and now - baseline.get("last_body_checked", 0) >= self.BODY_INTERVAL
                is_new = identifier in state["pending"] and snapshot is None
                if not is_new and not changed_directory and not body_due:
                    if identifier in state["pending"]:
                        state["pending"].remove(identifier)
                    continue
                if not force_due(baseline, now):
                    failures = True
                    continue
                if body_count >= self.BODY_BATCH:
                    deferred = True
                    break
                try:
                    body_count += 1
                    result = await self.host.refresh_saved_chapter(remote, identifier, previous_source_hash=baseline.get("hash"), save_new_book=subscribed)
                    latest_hash = text_hash(result["text"])
                    if is_new or (baseline.get("hash") and baseline["hash"] != latest_hash):
                        baseline.update(update_status="new" if is_new else "changed", update_detected_at=timestamp(now))
                        self.data["revision"] += 1
                    baseline.update(hash=latest_hash, signature=signature(chapter), last_body_checked=now)
                    baseline.pop("retry_at", None)
                    baseline.pop("failures", None)
                    if identifier in state["pending"]:
                        state["pending"].remove(identifier)
                    self.save()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures = True
                    baseline["failures"] = baseline.get("failures", 0) + 1
                    baseline["retry_at"] = now + min(interval * (2 ** min(baseline["failures"] - 1, 5)), 1800)
            state.update(last_checked_at=timestamp(now), next_check=now + (5 if deferred else interval))
            state["error"] = "部分章节暂时无法更新，将继续重试。" if failures else None
            state.pop("failures", None)
        except asyncio.CancelledError:
            raise
        except Exception:
            state["failures"] = state.get("failures", 0) + 1
            state.update(error="目录暂时无法读取，将稍后重试。", next_check=now + min(interval * (2 ** min(state["failures"] - 1, 5)), 3600))
        self.save()


def force_due(baseline, now):
    return baseline.get("retry_at", 0) <= now
