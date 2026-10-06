# 项目名（待起）

> 队名：待定 ｜ NBA × AWS Broadcast Code 12H ｜ 珠海 10.10 初赛 / 澳门 10.11 决赛

**一句话价值主张**（评委第一眼看的，30 字以内）：让每一次出手被重新讲述一遍——深度数据驱动的 AI 增强比赛画面。

## 快速开始

```bash
# 0. 前提：AWS CLI v2 + 双 profile（nba-sso / nba），详见 docs/runbook.md
aws sso login --profile nba-sso
aws sts get-caller-identity --profile nba --region us-east-1   # 须含 NBAHackathonParticipantRole

# 1. 部署（cdk.json 已配权限边界，主办方已 bootstrap，不要执行 cdk bootstrap）
cd cdk && npm install && cdk deploy --profile nba

# 2. 提交作品 URL（门户 Submit your entry，截止 2026-10-10 20:00 北京时间）
```

## 技术栈

| 环节 | 服务 |
| --- | --- |
| 智能体 | Bedrock AgentCore（硬性要求） |
| 模型 | Claude Sonnet 4.6（`global.anthropic.claude-sonnet-4-6`，文本/解说）+ Qwen3 VL 235B（`qwen.qwen3-vl-235b-a22b`，视觉） |
| 存储 | S3（每队预置桶） |
| 渲染 | ffmpeg（本地调试不占账号额度） |
| 交付 | CloudFront + Web 界面（硬性要求） |
| 基础设施 | AWS CDK（硬性要求，全部资源 us-east-1） |

## 目录

```
├── README.md          ← 本文件（评分要件：项目名/队名/价值主张/一键部署）
├── docs/
│   ├── architecture.md  架构说明（主人：A｜部署线）
│   ├── data-notes.md    深度数据格式与对齐方案（主人：B｜视觉线）
│   ├── decisions.md     关键决策记录 → 路演讲稿素材（主人：C｜呈现线）
│   └── runbook.md       比赛日作战手册
├── cdk/               基础设施即代码
├── agent/             AgentCore Agent
├── pipeline/          抽帧 → 视觉识别 → ffmpeg 叠加渲染
└── web/               Web 交互界面
```

## 声明

- 解说文案由 AI（Claude Sonnet 4.6）生成
- 训练用自备视频来源：（比赛日补充并披露）

> ⚠️ 仓库必须保持 Private；不要提交任何密钥/凭证（用 Secrets Manager / SSM / GitHub Secrets）。

## 视觉依赖许可

检测和跟踪用 [abdullahtarek/basketball_analysis](https://github.com/abdullahtarek/basketball_analysis)（MIT），源码在 `pipeline/vision/vendor/basketball_analysis/`。推理走 Ultralytics YOLO，Ultralytics 是 **AGPL-3.0**：比赛原型可用，若以后闭源分发需要换掉或遵守 AGPL。球场单应变换用同仓库的 `Homography`，接口对齐 roboflow/sports 的 ViewTransformer。球衣号码用 Roboflow 篮球号码 OCR 数据集的本地权重，运行时不调用 Roboflow API。
