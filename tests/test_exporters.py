import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from novelist.exporters import export_book, summarize


def example_book():
    return {
        "id": "17", "title": '测试 / 小说 <甲>&"乙"', "author": "作者 & 朋友",
        "url": "https://www.jjwxc.net/onebook.php?novelid=17&token=SECRET#fragment",
        "chapters": [
            {"id": "10", "title": "第十章 <初遇>", "state": "done", "text": "先出现的正文。\n\n甲 & 乙 <script>故事</script>", "method": "dom", "review": False},
            {"id": "2", "title": "第二章 复查", "state": "review", "text": "后出现的正文。", "method": "ocr", "review": True},
            {"id": "3", "title": "第三章 等待", "state": "pending", "text": "", "error": ""},
            {"id": "4", "title": "第四章 受限", "state": "blocked", "text": "请登录这一提示不应该成为正文", "error": "请先登录 <账户>"},
        ],
    }


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_summary_distinguishes_captured_review_and_missing(self):
        self.assertEqual(summarize(example_book()), {"total": 4, "captured": 2, "missing": 2, "review": 1, "complete": False, "auto_processed": 0})
        self.assertFalse(summarize({"chapters": []})["complete"])
        book = {"chapters": [{"state": "reviewed", "text": "确认的内容", "review": ["old warning"]}]}
        self.assertEqual(summarize(book), {"total": 1, "captured": 1, "missing": 0, "review": 0, "complete": True, "auto_processed": 0})
        book["chapters"][0]["text"] = "   "
        self.assertEqual(summarize(book)["missing"], 1)

    def test_coverage_ignores_whitespace_and_unexportable_characters(self):
        for text in (None, "", " \t\n\r\u00a0\u3000", "\x00\x01\ud800\ufffe\uffff"):
            with self.subTest(text=repr(text)):
                self.assertEqual(summarize({"chapters": [{"state": "done", "text": text}]})["captured"], 0)
        for text in ("\x00\u3000正文", "\ud800🙂\ufffe", "\ue000", "\ufffd", 0):
            with self.subTest(text=repr(text)):
                self.assertEqual(summarize({"chapters": [{"state": "done", "text": text}]})["captured"], 1)

    def test_text_formats_preserve_order_and_missing_markers(self):
        book = example_book()
        original = copy.deepcopy(book)
        for format in ("txt", "md"):
            result = export_book(book, self.directory, format)
            self.assertEqual(result.parent, self.directory)
            content = result.read_text(encoding="utf-8")
            self.assertIn("未完成", content)
            self.assertIn("待校对：1", content)
            self.assertIn("本章正文缺失", content)
            self.assertNotIn("请登录这一提示不应该成为正文", content)
            self.assertLess(content.index("先出现的正文"), content.index("后出现的正文"))
            self.assertNotIn("SECRET", content)
            self.assertNotIn("token=", content)
            if format == "md":
                self.assertNotIn("<script>", content)
                self.assertIn("&lt;script&gt;", content)
        self.assertEqual(book, original)

    def test_epub_is_well_formed_and_includes_all_chapters(self):
        result = export_book(example_book(), self.directory, "epub")
        with zipfile.ZipFile(result) as archive:
            first = archive.infolist()[0]
            self.assertEqual(first.filename, "mimetype")
            self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            for name in archive.namelist():
                if name.endswith((".xml", ".opf", ".xhtml")):
                    ET.fromstring(archive.read(name))
            ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/", "h": "http://www.w3.org/1999/xhtml"}
            package = ET.fromstring(archive.read("EPUB/package.opf"))
            self.assertEqual(package.attrib["version"], "3.0")
            self.assertEqual(package.find("opf:metadata/dc:language", ns).text, "zh")
            self.assertEqual(package.find("opf:metadata/dc:title", ns).text, example_book()["title"])
            modified = package.find("opf:metadata/opf:meta", ns).text
            self.assertRegex(modified, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            spine = [item.attrib["idref"] for item in package.findall("opf:spine/opf:itemref", ns)]
            self.assertEqual(spine, ["report", "chapter_00001", "chapter_00002", "chapter_00003", "chapter_00004"])
            navigation = ET.fromstring(archive.read("EPUB/nav.xhtml"))
            titles = [item.text for item in navigation.findall(".//h:a", ns)]
            self.assertEqual(titles[1:], [chapter["title"] for chapter in example_book()["chapters"]])
            chapter = ET.fromstring(archive.read("EPUB/chapter_00001.xhtml"))
            self.assertIn("甲 & 乙 <script>故事</script>", "".join(chapter.itertext()))
            self.assertIsNone(chapter.find(".//h:script", ns))
            self.assertIn("本章正文缺失", archive.read("EPUB/chapter_00004.xhtml").decode())
            report = archive.read("EPUB/report.xhtml").decode()
            self.assertIn("未完成", report)
            self.assertIn("待校对：1", report)
            for name in archive.namelist():
                self.assertNotIn(b"SECRET", archive.read(name))

    def test_review_only_book_does_not_claim_complete(self):
        book = {"title": "尚待校对", "chapters": [{"title": "一", "state": "review", "text": "正文"}]}
        for format in ("txt", "md"):
            text = export_book(book, self.directory, format).read_text()
            self.assertIn("待校对（正文已采集", text)
            self.assertNotIn("状态：完整", text)

    def test_different_books_with_the_same_title_keep_separate_exports(self):
        first = {"id": "101", "title": "同名小说" * 50, "chapters": [{"state": "done", "text": "第一本书的内容"}]}
        second = {"id": "102", "title": first["title"], "chapters": [{"state": "done", "text": "第二本书的内容"}]}
        for format in ("txt", "md", "epub"):
            with self.subTest(format=format):
                first_path = export_book(first, self.directory, format)
                original_bytes = first_path.read_bytes()
                second_path = export_book(second, self.directory, format)
                self.assertNotEqual(first_path, second_path)
                self.assertEqual(first_path.read_bytes(), original_bytes)
                self.assertIn("[101]", first_path.name)
                self.assertIn("[102]", second_path.name)
                self.assertLessEqual(len(first_path.name.encode("utf-8")), 255)

    def test_absent_previously_reviewed_chapter_requires_confirmation(self):
        book = {"id": "17", "title": "目录出现变动", "chapters": [{
            "id": "1", "title": "旧目录里的章节", "state": "reviewed",
            "text": "先前已经校对的内容", "review": False, "absent_from_toc": True,
        }]}
        self.assertEqual(summarize(book), {"total": 1, "captured": 1, "missing": 0, "review": 1, "complete": False, "auto_processed": 0})
        for format in ("txt", "md"):
            text = export_book(book, self.directory, format).read_text(encoding="utf-8")
            self.assertIn("本次目录未列出，保留先前内容等待确认", text)
            self.assertIn("先前已经校对的内容", text)
            self.assertNotIn("状态：完整", text)
        with zipfile.ZipFile(export_book(book, self.directory, "epub")) as archive:
            report = archive.read("EPUB/report.xhtml").decode("utf-8")
            self.assertIn("本次目录未列出，保留先前内容等待确认", report)
            self.assertIn("待校对：1", report)
            self.assertIn("先前已经校对的内容", archive.read("EPUB/chapter_00001.xhtml").decode("utf-8"))
        book["chapters"][0].update(state="pending", text="")
        self.assertEqual(summarize(book), {"total": 1, "captured": 0, "missing": 1, "review": 1, "complete": False, "auto_processed": 0})

    def test_safe_filename_and_atomic_failure_preserve_existing_export(self):
        book = {"title": "../CON/坏名字", "chapters": []}
        result = export_book(book, self.directory, "txt")
        previous = result.read_bytes()
        self.assertEqual(result.parent, self.directory)
        with patch("novelist.exporters.os.replace", side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                export_book({**book, "author": "不同作者"}, self.directory, "txt")
        self.assertEqual(result.read_bytes(), previous)
        self.assertEqual(list(self.directory.glob(".novelist-export-*")), [])

    def test_rejects_unsupported_format_and_sanitizes_xml_controls(self):
        with self.assertRaises(ValueError):
            export_book(example_book(), self.directory, "pdf")
        book = {"title": "控制\x00字符", "chapters": [{"state": "done", "text": "甲\x01乙"}]}
        result = export_book(book, self.directory, "epub")
        with zipfile.ZipFile(result) as archive:
            chapter = ET.fromstring(archive.read("EPUB/chapter_00001.xhtml"))
            self.assertIn("甲乙", "".join(chapter.itertext()))

    def test_clean_text_formats_keep_book_and_chapters_without_diagnostics(self):
        book = example_book()
        original = copy.deepcopy(book)
        for format in ("txt", "md"):
            with self.subTest(format=format):
                text = export_book(book, self.directory, format, clean=True).read_text()
                self.assertIn("作者", text)
                self.assertIn("第二章 复查", text)
                self.assertIn("先出现的正文", text)
                self.assertLess(text.index("先出现的正文"), text.index("后出现的正文"))
                self.assertEqual(text.count("【本章正文缺失】"), 2)
                for removed in ("导出检查报告", "导出时间", "采集状态", "采集方式", "来源：", "待校对", "访问受限", "请先登录", "请登录这一提示不应该成为正文", "jjwxc.net", "SECRET"):
                    self.assertNotIn(removed, text)
                self.assertNotIn(" — ", text)
                if format == "md":
                    self.assertIn("&lt;script&gt;", text)
                    self.assertNotIn("<script>", text)
        self.assertEqual(book, original)

    def test_clean_epub_removes_report_and_all_references_but_keeps_valid_navigation(self):
        book = example_book()
        with zipfile.ZipFile(export_book(book, self.directory, "epub", clean=True)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertNotIn("EPUB/report.xhtml", archive.namelist())
            self.assertEqual(archive.infolist()[0].filename, "mimetype")
            self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
            for name in archive.namelist():
                if name.endswith((".xml", ".opf", ".xhtml")):
                    text = archive.read(name).decode()
                    ET.fromstring(text)
                    for removed in ("report.xhtml", 'idref="report"', 'id="report"', "导出检查报告", "采集状态", "采集方式", "来源：", "SECRET", "请先登录"):
                        self.assertNotIn(removed, text)
            ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/", "h": "http://www.w3.org/1999/xhtml"}
            package = ET.fromstring(archive.read("EPUB/package.opf"))
            self.assertEqual(package.find("opf:metadata/dc:title", ns).text, book["title"])
            self.assertEqual(package.find("opf:metadata/dc:creator", ns).text, book["author"])
            self.assertIsNone(package.find("opf:metadata/dc:source", ns))
            manifest = {item.attrib["id"]: item.attrib["href"] for item in package.findall("opf:manifest/opf:item", ns)}
            spine = [item.attrib["idref"] for item in package.findall("opf:spine/opf:itemref", ns)]
            self.assertEqual(spine, ["titlepage", "chapter_00001", "chapter_00002", "chapter_00003", "chapter_00004"])
            self.assertTrue(all("EPUB/" + manifest[item] in archive.namelist() for item in spine))
            navigation = ET.fromstring(archive.read("EPUB/nav.xhtml"))
            links = navigation.findall(".//h:a", ns)
            self.assertEqual([link.text for link in links], [chapter["title"] for chapter in book["chapters"]])
            self.assertTrue(all("EPUB/" + link.attrib["href"] in archive.namelist() for link in links))
            titlepage = "".join(ET.fromstring(archive.read("EPUB/title.xhtml")).itertext())
            self.assertIn(book["title"], titlepage)
            self.assertIn(book["author"], titlepage)
            for number in (3, 4):
                chapter = ET.fromstring(archive.read(f"EPUB/chapter_{number:05d}.xhtml"))
                paragraphs = chapter.findall(".//h:p", ns)
                self.assertEqual([p.text for p in paragraphs], ["【本章正文缺失】"])

    def test_clean_and_report_modes_never_overwrite_each_other_or_legacy_filename(self):
        book = {"id": "x" * 60, "title": "很长的小说标题" * 80, "chapters": [{"title": "第一章", "state": "done", "text": "已保存的正文。"}]}
        for format in ("txt", "md", "epub"):
            with self.subTest(format=format):
                report = export_book(book, self.directory, format)
                previous_report = report.read_bytes()
                clean = export_book(book, self.directory, format, clean=True)
                self.assertNotEqual(report, clean)
                self.assertEqual(report.read_bytes(), previous_report)
                self.assertIn(" · 带报告.", report.name)
                self.assertIn(" · 纯净版.", clean.name)
                self.assertIn("[" + book["id"] + "]", clean.name)
                self.assertLessEqual(len(clean.name.encode()), 255)
                self.assertLessEqual(len(report.name.encode()), 255)
                previous_clean = clean.read_bytes()
                export_book(book, self.directory, format, clean=False)
                self.assertEqual(clean.read_bytes(), previous_clean)
        legacy = self.directory / "旧书 [17].txt"
        legacy.write_text("保留原来不带模式后缀的导出。")
        short_book = {"id": "17", "title": "旧书", "chapters": []}
        export_book(short_book, self.directory, "txt", clean=True)
        export_book(short_book, self.directory, "txt", clean=False)
        self.assertEqual(legacy.read_text(), "保留原来不带模式后缀的导出。")

    def test_clean_mode_rejects_non_boolean_values_without_creating_exports(self):
        for value in (None, 0, 1, "true", "false", [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                export_book(example_book(), self.directory, "txt", clean=value)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_clean_empty_book_epub_still_contains_title_and_valid_spine(self):
        book = {"title": "空书库样本", "author": "作者", "chapters": []}
        with zipfile.ZipFile(export_book(book, self.directory, "epub", clean=True)) as archive:
            self.assertNotIn("EPUB/report.xhtml", archive.namelist())
            package = ET.fromstring(archive.read("EPUB/package.opf"))
            ns = {"opf": "http://www.idpf.org/2007/opf"}
            self.assertEqual([item.attrib["idref"] for item in package.findall("opf:spine/opf:itemref", ns)], ["titlepage"])
            for name in archive.namelist():
                if name.endswith((".xml", ".opf", ".xhtml")):
                    ET.fromstring(archive.read(name))


if __name__ == "__main__":
    unittest.main()
