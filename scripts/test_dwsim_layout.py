"""Layout geometry, native round trips and physics invariance with isolated storage."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
_temporary=tempfile.TemporaryDirectory(prefix='chemellm-layout-tests-')
os.environ.update(CHEMELLM_DATA_ROOT=_temporary.name,CHEMELLM_KERNEL_DATA=str(Path(_temporary.name)/'kernel'),
                  CHEMELLM_DATA_DIR=str(Path(_temporary.name)/'kernel'),CHEMELLM_SNAPSHOT_DIR=str(Path(_temporary.name)/'.chem'),
                  CHEMELLM_KERNEL_TOKEN='',CHEMELLM_PHASE_GATE='enforce')
from app.dwsim import DwsimInput
from app.dwsim_layout import LayoutOptions,compile_layout,quality
from fastapi.testclient import TestClient
from kernel import server


def graph():
    types={'F':'MaterialStream','M':'NodeIn','B':'NodeOut','T':'DistillationColumn','P1':'MaterialStream',
           'P2':'MaterialStream','Q':'EnergyStream','I':'Tank','S':'MaterialStream'}
    pairs=[('F','M'),('M','S'),('S','B'),('B','T'),('T','P1'),('T','P2'),('P2','M'),('Q','T')]
    return dict(nodes=[dict(name=k,tag='长中文位号-'+k,type=t,simulation=True,x=100,y=100,width=50,height=50,
                           rotation=0,flipped_h=False,flipped_v=False,label_width=140) for k,t in types.items()],
                edges=[dict(source_id=a,target_id=b,source=a,target=b,source_port=i%2,target_port=0,points=[])
                       for i,(a,b) in enumerate(pairs)],metadata={})


class PlacementTests(unittest.TestCase):
    def test_branch_cycle_isolated_long_labels_and_determinism(self):
        original=graph();snapshot=deepcopy(original)
        plan=compile_layout(original,LayoutOptions())
        self.assertEqual(original,snapshot)
        self.assertEqual(plan,compile_layout(original,LayoutOptions()))
        by_id={n['name']:n for n in original['nodes']}
        for update in plan['nodes']:by_id[update['name']].update(update)
        self.assertEqual(quality(original)['overlap_count'],0)
        self.assertEqual(len({(n['x'],n['y']) for n in by_id.values()}),len(by_id))
        self.assertEqual(len(plan['groups']),2)

    def test_fixed_conflict_is_reported_and_aliases_rejected(self):
        g=graph()
        plan=compile_layout(g,LayoutOptions(pinned=['F','M']))
        self.assertEqual([(n['x'],n['y']) for n in plan['nodes'] if n['name'] in {'F','M'}],[(100,100)]*2)
        for update in plan['nodes']:next(n for n in g['nodes'] if n['name']==update['name']).update(update)
        self.assertGreater(quality(g)['overlap_count'],0)
        with self.assertRaisesRegex(ValueError,'不存在'):compile_layout(g,LayoutOptions(pinned=['missing']))
        with self.assertRaisesRegex(ValueError,'重复修改'):
            compile_layout(g,LayoutOptions(nodes=[{'object':'F','x':1,'y':1},{'object':'长中文位号-F','x':2,'y':2}]))
        g['nodes'][1]['tag']=g['nodes'][0]['tag']
        with self.assertRaisesRegex(ValueError,'歧义'):compile_layout(g,LayoutOptions(pinned=['长中文位号-F']))

    def test_manual_group_move_and_process_fields_forbidden(self):
        g=graph()
        plan=compile_layout(g,LayoutOptions(mode='manual',groups=[dict(id='zone',title='中文分区',members=['F','M'],x=500,y=600)]))
        self.assertTrue(all((n['x'],n['y'])==(500,600) for n in plan['nodes'] if n['name'] in {'F','M'}))
        for bad in ({'updates':[{'tag':'F','temperature_k':500}]},{'property_packages':[{'key':'p','name':'PR'}]},
                    {'layout':{'nodes':[{'object':'F','x':1}]}},{'view':'layout'}):
            with self.assertRaises(ValueError):DwsimInput(action='layout',flowsheet_path='model.dwxml',**bad)


@unittest.skipUnless(server.dwsim_bridge.status()['ready'],'本机没有 DWSIM 运行环境')
class NativeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(server.app)
        self.pid=self.client.post('/v1/projects',json={'name':'原生布局回归','workflow':'legacy'}).json()['project']['projectId']
        self.base='/v1/projects/'+self.pid

    def tearDown(self):
        self.client.delete(self.base);self.client.close()

    def call(self,**request):
        r=self.client.post(self.base+'/execute',json={'tool':'dwsim','args':request})
        self.assertEqual(r.status_code,200,r.text)
        response=r.json()
        if response.get('truncated'):
            result=response['result'];path=result.get('layout_path') or result.get('inspection_path')
            if path:response['result']=json.loads(Path(path).read_text(encoding='utf-8'))
        return response

    def test_saved_formats_annotations_no_calculation_and_actual_recalculation(self):
        # Create a known native heater with physics fixtures used by the numerical tests.
        self.client.post(self.base+'/requirements',json={'items':[{'code':'R1','title':'布局不改变物理结果'}]})
        self.client.post(self.base+'/plan',json={'deliverables':[{'key':'sim','title':'原生模型','requirementCodes':['R1'],'formats':['dwxmz']}]})
        self.client.post(self.base+'/objects',json={'kind':'basis','key':'feed','quantities':[{'key':'f','value':2,'unit':'kg/s',
            'origin':'assumption','conditions':'Water 300 K 200000 Pa','rationale':'确定性回归测试条件'}]})
        model={'compounds':['Water'],'property_package':'Steam Tables (IAPWS-IF97)','objects':[
            {'tag':'F','temperature_k':300,'pressure_pa':200000,'mass_flow_kg_s':2,'mole_fractions':{'Water':1}},
            {'tag':'H','type':'Heater','spec':{'mode':'outlet_temperature','temperature_k':350,'pressure_drop_pa':10000}},
            {'tag':'P'},{'tag':'Q','type':'EnergyStream'}],
            'connections':[{'source':'F','target':'H'},{'source':'H','target':'P'},{'source':'Q','target':'H','target_port':1}]}
        created=self.call(action='create',model=model,layout={'groups':[{'id':'heat','title':'中文加热分区','members':['H']}]})
        original=Path(created['result']['saved_flowsheet']);digest=hashlib.sha256(original.read_bytes()).hexdigest()
        self.assertTrue(created['result']['validation']['passed'])
        for fmt in ['dwxml','dwxmz']:
            edited=self.call(action='layout',flowsheet_path=str(original),output_format=fmt,
                layout={'mode':'manual','nodes':[{'object':'H','x':700,'y':400,'rotation':90,'width':70,'height':90,'flipped_h':True}]})
            self.assertIsNone(edited['calculationId'])
            r=edited['result'];self.assertFalse(r['solver_executed']);self.assertNotIn('validation',r)
            self.assertTrue(r['protected_data_unchanged']);self.assertTrue(r['saved_reload_verified'])
            h=next(n for n in r['graphics']['nodes'] if n['tag']=='H')
            self.assertEqual((h['x'],h['y'],h['rotation'],h['width'],h['height'],h['flipped_h']),(700,400,90,70,90,True))
            self.assertEqual(len([n for n in r['graphics']['nodes'] if not n['simulation']]),2)
            self.assertTrue(all(Path(p['path']).read_bytes().startswith(b'\x89PNG') for p in r['previews']))
            source=Path(r['saved_flowsheet'])
            repeated=self.call(action='layout',flowsheet_path=str(source),layout={'mode':'manual'})['result']
            self.assertEqual(len([n for n in repeated['graphics']['nodes'] if not n['simulation']]),2)
            solved=self.call(action='run',flowsheet_path=repeated['saved_flowsheet'])['result']
            self.assertTrue(solved['solved']);self.assertTrue(solved['validation']['passed'])
            product=next(s for s in solved['streams'] if s['tag']=='P')
            self.assertAlmostEqual(product['temperature_k'],350,places=5)
            self.assertAlmostEqual(product['pressure_pa'],190000,places=3)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),digest)
        project=server.load_project(self.pid)
        self.assertEqual(len(project['layout_edits']),4)
        self.assertEqual(len(project['calculations']),3)
        self.assertTrue(any(f.get('tool')=='dwsim_layout' for f in project['files']))

    def test_opaque_xml_and_archive_entries_preserved(self):
        # Real native sample plus an opaque extension and non-XML auxiliary payload.
        home=server.dwsim_bridge.find_home()
        sample=home/'samples/Petroleum Distillation with Reboiler Heating Fluid.dwxmz'
        root,name,entries=server.dwsim_bridge.read_native(sample)
        column=next(g for g in root.find('GraphicObjects') if g.findtext('ObjectType')=='DistillationColumn')
        import xml.etree.ElementTree as ET
        ET.SubElement(root,'UnrecognizedVendorExtension',{'key':'keep'}).text=' do not trim '
        source=Path(_temporary.name)/'opaque.dwxmz'
        entries.append((zipfile.ZipInfo('auxiliary/payload.bin'),b'opaque\x00data'))
        server.dwsim_bridge.write_native(root,source,name,entries)
        r=self.call(action='layout',flowsheet_path=str(source),layout={'mode':'manual',
            'nodes':[{'object':column.findtext('Name'),'x':1200,'y':700,'width':80,'height':280}]})['result']
        native_column=next(n for n in r['graphics']['nodes'] if n['name']==column.findtext('Name'))
        self.assertGreater(len(native_column['output_ports']),2)
        saved=Path(r['saved_flowsheet'])
        again,_,_=server.dwsim_bridge.read_native(saved)
        self.assertEqual(again.find('UnrecognizedVendorExtension').text,' do not trim ')
        with zipfile.ZipFile(saved) as z:self.assertEqual(z.read('auxiliary/payload.bin'),b'opaque\x00data')


if __name__=='__main__':unittest.main(verbosity=2)
