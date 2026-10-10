"""Archive jersey/registration observations from every official regular-season game page."""
import argparse
import concurrent.futures
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from build_roster import TEAMS
from fetch_nba_sources import fetch, rows


def game_roster(item):
    game_id, details = item
    url = details["url"]
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read()
    match = re.search(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw)
    game = json.loads(match[1])["props"]["pageProps"]["game"]
    if game["gameId"] != game_id:
        raise ValueError(f"Wrong game returned for {game_id}")
    observations = {}
    for side, roster_key in [("homeTeam", "homeTeamPlayers"), ("awayTeam", "awayTeamPlayers")]:
        team = game[side]
        code = team["teamTricode"]
        if code not in TEAMS:
            continue
        players = {}
        entries = [(player, roster_key) for player in game.get(roster_key, [])]
        entries += [(player, side + ".inactives") for player in team.get("inactives", [])]
        for player, source_field in entries:
            player_id = int(player["personId"])
            jersey = str(player.get("jerseyNum") or "").strip()
            if jersey and not jersey.isdigit():
                raise ValueError(f"Invalid historical jersey for {player_id} in {game_id}")
            name = player.get("name") or (player["firstName"] + " " + player["familyName"])
            if player_id in players and players[player_id]["jersey"] != jersey:
                raise ValueError(f"Conflicting jersey within game: {player_id}/{game_id}")
            players[player_id] = {"player_id": player_id, "name": name, "jersey": jersey or None,
                                  "source_field": source_field}
        observations[code] = list(players.values())
    return {**details, "game_id": game_id, "game_time_utc": game["gameTimeUTC"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "page_sha256": hashlib.sha256(raw).hexdigest(), "teams": observations}


def collect(output, season):
    logs, games = {}, {}
    for code, (team_id, _) in TEAMS.items():
        evidence = fetch("teamgamelog", {"TeamID": team_id, "Season": season, "SeasonType": "Regular Season", "LeagueID": "00"})
        logs[code] = evidence
        records = rows(evidence, "TeamGameLog")
        if len(records) != 82:
            raise ValueError(f"Expected completed 82-game season: {code} returned {len(records)}")
        for row in records:
            game_id = row["Game_ID"]
            opponent = row["MATCHUP"][-3:]
            away, home = (code, opponent) if " @ " in row["MATCHUP"] else (opponent, code)
            games[game_id] = {"date": datetime.strptime(row["GAME_DATE"], "%b %d, %Y").date().isoformat(),
                "url": f"https://www.nba.com/game/{away.lower()}-vs-{home.lower()}-{game_id}/box-score"}
    existing = {}
    if output.exists():
        old = json.loads(output.read_text())
        if old["season"] == season:
            existing = {game["game_id"]: game for game in old["games"]
                        if all("source_field" in player for players in game["teams"].values()
                               for player in players)}
    result = {"season": season, "scope": "all regular-season games, active roster plus official inactives",
        "team_game_logs": logs, "games": list(existing.values()), "complete": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = [(gid, details) for gid, details in sorted(games.items()) if gid not in existing]
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        tasks = {executor.submit(game_roster, item): item[0] for item in pending}
        for future in concurrent.futures.as_completed(tasks):
            try:
                result["games"].append(future.result())
            except Exception as error:
                errors.append({"game_id": tasks[future], "error": str(error)})
            if (len(result["games"]) + len(errors)) % 10 == 0:
                print(f"Game rosters fetched {len(result['games'])}/{len(games)}, errors {len(errors)}", flush=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    result["games"].sort(key=lambda g: (g["date"], g["game_id"]))
    result["errors"] = errors
    result["complete"] = not errors and {r["game_id"] for r in result["games"]} == set(games)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not result["complete"]:
        raise RuntimeError(f"Historical observations incomplete: {errors}")
    print(f"Verified {len(games)} unique official game rosters", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "sources/2025-26-historical-rosters.json")
    args = parser.parse_args()
    collect(args.output, args.season)
