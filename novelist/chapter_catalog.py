"""Small public Jinjiang directory reads for background update checks."""
from datetime import datetime
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urljoin, urlparse


class DirectoryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.anchor = None
        self.meta = {}
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style'}:
            self.ignored += 1
        if tag == 'meta':
            self.meta[attrs.get('property') or attrs.get('name')] = attrs.get('content', '')
        if tag == 'tr':
            self.row = [] if 'chapter' in attrs.get('itemprop', '').split() else None
        elif tag in {'td', 'th'} and self.row is not None:
            self.cell = {'text': '', 'links': [], 'words': 'wordCount' in attrs.get('itemprop', '').split()}
            self.row.append(self.cell)
        elif tag == 'a' and self.cell is not None:
            self.anchor = {'text': '', 'href': attrs.get('href'), 'rel': attrs.get('rel')}
            self.cell['links'].append(self.anchor)
        elif tag == 'br' and self.cell is not None:
            self.cell['text'] += '\n'

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.ignored = max(0, self.ignored - 1)
        if tag == 'a':
            self.anchor = None
        elif tag in {'td', 'th'}:
            self.cell = self.anchor = None
        elif tag == 'tr':
            if self.row:
                self.rows.append(self.row)
            self.row = self.cell = self.anchor = None

    def handle_data(self, text):
        if self.ignored:
            return
        if self.cell is not None:
            self.cell['text'] += text
        if self.anchor is not None:
            self.anchor['text'] += text


def parse_directory(raw, identifier):
    if not re.fullmatch(r'\d{1,12}', identifier):
        raise ValueError('作品 ID 无效。')
    if isinstance(raw, bytes):
        charset = re.search(br'charset\s*=\s*["\x27]?([\w-]+)', raw[:4096], re.I)
        encoding = charset.group(1).decode('ascii').lower() if charset else 'utf-8'
        raw = raw.decode('gb18030' if encoding in {'gb2312', 'gbk', 'gb18030'} else 'utf-8', errors='replace')
    parser = DirectoryParser()
    parser.feed(raw)
    canonical = 'https://www.jjwxc.net/onebook.php?novelid=' + identifier
    chapters = {}
    for cells in parser.rows:
        if len(cells) < 3:
            continue
        chapter_id = cells[0]['text'].strip()
        if not chapter_id.isdigit() or int(chapter_id) < 1:
            continue
        chapter = None
        for anchor in cells[1]['links']:
            for raw_url in (anchor['href'], anchor['rel']):
                if not raw_url:
                    continue
                url = urlparse(urljoin(canonical, raw_url))
                query = parse_qs(url.query)
                if (url.scheme not in {'http', 'https'} or url.hostname not in {'www.jjwxc.net', 'my.jjwxc.net', 'jjwxc.net', 'www.jjwxc.com', 'my.jjwxc.com', 'jjwxc.com'}
                    or url.username or url.password or url.port not in {None, 80, 443}
                    or url.path not in {'/onebook.php', '/onebook_vip.php'}
                    or query.get('novelid') != [identifier] or query.get('chapterid') != [chapter_id]):
                    continue
                title = ' '.join(anchor['text'].split())
                if title:
                    chapter = {'id': chapter_id, 'title': title, 'url': f'https://{url.hostname}{url.path}?novelid={identifier}&chapterid={chapter_id}', 'vip': '_vip' in url.path, 'state': 'pending'}
                    break
            if chapter:
                break
        if chapter is None:
            title = ' '.join(cells[1]['text'].split())
            chapter = {'id': chapter_id, 'title': title or f'第 {chapter_id} 章', 'url': canonical + '&chapterid=' + chapter_id, 'state': 'blocked', 'locked': bool(re.search(r'\[锁\]|锁定|锁章', title)), 'error': '目录中的本章没有可用阅读链接。'}
        timestamp = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(:\d{2})?', cells[-1]['text'].strip())
        updated = ''
        if timestamp:
            candidate = timestamp[1] + ' ' + timestamp[2] + (timestamp[3] or ':00')
            try:
                datetime.strptime(candidate, '%Y-%m-%d %H:%M:%S')
                updated = candidate
            except ValueError:
                pass
        count = next((cell['text'].strip() for cell in cells if cell['words']), '')
        chapter.update(summary=cells[2]['text'].strip()[:4000], source_updated_at=updated, expected_words=int(count) if count.isdigit() else 0)
        chapters[chapter_id] = chapter
    if not chapters:
        raise ValueError('未读取到章节目录。')
    result = {'id': identifier, 'url': canonical, 'platform': 'jjwxc', 'platform_name': '晋江文学城', 'chapters': sorted(chapters.values(), key=lambda c: int(c['id']))}
    for key, meta in [('title', 'og:novel:book_name'), ('author', 'og:novel:author')]:
        value = parser.meta.get(meta, '').strip()
        if value:
            result[key] = value
    return result
