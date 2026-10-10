"""Fetch supplementary official player information and transaction evidence.

Run after fetch_historical_rosters.py; these responses are evidence, not a final
jersey interval roster. Current player jerseys must not replace season jerseys.
"""
import json,concurrent.futures,hashlib,urllib.request
from pathlib import Path
from datetime import datetime,timezone
from fetch_nba_sources import fetch,rows
base=Path(__file__).parent/'sources'
p=json.loads((base/'2025-26-historical-rosters.json').read_text()); census={t:{} for t in ['HOU','DAL']}
for g in p['games']:
 for t,rs in g['teams'].items():
  for r in rs:census[t][r['player_id']]=r['name']
ids=sorted({i for rs in census.values() for i in rs});out={}
def run(i):return i,fetch('commonplayerinfo',{'PlayerID':i,'LeagueID':'00'})
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
 for i,s in ex.map(run,ids):out[str(i)]=s
original=json.loads((base/'2025-26-official-sources.json').read_text()); extra={}
for i in [1630284,1641890,203957]:
 if f'career_{i}' not in original['sources']:extra[f'career_{i}']=fetch('playercareerstats',{'PlayerID':i,'PerMode':'Totals','LeagueID':'00'})
movement_url='https://stats.nba.com/js/data/playermovement/NBA_Player_Movement.json'
with urllib.request.urlopen(movement_url,timeout=25) as response:
 raw=response.read()
movement=json.loads(raw)
relevant=[r for r in movement['NBA_Player_Movement']['rows'] if int(r['PLAYER_ID']) in ids]
res={'fetched_at':datetime.now(timezone.utc).isoformat(),'player_info':out,'extra_careers':extra,'transactions':{'url':'https://stats.nba.com/js/data/playermovement/NBA_Player_Movement.json','sha256':hashlib.sha256(raw).hexdigest(),'fetched_at':datetime.now(timezone.utc).isoformat(),'records':relevant},'census':{t:[{'player_id':i,'name':n} for i,n in sorted(rs.items())] for t,rs in census.items()}}
(base/'2025-26-registration-evidence.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n');print({t:len(rs) for t,rs in census.items()})
for i in [1630284,1641890,203957]:
 s=extra.get(f'career_{i}',original['sources'].get(f'career_{i}'));print(i,[r for r in rows(s,'SeasonTotalsRegularSeason') if r['SEASON_ID']=='2025-26'])
