"""分析师工具。只返回仓库或媒体桶里查到的字段。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from manifest import check, game_id_of, job_id_of
from season_data import find_player, load_slice, match_name, team_zones

OFFICIAL_PREFIX = "official/2025-26/"
GAME_FILE = re.compile(r"(\d{10})_(playbyplay|shotquality)", re.I)
CLOCK_ISO = re.compile(r"PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")
CLOCK_MS = re.compile(r"(\d+):(\d+(?:\.\d+)?)")
ASSIST = re.compile(r"\(([A-Za-z .'-]+) \d+ AST\)")


def clock_seconds(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    iso = CLOCK_ISO.fullmatch(text)
    if iso and (iso.group(1) or iso.group(2)):
        return int(iso.group(1) or 0) * 60 + float(iso.group(2) or 0)
    clock = CLOCK_MS.fullmatch(text)
    if clock:
        return int(clock.group(1)) * 60 + float(clock.group(2))
    return None


def _missing(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "")
        if code in {"NoSuchKey", "NotFound", "404", "NoSuchBucket"}:
            return True
    text = str(exc).lower()
    return "nosuchkey" in text or "not found" in text


def _pct(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1:
        return round(number * 100, 1)
    return round(number, 1)


class Media:
    def __init__(self, s3, bucket: str):
        self.s3 = s3
        self.bucket = bucket
        self._json: dict[str, dict | None] = {}
        self._keys: dict[str, list[str]] = {}

    def get_json(self, key: str):
        if key in self._json:
            return self._json[key]
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:
            if _missing(exc):
                self._json[key] = None
                return None
            raise
        if isinstance(body, str):
            text = body
        else:
            text = body.decode("utf-8")
        self._json[key] = json.loads(text)
        return self._json[key]

    def put_json(self, key: str, payload: dict) -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

    def list_keys(self, prefix: str) -> list[str]:
        if prefix in self._keys:
            return self._keys[prefix]
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kwargs)
            keys.extend(item["Key"] for item in page.get("Contents") or [])
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        self._keys[prefix] = keys
        return keys


@dataclass
class Ctx:
    media: Media
    bucket: str
    job_id: str
    key: str
    manifest: dict | None = None
    evidence: list = field(default_factory=list)

    def remember(self, source: str, data: dict) -> dict:
        self.evidence.append({"source": source, "data": data})
        return data


def _spoken(name: str) -> dict:
    """工具里带上统一译名，模型就不用自己翻译。"""
    raw = str(name or "")
    person = match_name(raw)
    shown = raw
    alias = person["alias_zh"] if person else ""
    if person and alias and len(raw) == len(alias) and sum(a != b for a, b in zip(raw, alias)) == 1:
        shown = alias
    out = {"name": shown}
    if person:
        out["name_zh"] = alias or person["name_zh"]
        out["name_en"] = person["name_en"]
    return out


def _who(ref) -> dict:
    if not isinstance(ref, dict):
        return {}
    spoken = _spoken(ref.get("name") or ref.get("nameZh") or "")
    return {
        "team": ref.get("team") or "",
        "number": ref.get("number") or "",
        "name": spoken.get("name") or "",
        **{key: spoken[key] for key in ("name_zh", "name_en") if key in spoken},
    }


def public_manifest(stream: dict, job_id: str) -> dict:
    possessions = []
    for poss in (stream.get("possessions") or [])[:8]:
        events = []
        for ev in (poss.get("events") or [])[:40]:
            official = ev.get("official") if isinstance(ev.get("official"), dict) else {}
            item = {
                "t": ev.get("t"),
                "type": ev.get("type"),
                "actor": _who(ev.get("actor")),
                "target": _who(ev.get("target")) if ev.get("target") else None,
            }
            if official:
                view = {}
                for field_name in ("made", "value", "description", "period"):
                    if field_name in official:
                        view[field_name] = official.get(field_name)
                clock = official.get("clock_s", official.get("clock"))
                if clock is not None:
                    view["clock_s"] = clock_seconds(clock)
                xfg = official.get("xfg")
                if xfg is not None:
                    view["xfg_pct"] = _pct(xfg)
                if official.get("assist"):
                    assist = official.get("assist")
                    view["assist"] = _who(assist) if isinstance(assist, dict) else _spoken(str(assist))
                item["official"] = view
            events.append(item)
        possessions.append({
            "possession_id": poss.get("possession_id"),
            "offense_team": poss.get("offense_team"),
            "t_start": poss.get("t_start"),
            "t_end": poss.get("t_end"),
            "events": events,
        })
    source = f"structured/{job_id}.json"
    top = stream.get("official") if isinstance(stream.get("official"), dict) else {}
    clip = {}
    if top.get("period") is not None:
        clip["period"] = top.get("period")
    if top.get("clock_window_s") is not None:
        clip["clock_window_s"] = top.get("clock_window_s")
    return {
        "source": source,
        "found": True,
        "version": stream.get("version"),
        "jobId": job_id_of(stream, job_id),
        "gameId": game_id_of(stream),
        "clip": clip,
        "possessions": possessions,
    }


def read_manifest(ctx: Ctx, _payload: dict) -> dict:
    source = f"structured/{ctx.job_id}.json"
    if ctx.manifest is None:
        ctx.manifest = ctx.media.get_json(ctx.key or source)
    if not isinstance(ctx.manifest, dict):
        return ctx.remember(source, {"source": source, "found": False})
    return ctx.remember(source, public_manifest(ctx.manifest, ctx.job_id))


def validate_manifest(ctx: Ctx, _payload: dict) -> dict:
    source = f"structured/{ctx.job_id}.json#schema"
    if ctx.manifest is None:
        ctx.manifest = ctx.media.get_json(ctx.key or f"structured/{ctx.job_id}.json")
    problems = check(ctx.manifest)
    return ctx.remember(source, {"source": source, "ok": not problems, "problems": problems})


def _name_fields(name: str) -> dict:
    spoken = _spoken(name)
    return {key: spoken[key] for key in ("name_zh", "name_en") if key in spoken}


def _assist(description: str) -> str:
    match = ASSIST.search(description or "")
    return match.group(1).strip() if match else ""


def play_from_action(raw: dict) -> dict:
    action = str(raw.get("actionType") or "").lower()
    kind = "shot" if action in {"2pt", "3pt"} else action
    description = raw.get("description") or ""
    value = 3 if "3pt" in action or "3PT" in str(description) else 2
    play = {
        "kind": kind,
        "period": raw.get("period"),
        "clock_s": clock_seconds(raw.get("clock")),
        "team": str(raw.get("teamTricode") or "").upper(),
        "made": str(raw.get("shotResult") or "").lower() == "made" if kind == "shot" else None,
        "value": value if kind == "shot" else None,
        "description": description,
        "assist": _assist(description),
        "player": raw.get("playerNameI") or raw.get("playerName") or "",
    }
    away = _score_int(raw.get("scoreAway"))
    home = _score_int(raw.get("scoreHome"))
    if away is not None:
        play["score_away"] = away
    if home is not None:
        play["score_home"] = home
    distance = _optional_float(raw.get("shotDistance"))
    if kind == "shot" and distance is not None:
        play["distance_ft"] = distance
    return play | _name_fields(raw.get("playerNameI") or raw.get("playerName") or "")


def _quality_index(shots: list) -> list[dict]:
    indexed = []
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        indexed.append({
            "period": shot.get("period"),
            "clock_s": clock_seconds(shot.get("gameClock")),
            "team": str(shot.get("teamAbbreviation") or "").upper(),
            "xfg_pct": _pct(shot.get("shotQuality")),
            "player": shot.get("playerName") or shot.get("playerNameI") or "",
        })
    return indexed


def _attach_quality(play: dict, quality: list[dict]) -> dict:
    if play.get("kind") != "shot":
        return play
    exact = [
        item for item in quality
        if item["period"] == play["period"] and item["team"] == play["team"]
        and item["clock_s"] is not None and play["clock_s"] is not None
        and abs(item["clock_s"] - play["clock_s"]) <= 2
    ]
    exact.sort(key=lambda item: abs(item["clock_s"] - play["clock_s"]))
    if exact and exact[0]["xfg_pct"] is not None:
        play = dict(play)
        play["xfg_pct"] = exact[0]["xfg_pct"]
    return play


def clip_window(stream) -> tuple[int | None, float | None, float | None]:
    """清单里这段回放的节次和比赛钟。没有钟就不猜整场。"""
    if not isinstance(stream, dict):
        return None, None, None
    clocks: list[float] = []
    periods: list[int] = []

    def take_clock(value) -> None:
        seconds = clock_seconds(value)
        if seconds is not None:
            clocks.append(seconds)

    def take_period(value) -> None:
        if isinstance(value, int) and not isinstance(value, bool):
            periods.append(value)

    official = stream.get("official") if isinstance(stream.get("official"), dict) else {}
    window = official.get("clock_window_s")
    if isinstance(window, (list, tuple)):
        for value in window[:2]:
            take_clock(value)
    take_period(official.get("period"))
    for poss in stream.get("possessions") or []:
        if not isinstance(poss, dict):
            continue
        poss_official = poss.get("official") if isinstance(poss.get("official"), dict) else {}
        for key in ("clock_start_s", "clock_end_s", "clock_s"):
            take_clock(poss_official.get(key))
        take_period(poss_official.get("period"))
        for ev in poss.get("events") or []:
            if not isinstance(ev, dict):
                continue
            ev_official = ev.get("official") if isinstance(ev.get("official"), dict) else {}
            take_clock(ev_official.get("clock_s", ev_official.get("clock")))
            take_period(ev_official.get("period"))
    if not clocks:
        return None, None, None
    period = periods[0] if periods and len(set(periods)) == 1 else None
    return period, min(clocks) - 2, max(clocks) + 2


def _in_window(play: dict, period, lo, hi) -> bool:
    if period is not None and play.get("period") != period:
        return False
    clock = play.get("clock_s")
    if lo is None and hi is None:
        return True
    if clock is None:
        return False
    if lo is not None and clock < lo:
        return False
    if hi is not None and clock > hi:
        return False
    return True


def _official_files(ctx: Ctx, game_id: str) -> tuple[dict | None, dict | None, str, str]:
    keys = [key for key in ctx.media.list_keys(OFFICIAL_PREFIX) if key.endswith(".json")]
    plays_key = ""
    quality_key = ""
    plays_doc = None
    quality_doc = None
    for key in keys:
        name = key.rsplit("/", 1)[-1]
        match = GAME_FILE.search(name)
        if not match or match.group(1) != game_id:
            continue
        kind = match.group(2).lower()
        doc = ctx.media.get_json(key)
        if not isinstance(doc, dict):
            continue
        if kind == "playbyplay":
            actions = (doc.get("game") or {}).get("actions") if isinstance(doc.get("game"), dict) else None
            if isinstance(actions, list) and (plays_doc is None or len(actions) > len((plays_doc.get("game") or {}).get("actions") or [])):
                plays_doc = doc
                plays_key = key
        elif kind == "shotquality" and isinstance(doc.get("shots"), list):
            quality_doc = doc
            quality_key = key
    return plays_doc, quality_doc, plays_key, quality_key


def _shot_descriptions(stream) -> list[str]:
    if not isinstance(stream, dict):
        return []
    found = []
    for poss in stream.get("possessions") or []:
        if not isinstance(poss, dict):
            continue
        for event in poss.get("events") or []:
            if not isinstance(event, dict):
                continue
            official = event.get("official") if isinstance(event.get("official"), dict) else {}
            desc = str(official.get("description") or "").strip()
            if desc:
                found.append(desc)
    return found


def _desc_hit(play: dict, descriptions: list[str]) -> bool:
    text = " ".join(str(play.get("description") or "").split()).lower()
    if not text:
        return False
    for desc in descriptions:
        other = " ".join(desc.split()).lower()
        if other and (other == text or other in text or text in other):
            return True
    return False


def recover_window(stream, plays) -> tuple[int | None, float, float] | None:
    """清单里只有出手描述、没有比赛钟时，用描述对上官方记录，再收到上一个进球之前。"""
    matched = [
        play for play in plays
        if play.get("kind") == "shot" and _desc_hit(play, _shot_descriptions(stream))
    ]
    clocks = [play["clock_s"] for play in matched if isinstance(play.get("clock_s"), (int, float))]
    if not clocks:
        return None
    lo, hi = min(clocks), max(clocks)
    earlier = [
        play["clock_s"] for play in plays
        if play.get("kind") == "shot" and play.get("made") is True
        and isinstance(play.get("clock_s"), (int, float)) and play["clock_s"] > hi + 0.5
    ]
    hi = (min(earlier) - 0.5) if earlier else hi + 18
    periods = {play.get("period") for play in matched if isinstance(play.get("period"), int) and not isinstance(play.get("period"), bool)}
    period = next(iter(periods)) if len(periods) == 1 else None
    return period, lo - 2, hi


def _link_contacts(plays: list[dict]) -> list[dict]:
    linked = []
    for index, play in enumerate(plays):
        item = dict(play)
        if item.get("kind") == "shot":
            for other in plays[index + 1:index + 4]:
                if other.get("kind") == "block" and "blocked_by" not in item:
                    item["blocked_by"] = other.get("player") or ""
                    if other.get("name_zh"):
                        item["blocked_by_zh"] = other["name_zh"]
                same_clock = other.get("clock_s") == item.get("clock_s")
                if other.get("kind") == "foul" and same_clock and "fouled_by" not in item:
                    item["fouled_by"] = other.get("player") or ""
                    if other.get("name_zh"):
                        item["fouled_by_zh"] = other["name_zh"]
        linked.append(item)
    return linked


def _final_score(plays_doc) -> dict | None:
    game = (plays_doc or {}).get("game") if isinstance(plays_doc, dict) else None
    if not isinstance(game, dict):
        return None
    away = game.get("awayTeam") if isinstance(game.get("awayTeam"), dict) else {}
    home = game.get("homeTeam") if isinstance(game.get("homeTeam"), dict) else {}
    away_score = _score_int(away.get("score"))
    home_score = _score_int(home.get("score"))
    if away_score is None or home_score is None:
        return None
    return {
        "away": str(away.get("teamTricode") or "").upper(),
        "away_score": away_score,
        "home": str(home.get("teamTricode") or "").upper(),
        "home_score": home_score,
    }


def read_official(ctx: Ctx, payload: dict) -> dict:
    if ctx.manifest is None:
        ctx.manifest = ctx.media.get_json(ctx.key or f"structured/{ctx.job_id}.json")
    game_id = str(payload.get("game_id") or payload.get("gameId") or game_id_of(ctx.manifest) or "")
    keys = ctx.media.list_keys(OFFICIAL_PREFIX)
    game_ids = sorted({
        match.group(1)
        for key in keys
        if (match := GAME_FILE.search(key.rsplit("/", 1)[-1]))
    })
    if not game_id:
        data = {"source": OFFICIAL_PREFIX, "found": False, "game_ids": game_ids}
        return ctx.remember(OFFICIAL_PREFIX, data)
    plays_doc, quality_doc, plays_key, quality_key = _official_files(ctx, game_id)
    source = plays_key or quality_key or f"{OFFICIAL_PREFIX}{game_id}"
    if plays_doc is None and quality_doc is None:
        return ctx.remember(source, {"source": source, "found": False, "gameId": game_id, "game_ids": game_ids})
    actions = ((plays_doc or {}).get("game") or {}).get("actions") or []
    plays = [play_from_action(item) for item in actions if isinstance(item, dict)]
    quality = _quality_index((quality_doc or {}).get("shots") or [])
    period = payload.get("period")
    if period is not None and period != "":
        try:
            period = int(period)
        except (TypeError, ValueError):
            period = None
    lo = _optional_float(payload.get("clock_lo_s"))
    hi = _optional_float(payload.get("clock_hi_s"))
    if lo is None and hi is None:
        auto_period, auto_lo, auto_hi = clip_window(ctx.manifest)
        if period is None:
            period = auto_period
        lo, hi = auto_lo, auto_hi
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    narrowed = lo is not None or hi is not None
    if not narrowed:
        recovered = recover_window(ctx.manifest, plays)
        if recovered:
            period, lo, hi = recovered
            narrowed = True
    final = _final_score(plays_doc)
    if not narrowed:
        data = {
            "source": source,
            "found": True,
            "gameId": game_id,
            "plays": [],
            "note": "清单里没有这段回放的比赛钟。不返回整场出手，避免把别的回合说成这一段。",
        }
        if final:
            data["final"] = final
        return ctx.remember(source, data)
    chosen = []
    for play in plays:
        if not _in_window(play, period, lo, hi):
            continue
        chosen.append(_attach_quality(play, quality))
        if len(chosen) >= 24:
            break
    data = {
        "source": source,
        "found": True,
        "gameId": game_id,
        "plays": _link_contacts(chosen),
        "truncated": len(chosen) >= 24,
        "note": "xfg_pct 已是 0–100。null 表示这份出手没有 shot quality，不是 0。比分是 score_away / score_home。没有的字段不要写成 0。",
    }
    if final:
        data["final"] = final
    return ctx.remember(source, data)


def _score_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


TEAM_QUERY = {
    "HOU": "HOU",
    "DAL": "DAL",
    "火箭": "HOU",
    "休斯顿": "HOU",
    "ROCKETS": "HOU",
    "HOUSTON": "HOU",
    "独行侠": "DAL",
    "达拉斯": "DAL",
    "MAVERICKS": "DAL",
    "DALLAS": "DAL",
}


def _team_query(name: str) -> str:
    token = str(name or "").strip()
    if not token:
        return ""
    if token.upper() in {"HOU", "DAL"}:
        return token.upper()
    return TEAM_QUERY.get(token) or TEAM_QUERY.get(token.upper()) or ""


def read_season(ctx: Ctx, payload: dict) -> dict:
    name = str(payload.get("name") or payload.get("player") or "").strip()
    team = str(payload.get("team") or "").strip().upper()
    team_code = "" if match_name(name) else (_team_query(name) or _team_query(team))
    if team_code:
        doc = load_slice()
        zones = team_zones(doc, team_code)
        source = f"season:2025-26:{team_code}"
        if zones is None:
            return ctx.remember(source, {"source": source, "found": False, "team": team_code})
        data = {
            "source": source,
            "found": True,
            "team": team_code,
            "name_zh": "火箭" if team_code == "HOU" else "独行侠",
            "name_en": "Houston Rockets" if team_code == "HOU" else "Dallas Mavericks",
            "offense": zones["offense"],
            "defense": zones["defense"],
            "players": zones["players"],
            "note": "进攻和防守三区都是队内球员的计数相加后再算命中率。百分数已是 0–100。null 不是 0。防守不是另一张官方球队表。",
        }
        return ctx.remember(source, data)
    resolved = match_name(name)
    if resolved and (not team or resolved["team"] == team):
        name = resolved["name_en"]
        team = team or resolved["team"]
    doc = load_slice()
    player, matches = find_player(doc, name, team)
    if player is None and matches:
        source = "season:2025-26"
        return ctx.remember(source, {
            "source": source,
            "found": False,
            "reason": "多名球员对得上，请换更完整的名字或加上球队",
            "candidates": [f"{item['team']} {item['player_name']}" for item in matches[:8]],
        })
    if player is None:
        source = "season:2025-26"
        return ctx.remember(source, {"source": source, "found": False, "query": name, "team": team})
    source = f"season:2025-26:{player['team']}:{player['player_name']}"
    data = {
        "source": source,
        "found": True,
        "team": player["team"],
        "player_id": player["player_id"],
        "player_name": player["player_name"],
        **_name_fields(player["player_name"]),
        "offense": player.get("offense") or {},
        "defense": player.get("defense") or {},
        "profile": player.get("profile"),
        "missing": [
            key for key, value in (player.get("profile") or {}).items() if value is None
        ],
        "note": doc.get("note"),
    }
    return ctx.remember(source, data)


def read_identity(ctx: Ctx, _payload: dict) -> dict:
    source = f"identity/{ctx.job_id}.json"
    doc = ctx.media.get_json(source)
    if not isinstance(doc, dict):
        return ctx.remember(source, {"source": source, "found": False})
    votes = []
    for item in (doc.get("votes") or [])[:30]:
        if not isinstance(item, dict):
            continue
        spoken = _spoken(item.get("nameZh") or item.get("name") or "")
        votes.append({
            "team": item.get("team") or "",
            "number": item.get("number") or "",
            "name": spoken.get("name") or "",
            **{key: spoken[key] for key in ("name_zh", "name_en") if key in spoken},
        })
    matches = []
    for item in (doc.get("matches") or [])[:30]:
        if not isinstance(item, dict):
            continue
        spoken = _spoken(item.get("name") or item.get("nameZh") or "")
        matches.append({
            "t": item.get("t"),
            "team": item.get("team") or "",
            "number": item.get("number") or "",
            "name": spoken.get("name") or "",
            **{key: spoken[key] for key in ("name_zh", "name_en") if key in spoken},
        })
    pinned = []
    for item in doc.get("pinned") or []:
        if not isinstance(item, dict):
            continue
        spoken = _spoken(item.get("nameZh") or item.get("name") or "")
        pinned.append({
            "team": item.get("team") or "",
            "number": item.get("number") or "",
            "name": spoken.get("name") or "",
            "role": item.get("role") or "",
            **{key: spoken[key] for key in ("name_zh", "name_en") if key in spoken},
        })
    first_second = []
    for item in doc.get("firstSecond") or []:
        if isinstance(item, str):
            first_second.append(_spoken(item).get("name") or item)
        else:
            first_second.append(item)
    return ctx.remember(source, {
        "source": source,
        "found": True,
        "firstSecond": first_second,
        "pinned": pinned,
        "votes": votes,
        "matches": matches,
    })


TOOLS = {
    "read_manifest": read_manifest,
    "validate_manifest": validate_manifest,
    "read_official": read_official,
    "read_season": read_season,
    "read_identity": read_identity,
}

TOOL_SPECS = [
    {"toolSpec": {"name": "read_manifest", "description": "读取本段回放的事件清单 structured/{jobId}.json，含回合、事件和清单里已经对上的官方出手。", "inputSchema": {"json": {"type": "object", "properties": {}}}}},
    {"toolSpec": {"name": "validate_manifest", "description": "校验事件清单格式：version 0.1，possessions 非空，进攻方只能是 HOU 或 DAL。", "inputSchema": {"json": {"type": "object", "properties": {}}}}},
    {"toolSpec": {"name": "read_official", "description": "读取媒体桶 official/2025-26/ 的逐回合和 shot quality。xfg_pct 已是百分数。不给 game_id 时只返回有哪些比赛。不给比赛钟时，如果清单里有这段的 clock，就只用那段；没有钟就不返回整场出手。", "inputSchema": {"json": {"type": "object", "properties": {"game_id": {"type": "string"}, "period": {"type": "integer"}, "clock_lo_s": {"type": "number", "description": "比赛钟剩余秒数下界"}, "clock_hi_s": {"type": "number", "description": "比赛钟剩余秒数上界"}}}}}},
    {"toolSpec": {"name": "read_season", "description": "读取 2025-26 赛季火箭或独行侠的球员攻防三区和个人资料。name 也可以是 HOU、DAL、火箭、独行侠，这时返回队内球员相加的三区。百分数已乘 100。null 和 missing 里的字段不是 0。查不到就说查不到。", "inputSchema": {"json": {"type": "object", "properties": {"name": {"type": "string"}, "team": {"type": "string", "description": "HOU 或 DAL"}}, "required": ["name"]}}}},
    {"toolSpec": {"name": "read_identity", "description": "读取认人结果 identity/{jobId}.json。只使用里面写出的姓名、球队和号码。", "inputSchema": {"json": {"type": "object", "properties": {}}}}},
]


def dispatch(name: str, payload: dict, ctx: Ctx) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        return {"source": name, "found": False, "error": "没有这个工具"}
    try:
        return fn(ctx, payload or {})
    except Exception as exc:  # noqa: BLE001 — 工具失败要告诉模型，不能把整次调用打崩
        return {"source": name, "found": False, "error": str(exc)[:300]}
