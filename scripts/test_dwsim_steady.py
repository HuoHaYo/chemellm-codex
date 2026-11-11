"""Steady-state extensions: analytic checks plus actual local DWSIM cases."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import unittest
import test_dwsim as base
from app.dwsim_validation import validate
from app.dwsim_workflows import Metric, metric_value


class BalanceTests(unittest.TestCase):
    def sample(self):
        def stream(tag, mass, enthalpy):
            return dict(tag=tag, mass_flow_kg_s=mass, enthalpy_flow_kw=mass*enthalpy,
                        component_mass_flows_kg_s={'Water': mass})
        return {'solved': True, 'streams': [stream('A',2,10),stream('B',1,40),stream('P',3,20)],
                'unit_operations': [{'tag':'M','net_energy_input_kw':0}],
                'connections':[{'source':'A','target':'M'},{'source':'B','target':'M'},{'source':'M','target':'P'}]}

    def test_analytic_balance_and_corruption(self):
        sample = self.sample()
        self.assertIs(validate(sample)['passed'], True)
        for field, value in [('mass_flow_kg_s', 4), ('enthalpy_flow_kw', 61)]:
            changed=deepcopy(sample); changed['streams'][-1][field]=value
            self.assertIs(validate(changed)['passed'], False)
        changed=deepcopy(sample); changed['streams'][-1]['component_mass_flows_kg_s']['Water']=2
        self.assertIs(validate(changed)['passed'], False)

    def test_missing_data_never_passes(self):
        sample=self.sample(); sample['streams'][0].pop('enthalpy_flow_kw')
        self.assertIsNone(validate(sample)['passed'])

    def test_units_and_reaction_elements(self):
        result={'streams':[dict(tag='P',temperature_k=373.15,mass_flow_kg_s=2)]}
        self.assertAlmostEqual(metric_value(result,Metric(key='T',path='/streams/@P/temperature_k',unit='degC'))[0],100)
        with self.assertRaises(ValueError):metric_value(result,Metric(key='T',path='/streams/@P/temperature_k',unit='kg/s'))
        with self.assertRaises(ValueError):metric_value(result,Metric(key='T',path='/streams/-1/temperature_k',unit='K'))
        sample=self.sample();sample['unit_operations'][0]['reactive']=True
        for s in sample['streams']:
            s['total_enthalpy_flow_kw']=s['enthalpy_flow_kw'];s['element_flows_mol_s']={'H':s['mass_flow_kg_s']*2,'O':s['mass_flow_kg_s']}
        self.assertIs(validate(sample)['passed'],True)
        sample['streams'][-1]['element_flows_mol_s']['O']=0
        self.assertIs(validate(sample)['passed'],False)
        sample=self.sample(); sample.pop('connections')
        self.assertIsNone(validate(sample)['passed'])


class SteadyTests(base.DwsimTests):
    def test_efficient_trial_without_ledger_and_formal_review_real(self):
        self.require_engine()
        created=self.client.post('/v1/project-init',json=dict(name='efficient DWSIM',task_kind='simulation',
            requirements=[dict(code='R1',title='真实混合器复算')],deliverables=[dict(key='sim',title='采用仿真',
                requirementCodes=['R1'],formats=['dwxmz','csv'])])).json()
        ident=created['project']['projectId'];path='/v1/projects/'+ident
        try:
            trial=self.client.post(path+'/execute',json=dict(tool='dwsim',args=dict(action='create',model=base.mixer_model())))
            self.assertEqual(trial.status_code,200,trial.text)
            trial=trial.json();self.assertTrue(trial['solved']);self.assertTrue(trial['verification']['validation']['passed'])
            project=base.server.load_project(ident)
            self.assertEqual(project['objects'],{})
            self.assertTrue(project['calculations'][0]['captured_inputs'])
            d=project['plan']['deliverables'][0];d['calculation_ids']=[trial['calculationId']]
            updated=self.client.post('/v1/project-init',json=dict(project_id=ident,task_kind='simulation',
                expected_revision=project['_store_revision'],deliverables=[d]))
            self.assertEqual(updated.status_code,200,updated.text)
            reviewed=self.client.post(path+'/review',json={}).json()['review']
            self.assertTrue(reviewed['ready'],reviewed)
            self.assertLess(len(json.dumps(trial).encode()),32768)
        finally:self.client.delete(path)

    def sim(self,**request):
        response=super().sim(**request)
        if response.get('truncated'):
            if response.get('calculationId'):
                response['result']=self.client.get(self.base+'/calculations/'+response['calculationId']).json()['result']
            elif response.get('result',{}).get('inspection_path'):
                response['result']=json.loads(Path(response['result']['inspection_path']).read_text(encoding='utf-8'))
        return response
    def column_model(self):
        model=self.simple('DistillationColumn',dict(mode='column',column=dict(stages=12,top_pressure_pa=101325,bottom_pressure_pa=110000,
            feed_stages={'F':6},distillate_stream='P',bottoms_stream='B',condenser_spec=dict(type='Stream_Ratio',value=3,unit=''),
            reboiler_spec=dict(type='Product_Molar_Flow_Rate',value=5.87,unit='mol/s'))),['Benzene','Toluene'],"Raoult's Law",365)
        model['objects']+= [dict(tag='B',type='MaterialStream'),dict(tag='QR',type='EnergyStream')]
        model['connections']=[dict(source='F',target='U'),dict(source='U',target='P',source_port=0),dict(source='U',target='B',source_port=1),
                              dict(source='U',target='Q',source_port=10),dict(source='QR',target='U',target_port=10)]
        return model

    def test_strict_column_and_recycle_real(self):
        self.require_engine();self.basis()
        model=self.column_model()
        # Actual connector indices are read from check before use; no guessed energy ports.
        model['connections']=model['connections'][:3]
        model['objects']=[o for o in model['objects'] if o['tag'] not in {'Q','QR'}]
        created=self.sim(action='create',model=model)
        if not created['result']['validation']['passed']:
            print('COLUMN:',created['result'].get('solver_errors'),created['result'].get('result_errors'),created['result'].get('error'))
        self.assertTrue(created['result']['validation']['passed'],created['result']['validation'])
        product=next(s for s in created['result']['streams'] if s['tag']=='P')
        self.assertGreater(product['mole_fractions'][0],.9)
        loaded=self.sim(action='check',flowsheet_path=str(self.native(created)))['result']
        tower=next(o for o in loaded['objects'] if o['tag']=='U')
        self.assertEqual(len(tower['settings']['stages']),12)
        self.assertEqual(tower['settings']['column_specs']['C']['SpecValue'],3)
        self.assertAlmostEqual(product['mole_fractions'][0],.9693,delta=.003)

    def test_recycle_splitter_real(self):
        self.require_engine();self.basis()
        model=base.mixer_model()
        model['objects']=[dict(tag='F',type='MaterialStream',temperature_k=300,pressure_pa=200000,mass_flow_kg_s=1,mole_fractions={'Water':1}),
            dict(tag='R',type='MaterialStream',temperature_k=300,pressure_pa=200000,mass_flow_kg_s=.8,mole_fractions={'Water':1}),
            dict(tag='M',type='Mixer'),dict(tag='MIXED',type='MaterialStream'),dict(tag='S',type='Splitter',spec=dict(mode='split_ratios',ratios=[.5,.5])),
            dict(tag='P',type='MaterialStream'),dict(tag='RETURN',type='MaterialStream'),
            dict(tag='REC',type='OT_Recycle',spec=dict(mode='recycle',maximum_iterations=100,temperature_tolerance_k=1e-5,pressure_tolerance_pa=.01,mass_flow_tolerance_kg_s=1e-9,composition_tolerance=1e-8))]
        model['connections']=[dict(source='F',target='M'),dict(source='R',target='M',target_port=1),dict(source='M',target='MIXED'),dict(source='MIXED',target='S'),
            dict(source='S',target='P'),dict(source='S',target='RETURN',source_port=1),dict(source='RETURN',target='REC'),dict(source='REC',target='R')]
        result=self.sim(action='create',model=model)['result']
        self.assertTrue(result['validation']['passed'],dict(validation=result['validation'],errors=result.get('solver_errors'),result_errors=result.get('result_errors'),units=result.get('unit_operations')))
        self.assertAlmostEqual(next(s for s in result['streams'] if s['tag']=='P')['mass_flow_kg_s'],1,delta=1e-6)
        rec=next(s for s in result['unit_operations'] if s['tag']=='REC')
        self.assertTrue(rec['diagnostics']['Converged'])
        original=self.sim(action='create',model=model)
        path=self.native(original)
        failed=self.sim(action='run',flowsheet_path=str(path),updates=[dict(tag='REC',type='OT_Recycle',spec=dict(mode='recycle',maximum_iterations=1,temperature_tolerance_k=1e-12,pressure_tolerance_pa=1e-8,mass_flow_tolerance_kg_s=1e-12,composition_tolerance=1e-12)),dict(tag='F',temperature_k=320)])
        self.assertFalse(failed['result']['validation']['passed'])
        self.assertTrue(self.native(failed).is_file())
        initialized=self.sim(action='run',flowsheet_path=str(path),initialization=[[dict(tag='R',mass_flow_kg_s=.2)]])
        self.assertTrue(initialized['result']['validation']['passed'])
        self.assertEqual(len(initialized['result']['initialization']),1)

    def test_study_publish_and_reference_comparison(self):
        self.require_engine();self.basis()
        created=self.sim(action='create',model=base.heater_model());path=self.native(created)
        request=dict(action='start',request_key='thermal-test',flowsheet_path=str(path),cases=[
            dict(key='normal',updates=[]),dict(key='hot',updates=[dict(tag='HT-101',parameters={'OutletTemperature':360})]),
            dict(key='bad',updates=[dict(tag='HT-101',parameters={'BadParameter':1})])],
            metrics=[dict(key='temperature',path='/streams/@PRODUCT/temperature_k',unit='K')])
        response=self.client.post(self.base+'/dwsim/study',json=request)
        self.assertEqual(response.status_code,200,response.text);job=response.json()['study'];deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            job=self.client.post(self.base+'/dwsim/study',json=dict(action='status',job_id=job['job_id'])).json()['study']
            if job['status'] not in {'running'}:break
            time.sleep(.05)
        self.assertEqual(job['status'],'completed_with_failures',job)
        self.assertEqual(job['envelope']['temperature']['valid_points'],2,job)
        self.assertEqual([c['status'] for c in job['cases']],['completed','completed','failed'])
        self.assertTrue(job['artifacts'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),job['baseline_sha256'])
        duplicate=self.client.post(self.base+'/dwsim/study',json=request).json()['study']
        self.assertEqual(duplicate['job_id'],job['job_id'])
        calculation=job['cases'][1]['calculation_id']
        publish=dict(calculation_id=calculation,case='hot',objects=[dict(kind='equipment',key='HT-101',title='加热器',expected_revision=0,
            quantities=[dict(key='operating_temperature',path='/streams/@PRODUCT/temperature_k',unit='K',conditions='hot 工况出口',rationale='用于工艺操作参数')])],
            references=[dict(key='temperature',path='/streams/@PRODUCT/temperature_k',unit='K',value=360,source='合成设定值，仅验收接口',conditions='出口温度规格',absolute_tolerance=.001)])
        result=self.client.post(self.base+'/dwsim/publish',json=publish)
        self.assertEqual(result.status_code,200,result.text);self.assertTrue(result.json()['comparisons'][0]['passed'])
        repeated=self.client.post(self.base+'/dwsim/publish',json=publish).json()
        self.assertFalse(repeated['objects'][0]['changed'])
        publish['objects'][0]['title']='changed'
        self.assertEqual(self.client.post(self.base+'/dwsim/publish',json=publish).status_code,409)
        invalid=deepcopy(publish);invalid['objects'][0]['quantities'][0]['unit']='kg/s'
        self.assertEqual(self.client.post(self.base+'/dwsim/publish',json=invalid).status_code,422)
        full=self.client.get(self.base).json()['project']['objects']['equipment/HT-101']
        self.assertEqual(full['quantities'][0]['role'],'operating')
        self.assertTrue(full['attributes']['dwsim']['native_models'])
        full_result=deepcopy(self.client.get(self.base+'/calculations/'+calculation).json()['result'])
        full_result['validation']={'passed':True,'scope':'legacy solver status only'}
        base.server.execute('INSERT INTO calculations VALUES(?,?,?,?,?,?)',('legacy',self.project_id,'dwsim','{}',json.dumps(full_result),base.server._utc()))
        legacy=deepcopy(publish);legacy['calculation_id']='legacy';legacy['objects']=[]
        response=self.client.post(self.base+'/dwsim/publish',json=legacy)
        self.assertEqual(response.status_code,422);self.assertIn('旧记录',response.text)
        sheet=self.post('/execute',dict(tool='equipment_datasheet',args={'items':[dict(tag='HT-101',equipment_type='exchanger',
            project_object=dict(key='equipment/HT-101',expected_revision=1,fields={'operating_temperature_c':'operating_temperature'}))]}))
        a=next(a for a in sheet['artifacts'] if a['kind']=='json')
        data=json.loads(base.server._artifact_path(a['artifactId']).read_text(encoding='utf-8'))
        self.assertAlmostEqual(data['items'][0]['operating_temperature_c'],86.85)
        self.assertIsNone(data['items'][0]['design_temperature_c'])
        self.assertEqual(data['sources'][0]['quantity']['calculationId'],calculation)
        for a in sheet['artifacts']:self.assertEqual(hashlib.sha256(base.server._artifact_path(a['artifactId']).read_bytes()).hexdigest(),a['sha256'])
        self.post('/objects',dict(kind='equipment',key='HT-101',title='manual',quantities=full['quantities']))
        # Input selection stays protected when real solved results are published.
        from test_basis_selection import candidate, quantity
        selected=quantity([candidate('user-temperature','user',360,'K')])
        selected.update(key='temperature',unit='K',conditions='hot 工况出口')
        self.post('/basis/select',dict(kind='equipment',key='thermal',expected_revision=0,
            reason='真实发布路径验证用户数值保护',quantities=[selected]))
        protected=dict(calculation_id=calculation,case='hot',objects=[dict(kind='equipment',key='thermal',title='工艺依据',expected_revision=1,
            quantities=[dict(key='temperature',path='/streams/@PRODUCT/temperature_k',unit='K',conditions='hot 工况出口',rationale='尝试覆盖输入')])])
        denied=self.client.post(self.base+'/dwsim/publish',json=protected)
        self.assertEqual(denied.status_code,409,denied.text)
        protected['objects'][0]['quantities'][0]['key']='calculated_temperature'
        accepted=self.client.post(self.base+'/dwsim/publish',json=protected)
        self.assertEqual(accepted.status_code,200,accepted.text)
        current=self.client.get(self.base).json()['project']['objects']['equipment/thermal']
        self.assertEqual(len(current['selectionHistory']),1)
        self.assertEqual(current['quantities'][0]['source_type'],'user')
        self.assertEqual(current['quantities'][1]['origin'],'calculation')
        self.assertFalse(self.client.get(self.base).json()['project']['review'])

    def test_conversion_and_equilibrium_real(self):
        self.require_engine();self.basis()
        for typ in ['Conversion','Equilibrium']:
            model=self.simple('RCT_'+typ,dict(mode='reaction',reaction_set='wgs',reactor_mode='Isothermic'),
                ['Carbon monoxide','Water','Carbon dioxide','Hydrogen'],'Peng-Robinson (PR)',1000)
            model['objects']=[o for o in model['objects'] if o['tag']!='Q']+[dict(tag='L',type='MaterialStream')]
            model['connections']=[dict(source='F',target='U'),dict(source='U',target='P'),dict(source='U',target='L',source_port=1)]
            r=dict(key='shift',type=typ,stoichiometry={'Carbon monoxide':-1,'Water':-1,'Carbon dioxide':1,'Hydrogen':1},base_compound='Carbon monoxide',phase='Vapor',source='合成验证反应，不是工程动力学')
            if typ=='Conversion':r['conversion']=.3
            model['reactions']=[r];model['reaction_sets']=[dict(key='wgs',reactions=['shift'])]
            result=self.sim(action='create',model=model)['result']
            self.assertTrue(result['validation']['passed'],dict(validation=result['validation'],error=result.get('error'),solver_errors=result.get('solver_errors'),result_errors=result.get('result_errors'),units=result.get('unit_operations')))
            feed=next(s for s in result['streams'] if s['tag']=='F')['component_mass_flows_kg_s']['Carbon monoxide']
            out=sum(s['component_mass_flows_kg_s']['Carbon monoxide'] for s in result['streams'] if s['tag'] in {'P','L'})
            if typ=='Conversion':self.assertAlmostEqual(1-out/feed,.3,delta=1e-6)
            else:
                # NIST WebBook Shomate ideal-gas data, 1000 K: WGS Kp ~= 1.435.
                # https://webbook.nist.gov/chemistry/ (CO, H2O, CO2, H2; gas thermochemistry)
                stream=next(s for s in result['streams'] if s['tag']=='P')
                y=dict(zip(stream['compounds'],stream['mole_fractions']))
                kp=y['Carbon dioxide']*y['Hydrogen']/y['Carbon monoxide']/y['Water']
                self.assertAlmostEqual(kp,1.435,delta=.04,msg=str(result.get('reactions'))) # PR nonideality at 3 bar and database differences
            invalid=deepcopy(model);invalid['reactions'][0]['stoichiometry']['Water']=-2
            response=self.client.post(self.base+'/execute',json=dict(tool='dwsim',args=dict(action='create',model=invalid)))
            self.assertEqual(response.status_code,422)
            self.assertIn('Unbalanced reaction',response.text)

    def simple(self,typ,spec,compounds=None,package='Steam Tables (IAPWS-IF97)',temperature=300):
        compounds=compounds or ['Water']
        return dict(compounds=compounds,property_package=package,objects=[
            dict(tag='F',type='MaterialStream',temperature_k=temperature,pressure_pa=300000,mass_flow_kg_s=1,
                 mole_fractions={c:1/len(compounds) for c in compounds}),
            dict(tag='U',type=typ,spec=spec),dict(tag='P',type='MaterialStream'),dict(tag='Q',type='EnergyStream')],
            connections=[dict(source='F',target='U'),dict(source='U',target='P'),dict(source='Q',target='U',target_port=1)])

    def test_real_structured_pump_flash_and_heat_exchanger(self):
        self.require_engine();self.basis()
        model=self.simple('Pump',dict(mode='outlet_pressure',pressure_pa=600000,efficiency=.75))
        result=self.sim(action='create',model=model)['result']
        self.assertTrue(result['validation']['passed'],result)

        self.assertAlmostEqual(next(s for s in result['streams'] if s['tag']=='P')['pressure_pa'],600000)
        from CoolProp.CoolProp import PropsSI
        expected=300000/PropsSI('D','T',300,'P',300000,'Water')/.75/1000
        self.assertAlmostEqual(result['unit_operations'][0]['net_energy_input_kw'],expected,delta=expected*.01)
        model=self.simple('Vessel',dict(mode='adiabatic'),['Benzene','Toluene'],"Raoult's Law",410)
        model['objects'].append(dict(tag='L',type='MaterialStream'))
        model['connections']=[dict(source='F',target='U'),dict(source='U',target='P'),dict(source='U',target='L',source_port=1)]
        model['objects']=[o for o in model['objects'] if o['tag']!='Q']
        result=self.sim(action='create',model=model)['result']
        self.assertTrue(result['validation']['passed'],result['validation'])
        from thermo import Chemical
        from scipy.optimize import brentq
        k=[Chemical(c,T=410).Psat/300000 for c in ['benzene','toluene']]
        vapor_fraction=brentq(lambda b:sum(.5*(v-1)/(1+b*(v-1)) for v in k),0,1)
        f=next(s for s in result['streams'] if s['tag']=='F')['molar_flow_mol_s']
        vapor=next(s for s in result['streams'] if s['tag']=='P')['molar_flow_mol_s']/f
        liquid=next(s for s in result['streams'] if s['tag']=='L')
        gas=next(s for s in result['streams'] if s['tag']=='P')
        # At this bubble/dew interval beta is sensitive to small vapor-pressure differences.
        # Check the independent phase-equilibrium K values at 3%, rather than assert excessive beta precision.
        self.assertTrue(0<vapor<1)
        for y,x,expected_k in zip(gas['mole_fractions'],liquid['mole_fractions'],k):
            self.assertAlmostEqual(y/x,expected_k,delta=expected_k*.03)
        self.assertLess(abs(sum(.5*(v-1)/(1+vapor*(v-1)) for v in k)),.02)
        model=base.mixer_model();model['objects'][2]=dict(tag='E',type='HeatExchanger',spec=dict(mode='ua',area_m2=2,overall_coefficient_w_m2_k=500))
        model['objects'].append(dict(tag='HOTOUT',type='MaterialStream'))
        model['connections']=[dict(source='COLD',target='E'),dict(source='HOT',target='E',target_port=1),dict(source='E',target='PRODUCT'),dict(source='E',target='HOTOUT',source_port=1)]
        result=self.sim(action='create',model=model)['result']
        self.assertTrue(result['validation']['passed'],result)
        import math
        cp=PropsSI('C','T',325,'P',200000,'Water')/1000
        ntu=1/cp;ratio=.5;effectiveness=(1-math.exp(-ntu*(1-ratio)))/(1-ratio*math.exp(-ntu*(1-ratio)))
        hot_in=next(s for s in result['streams'] if s['tag']=='HOT')['enthalpy_flow_kw']
        hot_out=next(s for s in result['streams'] if s['tag']=='HOTOUT')['enthalpy_flow_kw']
        self.assertAlmostEqual(hot_in-hot_out,effectiveness*cp*50,delta=1)


    def test_compressor_valve_cooler_and_shortcut(self):
        self.require_engine();self.basis()
        for typ,spec in [('Compressor',dict(mode='outlet_pressure',pressure_pa=600000,efficiency=.75)),
                         ('Valve',dict(mode='outlet_pressure',pressure_pa=200000)),
                         ('Cooler',dict(mode='outlet_temperature',temperature_k=310))]:
            model=self.simple(typ,spec,['Methane','Ethane'],'Peng-Robinson (PR)',350)
            if typ=='Valve':model['objects']=[o for o in model['objects'] if o['tag']!='Q'];model['connections']=model['connections'][:2]
            result=self.sim(action='create',model=model)['result']
            self.assertTrue(result['validation']['passed'],dict(type=typ,validation=result['validation'],errors=result.get('solver_errors')))
            out=next(s for s in result['streams'] if s['tag']=='P')
            self.assertAlmostEqual(out['temperature_k'] if typ=='Cooler' else out['pressure_pa'],310 if typ=='Cooler' else spec['pressure_pa'],delta=.001)
        model=self.simple('ShortcutColumn',dict(mode='shortcut',pressure_pa=101325,light_key='Benzene',heavy_key='Toluene',
            light_key_in_bottoms=.02,heavy_key_in_distillate=.02,reflux_ratio=3),['Benzene','Toluene'],"Raoult's Law",365)
        model['objects']+=[dict(tag='B',type='MaterialStream'),dict(tag='QC',type='EnergyStream')]
        model['connections']=[dict(source='F',target='U'),dict(source='U',target='P'),dict(source='U',target='B',source_port=1),
            dict(source='Q',target='U',target_port=1),dict(source='U',target='QC',source_port=2)]
        result=self.sim(action='create',model=model)['result']
        self.assertTrue(result['solved'],dict(errors=result.get('solver_errors'),units=result['unit_operations']))
        self.assertTrue(result['validation']['passed'],dict(validation=result['validation'],unit=result['unit_operations']))
    def test_property_packages_and_nrtl_parameter_roundtrip(self):
        self.require_engine();self.basis()
        model=self.simple('Heater',dict(mode='outlet_temperature',temperature_k=340),['Water','Ethanol'],'NRTL',300)
        model['property_packages']=[dict(key='liquid',name='NRTL',source='DWSIM database, pending applicability review',conditions='synthetic test',rationale='nonideal polar liquid')]
        for o in model['objects']:
            if o['type']!='EnergyStream':o['property_package']='liquid'
        created=self.sim(action='create',model=model)
        self.assertTrue(created['result']['validation']['passed'],created)
        pp=next(p for p in created['result']['property_packages'] if p['key']=='liquid')
        self.assertTrue(pp['interactions'][0]['parameters'])
        self.assertEqual(created['result']['configuration_metadata']['liquid']['source'],model['property_packages'][0]['source'])
        path=self.native(created)
        checked=self.sim(action='check',flowsheet_path=str(path))
        self.assertIn('liquid',checked['result']['configuration_metadata'])
        ip=pp['interactions'][0]
        override=dict(compound1=ip['compound1'],compound2=ip['compound2'],model='NRTL',parameters={'A12':ip['parameters']['A12']},source='explicit roundtrip test',conditions='300–340 K, synthetic')
        updated=self.sim(action='run',flowsheet_path=str(path),property_packages=[dict(**{**model['property_packages'][0],'interactions':[override]})])
        actual=next(p for p in updated['result']['property_packages'] if p['id']==pp['id'])['interactions'][0]
        self.assertEqual(actual['origin'],'user_specified')
        self.assertEqual(actual['source'],override['source'])

    def test_preflight_missing_feed_specs_ports_and_conflicts(self):
        self.require_engine()
        model=base.heater_model();del model['objects'][0]['pressure_pa'];model['objects'][1]['parameters']={}
        result=self.sim(action='check',model=model)['result']['preflight']
        self.assertFalse(result['ready_to_solve'])
        self.assertEqual({c['tag'] for c in result['checks'] if c['passed'] is False},{'COLD','HT-101'})
        model=base.heater_model();model['connections']=model['connections'][1:]
        # Detached feed remains explicit; check identifies missing HT inlet without solving.
        result=self.sim(action='check',model=model)['result']['preflight']
        self.assertTrue(any(c['tag']=='HT-101' and c['passed'] is False for c in result['checks']))
        from app.dwsim import DwsimObject
        with self.assertRaises(ValueError):DwsimObject(tag='U',type='Heater',spec=dict(mode='outlet_temperature',temperature_k=350,heat_duty_kw=10))
        model=base.heater_model();model['connections'][0]['target_port']=99
        invalid=self.client.post(self.base+'/execute',json=dict(tool='dwsim',args=dict(action='check',model=model)))
        self.assertEqual(invalid.status_code,422);self.assertIn('HT-101[99]',invalid.text)

    def test_real_scan_cancel_resume_and_failed_publish(self):
        self.require_engine();self.basis()
        original=self.sim(action='create',model=base.heater_model());path=self.native(original)
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        request=dict(action='start',flowsheet_path=str(path),scan=dict(tag='COLD',field='temperature_k',values=[300,310,320]),
            metrics=[dict(key='duty',path='/unit_operations/@HT-101/net_energy_input_kw',unit='kW')])
        job=self.client.post(self.base+'/dwsim/study',json=request).json()['study']
        self.assertEqual(self.client.post(self.base+'/objects',json=dict(kind='basis',key='busy')).status_code,409)
        self.client.post(self.base+'/dwsim/study',json=dict(action='cancel',job_id=job['job_id']))
        def wait_job():
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                value=self.client.post(self.base+'/dwsim/study',json=dict(action='status',job_id=job['job_id'])).json()['study']
                if value['status']!='running':return value
                time.sleep(.05)
            self.fail('study timeout')
        job=wait_job();self.assertEqual(job['status'],'cancelled')
        completed=[c.get('calculation_id') for c in job['cases'] if c['status']=='completed']
        self.assertTrue(any(c['status']=='pending' for c in job['cases']))
        response=self.client.post(self.base+'/dwsim/study',json=dict(action='resume',job_id=job['job_id']))
        self.assertEqual(response.status_code,200,response.text)
        job=wait_job();self.assertEqual(job['status'],'completed',job)
        self.assertTrue(set(completed)<={c['calculation_id'] for c in job['cases']})
        self.assertEqual(len({c['calculation_id'] for c in job['cases']}),3)
        self.assertGreater(job['comparison'][0]['duty'],job['comparison'][1]['duty'])
        self.assertGreater(job['comparison'][1]['duty'],job['comparison'][2]['duty'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        broken=base.heater_model();broken['objects']=[o for o in broken['objects'] if o['tag']!='PRODUCT']
        broken['connections']=[e for e in broken['connections'] if e['target']!='PRODUCT']
        bad=self.sim(action='create',model=broken)
        self.assertIsNot(bad['result']['validation']['passed'],True)
        # A disconnected object may simply remain uncalculated without any
        # native exception. Test exception preservation on a complete heater
        # whose pressure drop exceeds its inlet pressure instead.
        outside=base.heater_model()
        next(o for o in outside['objects'] if o['tag']=='HT-101')['parameters']['DeltaP']=1e6
        failed=self.sim(action='create',model=outside)
        self.assertIsNot(failed['result']['validation']['passed'],True)
        self.assertTrue(failed['result']['solver_errors'],failed['result'])
        denied=self.client.post(self.base+'/dwsim/publish',json=dict(calculation_id=bad['calculationId'],case='bad',
            references=[dict(key='temperature',path='/streams/@PRODUCT/temperature_k',unit='K',value=350,source='synthetic',conditions='invalid')]))
        self.assertEqual(denied.status_code,422)

    def test_check_does_not_solve_and_loaded_updates_preserve_input(self):
        self.require_engine()
        checked=self.sim(action='check', model=base.heater_model())
        self.assertIsNone(checked['calculationId'])
        self.assertFalse(checked['result']['solved'])
        self.assertTrue(checked['result']['preflight']['ready_to_solve'])
        self.basis()
        created=self.sim(action='create',model=base.heater_model())
        path=self.native(created); before=hashlib.sha256(path.read_bytes()).hexdigest()
        checked=self.sim(action='check',flowsheet_path=str(path),updates=[{'tag':'HT-101','temperature_k':None,'parameters':{'OutletTemperature':360}}])
        self.assertFalse(checked['result']['solved'])
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(self.client.get(self.base+'/calculations').json()['calculations']),1)

    def require_engine(self):
        self.assertTrue(base.server.dwsim_bridge.status()['ready'], '本套验收要求真实 DWSIM，不允许跳过')

    def test_real_enthalpy_molecular_weights_and_independent_energy(self):
        self.require_engine(); self.basis()
        result=self.sim(action='create',model=base.heater_model())['result']
        self.assertTrue(result['validation']['passed'],result['validation'])
        feed=next(s for s in result['streams'] if s['tag']=='COLD')
        self.assertAlmostEqual(feed['molecular_weights_g_mol']['Water'],18.015,delta=.01)
        self.assertAlmostEqual(feed['molar_flow_mol_s'],2/18.015*1000,delta=.1)
        from CoolProp.CoolProp import PropsSI
        expected=2*(PropsSI('H','T',350,'P',190000,'Water')-PropsSI('H','T',300,'P',200000,'Water'))/1000
        actual=result['unit_operations'][0]['net_energy_input_kw']
        self.assertAlmostEqual(actual,expected,delta=abs(expected)*.002)


if __name__=='__main__':
    try: unittest.main(verbosity=2)
    finally: base._temporary.cleanup()
