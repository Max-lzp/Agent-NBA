"""回溯和成片取舍。python3 -m unittest pipeline.vision.test_backtrack"""
from __future__ import annotations

import unittest

from pipeline.vision.backtrack import assign_lineup, chain, extend, keep_unique, visible_at, _fill_rest


def _rows(n, color="dark"):
    rows = []
    for i in range(n):
        x = 100 + i * 8
        rows.append({"players": [[x, 200, x + 40, 400]], "colors": [color], "balls": [[x + 10, 300, x + 30, 320]]})
    return rows


class BacktrackTest(unittest.TestCase):
    def test_extend_stays_inside_the_known_span(self):
        rows = _rows(20)
        anchors = {15: rows[15]["players"][0], 18: rows[18]["players"][0]}
        got = extend(rows, anchors, "dark")
        self.assertGreaterEqual(min(got), 15)
        self.assertLessEqual(max(got), 18)
        self.assertIn(16, got)

    def test_chain_walks_back_to_first_frame(self):
        rows = _rows(20)
        got = chain(rows, 19, rows[19]["players"][0], 0, "dark", {})
        self.assertEqual(min(got), 0)
        self.assertAlmostEqual(got[0][0], 100, delta=1)

    def test_chain_stops_when_two_boxes_are_equally_close(self):
        rows = _rows(5)
        rows[3] = {
            "players": [[128, 200, 168, 400], [136, 200, 176, 400]],
            "colors": ["dark", "dark"], "balls": [],
        }
        got = chain(rows, 4, rows[4]["players"][0], 0, "dark", {})
        self.assertNotIn(3, got)
        self.assertIn(2, got)

    def test_unique_keeps_higher_priority_box(self):
        low = {"priority": 1, "frames": {0: [0, 0, 40, 80]}}
        high = {"priority": 9, "frames": {0: [2, 0, 42, 80]}}
        kept = keep_unique([low, high])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["priority"], 9)

    def test_intro_shows_everyone_then_only_the_ball_side(self):
        players = [
            {"team": "DAL", "number": "32", "name": "Flagg", "role_seed": "shooter",
             "path": [[0.2, 100, 200, 140, 400], [8.0, 300, 200, 340, 400]]},
            {"team": "HOU", "number": "17", "name": "Eason", "role_seed": "lead",
             "path": [[0.2, 400, 200, 440, 400], [8.0, 360, 200, 400, 400]]},
        ]
        ball = [[0.2, 110, 300, 130, 320], [8.0, 310, 300, 330, 320]]
        early = visible_at(players, ball, [], [{"t0": 0, "t1": 9, "team": "DAL"}], 0.2)
        late = visible_at(players, ball, [{"t": 8.0}], [{"t0": 0, "t1": 9, "team": "DAL"}], 8.0, shot_t=8.2)
        self.assertEqual(sorted(p["number"] for p in early), ["17", "32"])
        self.assertEqual(sorted(p["number"] for p in late), ["17", "32"])
        self.assertIn("handler", {p["role"] for p in late})
        self.assertTrue(any(p["role"] in ("defender", "lead") for p in late if p["number"] == "17"))


class LineupTest(unittest.TestCase):
    def test_pin_takes_the_overlapping_slot_only(self):
        slots = [
            {"id": 1, "color": "dark", "height": 200},
            {"id": 2, "color": "dark", "height": 260},
            {"id": 3, "color": "white", "height": 220},
        ]
        pins = [{
            "team": "DAL", "number": "32", "name": "Flagg", "color": "dark",
            "priority": 100, "role_seed": "shooter", "scores": {1: 0.4, 2: 4.0, 3: 6.0},
        }]
        roster = [
            {"team": "DAL", "number": "32", "name": "Flagg", "color": "dark", "position": "前锋"},
            {"team": "DAL", "number": "21", "name": "Gafford", "color": "dark", "position": "中锋"},
        ]
        got = assign_lineup(slots, pins, roster)
        self.assertEqual(got[0]["slot_id"], 2)
        self.assertEqual(got[0]["number"], "32")

    def test_center_goes_to_the_taller_slot(self):
        slots = [{"id": 1, "color": "dark", "height": 280}, {"id": 2, "color": "dark", "height": 200}]
        roster = [
            {"team": "DAL", "number": "21", "name": "Gafford", "color": "dark", "position": "中锋"},
            {"team": "DAL", "number": "13", "name": "Marshall", "color": "dark", "position": "后卫"},
        ]
        got = _fill_rest(slots, roster)
        self.assertEqual([row["number"] for row in got], ["21", "13"])
        self.assertEqual(got[0]["slot_id"], 1)

    def test_similar_heights_stay_blank(self):
        slots = [{"id": 1, "color": "dark", "height": 210}, {"id": 2, "color": "dark", "height": 200}]
        roster = [
            {"team": "DAL", "number": "21", "name": "Gafford", "color": "dark", "position": "中锋"},
            {"team": "DAL", "number": "13", "name": "Marshall", "color": "dark", "position": "后卫"},
        ]
        self.assertEqual(_fill_rest(slots, roster), [])


if __name__ == "__main__":
    unittest.main()
