# Team04 名单与 xFG 模型交付

分支：`feat/team04-rosters-xfg`。这是接入框架；正式名单与真实模型结果仍待数据，不声称任务 A/B 已完全验收。

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

当前验证：8 项新增测试、4 项原有后端测试通过；使用 /tmp 中标记为 SYNTHETIC TEST ONLY 的 CSV 完成生成器与模型 CLI 验证，确认名单 JSON、模型 JSON、PNG 和 5 个候选事件可生成。正式数据结果尚未生成。检查了 main、events-possession-eval、p1-build、p2-build、vision-build；只有 events-possession-eval 含名单种子，其 ID/统计为 null，不能作为正式名单复用。

任务书已授权提交分支与 PR。若仓库写入或 GitHub API 权限不可用，保留本地提交/补丁，提供具体失败原因；PR 描述应明确“数据接入框架，真实数据待接入”，不能声称正式名单或增强回放已经完成。团队群地址未提供，暂不发送消息。
