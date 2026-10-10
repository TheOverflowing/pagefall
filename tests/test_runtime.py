"""Installed resources stay separate from writable reading data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RuntimeLocationsTests(unittest.TestCase):
    def test_installed_assets_and_data_use_different_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            resources = Path(folder) / 'Read-only app resources'
            data = Path(folder) / 'User library'
            env = dict(os.environ, NOVELIST_RESOURCE_ROOT=str(resources), NOVELIST_DATA_ROOT=str(data))
            result = subprocess.run([sys.executable, '-c', '''
import json
from novelist import browser, session, jinjiang, translation_settings, platforms, engine, proofreading
print(json.dumps({
 'data':str(browser.ROOT), 'session_data':str(session.ROOT),
 'settings':str(translation_settings.SETTINGS_PATH), 'token_helper':str(jinjiang.HELPER),
 'session_helper':str(session.HELPER), 'translation_helper':str(translation_settings.KEY_HELPER),
 'jinjiang_bundle':str(jinjiang.BUNDLE), 'rules_bundle':str(engine.BUNDLE),
 'manifest':str(platforms.UPSTREAM_MANIFEST), 'ocr':str(proofreading.ROOT)
}))
'''], env=env, capture_output=True, text=True, check=True)
            values = json.loads(result.stdout)
            for name in ('data', 'session_data', 'settings'):
                self.assertTrue(Path(values[name]).is_relative_to(data), name)
            for name in ('token_helper', 'session_helper', 'translation_helper', 'jinjiang_bundle', 'rules_bundle', 'manifest', 'ocr'):
                self.assertTrue(Path(values[name]).is_relative_to(resources), name)
            self.assertFalse(resources.exists())
            self.assertFalse(data.exists())
