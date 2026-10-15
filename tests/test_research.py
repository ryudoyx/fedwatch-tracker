import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fedwatch import archive, research
from fedwatch.store import Store


SEP_HTML = '''<table><thead><tr><th>Midpoint of target range or target level (Percent)</th>
<th>2026</th><th>2027</th><th>Longer run</th></tr></thead><tbody>
<tr><th>4.125</th><td>3</td><td>2</td><td>&nbsp;</td></tr>
<tr><th>4.375</th><td>2</td><td>3</td><td>5</td></tr></tbody></table>'''


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'test.sqlite')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def result(self, asof='2026-10-01', end=4.0, next_end=3.5):
        self.store.upsert_effr([{'date':'2025-12-31','rate':3.63,'lower':3.5,'upper':3.75}])
        self.store.save_result('calc',dt.date.fromisoformat(asof),3.75,'nyfed',[
            {'meeting':'2026-12-09','exp_lower':end,'dist':{400:1}},
            {'meeting':'2027-12-08','exp_lower':next_end,'dist':{350:1}}],[])

    def raw_macro(self):
        points = [{'date':f'{2025+(i//12)}-{i%12+1:02d}-01','value':100+i} for i in range(15)]
        return {'macro':{series:{'observations':points,'url':f'https://fred.stlouisfed.org/series/{series}',
                                  'label':series,'owner':spec['owner'],'fetched_at':'2026-10-15 08:00:00'}
                         for series,spec in research.SERIES.items()},'status':{},'revisions':[]}

    def test_policy_separates_realized_remaining_and_cross_year_changes(self):
        self.result('2026-09-24',3.9,3.6)
        self.result()
        rows=research.policy_book(self.store,'calc')['years']
        self.assertAlmostEqual(rows[0]['midpoint'],4.125)
        self.assertAlmostEqual(rows[0]['realized_bp'],25)
        self.assertAlmostEqual(rows[0]['remaining_bp'],25)
        self.assertAlmostEqual(rows[0]['calendar_year_bp'],50)
        self.assertAlmostEqual(rows[0]['changes']['1w']['bp'],10)
        self.assertAlmostEqual(rows[1]['calendar_year_bp'],-50)
        self.assertAlmostEqual(rows[1]['remaining_bp'],-25)
        self.assertIsNone(rows[1]['realized_bp'])

    def test_incomplete_year_and_stale_opening_are_not_estimated(self):
        self.store.save_result('calc',dt.date(2026,10,1),3.75,'nyfed',[
            {'meeting':'2026-10-28','exp_lower':4,'dist':{400:1}}],[])
        self.assertEqual(research.policy_book(self.store,'calc')['years'],[])
        self.result()
        self.store.conn.execute("update effr set date='2025-01-01'")
        self.store.conn.commit()
        row=research.policy_book(self.store,'calc')['years'][0]
        self.assertIsNone(row['calendar_year_bp'])
        self.assertIsNone(row['realized_bp'])

    def test_sep_reads_exact_dots_not_rounded_summary(self):
        sep=research.parse_sep(SEP_HTML,'2026-09-16','https://www.federalreserve.gov/')
        self.assertEqual(sep['years'][0]['median'],4.125)
        self.assertEqual(sep['years'][1]['median'],4.375)
        self.assertEqual(sep['years'][0]['participants'],5)
        self.assertEqual(sep['years'][2]['min'],4.375)
        with self.assertRaises(ValueError):
            research.parse_sep('<table><tr><th>unexpected layout</th></tr></table>','2026-09-16','url')

    def test_current_year_remains_after_last_scheduled_meeting(self):
        self.result('2026-12-10')
        # 实际快照在会议结束后仅包含次年会议。
        self.store.conn.execute("delete from meeting_calc where meeting='2026-12-09'")
        self.store.conn.execute("delete from probs where meeting='2026-12-09'")
        self.store.conn.commit()
        book=research.policy_book(self.store,'calc')
        year,next_year=book['years']
        self.assertEqual(year['year'],2026)
        self.assertEqual(year['remaining_bp'],0)
        self.assertEqual(year['calendar_year_bp'],25)
        self.assertEqual(next_year['calendar_year_bp'],-25)
        self.assertEqual(book['history'][0]['values'],[3.875])

    def test_sep_does_not_compare_a_future_publication_to_past_market(self):
        self.result('2026-08-01')
        sep=research.parse_sep(SEP_HTML,'2026-09-16','https://www.federalreserve.gov/')
        self.store.set_meta('research_data',{'sep':[sep]})
        self.assertIsNone(research.sep_comparison(self.store,'calc')['years'][0]['gap_bp'])
        self.result()
        self.assertAlmostEqual(research.sep_comparison(self.store,'calc')['years'][0]['gap_bp'],0)

    def test_macro_ratios_monthly_job_changes_and_missing_months(self):
        raw=self.raw_macro()
        self.store.set_meta('research_data',raw)
        rows=research.macro_scorecard(self.store)['metrics']
        self.assertAlmostEqual(rows[0]['value'],(114/102-1)*100)
        self.assertAlmostEqual(rows[0]['secondary'],(114/113-1)*100)
        self.assertEqual(rows[2]['value'],1)
        self.assertEqual(rows[2]['secondary'],1)
        # 缺一个月份时不跨月计算新增，也不将缺值当成零。
        raw['macro']['PAYEMS']['observations']=[r for r in raw['macro']['PAYEMS']['observations'] if r['date']!='2026-02-01']
        self.store.set_meta('research_data',raw)
        jobs=research.macro_scorecard(self.store)['metrics'][2]
        self.assertIsNone(jobs['value'])
        self.assertIsNone(jobs['secondary'])
        self.assertIsNone(jobs['trend'][-2]['value'])

    def test_series_rejects_malformed_nonfinite_and_future_data(self):
        text='observation_date,UNRATE\n'+''.join(f'2025-{i:02d}-01,4.1\n' for i in range(1,13))+'2026-01-01,4.2\n2026-02-01,4.3\n2026-12-01,9\n'
        rows=research.parse_series(text,'UNRATE',dt.date(2026,10,15))
        self.assertEqual(rows[-1]['date'],'2026-02-01')
        with self.assertRaises(ValueError):
            research.parse_series(text.replace('2025-01-01,4.1','2025-01-01,nan'),'UNRATE',dt.date(2026,10,15))

    def test_refresh_failures_keep_existing_data_and_archive_roundtrips(self):
        raw=self.raw_macro()
        self.store.set_meta('research_data',raw)
        with patch('fedwatch.research.sources._get',side_effect=research.sources.SourceError('offline')):
            warnings=research.refresh(self.store,None)
        self.assertEqual(len(warnings),6)
        self.assertEqual(self.store.get_meta('research_data')['macro'],raw['macro'])
        folder=self.root/'archive'
        archive.write(self.store,folder)
        other=Store(self.root/'other.sqlite')
        try:
            archive.load(other,folder)
            self.assertEqual(other.get_meta('research_data'),self.store.get_meta('research_data'))
        finally:
            other.close()

    def test_only_changes_between_captured_versions_are_revisions(self):
        raw=self.raw_macro()
        self.store.set_meta('research_data',raw)
        def response(session,url,**kwargs):
            if 'fredgraph.csv' not in url:
                raise research.sources.SourceError('SEP offline')
            series=url.split('id=')[1].split('&')[0]
            points=raw['macro'][series]['observations']
            content='observation_date,'+series+'\n'+''.join(f"{r['date']},{r['value']+(1 if series=='PAYEMS' and r['date']=='2026-03-01' else 0)}\n" for r in points)
            return SimpleNamespace(content=content.encode())
        with patch('fedwatch.research.sources._get',side_effect=response):
            research.refresh(self.store,None)
            research.refresh(self.store,None)
        revisions=self.store.get_meta('research_data')['revisions']
        self.assertEqual(len(revisions),1)
        self.assertEqual(revisions[0]['series'],'PAYEMS')
        self.assertEqual(revisions[0]['before'],114)
        self.assertEqual(revisions[0]['after'],115)


if __name__=='__main__':
    unittest.main()
