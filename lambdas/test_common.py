"""stdlib unittest：不连 AWS。"""

from __future__ import annotations

import unittest

import common


class CommonTest(unittest.TestCase):
    def test_extract_json_object_ignores_preamble(self):
        data = common.extract_json_object('好的\n{"events":[]}\n谢谢')
        self.assertEqual(data, {"events": []})

    def test_safe_filename_strips_path_and_spaces(self):
        self.assertEqual(common.safe_filename("../clips/my clip.mp4"), "my-clip.mp4")

    def test_object_key_uses_raw_prefix(self):
        key = common.object_key("abc", "clip.mp4")
        self.assertEqual(key, "raw/abc/clip.mp4")

    def test_normalize_events_clamps_unknown_type(self):
        events = common.normalize_events(
            {
                "events": [
                    {
                        "eventType": "dunk",
                        "timecode": "01:14",
                        "players": ["申京"],
                        "confidence": 1.4,
                        "label": "出手",
                        "detail": "",
                    }
                ]
            }
        )
        self.assertEqual(events[0]["eventType"], "hold")
        self.assertEqual(events[0]["confidence"], 1.0)
        self.assertEqual(events[0]["players"], ["申京"])


if __name__ == "__main__":
    unittest.main()
