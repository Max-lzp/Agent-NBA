# 深度数据格式与对齐方案

> 主人：B（视觉线）｜核心问题：官方数据只有出手瞬间点位、没有逐帧轨迹

## 1. 数据获取记录

| 项 | 内容 |
| --- | --- |
| 来源 | Portal → NBA data downloads（下载时间、文件名记入此表） |
| 官方字段 | 投篮难度 xFG%、球员引力（持球/无球）、关键度、出手点位坐标 |
| 格式 | ⬜ 待确认：JSON / CSV？逐回合还是逐出手？坐标系（球场 x/y 还是画面像素）？ |

> ⚠️ 下载到第一份真实数据后，第一时间把字段表原样贴进本文档——整个对齐方案都靠它设计。

## 2. 数据 ↔ 视频时间对齐（隐藏难点，最高优先级）

数据按比赛时钟记录，视频是独立文件。**对错了，数据准确性 30 分直接崩。**

候选方案：

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| A. 画面记分牌 OCR | Qwen3 VL 读转播画面里的比赛时钟，反推每个出手事件的视频时间点 | ⭐ 首选：不依赖数据格式细节，Agent 能力内 |
| B. 事件序列匹配 | 按出手顺序排列，结合画面事件检测首尾对齐 | 备选：累计误差风险 |
| C. 数据自带时间戳 | 若官方数据带视频偏移量 | 最优，等格式确认 |

## 3. 画面坐标系映射

数据点位（球场坐标）→ 画面像素位置：需要单应矩阵（homography）。直播机位固定，可用四分线/三分线等球场标记点标定一次后复用。

- [ ] 标定方法与参数记录

## 4. 二次加工指标（决赛「实现深度」18 分的素材）

自定义衍生指标草案：

- 决策质量分 = f(xFG%, 引力吸引的防守人数, 剩余进攻时间)
- 防守收缩速度 = 引力变化率
- [ ] 数学定义待推敲，定稿后记入 decisions.md

## 5. 验证清单

- [ ] 训练视频 A（弗拉格 2月1日，22s）跑通识别 + 对齐
- [ ] 训练视频 B（阿门 1月4日，23s）跑通识别 + 对齐
- [ ] 与官方 Inside the Game 页面对照同一场次数据，抽查一致性

## 6. 视觉步骤 1–3（2026-10-07）

只做了任务书的抽帧、单帧摸底、JSON prompt 定型。没有跑全量事件流，也没有做时间对齐。

### 6.1 抽帧

`ffmpeg -vf fps=3,showinfo`，文件名是时间戳 `t_00005.000.png`（秒，三位小数）。帧号 → 秒的映射在 `pipeline/vision/results/frame_map_flagg.json` 和 `frame_map_amen.json`，`pts_source=showinfo.pts_time`。PNG 在 `pipeline/vision/work/frames/`，已 gitignore，用 `python3 pipeline/vision/extract_frames.py` 复现。

| 片段 | 比赛 | 时长 | 帧数 | PNG 体积 |
| --- | --- | --- | --- | --- |
| flagg | 2026-02-01 DAL @ HOU，终场 107–111 | 21.55s | 65（0.000–21.333s） | 2.3–2.8MB |
| amen | 2026-01-04 HOU @ DAL，终场 110–104 | 22.89s | 69（0.000–22.667s） | 1.4–3.2MB，中位 2.9MB |

成本：$0。本地 ffmpeg，不占 Bedrock 额度。

### 6.2 单帧摸底

每段在去掉片头片尾 1 秒后的 20% / 50% / 80% 处取 1 帧，共 6 帧。不给名单，问球员、号码、动作和记分牌。模型 `qwen.qwen3-vl-235b-a22b`，`aws bedrock-runtime converse --profile nba --region us-east-1`。

| 片段 | 时间 | 记分牌 | 认出名了吗 | 号码 |
| --- | --- | --- | --- | --- |
| flagg | 5.000s | 对：4th 1:46，DAL 103–HOU 107 | 全是「未知」，没有 Flagg | 持球人被说成 DAL #00，不可用 |
| flagg | 10.667s | 对：4th 1:41，103–107，进攻时限 10 | 没有姓名 | #31、#28 能对上 Klay、Sengun 的号码 |
| flagg | 16.333s | 对：4th 1:35，103–107 | 没有姓名。画面里白色 #1 是 Amen | #21、#31、#1、#28 号码大体对 |
| amen | 5.000s | 对：3rd 7:40，HOU 60–DAL 63 | 读到球衣背字「Thompson」（#1 火箭）。不是靠脸 | #30 被说成独行侠 Carter，球队和姓名都错 |
| amen | 11.333s | 对：3rd 7:33，60–63 | 没有 Amen / Flagg。白色 #10 被叫成 Smith Jr.，但球队写成了独行侠 | 主客队球衣颜色整帧对调 |
| amen | 17.667s | 对：3rd 7:27，60–63 | #1 和 #32 都是「未知」。没有叫出 Amen 或 Flagg | #1 火箭、#32 独行侠、#10、#30 号码对 |

结论：不给名单时认不出 Flagg。Amen 只在球衣背面印着 Thompson 的那一帧被读到一次。记分牌 6/6 正确。身份不能直接信名字。

名单提示（只补了上面两帧里号码已经能读清的那两帧，没有把 6 帧重跑）：

| 片段 | 时间 | 加上本场名单之后 |
| --- | --- | --- |
| flagg | 16.333s | 写出 Gafford、Klay、Sengun、Amen 等。持球人写成 HOU #28 Sengun |
| amen | 17.667s | 写出 HOU #1 Amen 跳投、DAL #32 Flagg。与画面一致 |

名单因此是必要的。同号码必须看球衣颜色：#1 Amen / Hardy，#7 Durant / Powell，#10 Smith / Williams，#30 Capela / Cisse，#32 Flagg / Green。两场 Green 和 Hardy 都 DNP。名单在 `pipeline/vision/roster.json`，来源是 ESPN 当场 box score。

### 6.3 定型 prompt

系统提示在 `pipeline/vision/prompts/json_system.txt`：只输出一个 JSON 对象。用户消息带 `frame_t`、主客队和本场出场名单。`roster_map.confirm_document` 用号码 + 球队覆盖 `name`，不把模型自己写的名字当最终身份。

相对任务书初版，多了 `jersey`、`home_team`、`away_team`。`bbox` 仍是 `[x, y, w, h]`。两次实测模型写的是左上角和右下角，确认步骤里把它收成宽高（`bbox_raw` 保留原文）。

用定型 prompt 各验 1 帧，原文都是纯 JSON，没有围栏：

- amen 17.667s：记分牌对；`ball_handler=Amen`，事件 `shot`。名单确认后 Amen、Flagg、Smith、Capela 与画面一致。
- flagg 16.333s：记分牌对。同一帧的散文摸底说持球人是 Sengun，这次 JSON 写成 Flagg 出手。单帧持球人还不稳，步骤 4 要用前后帧交叉，不要单帧定出手。

### 6.4 成本

标价：Bedrock On-Demand、US East、Qwen3 VL 235B A22B，输入 $0.53 / 1M token，输出 $2.66 / 1M token（2026-10-07 价目页）。未传 service tier。费用 = 响应里的 `usage` × 标价。

| 调用 | 次数 | 输入 token | 输出 token | 美元 |
| --- | --- | --- | --- | --- |
| 摸底，无名单 | 6 | 13138 | 1142 | 0.0100 |
| 摸底，带名单 | 2 | 4968 | 395 | 0.0037 |
| JSON 第一次试跑（响应文件已被第二次覆盖） | 2 | — | 423+317 | 0.0049 |
| JSON 定型验证（当前 `results/json/`） | 2 | 5858 | 741 | 0.0051 |
| 超限失败的 1 次 PNG 请求 | 1 | 无 usage | — | 0 |
| 合计 | 13 | | | **0.0237** |

单帧输入大约 2200 token（无名单）到 2900 token（带名单的 JSON）。模型侧延迟大约 3–16 秒。2.6MB PNG 经 CLI 上传时墙钟曾到 100 秒；转成约 0.5MB JPEG 后墙钟降到 8–20 秒。

原文在 `pipeline/vision/results/probe/`、`probe-roster/`、`json/`。

