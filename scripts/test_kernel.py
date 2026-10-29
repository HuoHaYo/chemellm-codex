"""Kernel regression checks in an isolated data directory; no running service needed.

Run: python -X utf8 scripts/test_kernel.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_temporary = tempfile.TemporaryDirectory(prefix="chemellm-kernel-tests-")
os.environ.update({
    "CHEMELLM_DATA_ROOT": _temporary.name,
    "CHEMELLM_KERNEL_DATA": str(Path(_temporary.name) / "kernel"),
    "CHEMELLM_DATA_DIR": str(Path(_temporary.name) / "kernel"),
    "CHEMELLM_SNAPSHOT_DIR": str(Path(_temporary.name) / ".chem"),
    "CHEMELLM_KERNEL_TOKEN": "regression-token",
    "CHEMELLM_PHASE_GATE": "enforce",
})

from fastapi.testclient import TestClient  # noqa: E402
from kernel.server import app  # noqa: E402


class KernelWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, headers={"x-kernel-token": "regression-token"})
        created = self.client.post("/v1/projects", json={"name": "重构回归"})
        self.assertEqual(created.status_code, 200, created.text)
        self.base = "/v1/projects/" + created.json()["project"]["projectId"]

    def tearDown(self):
        self.client.delete(self.base)
        self.client.close()

    def post(self, suffix, payload):
        response = self.client.post(self.base + suffix, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_token_and_executable_catalog(self):
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get("/v1/health").status_code, 401)
        catalog = self.client.get("/v1/tools").json()
        self.assertIn("pid", catalog["executable"])
        self.assertNotIn("ask_user", catalog["executable"])
        self.assertNotIn("acceptance", catalog["executable"])

    def test_full_workflow_and_review_invalidation(self):
        for tool in ("properties", "export_xlsx", "export_freecad"):
            response = self.client.post(self.base + "/execute", json={"tool": tool, "args": {}})
            self.assertEqual(response.status_code, 409, response.text)
        self.post("/requirements", {"items": [{"code": "R01", "title": "物性计算与报告"}]})
        self.post("/plan", {"deliverables": [{
            "key": "report", "title": "计算报告", "requirementCodes": ["R01"], "formats": ["md"],
        }]})
        basis = {"kind": "basis", "key": "feed", "quantities": [{
            "key": "flow", "value": 100, "unit": "kmol/h", "origin": "assumption",
            "conditions": "298 K, 101325 Pa", "rationale": "回归验证用假设",
        }]}
        self.post("/objects", basis)
        calculation = self.post("/execute", {"tool": "properties", "args": {"compound": "water"}})
        self.assertTrue(calculation["calculationId"])
        report = self.post("/execute", {"tool": "export_report", "args": {
            "title": "回归计算报告", "body": "本文件仅用于验证确定性生成和成果打包。", "formats": ["md"],
        }})
        artifact = report["artifacts"][0]
        downloaded = self.client.get("/v1/artifacts/" + artifact["artifactId"] + "/download")
        self.assertEqual(downloaded.status_code, 200)
        review = self.post("/review", {"note": "refactor regression"})
        self.assertTrue(review["review"]["ready"], review)
        bundle = self.post("/bundle", {})
        response = self.client.get("/v1/bundles/" + bundle["bundle"]["name"])
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for item in manifest["files"]:
                self.assertEqual(hashlib.sha256(archive.read(item["archivePath"])).hexdigest(), item["sha256"])
        basis["quantities"][0]["value"] = 120
        self.post("/objects", basis)
        project = self.client.get(self.base).json()["project"]
        self.assertEqual(project["review"], {})


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _temporary.cleanup()
