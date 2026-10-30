"""Verify reproducible installer archives, file hashes and excluded local state."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts/build_release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseTests(unittest.TestCase):
    def test_manifest_exclusions_and_reproducibility(self):
        with tempfile.TemporaryDirectory(prefix="chemellm-release-test-") as temporary:
            first = builder.build(Path(temporary) / "first")
            second = builder.build(Path(temporary) / "second")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                root = first.stem + "/"
                manifest = json.loads(archive.read(root + "release-manifest.json"))
                self.assertEqual(len(manifest["files"]) + 1, len(archive.namelist()))
                for entry in manifest["files"]:
                    content = archive.read(root + entry["path"])
                    self.assertEqual(len(content), entry["bytes"])
                    self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"])
                for name in archive.namelist():
                    self.assertNotIn("/runtime/", name)
                    self.assertNotIn("/.venv/", name)
                    self.assertNotIn("/data/", name)
                    self.assertNotIn("/.chem/", name)
                    self.assertFalse(name.endswith("/.mcp.json"))
                self.assertTrue(archive.read(root + "scripts/install-windows.ps1").startswith(b"\xef\xbb\xbf"))
                self.assertIn(b"\r\n", archive.read(root + "INSTALL.cmd"))
            checksum = (first.parent / "SHA256SUMS.txt").read_text(encoding="utf-8").split()[0]
            self.assertEqual(checksum, hashlib.sha256(first.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
