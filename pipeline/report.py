#!/usr/bin/env python3
"""清单 → 单文件 HTML 报告：本地视频 + 可点的时间线 + 场上阵容 + 出手卡（半场落点、xFG、官方视频）。

    python3 -m pipeline.report --clip exam        # 写 pipeline/events/out/exam.html

只用标准库，不需要服务；双击 HTML 就能看。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "pipeline" / "events" / "out"
sys.path.insert(0, str(ROOT / "lambdas"))
from common import TEAM_ZH, timeline_from_stream  # noqa: E402

COLORS = {"HOU": "#CE1141", "DAL": "#0064B1"}
PX = 8                  # 每英尺像素
BASKET_Y = 5.25         # 篮筐中心离底线（英尺）


def _clock(s) -> str:
    if s is None:
        return "?"
    s = float(s)
    return f"{int(s // 60)}:{int(s % 60):02d}" if s >= 60 else f"{s:.1f}"


def _court(shots: list[dict]) -> str:
    """半场，篮筐在上；x 为负画在左（投篮人面向篮筐的左）。"""
    w, h = 50 * PX, 34 * PX
    sx = lambda x: (25 + x) * PX
    sy = lambda y: (BASKET_Y + y) * PX
    corner_y = (23.75 ** 2 - 22 ** 2) ** 0.5
    parts = [
        f'<svg viewBox="0 0 {w} {h}" class="court">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#f4e4c9"/>',
        f'<rect x="{sx(-8)}" y="0" width="{16 * PX}" height="{19 * PX}" fill="#ead2ab" stroke="#8a6d45"/>',
        f'<circle cx="{sx(0)}" cy="{19 * PX}" r="{6 * PX}" fill="none" stroke="#8a6d45"/>',
        f'<line x1="{sx(-22)}" y1="0" x2="{sx(-22)}" y2="{sy(corner_y)}" stroke="#8a6d45"/>',
        f'<line x1="{sx(22)}" y1="0" x2="{sx(22)}" y2="{sy(corner_y)}" stroke="#8a6d45"/>',
        f'<path d="M {sx(-22)} {sy(corner_y)} A {23.75 * PX} {23.75 * PX} 0 0 0 {sx(22)} {sy(corner_y)}" '
        f'fill="none" stroke="#8a6d45"/>',
        f'<circle cx="{sx(0)}" cy="{sy(0)}" r="{0.75 * PX}" fill="none" stroke="#d35400" stroke-width="2"/>',
        f'<line x1="{sx(-3)}" y1="{4 * PX}" x2="{sx(3)}" y2="{4 * PX}" stroke="#333" stroke-width="3"/>',
        f'<text x="6" y="{h - 8}" class="hint">左</text><text x="{w - 18}" y="{h - 8}" class="hint">右</text>',
    ]
    for s in shots:
        x, y = s["x"], s["y"]
        color = COLORS.get(s["team"], "#333")
        if s["made"]:
            parts.append(f'<circle cx="{sx(x)}" cy="{sy(y)}" r="9" fill="{color}" stroke="#fff" stroke-width="2"/>')
        else:
            d = 7
            parts.append(f'<path d="M {sx(x) - d} {sy(y) - d} L {sx(x) + d} {sy(y) + d} M {sx(x) + d} {sy(y) - d} '
                         f'L {sx(x) - d} {sy(y) + d}" stroke="{color}" stroke-width="4"/>')
    parts.append("</svg>")
    return "".join(parts)


def _shots(stream: dict) -> list[dict]:
    out = []
    for poss in stream["possessions"]:
        for ev in poss["events"]:
            off, geom = ev.get("official") or {}, ev.get("geometry") or {}
            if ev["type"] != "shot" or geom.get("x_ft") is None:
                continue
            out.append({"t": ev["t"], "team": ev["actor"].get("team", ""), "name": ev["actor"].get("name") or "",
                        "x": geom["x_ft"], "y": geom["y_ft"], "zone": geom.get("shot_zone", ""),
                        "made": off.get("made"), "value": off.get("value"), "xfg": off.get("xfg"),
                        "desc": off.get("description", ""), "video": off.get("video_url"),
                        "thumb": off.get("thumbnail"), "distance": off.get("distance_ft")})
    return out


def render(stream: dict, html_path: Path) -> str:
    e = html.escape
    video = stream.get("video") or {}
    src = video.get("source") or ""
    video_rel = os.path.relpath(ROOT / src, html_path.parent) if src else ""
    official = stream.get("official") or {}
    rows = timeline_from_stream(stream)
    shots = _shots(stream)

    poss_html = []
    for poss in stream["possessions"]:
        team = poss.get("offense_team") or ""
        po = poss.get("official") or {}
        score = ""
        if po.get("score_start") and po.get("score_end"):
            a, b = po["score_start"], po["score_end"]
            score = " · ".join(f"{k} {a[k]}→{b[k]}" if a[k] != b[k] else f"{k} {a[k]}" for k in a)
        items = "".join(
            f'<li data-t="{r["t"]}" class="ev {r["eventType"]} src-{r["source"]}">'
            f'<span class="tc">{r["t"]:.1f}s</span>{e(r["label"])}'
            f'<span class="conf">{r["confidence"]:.2f}</span></li>'
            for r in rows if poss["t_start"] - 1e-6 <= r["t"] <= poss["t_end"] + 1e-6)
        clock = ""
        if po.get("clock_start_s") is not None:
            clock = f'{_clock(po["clock_start_s"])} → {_clock(po.get("clock_end_s"))}'
        poss_html.append(
            f'<section class="poss" style="border-color:{COLORS.get(team, "#999")}">'
            f'<h3 data-t="{poss["t_start"]}"><b style="color:{COLORS.get(team, "#333")}">{e(poss["possession_id"])} '
            f'{e(TEAM_ZH.get(team, team))}进攻</b> <span>{poss["t_start"]:.1f}–{poss["t_end"]:.1f}s</span>'
            f'<span>{e(clock)}</span><span>{e(score)}</span></h3><ol>{items}</ol></section>')

    lineup = "".join(
        f'<div class="team" style="border-color:{COLORS.get(t, "#999")}"><b>{e(TEAM_ZH.get(t, t))}</b>'
        + "".join(f'<span>#{e(p.get("jersey", ""))} {e(p.get("full_name") or p.get("name", ""))}</span>' for p in ps)
        + "</div>"
        for t, ps in (official.get("on_court") or {}).items())

    cards = []
    for s in shots:
        result = {True: "命中", False: "不中"}.get(s["made"], "出手")
        xfg = (f'<div class="xfg"><div style="width:{float(s["xfg"]) * 100:.0f}%"></div></div>'
               f'<small>xFG {float(s["xfg"]):.0%}（同类出手的联盟预期命中率）</small>') if s["xfg"] is not None else ""
        clip = (f'<video controls preload="none" poster="{e(s["thumb"] or "")}" src="{e(s["video"])}"></video>'
                f'<small>官方这一球的录像</small>') if s["video"] else ""
        dist = f' · {s["distance"]:.0f} 英尺' if s["distance"] else ""
        cards.append(f'<div class="card" data-t="{s["t"]}"><h4>{e(s["name"])} {s["value"] or ""} 分{result}'
                     f'</h4><p>{e(s["zone"])}{dist} · {s["t"]:.1f}s</p><p class="desc">{e(s["desc"])}</p>{xfg}{clip}</div>')

    conflicts = official.get("track_conflicts") or []
    conflict_html = ("<p class='warn'>号码与官方不一致：" + "；".join(
        e(f'{c["event_id"]} {c["cv"]} ↔ {c["official"]}') for c in conflicts) + "</p>") if conflicts else ""
    head = [f'片段 {e(stream.get("job_id") or "")}']
    if official.get("game_id"):
        head.append(f'官方比赛 {e(official["game_id"])}')
    if official.get("period"):
        win = official.get("clock_window_s") or [None, None]
        head.append(f'第 {official["period"]} 节 {_clock(win[0])} → {_clock(win[1])}')

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>DeepCourt · {e(stream.get("job_id") or "")}</title>
<style>
body{{font:14px/1.5 -apple-system,"PingFang SC",sans-serif;margin:0;background:#111;color:#eee}}
header{{padding:12px 20px;background:#1c1c1c;display:flex;gap:18px;align-items:baseline}}
header h1{{font-size:18px;margin:0}} header span{{color:#aaa}}
main{{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(320px,1fr);gap:16px;padding:16px 20px}}
video{{width:100%;background:#000;border-radius:6px}}
.poss{{border-left:4px solid;padding:4px 10px;margin:0 0 12px;background:#1a1a1a;border-radius:4px}}
.poss h3{{font-size:14px;margin:4px 0;display:flex;gap:10px;flex-wrap:wrap;cursor:pointer}} .poss h3 span{{color:#aaa;font-weight:normal}}
ol{{list-style:none;padding:0;margin:0}} .ev{{padding:3px 6px;border-radius:3px;cursor:pointer;display:flex;gap:8px}}
.ev:hover,.ev.now{{background:#333}} .tc{{color:#888;width:46px;flex:none}} .conf{{margin-left:auto;color:#666}}
.ev.shot{{font-weight:600}} .src-official::after{{content:"官方";font-size:11px;color:#f1c40f;margin-left:6px}}
.lineup{{display:flex;gap:10px;margin:12px 0}} .team{{flex:1;border-top:3px solid;background:#1a1a1a;padding:6px 10px;border-radius:4px}}
.team span{{display:block;color:#ccc}} .court{{width:100%;max-width:420px;border-radius:6px;margin-top:8px}}
.hint{{fill:#8a6d45;font-size:14px}} .cards{{display:grid;gap:10px;margin-top:10px}}
.card{{background:#1a1a1a;border-radius:6px;padding:10px;cursor:pointer}} .card h4{{margin:0}} .card p{{margin:2px 0;color:#bbb}}
.desc{{font-size:12px;color:#888!important}} .xfg{{height:8px;background:#333;border-radius:4px;margin:6px 0 2px}}
.xfg div{{height:100%;background:#2ecc71;border-radius:4px}} small{{color:#888;display:block}}
.warn{{color:#f39c12}} h2{{font-size:15px;margin:14px 0 6px;color:#ccc}}
@media (max-width:900px){{main{{grid-template-columns:1fr}} header{{flex-direction:column;gap:2px}} .lineup{{flex-direction:column}}}}
</style></head><body>
<header><h1>DeepCourt 回合清单</h1><span>{" · ".join(head)}</span></header>
<main><div>
<video id="v" controls src="{e(video_rel)}"></video>
<h2>场上阵容（官方换人记录）</h2><div class="lineup">{lineup or "<span>没有官方数据</span>"}</div>
<h2>出手</h2>{_court(shots) if shots else ""}<div class="cards">{"".join(cards)}</div>
</div><div>{conflict_html}{"".join(poss_html)}
<small>点任意事件跳到视频那一刻。左右按投篮人面向篮筐。置信度 0.95 以上的出手人来自官方逐回合。</small></div></main>
<script>
const v=document.getElementById('v');
document.querySelectorAll('[data-t]').forEach(el=>el.addEventListener('click',()=>{{v.currentTime=Math.max(0,+el.dataset.t-1);v.play()}}));
const evs=[...document.querySelectorAll('li.ev')];
v.addEventListener('timeupdate',()=>{{let cur=null;for(const el of evs){{if(+el.dataset.t<=v.currentTime+0.05)cur=el}}
evs.forEach(el=>el.classList.toggle('now',el===cur))}});
</script></body></html>
"""


def write(stream_path: Path) -> Path:
    stream = json.loads(stream_path.read_text(encoding="utf-8"))
    html_path = stream_path.with_suffix(".html")
    html_path.write_text(render(stream, html_path), encoding="utf-8")
    return html_path


def main() -> None:
    ap = argparse.ArgumentParser(description="清单 → 单文件 HTML 报告")
    ap.add_argument("--clip", required=True)
    args = ap.parse_args()
    print(write(OUT / f"{args.clip}.json"))


if __name__ == "__main__":
    main()
