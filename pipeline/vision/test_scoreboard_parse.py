"""记分牌解析单测。token 坐标取自两段训练片的真实 OCR 结果（1080p，画面下方 1/4 条带内）。

    pipeline/vision/.venv/bin/python -m unittest pipeline/vision/test_scoreboard_parse.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scoreboard import clock_value, parse, plausible  # noqa: E402

# 弗拉格：横条「DAL 103 HOU 107 4TH 1:35 4.7」，上方有裁判 35 号
FLAGG = [("35", (1062, 131)), ("ESFn", (534, 170)), ("DAL", (654, 168)), ("103", (786, 167)),
         ("HOU", (922, 168)), ("107", (1052, 167)), ("4.TH", (1168, 168)), ("1:35", (1294, 171)),
         ("4.7", (1412, 170)), ("BONUS", (801, 222))]
# 阿门：右侧竖排，比分上下叠，钟右上、进攻钟右下
AMEN = [("60", (1547, 88)), ("7:29", (1669, 95)), ("TO:4", (1516, 116)), ("63", (1548, 146)),
        ("3RD", (1638, 148)), ("3.6", (1702, 148)), ("TO:5", (1516, 172))]


def last_minute(tokens, clock_text, game_clock, shot_clock=None):
    out = [(game_clock if t == clock_text else t, c) for t, c in tokens]
    if shot_clock is None:
        out = [(t, c) for t, c in out if t not in {"4.7", "3.6"}]
    else:
        out = [(shot_clock if t in {"4.7", "3.6"} else t, c) for t, c in out]
    return out


class ParseTest(unittest.TestCase):
    def test_regular_clock_both_layouts(self):
        f = parse(FLAGG)
        self.assertEqual((f["clock"], f["period"], f["shot_clock"], f["scores"]), ("1:35", "4TH", 4.7, [103, 107]))
        self.assertEqual(f["teams"], ["DAL", "HOU"])
        a = parse(AMEN)
        self.assertEqual((a["clock"], a["period"], a["shot_clock"], a["scores"]), ("7:29", "3RD", 3.6, [60, 63]))
        self.assertIsNone(a["teams"])

    def test_referee_number_is_not_a_score(self):
        self.assertEqual(parse(FLAGG)["scores"], [103, 107])

    def test_last_minute_tenths_horizontal(self):
        f = parse(last_minute(FLAGG, "1:35", "45.3", "12"))
        self.assertEqual((f["clock"], f["shot_clock"], f["scores"]), ("0:45.3", 12.0, [103, 107]))
        self.assertEqual(clock_value(f["clock"]), 45.3)

    def test_last_minute_both_tenths_vertical(self):
        a = parse(last_minute(AMEN, "7:29", "8.9", "3.6"))
        self.assertEqual((a["clock"], a["shot_clock"], a["scores"]), ("0:08.9", 3.6, [60, 63]))

    def test_last_minute_shot_clock_off(self):
        a = parse(last_minute(AMEN, "7:29", "8.9"))
        self.assertEqual((a["clock"], a["shot_clock"]), ("0:08.9", None))

    def test_anchor_keeps_clock_position(self):
        tokens = [(t, c) for t, c in last_minute(AMEN, "7:29", "8.9", "3.6") if t != "8.9"]
        self.assertIsNone(parse(tokens, anchor=(1669, 95))["clock"])
        self.assertEqual(parse(last_minute(AMEN, "7:29", "8.9", "3.6"), anchor=(1669, 95))["clock"], "0:08.9")

    def test_scale_for_720p(self):
        small = [(t, (x * 2 / 3, y * 2 / 3)) for t, (x, y) in FLAGG]
        self.assertEqual(parse(small, scale=2 / 3)["scores"], [103, 107])

    def test_plausible_clock(self):
        last = (10.0, "4TH", 60.0)
        self.assertTrue(plausible(59.2, "4TH", 10.7, last))
        self.assertTrue(plausible(60.0, "4TH", 12.0, last))       # 死球停表
        self.assertFalse(plausible(3.6, "4TH", 10.3, last))       # 把进攻钟当比赛钟
        self.assertFalse(plausible(75.0, "4TH", 10.3, last))      # 倒走
        self.assertTrue(plausible(720.0, "1ST", 10.3, last))      # 换节不检查


if __name__ == "__main__":
    unittest.main()
