"""事件流对真值打分：调阈值的唯一裁判。

真值在 ground_truth/{clip}.json：possessions（回合级，来自记分牌，可信）+ events（人工补）。

指标：
  poss      预测回合数 / 真值回合数
  team_acc  按 0.1s 采样，预测进攻方与真值一致的时间占比（无预测回合的时刻算错）
  fake_to   落在真值回合内部（离边界 > BOUNDARY_S）的 turnover，真值里没有就全是误报
  shot/pass 按类型、队伍、时间容差匹配的召回 / 精确率；真值没标的类型不打分

用法：
  python -m pipeline.events.evaluate                     # 两段片 × CV 来源 × 原始球掩码
  python -m pipeline.events.evaluate --source qwen3_vl --no-ball-raw
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .build_stream import run

EVENTS = Path(__file__).resolve().parent
VISION_RESULTS = EVENTS.parent / "vision" / "results"
CLIPS = ("amen", "flagg")
BOUNDARY_S = 1.0        # 回合边界附近的 turnover 不算误报
PASS_TOL_S = 0.5        # 传球默认时间容差；真值行可用 tol_s 覆盖


def _team_at(possessions: list[dict], t: float, key_team: str, key_s: str, key_e: str) -> str | None:
    for p in possessions:
        if p[key_s] <= t <= p[key_e]:
            return p[key_team]
    return None


def _match(gt: list[dict], pred: list[dict], default_tol: float) -> tuple[int, int, int]:
    """贪心一对一匹配，返回 (命中, 真值数, 预测数)。"""
    used: set[int] = set()
    hit = 0
    for g in gt:
        tol = g.get("tol_s", default_tol)
        best, best_dt = None, None
        for i, p in enumerate(pred):
            if i in used or (g.get("team") and p["team"] != g["team"]):
                continue
            dt = abs(p["t"] - g["t"])
            if dt <= tol and (best_dt is None or dt < best_dt):
                best, best_dt = i, dt
        if best is not None:
            used.add(best)
            hit += 1
    return hit, len(gt), len(pred)


def score(clip: str, stream: dict, truth: dict) -> dict:
    gp = truth["possessions"]
    pp = stream["possessions"]
    t_end = truth.get("duration_s") or max(p["t_end"] for p in gp)
    samples = [round(i * 0.1, 1) for i in range(int(t_end * 10) + 1)]
    judged = [t for t in samples if _team_at(gp, t, "team", "t_start", "t_end")]
    ok = sum(1 for t in judged
             if _team_at(pp, t, "offense_team", "t_start", "t_end") == _team_at(gp, t, "team", "t_start", "t_end"))

    pred = [dict(e, team=p["offense_team"]) for p in pp for e in p["events"]]
    fake_to = sum(1 for e in pred if e["type"] == "turnover"
                  and any(g["t_start"] + BOUNDARY_S < e["t"] < g["t_end"] - BOUNDARY_S for g in gp))

    out = {"clip": clip, "poss": f'{len(pp)}/{len(gp)}',
           "team_acc": ok / max(len(judged), 1), "fake_to": fake_to,
           "events": len(pred)}
    for etype, tol in (("shot", 1.0), ("pass", PASS_TOL_S)):
        g = [e for e in truth.get("events", []) if e["type"] == etype]
        if not g:
            continue
        hit, ng, npred = _match(g, [e for e in pred if e["type"] == etype], tol)
        out[etype] = f"R {hit}/{ng} P {hit}/{npred}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="事件流 vs 真值")
    ap.add_argument("--clip", choices=(*CLIPS, "all"), default="all")
    ap.add_argument("--source", default="cv", help="cv / qwen3_vl / ebard_yolov8n")
    ap.add_argument("--no-ball-raw", action="store_true", help="不用原始球掩码（插值球按真检测对待）")
    ap.add_argument("--ticks-dir", help="目录下 {clip}.ticks.json 作为记分牌跳变输入")
    ap.add_argument("--dump", help="把每段的结构化 JSON 写到此目录，便于抽查")
    args = ap.parse_args()

    for clip in (CLIPS if args.clip == "all" else (args.clip,)):
        suffix = "" if args.source == "cv" else f".{args.source}"
        ball_raw = VISION_RESULTS / "local" / f"{clip}.ball_raw.json"
        ticks = Path(args.ticks_dir) / f"{clip}.ticks.json" if args.ticks_dir else None
        stream = run(f"eval-{clip}", "vlm",
                     str(VISION_RESULTS / "local" / f"{clip}{suffix}.json"),
                     str(VISION_RESULTS / f"frame_map_{clip}.json"),
                     None if args.no_ball_raw or not ball_raw.is_file() else str(ball_raw),
                     str(ticks) if ticks and ticks.is_file() else None)
        if args.dump:
            Path(args.dump).mkdir(parents=True, exist_ok=True)
            stream.write(str(Path(args.dump) / f"{clip}.json"))
        truth = json.loads((EVENTS / "ground_truth" / f"{clip}.json").read_text(encoding="utf-8"))
        r = score(clip, stream.to_dict(), truth)
        extra = "  ".join(f"{k}={r[k]}" for k in ("shot", "pass") if k in r)
        print(f'{r["clip"]:6} poss={r["poss"]:6} team_acc={r["team_acc"]:.0%}  '
              f'fake_to={r["fake_to"]}  events={r["events"]}  {extra}')


if __name__ == "__main__":
    main()
