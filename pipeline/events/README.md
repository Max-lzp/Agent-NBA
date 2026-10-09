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

## 评估（调参前先跑）

```bash
python -m pipeline.events.evaluate                         # 两段训练片，CV 来源 + 原始球掩码
python -m pipeline.events.evaluate --source qwen3_vl       # 换来源对比
python -m pipeline.events.evaluate --dump /tmp/ev          # 顺带写出结构化 JSON 抽查
```

真值在 `ground_truth/{clip}.json`。回合级来自记分牌进攻钟（可信），传球级待人工补。
`team_acc` 是按 0.1s 采样、进攻方判对的时间占比。

原始球掩码 `pipeline/vision/results/local/{clip}.ball_raw.json` 由
`pipeline/vision/.venv/bin/python pipeline/vision/ball_raw.py` 生成（约 1 分钟 / 两段，MPS）。

## 纪律

- 每个事件必须有 `confidence` 和 `source`；宁可不出，不许硬出。
- 英尺坐标只在 `calibration != "none"` 时填写。
- 判定阈值全部收在各模块头部的常量里，白天用训练视频调，晚上只改常量。

## 踩坑（10.9 晚）

- **Qwen3 VL 当球源不行**：顺延逻辑救不回来，球覆盖 4% / 11%，两段都只出 1 个回合、0 事件。球交给 CV，VLM 只管身份。
- **CV timeline 的球 100% 是假象**：`analyze.py` 对球做了 interpolate + bfill，真检测只有 38%（amen）/ 44%（flagg）。用 `ball_raw.json` 区分，插值帧降到 `INTERP_BALL_CONF`。
- **持球人换队 ≠ 换回合**：旧逻辑把 22 秒片切成 12–13 个回合。现在走 `possession_spans`：只有记分牌跳变或对方持续控球 ≥ `SWITCH_HOLD_S` 才换。
- **CV 队伍标签有噪声**：持球帧里队伍标签与球衣颜色复判不一致 amen 18/62、flagg 8/78（复判本身在重叠框上也不稳）。amen 开场把贴身的 HOU 防守人当进攻方，`team_acc` 卡在 59%——要靠融合层修，调阈值救不了。
- **别信 `results/local/*.json` 里的 `events`**：那是 vendor 检测器的输出，不是真值（amen 报 7.7s 抢断，进攻钟证明整段没换过球权）。
- **记分牌读进攻钟最准**：进攻钟不重置 = 没换球权；比赛钟最后两分钟停 = 刚进球。真值就是这么定的。
- `SWITCH_HOLD_S=2.5` 只在两段片上扫出来，考试视频上先复核。
