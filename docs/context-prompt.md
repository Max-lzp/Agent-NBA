# 项目上下文 Prompt（Cursor / 队友通用）

> 用法：开新会话时把下面整段发给 Cursor（或发进群给队友），项目背景一次讲清。

---

## 背景

我们在打 NBA × AWS「Broadcast Code 12H」黑客松：做一个 AI Agent，输入 NBA 深度赛场数据 + 一段真实比赛视频，输出带数据可视化的「AI 增强比赛画面」，通过 Web 界面交付。

- 珠海初赛 10.10（07:40–22:30，20:00 硬截止），澳门决赛 10.11
- 白天 9 小时用训练视频构建，18:00 发布考试视频，2 小时调优后提交
- 评分：机器 35 + 人工 55（数据准确性 30 + UX 25）+ 知识问答 4 + 投篮 6
- 仓库：https://github.com/jasonlovegugu/nba-hackathon-team04（Private，队号 Team04）

## 三个硬性要求（缺一个直接影响成绩）

1. 智能体必须跑在 **Amazon Bedrock AgentCore**（本地进程或纯 LLM 循环不算）
2. 交付必须走 **CloudFront** 公网链接 + **Web 交互界面**（CLI/录屏不算）
3. 基础设施必须用 **AWS CDK** 定义（控制台手点、Terraform/SAM 不算）

## 环境纪律（踩坑高发区，逐条遵守）

- 所有资源只在 **us-east-1**；每条 AWS 命令都带 `--region us-east-1`
- 双 profile：`aws sso login --profile nba-sso` 登录；**日常所有操作一律 `--profile nba`**（用 nba-sso 会报 AWSReservedSSO 错）
- **不要跑 `cdk bootstrap`**（主办方已做好）；`cdk.json` 必须含 permissionsBoundary（NBAHackathonParticipantBoundary），否则 iam:CreateRole 报错
- CDK 部署用 `cdk deploy --profile nba`
- SSO 12 小时过期，失效就重跑 login
- Bedrock 只有两个模型可用：
  - 文本/解说：Claude Sonnet 4.6 → `global.anthropic.claude-sonnet-4-6`
  - 视觉：Qwen3 VL 235B → `qwen.qwen3-vl-235b-a22b`
- 运行时调 Bedrock 花比赛账号额度：抽帧先按 2–4fps，能省则省；ffmpeg 渲染本地调试
- 严禁把密钥/凭证写进代码或 push 进仓库（用 Secrets Manager / SSM）
- 代码随时 commit + push；评分取截止前最后一次有效提交（20:00 硬截止）

## 标准架构（官方口径）

```plaintext
S3（数据/成片）→ AgentCore（Agent 编排）→ Bedrock 双模型（Qwen3 VL 识画面 / Claude 解读+解说）
→ ffmpeg 叠加渲染 → S3 → CloudFront + Web 界面
```

## 数据事实

- 官方深度数据**只有出手瞬间点位，没有逐帧轨迹**（赛题边界明确）
- 训练视频两段各约 22 秒：弗拉格（独行侠）2月1日、阿门·汤普森（火箭）1月4日，1080p59.94
- 数据 ↔ 视频时间对齐是隐藏难点，首选方案：Qwen3 VL 读转播记分牌时钟反推对齐
- 指标口径（xFG% / 引力 / 关键度）见 docs/data-notes.md 和指标口径速查

## 当前阶段：赛前模拟期（10.7–10.8）

三条线并行，验收标准见 docs/runbook.md 赛前清单：

- A 部署线：CDK 最小栈 + AgentCore 最小 Agent + 模型压测
- B 视觉线：抽帧 → Qwen3 VL 识别 → 结构化事件（JSON schema 待定稿）
- C 呈现线：ffmpeg 叠加样式 + Web 界面 + 最小闭环（下载视频→叠字→传 S3→页面可播）

## 仓库文件主人（避免改乱）

| 文件 | 主人 |
| --- | --- |
| docs/architecture.md | A（部署线） |
| docs/data-notes.md | B（视觉线） |
| docs/decisions.md | C（呈现线） |
| docs/runbook.md | 全员，9 号定稿 |
