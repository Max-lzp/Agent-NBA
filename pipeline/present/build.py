#!/usr/bin/env python3
"""清单 + 追踪框 → 回放数据、烧录成片、中文解说。

    python3 -m pipeline.present --all
    python3 -m pipeline.present --clip exam

成片写到 web/assets/{clip}-broadcast.mp4。
回放数据写到 web/replay/clips.js，game.html 直接读。
页面上的英文原声和中文解说由 pipeline.present.tracks 另出，不混进这一步的短句。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lambdas"))
from common import TEAM_ZH, timeline_from_stream  # noqa: E402
from pipeline.enrich.deep_season import season_profile  # noqa: E402
from pipeline.enrich.official import position_zh  # noqa: E402
from pipeline.enrich.season_zones import decorate_shot  # noqa: E402
from pipeline.present.freeze import pick, render_replay  # noqa: E402
from pipeline.present.tracks import VOICE_NOTE  # noqa: E402

OUT = REPO / "pipeline" / "events" / "out"
VISION = REPO / "pipeline" / "vision" / "results" / "local"
WEB = REPO / "web"
WORK = Path(__file__).resolve().parent / "work"
FONT = WEB / "fonts" / "MiSans-Regular.ttf"
MIX = REPO / "commentary" / "mix.sh"

MIN_VOTES = 3
TAG_STEP = 0.2
FRAME_W, FRAME_H = 1920, 1080

# 只翻译名单里出现过的号码。没有条目就用清单里的英文短名。
NAME_ZH = {
    # HOU
    ("HOU", "0"): "霍利戴",
    ("HOU", "1"): "阿门",
    ("HOU", "2"): "芬尼史密斯",
    ("HOU", "7"): "杜兰特",
    ("HOU", "8"): "泰特",
    ("HOU", "10"): "史密斯",
    ("HOU", "15"): "谢泼德",
    ("HOU", "17"): "伊森",
    ("HOU", "20"): "奥科吉",
    ("HOU", "28"): "申京",
    ("HOU", "30"): "卡佩拉",
    # DAL（考试片固定独行侠进攻，优先覆盖 0022500701 出场）
    ("DAL", "00"): "克里斯蒂",
    ("DAL", "0"): "克里斯蒂",
    ("DAL", "1"): "哈迪",
    ("DAL", "3"): "戴维斯",
    ("DAL", "5"): "拉塞尔",
    ("DAL", "7"): "鲍威尔",
    ("DAL", "9"): "南姆哈德",
    ("DAL", "10"): "威廉姆斯",
    ("DAL", "13"): "马歇尔",
    ("DAL", "16"): "马丁",
    ("DAL", "21"): "加福德",
    ("DAL", "25"): "华盛顿",
    ("DAL", "30"): "西塞",
    ("DAL", "31"): "克莱",
    ("DAL", "32"): "弗拉格",
}
ZONE_ZH = {
    "paint": "油漆区",
    "restricted": "合理冲撞区",
    "midrange": "中距离",
    "corner3_l": "左底角三分",
    "corner3_r": "右底角三分",
    "wing3_l": "左侧翼三分",
    "wing3_r": "右侧翼三分",
    "top3": "弧顶三分",
}
PERIOD_ZH = {"1ST": "第一节", "2ND": "第二节", "3RD": "第三节", "4TH": "第四节", "OT": "加时"}
DIG = "零一二三四五六七八九"


def int_zh(n: int) -> str:
    n = int(n)
    if n < 0:
        return "负" + int_zh(-n)
    if n < 10:
        return DIG[n]
    if n < 20:
        return "十" + (DIG[n % 10] if n % 10 else "")
    if n < 100:
        tens, ones = divmod(n, 10)
        return DIG[tens] + "十" + (DIG[ones] if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        head = DIG[hundreds] + "百"
        if rest == 0:
            return head
        if rest < 10:
            return head + "零" + DIG[rest]
        return head + int_zh(rest)
    return str(n)


def xfg_zh(x: float) -> str:
    pct = int(round(float(x) * 100))
    tens, ones = divmod(pct, 10)
    if tens <= 0:
        return f"不足一成（{DIG[ones]}）"
    if ones == 0:
        return f"{DIG[tens]}成"
    return f"{DIG[tens]}成{DIG[ones]}"


def clock_zh(clock: str) -> str:
    minute, second = clock.split(":")
    return f"{int_zh(int(minute))}分{int_zh(int(second))}秒"


def display_name(team: str, number: str, fallback: str) -> str:
    key = (team or "", str(number or "").lstrip("0") or ("0" if number == "0" else number))
    if number in ("00", "0"):
        key = (team or "", "00")
    return NAME_ZH.get((team or "", str(number or "")), "") or NAME_ZH.get(key, "") or fallback or ""


def period_zh(period: str | None) -> str:
    if not period:
        return ""
    return PERIOD_ZH.get(period.upper(), period)


def contrast(made, xfg) -> str:
    """决策反差：只比较官方预期命中率和这一球的结果。"""
    if made is None or xfg is None:
        return ""
    if made and float(xfg) < 0.40:
        return "英雄球：预期低于四成却进了"
    if (not made) and float(xfg) > 0.60:
        return "可惜：预期高于六成却没进"
    return ""


def shot_phrase(official: dict) -> str:
    desc = (official.get("description") or "").lower()
    kind = (official.get("shot_type") or "").lower()
    if "dunk" in kind or "dunk" in desc:
        return "扣篮"
    if "finger" in desc:
        return "挑篮"
    if "layup" in kind or "layup" in desc:
        return "上篮"
    if "pullup" in desc or "pull-up" in desc or "step back" in desc:
        return "后撤步跳投"
    if official.get("value") == 3 or "3pt" in desc:
        return "三分"
    return "跳投"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _video_path(stream: dict) -> Path:
    src = (stream.get("video") or {}).get("source") or ""
    path = Path(src)
    if not path.is_absolute():
        path = (REPO / src).resolve()
    return path


def _allowed_tracks(vision: dict) -> dict[int, dict]:
    allowed = {}
    for player in vision.get("players") or []:
        if player.get("role") != "player":
            continue
        votes = int(player.get("jersey_votes") or 0)
        name = player.get("name") or ""
        if votes < MIN_VOTES or not name:
            continue
        allowed[int(player["track_id"])] = player
    return allowed


def _tags(vision: dict, allowed: dict[int, dict]) -> list[dict]:
    frames = []
    next_t = 0.0
    for frame in vision.get("timeline") or []:
        t = float(frame.get("t") or 0)
        if t + 1e-6 < next_t:
            continue
        next_t = t + TAG_STEP
        best: dict[tuple, dict] = {}
        for player in frame.get("players") or []:
            track = int(player.get("track_id") or -1)
            meta = allowed.get(track)
            if not meta:
                continue
            box = player.get("bbox") or []
            if len(box) != 4:
                continue
            x, y, w, h = [float(v) for v in box]
            if h < 48 or w < 24:
                continue
            team = meta.get("team") or player.get("team") or ""
            number = str(meta.get("jersey") or player.get("jersey") or "")
            name = display_name(team, number, meta.get("name") or "")
            if not name:
                continue
            key = (team, number or name)
            area = w * h
            prev = best.get(key)
            if prev and prev["area"] >= area:
                continue
            best[key] = {
                "area": area,
                "name": name,
                "jersey": number,
                "team": team,
                "x": round(x, 1),
                "y": round(y, 1),
                "w": round(w, 1),
                "h": round(h, 1),
            }
        if best:
            frames.append({
                "t": round(t, 3),
                "players": [{k: v for k, v in row.items() if k != "area"} for row in best.values()],
            })
    return frames


def _score_reads(board: dict) -> list[dict]:
    away = board.get("away") or ""
    home = board.get("home") or ""
    rows = []
    for read in board.get("reads") or []:
        scores = read.get("scores") or []
        if len(scores) < 2 or not read.get("clock"):
            continue
        rows.append({
            "t": round(float(read.get("t") or 0), 3),
            "period": period_zh(read.get("period")),
            "clock": read["clock"],
            "away": away,
            "home": home,
            "awayScore": int(scores[0]),
            "homeScore": int(scores[1]),
            "awayName": TEAM_ZH.get(away, away),
            "homeName": TEAM_ZH.get(home, home),
        })
    return rows


def _at(reads: list[dict], t: float) -> dict | None:
    found = None
    for read in reads:
        if read["t"] <= t + 0.05:
            found = read
        else:
            break
    return found


def _defender(defender: dict | None) -> dict | None:
    if not defender:
        return None
    team = defender.get("team") or ""
    return {**defender, "display": display_name(team, str(defender.get("number") or ""), defender.get("name") or "")}


def _shots(stream: dict) -> list[dict]:
    shots = []
    for poss in stream.get("possessions") or []:
        for ev in poss.get("events") or []:
            if ev.get("type") != "shot":
                continue
            official = ev.get("official") or {}
            geom = ev.get("geometry") or {}
            actor = ev.get("actor") or {}
            team = actor.get("team") or ""
            number = str(actor.get("number") or "")
            name = display_name(team, number, actor.get("name") or "")
            made = official.get("made")
            xfg = official.get("xfg")
            profile = official.get("season_profile") or season_profile(
                player_id=official.get("player_id"),
                team=team,
                number=number,
                name=actor.get("name") or "",
            )
            assist = official.get("assist") or {}
            assist_name = ""
            if assist:
                assist_name = display_name(assist.get("team") or "", str(assist.get("number") or ""), assist.get("name") or "")
            zone = ZONE_ZH.get(geom.get("shot_zone") or "", geom.get("shot_zone") or "")
            shot = {
                "t": round(float(ev.get("t") or 0), 3),
                "name": name,
                "team": team,
                "number": number,
                "made": made,
                "value": official.get("value"),
                "xfg": xfg,
                "zone": zone,
                "x": geom.get("x_ft"),
                "y": geom.get("y_ft"),
                "distance": official.get("distance_ft"),
                "description": official.get("description") or "",
                "assist": assist_name,
                "phrase": shot_phrase(official) if official else "出手",
                "contrast": contrast(made, xfg),
                "season_margin": official.get("season_margin"),
                "season_profile": profile,
                "zoneKey": geom.get("shot_zone") or "",
                "release": ev.get("release"),
                "leadBox": (ev.get("defense") or {}).get("lead_box"),
                "leadPath": (ev.get("defense") or {}).get("lead_path"),
                "shooterCheck": official.get("shooter_check"),
                "defender": _defender(official.get("defender")),
                "season": official.get("shooter_season"),
                "shooterRank": official.get("shooter_rank"),
                "zoneChart": official.get("zone_chart"),
            }
            defender = shot.get("defender") or {}
            decorate_shot(
                shot,
                shooter_id=official.get("player_id") or official.get("person_id"),
                defender_id=defender.get("person_id"),
                shooter_team=team,
                defender_team=defender.get("team") or "",
            )
            shots.append(shot)
    return shots


def _script(shot: dict | None, read: dict | None, board_end: dict | None) -> list[str]:
    if shot and shot.get("made") is not None:
        result = "进了" if shot["made"] else "没进"
        feet = ""
        if shot.get("distance"):
            feet = int_zh(int(round(float(shot["distance"])))) + "英尺"
        line = f"{result}。{shot['name']}{feet}{shot['phrase']}。"
        second = ""
        if shot.get("xfg") is not None:
            second = f"预期命中率{xfg_zh(shot['xfg'])}"
        if shot.get("assist") and shot["made"]:
            second += f"，{shot['assist']}助攻"
        if second:
            line += second + "。"
        lines = [line]
        if board_end:
            lines.append(
                f"{board_end['period']}，{board_end['awayName']}{int_zh(board_end['awayScore'])}，"
                f"{board_end['homeName']}{int_zh(board_end['homeScore'])}。"
            )
        return lines
    if read:
        return [f"{read['period']}{clock_zh(read['clock'])}，{read['awayName']}{int_zh(read['awayScore'])}，{read['homeName']}{int_zh(read['homeScore'])}。"]
    return ["这一段没有对上官方出手。画面上的名字来自球衣号码。"]


def _audio_fields(clip: str) -> dict:
    """页面音轨。文件由 pipeline.present.tracks 生成；没有文件时不假装已经换上。"""
    meta_path = Path(__file__).resolve().parent / "audio" / f"{clip}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    tracks = {}
    for lang in ("zh", "en"):
        if (WEB / "assets" / f"{clip}-{lang}.mp4").exists():
            tracks[lang] = f"assets/{clip}-{lang}.mp4"
    fields = {
        "scriptEn": meta.get("scriptEn") or "",
        "scriptZh": meta.get("scriptZh") or "",
        "voice": VOICE_NOTE if tracks else "本机中文语音 Tingting。解说由程序根据官方事件生成。Polly 开通后只替换声音。",
    }
    if tracks:
        fields["tracks"] = tracks
    return fields


def build_pack(clip: str) -> dict:
    stream_path = OUT / f"{clip}.json"
    vision_path = VISION / f"{clip}.json"
    board_path = VISION / f"{clip}.scoreboard.json"
    if not stream_path.exists():
        raise SystemExit(f"缺少清单 {stream_path}")
    stream = _load(stream_path)
    vision = _load(vision_path) if vision_path.exists() else {}
    board = _load(board_path) if board_path.exists() else {}
    reads = _score_reads(board)
    shots = _shots(stream)
    tags = _tags(vision, _allowed_tracks(vision)) if vision else []
    rows = timeline_from_stream(stream)
    events = []
    shot_by_t = {round(s["t"], 2): s for s in shots}
    for row in rows:
        shot = shot_by_t.get(round(float(row["t"]), 2))
        clock = _at(reads, float(row["t"]))
        events.append({
            "t": row["t"],
            "timecode": clock["clock"] if clock else row["timecode"],
            "label": row["label"],
            "eventType": row["eventType"],
            "detail": row["detail"],
            "source": row["source"],
            "shot": shot,
        })
        if shot:
            result = "命中" if shot.get("made") else "不中" if shot.get("made") is False else "出手"
            assist = f"（{shot['assist']} 助攻）" if shot.get("assist") and shot.get("made") else ""
            xfg = f" · 预期命中率 {int(round(float(shot['xfg']) * 100))}%" if shot.get("xfg") is not None else ""
            events[-1]["label"] = f"{shot['name']} {shot.get('value') or ''} 分{result}{assist}{xfg}".strip()
    focus = max(shots, key=lambda s: s["t"]) if shots else None
    end = reads[-1] if reads else None
    spoken = _script(focus, reads[0] if reads else None, end)
    official = stream.get("official") or {}
    head = reads[0] if reads else {}
    title = " ".join(part for part in (head.get("period"), head.get("clock"), f"{head.get('awayName', '')}对{head.get('homeName', '')}" if head else "") if part)
    return {
        "clip": clip,
        "title": title or clip,
        "dataset": "训练数据" if "train_video" in _video_path(stream).parts else "",
        "video": f"assets/{clip}-broadcast.mp4",
        "width": FRAME_W,
        "height": FRAME_H,
        "duration": float((stream.get("video") or {}).get("duration_s") or vision.get("duration_sec") or 0),
        "gameId": official.get("game_id") or "",
        "away": head.get("away") or board.get("away") or "",
        "home": head.get("home") or board.get("home") or "",
        "scorebug": _compact_scorebug(reads),
        "tags": tags,
        "events": events,
        "shots": shots,
        "onCourt": _on_court(official.get("on_court") or {}),
        "possessions": [
            {"t0": p.get("t_start") or 0, "t1": p.get("t_end") or 0, "team": p.get("offense_team") or ""}
            for p in stream.get("possessions") or []
        ],
        "passes": [
            {"t": ev["t"]}
            for poss in stream.get("possessions") or []
            for ev in poss.get("events") or []
            if ev.get("type") == "pass" and ev.get("t") is not None
        ],
        "playersFull": stream.get("players_full") or [],
        "ballPath": stream.get("ball_path") or [],
        "script": spoken,
        **_audio_fields(clip),
        "anchor": focus["t"] if focus else 0.8,
        "sourceVideo": str(_video_path(stream)),
        "replayVideo": f"assets/{clip}-replay.mp4" if (WEB / "assets" / f"{clip}-replay.mp4").exists() else "",
    }


def _compact_scorebug(reads: list[dict]) -> list[dict]:
    if not reads:
        return []
    compact = [reads[0]]
    for read in reads[1:]:
        prev = compact[-1]
        if (read["clock"], read["awayScore"], read["homeScore"], read["period"]) == (
            prev["clock"], prev["awayScore"], prev["homeScore"], prev["period"]
        ):
            continue
        compact.append(read)
    return compact


def _on_court(raw: dict) -> dict:
    out = {}
    for team, players in raw.items():
        out[team] = [{
            "jersey": p.get("jersey") or "",
            "name": display_name(team, str(p.get("jersey") or ""), p.get("name") or ""),
            "full": p.get("full_name") or p.get("name") or "",
            "position": position_zh(p.get("position")),
        } for p in players]
    return out


def _card_lines(shot: dict) -> tuple[str, str]:
    result = "命中" if shot.get("made") else "不中" if shot.get("made") is False else "出手"
    head = f"{shot['name']} · {result}"
    bits = []
    if shot.get("xfg") is not None:
        bits.append(f"预期命中率 {int(round(float(shot['xfg']) * 100))}%")
    if shot.get("zone"):
        bits.append(shot["zone"])
    if shot.get("distance"):
        bits.append(f"{float(shot['distance']):.0f} 英尺")
    if shot.get("contrast"):
        bits.append(shot["contrast"].split("：")[0])
    return head, " · ".join(bits)


def _deep_line(shot: dict) -> str:
    profile = shot.get("season_profile") or {}
    if profile.get("xfg") is not None and profile.get("fg_above") is not None:
        gap = float(profile["fg_above"]) * 100
        word = "高于预期" if gap >= 0 else "低于预期"
        return f"赛季预期 {float(profile['xfg']) * 100:.1f}% · {word} {abs(gap):.1f} 个点"
    bits = []
    if profile.get("gravity") is not None:
        bits.append(f"赛季引力 {float(profile['gravity']):.2f}")
    if profile.get("leverage") is not None:
        bits.append(f"赛季关键度 {float(profile['leverage']):.2f}")
    return " · ".join(bits)


def _margin_line(shot: dict) -> str:
    margin = shot.get("season_margin") or {}
    if margin.get("ts") is None:
        return ""
    line = f"赛季{margin['bucket_zh']} 真实命中率 {float(margin['ts']) * 100:.1f}%"
    if margin.get("team_ts") is not None:
        diff = (float(margin["ts"]) - float(margin["team_ts"])) * 100
        line += f" · 比全队同档{'高' if diff >= 0 else '低'} {abs(diff):.1f} 个百分点"
    return line


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        venv = REPO / "pipeline" / "vision" / ".venv" / "bin" / "python"
        if venv.exists() and Path(sys.executable).resolve() != venv.resolve():
            os.execv(str(venv), [str(venv), "-m", "pipeline.present", *sys.argv[1:]])
        raise SystemExit("烧录文字需要 Pillow。视觉环境里已经装了。")


def _label(draw, font, xy, text: str) -> None:
    x, y = xy
    box = draw.textbbox((x, y), text, font=font)
    draw.rectangle((box[0] - 14, box[1] - 8, box[2] + 14, box[3] + 8), fill=(20, 17, 14, 214))
    draw.text((x, y), text, font=font, fill=(243, 237, 226, 255))


NAME_RED = (255, 43, 43, 255)
NAME_GOLD = (245, 197, 66, 255)
NAME_EDGE = {"DAL": (70, 140, 220, 255), "HOU": (205, 205, 205, 255)}


def _at_path(path: list | None, t: float, slack: float = 0.12) -> list[float] | None:
    best = None
    for row in path or []:
        gap = abs(row[0] - t)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, row[1:])
    return best[1] if best else None


def _iou(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def _focused_tags(pack: dict, t: float) -> list[tuple]:
    """回溯名单：前 3 秒标出这一帧上已经认出的人；之后只标持球人、传球双方和防守人。"""
    from pipeline.vision.backtrack import visible_at

    shot_t = None
    for shot in pack.get("shots") or []:
        rel = (shot.get("release") or {}).get("t")
        if rel is not None:
            shot_t = float(rel)
    shown = visible_at(
        pack.get("playersFull") or [], pack.get("ballPath") or [], pack.get("passes") or [],
        pack.get("possessions") or [], t, shot_t,
    )
    handler = next((p for p in shown if p["role"] == "handler"), None)
    tags = []
    for person in shown:
        name = display_name(person.get("team") or "", str(person.get("number") or ""), person.get("name") or "")
        text = f"{name} #{person['number']}".strip()
        if person["role"] == "handler":
            edge = NAME_RED
        elif person["role"] in ("defender", "lead"):
            edge = NAME_GOLD
        else:
            edge = NAME_EDGE.get(person.get("team") or "", NAME_EDGE["HOU"])
        side = None
        if handler and person["role"] in ("defender", "lead"):
            side = "right" if person["box"][0] + person["box"][2] >= handler["box"][0] + handler["box"][2] else "left"
        tags.append((text, person["box"], edge, side))
    return tags


def name_tags_at(pack: dict, t: float) -> list[tuple]:
    """(文字, 源视频坐标框 x1 y1 x2 y2, 边色, 贴边)。有回溯名单时按前 3 秒全员、之后聚焦。
    没有回溯名单时，出手前跟住的投篮人和领防人一定标；其他人只标号码票数够的轨迹。"""
    if pack.get("playersFull"):
        return _focused_tags(pack, t)
    out: list[tuple] = []
    taken: set[tuple[str, str]] = set()
    for shot in pack.get("shots") or []:
        release = shot.get("release") or {}
        if release.get("t") is None or not (t <= float(release["t"]) + 0.3):
            continue
        shooter = _at_path(release.get("shooter_path"), t)
        if shooter:
            out.append((f"{shot['name']} #{shot.get('number') or ''}".strip(), shooter, NAME_RED, None))
            taken.add((shot.get("team") or "", str(shot.get("number") or "")))
        d = shot.get("defender") or {}
        box = _at_path(shot.get("leadPath"), t) if d.get("name") else None
        if box:
            ref = shooter or release.get("shooter_box") or box
            side = "right" if box[0] + box[2] >= ref[0] + ref[2] else "left"
            out.append((f"{d.get('display') or d['name']} #{d['number']}", box, NAME_GOLD, side))
            taken.add((d.get("team") or "", str(d.get("number") or "")))
    frame = None
    for row in pack.get("tags") or []:
        if abs(row["t"] - t) <= 0.15 and (frame is None or abs(row["t"] - t) < abs(frame["t"] - t)):
            frame = row
    for p in (frame or {}).get("players") or []:
        if (p.get("team") or "", str(p.get("jersey") or "")) in taken:
            continue
        box = [p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"]]
        if any(_iou(box, row[1]) > 0.4 for row in out):
            continue
        out.append((f"{p['name']} #{p['jersey']}", box, NAME_EDGE.get(p.get("team"), NAME_EDGE["HOU"]), None))
    return out


def _hits(rect: tuple, placed: list[tuple]) -> bool:
    return any(rect[0] < r[2] and r[0] < rect[2] and rect[1] < r[3] and r[1] < rect[3] for r in placed)


def tag_rect(draw, font, text: str, box: list[float], scale: float, side, placed: list[tuple],
             width: float = 1e9) -> tuple | None:
    """标签放哪：贴边的放在躯干旁，那一侧放不下换另一侧；头顶的撞到已放标签就往上挪，挪不开就不画。"""
    tb = draw.textbbox((0, 0), text, font=font)
    w, h = tb[2] - tb[0] + 16, tb[3] - tb[1] + 10
    x1, y1, x2, y2 = (v * scale for v in box)
    if side:
        y = y1 + (y2 - y1) * 0.25
        for s in (side, "left" if side == "right" else "right"):
            x = x2 + 6 if s == "right" else x1 - w - 6
            rect = (x, y, x + w, y + h)
            if x >= 0 and x + w <= width and not _hits(rect, placed):
                return rect
        return None
    x = (x1 + x2) / 2 - w / 2
    for k in range(3):
        y = y1 - h - 6 - k * (h + 4)
        rect = (x, y, x + w, y + h)
        if y >= 4 and not _hits(rect, placed):
            return rect
    return None


def draw_tag(draw, font, text: str, rect: tuple, edge: tuple, edge_right: bool = False) -> None:
    tb = draw.textbbox((0, 0), text, font=font)
    x, y, x2, y2 = rect
    draw.rectangle(rect, fill=(20, 17, 14, 214))
    ex = x2 - 4 if edge_right else x
    draw.rectangle((ex, y, ex + 4, y2), fill=edge)
    draw.text((x + (7 if edge_right else 9), y + 4 - tb[1]), text, font=font, fill=(243, 237, 226, 255))


def draw_names(draw, font, tags: list[tuple], scale: float, placed: list[tuple] | None = None,
               width: float = 1e9) -> None:
    placed = placed if placed is not None else []
    for text, box, edge, side in tags:
        rect = tag_rect(draw, font, text, box, scale, side, placed, width)
        if rect:
            placed.append(rect)
            draw_tag(draw, font, text, rect, edge, edge_right=rect[0] < box[0] * scale)


def freeze_others(pack: dict) -> list[tuple[str, list[float], tuple]]:
    shot = pick(pack)
    if not shot:
        return []
    return [row for row in name_tags_at(pack, float(shot["release"]["t"])) if row[2] not in (NAME_RED, NAME_GOLD)]


def write_overlay(clip: str, pack: dict) -> Path:
    """本机 ffmpeg 没有 drawtext。用 Pillow 画透明层，再交给 overlay。"""
    Image, ImageDraw, ImageFont = _pil()
    WORK.mkdir(parents=True, exist_ok=True)
    width, height, fps = 1280, 720, 10
    duration = float(pack["duration"] or 23)
    frames = int(duration * fps) + 1
    font = ImageFont.truetype(str(FONT), 34)
    small = ImageFont.truetype(str(FONT), 26)
    tiny = ImageFont.truetype(str(FONT), 18)
    scale = width / float(pack.get("width") or width)
    dest = WORK / f"{clip}-overlay.mov"
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps),
            "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb", str(dest),
        ],
        stdin=subprocess.PIPE,
    )
    assert proc.stdin is not None
    for index in range(frames):
        t = index / fps
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw_names(draw, tiny, name_tags_at(pack, t), scale, width=width)
        for shot in pack["shots"]:
            if float(shot["t"]) - 0.25 <= t <= float(shot["t"]) + 3.2:
                head, sub = _card_lines(shot)
                _label(draw, font, (28, 22), head)
                if sub:
                    _label(draw, small, (28, 74), sub)
                y = 120
                margin = _margin_line(shot)
                if margin:
                    _label(draw, small, (28, y), margin)
                    y = 166
                deep = _deep_line(shot)
                if deep:
                    _label(draw, small, (28, y), deep)
        proc.stdin.write(image.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"{clip} 透明层编码失败")
    return dest


def synthesize(text: str, dest: Path) -> None:
    """本机中文语音。Polly 可用时改为调用 Zhiyu，输出仍是这段文字的音频文件。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    aiff = dest.with_suffix(".aiff")
    subprocess.run(["say", "-v", "Tingting", "-r", "168", "-o", str(aiff), text], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(aiff), "-c:a", "aac", "-b:a", "160k", str(dest)],
        check=True,
    )


def render_video(clip: str, pack: dict) -> Path:
    source = Path(pack["sourceVideo"])
    if not source.exists():
        raise SystemExit(f"找不到视频 {source}")
    if not FONT.exists():
        raise SystemExit(f"找不到字体 {FONT}")
    WORK.mkdir(parents=True, exist_ok=True)
    overlay = write_overlay(clip, pack)
    bed = WORK / f"{clip}-bed.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-i", str(overlay),
        "-filter_complex", "[0:v]scale=1280:720[v];[v][1:v]overlay=format=auto",
        "-af", "volume=0.22",
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(bed),
    ]
    print("烧录", clip)
    subprocess.run(cmd, cwd=REPO, check=True)
    spoken = "".join(pack["script"])
    voice = WORK / f"{clip}-voice.m4a"
    print("解说", spoken)
    synthesize(spoken, voice)
    delay_ms = int(max(0.0, float(pack["anchor"]) - 0.4) * 1000)
    padded = WORK / f"{clip}-voice-pad.m4a"
    whole = max(float(pack["duration"]) + 0.4, 1)
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(voice),
        "-af", f"aformat=channel_layouts=stereo,adelay={delay_ms}|{delay_ms},apad=whole_dur={whole:.3f}",
        "-c:a", "aac", "-b:a", "160k", str(padded),
    ], check=True)
    final = WEB / "assets" / f"{clip}-broadcast.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["sh", str(MIX), str(bed), str(padded), str(final)], check=True)
    replay = render_replay(clip, pack, bed, padded, WEB / "assets" / f"{clip}-replay.mp4", WORK, FONT,
                           source, freeze_others(pack))
    if replay:
        pack["replayVideo"] = f"assets/{replay.name}"
        print("定格回放", replay)
    return final


def write_clips(packs: dict[str, dict]) -> Path:
    public = {}
    for clip, pack in packs.items():
        public[clip] = {k: v for k, v in pack.items() if k != "sourceVideo"}
    dest = WEB / "replay" / "clips.js"
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(public, ensure_ascii=False, separators=(",", ":"))
    dest.write_text(f"window.DEEPCOURT_CLIPS={body};\n", encoding="utf-8")
    return dest


def clips_from_disk() -> list[str]:
    return sorted(path.stem for path in OUT.glob("*.json"))


def main() -> None:
    parser = argparse.ArgumentParser(description="生成评委回放成片和页面数据")
    parser.add_argument("--clip", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--skip-video", action="store_true")
    args = parser.parse_args()
    names = args.clip or (["amen", "flagg"] if args.all or not args.clip else [])
    if args.all and not args.clip:
        names = [name for name in ("amen", "flagg") if (OUT / f"{name}.json").exists()]
    if not names:
        raise SystemExit("指定 --clip，或用 --all")
    if not shutil.which("ffmpeg"):
        raise SystemExit("需要本机 ffmpeg")
    packs = {}
    for name in names:
        pack = build_pack(name)
        packs[name] = pack
        print(
            f"{name}: 比分 {len(pack['scorebug'])} 段, 人名帧 {len(pack['tags'])}, "
            f"出手 {len(pack['shots'])}, 解说 {''.join(pack['script'])}"
        )
        if not args.skip_video:
            print(render_video(name, pack))
    # 保留已经生成、这次没重跑的片段，方便页面切换。
    existing = WEB / "replay" / "clips.js"
    if existing.exists():
        text = existing.read_text(encoding="utf-8")
        prefix = "window.DEEPCOURT_CLIPS="
        if text.startswith(prefix):
            previous = json.loads(text[len(prefix):].rstrip().rstrip(";"))
            for key, value in previous.items():
                packs.setdefault(key, value)
    print(write_clips(packs))


if __name__ == "__main__":
    main()
