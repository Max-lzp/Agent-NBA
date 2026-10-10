"""stdlib unittest：不连 AWS。"""

from __future__ import annotations

import unittest

import common


class CommonTest(unittest.TestCase):
    def test_safe_filename_strips_path_and_spaces(self):
        self.assertEqual(common.safe_filename("../clips/my clip.mp4"), "my-clip.mp4")

    def test_object_key_uses_raw_prefix(self):
        key = common.object_key("abc", "clip.mp4")
        self.assertEqual(key, "raw/abc/clip.mp4")

    def test_timeline_from_stream_labels_and_sorts(self):
        stream = {
            "possessions": [
                {
                    "possession_id": "P01",
                    "offense_team": "HOU",
                    "events": [
                        {"t": 19.35, "type": "shot", "actor": {"team": "HOU", "number": "30", "name": "Capela"},
                         "confidence": 0.7, "source": "cv"},
                        {"t": 4.4, "type": "pass", "actor": {"team": "HOU", "number": "17", "name": "Eason"},
                         "target": {"team": "HOU", "number": "", "track_id": 9}, "confidence": 0.8, "source": "cv"},
                        {"t": 5.0, "type": "scoreboard_sync", "actor": {"team": "HOU", "number": ""},
                         "confidence": 1.0, "source": "cv"},
                    ],
                }
            ]
        }
        rows = common.timeline_from_stream(stream)
        self.assertEqual([row["eventType"] for row in rows], ["pass", "shot"])
        self.assertEqual(rows[0]["label"], "Eason → 火箭球员 传球")
        self.assertEqual(rows[1]["timecode"], "00:19")
        self.assertEqual(rows[1]["t"], 19.35)

    def test_timeline_uses_official_shot(self):
        stream = {"possessions": [{"possession_id": "P01", "offense_team": "HOU", "events": [
            {"t": 19.3, "type": "shot", "actor": {"team": "HOU", "number": "30", "name": "Capela"},
             "confidence": 0.95, "source": "cv",
             "official": {"made": True, "value": 2, "assist": {"team": "HOU", "number": "28", "name": "Sengun"},
                          "xfg": 0.62}},
            {"t": 5.0, "type": "turnover", "actor": {"team": "DAL", "number": "32", "name": "Flagg"},
             "confidence": 0.95, "source": "official"},
        ]}]}
        rows = common.timeline_from_stream(stream)
        self.assertEqual(rows[0]["label"], "Flagg 失误")
        self.assertEqual(rows[1]["label"], "Capela 2 分命中（Sengun 助攻） · xFG 62%")

    def test_timeline_from_stream_rejects_other_json(self):
        with self.assertRaises(ValueError):
            common.timeline_from_stream({"events": []})

if __name__ == "__main__":
    unittest.main()
