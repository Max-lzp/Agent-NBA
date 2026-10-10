"""Collect official defensive shooting bins; never equate them with the RA zone."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import gzip,hashlib,json,urllib.parse,urllib.request
from pathlib import Path
from fetch_season_data import result_rows,TEAMS
from build_season_artifacts import ratio,write_csv

ROOT=Path('data/season/2025-26')
CATEGORIES={'overall':('Overall','D_FGM','D_FGA','NORMAL_FG_PCT','PCT_PLUSMINUS'),
            'near_six_ft':('Less Than 6Ft','FGM_LT_06','FGA_LT_06','NS_LT_06_PCT','PLUSMINUS'),
            'near_ten_ft':('Less Than 10Ft','FGM_LT_10','FGA_LT_10','NS_LT_10_PCT','PLUSMINUS'),
            'two_point':('2 Pointers','FG2M','FG2A','NS_FG2_PCT','PLUSMINUS'),
            'three_point':('3 Pointers','FG3M','FG3A','NS_FG3_PCT','PLUSMINUS'),
            'greater_fifteen_ft':('Greater Than 15Ft','FGM_GT_15','FGA_GT_15','NS_GT_15_PCT','PLUSMINUS')}


def main():
    manifest=json.loads((ROOT/'manifest.json').read_text())
    def fetch(scope,team,band):
        params={'LeagueID':'00','Season':'2025-26','SeasonType':'Regular Season','PerMode':'Totals','TeamID':team,'LastNGames':0,'Month':0,'OpponentTeamID':0,'Period':0,'DefenseCategory':CATEGORIES[band][0]}
        key=f'{scope}_defense_{band}';url='https://stats.nba.com/stats/leaguedashptdefend?'+urllib.parse.urlencode(params)
        source=manifest['sources'].get(key)
        if source and source['url']==url:
            raw=gzip.decompress((ROOT/source['file']).read_bytes())
            if hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('Source hash mismatch')
        else:
            request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.nba.com/'})
            with urllib.request.urlopen(request,timeout=45) as response:raw=response.read()
            path=ROOT/'raw'/f'{key}.json.gz';path.write_bytes(gzip.compress(raw,mtime=0))
            source={'url':url,'file':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(raw).hexdigest(),'fetched_at':datetime.now(timezone.utc).isoformat(),'bytes_uncompressed':len(raw)}
        records=result_rows(json.loads(raw),'LeagueDashPTDefend')
        by_id={int(r['CLOSE_DEF_PERSON_ID']):r for r in records}
        if len(records)!=len(by_id):raise ValueError('Duplicate defender identities')
        return key,source,by_id
    data={}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures={executor.submit(fetch,scope,team,band):(scope,band) for scope,team in [('NBA',0),*TEAMS.items()] for band in CATEGORIES}
        for future in as_completed(futures):
            scope,band=futures[future]
            try:
                key,source,records=future.result();manifest['sources'][key]=source;manifest['errors'].pop(key,None);data[(scope,band)]=records
            except Exception as e:manifest['errors'][f'{scope}_defense_{band}']=str(e)
            (ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    if len(data)!=18:raise RuntimeError(f'Incomplete defensive sources: {manifest["errors"]}')
    payload=json.loads((ROOT/'visualization_data.json').read_text())
    records=[];coverage={}
    for scope in ['NBA','HOU','DAL']:
        census=[r for r in payload['shooting'] if r['scope']==scope]
        coverage[scope]={'appeared':len(census),'tracked_overall':sum(r['player_id'] in data[(scope,'overall')] for r in census),'per_category':{}}
        for band in CATEGORIES:coverage[scope]['per_category'][band]=sum(r['player_id'] in data[(scope,band)] for r in census)
        for player in census:
            id=player['player_id']
            for band,(_,made_key,attempt_key,normal_key,diff_key) in CATEGORIES.items():
                row=data[(scope,band)].get(id)
                if row:
                    made,attempts=row[made_key],row[attempt_key]
                    if not isinstance(made,(int,float)) or not isinstance(attempts,(int,float)) or not 0<=made<=attempts:raise ValueError('Invalid defender counts')
                else:made=attempts=None
                records.append({'scope':scope,'player_id':id,'player_name':player['player_name'],'band':band,
                                'opponent_fgm':made,'opponent_fga':attempts,'opponent_fg_pct':ratio(made,attempts) if row else None,
                                'opponents_normal_fg_pct_official':row[normal_key] if row else None,
                                'diff_from_opponents_normal_official':row[diff_key] if row else None,
                                'tracking_games':row['G'] if row else None,'frequency_official':row['FREQ'] if row else None,
                                'status':'official_tracking_counts' if row else 'no_row_in_complete_official_tracking_response',
                                'source_key':f'{scope}_defense_{band}'})
            two=data[(scope,'two_point')].get(id);near=data[(scope,'near_six_ft')].get(id)
            if two and near:
                made=two['FG2M']-near['FGM_LT_06'];attempts=two['FG2A']-near['FGA_LT_06']
                if not 0<=made<=attempts:raise ValueError('Inconsistent defensive subset counts')
            else:made=attempts=None
            records.append({'scope':scope,'player_id':id,'player_name':player['player_name'],'band':'other_two_point_ge_six_ft',
                            'opponent_fgm':made,'opponent_fga':attempts,'opponent_fg_pct':ratio(made,attempts) if attempts is not None else None,
                            'opponents_normal_fg_pct_official':None,'diff_from_opponents_normal_official':None,
                            'tracking_games':None,'frequency_official':None,'status':'derived_from_two_point_minus_under_six_ft_counts' if two and near else 'unresolved_due_to_missing_source_row',
                            'source_key':f'{scope}_defense_two_point;{scope}_defense_near_six_ft'})
    write_csv('player_defensive_shooting.csv',records)
    write_csv('priority_team_defensive_shooting.csv',[r for r in records if r['scope']!='NBA'])
    result={'season':'2025-26','season_type':'Regular Season','coverage':coverage,'rows':records,
            'three_band_order':['three_point','other_two_point_ge_six_ft','near_six_ft'],
            'definition_warning':'Under 6 feet is not NBA Restricted Area. Other 2PT at >=6 feet is not pure Mid-Range. Lower opponent FG% is better, but not a causal individual defense score.'}
    (ROOT/'defensive_visualization_data.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(coverage,ensure_ascii=False))


if __name__=='__main__':main()
