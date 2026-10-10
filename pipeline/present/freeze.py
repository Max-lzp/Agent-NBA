"""进球后的出手定格烧进成片。

网页那份成片保持原时间轴，人名和时间线都按它对齐；这里另出一份 `{clip}-replay.mp4`：
进球后插入 5 秒出手帧，画红圈、防守人虚线圈、数据卡和分区热区图。解说照原时间铺，
定格期间现场声静音。
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

FREEZE_S = 5.0
OUT_W, OUT_H = 1280, 720
RED = (255, 43, 43, 255)
GOLD = (245, 197, 66, 255)
CREAM = (243, 237, 226, 255)
MUTED = (184, 172, 156, 255)
PANEL = (20, 17, 14, 228)
TEAM_ZH = {"HOU": "火箭", "DAL": "独行侠"}
ZONE_ZH = {
    "restricted": "合理冲撞区", "paint": "油漆区", "midrange": "中距离",
    "corner3_l": "左底角三分", "corner3_r": "右底角三分", "wing3_l": "左侧翼三分", "wing3_r": "右侧翼三分", "top3": "弧顶三分",
}
ZONE_LABEL_AT = {
    "restricted": (0, 1.2), "paint": (0, 10), "midrange": (-14, 11), "corner3_l": (-23.6, 3), "corner3_r": (23.6, 3),
    "wing3_l": (-19, 22), "wing3_r": (19, 22), "top3": (0, 26.5),
}
BASKET_Y = 5.25
CORNER_Y = math.sqrt(23.75 ** 2 - 22 ** 2)


def pick(pack: dict) -> dict | None:
    for shot in pack.get("shots") or []:
        if shot.get("made") and (shot.get("release") or {}).get("t") is not None:
            return shot
    return None


def _bug_at(pack: dict, t: float) -> dict | None:
    found = None
    for row in pack.get("scorebug") or []:
        if row["t"] <= t + 0.05:
            found = row
    return found


def goal_time(pack: dict, shot: dict) -> float:
    release = float(shot["release"]["t"])
    before = _bug_at(pack, release)
    after = None
    if before:
        total = before["awayScore"] + before["homeScore"]
        after = next((row for row in pack.get("scorebug") or []
                      if row["t"] > release and row["awayScore"] + row["homeScore"] > total), None)
    at = after["t"] + 0.3 if after else release + 1.5
    limit = float(pack.get("duration") or 0) - 0.4
    return min(at, limit) if limit > release else at


def zone_of(x: float, y: float) -> str:
    three = abs(x) >= 22 if y <= CORNER_Y else math.hypot(x, y) >= 23.75
    if three:
        if abs(x) < 8:
            return "top3"
        side = "l" if x < 0 else "r"
        return f"corner3_{side}" if y <= 9 else f"wing3_{side}"
    if math.hypot(x, y) <= 4:
        return "restricted"
    if abs(x) <= 8 and y <= 14:
        return "paint"
    return "midrange"


def _pct(v, digits: int = 1) -> str:
    return f"{float(v) * 100:.{digits}f}%"


def card_lines(pack: dict, shot: dict) -> list[tuple[str, str]]:
    """(样式, 文字)。样式 head / score / off / def / line。"""
    lines: list[tuple[str, str]] = [("head", "回放 · 出手瞬间")]
    bug = _bug_at(pack, float(shot["release"]["t"]))
    if bug:
        mine = bug["awayScore"] - bug["homeScore"] if shot.get("team") == bug["away"] else bug["homeScore"] - bug["awayScore"]
        gap = "打平" if mine == 0 else f"{TEAM_ZH.get(shot.get('team'), shot.get('team'))}{'领先' if mine > 0 else '落后'} {abs(mine)} 分"
        lines.append(("score", f"{bug['period']} {bug['clock']} · {bug['awayName']} {bug['awayScore']} : {bug['homeScore']} {bug['homeName']} · {gap}"))
    lines.append(("off", f"{shot['name']} #{shot.get('number') or ''}".strip()))
    season = shot.get("season")
    if season:
        lines.append(("line", f"赛季场均 {season['pts']} 分 · 投篮 {_pct(season['fg_pct'])} · 三分 {_pct(season['fg3_pct'])}"))
    if shot.get("xfg") is not None:
        where = " · ".join(b for b in (shot.get("zone"), f"{float(shot['distance']):.0f} 英尺" if shot.get("distance") else "") if b)
        lines.append(("line", f"这一投预期命中率 {_pct(shot['xfg'])}" + (f" · {where}" if where else "")))
    margin = shot.get("season_margin") or {}
    if margin.get("ts") is not None:
        text = f"{margin['bucket_zh']}真实命中率 {_pct(margin['ts'])}"
        if margin.get("team_ts") is not None:
            diff = (float(margin["ts"]) - float(margin["team_ts"])) * 100
            text += f"，比全队同档{'高' if diff >= 0 else '低'} {abs(diff):.1f} 个百分点"
        lines.append(("line", text))
    near = shot.get("nearest")
    defender = shot.get("defender")
    if near or defender:
        lines.append(("def", "防守"))
    if near:
        who = near.get("display") or f"{TEAM_ZH.get(near.get('team'), near.get('team') or '')}球员" + (
            f" #{near['number']}" if near.get("number") else "，号码没读出")
        lines.append(("line", f"最近防守人：{who}（画面）"))
    if defender:
        head = "本场主防（对位表）" if defender.get("source") == "matchup" else "防守人"
        text = f"{head}：{defender.get('display') or defender.get('name')} #{defender.get('number')}"
        vs = defender.get("vs_shooter")
        if vs:
            text += f" · 对位 {round(vs['seconds'] / 60)} 分钟，{shot['name']} {vs['fgm']}/{vs['fga']}"
        lines.append(("line", text))
        ds = defender.get("season")
        if ds:
            lines.append(("line", f"赛季场均 抢断 {ds['stl']} · 盖帽 {ds['blk']} · 防守篮板 {ds['dreb']}"))
    return lines


def _ellipse(draw, box, scale, color, fill=None, dashed=False):
    x1, _, x2, y2 = box
    cx, cy = (x1 + x2) / 2 * scale, y2 * scale
    rx = max((x2 - x1) * scale * 0.85, 26)
    ry = rx * 0.3
    bbox = (cx - rx, cy - ry, cx + rx, cy + ry)
    if fill:
        draw.ellipse(bbox, fill=fill)
    if dashed:
        for start in range(0, 360, 24):
            draw.arc(bbox, start, start + 14, fill=color, width=4)
    else:
        draw.ellipse(bbox, outline=color, width=5)
    return cx, cy


def _tag(draw, font, text, cx, top, color):
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0] + 16, box[3] - box[1] + 10
    x, y = cx - w / 2, max(4, top - h - 6)
    draw.rectangle((x, y, x + w, y + h), fill=PANEL)
    draw.rectangle((x, y, x + 4, y + h), fill=color)
    draw.text((x + 9, y + 4 - box[1]), text, font=font, fill=CREAM)


def _heat_fill(zone: dict | None):
    if not zone or zone.get("xfg") is None or zone.get("league_xfg") is None:
        return (92, 81, 70, 150)
    diff = float(zone["xfg"]) - float(zone["league_xfg"])
    a = int(255 * min(0.9, 0.3 + abs(diff) / 0.1 * 0.6))
    return (241, 104, 30, a) if diff >= 0 else (0, 100, 177, a)


def heatmap(Image, ImageDraw, font, shot: dict, px: float = 5.0):
    zones = (shot.get("zoneChart") or {}).get("zones") or {}
    w, h = int(50 * px), int(34 * px)
    img = Image.new("RGBA", (w, h), (42, 36, 28, 240))
    draw = ImageDraw.Draw(img)
    sx = lambda x: (25 + x) * px  # noqa: E731
    sy = lambda y: (BASKET_Y + y) * px  # noqa: E731
    y = -BASKET_Y
    while y < 34 - BASKET_Y:
        x = -25
        while x < 25:
            zone = zone_of(x + 0.5, y + 0.5)
            draw.rectangle((sx(x), sy(y), sx(x + 1), sy(y + 1)), fill=_heat_fill(zones.get(zone)))
            if zone == shot.get("zoneKey"):
                draw.rectangle((sx(x), sy(y), sx(x + 1), sy(y + 1)), fill=(255, 43, 43, 70))
            x += 1
        y += 1
    line = (20, 17, 14, 200)
    draw.rectangle((sx(-8), 0, sx(8), 19 * px), outline=line, width=2)
    draw.ellipse((sx(-4), sy(-4), sx(4), sy(4)), outline=line, width=2)
    draw.line((sx(-22), 0, sx(-22), sy(CORNER_Y)), fill=line, width=2)
    draw.line((sx(22), 0, sx(22), sy(CORNER_Y)), fill=line, width=2)
    r = 23.75 * px
    start = math.degrees(math.atan2(CORNER_Y, 22))
    draw.arc((sx(0) - r, sy(0) - r, sx(0) + r, sy(0) + r), start, 180 - start, fill=line, width=2)
    for key, (zx, zy) in ZONE_LABEL_AT.items():
        zone = zones.get(key)
        text = _pct(zone["xfg"], 0) if zone and zone.get("xfg") is not None else "—"
        draw.text((sx(zx), sy(zy)), text, font=font, fill=CREAM, anchor="mm", stroke_width=2, stroke_fill=(20, 17, 14, 220))
    if shot.get("x") is not None and shot.get("y") is not None:
        cx, cy = sx(float(shot["x"])), sy(float(shot["y"]))
        draw.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=(255, 43, 43, 90), outline=RED, width=4)
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=RED)
    return img


def draw_still(frame: Path, dest: Path, pack: dict, shot: dict, font_path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    base = Image.open(frame).convert("RGBA").resize((OUT_W, OUT_H))
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    big = ImageFont.truetype(str(font_path), 24)
    mid = ImageFont.truetype(str(font_path), 19)
    small = ImageFont.truetype(str(font_path), 16)
    tiny = ImageFont.truetype(str(font_path), 13)
    scale = OUT_W / float(pack.get("width") or OUT_W)

    near = shot.get("nearest") or {}
    if near.get("box"):
        cx, _ = _ellipse(draw, near["box"], scale, GOLD, dashed=True)
        _tag(draw, small, "最近防守", cx, near["box"][1] * scale, GOLD)
    box = shot["release"]["shooter_box"]
    draw.rounded_rectangle(tuple(v * scale for v in box), radius=10, outline=(255, 43, 43, 200), width=3)
    cx, _ = _ellipse(draw, box, scale, RED, fill=(255, 43, 43, 80))
    _tag(draw, small, f"出手 · {shot['name']}", cx, box[1] * scale, RED)

    right = (box[0] + box[2]) / 2 * scale < OUT_W / 2
    card_w = 470
    x0 = OUT_W - card_w - 24 if right else 24
    y = 78
    rows = []
    for style, text in card_lines(pack, shot):
        font = {"head": big, "score": mid, "off": mid, "def": mid}.get(style, small)
        height = draw.textbbox((0, 0), text, font=font)[3] + (12 if style in ("off", "def") else 7)
        rows.append((style, text, font, height))
    card_h = sum(r[3] for r in rows) + 24
    draw.rectangle((x0, y, x0 + card_w, y + card_h), fill=PANEL)
    edge = x0 + card_w - 5 if right else x0
    draw.rectangle((edge, y, edge + 5, y + card_h), fill=RED)
    ty = y + 12
    for style, text, font, height in rows:
        tx = x0 + 18
        if style in ("off", "def"):
            ty += 5
            dot = RED if style == "off" else GOLD
            draw.ellipse((tx, ty + 6, tx + 10, ty + 16), fill=dot)
            tx += 18
        color = RED if style == "head" else CREAM if style in ("score", "off", "def") else MUTED
        draw.text((tx, ty), text, font=font, fill=color)
        ty += height

    if shot.get("zoneChart"):
        heat = heatmap(Image, ImageDraw, tiny, shot)
        zone = ((shot.get("zoneChart") or {}).get("zones") or {}).get(shot.get("zoneKey")) or {}
        name = ZONE_ZH.get(shot.get("zoneKey"), shot.get("zone") or "")
        cap = f"{shot['name']}分区热区图 · 这一投 {_pct(shot['xfg'])}" if shot.get("xfg") is not None else f"{shot['name']}分区热区图"
        if zone.get("xfg") is not None:
            sub = f"{name}平均 {_pct(zone['xfg'])}（{zone['fga']} 投）· 全部球员 {_pct(zone['league_xfg'])}"
        else:
            sub = f"{name}样本不足"
        panel_w = max(heat.width, draw.textlength(cap, font=small), draw.textlength(sub, font=tiny))
        hx = x0 + card_w - panel_w - 8 if right else x0 + 8
        hy = y + card_h + 16
        cap_h = 48
        if hy + heat.height + cap_h > OUT_H - 90:
            hx = OUT_W - panel_w - 32 if not right else 32
            hy = OUT_H - heat.height - cap_h - 96
        draw.rectangle((hx - 8, hy - 8, hx + panel_w + 8, hy + heat.height + cap_h), fill=PANEL)
        layer.alpha_composite(heat, (int(hx + (panel_w - heat.width) / 2), int(hy)))
        draw.text((hx, hy + heat.height + 6), cap, font=small, fill=CREAM)
        draw.text((hx, hy + heat.height + 28), sub, font=tiny, fill=MUTED)

    out = Image.alpha_composite(base, layer).convert("RGB")
    out.save(dest)
    return dest


def render_replay(clip: str, pack: dict, bed: Path, voice_pad: Path, dest: Path, work: Path, font_path: Path) -> Path | None:
    shot = pick(pack)
    if not shot:
        return None
    work.mkdir(parents=True, exist_ok=True)
    release = float(shot["release"]["t"])
    goal = goal_time(pack, shot)
    frame = work / f"{clip}-release.png"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{release:.3f}", "-i", str(bed),
        "-frames:v", "1", str(frame),
    ], check=True)
    still = draw_still(frame, work / f"{clip}-freeze.png", pack, shot, font_path)
    total = float(pack.get("duration") or 0) + FREEZE_S
    graph = (
        f"[0:v]trim=0:{goal:.3f},setpts=PTS-STARTPTS[a];"
        f"[1:v]scale={OUT_W}:{OUT_H},fps=60000/1001,format=yuv420p,trim=0:{FREEZE_S},setpts=PTS-STARTPTS[f];"
        f"[0:v]trim=start={goal:.3f},setpts=PTS-STARTPTS[b];"
        f"[a][f][b]concat=n=3:v=1:a=0[v];"
        f"[0:a]atrim=0:{goal:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[x];"
        f"anullsrc=r=48000:cl=stereo,atrim=0:{FREEZE_S}[s];"
        f"[0:a]atrim=start={goal:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[y];"
        f"[x][s][y]concat=n=3:v=0:a=1,aformat=channel_layouts=stereo[crowd];"
        f"[2:a]aresample=48000,aformat=channel_layouts=stereo,apad=whole_dur={total:.3f}[voice];"
        f"[crowd][voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
    )
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(bed), "-loop", "1", "-i", str(still), "-i", str(voice_pad),
        "-filter_complex", graph, "-map", "[v]", "-map", "[mix]",
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
    ], check=True)
    return dest
