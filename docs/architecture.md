# 架构说明

> 主人：A（部署线）。别人可以提意见，只有主人改。
> 用途：机器评分扫仓库时对照「每个 AWS 服务为什么出现」。状态：P0 代码在仓库里。主办方共享账号已关闭，部署改到本队账号 `405496568869`。

## 评委看到的路径

提交门户的链接打开成片回放页，页面上标明 Team 04。评委看的是视频最终效果，不在页面上选文件、等处理。上传页只给我们自己生产用。同一域名的 `/readme` 用文字介绍作品亮点，不求美观。（10.9 组委会口径，见 decisions.md D-006。）

下面是成片背后的生产链路，不是提交链接的第一屏：

```plaintext
浏览器打开 CloudFront
  → index.html 选视频或粘贴 URL
  → POST /api/jobs（CloudFront 转到 API Gateway）
  → Lambda create_job 写 DynamoDB JobsTable，返回 S3 预签名 PUT
  → 浏览器把视频直传到 S3 raw/{jobId}/
  → S3 事件触发 Lambda analyze_job
  → analyze_job 调用 Bedrock AgentCore Runtime（DeepCourtAnalyst）
  → Agent 调 Bedrock：有画面用 qwen.qwen3-vl-235b-a22b，否则用 global.anthropic.claude-sonnet-4-6，失败互换
  → 事件 JSON 写入 DynamoDB EventsTable，任务状态 PENDING → ANALYZING → DONE / FAILED
  → game.html?jobId= 轮询 GET /jobs/{id} 与 GET /jobs/{id}/events，时间线可点，暂停展开防守压力盒
```

URL 模式不经过 S3：create_job 异步调用 analyze_job，只把链接和 gameMeta 交给模型，置信度被提示词压到 0.4 以下。

单文件超过 40MB 时 Lambda 不把整段视频拉进内存，只做元数据草稿。整段 GPU 跟踪留在 P1 的 Batch。

## 服务为什么在这里

| 服务 | 代码位置 | 为什么是它 |
| --- | --- | --- |
| CDK / CloudFormation | `cdk/` | 硬性要求。一个栈 `Team04DeepCourt`，避免跨栈导出互相卡死。部署到本队账号时不挂主办方权限边界；只有再部署回已关闭的共享账号才挂 `NBAHackathonParticipantBoundary`。标签 `team=04` `stage=prod` `project=deepcourt`。 |
| IAM | CDK `grant*` 与 Agent 角色 | create 只能写 Jobs 表和 `raw/*`；get 只能读 Jobs 和 `raw/*`；list 只能读 Events；analyze 才能写 Events、调 Bedrock 和 AgentCore。 |
| S3 | `MediaBucket` 前缀 `raw/`，`WebBucket` | 视频不经应用服务器，浏览器拿预签名地址直传。网页桶不公开，只给 CloudFront OAC。 |
| DynamoDB | `JobsTable`（PK jobId），`EventsTable`（PK jobId，SK `TS#序号`） | 任务状态机和事件列表分开，回放页按 jobId 查询。 |
| Lambda | `lambdas/create_job` `analyze_job` `get_job` `list_events` | 四个入口对应建任务、分析、读状态、读事件。日志进 CloudWatch Logs，保留 7 天。 |
| API Gateway | `JobsApi` REST：`POST /jobs`，`GET /jobs/{id}`，`GET /jobs/{id}/events` | 和组委会样例一样用 REST API。浏览器不直连执行地址，走 CloudFront 的 `/api/*`。 |
| Bedrock | `agent/agent.py` 与 analyze 的回退 | 视觉模型读抽帧，文本模型写事件 JSON。一个模型失败就换另一个，并在日志里记下原因。 |
| Bedrock AgentCore | `AWS::BedrockAgentCore::Runtime` `DeepCourtAnalyst` | 硬性要求：分析编排跑在 AgentCore，而不是笔记本上的循环。Lambda 用 `bedrock-agentcore:InvokeAgentRuntime` 调用它；Agent 不可用时 Lambda 直接 `bedrock-runtime.converse`，页面不会停在 ANALYZING。 |
| CloudFront | `Site` | 硬性要求的作品链接。默认行为是网页桶，函数把 `/readme` 指到 `readme.html`；`/api/*` 转到 API Gateway，函数去掉 `/api` 前缀。 |
| CloudWatch Logs | 每个 Lambda 的日志组 | 分析日志带 jobId，排错不用进机器。仪表盘留到 P1。 |
| STS | 角色信任策略 | Lambda 信任 `lambda.amazonaws.com`，Agent 信任 `bedrock-agentcore.amazonaws.com`。部署身份用 profile `team04`。 |

区域只使用 `us-east-1`。本队账号 `405496568869`（profile `team04`）。主办方共享账号 `278477606068` 已关闭，不再部署到那里。本队账号若还没有 CDK bootstrap，部署前要先做一次。

## 和后续阶段的边界

P0 不包含 Batch、Textract、MediaConvert、Polly、Cognito、X-Ray。播放器里的数字解说开关标着 COMING SOON，对应 Polly。画面上的示例回合（弗拉格 / 申京）只在没有 `jobId` 时出现，用来对齐设计稿；带 `jobId` 之后时间线换成表里的事件。

## 本地检查

```bash
cd lambdas && python3 -m unittest test_common.py
cd cdk && npm install && npx cdk synth --profile nba
```

部署与 curl 步骤写在仓库根目录 `README.md`。
