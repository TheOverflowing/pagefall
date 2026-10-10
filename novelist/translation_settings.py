"""Local translation preferences and narrowly scoped macOS Keychain access.

This module performs no network requests. Public settings never contain an API
key, an encoded key, a key suffix, or provider response data.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid


from .runtime import DATA_ROOT as ROOT, RESOURCE_ROOT
SETTINGS_PATH = ROOT / ".local" / "translation-settings.json"
KEY_HELPER = RESOURCE_ROOT / ".build" / "api-key-store"
PROVIDERS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "default_model": "deepseek-flash"},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "default_model": "deepseek/deepseek-v4-flash"},
}
DEFAULT_SETTINGS = {"provider": "deepseek", "model": "deepseek-flash", "source_language": "auto", "target_language": "zh-Hans"}
# Suggested UI choices; normalized language tags also allow future additions.
SOURCE_LANGUAGES = ("auto", "ja", "zh", "en", "ko", "fr", "de", "es")
TARGET_LANGUAGES = ("zh-Hans", "zh-Hant", "en", "ja", "ko", "fr", "de", "es")
_FIELDS = frozenset(DEFAULT_SETTINGS)
_LOCK = threading.RLock()


class TranslationSettingsError(ValueError):
    """An input/configuration error safe to show without including input data."""


class TranslationKeychainError(RuntimeError):
    """A sanitized failure of this application's own Keychain helper."""


def _provider(value):
    if not isinstance(value, str) or value not in PROVIDERS:
        raise TranslationSettingsError("请选择 DeepSeek 或 OpenRouter。")
    return value


def _language(value, *, source):
    if not isinstance(value, str):
        raise TranslationSettingsError("语言设置无效。")
    value = value.strip()
    if source and value.lower() == "auto":
        return "auto"
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}", value):
        raise TranslationSettingsError("请输入有效语言代码，例如 ja、en 或 zh-Hans。")
    parts = value.split("-")
    return "-".join([parts[0].lower()] + [part.title() if len(part) == 4 and part.isalpha() else part.upper() if len(part) == 2 and part.isalpha() else part.lower() for part in parts[1:]])


def normalize_language(value, *, source=False):
    """Return a bounded, path-safe language tag; only sources accept ``auto``.

    This accepts a simple BCP 47 subset (language, optional script/region). It
    validates syntax, not language recognition by a selected provider/model.
    """
    return _language(value, source=source)


def validate_target(value):
    return normalize_language(value)


def validate_source(value):
    return normalize_language(value, source=True)


def _model(provider, value):
    if not isinstance(value, str):
        raise TranslationSettingsError("请输入模型 ID。")
    value = value.strip()
    if provider == "deepseek":
        valid = re.fullmatch(r"deepseek-[A-Za-z0-9][A-Za-z0-9._:-]{0,150}", value)
    else:
        valid = re.fullmatch(r"~?[A-Za-z0-9][A-Za-z0-9_.-]{0,79}/[A-Za-z0-9][A-Za-z0-9._:+-]{0,159}", value)
    if not valid:
        raise TranslationSettingsError("模型 ID 格式无效，请填写服务商提供的模型名称。")
    return value


def _validate_settings(value):
    provider = _provider(value.get("provider"))
    return {"provider": provider, "model": _model(provider, value.get("model")),
            "source_language": normalize_language(value.get("source_language"), source=True),
            "target_language": normalize_language(value.get("target_language"))}


def _validate_key(value):
    if not isinstance(value, str):
        raise TranslationSettingsError("API Key 格式无效。")
    value = value.strip()
    if not 12 <= len(value) <= 4096 or any(not 33 <= ord(char) <= 126 for char in value):
        raise TranslationSettingsError("API Key 格式无效，请重新复制完整密钥。")
    return value


def _read_config():
    try:
        if SETTINGS_PATH.stat().st_size > 16384:
            raise TranslationSettingsError("翻译设置文件异常，请重新保存设置。")
        payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_SETTINGS)
    except (OSError, ValueError, UnicodeError):
        raise TranslationSettingsError("无法读取翻译设置，请重新保存设置。") from None
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        # A configuration file is never a fallback credential store.
        raise TranslationSettingsError("翻译设置文件格式无效，请重新保存设置。")
    return _validate_settings(payload)


def _helper(command, provider, *, key=None):
    provider = _provider(provider)
    if not KEY_HELPER.is_file():
        if command == "has":
            return b'{"has_key":false}'
        raise TranslationKeychainError("未找到钥匙串组件，请重新构建拾页后再保存 API Key。")
    payload = json.dumps({"key": key}, ensure_ascii=True).encode("utf-8") if key is not None else None
    try:
        result = subprocess.run([str(KEY_HELPER), command, provider], input=payload,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    except Exception:
        raise TranslationKeychainError("无法访问拾页的 API Key，请检查 Mac 钥匙串后重试。") from None
    if result.returncode != 0:
        # OS/helper diagnostics are intentionally not interpolated: subprocess
        # exceptions and stderr could otherwise expose a supplied API key.
        raise TranslationKeychainError("API Key 钥匙串操作未完成，请稍后重试。")
    return result.stdout


def has_provider_key(provider):
    provider = _provider(provider)
    with _LOCK:
        try:
            result = json.loads(_helper("has", provider))
            if not isinstance(result, dict) or not isinstance(result.get("has_key"), bool):
                raise ValueError()
            return result["has_key"]
        except TranslationKeychainError:
            raise
        except Exception:
            raise TranslationKeychainError("暂时无法确认 API Key 是否已保存。") from None


def _public(settings):
    return {**settings, "base_url": PROVIDERS[settings["provider"]]["base_url"],
            "has_key": has_provider_key(settings["provider"])}


def load_settings():
    """Return non-secret preferences and key presence, without loading key data."""
    with _LOCK:
        return _public(_read_config())


def save_settings(patch, api_key=None):
    """Save a preference patch; optional api_key is write-only, blank keeps it.

    A provider change with no model supplied chooses that provider's documented
    default. Validation completes before any Keychain or configuration write.
    """
    if not isinstance(patch, dict):
        raise TranslationSettingsError("翻译设置格式无效。")
    values = dict(patch)
    if "api_key" in values:
        if api_key is not None:
            raise TranslationSettingsError("API Key 不能重复提供。")
        api_key = values.pop("api_key")
    if set(values) - _FIELDS:
        raise TranslationSettingsError("翻译设置包含不支持的字段。")
    with _LOCK:
        try:
            current = _read_config()
        except TranslationSettingsError:
            current = dict(DEFAULT_SETTINGS)
        if "provider" in values and values["provider"] != current["provider"] and "model" not in values:
            values["model"] = PROVIDERS[_provider(values["provider"])]["default_model"]
        settings = _validate_settings({**current, **values})
        if api_key is not None and not isinstance(api_key, str):
            raise TranslationSettingsError("API Key 格式无效。")
        secret = _validate_key(api_key) if isinstance(api_key, str) and api_key.strip() else None
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = SETTINGS_PATH.with_name("." + SETTINGS_PATH.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(settings, stream, ensure_ascii=False, indent=2)
            if secret is not None:
                _helper("set", settings["provider"], key=secret)
            temporary.replace(SETTINGS_PATH)
        except TranslationKeychainError:
            raise
        except OSError:
            raise TranslationSettingsError("无法保存翻译设置，请检查项目目录。") from None
        finally:
            temporary.unlink(missing_ok=True)
        return _public(settings)


def load_provider_key(provider):
    """Internal translation-call use only. Never expose this through a GET API."""
    provider = _provider(provider)
    with _LOCK:
        payload = _helper("get", provider)
    if not payload:
        return None
    try:
        return _validate_key(payload.decode("utf-8"))
    except (UnicodeError, TranslationSettingsError):
        raise TranslationKeychainError("已保存的 API Key 无法读取，请重新保存。") from None


def delete_provider_key(provider):
    """Delete only the selected provider's item owned by this application."""
    provider = _provider(provider)
    with _LOCK:
        _helper("delete", provider)
