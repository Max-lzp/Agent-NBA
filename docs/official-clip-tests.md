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

## 网络恢复后的实际结果

环境配置生效后，NBA 官方事件页已能返回 HTTP 200。原三个出手链接也返回 200，但它们返回的文件完全相同：869,898 字节、15.015 秒、SHA-256 `afcfdc3190903e3fc13c56c054fa40c39d1775af4a53d40ce040d6a25ea3ad3e`。抽帧确认为“VIDEO NOT AVAILABLE”占位画面，不能用于球员/事件测试，继续保留为未通过。

因此新增 NBA Video Rulebook 官方规则示例作为可用的教学样本，而不尝试绕过原出手 CDN 的访问限制。网络无须再次申请放行。

探测报告在 `web/replay/official-clips-validation.json`。这些原始统计记录依然有来源价值，但不因统计记录真实就视为视频可用；当前没有为它们添加出手帧和人物框。

## 已通过的官方规则样本

网站 `rulebook.html` 提供两个真实示例，来源为 NBA 官方 [Video Rulebook](https://videorulebook.nba.com/)：

- [挡拆中伸出双臂推防守人的非法掩护](https://videorulebook.nba.com/archive/illegal-screen-pickn-roll-screener-pushes-defender-with-two-hands-4/)，约 23.09 秒。
- [收球后超过允许步数的走步](https://videorulebook.nba.com/archive/travel-on-the-perimeter-gather-then-too-many-steps/)，约 27.99 秒。

来源页面、MP4 地址、视频内容哈希、媒体格式和时长均已通过普通 HTTPS 请求核对。实际下载的同一份视频已通过本地 Chromium 解码与播放测试，并检查官方画面；浏览器测试并非合成动画替代。这与访客浏览器到 NBA CDN 的实时连通仍是不同验证范围。

网站直接引用 NBA 原站视频，不重新托管这两段文件。中文解释根据官方判例整理，可切换老球迷、新球迷、一起看三种说明。没有给规则示例添加不存在的出手概率、逐帧追踪或比赛事件 ID。

数据和证据位于 `data/official-rulebook/clips.json`、`web/replay/official-rulebook.json` 和 `rulebook-validation.json`。

```bash
python3 scripts/package_rulebook.py --probe
python3 scripts/qa/check_rulebook.py --media-dir /tmp/agent-nba-rulebook-cache
```

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
