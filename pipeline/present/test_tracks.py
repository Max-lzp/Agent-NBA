"""音轨时间对齐。python3 -m unittest pipeline.present.test_tracks"""
from __future__ import annotations

import unittest

from pipeline.present.tracks import (
    playback_rate, polish_zh, prefer_broadcast, prepare_sentences, sentences_from_items,
    transcribe_job_name, voice_filter,
)


class TrackTextTests(unittest.TestCase):
    def test_sentences_keep_timestamps(self):
        items = [
            {"type": "pronunciation", "start_time": "1.20", "end_time": "1.50",
             "alternatives": [{"content": "Flagg"}]},
            {"type": "pronunciation", "start_time": "1.50", "end_time": "1.90",
             "alternatives": [{"content": "scores"}]},
            {"type": "punctuation", "alternatives": [{"content": "."}]},
            {"type": "pronunciation", "start_time": "2.40", "end_time": "2.80",
             "alternatives": [{"content": "Rockets"}]},
        ]
        got = sentences_from_items(items)
        self.assertEqual(got[0]["start"], 1.2)
        self.assertEqual(got[0]["end"], 1.9)
        self.assertEqual(got[0]["text"], "Flagg scores")
        self.assertEqual(got[1]["text"], "Rockets")

    def test_prepares_broadcast_wording(self):
        items = [
            {"type": "pronunciation", "start_time": "0.03", "end_time": "0.40",
             "alternatives": [{"content": "Smith"}]},
            {"type": "pronunciation", "start_time": "3.28", "end_time": "3.50",
             "alternatives": [{"content": "Flagg"}]},
            {"type": "pronunciation", "start_time": "3.60", "end_time": "4.00",
             "alternatives": [{"content": "cooking"}]},
            {"type": "punctuation", "alternatives": [{"content": "."}]},
        ]
        got = prepare_sentences(items)
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0]["start"], 3.28)
        self.assertIn("Flagg", got[0]["text"])
        self.assertIn("playing very well", got[0]["text"])
        self.assertNotIn("Smith", got[0]["text"])

    def test_transcribe_job_name_drops_characters_aws_rejects(self):
        self.assertEqual(transcribe_job_name("exam/1", 10), "deepcourt-exam-1-10")

    def test_known_call_uses_broadcast_wording(self):
        line = prefer_broadcast(
            "Flagg with five seconds left on the shot clock, drives to the basket left-handed layup goes in.",
            "弗拉格在驱动器里。",
        )
        self.assertIn("左手上篮打进", line)
        self.assertNotIn("驱动器", line)

    def test_names_match_the_page(self):
        text = polish_zh("库珀·弗拉格面对达拉斯小牛，凯文·杜兰特把球给了阿门·汤普森。")
        self.assertIn("弗拉格", text)
        self.assertNotIn("库珀", text)
        self.assertIn("独行侠", text)
        self.assertNotIn("小牛", text)
        self.assertIn("杜兰特", text)
        self.assertIn("阿门", text)
        self.assertNotIn("汤普森", text)

    def test_rate_only_speeds_up_when_the_line_is_longer_than_the_clip(self):
        self.assertEqual(playback_rate(5, 8), 1.0)
        self.assertAlmostEqual(playback_rate(10, 8), 1.25)
        self.assertAlmostEqual(playback_rate(20, 8), 1.35)

    def test_filter_pads_to_the_picture(self):
        filt = voice_filter(0.8, 1.1, 21.548)
        self.assertIn("atempo=1.1000", filt)
        self.assertIn("adelay=800|800", filt)
        self.assertIn("atrim=end=21.548", filt)


if __name__ == "__main__":
    unittest.main()
