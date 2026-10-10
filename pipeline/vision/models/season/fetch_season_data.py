"""Archive official NBA 2025-26 regular-season shooting, efficiency and bios."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

SEASON = '2025-26'
TEAMS = {'HOU': 1610612745, 'DAL': 1610612742}
BASE = {'LeagueID': '00', 'Season': SEASON, 'SeasonType': 'Regular Season',
        'PerMode': 'Totals', 'LastNGames': 0, 'Month': 0, 'OpponentTeamID': 0,
        'PaceAdjust': 'N', 'PlusMinus': 'N', 'Rank': 'N', 'TeamID': 0, 'Period': 0}
SHOTS = {'LeagueID': '00', 'Season': SEASON, 'SeasonType': 'Regular Season',
         'PlayerID': 0, 'TeamID': 0, 'ContextMeasure': 'FGA', 'LastNGames': 0,
         'Month': 0, 'OpponentTeamID': 0, 'Period': 0, 'GameID': '',
         'DateFrom': '', 'DateTo': '', 'GameSegment': '', 'SeasonSegment': '',
         'Position': '', 'PlayerPosition': '', 'Location': '', 'Outcome': '',
         'RookieYear': '', 'VsConference': '', 'VsDivision': '', 'ContextFilter': ''}


def result_rows(data, name):
    sets = data['resultSets']
    sets = sets if isinstance(sets, list) else [sets]
    result = next(r for r in sets if r['name'] == name)
    return [dict(zip(result['headers'], row, strict=True)) for row in result['rowSet']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('data/season/2025-26'))
    args = parser.parse_args()
    raw_dir = args.output / 'raw'
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'season': SEASON, 'season_type': 'Regular Season', 'sources': {}, 'errors': {}}

    def fetch(key, endpoint, params):
        url = 'https://stats.nba.com/stats/' + endpoint + '?' + urllib.parse.urlencode(params)
        existing = manifest['sources'].get(key)
        if existing and existing['url'] == url:
            path = args.output / existing['file']
            raw = gzip.decompress(path.read_bytes())
            if hashlib.sha256(raw).hexdigest() != existing['sha256']:
                raise ValueError(f'Cached source hash mismatch: {key}')
            return key, existing, json.loads(raw)
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.nba.com/', 'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
        data = json.loads(raw)
        if 'resultSets' not in data:
            raise ValueError(f'Unexpected NBA response: {key}')
        path = raw_dir / (key + '.json.gz')
        path.write_bytes(gzip.compress(raw, mtime=0))
        evidence = {'url': url, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                    'file': str(path.relative_to(args.output)), 'sha256': hashlib.sha256(raw).hexdigest(),
                    'bytes_uncompressed': len(raw)}
        return key, evidence, data

    def batch(tasks):
        results = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fetch, *task): task[0] for task in tasks}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    key, evidence, data = future.result()
                    manifest['sources'][key] = evidence
                    manifest['errors'].pop(key, None)
                    results[key] = data
                except Exception as error:
                    manifest['errors'][key] = str(error)
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
                if len(results) % 25 == 0 or len(tasks) < 30:
                    print(f'Fetched {len(results)}/{len(tasks)}; key={key}; errors={len(manifest["errors"])}', flush=True)
        return results

    tasks = []
    for scope, team_id in [('NBA', 0), *TEAMS.items()]:
        for measure in ['Base', 'Advanced']:
            tasks.append((scope + '_' + measure.lower(), 'leaguedashplayerstats', {**BASE, 'TeamID': team_id, 'MeasureType': measure}))
        tasks.append((scope + '_zones', 'leaguedashplayershotlocations', {**BASE, 'TeamID': team_id, 'MeasureType': 'Base', 'DistanceRange': 'By Zone'}))
    stats = batch(tasks)
    if 'NBA_base' not in stats:
        raise RuntimeError('No league player census; see manifest errors')
    players = result_rows(stats['NBA_base'], 'LeagueDashPlayerStats')
    ids = sorted({int(p['PLAYER_ID']) for p in players if int(p['GP']) > 0})
    # Official NBA combine measurements. Null measurements stay null.
    batch([(f'combine_{year}', 'draftcombineplayeranthro', {'LeagueID': '00', 'SeasonYear': year}) for year in range(2000, 2026)])
    batch([(f'bio_{player_id}', 'commonplayerinfo', {'PlayerID': player_id, 'LeagueID': '00'}) for player_id in ids])
    # Coordinates for the two priority teams, not merely the current roster.
    batch([(scope + '_shots', 'shotchartdetail', {**SHOTS, 'TeamID': team_id})
           for scope, team_id in TEAMS.items()])
    # A whole-league request can silently cap at 102400 rows. Fetch calendar
    # months separately and require total conservation during artifact building.
    # This endpoint numbers season months: 1=October, ..., 7=April.
    batch([(f'NBA_shots_month_{month:02}', 'shotchartdetail', {**SHOTS, 'Month': season_month})
           for season_month, month in enumerate([10,11,12,1,2,3,4],1)])
    batch([(f'draft_history_{year}', 'drafthistory', {'LeagueID':'00', 'Season':str(year)})
           for year in [2015,2018]])
    if 'NBA_shots' in manifest['sources']:
        manifest['sources']['NBA_shots']['status'] = 'excluded_truncated_response_102400_rows'
    manifest['census_players_with_appearances'] = len(ids)
    required = ([t[0] for t in tasks] + [f'bio_{i}' for i in ids]
                + [f'combine_{year}' for year in range(2000,2026)]
                + ['HOU_shots','DAL_shots','draft_history_2015','draft_history_2018']
                + [f'NBA_shots_month_{month:02}' for month in [10,11,12,1,2,3,4]])
    manifest['complete_required_sources'] = all(key in manifest['sources'] for key in required)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'Player census {len(ids)}; required sources complete={manifest["complete_required_sources"]}; errors={manifest["errors"]}', flush=True)


if __name__ == '__main__':
    main()
