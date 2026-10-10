import json
from pathlib import Path
import unittest
from build_season_artifacts import ratio,height_inches

ROOT=Path(__file__).resolve().parents[4]/'data/season/2025-26'


class SeasonContractTests(unittest.TestCase):
    def test_zero_attempts_are_missing_rate_not_zero_percent(self):
        self.assertIsNone(ratio(0,0))
        self.assertEqual(ratio(0,2),0)

    def test_fractional_inches_preserved(self):
        self.assertEqual(height_inches('7-4.75'),88.75)
        self.assertIsNone(height_inches('NA'))

    def test_priority_coverage_matches_supplied_roster_and_all_official_players(self):
        data=json.loads((ROOT/'visualization_data.json').read_text())
        self.assertEqual(len(data['profiles']),582)
        self.assertEqual(data['coverage']['HOU']['official_appeared'],16)
        self.assertEqual(data['coverage']['DAL']['official_appeared'],23)
        for result in data['coverage'].values():
            self.assertFalse(result['missing_ids']);self.assertFalse(result['extra_ids'])

    def test_three_bands_are_non_overlapping_and_full_two_point_is_separate(self):
        data=json.loads((ROOT/'visualization_data.json').read_text())
        for row in data['shooting']:
            self.assertEqual(row['near_rim_fga']+row['other_two_point_fga'],row['two_point_fga'])
            self.assertEqual(row['near_rim_fgm']+row['other_two_point_fgm'],row['two_point_fgm'])
            zones=[z for z in data['zones'] if z['scope']==row['scope'] and z['player_id']==row['player_id']]
            self.assertEqual(len(zones),7)
            self.assertNotIn('Corner 3',{z['zone'] for z in zones})
            self.assertEqual(sum(z['fga'] for z in zones),row['two_point_fga']+row['three_point_fga'])


class DefenseContractTests(unittest.TestCase):
    def test_defensive_census_keeps_all_players_even_without_tracking(self):
        data=json.loads((ROOT/'defensive_visualization_data.json').read_text())
        for scope,count in [('NBA',582),('HOU',16),('DAL',23)]:
            rows=[r for r in data['rows'] if r['scope']==scope]
            self.assertEqual(len({r['player_id'] for r in rows}),count)
            self.assertEqual(len(rows),count*7)
            for r in rows:
                if r['opponent_fga'] is None or r['opponent_fga']==0:self.assertIsNone(r['opponent_fg_pct'])

    def test_defensive_middle_band_uses_counts_and_does_not_invent_normal_baseline(self):
        data=json.loads((ROOT/'defensive_visualization_data.json').read_text())
        by_key={(r['scope'],r['player_id'],r['band']):r for r in data['rows']}
        for r in data['rows']:
            if r['band']!='other_two_point_ge_six_ft':continue
            near=by_key[(r['scope'],r['player_id'],'near_six_ft')];two=by_key[(r['scope'],r['player_id'],'two_point')]
            self.assertIsNone(r['opponents_normal_fg_pct_official'])
            if r['opponent_fga'] is not None:
                self.assertEqual(r['opponent_fga']+near['opponent_fga'],two['opponent_fga'])
                self.assertEqual(r['opponent_fgm']+near['opponent_fgm'],two['opponent_fgm'])

    def test_distance_version_is_separate_from_restricted_area_version(self):
        data=json.loads((ROOT/'visualization_data.json').read_text())
        self.assertEqual(len(data['distance_aligned_shooting']),621)
        raw={ (r['scope'],r['player_id']):r for r in data['shooting'] }
        for r in data['distance_aligned_shooting']:
            original=raw[(r['scope'],r['player_id'])]
            self.assertEqual(r['near_six_ft_fga']+r['other_two_point_ge_six_ft_fga'],original['two_point_fga'])
            self.assertEqual(r['three_point_fga'],original['three_point_fga'])

if __name__=='__main__':unittest.main()
