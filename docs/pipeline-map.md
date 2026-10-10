# 数据管道全景

> 2026-10-10 下午。账号 `382918437747`（profile `nba`，区域 `us-east-1`）。
> 这是步骤和 AWS 服务的对照。改了管道或换了服务，同一处改动里更新本文件和 `web/readme.html` 的「数据管道」。见仓库 `.cursor/rules/pipeline-map.mdc`。

事件只来自结构化清单。模型不看图编事件。页面中文音轨是英文解说的译文，不是模型看图写的。

```mermaid
flowchart LR
  video[视频] --> machine[c7i 追踪、记分牌、ffmpeg]
  machine --> film[成片，音轨仍是原声]
  film --> en[英文原声]
  film --> asr[Transcribe]
  asr --> zhwords[Translate]
  zhwords --> polly[Polly Neural 知语]
  en --> page[CloudFront 播放器]
  polly --> page
  video --> stream[本机事件清单]
  official[官方逐回合 本地文件] --> stream
  stream --> s3[S3 structured]
  s3 --> analyze[Lambda analyze_job]
  analyze --> agent[Bedrock AgentCore]
  agent --> ddb[DynamoDB]
  analyze --> ddb
  ddb --> page
  stream --> cut[本机训练片画面]
  cut --> page
```

云上新片子走上面一行：成片出来之后才做两条音轨。下面一行是本机清单和训练片，不经过 Transcribe。

## 步骤

| 步骤 | 做什么 | 现在跑在哪 | AWS 服务 | 状态 |
| --- | --- | --- | --- | --- |
| 1. 收片 | 训练片或考试片进入管道。上传页走预签名直传；`run_clip` 也可以直接读本地文件 | 本地命令，或浏览器 | S3 `raw/`、API Gateway `POST /jobs`、Lambda `create_job`、DynamoDB JobsTable、CloudFront | 主路径。栈 `Team04DeepCourt` 已创建，分配 `d2vzd2batw1eqe.cloudfront.net` 已部署 |
| 2. 追踪 | YOLO 检测 + ByteTrack，产出人和球的轨迹。一次看 16 帧，不跑球场关键点 | Batch 队列 `deepcourt-track` 保持一台 EC2 `c7i.48xlarge`。本机 MPS 仍能出训练片 | ECR `deepcourt-tracker:ready`、Batch、EC2 `c7i.48xlarge`、EBS gp3 1000GB、S3 `bootstrap/` 与 `tracking/` | 已接通。G/VT 配额为 0。标准配额 256 vCPU，单机用其中 192，并且保持开着。镜像已烤好。探测作业 `warm-probe` 写出 39 帧轨迹 |
| 3. 记分牌 | 读节次、比赛钟、进攻钟、比分。检测边长限制 640，避免整幅画面把 OCR 拖慢 | 考试片在同一台 `c7i.48xlarge` 上跑 `pipeline/vision/scoreboard.py`。训练片仍在本机 | Textract `DetectDocumentText` 只做过探测，不在主路径 | 已接通。`warm-probe` 读出 6 条比分条。训练片主路径仍在本地 |
| 4. 认人 | 号码 OCR 对上场名单。这一回合的 10 个人先钉在重叠最高的同色身体坑上，剩下的人只在同队空坑里排除；中锋只在明显更高的坑上才分。成片前 3 秒标出已经对上的人 | 本机 `pipeline/vision/backtrack.py`。AWS 成片作业 `inference/run_job.py` 还没调用这一步 | 无 | 本地 |
| 5. 事件清单 | 持球、传球、出手、回合。出手前后按原帧率重检，用球路找脱手那一帧，记下投篮人框。再从出手往前推 5 秒，按球衣颜色跟住投篮人，贴他最久的对方球员算领防人，再把他的轨迹接到出手帧；两人号码只在当时在场名单里认，票数不够就报号码没读出 | 本地 `pipeline/events`、`pipeline/vision/release.py` | 无 | 本地 |
| 6. 官方数据 | 用逐回合、boxscore、shot quality 补出手人、助攻、xFG、坐标。独行侠出手再贴 `data/2025-26/dal_margin_offense.json` 里、按当时分差取的赛季效率。对得上的人再贴 `data/deep` 的赛季出手质量、引力和关键度。每投再贴领防人和投篮人的本场对位、双方赛季场均和队内排名（`leaguedashplayerstats` 加同接口 Advanced 的真实命中率、防守效率；队内第 1 名五星，每往后一名少半星），和投篮人四场的分区预期命中率 | 本地 `pipeline/enrich`，读 `data/` 和仓库 `data/deep` | 无 | 本地。没有对上的比赛就跳过 |
| 7. 清单上云 | `structured/{jobId}.json` | S3 | S3 媒体桶 `team04deepcourt-mediabucketbcbb02ba-haisfxujonfg` | 主路径 |
| 8. 校验 | 取回清单并写成页面事件。Agent 不可用时 Lambda 直接读清单 | Lambda `analyze_job` | Bedrock AgentCore `DeepCourtAnalyst`、DynamoDB EventsTable、CloudWatch Logs | 主路径。这三项资源已创建 |
| 9. 回放 | 成片、时间线、暂停展开。进球后跳回出手帧定格 5 秒，投篮人脚下画红圈，弹出比分、投篮人和领防人的队内排名星级；领防人脚下画黄色虚线圈；领防人号码没读出就只写没读出；右栏是分区热区图，红圈标出这一投。站点本身在第 12 步 | 浏览器。考试片的 mp4 和 `replay/clips.js` 用新文件名直传网页桶，只失效这几个路径 | API Gateway `GET /jobs/{id}` 与 `/events`、Lambda `get_job` `list_events` | 主路径 |
| 10. 解说 | 页面上选两条音轨。英文是成片里的原声，不另处理。中文先把这条原声交给 Transcribe，再由 Translate 译成中文，按原句的时间用 Polly Neural 知语合成。原解说不留在中文音轨里。现场声和原解说叠在一起，声道又几乎是单声道，分不开，所以中文音轨只有合成人声 | 考试片：成片之后在同一容器里做。启动时从 S3 `bootstrap/track/` 拉 `run_job.py`、`dub.py`、`commentary.py`。训练片弗拉格、阿门的两条音轨已经在网页桶，那是本机做的 | Transcribe、Translate、Polly Neural `Zhiyu`（`cmn-CN`）、S3 `commentary/asr/`、任务角色 `TrackJobRole` | 部署中。角色和任务定义已部署，脚本已在 S3。还没有一支云上作业跑完解说。GitHub 没有 Actions，不会自动部署 |
| 11. 成片转码 | 云上成片只把记分牌烧进 `assets/{jobId}-broadcast.mp4`，原声留在里面，不烧人名。训练片在本机另烧人名，并出 `{clip}-replay.mp4`：进球后插入 5 秒出手帧定格 | 考试片在同一台 `c7i.48xlarge` 上用 ffmpeg。训练片仍是本机 ffmpeg | MediaConvert 模板 `deepcourt-render-template` | 已接通。`assets/warm-probe-broadcast.mp4` 已在网页桶。MediaConvert 还没有提交转码作业 |
| 12. 网页呈现 | 评委打开的播放端和作品页。根路径是 `game.html`：成片、时间线、音轨选择。`/readme` 指到 `readme.html`。`/index.html` 只给自己上传，不作为提交链接。网页桶不公开，只经 CloudFront OAC 读取。`/api/*` 转到 API Gateway | 浏览器。CDK `DeployWeb` 把 `web/` 部署到网页桶 | CloudFront 分配 `Site`（`d2vzd2batw1eqe.cloudfront.net`）、S3 网页桶、CloudFront Function `ReadmePath` 与 `StripApiPrefix` | 主路径。`/` 和 `/readme` 已能打开 |
| 13. 观测和密钥 | 看限流、放 webhook 和 token | 仪表盘和空密钥已建 | CloudWatch 仪表盘 `deepcourt-overview`、告警 `deepcourt-bedrock-throttles`、X-Ray、Secrets Manager `deepcourt/feishu-webhook` `deepcourt/github-token` `deepcourt/kuaishou`、Cost Explorer | 已接通。密钥仍是空占位。成本分配标签激活被拒绝。X-Ray 写过一条追踪 |

## 不在这条管道里

- Cognito 不建。回放和上传不登录。
- 快手 OAuth 代码在 `lambdas/kuaishou`。没有 app_id 时接口只返回敬请期待。
- 预置桶 `nba-hackathon-382918437747-team04` 只放过探测对象，不是媒体桶，也不是网页桶。

## 部署身份

profile `nba` 代入 `NBAHackathonParticipantRole`。CDK 栈名 `Team04DeepCourt`。标签 `team=04` `stage=prod` `project=deepcourt`。这个角色不能新建普通 IAM 角色，只能把 `NBAHackathonWorkloadRole` 传给受信任的服务。
