"""空地摆卡：左右半场、躲开球员、单帧要快。"""
from __future__ import annotations

import time
import unittest

import numpy as np
from PIL import Image

import card_layout


def _hit(a, b, pad=0):
    return a[0] < b[0] + b[2] + pad and b[0] < a[0] + a[2] + pad and a[1] < b[1] + b[3] + pad and b[1] < a[1] + a[3] + pad


class PlaceCardsTest(unittest.TestCase):
    def test_sides_avoid_players_and_each_other(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[:, :640] = (70, 90, 110)
        frame[:, 640:] = (40, 70, 48)
        player = (900, 200, 120, 280)
        frame[200:480, 900:1020] = np.random.default_rng(0).integers(0, 255, (280, 120, 3), dtype=np.uint8)
        cards = [{"id": "offense", "w": 300, "h": 160}, {"id": "defense", "w": 280, "h": 150}]
        placed = card_layout.place_cards(frame, [player], cards, {"offense": "left", "defense": "right"})
        self.assertEqual([item["side"] for item in placed], ["left", "right"])
        self.assertLess(placed[0]["x"] + placed[0]["w"], 640)
        self.assertGreaterEqual(placed[1]["x"], 640)
        self.assertFalse(_hit((placed[0]["x"], placed[0]["y"], placed[0]["w"], placed[0]["h"]), (placed[1]["x"], placed[1]["y"], placed[1]["w"], placed[1]["h"])))
        defense = (placed[1]["x"], placed[1]["y"], placed[1]["w"], placed[1]["h"])
        self.assertFalse(_hit(defense, player, pad=8))

    def test_fallback_corner_when_floor_is_blocked(self):
        frame = np.full((720, 1280, 3), 20, dtype=np.uint8)
        boxes = [(0, 80, 1280, 640)]
        placed = card_layout.place_cards(
            frame, boxes,
            [{"id": "offense", "w": 400, "h": 220}, {"id": "defense", "w": 400, "h": 220}],
            {"offense": "left", "defense": "right"},
        )
        self.assertLess(placed[0]["y"], 80)
        self.assertLess(placed[1]["y"], 80)
        self.assertLess(placed[0]["x"], 200)
        self.assertGreater(placed[1]["x"], 600)

    def test_basket_side_follows_orange_rim(self):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[40:70, 500:560] = (220, 110, 30)
        self.assertEqual(card_layout.basket_side(frame), "right")
        frame[40:70, 500:560] = 0
        frame[40:70, 40:100] = (220, 110, 30)
        self.assertEqual(card_layout.basket_side(frame), "left")
        self.assertIsNone(card_layout.basket_side(np.zeros((360, 640, 3), dtype=np.uint8)))

    def test_one_frame_under_200ms(self):
        rng = np.random.default_rng(1)
        frame = rng.integers(0, 40, (720, 1280, 3), dtype=np.uint8)
        frame[200:520, 160:1100] = (90, 80, 60)
        started = time.perf_counter()
        card_layout.place_cards(
            Image.fromarray(frame),
            [(400, 300, 80, 160), (700, 280, 90, 180)],
            [{"id": "offense", "w": 360, "h": 200}, {"id": "defense", "w": 360, "h": 220}],
            {"offense": "right", "defense": "left"},
        )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.2, elapsed)


if __name__ == "__main__":
    unittest.main()
