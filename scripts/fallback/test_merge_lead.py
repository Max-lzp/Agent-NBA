"""python3 -m unittest scripts.fallback.test_merge_lead"""

from __future__ import annotations

import unittest

from scripts.fallback.merge_lead import merge_text, parse_clips_js


class MergeLeadTest(unittest.TestCase):
    def test_keeps_other_clips_and_sets_lead(self):
        local = {"exam": {"video": "assets/exam-broadcast.mp4", "title": "考试片"}, "flagg": {"video": "old"}}
        remote = {"flagg": {"video": "assets/flagg-broadcast.mp4"}, "amen": {"video": "assets/amen-broadcast.mp4"}}
        text = merge_text("exam", local, remote)
        data = parse_clips_js(text)
        self.assertTrue(text.startswith('window.DEEPCOURT_LEAD="exam";\n'))
        self.assertEqual(data["exam"]["video"], "assets/exam-broadcast.mp4")
        self.assertEqual(data["flagg"]["video"], "assets/flagg-broadcast.mp4")
        self.assertIn("amen", data)

    def test_rejects_a_clip_name_that_would_break_the_script(self):
        with self.assertRaises(ValueError):
            merge_text('exam";alert(1)', {"exam": {}}, {})

    def test_missing_local_clip(self):
        with self.assertRaises(ValueError):
            merge_text("exam", {"flagg": {}}, {})


if __name__ == "__main__":
    unittest.main()
