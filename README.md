# Deep Court

> 主人：A（部署线）。别人可以提意见，只有主人改。
> 队名：Team 04 ｜ NBA × AWS Broadcast Code 12H ｜ 珠海 10.10 初赛 / 澳门 10.11 决赛

**一句话价值主张**（评委第一眼看的，30 字以内）：让每一次出手被重新讲述一遍——深度数据驱动的 AI 增强比赛画面。

## 交付标准（必读，10.7 校准）

**交付物不是「一个视频」，而是一个 Web 页面。** 硬性要求第 2 条：必须以 CloudFront 发布且具备 **Web 交互界面**——录屏视频、纯 API、CLI 都不算满足。

五件套：平台部署的 Agent + 代码仓库（Private）+ 说明文档 + 演示视频 + CloudFront 公网链接（承载考试视频完整成片，链接有效期覆盖 20:00–22:20 审核时段）。

评分对应：UX 25 分明写评「操作性」，评委点开 CloudFront 链接看交互页面打分。所以架构是 **视频打底、交互拿分**：

```plaintext
ffmpeg 预渲染增强成片（保底：打开即看，不点任何交互也成立）
+ Web 播放器 + Canvas/DOM 叠加层（currentTime 驱动，毫秒级同步）
+ 三层信息：默认轻量常驻 → 暂停/点击展开深度解读 → 时间线双向绑定
```

两条纪律：①演示视频照录，是提交要件 + 评委无脑看的保底路径；②默认画面必须自解释，展开层是「越看越有」不是「不点不懂」。

## 提交口径（10.9 组委会补充）

1. **作品页标明 Team ID。** 本队是 Team 04，评委打开页面就能看见，不要只写在仓库名里。
2. **提交的 CloudFront 链接必须直接打开最终成片。** 评委看的是视频效果，不会在我们的页面上选文件、等分析跑完。界面若是「Open file / 选择视频，然后再处理」，会拖慢评审；处理卡住出不来，0 分概率很大，而且责任分不清。上传入口可以留给自己用，不能当作品链接。
3. **同一域名下加 `/readme`。** 组委会强烈建议作品链接后面有这一页，介绍作品亮点。不求美观，把亮点写清楚即可。例如成片在 `https://xxxx.cloudfront.net/`，说明在 `https://xxxx.cloudfront.net/readme`。

## 快速开始

```bash
# 0. 前提：AWS CLI v2。本地构建不需要比赛共享账号。
#    部署用 profile team04（本队账号 405496568869，us-east-1）。
#    主办方共享账号 278477606068 已关闭。
aws sso login --profile team04
aws sts get-caller-identity --profile team04 --region us-east-1

# 1. 本地构建（不离开仓库根目录）
(cd cdk && npm install && npm run build && npx cdk synth)

# 2. 评委成片 + 部署。先生成回放，再部署。
#    根路径打开的是回放页 game.html（给评委）。
#    发布台在 /hub.html，只管理 GitHub 同步和 CloudFront 部署，不要把 hub 当提交页。
#    上传页仍在 /index.html。部署后把 CloudFront 根 URL 写进 web/config.js 的 submitUrl。
#    GPU 配额为 0，不带 Batch。Polly 未开通时解说留在本地成片里，不带 P2。
#    账号还不能登录时，脚本会停在本地成片，不报部署失败。
sh scripts/publish.sh
#    若只要手动部署：
#    cd cdk && npx cdk deploy Team04DeepCourt -c enableP1=false -c enableP2=false --profile team04

# 3. 验收闭环（把 <api> 换成栈输出 ApiUrl，不要带末尾多余空格）
curl -sS -X POST "<api>jobs" -H 'content-type: application/json' \
  -d '{"gameMeta":"dal-vs-hou","filename":"clip.mp4","contentType":"video/mp4"}'
# 用返回的 uploadUrl 上传视频：curl -X PUT -H "Content-Type: video/mp4" --upload-file clip.mp4 "<uploadUrl>"
# 轮询直到 status 为 DONE：curl -sS "<api>jobs/<jobId>"
# 读事件：curl -sS "<api>jobs/<jobId>/events"
# 浏览器打开栈输出 CloudFrontUrl 做自测。
# 提交门户的链接是成片回放页（页面上有 Team 04），不是上传首页。
# 同域名 /readme 写作品亮点，不求美观。

# 4. 提交作品 URL（门户 Submit your entry，截止 2026-10-10 20:00 北京时间）
```

## 从干净账号重跑

考试片出完之后再部署。步骤、context 开关和「默认部署不会替换哪些资源」写在 [docs/deploy.md](docs/deploy.md)。

```bash
cd cdk && npm ci && npx cdk deploy Team04DeepCourt --profile nba --require-approval never
```

默认仍用已经烤好的镜像 `deepcourt-tracker:ready`，并由 Step Functions 接 `raw/` 上传。没有这枚镜像时才加 `-c buildTrackerImage=true`。要回到现在的 S3 通知、不自动提交 Batch：`-c enableOrchestration=false`。

## 技术栈

| 环节 | 服务 |
| --- | --- |
| 智能体 | Bedrock AgentCore（硬性要求） |
| 模型 | Claude Sonnet 4.6（`global.anthropic.claude-sonnet-4-6`，文本/解说）+ Qwen3 VL 235B（`qwen.qwen3-vl-235b-a22b`，视觉） |
| 存储 | S3（每队预置桶） |
| 渲染 | ffmpeg（本地调试不占账号额度） |
| 交付 | CloudFront + Web 界面（硬性要求） |
| 基础设施 | AWS CDK（硬性要求，全部资源 us-east-1） |

## Bedrock AgentCore 智能体做什么

比赛要求智能体跑在 Amazon Bedrock AgentCore 上。智能体逻辑如果只在自己电脑的进程里，或者只是一段循环在调大模型，都不算数。

Deep Court 的分析师就住在这个运行时里，名字叫 DeepCourtAnalyst。一条回放的事件清单写进 S3 之后，Lambda `analyze_job` 会叫醒它。它自己决定要查什么：这份清单、官方逐回合和投篮质量、火箭和独行侠的赛季三分区、以及认人结果。然后用 Claude Sonnet 4.6 写成中英文解说、定格卡上的一句话，和全片三条亮点。模型号必须是 `global.anthropic.claude-sonnet-4-6`。每句话都标了数据来源。工具里没有的数字和人名，它不说。

观众也可以就这段回放提问。问题走网站的 `/api/agent/ask`，还是同一个分析师回答，不会另外编一套。

如果运行时还在冷启动，或者这次没叫起来，收清单的 Lambda 会自己把事件写进页面，回放不会卡住。解说会等运行时醒了再出现。

上次探测时，运行时报「30 秒内没完成初始化」。原因不是模型慢，而是代码包里只有 `agent.py`。这个部署方式不会在启动时安装 `requirements.txt`，入口却去导入没打进包的 `bedrock-agentcore`。进程一起床就崩了，报错被说成初始化超时。现在入口先用 Python 标准库把健康检查端口打开，boto3 打进代码包，第一次真正分析时才加载。

## 目录

本仓库只放交付物。每个文件一个主人。

```text
repo/
├── README.md            ← 项目名/队名/一句话价值主张/一键部署步骤（评分要件，主人：A）
├── docs/
│   ├── architecture.md  ← 架构说明（决赛技术分 18 分的素材库，主人：A）
│   ├── pipeline-map.md  ← 数据管道全景：每一步用哪个 AWS 服务。和 web/readme.html 一起改
│   ├── data-notes.md    ← 深度数据格式、字段口径、对齐方案（主人：B）
│   └── decisions.md     ← 关键决策记录（主人：呈现线）
├── cdk/                 ← 基础设施
├── agent/               ← AgentCore Agent 代码
├── pipeline/            ← 视觉识别 + ffmpeg 渲染管线
└── web/                 ← 前端
```

协作文档不进本仓库，权威版本在飞书文件夹：<https://hu6srn8qnl.feishu.cn/drive/folder/Ndbufq6uLlAgawdFtnkczyGaneh>

| 飞书文档 | 主人 |
| --- | --- |
| 作战中枢 | 当次记录人，当场确认 |
| 踩坑记录 | 踩坑的人当天写入 |
| 比赛日 Runbook | 呈现线。9 号碰面定稿 |

## 声明

- 解说文案由 AI（Claude Sonnet 4.6）生成
- 训练用自备视频来源：（比赛日补充并披露）

> ⚠️ 仓库必须保持 Private；不要提交任何密钥/凭证（用 Secrets Manager / SSM / GitHub Secrets）。

## 视觉依赖许可

检测和跟踪用 [abdullahtarek/basketball_analysis](https://github.com/abdullahtarek/basketball_analysis)（MIT），源码在 `pipeline/vision/vendor/basketball_analysis/`。推理走 Ultralytics YOLO，Ultralytics 是 **AGPL-3.0**：比赛原型可用，若以后闭源分发需要换掉或遵守 AGPL。球场单应变换用同仓库的 `Homography`，接口对齐 roboflow/sports 的 ViewTransformer。球衣号码用 Roboflow 篮球号码 OCR 数据集的本地权重，运行时不调用 Roboflow API。
