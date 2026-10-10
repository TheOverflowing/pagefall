"""Chrome Native Messaging host. Never print cookie data or unframed stdout."""

import errno
import json
import math
import re
import struct
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .session import MAX_COOKIES, normalize_cookies


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 4096
BACKEND_IMPORT_URL = "http://127.0.0.1:8765/api/account/import"
ALLOWED_ORIGIN = "chrome-extension://ebiomijpkjmejjdbpokfcjmdjlopedkk/"
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*jjwxc\.(?:net|com)\Z")


class ProtocolError(ValueError):
    pass


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ProtocolError("连接消息不完整。")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        header += _read_exact(stream, 4 - len(header))
    length = struct.unpack("=I", header)[0]
    if not 0 < length <= MAX_MESSAGE_BYTES:
        raise ProtocolError("连接消息大小无效。")
    try:
        message = json.loads(_read_exact(stream, length).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ProtocolError("连接消息格式无效。") from None
    if not isinstance(message, dict):
        raise ProtocolError("连接消息格式无效。")
    return message


def write_message(stream, message):
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("连接响应过大。")
    stream.write(struct.pack("=I", len(payload)))
    stream.write(payload)
    stream.flush()


def validate_cookies(cookies):
    if not isinstance(cookies, list) or not 0 < len(cookies) <= MAX_COOKIES:
        raise ProtocolError("没有可连接的晋江登录数据，或数据数量异常。")
    for cookie in cookies:
        if not isinstance(cookie, dict):
            raise ProtocolError("晋江登录数据格式无效。")
        domain = cookie.get("domain")
        if not isinstance(domain, str) or len(domain) > 253 or not _DOMAIN.fullmatch(domain.lower().removeprefix(".")):
            raise ProtocolError("连接只允许晋江站点。")
        name, value, path = cookie.get("name"), cookie.get("value"), cookie.get("path")
        if (
            not isinstance(name, str) or not 0 < len(name) <= 1024
            or not isinstance(value, str) or len(value) > 65536
            or not isinstance(path, str) or not path.startswith("/") or len(path) > 4096
            or not isinstance(cookie.get("secure"), bool)
            or not isinstance(cookie.get("httpOnly"), bool)
            or not isinstance(cookie.get("session"), bool)
            or cookie.get("sameSite") not in {"no_restriction", "lax", "strict", "unspecified"}
        ):
            raise ProtocolError("晋江登录数据格式无效。")
        expiry = cookie.get("expirationDate")
        if expiry is not None and (isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry < 0):
            raise ProtocolError("晋江登录数据有效期无效。")
    try:
        if not normalize_cookies(cookies):
            raise ValueError()
    except (ValueError, TypeError):
        raise ProtocolError("没有可连接的晋江登录数据，或数据格式无效。") from None
    return cookies


def import_backend_session(cookies):
    # The backend holds its account-operation guard for the entire import. A
    # separate busy-state check followed by a local Keychain write would race
    # new downloads. Requiring the running app also avoids startup races.
    opener = build_opener(ProxyHandler({}))
    try:
        request = Request(BACKEND_IMPORT_URL, data=json.dumps({"cookies": cookies}, allow_nan=False).encode(), headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST")
        with opener.open(request, timeout=35) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ProtocolError("拾页未返回有效结果，请重试。")
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ProtocolError("拾页未返回有效结果，请重试。")
    except HTTPError as exc:
        # Never display an arbitrary backend body: it could contain submitted
        # authentication data after a future validation or debugging change.
        if exc.code == 409:
            raise ProtocolError("拾页正在处理任务，请暂停并等待当前操作结束后再连接 Chrome。") from None
        if exc.code == 404:
            raise ProtocolError("请更新并重新打开拾页，再连接 Chrome。") from None
        raise ProtocolError("连接未保存，请回到拾页检查后重试。") from None
    except URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError) or getattr(exc.reason, "errno", None) in {errno.ECONNREFUSED}:
            raise ProtocolError("请先打开拾页，再连接 Chrome。") from None
        raise ProtocolError("暂时无法连接拾页，请稍后重试。") from None
    except (OSError, ValueError):
        raise ProtocolError("暂时无法确认连接结果，请回到拾页检查后重试。") from None


def handle_message(message):
    try:
        if not isinstance(message, dict):
            raise ProtocolError("连接消息格式无效。")
        action = message.get("action")
        if action == "ping":
            return {"ok": True, "message": "拾页本机连接器已就绪。"}
        if action != "connect":
            raise ProtocolError("不支持此连接操作。")
        cookies = validate_cookies(message.get("cookies"))
        import_backend_session(cookies)
        return {"ok": True, "message": "连接已保存。拾页正在验证登录并同步书架。"}
    except ProtocolError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception:
        # Storage/subprocess exceptions can contain input; never echo them.
        return {"ok": False, "message": "未能保存连接，请打开拾页后重试。"}


def main(stdin=None, stdout=None, argv=None):
    source = sys.stdin.buffer if stdin is None else stdin
    destination = sys.stdout.buffer if stdout is None else stdout
    arguments = sys.argv[1:] if argv is None else argv
    # Chrome supplies the initiating extension origin as argv[1]. The manifest
    # allowed_origins is the browser boundary; check it here as defense in depth.
    if arguments != [ALLOWED_ORIGIN]:
        write_message(destination, {"ok": False, "message": "连接来源无效，请从拾页 Chrome 扩展重试。"})
        return 1
    while True:
        try:
            message = read_message(source)
            if message is None:
                return 0
            write_message(destination, handle_message(message))
        except ProtocolError as exc:
            write_message(destination, {"ok": False, "message": str(exc)})
            return 1
        except (BrokenPipeError, OSError):
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
