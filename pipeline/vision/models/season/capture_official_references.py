from playwright.sync_api import sync_playwright
from urllib.parse import urlparse, urlencode
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json
root=Path('data/season/2025-26/references');records=[]
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,proxy={'server':'http://proxy:8080'},args=['--no-sandbox','--disable-dev-shm-usage'])
 page=browser.new_page(viewport={'width':1440,'height':1500})
 page.route('**/*',lambda r:r.continue_() if urlparse(r.request.url).hostname in ['www.nba.com','stats.nba.com','cdn.nba.com'] else r.abort())
 for name,pid,season in [('flagg',1642843,'2025-26'),('durant',201142,'2024-25')]:
  u='https://www.nba.com/stats/events/?'+urlencode({'CFID':'','CFPARAMS':'','ContextMeasure':'FGA','GameID':'','PlayerID':pid,'Season':season,'SeasonType':'Regular Season','TeamID':'','flag':3,'sct':'plot','section':'game'})
  page.goto(u,wait_until='domcontentloaded',timeout=45000)
  page.get_by_role('button',name='Shot Zones',exact=True).wait_for(timeout=25000)
  page.wait_for_timeout(4000)
  section=page.locator('section[class*="EventsView_eventsTab"]').first
  for label,slug in [('Shot Zones','zones'),('Hex Map','hex'),('Heat','heat')]:
   page.get_by_role('button',name=label,exact=True).click();page.wait_for_timeout(1000)
   text=section.inner_text()
   if season not in text:raise ValueError('Wrong season displayed')
   path=root/f'official_{name}_{season}_{slug}.png';section.screenshot(path=str(path))
   records.append({'player_id':pid,'season':season,'mode':label,'file':path.name,'source_url':page.url,'captured_at':datetime.now(timezone.utc).isoformat(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'kind':'screenshot_of_official_NBA_chart_not_Team04_redraw'})
   print(path,flush=True)
 browser.close()
(root/'official_chart_references.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
