# Team04 名单与 xFG 模型交付

分支：`feat/team04-rosters-xfg`，草稿 PR #8。已取得官方赛季统计及深度 CSV，生成真实 xFG 模型结果；正式名单仍待历史登记核验，高 Leverage 回放和训练视频内偏移尚未验收。

## 官方深度数据与真实模型结果

data/deep 已包含 NBA 官方 125 人出手难度、234 人引力、234 人 Leverage，以及弗拉格 23 次/阿门 18 次出手的 CSV。无需另找账号或上传 CSV。原始 JSON、URL、UTC 抓取时间及 SHA-256 在 raw/ 和 manifest.json；字段定义与样本限制见 data/deep/README.md。

```bash
cd /workspace/nba-hackathon-team04
source /workspace/.venvs/team04/bin/activate
export XDG_CACHE_HOME=/workspace/.cache
export MPLCONFIGDIR=/workspace/.cache/matplotlib
python -m pip install --cache-dir /workspace/.cache/pip -r pipeline/vision/models/requirements-model.txt
# 可选刷新；已有真实快照时无需每次下载
python pipeline/vision/models/fetch_deep_data.py --output-dir data/deep
python pipeline/vision/models/run_official_model.py
```

真实结果在 pipeline/vision/models/results：8 名两队球员的 xfg_deviation_output.json、xfg_decomposition.png，及 41 条单球叠加数据 shot_sequence_output.json。后者选 5 个大残差候选，未冒充高 Leverage 回合。runner 校验原始与 CSV 哈希；基准为返回 125 人的 FGA 加权 xFG，不是完整联盟实际 FG%。

## 2026-10-10 官方统计更新

stats.nba.com 现在可访问，已提交 `pipeline/vision/rosters/sources/2025-26-official-sources.json`（完整接口响应、URL、抓取时间及 SHA-256）与 `2025-26-verified-totals.csv`。最初火箭 17 人、独行侠 24 人的快照候选取得真实 ID/GP/GS/总分钟；随后通过历史游戏页面补齐为火箭 19 人、独行侠 25 人（44 条球员—球队统计记录），两队 GS 各合计 410。GP/MIN 来自球队过滤的 leaguedashplayerstats；GS 来自 playercareerstats 的 2025-26、对应球队行，因为 leaguedashplayerstats 不提供 GS。career MIN 为整数，校验其与精确总分钟的差不超过 1 分钟；输出保留 leaguedash 的精度。

```bash
python3 pipeline/vision/rosters/fetch_nba_sources.py \
  --output pipeline/vision/rosters/sources/2025-26-official-sources.json
```

候选是 commonteamroster 快照与该队赛季统计的并集，不宣称覆盖所有未出场且赛季中已离队的登记人员。球队过滤统计返回的 TEAM_ID/TEAM_ABBREVIATION 可能仍指球员最新所属队（如 Davis），所以不能据此丢弃已离队球员；脚本通过 career 的赛季-球队行核对 GP、GS、MIN。

零出场者（VanVleet、Irving）由官方名单快照及两份完整出场统计中无当季出场行共同确认，未将网络失败或缺失值直接改为零。位置已补查官方 commonplayerinfo；当前还缺经过独立复核的完整历史登记日期/号码；John Poulakidas 的快照号码为空、两名球员显示 21 号，不能直接把快照当时段名单。待这些字段核验后，用 verified-totals.csv 和真实 registrations.csv 生成目标 2025-26.json。详细待核验字段逐人保存在 sources JSON 的 candidates 中。

## 名单输入与生成

请从门户、api.nba.cn 或团队已核验导出中提供两张 CSV，保存源标识和抓取日期：

- registrations.csv：`team,player_id,name,short_name,jersey,position,from,to`。包含赛季内所有登记记录，包括双向和零出场人员；交易或换号各写独立日期区间。号码使用文本，保留 00；日期来自真实登记/交易记录，不用首场出场日期猜登记日期。
- totals.csv：`team,player_id,games_played,games_started,minutes_total`。每个球员-球队一行，使用该队 2025-26 赛季总值，不使用跨队 TOT 行或平均每场分钟。零出场者也需要核实后的显式零值，不将缺失值自动改成零。

```bash
cd /workspace/nba-hackathon-team04
python3 pipeline/vision/rosters/build_roster.py \
  --registrations data/deep/registrations.csv --totals data/deep/totals.csv \
  --source '<真实官方导出标识或来源 URL>' --fetched-at '<真实抓取日期 YYYY-MM-DD>'
```

输出为 `pipeline/vision/rosters/2025-26.json`，遵守同目录 README，不触碰 `pipeline/vision/roster.json`。固定已知 short_name，新增人员必须明确提供审阅后的 short_name。按总分钟降序，记录输入哈希。换号后的多行重复该球员-球队**赛季总统计**；消费端不能把这些区间的 MIN 再相加。脚本检查完整字段、日期区间、统计范围、遗漏匹配；无法仅凭 CSV 判断来源真实性或历史登记是否齐全，仍需官方复核。

## 真实模型运行

```bash
source /workspace/.venvs/team04/bin/activate
export XDG_CACHE_HOME=/workspace/.cache
export MPLCONFIGDIR=/workspace/.cache/matplotlib
python -m pip install --cache-dir /workspace/.cache/pip -r pipeline/vision/models/requirements-model.txt
python pipeline/vision/models/xfg_decomposition.py \
  --input data/deep/shot_difficulty.csv --unit fraction \
  --baseline '<已核验且可比的基准概率>' --baseline-source '<基准出处>' \
  --source '<CSV官方来源标识>' --fetched-at '<真实抓取日期 YYYY-MM-DD>' \
  --output-dir pipeline/vision/models/results
```

字段默认 PLAYER_NAME、AVGSHOTQUALITY、FGPCTABOVEEXPECTED；若列名不同，使用 `--name-column`、`--quality-column`、`--excess-column`。详细公式、数据定义与回放输入见 docs/model-notes-xfg.md。不要以未核验的基准值执行上述命令。

输出为 results/xfg_deviation_output.json 与 results/xfg_decomposition.png。有真实事件级关键度和视频对齐后，增加 `--replay-input data/deep/replay_events.csv --leverage-definition '<官方定义>'`，生成最多 5 个高关键度回放候选。

## 验证与提交

```bash
python -m unittest discover -s pipeline/vision/models -p 'test_*.py' -v
python -m unittest discover -s lambdas -v
git diff --check
```

测试使用明确标记的合成数据验证公式守恒、单位转换、非法输入拒绝、关键度排序、号码 00、short_name、零出场、换号区间；它们不证明 NBA 数据已取得。

验证包括模型/名单契约测试、原有后端测试、合成输入的 CLI 检查，以及官方抓取与真实 CSV→JSON/PNG 流程。其他分支中的名单种子 ID/统计为空，未作为正式名单复用。正式深度结果已生成；历史号码/登记缺失仍阻止正式名单验收，详见 docs/roster-history-notes.md。

任务书已授权提交分支与 PR，现已推送并创建草稿 PR #8。PR 描述区分已取得的真实数据/模型结果与尚未完成的历史名单/高 Leverage 回放。团队群地址未提供，暂不发送消息。
