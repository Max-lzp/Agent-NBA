"""进球后的出手定格烧进成片。

网页那份成片保持原时间轴，人名和时间线都按它对齐；这里另出一份 `{clip}-replay.mp4`：
进球后插入 5 秒出手帧，画红圈、领防人虚线圈、数据卡和分区热区图。解说照原时间铺，
定格期间现场声静音。
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

from pipeline.enrich.season_zones import profile_line, zone_lines

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


OFF_RANKS = (("pts", "场均得分", "{:.1f}"), ("fg_pct", "投篮命中率", "pct"), ("ts_pct", "真实命中率", "pct"))
DEF_RANKS = (("stl", "场均抢断", "{:.1f}"), ("blk", "场均盖帽", "{:.1f}"), ("dreb", "场均防守篮板", "{:.1f}"),
             ("def_rating", "防守效率", "{:.1f}"))


def rank_rows(ranks: dict | None, spec) -> list[tuple]:
    out = []
    for key, label, fmt in spec:
        r = (ranks or {}).get(key)
        if r:
            value = _pct(r["value"]) if fmt == "pct" else fmt.format(r["value"])
            out.append(("rank", f"{label} {value} · 队内第 {r['rank']}/{r['of']}", r["stars"]))
    return out


def lead_side(shooter_box: list[float], lead_box: list[float] | None) -> str | None:
    """领防标签贴在防守人躯干旁、背向投篮人的一侧：
    防守人中心在投篮人右边就放右边，否则放左边。没有领防框就不贴。"""
    if not lead_box:
        return None
    return "right" if (lead_box[0] + lead_box[2]) >= (shooter_box[0] + shooter_box[2]) else "left"


def card_lines(pack: dict, shot: dict) -> list[tuple]:
    """(样式, 文字[, 星级])。样式 head / off / def / line / rank。
    不再写比分条：比分跟着记分牌走，定格卡只讲这一投和领防。"""
    lines: list[tuple[str, str]] = [("head", "回放 · 出手瞬间")]
    lines.append(("off", f"{shot['name']} #{shot.get('number') or ''}（{shooter_source(shot)}）"))
    bio = profile_line(shot.get("shooterProfile"))
    if bio:
        lines.append(("line", bio))
    season = shot.get("season")
    ranked = rank_rows(shot.get("shooterRank"), OFF_RANKS)
    lines.extend(ranked)
    if season and not ranked:
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
    lines.append(("def", "防守"))
    d = shot.get("defender")

    def add_defender_bio() -> None:
        bio = profile_line(d.get("profile") if isinstance(d, dict) else None)
        if bio:
            lines.append(("line", bio))

    if not d or not d.get("name"):
        team = f"{TEAM_ZH.get(d.get('team'), d.get('team'))}球员，" if d and d.get("team") else ""
        lines.append(("line", f"领防：{team}号码没读出"))
        add_defender_bio()
    else:
        who = d.get("display") or d["name"]
        lines.append(("line", f"领防：{who} #{d['number']}（画面，出手前贴防 {float(d['guard_seconds']):.1f} 秒）"))
        add_defender_bio()
        vs = d.get("vs_shooter")
        if vs:
            lines.append(("line", f"本场对位 {round(vs['seconds'] / 60)} 分钟，{shot['name']} {vs['fgm']}/{vs['fga']}、得 {vs['points']} 分"))
        ds = d.get("season")
        ranked = rank_rows(d.get("team_rank"), DEF_RANKS)
        lines.extend(ranked)
        if ds and not ranked:
            lines.append(("line", f"赛季场均 抢断 {ds['stl']} · 盖帽 {ds['blk']} · 防守篮板 {ds['dreb']}"))
    lines.extend(("line", text) for text in zone_lines(shot.get("seasonZone")))
    return lines


def _star_points(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _stars(draw, x: float, y: float, size: float, rating: float) -> None:
    """五颗星，金色填到 rating（半星画左半边）。"""
    from PIL import Image, ImageDraw

    for k in range(5):
        cx, cy, r = x + k * (size + 2) + size / 2, y + size / 2, size / 2
        draw.polygon(_star_points(cx, cy, r), fill=(88, 80, 70, 255))
        fill = min(max(rating - k, 0.0), 1.0)
        if fill <= 0:
            continue
        w = int(size) + 2
        star = Image.new("L", (w, w), 0)
        ImageDraw.Draw(star).polygon(_star_points(w / 2, w / 2, r), fill=255)
        if fill < 1:
            star.paste(0, (int(w * fill), 0, w, w))
        draw._image.paste(Image.new("RGBA", (w, w), GOLD), (int(cx - w / 2), int(cy - w / 2)), star)


def shooter_source(shot: dict) -> str:
    c = shot.get("shooterCheck") or {}
    if c.get("number") and c["number"] == c.get("official_number"):
        return "画面确认"
    if c.get("number"):
        return f"画面读到 #{c['number']}，以官方记录为准"
    return "官方记录"


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




def _cover(a: list[float], b: list[float]) -> float:
    """a 有多大比例被 b 盖住。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0) / max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))


def _overlaps(rect: tuple, placed: list[tuple]) -> bool:
    return any(rect[0] < r[2] and r[0] < rect[2] and rect[1] < r[3] and r[1] < rect[3] for r in placed)


def draw_lead_tag(draw, font, text: str, lead_box: list[float], scale: float, side: str | None,
                  placed: list[tuple], color, width: float = OUT_W) -> None:
    """领防标签贴在防守人躯干旁、背向投篮人的一侧。
    背向那一侧放不下就换到另一侧；两侧都被卡片挡住时仍贴在背向侧，保证定格里能看到领防名签。"""
    from pipeline.present.build import draw_tag

    tb = draw.textbbox((0, 0), text, font=font)
    w, h = tb[2] - tb[0] + 16, tb[3] - tb[1] + 10
    x1, _, x2, y2 = (v * scale for v in lead_box)
    y1 = lead_box[1] * scale
    y = y1 + (y2 - y1) * 0.45 - h / 2  # 躯干高度，不再贴在头肩
    away = side or "right"
    toward = "left" if away == "right" else "right"
    for candidate in (away, toward):
        x = x2 + 6 if candidate == "right" else x1 - w - 6
        rect = (x, y, x + w, y + h)
        if 0 <= x and x + w <= width and not _overlaps(rect, placed):
            placed.append(rect)
            draw_tag(draw, font, text, rect, color, edge_right=(candidate == "left"))
            return
    x = x2 + 6 if away == "right" else x1 - w - 6
    rect = (x, y, x + w, y + h)
    placed.append(rect)
    draw_tag(draw, font, text, rect, color, edge_right=(away == "left"))


def card_on_right(shooter: list[float], lead: list[float] | None, side: str | None, tag_w: float,
                  scale: float, card_w: float, width: float = OUT_W) -> bool:
    """卡片默认放投篮人对面；挡住领防人的标签就换边，换过去不挡投篮人和领防人才换。"""
    def span(right: bool):
        return (width - card_w - 24, width - 24) if right else (24, 24 + card_w)

    def blocks(right: bool) -> bool:
        lo, hi = span(right)
        boxes = [shooter] + ([lead] if lead else [])
        if any(b[0] * scale < hi and lo < b[2] * scale for b in boxes):
            return True
        if lead and side:
            x = lead[2] * scale + 6 if side == "right" else lead[0] * scale - tag_w - 6
            return x < hi and lo < x + tag_w
        return False

    first = (shooter[0] + shooter[2]) / 2 * scale < width / 2
    return first if not blocks(first) or blocks(not first) else not first


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


def draw_still(frame: Path, dest: Path, pack: dict, shot: dict, font_path: Path, others=()) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    base = Image.open(frame).convert("RGBA").resize((OUT_W, OUT_H))
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    big = ImageFont.truetype(str(font_path), 24)
    mid = ImageFont.truetype(str(font_path), 19)
    small = ImageFont.truetype(str(font_path), 16)
    tiny = ImageFont.truetype(str(font_path), 13)
    scale = OUT_W / float(pack.get("width") or OUT_W)

    from pipeline.present.build import draw_names

    box = shot["release"]["shooter_box"]
    card_w = 470
    lead = shot.get("leadBox")
    d = shot.get("defender") or {}
    label = f"领防 · {d.get('display') or d.get('name')} #{d.get('number')}" if d.get("name") else "领防"
    side = lead_side(box, lead)
    lead_w = draw.textlength(label, font=small) + 22 if lead else 0
    right = card_on_right(box, lead, side, lead_w, scale, card_w)
    card_x = OUT_W - card_w - 24 if right else 24
    tags = [(f"出手 · {shot['name']}", box, RED, None)]
    if lead:
        _ellipse(draw, lead, scale, GOLD, dashed=True)
    tags.extend(t for t in others if not any(_cover(t[1], b) > 0.2 for b in (box, lead) if b))
    _ellipse(draw, box, scale, RED, fill=(255, 43, 43, 80))
    placed = [(card_x, 0, card_x + card_w, OUT_H)]
    draw_names(draw, small, tags, scale, placed=placed, width=OUT_W)
    if lead:
        draw_lead_tag(draw, small, label, lead, scale, side, placed, GOLD, width=OUT_W)

    x0 = card_x
    y = 78
    rows = []
    for style, text, *extra in card_lines(pack, shot):
        font = {"head": big, "score": mid, "off": mid, "def": mid}.get(style, small)
        height = draw.textbbox((0, 0), text, font=font)[3] + (12 if style in ("off", "def") else 7)
        rows.append((style, text, font, height, extra[0] if extra else None))
    card_h = sum(r[3] for r in rows) + 24
    draw.rectangle((x0, y, x0 + card_w, y + card_h), fill=PANEL)
    edge = x0 + card_w - 5 if right else x0
    draw.rectangle((edge, y, edge + 5, y + card_h), fill=RED)
    ty = y + 12
    for style, text, font, height, rating in rows:
        tx = x0 + 18
        if style in ("off", "def"):
            ty += 5
            dot = RED if style == "off" else GOLD
            draw.ellipse((tx, ty + 6, tx + 10, ty + 16), fill=dot)
            tx += 18
        color = RED if style == "head" else CREAM if style in ("score", "off", "def") else MUTED
        draw.text((tx, ty), text, font=font, fill=color)
        if rating is not None:
            _stars(draw, x0 + card_w - 22 - 5 * 18, ty + 1, 16, rating)
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


def render_replay(clip: str, pack: dict, bed: Path, voice_pad: Path, dest: Path, work: Path, font_path: Path,
                  source: Path | None = None, others=()) -> Path | None:
    """定格帧从没烧字的原视频截（source），others 是这一刻除投篮人、领防人以外的人名标签。"""
    shot = pick(pack)
    if not shot:
        return None
    work.mkdir(parents=True, exist_ok=True)
    release = float(shot["release"]["t"])
    goal = goal_time(pack, shot)
    frame = work / f"{clip}-release.png"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{release:.3f}", "-i", str(source or bed),
        "-frames:v", "1", str(frame),
    ], check=True)
    still = draw_still(frame, work / f"{clip}-freeze.png", pack, shot, font_path, others)
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
