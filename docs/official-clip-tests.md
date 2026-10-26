# NBA 官方片段样本封装

入口：站点首页 →「NBA 官方片段测试」，对应 `official-clips.html`。沿用原比赛主题，不替换已恢复的首页回放。

## 样本与出处

从仓库中已保存的 NBA 官方 `shotqualityvideologs` 原始响应选取三条记录。不是新的网页检索结果，也没有把已有比赛成片重命名为其他回合。

| 球员 | 比赛 / 事件 | 记录 | xFG | 官方记录时长 |
| --- | --- | --- | --- | --- |
| 弗拉格 | 0022500701 / 63 | 第一节 7:35，两分命中 | 71.6% | 8.483 秒 |
| 弗拉格 | 0022500701 / 228 | 第二节 8:24，三分未中 | 38.2% | 9.716 秒 |
| 阿门·汤普森 | 0022500491 / 18 | 第一节 10:53，两分命中 | 40.0% | 11.533 秒 |

原始响应在 `data/deep/raw/flagg_shot_sequence.json` 和 `amen_shot_sequence.json`，来源 URL、2026-10-10 采集时间和 SHA-256 在 `data/deep/manifest.json`。封装器核对原始文件哈希，再按官方事件 ID 导出，保留官方 CDN 地址、元数据与来源证据。

来源目录为 `web/replay/official-clips.json`。不打包或重新托管 NBA 官方新片段，不发送伪装浏览器的请求，不绕过源站访问策略。点击加载按钮后，浏览器直接尝试 NBA 官方视频源；也可以打开官方事件页或原视频链接。

## 哪些测试通过了

- 两份原始数据的 SHA-256 一致，三条记录的比赛、事件、结果与统计来自同一原始响应。
- 简化预期得分 = 出手分值 × xFG，实际得分 = 出手分值 × 命中结果；不当作包含罚球和篮板的完整回合价值。
- 使用明确的本地测试动画验证播放器成功路径、元数据展示、时长不符拒绝、CDN 403 提示和手机布局。
- 原比赛回放的浏览器回归测试仍通过。

**本地测试动画只用于自动化测试，不在官方样本页面替代真实视频。这些通过结果不证明 NBA 真实媒体已播放成功。**

## 当前实际阻塞

本次云环境访问 `www.nba.com`、`videos.nba.com` 和 `stats.nba.com`，连接层返回 `Tunnel connection failed: 403 Forbidden`。三条媒体探测均记录为 `network_blocked`，不是通过，也不能判断 NBA 源站本身已拒绝或链接失效。

网络配置草稿已保留现有规则并添加 `www.nba.com`、`nba.com`、`videos.nba.com`、`stats.nba.com`。草稿保存不自动应用；用户需在云环境设置中保存并发布相关配置后再重试。

探测报告在 `web/replay/official-clips-validation.json`。放行后仍须验证真实文件、视频流与时长，并核对画面中的球员、比分、节次及事件。仅凭官方事件 ID 和相同视频时长不能证明逐帧对应，当前没有添加出手帧和人物框。

## 重现

```bash
# 离线封装，保留在线探测报告
python3 scripts/package_official_clips.py

# 实际访问 NBA 视频源，临时检查 MP4/视频流/时长，保存探测结果
# 任一失败时命令返回非零；不把失败掩盖为成功
python3 scripts/package_official_clips.py --probe

# 来源与界面回归，仅本地模拟媒体成功/拒绝场景
python3 scripts/qa/check_official_clips.py
```

在线探测需要 FFprobe。浏览器测试需要 Playwright、Chromium 和 FFmpeg。部署只复制来源目录和页面，不发起 NBA 请求。
