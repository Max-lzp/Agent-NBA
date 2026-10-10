# pipeline/enrich —— 第二阶段：并入官方数据

把事件流清单（`pipeline/events/out/{clip}.json`）和官方逐回合数据对上，填进清单里预留的字段。

## 数据放哪

`data/` 下任意层级，文件名 `{gameId}_{接口名}.json`，按内容自动识别：

| 内容 | 认法 | 用来填 |
| --- | --- | --- |
| 逐回合 `playbyplayv3`（stats）或 CDN liveData `playbyplay` | 有 `game.actions` | 出手人、进没进、几分、助攻、坐标、篮板、失误 |
| boxscore（`boxscoresummaryv3` / `boxscoretraditionalv3` / CDN） | 有 `homeTeam.players` | 号码 → 球员，这场的出场名单 |
| `shotqualityshotlog` | 有非空 `shots` | xFG%、防守人 |
| `2025-26/dal_margin_offense.json` | 固定文件名，不按场次 | 独行侠球员在四档分差下的赛季真实命中率、每球权得分 |
| `data/deep/*.csv` | 仓库内 `data/deep`，没有时再看数据目录的 `2025-26/deep` | 赛季出手质量、实际命中率、引力、关键度。按球员 ID 或球队+号码贴到出手上。赛季汇总，不是这一球的胜率变化 |

`DEEPCOURT_DATA=/别的目录` 可以临时换数据目录。

## 怎么对

- **时间**：记分牌每次读数给出（视频秒, 节次, 比赛钟）。官方记录的视频时刻 = 第一次读到同节、钟 ≤ 记录时钟的那一刻，误差约 0.33 秒。
- **进球**：记分牌确认得分后的比分（客, 主）直接查官方命中记录，比分相同就是这一球，不依赖时钟。
- **补与删**：片段时间窗内官方有、清单没有的出手 / 篮板 / 失误补进来（`source: official`）；官方没记的视觉失误删掉。

## 用法

```bash
python3 -m pipeline.run_clip --video 考试.mp4 --away DAL --home HOU --white HOU --game-id 0022500701   # 一条命令自动并入
python3 -m pipeline.enrich.join --clip amen --game-id 0022500491 --write-roster                        # 单独跑
python3 -m unittest pipeline.enrich.test_join
```

有 boxscore 且 `roster.json` 里还没有这场时，`run_clip` 会先写入出场名单再重建一次清单，认人用上官方号码。

## 产出字段

- 事件 `official`：action_number / period / clock_s / made / value / shot_type / distance_ft / assist / xfg / defender / description / player_id；独行侠出手多一个 `season_margin`（分差档、真实命中率、每球权得分）。对得上 `data/deep` 的人多一个 `season_profile`（赛季预期命中率、实际命中率、高于或低于预期、引力、关键度）。出手还会填 `geometry.x_ft / y_ft / shot_zone`。
- 出手还会多这几项（`defense.attach`，读 `pipeline/vision/results/local/{clip}.release.json`、`boxscorematchupsv3`、`leaguedashplayerstats`、四场 `shotqualityshotlog`）：
  - `release`：出手帧时刻、投篮人框、球框。由 `pipeline/vision/release.py` 按原帧率重检得出。
  - `defense.nearest` / `defense.nearest_box`：出手帧离投篮人最近的对方球员。号码读不出时只有球队。
  - `official.defender`：画面读出号码时用画面（`source: cv`），否则用本场对位表里对位时间最长的人（`source: matchup`）。对位表是全场统计，不是这一投的防守人。附 `vs_shooter`（本场对位秒数、回合、投篮人的出手和得分）和 `season`（赛季场均）。
  - `official.shooter_season`：投篮人赛季场均。
  - `official.zone_chart`：投篮人四场在 8 个区的出手、命中、平均 xFG，以及全部球员同区平均。不足 3 投不给 xFG。
- 回合 `official`：period / clock_start_s / clock_end_s / score_start / score_end。
- 顶层 `official`：game_id / 用到的文件 / 时间窗 / 对上与补入统计 / 片段内全部官方记录 `plays`（含罚球、犯规、换人）。

## 左右怎么定

- 左右一律按**投篮人面向篮筐时的左右**（NBA 官方区域名 Left Corner 3 的惯例），出手区域 `corner3_l / wing3_r` 等都按这个。
- 依据按优先级写在事件 `official.side_basis`：
  1. `official`：这条记录的区域名里带 Left / Right（如 `Left Corner 3`、`Right Side(R)`）。
  2. `official_calibrated`：这份数据里至少 3 条记录同时有坐标和左右区域名，按多数定「x 为负是哪一侧」，再套用到没标签的记录。
  3. `assumed`：没有可用标签，用默认「x 为负 = 左」。顶层 `official.side_basis` 会写明。
- 实测 2025-26 四场：259 次带左右区域名的投篮，全部是 `xLegacy` 为负 = 左、为正 = 右，没有例外。默认值就是这个，一般不用动。
- **CDN 的 `side` 不是投篮人左右**，是电视画面里的左 / 右半场（和 CDN 的 `x < 50` 一一对应）。它存成 `official.court_half`，可以用来核对「这一回合往画面哪边的篮筐打」。
- 想自己核实：找一个区域名是 Left Corner 3 的进球，看画面里投篮人面向篮筐时人在他左手边的底角就对了。真要翻转用 `export DEEPCOURT_X_NEGATIVE=right`。
- 不知道左右时，底角和 45 度三分不分左右也不出区域名（不硬猜），正面三分、禁区、中距离照常给。

## 已知限制

- 只在合成数据上测过；2025-26 真实数据落盘后先用两段训练片验证（阿门 0022500491、弗拉格 0022500701）。
- 比赛钟最后一分钟（SS.s）已支持，但两段训练片都不在最后一分钟，只有单测覆盖。
