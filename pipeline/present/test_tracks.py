"""音轨时间对齐。python3 -m unittest pipeline.present.test_tracks"""
from __future__ import annotations

import csv
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from pipeline.present import tracks
from pipeline.present.tracks import (
    boto_translate_chunk, cli_translate_chunk, playback_rate, polish_zh, prefer_broadcast,
    prepare_sentences, reset_terminology_fallback, sentences_from_items,
    transcribe_job_name, voice_filter,
)

REPO = Path(__file__).resolve().parents[2]


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


class _TranslateError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class TerminologyTests(unittest.TestCase):
    def setUp(self):
        reset_terminology_fallback()
        self._saved = os.environ.get("TRANSLATE_TERMINOLOGY")
        os.environ.pop("TRANSLATE_TERMINOLOGY", None)

    def tearDown(self):
        reset_terminology_fallback()
        if self._saved is None:
            os.environ.pop("TRANSLATE_TERMINOLOGY", None)
        else:
            os.environ["TRANSLATE_TERMINOLOGY"] = self._saved

    def test_without_env_the_boto_call_stays_the_same(self):
        client = mock.Mock()
        client.translate_text.return_value = {"TranslatedText": "挡拆"}
        got = boto_translate_chunk(client, "pick and roll")
        self.assertEqual(got, "挡拆")
        client.translate_text.assert_called_once_with(
            Text="pick and roll", SourceLanguageCode="en", TargetLanguageCode="zh",
        )

    def test_blank_env_is_the_same_as_unset(self):
        os.environ["TRANSLATE_TERMINOLOGY"] = "   "
        client = mock.Mock()
        client.translate_text.return_value = {"TranslatedText": "快攻"}
        boto_translate_chunk(client, "fast break")
        self.assertNotIn("TerminologyNames", client.translate_text.call_args.kwargs)

    def test_env_adds_terminology_names(self):
        os.environ["TRANSLATE_TERMINOLOGY"] = "deepcourt-basketball"
        client = mock.Mock()
        client.translate_text.return_value = {"TranslatedText": "挡拆"}
        boto_translate_chunk(client, "pick and roll")
        client.translate_text.assert_called_once_with(
            Text="pick and roll",
            SourceLanguageCode="en",
            TargetLanguageCode="zh",
            TerminologyNames=["deepcourt-basketball"],
        )

    def test_missing_terminology_retries_once_without_it(self):
        os.environ["TRANSLATE_TERMINOLOGY"] = "deepcourt-basketball"
        client = mock.Mock()
        client.translate_text.side_effect = [
            _TranslateError("ResourceNotFoundException", "The terminology deepcourt-basketball is not found."),
            {"TranslatedText": "篮板"},
        ]
        got = boto_translate_chunk(client, "rebound")
        self.assertEqual(got, "篮板")
        self.assertEqual(client.translate_text.call_count, 2)
        self.assertNotIn("TerminologyNames", client.translate_text.call_args_list[1].kwargs)
        client.translate_text.reset_mock()
        client.translate_text.side_effect = None
        client.translate_text.return_value = {"TranslatedText": "失误"}
        boto_translate_chunk(client, "turnover")
        self.assertNotIn("TerminologyNames", client.translate_text.call_args.kwargs)

    def test_access_denied_retries_without_terminology(self):
        os.environ["TRANSLATE_TERMINOLOGY"] = "deepcourt-basketball"
        client = mock.Mock()
        client.translate_text.side_effect = [
            _TranslateError("AccessDeniedException", "User is not authorized to perform translate:TranslateText"),
            {"TranslatedText": "罚球"},
        ]
        self.assertEqual(boto_translate_chunk(client, "free throw"), "罚球")
        self.assertEqual(client.translate_text.call_count, 2)
        self.assertNotIn("TerminologyNames", client.translate_text.call_args_list[1].kwargs)

    def test_unrelated_translate_error_is_not_retried(self):
        os.environ["TRANSLATE_TERMINOLOGY"] = "deepcourt-basketball"
        client = mock.Mock()
        client.translate_text.side_effect = _TranslateError("ThrottlingException", "Rate exceeded")
        with self.assertRaises(_TranslateError):
            boto_translate_chunk(client, "layup")
        self.assertEqual(client.translate_text.call_count, 1)

    def test_cli_without_env_keeps_the_old_arguments(self):
        with mock.patch.object(tracks, "_aws", return_value='{"TranslatedText":"上篮"}') as aws:
            self.assertEqual(cli_translate_chunk("layup"), "上篮")
        aws.assert_called_once_with([
            "translate", "translate-text",
            "--source-language-code", "en",
            "--target-language-code", "zh",
            "--text", "layup",
        ])

    def test_cli_drops_a_missing_terminology_and_retries(self):
        os.environ["TRANSLATE_TERMINOLOGY"] = "deepcourt-basketball"
        calls = []

        def fake_aws(args, json_out=True):
            calls.append(list(args))
            if "--terminology-names" in args:
                raise SystemExit(
                    "An error occurred (ResourceNotFoundException) when calling the TranslateText operation: "
                    "terminology deepcourt-basketball not found."
                )
            return '{"TranslatedText":"进攻时间"}'

        with mock.patch.object(tracks, "_aws", fake_aws):
            self.assertEqual(cli_translate_chunk("shot clock"), "进攻时间")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][calls[0].index("--terminology-names") + 1], "deepcourt-basketball")
        self.assertNotIn("--terminology-names", calls[1])

    def test_glossary_matches_the_roster_names(self):
        with (REPO / "data/terminology/basketball-en-zh.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ["en", "zh"])
        body = rows[1:]
        keys = [row[0].casefold() for row in body]
        self.assertEqual(len(keys), len(set(keys)))
        glossary = {en: zh for en, zh in body}
        self.assertEqual(glossary["pick and roll"], "挡拆")
        self.assertEqual(glossary["shot clock"], "进攻时间")
        self.assertEqual(glossary["and-one"], "打成 2+1")
        self.assertEqual(glossary["Houston Rockets"], "休斯顿火箭队")
        self.assertEqual(glossary["Dallas Mavericks"], "达拉斯独行侠队")
        names = json.loads((REPO / "data/names/zh-names.json").read_text(encoding="utf-8"))
        with (REPO / "data/season/2025-26/priority_team_profiles_reviewed.csv").open(encoding="utf-8", newline="") as handle:
            roster = list(csv.DictReader(handle))
        self.assertEqual(
            {row["player_id"] for row in roster},
            {player["player_id"] for player in names["players"]},
        )
        for player in names["players"]:
            self.assertEqual(glossary[player["name_en"]], player["name_zh"])
            self.assertFalse(player["china_nba_cn_verified"])
        self.assertTrue(all(team["china_nba_cn_verified"] for team in names["teams"]))


if __name__ == "__main__":
    unittest.main()
