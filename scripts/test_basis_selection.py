"""Deterministic input selection checks, with no project or external data writes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.basis_selection import BasisSelectionIn, QuantitySelection, apply_selection, select_quantity


def candidate(ident='fallback', kind='assumption', value=8, unit='kg/s', **updates):
    return dict(id=ident, source_type=kind, value=value, unit=unit,
                conditions='基准总质量流量', source='测试合成来源，非工程数据',
                rationale='仅验证候选选取', applicability_reason='适用于测试基准') | updates


def quantity(candidates=None, **updates):
    return dict(key='flow', unit='kg/s', conditions='基准总质量流量',
                candidates=candidates or [candidate()], search_summary='测试候选，未做外部搜索',
                selection_reason='验证来源优先级及适用性', **updates)


def derived(ident='derived', assumed=False):
    return candidate(ident, 'derived', 10, derivation=dict(method='质量除以时间', formula='flow=mass/time', inputs=[
        dict(key='mass', value=36000, unit='kg', source_type='user', source='测试题设'),
        dict(key='time', value=3600, unit='s', source_type='assumption' if assumed else 'user', source='测试输入')]))


class BasisSelectionTests(unittest.TestCase):
    def select(self, data, previous=None):
        return select_quantity(QuantitySelection.model_validate(data), previous)

    def test_user_has_priority_and_original_units_survive(self):
        result = self.select(quantity([candidate(), candidate('ref', 'reference', 9), candidate('user', 'user', 36, 't/h')]))
        self.assertEqual(result['value'], 10)
        self.assertEqual(result['origin'], 'user')
        self.assertFalse(result['assumed'])
        self.assertEqual(result['selection']['candidates'][2]['unit'], 't/h')
        with self.assertRaisesRegex(ValueError, '最高可用优先级'):
            self.select(quantity([candidate(), candidate('user', 'user')], preferred_candidate_id='fallback'))

    def test_reference_and_supported_derivation_are_peers(self):
        result = self.select(quantity([candidate('ref', 'reference', 9), derived()], preferred_candidate_id='derived'))
        self.assertEqual(result['selection']['priority'], 1)
        self.assertEqual(result['value'], 10)
        self.assertEqual(len(result['derivation']['inputs']), 2)
        self.assertFalse(result['selection']['evidence_verified'])

    def test_assumptions_propagate_and_user_assumption_still_wins(self):
        result = self.select(quantity([derived(assumed=True), candidate('ref', 'reference', 9)]))
        self.assertEqual(result['source_type'], 'reference')
        fallback = self.select(quantity([derived(assumed=True)]))
        self.assertTrue(fallback['assumed'])
        self.assertTrue(fallback['sensitivity_required'])
        self.assertEqual(fallback['origin'], 'assumption')
        result = self.select(quantity([candidate('ref', 'reference'), candidate('user', 'user', assumed=True)]))
        self.assertEqual(result['source_type'], 'user')
        self.assertEqual(result['selection']['priority'], 0)
        self.assertEqual(result['origin'], 'assumption')

    def test_applicability_bounds_and_explicit_exclusions(self):
        result = self.select(quantity([candidate('user', 'user', -1), candidate('ref', 'reference', 4)], minimum=0))
        self.assertEqual(result['value'], 4)
        self.assertFalse(result['selection']['decisions'][0]['eligible'])
        self.assertIn('上下界', result['selection']['decisions'][0]['reason'])
        result = self.select(quantity([candidate('user', 'user'), candidate()], exclusions={'user': '旧工况数据，已记录不适用'}))
        self.assertEqual(result['origin'], 'assumption')
        self.assertIn('旧工况', result['selection']['exclusions']['user'])
        result = self.select(quantity([candidate('user', 'user', applicable=False), candidate()]))
        self.assertEqual(result['source_type'], 'assumption')

    def test_partial_updates_preserve_other_quantities_candidates_and_history(self):
        req = dict(kind='basis', key='feed', expected_revision=0, reason='测试补齐', quantities=[quantity()])
        first = apply_selection({}, BasisSelectionIn.model_validate(req), 'test-time')
        first['quantities'].append(dict(key='other', value=3))
        first['attributes']['keep'] = '保留'
        req.update(expected_revision=1, quantities=[quantity([candidate('user', 'user', 36, 't/h')])])
        second = apply_selection(first, BasisSelectionIn.model_validate(req), 'test-time-2')
        req.update(expected_revision=2, quantities=[quantity([candidate('fallback-2', value=1)])])
        third = apply_selection(second, BasisSelectionIn.model_validate(req), 'test-time-3')
        self.assertEqual(third['quantities'][0]['value'], 10)
        self.assertEqual(third['quantities'][1], first['quantities'][1])
        self.assertEqual(third['attributes']['keep'], '保留')
        self.assertEqual(len(third['quantities'][0]['selection']['candidates']), 3)
        self.assertEqual(len(third['selectionHistory']), 3)
        self.assertEqual(third['selectionHistory'][1]['changes'][0]['before']['value'], 8)
        self.assertEqual(first['quantities'][0]['value'], 8)

    def test_offsets_and_uncertainty_are_converted(self):
        c = candidate(unit='degC', value=100, low=90, high=110)
        data = quantity([c]); data['unit'] = 'K'
        result = self.select(data)
        self.assertAlmostEqual(result['value'], 373.15)
        self.assertAlmostEqual(result['uncertainty']['low'], 363.15)

    def test_invalid_units_identifiers_provenance_and_empty_fallback(self):
        for data in [quantity([candidate(unit='m')]), quantity([candidate(), candidate()]),
                     quantity([candidate(value=True)]), quantity([candidate(value=float('inf'))]),
                     quantity([candidate(kind='derived')]), quantity([candidate(source=' ')]),
                     quantity([candidate()], exclusions={'unknown': 'test'})]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.select(data)
        with self.assertRaisesRegex(ValueError, '继续检索'):
            self.select(quantity([candidate(applicable=False)]))

    def test_revision_candidates_and_conditions_are_not_reinterpreted(self):
        first = self.select(quantity())
        with self.assertRaisesRegex(ValueError, '新的候选 id'):
            self.select(quantity([candidate(value=7)]), first)
        changed = quantity(); changed['conditions'] = '不同工况'
        with self.assertRaisesRegex(ValueError, '情景对象'):
            self.select(changed, first)

    def test_adoption_preserves_legacy_user_and_assumption(self):
        old = dict(key='flow', value=8, unit='kg/s', origin='user', conditions='基准总质量流量')
        with self.assertRaisesRegex(ValueError, 'existing'):
            self.select(quantity(), old)
        with self.assertRaisesRegex(ValueError, 'source_type=user'):
            self.select(quantity([candidate('existing')]), old)
        result = self.select(quantity([candidate('existing', 'user'), candidate('new')]), old)
        self.assertEqual(result['origin'], 'user')
        old['origin'] = 'assumption'
        with self.assertRaisesRegex(ValueError, '假设标记'):
            self.select(quantity([candidate('existing', 'reference')]), old)

    def test_empty_legacy_placeholder_is_completed_and_retained(self):
        old = dict(key='flow', value=None, conditions='', note='尚未提供流量')
        result = self.select(quantity(), old)
        self.assertEqual(result['value'], 8)
        self.assertTrue(result['assumed'])
        self.assertEqual(result['selection']['legacy_quantity'], old)
        old.update(value=8, conditions='基准总质量流量')
        with self.assertRaisesRegex(ValueError, '原记录'):
            self.select(quantity([candidate('existing')]), old)


if __name__ == '__main__':
    unittest.main(verbosity=2)
