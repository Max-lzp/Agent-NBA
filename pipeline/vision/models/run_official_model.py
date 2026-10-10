"""Validate downloaded source hashes, run team xFG model and export true shot overlays."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def run(data_dir, output_dir):
    manifest = json.loads((data_dir / "manifest.json").read_text())
    for name, metadata in manifest["datasets"].items():
        for field, hash_field in [("raw", "sha256"), ("csv", "csv_sha256")]:
            path = data_dir / metadata[field]
            if hashlib.sha256(path.read_bytes()).hexdigest() != metadata[hash_field]:
                raise ValueError(f"Source hash mismatch: {name}/{field}; refresh or audit data before use")
    source = manifest["datasets"]["shot_difficulty"]
    baseline = manifest["baseline"]
    subprocess.run([sys.executable, str(Path(__file__).with_name("xfg_decomposition.py")),
        "--input", str(data_dir / source["csv"]), "--name-column", "PLAYERNAME", "--teams", "HOU", "DAL",
        "--unit", "fraction", "--baseline", str(baseline["probability"]),
        "--baseline-source", baseline["definition"] + "; source_raw_sha256=" + source["sha256"],
        "--source", source["url"], "--fetched-at", source["fetched_at"][:10],
        "--output-dir", str(output_dir)], check=True)
    shots = []
    for name in ["flagg_shot_sequence", "amen_shot_sequence"]:
        if name not in manifest["datasets"]:
            continue
        metadata = manifest["datasets"][name]
        raw = json.loads((data_dir / metadata["raw"]).read_text())
        for row in raw["shotList"]:
            q, made = row["shotQuality"], row["success"]
            if not 0 <= q <= 1 or made not in (0, 1):
                raise ValueError("Invalid official shot probability/result")
            value = {"2PT Field Goal": 2, "3PT Field Goal": 3}[row["shotType"]]
            residual = value * (made - q)
            shots.append({"game_id": row["gameId"], "event_id": row["eventNum"], "player_id": row["playerId"],
                "player": raw["playerName"], "period": row["period"], "clock": row["gameClock"],
                "shot_quality": q, "expected_points": value * q, "actual_points": value * made,
                "scoring_residual_points": residual, "official_clip_url": row["largeUrl"],
                "alignment_status": "official event ID; offset in uploaded training video not verified",
                "source": metadata["url"], "source_raw_sha256": metadata["sha256"],
                "overlay": {"label": "出手难度与结果", "text":
                    f"{raw['playerName']}：预期命中率{100*q:.1f}%，预期{value*q:.2f}分，实际{value*made}分。"}})
    output = {"model": "shot_result_residual_v1", "shots": shots,
        "featured_candidates": sorted(shots, key=lambda s: (-abs(s["scoring_residual_points"]), s["game_id"], s["event_id"]))[:5],
        "featured_criterion": "absolute realized-minus-expected scoring residual; NOT event leverage or WPA",
        "high_leverage_replay_status": "unverified: no event-level leverage or uploaded-video offset in supplied source",
        "scope": "two training-game players, not all shots or all possessions"}
    (output_dir / "shot_sequence_output.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(f"Exported {len(shots)} real shot overlays and {len(output['featured_candidates'])} outcome-surprise candidates")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/deep"))
    parser.add_argument("--output-dir", type=Path, default=Path("pipeline/vision/models/results"))
    args = parser.parse_args()
    run(args.data_dir, args.output_dir)
