# NBA 官方深度数据（2025-26 常规赛）

直接来自 NBA 主站 Inside the Game 所使用的 `stats.nba.com/stats/` 接口，无需账户登录。门户给出的 china.nba.cn 页面当前返回 404；改用官方主站页面确认接口和字段，没有采用第三方 CSV。

| 文件 | 记录数 | 官方接口 |
| --- | ---: | --- |
| shot_difficulty.csv | 125 | shotqualityleaders |
| player_gravity.csv | 234 | gravityleaders |
| player_leverage.csv | 234 | leverageleaders |
| flagg_shot_sequence.csv | 23 | shotqualityvideologs，0022500701 / 1642843 |
| amen_shot_sequence.csv | 18 | shotqualityvideologs，0022500491 / 1641708 |

`raw/` 保留原始响应字节，`manifest.json` 保存完整 URL、UTC 抓取时间、原始 JSON/CSV 哈希、字段及基准。CSV 保留原始字段名、值与视频链接，不替换成演示数据。实际取得的数量与任务书估计不同，以本次官方响应为准。

官方字段说明：<https://www.nba.com/news/inside-the-game-stat-glossary>。AVGSHOTQUALITY 为平均球员在同等跟踪条件下的预期命中率；FGPCTABOVEEXPECTED 为实际减预期的命中率差。接口概率单位是 0–1；图表与叠加层换算为百分点。引力是超过预期的防守压力，不是命中概率。Leverage 是官方分项指标；FULL 等原始值保持原样，官网显示层对这些列乘 10，不将其直接解释为胜率。

shot_difficulty 为官方返回的合格球员/跟踪出手样本，不是全联盟全部球员，也不保证 FGA 与完整赛季统计相同。两队仅 HOU 5 人、DAL 3 人在该响应中；其他人员没有此数据，不补零。模型基准是这 125 人按跟踪 FGA 加权的 xFG，约 46.5813%，不是完整联盟实际 FG%。

训练片段标注的北京日期 2026-02-01 / 2026-01-04，对应 NBA 官方美国比赛日期 2026-01-31 / 2026-01-03。使用 GameID、PlayerID、eventNum 和节次/比赛时钟连接数据；不能把比赛时钟直接当视频时间。单场记录只覆盖两名球员的出手，不是整场全部回合。

刷新真实数据：`python3 pipeline/vision/models/fetch_deep_data.py --output-dir data/deep`。
生成模型：激活模型依赖环境并设置可写图形缓存，然后运行 `python pipeline/vision/models/run_official_model.py`。详细命令见 DELIVERY-README.md。
