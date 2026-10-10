# 2025-26 历史名单核验（2026-10-10）

本轮按用户优先级补基础名单，暂停模型扩展。正式 `pipeline/vision/rosters/2025-26.json` 尚未生成：不能把历史比赛页面中混入的当前号码、首场观察日期、未知离队日期作为真实号码区间。

## 已核验统计与覆盖

`2025-26-verified-totals.csv` 已扩大至 HOU 19 人、DAL 25 人，共 44 条球员—球队记录（Tyler Smith 分别在两队登记）。统计沿用官方球队过滤 leaguedashplayerstats 的 GP/精确总分钟和 playercareerstats 的对应赛季、球队 GS；两队 GS 各 410。新增 Kevon Harris（HOU）、Tyler Smith（HOU）、Dante Exum（DAL）均通过完整 career 响应无对应赛季球队出场行及官方历史登记观察确认零出场，未使用缺失值占位。

两队各 82 场常规赛，去重为 160 场。`2025-26-historical-rosters.json` 包含所有比赛页面 active roster 与 inactives 的人员/号码、字段位置、URL、比赛日期、UTC 抓取时间与页面 SHA-256。该覆盖证明比赛名单出现过的人已收集；仍不能证明覆盖两场比赛之间签约并离队、从未进入比赛名单的人，也不等同于完整合同登记历史。

`2025-26-registration-evidence.json` 保留 43 名不同球员的官方 commonplayerinfo、补查 career 及官方交易记录。current profile 仅辅助位置核验，不能取代历史号码。`2025-26-registration-review.json` 汇总每人的来源、号码冲突与交易记录，不是可直接给视觉识别使用的正式名单。

## 为什么不能自动补日期和号码

- Newton：HOU 主名单字段为 13，部分 inactive 字段为 00；当前 profile 已是 SAC 00。不能把 inactive 的 00 当作 HOU 换号日期。
- Crawford：首场 inactive 为 24，随后主名单为 27；不能仅凭这一差异断言正式赛季换号。
- Tyler Smith：HOU 21；DAL 主名单为 23，部分 inactive/赛季快照仍为 21。必须区分球队，不能复用快照号码。
- Poulakidas：历史页面部分号码为空；DAL 主名单与官方球员页面有 1，可用于号码交叉核验，但空值不代表换号。
- Robinson-Earl：DAL 五场主名单元数据写 25，同场 Washington 也写 25。保留冲突，未任意选另一个号码。官方交易记录确认 2026-01-15 签 10-Day Contract；合约约定、是否续约和最终日期需要单独核实，不能用最后出场日推断。
- current profile 的球队/号码可能已经更新到下赛季，旧赛季 commonteamroster 也可能是季末快照。`to: null` 要求到抓取日仍在该队，不能因为交易列表没找到离队记录便认定仍在队。

官方游戏页提供比赛记录 PDF，实际下载基址是 `https://statsdmz.nba.com/pdfs/`。已尝试 20260115/20260115_UTADAL_book.pdf，当前网络代理返回 tunnel 403；同路径 stats.nba.com 返回 404。网络配置草稿更新返回 stale_base，未确认保存或生效。没有将失败请求当作内容证据，也没有猜测号码或日期。

## 复现

从仓库根目录执行：

```bash
python3 pipeline/vision/rosters/fetch_nba_sources.py --output pipeline/vision/rosters/sources/2025-26-official-sources.json
python3 pipeline/vision/rosters/fetch_historical_rosters.py
python3 pipeline/vision/rosters/fetch_registration_evidence.py
python3 pipeline/vision/rosters/audit_registration_sources.py
```

历史采集只支持已完成 82 场的常规赛，并支持断点续传；没有字段位置的旧缓存会重新抓取。核验器要求完整比赛覆盖，对新增零出场者检查完整 career 响应，并检查两队首发次数总和。真实登记区间核验后，再使用 build_roster.py 生成正式名单；不改历史 `pipeline/vision/roster.json`。
