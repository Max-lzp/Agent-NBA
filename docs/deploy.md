# 从干净账号重跑

账号 `382918437747`，profile `nba`，区域只有 `us-east-1`。账号已经 bootstrap，不要再跑 `cdk bootstrap`。IAM 角色的权限边界 `NBAHackathonParticipantBoundary` 由 `cdk/bin/app.ts` 加上，新角色不用手改。

考试片仍走现在已经部署的作业定义。18:00–19:45 不要部署这一份。成片出来之后再部署。默认路径不替换 Batch 计算环境、作业队列、CloudFront 分配、网页桶和媒体桶。作业定义名字仍是 `deepcourt-track`，镜像仍是 `deepcourt-tracker:ready`。术语表会让作业定义出新修订版，见下面「术语表」。

## 一条命令

在仓库根目录：

```bash
cd cdk && npm ci && npx cdk deploy Team04DeepCourt --profile nba --require-approval never
```

先看模板、不连账号：

```bash
cd cdk && npm ci && npx cdk synth
```

`CDK_DEFAULT_ACCOUNT` 为空时，`cdk/bin/app.ts` 用 `382918437747`。`cdk/cdk.context.json` 里已经有这个账号在 `us-east-1` 的可用区，synth 不查实时账号。

## Context 开关

| 开关 | 默认 | 什么时候改 |
| --- | --- | --- |
| `enableOrchestration` | 开。只有显式 `-c enableOrchestration=false` 才关 | 关：不建状态机、不建 `deepcourt-raw-upload`，`raw/` 和 `structured/*.json` 仍通知 `analyze_job`。脚本照样传到 `bootstrap/track/`。 |
| `enableP1` | 开。`-c enableP1=false` 才关 | 关：不建 Batch，也不建状态机。上传仍走原来的 S3 通知。 |
| `buildTrackerImage` | 关。只有 `-c buildTrackerImage=true` 才开 | 开：用 `ecr-assets` 从仓库根目录的 `inference/Dockerfile` 构建 `linux/amd64`。已部署的栈不要开，见下面「镜像」。干净账号没有 `deepcourt-tracker:ready` 时才开。 |
| `enableP2` | 开。`-c enableP2=false` 才关 | 这次没改。关了解说 Lambda、密钥和 X-Ray。 |

已经部署的考试账号，成片之后用默认值即可，不要加 `buildTrackerImage`。

## 作业脚本

`s3deploy.BucketDeployment`（逻辑 ID `TrackBootstrap`，256MiB，和网页部署的 512MiB 分开）把下面这些文件传到媒体桶 `bootstrap/track/`。`prune` 为 false，`retainOnDelete` 为 true，不会删掉前缀外面的对象，也不会在删除这条资源时清空前缀。

| 仓库文件 | 桶里的名字 |
| --- | --- |
| `inference/run_job.py` | `bootstrap/track/run_job.py` |
| `inference/chain.py` | `bootstrap/track/chain.py` |
| `inference/dub.py` | `bootstrap/track/dub.py` |
| `pipeline/present/tracks.py` | `bootstrap/track/commentary.py` |
| `web/game.html` | `bootstrap/track/web/game.html` |
| `web/game.js` | `bootstrap/track/web/game.js` |
| `web/styles.css` | `bootstrap/track/web/styles.css` |

容器启动命令下载的就是这些名字。这次没有改 `inference/run_job.py`、`inference/chain.py` 的业务逻辑。

权重不在 git 里。干净账号还要把 `player_detector.pt`、`ball_detector_model.pt` 放到媒体桶 `bootstrap/models/`。`official/` 也不由这次 CDK 创建。

## 镜像

默认作业定义仍引用 ECR `deepcourt-tracker:ready`。`GpuCompute`、`TrackQueue` 的逻辑 ID 和属性不变，部署不会替换这两样。`TrackJob` 的逻辑 ID 和名字 `deepcourt-track` 不变，镜像标签仍是 `ready`。环境变量多了 `TRANSLATE_TERMINOLOGY`，部署时这条作业定义出新修订版。

`inference/bake.sh` 仍是在已经开着的 Batch 主机上，从 S3 `bootstrap/bake/` 的扁平目录构建并推 `:ready`。仓库里的 Dockerfile 按仓库根目录构建，不要拿它覆盖 `bootstrap/bake/` 里那份扁平 Dockerfile。

`-c buildTrackerImage=true` 会把作业定义的镜像改成 CDK 资产仓库里的标签，并给执行角色加上拉取该仓库的权限。CloudFormation 更新 `AWS::Batch::JobDefinition` 的 `ContainerProperties` 会替换作业定义。队列和计算环境的逻辑 ID 仍不变。已有栈在考试窗口不要开这个开关。

## 术语表

`aws-cdk-lib/aws-translate` 只有 `AWS::Translate::ParallelData`，没有术语表 L1。`BasketballTerminology` 用 `AwsCustomResource` 调 `ImportTerminology`（`MergeStrategy` `OVERWRITE`），文件内容是仓库 `data/terminology/basketball-en-zh.csv`，方向 `UNI`。删除这条资源时调 `DeleteTerminology`。作业定义环境变量是 `TRANSLATE_TERMINOLOGY=deepcourt-basketball`。自定义资源的角色挂 `NBAHackathonParticipantBoundary`，并且 `installLatestAwsSdk` 为 false。

`TrackJobRole` 原有的 `translate:TranslateText` 仍是 `Resource: "*"`。服务授权参考里这个动作的资源类型是可选的 terminology / parallel-data；不带术语表的回退请求没有术语表这条资源，所以不能只写 ARN。带上术语表还要 `translate:GetTerminology`。新增的一条把 `translate:GetTerminology` 和 `translate:TranslateText` 限定到 `arn:aws:translate:us-east-1:<账号>:terminology/deepcourt-basketball/LATEST`。

两份都是 `npx cdk synth`，默认开关，没有连账号。相对加术语表之前的模板：

不会替换，模板逐字节相同：

- 计算环境 `GpuCompute`
- 作业队列 `TrackQueue`（名字 `deepcourt-track`）
- CloudFront 分配 `Site`
- 网页桶 `WebBucket`
- 媒体桶 `MediaBucket`

就地修改：

- `TrackJob`：逻辑 ID 和名字 `deepcourt-track` 不变，镜像仍是 `deepcourt-tracker:ready`。只多了环境变量 `TRANSLATE_TERMINOLOGY`，以及等术语表导入完成的 `DependsOn`。部署时这个名字会出新修订版。
- `TrackJobRoleDefaultPolicy`：角色本身不变，策略多了上面那条术语表权限。
- `CDKMetadata`：CDK 元数据。

新增：

- `BasketballTerminology`（`Custom::TranslateTerminology`）
- 它的策略 `BasketballTerminologyCustomResourcePolicy`（`translate:ImportTerminology` 与 `translate:DeleteTerminology`，资源是同一个术语表 ARN）
- 自定义资源提供方的 Lambda、日志组和角色（角色挂权限边界）

状态机 `FilmPipeline` 的模板文本没变，提交参数仍是作业定义的 `Ref`。部署时修订号变了，变更集会把新的作业定义 ARN 写进状态机。这是就地更新，不是另建一条状态机，也不是替换队列或计算环境。

这次不要部署。作业定义要等术语表导入成功才更新；导入失败时现有修订版还在。

## 上传之后谁提交 Batch

默认只有状态机 `deepcourt-film`（标准工作流）会 `SubmitJob` 并 `.sync` 等到结束。EventBridge 规则 `deepcourt-raw-upload` 只匹配媒体桶 `raw/` 的对象创建。`create_job` 不启动执行，避免文件还没传完就跑，也避免和上传事件各交一次。`SubmitTrack` 留着，没有事件源。

`analyze_job` 不再接收 `structured/` 的 S3 通知。状态机在 Batch 成功之后用 `{jobId, key}` 调用它，`key` 是 `structured/{jobId}.json`。函数代码这次没改。

片单正文仍由追踪作业里的 `run_job.py` 写入网页桶 `replay/clips.js`。状态机只对成片和 `replay/clips.js` 做 `HeadObject`，再 `CreateInvalidation` 这四条路径：`/assets/{jobId}-broadcast.mp4`、`/assets/{jobId}-zh.mp4`、`/assets/{jobId}-en.mp4`、`/replay/clips.js`。不失效 `/`、`/game.html` 或 `/*`。容器里原来的失效还在，会再失效一次，无害。

同一次上传的重复事件：Jobs 状态已经是 `TRACKING`、`ANALYZING` 或 `PUBLISHING` 时，条件更新失败，执行停在 `DuplicateSkipped`，不会再交一次 Batch。对象已经在桶里、想手工重跑时：

```bash
aws stepfunctions start-execution \
  --state-machine-arn "<FilmPipelineArn>" \
  --input '{"jobId":"<jobId>","key":"raw/<jobId>/<filename>"}' \
  --profile nba --region us-east-1
```

`key` 必须是 `raw/{jobId}/文件名`。部署之后不要再对同一次 `raw/` 上传手工 `aws batch submit-job`，那条路不看 Jobs 条件，会和状态机各跑一遍。

## 默认部署相对当前模板

两份对照都是 `npx cdk synth`，没有连账号。默认开关：`enableOrchestration` 开，`buildTrackerImage` 关。

### 相对已经合入的 `feat/standard-player`

术语表相对当前模板的差异见上面「术语表」。`TrackJob` 不再与加术语表之前逐字节相同。下面是编排合入时相对更早模板的差异，其中把 `TrackJob` 算成逐字节相同的那句，以「术语表」为准。

这是编排那次改动自己带上的差异。`GpuCompute`、`TrackQueue`、`Site`（CloudFront 分配）、`WebBucket`、`WebBucketPolicy`、`AnalyzeJob`（含已经改成 240 秒的超时）、`SubmitTrack`、`AskAgent`、`AnalystRuntime`、网页部署用的 512MiB 角色和策略，与那份基线逐字节相同。当时的 `TrackJob` 及其角色也相同；术语表随后改了作业定义的环境变量和角色策略。

新增：

- 状态机 `FilmPipeline`、角色 `FilmPipelineRole`、策略、日志组 `FilmPipelineLogs`
- EventBridge 规则 `RawUpload`、调用角色 `FilmPipelineEventsRole` 及策略
- 256MiB 的 `TrackBootstrap` 自定义资源、函数、日志组、角色、策略、AWS CLI Layer

这些新角色都挂 `NBAHackathonParticipantBoundary`。256MiB 部署角色的策略范围是整个媒体桶（含 `s3:DeleteObject`），但自定义资源的前缀是 `bootstrap/track`，`Prune=false`，同步不会删其他前缀。

删除：

- `MediaBucketAllowBucketNotificationsToTeam04DeepCourtAnalyzeJob…`：允许 S3 调用 `analyze_job` 的 Lambda 权限。通知去掉之后这条权限一并去掉。

就地修改，不是替换：

- `MediaBucket`：只多一个标签 `aws-cdk:cr-owned:bootstrap/track:<hash>`。桶不重建。`-c enableOrchestration=false` 时这个标签也在，因为脚本部署仍在。
- `MediaBucketNotifications`：同一条自定义资源，配置从两条 Lambda 通知改成只剩 `EventBridgeConfiguration`。同一次 `PutBucketNotification`，不会先留下旧通知再加新规则。`-c enableOrchestration=false` 时这条与现在的模板相同。
- `Overview`：仪表盘正文加上状态机成功、失败、超时和耗时。
- `DeployWebCustomResource512MiB`：只改了网页包的 zip 哈希，因为 `web/readme.html` 的数据管道写上了编排。第二个 `config.js` 包的哈希不变。下次部署时，现有网页部署会按它原来的配置重传 `web/` 并失效 CloudFront `/*`。分配 `Site` 和网页桶本身的模板没有改。这是本改动里会碰到正在播的页面缓存的地方，所以等考试片出完再部署。
- `CDKMetadata`：CDK 元数据。

### 相对更早的线上模板

`feat/standard-player` 上已经合入的分析师改动（AgentCore、`POST /agent/ask`、`web/analyst.js`）在全景里标的是「部署中」，synth 对照也还没出现在更早的模板里。考试片出完后的第一次 `cdk deploy` 会把那一份和上面的编排一起上去。那一份会更新 `AnalystRuntime`、`AnalyzeJob` 超时（120 秒改为 240 秒）和各 Lambda 的代码包，并替换 API Gateway 的 Deployment（阶段仍指向同一个 Stage，分配 `Site` 不变）。它也会让网页部署重传并失效 `/*`，因为网页包里多了 `web/analyst.js`。Batch 队列和计算环境在那一份里同样不变。作业定义要等这次术语表改动才出新修订版，见上面「术语表」。

状态机调用 `analyze_job` 的等待时间是 300 秒，盖过函数自己的 240 秒，避免函数还在写 DONE 时状态机先判失败。

`enableOrchestration=false` 时不新增状态机和规则，不删除上面那条 Lambda 权限，通知配置不变。脚本部署、媒体桶标签、以及 readme 引起的网页包哈希变化仍在。

`-c buildTrackerImage=true` 时，除了上面的默认差异，`TrackJob` 的镜像从 `:ready` 换成 CDK 资产仓库的标签（替换作业定义），`TrackExecutionRole` 的策略增加对该资产仓库的拉取。队列、计算环境不替换。默认部署不要开。
