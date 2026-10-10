"""Narrow Python boundary for the pinned, experimental browser rule bundle."""

import asyncio
from pathlib import Path
import re

from .platforms import book_identity, normalize_book_url, normalize_chapter_url, platform_for


BUNDLE = Path(__file__).resolve().parent.parent / "engine" / "dist" / "novelist-rules.js"


def failure(message, pause=True):
    # Imported lazily: Reader dispatches here after its own module is initialized.
    from .browser import CaptureError
    return CaptureError(message, "blocked", pause)


async def invoke(page, method, arguments):
    try:
        result = await asyncio.wait_for(page.evaluate(
            "async ({method,args}) => { try { return {ok:true,value:await window.NovelistRules[method](args)}; } catch { return {ok:false}; } }",
            {"method": method, "args": arguments}), timeout=125)
    except Exception:
        raise failure("此平台的兼容规则暂时无法完成读取，请稍后重试或检查原网站。") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise failure("此平台的兼容规则未能读取内容，可能遇到页面变更、登录或访问限制。")
    return result.get("value")


async def scan(page, url):
    url = normalize_book_url(url)
    spec = platform_for(url)
    if spec.adapter_kind != "upstream" or normalize_book_url(page.url) != url:
        raise failure("页面与当前作品不一致，请重新添加作品主页链接。")
    if not BUNDLE.is_file():
        raise failure("多平台读取组件尚未准备好，请重新构建拾页。")
    try:
        await page.add_script_tag(path=str(BUNDLE))
        await page.wait_for_function("!!window.NovelistRules", timeout=10000)
    except Exception:
        raise failure("这个网站暂时无法加载兼容规则。") from None
    raw = await invoke(page, "scan", {"rule_id": spec.rule_id})
    if not isinstance(raw, dict) or raw.get("rule_id") != spec.rule_id or not isinstance(raw.get("title"), str) or not raw["title"].strip():
        raise failure("尚未识别到作品信息，请检查链接是否为作品目录页。")
    rows = raw.get("chapters")
    if not isinstance(rows, list) or not 0 < len(rows) <= 20000:
        raise failure("尚未读取到完整目录，请在原网站确认目录已显示。")
    chapters, identifiers = [], set()
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r"c_[0-9a-f]{24}", str(row.get("id", ""))) or row["id"] in identifiers:
            raise failure("兼容规则返回了重复或无效的章节，已停止导入。")
        try:
            chapter_url = normalize_chapter_url(url, row["url"])
        except (KeyError, TypeError, ValueError):
            raise failure("目录包含无法读取的链接，已停止导入以避免遗漏章节。") from None
        identifiers.add(row["id"])
        blocked = bool(row.get("blocked") or row.get("vip") and row.get("paid") is not True)
        chapters.append({"id": row["id"], "title": str(row.get("title") or f"第 {len(chapters)+1} 章")[:500],
                         "url": chapter_url, "book_url": url, "platform": spec.id, "rule_id": spec.rule_id,
                         "ordinal": len(chapters)+1, "expected_words": 0, "vip": bool(row.get("vip")),
                         "paid": row.get("paid") is True, "locked": blocked,
                         "state": "blocked" if blocked else "pending",
                         "error": "此章节尚未确认可读，请在原网站确认登录或订阅。" if blocked else ""})
    return {"id": book_identity(url), "url": url, "title": raw["title"].strip()[:500],
            "author": str(raw.get("author") or "")[:200], "platform": spec.id, "platform_name": spec.name,
            "chapters": chapters, "experimental": True}


async def read(page, chapter):
    spec = platform_for(chapter["book_url"])
    if normalize_book_url(page.url) != normalize_book_url(chapter["book_url"]) or spec.rule_id != chapter.get("rule_id"):
        raise failure("当前目录已变化，请重新读取目录后继续。")
    raw = await invoke(page, "read", {"chapter_id": chapter["id"]})
    if not isinstance(raw, dict):
        raise failure("兼容规则返回的正文格式无效。")
    if raw.get("gate"):
        raise failure("原网站要求登录、订阅或验证，当前没有可读取的正文。")
    if raw.get("needs_ocr") or raw.get("images"):
        raise failure("本章包含图片或特殊字体；此平台的兼容规则暂不支持 OCR，已保留进度。")
    text = raw.get("text")
    if not isinstance(text, str) or len(text.strip()) < 20 or len(text) > 2_000_000:
        raise failure("未读取到足够的章节正文，请检查原网站。")
    if re.search(r"[\uE000-\uF8FF\U000F0000-\U000FFFFD\U00100000-\U0010FFFD\uFFFD]", text):
        raise failure("正文含有无法确认的字符，此平台需要专门的 OCR 适配。")
    return {"text": text.strip(), "raw_text": text.strip(), "method": "upstream_text", "review": True,
            "incomplete": False, "screenshots": [], "error": "兼容规则读取的正文已保存，请校对内容与完整性。"}
