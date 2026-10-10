"""Download NBA Inside the Game's public JSON endpoints and export real CSVs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATASETS = {
    "shot_difficulty": ("shotqualityleaders", "shots", "/inside-the-game/player/shot-difficulty"),
    "player_gravity": ("gravityleaders", "leaders", "/inside-the-game/player/gravity"),
    "player_leverage": ("leverageleaders", "leaders", "/inside-the-game/player/leverage"),
}
TRAINING_GAMES = {
    "flagg": ("0022500701", "1642843"),  # Jan 31 US / Feb 1 China
    "amen": ("0022500491", "1641708"),   # Jan 3 US / Jan 4 China
}


def download(endpoint, params):
    url = "https://stats.nba.com/stats/" + endpoint + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read()
    return raw, json.loads(raw), {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
                                 "sha256": hashlib.sha256(raw).hexdigest()}


def write_dataset(root, name, raw, records):
    if not records:
        raise ValueError(f"Empty official dataset: {name}")
    fields = list(dict.fromkeys(key for row in records for key in row))
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / (name + ".json")).write_bytes(raw)
    with (root / (name + ".csv")).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def collect(output, season):
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"season": season, "season_type": "Regular Season", "provider": "NBA official Inside the Game / stats.nba.com",
                "datasets": {}, "unit": "shot quality and FG rates are fractions in [0,1]"}
    for name, (endpoint, key, page) in DATASETS.items():
        raw, data, metadata = download(endpoint, {"Season": season, "SeasonType": "Regular Season", "LeagueID": "00", "TeamID": 0})
        if data["params"]["seasonYear"] != season or data["params"]["seasonType"] != "Regular Season":
            raise ValueError("Official response returned a different season/type")
        records = data[key]
        write_dataset(output, name, raw, records)
        manifest["datasets"][name] = {**metadata, "row_count": len(records), "official_page": "https://www.nba.com" + page,
            "csv_sha256": hashlib.sha256((output / (name + ".csv")).read_bytes()).hexdigest(),
            "csv": name + ".csv", "raw": "raw/" + name + ".json", "fields": list(records[0])}
        if name == "shot_difficulty":
            for row in records:
                if not 0 <= row["AVGSHOTQUALITY"] <= 1 or abs(row["AVGSHOTQUALITY"] + row["FGPCTABOVEEXPECTED"] - row["FGPCT"]) > 1e-9:
                    raise ValueError("Incompatible official quality/FG fields")
                if row["FGA"] != row["FG2A"] + row["FG3A"] or row["FGA"] <= 0:
                    raise ValueError("Invalid tracked attempt counts")
            denominator = sum(row["FGA"] for row in records)
            manifest["baseline"] = {"probability": sum(row["FGA"] * row["AVGSHOTQUALITY"] for row in records) / denominator,
                "definition": "FGA-weighted expected FG probability of the returned qualified-player tracking cohort; not full-league actual FG%",
                "attempts": denominator, "player_count": len(records), "precision": "computed from officially rounded per-player xFG"}
        print(f"Downloaded {name}: {len(records)} official rows", flush=True)
    if season == "2025-26":
        for name, (game_id, player_id) in TRAINING_GAMES.items():
            raw, data, metadata = download("shotqualityvideologs", {"GameID": game_id, "PlayerID": player_id})
            if data["gameId"] != game_id or int(data["playerId"]) != int(player_id):
                raise ValueError("Wrong game/player returned")
            dataset = name + "_shot_sequence"
            write_dataset(output, dataset, raw, data["shotList"])
            manifest["datasets"][dataset] = {**metadata, "row_count": len(data["shotList"]), "game_id": game_id,
                "csv_sha256": hashlib.sha256((output / (dataset + ".csv")).read_bytes()).hexdigest(),
                "player_id": int(player_id), "game_date_official": data["gameDate"], "csv": dataset + ".csv",
                "raw": "raw/" + dataset + ".json", "fields": list(data["shotList"][0])}
            print(f"Downloaded {dataset}: {len(data['shotList'])} official shots", flush=True)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/deep"))
    parser.add_argument("--season", default="2025-26")
    args = parser.parse_args()
    collect(args.output_dir, args.season)
