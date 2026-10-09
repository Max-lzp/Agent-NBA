"""记分牌（节次 + 比赛钟 + 比分）→ data/ 下哪一场比赛。

    python3 -m pipeline.enrich.find_game --clip exam
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .official import DATA, clock_seconds, load_game, period_number

RESULTS = Path(__file__).resolve().parents[1] / "vision" / "results" / "local"
GAME_FILE = re.compile(r"(\d{10})_playbyplay", re.I)
SCORE_LAG_S = 3.0      # 记分牌比官方记录晚几秒更新也算对上


def game_ids(data_dir: Path = DATA) -> list[str]:
    return sorted({m.group(1) for p in data_dir.rglob("*_playbyplay*.json") if (m := GAME_FILE.match(p.name))})


def score_at(plays: list[dict], period: int, clock_s: float) -> set[tuple[int, int]]:
    """官方在（节次, 比赛钟）这一刻前后 SCORE_LAG_S 内出现过的（客, 主）比分。"""
    seen, last = set(), None
    for p in sorted(plays, key=lambda p: (p["period"] or 0, -(p["clock_s"] or 0), p["action_number"] or 0)):
        if p["score_away"] is None or p["score_home"] is None:
            continue
        before = (p["period"] or 0, -(p["clock_s"] or 0)) <= (period, -(clock_s - SCORE_LAG_S))
        if before:
            last = (p["score_away"], p["score_home"])
        elif p["period"] == period and p["clock_s"] is not None and p["clock_s"] >= clock_s - SCORE_LAG_S:
            seen.add((p["score_away"], p["score_home"]))
        else:
            break
    if last:
        seen.add(last)
    return seen


def find(board: dict, data_dir: Path = DATA) -> list[dict]:
    """每一场按「记分牌读数与官方比分对上的比例」打分，高的在前。"""
    reads = [(period_number(r.get("period")), clock_seconds(r.get("clock")), tuple(r["scores"]))
             for r in board.get("reads", []) if r.get("scores")]
    reads = [r for r in reads if r[0] and r[1] is not None]
    if not reads:
        return []
    ranked = []
    for game_id in game_ids(data_dir):
        plays = load_game(game_id, data_dir)["plays"]
        if not plays:
            continue
        hits = sum(scores in score_at(plays, period, clock) for period, clock, scores in reads)
        ranked.append({"game_id": game_id, "hits": hits, "reads": len(reads), "share": round(hits / len(reads), 3)})
    return sorted(ranked, key=lambda g: -g["share"])


def best(board: dict, data_dir: Path = DATA, min_share: float = 0.6) -> str | None:
    ranked = find(board, data_dir)
    if not ranked or ranked[0]["share"] < min_share:
        return None
    if len(ranked) > 1 and ranked[1]["share"] >= ranked[0]["share"] - 0.1:
        return None
    return ranked[0]["game_id"]


def main() -> None:
    ap = argparse.ArgumentParser(description="记分牌 → 哪一场比赛")
    ap.add_argument("--clip", required=True)
    ap.add_argument("--data-dir", default=str(DATA))
    args = ap.parse_args()
    board = json.loads((RESULTS / f"{args.clip}.scoreboard.json").read_text(encoding="utf-8"))
    for g in find(board, Path(args.data_dir))[:5]:
        print(f"{g['game_id']}  {g['hits']}/{g['reads']}  {g['share']:.0%}")
    print("结论：", best(board, Path(args.data_dir)) or "没有唯一对上的比赛")


if __name__ == "__main__":
    main()
