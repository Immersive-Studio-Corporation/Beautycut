from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beautycut.updater import UpdateError, UpdateManifest, download_update, parse_update_manifest


class _FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def geturl(self):
        return self._url


class UpdateTests(unittest.TestCase):
    def test_manifest_only_returns_newer_https_versions(self) -> None:
        data = {
            "version": "0.3.1",
            "installer_url": "https://example.com/BeautyCut-Setup.exe",
            "sha256": "a" * 64,
            "release_notes": "Correction de bugs",
        }
        update = parse_update_manifest(json.dumps(data), "0.3.0")
        self.assertEqual(update.version, "0.3.1")
        self.assertEqual(update.release_notes, "Correction de bugs")
        self.assertIsNone(parse_update_manifest(json.dumps(data), "0.3.1"))
        self.assertIsNone(parse_update_manifest(json.dumps(data), "0.3.1.0"))

    def test_manifest_rejects_untrusted_urls_and_bad_hashes(self) -> None:
        data = {
            "version": "0.3.1",
            "installer_url": "http://example.com/BeautyCut-Setup.exe",
            "sha256": "a" * 64,
        }
        with self.assertRaises(UpdateError):
            parse_update_manifest(data, "0.3.0")
        data["installer_url"] = "https://example.com/BeautyCut-Setup.exe"
        data["sha256"] = "incorrect"
        with self.assertRaises(UpdateError):
            parse_update_manifest(data, "0.3.0")

    def test_download_verifies_hash_before_saving_installer(self) -> None:
        payload = b"verified test installer"
        digest = hashlib.sha256(payload).hexdigest()
        manifest = UpdateManifest("0.3.1", "https://example.com/setup.exe", digest)
        response = _FakeResponse(payload, manifest.installer_url)
        progress = []
        with tempfile.TemporaryDirectory() as directory, patch("beautycut.updater.urlopen", return_value=response):
            installer = download_update(manifest, destination_dir=directory, progress=progress.append)
            self.assertEqual(installer.read_bytes(), payload)
            self.assertEqual(progress[-1], 100)
            self.assertFalse(list(Path(directory).glob("*.download")))

    def test_download_discards_installer_with_wrong_hash(self) -> None:
        response = _FakeResponse(b"not trusted", "https://example.com/setup.exe")
        manifest = UpdateManifest("0.3.1", "https://example.com/setup.exe", "0" * 64)
        with tempfile.TemporaryDirectory() as directory, patch("beautycut.updater.urlopen", return_value=response):
            with self.assertRaises(UpdateError):
                download_update(manifest, destination_dir=directory)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
