"""Check chart data, real rendering, and delivery using isolated project storage."""
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from test_kernel import _temporary, app, TestClient
from app.db import execute, now, one, uid
from kernel import chart_export, server
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula
from PIL import Image, ImageChops
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, headers={"x-kernel-token": "regression-token"})
        self.addCleanup(self.client.close)
        self.project = self.new_project()

    def new_project(self):
        response = self.client.post("/v1/projects", json={"name": "图表专用测试", "workflow": "legacy"})
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()["project"]["projectId"]
        self.addCleanup(self.client.delete, f"/v1/projects/{project}")
        return project

    def upload(self, name, content):
        path = server.DATA_ROOT / name
        path.write_bytes(content)
        response = self.client.post(f"/v1/projects/{self.project}/intake", json={"path": str(path)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["artifact"]["artifactId"]

    def run_tool(self, tool, args, project=None):
        return self.client.post(f"/v1/projects/{project or self.project}/execute", json={"tool": tool, "args": args})

    def chart(self, args):
        response = self.run_tool("export_chart", args)
        self.assertEqual(response.status_code, 200, response.text)
        artifacts = response.json()["artifacts"]
        by_name = {Path(one("SELECT path FROM artifacts WHERE id=?", (a["artifactId"],))["path"]).name: a
                   for a in artifacts}
        payloads = {}
        for name, artifact in by_name.items():
            reply = self.client.get(artifact["downloadPath"])
            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertEqual(hashlib.sha256(reply.content).hexdigest(), artifact["sha256"])
            payloads[name] = reply.content
        return by_name, payloads

    def test_csv_roundtrip_and_selective_bundle(self):
        csv = "温度,响应,样品\n30,3,A\n20,2,A\n\n20,4,B\n30,6,B\n".encode()
        aid = self.upload("温度 实验.csv", csv)
        files, content = self.chart({"mode": "table", "title": "温度与响应 CO₂", "artifact_id": aid,
                                     "mark": "line", "x_column": "温度", "y_column": "响应",
                                     "group_column": "样品", "x_unit": "°C", "y_unit": "μmol·m⁻²"})
        self.assertEqual(set(files), {"chart.svg", "chart.pdf", "chart.png", "chart.vl.json",
                                      "chart-source.json", "chart-data.json"})
        spec = json.loads(content["chart.vl.json"])
        actual_data = json.loads(content["chart-data.json"])
        self.assertEqual(spec["datasets"], actual_data)
        self.assertEqual([row["source_row"] for row in actual_data["points"]], [3, 2, 5, 6])
        manifest = json.loads(content["chart-source.json"])
        self.assertEqual(manifest["source"]["sha256"], hashlib.sha256(csv).hexdigest())
        self.assertEqual(manifest["source"]["excluded_rows"][0]["source_row"], 4)
        self.assertEqual(manifest["renderer"]["vega_lite"], "5.21")
        for text in ["温度与响应 CO₂", "温度 [°C]", "响应 [μmol·m⁻²]"]:
            self.assertIn(text, content["chart.svg"].decode())
        self.assertEqual(len(PdfReader(io.BytesIO(content["chart.pdf"])).pages), 1)
        self.assertTrue(manifest["renderer"]["pdf_fonts"])
        pdf_text = PdfReader(io.BytesIO(content["chart.pdf"])).pages[0].extract_text()
        self.assertIn("CO₂", pdf_text)
        self.assertIn("°C", pdf_text)
        self.assertIn("μmol·m⁻²", pdf_text)
        replay = chart_export.render(spec, ["svg", "png"])
        self.assertEqual(replay["svg"], content["chart.svg"])
        with Image.open(io.BytesIO(replay["png"])) as a, Image.open(io.BytesIO(content["chart.png"])) as b:
            self.assertIsNone(ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox())
        self.assertEqual(self.client.get(files["chart.svg"]["previewPath"]).content, content["chart.png"])
        bundle = self.client.post(f"/v1/projects/{self.project}/bundle", json={"artifact_ids": [files["chart.svg"]["artifactId"]]})
        self.assertEqual(bundle.status_code, 200, bundle.text)
        with zipfile.ZipFile(server.BUNDLES_DIR / bundle.json()["bundle"]["name"]) as archive:
            entries = {Path(name).name: archive.read(name) for name in archive.namelist() if name.startswith("charts/")}
        self.assertEqual(entries, {name: content[name] for name in (
            "chart.svg", "chart.vl.json", "chart-data.json", "chart-source.json")})
        self.save_example("table", content)

    def test_analyzed_dataset_keeps_source_rows(self):
        aid = self.upload("dataset.csv", b"time,height\n0,1\n1,3.2\n\n2,4.6\n3,7.4\n4,8.8\n")
        response = self.run_tool("analyze_dataset", {"artifact_id": aid, "x_column": "time", "y_column": "height",
                                "x_unit": "s", "y_unit": "mm", "models": ["linear", "power_law"]})
        self.assertEqual(response.status_code, 200, response.text)
        cid = response.json()["calculationId"]
        _, content = self.chart({"mode": "fit", "title": "表格拟合", "calculation_id": cid,
                                 "model": "linear", "formats": ["svg"]})
        data = json.loads(content["chart-data.json"])
        self.assertEqual([row["source_row"] for row in data["observations"]], [2, 3, 5, 6, 7])
        self.assertEqual(data["observations"][2]["result_path"], "/fits/0/observations/2")
        source = json.loads(content["chart-source.json"])["source"]
        self.assertEqual(source["dataset"]["artifact_id"], aid)

    def test_xlsx_grouped_bar_and_invalid_sources(self):
        def workbook(value, group="A"):
            book = Workbook()
            book.active.title = "对比"
            book.active.append(["工况", "产率", "系列"])
            book.active.append(["低温", value, group])
            book.active.append(["低温", 65, "B"])
            output = io.BytesIO()
            book.save(output)
            return output.getvalue()

        aid = self.upload("grouped.xlsx", workbook(60))
        args = {"mode": "table", "title": "分组产率", "artifact_id": aid, "sheet": "对比", "mark": "bar",
                "x_column": "工况", "y_column": "产率", "group_column": "系列", "x_unit": "dimensionless",
                "y_unit": "%", "formats": ["svg"]}
        files, content = self.chart(args)
        spec = json.loads(content["chart.vl.json"])
        self.assertTrue(spec["encoding"]["y"]["scale"]["zero"])
        self.assertEqual(spec["encoding"]["xOffset"]["field"], "group")
        self.assertEqual(files["chart.png"]["delivery_role"], "resource")
        self.assertEqual(json.loads(content["chart-source.json"])["source"]["sheet"], "对比")
        cross = self.run_tool("export_chart", args, self.new_project())
        self.assertEqual(cross.status_code, 422, cross.text)

        changed = Path(one("SELECT path FROM artifacts WHERE id=?", (aid,))["path"])
        changed.write_bytes(workbook(15))
        response = self.run_tool("export_chart", args)
        self.assertEqual(response.status_code, 422)
        self.assertIn("哈希", response.text)
        aid = self.upload("formula.xlsx", workbook("=30+30"))
        response = self.run_tool("export_chart", {**args, "artifact_id": aid})
        self.assertEqual(response.status_code, 422)
        self.assertIn("第2行的 产率 列", response.text)
        aid = self.upload("array-formula.xlsx", workbook(60, ArrayFormula("C2", "=B2*2")))
        response = self.run_tool("export_chart", {**args, "artifact_id": aid})
        self.assertEqual(response.status_code, 422)
        self.assertIn("第2行的 系列 列", response.text)

    def test_fit_from_saved_calculation_uses_original_band(self):
        base = f"/v1/projects/{self.project}"
        self.client.post(base + "/requirements", json={"items": [{"code": "FIT", "title": "拟合"}]})
        self.client.post(base + "/plan", json={"deliverables": [{"key": "fit", "title": "拟合图"}]})
        self.client.post(base + "/objects", json={"kind": "basis", "key": "fit",
                         "quantities": [{"key": "x", "value": 1, "unit": "s", "origin": "assumption",
                                         "conditions": "合成测试", "rationale": "拟合回归"}]})
        fit = self.run_tool("fit_data", {"x": [0, 1, 2, 3, 4], "y": [1, 3.2, 4.6, 7.4, 8.8],
                            "x_unit": "s", "y_unit": "mm", "confidence": .9,
                            "prediction_x": [-1, 0, 1, 2, 3, 4, 5], "allow_extrapolation": True})
        self.assertEqual(fit.status_code, 200, fit.text)
        cid = fit.json()["calculationId"]
        raw = one("SELECT result FROM calculations WHERE id=?", (cid,))["result"]
        result = json.loads(raw)
        _, content = self.chart({"mode": "fit", "title": "合成观测与回归", "calculation_id": cid})
        data = json.loads(content["chart-data.json"])
        self.assertEqual([(row["x"], row["y"]) for row in data["observations"]],
                         [(row["x"], row["y"]) for row in result["observations"]])
        for band, original in zip(data["bands"], result["curve"]):
            self.assertEqual([band["lower"], band["upper"]], original["mean_confidence_interval"])
        self.assertGreater(len({row["segment"] for row in data["lines"]}), 1)
        source = json.loads(content["chart-source.json"])["source"]
        self.assertEqual(source["result_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(source["interval"]["confidence"], .9)
        self.assertIn("90% 局部模型均值置信带", content["chart.svg"].decode())
        self.save_example("fit", content)

    def test_scan_rejects_unverified_cases_and_breaks_lines(self):
        job_id = uid()
        cases = []
        for i, (solved, passed, pressure) in enumerate(
                [(True, True, 230000), (True, True, 240000), (True, None, 260000),
                 (True, True, 270000), (True, True, 280000), (False, True, 300000),
                 (True, True, None)], 1):
            cid = uid()
            result = {"solved": solved, "validation": {"passed": passed},
                      "streams": [{"tag": "OUT", "pressure_pa": pressure}]}
            execute("INSERT INTO calculations VALUES(?,?,?,?,?,?)",
                    (cid, self.project, "dwsim", "{}", json.dumps(result), now()))
            cases.append({"key": f"point-{i}", "status": "completed", "calculation_id": cid})
        study = server.DWSIM_STUDIES.folder(self.project) / job_id
        study.mkdir(parents=True)
        (study / "study.json").write_text(json.dumps({
            "job_id": job_id, "project_id": self.project, "status": "completed_with_failures", "cases": cases,
            "request": {"scan": {"tag": "FEED", "field": "temperature_k", "values": list(range(300, 370, 10))},
                        "metrics": [{"key": "pressure", "path": "/streams/@OUT/pressure_pa", "unit": "kPa"}]},
        }), encoding="utf-8")
        _, content = self.chart({"mode": "dwsim_scan", "title": "测试扫描与校核状态", "job_id": job_id,
                                 "metric_key": "pressure"})
        data = json.loads(content["chart-data.json"])
        self.assertEqual([r["x"] for r in data["cases"]], list(range(300, 370, 10)))
        self.assertEqual([r["y"] for r in data["cases"]], [230, 240, None, 270, 280, None, None])
        self.assertEqual([r["segment"] for r in data["valid"]], [0, 0, 1, 1])
        self.assertEqual(data["valid"][0]["result_path"], "/streams/0/pressure_pa")
        self.assertIn("校核未通过", content["chart.svg"].decode())
        self.assertIn("未求解", content["chart.svg"].decode())
        self.save_example("scan-fixture", content)

    def test_render_failure_leaves_no_chart_artifacts(self):
        artifact = self.upload("failed-render.csv", b"x,y\n1,2\n2,3\n")
        args = {"mode": "table", "title": "Failure", "artifact_id": artifact, "mark": "point",
                "x_column": "x", "y_column": "y", "x_unit": "s", "y_unit": "m"}
        with patch.object(chart_export, "render", side_effect=RuntimeError("PDF export failed")):
            reply = self.run_tool("export_chart", args)
        self.assertEqual(reply.status_code, 409, reply.text)
        self.assertIsNone(one("SELECT id FROM artifacts WHERE project_id=? AND id!=?", (self.project, artifact)))

    def save_example(self, name, content):
        import os
        if os.environ.get("CHEMELLM_CHART_SAVE_QA") == "1":
            folder = ROOT / "output" / "chart-qa" / name
            folder.mkdir(parents=True, exist_ok=True)
            for filename, payload in content.items():
                (folder / filename).write_bytes(payload)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _temporary.cleanup()
