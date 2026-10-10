#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NBA 官方事件录像批量下载器（shotqualityshotlog 里的 mp4 直链）

用法：
  python3 scripts/fetch_clips.py                 # 抓四场「最后 60 秒」投篮（默认）
  python3 scripts/fetch_clips.py --all           # 抓四场全部投篮
  python3 scripts/fetch_clips.py --per-game 50   # 每场抓前 50 个
  python3 scripts/fetch_clips.py --game 0022500160 --all

输出：
  data/2025-26/clips/{gameId}_{eventNum}.mp4
  data/2025-26/clips/_manifest_<筛选模式>.json   # 每条含官方节次/比赛钟/投篮人/URL/落盘结果

## 关键经验（2026-10-10 验证，别删）

1. **必须带全套 Chrome 视频元素请求头**，否则 Akamai 返回 200 + 15 秒
   "VIDEO NOT AVAILABLE" 占位视频（31,580,089 字节，md5 2dd8e05a…），
   不报错、与真视频同 URL——唯一识别方法是 ffprobe 时长对不上官方记录。
   已验证「只加个别几个头」（Range / Referer / Sec-Fetch-Dest）都不够，
   必须用下面的完整组合（HEADERS 常量）。
2. **真实** 1280x720 约 5.7Mbps（12 秒 ≈ 8.6MB）；占位恒为 31580089 字节。
   脚本下载后自动 ffprobe 校验时长（容差 0.6s），不过自动重下，占位永不落盘。
3. gameClock 格式是 M:SS（分钟不补零，如 "0:46"），筛「小于 1:00」要用分钟==0，
   别用前缀 "00:"。
4. 三个清晰度 URL（1280x720/960x540/320x180）在裸请求下都返回同一份占位视频；
   带请求头后各自是真实不同文件。直接用 largeUrl。
5. 日志里 largeHls（.m3u8）裸请求返回的也是 mp4 占位，别当 HLS 用 ffmpeg 拉。
6. nba.com 官网的 stats/events 页播放器（bitmovin）能正常播这些 mp4——
   说明链接本身没过期，只是 CDN 对非浏览器请求做了「软拦截」。
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]  # team-repo/scripts -> 工作区根
DATA = WORKSPACE / "data" / "2025-26"
LOGS = sorted(DATA.glob("games/*_shotqualityshotlog.json"))
CLIPS = DATA / "clips"

# 完整 Chrome 视频元素请求头（缺一不可，见文件头经验 1）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_26_6) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "sec-ch-ua": '"Chromium";v="150", "Not/A Brand";v="8"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
    "Referer": "https://www.nba.com/game/{gid}/play-by-play",
}
PLACEHOLDER_BYTES = 31580089  # 占位视频特征（经验 2）
TOL = 0.6  # 时长校验容差（秒）


def parse_clock(gc: str):
    """'M:SS' -> 剩余秒数；解析失败返回 None"""
    try:
        m, s = gc.split(":")
        return int(m) * 60 + int(s)
    except Exception:
        return None


def probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except Exception:
        return None


def collect_shots(game_id: str | None, mode: str, per_game: int):
    jobs = []
    for f in LOGS:
        d = json.load(open(f))
        gid = d.get("parameters", {}).get("GameID") or f.name.split("_")[0]
        if game_id and gid != game_id:
            continue
        shots = d.get("shots", [])
        if mode == "last60":
            picked = [s for s in shots
                      if parse_clock(s.get("gameClock", "")) is not None
                      and parse_clock(s.get("gameClock", "")) < 60]
        elif mode == "all":
            picked = list(shots)
        else:  # first-n per game
            picked = shots[:per_game]
        for s in picked:
            jobs.append({
                "gameId": gid,
                "eventNum": s.get("eventNum"),
                "period": s.get("period"),
                "gameClock": s.get("gameClock"),
                "clockSec": parse_clock(s.get("gameClock", "")),
                "playerId": s.get("playerId"),
                "playerName": s.get("playerName"),
                "teamAbbreviation": s.get("teamAbbreviation"),
                "eventType": s.get("eventType"),
                "success": s.get("success"),
                "durationMs": s.get("largeDuration"),
                "url": s.get("largeUrl") or s.get("mediumUrl") or s.get("smallUrl"),
                "shotUtcTime": s.get("shotUtcTime"),
            })
    return jobs


def fetch(url: str, gid: str, out: Path):
    """带全套头下载到 .part，成功返回字节数"""
    part = out.with_suffix(".part")
    headers = dict(HEADERS)
    headers["Referer"] = HEADERS["Referer"].format(gid=gid)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as r, open(part, "wb") as w:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            w.write(chunk)
    return part


def download(job):
    out = CLIPS / f"{job['gameId']}_{job['eventNum']}.mp4"
    rec = dict(job)
    rec["file"] = str(out.relative_to(WORKSPACE))
    expect = (job.get("durationMs") or 0) / 1000.0

    def valid(p: Path) -> bool:
        if not p.exists() or p.stat().st_size < 100_000:
            return False
        if p.stat().st_size == PLACEHOLDER_BYTES:  # 占位特征，快速判死
            return False
        if expect > 0:
            dur = probe_duration(p)
            rec["probeDuration"] = dur
            if dur is None or abs(dur - expect) > TOL:
                return False
        return True

    if valid(out):
        rec["status"] = "exists"
        rec["bytes"] = out.stat().st_size
        return rec

    url = job["url"]
    if not url:
        rec["status"] = "no-url"
        return rec
    last_err = None
    for attempt in range(3):
        try:
            part = fetch(url, job["gameId"], out)
            if expect > 0:
                dur = probe_duration(part)
                rec["probeDuration"] = dur
                if dur is None or abs(dur - expect) > TOL:
                    raise RuntimeError(f"内容校验失败: 时长 {dur}s ≠ 官方 {expect:.1f}s（疑似占位视频）")
            part.replace(out)
            rec["status"] = "ok"
            rec["bytes"] = out.stat().st_size
            return rec
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
            if out.with_suffix(".part").exists():
                out.with_suffix(".part").unlink()
    rec["status"] = "fail"
    rec["error"] = last_err
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", help="只抓指定场次")
    ap.add_argument("--all", action="store_true", help="全部投篮")
    ap.add_argument("--per-game", type=int, default=0, help="每场前 N 个")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    mode = "last60"
    if args.all:
        mode = "all"
    elif args.per_game:
        mode = f"first{args.per_game}"

    jobs = collect_shots(args.game, mode, args.per_game)
    print(f"模式={mode} 待抓 {len(jobs)} 个片段 -> {CLIPS}", flush=True)
    CLIPS.mkdir(parents=True, exist_ok=True)

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(download, j): j for j in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            results.append(rec)
            print(f"[{i}/{len(jobs)}] {rec['status'].upper()} {rec['gameId']}_{rec['eventNum']} "
                  f"P{rec['period']} {rec['gameClock']} {rec['playerName']} "
                  f"{rec.get('bytes', 0)//1024}KB", flush=True)

    results.sort(key=lambda r: (r["gameId"], r["eventNum"] or 0))
    man = CLIPS / f"_manifest_{mode}{'_' + args.game if args.game else ''}.json"
    json.dump(results, open(man, "w"), ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["status"] in ("ok", "exists"))
    fail = [r for r in results if r["status"] == "fail"]
    gb = sum(r.get("bytes", 0) for r in results) / 1e9
    print(f"\n完成 {ok}/{len(results)}，失败 {len(fail)}，共 {gb:.2f} GB，用时 {time.time()-t0:.0f}s")
    print(f"清单: {man}")
    if fail:
        print("失败列表:", [f"{r['gameId']}_{r['eventNum']}" for r in fail])
        sys.exit(2)


if __name__ == "__main__":
    main()
