"""Translation preference and credential boundary checks, with no real keys.

All helper calls use an in-memory fake. These tests never open macOS Keychain,
create a real credential, or contact a provider.
"""

import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from novelist import translation_settings as settings


class TranslationSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="novelist-translation-settings-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "preferences" / "translation-settings.json"
        self.helper = self.root / "fake-api-key-store"
        self.helper.touch()
        self.keys = {}
        self.calls = []
        for name, value in (("SETTINGS_PATH", self.path), ("KEY_HELPER", self.helper)):
            mocked = patch.object(settings, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        runner_patch = patch.object(settings.subprocess, "run", side_effect=self.run_helper)
        self.runner = runner_patch.start()
        self.addCleanup(runner_patch.stop)

    def run_helper(self, args, **kwargs):
        self.calls.append((args, kwargs))
        self.assertEqual(args[0], str(self.helper))
        self.assertEqual(len(args), 3)
        command, provider = args[1:]
        self.assertIn(provider, settings.PROVIDERS)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(kwargs["timeout"], 30)
        data = b""
        if command == "set":
            payload = json.loads(kwargs["input"])
            self.assertEqual(set(payload), {"key"})
            self.keys[provider] = payload["key"]
        elif command == "has":
            data = json.dumps({"has_key": provider in self.keys}).encode()
        elif command == "get":
            data = self.keys.get(provider, "").encode()
        elif command == "delete":
            self.keys.pop(provider, None)
        else:
            self.fail("Unrecognized helper command")
        return subprocess.CompletedProcess(args, 0, data, b"")

    def test_default_read_reports_presence_without_reading_key_or_creating_file(self):
        self.keys["deepseek"] = "fixture-deepseek-secret"
        result = settings.load_settings()
        self.assertEqual(result, {**settings.DEFAULT_SETTINGS,
                                 "base_url": settings.PROVIDERS["deepseek"]["base_url"], "has_key": True})
        self.assertEqual([call[0][1] for call in self.calls], ["has"])
        self.assertFalse(self.path.exists())
        self.assertNotIn("fixture-deepseek-secret", json.dumps(result))

    def test_save_keeps_secret_only_in_helper_stdin_and_config_is_private(self):
        fake_key = "fixture-not-a-real-api-key"
        result = settings.save_settings({"api_key": fake_key, "source_language": "JA", "target_language": "zh-hans"})
        self.assertTrue(result["has_key"])
        self.assertEqual(self.keys, {"deepseek": fake_key})
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved, {**settings.DEFAULT_SETTINGS, "source_language": "ja"})
        self.assertNotIn(fake_key, self.path.read_text())
        self.assertNotIn(fake_key, json.dumps(result))
        self.assertNotIn(fake_key, str([call[0] for call in self.calls]))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertNotIn("get", [call[0][1] for call in self.calls])

    def test_provider_change_resets_default_model_and_keeps_keys_separate(self):
        settings.save_settings({"provider": "deepseek", "model": "deepseek-v4-pro"}, "fixture-deepseek-key")
        changed = settings.save_settings({"provider": "openrouter"}, "fixture-openrouter-key")
        self.assertEqual(changed["model"], settings.PROVIDERS["openrouter"]["default_model"])
        self.assertEqual(changed["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(settings.load_provider_key("deepseek"), "fixture-deepseek-key")
        self.assertEqual(settings.load_provider_key("openrouter"), "fixture-openrouter-key")
        changed = settings.save_settings({"provider": "deepseek"})
        self.assertEqual(changed["model"], settings.PROVIDERS["deepseek"]["default_model"])

    def test_blank_key_preserves_existing_key_and_delete_is_provider_scoped(self):
        self.keys.update(deepseek="fixture-deepseek-key", openrouter="fixture-openrouter-key")
        result = settings.save_settings({"api_key": " \n\t "})
        self.assertTrue(result["has_key"])
        self.assertNotIn("set", [call[0][1] for call in self.calls])
        settings.delete_provider_key("deepseek")
        self.assertEqual(self.keys, {"openrouter": "fixture-openrouter-key"})
        self.assertIsNone(settings.load_provider_key("deepseek"))
        self.assertFalse(settings.load_settings()["has_key"])
        settings.delete_provider_key("deepseek")  # Missing is idempotent.

    def test_invalid_inputs_do_not_mutate_config_or_keychain(self):
        settings.save_settings({}, "fixture-original-key")
        original = self.path.read_bytes()
        initial_keys = dict(self.keys)
        for value in (
            [], {"provider": "other"}, {"provider": "openrouter", "model": "deepseek-flash"},
            {"model": "https://example.test/api"}, {"model": "deepseek-flash\nAuthorization: invalid"},
            {"source_language": "../../en"}, {"target_language": "auto"},
            {"target_language": "en/../../other"}, {"target_language": None},
            {"api_key": True}, {"api_key": "short"}, {"api_key": "fixture-with\n-newline"},
            {"base_url": "https://example.test"}, {"has_key": False}, {"delete_key": True},
        ):
            with self.subTest(value=value):
                self.calls.clear()
                with self.assertRaises(settings.TranslationSettingsError):
                    settings.save_settings(value)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(self.keys, initial_keys)

    def test_duplicate_key_arguments_are_rejected_before_access(self):
        with self.assertRaises(settings.TranslationSettingsError):
            settings.save_settings({"api_key": "fixture-first-key"}, "fixture-second-key")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.path.exists())

    def test_language_normalization_is_reused_by_settings_and_path_safe(self):
        for raw, normalized in (("zh-hans", "zh-Hans"), ("EN-us", "en-US"), ("zh-hant-tw", "zh-Hant-TW"), ("ru", "ru")):
            with self.subTest(raw=raw):
                self.assertEqual(settings.normalize_language(raw), normalized)
                self.assertEqual(settings.validate_target(raw), normalized)
                result = settings.save_settings({"target_language": raw})
                self.assertEqual(result["target_language"], normalized)
        self.assertEqual(settings.normalize_language("AUTO", source=True), "auto")
        self.assertEqual(settings.validate_source("auto"), "auto")
        for value in ("auto", "../zh", "en\\other", "en%2Fother", "/en", "en\x00", "z", "a" * 1000, 7):
            with self.subTest(value=value), self.assertRaises(settings.TranslationSettingsError):
                settings.normalize_language(value)
        for value in settings.TARGET_LANGUAGES:
            self.assertEqual(settings.normalize_language(value), value)
        for value in settings.SOURCE_LANGUAGES:
            self.assertEqual(settings.normalize_language(value, source=True), value)

    def test_unknown_provider_model_slug_may_be_saved_without_network_validation(self):
        result = settings.save_settings({"provider": "openrouter", "model": "author/model:free"})
        self.assertEqual(result["model"], "author/model:free")
        self.assertEqual([call[0][1] for call in self.calls], ["has"])

    def test_corrupt_or_secret_bearing_file_is_not_a_credential_fallback(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({**settings.DEFAULT_SETTINGS, "api_key": "fixture-secret-in-bad-file"}))
        with self.assertRaises(settings.TranslationSettingsError):
            settings.load_settings()
        self.assertEqual(self.calls, [])
        self.assertIsNone(settings.load_provider_key("deepseek"))
        result = settings.save_settings({"source_language": "en"})
        self.assertEqual(result["source_language"], "en")
        self.assertNotIn("api_key", self.path.read_text())
        self.assertEqual(self.keys, {})

    def test_oversized_config_is_rejected_and_explicit_save_repairs_it(self):
        self.path.parent.mkdir()
        self.path.write_text(" " * 20000)
        with self.assertRaises(settings.TranslationSettingsError):
            settings.load_settings()
        result = settings.save_settings({})
        self.assertEqual(result["provider"], "deepseek")
        self.assertLess(self.path.stat().st_size, 20000)

    def test_helper_failure_does_not_replace_existing_config_or_leak_diagnostics(self):
        settings.save_settings({"source_language": "ja"})
        original = self.path.read_bytes()
        self.runner.side_effect = lambda args, **kw: subprocess.CompletedProcess(args, 1, b"fixture-secret-in-stdout", b"fixture-secret-in-stderr")
        with self.assertRaises(settings.TranslationKeychainError) as raised:
            settings.save_settings({"source_language": "en"}, "fixture-new-secret")
        self.assertNotIn("fixture", str(raised.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_helper_exception_is_sanitized(self):
        self.runner.side_effect = RuntimeError("fixture-secret-from-runtime")
        with self.assertRaises(settings.TranslationKeychainError) as raised:
            settings.load_provider_key("deepseek")
        self.assertNotIn("fixture", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_malformed_helper_responses_are_sanitized(self):
        for data in (b"fixture-not-json-secret", b'{"has_key":"fixture-secret"}', b"true"):
            with self.subTest(data=data):
                self.runner.side_effect = lambda args, **kw: subprocess.CompletedProcess(args, 0, data, b"")
                with self.assertRaises(settings.TranslationKeychainError) as raised:
                    settings.load_settings()
                self.assertNotIn("fixture", str(raised.exception))
        for data in (b"too-short", b"\xff", b"fixture-secret\nwith-newline"):
            with self.subTest(data=data):
                self.runner.side_effect = lambda args, **kw: subprocess.CompletedProcess(args, 0, data, b"")
                with self.assertRaises(settings.TranslationKeychainError) as raised:
                    settings.load_provider_key("deepseek")
                self.assertNotIn("fixture", str(raised.exception))

    def test_missing_helper_still_allows_preferences_but_not_credential_operations(self):
        self.helper.unlink()
        self.assertFalse(settings.load_settings()["has_key"])
        self.assertFalse(settings.save_settings({"target_language": "en"})["has_key"])
        for operation in (
            lambda: settings.save_settings({}, "fixture-new-key"),
            lambda: settings.load_provider_key("deepseek"),
            lambda: settings.delete_provider_key("deepseek"),
        ):
            with self.subTest(operation=operation), self.assertRaises(settings.TranslationKeychainError):
                operation()
        self.assertEqual(self.calls, [])

    def test_credential_operations_reject_outside_provider_scope(self):
        for operation in (settings.has_provider_key, settings.load_provider_key, settings.delete_provider_key):
            with self.subTest(operation=operation), self.assertRaises(settings.TranslationSettingsError):
                operation("../../other-app")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
