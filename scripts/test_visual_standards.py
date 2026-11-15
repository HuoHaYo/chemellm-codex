"""Geometry and cross-format drawing regression; isolated, no engineering store."""
import math
from collections import Counter
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'kernel')]
from app.pipe_geometry import pipe_mesh, pipe_path, norm, sub, dot
from app.blender import BlenderInput
import pid_sheet
import drawing_tools
import pid_standard


class VisualStandardsTests(unittest.TestCase):
    def test_elbows_closed_connected_constant_section(self):
        for points in ([[0,0,0],[4,0,0],[4,4,0]],
                       [[0,0,0],[4,0,0],[6,2,0],[6,2,4]],
                       [[0,0,0],[1,0,0],[4,0,0]], [[0,0,0],[0,0,4]]):
            with self.subTest(points=points):
                vertices, faces, report = pipe_mesh(points, .2, .4)
                samples, _ = pipe_path(points, .2, .4)
                edges, graph = Counter(), {}
                for face in faces:
                    for a, b in zip(face, face[1:]+face[:1]):
                        edges[tuple(sorted((a,b)))] += 1
                        graph.setdefault(a,set()).add(b)
                        graph.setdefault(b,set()).add(a)
                self.assertTrue(all(n==2 for n in edges.values()))
                seen, pending = set(), [0]
                while pending:
                    a=pending.pop()
                    if a in seen: continue
                    seen.add(a)
                    pending.extend(graph[a]-seen)
                self.assertEqual(len(seen),len(vertices))
                for i,(center,tangent) in enumerate(samples):
                    for vertex in vertices[i*32:(i+1)*32]:
                        offset=sub(vertex,center)
                        self.assertAlmostEqual(norm(offset),.1)
                        self.assertAlmostEqual(dot(offset,tangent),0)
                self.assertEqual(report['endpoints'],[points[0],points[-1]])

    def test_invalid_bends_rejected_and_units_equivalent(self):
        points=[[0,0,0],[4,0,0],[4,4,0]]
        args={'items':[{'id':'P','kind':'pipe','diameter':.2,'points':points,'source':'test'}]}
        for path, radius in (([[0,0,0],[0,0,0]],None), ([[0,0,0],[1,0,0],[0,0,0]],None),
                             ([[0,0,0],[.1,0,0],[.1,.1,0]],1), (points,.05),
                             ([[0,0,0],[1,0,0],[1,1e-12,0]],None)):
            args['items'][0].update(points=path,bend_radius=radius)
            with self.assertRaises(ValueError): BlenderInput.model_validate(args)
        a,_,_=pipe_mesh(points,.2,.4)
        b,_,_=pipe_mesh([[v*1000 for v in p] for p in points],200,400)
        for p,q in zip(a,b):
            for x,y in zip(p,q): self.assertAlmostEqual(x,y/1000)

    def test_cad_signal_styles_match_svg_and_pdf_primitives(self):
        import ezdxf
        for module in (pid_sheet,drawing_tools):
            palette=module.PALETTE
            prims=[('line',10.,10.,110.,10.,palette['signal'],.3,'dash'),
                   ('line',10.,20.,110.,20.,palette['interlock'],.35,'dashdot'),
                   ('line',10.,30.,110.,30.,palette['process'],.5,'solid')]
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'style.dxf'
                module._dxf(150,100,prims,path)
                doc=ezdxf.readfile(path)
                self.assertFalse(doc.audit().has_errors)
                lines=list(doc.modelspace().query('LINE'))
                self.assertEqual([l.dxf.linetype for l in lines],['CHEM_DASH','CHEM_DASHDOT','CONTINUOUS'])
                self.assertEqual([l.dxf.lineweight for l in lines],[30,35,50])
                self.assertEqual(doc.layers.get('SIGNAL').dxf.linetype,'CHEM_DASH')
                self.assertGreater(len(doc.linetypes.get('CHEM_DASH').pattern_tags.tags),0)
                svg=module._svg(150,100,prims)
                self.assertEqual(svg.count('stroke-dasharray'),2)
                if module is pid_sheet:
                    self.assertEqual(doc.dxfversion,'AC1018')
                    self.assertEqual(doc.header['$INSUNITS'],4)

    def test_standard_sheet_geometry_styles_and_review_gaps(self):
        from copy import deepcopy
        import ezdxf
        from drawing_style import equipment_strokes
        model={'title':'虚构线型回归','drawing_number':'TEST-001','owner':'虚构测试队',
               'designer':'待审核','checker':'待审核',
               'nodes':[{'id':'V-101','kind':'vessel','label':'测试容器','x':0,'y':0},
                        {'id':'E-101','kind':'exchanger','label':'测试换热器','x':4,'y':0},
                        {'id':'TI-101','kind':'instrument','label':'测试测点','x':2,'y':2}],
               'edges':[{'source':'V-101','target':'E-101','line_no':'PG-0101-100-A1A'},
                        {'source':'E-101','target':'V-101','line_no':'PG-0102-100-A1A','line_role':'secondary'},
                        {'source':'TI-101','target':'E-101','kind':'signal'}]}
        with tempfile.TemporaryDirectory() as tmp:
            result=pid_standard.build_pid_files(model,Path(tmp))
            self.assertTrue(result['validation']['passed'])
            self.assertTrue(any('同一人员' in w for w in result['validation']['warnings']))
            self.assertEqual(result['validation']['visual_review']['professional_review'],'not_performed')
            doc=ezdxf.readfile(result['dxf'])
            self.assertFalse(doc.audit().has_errors)
            lines=list(doc.modelspace().query('LINE'))
            self.assertTrue(any(l.dxf.lineweight==80 for l in lines))
            self.assertTrue(any(l.dxf.lineweight==40 for l in lines))
            self.assertTrue(any(l.dxf.linetype=='CHEM_DASH' for l in lines))
            outlines=list(doc.modelspace().query('LWPOLYLINE[layer=="EQUIPMENT"]'))
            self.assertTrue(any(o.dxf.lineweight==25 for o in outlines))
            from pypdf import PdfReader
            pdf=PdfReader(result['pdf'])
            self.assertAlmostEqual(float(pdf.pages[0].mediabox.width)*25.4/72,841,places=3)
        dense=deepcopy(model)
        dense['edges']=[{'source':'V-101','target':'E-101'} for _ in range(9)]+[model['edges'][-1]]
        self.assertTrue(any('续表' in w for w in pid_standard.validate_standard(dense)['warnings']))
        model['edges'][0]['line_role']='invented'
        self.assertFalse(pid_standard.validate_standard(model)['passed'])


if __name__=='__main__': unittest.main(verbosity=2)
