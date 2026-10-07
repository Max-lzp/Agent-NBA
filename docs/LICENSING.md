# 开源合规清单（LICENSING）

> 维护人：TEAM 04 · 最近更新：2026-10-07
> 本页与 `web/arch.html`「架构与开源合规」一致；评审时以本文件为准。

## 技术清单

| 项目 | 用途 | 许可 |
|---|---|---|
| Ultralytics YOLO11 | 球员 / 篮球检测 | AGPL-3.0 |
| ByteTrack | 多目标跟踪 · 球员 ID 保持 | MIT |
| Roboflow Supervision | 标注渲染 · 热力图 · 跟踪可视化 | MIT |
| FFmpeg（官方 LGPL 构建） | 视频切片 · 烧录叠加 · 格式转换 | LGPL-2.1 / GPL-2.0（未启用 GPL 编码器） |
| Vite + React | 回放页前端 · Canvas 叠加层 | MIT |
| abdullahtarek/basketball_analysis | 检测跟踪参考实现（vendor 目录） | MIT |

## 合规说明

1. **AGPL-3.0（YOLO）—— 当前用法不触发 copyleft。** 本作品作为评审用内部工具、不对外提供网络服务，不构成「向外部传播」；若赛后对外发布，改用 MIT 系检测模型或获取 Ultralytics 商业授权。
2. **MIT（ByteTrack / Supervision / React 等）。** 保留原始版权声明与许可文本，随仓库附 LICENSE 副本。
3. **FFmpeg（LGPL 构建）。** 使用官方动态链接构建、未修改源码、未启用 GPL 编码器，无衍生作品开源义务。
4. **比赛画面与数据版权。** 比赛录像及数据版权归 NBA / 赛事方所有，仅用于本次黑客松评审，不做二次分发。训练用自备录像来源随交付披露。
