import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .browser import Reader, CaptureError, normalize_url, ROOT
from .exporters import export_book, summarize, proofread_current
from .session import normalize_cookies, save_cookies_sync
from .metadata import CoverCache, CoverRetryableError
from .platforms import book_identity, platforms, platform_for
from .translation_store import TranslationStore
from .chapter_versions import ChapterVersions
from .update_monitor import UpdateMonitor, text_hash

DATA = ROOT / ".local"
BOOKS = DATA / "books"
EXPORTS = ROOT / "exports"
for directory in (DATA, BOOKS, EXPORTS):
    directory.mkdir(exist_ok=True, parents=True, mode=0o700)
os.chmod(DATA, 0o700)
cover_cache = CoverCache(DATA / "covers")
from .catalog import Catalog, public_app_basic_metadata, public_author_publications, PublicMetadataInterrupted, metadata_classification, CLASSIFICATION_VERSION
catalog = Catalog(DATA / "catalogs")
from .jinjiang_auth import Connection
jinjiang_connection = Connection(DATA / "jinjiang-device.json")


def translation_store():
    return TranslationStore(DATA / "translations")


def chapter_versions():
    return ChapterVersions(DATA / "chapter-versions")


def archive_chapter(book_id, chapter, kind="existing"):
    if chapter.get("text", "").strip() and chapter.get("state") in {"done", "review", "reviewed"} and not chapter.get("incomplete"):
        if kind == "source":
            chapter["text_source_updated_at"] = chapter.get("source_updated_at")
        # A later directory refresh may change source_updated_at without reading the body.
        snapshot = {**chapter, "source_updated_at": chapter.get("text_source_updated_at")}
        return chapter_versions().record(book_id, snapshot, kind=kind)


def cover_url(identifier):
    if re.fullmatch(r"\d{1,12}", str(identifier)):
        return "http://127.0.0.1:8765/api/cover?book_id=" + str(identifier)
    return None


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise HTTPException(400, "作品或章节 ID 无效。")
    return value


def write_json(path, value):
    path.parent.mkdir(exist_ok=True, parents=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def public_book(book):
    fields = {"id", "title", "author", "url", "updated_at", "toc_chapter_count", "demo", "platform", "platform_name", "details_stale"}
    chapter_fields = {"id", "title", "url", "state", "characters", "method", "error", "review", "incomplete", "absent_from_toc", "vip", "locked", "expected_words", "ordinal", "proofreading", "summary", "source_updated_at"}
    result = {key: value for key, value in book.items() if key in fields}
    result["chapters"] = [{key: value for key, value in chapter.items() if key in chapter_fields} for chapter in book.get("chapters", [])]
    # Polling state stays compact; detailed issue/geometry evidence is fetched
    # only when the reader opens an individual chapter.
    for source, chapter in zip(book.get("chapters", []), result["chapters"]):
        if proofread_current(source):
            chapter["proofreading"] = {key: value for key, value in source["proofreading"].items() if key in {"version", "status", "summary", "auto_processed", "human_reviewed", "issues_count", "changes_count"}}
        else:
            chapter.pop("proofreading", None)
    progress = summarize(book)
    result["progress"] = {**progress, "done": progress["captured"]}
    result["cover"] = cover_url(book.get("id"))
    cached = catalog.cached(book['id']) or {}
    result["metadata"] = book.get("metadata") or cached.get("metadata")
    result["classification"] = {**(metadata_classification(result["metadata"]) or {}), **workbench.library_classifications.get(book['id'], {})}
    chapter_metadata = {c['id']:c for c in cached.get('chapters',[])}
    for chapter in result['chapters']:
        metadata=chapter_metadata.get(chapter['id'],{})
        if metadata.get('summary'):chapter['summary']=metadata['summary']
        if 'source_updated_at' not in chapter and 'source_updated_at' in metadata:chapter['source_updated_at']=metadata['source_updated_at']
    result["proofreading"] = proofread_summary(book)
    translations = translation_store()
    index = translations.load(book["id"])
    result["translation"] = translations.summary(book)
    if index:
        dependencies = translations.source_dependencies(book)
        for source, chapter in zip(book.get("chapters", []), result["chapters"]):
            chapter["translation"] = translations.chapter(book, source, index=index, dependencies=dependencies)
    if getattr(workbench, "updates", None):
        workbench.updates.decorate(result)
    return result


def proofread_summary(book):
    chapters = [c for c in book.get("chapters", []) if proofread_current(c)]
    return {"can_undo": any(c.get("proofread_undo") for c in chapters), "done": len(chapters),
            "total": sum(c.get("state") in {"done", "review"} and bool(c.get("text")) for c in book.get("chapters", [])),
            "changed": sum(bool(c["proofreading"].get("changes_count")) for c in chapters),
            "issues": sum(c["proofreading"].get("issues_count", 0) for c in chapters),
            "completed_at": book.get("proofread_completed_at")}


def apply_proofreading(chapter):
    from .proofreading import proofread_chapter
    if chapter.get("state") not in {"done", "review"} or not chapter.get("text", "").strip() or chapter.get("incomplete") or proofread_current(chapter):
        return False
    result = proofread_chapter(chapter)
    fields = ("text", "state", "review", "error", "characters", "proofreading")
    chapter["proofread_undo"] = {"values": {key: copy.deepcopy(chapter[key]) for key in fields if key in chapter}, "missing": [key for key in fields if key not in chapter]}
    chapter["text"] = result["text"]
    chapter["proofreading"] = result["proofreading"]
    chapter["characters"] = len(re.sub(r"\s", "", chapter["text"]))
    return True


def empty_library():
    return {"account": {"state": "unknown", "nickname": "", "verified_at": None}, "books": [], "expected_count": None, "complete": False, "message": "连接晋江账号后，可同步收藏书架。"}


def clean_library(value):
    """Only account display metadata and canonical book references may leave Reader."""
    value = value if isinstance(value, dict) else {}
    account = value.get("account") if isinstance(value.get("account"), dict) else {}
    state = account.get("state", "unknown")
    if state not in {"unknown", "connected", "expired", "error"}:
        state = "unknown"
    message = str(value.get("message") or "")
    if message in {"阅读窗口已关闭。重新打开拾页后可继续。", "页面加载超时，进度已保存。请检查阅读窗口和网络后继续。"}:
        message = "收藏刷新失败，请重试。"
    books = {}
    for item in value.get("books", []) if isinstance(value.get("books"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            url = normalize_url(item.get("url") or str(item.get("id", "")))
            identifier = parse_qs(urlparse(url).query)["novelid"][0]
        except (ValueError, TypeError, AttributeError, KeyError):
            continue
        books[identifier] = {"id": identifier, "title": str(item.get("title") or f"作品 {identifier}"), "author": str(item.get("author") or ""), "url": url, "cover": cover_url(identifier)}
    expected = value.get("expected_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        expected = None
    return {
        "account": {"state": state, "nickname": str(account.get("nickname") or ""), "verified_at": account.get("verified_at") if isinstance(account.get("verified_at"), str) else None,
                    "message": message if state in {"error", "expired"} else None},
        "books": list(books.values()), "expected_count": expected,
        "complete": value.get("complete") is True and state in {"connected", "error"} and (expected is None or len(books) >= expected),
        "message": message,
    }


class Workbench:
    def __init__(self):
        translation_store().recover_interrupted()
        self.reader = Reader(DATA / "browser")
        self.book = None
        self.phase, self.message = "idle", "从书架选择小说，或添加作品链接。"
        self.task = None
        self.chapter_refresh_id = None
        self.library_task = None
        self.tags_task = None
        self.account_task = None
        self.account_waiting = False
        self.session_importing = False
        self.replace_library_on_sync = False
        self.closing = False
        self.pause = False
        self.queue = []
        self.queue_paused = False
        self.proofreading = {"book_id": None, "running": False, "done": 0, "total": 0, "changed": 0, "issues": 0, "message": ""}
        self.translation = {"book_id": None, "running": False, "done": 0, "total": 0, "failed": 0, "message": "", "source_language": "auto", "target_language": "zh-Hans"}
        self.active_download = None
        self.library_cache = empty_library()
        try:
            self.library_cache = clean_library(json.loads((DATA / "library.json").read_text()))
        except (OSError, ValueError, TypeError):
            pass
        self.library_tags = {}
        try:
            saved_tags = json.loads((DATA / "library-tags.json").read_text())
            self.library_tags = {str(key): [str(tag) for tag in value][:50] for key, value in saved_tags.items() if isinstance(value, list)}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.library_classifications = {}
        self.classification_checked = set()
        self.classification_basic = set()
        self.classification_authors = {}
        try:
            saved = json.loads((DATA / "library-classifications.json").read_text())
            self.library_classifications = {str(key): metadata_classification({"classification": value}) for key, value in saved.items() if isinstance(value, dict)}
            self.classification_checked = {str(key) for key, value in saved.items() if isinstance(value, dict) and value.get("_checked_version") == CLASSIFICATION_VERSION}
            self.classification_basic = self.classification_checked | {str(key) for key, value in saved.items() if isinstance(value, dict) and value.get("_basic_checked") is True}
            self.classification_authors = {str(key): value["_author_id"] for key, value in saved.items() if isinstance(value, dict) and isinstance(value.get("_author_id"), str) and re.fullmatch(r"\d{1,12}", value["_author_id"])}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        try:
            saved_queue = json.loads((DATA / "queue.json").read_text())
            seen = set()
            for item in saved_queue.get("items", []):
                entry = self.download_entry(item.get("url", ""), item.get("mode", "auto"), item.get("delay", 3))
                if entry["id"] not in seen:
                    self.queue.append(entry)
                    seen.add(entry["id"])
            self.queue_paused = bool(saved_queue.get("paused"))
        except (OSError, ValueError, TypeError, AttributeError, HTTPException):
            pass
        try:
            last = json.loads((DATA / "current.json").read_text())["id"]
            if re.fullmatch(r"[\w-]+", last):
                self.book = json.loads((BOOKS / last / "book.json").read_text())
                for chapter in self.book["chapters"]:
                    if chapter["state"] == "capturing":
                        chapter["state"] = "pending"
                self.message = "已恢复上次进度，可继续处理未完成章节。"
        except (OSError, ValueError, KeyError):
            pass
        self.monitor_operation = False
        self.monitor_saved = {}
        self.updates = UpdateMonitor(DATA, self, catalog, read_auto_subscriptions)

    @property
    def busy(self):
        return self.monitor_operation or self.chapter_refresh_id is not None or (self.task is not None and not self.task.done())

    @property
    def account_busy(self):
        return jinjiang_connection.busy or self.session_importing or (self.account_task is not None and not self.account_task.done())

    @property
    def library_syncing(self):
        return (self.library_task is not None and not self.library_task.done()) or self.account_busy

    def monitor_busy(self):
        return self.closing or self.busy or self.account_busy or self.account_waiting or self.library_syncing or self.proofreading["running"] or self.translation["running"]

    async def monitor_subscriptions(self, operation):
        self.monitor_operation = True
        try:
            return await operation(self.reader)
        finally:
            self.monitor_operation = False
            self.kick_queue()

    def monitor_books(self):
        self.monitor_saved = {}
        entries = {}
        for path in BOOKS.glob("*/book.json"):
            try:
                book = self.get_book(path.parent.name)
                if book.get("url") and any(readable_chapter(chapter) for chapter in book.get("chapters", [])):
                    entries[book["id"]] = book
                    self.monitor_saved[book["id"]] = book
            except (HTTPException, KeyError, TypeError):
                continue
        for folder in (DATA / "reading").glob("*"):
            if folder.is_dir() and folder.name not in entries and any(folder.glob("*.json")):
                cached = catalog.cached(folder.name)
                if cached and cached.get("url"):
                    entries[folder.name] = cached
        return list(entries.values())

    def monitor_chapters(self, identifier):
        book = self.monitor_saved.get(identifier)
        saved = {chapter["id"] for chapter in (book or {}).get("chapters", []) if readable_chapter(chapter)}
        return saved | {path.stem for path in (DATA / "reading" / safe_id(identifier)).glob("*.json")}

    def monitor_snapshot(self, identifier, chapter_id):
        reading = catalog.cached_read(identifier, chapter_id)
        book = self.monitor_saved.get(identifier)
        saved = next((chapter for chapter in (book or {}).get("chapters", []) if chapter["id"] == chapter_id and readable_chapter(chapter)), None)
        # A later full download can be newer than the online-reading copy.
        saved_time = (saved or {}).get("text_source_updated_at")
        reading_time = (reading or {}).get("source_updated_at")
        if saved and saved_time and (not reading_time or saved_time > reading_time):
            return saved
        return reading or saved

    async def refresh_saved_chapter(self, source, chapter_id, previous_source_hash=None, save_new_book=False):
        identifier = safe_id(source["id"])
        try:
            saved = self.get_book(identifier)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            saved = None
        current = next((chapter for chapter in (saved or {}).get("chapters", []) if chapter["id"] == chapter_id), None)
        if current:
            archive_chapter(identifier, current)
        self.chapter_refresh_id = identifier
        try:
            options = {"previous_source_hash": previous_source_hash} if previous_source_hash is not None else {}
            result = await catalog.read(source, chapter_id, refresh=True, **options)
            if saved is None and save_new_book:
                saved = {**source, "author": source.get("author") or "", "chapters": []}
                for source_chapter in source["chapters"]:
                    item = {key: value for key, value in source_chapter.items() if key not in {"text", "raw_text", "proofreading", "proofread_undo", "screenshots", "lines", "characters"}}
                    item["state"] = "blocked" if item.get("locked") else "pending"
                    saved["chapters"].append(item)
                current = next(chapter for chapter in saved["chapters"] if chapter["id"] == chapter_id)
                self.monitor_saved[identifier] = saved
            if saved:
                if previous_source_hash is None and current and readable_chapter(current) and current.get("text") == result["text"]:
                    return result
                unchanged_source = previous_source_hash is not None and text_hash(result["text"]) == previous_source_hash
                if current is None:
                    current = dict(next(chapter for chapter in source["chapters"] if chapter["id"] == chapter_id))
                    saved["chapters"].append(current)
                if not unchanged_source or not readable_chapter(current):
                    changed = current.get("text") != result["text"]
                    if changed or not readable_chapter(current):
                        needs_review = bool(result.get("review")) or result.get("method") == "ocr"
                        for key in ("proofreading", "proofread_undo", "lines", "screenshots"):
                            current.pop(key, None)
                        current.update({key: value for key, value in result.items() if key in {"title", "url", "text", "method", "source_updated_at"}})
                        current.update(raw_text=result["text"], text_source_updated_at=result.get("source_updated_at"), state="review" if needs_review else "done", review=needs_review, incomplete=False, error="", characters=len(re.sub(r"\s", "", result["text"])))
                        saved.pop("proofread_completed_at", None)
                current["source_updated_at"] = result.get("source_updated_at")
                source_chapter = next(chapter for chapter in source["chapters"] if chapter["id"] == chapter_id)
                for key in ("locked", "vip", "expected_words", "summary", "ordinal", "url", "title"):
                    if key in source_chapter:
                        current[key] = source_chapter[key]
                saved["toc_chapter_count"] = len(source["chapters"])
                self.save_book(saved)
            return result
        finally:
            self.chapter_refresh_id = None
            self.kick_queue()

    def save_book(self, book, current=False):
        identifier = safe_id(book["id"])
        book["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(BOOKS / identifier / "book.json", book)
        if current:
            write_json(DATA / "current.json", {"id": identifier})

    def save(self):
        if self.book is None:
            return
        self.save_book(self.book, current=True)

    def get_book(self, identifier=None):
        if identifier is None:
            if self.book is None:
                raise HTTPException(400, "请先读取目录。")
            return self.book
        safe_id(identifier)
        if self.book and self.book["id"] == identifier:
            return self.book
        try:
            book = json.loads((BOOKS / identifier / "book.json").read_text())
            if book["id"] != identifier or not isinstance(book["chapters"], list):
                raise ValueError()
            return book
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, "本地尚未保存这本作品。")

    def downloads(self):
        result = []
        identifiers = {path.parent.name for path in BOOKS.glob("*/book.json")}
        if self.book:
            identifiers.add(self.book["id"])
        for identifier in identifiers:
            try:
                book = self.get_book(identifier)
                progress = summarize(book)
                active_id = self.active_download["id"] if self.active_download else self.book["id"] if self.book else None
                active = bool(self.busy and active_id == identifier)
                result.append({"id": identifier, "title": book.get("title", ""), "author": book.get("author", ""), "url": book.get("url", ""), "updated_at": book.get("updated_at", ""), "chapters": [], "progress": {**progress, "done": progress["captured"]}, "active": active, "phase": self.phase if active else "saved", "platform": book.get("platform", "jjwxc"), "platform_name": book.get("platform_name", "晋江文学城"), "cover": cover_url(identifier), "proofreading": proofread_summary(book), "translation": translation_store().summary(book)})
            except (HTTPException, TypeError, KeyError):
                continue
        return sorted(result, key=lambda book: book.get("updated_at", ""), reverse=True)

    def state(self):
        public = None
        progress = {"done": 0, "total": 0}
        if self.book:
            public = public_book(self.book)
            progress = public["progress"]
        return {"busy": self.busy or self.account_busy, "browser_open": self.reader.opened, "phase": self.phase, "message": self.message, "book": public, "progress": progress, "account": self.library_cache["account"], "account_waiting": self.account_waiting, "library_syncing": self.library_syncing, "background": bool(getattr(self.reader, "headless", False)), "downloads": self.downloads(), "queue": self.queue, "queue_paused": self.queue_paused, "proofreading": self.proofreading, "translation": self.translation, "updates": self.updates.status()}

    def launch(self, operation, phase, queue_entry=None):
        if self.busy or self.account_busy or self.account_waiting:
            operation.close()
            raise HTTPException(409, "正在处理，请先暂停或等待当前操作完成。")
        self.phase = phase
        async def run():
            try:
                await operation
            except asyncio.CancelledError:
                self.queue_paused = True
                self.phase, self.message = "paused", "任务已停止，已保存的进度仍然保留。"
                self.save()
            except Exception as exc:
                self.queue_paused = True
                self.phase = "error"
                self.message = human_error(exc)
                self.save()
            finally:
                if self.phase in {"paused", "error"} or self.pause:
                    self.queue_paused = True
                if queue_entry:
                    if self.phase not in {"paused", "error"} and not self.pause:
                        self.queue = [entry for entry in self.queue if entry["id"] != queue_entry["id"]]
                    else:
                        self.queue_paused = True
                    self.active_download = None
                self.save_queue()
        self.task = asyncio.create_task(run())
        self.task.add_done_callback(lambda _: self.kick_queue())

    def save_queue(self):
        write_json(DATA / "queue.json", {"items": self.queue, "paused": self.queue_paused})

    @staticmethod
    def download_entry(url, mode="auto", delay=3):
        url = normalize_url(url)
        if mode not in {"auto", "ocr"}:
            raise ValueError("识别模式无效")
        try:
            delay = min(30, max(3, float(delay)))
        except (ValueError, TypeError):
            raise ValueError("章节间隔无效")
        if platform_for(url).id == "gongzicp":
            delay = max(delay, 10)
        return {"id": book_identity(url), "url": url, "mode": mode, "delay": delay}

    def enqueue(self, url, mode="auto", delay=3):
        entry = self.download_entry(url, mode, delay)
        if self.busy and self.book and self.book["id"] == entry["id"] and self.phase == "capturing":
            return False
        if any(item["id"] == entry["id"] for item in self.queue):
            return False
        self.queue.append(entry)
        self.save_queue()
        self.kick_queue()
        return True

    def kick_queue(self):
        if self.closing or self.busy or self.account_busy or self.account_waiting or self.queue_paused or not self.queue:
            return
        entry = self.queue[0]
        self.active_download = entry
        self.launch(self.download(entry), "scanning", queue_entry=entry)

    async def download(self, entry):
        await self.scan(entry["url"])
        if self.pause or self.queue_paused:
            self.phase = "paused"
            return
        self.phase = "capturing"
        await self.capture(entry["mode"], entry["delay"])

    def pause_all(self):
        self.pause = self.queue_paused = True
        self.save_queue()
        self.message = "将在当前章节保存后暂停…" if self.busy else "队列已暂停。"

    def resume_queue(self):
        self.pause = self.queue_paused = False
        self.save_queue()
        self.kick_queue()

    def resume_current(self, mode, delay):
        if self.busy or self.account_busy or self.account_waiting:
            raise HTTPException(409, "正在处理，请稍后继续。")
        self.get_book()
        self.pause = self.queue_paused = False
        self.queue = [entry for entry in self.queue if entry["id"] != self.book["id"]]
        entry = self.download_entry(self.book["url"], mode, delay)
        self.queue.insert(0, entry)
        self.active_download = entry
        self.save_queue()
        self.launch(self.capture(entry["mode"], entry["delay"]), "capturing", queue_entry=entry)

    def library_state(self):
        books = [{**book, "tags": self.library_tags.get(book["id"], []), "classification": self.library_classifications.get(book["id"], {})} for book in self.library_cache["books"]]
        return {**self.library_cache, "books": books, "syncing": self.library_syncing,
                "classification_loading": self.tags_task is not None and not self.tags_task.done(),
                "classification_count": sum(book["id"] in self.classification_checked for book in books),
                "classification_basic_count": sum(book["id"] in self.classification_basic for book in books)}

    def remember_tags(self, identifier, tags):
        if isinstance(tags, list):
            self.library_tags[identifier] = [str(tag) for tag in tags][:50]
            write_json(DATA / "library-tags.json", self.library_tags)

    def remember_metadata(self, identifier, metadata):
        self.remember_tags(identifier, metadata.get("tags"))
        classification = metadata_classification(metadata)
        if classification is not None:
            self.library_classifications[identifier] = {**self.library_classifications.get(identifier, {}), **classification}
            if metadata.get("classification_version") == CLASSIFICATION_VERSION:
                self.classification_checked.add(identifier)
                self.classification_basic.add(identifier)
            elif metadata.get("basic_checked") is True:
                self.classification_basic.add(identifier)
            if isinstance(metadata.get("author_id"), str) and re.fullmatch(r"\d{1,12}", metadata["author_id"]):
                self.classification_authors[identifier] = metadata["author_id"]
            self.save_classifications()

    def save_classifications(self):
        saved = {}
        for identifier, classification in self.library_classifications.items():
            entry = dict(classification)
            if identifier in self.classification_checked:entry["_checked_version"] = CLASSIFICATION_VERSION
            if identifier in self.classification_basic:entry["_basic_checked"] = True
            if identifier in self.classification_authors:entry["_author_id"] = self.classification_authors[identifier]
            saved[identifier] = entry
        write_json(DATA / "library-classifications.json", saved)

    def launch_tag_lookup(self):
        if self.closing or (self.tags_task is not None and not self.tags_task.done()):
            return
        if all(book["id"] in self.library_tags and book["id"] in self.classification_checked for book in self.library_cache["books"]):
            return
        self.tags_task = asyncio.create_task(self.lookup_tags())

    async def lookup_tags(self):
        """Add shelf facets once per book, reusing details already on disk."""
        pending = []
        for book in self.library_cache["books"]:
            identifier = book["id"]
            if identifier in self.classification_checked and identifier in self.library_tags:
                continue
            metadata = ((catalog.cached(identifier) or {}).get("metadata") or {})
            classification = metadata_classification(metadata)
            if classification is not None and (identifier not in self.library_classifications or metadata.get("classification_version") == CLASSIFICATION_VERSION):
                self.library_classifications[identifier] = {**self.library_classifications.get(identifier, {}), **classification}
            if metadata.get("classification_version") == CLASSIFICATION_VERSION:
                self.classification_checked.add(identifier)
                self.classification_basic.add(identifier)
            elif metadata.get("basic_checked") is True:
                self.classification_basic.add(identifier)
            if isinstance(metadata.get("author_id"), str) and re.fullmatch(r"\d{1,12}", metadata["author_id"]):
                self.classification_authors[identifier] = metadata["author_id"]
            if identifier not in self.library_tags and isinstance(metadata.get("tags"), list):
                self.library_tags[identifier] = [str(tag) for tag in metadata["tags"]][:50]
            if identifier not in self.library_tags or identifier not in self.classification_checked:
                pending.append(identifier)
        queue = asyncio.Queue()
        for identifier in pending:queue.put_nowait(identifier)
        network_failures = 0
        author_failures = 0
        interrupted = False
        dates_interrupted = False

        async def worker():
            nonlocal network_failures, author_failures, interrupted, dates_interrupted
            while not self.closing and not interrupted and not queue.empty():
                identifier = queue.get_nowait()
                if identifier in self.classification_checked and identifier in self.library_tags:
                    continue
                try:
                    if identifier not in self.classification_basic or identifier not in self.library_tags:
                        extra = await asyncio.to_thread(public_app_basic_metadata, identifier)
                        if not extra:
                            network_failures = 0
                            continue
                        self.remember_metadata(identifier, {**extra, "basic_checked": True})
                        network_failures = 0
                    if identifier in self.classification_checked:
                        continue
                    author = self.classification_authors.get(identifier)
                    if not author:
                        self.classification_checked.add(identifier)
                        self.save_classifications()
                        continue
                    if dates_interrupted:
                        continue
                    try:
                        publications = await asyncio.to_thread(public_author_publications, author, DATA / "author-publications", identifier)
                    except PublicMetadataInterrupted as error:
                        author_failures += 1
                        if error.kind == "rate_limit" or author_failures >= 3:dates_interrupted = True
                        continue
                    author_failures = 0
                    if publications is None:
                        continue
                    for book_id, author_id in self.classification_authors.items():
                        if author_id == author and book_id in self.classification_basic and book_id in publications:
                            if publications[book_id]:self.library_classifications[book_id]["published_at"] = publications[book_id]
                            self.classification_checked.add(book_id)
                    self.save_classifications()
                except PublicMetadataInterrupted as error:
                    network_failures += 1
                    if error.kind == "rate_limit" or network_failures >= 3:
                        interrupted = True

        workers = [asyncio.create_task(worker()) for _ in range(min(3, len(pending)))]
        try:
            await asyncio.gather(*workers)
        finally:
            for task in workers:
                if not task.done():task.cancel()
            if workers:await asyncio.gather(*workers, return_exceptions=True)
            write_json(DATA / "library-tags.json", self.library_tags)
            self.save_classifications()

    async def refresh_library(self):
        try:
            result = clean_library(await self.reader.library())
            if result["account"]["state"] == "connected":
                result["account"]["verified_at"] = datetime.now(timezone.utc).isoformat()
                if not result["account"]["nickname"] and not self.replace_library_on_sync:
                    result["account"]["nickname"] = self.library_cache["account"]["nickname"]
            if result["account"]["state"] != "connected":
                result["books"] = self.library_cache["books"]
                result["account"]["nickname"] = result["account"]["nickname"] or self.library_cache["account"]["nickname"]
                result["account"]["verified_at"] = result["account"]["verified_at"] or self.library_cache["account"]["verified_at"]
            elif not result["complete"] and not self.replace_library_on_sync:
                previous = {book["id"]: book for book in self.library_cache["books"]}
                previous.update({book["id"]: book for book in result["books"]})
                result["books"] = list(previous.values())
            if result["account"]["state"] == "connected":
                self.replace_library_on_sync = False
            self.library_cache = result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = human_error(exc) if isinstance(exc, CaptureError) else "收藏刷新失败，请重试。"
            self.library_cache = {**self.library_cache, "account": {**self.library_cache["account"], "state": "error", "message": message}, "message": message}
        write_json(DATA / "library.json", self.library_cache)
        self.launch_tag_lookup()
        return self.library_state()

    def launch_library_refresh(self):
        if self.library_syncing:
            return
        self.library_task = asyncio.create_task(self.refresh_library())

    async def import_session(self, cookies):
        # The check and guard are synchronous until the first await. Every path
        # that starts reader work observes account_busy, so an import cannot
        # replace an account while a book or favorites page is being read.
        if self.busy or self.account_busy or self.library_syncing or (self.account_waiting and not getattr(self.reader, "headless", True)):
            raise HTTPException(409, "请先暂停下载并等待书架同步或账号连接结束，再连接 Chrome。")
        try:
            clean = normalize_cookies(cookies)
            if not clean:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, "没有可连接的晋江登录状态，请先在 Chrome 登录晋江。") from None
        self.session_importing = True
        try:
            await asyncio.to_thread(save_cookies_sync, clean)
            # Non-secret marker tells an existing Reader to load the new session.
            write_json(DATA / "connection-update.json", {
                "provider": "chrome", "nonce": uuid.uuid4().hex,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            # A partial first sync of a different account must not merge the old
            # account's private favorites into the newly connected bookshelf.
            self.library_cache = empty_library()
            self.library_cache["message"] = "Chrome 连接已保存，正在等待同步书架。"
            self.replace_library_on_sync = True
            write_json(DATA / "library.json", self.library_cache)
        except Exception:
            raise HTTPException(500, "未能保存晋江连接，请稍后重试。") from None
        finally:
            self.session_importing = False
        # Keep the queued downloads held until the imported account is verified.
        # No await separates guard release from launching this account operation.
        self.account_waiting = True
        self.launch_account(finish=True)
        return {"ok": True, "message": "晋江连接已保存，拾页正在验证并同步书架。"}

    def launch_account(self, finish=False, cancel=False):
        if self.busy or self.account_busy or self.library_syncing:
            raise HTTPException(409, "请等待当前采集或书架同步结束，再处理账号连接。")
        async def operation():
            try:
                if cancel:
                    await self.reader.background()
                    self.account_waiting = False
                    self.message = ""
                elif finish:
                    await self.refresh_library()
                    self.message = self.library_cache["message"]
                    if self.library_cache["account"]["state"] == "connected":
                        await self.reader.background()
                        self.account_waiting = False
                        self.message = "账号已连接"
                else:
                    self.account_waiting = True
                    self.replace_library_on_sync = True
                    await self.reader.show_login()
                    self.message = "请在晋江窗口完成登录，然后点击完成连接。"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not finish:
                    self.account_waiting = False
                self.library_cache = {**self.library_cache, "account": {**self.library_cache["account"], "state": "error"}, "complete": False, "message": human_error(exc)}
                write_json(DATA / "library.json", self.library_cache)
                self.message = self.library_cache["message"]
        self.account_task = asyncio.create_task(operation())
        self.account_task.add_done_callback(lambda _: self.kick_queue())

    async def open(self, url):
        self.message = "正在打开独立阅读窗口…"
        await self.reader.open(url)
        self.phase, self.message = "ready", "晋江窗口已打开。完成验证后，可回到拾页继续。"

    async def scan(self, url):
        self.message = "正在读取作品目录…"
        book = await self.reader.scan(url)
        safe_id(book["id"])
        for chapter in book["chapters"]:
            safe_id(chapter["id"])
        previous = {}
        try:
            old = json.loads((BOOKS / book["id"] / "book.json").read_text())
            previous = {chapter["id"]: chapter for chapter in old["chapters"]}
        except (OSError, ValueError, KeyError):
            pass
        book["toc_chapter_count"] = len(book["chapters"])
        for chapter in book["chapters"]:
            if chapter["id"] in previous:
                old = previous[chapter["id"]]
                access_state, access_error = chapter.get("state"), chapter.get("error", "")
                chapter.update({k: v for k, v in old.items() if k not in {"title", "url", "id", "expected_words", "vip", "locked", "absent_from_toc", "order", "ordinal", "book_url", "platform", "rule_id", "paid", "source_updated_at"}})
                if access_state == "blocked" and not (old.get("text", "").strip() and old.get("state") in {"done", "review", "reviewed"}):
                    chapter["state"], chapter["error"] = access_state, access_error
                if chapter["state"] == "capturing":
                    chapter["state"] = "pending"
        fresh_ids = {chapter["id"] for chapter in book["chapters"]}
        for chapter_id, old in previous.items():
            if chapter_id not in fresh_ids:
                preserved = dict(old)
                preserved.update(absent_from_toc=True, review=True, error="本次目录未列出此章，已保留先前内容，请核对目录。")
                if preserved.get("text", "").strip() and preserved.get("state") in {"done", "review", "reviewed"}:
                    preserved["state"] = "review"
                elif preserved.get("state") not in {"blocked", "failed"}:
                    preserved["state"] = "blocked"
                book["chapters"].append(preserved)
        if book.get("platform", "jjwxc") == "jjwxc":
            book["chapters"].sort(key=lambda chapter: int(chapter["id"]))
        for ordinal, chapter in enumerate(book["chapters"], 1):
            chapter["ordinal"] = ordinal
        self.book = book
        self.save()
        self.phase, self.message = "ready", f"已读取 {len(book['chapters'])} 章"

    async def capture(self, mode, delay):
        interrupted = False
        for ordinal, chapter in enumerate(self.book["chapters"], 1):
            if self.pause:
                interrupted = True
                break
            if chapter.get("text") and chapter["state"] in {"done", "review", "reviewed"}:
                continue
            if chapter.get("locked"):
                chapter["state"], chapter["error"] = "blocked", chapter.get("error") or "目录标注本章已锁定，当前无法阅读。"
                self.save()
                continue
            chapter["state"], chapter["error"] = "capturing", ""
            self.message = f"正在处理第 {ordinal} 章 · {chapter['title']}"
            self.save()
            try:
                folder = BOOKS / self.book["id"] / "sources" / chapter["id"]
                result = await self.reader.capture(chapter, folder, mode)
                chapter.update(result)
                chapter["characters"] = len(re.sub(r"\s", "", chapter["text"]))
                chapter["state"] = "failed" if chapter.get("incomplete") else "review" if chapter["review"] else "done"
                archive_chapter(self.book["id"], chapter, "source")
                if apply_proofreading(chapter):
                    archive_chapter(self.book["id"], chapter, "proofread")
            except CaptureError as exc:
                chapter["state"], chapter["error"] = exc.kind, str(exc)
                if exc.pause:
                    self.message = str(exc)
                    interrupted = True
                    break
            except Exception as exc:
                chapter["state"], chapter["error"] = "failed", human_error(exc)
                # A transport error is not permission to hammer the remaining book.
                self.message = chapter["error"]
                interrupted = True
                break
            finally:
                self.save()
            try:
                await asyncio.wait_for(self._wait_pause(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        result = summarize(self.book)
        self.phase = "paused" if interrupted or self.pause else "finished"
        if self.pause:
            self.message = "已暂停并保存进度。"
        elif not interrupted:
            self.message = f"本轮处理结束：已保存 {result['captured']}/{result['total']} 章，缺失 {result['missing']} 章，待校对 {result['review']} 章。"
        self.save()

    async def _wait_pause(self):
        while not self.pause:
            await asyncio.sleep(0.2)

    async def proofread_book(self, book):
        eligible = [chapter for chapter in book["chapters"] if chapter.get("state") in {"done", "review"} and chapter.get("text", "").strip() and not chapter.get("incomplete")]
        self.proofreading = {"book_id": book["id"], "running": True, "done": 0, "total": len(eligible), "changed": 0, "issues": 0, "message": "正在本机自动校对…"}
        self.pause = False
        try:
            for chapter in eligible:
                if self.pause:
                    break
                archive_chapter(book["id"], chapter)
                if apply_proofreading(chapter):
                    archive_chapter(book["id"], chapter, "proofread")
                report = chapter.get("proofreading") or {}
                self.proofreading["done"] += 1
                self.proofreading["changed"] += bool(report.get("changes_count"))
                self.proofreading["issues"] += report.get("issues_count", 0)
                self.message = f"正在自动校对 {self.proofreading['done']}/{len(eligible)} 章"
                self.proofreading["message"] = self.message
                if self.proofreading["done"] % 10 == 0:
                    self.save_book(book, current=book is self.book)
                await asyncio.sleep(0.01)
            self.phase = "paused" if self.pause else "finished"
            if not self.pause:
                book["proofread_completed_at"] = datetime.now(timezone.utc).isoformat()
            self.message = "自动校对已暂停，已保存处理结果。" if self.pause else f"自动校对完成：{self.proofreading['done']} 章，{self.proofreading['issues']} 处需注意。"
        finally:
            self.proofreading["running"] = False
            self.proofreading["message"] = self.message
            self.save_book(book, current=book is self.book)


def human_error(exc):
    if isinstance(exc, (ValueError, CaptureError)):
        return str(exc)
    text = str(exc)
    if "ERR_CERT" in text:
        return "浏览器未能验证网站证书，请检查网络或代理证书后重试。"
    if "Timeout" in type(exc).__name__ or "Timeout" in text:
        return "请求超时，请稍后重试。"
    if "closed" in text.lower():
        return "连接已中断，请重试。"
    return "操作未完成：" + text[:300]


async def read_auto_subscriptions(reader):
    from .jinjiang_subscriptions import read_subscriptions
    return await read_subscriptions(reader)


workbench = Workbench()


@asynccontextmanager
async def lifespan(app):
    workbench.kick_queue()
    workbench.launch_tag_lookup()
    workbench.updates.start()
    yield
    workbench.closing = True
    await workbench.updates.close()
    tasks = [task for task in (workbench.task, workbench.library_task, workbench.tags_task, workbench.account_task) if task and not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await workbench.reader.close()


app = FastAPI(title="拾页", lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.headers.get("host", "")
    if not re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::\d+)?", host):
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != f"http://{host}":
            return JSONResponse({"detail": "请求来源不匹配"}, status_code=403)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "需要 JSON 请求"}, status_code=415)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/api/state")
async def state():
    return workbench.state()


@app.get("/api/updates")
async def updates_status():
    return workbench.updates.status()


@app.post("/api/updates/settings")
async def updates_settings(request: Request):
    data = await body(request)
    if not isinstance(data.get("enabled"), bool):
        raise HTTPException(400, "请求内容无效")
    workbench.updates.configure(data["enabled"])
    return {"ok": True, **workbench.updates.status()}


@app.post("/api/updates/check")
async def updates_check():
    workbench.updates.request_check()
    return {"ok": True, **workbench.updates.status()}


@app.post("/api/chapter-updates/read")
async def chapter_update_read(request: Request):
    data = await body(request)
    identifier, chapter_id = safe_id(data.get("book_id")), safe_id(data.get("chapter_id"))
    workbench.updates.mark_read(identifier, chapter_id)
    return {"ok": True}


@app.get("/api/library")
async def library():
    return workbench.library_state()


@app.get("/api/platforms")
async def supported_platforms():
    return {"platforms": [spec.public() for spec in platforms()]}


@app.get("/api/cover")
async def cover(book_id: str, retry: bool = False):
    try:
        path = await cover_cache.get(book_id, refresh=retry)
    except ValueError:
        raise HTTPException(400, "作品 ID 无效。") from None
    except CoverRetryableError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503, headers={"Retry-After": str(exc.retry_after), "Cache-Control": "no-store"})
    if path is None:
        return JSONResponse({"detail": "这本小说暂时没有可用封面。"}, status_code=404, headers={"Cache-Control": "no-store"})
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/library/refresh")
async def refresh_library(request: Request):
    await body(request)
    workbench.launch_library_refresh()
    return {"ok": True}


@app.post("/api/account/connect")
async def account_connect(request: Request):
    await body(request)
    workbench.launch_account()
    return {"ok": True}


@app.post("/api/account/finish")
async def account_finish(request: Request):
    await body(request)
    workbench.launch_account(finish=True)
    return {"ok": True}


@app.post("/api/account/import")
async def account_import(request: Request):
    data = await body(request)
    return await workbench.import_session(data.get("cookies"))


@app.post("/api/account/cancel")
async def account_cancel(request: Request):
    await body(request)
    workbench.launch_account(cancel=True)
    return {"ok": True}


@app.get("/api/downloads")
async def downloads():
    return {"books": workbench.downloads(), "queue": workbench.queue, "queue_paused": workbench.queue_paused}


@app.get("/api/book")
async def get_book(id: str):
    return public_book(workbench.get_book(id))


@app.post('/api/book-details')
async def book_details(request: Request):
    data = await body(request)
    identifier = safe_id(data.get('id'))
    refresh = data.get('refresh',False)
    if not isinstance(refresh,bool):raise HTTPException(400,'请求内容无效')
    try:
        saved = workbench.get_book(identifier)
    except HTTPException as error:
        if error.status_code!=404:raise
        saved = None
    cached = catalog.cached(identifier)
    entry = saved or next((b for b in workbench.library_cache['books'] if b['id']==identifier),None) or cached
    if not entry:raise HTTPException(404,'书籍不在本机书架中。')
    if not entry.get('url'):return public_book(entry)
    try:
        remote = await catalog.fetch(entry['url'],refresh)
    except (CaptureError,ValueError) as error:
        raise HTTPException(400,str(error)) from None
    except Exception:
        raise HTTPException(503,'作品信息暂时无法加载，请稍后重试。') from None
    if remote.get('cached_at')!=(cached or {}).get('cached_at') and any(b['id']==identifier for b in workbench.library_cache['books']):
        workbench.remember_metadata(identifier,remote.get('metadata') or {})
    if saved:
        previous={chapter['id']:chapter for chapter in saved['chapters']}
        chapters=[]
        source_fields={'title','url','id','expected_words','vip','locked','absent_from_toc','order','ordinal','book_url','platform','rule_id','paid','summary','source_updated_at'}
        for current in remote['chapters']:
            old=previous.pop(current['id'],{})
            chapter={**current,**{key:value for key,value in old.items() if key not in source_fields}}
            if current.get('state')=='blocked' and not (old.get('text','').strip() and old.get('state') in {'done','review','reviewed'}):
                chapter.update(state='blocked',error=current.get('error',''))
            if chapter.get('state')=='capturing':chapter['state']='pending'
            chapters.append(chapter)
        for old in previous.values():
            chapter={**old,'absent_from_toc':True,'review':True,'error':'本次目录未列出此章，已保留先前内容，请核对目录。'}
            if old.get('text','').strip() and old.get('state') in {'done','review','reviewed'}:chapter['state']='review'
            elif old.get('state') not in {'blocked','failed'}:chapter['state']='blocked'
            chapters.append(chapter)
        if remote.get('platform','jjwxc')=='jjwxc':chapters.sort(key=lambda chapter:int(chapter['id']))
        for ordinal,chapter in enumerate(chapters,1):chapter['ordinal']=ordinal
        return public_book({**saved,**remote,'chapters':chapters,'toc_chapter_count':len(remote['chapters'])})
    return public_book(remote)


@app.post('/api/read-chapter')
async def read_online_chapter(request: Request):
    data=await body(request)
    identifier,chapter_id=safe_id(data.get('book_id')),safe_id(data.get('chapter_id'))
    try:
        saved = workbench.get_book(identifier)
        chapter = chapter_by_id(chapter_id, saved)
        if readable_chapter(chapter):
            return await get_chapter(chapter_id, book_id=identifier)
    except HTTPException as error:
        if error.status_code != 404:raise
    reading = catalog.cached_read(identifier, chapter_id)
    if reading:
        chapter_versions().record(identifier, reading, kind="existing")
        return reading
    book=catalog.cached(identifier)
    if not book:raise HTTPException(404,'请先打开书籍详情。')
    try:return await catalog.read(book,chapter_id)
    except (CaptureError,ValueError) as error:raise HTTPException(400,str(error)) from None
    except Exception:raise HTTPException(503,'本章暂时无法读取，请稍后重试。') from None


def readable_chapter(chapter):
    return bool(chapter.get("text", "").strip()) and chapter.get("state", "done") in {"done", "review", "reviewed"} and not chapter.get("incomplete")


def version_context(book_id, chapter_id):
    book_id, chapter_id = safe_id(book_id), safe_id(chapter_id)
    saved = None
    try:
        saved = workbench.get_book(book_id)
    except HTTPException as error:
        if error.status_code != 404:raise
    cached = catalog.cached(book_id)
    chapter = next((chapter for book in (saved, cached) if book for chapter in book.get("chapters", []) if chapter["id"] == chapter_id), None)
    reading = catalog.cached_read(book_id, chapter_id)
    if chapter is None and reading is None:
        raise HTTPException(404, "章节不存在。")
    if reading:
        chapter_versions().record(book_id, reading, kind="existing")
    if chapter and readable_chapter(chapter):
        archive_chapter(book_id, chapter)
    return saved, cached, chapter


@app.get("/api/chapter-versions")
async def list_chapter_versions(book_id: str, chapter_id: str):
    version_context(book_id, chapter_id)
    return {"versions": chapter_versions().list(book_id, chapter_id)}


@app.get("/api/chapter-version")
async def get_chapter_version(book_id: str, chapter_id: str, version_id: str):
    version_context(book_id, chapter_id)
    safe_id(version_id)
    try:
        return chapter_versions().get(book_id, chapter_id, version_id)
    except ValueError:
        raise HTTPException(404, "章节版本不存在。") from None


@app.get("/api/chapter-diff")
async def compare_chapter_versions(book_id: str, chapter_id: str, from_id: str, to_id: str):
    version_context(book_id, chapter_id)
    safe_id(from_id), safe_id(to_id)
    try:
        return chapter_versions().compare(book_id, chapter_id, from_id, to_id)
    except ValueError:
        raise HTTPException(404, "章节版本不存在。") from None


@app.post("/api/chapter-refresh")
async def refresh_chapter(request: Request):
    data = await body(request)
    identifier, chapter_id = safe_id(data.get("book_id")), safe_id(data.get("chapter_id"))
    require_idle()
    saved, cached, chapter = version_context(identifier, chapter_id)
    if saved:require_book_idle(saved)
    source = cached if cached and any(c["id"] == chapter_id for c in cached.get("chapters", [])) else saved
    if not source or not source.get("url") or not chapter:
        raise HTTPException(400, "本章没有可更新的来源。")
    try:
        result = await workbench.refresh_saved_chapter(source, chapter_id)
        if saved and any(current["id"] == chapter_id for current in saved["chapters"]):
            return await get_chapter(chapter_id, book_id=identifier)
        return result
    except HTTPException:raise
    except (CaptureError, ValueError) as error:
        raise HTTPException(400, str(error)) from None
    except Exception:
        raise HTTPException(503, "正文更新失败，请稍后重试。") from None


@app.post('/api/translation/drafts')
async def create_translation_draft(request: Request):
    data=await body(request)
    text,title=data.get('text'),data.get('title','')
    if not isinstance(text,str) or not 1<=len(text.strip())<=20000 or not isinstance(title,str) or len(title)>200:
        raise HTTPException(400,'请输入 1 至 20000 字的原文和不超过 200 字的标题。')
    identifier='local_'+uuid.uuid4().hex
    title=title.strip() or '未命名译稿'
    book={'id':identifier,'title':title,'author':'','url':'','platform':'local','platform_name':'本机文本','chapters':[{'id':'1','title':title,'state':'done','text':text,'raw_text':text,'method':'import','review':False,'characters':len(text.strip()),'ordinal':1}]}
    workbench.save_book(book)
    return public_book(book)


@app.post("/api/download")
async def download(request: Request):
    data = await body(request)
    try:
        workbench.enqueue(data.get("url", ""), data.get("mode", "auto"), data.get("delay", 3))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else "作品链接无效。")
    return {"ok": True}


@app.post("/api/queue/resume")
async def resume_queue(request: Request):
    await body(request)
    workbench.resume_queue()
    return {"ok": True}


async def body(request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except Exception:
        raise HTTPException(400, "请求内容无效")


def require_book():
    if workbench.book is None:
        raise HTTPException(400, "请先读取目录。")


def require_idle():
    if workbench.busy or workbench.account_busy:
        raise HTTPException(409, "请先暂停并等待当前章节保存。")


def require_book_idle(book):
    if workbench.chapter_refresh_id == book["id"]:
        raise HTTPException(409, "本章正在更新，请等待正文保存。")
    if workbench.translation["running"] and workbench.translation["book_id"] == book["id"]:
        raise HTTPException(409, "这本作品正在翻译，请先暂停并等待当前段落保存。")
    if workbench.proofreading["running"] and workbench.proofreading["book_id"] == book["id"]:
        raise HTTPException(409, "这本作品正在自动校对，请先暂停。")
    if workbench.busy and workbench.book and workbench.book["id"] == book["id"]:
        raise HTTPException(409, "这本作品正在采集，请先暂停并等待当前章节保存。")


@app.post("/api/open")
async def open_reader(request: Request):
    if workbench.library_syncing:
        raise HTTPException(409, "请等待书架同步结束，再打开阅读窗口。")
    data = await body(request)
    url = data.get("url", "")
    try:
        if url:
            url = normalize_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    workbench.launch(workbench.open(url), "opening")
    return {"ok": True}


@app.post("/api/scan")
async def scan(request: Request):
    data = await body(request)
    try:
        url = normalize_url(data.get("url", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    workbench.launch(workbench.scan(url), "scanning")
    return {"ok": True}


@app.post("/api/start")
async def start(request: Request):
    require_book()
    data = await body(request)
    if workbench.book["id"] == "demo":
        raise HTTPException(400, "演示样本已就绪，可直接校对或导出。输入作品链接后读取真实目录。")
    mode = data.get("mode", "auto")
    if mode not in {"auto", "ocr"}:
        raise HTTPException(400, "识别模式无效")
    try:
        delay = min(30, max(3, float(data.get("delay", 3))))
    except (ValueError, TypeError):
        raise HTTPException(400, "章节间隔无效")
    workbench.resume_current(mode, delay)
    return {"ok": True}


@app.post("/api/pause")
async def pause(request: Request):
    await body(request)
    workbench.pause_all()
    return {"ok": True}


@app.post("/api/export")
async def export(request: Request):
    data = await body(request)
    book = workbench.get_book(data.get("book_id"))
    require_book_idle(book)
    try:
        clean = data.get("clean", True)
        if not isinstance(clean, bool):
            raise ValueError("请选择纯净导出或保留检查报告。")
        variant = data.get("variant", "source")
        if variant not in {"source", "translation"}:
            raise ValueError("请选择导出原文或译文。")
        if variant == "translation":
            book = translation_store().export_book(book, data.get("target_language"))
        path = export_book(book, EXPORTS, data.get("format", "txt"), clean=clean)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"path": str(path), "url": "/exports/" + path.name, "summary": summarize(book), "clean": clean}


@app.get('/api/jinjiang/settings')
async def jinjiang_settings():
    from .jinjiang import has_token
    try:
        configured = await asyncio.to_thread(has_token)
    except ValueError:
        configured = False
    return {'has_token': configured}


@app.get('/api/jinjiang/connection')
async def jinjiang_connection_status():
    from .jinjiang import token_expired
    settings = await jinjiang_settings()
    return {'state': 'needs_reconnect' if token_expired and settings['has_token'] else 'connected' if settings['has_token'] else 'not_connected'}


@app.post('/api/jinjiang/connect')
async def connect_jinjiang(request: Request):
    data = await body(request)
    require_idle()
    try:
        return await jinjiang_connection.login(data)
    except ValueError as error:
        raise HTTPException(400,str(error)) from None
    finally:
        workbench.kick_queue()


@app.post('/api/jinjiang/verification')
async def verify_jinjiang(request: Request):
    data = await body(request)
    require_idle()
    try:
        return await jinjiang_connection.verification(data.get('challenge_id'))
    except ValueError as error:
        raise HTTPException(400,str(error)) from None
    finally:
        workbench.kick_queue()


@app.post('/api/jinjiang/cancel-connection')
async def cancel_jinjiang_connection(request: Request):
    data = await body(request)
    if jinjiang_connection.busy:
        raise HTTPException(409,'正在连接晋江，请稍候。')
    identifier = data.get('challenge_id')
    if identifier is not None and not isinstance(identifier,str):
        raise HTTPException(400,'本次验证已过期，请重新连接。')
    jinjiang_connection.clear(identifier)
    return {'ok': True}


@app.post('/api/jinjiang/settings')
async def save_jinjiang_settings(request: Request):
    from .jinjiang import save_token
    data = await body(request)
    require_idle()
    if not isinstance(data.get('delete_token', False), bool):
        raise HTTPException(400,'晋江 Token 设置无效。')
    try:
        return await asyncio.to_thread(save_token,data.get('token'),data.get('delete_token',False))
    except ValueError as error:
        raise HTTPException(400,str(error)) from None


@app.post("/api/proofread")
async def proofread(request: Request):
    data = await body(request)
    require_idle()
    book = workbench.get_book(data.get("book_id"))
    if not any(c.get("text", "").strip() and c.get("state") in {"done", "review"} and not c.get("incomplete") for c in book["chapters"]):
        raise HTTPException(400, "没有可自动校对的已保存章节。")
    workbench.launch(workbench.proofread_book(book), "proofreading")
    return {"ok": True}


@app.post("/api/proofread/pause")
async def pause_proofread(request: Request):
    await body(request)
    if workbench.proofreading["running"]:
        workbench.pause = True
    return {"ok": True}


@app.post("/api/proofread/undo")
async def undo_proofread(request: Request):
    data = await body(request)
    book = workbench.get_book(data.get("book_id"))
    require_book_idle(book)
    restored = 0
    for chapter in book["chapters"]:
        if not chapter.get("proofread_undo") or not proofread_current(chapter):
            continue
        archive_chapter(book["id"], chapter)
        snapshot = chapter.pop("proofread_undo")
        for key in snapshot["missing"]:
            chapter.pop(key, None)
        chapter.update(snapshot["values"])
        archive_chapter(book["id"], chapter, "proofread_undo")
        restored += 1
    if not restored:
        raise HTTPException(400, "没有可撤销的自动校对结果。手动修改后的章节会保留。")
    book.pop("proofread_completed_at", None)
    workbench.save_book(book, current=book is workbench.book)
    if workbench.proofreading["book_id"] == book["id"]:
        workbench.proofreading = {"book_id": None, "running": False, "done": 0, "total": 0, "changed": 0, "issues": 0, "message": ""}
    workbench.message = "自动校对已撤销，已恢复处理前的正文。"
    return {"ok": True}


def public_translation_settings():
    from . import translation_settings as settings
    result = settings.load_settings()
    result["providers"] = [{"id": key, "name": spec["name"], "model": spec["default_model"],
                            "key_configured": settings.has_provider_key(key)} for key, spec in settings.PROVIDERS.items()]
    return result


@app.get("/api/translation/settings")
async def get_translation_settings():
    try:
        return await asyncio.to_thread(public_translation_settings)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/translation/settings")
async def set_translation_settings(request: Request):
    from . import translation_settings as settings
    data = await body(request)
    require_idle()
    delete_key = data.pop("delete_key", False)
    try:
        result = await asyncio.to_thread(settings.save_settings, data)
        if delete_key is True:
            await asyncio.to_thread(settings.delete_provider_key, result["provider"])
        return {"ok": True, **await asyncio.to_thread(public_translation_settings)}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/translate")
async def translate(request: Request):
    from . import translation_settings as settings
    from .translation import normalize_language, TranslationError
    from .translation_jobs import run_translation
    data = await body(request)
    require_idle()
    book = workbench.get_book(data.get("book_id"))
    scope = data.get("scope", "all")
    if scope not in {"first", "all"}:
        raise HTTPException(400, "请选择首章试译或翻译全部已保存章节。")
    if not any(chapter.get("text", "").strip() and chapter.get("state") in {"done", "review", "reviewed"} and not chapter.get("incomplete") for chapter in book["chapters"]):
        raise HTTPException(400, "请先下载小说正文，再开始翻译。")
    try:
        config = await asyncio.to_thread(settings.load_settings)
        config["source_language"] = settings.validate_source(data.get("source_language", config["source_language"]))
        platform = book.get("platform")
        known_chinese_source = platform in ("jjwxc", "gongzicp", "fanqie") or (
            platform is None and re.fullmatch(r"[0-9]+", book["id"]) is not None)
        if config["source_language"] == "auto" and known_chinese_source:
            config["source_language"] = "zh"
        source_language = normalize_language(config["source_language"])
        config["target_language"] = normalize_language(data.get("target_language", config["target_language"]), target=True)
        if source_language != "auto" and source_language.split("-", 1)[0].lower() == config["target_language"].split("-", 1)[0].lower():
            raise ValueError("源语言和目标语言相同，请选择另一种目标语言。")
        key = await asyncio.to_thread(settings.load_provider_key, config["provider"])
        if not key:
            raise ValueError("请先在 AI 翻译设置中保存服务商的 API Key。")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from None
    workbench.launch(run_translation(workbench, translation_store(), book, config, key, scope), "translating")
    return {"ok": True}


@app.post("/api/translate/pause")
async def pause_translation(request: Request):
    await body(request)
    if workbench.translation["running"]:
        workbench.pause = True
        workbench.translation["message"] = "将在当前翻译段落保存后暂停…"
    return {"ok": True}


@app.get("/api/translation/glossary")
async def translation_glossary(book_id: str, target_language: str | None = None):
    workbench.get_book(book_id)
    try:
        return translation_store().glossary(book_id, target_language)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/translation/glossary")
async def save_translation_glossary(request: Request):
    from .translation_settings import load_settings, validate_target
    from .translation import normalize_glossary
    data = await body(request)
    require_idle()
    book = workbench.get_book(data.get("book_id"))
    store = translation_store()
    try:
        terms = normalize_glossary(data.get("terms"))
        language = data.get("target_language") or store.selected(book["id"])
        if not language or not store.load(book["id"], language):
            config = await asyncio.to_thread(load_settings)
            require_idle()
            config["target_language"] = validate_target(language or config["target_language"])
            store.create(book["id"], config)
            language = config["target_language"]
        return {"ok": True, **store.update_glossary(book["id"], terms, language)}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from None


def chapter_by_id(chapter_id, book=None):
    safe_id(str(chapter_id))
    book = book if book is not None else workbench.get_book()
    for chapter in book["chapters"]:
        if str(chapter["id"]) == str(chapter_id):
            return chapter
    raise HTTPException(404, "章节不存在")


@app.get("/api/chapter")
async def get_chapter(id: str, book_id: str | None = None):
    book = workbench.get_book(book_id)
    chapter = chapter_by_id(id, book)
    archive_chapter(book["id"], chapter)
    fields = {"id", "title", "url", "state", "text", "raw_text", "characters", "method", "error", "review", "incomplete", "absent_from_toc", "vip", "locked", "expected_words", "lines", "ordinal", "proofreading", "source_updated_at"}
    result = {key: value for key, value in chapter.items() if key in fields}
    result["book_id"] = book["id"]
    result["translation"] = translation_store().chapter(book, chapter, full=True)
    result["screenshots"] = []
    for path in chapter.get("screenshots", []):
        resolved = Path(path).resolve()
        if resolved.is_relative_to(BOOKS.resolve()) and resolved.suffix.lower() == ".png":
            result["screenshots"].append("/sources/" + str(resolved.relative_to(BOOKS.resolve())))
    return result


@app.post("/api/chapter")
async def update_chapter(request: Request):
    data = await body(request)
    book = workbench.get_book(data.get("book_id"))
    require_book_idle(book)
    chapter = chapter_by_id(data.get("id"), book)
    if "base_text" in data and data["base_text"] != chapter.get("text", ""):
        raise HTTPException(409, "本章已更新，请重新打开后再保存。")
    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "校对正文不能为空。")
    if not chapter.get("text"):
        raise HTTPException(400, "该章节尚未采集，不能以校对操作标记完成。")
    if chapter.get("state") == "blocked":
        raise HTTPException(400, "本章当前未显示可读取正文，不能通过校对标记完成。")
    archive_chapter(book["id"], chapter)
    chapter.update(text=text.strip(), state="reviewed", review=False, incomplete=False, error="", characters=len(re.sub(r"\s", "", text)))
    chapter.pop("proofreading", None)
    chapter.pop("proofread_undo", None)
    archive_chapter(book["id"], chapter, "edit")
    workbench.save_book(book, current=book is workbench.book)
    return {"ok": True}


@app.get("/sources/{path:path}")
async def source(path: str):
    resolved = (BOOKS / path).resolve()
    if not resolved.is_relative_to(BOOKS.resolve()) or resolved.suffix.lower() != ".png" or not resolved.is_file():
        raise HTTPException(404)
    return FileResponse(resolved)


@app.post("/api/demo")
async def demo(request: Request):
    await body(request)
    require_idle()
    original = [
        ("第一章 · 雨里的书店", "清晨，林溪推开旧书店的窗，雨水正沿着屋檐落下。\n\n柜台上躺着一封没有署名的信。信里只写了一句话：请把第七页还给明天。\n\n她翻开那本蓝色封面的旧书。第六页之后，是一张画着海岸线的空白地图。"),
        ("第二章 · 留白的地图", "午后，雨终于停了。林溪把地图铺在窗边，纸上渐渐浮出一条很细的墨线。\n\n墨线穿过她从未到过的小镇，停在一座没有钟的钟楼下面。\n\n她决定明天出发，先去看看那个被留白的地方。"),
        ("第三章 · 缺失示例", ""),
    ]
    workbench.book = {"id": "demo", "title": "雨里的书店 · 原创演示", "author": "拾页测试样本", "url": "", "demo": True, "chapters": [
        {"id": str(i+1), "title": title, "url": "", "text": text, "raw_text": text, "method": "demo", "review": i == 1, "state": "done" if i == 0 else "review" if i == 1 else "blocked", "error": "演示：该章不可读，导出时会保留缺章提示。" if i == 2 else "", "characters": len(text)} for i, (title, text) in enumerate(original)
    ]}
    workbench.save()
    workbench.phase, workbench.message = "ready", "这是原创演示，含一个待校对章节和一个缺章示例，不代表晋江采集结果。"
    return {"ok": True}


app.mount("/exports", StaticFiles(directory=EXPORTS), name="exports")


@app.get("/")
async def identity():
    return {"app": "拾页", "version": "0.7.0", "ui": "请打开项目中的 拾页.app"}
