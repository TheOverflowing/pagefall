"""Local shelf folders and reading history, independent of website mutations."""
from pathlib import Path
import re
import time
from uuid import uuid4

from .translation_store import atomic_json, identifier, read_json


def folder_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise ValueError('文件夹名称须为 1 至 80 个字。')
    return value.strip()


def update_time(value):
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?', value.strip()):
        return value.strip()
    return None


class LibraryOrganization:
    def __init__(self, path):
        self.path = Path(path)
        self.data = read_json(self.path, {})
        for key, default in (('folders', {}), ('assignments', {}), ('ignored_source_ids', []), ('last_read', {}), ('updated_at', {})):
            self.data.setdefault(key, default)

    def save(self):
        atomic_json(self.path, self.data)

    def folders(self):
        return [{key: value for key, value in folder.items() if key != 'renamed'} for folder in self.data['folders'].values()]

    def merge_source(self, folders):
        changed = False
        ignored = set(self.data['ignored_source_ids'])
        for special in ('jjwxc_0', 'jjwxc_-1'):
            if special in self.data['folders']:
                del self.data['folders'][special]
                changed = True
        for source in folders:
            source_id = identifier(source['id'])
            if source_id in ignored or source_id in {'0', '-1'}:
                continue
            folder_id = 'jjwxc_' + source_id
            name = folder_name(source['name'])
            existing = self.data['folders'].get(folder_id)
            if existing is None:
                self.data['folders'][folder_id] = {'id': folder_id, 'name': name, 'source': 'jjwxc', 'source_id': source_id}
                changed = True
            elif not existing.get('renamed') and existing['name'] != name:
                existing['name'] = name
                changed = True
        if changed:
            self.save()

    def create(self, name):
        name = folder_name(name)
        folder_id = 'local_' + uuid4().hex
        self.data['folders'][folder_id] = {'id': folder_id, 'name': name, 'source': 'local'}
        self.save()
        return folder_id

    def require_folder(self, folder_id):
        identifier(folder_id)
        if folder_id not in self.data['folders']:
            raise KeyError('文件夹不存在。')
        return self.data['folders'][folder_id]

    def rename(self, folder_id, name):
        folder = self.require_folder(folder_id)
        folder.update(name=folder_name(name), renamed=True)
        self.save()

    def delete(self, folder_id):
        folder = self.require_folder(folder_id)
        if folder.get('source_id'):
            self.data['ignored_source_ids'].append(folder['source_id'])
        del self.data['folders'][folder_id]
        for book_id, assigned in self.data['assignments'].items():
            if assigned == folder_id:
                self.data['assignments'][book_id] = None
        self.save()

    def assign(self, book_ids, folder_id):
        if folder_id is not None:
            self.require_folder(folder_id)
        for book_id in book_ids:
            self.data['assignments'][identifier(book_id)] = folder_id
        self.save()

    def fields(self, book):
        book_id = book['id']
        if book_id in self.data['assignments']:
            assigned = self.data['assignments'][book_id]
            folders = [assigned] if assigned in self.data['folders'] else []
        else:
            folders = ['jjwxc_' + source_id for source_id in book.get('source_folder_ids', [])
                       if 'jjwxc_' + source_id in self.data['folders']]
        dates = [date for value in (self.data['updated_at'].get(book_id), book.get('updated_at')) if (date := update_time(value))]
        return {'folder_ids': folders, 'last_read_at': self.data['last_read'].get(book_id),
                'updated_at': max(dates, key=lambda value: value.replace('T', ' '), default=None)}

    def mark_read(self, book_id):
        timestamp = time.time()
        self.data['last_read'][identifier(book_id)] = timestamp
        self.save()
        return timestamp

    def remember_updated(self, book_id, value):
        date = update_time(value)
        if date and self.data['updated_at'].get(book_id) != date:
            self.data['updated_at'][identifier(book_id)] = date
            self.save()
