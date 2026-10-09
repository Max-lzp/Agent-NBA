# pipeline/enrich —— 第二阶段：并入官方数据

把事件流清单（`pipeline/events/out/{clip}.json`）和官方逐回合数据对上，填进清单里预留的字段。

## 数据放哪

`data/` 下任意层级，文件名 `{gameId}_{接口名}.json`，按内容自动识别：

| 内容 | 认法 | 用来填 |
| --- | --- | --- |
| 逐回合 `playbyplayv3`（stats）或 CDN liveData `playbyplay` | 有 `game.actions` | 出手人、进没进、几分、助攻、坐标、篮板、失误 |
| boxscore（`boxscoresummaryv3` / `boxscoretraditionalv3` / CDN） | 有 `homeTeam.players` | 号码 → 球员，这场的出场名单 |
| `shotqualityshotlog` | 有非空 `shots` | xFG%、防守人 |

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

- 事件 `official`：action_number / period / clock_s / made / value / shot_type / distance_ft / assist / xfg / defender / description；出手还会填 `geometry.x_ft / y_ft / shot_zone`。
- 回合 `official`：period / clock_start_s / clock_end_s / score_start / score_end。
- 顶层 `official`：game_id / 用到的文件 / 时间窗 / 对上与补入统计 / 片段内全部官方记录 `plays`（含罚球、犯规、换人）。

## 已知限制

- 比赛钟只认 `M:SS`。每节最后一分钟转播显示 `SS.s`，现在读不到，片子落在最后一分钟时要先补。
- 坐标左右（`_l` / `_r`）按官方 x 的正负分，未对照画面核实。
- 只在合成数据上测过；2025-26 真实数据落盘后先用两段训练片验证（阿门 0022500491、弗拉格 0022500701）。
