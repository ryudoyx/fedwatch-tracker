import datetime as dt
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from fedwatch import analysis, archive, calc, events
from fedwatch.store import Store


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'test.sqlite')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_both_quotes_survive_and_selection_stays_tradingview(self):
        day = dt.date(2026, 10, 14)
        self.store.upsert_prices({'ZQV26': {day: 96.1}}, 'tradingview')
        self.store.upsert_prices({'ZQV26': {day: 96.2}}, 'yahoo')
        self.assertEqual(self.store.q('select close from prices')[0][0], 96.1)
        self.assertEqual(len(self.store.q('select * from price_quotes')), 2)
        self.assertEqual(self.store.price_inputs(day + dt.timedelta(days=2))[0]['age_days'], 2)
        self.assertEqual(self.store.price_inputs(day + dt.timedelta(days=8)), [])
        self.store.close()
        self.store = Store(self.root / 'test.sqlite')
        self.assertEqual(len(self.store.q('select * from price_quotes')), 2)
        self.assertAlmostEqual(self.store.avg_rates(day)[(2026, 10)], 3.9)

    def test_archive_preserves_quotes_and_original_timestamps(self):
        day = dt.date(2026, 10, 14)
        for source, px in [('yahoo', 96.2), ('tradingview', 96.1)]:
            self.store.upsert_prices({'ZQV26': {day: px}}, source)
        self.store.conn.execute("update price_quotes set fetched_at='2026-10-15 08:00:00'")
        self.store.conn.execute("update prices set updated_at='2026-10-15 08:00:00'")
        self.store.conn.commit()
        folder = self.root / 'archive'
        archive.write(self.store, folder)
        other = Store(self.root / 'restored.sqlite')
        try:
            archive.load(other, folder)
            self.assertEqual([tuple(r) for r in self.store.q('select * from price_quotes order by source')],
                             [tuple(r) for r in other.q('select * from price_quotes order by source')])
            self.assertEqual(other.q('select updated_at from prices')[0][0], '2026-10-15 08:00:00')
        finally:
            other.close()

    def test_old_archive_marks_fetch_time_unknown(self):
        folder = self.root / 'old'
        folder.mkdir()
        (folder / archive.PRICES).write_text('trade_date,contract,close,source\n2026-10-14,ZQV26,96.1,yahoo\n')
        archive.load(self.store, folder)
        self.assertIn('未知', self.store.q('select fetched_at from price_quotes')[0][0])

    def test_existing_database_migrates_without_inventing_quotes(self):
        self.store.conn.execute("insert into prices values ('2026-10-14','ZQV26','2026-10',96.1,'original','yahoo')")
        self.store.conn.commit()
        self.store.close()
        self.store = Store(self.root / 'test.sqlite')
        rows = self.store.q('select * from price_quotes')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['fetched_at'], 'original')

    def test_extreme_paths_respect_zero_floor_and_normalize(self):
        steps = [{-2: 1.0}, {1: 0.5, 2: 0.5}]
        self.assertEqual(calc.extreme_distribution(steps, 'trough', 0.25), {-1: 1.0})
        # After reaching zero, the rebound starts from zero (not a negative shadow level).
        self.assertEqual(calc.extreme_distribution(steps, 'peak', 0.25), {0: 0.5, 1: 0.5})
        self.assertAlmostEqual(sum(calc.extreme_distribution(steps, 'peak', 0.25).values()), 1)

    def test_repricing_aligns_same_meeting_and_keeps_missing_values(self):
        meeting = '2027-03-17'
        def result(day, base, value):
            self.store.save_result('calc', dt.date.fromisoformat(day), base, 'nyfed',
                                   [{'meeting': meeting, 'exp_lower': value, 'dist': {400: 1.0}}], [])
        result('2026-10-01', 3.5, 4.0)
        result('2026-10-08', 3.75, 4.1)
        result('2026-10-09', 3.75, 4.2)
        out = analysis.repricing(self.store, 'calc')
        self.assertAlmostEqual(out['ranking'][0]['changes']['1d'], 10)
        self.assertAlmostEqual(out['ranking'][0]['changes']['1w'], 20)
        self.assertIsNone(out['ranking'][0]['changes']['1m'])
        self.assertEqual(analysis.repricing(self.store, 'cme')['dates'], [])

    def test_ics_filters_unrelated_and_cancelled_events(self):
        text = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Personal Income and \r\n Outlays\\, September\r\n'
                'DTSTART;VALUE=DATE-TIME:20261030T123000Z\r\nEND:VEVENT\r\n'
                'BEGIN:VEVENT\nSUMMARY:GDP\nDTSTART:20261029T123000Z\nEND:VEVENT\n'
                'BEGIN:VEVENT\nSUMMARY:Consumer Price Index\nDTSTART:20261014T123000Z\nSTATUS:CANCELLED\nEND:VEVENT')
        out = events.parse_ics(text, 'https://www.bea.gov/')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['date'], '2026-10-30')
        self.assertEqual(out[0]['kind'], 'PCE')

    def test_calendar_failure_preserves_cached_events_and_records_status(self):
        cached = [{'date': '2026-10-14', 'kind': 'CPI', 'label': 'CPI', 'url': events.FEEDS['bls']}]
        self.store.set_meta('macro_events', cached)
        with patch('fedwatch.events.sources._get', side_effect=events.sources.SourceError('offline')):
            warnings = events.refresh(self.store, None)
        self.assertEqual(len(warnings), 2)
        self.assertEqual(self.store.get_meta('macro_events'), cached)
        self.assertEqual(self.store.get_meta('event_status')['bls']['error'], 'offline')

    def test_quality_reports_stale_prices_and_source_spreads(self):
        day = dt.date(2026, 10, 14)
        self.store.upsert_prices({'ZQV26': {day: 96.1}}, 'tradingview')
        self.store.upsert_prices({'ZQV26': {day: 96.12}}, 'yahoo')
        self.store.upsert_prices({'ZQX26': {day-dt.timedelta(days=1): 96.0}}, 'yahoo')
        self.store.save_result('calc', day, 3.75, 'nyfed',
                              [{'meeting': '2026-10-28', 'exp_lower': 4, 'dist': {400:1}}], [])
        result = analysis.quality(self.store)
        self.assertEqual(result['stale_contracts'], 1)
        self.assertAlmostEqual(result['source_spreads'][0]['price_gap_bp'], 2)


if __name__ == '__main__':
    unittest.main()
