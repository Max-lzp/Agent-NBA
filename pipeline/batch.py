#!/usr/bin/env python3
"""多段视频批量调试：每段跑完整条管道、出成片，再写一张认人报告。

    python3 -m pipeline.batch --official 6                         # 官方录像里挑 6 段进球，带标准答案
    python3 -m pipeline.batch --official-ids 0022500701_21 0022500491_232
    python3 -m pipeline.batch --video a.mp4 b.mp4 --game-id 0022500701 --away DAL --home HOU
    python3 -m pipeline.batch --official 6 --run r1                # 同名批次再跑：视觉结果在就跳过追踪

产物都在 pipeline/batch/out/{run}/：每段一个目录（成片、定格版、四帧截图、日志），
外加 report.html 和 summary.json。不写 web/assets、web/replay/clips.js；
clips.json 里为批量登记的片段跑完删掉。
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.enrich.official import DATA  # noqa: E402

CLIPS_FILE = ROOT / "pipeline" / "vision" / "clips.json"
RESULTS = ROOT / "pipeline" / "vision" / "results" / "local"
EVENTS = ROOT / "pipeline" / "events" / "out"
WEB_ASSETS = ROOT / "web" / "assets"
BATCH = ROOT / "pipeline" / "batch" / "out"
OFFICIAL = DATA / "2025-26" / "clips"
SEASON_GAMES = DATA / "2025-26" / "games"
PREFIX = "b_"
SHEET_AT = (0.15, 0.4, 0.6, 0.85)


def _teams(game_id: str) -> tuple[str, str]:
    doc = json.loads((SEASON_GAMES / f"{game_id}_boxscore.json").read_text(encoding="utf-8"))
    game = doc.get("game") or doc
    return game["awayTeam"]["teamTricode"], game["homeTeam"]["teamTricode"]


def _made(game_id: str) -> dict[int, dict]:
    doc = json.loads((SEASON_GAMES / f"{game_id}_playbyplay.json").read_text(encoding="utf-8"))
    return {a["actionNumber"]: a for a in (doc.get("game") or doc)["actions"] if a.get("shotResult") == "Made"}


def official_jobs(ids: list[str], count: int) -> list[dict]:
    """官方事件录像：带投篮人和号码，当标准答案。不给 id 就按场轮流挑进球。"""
    meta = json.loads((OFFICIAL / "_clip_events.json").read_text(encoding="utf-8"))
    if not ids:
        by_game: dict[str, list[str]] = {}
        for key in sorted(meta, key=lambda k: (meta[k]["gameId"], meta[k]["eventNum"])):
            gid = meta[key]["gameId"]
            if meta[key]["eventNum"] in _made(gid) and (OFFICIAL / f"{key}.mp4").exists():
                by_game.setdefault(gid, []).append(key)
        lists = list(by_game.values())
        step = [max(1, len(v) // max(1, -(-count // len(lists)))) for v in lists]
        k = 0
        while len(ids) < count and any(lists):
            for i, keys in enumerate(lists):
                pick = k * step[i]
                if pick < len(keys) and len(ids) < count:
                    ids.append(keys[pick])
            k += 1
    jobs = []
    for key in ids:
        m = meta[key]
        away, home = _teams(m["gameId"])
        jobs.append({
            "clip": PREFIX + key, "video": OFFICIAL / f"{key}.mp4", "game_id": m["gameId"], "away": away, "home": home,
            "truth": {"shooter": m["shooter"], "shooter_id": m["shooterId"], "number": m["shooterNumber"],
                      "team": m["shooterTeam"], "event_num": m["eventNum"]},
        })
    return jobs


def video_jobs(videos: list[str], game_id: str, away: str, home: str) -> list[dict]:
    jobs = []
    for v in videos:
        path = Path(v).expanduser().resolve()
        stem = "".join(c if c.isalnum() else "_" for c in path.stem).strip("_").lower()
        jobs.append({"clip": PREFIX + stem, "video": path, "game_id": game_id, "away": away, "home": home, "truth": None})
    return jobs


def run_pipeline(job: dict, log: Path) -> tuple[bool, float]:
    vision_done = (RESULTS / f"{job['clip']}.json").exists()
    cmd = [sys.executable, "-m", "pipeline.run_clip", "--video", str(job["video"]), "--clip", job["clip"]]
    for flag in ("game_id", "away", "home", "white"):
        if job.get(flag):
            cmd += [f"--{flag.replace('_', '-')}", job[flag]]
    if vision_done:
        cmd.append("--skip-vision")
    t0 = time.time()
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"$ {' '.join(cmd)}\n")
        fh.flush()
        ok = subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT).returncode == 0
    return ok, time.time() - t0


def render(job: dict, out: Path, log: Path) -> dict:
    """成片和定格版写到批次目录，不留在 web/assets。"""
    from pipeline.present import build

    pack = build.build_pack(job["clip"])
    t0 = time.time()
    with log.open("a", encoding="utf-8") as fh:
        fh.write("$ present.render_video\n")
    build.render_video(job["clip"], pack)
    for suffix in ("broadcast", "replay"):
        src = WEB_ASSETS / f"{job['clip']}-{suffix}.mp4"
        if src.exists():
            shutil.move(str(src), out / f"{suffix}.mp4")
    pack["_render_s"] = time.time() - t0
    return pack


def contact_sheet(out: Path, duration: float) -> Path | None:
    video = out / "broadcast.mp4"
    if not video.exists():
        return None
    tiles = []
    for i, frac in enumerate(SHEET_AT):
        tile = out / f"_t{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{duration * frac:.2f}",
                        "-i", str(video), "-frames:v", "1", "-vf", "scale=640:-1", str(tile)], check=False)
        if tile.exists():
            tiles.append(tile)
    replay = out / "replay.mp4"
    if replay.exists():
        tile = out / "_freeze.jpg"
        freeze_at = _freeze_at(out)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{freeze_at:.2f}", "-i", str(replay),
                        "-frames:v", "1", "-vf", "scale=640:-1", str(tile)], check=False)
        if tile.exists():
            freeze = out / "freeze.jpg"
            shutil.copy(tile, freeze)
    if len(tiles) < 4:
        return None
    sheet = out / "sheet.jpg"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *sum((["-i", str(t)] for t in tiles), []),
                    "-filter_complex", "[0][1]hstack[a];[2][3]hstack[b];[a][b]vstack", str(sheet)], check=False)
    for t in tiles + [out / "_freeze.jpg"]:
        t.unlink(missing_ok=True)
    return sheet if sheet.exists() else None


def _freeze_at(out: Path) -> float:
    meta = out / "metrics.json"
    if meta.exists():
        return float(json.loads(meta.read_text(encoding="utf-8")).get("freeze_at") or 0)
    return 0.0


def metrics(job: dict, pack: dict | None) -> dict:
    """认人：投篮人号码读对没有，领防人认出没有，多少帧标上了人名，记分牌读全几帧。"""
    clip = job["clip"]
    m: dict = {"clip": clip, "video": str(job["video"]), "truth": job.get("truth")}
    vision_path = RESULTS / f"{clip}.json"
    if vision_path.exists():
        v = json.loads(vision_path.read_text(encoding="utf-8"))
        players = [p for p in v.get("players") or [] if p.get("role") == "player"]
        m["tracks"] = len(players)
        m["named_tracks"] = sum(1 for p in players if p.get("name"))
        m["named_players"] = v.get("named_players") or []
    board_path = RESULTS / f"{clip}.scoreboard.json"
    if board_path.exists():
        reads = json.loads(board_path.read_text(encoding="utf-8")).get("reads") or []
        m["scoreboard_full"] = f"{sum(1 for r in reads if r.get('scores') and r.get('clock'))}/{len(reads)}"
    rel_path = RESULTS / f"{clip}.release.json"
    shots = json.loads(rel_path.read_text(encoding="utf-8")).get("shots") or [] if rel_path.exists() else []
    events_path = EVENTS / f"{clip}.json"
    official_shots = []
    if events_path.exists():
        doc = json.loads(events_path.read_text(encoding="utf-8"))
        official_shots = [e for p in doc.get("possessions") or [] for e in p.get("events") or []
                          if e.get("type") == "shot" and e.get("official")]
    truth = job.get("truth")
    target = next((e for e in official_shots if truth and e["official"].get("person_id") == truth["shooter_id"]),
                  official_shots[0] if official_shots else None)
    m["official_matched"] = bool(target) if truth is None else bool(target and target["official"].get("person_id") == truth["shooter_id"])
    rel = next((s for s in shots if target and s.get("event_id") == target["event_id"]), shots[0] if shots else {})
    lead = rel.get("lead") or {}
    sh, df = lead.get("shooter") or {}, lead.get("defender") or {}
    m["release"] = rel.get("t") is not None
    m["shooter_read"] = sh.get("number")
    m["shooter_votes"] = f"{sh.get('votes', 0)}/{sh.get('reads', 0)}"
    if truth:
        m["shooter_ok"] = None if not sh.get("number") else sh.get("number") == truth["number"]
    m["lead_status"] = df.get("status") or lead.get("status")
    m["lead_number"] = df.get("number")
    m["lead_name"] = df.get("name")
    m["lead_votes"] = f"{df.get('votes', 0)}/{df.get('reads', 0)}" if df else ""
    m["lead_guard_s"] = df.get("guard_seconds")
    m["lead_box_at_release"] = bool(df.get("box_at_release"))
    if pack:
        from pipeline.present import build
        from pipeline.present.freeze import goal_time, pick

        duration = float(pack.get("duration") or 0)
        steps = [i / 10 for i in range(int(duration * 10))]
        counts = [len(build.name_tags_at(pack, t)) for t in steps]
        m["name_frames"] = round(sum(1 for c in counts if c) / max(1, len(counts)), 3)
        m["names_per_frame"] = round(sum(counts) / max(1, len(counts)), 2)
        m["duration"] = round(duration, 2)
        shot = pick(pack)
        m["freeze_at"] = round(goal_time(pack, shot) + 2.0, 2) if shot else 0
        m["render_s"] = round(pack.get("_render_s") or 0)
    return m


def _cell(v) -> str:
    if v is True:
        return '<td class="ok">✓</td>'
    if v is False:
        return '<td class="bad">✗</td>'
    return f"<td>{html.escape('' if v is None else str(v))}</td>"


def write_report(run_dir: Path, rows: list[dict]) -> Path:
    head = ("片段", "官方对上", "出手帧", "投篮人读到", "读对", "票数", "领防", "领防号码", "领防票数",
            "贴防秒", "人名帧占比", "每帧人名", "记分牌读全", "用时")
    body = []
    for m in rows:
        truth = m.get("truth") or {}
        name = f"{m['clip']}<br><small>{html.escape(truth.get('shooter', ''))} #{truth.get('number', '')}</small>"
        lead = f"{m.get('lead_status') or ''} {m.get('lead_name') or ''}".strip()
        cells = [f"<td>{name}</td>", _cell(m.get("official_matched")), _cell(m.get("release")),
                 _cell(m.get("shooter_read")), _cell(m.get("shooter_ok")), _cell(m.get("shooter_votes")),
                 _cell(lead), _cell(m.get("lead_number")), _cell(m.get("lead_votes")), _cell(m.get("lead_guard_s")),
                 _cell(m.get("name_frames")), _cell(m.get("names_per_frame")), _cell(m.get("scoreboard_full")),
                 _cell(f"{m.get('pipeline_s', 0):.0f}+{m.get('render_s', 0)}s")]
        body.append("<tr>" + "".join(cells) + "</tr>")
        clip_dir = m["clip"]
        imgs = "".join(f'<img src="{clip_dir}/{n}" loading="lazy">' for n in ("sheet.jpg", "freeze.jpg")
                       if (run_dir / clip_dir / n).exists())
        links = " · ".join(f'<a href="{clip_dir}/{n}">{n}</a>' for n in ("broadcast.mp4", "replay.mp4", "run.log")
                           if (run_dir / clip_dir / n).exists())
        err = f'<p class="bad">{html.escape(m["error"])}</p>' if m.get("error") else ""
        body.append(f'<tr class="media"><td colspan="{len(head)}">{err}{imgs}<p>{links}</p></td></tr>')
    done = [m for m in rows if m.get("truth")]
    read = [m for m in done if m.get("shooter_read")]
    right = [m for m in read if m.get("shooter_ok")]
    leads = [m for m in rows if m.get("lead_name")]
    summary = (f"{len(rows)} 段 · 官方对上 {sum(1 for m in rows if m.get('official_matched'))} · "
               f"投篮人号码读到 {len(read)}/{len(done)}，读对 {len(right)} · 领防认出名字 {len(leads)}/{len(rows)}")
    page = f"""<!doctype html><meta charset="utf-8"><title>批量调试 {run_dir.name}</title>
<style>body{{font:14px -apple-system,"PingFang SC",sans-serif;margin:24px;background:#14110e;color:#f3ede2}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #3a332b;padding:6px 8px;text-align:left;vertical-align:top}}
th{{color:#b8ac9c;font-weight:500}}.ok{{color:#5fd38d}}.bad{{color:#ff6b6b}}small{{color:#b8ac9c}}
tr.media img{{width:49%;margin:2px}}a{{color:#f5c542}}</style>
<h1>批量调试 · {html.escape(run_dir.name)}</h1><p>{html.escape(summary)}</p>
<table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{''.join(body)}</table>"""
    dest = run_dir / "report.html"
    dest.write_text(page, encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description="多段视频批量调试")
    ap.add_argument("--official", type=int, default=0, help="从官方事件录像里按场轮流挑这么多段进球")
    ap.add_argument("--official-ids", nargs="*", default=[], help="指定官方录像 id，例如 0022500701_21")
    ap.add_argument("--video", nargs="*", default=[], help="任意视频路径")
    ap.add_argument("--game-id", default="", help="--video 用：官方 gameId")
    ap.add_argument("--away", default="", help="--video 用")
    ap.add_argument("--home", default="", help="--video 用")
    ap.add_argument("--run", default=time.strftime("%m%d-%H%M"), help="批次名；同名再跑会跳过已有的视觉结果")
    ap.add_argument("--no-video", action="store_true", help="只跑管道和报告，不出成片")
    args = ap.parse_args()

    jobs = official_jobs(list(args.official_ids), args.official) if (args.official or args.official_ids) else []
    jobs += video_jobs(args.video, args.game_id, args.away, args.home)
    if not jobs:
        sys.exit("给 --official N、--official-ids 或 --video")
    run_dir = BATCH / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    before = set(json.loads(CLIPS_FILE.read_text(encoding="utf-8")))
    rows = []
    try:
        for n, job in enumerate(jobs, 1):
            out = run_dir / job["clip"]
            out.mkdir(exist_ok=True)
            log = out / "run.log"
            print(f"[{n}/{len(jobs)}] {job['clip']} …", flush=True)
            ok, took = run_pipeline(job, log)
            pack, error = None, ""
            if not ok:
                error = "管道失败，见 run.log"
            elif not args.no_video:
                try:
                    pack = render(job, out, log)
                except (SystemExit, Exception) as exc:  # noqa: BLE001
                    error = f"出片失败：{exc}"
            if pack is None and ok:
                try:
                    from pipeline.present import build
                    pack = build.build_pack(job["clip"])
                except (SystemExit, Exception) as exc:  # noqa: BLE001
                    error = error or f"回放数据失败：{exc}"
            m = metrics(job, pack)
            m["pipeline_s"] = took
            m["error"] = error
            (out / "metrics.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
            contact_sheet(out, float(m.get("duration") or 10))
            rows.append(m)
            print(f"    投篮人 {m.get('shooter_read')}（{m.get('shooter_ok')}） 领防 {m.get('lead_status')} "
                  f"{m.get('lead_name') or ''} · 人名帧 {m.get('name_frames')} · {took:.0f}s {error}", flush=True)
            (run_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            write_report(run_dir, rows)
    finally:
        clips = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))
        extra = [k for k in clips if k.startswith(PREFIX) and k not in before]
        if extra:
            for k in extra:
                clips.pop(k)
            CLIPS_FILE.write_text(json.dumps(clips, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[报告] {write_report(run_dir, rows)}")


if __name__ == "__main__":
    main()
