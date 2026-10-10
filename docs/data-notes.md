# 深度数据格式与对齐方案

## 已获取的官方深度数据与字段字典（2026-10-10）

NBA 主站 <https://www.nba.com/inside-the-game> 可访问，无需登录即可通过页面使用的 stats.nba.com 接口取得数据。门户给出的 china.nba.cn 地址当前返回 404，因此采用主站官方来源。已保存 data/deep 下的 5 张真实 CSV、原始 JSON 及 manifest.json（URL、UTC 抓取时间、双格式哈希）。

| 数据 | 官方接口 | 本次记录数 | 关键字段与口径 |
| --- | --- | ---: | --- |
| 出手难度 | shotqualityleaders | 125 | PLAYERID/PLAYERNAME/TEAMABBREVIATION；AVGSHOTQUALITY=预期 FG 概率；FGPCT=实际 FG 概率；FGPCTABOVEEXPECTED=实际减预期；FGA/FG2A/FG3A=跟踪出手次数 |
| 引力 | gravityleaders | 234 | AVGGRAVITYSCORE=高于预期的平均防守压力；ONBALL/OFFBALL、PERIMETER/INTERIOR 分项；FRAMES=计算覆盖帧数；GAMESPLAYED、MINUTES |
| 关键度 | leverageleaders | 234 | FULL、OFFENSE、DEFENSE、SHOOTING、CREATION、REBOUNDS、TURNOVERS、ONBALLDEF；保留 API 原始值，不把球员汇总当回合 WPA |
| 弗拉格单场出手 | shotqualityvideologs | 23 | gameId=0022500701、playerId=1642843、eventNum、period、gameClock、shotQuality、success、shotType、locX/locY、官方视频 URL |
| 阿门单场出手 | shotqualityvideologs | 18 | gameId=0022500491、playerId=1641708；字段同上 |

定义依据 <https://www.nba.com/news/inside-the-game-stat-glossary>：xFG 表示平均 NBA 球员在同等跟踪条件下的预期命中率，FG%+ 是实际减预期。接口单位为 0–1，展示时转百分比；125 行均核验守恒。Leverage 官网显示层会对 FULL 等列乘 10，CSV 保留原始值，不擅自定义胜率单位。引力不是百分比。locX/locY 原样留存，坐标原点、单位尚未核验，不用于画面映射。

两队在出手质量样本中 HOU 5 人、DAL 3 人，其余人员不补零。基准为这 125 人按跟踪 FGA 加权的 xFG=46.5813473%，不是完整联盟实际 FG%。已有真实模型 JSON/图表及 41 条单球叠加数据；5 个大残差候选不冒充高 Leverage 回合。两场官方 winprobabilitypbp 请求返回 500，WPA 和训练视频偏移未得到验证。

NBA 美国比赛日期 2026-01-31/01-03 对应训练视频北京日期 02-01/01-04。通过官方 GameID 与节次、比赛时钟匹配，不直接把 gameClock 当视频时间。

## 官方门户数据入口核查（2026-10-10）

### 后续 NBA Stats 拉取成功

当前 stats.nba.com 的 commonteamroster、leaguedashplayerstats、playercareerstats 已返回 HTTP 200，证据在 `pipeline/vision/rosters/sources/2025-26-official-sources.json`。前面的代理失败记录是历史状态，不再代表这些 Stats 请求的现状。

已核验 HOU 17 人、DAL 24 人的 ID/GP/GS/精确总分钟。字段口径：commonteamroster 的 PLAYER_ID、PLAYER、NUM、POSITION 用于名单快照；leaguedashplayerstats 使用 Regular Season + PerMode=Totals + TeamID，GP 为该队赛季出场，MIN 为赛季总分钟（小数）；该接口没有 GS，补查 playercareerstats 的 SeasonTotalsRegularSeason，按 SEASON_ID 和 TEAM_ID 取 GS。两队 GS 总和均为 410。快照和统计并集不能证明赛季登记历史完整，from/to 与历史号码仍待官方历史记录，不生成带猜测日期的正式名单。

Codex 已读取官方门户 `/portal` 页面及其页面资源。“NBA data downloads”在当前版本提供以下入口，而非直接提供 CSV 字段表：

- NBA 比赛页：<https://www.nba.com/games>
- 深度数据页：<https://china.nba.cn/inside-the-game>
- 两段高清训练视频：<https://comfile.csdnimg.cn/file/train_video.zip>

门户页面 HTTP 200；登录入口跳转 Amazon Cognito，当前代理拦截登录域名和 china.nba.cn，因此尚未完成账号登录或取得数据文件。api.nba.cn 根路径返回源站 HTML 403（标题 405），不同于代理隧道拒绝；这不能证明具体 NBA API 数据端点不可用。需要从深度数据页确认真实端点、参数和字段定义后请求，不猜测接口。

字段字典、官方数据复核与正式模型输出仍待实际文件。上列信息仅证明官方页面当前公布了哪些链接，不证明文件已下载、登录成功或数据字段已核验。账号密码和认证响应不存入仓库。

> 主人：B（视觉线）。别人可以提意见，只有主人改。
> 核心问题：官方数据只有出手瞬间点位、没有逐帧轨迹。

## 0. 视觉管线方案（10.7 定稿：开源组件 + 全本地推理）

**主干**：[abdullahtarek/basketball_analysis](https://github.com/abdullahtarek/basketball_analysis)（MIT）
检测+跟踪（预训练权重 Google Drive 下载即用）、球权/传球事件、球场关键点、画面叠加 drawers。
目录结构照搬为 `pipeline/vision/` 的模块划分。

**零件**：[roboflow/sports](https://github.com/roboflow/sports) + [官方篮球教程](https://roboflow.com/blog/identify-basketball-players)
- `ViewTransformer`：球场坐标 ↔ 画面像素单应变换（服务本文第 3 节）
- 球衣号码 OCR：[篮球号码数据集](https://universe.roboflow.com/roboflow-jvuqo/basketball-jersey-numbers-ocr)，本地推理
- 备选检测器 RF-DETR（Apache 2.0），在意 Ultralytics AGPL 时替换

**规则**：
- 数据集/权重只从网站**下载到本地**，Pipeline 零 Roboflow API 调用、零外部 token
- 比赛日识别全在本地 CPU 跑（22 秒 ≈ 几分钟），只把结构化 JSON 传 S3
- Qwen3 VL 只保留三件事：读记分牌时钟、判战术事件、兜底难帧
- Ultralytics 为 AGPL-3.0：可用，README 需注明
- 球员身份 = 球衣号码 OCR + 号码→花名册映射表（弗拉格 32 号 / 阿门 1 号，两队 roster 赛前整理）

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

## 7. 开源本地推理（2026-10-07）

按第 0 节跑了两段训练视频。检测和跟踪用 `abdullahtarek/basketball_analysis` 的三份 Google Drive 权重，在本机 Apple MPS 上推理，没有上云，也没有逐帧调 Qwen3 VL。采样目标 8 fps，源视频 59.94 fps，实际每 7 帧取 1 帧，约 8.56 fps。

| 片段 | 帧数 | 时长 | 每帧球员中位数 | 球场关键点 | 球检出 | 轨迹 | 本地事件 | 名单姓名 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flagg | 185 | 21.49s | 9 | 184/185 | 185/185 | 43 | 传球 3，抢断 2 | 0 |
| amen | 196 | 22.77s | 7 | 166/196 | 196/196 | 43 | 传球 3，抢断 3 | 0 |

两段合计墙钟约 303 秒。结构化结果在 `pipeline/vision/results/local/flagg.json` 和 `amen.json`。本地计算成本 $0。

权重在 `pipeline/vision/models/`，已 gitignore：

| 文件 | 体积 | 说明 |
| --- | --- | --- |
| player_detector.pt | 165MB | 类名 Ball、Clock、Hoop、Overlay、Player、Ref、Scoreboard |
| ball_detector_model.pt | 165MB | 类名与球员权重相同，文件哈希不同 |
| court_keypoint_detector.pt | 398MB | 球场关键点 |
| jersey_numbers.pt | 无 | 见下面号码一节 |

球员跟踪能跟上：弗拉格片段每帧大约 9 人，最长轨迹跨 156 帧。球的框在整段里从画面左侧移到右侧，不是钉在一个点上。上游 `remove_wrong_detections` 的 25 像素是按大约 24 fps 的相邻帧写的，8 fps 采样时按步长 7 放宽，否则真球会被删掉。

### 7.1 球衣号码没有跑起来

任务书要的是 Roboflow `basketball-jersey-numbers-ocr` 的本地权重。官方发布形态是 SmolVLM2 LoRA，模型 ID `basketball-jersey-numbers-ocr/3`，下载需要 `ROBOFLOW_API_KEY`。本机没有这个 key。Universe 页面返回 Cloudflare 403，`repo.roboflow.com/.../5/weights.pt` 返回 404。管线因此没有号码文件，运行时也没有调用 Roboflow，没有把任何 token 写进仓库。两段的 `jersey` 和 `name` 都是空。

### 7.2 球衣颜色不能单独当球队

HSV 规则是高明度、低饱和度算白，明度低于 110 算深色。抽查两张持球裁切，投票和画面相反：

| 片段 | 轨迹 | 时间 | HSV 投票 | 裁切上看到的 |
| --- | --- | --- | --- | --- |
| flagg | 14 | 2.803s | HOU / 白（V=153，S=78） | 深色 32 号，手里有球 |
| amen | 27 | 6.306s | DAL / 深（V=98，S=58） | 白色 17 号 |

深色球衣在场馆灯光下明度偏高，白球衣裁切又常被暗背景拉低。颜色只作参考，不能拿来盖号码。

### 7.3 Qwen 只兜了两张难帧

记分牌沿用第 6.2 节，6/6 正确，没有重读。战术事件用本地传球/抢断，没有逐帧问模型。号码权重缺失，所以每段只拿持球帧数最多的那条轨迹，裁一张人，各问一次 `qwen_roles.fallback_number`。

| 片段 | 模型输出 | 名单 | 美元 |
| --- | --- | --- | --- |
| flagg t=2.803s | jersey `"22"`，color `"white\|dark"` | 对不上。color 把提示里的两个选项原样写回了 | 0.000134 |
| amen t=6.306s | jersey `"17"`，color `white` | HOU Eason，和裁切一致 | 0.000125 |

两次合计 $0.0003。加上第 6.4 节的 $0.0237，视觉线累计 **$0.0240**。提示词已改成 color 只能填 white、dark 或 null，没有为这句话再打一次模型。原文在 `pipeline/vision/results/local/hard_frame.json`。

## 8. 回放台对照组件（2026-10-07）

回放台的首要用途是看准确度，不在页面里选型。同一段视频、同一个播放时间，切换组件就换成那一版的框。清单在 `pipeline/vision/components.json`。

| 组件 | 状态 | 本地看到的差别 |
| --- | --- | --- |
| [basketball_analysis](https://github.com/abdullahtarek/basketball_analysis) | 两段都重跑过，Ref 写进时间线 | 球员大约每帧 8.3 / 7.1 人。裁判大约每帧 2.6 / 2.4 人，弗拉格 183/185 帧、阿门 183/196 帧有裁判。7.12s 这一帧是 3 个灰虚线框。球仍是每帧都有。持球没有落到裁判轨迹上。传球/抢断变成弗拉格 3 个、阿门 2 个：跟踪现在只吃 Player 类，轨迹编号和上一版不同。 |
| [Ultralytics YOLOv8n COCO](https://github.com/ultralytics/ultralytics) | 两段都有结果，`*.yolov8n_coco.json` | 人框数量接近，但会多框到场边的人。没有裁判类。球几乎检不出来（弗拉格 185 帧里只有 1 帧有球）。没有传球/抢断。 |
| [RF-DETR](https://github.com/roboflow/rf-detr) | 还没跑 | 任务书里的备选检测器。权重还没下到本地，按钮是灰的。没有裁判类，先不跑。 |
| [E-BARD YOLOv8n](https://github.com/GabrieleGiudic/E-BARD) | 两段都有结果，`*.ebard_yolov8n.json` | 权重 `BODD_yolov8n_0001.pt`，类是 basketball / hoop / player / referee。裁判大约每帧 2.5 / 2.3 人，和上面接近。同一帧 7.12s 也是 3 个裁判。球只覆盖弗拉格 105/185 帧、阿门 114/196 帧，没有传球/抢断。用来对裁判，不替换现在的球。 |

球衣颜色不再用上身平均明度。深色球衣的白背心和浅色地板会把平均明度抬高，白球衣框进深色前景又会把明度压低。现在看下摆和短裤，而且按这一帧单独上色。跟踪串人时，一条轨迹不再被涂成同一个队。

号码不再等 Roboflow 权重。RapidOCR 对每条球员轨迹最多 8 张躯干裁切投票，至少两票（或一票且置信度不低于 0.95）才收下，而且只有对上出场名单才写姓名。basketball_analysis 弗拉格写出 Amen、Christie、Flagg、Klay、Sengun、Smith，185 帧里 182 帧至少有一个姓名；阿门写出 Amen、Capela、Christie、Davis、Durant、Eason、Flagg、Marshall、Smith。侧面和背面仍然是空的。E-BARD 用同一次投票，弗拉格还多认出 Gafford 和 Durant。没有再调用 Qwen。


## 2026-10-10 历史名单补查

基础统计扩充到 HOU 19 / DAL 25（44 条球员—球队记录），真实 ID/GP/GS/MIN 在 `pipeline/vision/rosters/sources/2025-26-verified-totals.csv`。已收集两队 160 场去重常规赛的名单与 inactive 观察，并保留官方交易和位置响应。观察日期不能替代登记日期，当前 profile 不能替代历史号码；正式名单仍受号码冲突和完整登记区间核验阻塞。复现与逐项说明见 [roster-history-notes.md](roster-history-notes.md)。模型扩展依用户指示暂停。

## 2026-10-10 全联盟投篮三段、效率、选秀与身体资料

新增 `data/season/2025-26/`，NBA 官方接口 582 名常规赛出场球员，两队球队过滤统计 HOU 16 / DAL 23，逐人核对上传 39 人表无遗漏。三段可视化使用“三分／其余两分／Restricted Area 近框”，同时保留完整两分与纯中距离命中率、FGM/FGA、七个原始区域与在场 OFF/DEF/NET Rating。两队真实逐次出手坐标与完整赛季出手数已核对。

身高、选秀、位置来自官方资料快照；26 年官方试训能核验 363 人臂展，219 人缺失留空。原上传号码和五位置、团内担当类比均单独标明未核验/主观，不移入正式名单。Caleb Martin 臂展修订采用官方测量，审核结果在 user_submission_audit.json。

已有 5 张自绘 PNG、离线交互页面及 6 张 NBA 官方图表参考截图，原始接口/URL/UTC 抓取时间/SHA-256 一并保留。详细字段、公式、缺失值与展示建议见 `data/season/2025-26/README.md`。七类几何战术候选 demo 在 `pipeline/vision/tactics_demo/`，只读旁路，合成示例与真实比赛数据分开，尚未验证真实视频识别准确率。

### 防守三段补充

已取得全联盟及两队 leaguedashptdefend 六种官方分类，派生“≥6ft 其余两分”。全联盟 582 人全部保留，580 人有 Overall 追踪；HOU 16 / DAL 23 的六种分类均找到记录。定义、样本和方向见 `data/season/2025-26/DEFENSE-NOTES.md`。防守近框 <6ft 不等于 Restricted Area；额外提供完整联盟按距离分段的进攻数据，不混用不同定义。全联盟逐球 API 有 102400 行上限，改为赛季月份 1–7 采集并逐人核对 FGM/FGA，不能把静默截断当完整赛季。
