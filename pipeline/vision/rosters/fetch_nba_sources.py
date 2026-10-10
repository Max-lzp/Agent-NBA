"""Fetch official roster, team-filtered totals and career GS with preserved evidence.

Historical registrations/jersey intervals are separate facts: this command never
guesses them or publishes an apparently complete season roster.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from build_roster import TEAMS


def fetch(endpoint, parameters):
    url = "https://stats.nba.com/stats/" + endpoint + "?" + urllib.parse.urlencode(parameters)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
        "Referer": "https://www.nba.com/", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read()
    data = json.loads(raw)
    if not isinstance(data.get("resultSets"), list):
        raise ValueError(f"Unexpected official response: {endpoint}")
    return {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(), "response": data}


def rows(evidence, name):
    result = next(r for r in evidence["response"]["resultSets"] if r["name"] == name)
    if len(set(result["headers"])) != len(result["headers"]):
        raise ValueError("Duplicate response headers")
    return [dict(zip(result["headers"], row, strict=True)) for row in result["rowSet"]]


def collect(season):
    sources, rosters, totals = {}, {}, {}
    for code, (team_id, _) in TEAMS.items():
        key = f"{code}_roster"
        sources[key] = fetch("commonteamroster", {"LeagueID": "00", "Season": season, "TeamID": team_id})
        rosters[code] = rows(sources[key], "CommonTeamRoster")
        key = f"{code}_totals"
        sources[key] = fetch("leaguedashplayerstats", {"Season": season, "SeasonType": "Regular Season",
            "PerMode": "Totals", "MeasureType": "Base", "LeagueID": "00", "LastNGames": 0,
            "Month": 0, "OpponentTeamID": 0, "PaceAdjust": "N", "PlusMinus": "N", "Rank": "N",
            "TeamID": team_id, "Period": 0})
        totals[code] = rows(sources[key], "LeagueDashPlayerStats")
        print(f"{code}: roster {len(rosters[code])}, team-filtered stats {len(totals[code])}", flush=True)
    ids = sorted({int(r["PLAYER_ID"]) for records in list(rosters.values()) + list(totals.values()) for r in records})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        tasks = {executor.submit(fetch, "playercareerstats", {"PlayerID": player_id,
                  "PerMode": "Totals", "LeagueID": "00"}): player_id for player_id in ids}
        for future in concurrent.futures.as_completed(tasks):
            player_id = tasks[future]
            sources[f"career_{player_id}"] = future.result()
            print(f"Verified career source {player_id}", flush=True)
    candidates = {}
    for code, (team_id, _) in TEAMS.items():
        by_id = {int(r["PLAYER_ID"]): r for r in rosters[code]}
        stat_by_id = {int(r["PLAYER_ID"]): r for r in totals[code]}
        if len(stat_by_id) != len(totals[code]) or len(by_id) != len(rosters[code]):
            raise ValueError(f"Duplicate IDs in {code} response")
        candidates[code] = []
        for player_id in sorted(by_id.keys() | stat_by_id.keys()):
            roster, stat = by_id.get(player_id), stat_by_id.get(player_id)
            career = [r for r in rows(sources[f"career_{player_id}"], "SeasonTotalsRegularSeason")
                      if r["SEASON_ID"] == season and int(r["TEAM_ID"]) == team_id]
            if len(career) > 1:
                raise ValueError(f"Ambiguous season-team career rows for {player_id}")
            if stat and not career:
                raise ValueError(f"No matching career row for played player {player_id}/{code}")
            # Missing from both appearances datasets plus a roster registration is
            # evidence of zero regular-season appearances; never zero-fill a failed request.
            gp, gs, minutes = 0, 0, 0.0
            if career:
                gp, gs = int(career[0]["GP"]), int(career[0]["GS"])
                if not stat or int(stat["GP"]) != gp:
                    raise ValueError(f"GP disagreement for {player_id}/{code}")
                minutes = float(stat["MIN"])
                # Career MIN is rounded; retain precise leaguedash totals.
                if abs(float(career[0]["MIN"]) - minutes) > 1:
                    raise ValueError(f"MIN disagreement for {player_id}/{code}")
            item = {"player_id": player_id, "name": roster["PLAYER"] if roster else stat["PLAYER_NAME"],
                    "games_played": gp, "games_started": gs, "minutes_total": minutes,
                    "registration_status": "needs historical dates and season-wide completeness verification"}
            if roster:
                item["season_snapshot_jersey"] = roster["NUM"]
                item["season_snapshot_position"] = roster["POSITION"]
                item["how_acquired_raw"] = roster["HOW_ACQUIRED"]
            item["missing_for_final_roster"] = ["verified from/to intervals", "season-wide registration coverage"]
            if not roster or roster["NUM"] is None:
                item["missing_for_final_roster"].append("verified season jersey")
            if not roster:
                item["missing_for_final_roster"].append("verified position")
            candidates[code].append(item)
        candidates[code].sort(key=lambda r: (-r["minutes_total"], r["player_id"]))
    return {"season": season, "season_type": "Regular Season", "status": "official statistics verified; registration history incomplete",
            "candidates": candidates, "sources": sources}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = collect(args.season)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Saved official sources and verified statistics to {args.output}")


if __name__ == "__main__":
    main()
