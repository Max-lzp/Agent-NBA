# 架构说明

> 主人：A（部署线）｜状态：建设中（模拟期跑通后更新为正式版）

## 总链路

```plaintext
① 官方数据 + 视频 ──► S3 桶
② AgentCore Agent 读取（编排中枢）
③ Bedrock 双模型：Qwen3 VL 识画面 / Claude Sonnet 4.6 解读+解说
④ ffmpeg 把数据可视化叠进视频 → 成片回传 S3
⑤ CloudFront 对外发布：成片 + Web 交互界面
⑥ 提交 CloudFront URL → 人工评分看链接 / 机器评分拉仓库重跑
⑦ 全部资源由 CDK 定义部署（us-east-1）
```

## 组件分工

| 组件 | 职责 | 关键风险 | 状态 |
| --- | --- | --- | --- |
| S3 | 数据/成片存储 | 桶策略与 CloudFront 的 OAC 配置 | ⬜ |
| AgentCore | Agent 承载（硬性要求） | 运行时权限、工具调用 schema | ⬜ |
| 本地视觉管线 | YOLO/ByteTrack 检测跟踪 + 球场关键点 + 球衣号码 OCR（开源组件，CPU 本地推理，见 data-notes.md §0） | 预训练权重对转播机位的泛化 | ⬜ |
| Qwen3 VL | 记分牌时钟读取、战术事件判定、难帧兜底（不再逐帧识别） | 稀疏关键帧上的准确性 | ⬜ |
| Claude Sonnet 4.6 | 战术解读、解说文案、衍生指标解释 | 篮球常识幻觉 → 人工校验 | ⬜ |
| ffmpeg | 叠加渲染（本地调试 + 云上批处理） | 字体/中文渲染、性能 | ⬜ |
| CloudFront | 公网交付（硬性要求） | 缓存刷新、链接有效期覆盖 20:00–22:20 | ⬜ |
| Web | 交互界面（时间线 + 数据-画面双向绑定） | — | ⬜ |

## 待补

- [ ] 正式架构图（docs/architecture.png）
- [ ] 数据事件 ↔ 视频时间对齐方案（与 B 共同确定，见 data-notes.md）
- [ ] AgentCore 工具清单（Agent 能调哪些动作）
