# 考试当天操作手册

目标：拿到一段新的 24 秒进攻视频，5 分钟内出结构化清单（谁持球、谁传给谁、谁投、进没进、xFG、场上阵容）。

## 0. 到场先做（5 分钟）

```bash
cd "NBA 黑客松项目/team-repo"
git pull
pipeline/vision/.venv/bin/python -m unittest pipeline/vision/test_scoreboard_parse.py   # 8 个 OK
python3 -m unittest pipeline.enrich.test_join                                          # 12 个 OK
python3 -m pipeline.events.evaluate                                                    # 两段训练片指标见下
```

评测应为：`amen poss=2/2 team_acc=100% fake_to=0 shot=R 1/1`、`flagg poss=2/2 team_acc=98% fake_to=0 shot=R 1/1`。

## 1. 跑新视频（一条命令）

```bash
python3 -m pipeline.run_clip --video "../官方资料/<考试视频>.mp4" --clip exam
```

什么都不用填，命令会自己做：

| 步骤 | 用时（M 系列 Mac） | 自动做了什么 |
| --- | --- | --- |
| 登记 | 即时 | 写 `pipeline/vision/clips.json`，白衣先按主队 |
| 追踪 ∥ 记分牌 | 约 130–150 秒 | 球员 / 球 / 号码；记分牌读节次、比赛钟、比分、队名 |
| 认客主队 | 即时 | 记分牌读到队名缩写 ≥5 次就以它为准 |
| 认白衣队 | 即时（认错要多跑约 150 秒） | 号码只在某队名单里有 → 投票；明显不符就改登记并重跑追踪 |
| 建清单 | 几秒 | 回合、持球、传球、出手 |
| 认比赛 + 官方补全 | 1 秒 | 记分牌比分对 `data/` 下各场；对上就补投篮人、助攻、xFG、场上阵容、官方视频链接 |

产物：`pipeline/events/out/exam.json`（清单）和 `exam.html`（报告：视频 + 可点时间线 + 场上阵容 + 出手卡）。报告用 `open pipeline/events/out/exam.html` 直接打开；用 Python 自带的 http.server 看时视频不能拖动（它不支持分段读取）。

已知比赛时可以直接给：`--game-id 0022500701`。已知队伍也可以给 `--away DAL --home HOU --white HOU`，和记分牌不一致时会报警并以记分牌为准。

## 2. 看终端这几行就知道对不对

- `[记分牌] 4TH 1:52 → 1:34，比分 [...]（DAL 在前），读全 61/65`：读全比例低于一半要警惕（画面没有记分牌，或记分牌样式没见过）。
- `[白衣] 号码投票 HOU 4/4`：票数太少（<3）时不会改登记，自己看一帧确认。
- `[认比赛] ... → 0022500xxx`：没认出来说明 `data/` 下没有这场，见第 3 步。
- `[官方] ... 比分对上 1`：比分变了但对上 0，看片段是不是跨了节。
- 清单 `official.track_conflicts` 非空：号码认出的人和官方打架，以官方为准。

## 3. 考试视频不在已下载的四场里

2025-26 常规赛 HOU 和 DAL 的四次交手都在 `../data/2025-26/games/`：

| gameId | 日期（美东） | 对阵 |
| --- | --- | --- |
| 0022500160 | 2025-11-03 | DAL @ HOU |
| 0022500356 | 2025-12-06 | HOU @ DAL |
| 0022500491 | 2026-01-03 | HOU @ DAL（阿门片） |
| 0022500701 | 2026-01-31 | DAL @ HOU（弗拉格片） |

不在其中时，按 `../scripts/README.md` 的「模式三」抓逐回合、boxscore、投篮质量三个文件到 `../data/2025-26/games/`，再跑：

```bash
python3 -m pipeline.run_clip --clip exam --skip-vision            # 不重跑视觉，只认比赛 + 补全
python3 -m pipeline.enrich.find_game --clip exam                  # 单看各场对上比例
```

没有官方数据也能出清单，只是投篮人、助攻、xFG 要靠画面，名字可能缺。

## 4. 左右

清单里的左右一律是**投篮人面向篮筐时的左右**（和官方 Left Corner 3 一致）。四场 259 次带左右区域名的投篮全部是「官方坐标 x 为负 = 左」，不用再核。`official.court_half` 是电视画面里的左 / 右半场，别和它混。

## 5. 云端

当前账号（Team04 SSO）没有 CloudFormation 读权限，原来的媒体桶也已不存在，云上回放页暂时用不了。本地清单 + HTML 报告就是完整产物，演示直接开报告。有新桶时：

```bash
python3 -m pipeline.run_clip --clip exam --skip-vision --upload --bucket <桶名>
```

上传失败只报警告，不影响本地结果。

## 6. 出问题时

| 现象 | 处理 |
| --- | --- |
| `记分牌上认不出客主队` | 加 `--away / --home` 重跑 |
| 记分牌读全很低 | 记分牌样式变了；清单仍能出，比分驱动的出手补全会缺 |
| 白衣认错没自动改 | 加 `--white` 重跑（要重跑视觉） |
| 片段跨了节 | 补全只用记分牌读到最多的那一节，另一节的官方记录不会并进来 |
| 想调事件规则不想等视觉 | `--skip-vision` |
