import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib/core';
import * as batch from 'aws-cdk-lib/aws-batch';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import { Construct } from 'constructs';

const TRANSIENT = [
  'States.Timeout',
  'DynamoDB.InternalServerError',
  'DynamoDB.ProvisionedThroughputExceededException',
  'DynamoDB.RequestLimitExceeded',
  'Lambda.ServiceException',
  'Lambda.AWSLambdaException',
  'Lambda.SdkClientException',
  'Lambda.ClientExecutionTimeoutException',
  'Batch.ServerException',
  'S3.InternalError',
  'S3.ServiceUnavailable',
  'CloudFront.ServiceUnavailable',
  'CloudFront.TooManyInvalidationsInProgress',
];

/**
 * 默认开。`-c enableP1=false` 或 `-c enableOrchestration=false` 时关掉。
 * 关掉后 S3 仍通知 analyze_job，状态机不会在上传时提交 Batch。
 */
export function orchestrationEnabled(scope: Construct): boolean {
  return !contextOff(scope, 'enableP1') && !contextOff(scope, 'enableOrchestration');
}

export function buildTrackerImageEnabled(scope: Construct): boolean {
  const value = scope.node.tryGetContext('buildTrackerImage');
  return value === true || value === 'true';
}

function contextOff(scope: Construct, key: string): boolean {
  const value = scope.node.tryGetContext(key);
  return value === false || value === 'false';
}

/**
 * 把仓库里的作业脚本放到媒体桶 bootstrap/track/，文件名和容器启动时下载的名字一致。
 * 状态机默认接上：raw/ 上传 → 改任务状态 → Batch .sync → analyze_job → 核对成片和片单并失效。
 * enableOrchestration=false 时只部署脚本，不建规则，也不拆掉 analyze_job 的 S3 通知。
 */
export function addOrchestration(
  stack: cdk.Stack,
  props: {
    mediaBucket: s3.Bucket;
    webBucket: s3.Bucket;
    distribution: cloudfront.IDistribution;
    jobs: dynamodb.ITable;
    analyzeFn: lambda.IFunction;
    queue: batch.IJobQueue;
    jobDefinition: batch.IJobDefinition;
    dashboard: cloudwatch.Dashboard;
    jobRole: iam.IRole;
    executionRole: iam.IRole;
  },
): void {
  deployTrackBootstrap(stack, props.mediaBucket);
  if (contextOff(stack, 'enableOrchestration')) return;

  props.mediaBucket.enableEventBridgeNotification();
  const machine = filmPipeline(stack, props);
  new events.Rule(stack, 'RawUpload', {
    ruleName: 'deepcourt-raw-upload',
    description: 'raw/ 上传只从这里启动成片状态机，避免再交一次 Batch。',
    eventPattern: {
      source: ['aws.s3'],
      detailType: ['Object Created'],
      detail: {
        bucket: { name: [props.mediaBucket.bucketName] },
        object: { key: [{ prefix: 'raw/' }] },
      },
    },
    targets: [new targets.SfnStateMachine(machine)],
  });

  props.dashboard.addWidgets(
    new cloudwatch.GraphWidget({
      title: '编排成功 / 失败',
      left: [machine.metricSucceeded({ period: cdk.Duration.minutes(1), label: '成功' })],
      right: [
        machine.metricFailed({ period: cdk.Duration.minutes(1), label: '失败' }),
        machine.metricTimedOut({ period: cdk.Duration.minutes(1), label: '超时' }),
      ],
      width: 12,
    }),
    new cloudwatch.GraphWidget({
      title: '编排耗时',
      left: [
        machine.metricTime({ period: cdk.Duration.minutes(1), statistic: 'Average', label: '平均' }),
        machine.metricTime({ period: cdk.Duration.minutes(1), statistic: 'Maximum', label: '最长' }),
      ],
      width: 12,
    }),
  );
  props.dashboard.addWidgets(new cloudwatch.TextWidget({
    markdown: [
      '状态机 `deepcourt-film`（标准工作流）是上传后唯一会提交 Batch 的路径。',
      'EventBridge 只匹配媒体桶 `raw/`。`SubmitTrack` 没有事件源。',
      '`structured/` 不再通知 `analyze_job`，由状态机在 Batch 成功后调用。',
      '片单正文仍由追踪作业写入网页桶 `replay/clips.js`；状态机核对成片和片单，再失效这几个路径。',
    ].join(' '),
    width: 24,
    height: 3,
  }));

  new cdk.CfnOutput(stack, 'FilmPipelineArn', { value: machine.stateMachineArn });
}

function deployTrackBootstrap(stack: cdk.Stack, mediaBucket: s3.Bucket): void {
  new s3deploy.BucketDeployment(stack, 'TrackBootstrap', {
    sources: [s3deploy.Source.asset(stageTrackBootstrap())],
    destinationBucket: mediaBucket,
    destinationKeyPrefix: 'bootstrap/track',
    extract: true,
    prune: false,
    retainOnDelete: true,
    // 和网页桶的 512MiB 部署分开，避免那个角色拿到媒体桶的写权限。
    memoryLimit: 256,
  });
}

/** 容器下载的是这些文件名。commentary.py 就是 pipeline/present/tracks.py。 */
function stageTrackBootstrap(): string {
  const dir = path.join(os.tmpdir(), 'deepcourt-track-bootstrap');
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(path.join(dir, 'web'), { recursive: true });
  const root = path.join(__dirname, '../..');
  const copy = (from: string, to: string) => {
    fs.copyFileSync(path.join(root, from), path.join(dir, to));
  };
  copy('inference/run_job.py', 'run_job.py');
  copy('inference/chain.py', 'chain.py');
  copy('inference/dub.py', 'dub.py');
  copy('pipeline/present/tracks.py', 'commentary.py');
  copy('web/game.html', 'web/game.html');
  copy('web/game.js', 'web/game.js');
  copy('web/styles.css', 'web/styles.css');
  return dir;
}

function filmPipeline(
  stack: cdk.Stack,
  props: {
    mediaBucket: s3.Bucket;
    webBucket: s3.Bucket;
    distribution: cloudfront.IDistribution;
    jobs: dynamodb.ITable;
    analyzeFn: lambda.IFunction;
    queue: batch.IJobQueue;
    jobDefinition: batch.IJobDefinition;
    jobRole: iam.IRole;
    executionRole: iam.IRole;
  },
): sfn.StateMachine {
  const failed = new sfn.Fail(stack, 'FilmFailed', {
    error: 'FilmPipelineFailed',
    cause: '任务已写成 FAILED',
  });
  const badInput = new sfn.Fail(stack, 'BadInput', {
    error: 'BadInput',
    cause: '需要 S3 raw/ 对象创建事件，或直接启动时带上已经在桶里的 jobId 和 key',
  });
  const badKey = new sfn.Fail(stack, 'BadRawKey', {
    error: 'BadRawKey',
    cause: '对象键必须是 raw/{jobId}/文件名',
  });
  const duplicate = new sfn.Succeed(stack, 'DuplicateSkipped', {
    comment: '同一任务已经在跑或刚跑完且状态不允许重入，不再提交第二次 Batch。',
  });
  const done = new sfn.Succeed(stack, 'FilmDone', {
    comment: 'Batch、校验和 CloudFront 失效都完成了。',
  });
  const analyzeFailed = new sfn.Fail(stack, 'AnalyzeFailed', {
    error: 'AnalyzeFailed',
    cause: 'analyze_job 没有通过。任务状态和原因由 Lambda 写入 Jobs 表。',
  });

  const markFailed = retry(new tasks.DynamoUpdateItem(stack, 'MarkFailed', {
    comment: '失败时把 Jobs 表写成 FAILED，并记下是哪一步。',
    table: props.jobs,
    key: {
      jobId: tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$.jobId')),
    },
    updateExpression: 'SET #status = :status, #updatedAt = :updatedAt, #error = :error, #submittedBy = :by',
    expressionAttributeNames: {
      '#status': 'status',
      '#updatedAt': 'updatedAt',
      '#error': 'error',
      '#submittedBy': 'submittedBy',
    },
    expressionAttributeValues: {
      ':status': tasks.DynamoAttributeValue.fromString('FAILED'),
      ':updatedAt': tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$$.State.EnteredTime')),
      ':error': tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$.cause')),
      ':by': tasks.DynamoAttributeValue.fromString('stepfunctions'),
    },
    resultPath: sfn.JsonPath.DISCARD,
  }));
  markFailed.next(failed);
  markFailed.addCatch(failed, { errors: ['States.ALL'], resultPath: '$.error' });

  const useDirect = new sfn.Pass(stack, 'UseDirectInput', {
    comment: '直接 StartExecution 的输入：{jobId, key}。create_job 不调用这里，避免和上传事件各交一次。',
    parameters: {
      jobId: sfn.JsonPath.stringAt('$.jobId'),
      key: sfn.JsonPath.stringAt('$.key'),
    },
  });
  const splitRaw = new sfn.Pass(stack, 'SplitRawKey', {
    parameters: {
      key: sfn.JsonPath.stringAt('$.detail.object.key'),
      parts: sfn.JsonPath.stringAt("States.StringSplit($.detail.object.key, '/')"),
    },
  });
  const countParts = new sfn.Pass(stack, 'CountRawParts', {
    parameters: {
      key: sfn.JsonPath.stringAt('$.key'),
      parts: sfn.JsonPath.stringAt('$.parts'),
      prefix: sfn.JsonPath.stringAt('States.ArrayGetItem($.parts, 0)'),
      partCount: sfn.JsonPath.stringAt('States.ArrayLength($.parts)'),
    },
  });
  const jobFromKey = new sfn.Pass(stack, 'JobIdFromKey', {
    parameters: {
      key: sfn.JsonPath.stringAt('$.key'),
      jobId: sfn.JsonPath.stringAt('States.ArrayGetItem($.parts, 1)'),
    },
  });
  const attachBucket = new sfn.Pass(stack, 'AttachBucket', {
    parameters: {
      jobId: sfn.JsonPath.stringAt('$.jobId'),
      key: sfn.JsonPath.stringAt('$.key'),
      bucket: props.mediaBucket.bucketName,
    },
  });
  const derive = new sfn.Pass(stack, 'DerivePaths', {
    comment: '片单路径留给后面的核对。正文由追踪作业写入，状态机不覆盖 replay/clips.js。',
    parameters: {
      jobId: sfn.JsonPath.stringAt('$.jobId'),
      key: sfn.JsonPath.stringAt('$.key'),
      videoS3: sfn.JsonPath.stringAt("States.Format('s3://{}/{}', $.bucket, $.key)"),
      outputS3: sfn.JsonPath.stringAt("States.Format('s3://{}/tracking/{}/tracks.json', $.bucket, $.jobId)"),
      structuredKey: sfn.JsonPath.stringAt("States.Format('structured/{}.json', $.jobId)"),
      jobName: sfn.JsonPath.stringAt("States.Format('track-{}', $.jobId)"),
      filmKey: sfn.JsonPath.stringAt("States.Format('assets/{}-broadcast.mp4', $.jobId)"),
      playlistKey: 'replay/clips.js',
      invalidationRef: sfn.JsonPath.stringAt("States.Format('{}-{}', $.jobId, $$.Execution.Name)"),
      paths: sfn.JsonPath.stringAt(
        "States.Array(States.Format('/assets/{}-broadcast.mp4', $.jobId), States.Format('/assets/{}-zh.mp4', $.jobId), States.Format('/assets/{}-en.mp4', $.jobId), '/replay/clips.js')",
      ),
    },
  });

  const markTracking = retry(new tasks.DynamoUpdateItem(stack, 'MarkTracking', {
    comment: '条件更新挡住同一次上传的第二笔事件。手工 submit-job 不走这里。',
    table: props.jobs,
    key: {
      jobId: tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$.jobId')),
    },
    updateExpression: 'SET #status = :status, #updatedAt = :updatedAt, #error = :empty, #submittedBy = :by',
    conditionExpression: 'attribute_not_exists(#status) OR #status IN (:pending, :failed, :done)',
    expressionAttributeNames: {
      '#status': 'status',
      '#updatedAt': 'updatedAt',
      '#error': 'error',
      '#submittedBy': 'submittedBy',
    },
    expressionAttributeValues: {
      ':status': tasks.DynamoAttributeValue.fromString('TRACKING'),
      ':updatedAt': tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$$.State.EnteredTime')),
      ':empty': tasks.DynamoAttributeValue.fromString(''),
      ':by': tasks.DynamoAttributeValue.fromString('stepfunctions'),
      ':pending': tasks.DynamoAttributeValue.fromString('PENDING'),
      ':failed': tasks.DynamoAttributeValue.fromString('FAILED'),
      ':done': tasks.DynamoAttributeValue.fromString('DONE'),
    },
    resultPath: sfn.JsonPath.DISCARD,
  }));
  markTracking.addCatch(duplicate, {
    errors: ['DynamoDB.ConditionalCheckFailedException'],
    resultPath: '$.error',
  });
  catchFailed(stack, markFailed, 'MarkTracking', markTracking);

  const submit = retry(new tasks.BatchSubmitJob(stack, 'SubmitBatch', {
    comment: '唯一的自动提交。sync 等到作业 SUCCEEDED 或失败。单次尝试上限 4 小时。',
    jobDefinitionArn: props.jobDefinition.jobDefinitionArn,
    jobQueueArn: props.queue.jobQueueArn,
    jobName: sfn.JsonPath.stringAt('$.jobName'),
    integrationPattern: sfn.IntegrationPattern.RUN_JOB,
    taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(4)),
    containerOverrides: {
      environment: {
        JOB_ID: sfn.JsonPath.stringAt('$.jobId'),
        VIDEO_S3: sfn.JsonPath.stringAt('$.videoS3'),
        OUTPUT_S3: sfn.JsonPath.stringAt('$.outputS3'),
      },
    },
    resultSelector: {
      'JobId.$': '$.JobId',
      'Status.$': '$.Status',
    },
    resultPath: '$.batch',
  }));
  catchFailed(stack, markFailed, 'SubmitBatch', submit);

  const analyze = retry(new tasks.LambdaInvoke(stack, 'Analyze', {
    comment: '调用现有 analyze_job。输入是 structured/{jobId}.json，不再靠 S3 通知。',
    lambdaFunction: props.analyzeFn,
    payload: sfn.TaskInput.fromObject({
      jobId: sfn.JsonPath.stringAt('$.jobId'),
      key: sfn.JsonPath.stringAt('$.structuredKey'),
    }),
    payloadResponseOnly: true,
    retryOnServiceExceptions: false,
    resultPath: '$.analyze',
    // analyze_job 的 Lambda 超时是 240 秒，分析师预算约 200 秒。这里留出返回时间，避免状态机先超时、函数后把任务写成 DONE。
    taskTimeout: sfn.Timeout.duration(cdk.Duration.seconds(300)),
  }));
  catchFailed(stack, markFailed, 'Analyze', analyze);

  const markPublishing = retry(new tasks.DynamoUpdateItem(stack, 'MarkPublishing', {
    comment: '校验已结束。接下来核对网页桶里的成片和片单。',
    table: props.jobs,
    key: {
      jobId: tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$.jobId')),
    },
    updateExpression: 'SET #status = :status, #updatedAt = :updatedAt, #error = :empty',
    expressionAttributeNames: {
      '#status': 'status',
      '#updatedAt': 'updatedAt',
      '#error': 'error',
    },
    expressionAttributeValues: {
      ':status': tasks.DynamoAttributeValue.fromString('PUBLISHING'),
      ':updatedAt': tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$$.State.EnteredTime')),
      ':empty': tasks.DynamoAttributeValue.fromString(''),
    },
    resultPath: sfn.JsonPath.DISCARD,
  }));
  catchFailed(stack, markFailed, 'MarkPublishing', markPublishing);

  const distributionArn = `arn:aws:cloudfront::${stack.account}:distribution/${props.distribution.distributionId}`;
  const headFilm = headObject(stack, 'HeadFilm', props.webBucket, '$.filmKey', props.webBucket.arnForObjects('assets/*'));
  const headPlaylist = headObject(
    stack,
    'HeadPlaylist',
    props.webBucket,
    '$.playlistKey',
    props.webBucket.arnForObjects('replay/clips.js'),
  );
  catchFailed(stack, markFailed, 'HeadFilm', headFilm);
  catchFailed(stack, markFailed, 'HeadPlaylist', headPlaylist);

  const invalidate = retry(new tasks.CallAwsService(stack, 'Invalidate', {
    comment: '只失效这一支片子的成片、音轨和片单，不失效根路径。',
    service: 'cloudfront',
    action: 'createInvalidation',
    iamAction: 'cloudfront:CreateInvalidation',
    iamResources: [distributionArn],
    parameters: {
      DistributionId: props.distribution.distributionId,
      InvalidationBatch: {
        CallerReference: sfn.JsonPath.stringAt('$.invalidationRef'),
        Paths: {
          Quantity: 4,
          Items: sfn.JsonPath.stringAt('$.paths'),
        },
      },
    },
    resultPath: sfn.JsonPath.DISCARD,
  }));
  catchFailed(stack, markFailed, 'Invalidate', invalidate);

  const markDone = retry(new tasks.DynamoUpdateItem(stack, 'MarkDone', {
    table: props.jobs,
    key: {
      jobId: tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$.jobId')),
    },
    updateExpression: 'SET #status = :status, #updatedAt = :updatedAt, #error = :empty',
    expressionAttributeNames: {
      '#status': 'status',
      '#updatedAt': 'updatedAt',
      '#error': 'error',
    },
    expressionAttributeValues: {
      ':status': tasks.DynamoAttributeValue.fromString('DONE'),
      ':updatedAt': tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$$.State.EnteredTime')),
      ':empty': tasks.DynamoAttributeValue.fromString(''),
    },
    resultPath: sfn.JsonPath.DISCARD,
  }));
  catchFailed(stack, markFailed, 'MarkDone', markDone);

  const analyzed = new sfn.Choice(stack, 'AnalyzeOk')
    .when(sfn.Condition.and(
      sfn.Condition.isPresent('$.analyze.ok'),
      sfn.Condition.booleanEquals('$.analyze.ok', true),
    ), markPublishing)
    .otherwise(analyzeFailed);

  const keyShape = new sfn.Choice(stack, 'RawKeyShape')
    .when(sfn.Condition.and(
      sfn.Condition.numberGreaterThanEquals('$.partCount', 3),
      sfn.Condition.stringEquals('$.prefix', 'raw'),
    ), jobFromKey)
    .otherwise(badKey);

  const route = new sfn.Choice(stack, 'RouteInput')
    .when(sfn.Condition.and(
      sfn.Condition.isPresent('$.jobId'),
      sfn.Condition.isPresent('$.key'),
      sfn.Condition.stringMatches('$.key', 'raw/*'),
    ), useDirect)
    .when(sfn.Condition.isPresent('$.detail.object.key'), splitRaw)
    .otherwise(badInput);

  useDirect.next(attachBucket);
  splitRaw.next(countParts);
  countParts.next(keyShape);
  jobFromKey.next(attachBucket);
  attachBucket.next(derive);
  derive.next(markTracking);
  markTracking.next(submit);
  submit.next(analyze);
  analyze.next(analyzed);
  markPublishing.next(headFilm);
  headFilm.next(headPlaylist);
  headPlaylist.next(invalidate);
  invalidate.next(markDone);
  markDone.next(done);

  const machine = new sfn.StateMachine(stack, 'FilmPipeline', {
    stateMachineName: 'deepcourt-film',
    stateMachineType: sfn.StateMachineType.STANDARD,
    definitionBody: sfn.DefinitionBody.fromChainable(route),
    queryLanguage: sfn.QueryLanguage.JSON_PATH,
    timeout: cdk.Duration.hours(5),
    tracingEnabled: true,
    logs: {
      destination: new logs.LogGroup(stack, 'FilmPipelineLogs', {
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      level: sfn.LogLevel.ALL,
      includeExecutionData: true,
    },
  });
  machine.addToRolePolicy(new iam.PolicyStatement({
    actions: ['batch:DescribeJobs', 'batch:TerminateJob'],
    resources: ['*'],
  }));
  machine.addToRolePolicy(new iam.PolicyStatement({
    actions: ['iam:PassRole'],
    resources: [props.jobRole.roleArn, props.executionRole.roleArn],
    conditions: {
      StringEquals: {
        'iam:PassedToService': 'ecs-tasks.amazonaws.com',
      },
    },
  }));
  return machine;
}

function headObject(
  stack: cdk.Stack,
  id: string,
  bucket: s3.IBucket,
  keyPath: string,
  objectArn: string,
): tasks.CallAwsService {
  return retry(new tasks.CallAwsService(stack, id, {
    comment: '核对追踪作业已经写好的对象。不在这里重写片单，避免盖掉训练片。',
    service: 's3',
    action: 'headObject',
    iamAction: 's3:GetObject',
    iamResources: [objectArn],
    additionalIamStatements: [
      new iam.PolicyStatement({
        actions: ['s3:ListBucket'],
        resources: [bucket.bucketArn],
      }),
    ],
    parameters: {
      Bucket: bucket.bucketName,
      Key: sfn.JsonPath.stringAt(keyPath),
    },
    resultPath: sfn.JsonPath.DISCARD,
  }));
}

function retry<T extends sfn.TaskStateBase>(task: T, attempts = 3): T {
  task.addRetry({
    errors: TRANSIENT,
    interval: cdk.Duration.seconds(5),
    backoffRate: 2,
    maxAttempts: attempts,
  });
  return task;
}

function catchFailed(
  stack: cdk.Stack,
  markFailed: sfn.IChainable,
  id: string,
  task: sfn.TaskStateBase,
): void {
  const note = new sfn.Pass(stack, `${id}Note`, {
    comment: `${id} 失败，把任务写成 FAILED。`,
    parameters: {
      jobId: sfn.JsonPath.stringAt('$.jobId'),
      cause: sfn.JsonPath.stringAt(`States.Format('${id}: {}', States.JsonToString($.error))`),
    },
  });
  note.next(markFailed);
  task.addCatch(note, { errors: ['States.ALL'], resultPath: '$.error' });
}
