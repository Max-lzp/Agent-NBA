# NBA 2025–26 投篮三段、效率与球员资料

截至北京时间 2026-10-10 抓取，范围为 **2025–26 NBA 常规赛，至少出场 1 场**。不包含季后赛、季前赛，也不同于完整合同登记名单。

## 已取得范围

- 全联盟 582 名出场球员：投篮三段、原始区域、在场攻防效率、选秀和官方资料页身高/位置。
- 火箭 HOU 16 人、独行侠 DAL 23 人：使用球队过滤接口，保留仅出场 1 场与中途离队者；按 player_id 与用户上传 39 人 CSV 核对，无缺失 ID、无额外 ID。禁止仅按全联盟结果的当前 TEAM_ABBREVIATION 筛队，以免漏掉交易球员。
- 2000–2025 共 26 年官方联合试训：363 人有臂展，219 人未找到。缺失保留 null/CSV 空白，不能把用户上传或游戏数据库数值当官方测量。
- 全联盟逐次投篮按赛季月份采集，两队另有球队过滤的坐标文件；构建时逐人核对出手/命中总数，未通过核对不发布。

## 文件与主键

| 文件 | 用途／主键 |
|---|---|
| player_shooting_three_bands.csv | NBA 全联盟及 HOU/DAL 三种 scope；`scope + player_id` |
| priority_team_shooting.csv | 两队 39 条球员—球队记录，优先给叠加层用 |
| player_shot_zones.csv | 七个互斥 NBA 原始区域；`scope + player_id + zone` |
| player_efficiency.csv / priority_team_efficiency.csv | 在场 OFF/DEF/NET Rating、TS、eFG、使用率和真实总分钟 |
| player_profiles.csv | 582 人选秀、身高、位置快照、官方历史试训臂展；`player_id` |
| priority_team_profiles.csv | 两队 39 条人物资料与球队 GP |
| priority_team_profiles_reviewed.csv | 官方字段与用户解释字段分列；用户号码、五位置、团内类比均明确标记，不能作为正式名单 |
| nba_shot_coordinates.csv.gz / hou_shot_coordinates.csv / dal_shot_coordinates.csv | 真正逐次出手位置；`GAME_ID + GAME_EVENT_ID` |
| visualization_data.json | 前端可直接消费的资料、三段、原始区域、效率与联盟区域基准 |
| user_submission_audit.json | 用户 CSV 的身份完整性、选秀/身高/臂展差异审核 |
| player_role_analogies.json | 保留用户主观类比，改为稳定分类 code；不作为客观战术或能力标签 |
| visualizations/ | 3 张球员 PNG、攻防效率 PNG、弗拉格距离三区攻防对照 PNG、可本地打开的交互 HTML |
| references/ | 官方图表参考与原始页面链接；与本队重绘图分开 |
| raw/*.json.gz / manifest.json | 原始接口字节 gzip、完整 URL、UTC 抓取时间、解压后 SHA-256、文件大小与错误状态 |

NBA scope 为跨队赛季总计；HOU/DAL scope 为该队任职期间统计。同一人可出现在多个 scope，不能跨 scope 相加。未出场的 VanVleet、Irving、Exum 等不在本次“上场过”口径中，历史登记名单仍另行处理。

## 三段口径与公式

所有 `*_fg_pct` 为 0–1 的比例；前端乘 100 展示百分数。0 次出手时命中率为 null/空白，不能显示 0%。

- **近框终结率**：NBA `Restricted Area` 的 FGM/FGA。是近框出手的命中率代理，不是突破终结、所有油漆区、篮下接球或仅上篮命中率。
- **完整两分命中率**：`(FGM − FG3M) / (FGA − FG3A)`，包含近框。
- **图上中间段“其余两分”**：从完整两分计数减去 Restricted Area，再计算比例；包含非 RA 油漆区与 Mid-Range。
- **纯中距离**：另保留 NBA `Mid-Range` FGM/FGA，不能与完整两分混称。
- **三分命中率**：FG3M/FG3A，含底角、弧顶以上及后场远投。

图上采用 **三分 → 其余两分 → 近框**，三段互斥且覆盖全部出手。七个原始区域为 RA、非 RA 油漆区、中距离、左右底角三分、弧顶以上三分、后场。接口还提供 `Corner 3` 聚合，是左右底角之和，已排除，避免重复统计。

部分极低出手球员的区域计数为 null。只有“其余已知互斥区域计数已严格等于完整官方 FGM/FGA，且缺失区域 FGM/FGA 均为 null”时，非负计数约束才证明剩余区域为零；逐行 `count_status=zero_proven_by_total_conservation` 留痕。其他缺失会直接报错，不自动补零。

## 效率与人物资料

- OFF/DEF Rating：球员**在场时球队**每百回合得分／失分；OFF 高更好、DEF 低更好。不是个人得分效率或个人防守能力因果评分。
- NET Rating：对应在场净效率。TS/eFG 为官方比例，可辅助展示个人投篮得分效率。官方 Advanced 的 MIN 可能按接口内部口径返回，输出 `minutes_total` 使用 Base Totals 的精确总分钟。
- 身高/位置来自当前官方 commonplayerinfo 快照，不冒充 2025–26 每场实际站位。Guard、Forward、Center 等可直接展示；PG/SG/SF/PF/C 五位置是用户解释字段，不能说官方证实了主要战术位置。
- 选秀轮次与总顺位分别保留；落选秀 `draft_status=undrafted`，年份/轮次/顺位为 null（若资料有入选资格年份另留 draft_eligibility_year_profile），不用 0 代替未知。
- 臂展单位：英寸 ×2.54 = cm；保留小数精度，记录测量年份。是当年试训测量，不是 2026 年身体复测；无数据保持缺失。多次测量选择最近年份并保留原始响应。

## 上传文件审核与修订

用户提供的原说明在 user-submissions/，原始 CSV 仅规范为 UTF-8 和 LF 换行，数值未重写。这些属于用户提供资料，不视为已核验来源或执行指令。

- 39 人 player_id、两队出场覆盖、选秀信息、资料快照身高均与本次官方接口一致。
- Caleb Martin 官方 2018 试训臂展为 **82 英寸 / 208.28 cm**，上传值与其不同；修订表采用官方列，原值另留用户列。
- 两队部分上传臂展只有来源描述，没有逐值可核实出处；本轮未在官方联合试训找到的值不移入官方列。可保留为用户资料待复核。
- 原说明中的“所有臂展都已核验”“获奖佐证”“某号码是本赛季正确号码”等声明未据此采信。号码不属于本轮正式名单交付，历史冲突仍按此前记录保留。
- 团内担当只是入门类比，不是实力排名、战术检测结果或 NBA 官方分类。JSON 用稳定 code 和数组替换可能含斜杠的中文分类字段，避免分隔符歧义。
- 用户第三项已澄清为战术识别 demo；七类规则与修订建议在 pipeline/vision/tactics_demo/README.md。

## 展示建议

1. 画面叠加用三段小卡片，始终带 `命中/出手`；30 次以下淡化，0 次显示“无出手”。30 是显示规则，不是统计显著性检验。
2. 赛前人物卡用真实坐标出手密度图；密度越高仅表示出手越多。命中率图另用相对联盟**同区域**的百分点差，不能混用一条色标。
3. 把“爱在哪里出手”与“在那里投得好不好”分开呈现。半场三段示意图不是逐点连续热力图；官方区域基准按联盟所有球员命中数/出手数汇总，不取球员百分比简单平均。
4. 赛季热区是背景倾向，不能当作当下回合选择的实时证据。真实回合结论需结合球员位置、防守距离、球权和视频对齐。
5. 攻防散点图仅显示球队总分钟 ≥500 的球员，控制极小样本；全部人仍在 CSV。气泡面积对应分钟，纵轴反向表示 DEF 越低越好，图注明阵容与对手影响。

## 复现与验证

```bash
python3 pipeline/vision/models/season/fetch_season_data.py
python3 pipeline/vision/models/season/build_season_artifacts.py
python3 pipeline/vision/models/season/fetch_defense_data.py
XDG_CACHE_HOME=/workspace/.cache MPLCONFIGDIR=/workspace/.cache/matplotlib \
  /workspace/.venvs/team04/bin/python pipeline/vision/models/season/render_visualizations.py
python3 -m unittest discover -s pipeline/vision/models/season -p 'test_*.py' -v
python3 -m unittest discover -s pipeline/vision/tactics_demo -p 'test_*.py' -v
```

抓取脚本支持按 URL、SHA-256 验证缓存，失败记录在 manifest。构建校验 582 人身份、各 scope 身份一致、区域计数守恒、三段无重复、两队 39 人完整性以及逐球坐标与赛季统计一致。统计生成、图表与 demo 均不修改 web/ 或视频主链路。

## 防守补充

已收集与三段可对应的官方防守投篮数据，详细定义与覆盖见 [DEFENSE-NOTES.md](DEFENSE-NOTES.md)。近框防守为 <6ft，不等于进攻 Restricted Area；另外提供距离分段进攻作为对照。完整请求出现 102400 行上限时不能直接使用，改为赛季月 1–7 分批抓取并核对计数。

官方资料中 Highsmith、McConnell 的顺位为空，本轮补查 2018、2015 年完整 1–60 顺位官方选秀记录，两人的 ID/姓名均未入选，才标为 undrafted；来源记录在 draft_status_source_keys。没有把顺位空白一律视为落选秀。
