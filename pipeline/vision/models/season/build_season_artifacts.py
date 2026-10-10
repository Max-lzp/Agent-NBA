"""Build auditable season-wide and team-split shooting/profile artifacts."""
from collections import defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import io
from fetch_season_data import result_rows

ROOT = Path('data/season/2025-26')
ZONES = ['Restricted Area', 'In The Paint (Non-RA)', 'Mid-Range', 'Left Corner 3', 'Right Corner 3', 'Above the Break 3', 'Backcourt']
ROLE_NAMES = ['控场大脑/指挥官','王牌得分手/关键先生','高塔门神/内线核心','万能锋线/瑞士军刀','狙击手/空间射手','防守工兵/保安队长','替补微波炉/第六人','老将导师/定海神针','新秀潜力/双向待命']
ROLE_CODES = ['floor_general','primary_scorer','interior_anchor','versatile_wing','floor_spacer','defense_specialist','bench_scorer','veteran_mentor','developmental']


def write_csv(name, records):
    if not records:
        raise ValueError(f'Empty output: {name}')
    if name.endswith('.gz'):
        handle = io.StringIO(newline='')
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(records)
        (ROOT / name).write_bytes(gzip.compress(handle.getvalue().encode('utf-8'), mtime=0))
    else:
        with (ROOT / name).open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(records)


def ratio(made, attempted):
    return made / attempted if attempted else None


def height_inches(value):
    match = re.fullmatch(r'(\d+)-(\d+(?:\.\d+)?)', str(value))
    return int(match[1]) * 12 + float(match[2]) if match else None


def number(value):
    return int(value) if str(value).isdigit() else None


def main():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    if not manifest['complete_required_sources']:
        raise ValueError('Required official sources incomplete')

    def load(key):
        source = manifest['sources'][key]
        raw = gzip.decompress((ROOT / source['file']).read_bytes())
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError(f'Hash mismatch: {key}')
        return json.loads(raw)

    def stats(key):
        return result_rows(load(key), 'LeagueDashPlayerStats')

    combine = defaultdict(list)
    for year in range(2000, 2026):
        key = f'combine_{year}'
        if key not in manifest['sources']:
            continue
        for row in result_rows(load(key), 'Results'):
            if row.get('PLAYER_ID') and row.get('WINGSPAN'):
                combine[int(row['PLAYER_ID'])].append({'year': year, 'inches': float(row['WINGSPAN']), 'source_key': key, 'position': row['POSITION']})
    all_base = stats('NBA_base')
    profiles = []
    for player in all_base:
        player_id = int(player['PLAYER_ID'])
        info = result_rows(load(f'bio_{player_id}'), 'CommonPlayerInfo')[0]
        if int(info['PERSON_ID']) != player_id:
            raise ValueError('Wrong biography identity')
        inches = height_inches(info['HEIGHT'])
        measurements = sorted(combine[player_id], key=lambda r: r['year'])
        chosen = measurements[-1] if measurements else None
        draft_status = 'undrafted' if str(info['DRAFT_NUMBER']).lower() == 'undrafted' else 'drafted' if number(info['DRAFT_NUMBER']) else 'unknown'
        draft_source = f'bio_{player_id}'
        history_key = f'draft_history_{number(info["DRAFT_YEAR"])}'
        if draft_status == 'unknown' and history_key in manifest['sources']:
            history = result_rows(load(history_key), 'DraftHistory')
            if {r['OVERALL_PICK'] for r in history} == set(range(1,61)) and not any(
                r['PERSON_ID'] == player_id or r['PLAYER_NAME'].casefold() == player['PLAYER_NAME'].casefold() for r in history):
                draft_status = 'undrafted'
                draft_source += ';' + history_key
        profiles.append({'player_id': player_id, 'player_name': player['PLAYER_NAME'],
                         'gp_season_all_teams': player['GP'], 'team_label_in_league_response': player['TEAM_ABBREVIATION'],
                         'team_count': player['TEAM_COUNT'], 'height_ft_in_profile': info['HEIGHT'],
                         'height_cm_profile': round(inches * 2.54, 4) if inches else None,
                         'position_profile': info['POSITION'], 'draft_year': number(info['DRAFT_YEAR']) if draft_status=='drafted' else None,
                         'draft_round': number(info['DRAFT_ROUND']) if draft_status=='drafted' else None,
                         'draft_overall_pick': number(info['DRAFT_NUMBER']) if draft_status=='drafted' else None,
                         'draft_status': draft_status, 'draft_status_source_keys': draft_source,
                         'draft_eligibility_year_profile': number(info['DRAFT_YEAR']) if draft_status=='undrafted' else None,
                         'wingspan_cm_official_combine': round(chosen['inches'] * 2.54, 4) if chosen else None,
                         'wingspan_inches_official_combine': chosen['inches'] if chosen else None,
                         'wingspan_measurement_year': chosen['year'] if chosen else None,
                         'wingspan_source_key': chosen['source_key'] if chosen else None,
                         'wingspan_status': 'official_historical_measurement' if chosen else 'not_found_in_2000_2025_official_combine',
                         'bio_source_key': f'bio_{player_id}'})
    by_id = {p['player_id']: p for p in profiles}
    shooting, zone_output, efficiency, team_profiles = [], [], [], []
    team_ids = {}
    for scope in ['NBA', 'HOU', 'DAL']:
        base = {int(p['PLAYER_ID']): p for p in stats(scope + '_base')}
        advanced = {int(p['PLAYER_ID']): p for p in stats(scope + '_advanced')}
        if base.keys() != advanced.keys():
            raise ValueError(f'Statistics census mismatch: {scope}')
        team_ids[scope] = sorted(base)
        result = load(scope + '_zones')['resultSets']
        group, columns = result['headers']
        if columns['columnNames'][:6] != ['PLAYER_ID','PLAYER_NAME','TEAM_ID','TEAM_ABBREVIATION','AGE','NICKNAME'] or group['columnSpan'] != 3:
            raise ValueError('Unexpected zone schema')
        names = group['columnNames']
        for row in result['rowSet']:
            player_id = int(row[0])
            player = base[player_id]
            zones = {name: {'fgm': row[6 + index * 3], 'fga': row[7 + index * 3]} for index, name in enumerate(names)}
            missing = [name for name in ZONES if zones[name]['fgm'] is None or zones[name]['fga'] is None]
            if missing:
                known = [name for name in ZONES if name not in missing]
                # Nonnegative zone counts and a complete, conserved season total
                # prove these null count pairs are zero; no failed request is filled.
                if (sum(zones[n]['fga'] for n in known) != player['FGA'] or
                    sum(zones[n]['fgm'] for n in known) != player['FGM'] or
                    any(zones[n] != {'fgm': None, 'fga': None} for n in missing)):
                    raise ValueError(f'Unresolved null zone counts: {scope}/{player_id}')
                for name in missing:
                    zones[name] = {'fgm': 0, 'fga': 0}
            if sum(zones[name]['fga'] for name in ZONES) != player['FGA'] or sum(zones[name]['fgm'] for name in ZONES) != player['FGM']:
                raise ValueError(f'Zone vs base FGM/FGA mismatch: {scope}/{player_id}')
            near = zones['Restricted Area']
            twom, twoa = player['FGM'] - player['FG3M'], player['FGA'] - player['FG3A']
            restm, resta = twom - near['fgm'], twoa - near['fga']
            item = {'scope': scope, 'player_id': player_id, 'player_name': player['PLAYER_NAME'],
                    'team_label_in_response': player['TEAM_ABBREVIATION'], 'gp': player['GP'], 'minutes_total': player['MIN'],
                    'near_rim_fgm': near['fgm'], 'near_rim_fga': near['fga'], 'near_rim_fg_pct': ratio(near['fgm'],near['fga']),
                    'two_point_fgm': twom, 'two_point_fga': twoa, 'two_point_fg_pct': ratio(twom,twoa),
                    'other_two_point_fgm': restm, 'other_two_point_fga': resta, 'other_two_point_fg_pct': ratio(restm,resta),
                    'mid_range_fgm': zones['Mid-Range']['fgm'], 'mid_range_fga': zones['Mid-Range']['fga'], 'mid_range_fg_pct': ratio(zones['Mid-Range']['fgm'],zones['Mid-Range']['fga']),
                    'three_point_fgm': player['FG3M'], 'three_point_fga': player['FG3A'], 'three_point_fg_pct': ratio(player['FG3M'],player['FG3A']),
                    'source_keys': f'{scope}_base;{scope}_zones'}
            assert near['fga'] + resta + player['FG3A'] == player['FGA']
            shooting.append(item)
            for zone in ZONES:
                values = zones[zone]
                zone_output.append({'scope': scope, 'player_id': player_id, 'player_name': player['PLAYER_NAME'], 'zone': zone,
                                    **values, 'fg_pct': ratio(values['fgm'], values['fga']),
                                    'attempt_share': ratio(values['fga'], player['FGA']), 'source_key': scope + '_zones',
                                    'count_status': 'zero_proven_by_total_conservation' if zone in missing else 'official_counts'})
        if {r['player_id'] for r in shooting if r['scope'] == scope} != base.keys():
            raise ValueError('Shooting coverage incomplete')
        for player_id, player in base.items():
            adv = advanced[player_id]
            efficiency.append({'scope': scope, 'player_id': player_id, 'player_name': player['PLAYER_NAME'],
                               'gp': player['GP'], 'minutes_total': player['MIN'], 'possessions': adv['POSS'],
                               'off_rating_on_court': adv['OFF_RATING'], 'def_rating_on_court': adv['DEF_RATING'],
                               'net_rating_on_court': adv['NET_RATING'], 'ts_pct': adv['TS_PCT'],
                               'efg_pct': adv['EFG_PCT'], 'usage_pct': adv['USG_PCT'],
                               'source_keys': scope + '_base;' + scope + '_advanced'})
            if scope != 'NBA':
                team_profiles.append({'scope': scope, 'gp_for_team': player['GP'], **by_id[player_id]})
    uploaded = list(csv.DictReader((ROOT / 'user-submissions/original-roster-roles.csv').open(encoding='utf-8-sig')))
    submitted = defaultdict(set)
    audit, roles = [], []
    for row in uploaded:
        scope = {'Houston Rockets':'HOU','Dallas Mavericks':'DAL'}[row['team']]
        player_id = int(row['player_id'])
        if player_id in submitted[scope]:
            raise ValueError('Duplicate user roster identity')
        submitted[scope].add(player_id)
        verified = by_id.get(player_id)
        findings = []
        if not verified or player_id not in team_ids[scope]:
            findings.append('id_not_in_official_team_appearance_census')
        else:
            for user_key, official_key in [('draft_year','draft_year'),('draft_round','draft_round'),('draft_pick','draft_overall_pick')]:
                if number(row[user_key]) != verified[official_key]:
                    findings.append(f'{user_key}_differs_from_official_profile')
            user_inches = height_inches(row['height_ft_in'])
            official_inches = height_inches(verified['height_ft_in_profile'])
            if user_inches != official_inches:
                findings.append('height_differs_from_current_official_profile_not_necessarily_historical_error')
            wing = verified['wingspan_inches_official_combine']
            if wing is None:
                findings.append('uploaded_wingspan_not_independently_verified')
            elif height_inches(row['wingspan_ft_in']) != wing:
                findings.append('wingspan_differs_from_official_combine')
        audit.append({'scope':scope,'player_id':player_id,'player_name':row['player_name'],'findings':findings})
        roles.append({'scope':scope,'player_id':player_id,'player_name':row['player_name'],
                      'primary_role_code':dict(zip(ROLE_NAMES,ROLE_CODES))[row['primary_role']],
                      'secondary_role_codes':[code for name,code in zip(ROLE_NAMES,ROLE_CODES) if name in row['secondary_role']],
                      'idol_position':row['idol_position'],'idol_cue':row['idol_cue'],'idol_analogy':row['idol_analogy'],
                      'classification_status':'user_supplied_subjective_analogy_not_official_statistic'})
    coverage = {scope:{'official_appeared':len(team_ids[scope]),'uploaded':len(submitted[scope]),
                        'missing_ids':sorted(set(team_ids[scope])-submitted[scope]),
                        'extra_ids':sorted(submitted[scope]-set(team_ids[scope]))} for scope in ['HOU','DAL']}
    if any(v['missing_ids'] or v['extra_ids'] for v in coverage.values()):
        raise ValueError(f'Uploaded roster does not match official team coverage: {coverage}')
    for name, records in [('player_profiles.csv',profiles),('priority_team_profiles.csv',team_profiles),
                          ('player_shooting_three_bands.csv',shooting),('player_shot_zones.csv',zone_output),('player_efficiency.csv',efficiency)]:
        write_csv(name,records)
    write_csv('priority_team_shooting.csv',[p for p in shooting if p['scope'] != 'NBA'])
    write_csv('priority_team_efficiency.csv',[p for p in efficiency if p['scope'] != 'NBA'])
    user_by_key = {({'Houston Rockets':'HOU','Dallas Mavericks':'DAL'}[r['team']], int(r['player_id'])): r for r in uploaded}
    reviewed = []
    for row in team_profiles:
        user = user_by_key[(row['scope'], row['player_id'])]
        reviewed.append({**row, 'jersey_number_user_unverified': user['jersey_number'],
                         'position_five_way_user_interpretation': user['position'],
                         'wingspan_cm_user_unverified': float(user['wingspan_m'])*100 if user['wingspan_m'] else None,
                         'primary_role_user_subjective': user['primary_role'],
                         'secondary_role_user_subjective': user['secondary_role'],
                         'idol_analogy_user_subjective': user['idol_analogy']})
    write_csv('priority_team_profiles_reviewed.csv', reviewed)
    league_zones = {}
    for zone in ZONES:
        rows = [r for r in zone_output if r['scope']=='NBA' and r['zone']==zone]
        made,attempted = sum(r['fgm'] for r in rows),sum(r['fga'] for r in rows)
        league_zones[zone] = {'fgm':made,'fga':attempted,'fg_pct':ratio(made,attempted)}
    payload = {'season':'2025-26','season_type':'Regular Season','league_zone_baselines':league_zones,
               'profiles':profiles,'shooting':shooting,'zones':zone_output,'efficiency':efficiency,'coverage':coverage}
    (ROOT/'visualization_data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    (ROOT/'user_submission_audit.json').write_text(json.dumps({'coverage':coverage,'players':audit},ensure_ascii=False,indent=2)+'\n')
    (ROOT/'player_role_analogies.json').write_text(json.dumps({'official':False,'role_codes':dict(zip(ROLE_CODES,ROLE_NAMES)),'players':roles},ensure_ascii=False,indent=2)+'\n')
    distance_aligned = []
    for scope in ['NBA','HOU','DAL']:
        rows = ([r for month in [10,11,12,1,2,3,4]
                 for r in result_rows(load(f'NBA_shots_month_{month:02}'),'Shot_Chart_Detail')]
                if scope=='NBA' else result_rows(load(scope+'_shots'),'Shot_Chart_Detail'))
        event_keys = [(r['GAME_ID'],r['GAME_EVENT_ID']) for r in rows]
        if scope=='NBA':
            for month in [10,11,12,1,2,3,4]:
                expected=f'{2025 if month>=10 else 2026}{month:02}'
                if any(r['GAME_DATE'][:6]!=expected for r in result_rows(load(f'NBA_shots_month_{month:02}'),'Shot_Chart_Detail')):
                    raise ValueError(f'Wrong calendar month returned: {month}')
        if len(set(event_keys)) != len(event_keys):
            raise ValueError(f'Duplicate shot events: {scope}')
        # Coordinate and team labels are authoritative for that shot, unlike latest-team labels in aggregate stats.
        write_csv(scope.lower()+'_shot_coordinates.csv'+('.gz' if scope=='NBA' else ''),[{k:r[k] for k in ['GAME_ID','GAME_EVENT_ID','PLAYER_ID','PLAYER_NAME','TEAM_ID','TEAM_NAME','PERIOD','MINUTES_REMAINING','SECONDS_REMAINING','SHOT_TYPE','SHOT_ZONE_BASIC','SHOT_ZONE_AREA','SHOT_ZONE_RANGE','SHOT_DISTANCE','LOC_X','LOC_Y','SHOT_MADE_FLAG']} for r in rows])
        counts=defaultdict(lambda:[0,0])
        bands_by_id=defaultdict(lambda:{band:[0,0] for band in ['near_six_ft','other_two_point_ge_six_ft','three_point']})
        for r in rows:
            counts[r['PLAYER_ID']][0]+=1;counts[r['PLAYER_ID']][1]+=r['SHOT_MADE_FLAG']
            band = 'three_point' if r['SHOT_TYPE']=='3PT Field Goal' else 'near_six_ft' if r['SHOT_DISTANCE']<6 else 'other_two_point_ge_six_ft'
            bands_by_id[r['PLAYER_ID']][band][0]+=r['SHOT_MADE_FLAG']
            bands_by_id[r['PLAYER_ID']][band][1]+=1
        for p in stats(scope+'_base'):
            if counts[p['PLAYER_ID']] != [p['FGA'],p['FGM']]:
                raise ValueError(f'Shot coordinate coverage mismatch: {scope}/{p["PLAYER_ID"]}')
            row={'scope':scope,'player_id':p['PLAYER_ID'],'player_name':p['PLAYER_NAME'],
                 'source_key':'NBA_shots_month_10;11;12;01;02;03;04' if scope=='NBA' else scope+'_shots'}
            for band,(made,attempts) in bands_by_id[p['PLAYER_ID']].items():
                row.update({band+'_fgm':made,band+'_fga':attempts,band+'_fg_pct':ratio(made,attempts)})
            distance_aligned.append(row)
    write_csv('player_shooting_distance_aligned.csv',distance_aligned)
    payload['distance_aligned_shooting']=distance_aligned
    payload['distance_alignment_note']='Offense uses reported SHOT_DISTANCE integer feet; defense uses tracking distance categories. Bin names aligned, not identical event measurement or sample coverage. Restricted Area remains separately available.'
    (ROOT/'visualization_data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    manifest['artifacts']={p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in ROOT.iterdir() if p.suffix in ['.csv','.json','.gz'] and p.name!='manifest.json'}
    (ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'league_players':len(profiles),'official_wingspans':sum(bool(p['wingspan_inches_official_combine']) for p in profiles),'priority_coverage':coverage,'audited_user_rows':len(audit)},ensure_ascii=False))


if __name__=='__main__':
    main()
