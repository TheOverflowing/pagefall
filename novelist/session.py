"""Jinjiang-only session storage. Secret payloads travel over pipes, never logs."""
import json
import math
from pathlib import Path
import re
import subprocess
import time

from .runtime import DATA_ROOT as ROOT, RESOURCE_ROOT
HELPER = RESOURCE_ROOT / ".build" / "session-store"
MAX_COOKIES = 256
MAX_BYTES = 1_048_576
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*jjwxc\.(?:net|com)\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def normalize_cookies(cookies):
    if not isinstance(cookies, list) or len(cookies) > MAX_COOKIES:
        raise ValueError("晋江登录信息格式无效。")
    try:
        size = len(json.dumps(cookies, allow_nan=False).encode())
    except (TypeError, ValueError, RecursionError):
        raise ValueError("晋江登录信息格式无效。") from None
    if size > MAX_BYTES:
        raise ValueError("登录信息超过大小限制。")
    result = []
    for source in cookies:
        if not isinstance(source, dict):
            raise ValueError("晋江登录信息格式无效。")
        domain = source.get("domain", "")
        if not isinstance(domain, str) or len(domain) > 253:
            raise ValueError("仅接受晋江网站的登录信息。")
        domain = domain.lower()
        host = domain.removeprefix(".")
        if not _DOMAIN.fullmatch(host):
            raise ValueError("仅接受晋江网站的登录信息。")
        name, value, path = source.get("name"), source.get("value"), source.get("path", "/")
        if (not isinstance(name, str) or not name or len(name) > 1024 or _CONTROL.search(name)
                or not isinstance(value, str) or len(value) > 65536 or _CONTROL.search(value)
                or not isinstance(path, str) or not path.startswith("/") or len(path) > 4096 or _CONTROL.search(path)
                or not isinstance(source.get("secure", False), bool) or not isinstance(source.get("httpOnly", False), bool)
                or ("session" in source and not isinstance(source["session"], bool))):
            raise ValueError("晋江登录信息格式无效。")
        # Third-party partitioned cookies are not account login cookies. Do not
        # flatten their partition boundary into a first-party session.
        if "partitionKey" in source and source["partitionKey"] is not None:
            continue
        expires = source.get("expirationDate", source.get("expires", -1))
        if isinstance(expires, bool) or not isinstance(expires, (float, int)) or not math.isfinite(expires) or expires < -1:
            raise ValueError("登录信息有效期无效。")
        if source.get("session"):
            expires = -1
        if expires != -1 and expires < time.time():
            continue
        same_site = {"no_restriction": "None", "unspecified": "Lax", "lax": "Lax", "strict": "Strict", "none": "None"}.get(str(source.get("sameSite", "Lax")).lower())
        if same_site is None:
            raise ValueError("晋江登录信息格式无效。")
        result.append({"name": name, "value": value, "domain": domain, "path": path, "expires": expires if expires > 0 else -1, "secure": bool(source.get("secure")), "httpOnly": bool(source.get("httpOnly")), "sameSite": same_site})
    return result


def save_cookies_sync(cookies):
    clean = normalize_cookies(cookies)
    if not clean:
        raise ValueError("没有找到可连接的晋江登录状态，请先在浏览器中登录晋江。")
    result = subprocess.run([str(HELPER), "set"], input=json.dumps(clean).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise RuntimeError("无法将登录状态保存到 Mac 钥匙串。")


def load_cookies_sync():
    if not HELPER.exists():
        return []
    result = subprocess.run([str(HELPER), "get"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise RuntimeError("无法读取 Mac 钥匙串中的晋江登录状态。")
    return normalize_cookies(json.loads(result.stdout or b"[]"))


def connection_nonce():
    try:
        metadata = json.loads((ROOT / ".local" / "connection-update.json").read_text())
        return str(metadata.get("nonce", ""))
    except (OSError, ValueError):
        return ""
