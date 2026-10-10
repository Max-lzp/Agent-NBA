"""Synthetic contract tests; fixtures are not official NBA data or deliverable results."""
import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from xfg_decomposition import decompose, replay_candidates
from run_official_model import run

spec = importlib.util.spec_from_file_location("build_roster", Path(__file__).parents[1] / "rosters/build_roster.py")
roster = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roster)


class ModelTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame([{"PLAYER_NAME": "Synthetic Test", "AVGSHOTQUALITY": 0.4, "FGPCTABOVEEXPECTED": 0.1}])

    def test_decomposition_conserves_total_and_separates_components(self):
        row = decompose(self.frame(), 0.45, "fraction")["players"][0]
        self.assertAlmostEqual(row["quality_contribution_pp"], -5)
        self.assertAlmostEqual(row["execution_residual_pp"], 10)
        self.assertAlmostEqual(row["total_vs_baseline_pp"], 5)
        self.assertAlmostEqual(row["total_vs_baseline_pp"], row["quality_contribution_pp"] + row["execution_residual_pp"])

    def test_percent_input_has_same_result(self):
        frame = self.frame()
        frame[["AVGSHOTQUALITY", "FGPCTABOVEEXPECTED"]] *= 100
        self.assertEqual(decompose(frame, 45, "percent"), decompose(self.frame(), .45, "fraction"))

    def test_reported_fg_disagreement_rejected(self):
        frame = self.frame()
        frame["FGPCT"] = .6
        with self.assertRaises(ValueError):
            decompose(frame, .45, "fraction")

    def test_official_identity_and_attempts_preserved(self):
        frame = self.frame()
        frame["PLAYERID"], frame["TEAMABBREVIATION"], frame["FGA"] = 42, "HOU", 100
        row = decompose(frame, .45, "fraction")["players"][0]
        self.assertEqual((row["player_id"], row["team"], row["tracked_attempts"]), (42, "HOU", 100))

    def test_changed_source_is_rejected_before_running_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "raw.json").write_bytes(b"{}")
            (root / "data.csv").write_text("changed\n")
            manifest = {"datasets": {"test": {"raw": "raw.json", "csv": "data.csv",
                "sha256": hashlib.sha256(b"{}").hexdigest(), "csv_sha256": hashlib.sha256(b"original\n").hexdigest()}}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Source hash mismatch"):
                run(root, root / "output")
            self.assertFalse((root / "output").exists())

    def test_invalid_or_missing_probabilities_rejected(self):
        for q, excess in [(float("nan"), 0), (.9, .2), (.1, -.2), (40, 10)]:
            with self.subTest(q=q, excess=excess), self.assertRaises(ValueError):
                decompose(pd.DataFrame([{"PLAYER_NAME": "Test", "AVGSHOTQUALITY": q, "FGPCTABOVEEXPECTED": excess}]), .45, "fraction")

    def test_replays_rank_event_leverage_and_score_two_or_three_points(self):
        rows = [{"event_id": str(i), "game_id": "test", "player": "Synthetic Test", "period": 4,
                 "clock": "01:00", "video_time_s": 2, "leverage": i, "shot_quality": .4,
                 "made": 1 if i == 5 else 0, "shot_value": 3 if i == 5 else 2, "source": "synthetic-test"} for i in range(6)]
        result = replay_candidates(pd.DataFrame(rows), "fraction")
        self.assertEqual([r["event_id"] for r in result], ["5", "4", "3", "2", "1"])
        self.assertAlmostEqual(result[0]["scoring_residual_points"], 1.8)
        self.assertAlmostEqual(result[1]["scoring_residual_points"], -.8)
        self.assertEqual(result[0]["clip_start_s"], 0)
        with self.assertRaises(ValueError):
            replay_candidates(pd.DataFrame(rows + [rows[0]]), "fraction")


class RosterTests(unittest.TestCase):
    def fixtures(self):
        registrations = [{"team": "HOU", "player_id": "1", "name": "Amen Thompson", "jersey": "1",
                          "position": "G-F", "from": "2025-10-01", "to": ""},
                         {"team": "DAL", "player_id": "2", "name": "Max Christie", "jersey": "00",
                          "position": "G", "from": "2025-10-01", "to": ""}]
        totals = [{"team": r["team"], "player_id": r["player_id"], "games_played": "2",
                   "games_started": "1", "minutes_total": "40.5"} for r in registrations]
        return registrations, totals

    def test_string_jersey_short_names_and_totals(self):
        registrations, totals = self.fixtures()
        result = roster.build(registrations, totals, "2026-10-10")
        self.assertEqual(result["teams"]["DAL"]["players"][0]["jersey"], "00")
        self.assertEqual(result["teams"]["HOU"]["players"][0]["short_name"], "Amen")
        self.assertEqual(result["teams"]["DAL"]["players"][0]["minutes_total"], 40.5)

    def test_number_change_emits_two_intervals_without_summing_totals(self):
        registrations, totals = self.fixtures()
        registrations[0]["to"] = "2026-01-01"
        registrations.append({**registrations[0], "jersey": "0", "from": "2026-01-02", "to": ""})
        result = roster.build(registrations, totals, "2026-10-10")
        self.assertEqual(len(result["teams"]["HOU"]["players"]), 2)
        self.assertEqual(result["teams"]["HOU"]["players"][1]["minutes_total"], 40.5)

    def test_overlap_and_missing_totals_are_rejected(self):
        registrations, totals = self.fixtures()
        with self.assertRaises(ValueError):
            roster.build(registrations + [registrations[0]], totals, "2026-10-10")
        with self.assertRaises(ValueError):
            roster.build(registrations, totals[:1], "2026-10-10")

    def test_zero_appearance_players_are_retained_and_sorted_last(self):
        registrations, totals = self.fixtures()
        registrations.append({"team": "HOU", "player_id": "3", "name": "Synthetic New Player",
            "short_name": "New", "jersey": "00", "position": "F", "from": "2025-10-01", "to": ""})
        totals.append({"team": "HOU", "player_id": "3", "games_played": "0", "games_started": "0", "minutes_total": "0"})
        players = roster.build(registrations, totals, "2026-10-10")["teams"]["HOU"]["players"]
        self.assertEqual([p["player_id"] for p in players], [1, 3])


if __name__ == "__main__":
    unittest.main()
