"""Discovery, selective reads and plugin pairing, using temporary project data."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix='chemellm-skill-efficiency-')
DATA = Path(TEMP.name)/'data'
os.environ.update(CHEMELLM_DATA_ROOT=str(DATA), CHEMELLM_KERNEL_DATA=str(DATA/'kernel'),
                  CHEMELLM_DATA_DIR=str(DATA/'kernel'), CHEMELLM_SNAPSHOT_DIR=str(DATA/'.chem'),
                  CHEMELLM_KERNEL_TOKEN='', CHEMELLM_PHASE_GATE='enforce')
from fastapi.testclient import TestClient
from kernel import server as k
from app.db import connection
from chemellm_codex import skill_fingerprint, skill_inventory


def script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


class SkillEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(k.app)

    def tearDown(self):
        self.client.close()

    def init(self, legacy=False):
        if legacy:
            return self.client.post('/v1/projects', json={'name':'legacy read fixture','workflow':'legacy'}).json()['project']['projectId']
        return self.client.post('/v1/project-init', json={'name':'selective read fixture','task_kind':'research'}).json()['project']['projectId']

    def test_catalog_is_lazy_shared_paginated_and_compatible(self):
        full = self.client.get('/v1/tools').json()
        self.assertEqual(full, self.client.get('/v1/tools?view=full').json())
        class Poison:
            @classmethod
            def model_json_schema(cls):
                raise AssertionError('An unrelated schema was generated')
        with patch.dict(k.SCHEMAS, {'unrelated': Poison}):
            page = self.client.get('/v1/tools?view=summary&limit=3').json()
            self.assertEqual(len(page['items']), 3)
            self.assertEqual(page['next_offset'], 3)
            self.assertNotIn('schemas', page)
            self.assertEqual(self.client.get('/v1/tools?tool=dwsim').json()['schema'], k.DwsimInput.model_json_schema())
        names = []
        offset = 0
        while offset is not None:
            page = self.client.get('/v1/tools', params={'view':'summary','offset':offset,'limit':7}).json()
            names.extend(row['id'] for row in page['items']);offset=page['next_offset']
        self.assertEqual(names, full['executable'])
        scene = self.client.get('/v1/tools?view=summary&category=3d').json()
        self.assertIn('export_blender', [r['id'] for r in scene['items']])
        refs = self.client.get('/v1/tools?view=summary&category=references').json()
        self.assertIn('drawing_read', [r['id'] for r in refs['items']])
        for query in ('tool=unknown','category=unknown','view=summary&limit=0','view=summary&offset=-1'):
            self.assertEqual(self.client.get('/v1/tools?'+query).status_code,422)
        small = self.client.get('/v1/tools?view=summary').json()
        self.assertLess(len(encoded(small)), len(encoded(full))*.2)
        self.assertLess(len(encoded(small)),32768)

    def test_summary_and_selected_reads_are_scoped_exact_and_do_not_write(self):
        for legacy in (False, True):
            pid = self.init(legacy)
            ident = k.uid()
            result = {'streams':[{'flow':1.2345678901234567,'unit':'kg/s'}, {'flow':2.0,'unit':'kg/s'}],
                      'a/b':{'~key':42},'diagnostics':'长诊断'*50000,'solved':False,'validation':{'passed':False}}
            raw = json.dumps(result,ensure_ascii=False)
            k.execute('INSERT INTO calculations VALUES(?,?,?,?,?,?)',(ident,pid,'fixture','{}',raw,k.now()))
            with connection() as db:
                counts = tuple(db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['calculations','runs','codex_project_history'])
            path = '/v1/projects/'+pid+'/calculations'
            # No project snapshot/result history is loaded for either new read mode.
            with patch.object(k,'load_project',side_effect=AssertionError('Unnecessary project load')):
                page = self.client.get(path+'?view=summary&limit=1').json()
                self.assertEqual(page['items'][0]['calculationId'],ident)
                self.assertEqual(page['items'][0]['status'],'recorded')
                self.assertNotIn('result',page['items'][0]);self.assertNotIn('input',page['items'][0])
                selected = self.client.get(path+'/'+ident,params={'view':'selected','result_path':'/streams','limit':1}).json()
                self.assertEqual(selected['value'],result['streams'][:1]);self.assertEqual(selected['next_offset'],1)
                self.assertEqual(selected['result_sha256'],hashlib.sha256(raw.encode()).hexdigest())
                escaped = self.client.get(path+'/'+ident,params={'view':'selected','result_path':'/a~1b/~0key'}).json()
                self.assertEqual(escaped['value'],42)
                large = self.client.get(path+'/'+ident,params={'view':'selected','result_path':'/diagnostics'}).json()
                self.assertNotIn('value',large);self.assertIn('notice',large);self.assertLess(len(encoded(large)),32768)
            full = self.client.get(path+'/'+ident).json()
            self.assertEqual(full['result'],result)
            self.assertEqual(full,self.client.get(path+'/'+ident+'?view=full').json())
            for pointer in ['/missing','/streams/99','/streams/01','/a~2b']:
                self.assertEqual(self.client.get(path+'/'+ident,params={'view':'selected','result_path':pointer}).status_code,422)
            self.assertEqual(self.client.get(path+'?view=selected').status_code,422)
            other = self.init()
            for view in ['selected','summary','full']:
                self.assertEqual(self.client.get('/v1/projects/'+other+'/calculations/'+ident,params={'view':view}).status_code,404)
            with connection() as db:
                after = tuple(db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['calculations','runs','codex_project_history'])
            self.assertEqual(after,counts)

    def test_record_key_pagination_consistent_across_backends(self):
        for legacy in (False,True):
            pid = self.init(legacy)
            project = k.load_project(pid)
            project['calculations']=[{'calculationId':'first','tool':'fixture'},{'calculationId':'second','tool':'fixture'}]
            k.save_project(project)
            page = self.client.get('/v1/projects/'+pid+'/records?section=calculations&key=second&limit=1').json()
            self.assertEqual(page['total'],1)
            row=page['items'][0];self.assertEqual(row.get('data',row)['calculationId'],'second')
            self.assertIsNone(page['next_offset'])

    def test_config_preservation_and_actual_cache_validation(self):
        generator=script('configure-codex.py');checker=script('check-installation.py')
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);config=folder/'launcher.json';storage=folder/'storage';allowed=folder/'task'
            initial=generator.generate(config,storage,[allowed])
            again=generator.generate(config)
            self.assertEqual(initial,again)
            args=again['mcpServers']['chemellm']['args']
            self.assertEqual(args[args.index('--data-root')+1],str(storage.resolve()))
            self.assertEqual(args[args.index('--allowed-root')+1],str(allowed.resolve()))
            changed=generator.generate(config,folder/'new',[folder/'different'])['mcpServers']['chemellm']['args']
            self.assertNotIn(str(storage.resolve()),changed);self.assertNotIn(str(allowed.resolve()),changed)
            cache=folder/'cache';shutil.copytree(ROOT/'codex-plugin/skills',cache/'skills')
            shutil.copy2(config,cache/'.mcp.json')
            checker.validate_configuration(config,cache)
            for skill in skill_inventory(cache/'skills'):
                target=cache/'skills'/skill['name']/'SKILL.md';raw=target.read_bytes()
                target.write_bytes(raw+b'\nchanged\n')
                with self.assertRaises(ValueError):checker.validate_configuration(config,cache)
                target.write_bytes(raw)
            # Cache configuration differences are also detected, independently of source.
            (cache/'.mcp.json').write_text('{}',encoding='utf-8')
            with self.assertRaises((ValueError,KeyError)):checker.validate_configuration(config,cache)

    def test_health_pairing_covers_specialists_and_legacy_is_explicit(self):
        with patch.dict(os.environ,{'CHEMELLM_SKILL_FINGERPRINT':skill_fingerprint(ROOT/'codex-plugin/skills/chemellm-engineering'),
                                    'CHEMELLM_SKILL_SET_FINGERPRINT':skill_fingerprint(ROOT/'codex-plugin/skills')}):
            health=k.health();self.assertEqual(health['skillPairing'],'matched');self.assertEqual(health['skillSetPairing'],'matched')
            self.assertEqual(len(health['skills']),5)
            with patch.dict(os.environ,{'CHEMELLM_SKILL_SET_FINGERPRINT':'bad'}):
                self.assertEqual(k.health()['skillSetPairing'],'mismatch')
            with patch.dict(os.environ,{'CHEMELLM_SKILL_SET_FINGERPRINT':''}):
                self.assertEqual(k.health()['skillSetPairing'],'unconfigured')

    def test_skill_links_budgets_examples_and_measured_savings(self):
        skills=ROOT/'codex-plugin/skills'
        self.assertEqual(len(skill_inventory(skills)),5)
        for skill in skill_inventory(skills):
            p=skills/skill['name']/'SKILL.md';body=p.read_text(encoding='utf-8').split('---',2)[2]
            self.assertLessEqual(len(body.encode()),2048 if skill['name']=='chemellm-engineering' else 4096)
        for p in skills.rglob('*.md'):
            for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
                if '://' not in target:
                    self.assertTrue((p.parent/target.split('#')[0]).is_file(),str(p)+': '+target)
        from app.dwsim import DwsimInput
        for p in (skills/'chemellm-dwsim/references/examples').glob('*.json'):
            DwsimInput.model_validate(json.loads(p.read_text(encoding='utf-8'))['request'])
        baseline_path=ROOT/'output/skill-efficiency/baseline.json'
        if baseline_path.exists():
            baseline=json.loads(baseline_path.read_text(encoding='utf-8'))
            current=sum(p.stat().st_size for p in [skills/'chemellm-dwsim/SKILL.md',skills/'chemellm-dwsim/references/basics.md'])
            self.assertLessEqual(current,baseline['basic_dwsim_read_bytes']*.6)


if __name__=='__main__':
    unittest.main(verbosity=2)
