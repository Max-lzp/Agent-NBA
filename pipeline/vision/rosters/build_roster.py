"""Build the season roster from verified registration intervals and team-split totals.

No network scraping or guessed values: see DELIVERY-README.md for input contracts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

TEAMS = {"HOU": (1610612745, "Houston Rockets"), "DAL": (1610612742, "Dallas Mavericks")}
SHORT_NAMES = {
    "Amen Thompson": "Amen", "Kevin Durant": "Durant", "Jabari Smith Jr.": "Smith",
    "Tari Eason": "Eason", "Alperen Sengun": "Sengun", "Alperen Şengün": "Sengun",
    "Dorian Finney-Smith": "Finney-Smith", "Clint Capela": "Capela", "Josh Okogie": "Okogie",
    "Reed Sheppard": "Sheppard", "Jae'Sean Tate": "Tate", "Aaron Holiday": "Holiday",
    "Jeff Green": "Green", "Jack McVeigh": "McVeigh", "Isaiah Crawford": "Crawford",
    "JD Davison": "Davison", "Cooper Flagg": "Flagg", "Max Christie": "Christie",
    "Daniel Gafford": "Gafford", "P.J. Washington": "Washington", "Naji Marshall": "Marshall",
    "Dwight Powell": "Powell", "Caleb Martin": "Martin", "Moussa Cisse": "Cisse",
    "Klay Thompson": "Klay", "Brandon Williams": "Williams", "Ryan Nembhard": "Nembhard",
    "D'Angelo Russell": "Russell", "Jaden Hardy": "Hardy", "Anthony Davis": "Davis",
}


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def integer(value, label):
    number = int(value)
    if str(number) != str(value).strip() or number < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return number


def build(registrations, totals, fetched_at):
    cutoff = date.fromisoformat(fetched_at)
    stats = {}
    for row in totals:
        key = (row["team"], integer(row["player_id"], "player_id"))
        if key[0] not in TEAMS or key in stats:
            raise ValueError(f"Unknown/duplicate team-split total: {key}")
        gp, gs = integer(row["games_played"], "GP"), integer(row["games_started"], "GS")
        minutes = float(row["minutes_total"])
        if not 0 <= gs <= gp or not 0 <= minutes < float("inf") or (gp == 0 and minutes != 0):
            raise ValueError(f"Invalid GP/GS/MIN: {key}")
        stats[key] = {"games_played": gp, "games_started": gs, "minutes_total": minutes}
    teams = {code: {"team_id": ident, "name": name, "players": []} for code, (ident, name) in TEAMS.items()}
    intervals = {}
    used = set()
    for row in registrations:
        team, player_id = row["team"], integer(row["player_id"], "player_id")
        key = (team, player_id)
        if team not in TEAMS or player_id == 0 or key not in stats:
            raise ValueError(f"Missing verified total or invalid registration: {key}")
        name, jersey = row["name"].strip(), row["jersey"]
        if not name or not jersey.isascii() or not jersey.isdigit() or len(jersey) > 2:
            raise ValueError(f"Invalid name/jersey: {key}")
        start = date.fromisoformat(row["from"])
        end = date.fromisoformat(row["to"]) if row["to"] else None
        if start > cutoff or (end and (end < start or end > cutoff)):
            raise ValueError(f"Invalid interval: {key}")
        # Dates are inclusive; consecutive non-overlapping registrations are allowed.
        for old_start, old_end in intervals.get(key, []):
            if start <= (old_end or cutoff) and old_start <= (end or cutoff):
                raise ValueError(f"Overlapping registrations: {key}")
        intervals.setdefault(key, []).append((start, end))
        short = SHORT_NAMES.get(name) or row.get("short_name", "").strip()
        if not short or not row["position"].strip():
            raise ValueError(f"Provide a reviewed short_name and position: {name}")
        teams[team]["players"].append({"player_id": player_id, "name": name,
            "short_name": short, "jersey": jersey, "position": row["position"].strip(),
            **stats[key], "from": start.isoformat(), "to": end.isoformat() if end else None})
        used.add(key)
    if used != set(stats) or any(not team["players"] for team in teams.values()):
        raise ValueError("Both teams need registrations; every supplied total must be matched")
    for team in teams.values():
        team["players"].sort(key=lambda p: (-p["minutes_total"], p["player_id"], p["from"]))
    return {"season": "2025-26", "fetched_at": fetched_at, "teams": teams}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registrations", type=Path, required=True)
    parser.add_argument("--totals", type=Path, required=True)
    parser.add_argument("--source", required=True, help="Official source URL/export identifier")
    parser.add_argument("--fetched-at", required=True, help="YYYY-MM-DD source retrieval date")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("2025-26.json"))
    args = parser.parse_args()
    result = build(read_csv(args.registrations), read_csv(args.totals), args.fetched_at)
    result["source"] = args.source
    result["provenance"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                   for p in (args.registrations, args.totals)],
        "statistics_scope": "player-team season totals; repeated across jersey intervals, do not sum intervals",
        "date_interval": "inclusive; null to means active at fetched_at",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
