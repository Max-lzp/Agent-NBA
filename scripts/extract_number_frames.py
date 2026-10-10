#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从已下载的官方事件录像截帧，生成「带标准答案的球衣号码数据集」。

输入: data/2025-26/clips/{gameId}_{eventNum}.mp4（fetch_clips.py 产物）
输出:
  data/2025-26/frames/{gameId}_{eventNum}/f_{idx:04d}.jpg   每 15 帧抽 1 张（≈2fps）
  data/2025-26/frames/_labels.jsonl                        每帧一行标准答案
  data/2025-26/frames/_dataset_summary.json                统计

标准答案来源（全是官方记录）：
  - 投篮人/节次/比赛钟：shotqualityshotlog（与 mp4 文件名同 key）
  - 背号：CDN boxscore_{gid}.json 的 homeTeam/awayTeam players[].jerseyNum

帧时间：t = idx / FPS_OUT 秒（剪辑起点 ≈ 投篮前 5-7 秒，供下游对齐记分牌）。
注意：官方录像是 60fps（不是转播常见的 29.97），抽帧间隔按 60fps 算。
断点续跑：已存在且帧数吻合的目录直接跳过。
"""
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
DATA = WORKSPACE / "data" / "2025-26"
CLIPS = DATA / "clips"
FRAMES = DATA / "frames"
EVERY_N = 30          # 每 30 帧抽 1（官方录像是 60fps → 输出 2fps）
FPS_OUT = 60 / EVERY_N
JPEG_Q = "3"          # 高质量


def build_roster():
    """playerId -> {name, number, team}，从四场 boxscore（不含裁判）"""
    roster = {}
    conflicts = []
    for f in sorted(DATA.glob("games/*_boxscore.json")):
        d = json.load(open(f))
        for side in ("homeTeam", "awayTeam"):
            team = d["game"][side]
            tri = team.get("teamTricode", "?")
            for p in team.get("players", []):
                pid = p["personId"]
                rec = {"name": p.get("name"), "number": p.get("jerseyNum"), "team": tri}
                if pid in roster and roster[pid]["number"] != rec["number"]:
                    conflicts.append((pid, roster[pid], rec))
                roster.setdefault(pid, rec)
    return roster, conflicts


def load_truth():
    """(gameId,eventNum) -> 投篮记录 + 背号"""
    roster, conflicts = build_roster()
    if conflicts:
        print("警告: 背号冲突", conflicts, file=sys.stderr)
    truth, missing = {}, []
    for f in sorted(DATA.glob("games/*_shotqualityshotlog.json")):
        d = json.load(open(f))
        for s in d.get("shots", []):
            key = (str(d.get("parameters", {}).get("GameID") or f.name.split("_")[0]), s["eventNum"])
            p = roster.get(s["playerId"])
            if not p or not p.get("number"):
                missing.append((key, s["playerName"]))
                continue
            truth[key] = {
                "gameId": key[0], "eventNum": key[1],
                "period": s.get("period"), "gameClock": s.get("gameClock"),
                "playerId": s["playerId"], "playerName": s["playerName"],
                "number": p["number"], "team": p["team"],
                "eventType": s.get("eventType"), "success": s.get("success"),
                "durationMs": s.get("largeDuration"),
            }
    return truth, missing


def process(clip: Path, meta):
    """截帧一个剪辑；返回 (帧数, 状态)"""
    outdir = FRAMES / clip.stem
    expect = max(1, int(((meta.get("durationMs") or 10000) / 1000) * FPS_OUT))
    done_marker = outdir / "_done"
    if done_marker.exists():
        n = len(list(outdir.glob("f_*.jpg")))
        if abs(n - expect) <= 2:
            return n, "exists"
    outdir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip),
         "-vf", f"select='not(mod(n\\,{EVERY_N}))'",
         "-fps_mode", "vfr", "-q:v", JPEG_Q,
         str(outdir / "f_%04d.jpg"), "-y"],
        capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])
    n = len(list(outdir.glob("f_*.jpg")))
    if abs(n - expect) > max(2, expect * 0.3):
        raise RuntimeError(f"帧数异常 {n} vs 预期~{expect}")
    done_marker.write_text(str(n))
    return n, "ok"


def main():
    only = sys.argv[1:]  # 可传 clips 子集名，如 0022500160_163
    truth, missing = load_truth()
    if missing:
        print("警告: 以下投篮人查不到背号", missing, file=sys.stderr)
    clips = sorted(CLIPS.glob("*.mp4"))
    if only:
        want = set(only)
        clips = [c for c in clips if c.stem in want]
    print(f"剪辑 {len(clips)} 个，FPS_OUT≈{FPS_OUT:.2f}", flush=True)
    FRAMES.mkdir(parents=True, exist_ok=True)

    labels, results = [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {}
        for c in clips:
            meta = truth.get((c.stem.rsplit("_", 1)[0], int(c.stem.rsplit("_", 1)[1])))
            if not meta:
                results.append((c.stem, 0, "no-truth"))
                continue
            futs[ex.submit(process, c, meta)] = (c, meta)
        for i, fut in enumerate(as_completed(futs), 1):
            c, meta = futs[fut]
            try:
                n, st = fut.result()
                results.append((c.stem, n, st))
                for k in range(1, n + 1):
                    labels.append({
                        "file": f"data/2025-26/frames/{c.stem}/f_{k:04d}.jpg",
                        "clip": c.stem, "frameIdx": k, "tSec": round(k / FPS_OUT, 2),
                        "period": meta["period"], "gameClock": meta["gameClock"],
                        "shooterId": meta["playerId"], "shooter": meta["playerName"],
                        "number": meta["number"], "team": meta["team"],
                        "eventType": meta["eventType"], "success": meta["success"],
                    })
            except Exception as e:
                results.append((c.stem, 0, f"fail:{e}"))
            if i % 25 == 0:
                print(f"[{i}/{len(futs)}] {time.time()-t0:.0f}s", flush=True)

    labels.sort(key=lambda r: (r["clip"], r["frameIdx"]))
    with open(FRAMES / "_labels.jsonl", "w") as w:
        for r in labels:
            w.write(json.dumps(r, ensure_ascii=False) + "\n")
    ok = sum(1 for _, _, s in results if s in ("ok", "exists"))
    fail = [r for r in results if r[2].startswith("fail") or r[2] == "no-truth"]
    summary = {
        "clips_total": len(clips), "clips_ok": ok, "problems": fail,
        "frames_total": len(labels),
        "fps_out": round(FPS_OUT, 3), "every_n_frames": EVERY_N,
        "per_player": {},
    }
    for r in labels:
        k = f"{r['shooter']}#{r['number']}({r['team']})"
        summary["per_player"][k] = summary["per_player"].get(k, 0) + 1
    json.dump(summary, open(FRAMES / "_dataset_summary.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"完成 {ok}/{len(clips)}，帧总数 {len(labels)}，问题 {len(fail)}，用时 {time.time()-t0:.0f}s")
    if fail:
        print(fail[:10])
        sys.exit(2)


if __name__ == "__main__":
    main()
