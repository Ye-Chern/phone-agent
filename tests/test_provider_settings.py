import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phoneagent_core import provider_settings as settings


class ProviderSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {
            "PHONEAGENT_DEEPSEEK_KEY": "",
            "PHONEAGENT_GEMINI_KEY": "",
            "PHONEAGENT_EXA_KEY": "",
        }, clear=False)
        self.env.start()
        for name in ("PHONEAGENT_DEEPSEEK_KEY", "PHONEAGENT_GEMINI_KEY", "PHONEAGENT_EXA_KEY"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_initial_update_creates_private_json_and_google_file(self):
        result = settings.update(self.root, {
            "deepseek_key": "fake-deepseek",
            "gemini_key": "fake-gemini",
            "exa_key": "fake-exa",
        })
        self.assertEqual((self.root / "gemini.key").read_text("utf-8"), "fake-gemini\n")
        saved = json.loads((self.root / "secrets.json").read_text("utf-8"))
        self.assertEqual(saved, {"deepseek_key": "fake-deepseek", "exa_key": "fake-exa"})
        self.assertEqual(result["deepseek_key_configured"], True)
        self.assertEqual(result["gemini_key_configured"], True)
        self.assertEqual(result["exa_key_configured"], True)
        self.assertNotIn("fake-", json.dumps(result))
        if os.name != "nt":
            self.assertEqual((self.root / "secrets.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.root / "gemini.key").stat().st_mode & 0o777, 0o600)

    def test_preserves_unrelated_json_fields_and_blank_keeps_keys(self):
        (self.root / "secrets.json").write_text(json.dumps({
            "deepseek_key": "old", "exa_key": "exa-old", "other": {"keep": 1},
        }), "utf-8")
        result = settings.update(self.root, {"deepseek_key": "  "})
        saved = json.loads((self.root / "secrets.json").read_text("utf-8"))
        self.assertEqual(saved["deepseek_key"], "old")
        self.assertEqual(saved["exa_key"], "exa-old")
        self.assertEqual(saved["other"], {"keep": 1})
        self.assertTrue(result["deepseek_key_configured"])

    def test_trims_saved_key_and_tightens_existing_secret_permissions(self):
        secrets = self.root / "secrets.json"
        secrets.write_text(json.dumps({"other": "preserved"}), "utf-8")
        gemini = self.root / "gemini.key"
        gemini.write_text("old-key\n", "utf-8")
        if os.name != "nt":
            secrets.chmod(0o644)
            gemini.chmod(0o644)
        settings.update(self.root, {"deepseek_key": "  fake-key \t"})
        saved = json.loads(secrets.read_text("utf-8"))
        self.assertEqual(saved["deepseek_key"], "fake-key")
        if os.name != "nt":
            self.assertEqual(secrets.stat().st_mode & 0o777, 0o600)
            self.assertEqual(gemini.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.root / ".provider-settings.lock").stat().st_mode & 0o777, 0o600)

    def test_clear_google_removes_legacy_secret(self):
        (self.root / "secrets.json").write_text(json.dumps({
            "gemini_key": "legacy", "other": "preserved",
        }), "utf-8")
        (self.root / "gemini.key").write_text("local-key\n", "utf-8")
        result = settings.update(self.root, {"clear_gemini_key": True})
        self.assertFalse((self.root / "gemini.key").exists())
        self.assertEqual(json.loads((self.root / "secrets.json").read_text("utf-8")), {"other": "preserved"})
        self.assertFalse(result["gemini_key_configured"])

    def test_environment_key_is_reported_as_configured_and_managed(self):
        os.environ["PHONEAGENT_GEMINI_KEY"] = "environment-secret"
        result = settings.status(self.root)
        self.assertTrue(result["gemini_key_configured"])
        self.assertTrue(result["env_managed"]["gemini_key"])
        self.assertNotIn("environment-secret", json.dumps(result))

    def test_invalid_request_does_not_change_files(self):
        (self.root / "secrets.json").write_text('{"keep":"yes"}', "utf-8")
        before = (self.root / "secrets.json").read_bytes()
        bad_bodies = [
            {"deepseek_key": "new", "clear_deepseek_key": True},
            {"deepseek_key": 42},
            {"clear_exa_key": "true"},
            {"unknown": "value"},
            {"exa_key": "line1\nline2"},
            {"gemini_key": "x" * 8193},
        ]
        for body in bad_bodies:
            with self.subTest(body=str(body)[:30]):
                with self.assertRaises(ValueError):
                    settings.update(self.root, body)
                self.assertEqual((self.root / "secrets.json").read_bytes(), before)
                self.assertFalse((self.root / "gemini.key").exists())


if __name__ == "__main__":
    unittest.main()
