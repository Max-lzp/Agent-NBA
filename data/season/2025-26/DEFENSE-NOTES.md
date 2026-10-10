# 防守三区：有哪些可与进攻对应的数据

NBA 官方 Defensive Dashboard 接口为 `leaguedashptdefend`。本次收集 2025–26 常规赛、NBA/HOU/DAL 三个 scope 的六类追踪数据：Overall、Less Than 6Ft、Less Than 10Ft、2 Pointers、3 Pointers、Greater Than 15Ft。

## 可直接做图的三个区域

| 画面区域 | 防守统计 | 进攻对照 | 是否与原进攻 RA 版本相同 |
|---|---|---|---|
| 外线三分 | 对手三分命中数/防守三分出手数 | 本人三分 FGM/FGA | 三分定义对应，但追踪与全部出手覆盖不同 |
| 中间区域 | 两分防守计数减去 <6ft 防守计数，再计算对手 FG% | 本人其余两分 ≥6ft 的命中率 | 不是纯 Mid-Range，也不是原来的“其余两分减 RA” |
| 近框 | Less Than 6Ft 对手命中率 | 本人 <6ft 两分命中率 | **不等于 Restricted Area**；不能把两个近框值当同口径 |

原进攻版本仍保留 RA/其余两分/三分。另新增 `player_shooting_distance_aligned.csv`，使用 shotchartdetail 的整数 `SHOT_DISTANCE`，按 <6ft、其余两分 ≥6ft、三分分组。防守接口按追踪距离分类，底层距离精度、覆盖样本与进攻端不必相同；只能说距离分段名称统一，不能声称完全相同的逐球判定或直接对位样本。

## 字段解释

- `opponent_fgm / opponent_fga / opponent_fg_pct`：球员被官方追踪归为防守者时，对手命中数、出手数、命中率；**命中率越低越好**。
- `opponents_normal_fg_pct_official`：NBA 返回的对手正常命中率基准，不是简单全联盟平均，也不是正在进攻的某一名球员命中率。
- `diff_from_opponents_normal_official`：官方差值，负值表示对手命中率低于正常水平；源百分数经过舍入。与精确计数再算的比例可能差约一个舍入单位。
- 中间派生区域只有精确命中/出手计数；不从已经舍入的正常命中率强行倒推出“官方基准”，相应基准和差值留 null。
- `tracking_games / frequency_official`：官方该分类覆盖比赛数和频率，不等于完整出场 GP。派生区域不凭相减猜比赛数。
- 缺少该分类记录或零出手都显示没有可用命中率；不把“没有追踪样本”解读为防守完美或防守差。

## 已取得范围

全联盟 582 名出场人员全部保留在输出，580 人有 Overall 防守追踪记录；<6ft 576 人、<10ft 578 人、两分 579 人、三分 575 人、>15ft 579 人。分类覆盖不同是真实限制，不漏行、不补假的 0。HOU 16 / DAL 23 共 39 人在六种官方分类中均找到记录。

- `player_defensive_shooting.csv`：582 人 NBA scope + 两队 39 人球队 scope，各 7 类记录（6 个官方分类 + 1 个派生中间区）。
- `priority_team_defensive_shooting.csv`：两队数据。
- `defensive_visualization_data.json`：前端直接消费，包含覆盖统计和分区警示。
- `visualizations/flagg_offense_defense_distance_bands.png`：弗拉格双栏进攻／对手防守命中率示例，分开解释数值方向。

所有记录以 `scope + player_id + band` 为键。离队/交易球员用请求的 TeamID 过滤统计，不能用 PLAYER_LAST_TEAM_* 二次筛选来丢掉历史球员。

## 解释限度

防守方低命中率也受对手难度、协防、阵容与运气影响。近框数据不等于篮筐保护全部价值，外线对手命中率也不能独立评价防守能力。图上使用“对手命中率／官方追踪”，不称“防守成功率”，不拿 1−FG% 当个人防守能力。

例：官方 DAL 数据中弗拉格近框 <6ft 防守时对手 **214/360，59.4%**；三分防守对手 **139/401，34.7%**；≥6ft 其余两分为派生 **76/181，42.0%**。这些是赛季背景，不能代替当前回合的真实防守位置。

## 复现

```bash
python3 pipeline/vision/models/season/fetch_defense_data.py
```

原始响应、完整 URL、UTC 时间与解压后 SHA-256 追加到 raw/ 和 manifest.json。进攻端全联盟逐次出手请求会截断至 102400 行，完整坐标按月份重新采集，逐人核对 FGM/FGA。NBA shotchartdetail 的 Month 为赛季序号（1=10 月，7=4 月），不是自然月份编号；使用实际 GAME_DATE 交叉检查，杜绝把截断或空响应当完整数据。
