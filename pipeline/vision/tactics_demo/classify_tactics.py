"""Explainable geometry-only tactical candidates; not a trained video classifier.

Input: calibrated, stable player tracks in METERS, attack basket at (0, 0).
No identity/possession/calibration inference is performed by this sidecar demo.
"""
import argparse
import json
import math
from pathlib import Path

LABELS = {'pick_and_roll':'挡拆','handoff':'手递手','isolation':'单打','horns':'牛角','cut':'空切','off_ball_screen':'无球掩护','floppy':'Floppy'}
THRESHOLDS = {'min_track_quality': .7, 'screen_distance_m': 2.0, 'screen_defender_distance_m': 1.0,
              'screen_max_speed_mps': .6, 'handoff_distance_m': 1.5, 'min_motion_m': .4,
              'cut_speed_mps': 2.0, 'elbow_x_m': 2.44, 'elbow_y_m': 4.19,
              'elbow_tolerance_m': 1.2, 'isolation_spacing_m': 6.7, 'min_window_s': .4,
              'max_frame_gap_s': .5}


def distance(a,b):return math.hypot(a['x']-b['x'],a['y']-b['y'])
def hoop_distance(p):return math.hypot(p['x'],p['y'])


def classify(data):
    if data.get('coordinate_unit') != 'meters' or data.get('attack_basket') != [0,0]:
        raise ValueError('Explicit calibrated meters and attack_basket [0,0] required')
    frames=data['frames']
    if len(frames)<3:raise ValueError('At least three frames required')
    previous_t=-float('inf')
    for f in frames:
        t=f['t']
        if not isinstance(t,(int,float)) or not math.isfinite(t) or t<=previous_t:raise ValueError('Strictly increasing finite timestamps required')
        previous_t=t
        ids=[p['track_id'] for p in f['players']]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate track identities')
        for p in f['players']:
            if not all(isinstance(p[k],(int,float)) and math.isfinite(p[k]) for k in ['x','y']):raise ValueError('Finite calibrated coordinates required')
    hits=[];skipped=0
    for index in range(1,len(frames)-1):
        before,current,after=frames[index-1:index+2]
        dt=after['t']-before['t']
        if (any(not f.get('camera_segment') or f.get('camera_segment')!=current.get('camera_segment') or not f.get('calibration_valid',False) for f in [before,current,after])
            or any(b['t']-a['t']>THRESHOLDS['max_frame_gap_s'] for a,b in [(before,current),(current,after)])
            or dt+1e-9<THRESHOLDS['min_window_s']):
            skipped+=1;continue
        maps=[{p['track_id']:p for p in f['players'] if p.get('tracking_confidence',0)>=THRESHOLDS['min_track_quality']} for f in [before,current,after]]
        stable=set(maps[0])&set(maps[1])&set(maps[2])
        offense=[p for p in maps[1].values() if p['team']==data['offense_team'] and p['track_id'] in stable]
        defense=[p for p in maps[1].values() if p['team']!=data['offense_team'] and p['track_id'] in stable]
        carrier=current.get('ball_handler_track_id');old=before.get('ball_handler_track_id');new=after.get('ball_handler_track_id')
        if len(offense)!=5 or len(defense)!=5 or carrier not in stable or maps[1][carrier]['team']!=data['offense_team']:
            skipped+=1;continue
        holder=maps[1][carrier]
        speed={i:distance(maps[0][i],maps[2][i])/dt for i in stable}
        screens=[]
        for p in offense:
            i=p['track_id']
            if i!=carrier and speed[i]<=THRESHOLDS['screen_max_speed_mps'] and min(distance(p,d) for d in defense)<=THRESHOLDS['screen_defender_distance_m']:
                screens.append(p)
        def emit(label,ids,evidence):
            hits.append({'label':label,'label_zh':LABELS[label],'t':current['t'],'camera_segment':current['camera_segment'],'track_ids':ids,
                         'evidence':evidence,'status':'geometry_rule_candidate_requires_video_review'})
        if old!=new and old in stable and new in stable and maps[1][old]['team']==maps[1][new]['team']==data['offense_team']:
            close=distance(maps[1][old],maps[1][new]);motion=distance(maps[0][new],maps[2][new])
            if close<=THRESHOLDS['handoff_distance_m'] and motion>=THRESHOLDS['min_motion_m']:
                emit('handoff',[old,new],{'separation_m':close,'receiver_motion_m':motion,'possession_transfer':[old,new]})
        for screen in screens:
            i=screen['track_id'];separation=distance(holder,screen);roll=hoop_distance(maps[1][i])-hoop_distance(maps[2][i])
            holder_motion=distance(maps[0][carrier],maps[2][carrier])
            if separation<=THRESHOLDS['screen_distance_m'] and holder_motion>=THRESHOLDS['min_motion_m'] and roll>=.15:
                emit('pick_and_roll',[carrier,i],{'handler_screener_distance_m':separation,'screener_speed_mps':speed[i],'roll_toward_basket_m':roll,'defender_near_screen':True})
            for cutter in offense:
                j=cutter['track_id']
                if j not in [carrier,i] and distance(cutter,screen)<=THRESHOLDS['screen_distance_m'] and speed[j]>=1:
                    emit('off_ball_screen',[i,j],{'receiver_screener_distance_m':distance(cutter,screen),'screen_speed_mps':speed[i],'receiver_speed_mps':speed[j]})
        for p in offense:
            i=p['track_id'];inward=(hoop_distance(maps[0][i])-hoop_distance(maps[2][i]))/dt
            if i not in [old,carrier,new] and inward>=THRESHOLDS['cut_speed_mps']:
                emit('cut',[i],{'inward_radial_speed_mps':inward,'off_ball_through_window':True})
        # Horns is a spatial formation. Do not call players big men based on height alone.
        left=[p for p in offense if p['track_id']!=carrier and math.hypot(p['x']+THRESHOLDS['elbow_x_m'],p['y']-THRESHOLDS['elbow_y_m'])<=THRESHOLDS['elbow_tolerance_m']]
        right=[p for p in offense if p['track_id']!=carrier and math.hypot(p['x']-THRESHOLDS['elbow_x_m'],p['y']-THRESHOLDS['elbow_y_m'])<=THRESHOLDS['elbow_tolerance_m']]
        if left and right and abs(holder['x'])<=1.5 and holder['y']>=6.2:
            emit('horns',[carrier,left[0]['track_id'],right[0]['track_id']],{'two_elbow_occupants':True,'handler_central_above_elbows':True})
        nearby=[d for d in defense if distance(holder,d)<=2.5]
        spaced=all(hoop_distance(p)>=THRESHOLDS['isolation_spacing_m'] for p in offense if p['track_id']!=carrier)
        if len(nearby)==1 and spaced and not any(distance(holder,p)<=THRESHOLDS['screen_distance_m'] for p in screens):
            emit('isolation',[carrier,nearby[0]['track_id']],{'nearby_defenders':1,'four_other_attackers_spaced':True,'no_nearby_screen_candidate':True})
    # Floppy needs sequence evidence: same off-ball receiver passes two distinct
    # screen candidates near baseline, then gains possession. Generic false positives remain possible.
    screen_hits=[h for h in hits if h['label']=='off_ball_screen']
    for index,f in enumerate(frames[1:],1):
        receiver=f.get('ball_handler_track_id')
        if receiver==frames[index-1].get('ball_handler_track_id') or not f.get('calibration_valid',False):continue
        players={p['track_id']:p for p in f['players']}
        screeners={h['track_ids'][0] for h in screen_hits if h['track_ids'][1]==receiver
                   and h['camera_segment']==f.get('camera_segment') and 0<f['t']-h['t']<=3.0}
        if len(screeners)>=2 and receiver in players and players[receiver]['y']<=6:
            hits.append({'label':'floppy','label_zh':LABELS['floppy'],'t':f['t'],'track_ids':[receiver,*sorted(screeners)],
                         'evidence':{'distinct_prior_screen_candidates':len(screeners),'receiver_gains_possession_near_baseline':True},
                         'status':'weak_sequence_candidate_requires_video_review'})
    return {'input_kind':data.get('input_kind','provided_tracks'),'thresholds':THRESHOLDS,
            'labels_supported':LABELS,'candidates':hits,'skipped_windows':skipped,
            'limitations':['Not trained or evaluated on labeled game video','Requires upstream calibration, stable IDs and verified possession',
                           'No calibrated confidence probabilities','Multiple labels may coexist; no forced majority label',
                           'Missed shots do not necessarily end a possession; offensive rebounds retain possession']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=classify(json.loads(args.input.read_text()));args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n');print(f'{len(result["candidates"])} rule candidates -> {args.output}')


if __name__=='__main__':main()
