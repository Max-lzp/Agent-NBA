#!/usr/bin/env python3
"""一条命令跑一段新视频：登记片段 → 追踪（顺带球掩码）与记分牌并行 → 事件流清单 → 可选上传。

    python3 -m pipeline.run_clip --video "../官方资料/exam.mp4"                    # 客主队、白衣队自动认
    python3 -m pipeline.run_clip --video "../官方资料/exam.mp4" --away DAL --home HOU --white HOU
    python3 -m pipeline.run_clip --clip exam --job-id <jobId> --upload      # 已登记过，只重跑并上传

产物：pipeline/vision/results/local/{clip}*.json，pipeline/events/out/{clip}.json 和同名 .html 报告。
视觉三步用 pipeline/vision/.venv；事件层只用标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VISION = ROOT / "pipeline" / "vision"
RESULTS = VISION / "results" / "local"
OUT = ROOT / "pipeline" / "events" / "out"
CLIPS_FILE = VISION / "clips.json"
VENV_PY = VISION / ".venv" / "bin" / "python"
TEAMS = ("HOU", "DAL")
WHITE_MIN_VOTES = 3        # 白衣队号码投票：胜票至少这么多
WHITE_MIN_SHARE = 0.75     # 且占比至少这么多，才推翻登记
STACK = "Team04DeepCourt"
PROFILE = os.environ.get("AWS_PROFILE", "team04")
REGION = "us-east-1"


def register(args) -> str:
    clips = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))
    if not args.video:
        if args.clip not in clips:
            sys.exit(f"片段 {args.clip} 没登记过，要给 --video")
        return args.clip
    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        sys.exit(f"找不到视频：{video}")
    clip_id = args.clip or re.sub(r"[^A-Za-z0-9]+", "_", video.stem).strip("_").lower() or "clip"
    away, home = args.away, args.home
    if away and not home:
        home = _other(away)
    if home and not away:
        away = _other(home)
    if away and away == home:
        sys.exit("--away / --home 必须是 HOU 和 DAL 各一个")
    white = args.white or home or "HOU"
    clips[clip_id] = {
        "game_id": args.game_id or clip_id,
        "official_game_id": args.game_id or "",
        "video": os.path.relpath(video, ROOT),
        "away": away,
        "home": home,
        "jersey_color_to_team": {"white": white, "dark": _other(white)},
    }
    CLIPS_FILE.write_text(json.dumps(clips, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[登记] {clip_id}: {away or '?'} @ {home or '?'}（记分牌认得出就以记分牌为准），先按白衣 {white}，{video.name}")
    return clip_id


def _other(team: str) -> str:
    return TEAMS[1] if team == TEAMS[0] else TEAMS[0]


def _set_clip(clip_id: str, **fields) -> dict:
    clips = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))
    clips[clip_id].update(fields)
    CLIPS_FILE.write_text(json.dumps(clips, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return clips[clip_id]


def check_teams(clip_id: str) -> None:
    """记分牌认出的客主队写回登记；与命令行不一致就报警。"""
    clip = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))[clip_id]
    board = json.loads((RESULTS / f"{clip_id}.scoreboard.json").read_text(encoding="utf-8"))
    away, home = board["away"], board["home"]
    if board.get("detected_reads", 0) and (clip.get("away"), clip.get("home")) not in {(None, None), (away, home)}:
        print(f"[警告] 命令行写的是 {clip['away']} @ {clip['home']}，记分牌读到 {away} @ {home}"
              f"（{board['detected_reads']} 次），已按记分牌改")
    _set_clip(clip_id, away=away, home=home)


def infer_white(clip_id: str) -> tuple[str | None, int, int]:
    """白衣球员读到的号码只在哪队赛季名单里有，就给那队投一票；深色球员反过来。返回（白衣队, 胜票, 总票）。"""
    sys.path.insert(0, str(ROOT))
    from pipeline.vision.roster_map import normalize_jersey, season_roster

    numbers = {team: {normalize_jersey(p["jersey"]) for p in block["players"]}
               for team, block in season_roster()["teams"].items()}
    doc = json.loads((RESULTS / f"{clip_id}.json").read_text(encoding="utf-8"))
    votes = {team: 0 for team in TEAMS}
    for p in doc["players"]:
        number, color = normalize_jersey(p.get("jersey")), p.get("jersey_color")
        if p.get("role") != "player" or not number or color not in {"white", "dark"}:
            continue
        owners = [team for team in TEAMS if number in numbers.get(team, set())]
        if len(owners) != 1:
            continue
        votes[owners[0] if color == "white" else _other(owners[0])] += 1
    total = sum(votes.values())
    if not total:
        return None, 0, 0
    team = max(votes, key=votes.get)
    return team, votes[team], total


def _start(cmd: list[str]) -> tuple[subprocess.Popen, float]:
    return subprocess.Popen([str(VENV_PY), *cmd], cwd=VISION, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True), time.time()


def _finish(name: str, proc: subprocess.Popen, start: float) -> bool:
    out, _ = proc.communicate()
    last = [line for line in out.strip().splitlines() if line.strip()][-1:] or [""]
    print(f"[{name}] {time.time() - start:.0f}s  {last[0][:160]}")
    if proc.returncode:
        print(out[-2000:])
    return proc.returncode == 0


def vision(clip_id: str) -> None:
    """追踪（GPU 为主，顺带写球掩码）和记分牌（CPU OCR）并行；白衣队认错就改登记重跑追踪。"""
    t0 = time.time()
    track = _start(["analyze.py", "--clip", clip_id])
    board = _start(["scoreboard.py", "--clip", clip_id])
    ok = _finish("记分牌", *board)
    ok = _finish("追踪+号码+球掩码", *track) and ok
    if not ok:
        sys.exit("视觉步骤失败，见上面的输出")
    check_teams(clip_id)
    clip = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))[clip_id]
    configured = clip["jersey_color_to_team"]["white"]
    team, won, total = infer_white(clip_id)
    print(f"[白衣] 号码投票 {team} {won}/{total}，登记的是 {configured}")
    if team and team != configured and won >= WHITE_MIN_VOTES and won / total >= WHITE_MIN_SHARE:
        print(f"[警告] 白衣更像 {team}，改登记并重跑追踪")
        _set_clip(clip_id, jersey_color_to_team={"white": team, "dark": _other(team)})
        if not _finish("追踪（重跑）", *_start(["analyze.py", "--clip", clip_id])):
            sys.exit("追踪重跑失败")
    print(f"[视觉] 合计 {time.time() - t0:.0f}s")


def build(clip_id: str, job_id: str) -> Path:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "lambdas"))
    from pipeline.events.build_stream import run
    import common

    clip = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))[clip_id]
    stream = run(job_id, "full", str(RESULTS / f"{clip_id}.json"), None,
                 str(RESULTS / f"{clip_id}.ball_raw.json"), str(RESULTS / f"{clip_id}.scoreboard.json"))
    stream.video.update({"source": clip["video"], "game_id": clip["game_id"]})
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{clip_id}.json"
    stream.write(str(path))

    board = json.loads((RESULTS / f"{clip_id}.scoreboard.json").read_text(encoding="utf-8"))
    reads = [r for r in board["reads"] if r["clock"]]
    if reads:
        first, last = reads[0], reads[-1]
        full = sum(1 for r in board["reads"] if r["clock"] and r["scores"])
        print(f"[记分牌] {first['period']} {first['clock']} → {last['clock']}，"
              f"比分 {first['scores']} → {last['scores']}（{board['away']} 在前），读全 {full}/{len(board['reads'])}")
    print(f"[记分牌] 得分 {board['ticks']}  进攻钟重置 {[r['t_sec'] for r in board['resets']]}")
    doc = stream.to_dict()
    for poss in doc["possessions"]:
        print(f"[回合] {poss['possession_id']} {poss['offense_team']} {poss['t_start']:.1f}–{poss['t_end']:.1f}s")
    for row in common.timeline_from_stream(doc):
        print(f"   {row['t']:6.2f}s  {row['label']}  ({row['confidence']:.2f})")
    print(f"[清单] {path}")
    return path


def enrich_official(clip_id: str, path: Path | str) -> bool:
    """data/ 下有这场的官方逐回合数据就并进清单；没有就跳过。
    返回 True 表示刚把 boxscore 出场名单写进 roster.json，调用方要重建清单再来一次。"""
    from pipeline.enrich.join import RESULTS as BOARD_DIR, clip_on_court, enrich, write_game_roster
    from pipeline.enrich.official import load_game
    import common

    from pipeline.enrich.find_game import best, find

    clip = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))[clip_id]
    board = json.loads((BOARD_DIR / f"{clip_id}.scoreboard.json").read_text(encoding="utf-8"))
    game_id = clip.get("official_game_id") or ""
    if not game_id:
        game_id = best(board) or ""
        top = find(board)[:2]
        print(f"[认比赛] 记分牌比分对官方：" + "，".join(f"{g['game_id']} {g['share']:.0%}" for g in top)
              + (f" → {game_id}" if game_id else " → 没有唯一对上的，跳过官方补全（可用 --game-id 指定）"))
        if not game_id:
            return False
        clip = _set_clip(clip_id, official_game_id=game_id)
    game = load_game(game_id)
    if not game["plays"]:
        print(f"[官方] data/ 下没有 {game_id} 的逐回合数据，跳过")
        return False
    roster_file = VISION / "roster.json"
    roster = json.loads(roster_file.read_text(encoding="utf-8"))
    changed = []
    if game["players"] and clip["game_id"] not in roster["games"]:
        write_game_roster(clip["game_id"], game["players"])
        roster = json.loads(roster_file.read_text(encoding="utf-8"))
        changed.append("出场名单")
    court = clip_on_court(game, board)
    entry = roster["games"].get(clip["game_id"])
    if entry is not None and court and entry.get("on_court") != court:
        entry["on_court"] = court
        roster_file.write_text(json.dumps(roster, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed.append(f"场上阵容 {court}")
    if changed:
        import pipeline.vision.roster_map as roster_map
        roster_map.ROSTER = json.loads(roster_file.read_text(encoding="utf-8"))
        print(f"[官方] roster.json games[{clip['game_id']}] 写入{'、'.join(changed)}，重建清单")
        return True
    path = Path(path)
    doc = enrich(json.loads(path.read_text(encoding="utf-8")), board, game, game_id)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    info = doc["official"]
    print(f"[官方] {game_id} 片段内 {len(info['plays'])} 条记录，比分对上 {info['matched_by_score']}，"
          f"补入 {info['added_events']}，删掉视觉失误 {info['dropped_cv_turnovers']}")
    for row in common.timeline_from_stream(doc):
        print(f"   {row['t']:6.2f}s  {row['label']}  ({row['confidence']:.2f}, {row['source']})")
    return False


def upload(path: Path, job_id: str, bucket: str = "") -> None:
    """传到 S3 structured/{jobId}.json。失败只报警告：本地清单已经是完整产物。"""
    bucket = bucket or os.environ.get("DEEPCOURT_BUCKET", "")
    if not bucket:
        found = subprocess.run(
            ["aws", "cloudformation", "describe-stacks", "--stack-name", STACK, "--region", REGION,
             "--profile", PROFILE, "--query", "Stacks[0].Outputs[?OutputKey=='MediaBucketName'].OutputValue",
             "--output", "text"], capture_output=True, text=True)
        bucket = found.stdout.strip() if found.returncode == 0 else ""
        if not bucket or bucket == "None":
            print(f"[上传] 跳过：查不到栈 {STACK} 的桶（{found.stderr.strip()[:120] or '没有输出'}）。"
                  f"用 --bucket 或 DEEPCOURT_BUCKET 指定；本地清单在 {path}")
            return
    key = f"structured/{job_id}.json"
    sent = subprocess.run(["aws", "s3", "cp", str(path), f"s3://{bucket}/{key}", "--region", REGION,
                           "--profile", PROFILE, "--content-type", "application/json"], capture_output=True, text=True)
    if sent.returncode != 0:
        print(f"[上传] 失败：{sent.stderr.strip()[:200]}；本地清单在 {path}")
        return
    print(f"[上传] s3://{bucket}/{key}，回放页 game.html?jobId={job_id}")


def main() -> None:
    ap = argparse.ArgumentParser(description="一条命令跑新视频")
    ap.add_argument("--video", help="视频路径；不给就用 clips.json 里已登记的 --clip")
    ap.add_argument("--clip", help="片段 id，默认取文件名")
    ap.add_argument("--away", choices=TEAMS, help="客队（记分牌左 / 上）；记分牌读得到队名时以记分牌为准")
    ap.add_argument("--home", choices=TEAMS, help="主队")
    ap.add_argument("--white", choices=TEAMS, help="穿白（浅色）球衣的队，默认主队；号码投票明显不符时自动改")
    ap.add_argument("--game-id", help="官方 gameId，例如 0022500701")
    ap.add_argument("--job-id", help="云上任务 id；默认等于片段 id")
    ap.add_argument("--skip-vision", action="store_true", help="视觉产物已在，只重建清单（调事件规则时用）")
    ap.add_argument("--upload", action="store_true", help="把清单传到 S3 structured/{jobId}.json；失败不影响本地产物")
    ap.add_argument("--bucket", default="", help="S3 桶名；默认读环境变量 DEEPCOURT_BUCKET，再不行查 CloudFormation 输出")
    args = ap.parse_args()

    t0 = time.time()
    clip_id = register(args)
    if not args.skip_vision:
        vision(clip_id)
    path = build(clip_id, args.job_id or clip_id)
    if enrich_official(clip_id, path):
        path = build(clip_id, args.job_id or clip_id)
        enrich_official(clip_id, path)
    from pipeline.report import write as write_report
    print(f"[报告] {write_report(Path(path))}（双击打开；点事件跳到视频那一刻）")
    if args.upload:
        upload(path, args.job_id or clip_id, args.bucket)
    print(f"[完成] {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
