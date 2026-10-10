"""Per-book translation state; source text and API credentials never live here."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone


def digest(text):
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise ValueError('作品或章节 ID 无效。')
    return value


def target_code(value):
    from .translation_settings import normalize_language
    return normalize_language(value, source=False)


def eligible_source(chapter):
    text = chapter.get('text')
    return (isinstance(text, str) and bool(text.strip())
            and chapter.get('state') in {'done', 'review', 'reviewed'}
            and not chapter.get('incomplete'))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        result = json.loads(path.read_text())
        return result if isinstance(result, dict) else default
    except (FileNotFoundError, ValueError, UnicodeError):
        return default


class TranslationStore:
    def __init__(self, root):
        self.root = Path(root)

    def recover_interrupted(self):
        for path in self.root.glob('*/*/index.json'):
            index = read_json(path)
            if not index or not isinstance(index.get('chapters'), dict):
                continue
            changed = False
            for row in index['chapters'].values():
                if isinstance(row, dict) and row.get('status') == 'translating':
                    row.update(status='paused', error='上次翻译已中断，已保存完成的段落。', updated_at=now())
                    changed = True
            if changed:
                atomic_json(path, index)

    def folder(self, book_id, language):
        return self.root / identifier(book_id) / target_code(language)

    def selected(self, book_id):
        value = read_json(self.root / identifier(book_id) / 'selection.json', {})
        try:
            return target_code(value['target_language'])
        except (KeyError, ValueError, TypeError):
            return None

    def select(self, book_id, language):
        atomic_json(self.root / identifier(book_id) / 'selection.json', {'target_language': target_code(language)})

    def load(self, book_id, language=None):
        language = language or self.selected(book_id)
        if language is None:
            return None
        value = read_json(self.folder(book_id, language) / 'index.json')
        if not value or not isinstance(value.get('chapters'), dict):
            return None
        return value

    def create(self, book_id, config):
        language = target_code(config['target_language'])
        value = self.load(book_id, language) or {'book_id': book_id, 'chapters': {}, 'glossary': [], 'glossary_revision': 0}
        previous = (value.get('provider'), value.get('model'), value.get('source_language'))
        current = (config['provider'], config['model'], config['source_language'])
        if value.get('provider') and previous != current:
            value['glossary_revision'] += 1
        value.update({key: config[key] for key in ('provider', 'model', 'source_language', 'target_language')})
        self.save(value)
        self.select(book_id, language)
        return value

    def save(self, value):
        value['updated_at'] = now()
        atomic_json(self.folder(value['book_id'], value['target_language']) / 'index.json', value)

    def chapter_path(self, book_id, language, chapter_id):
        return self.folder(book_id, language) / 'chapters' / (identifier(chapter_id) + '.json')

    def read_work(self, book_id, language, chapter_id):
        return read_json(self.chapter_path(book_id, language, chapter_id), {})

    def write_work(self, book_id, language, chapter_id, value):
        atomic_json(self.chapter_path(book_id, language, chapter_id), value)

    def source_dependencies(self, book):
        """Fingerprint preceding eligible source versions, in catalog order.

        Call once for batch views/jobs and pass the mapping to ``chapter`` so
        polling a long book does not repeatedly hash every chapter's text.
        """
        prefix = digest('novelist-source-prefix-v1')
        dependencies = {}
        for chapter in book['chapters']:
            dependencies[chapter['id']] = prefix
            if eligible_source(chapter):
                version = [chapter['id'], chapter.get('title', ''), digest(chapter['text'])]
                prefix = digest(prefix + '\0' + json.dumps(version, ensure_ascii=False, separators=(',', ':')))
        return dependencies

    def chapter(self, book, chapter, *, full=False, language=None, index=None, dependencies=None):
        index = index or self.load(book['id'], language)
        if index is None:
            return None
        row = index['chapters'].get(chapter['id'])
        if not row:
            return {'status': 'pending', 'source_language': index['source_language'], 'target_language': index['target_language']}
        dependencies = self.source_dependencies(book) if dependencies is None else dependencies
        result = {key: row[key] for key in ('status', 'error', 'updated_at', 'translated_title', 'quality_issues_count', 'translated_characters') if key in row}
        if (row.get('source_hash') != digest(chapter.get('text', ''))
                or row.get('source_title') != chapter.get('title', '')
                or row.get('source_dependency') != dependencies.get(chapter['id'])
                or row.get('glossary_revision') != index['glossary_revision']):
            result['status'] = 'stale'
        result.update(source_language=index['source_language'], target_language=index['target_language'])
        if full:
            saved = self.read_work(book['id'], index['target_language'], chapter['id']).get('result') or {}
            result.update({key: saved[key] for key in ('text', 'quality_issues', 'translated_title') if key in saved})
        return result

    def summary(self, book, language=None):
        index = self.load(book['id'], language)
        if not index:
            return None
        eligible = [c for c in book['chapters'] if eligible_source(c)]
        dependencies = self.source_dependencies(book)
        rows = [self.chapter(book, chapter, index=index, dependencies=dependencies) for chapter in eligible]
        done = sum(row['status'] == 'done' for row in rows)
        failed = sum(row['status'] == 'failed' for row in rows)
        stale = sum(row['status'] == 'stale' for row in rows)
        return {key: index[key] for key in ('provider', 'model', 'source_language', 'target_language')} | {
            'status': 'stale' if stale else 'done' if eligible and done == len(eligible) else 'partial',
            'done': done, 'total': len(eligible), 'failed': failed, 'stale': stale,
            'glossary_count': len(index['glossary']), 'updated_at': index.get('updated_at'),
            'has_text': any(row.get('translated_characters', 0) > 0 for row in rows)}

    def glossary(self, book_id, language=None):
        index = self.load(book_id, language)
        return {'book_id': book_id, 'target_language': index['target_language'] if index else language,
                'terms': index['glossary'] if index else []}

    def update_glossary(self, book_id, terms, language=None):
        from .translation import normalize_glossary
        index = self.load(book_id, language)
        if index is None:
            raise ValueError('请先选择这本书的翻译语言。')
        cleaned = normalize_glossary(terms)
        if cleaned != index['glossary']:
            index['glossary'] = cleaned
            index['glossary_revision'] += 1
            self.save(index)
        return self.glossary(book_id, index['target_language'])

    def merge_terms(self, index, terms):
        from .translation import normalize_glossary
        # Keep first accepted mappings fixed throughout this book. Only an
        # explicit glossary edit changes mappings and invalidates old chapters.
        merged = normalize_glossary([*index['glossary'], *terms])
        for term in merged:
            term['locked'] = True
        index['glossary'] = merged

    def export_book(self, book, language=None):
        index = self.load(book['id'], language)
        if not index:
            raise ValueError('这本书还没有译文。')
        translated = {key: value for key, value in book.items() if key != 'chapters'}
        translated['id'] = book['id'] + '_translation_' + index['target_language']
        translated['title'] = book['title'] + ' · ' + index['target_language']
        translated['translation_info'] = {key: index[key] for key in ('provider', 'model', 'source_language', 'target_language')}
        translated['chapters'] = []
        available = 0
        dependencies = self.source_dependencies(book)
        for chapter in book['chapters']:
            result = self.chapter(book, chapter, full=True, index=index, dependencies=dependencies)
            text = result.get('text') if result else ''
            good = bool(text) and result['status'] == 'done'
            translated['chapters'].append({'id': chapter['id'], 'title': result.get('translated_title') or chapter['title'],
                'url': chapter.get('url', ''), 'text': text if good else '', 'state': 'review' if good else 'blocked',
                'review': good, 'method': 'ai_translation', 'error': 'AI 译文，未经人工确认。' if good else '本章尚未翻译或译文已过期，请更新后导出。'})
            available += good
        if not available:
            raise ValueError('没有可导出的当前译文，请先翻译或更新章节。')
        return translated
