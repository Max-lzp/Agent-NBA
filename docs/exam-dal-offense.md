# 考试日数据准备：独行侠进攻 / 火箭防守

组委会确认（10-10）：**18:00 考试视频是独行侠进攻、火箭防守**，形式与训练片相同，只是换进攻回合。不再准备其他对阵。

## 主模板：0022500701（弗拉格训练片同场）

| 项 | 值 |
| --- | --- |
| 比赛 | 2026-01-31 / 北京时间 2 月 1 日 · DAL @ HOU |
| 官方 ID | `0022500701` |
| 本地片段 | `flagg` · `训练_弗拉格2月1日.mp4` |
| 球衣 | 深色 = DAL（进攻）· 白衣 = HOU（防守） |
| 训练片出手 | 第四节 1:34 · Flagg #32 挑篮 · xFG 0.504 · 进 |

官方文件（已齐）：

- `../data/2025-26/games/0022500701_{playbyplay,boxscore,shotqualityshotlog,boxscorematchupsv3}.json`
- `../data/2025-26/shotcards/0022500701_shotcard.json`

名单见 `pipeline/vision/roster.json` → `games.flagg_20260201`（短名用 Amen / Klay，避免两个 Thompson 撞车）。

## 同对阵备库（只在认比赛时用）

DAL 进攻、HOU 防守还可能落在另一场客场：

| gameId | 日期 | 对阵 |
| --- | --- | --- |
| `0022500160` | 2025-11-03 | DAL @ HOU |
| `0022500701` | 2026-01-31 | DAL @ HOU（主模板） |

`HOU @ DAL` 的两场（`0022500356`、`0022500491`）球衣主客对调，考试片若仍是「深色独行侠进攻」应优先对客场两场。阿门训练片是火箭进攻，只作演示，不当考试模板。

## 第四节末独行侠出手速查（0022500701，xFG = shotQuality）

| 比赛钟 | 球员 | 结果 | xFG | 备注 |
| --- | --- | --- | --- | --- |
| 2:14 | Gafford #21 | 进 | 0.659 | 扣篮 |
| **1:34** | **Flagg #32** | **进** | **0.504** | **训练片** |
| 0:50 | Flagg #32 | 不中 | 0.407 | 17 英尺急停 |
| 0:47 | Marshall #13 | 进 | 0.573 | Christie 助攻 |
| 0:25 | Flagg #32 | 不中 | 0.215 | 上篮 |
| 0:09 | Flagg #32 | 不中 | 0.336 | 三分 |

更早的第四节独行侠出手（Christie / Klay / Flagg 连打）也在同一份 shotquality 里，认比赛后由 `enrich` 自动贴 xFG，不必手抄。

## 出场与撞号（这场）

独行侠出场常用号：32 Flagg、00 Christie、21 Gafford、13 Marshall、31 Klay、25 Washington、16 Martin、7 Powell、10 Williams、9 Nembhard、30 Cisse。

撞号（认人时看队色）：

- `#1` Amen（HOU）/ Hardy（DAL，本场 DNP）
- `#7` Durant（HOU）/ Powell（DAL，本场出场）
- `#10` Smith（HOU）/ Williams（DAL）
- `#30` Capela（HOU）/ Cisse（DAL）
- `#32` Flagg（DAL）/ Green（HOU，本场 DNP）

## 拿到考试视频后

```bash
cd team-repo
# 已知是独行侠进攻时可先写死球衣，减少误认
python3 -m pipeline.run_clip \
  --video "../官方资料/<考试视频>.mp4" \
  --clip exam \
  --away DAL --home HOU --white HOU \
  --game-id 0022500701

# 若记分牌比分对不上 0701，去掉 --game-id，让程序在四场里认；
# 仍优先看两场 DAL @ HOU。

python3 -m pipeline.present --clip exam
# 本地：web/game.html?clip=exam
# 账号可用：sh scripts/publish.sh
```

中文名表在 `pipeline/present/build.py` 的 `NAME_ZH`，已按本场独行侠出场补全。
