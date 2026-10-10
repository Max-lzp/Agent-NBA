"""Read real season CSVs; export descriptive xFG decomposition and replay candidates."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


def probability(value, unit, signed=False):
    number = float(value) / (100 if unit == "percent" else 1)
    if not np.isfinite(number) or not (-1 if signed else 0) <= number <= 1:
        raise ValueError(f"Invalid {unit} probability/difference: {value}")
    return number


def decompose(frame, baseline, unit, quality_column="AVGSHOTQUALITY",
              excess_column="FGPCTABOVEEXPECTED", name_column="PLAYER_NAME"):
    if frame.empty:
        raise ValueError("Empty shot_difficulty input")
    required = {name_column, quality_column, excess_column}
    if not required.issubset(frame.columns):
        raise ValueError(f"Missing columns: {sorted(required - set(frame.columns))}")
    baseline = probability(baseline, unit)
    records = []
    for _, row in frame.iterrows():
        name = str(row[name_column]).strip()
        if not name or pd.isna(row[name_column]):
            raise ValueError("Player name must be present")
        q = probability(row[quality_column], unit)
        excess = probability(row[excess_column], unit, signed=True)
        actual = q + excess
        if not 0 <= actual <= 1:
            raise ValueError(f"Derived FG probability outside [0,1]: {name}")
        if "FGPCT" in frame.columns and abs(probability(row["FGPCT"], unit) - actual) > .0011:
            raise ValueError(f"Reported FG% disagrees with quality + residual: {name}")
        quality = q - baseline
        quadrant = ("优质出手" if quality >= 0 else "高难度出手") + " / " + ("超预期命中" if excess >= 0 else "低于预期命中")
        # Values are percentage points / extra makes per 100 FGA, not points scored.
        records.append({"player": name, "expected_fg_probability": q,
            "actual_fg_probability_derived": actual, "quality_contribution_pp": 100 * quality,
            "execution_residual_pp": 100 * excess, "total_vs_baseline_pp": 100 * (actual - baseline),
            "quadrant": quadrant,
            "overlay": {"label": "出手质量 / 命中偏差", "text":
                f"{name}：每100次出手，质量较基准{100*quality:+.1f}次命中，实际较预期{100*excess:+.1f}次。"}})
        if "PLAYERID" in frame.columns:
            records[-1]["player_id"] = int(row["PLAYERID"])
        if "TEAMABBREVIATION" in frame.columns:
            records[-1]["team"] = str(row["TEAMABBREVIATION"])
        if "FGA" in frame.columns:
            attempts = float(row["FGA"])
            if not np.isfinite(attempts) or not attempts.is_integer() or attempts <= 0:
                raise ValueError(f"Invalid tracking FGA: {name}")
            records[-1]["tracked_attempts"] = int(attempts)
    return {"model": "xfg_decomposition_v1", "baseline_fg_probability": baseline,
            "scope": "season player aggregates; descriptive, not a single-play or causal estimate",
            "players": sorted(records, key=lambda r: (-r["execution_residual_pp"], r["player"]))}


def replay_candidates(frame, unit, limit=5):
    required = {"event_id", "game_id", "player", "period", "clock", "video_time_s",
                "leverage", "shot_quality", "made", "shot_value", "source"}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError(f"Replay input needs nonempty rows and columns {sorted(required)}")
    records, seen = [], set()
    for _, row in frame.iterrows():
        for field in ("event_id", "game_id", "player", "clock", "source"):
            if pd.isna(row[field]) or not str(row[field]).strip():
                raise ValueError(f"Missing {field}")
        key = (str(row["game_id"]), str(row["event_id"]))
        if key in seen:
            raise ValueError(f"Duplicate event: {key}")
        seen.add(key)
        period, made, value = float(row["period"]), float(row["made"]), float(row["shot_value"])
        video_time, leverage = float(row["video_time_s"]), float(row["leverage"])
        if not period.is_integer() or period < 1 or made not in (0, 1) or value not in (2, 3):
            raise ValueError(f"Invalid period/made/shot_value: {key}")
        if not np.isfinite(video_time) or video_time < 0 or not np.isfinite(leverage) or leverage < 0:
            raise ValueError(f"Invalid timestamp/leverage: {key}")
        q = probability(row["shot_quality"], unit)
        expected, residual = value * q, value * (made - q)
        records.append({"event_id": key[1], "game_id": key[0], "player": str(row["player"]),
            "period": int(period), "clock": str(row["clock"]), "video_time_s": video_time,
            "leverage": leverage, "expected_points": expected, "scoring_residual_points": residual,
            "source": str(row["source"]), "clip_start_s": max(0, video_time - 4),
            "clip_end_s": video_time + 3, "requires_clip_boundary_review": True,
            "overlay": {"label": "这次出手", "text":
                f"{row['player']}：预期{expected:.2f}分，实际{value*made:.0f}分，偏差{residual:+.2f}分。"}})
    # Leverage must be per-event, never player season TOT_LVG copied onto shots.
    return sorted(records, key=lambda r: (-r["leverage"], r["game_id"], r["event_id"]))[:limit]


def provenance(path):
    return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Real data/deep/shot_difficulty CSV")
    parser.add_argument("--unit", choices=("fraction", "percent"), required=True)
    parser.add_argument("--baseline", type=float, required=True, help="Verified comparable baseline, same unit")
    parser.add_argument("--baseline-source", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--fetched-at", required=True)
    parser.add_argument("--quality-column", default="AVGSHOTQUALITY")
    parser.add_argument("--excess-column", default="FGPCTABOVEEXPECTED")
    parser.add_argument("--name-column", default="PLAYER_NAME")
    parser.add_argument("--teams", nargs="+", help="Optional TEAMABBREVIATION filter; baseline stays as explicitly supplied")
    parser.add_argument("--replay-input", type=Path)
    parser.add_argument("--leverage-definition", help="Verified per-event leverage definition, required with replay input")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    datetime.strptime(args.fetched_at, "%Y-%m-%d")
    frame = pd.read_csv(args.input)
    if args.teams:
        if "TEAMABBREVIATION" not in frame.columns:
            parser.error("--teams requires TEAMABBREVIATION")
        frame = frame[frame["TEAMABBREVIATION"].isin(args.teams)]
    result = decompose(frame, args.baseline, args.unit, args.quality_column, args.excess_column, args.name_column)
    result["provenance"] = {"source": args.source, "fetched_at": args.fetched_at,
        "generated_at": datetime.now(timezone.utc).isoformat(), "input": provenance(args.input),
        "baseline_source": args.baseline_source, "input_unit": args.unit,
        "teams_filter": args.teams,
        "columns": {"quality": args.quality_column, "excess": args.excess_column, "name": args.name_column}}
    if args.replay_input:
        if not args.leverage_definition:
            parser.error("--replay-input requires --leverage-definition")
        replay_frame = pd.read_csv(args.replay_input, dtype={"event_id": str, "game_id": str, "clock": str})
        result["replay_candidates"] = replay_candidates(replay_frame, args.unit)
        result["provenance"]["replay_input"] = provenance(args.replay_input)
        result["provenance"]["leverage_definition"] = args.leverage_definition
    else:
        result["replay_candidates"] = []
        result["replay_status"] = "unavailable: verified event-level leverage and video alignment not supplied"
    # Only create artifacts after validating every input.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    players = result["players"]
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter([r["quality_contribution_pp"] for r in players], [r["execution_residual_pp"] for r in players])
    for r in players:
        ax.annotate(r["player"], (r["quality_contribution_pp"], r["execution_residual_pp"]), fontsize=7)
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.axvline(0, color="gray", linewidth=0.8)
    ax.set(xlabel="Shot quality vs supplied baseline (percentage points)",
           ylabel="Actual minus expected FG (percentage points)",
           title="Descriptive xFG decomposition: quality and execution residual")
    fig.tight_layout()
    fig.savefig(args.output_dir / "xfg_decomposition.png", dpi=160)
    plt.close(fig)
    (args.output_dir / "xfg_deviation_output.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Exported {len(players)} players, {len(result['replay_candidates'])} replay candidates")


if __name__ == "__main__":
    main()
