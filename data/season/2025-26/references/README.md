# NBA 官方热区图参考

这里 6 张 PNG 是 **NBA 官方 Stats 页面实际渲染的图表截图**，不是 Team04 重绘。NBA.com 水印、球员、赛季标题保留；参考来源与截图 UTC 时间、SHA-256 在 official_chart_references.json。

| 球员／赛季 | 图表类型 | 图片 |
|---|---|---|
| Cooper Flagg，2025–26 常规赛 | 区域命中率 | official_flagg_2025-26_zones.png |
| 同上 | 六边形投篮图 | official_flagg_2025-26_hex.png |
| 同上 | 连续出手热区 | official_flagg_2025-26_heat.png |
| Kevin Durant，2024–25 常规赛 | 区域命中率 | official_durant_2024-25_zones.png |
| 同上 | 六边形投篮图 | official_durant_2024-25_hex.png |
| 同上 | 连续出手热区 | official_durant_2024-25_heat.png |

官方入口：

- [弗拉格 2025–26 Shot Charts](https://www.nba.com/stats/events/?ContextMeasure=FGA&PlayerID=1642843&Season=2025-26&SeasonType=Regular%20Season&flag=3&sct=zone&section=game)
- [杜兰特 2024–25 Shot Charts](https://www.nba.com/stats/events/?ContextMeasure=FGA&PlayerID=201142&Season=2024-25&SeasonType=Regular%20Season&flag=3&sct=zone&section=game)
- [NBA 投篮区域统计](https://www.nba.com/stats/players/shooting?Season=2025-26&SeasonType=Regular%20Season)
- [官方统计词汇](https://www.nba.com/stats/help/glossary)

打开官方 Events 页面后可切换 Shot Plot / Hex Map / Shot Zones / Heat / Stream。Heat 表示出手的空间分布，不能直接当命中率；Shot Zones 才提供区域表现比较。杜兰特 2024–25 图仅作旧版官方视觉参考，不能混入本次 2025–26 数值。

Team04 自绘的真实 2025–26 图在 ../visualizations/，来源标注及数据口径另见上级 README。

截图复现（可选，需要 Playwright 和 Chromium，以及正常的代理/证书信任）：

```bash
python3 -m pip install playwright
python3 pipeline/vision/models/season/capture_official_references.py
```

脚本使用 /usr/bin/chromium、环境 HTTP 代理，只访问公开 NBA 页面，不登录账号；保持 TLS 验证。当前环境 Chromium 使用证书数据库需要额外文件写权限，采集时经过环境审批。没有保存账号密码或浏览器用户数据到仓库。视频加载失败不影响官方 Shot Charts 的图表读取，本次只保存图表区域。
