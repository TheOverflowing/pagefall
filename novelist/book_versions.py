"""Whole-book snapshots, independent from the working download and chapter history."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .translation_store import atomic_json, identifier, read_json


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def saved_chapter(chapter):
    return isinstance(chapter.get('text'), str) and bool(chapter['text'].strip()) and not chapter.get('incomplete')


class BookVersions:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, book_id):
        return self.root / identifier(book_id)

    def index(self, book_id):
        return read_json(self.folder(book_id) / 'index.json', {'versions': [], 'deleted': []})

    def list(self, book_id):
        return list(reversed(self.index(book_id)['versions']))

    def initialize(self, book, translations=None):
        if (self.folder(book['id']) / 'index.json').exists():
            return
        self.record(book, kind='existing', translations=translations)
        if not (self.folder(book['id']) / 'index.json').exists():
            atomic_json(self.folder(book['id']) / 'index.json', {'versions': [], 'deleted': []})

    def record(self, book, *, version_id=None, kind='download', refresh=False, refresh_failed=0, translations=None):
        chapters = [chapter for chapter in book.get('chapters', []) if saved_chapter(chapter)]
        if not chapters:
            return None
        book_id = identifier(book['id'])
        index = self.index(book_id)
        translation_fingerprint = hashlib.sha256()
        translation_source = Path(translations) / book_id if translations else None
        if translation_source and translation_source.is_dir():
            for path in sorted(translation_source.rglob('*.json')):
                translation_fingerprint.update(str(path.relative_to(translation_source)).encode())
                translation_fingerprint.update(path.read_bytes())
        fingerprint = hashlib.sha256(json.dumps([
            [chapter['id'], chapter.get('title'), chapter.get('text'), chapter.get('method'), chapter.get('proofreading')]
            for chapter in chapters
        ] + [translation_fingerprint.hexdigest()], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if kind == 'existing' and index['versions'] and index['versions'][-1].get('fingerprint') == fingerprint:
            return index['versions'][-1]
        version_id = identifier(version_id or uuid4().hex)
        if version_id in index.get('deleted', []):
            return None
        previous = next((row for row in index['versions'] if row['id'] == version_id), {})
        methods = sorted({chapter.get('method', 'text') for chapter in chapters})
        sources = {'ocr' if method == 'ocr' else 'api' if method == 'jjwxc_api' else 'text' for method in methods}
        stamp = now()
        summary = {'id': version_id, 'created_at': previous.get('created_at', stamp), 'updated_at': stamp,
                   'kind': kind, 'source': next(iter(sources)) if len(sources) == 1 else 'mixed',
                   'methods': methods, 'saved': len(chapters), 'total': len(book.get('chapters', [])),
                   'refresh': bool(refresh), 'refresh_failed': refresh_failed, 'fingerprint': fingerprint}
        folder = self.folder(book_id) / version_id
        snapshot = copy.deepcopy(book)
        snapshot.pop('download_run', None)
        snapshot.update(version_id=version_id)
        atomic_json(folder / 'book.json', snapshot)
        if translations:
            source = Path(translations) / book_id
            destination = folder / 'translations' / book_id
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
        index['versions'] = [row for row in index['versions'] if row['id'] != version_id] + [summary]
        atomic_json(self.folder(book_id) / 'index.json', index)
        return summary

    def get(self, book_id, version_id):
        identifier(version_id)
        if not any(row['id'] == version_id for row in self.index(book_id)['versions']):
            raise ValueError('下载版本不存在。')
        result = read_json(self.folder(book_id) / version_id / 'book.json')
        if result is None:
            raise ValueError('下载版本不存在。')
        return result

    def translations(self, book_id, version_id):
        return self.folder(book_id) / identifier(version_id) / 'translations'

    def delete(self, book_id, version_id):
        self.get(book_id, version_id)
        index = self.index(book_id)
        source = self.folder(book_id) / version_id
        trash = self.root / '.trash' / identifier(book_id) / (version_id + '-' + uuid4().hex)
        trash.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        source.replace(trash)
        index['versions'] = [row for row in index['versions'] if row['id'] != version_id]
        index.setdefault('deleted', []).append(version_id)
        atomic_json(self.folder(book_id) / 'index.json', index)
        return self.list(book_id)
