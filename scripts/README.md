# 数据获取与评测脚本（team04）

从 NBA 官网抓官方事件录像、建带标准答案的号码数据集、生成官方事件邻接表。
数据产物（`../data/2025-26/clips` ~2GB、`frames` ~1GB）**不进 git**，
用下面脚本可随时重建；工作区根目录 = 本仓库的上一级。

| 脚本 | 作用 | 产物 |
|---|---|---|
| `fetch_clips.py` | 批量下载 shotqualityshotlog 里的官方 mp4（带全套 Chrome 请求头绕 CDN 软拦截，下载后自动校验时长） | `../data/2025-26/clips/{gameId}_{eventNum}.mp4` + `_manifest_*.json` |
| `extract_number_frames.py` | 截帧（60fps 源按 2fps 抽）+ 官方背号标签 | `../data/2025-26/frames/` + `_labels.jsonl` |
| `build_clip_events.py` | 每个投篮剪辑挂时间窗内官方非投篮事件（篮板/抢断/失误/犯规/盖帽，带 actor 背号） | `../data/2025-26/clips/_clip_events.json` |

踩坑全记录在 [录像下载-方法与教训.md](录像下载-方法与教训.md)（占位视频、请求头、60fps 等）。
号码识别基线评测在 `../pipeline/vision/eval_number_dataset.py`，Roboflow 对照在 `../pipeline/vision/roboflow_compare.py`。
