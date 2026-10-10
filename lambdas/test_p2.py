import unittest

from commentary.lines import two_sentences
from kuaishou.urls import build_connect_url


class CommentaryLineTests(unittest.TestCase):
    def test_keeps_two_sentences(self):
        text = two_sentences("持球人停在弧顶。协防慢了半步。这句不该留下。")
        self.assertEqual(text, "持球人停在弧顶。协防慢了半步。")

    def test_empty(self):
        self.assertEqual(two_sentences("   "), "")


class KuaishouUrlTests(unittest.TestCase):
    def test_connect_url_has_no_secret(self):
        url = build_connect_url("app-demo", "https://example.com/callback", "state-1")
        self.assertTrue(url.startswith("https://open.kuaishou.com/oauth2/connect?"))
        self.assertIn("app_id=app-demo", url)
        self.assertIn("user_video_publish", url)
        self.assertNotIn("app_secret", url)


if __name__ == "__main__":
    unittest.main()
