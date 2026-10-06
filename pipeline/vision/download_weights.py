#!/usr/bin/env python3
"""把预训练权重下载到 pipeline/vision/models/。只在准备机器时跑一次。

检测跟踪三份权重来自 abdullahtarek/basketball_analysis 的 Google Drive。
球衣号码权重是 Roboflow basketball-jersey-numbers-ocr 的本地导出文件。
运行时不访问 Roboflow API，也不读取任何 token。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

VISION = Path(__file__).resolve().parent
MODELS = VISION / "models"

DETECTORS = {
    "player_detector.pt": "1fVBLZtPy9Yu6Tf186oS4siotkioHBLHy",
    "ball_detector_model.pt": "1KejdrcEnto2AKjdgdo1U1syr5gODp6EL",
    "court_keypoint_detector.pt": "1nGoG-pUkSg4bWAUIeQ8aN6n7O1fOkXU0",
}

# 数据集页：https://universe.roboflow.com/roboflow-jvuqo/basketball-jersey-numbers-ocr
# 把该数据集训练出的 YOLO 权重复制为这个文件名。不要把 API key 写进仓库。
JERSEY_NAME = "jersey_numbers.pt"


def gdown(file_id: str, dest: Path) -> None:
    exe = shutil.which("gdown") or str(Path(sys.executable).parent / "gdown")
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([exe, f"https://drive.google.com/uc?id={file_id}", "-O", str(dest)], check=True)


def main() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    for name, file_id in DETECTORS.items():
        dest = MODELS / name
        if dest.is_file() and dest.stat().st_size > 1_000_000:
            print(f"已有 {dest.name} ({dest.stat().st_size} bytes)")
            continue
        print(f"下载 {name}")
        gdown(file_id, dest)
        print(f"  {dest.stat().st_size} bytes")
    jersey = MODELS / JERSEY_NAME
    if jersey.is_file():
        print(f"已有 {jersey.name} ({jersey.stat().st_size} bytes)")
    else:
        print(
            f"还没有 {jersey}。请把 Roboflow basketball-jersey-numbers-ocr "
            "的本地 YOLO 权重复制到这个路径后再跑号码识别。"
        )


if __name__ == "__main__":
    main()
