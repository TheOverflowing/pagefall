"""Local, atomic TXT, Markdown and EPUB 3 exports of a captured book."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import escape
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid
from xml.sax.saxutils import escape as xml_escape
import zipfile


_CAPTURED_STATES = {"done", "reviewed", "review"}
_SECRET_QUERY_PARTS = ("token", "cookie", "password", "secret", "credential", "session", "authorization")
_STATE_LABELS = {
    "pending": "尚未采集", "failed": "采集失败", "blocked": "访问受限",
    "done": "已采集", "review": "待校对", "reviewed": "已校对",
}


def _text(value: object) -> str:
    """Remove characters that XML 1.0 cannot represent, preserving newlines."""
    return "".join(
        char for char in str(value if value is not None else "")
        if char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF
    )


def _captured(chapter: dict) -> bool:
    return chapter.get("state") in _CAPTURED_STATES and bool(_text(chapter.get("text")).strip())


def _needs_review(chapter: dict) -> bool:
    if chapter.get("absent_from_toc"):
        return True
    if proofread_current(chapter) and chapter.get("state") != "reviewed":
        return bool(chapter["proofreading"].get("issues_count"))
    return _captured(chapter) and chapter.get("state") != "reviewed" and (
        chapter.get("state") == "review" or bool(chapter.get("review"))
    )


def proofread_current(chapter: dict) -> bool:
    result = chapter.get("proofreading") or {}
    return bool(result.get("auto_processed") and result.get("result_hash") == hashlib.sha256(str(chapter.get("text", "")).encode()).hexdigest())


def summarize(book: dict) -> dict:
    """Describe captured coverage, without treating OCR review as complete."""
    chapters = book.get("chapters") or []
    total = len(chapters)
    captured = sum(_captured(chapter) for chapter in chapters)
    review = sum(_needs_review(chapter) for chapter in chapters)
    missing = total - captured
    return {
        "total": total, "captured": captured, "missing": missing, "review": review,
        "complete": total > 0 and missing == 0 and review == 0,
        "auto_processed": sum(_captured(chapter) and proofread_current(chapter) for chapter in chapters),
    }


def _source_url(value: object) -> str:
    """Keep useful source identifiers, excluding credentials and fragments."""
    try:
        parts = urlsplit(_text(value).strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return ""
        hostname = parts.hostname
        if ":" in hostname:
            hostname = f"[{hostname}]"
        if parts.port:
            hostname += f":{parts.port}"
        query = [
            (key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not any(part in key.lower() for part in _SECRET_QUERY_PARTS)
            and key.lower() not in {"auth", "key", "sid", "access_key", "api_key"}
        ]
        return urlunsplit((parts.scheme.lower(), hostname, parts.path, urlencode(query), ""))
    except ValueError:
        return ""


def _title(chapter: dict, index: int) -> str:
    return _text(chapter.get("title")).strip() or f"第 {index + 1} 章"


def _filename(title: object) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", _text(title)).strip(" .")
    name = name or "未命名小说"
    # A UTF-8 byte bound also leaves room for the extension on common filesystems.
    name = name.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])", name):
        name = "_" + name
    return name or "未命名小说"


def _chapter_status(chapter: dict) -> str:
    if not _captured(chapter):
        return "缺失 / " + _STATE_LABELS.get(chapter.get("state"), "未取得有效正文")
    if _needs_review(chapter):
        return "已自动检查 / 有疑点" if proofread_current(chapter) else "已采集 / 待校对"
    if proofread_current(chapter) and chapter.get("state") != "reviewed":
        return "已自动检查 / 未发现规则内疑点"
    return _STATE_LABELS.get(chapter.get("state"), "已采集")


def _report_lines(book: dict, timestamp: str) -> list[str]:
    summary = summarize(book)
    if summary["complete"]:
        status = "完整（目录内全部章节已采集，无待校对项）"
    elif not summary["total"]:
        status = "未完成（没有章节）"
    elif summary["missing"]:
        status = "未完成（存在缺失章节）"
    else:
        status = "待校对（正文已采集，尚未全部确认）"
    lines = [
        "导出检查报告", f"状态：{status}",
        f"目录章节：{summary['total']}；已采集：{summary['captured']}；缺失：{summary['missing']}；待校对：{summary['review']}",
        "范围：以上统计仅针对本次已载入的目录；不保证平台目录未发生变更。",
        f"导出时间（UTC）：{timestamp}",
    ]
    if summary["auto_processed"]:
        lines.extend([f"本机自动检查：{summary['auto_processed']} 章。", "自动检查整理段落与重复行、标注识别疑点，不代表人工确认或全文无错字。"])
    if book.get("translation_info"):
        translation = book["translation_info"]
        lines.append(f"AI 译本：{translation.get('source_language', 'auto')} → {translation.get('target_language', '')}；{translation.get('provider', '')} / {translation.get('model', '')}。")
        lines.append("译文与原文分别保存；AI 翻译尚未经过人工确认。未翻译或过期章节保留占位。")
    for index, chapter in enumerate(book.get("chapters") or []):
        if not _captured(chapter):
            detail = _text(chapter.get("error")).strip()
            suffix = f"；{detail}" if detail else ""
            lines.append(f"缺失章节：{_title(chapter, index)}（{_chapter_status(chapter)}{suffix}）")
        elif _needs_review(chapter):
            lines.append(f"待校对章节：{_title(chapter, index)}")
        if chapter.get("absent_from_toc"):
            lines.append(f"目录差异：{_title(chapter, index)}（本次目录未列出，保留先前内容等待确认）")
    return lines


def _chapter_body(chapter: dict, *, clean: bool = False) -> str:
    if _captured(chapter):
        return _text(chapter.get("text")).strip()
    if clean:
        return "【本章正文缺失】"
    error = _text(chapter.get("error")).strip()
    return "【本章正文缺失：" + _chapter_status(chapter) + (f"；{error}" if error else "") + "】"


def _markdown_text(value: str) -> str:
    # Source material remains literal even when it resembles HTML or Markdown.
    value = escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", value)


def _plain_document(book: dict, timestamp: str, markdown: bool = False, *, clean: bool = False) -> str:
    title = _text(book.get("title")).strip() or "未命名小说"
    author = _text(book.get("author")).strip() or "未知作者"
    source = _source_url(book.get("url"))
    literal = _markdown_text if markdown else lambda text: text
    lines = [("# " if markdown else "") + literal(title), "", "作者：" + literal(author)]
    if source and not clean:
        lines.append("来源：" + literal(source))
    lines.append("")
    if not clean:
        report = _report_lines(book, timestamp)
        lines.extend([("## " if markdown else "") + report[0], ""] + [literal(item) for item in report[1:]])
    lines.extend(["", "## 目录" if markdown else "目录", ""])
    chapters = book.get("chapters") or []
    for index, chapter in enumerate(chapters):
        status = "" if clean else f" — {_chapter_status(chapter)}"
        lines.append(f"{index + 1}. {literal(_title(chapter, index))}{status}")
    for index, chapter in enumerate(chapters):
        lines.extend(["", "---", "", ("## " if markdown else "") + literal(_title(chapter, index)), ""])
        if not clean:
            lines.append("采集状态：" + _chapter_status(chapter))
            source = _source_url(chapter.get("url"))
            if source:
                lines.append("来源：" + literal(source))
            method = _text(chapter.get("method")).strip()
            if method:
                lines.append("采集方式：" + literal(method))
        lines.extend(["", literal(_chapter_body(chapter, clean=clean))])
    return "\n".join(lines).rstrip() + "\n"


def _xhtml(title: str, body: str, extra_namespaces: str = "", language: str = "zh") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{extra_namespaces} xml:lang="{escape(language)}" lang="{escape(language)}">'
        f'<head><meta charset="utf-8"/><title>{escape(title)}</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
        f'<body>{body}</body></html>'
    )


def _paragraphs(value: str) -> str:
    return "\n".join(f"<p>{escape(line)}</p>" for line in value.splitlines() if line.strip())


def _write_epub(book: dict, path: Path, timestamp: str, *, clean: bool = False) -> None:
    language = str((book.get("translation_info") or {}).get("target_language", "zh"))
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}", language):
        language = "zh"
    title = _text(book.get("title")).strip() or "未命名小说"
    author = _text(book.get("author")).strip() or "未知作者"
    chapters = book.get("chapters") or []
    identifier = "urn:uuid:" + str(uuid.uuid4())
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="titlepage" href="title.xhtml" media-type="application/xhtml+xml"/>' if clean else '<item id="report" href="report.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine = ['<itemref idref="titlepage"/>' if clean else '<itemref idref="report"/>']
    navigation = [] if clean else ['<li><a href="report.xhtml">导出检查报告</a></li>']
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="EPUB/package.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        ))
        archive.writestr("EPUB/style.css", (
            'body{font-family:serif;line-height:1.8;margin:5%;overflow-wrap:anywhere}'
            'h1{font-size:1.5em}p{white-space:pre-wrap;margin:.6em 0}'
            '.status{font-family:sans-serif;color:#555;font-size:.85em}'
        ))
        if clean:
            title_body = f"<h1>{escape(title)}</h1><p>作者：{escape(author)}</p>"
            archive.writestr("EPUB/title.xhtml", _xhtml(title, title_body, language=language))
        else:
            report = _report_lines(book, timestamp)
            report_body = f"<h1>{escape(report[0])}</h1><h2>{escape(title)}</h2><p>作者：{escape(author)}</p>"
            source = _source_url(book.get("url"))
            if source:
                report_body += f"<p>来源：{escape(source)}</p>"
            report_body += _paragraphs("\n".join(report[1:]))
            archive.writestr("EPUB/report.xhtml", _xhtml(report[0], report_body, language=language))
        for index, chapter in enumerate(chapters):
            item_id = f"chapter_{index + 1:05d}"
            filename = item_id + ".xhtml"
            chapter_title = _title(chapter, index)
            manifest.append(f'<item id="{item_id}" href="{filename}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{item_id}"/>')
            navigation.append(f'<li><a href="{filename}">{escape(chapter_title)}</a></li>')
            body = f"<h1>{escape(chapter_title)}</h1>"
            if not clean:
                body += f'<p class="status">采集状态：{escape(_chapter_status(chapter))}</p>'
                source = _source_url(chapter.get("url"))
                if source:
                    body += f'<p class="status">来源：{escape(source)}</p>'
                method = _text(chapter.get("method")).strip()
                if method:
                    body += f'<p class="status">采集方式：{escape(method)}</p>'
            body += _paragraphs(_chapter_body(chapter, clean=clean))
            archive.writestr("EPUB/" + filename, _xhtml(chapter_title, body, language=language))
        nav_body = '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>' + "".join(navigation) + "</ol></nav>"
        archive.writestr("EPUB/nav.xhtml", _xhtml("目录", nav_body, ' xmlns:epub="http://www.idpf.org/2007/ops"', language=language))
        metadata = (
            f'<dc:identifier id="book-id">{identifier}</dc:identifier>'
            f'<dc:title>{xml_escape(title)}</dc:title><dc:creator>{xml_escape(author)}</dc:creator>'
            f'<dc:language>{xml_escape(language)}</dc:language>'
            f'<meta property="dcterms:modified">{timestamp}</meta>'
        )
        source = _source_url(book.get("url"))
        if source and not clean:
            metadata += f"<dc:source>{xml_escape(source)}</dc:source>"
        package = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="{escape(language)}">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">' + metadata + '</metadata>'
            '<manifest>' + "".join(manifest) + '</manifest><spine>' + "".join(spine) + '</spine></package>'
        )
        archive.writestr("EPUB/package.opf", package)


def export_book(book: dict, directory: Path, format: str, *, clean: bool = False) -> Path:
    """Export all chapters; clean mode omits diagnostics, never missing chapters.

    Direct callers retain report mode by default. The app's HTTP endpoint opts
    into clean mode unless the reader explicitly requests a report.
    """
    if not isinstance(clean, bool):
        raise ValueError("导出纯净模式必须为 true 或 false")
    output_format = format.lower().strip()
    if output_format not in {"txt", "md", "epub"}:
        raise ValueError("不支持的导出格式；请选择 txt、md 或 epub")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    title = _filename(book.get("title"))
    identifier_suffix = ""
    if _text(book.get("id")).strip():
        # Append after truncating the title so long titles cannot hide the ID.
        identifier = _filename(book["id"]).encode("utf-8")[:60].decode("utf-8", errors="ignore").rstrip(" .")
        identifier_suffix = f" [{identifier}]"
    mode_suffix = " · 纯净版" if clean else " · 带报告"
    suffix = identifier_suffix + mode_suffix + "." + output_format
    title_budget = 255 - len(suffix.encode("utf-8"))
    title = title.encode("utf-8")[:title_budget].decode("utf-8", errors="ignore").rstrip(" .")
    filename = title + identifier_suffix + mode_suffix
    destination = directory / f"{filename}.{output_format}"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".novelist-export-", dir=directory)
    os.close(descriptor)
    temporary = Path(temporary_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        if output_format == "epub":
            _write_epub(book, temporary, timestamp, clean=clean)
        else:
            temporary.write_text(_plain_document(book, timestamp, markdown=output_format == "md", clean=clean), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
