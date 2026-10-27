import copy
import importlib.util
import json
import math
import statistics
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'update_portfolio.py'
spec = importlib.util.spec_from_file_location('qt_updater', PATH)
qt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qt)


def business_days(start, end):
    out, cur = [], date.fromisoformat(start)
    while cur <= date.fromisoformat(end):
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


class PortfolioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = qt.load_config()

    def p1(self, config=None):
        return (config or self.config)['periods'][0]

    def make_bars(self, days=('2026-10-16', '2026-10-19', '2026-10-20'), growth=0.01, config=None):
        out = {}
        for x in self.p1(config)['positions']:
            base = float(x['official_reference_close'])
            out[x['ticker']] = {day: {'close': base * (1 + growth * i), 'dividend': 0, 'split': 0}
                                for i, day in enumerate(days)}
        return out

    # ---------------------------------------------------------------- NAV
    def test_opening_reconciles(self):
        p = self.p1()
        base = sum(qt.money(x['official_basis_usd']) for x in p['positions']) + qt.money(p['cash_usd'])
        self.assertEqual(base, qt.money(100000000))

    def test_only_observations_from_oct19(self):
        pts = qt.construct_series(self.config, self.make_bars(), today=date(2026, 10, 21))
        self.assertEqual([x['date'] for x in pts], ['2026-10-19', '2026-10-20'])
        self.assertAlmostEqual(pts[0]['return_pct'], .999989, places=4)

    def test_dividend_is_counted_once(self):
        bars = self.make_bars()
        base = float(self.p1()['positions'][0]['official_reference_close'])
        bars['IVV']['2026-10-20']['close'] = base * 1.02 - 1.00
        bars['IVV']['2026-10-20']['dividend'] = 1.0
        pts = qt.construct_series(self.config, bars, today=date(2026, 10, 21))
        self.assertAlmostEqual(pts[-1]['nav_usd'], 102000000 - 1134.93 * .02, delta=1)

    def test_missing_ticker_fails(self):
        bars = self.make_bars()
        del bars['GLD']['2026-10-20']
        with self.assertRaisesRegex(ValueError, 'Incomplete date'):
            qt.construct_series(self.config, bars, today=date(2026, 10, 21))

    def test_split_requires_manual_review(self):
        bars = self.make_bars()
        bars['IVV']['2026-10-20']['split'] = 2.0
        with self.assertRaisesRegex(ValueError, 'split'):
            qt.construct_series(self.config, bars, today=date(2026, 10, 21))

    def test_spurious_vendor_base_level_fails(self):
        bars = self.make_bars()
        bars['IVV']['2026-10-16']['close'] *= 1.15
        with self.assertRaisesRegex(ValueError, 'differs >5%'):
            qt.construct_series(self.config, bars, today=date(2026, 10, 21))

    def test_retroactive_material_revision_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fn = Path(tmp) / 'history.json'
            fn.write_text(json.dumps({'status': 'validated_eod', 'series': [{'date': '2026-10-19', 'nav_usd': 101000000}]}))
            with self.assertRaisesRegex(ValueError, 'revised >10 bps'):
                qt.validate_history([{'date': '2026-10-19', 'nav_usd': 102000000}], fn)

    def test_stale_series_fails_while_active(self):
        with self.assertRaisesRegex(ValueError, 'Stale'):
            qt.construct_series(self.config, self.make_bars(), today=date(2026, 10, 30))

    def test_frozen_after_valid_until_is_not_stale(self):
        days = business_days('2026-10-16', '2026-11-06')
        bars = self.make_bars(days, growth=0.0005)
        pts = qt.construct_series(self.config, bars, today=date(2026, 11, 20))
        self.assertEqual(pts[-1]['date'], '2026-11-06')
        self.assertEqual(qt.holdings_status(self.config, date(2026, 11, 20)), 'frozen_awaiting_next_period')

    def test_bars_after_valid_until_are_ignored(self):
        days = business_days('2026-10-16', '2026-11-13')
        pts = qt.construct_series(self.config, self.make_bars(days, growth=0.0005), today=date(2026, 11, 16))
        self.assertEqual(pts[-1]['date'], '2026-11-06')

    def test_chain_linking_second_period(self):
        cfg = copy.deepcopy(self.config)
        p2 = {'label': 'Round 2', 'reference_date': '2026-11-06', 'valid_until': '2026-11-27',
              'cash_usd': 0.0,
              'positions': [{'ticker': 'AAA', 'shares': 1000, 'official_basis_usd': 50000.0,
                             'official_reference_close': 50.0}]}
        cfg['periods'].append(p2)
        days1 = business_days('2026-10-16', '2026-11-06')
        bars = self.make_bars(days1, growth=0.0)  # flat round 1: NAV stays 100m
        bars['AAA'] = {'2026-11-06': {'close': 50.0, 'dividend': 0, 'split': 0},
                       '2026-11-09': {'close': 55.0, 'dividend': 0, 'split': 0}}
        pts = qt.construct_series(cfg, bars, today=date(2026, 11, 10))
        self.assertEqual(pts[-1]['date'], '2026-11-09')
        self.assertEqual(pts[-1]['period'], 'Round 2')
        self.assertAlmostEqual(pts[-1]['nav_usd'], 110000000, delta=0.01)
        self.assertAlmostEqual(pts[-1]['daily_return_pct'], 10 - 1e-9, places=5)

    # ---------------------------------------------------------------- risk-free and metrics
    def test_fred_parsing_skips_holidays(self):
        rows = qt.parse_fred_csv('observation_date,DGS1MO\n2026-10-15,4.10\n2026-10-16,.\n2026-10-19,4.12\n')
        self.assertEqual(rows, [('2026-10-15', 4.10), ('2026-10-19', 4.12)])

    def test_rf_uses_previous_close_over_252(self):
        rates = [('2026-10-15', 3.65), ('2026-10-16', 3.78), ('2026-10-19', 9.99)]
        rf = qt.rf_for_interval(rates, '2026-10-16', '2026-10-19', 7)
        self.assertAlmostEqual(rf, 0.0378 / 252, places=12)

    def test_rf_stale_fails(self):
        with self.assertRaisesRegex(ValueError, 'stale'):
            qt.rf_for_interval([('2026-10-01', 4.0)], '2026-10-16', '2026-10-19', 7)

    def test_metrics_match_independent_computation(self):
        days = business_days('2026-10-16', '2026-10-30')
        bars = self.make_bars(days, growth=0.0)
        moves = [0.004, -0.002, 0.003, -0.001, 0.002, 0.001, -0.003, 0.0025, 0.0005, -0.0015]
        for x in self.p1()['positions']:
            level = float(x['official_reference_close'])
            for day, mv in zip(days[1:], moves):
                level *= 1 + mv
                bars[x['ticker']][day]['close'] = level
        pts = qt.construct_series(self.config, bars, today=date(2026, 10, 31))
        rates = [(day, 3.80) for day in business_days('2026-10-01', '2026-10-30')]
        qt.attach_risk_free(self.config, pts, rates)
        s = qt.window_stats(self.config, pts, '2026-10-16', pts[-1]['date'])

        # independent recomputation from NAV levels
        navs = [100000000.0] + [p['nav_usd'] for p in pts]
        r = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
        dates = ['2026-10-16'] + [p['date'] for p in pts]
        rf = [0.038 / 252] * len(r)
        sd = statistics.stdev(r)
        self.assertEqual(s['n_returns'], 10)
        self.assertAlmostEqual(s['volatility_pct'], sd * math.sqrt(252) * 100, places=3)
        self.assertAlmostEqual(s['sharpe'], (statistics.fmean(r) - statistics.fmean(rf)) / sd * math.sqrt(252), places=3)
        self.assertAlmostEqual(s['return_pct'], (navs[-1] / navs[0] - 1) * 100, places=3)

    def test_risk_measures_hidden_below_min_obs(self):
        pts = qt.construct_series(self.config, self.make_bars(), today=date(2026, 10, 21))
        qt.attach_risk_free(self.config, pts, [('2026-10-16', 4.0), ('2026-10-19', 4.0)])
        s = qt.window_stats(self.config, pts, '2026-10-16', pts[-1]['date'])
        self.assertIsNone(s['volatility_pct'])
        self.assertIsNone(s['sharpe'])
        self.assertIsNotNone(s['return_pct'])

    def test_milestone_final_only_after_date_and_with_coverage(self):
        days = business_days('2026-10-16', '2026-11-06')
        pts = qt.construct_series(self.config, self.make_bars(days, growth=0.0005), today=date(2026, 11, 9))
        qt.attach_risk_free(self.config, pts, [(day, 4.0) for day in business_days('2026-10-01', '2026-11-06')])
        m = {x['key']: x for x in qt.build_metrics(self.config, pts, date(2026, 11, 9))['milestones']}
        self.assertEqual(m['first_round']['status'], 'final')
        self.assertEqual(m['first_round']['as_of_close'], '2026-11-06')
        self.assertEqual(m['first_round']['start_close'], '2026-10-16')
        self.assertEqual(m['second_round']['status'], 'pending')  # series frozen at 6 Nov
        m_early = {x['key']: x for x in qt.build_metrics(self.config, pts[:5], date(2026, 10, 26))['milestones']}
        self.assertEqual(m_early['first_round']['status'], 'pending')

    def test_second_round_is_measured_on_its_own_period(self):
        cfg = copy.deepcopy(self.config)
        cfg['periods'].append({'label': 'Round 2', 'reference_date': '2026-11-06', 'valid_until': '2026-11-27',
                               'cash_usd': 0.0, 'positions': [{'ticker': 'AAA', 'shares': 1000,
                                                               'official_basis_usd': 50000.0, 'official_reference_close': 50.0}]})
        days1 = business_days('2026-10-16', '2026-11-06')
        days2 = business_days('2026-11-06', '2026-11-27')
        bars = self.make_bars(days1, growth=0.001)          # round 1 rises about 1.5%
        level, bars['AAA'] = 50.0, {}
        for i, day in enumerate(days2):
            level = 50.0 if i == 0 else level * (1.002 if i % 2 else 0.999)
            bars['AAA'][day] = {'close': level, 'dividend': 0, 'split': 0}
        pts = qt.construct_series(cfg, bars, today=date(2026, 11, 30))
        qt.attach_risk_free(cfg, pts, [(day, 4.0) for day in business_days('2026-10-01', '2026-11-27')])
        m = {x['key']: x for x in qt.build_metrics(cfg, pts, date(2026, 11, 30))['milestones']}
        r2 = m['second_round']
        self.assertEqual(r2['status'], 'final')
        self.assertEqual((r2['start_close'], r2['as_of_close']), ('2026-11-06', '2026-11-27'))
        self.assertAlmostEqual(r2['return_pct'], (level / 50.0 - 1) * 100, places=3)   # not cumulative from 16 Oct
        self.assertEqual(r2['n_returns'], len(days2) - 1)
        nav6 = [p for p in pts if p['date'] == '2026-11-06'][0]['nav_usd']
        self.assertAlmostEqual(m['first_round']['return_pct'], (nav6 / 1e8 - 1) * 100, places=3)
        self.assertEqual(m['whole_challenge']['status'], 'pending')

    def test_non_contiguous_period_rejected(self):
        cfg = copy.deepcopy(self.config)
        cfg['periods'].append({'label': 'R2', 'reference_date': '2026-11-09', 'valid_until': '2026-11-27',
                               'cash_usd': 0, 'positions': [{'ticker': 'AAA', 'shares': 1,
                                                             'official_basis_usd': 10, 'official_reference_close': 10}]})
        with tempfile.TemporaryDirectory() as tmp:
            fn = Path(tmp) / 'c.json'
            fn.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError, 'contiguous'):
                qt.load_config(fn)


if __name__ == '__main__':
    unittest.main()
