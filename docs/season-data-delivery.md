# Team04 本轮数据交付入口

本轮待用户确认后合并，数据先上传 GitHub 分支与 PR，并在 PR 中 @jasonlovegugu 提醒。未改 web/、历史单场名单或视频主链路。

| 交付 | 路径 |
|---|---|
| 582 人官方进攻、效率、选秀、身高、位置及可核验臂展 | data/season/2025-26/README.md |
| 对应防守三段、样本覆盖和计数口径 | data/season/2025-26/DEFENSE-NOTES.md |
| 用户 39 人表修订审核 | data/season/2025-26/priority_team_profiles_reviewed.csv、user_submission_audit.json |
| NBA 官方 6 张区域/六边形/热区参考图 | data/season/2025-26/references/README.md |
| 5 张本队效果图及球员切换页 | data/season/2025-26/visualizations/ |
| 七类战术候选几何规则 demo | pipeline/vision/tactics_demo/README.md |
| 原始响应与可复现抓取/生成 | data/season/2025-26/raw/、manifest.json、pipeline/vision/models/season/ |

需要确认的展示选择：叠加层采用“三分→其余两分→近框”，每段展示命中/出手；防守版展示对手命中率、数值越低越好。原进攻 RA 与防守 <6ft 明确区分，距离对照版另列，不将区域差异隐藏。臂展 363 人官方历史测量可用，219 人缺失不编造。战术 demo 仅合成输入示例，规则输出候选而非真实视频已识别结论。
