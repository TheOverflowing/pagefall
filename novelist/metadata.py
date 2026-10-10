"""Lazy public Jinjiang cover lookup; no account cookies or private APIs.

Only a requested book is fetched. Redirects remain on the explicit public-page
and image-host allowlists, and both compressed and decoded bodies are bounded.
"""

import asyncio
from html.parser import HTMLParser
from io import BytesIO
import json
import ipaddress
import os
from pathlib import Path
import re
import ssl
import socket
import time
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.error import HTTPError, URLError
import uuid
import zlib

from PIL import Image


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
_IMAGE_HOST = re.compile(r"(?:[is]\d+-static|bbs-static|static|img\d*)\.jjwxc\.(?:net|com)\Z")
# These exact public hosts were observed in Jinjiang's noveldefaultimage tags
# or verified image-CDN redirects from those tags.
# They cannot be supplied by API callers; the API still accepts only a book ID.
_EXTERNAL_IMAGE_HOSTS = {"pic1.imgdb.cn", "files.superbed.cc", "img11.360buyimg.com", "image.58sb.cn", "tanxuling.com", "wx4.sinaimg.cn"}
_IMAGE_MIME = {"JPEG": {"image/jpeg", "image/jpg"}, "PNG": {"image/png"}, "WEBP": {"image/webp"}, "GIF": {"image/gif"}}
_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}
CACHE_SCHEMA = 2


class CoverRetryableError(Exception):
    def __init__(self, retry_after=15):
        self.retry_after = max(1, int(retry_after))
        super().__init__("封面暂时未能加载，稍后可重试。")


def normalize_book_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,12}", value) or int(value) < 1:
        raise ValueError("作品 ID 无效。")
    return str(int(value))


def normalize_cover_url(value, base="https://www.jjwxc.net/"):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 for char in value):
        raise ValueError("封面地址无效。")
    parsed = urlparse(urljoin(base, value.strip()))
    official = bool(_IMAGE_HOST.fullmatch(parsed.hostname or ""))
    external = parsed.hostname in _EXTERNAL_IMAGE_HOSTS
    if (parsed.scheme not in ({"http", "https"} if official else {"https"}) or not (official or external)
            or parsed.username is not None or parsed.password is not None or parsed.port not in {None, 80, 443}
            or not parsed.path or parsed.params):
        raise ValueError("封面地址不属于已验证的公开图片来源。")
    return urlunparse(("https", parsed.hostname, parsed.path, "", parsed.query, ""))


def _public_page_url(value):
    parsed = urlparse(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"www.jjwxc.net", "www.jjwxc.com"}
            or parsed.path != "/onebook.php" or parsed.username is not None or parsed.password is not None
            or parsed.port not in {None, 80, 443} or parsed.params):
        raise ValueError("仅允许晋江公开作品页。")
    return urlunparse(("https", parsed.hostname, parsed.path, "", parsed.query, ""))


class _CoverParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.explicit, self.social = [], []
        self.has_book = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if attrs.get("id") == "oneboolt":
            self.has_book = True
        if tag == "img" and "noveldefaultimage" in attrs.get("class", "").split():
            self.explicit.extend(attrs.get(name, "") for name in ("src", "data-src") if attrs.get(name))
        if tag == "meta" and (attrs.get("property") or attrs.get("name", "")).lower() in {"og:image", "og:novel:image", "twitter:image"}:
            self.social.append(attrs.get("content", ""))


def extract_cover_url(html, page_url):
    parser = _CoverParser()
    parser.feed(html)
    for candidate in parser.explicit + parser.social:
        try:
            return normalize_cover_url(candidate, page_url)
        except ValueError:
            continue
    return None


def cover_candidates(html, page_url, identifier):
    parser = _CoverParser()
    parser.feed(html)
    result = []
    for candidate in parser.explicit + parser.social:
        try:
            url = normalize_cover_url(candidate, page_url)
            if url not in result:
                result.append(url)
        except ValueError:
            continue
    # Jinjiang exposes its own title/author cover even when an author's chosen
    # external image is unavailable. This endpoint was verified on real books;
    # use it only after a public book page or explicit cover tag was found.
    if parser.has_book or parser.explicit:
        fallback = f"https://i9-static.jjwxc.net/novelimage.php?novelid={identifier}"
        if fallback not in result:
            result.append(fallback)
    return result


def _image_target(value):
    url = normalize_cover_url(value)
    parsed = urlparse(url)
    if parsed.hostname in _EXTERNAL_IMAGE_HOSTS:
        addresses = {ipaddress.ip_address(entry[4][0]) for entry in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
        benchmark = ipaddress.ip_network("198.18.0.0/15")
        proxy_route = any(address.is_global for address in addresses) and any(address.version == 4 and address in benchmark for address in addresses)
        if not addresses or not all(address.is_global or (proxy_route and address.version == 4 and address in benchmark) for address in addresses):
            # Only the observed proxy-DNS pair (a public address plus a reserved
            # 198.18/15 synthetic IPv4 route) is accepted for these fixed
            # hosts. HTTPS still verifies their certificates. Real private,
            # loopback and link-local targets are never allowed.
            raise ValueError("外链图片没有解析到可确认的公网地址。")
    return url


def _decode_body(payload, encoding, limit):
    encoding = (encoding or "identity").strip().lower()
    if len(payload) > limit:
        raise ValueError("封面响应超过大小限制。")
    if encoding in {"", "identity"}:
        return payload
    if encoding not in {"gzip", "deflate"}:
        raise ValueError("不支持的响应编码。")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
    result = decoder.decompress(payload, limit + 1)
    if len(result) > limit or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
        raise ValueError("封面响应超过大小限制或内容不完整。")
    return result


class _ScopedRedirect(HTTPRedirectHandler):
    max_redirections = 5

    def __init__(self, validator):
        super().__init__()
        self.validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, self.validator(newurl))


def _ssl_context():
    context = ssl.create_default_context()
    # Python.org macOS installations may lack their optional certificate bundle.
    # Use the system's public CA bundle; certificate verification stays enabled.
    if not context.cert_store_stats().get("x509_ca") and Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations("/etc/ssl/cert.pem")
    return context


def _fetch_bytes(url, kind, referer):
    validator = _image_target if kind == "image" else _public_page_url
    url = validator(url)
    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_HTML_BYTES
    opener = build_opener(_ScopedRedirect(validator), HTTPSHandler(context=_ssl_context()))
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Novelist/0.2)",
        "Accept-Encoding": "identity",
        "Accept": "image/png,image/jpeg,image/webp,image/gif" if kind == "image" else "text/html",
    }
    if urlparse(url).hostname not in _EXTERNAL_IMAGE_HOSTS:
        headers["Referer"] = referer
    request = Request(url, headers=headers)
    with opener.open(request, timeout=18) as response:
        validator(response.url)
        mime = response.headers.get_content_type().lower()
        if kind == "image" and mime not in set().union(*_IMAGE_MIME.values(), {"application/octet-stream"}):
            raise ValueError("封面返回的不是支持的图片格式。")
        if kind == "page" and mime not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("作品页返回格式无效。")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > limit:
            raise ValueError("封面响应超过大小限制。")
        payload = _decode_body(response.read(limit + 1), response.headers.get("Content-Encoding"), limit)
        charset = response.headers.get_content_charset() or "gb18030"
    return payload, mime, charset


def _image_extension(payload, mime):
    with Image.open(BytesIO(payload)) as image:
        image_format = image.format
        if image_format not in _IMAGE_MIME or mime not in set().union(*_IMAGE_MIME.values(), {"application/octet-stream"}):
            raise ValueError("封面没有包含支持的真实图片。")
        # The official novelimage endpoint labels valid PNG bytes image/jpeg.
        # Trust the bounded raster decoder for the extension, never an HTML/SVG
        # payload or the possibly inaccurate Content-Type/URL suffix alone.
        if image.width < 1 or image.height < 1 or image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("封面图片尺寸无效。")
        image.verify()
    with Image.open(BytesIO(payload)) as image:
        image.load()
    return _EXTENSIONS[image_format]


def _atomic_write(path, payload):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class CoverCache:
    def __init__(self, cache_dir, *, concurrency=2, request_interval=0.35, ttl=7 * 86400, negative_ttl=900, transient_ttl=15, max_pending=64, retry_delay=0.7):
        self.directory = Path(cache_dir)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ttl, self.negative_ttl, self.max_pending = ttl, negative_ttl, max_pending
        self.transient_ttl, self.retry_delay = transient_ttl, retry_delay
        self.request_interval = request_interval
        self._semaphore = asyncio.Semaphore(concurrency)
        self._request_lock = asyncio.Lock()
        self._last_request = 0.0
        self._inflight = {}

    async def get(self, book_id, *, refresh=False):
        identifier = normalize_book_id(book_id)
        task = self._inflight.get(identifier)
        if task is None:
            if len(self._inflight) >= self.max_pending:
                raise CoverRetryableError(2)
            task = asyncio.create_task(self._get(identifier, refresh=refresh))
            self._inflight[identifier] = task
            task.add_done_callback(lambda finished: self._inflight.pop(identifier, None) if self._inflight.get(identifier) is finished else None)
        return await asyncio.shield(task)

    async def _request(self, url, kind, referer):
        async with self._request_lock:
            delay = self.request_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()
        return await asyncio.to_thread(_fetch_bytes, url, kind, referer)

    async def _request_with_retry(self, url, kind, referer):
        for attempt in range(2):
            try:
                return await self._request(url, kind, referer)
            except (OSError, URLError) as exc:
                retryable = not isinstance(exc, HTTPError) or exc.code in {408, 429, 500, 502, 503, 504}
                if attempt or not retryable:
                    raise
                await asyncio.sleep(self.retry_delay)

    async def _get(self, identifier, *, refresh=False):
        stale = next((self.directory / (identifier + extension) for extension in _EXTENSIONS.values() if (self.directory / (identifier + extension)).is_file()), None)
        now = time.time()
        if not refresh and stale is not None and now - stale.stat().st_mtime < self.ttl:
            return stale
        marker = self.directory / (identifier + ".json")
        try:
            status = json.loads(marker.read_text())
            if not refresh and status.get("version") == CACHE_SCHEMA and now < float(status["retry_after"]):
                if stale is not None or status.get("reason") == "missing":
                    return stale
                raise CoverRetryableError(max(1, int(status["retry_after"] - now)))
        except (OSError, ValueError, TypeError, KeyError):
            pass
        async with self._semaphore:
            page_url = f"https://www.jjwxc.net/onebook.php?novelid={identifier}"
            try:
                html, _mime, charset = await self._request_with_retry(page_url, "page", page_url)
                candidates = cover_candidates(html.decode(charset, errors="replace"), page_url, identifier)
                if not candidates:
                    _atomic_write(marker, json.dumps({"version": CACHE_SCHEMA, "reason": "missing", "retry_after": time.time() + self.negative_ttl}).encode())
                    return stale
                for index, cover_url in enumerate(candidates):
                    try:
                        payload, mime, _charset = await self._request_with_retry(cover_url, "image", page_url)
                        extension = await asyncio.to_thread(_image_extension, payload, mime)
                        break
                    except Exception:
                        if index + 1 == len(candidates):
                            raise
                target = self.directory / (identifier + extension)
                await asyncio.to_thread(_atomic_write, target, payload)
                if stale is not None and stale != target:
                    stale.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                return target
            except Exception:
                _atomic_write(marker, json.dumps({"version": CACHE_SCHEMA, "reason": "temporary", "retry_after": time.time() + self.transient_ttl}).encode())
                if stale is not None:
                    return stale
                raise CoverRetryableError(self.transient_ttl) from None
