"""Proofreading fixtures contain invented text; no private novel is embedded."""

import copy
import unittest

from novelist.proofreading import proofread_chapter, text_hash


def line(text, *, x=0, y=0, width=0.95, height=0.02, confidence=1):
    return {"text": text, "x": x, "y": y, "width": width, "height": height, "confidence": confidence}


def chapter(lines, **changes):
    text = "\n".join(item["text"] for item in lines)
    result = {"id": "fixture", "title": "A fixture chapter", "state": "review", "method": "ocr", "text": text, "raw_text": text, "lines": lines, "review": True}
    result.update(changes)
    return result


class ProofreadingTests(unittest.TestCase):
    def test_wraps_join_and_indented_paragraphs_remain_separate_without_rewriting_words(self):
        first = "这是一段为了测试换行整理而虚构的文字，"
        second = "它的后半句仍然在原来的同一段中。"
        third = "另一段从缩进位置开始，应当保留段落。"
        value = chapter([line(first, x=0.08, width=0.9), line(second, y=0.03, width=0.7), line(third, x=0.08, y=0.08, width=0.82)])
        result = proofread_chapter(value)
        self.assertEqual(result["text"], first + second + "\n\n" + third)
        self.assertIn("soft_line_break", [item["kind"] for item in result["changes"]])
        self.assertIn("paragraph_break", [item["kind"] for item in result["changes"]])
        self.assertFalse(result["proofreading"]["human_reviewed"])

    def test_spatially_overlapping_duplicate_is_removed_but_real_repetition_is_kept(self):
        repeated = "这是有意使用相同句子的测试文本。"
        value = chapter([line(repeated, y=0.01), line(repeated, y=0.012), line(repeated, y=0.1)])
        result = proofread_chapter(value)
        self.assertEqual(result["text"], repeated + "\n\n" + repeated)
        duplicates = [item for item in result["changes"] if item["kind"] == "duplicate_ocr_line"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["source_line_end"], 2)

    def test_same_printed_row_fragments_are_joined(self):
        value = chapter([line("测试行前半段", x=0.05, width=0.2), line("以及后半段", x=0.26, width=0.2, y=0.001)])
        result = proofread_chapter(value)
        self.assertEqual(result["text"], "测试行前半段以及后半段")
        self.assertEqual(result["changes"][0]["kind"], "join_fragments")

    def test_sentence_boundary_is_preserved_when_layout_is_ambiguous(self):
        first, second = "这段话正好在满行位置结束。", "下一行也可能属于另一个段落。"
        value = chapter([line(first), line(second, y=0.03)])
        self.assertEqual(proofread_chapter(value)["text"], first + "\n\n" + second)

    def test_manual_edits_are_never_replaced_with_old_ocr_words(self):
        value = chapter([line("这是旧的原始 OCR 识别内容。")], text="人物名字已由我手动修正。")
        result = proofread_chapter(value)
        self.assertEqual(result["text"], value["text"])
        self.assertIn("text_changed_since_ocr", [item["kind"] for item in result["issues"]])

    def test_raw_input_is_immutable_and_result_contains_no_raw_text_overwrite(self):
        value = chapter([line("保持原始识别数据，测试本地规则。"), line("换行整理也不覆盖截图或原文。", y=0.05)])
        original = copy.deepcopy(value)
        result = proofread_chapter(value)
        self.assertEqual(value, original)
        self.assertNotIn("raw_text", result)
        self.assertEqual(result["proofreading"]["source_hash"], text_hash(original["text"]))
        self.assertEqual(result["proofreading"]["result_hash"], text_hash(result["text"]))

    def test_low_confidence_replacement_characters_and_missing_length_are_flags_only(self):
        original = "这是带有�和\ue123字符的测试，人名不应被猜测。"
        value = chapter([line(original, confidence=0.45)], expected_words=1000)
        result = proofread_chapter(value)
        self.assertEqual(result["text"], original)
        kinds = {item["kind"] for item in result["issues"]}
        self.assertTrue({"low_ocr_confidence", "unrecognized_characters", "possible_missing_text"}.issubset(kinds))
        self.assertEqual(result["proofreading"]["issues_count"], len(result["issues"]))
        self.assertEqual(result["proofreading"]["issues"], result["issues"])

    def test_title_watermark_and_cjk_spacing_are_preserved_as_suspicions(self):
        value = chapter([], method="text", title="测试标题", text="测试标题\n\n无限好文，尽在晋江文学城\n\n人 名应当保留。")
        result = proofread_chapter(value)
        self.assertEqual(result["text"], value["text"])
        self.assertTrue({"possible_heading", "possible_watermark", "cjk_spacing"}.issubset({item["kind"] for item in result["issues"]}))

    def test_plain_text_normalizes_whitespace_without_guessing_paragraphs(self):
        value = {"text": "\ufeffFirst line  \r\nSecond line\r\n\r\n\r\nThird", "method": "text", "state": "done"}
        result = proofread_chapter(value)
        self.assertEqual(result["text"], "First line\nSecond line\n\nThird")
        self.assertEqual([item["kind"] for item in result["changes"]], ["whitespace"])
        self.assertEqual(result["proofreading"]["status"], "checked")

    def test_second_pass_disagreement_is_not_silently_used_to_replace_a_name(self):
        value = chapter([line("人物甲叫做林乔，测试中必须保留这个名字。", confidence=0.5)])
        candidate = "人物甲叫做林桥，测试中必须保留这个名字。"
        result = proofread_chapter(value, secondary_lines=[{"source_line": 1, "text": candidate, "confidence": 1}])
        self.assertEqual(result["text"], value["text"])
        issue = next(item for item in result["issues"] if item["kind"] == "ocr_disagreement")
        self.assertEqual(issue["candidate"], candidate)

    def test_repeated_runs_are_idempotent_with_saved_metadata(self):
        value = chapter([line("这是重复运行时不应再次整理的第一段。"), line("这是另一个段落，内容应当保持完全一样。", y=0.05)])
        first = proofread_chapter(value)
        updated = {**value, "text": first["text"], "proofreading": first["proofreading"]}
        second = proofread_chapter(updated)
        self.assertEqual(second["text"], first["text"])
        self.assertEqual(second["changes"], [])
        self.assertEqual(second["proofreading"], first["proofreading"])
        self.assertTrue(second["unchanged"])

    def test_blocked_empty_and_human_reviewed_chapters_are_not_modified(self):
        for value in ({"text": "", "state": "review"}, {"text": "保留这个访问提示", "state": "blocked"}, {"text": "人工校对完成。  ", "state": "reviewed"}):
            with self.subTest(state=value["state"]):
                result = proofread_chapter(value)
                self.assertEqual(result["text"], value["text"])
                self.assertFalse(result["proofreading"]["auto_processed"])
                self.assertEqual(result["proofreading"]["status"], "skipped")

    def test_invalid_geometry_does_not_delete_or_reorder_text(self):
        value = chapter([line("这是第一句。"), line("这是第二句。", y=float("nan"))])
        result = proofread_chapter(value)
        self.assertEqual(result["text"], value["text"])
        self.assertIn("missing_ocr_geometry", {item["kind"] for item in result["issues"]})

    def test_human_review_after_automatic_check_takes_precedence_over_cached_result(self):
        value = chapter([line("这段文字随后经过了人工确认。")])
        first = proofread_chapter(value)
        reviewed = {**value, "text": first["text"], "proofreading": first["proofreading"], "state": "reviewed"}
        result = proofread_chapter(reviewed)
        self.assertFalse(result["proofreading"]["auto_processed"])
        self.assertEqual(result["proofreading"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
