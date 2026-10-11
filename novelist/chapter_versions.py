"""Local snapshots of chapter text actually acquired or edited in Pagefall."""
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import tempfile
import threading
from uuid import uuid4


_LOCKS = {}
_LOCKS_LOCK = threading.Lock()
_KINDS = {'source', 'edit', 'proofread', 'existing', 'proofread_undo'}


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise ValueError('Invalid chapter version identifier')
    return value


def _summary(version):
    return {key: value for key, value in version.items() if key != 'text'}


class ChapterVersions:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, book_id, chapter_id):
        return self.directory / _identifier(book_id) / (_identifier(chapter_id) + '.json')

    def _lock(self, path):
        with _LOCKS_LOCK:
            return _LOCKS.setdefault(str(path.resolve()), threading.RLock())

    def _load(self, path):
        try:
            return json.loads(path.read_text(encoding='utf-8'))['versions']
        except FileNotFoundError:
            return []

    def record(self, book_id, chapter, kind='source'):
        if kind not in _KINDS:
            raise ValueError('Invalid chapter version kind')
        text = chapter.get('text')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Chapter text is empty')
        path = self._path(book_id, chapter['id'])
        with self._lock(path):
            versions = self._load(path)
            # Importing an old local copy must not make it a new revision again.
            candidates = reversed(versions) if kind == 'existing' else versions[-1:]
            for version in candidates:
                if version['text'] == text:
                    return dict(version)
            version = {
                'id': uuid4().hex,
                'title': chapter.get('title', ''),
                'text': text,
                'recorded_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                'source_updated_at': chapter.get('source_updated_at') or None,
                'kind': kind,
                'characters': len(text.strip()),
            }
            versions.append(version)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as output:
                    temporary = Path(output.name)
                    json.dump({'book_id': book_id, 'chapter_id': chapter['id'], 'versions': versions}, output, ensure_ascii=False)
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return dict(version)

    def list(self, book_id, chapter_id):
        path = self._path(book_id, chapter_id)
        with self._lock(path):
            return [_summary(version) for version in reversed(self._load(path))]

    def get(self, book_id, chapter_id, version_id):
        path = self._path(book_id, chapter_id)
        with self._lock(path):
            for version in self._load(path):
                if version['id'] == version_id:
                    return dict(version)
        raise ValueError('章节版本不存在。')

    def compare(self, book_id, chapter_id, from_id, to_id):
        before = self.get(book_id, chapter_id, from_id)
        after = self.get(book_id, chapter_id, to_id)
        old_lines, new_lines = before['text'].splitlines(), after['text'].splitlines()
        rows = []
        added = deleted = 0
        for kind, old_start, old_end, new_start, new_end in SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
            if kind == 'equal':
                rows.extend({'kind': 'equal', 'text': line} for line in old_lines[old_start:old_end])
            else:
                if kind in {'delete', 'replace'}:
                    rows.extend({'kind': 'delete', 'text': line} for line in old_lines[old_start:old_end])
                    deleted += sum(bool(line.strip()) for line in old_lines[old_start:old_end])
                if kind in {'insert', 'replace'}:
                    rows.extend({'kind': 'insert', 'text': line} for line in new_lines[new_start:new_end])
                    added += sum(bool(line.strip()) for line in new_lines[new_start:new_end])
        return {'from_version': _summary(before), 'to_version': _summary(after), 'rows': rows, 'added': added, 'deleted': deleted}
