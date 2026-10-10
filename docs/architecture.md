# 架构说明

> 主人：A（部署线）。别人可以提意见，只有主人改。
> 用途：机器评分扫仓库时对照「每个 AWS 服务为什么出现」。步骤和当前状态以 [pipeline-map.md](pipeline-map.md) 为准，并同步 `web/readme.html` 的「数据管道」。主办方共享账号已关闭。当前部署账号是 `382918437747`（profile `nba`）。

## 评委看到的路径

提交门户的链接打开成片回放页，页面上标明 Team 04。评委看的是视频最终效果，不在页面上选文件、等处理。上传页只给我们自己生产用。同一域名的 `/readme` 用文字介绍作品亮点，不求美观。（10.9 组委会口径，见 decisions.md D-006。）

下面是成片背后的生产链路，不是提交链接的第一屏：

```plaintext
浏览器打开 CloudFront
  → index.html 选视频或粘贴 URL
  → POST /api/jobs（CloudFront 转到 API Gateway）
  → Lambda create_job 写 DynamoDB JobsTable，返回 S3 预签名 PUT
  → 浏览器把视频直传到 S3 raw/{jobId}/
  → S3 事件触发 Lambda analyze_job，任务状态 PENDING → TRACKING（等待本地追踪）
  → 本地 CV 管线（YOLO + ByteTrack、号码 OCR、记分牌 OCR、事件流引擎）产出结构化清单
  → 清单上传到 S3 structured/{jobId}.json，再次触发 analyze_job
  → analyze_job 调用 Bedrock AgentCore Runtime（DeepCourtAnalyst），Agent 取回并校验清单
  → 清单转成页面事件写入 DynamoDB EventsTable，状态 ANALYZING → DONE / FAILED
  → game.html?jobId= 轮询 GET /jobs/{id}，拿到成片地址后用系统控件播放
```

**事件只来自结构化清单，不让任何模型看图生成事件。**旧路（抽 3 帧问 Qwen3 VL「发生了什么」）准确率低，10.9 起停用。识别细节见 `docs/recognition.md`，清单格式见 `pipeline/events/event_stream.schema.json`。

URL 模式不经过 S3：create_job 只把任务标成 TRACKING，等本地追踪产出清单。整段 GPU 跟踪留在 P1 的 Batch。

## 服务为什么在这里

| 服务 | 代码位置 | 为什么是它 |
| --- | --- | --- |
| CDK / CloudFormation | `cdk/` | 硬性要求。一个栈 `Team04DeepCourt`，避免跨栈导出互相卡死。部署到本队账号时不挂主办方权限边界；只有再部署回已关闭的共享账号才挂 `NBAHackathonParticipantBoundary`。标签 `team=04` `stage=prod` `project=deepcourt`。 |
| IAM | CDK `grant*` 与 Agent 角色 | create 只能写 Jobs 表和 `raw/*`；get 只能读 Jobs 和 `raw/*`；list 只能读 Events；analyze 读 `structured/*`、写 Events、调 AgentCore；Agent 角色只读 `structured/*`。 |
| S3 | `MediaBucket` 前缀 `raw/`（原始视频）、`structured/`（结构化清单），`WebBucket` | 视频不经应用服务器，浏览器拿预签名地址直传。网页桶不公开，只给 CloudFront OAC。 |
| DynamoDB | `JobsTable`（PK jobId），`EventsTable`（PK jobId，SK `TS#序号`） | 任务状态机和事件列表分开，回放页按 jobId 查询。 |
| Lambda | `lambdas/create_job` `analyze_job` `get_job` `list_events` | 四个入口对应建任务、分析、读状态、读事件。日志进 CloudWatch Logs，保留 7 天。 |
| API Gateway | `JobsApi` REST：`POST /jobs`，`GET /jobs/{id}`，`GET /jobs/{id}/events` | 和组委会样例一样用 REST API。浏览器不直连执行地址，走 CloudFront 的 `/api/*`。 |
| Bedrock | `lambdas/commentary` | Claude Sonnet 4.6 只根据事件清单写两句解说，不参与生成事件。本队账号目前调用被拒（AccessDenied），解说暂时出不了声。 |
| Bedrock AgentCore | `AWS::BedrockAgentCore::Runtime` `DeepCourtAnalyst` | 硬性要求：分析编排跑在 AgentCore，而不是笔记本上的循环。Lambda 用 `bedrock-agentcore:InvokeAgentRuntime` 调用它；Agent 负责取回、校验结构化清单，不调模型；Agent 不可用时 Lambda 直接读清单，页面不会停在 ANALYZING。 |
| CloudFront | `Site` | 硬性要求的作品链接。默认行为是网页桶，函数把 `/readme` 指到 `readme.html`；`/api/*` 转到 API Gateway，函数去掉 `/api` 前缀。 |
| CloudWatch Logs | 每个 Lambda 的日志组 | 分析日志带 jobId，排错不用进机器。仪表盘留到 P1。 |
| STS | 角色信任策略 | Lambda 信任 `lambda.amazonaws.com`，Agent 信任 `bedrock-agentcore.amazonaws.com`。部署身份用 profile `team04`。 |

区域只使用 `us-east-1`。当前栈 `Team04DeepCourt` 建在参赛账号 `382918437747`（profile `nba`）。本队 SSO 账号 `405496568869`（profile `team04`）不再作为部署目标。主办方共享账号 `278477606068` 已关闭。

## 和后续阶段的边界

回放页在没有 `jobId` 时播放仓库里的示例成片（弗拉格 / 阿门）。带 `jobId` 时播放该任务的成片地址。解说声音仍等 Polly，词已经混进成片。

## P1（代码在同一栈。有没有建出来以 pipeline-map.md 为准）

上下文 `enableP1=false` 可以整段跳过。GPU 配额或 Batch 服务角色卡住时，用这个开关先把 P0 部署出去。跟踪作业不由 `analyze_job` 调用。

| 服务 | 代码位置 | 为什么是它 |
| --- | --- | --- |
| ECR | `TrackerRepo` `deepcourt-tracker` | 跟踪镜像的仓库。镜像定义在 `inference/`，推送要等账号恢复。 |
| Batch + EC2 + EBS | `GpuCompute` g4dn.xlarge，启动模板 gp3 100GB，队列 `deepcourt-track` | 整段视频的 GPU 跟踪。空闲 vCPU 为 0。`submit_track` 单独提交作业。 |
| Textract | `lambdas/ocr_scoreboard` | 读记分牌帧上的比分、节次、时钟。解析失败置信度记 0。 |
| MediaConvert | 模板 `deepcourt-render-template` | 把 PNG 叠加层合成 1080p MP4，写到 `rendered/`。 |
| CloudWatch | 仪表盘 `deepcourt-overview`，两条告警 | 看分析吞吐、延迟、Bedrock 限流。告警先不接通知。 |

## P2（代码在同一栈，尚未部署）

上下文 `enableP2=false` 时不建解说、密钥和 X-Ray。创建任务和回放都不登录。

| 服务 | 代码位置 | 为什么是它 |
| --- | --- | --- |
| Polly + Bedrock | `lambdas/commentary` | 两句中文解说，合成 MP3 放到 `commentary/`。混进成片用本机 `commentary/mix.sh`，因为 Lambda 里没有 ffmpeg。 |
| X-Ray | API 阶段和 Lambda `tracing: ACTIVE` | 处理段调用 `trace_job.annotate`，装了 SDK 时片段上会有 jobId。 |
| Secrets Manager | `deepcourt/feishu-webhook`、`deepcourt/github-token`、`deepcourt/kuaishou` | 真实密钥部署后写入。仓库和前端都不放。 |
| Cost Explorer | `scripts/cost-report.sh` | 按 `project` 标签看花费。栈上的 `team`、`stage`、`project` 会传到可标记的资源。 |
| 快手 OAuth | `lambdas/kuaishou` | 扫码地址由后端拼。没有 app_id 时接口返回敬请期待，不把密钥给浏览器。 |

## 本地检查

```bash
cd lambdas && python3 -m unittest test_common.py
cd cdk && npm install && npx cdk synth --profile nba
```

部署与 curl 步骤写在仓库根目录 `README.md`。
