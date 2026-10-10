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
