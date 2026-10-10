# pipeline/events —— 事件流引擎

把「逐帧观测」变成「结构化事件流文件」。这是全队的中间合同：上游（vision 追踪 / VLM 降级）只负责产出归一化观测，下游（enrich、战术、渲染、解说）只读这份 JSON。

## 模块

| 文件 | 职责 |
| --- | --- |
| `schema.py` | 结构化文件的 Python 表达（dataclass），与 `event_stream.schema.json` 一一对应 |
| `event_stream.schema.json` | JSON Schema，冻结后改动要全队同步 |
| `geometry.py` | 单应标定（像素 → 英尺）、距离、出手区域 |
| `possession.py` | 持球判定：逐帧持球人 + 防抖 + 持球段 |
| `passes.py` | 持球权转移 → 传球 / 失误事件 |
| `shots.py` | 出手检测：球轨迹 + 记分牌跳变交叉；防守距离、出手区域 |
| `screens.py` | 掩护几何谓词：screen_set / screen_use 候选（战术识别的地基） |
| `build_stream.py` | 总装：观测 → 结构化 JSON，CLI 入口 |

## 输入归一化

两种来源都转成 `possession.FrameObs`（逐帧：球员位置 + 球位置 + 标定状态）：

- `full` 模式：ByteTrack 追踪输出（30fps，像素坐标 + track_id）
- `vlm` 模式：P0 降级链的帧级 JSON（`pipeline/vision/results/json/*.json`，3fps，身份已由 Qwen3 VL + roster 给出）

## 用法

```bash
cd team-repo
python -m pipeline.events.build_stream --job-id test01 --mode vlm \
  --obs pipeline/vision/results/json/amen.json \
  --frame-map pipeline/vision/results/frame_map_amen.json \
  --out structured_test01.json
```

## 纪律

- 每个事件必须有 `confidence` 和 `source`；宁可不出，不许硬出。
- 英尺坐标只在 `calibration != "none"` 时填写。
- 判定阈值全部收在各模块头部的常量里，白天用训练视频调，晚上只改常量。
