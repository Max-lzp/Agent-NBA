# 项目迁移与页面检查

本次检查基于本地 HEAD `6caab8e`，通过 Chromium 实际加载页面并操作控件；未把页面 HTTP 200 当作完整业务成功。

## GitHub 与迁移

- 原仓库 `jasonlovegugu/nba-hackathon-team04` 的 Git 读取正常。
- 用户已创建个人仓库 `Max-lzp/Agent-NBA`。通过 Git 写入成功，原始 36 个分支已迁移并逐项核对提交哈希。GitHub API 仍返回 `Forbidden`，无法通过 API 配置 Pages 或查询 Actions 状态。
- 原工作目录不重置、不改写远端；此前新增但未提交的作品集页面、工作流和文档单独保留。
- 完整工作目录归档（包含 `.git` 及未提交文件）、远端全引用 Git bundle、校验清单、浏览器截图与 JSON 检查结果位于 `/workspace/shared/deepcourt-migration/`。原始历史已推送到个人远端；新增作品集内容作为后续独立提交保留。
- Git 迁移可保留提交、分支和标签；GitHub Issues、PR、Actions 历史、仓库设置及云端对象不包含在 Git bundle 中。

## 页面实际结果

| 入口 | 实测 | 原因 / 范围 |
| --- | --- | --- |
| `web/index.html` 原上传页 | HTML 和脚本正常，提交任务失败 | 任务创建依赖 `/api/jobs`；静态服务不能提供 AWS API |
| `web/game.html?mode=xinfan` 原回放页 | 无 JavaScript 异常，但没有可播放视频，显示“考试片生成中” | `examOnly: true` 隐藏现有训练片；当前清单只有 flagg 和 amen |
| `web/game.html?mode=xinfan&clip=flagg` 指定训练片 | 播放失败 | 默认请求 `assets/flagg-en.mp4` 返回 404，视频错误码 4 |
| `portfolio/index.html` 作品集展示页 | 可用 | 三种模式 3/3 切换成功；1280px 与 390px 无横向溢出；无 JavaScript 异常 |

`portfolio/` 是明确标注合成数据和预写文案的产品交互示例，不是原视频分析产品的完整替代。

### 后续实现：无 AWS 回放第一阶段

新增 `portfolio/player.html`，已用浏览器验证合成教学片的真实播放、事件跳转暂停、观看模式、自选本地视频、标记增删、JSON 导出回导和本地关键词查询。错误时间线与不匹配的视频数据会被拒绝；检查中没有 AWS 或外部请求。首页已增加入口，详见 [local-replay.md](local-replay.md)。原 `web/` 的比赛版本未改写；自动识别与实时大模型问答仍未恢复。

## 素材与后端

- 本地 `demo-possession.mp4` 可被 ffprobe 解析：约 10.03 秒，H.264 / AAC。
- 本地 `flagg-replay.mp4` 可被 ffprobe 解析：约 26.55 秒，H.264 / AAC。
- 这些文件不是回放清单当前请求的 `flagg-en.mp4`、`flagg-broadcast.mp4`、`amen-en.mp4` 等，不能仅改文件名假设时间轴与标注一致。
- Git 中没有完整云端媒体桶、模型权重或可用 AWS 后端；完整拷贝 Git 仓库不会自动恢复这些资源。
- 比赛素材公开分发范围需要遵守 `LICENSING.md` 中的既有约定；私有保存源码与公开发布视频是不同操作。

## 后续顺序

1. 已完成：向 `Max-lzp/Agent-NBA` 复制原始分支历史；不更改仓库可见性。
2. 将后续作品集修改作为独立提交。保留团队来源与个人改动边界。
3. 优先发布已通过检查的 `portfolio/` 静态页面。
4. 若恢复原播放器，先补齐与事件时间轴一致的媒体清单，再调整默认入口与静态模式，最后决定是否恢复云端问答和上传分析。
