# 考试应急预案

考试片 18:00 发布。成片要在 20:00 前经 CloudFront 根路径交上去，并且 20:00–22:20 一直打得开。

账号：profile `nba`，区域 `us-east-1`。媒体桶 `team04deepcourt-mediabucketbcbb02ba-haisfxujonfg`。提交链接：

```text
https://d2vzd2batw1eqe.cloudfront.net/
```

云上作业在队列 `deepcourt-track`、作业定义 `deepcourt-track`，机器是保持开着的 `c7i.48xlarge`。容器启动时从 S3 `bootstrap/track/` 拉 `inference/run_job.py`。成片写到网页桶 `assets/{jobId}-broadcast.mp4`，中英文音轨是 `assets/{jobId}-zh.mp4` 和 `assets/{jobId}-en.mp4`。网页桶名和分布编号从栈 `Team04DeepCourt` 的输出读，不要手写。

下面的命令都在仓库根目录、本机 macOS 上跑。先 `aws sso login --profile nba`（或你们平时的登录方式），确认：

```bash
aws sts get-caller-identity --profile nba --region us-east-1
```

## 18:00–20:00 时间线

| 时刻 | 还没有能交的成片时 |
| --- | --- |
| 18:00 | 拿到考试片。上传到媒体桶并提交 Batch（下面 (a) 的提交命令）。 |
| 18:25 | 根路径上这支成片还不是 HTTP 200。看 Batch。状态是 `FAILED`，或 `RUNNING` 已超过 25 分钟，或 `RUNNABLE` 超过 10 分钟：停掉卡住的那一支，**重交一次**。 |
| 18:50 | 重交的成片仍不是 HTTP 200，或第二支也 `FAILED`。停掉云上作业，改走本机 (b)：`scripts/fallback/local_exam.sh`。 |
| 19:30 | 本机完整成片（人名、音轨都在）还没出来，或自检写名人不在场上、同名两条轨迹、解说或官方数据失败。改交只烧记分牌的成片 (c)。 |
| 19:45 | 上传和失效做完。跑 `scripts/qa/check_output.py`。报告第一句是「可以提交」再填门户。 |
| 19:50 | 不再改页面，也不再失效 `/`、`/game.html`。只在根链接打不开时，按报告补那一个文件。 |
| 20:00 | 门户里填上面的根路径。20:00–22:20 让这个链接保持可打开，不要再传会换掉首页的文件。 |

作业定义自己还会重试 1 次。人只再交一支。队列里只有一台 192 vCPU 的机器，两支一起跑会抢 CPU。

## (a) 云上作业失败或超时

**算失败或超时（满足一条就处理）：**

- `describe-jobs` 的 `status` 是 `FAILED`
- `status` 是 `RUNNING`，且 `startedAt` 距现在超过 25 分钟（24 秒考试片正常应在这之前写完成片）
- `status` 是 `RUNNABLE` 超过 10 分钟（机器应一直开着，不该排这么久）
- `status` 是 `SUCCEEDED`，但过了 3 分钟 `assets/{jobId}-broadcast.mp4` 仍不是 HTTP 200，并且网页桶里也没有这个对象

**看状态：**

```bash
PROFILE=nba
REGION=us-east-1
aws batch list-jobs --job-queue deepcourt-track --job-status RUNNING --profile "$PROFILE" --region "$REGION"
aws batch list-jobs --job-queue deepcourt-track --job-status RUNNABLE --profile "$PROFILE" --region "$REGION"
aws batch list-jobs --job-queue deepcourt-track --job-status FAILED --profile "$PROFILE" --region "$REGION"
aws batch list-jobs --job-queue deepcourt-track --job-status SUCCEEDED --profile "$PROFILE" --region "$REGION"
```

把列表里的 `jobId`（这是 Batch 的 id，不是我们的 `JOB_ID`）代进：

```bash
BATCH_ID=<上面的 jobId>
aws batch describe-jobs --jobs "$BATCH_ID" --profile "$PROFILE" --region "$REGION" \
  --query 'jobs[0].{status:status,statusReason:statusReason,startedAt:startedAt,stoppedAt:stoppedAt}'
```

`startedAt` 是毫秒。当前毫秒：

```bash
python3 -c 'import time; print(int(time.time()*1000))'
```

**看 CloudWatch 日志。** 先拿日志流名字，再拿日志组（作业定义里的 awslogs 配置）：

```bash
aws batch describe-jobs --jobs "$BATCH_ID" --profile "$PROFILE" --region "$REGION" \
  --query 'jobs[0].attempts[-1].container.logStreamName' --output text

aws batch describe-job-definitions --job-definition-name deepcourt-track --status ACTIVE \
  --profile "$PROFILE" --region "$REGION" \
  --query 'jobDefinitions[0].containerProperties.logConfiguration'
```

`options` 里的 `awslogs-group` 就是日志组，`logStreamName` 就是上面那条。然后：

```bash
LOG_GROUP=<awslogs-group>
LOG_STREAM=<logStreamName>
aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "$LOG_STREAM" \
  --limit 80 --profile "$PROFILE" --region "$REGION" \
  --query 'events[].message' --output text
```

日志组若是 `/aws/batch/job`，就用这个名字。`ResourceNotFoundException` 时先列出再选：

```bash
aws logs describe-log-groups --log-group-name-prefix /aws/batch --profile "$PROFILE" --region "$REGION" \
  --query 'logGroups[].logGroupName' --output text
```

日志里这些句子决定下一步：`记分牌没读完` 仍会出成片；`认人、事件或官方数据没写完` 仍会出只烧记分牌的成片；音轨或 `ffmpeg 退出` 会让作业失败，成片可能没上传。

**重交一次。** 先停掉还占着机器的那支（`FAILED` 的不用停）：

```bash
aws batch terminate-job --job-id "$BATCH_ID" --reason "exam resubmit once" \
  --profile "$PROFILE" --region "$REGION"
```

新作业用新的 `JOB_ID`（例如 `exam2`），避免和第一支的清单抢同一个键。视频先放进媒体桶：

```bash
MEDIA=team04deepcourt-mediabucketbcbb02ba-haisfxujonfg
JOB_ID=exam2
VIDEO=<考试片的本地路径>
aws s3 cp "$VIDEO" "s3://${MEDIA}/raw/${JOB_ID}/exam.mp4" --profile "$PROFILE" --region "$REGION"

aws batch submit-job \
  --job-name "track-${JOB_ID}" \
  --job-queue deepcourt-track \
  --job-definition deepcourt-track \
  --profile "$PROFILE" \
  --region "$REGION" \
  --container-overrides "{\"environment\":[{\"name\":\"JOB_ID\",\"value\":\"${JOB_ID}\"},{\"name\":\"VIDEO_S3\",\"value\":\"s3://${MEDIA}/raw/${JOB_ID}/exam.mp4\"},{\"name\":\"OUTPUT_S3\",\"value\":\"s3://${MEDIA}/tracking/${JOB_ID}/tracks.json\"},{\"name\":\"AWAY\",\"value\":\"DAL\"},{\"name\":\"HOME_TEAM\",\"value\":\"HOU\"},{\"name\":\"GAME_ID\",\"value\":\"0022500701\"}]}"
```

作业定义里已经有 `MEDIA_BUCKET`、`WEB_BUCKET`、`DISTRIBUTION_ID`。上面只覆盖考试需要的变量。成功后容器会把成片和 `replay/clips.js` 写进网页桶，并把 `window.DEEPCOURT_LEAD` 设成这个 `JOB_ID`，再失效这几个新路径。根路径就会打开这支片子。

作业已经 `SUCCEEDED`、网页桶里有对象、CloudFront 仍是旧页面时，只补失效，不要重交：

```bash
WEB=$(aws cloudformation describe-stacks --stack-name Team04DeepCourt --profile "$PROFILE" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='WebBucketName'].OutputValue" --output text)
DIST=$(aws cloudformation describe-stacks --stack-name Team04DeepCourt --profile "$PROFILE" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)
aws s3api head-object --bucket "$WEB" --key "assets/${JOB_ID}-broadcast.mp4" --profile "$PROFILE" --region "$REGION"
aws cloudfront create-invalidation --distribution-id "$DIST" \
  --paths "/assets/${JOB_ID}-broadcast.mp4" "/assets/${JOB_ID}-zh.mp4" "/assets/${JOB_ID}-en.mp4" "/replay/clips.js" \
  --profile "$PROFILE" --region "$REGION"
```

不要失效 `/*`，也不要失效 `/` 或 `/game.html`。

## (b) 云上仍失败：本机出片再上传

**改走本机的标准：** 18:50 仍没有 HTTP 200 的成片，或按 (a) 重交的那支也是 `FAILED`。不要再交第三支 Batch。

本机用仓库里的 `pipeline/run_clip.py` 和 `python3 -m pipeline.present`，和云上是同一套追踪、记分牌、成片代码。需要已经装好的 `pipeline/vision/.venv` 和本机 `ffmpeg`（考试手册第 0 步那套）。

```bash
scripts/fallback/local_exam.sh "<考试片路径>" exam
```

脚本会：

1. `python3 -m pipeline.run_clip --video … --clip exam --away DAL --home HOU --white HOU --game-id 0022500701`
2. `python3 -m pipeline.present --clip exam`，再尽量做中英文音轨。音轨失败不挡成片。
3. 把网页桶里现有的 `replay/clips.js` 下载下来，放进 `exam` 这一条，并写上 `window.DEEPCOURT_LEAD="exam"`。根路径打开这支考试片。
4. 上传 `assets/exam-broadcast.mp4`、已有的音轨或定格片，以及 `replay/clips.js`。失效只带这些新路径。

比分对不上 0701 时：

```bash
SKIP_GAME_ID=1 scripts/fallback/local_exam.sh "<考试片路径>" exam
```

视觉已经跑过、只想重出片：

```bash
SKIP_VISION=1 scripts/fallback/local_exam.sh "<考试片路径>" exam
```

传完用无痕窗口打开根路径，确认是考试片。然后：

```bash
python3 scripts/qa/check_output.py exam --clip exam
```

## (c) 认人、解说或官方数据只有一部分失败

成片（记分牌已经烧进去、HTTP 200）在，就先交，不要为了补字段重跑到 20:00。

**认人失败。** 日志有 `认人、事件或官方数据没写完`，或媒体桶没有 `identity/{jobId}.json`，或自检写「画面上没有人名 / 不在当时场上 / 同一名字落在两条轨迹」。云上成片本来只烧记分牌、不烧人名。这条成片可以先交。人名稍后在本机补：

```bash
python3 -m pipeline.run_clip --clip exam --skip-vision --away DAL --home HOU --white HOU --game-id 0022500701
python3 -m pipeline.present --clip exam
```

present 会把人名烧进画面。补完后只上传新成片和 `replay/clips.js`，失效也只带这些路径（命令和 (a) 最后那一段一样，把 `JOB_ID` 换成 `exam`）。人名仍不对，就不要交带人名的这版，改交只烧记分牌的：

```bash
SCOREBOARD_ONLY=1 scripts/fallback/local_exam.sh "<考试片路径>" exam
```

视觉结果已经在时加上 `SKIP_VISION=1`。这一支用 `scripts/fallback/scoreboard_film.py` 只画节次、比赛钟和比分，回放数据里 `tags` 是空的，页面上不跟人名。

**解说失败。** `assets/{jobId}-broadcast.mp4` 是 200，但 `assets/{jobId}-zh.mp4` 或 `-en.mp4` 不是。日志里是 Transcribe、Translate、Polly，或「没有转写出英文解说」。页面没有音轨条目时会播成片里的原声，先交。补音轨：

```bash
python3 -m pipeline.present.tracks --clip exam
```

然后只上传 `assets/exam-zh.mp4`、`assets/exam-en.mp4` 和 `replay/clips.js`，失效这三条。

**官方数据失败。** 清单里 `gameId` 为空，或自检写记分牌对不上且 `official/` 缺文件。记分牌成片不依赖出手补全，先交。确认媒体桶 `official/2025-26/` 里有逐回合和 boxscore 之后：

```bash
python3 -m pipeline.run_clip --clip exam --skip-vision --away DAL --home HOU --white HOU --game-id 0022500701
```

## 交之前的自检

```bash
python3 scripts/qa/check_output.py <jobId>
python3 scripts/qa/check_output.py <jobId> --clip exam
```

`--clip` 在成片文件名用的是片名而不是 jobId 时才加（本机脚本用片名）。报告在 `scripts/qa/out/<jobId>.md` 和同名 `.json`。第一句是「可以提交。」或「有问题：…。建议：…。」某一项失败不会让脚本中途退出。退出码 0 只表示报告写出来了，交不交看第一句。

检查 CloudFront 根路径、`/readme`、成片和中英文音轨（HTTP 200、大小），并用 ffprobe 看时长、分辨率、音轨数；媒体桶 `structured/{jobId}.json`；Jobs 表状态是不是 `DONE`、事件数和清单是否一致；`identity/{jobId}.json` 里写出的中文名是否都在官方逐回合推出的当时场上、有没有同一个名字落在两条轨迹；记分牌的比分、节次、比赛钟是否对得上 `official/` 的逐回合。

要把报告放到网页上时才加 `--publish`（默认不放）。它写到网页桶 `qa/{jobId}.html`，并且只失效 `/qa/{jobId}.html`。

```bash
python3 scripts/qa/check_output.py <jobId> --publish
```
