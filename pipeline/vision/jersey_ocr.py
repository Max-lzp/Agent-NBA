"""本地读取球衣号码。

权重来自 Roboflow basketball-jersey-numbers-ocr 数据集训练出的 YOLO，
类名是单个数字（0-9）或完整号码。推理只读本地 .pt，不调用 Roboflow API。
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from team_color import torso_crop


class JerseyNumberReader:
    def __init__(self, weights):
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.names = {int(k): str(v) for k, v in self.model.names.items()}

    def read_crop(self, crop: np.ndarray) -> tuple[str | None, float]:
        if crop is None or crop.size == 0:
            return None, 0.0
        result = self.model.predict(crop, conf=0.25, verbose=False)[0]
        if result.boxes is None or len(result.boxes) == 0:
            return None, 0.0
        digits = []
        full_numbers = []
        for box in result.boxes:
            cls_id = int(box.cls.item())
            conf = float(box.conf.item())
            label = self.names.get(cls_id, "")
            x1 = float(box.xyxy[0][0].item())
            if label.isdigit() and len(label) == 1:
                digits.append((x1, label, conf))
            elif label.isdigit():
                full_numbers.append((label, conf))
        if full_numbers:
            label, conf = max(full_numbers, key=lambda item: item[1])
            return label, conf
        if not digits:
            return None, 0.0
        digits.sort(key=lambda item: item[0])
        # 两位数球衣。多于两个数字时只留置信度最高的两个，再按从左到右拼。
        if len(digits) > 2:
            digits = sorted(digits, key=lambda item: item[2], reverse=True)[:2]
            digits.sort(key=lambda item: item[0])
        text = "".join(item[1] for item in digits)
        conf = sum(item[2] for item in digits) / len(digits)
        if text == "0":
            return "0", conf
        return text.lstrip("0") or "0", conf

    def vote(self, frame_crops: list[np.ndarray]) -> tuple[str | None, float, int]:
        votes: Counter[str] = Counter()
        confs: dict[str, list[float]] = {}
        for crop in frame_crops:
            number, conf = self.read_crop(crop)
            if not number:
                continue
            votes[number] += 1
            confs.setdefault(number, []).append(conf)
        if not votes:
            return None, 0.0, 0
        number, count = votes.most_common(1)[0]
        mean_conf = sum(confs[number]) / len(confs[number])
        return number, mean_conf, count


def number_crops(frames: list[np.ndarray], bboxes: list[list[float]], limit: int = 8) -> list[np.ndarray]:
    ranked = []
    for frame, bbox in zip(frames, bboxes):
        height = bbox[3] - bbox[1]
        if height < 70:
            continue
        crop = torso_crop(frame, bbox)
        if crop is not None:
            ranked.append((height, crop))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [crop for _, crop in ranked[:limit]]
