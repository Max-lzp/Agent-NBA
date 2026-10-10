"""Render standalone figures and an offline player explorer from verified data."""
import csv
import json
from pathlib import Path
import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle, Arc, Wedge
from matplotlib.colors import Normalize

ROOT = Path('data/season/2025-26')
OUT = ROOT / 'visualizations'
BANDS = [('three_point','3PT'),('other_two_point','Other 2PT'),('near_rim','Near rim (Restricted Area)')]


def court(ax):
    ax.add_patch(Rectangle((-25,-5.25),50,47,fill=False,lw=1.5,color='#bdc6d4'))
    ax.add_patch(Rectangle((-8,-5.25),16,19,fill=False,lw=1,color='#bdc6d4'))
    ax.add_patch(Arc((0,13.75),12,12,theta1=0,theta2=180,color='#bdc6d4'))
    ax.add_patch(Arc((0,0),8,8,theta1=0,theta2=180,color='#bdc6d4'))
    ax.plot([-22,-22],[-5.25,8.947],color='#bdc6d4');ax.plot([22,22],[-5.25,8.947],color='#bdc6d4')
    angle=math.degrees(math.acos(22/23.75))
    ax.add_patch(Arc((0,0),47.5,47.5,theta1=angle,theta2=180-angle,color='#bdc6d4'))
    ax.add_patch(Circle((0,0),.75,fill=False,color='#fff',lw=2));ax.plot([-3,3],[-1.25,-1.25],color='#fff')
    ax.set(xlim=(-26,26),ylim=(-6.25,42.75),aspect='equal');ax.axis('off')


def main():
    OUT.mkdir(exist_ok=True)
    payload=json.loads((ROOT/'visualization_data.json').read_text())
    defense_path=ROOT/'defensive_visualization_data.json'
    if defense_path.exists():
        payload['defense']=json.loads(defense_path.read_text())
    for scope,pid in [('DAL',1642843),('HOU',1641708),('HOU',201142)]:
        row=next(p for p in payload['shooting'] if p['scope']==scope and p['player_id']==pid)
        coords=list(csv.DictReader((ROOT/(scope.lower()+'_shot_coordinates.csv')).open()))
        points=[r for r in coords if int(r['PLAYER_ID'])==pid]
        xs=[float(r['LOC_X'])/10 for r in points];ys=[float(r['LOC_Y'])/10 for r in points]
        fig,axes=plt.subplots(1,2,figsize=(12,6.6),facecolor='#101827',gridspec_kw={'width_ratios':[1,1.12]})
        for ax in axes:ax.set_facecolor('#101827')
        density=axes[0].hexbin(xs,ys,gridsize=22,extent=(-25,25,-5.25,41.75),mincnt=1,cmap='YlOrRd',linewidths=.1)
        colorbar=fig.colorbar(density,ax=axes[0],fraction=.035,pad=.02)
        colorbar.set_label('Attempts per hexagon',color='#bdc6d4')
        colorbar.ax.tick_params(colors='#bdc6d4')
        court(axes[0]);axes[0].set_title('Attempt density | true shot coordinates',color='white',fontsize=12)
        axes[1].set_title('Three non-overlapping shooting bands',color='white',fontsize=12)
        labels=[]
        for index,(band,label) in enumerate(BANDS):
            made,attempts=row[band+'_fgm'],row[band+'_fga'];rate=row[band+'_fg_pct']
            league=[r for r in payload['shooting'] if r['scope']=='NBA']
            baseline=sum(r[band+'_fgm'] for r in league)/sum(r[band+'_fga'] for r in league)
            axes[1].barh(2-index,rate or 0,color=['#869cfa','#edbc70','#70ddd0'][index],height=.48,alpha=1 if attempts>=30 else .4)
            axes[1].plot([baseline,baseline],[2-index-.29,2-index+.29],color='white',lw=2)
            text=f'{rate:.1%}  |  {made}/{attempts}' if attempts else 'N/A | no attempts'
            labels.append(label);axes[1].text(.02,2-index+.35,text,color='white',fontsize=11)
        axes[1].set(yticks=[2,1,0],yticklabels=labels,xlim=(0,1),ylim=(-.7,2.8))
        axes[1].tick_params(colors='white');axes[1].set_xlabel('FG% | white tick = league attempt-weighted rate',color='#bdc6d4')
        for spine in axes[1].spines.values():spine.set_visible(False)
        fig.suptitle(f'{row["player_name"]} | {scope} | 2025-26 Regular Season',color='white',fontsize=18)
        outside=sum(not(-25<=x<=25 and -5.25<=y<=41.75) for x,y in zip(xs,ys))
        fig.text(.04,.035,f'Source: NBA Stats | Restricted Area is a defined zone, not all paint shots. Low samples (<30 FGA) faded.\nCourt shows half-court density ({outside} shots outside view); band rates include all official attempts. No predicted xFG.',color='#bdc6d4',fontsize=9)
        fig.tight_layout(rect=(0,.1,1,.93));fig.savefig(OUT/f'{scope.lower()}_{pid}_shooting.png',dpi=180,facecolor=fig.get_facecolor());plt.close(fig)
    players=[r for r in payload['efficiency'] if r['scope'] in ['HOU','DAL'] and r['minutes_total']>=500]
    fig,ax=plt.subplots(figsize=(11,7),facecolor='#101827');ax.set_facecolor('#101827')
    for team,color in [('HOU','#ef868c'),('DAL','#74b7f4')]:
        rs=[r for r in players if r['scope']==team]
        ax.scatter([r['off_rating_on_court'] for r in rs],[r['def_rating_on_court'] for r in rs],s=[40+r['minutes_total']/18 for r in rs],c=color,alpha=.8,label=team)
        for r in rs:ax.annotate(r['player_name'],(r['off_rating_on_court'],r['def_rating_on_court']),xytext=(4,4),textcoords='offset points',fontsize=7,color='white')
    ax.set_xlabel('On-court offensive rating | higher better',color='white');ax.set_ylabel('On-court defensive rating | lower better',color='white');ax.tick_params(colors='white');ax.invert_yaxis();ax.grid(alpha=.15);ax.legend()
    ax.set_title('2025-26 team-split on-court efficiency | 500+ team minutes',color='white')
    fig.text(.03,.025,'NBA Stats Advanced | points scored/allowed per 100 possessions while on court. Context includes teammates, opponents and lineup.\nNot individual scoring efficiency or a causal measure of individual defense. Circle size = team minutes; all players remain in CSV.',fontsize=9,color='#bdc6d4')
    fig.tight_layout(rect=(0,.08,1,1));fig.savefig(OUT/'priority_team_efficiency.png',dpi=180,facecolor=fig.get_facecolor());plt.close(fig)
    if 'defense' in payload and 'distance_aligned_shooting' in payload:
        offense=next(r for r in payload['distance_aligned_shooting'] if r['scope']=='DAL' and r['player_id']==1642843)
        bands=[('three_point','3PT'),('other_two_point_ge_six_ft','Other 2PT >=6 ft'),('near_six_ft','Near rim <6 ft')]
        fig,axes=plt.subplots(1,2,figsize=(12,6),facecolor='#101827',sharey=True)
        for ax in axes:ax.set_facecolor('#101827');ax.tick_params(colors='white')
        for index,(band,label) in enumerate(bands):
            row=next(r for r in payload['defense']['rows'] if r['scope']=='DAL' and r['player_id']==1642843 and r['band']==band)
            for ax,rate,made,attempts,color in [(axes[0],offense[band+'_fg_pct'],offense[band+'_fgm'],offense[band+'_fga'],'#70ddd0'),(axes[1],row['opponent_fg_pct'],row['opponent_fgm'],row['opponent_fga'],'#edbc70')]:
                ax.barh(2-index,rate or 0,color=color,height=.45)
                ax.text(.015,2-index+.29,f'{rate:.1%} | {made}/{attempts}' if rate is not None else 'No tracked sample',color='white')
        for ax in axes:
            ax.set(yticks=[2,1,0],yticklabels=[b[1] for b in bands],xlim=(0,1),ylim=(-.6,2.8));ax.set_xlabel('FG%',color='white')
            for spine in ax.spines.values():spine.set_visible(False)
        axes[0].set_title('Own shooting | higher better',color='white');axes[1].set_title('Opponent shooting when defending | lower better',color='white')
        fig.suptitle('Cooper Flagg | 2025-26 DAL | distance-bin offense / defense',color='white',fontsize=16)
        fig.text(.035,.035,'NBA Stats | Offense: reported integer shot distance. Defense: tracking distance bins. Definitions/samples may differ.\n<6 ft is NOT Restricted Area; other 2PT >=6 ft is NOT pure Mid-Range. No causal defense score or direct duel prediction.',color='#bdc6d4',fontsize=9)
        fig.tight_layout(rect=(0,.1,1,.93));fig.savefig(OUT/'flagg_offense_defense_distance_bands.png',dpi=180,facecolor=fig.get_facecolor());plt.close(fig)
    # Embedded JSON works when opening the standalone HTML locally, without a server.
    template=Path(__file__).with_name('explorer_template.html').read_text()
    (OUT/'player_explorer.html').write_text(template.replace('__DATA__',json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('</','<\\/')))
    print(f'Rendered {len(list(OUT.glob("*.png")))} PNGs and standalone player_explorer.html')


if __name__=='__main__':main()
