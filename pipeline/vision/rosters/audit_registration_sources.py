"""Produce a season-wide review census without guessing jersey/date intervals.

This audit is intentionally separate from 2025-26.json: historical HTML can
contain present-day profile numbers and membership dates need separate evidence.
"""
from collections import defaultdict
import csv
import json
from pathlib import Path

from build_roster import TEAMS, SHORT_NAMES
from fetch_nba_sources import rows

BASE = Path(__file__).parent / 'sources'


def main():
    historical = json.loads((BASE / '2025-26-historical-rosters.json').read_text())
    if not historical['complete']:
        raise ValueError('Incomplete game coverage')
    official = json.loads((BASE / '2025-26-official-sources.json').read_text())
    supplement = json.loads((BASE / '2025-26-registration-evidence.json').read_text())
    sources = {**official['sources'], **supplement['extra_careers']}
    observed = defaultdict(list)
    for game in historical['games']:
        for team, players in game['teams'].items():
            for player in players:
                observed[(team, player['player_id'])].append({**player, 'date': game['date'], 'url': game['url']})
    totals = {}
    for team, candidates in official['candidates'].items():
        for player in candidates:
            totals[(team, player['player_id'])] = {k: player[k] for k in ('games_played', 'games_started', 'minutes_total')}
    review = []
    for (team, player_id), observations in sorted(observed.items()):
        name = observations[0]['name']
        if (team, player_id) not in totals:
            career = [row for row in rows(sources[f'career_{player_id}'], 'SeasonTotalsRegularSeason')
                      if row['SEASON_ID'] == '2025-26' and int(row['TEAM_ID']) == TEAMS[team][0]]
            if career:
                raise ValueError(f'Played person missing verified totals: {team}/{player_id}')
            # A complete career response AND official game registration establish
            # zero appearances. Failed requests never reach this branch.
            totals[(team, player_id)] = {'games_played': 0, 'games_started': 0, 'minutes_total': 0.0}
        info = rows(supplement['player_info'][str(player_id)], 'CommonPlayerInfo')[0]
        primary = sorted({o['jersey'] for o in observations if o['jersey'] and o['source_field'].endswith('TeamPlayers')})
        inactive = sorted({o['jersey'] for o in observations if o['jersey'] and o['source_field'].endswith('.inactives')})
        all_numbers = sorted(set(primary + inactive))
        transactions = [r for r in supplement['transactions']['records'] if int(r['PLAYER_ID']) == player_id
                        and '2025-07-01' <= r['TRANSACTION_DATE'][:10] <= '2026-10-10'
                        and (int(r['TEAM_ID']) == TEAMS[team][0] or 'from ' + TEAMS[team][1] in r['TRANSACTION_DESCRIPTION'])]
        review.append({'team': team, 'player_id': player_id, 'name': name,
                      'short_name': SHORT_NAMES.get(name, name.split()[-1]),
                      'position': info['POSITION'], **totals[(team, player_id)],
                      'primary_observed_jerseys': primary, 'inactive_observed_jerseys': inactive,
                      'first_observed': observations[0]['date'], 'last_observed': observations[-1]['date'],
                      'jersey_conflict': len(all_numbers) > 1,
                      'current_profile_team': info['TEAM_ABBREVIATION'], 'current_profile_jersey': info['JERSEY'],
                      'transactions': transactions,
                      'registration_intervals_status': 'requires reviewed membership and number dates; observations are not registration dates',
                      'observations': observations})
    report = {'season': '2025-26', 'status': 'review evidence; NOT the final roster',
              'coverage': {'games': len(historical['games']), 'HOU': 19, 'DAL': 25},
              'critical_issue': 'DAL Robinson-Earl and Washington both reported as 25 by game-page primary metadata; requires independent historical number evidence',
              'players': review}
    assert len(review) == 44
    for team in TEAMS:
        assert sum(p['games_started'] for p in review if p['team'] == team) == 410
    (BASE / '2025-26-registration-review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    with (BASE / '2025-26-verified-totals.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['team', 'player_id', 'games_played', 'games_started', 'minutes_total'], lineterminator='\n')
        writer.writeheader()
        for key, value in sorted(totals.items()):
            writer.writerow({'team': key[0], 'player_id': key[1], **value})
    print('44 verified team-player totals; HOU 19 / DAL 25; GS sums 410 each; historical review saved')


if __name__ == '__main__':
    main()
