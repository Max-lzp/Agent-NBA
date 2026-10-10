# NBA 2025-26 赛季 火箭 / 独行侠 球员数据 + 男团女团担当 说明文档

## 7.1 数据集概述

- **赛季**：2025-26 NBA（常规赛；季后赛如适用）
- **球队**：休斯顿火箭（Houston Rockets）、达拉斯独行侠（Dallas Mavericks）
- **球员数**：火箭 **16 人**，独行侠 **23 人**，合计 **39 人**（全部为当季至少代表所属球队出场 1 场比赛的球员，含双向合同、10 天合同、赛季中期交易/签约且上场过的球员）
- **数据更新时间**：2026-10-10
- **文件**：`nba_2025_26_rockets_mavericks_roster_with_idol_roles.csv`（UTF-8 带 BOM，逗号分隔，可直接用 Excel 打开）

### 球员完整性说明

以 Basketball-Reference 球队赛季名单页与逐场统计页（Per Game）为准逐人核对，两队上场名单与交易记录交叉验证，**无遗漏、无冗余**：

- 火箭 16 人：所有进入当季逐场统计表的球员均已收录（含双向合同 Isaiah Crawford、JD Davison，双向→正式 JD Davison，以及只上场 1 场的双向球员 Tristen Newton）。
- 独行侠 23 人：含 2025 年状元 Cooper Flagg、双向合同（Miles Kelly、Ryan Nembhard、Moussa Cisse、John Poulakidas、Tyler Smith）、10 天合同（Jeremiah Robinson-Earl）、赛季中期交易加盟（Tyus Jones、Marvin Bagley III、AJ Johnson、Khris Middleton、D'Angelo Russell 2月5日前为独行侠出战）。

**按任务要求排除的"仅签约未上场"球员**（未进入两队当季逐场统计表，未达"出场≥1"标准）：

- 独行侠：Kyrie Irving（7月签约，未上场）、Dante Exum（8月签约未上场）、Dennis Smith（签约后被裁）、Olivier-Maxence Prosper（赛季前被裁）
- 火箭：Fred VanVleet（7月签约，未进入上场名单）、Kevon Harris（双向合同，未上场）、Tyler Smith（12月签火箭双向未上场，1月被裁，2月底转签独行侠并上场，故归入独行侠 23 人）

---

## 7.2 字段说明

| 列名 | 含义 | 单位/格式 | 来源 | 缺失值处理 |
|---|---|---|---|---|
| `team` | 球队名称 | 文本 | Basketball-Reference 2025-26 球队页 | - |
| `player_name` | 球员姓名（英文） | 文本 | NBA.com 官网球员页 | - |
| `jersey_number` | 球衣号码 | **字符串**（保留 `00`） | Basketball-Reference 球队名单页，与 NBA.com 2025-26 数据交叉核对 | - |
| `draft_year` | 选秀年份 | 4位数字；落选秀填 `Undrafted` | NBA.com 官网球员页选秀信息 | - |
| `draft_round` | 选秀轮次 | 1/2；落选秀填 `Undrafted` | 同上 | - |
| `draft_pick` | 总顺位 | 数字；落选秀填 `Undrafted` | 同上 | - |
| `height_m` | 身高（米） | 米，保留2位小数 | 由 NBA.com 官方身高（英尺-英寸）换算（1 in = 0.0254 m） | - |
| `height_ft_in` | 身高（英尺-英寸） | 如 `6-11` | NBA.com 官网球员页 / Basketball-Reference | - |
| `wingspan_m` | 臂展（米） | 米，保留2位小数 | 见下方臂展来源说明 | 无官方/可靠数据留空（如 Brandon Williams） |
| `wingspan_ft_in` | 臂展（英尺-英寸） | 如 `7-4.75` | 见下方臂展来源说明 | 无数据填 `NA` |
| `position` | 常任位置 | PG/SG/SF/PF/C | Basketball-Reference 2025-26 逐场统计位置 | - |
| `player_id` | NBA 官网 person_id | 纯数字 | NBA.com 官网球员页 URL（逐一抓取核验，非 NEEDS_VERIFY） | - |
| `player_id_source` | ID 来源 | URL | `https://nba.com/player/{person_id}` | - |
| `primary_role` | 主职能 | 9类之一（见 7.3） | 依据 2025-26 赛季实际比赛风格人工/智能判断 | - |
| `secondary_role` | 兼职职能 | 0~2 个，斜杠 `/` 分隔 | 同上 | 无兼职留空 |
| `idol_position` | 团内担当 | 多个担当用 ` + ` 连接 | 依据 7.3 映射表由主/兼职职能推导 | - |
| `idol_cue` | 团综/舞台辨认线索 | 一句话 | 同上 | - |
| `idol_analogy` | 一句话团内类比 | 一句话 | 同上 | - |

### 臂展来源说明

臂展优先采用 NBA 官方联合试训体测数据，其次为 CraftedNBA 聚合的官方测量数据库，再次为球探报告/球队试训记录：

- **2025 联合试训体测**：Cooper Flagg（7-0）、Moussa Cisse（PIT 7-5.5）、John Poulakidas（PIT 6-10）、Miles Kelly（G 联盟精英赛 6-8.25）、Ryan Nembhard（G 联盟精英赛 6-2.25）
- **2024 联合试训体测**：Reed Sheppard（6-3.25）、Tristen Newton（6-6.75）、Tyler Smith（7-1）、AJ Johnson（6-8.5）、Isaiah Crawford（G 联盟精英赛 7-0.5）
- **2021 联合试训体测**：Jeremiah Robinson-Earl（6-9.75）
- **CraftedNBA 测量数据库**（聚合 NBA 官方测量，含多来源交叉核验）：其余 32 人中的 31 人，如 Kevin Durant 7-4.75、Anthony Davis 7-5.5、Dereck Lively II 7-7、Klay Thompson 6-9 等
- **Brandon Williams**：无官方体测记录（2021 年落选时因伤未参加联合试训），也无可靠的球探试训臂展数据，**臂展填 `NA`、米字段留空，未编造数据**

> 注：落选秀/国际球员若官方体测缺失而采用球探报告时会有约 1-2 英寸误差；本表所有臂展均来自上述官方体测或聚合官方数据，未使用游戏/球迷数据库（如 NBA 2K）数值。

### 球衣号码说明

- 以该球员 2025-26 赛季**主要时段**所穿号码为准；号码为字符串，`00` 保留两位（Max Christie 穿 `00`，Tristen Newton 穿 `00`）。
- **独行侠 `#1` 的三人分属不同时段**：Jaden Hardy（2025年10月—2026年2月5日交易前穿 1 号）、Tyus Jones（2月5日加盟至2月28日被裁期间穿 1 号）、John Poulakidas（2月28日签双向合同后穿 1 号）。三人从未同时身披 1 号，故均记 `1`。
- **独行侠 `#23` 的两人分属不同时段**：Jeremiah Robinson-Earl（1月 10 天合同期间穿 23 号）、Tyler Smith（2月底签双向合同后穿 23 号，有 2026年3月16日比赛记录佐证）。NBA.com 当前页显示的号码为 2026-27 赛季号码，与本赛季无关（如 Tyler Smith 现为 21 号、Max Christie 现为 0 号）。
- 其余号码与 Basketball-Reference 2025-26 球队名单页一致。

---

## 7.3 职能分类标准：篮球职能 → 男团/女团担当映射表

| # | 篮球职能 | 团内担当 | 团综/舞台辨认线索 |
|---|---|---|---|
| 1 | 控场大脑/指挥官 | 队长 + 主MC + 创作担当 | 谁在团综里喊开始、控流程、发言最多 |
| 2 | 王牌得分手/关键先生 | C位 + ACE + 舞台王者 | 副歌站中间、结尾特写、solo最多 |
| 3 | 高塔门神/内线核心 | 门面 + 力量担当 + 大哥/大姐 | 谁最高最壮、合照站后排中间 |
| 4 | 万能锋线/瑞士军刀 | 全能ACE + 副队长 | 唱跳rap都能补，哪里缺人顶哪里 |
| 5 | 狙击手/空间射手 | 主唱 + 高音担当 | 平时站边上，一到高音部分镜头给特写 |
| 6 | 防守工兵/保安队长 | 主舞 + 领舞 | 干最累的舞蹈，镜头不一定多但舞台离不开 |
| 7 | 替补微波炉/第六人 | 领唱 + 副C + 气氛担当 | 副歌前一句一开口就炸，替补席站起来 |
| 8 | 老将导师/定海神针 | 队长 + 大哥/大姐 + 团魂 | 年龄最大、讲话最多、稳定军心 |
| 9 | 新秀潜力/双向待命 | 忙内 + 练习生 + 未来C位 | 最年轻、镜头少，但公司力捧 |

**填写规则**：

- `primary_role` 每名球员恰好 1 项，取 9 类之一。
- `secondary_role` 0~2 项，斜杠 `/` 分隔（如 `老将导师/关键先生`）。
- `idol_position` 依据主、兼职职能综合推导，多个担当用 ` + ` 连接（如 `C位 + ACE + 主唱`）。
- **重要声明**："团内担当"仅为帮助零基础球迷快速辨认球员场上职能的类比，**不代表球员实力排名**；"门面"不代表没实力、"C位"不代表永远第一，同一人可同时是队长、主唱和 ACE。

---

## 7.4 数据来源

| 数据 | 来源 |
|---|---|
| 球队名单、上场球员、位置、球衣号码、逐场数据 | Basketball-Reference 2025-26 球队页（`basketball-reference.com/teams/HOU/2026.html`、`/teams/DAL/2026.html`）及交易记录页（`2026_transactions.html`） |
| 球员基础信息（身高、选秀、位置） | NBA.com 官网球员页（`nba.com/player/{person_id}`），39 人逐一抓取 |
| `player_id`（person_id） | NBA.com 官网球员页 URL 数字 ID（全部经页面抓取核验，无 NEEDS_VERIFY） |
| 臂展 | NBA 官方联合试训体测（2021/2024/2025，经 NBADraft.net 体测表、Sporting News 等转写核验）；CraftedNBA 测量数据库（craftednba.com/player-traits/length，聚合官方测量） |
| 职能分类 | 依据 2025-26 赛季实际比赛风格（场均数据、出手结构、防守任务、获奖情况）人工/智能判断 |

**获奖与数据佐证（用于职能分类）**：Kevin Durant（26.0 分、NBA 二阵、全明星）、Alperen Sengun（20.4 分 8.9 板 6.2 助、全明星）、Cooper Flagg（21.0 分、最佳新秀）、Amen Thompson（18.3 分 7.8 板、DPOY 候选）、Reed Sheppard（最佳第六人票选第 6）等，详见 BBR 逐场统计页。

---

## 7.5 已知局限

1. **臂展**：并非所有球员都有官方记录；本表优先使用 NBA 官方联合试训数据，其余来自聚合官方测量数据库。若未来获得更权威的个体实测数据，可能存在 1-2 英寸（约 2-5 cm）误差。
2. **球衣号码**：可能因赛季中期交易/换号而变化，本表以主要时段为准；独行侠 `#1`（Hardy / Tyus Jones / Poulakidas）与 `#23`（Robinson-Earl / Tyler Smith）为分时段使用，详见 7.2。
3. **`player_id`**：全部为 NBA 官网 person_id 并逐人核验，无 `NEEDS_VERIFY` 项。
4. **"团内担当"仅为类比**，不代表实力排名，也不代表球员真实从事类似岗位。
5. **职能分类为智能判断**，基于 2025-26 赛季公开数据与比赛风格；同一球员在不同教练体系下的职能可能不同，本表为主观分类参考。
6. **排除标准**：仅签约但未上场（含被裁、未达出场标准）的球员已按任务要求排除，见 7.1 清单。
7. **数据快照**：以上数据抓取自 2026-10-10；NBA 官网当前页显示的是 2026-27 赛季信息，本数据集以 2025-26 赛季口径为准。

---

## 7.6 使用建议

- 可直接导入 **Excel**（UTF-8 带 BOM，双击即可）、**Google Sheets**、**Python pandas**（`pd.read_csv('nba_2025_26_rockets_mavericks_roster_with_idol_roles.csv')`）。
- **主键**：`team + player_name`（同队无重名）。
- 快速筛选示例：
  - 找"指挥官"：`df[df['primary_role']=='控场大脑/指挥官']`
  - 找"C位/主唱"：`df[df['idol_position'].str.contains('C位')]`
  - 找"高音担当"：`df[df['idol_position'].str.contains('高音担当')]`
- 按 `primary_role` 或 `idol_position` 分组可快速对比两队的职能配置（如火箭的双塔门神、独行侠的多位第六人/练习生）。
- 指标换算：身高/臂展米制列已由英制换算（1 in = 0.0254 m），如需原始英制请用 `height_ft_in` / `wingspan_ft_in` 列。
