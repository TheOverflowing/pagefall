"""Local, conservative proofreading of saved chapters.

No language model or network service is used. Layout fixes require OCR geometry
that still matches the current text. Words and names are never guessed. The
caller owns persistence and undo snapshots; this module never mutates a chapter.
"""

import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import tempfile


VERSION = "local-geometry-v1"
ROOT = Path(__file__).resolve().parent.parent
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
_WATERMARK = re.compile(r"(?:@\s*)?无限好文[，,、]?\s*尽在.{0,4}文学城|(?:www\.)?jjwxc\.(?:net|com)", re.I)
_BROKEN_CHARACTERS = re.compile(r"[\uFFFD\uE000-\uF8FF\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]+")
_TERMINAL = re.compile(r"[。！？!?；;：:][”’」』\"']?$", re.U)


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compact(text):
    return re.sub(r"\s", "", text)


def _plain(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _valid_lines(source):
    if not isinstance(source, list) or not source:
        return []
    result = []
    for index, item in enumerate(source, 1):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or "\n" in item["text"]:
            return []
        line = {"text": item["text"].strip(), "source_line": index}
        for key in ("x", "y", "width", "height", "confidence"):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return []
            line[key] = float(value)
        if not (-0.03 <= line["x"] <= 1.03 and -0.03 <= line["y"] <= 1.03 and 0 < line["width"] <= 1.1 and 0 < line["height"] <= 1.1 and 0 <= line["confidence"] <= 1):
            return []
        result.append(line)
    return result


def _overlap(first, second, axis, size):
    intersect = max(0, min(first[axis] + first[size], second[axis] + second[size]) - max(first[axis], second[axis]))
    return intersect / min(first[size], second[size])


def _same_printed_line(first, second):
    return (first["text"] == second["text"] and len(first["text"]) >= 2
            and _overlap(first, second, "x", "width") >= 0.85
            and _overlap(first, second, "y", "height") >= 0.65)


def _joiner(left, right):
    # Preserve a word boundary when two Latin runs meet. Chinese has no spaces
    # between ordinary wrapped lines; existing punctuation is left untouched.
    return " " if re.search(r"[A-Za-z0-9]$", left) and re.match(r"^[A-Za-z0-9]", right) else ""


def _layout(lines):
    changes, retained = [], []
    for line in lines:
        duplicate = next((old for old in retained[-4:] if _same_printed_line(old, line)), None)
        if duplicate is not None:
            changes.append({"kind": "duplicate_ocr_line", "source_line_start": duplicate["source_line"], "source_line_end": line["source_line"], "count": 1, "reason": "相同文字覆盖了同一处印刷位置，已移除重复识别行。"})
            continue
        retained.append(line)
    if not retained:
        return "", changes, retained
    height = statistics.median(line["height"] for line in retained)
    long_lines = [line for line in retained if len(_compact(line["text"])) >= 12] or retained
    char_widths = [line["width"] / len(_compact(line["text"])) for line in long_lines if _compact(line["text"])]
    glyph = statistics.median(char_widths) if char_widths else 0.02
    lefts, rights = sorted(line["x"] for line in long_lines), sorted(line["x"] + line["width"] for line in long_lines)
    left_edge = lefts[int((len(lefts) - 1) * 0.1)]
    right_edge = rights[int((len(rights) - 1) * 0.9)]
    output = retained[0]["text"]
    for previous, line in zip(retained, retained[1:]):
        center_delta = (line["y"] + line["height"] / 2) - (previous["y"] + previous["height"] / 2)
        horizontal_gap = line["x"] - (previous["x"] + previous["width"])
        same_row = abs(center_delta) <= min(line["height"], previous["height"]) * 0.45
        fragment = same_row and -glyph * 0.3 <= horizontal_gap <= glyph * 2.5
        wrapped = (height * 0.6 < center_delta <= height * 1.85
                   and line["x"] <= left_edge + glyph * 0.9
                   and previous["x"] + previous["width"] >= right_edge - glyph * 1.6
                   and len(previous["text"]) >= 8 and not _TERMINAL.search(previous["text"]))
        if fragment or wrapped:
            output += _joiner(previous["text"], line["text"]) + line["text"]
            changes.append({"kind": "join_fragments" if fragment else "soft_line_break", "source_line_start": previous["source_line"], "source_line_end": line["source_line"], "count": 1, "reason": "识别框属于同一印刷行，已合并分段。" if fragment else "满行文字在左边缘按正常行距继续，已合并软换行。"})
        else:
            output += "\n\n" + line["text"]
            changes.append({"kind": "paragraph_break", "source_line_start": previous["source_line"], "source_line_end": line["source_line"], "count": 1, "reason": "保留段落边界，未改写文字。"})
    return output, changes, retained


def _issue(kind, message, **fields):
    return {"kind": kind, "message": message, "severity": "warning", **fields}


def _confidence_issues(lines):
    groups = []
    for line in lines:
        if line["confidence"] >= 0.8 or len(_compact(line["text"])) < 2:
            continue
        if groups and line["source_line"] == groups[-1][-1]["source_line"] + 1:
            groups[-1].append(line)
        else:
            groups.append([line])
    return [_issue("low_ocr_confidence", "这些行的识别置信度较低，请对照原图确认。", source_line_start=group[0]["source_line"], source_line_end=group[-1]["source_line"], minimum_confidence=min(line["confidence"] for line in group)) for group in groups]


def _secondary_issues(lines, secondary):
    if secondary is None:
        return []
    result = []
    primary = {line["source_line"]: line for line in lines}
    for alternative in secondary:
        if not isinstance(alternative, dict) or not isinstance(alternative.get("text"), str):
            continue
        original = primary.get(alternative.get("source_line"))
        if original and _compact(original["text"]) != _compact(alternative["text"]):
            result.append(_issue("ocr_disagreement", "第二次本地识别与原结果不同，未自动替换任何字词。", source_line_start=original["source_line"], source_line_end=original["source_line"], candidate=alternative["text"][:160], candidate_confidence=alternative.get("confidence")))
    return result


def proofread_chapter(chapter, screenshot_paths=None, *, secondary_lines=None):
    """Return text, changes, issues and metadata without editing the input.

    ``secondary_lines`` may contain local rechecks as ``{source_line,text,
    confidence}`` (one-based source indexes). They are suggestions only. The
    returned metadata intentionally never marks a chapter human-reviewed.
    ``raw_text`` is not returned and must be preserved by the caller.
    """
    if not isinstance(chapter, dict) or not isinstance(chapter.get("text", ""), str):
        raise ValueError("章节正文必须是文本。")
    source = chapter.get("text", "")
    source_hash = text_hash(source)
    previous = chapter.get("proofreading")
    if isinstance(previous, dict) and previous.get("version") == VERSION and previous.get("result_hash") == source_hash and secondary_lines is None and chapter.get("state") not in {"blocked", "failed", "pending", "capturing", "reviewed"}:
        return {"text": source, "changes": [], "issues": copy.deepcopy(previous.get("issues", [])), "proofreading": copy.deepcopy(previous), "unchanged": True}
    if not source.strip() or chapter.get("state") in {"blocked", "failed", "pending", "capturing", "reviewed"}:
        metadata = {"version": VERSION, "processed_at": datetime.now(timezone.utc).isoformat(), "source_hash": source_hash, "result_hash": source_hash, "status": "skipped", "auto_processed": False, "human_reviewed": False, "issues_count": 0, "changes_count": 0, "issues": [], "changes": [], "summary": "已跳过不可用或经过人工校对的章节。"}
        return {"text": source, "changes": [], "issues": [], "proofreading": metadata, "unchanged": True}
    lines = _valid_lines(chapter.get("lines"))
    text, changes, issues = source, [], []
    paths = screenshot_paths if screenshot_paths is not None else chapter.get("screenshots", [])
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = list(paths or [])
    geometry_matches = bool(lines) and _plain(source) == _plain("\n".join(line["text"] for line in lines))
    if geometry_matches and len(paths) <= 1:
        text, changes, retained = _layout(lines)
    else:
        retained = lines
        if chapter.get("method") == "ocr" and not lines:
            issues.append(_issue("missing_ocr_geometry", "缺少识别行位置，已保留现有段落。"))
        elif lines and not geometry_matches:
            issues.append(_issue("text_changed_since_ocr", "当前文字已不同于原始识别结果，已保留现有段落。"))
        elif len(paths) > 1:
            issues.append(_issue("multiple_source_images", "本章包含多张原图，需要确认跨图段落。"))
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    normalized = re.sub(r"[ \t]+(?=\n|$)", "", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip("\n")
    if normalized != text:
        changes.append({"kind": "whitespace", "count": 1, "reason": "已统一换行符、清理行尾空格和多余空行。"})
    text = normalized
    issues.extend(_confidence_issues(retained))
    for match in _BROKEN_CHARACTERS.finditer(text):
        issues.append(_issue("unrecognized_characters", "正文仍有无法识别的替代字符或特殊字体编码。", start=match.start(), end=match.end()))
    for match in _WATERMARK.finditer(text):
        issues.append(_issue("possible_watermark", "疑似平台水印，已保留以便确认。", start=match.start(), end=match.end()))
    heading = chapter.get("title", "").strip()
    if heading and any(line.strip() == heading for line in text.splitlines()[:3]):
        issues.append(_issue("possible_heading", "章节标题可能混入正文，已保留以便确认。"))
    spacing = list(re.finditer(f"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])", text))
    if spacing:
        issues.append(_issue("cjk_spacing", "汉字之间的空格可能来自识别误差，已保留以便确认。", occurrences=len(spacing), start=spacing[0].start(), end=spacing[0].end()))
    expected = chapter.get("expected_words")
    actual = len(_compact(text))
    ratio = None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool) and math.isfinite(expected) and expected > 0:
        ratio = actual / expected
        if ratio < 0.8:
            issues.append(_issue("possible_missing_text", "保存的正文明显短于章节标注字数，可能存在缺字或缺段。", expected_words=expected, actual_characters=actual, ratio=round(ratio, 4)))
        elif ratio > 1.25:
            issues.append(_issue("unexpected_length", "保存的正文明显长于标注字数，请检查重复段落或无关内容。", expected_words=expected, actual_characters=actual, ratio=round(ratio, 4)))
    if chapter.get("incomplete") and not any(issue["kind"] == "possible_missing_text" for issue in issues):
        issues.append(_issue("capture_incomplete", "原始采集被标记为不完整，本地格式整理无法补回缺失正文。"))
    issues.extend(_secondary_issues(lines, secondary_lines))
    changed = text != source
    # Report real mutations only, including when geometry already contained the
    # desired spacing. Metadata remains meaningful for idempotent second runs.
    if not changed:
        changes = []
    metadata = {"version": VERSION, "processed_at": datetime.now(timezone.utc).isoformat(), "source_hash": source_hash, "result_hash": text_hash(text), "status": "needs_attention" if issues else "checked", "auto_processed": True, "human_reviewed": False, "issues_count": len(issues), "changes_count": len(changes), "issues": issues, "changes": changes, "offset_units": "unicode_codepoints", "source_characters": len(_compact(source)), "result_characters": actual, "source_lines": len(lines), "paragraphs": len([paragraph for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]), "length_ratio": round(ratio, 4) if ratio is not None else None, "summary": f"已完成本地检查，整理 {len(changes)} 处格式；{len(issues)} 处需确认。"}
    return {"text": text, "changes": changes, "issues": issues, "proofreading": metadata, "unchanged": not changed}


async def recheck_low_confidence(chapter, screenshot_path, *, max_lines=6, binary=None):
    """Optional second local pass over a few uncertain rows, never whole-book.

    Rechecks can expose disagreements but are not independent proof of accuracy.
    This helper returns candidate readings; ``proofread_chapter`` never replaces
    words with them. All temporary crops stay on this Mac and are removed.
    """
    from PIL import Image
    helper = Path(binary) if binary else ROOT / ".build" / "vision-ocr"
    if not helper.is_file():
        raise ValueError("本地 OCR 组件不可用。")
    lines = _valid_lines(chapter.get("lines"))
    selected = sorted((line for line in lines if line["confidence"] < 0.8 and len(line["text"]) >= 4), key=lambda line: line["confidence"])[:max(0, min(int(max_lines), 20))]
    candidates = []
    with Image.open(screenshot_path) as source, tempfile.TemporaryDirectory(prefix="novelist-local-recheck-") as folder:
        width, height = source.size
        for line in selected:
            top = max(0, math.floor((line["y"] - line["height"] * 0.45) * height))
            bottom = min(height, math.ceil((line["y"] + line["height"] * 1.45) * height))
            if bottom <= top:
                continue
            crop = Path(folder) / "line.png"
            source.crop((0, top, width, bottom)).save(crop)
            process = await asyncio.create_subprocess_exec(str(helper), str(crop), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.wait()
                raise
            if process.returncode:
                continue
            try:
                observed = json.loads(stdout).get("lines", [])
            except (ValueError, AttributeError):
                continue
            # Select only rows centered on the intended source line, excluding
            # adjacent text that fell within the crop's padding.
            center = (line["y"] + line["height"] / 2) * height
            target = [row for row in observed if isinstance(row.get("text"), str) and abs(top + (row["y"] + row["height"] / 2) * (bottom - top) - center) <= line["height"] * height * 0.65]
            if target:
                target.sort(key=lambda row: row.get("x", 0))
                candidates.append({"source_line": line["source_line"], "text": "".join(row["text"] for row in target), "confidence": min(row.get("confidence", 0) for row in target)})
    return candidates
