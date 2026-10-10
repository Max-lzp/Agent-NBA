import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib/core';
import * as batch from 'aws-cdk-lib/aws-batch';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as mediaconvert from 'aws-cdk-lib/aws-mediaconvert';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { buildTrackerImageEnabled } from './orchestration';
import { BASKETBALL_TERMINOLOGY_NAME, basketballTerminologyArn, importBasketballTerminology } from './terminology';

const TRACK_BOOTSTRAP = [
  'import os, boto3, runpy',
  'from pathlib import Path',
  'bucket = os.environ["MEDIA_BUCKET"]',
  'root = Path("/opt/track")',
  's3 = boto3.client("s3")',
  'required = {"run_job.py", "chain.py", "dub.py", "commentary.py"}',
  'missing = []',
  'names = ["run_job.py", "chain.py", "dub.py", "commentary.py", "web/game.html", "web/game.js", "web/styles.css"]',
  'for name in names:',
  '    dest = root / name',
  '    dest.parent.mkdir(parents=True, exist_ok=True)',
  '    try:',
  '        s3.download_file(bucket, "bootstrap/track/" + name, str(dest))',
  '        print("overlay", name)',
  '    except Exception as exc:',
  '        print("keep baked", name, type(exc).__name__)',
  '        if name in required:',
  '            missing.append(name)',
  'if missing:',
  '    raise SystemExit("解说脚本没拉下来: " + ",".join(missing))',
  'runpy.run_path(str(root / "run_job.py"), run_name="__main__")',
].join('\n');

/**
 * P1 加在同一个栈里，避免再做一层跨栈导出。
 * 上下文 enableP1=false 时整段跳过。
 * G/VT 配额为 0，跟踪用标准配额 256 vCPU 里最大的 c7i.48xlarge（192 vCPU）。
 * minvCpus 保持 192，下一支片子不再等开机。镜像在主机上烤好后，作业直接跑 run_job.py。
 */
export function addP1(
  stack: cdk.Stack,
  props: {
    mediaBucket: s3.Bucket;
    webBucket: s3.Bucket;
    distribution: cloudfront.Distribution;
    jobs: dynamodb.Table;
    events: dynamodb.Table;
    analyzeFn: lambda.Function;
    code: lambda.Code;
  },
): {
  queue: batch.IJobQueue;
  jobDefinition: batch.IJobDefinition;
  dashboard: cloudwatch.Dashboard;
  jobRole: iam.IRole;
  executionRole: iam.IRole;
} {
  const repo = new ecr.Repository(stack, 'TrackerRepo', {
    repositoryName: 'deepcourt-tracker',
    imageScanOnPush: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
    emptyOnDelete: false,
  });

  const vpc = new ec2.Vpc(stack, 'BatchVpc', {
    natGateways: 0,
    availabilityZones: ['us-east-1a', 'us-east-1b'],
  });
  const launchTemplate = new ec2.LaunchTemplate(stack, 'GpuLaunchTemplate', {
    blockDevices: [
      {
        deviceName: '/dev/xvda',
        volume: ec2.BlockDeviceVolume.ebs(1000, {
          volumeType: ec2.EbsDeviceVolumeType.GP3,
          encrypted: true,
          iops: 16000,
          throughput: 1000,
        }),
      },
    ],
  });
  const instanceRole = new iam.Role(stack, 'TrackInstanceRole', {
    assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
    description: 'Deep Court Batch host pulls and pushes the tracker image',
    managedPolicies: [
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonEC2ContainerServiceforEC2Role'),
      iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
    ],
  });
  repo.grantPullPush(instanceRole);
  props.mediaBucket.grantRead(instanceRole, 'bootstrap/*');
  props.mediaBucket.grantPut(instanceRole, 'bootstrap/bake.log');

  const compute = new batch.ManagedEc2EcsComputeEnvironment(stack, 'GpuCompute', {
    vpc,
    vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
    instanceTypes: [ec2.InstanceType.of(ec2.InstanceClass.C7I, ec2.InstanceSize.XLARGE48)],
    useOptimalInstanceClasses: false,
    images: [{ imageType: batch.EcsMachineImageType.ECS_AL2023 }],
    minvCpus: 192,
    maxvCpus: 256,
    launchTemplate,
    instanceRole,
  });
  const queue = new batch.JobQueue(stack, 'TrackQueue', {
    jobQueueName: 'deepcourt-track',
    computeEnvironments: [{ computeEnvironment: compute, order: 1 }],
  });

  const jobRole = new iam.Role(stack, 'TrackJobRole', {
    assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    description: 'Deep Court Batch tracker reads raw video and writes the film to the site bucket',
  });
  props.mediaBucket.grantRead(jobRole, 'raw/*');
  props.mediaBucket.grantRead(jobRole, 'bootstrap/*');
  props.mediaBucket.grantRead(jobRole, 'official/*');
  props.mediaBucket.grantPut(jobRole, 'tracking/*');
  props.mediaBucket.grantPut(jobRole, 'identity/*');
  props.mediaBucket.grantPut(jobRole, 'structured/*');
  props.jobs.grantWriteData(jobRole);
  props.webBucket.grantRead(jobRole);
  props.webBucket.grantPut(jobRole, 'assets/*');
  props.webBucket.grantPut(jobRole, 'replay/*');
  props.webBucket.grantPut(jobRole, 'game.html');
  props.webBucket.grantPut(jobRole, 'game.js');
  props.webBucket.grantPut(jobRole, 'styles.css');
  props.mediaBucket.grantReadWrite(jobRole, 'commentary/asr/*');
  jobRole.addToPolicy(new iam.PolicyStatement({
    actions: ['bedrock:InvokeModel'],
    resources: ['arn:aws:bedrock:us-east-1::foundation-model/qwen.qwen3-vl-235b-a22b'],
  }));
  jobRole.addToPolicy(new iam.PolicyStatement({
    actions: ['rekognition:DetectText'],
    resources: ['*'],
  }));
  jobRole.addToPolicy(new iam.PolicyStatement({
    actions: [
      'transcribe:StartTranscriptionJob',
      'transcribe:GetTranscriptionJob',
      'translate:TranslateText',
      'polly:SynthesizeSpeech',
      'textract:DetectDocumentText',
    ],
    resources: ['*'],
  }));
  // 服务授权参考里 TranslateText 的资源类型是可选的 terminology / parallel-data，不是必填。
  // 不带术语表的回退请求没有术语表这条资源，Resource 仍要按 * 保留在上面的语句里。
  // 带上术语表时另要 GetTerminology。这一条限定到本表 ARN（官方格式带 /LATEST）。
  // TranslateText 再授一次同一 ARN，带表的授权请求能对上这条资源。
  jobRole.addToPolicy(new iam.PolicyStatement({
    actions: ['translate:GetTerminology', 'translate:TranslateText'],
    resources: [basketballTerminologyArn(stack)],
  }));
  jobRole.addToPolicy(new iam.PolicyStatement({
    actions: ['cloudfront:CreateInvalidation'],
    resources: [`arn:aws:cloudfront::${stack.account}:distribution/${props.distribution.distributionId}`],
  }));
  const executionRole = new iam.Role(stack, 'TrackExecutionRole', {
    assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    description: 'Deep Court Batch agent pulls the baked tracker image',
    managedPolicies: [
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
    ],
  });
  repo.grantPull(executionRole);

  // 默认仍指向已经烤好的 :ready，TrackJob / GpuCompute / TrackQueue 的逻辑 ID 不动。
  // 干净账号没有这个标签时：-c buildTrackerImage=true，从仓库根目录的 inference/Dockerfile 构建。
  const image = trackerImage(stack, repo, executionRole);
  const terminology = importBasketballTerminology(stack);
  const jobDef = new batch.EcsJobDefinition(stack, 'TrackJob', {
    jobDefinitionName: 'deepcourt-track',
    container: new batch.EcsEc2ContainerDefinition(stack, 'TrackContainer', {
      image,
      cpu: 180,
      memory: cdk.Size.gibibytes(340),
      jobRole,
      executionRole,
      command: ['python3', '-c', TRACK_BOOTSTRAP],
      environment: {
        MEDIA_BUCKET: props.mediaBucket.bucketName,
        WEB_BUCKET: props.webBucket.bucketName,
        DISTRIBUTION_ID: props.distribution.distributionId,
        OMP_NUM_THREADS: '180',
        MKL_NUM_THREADS: '180',
        TRANSLATE_TERMINOLOGY: BASKETBALL_TERMINOLOGY_NAME,
      },
    }),
    retryAttempts: 1,
  });
  jobDef.node.addDependency(terminology);

  // 没有事件源。自动提交只在 orchestration.ts 的状态机里。留下这个函数，避免改掉已经部署的逻辑 ID。
  const submitFn = new lambda.Function(stack, 'SubmitTrack', {
    runtime: lambda.Runtime.PYTHON_3_12,
    handler: 'submit_track.handler.handler',
    code: props.code,
    timeout: cdk.Duration.seconds(20),
    memorySize: 256,
    tracing: stack.node.tryGetContext('enableP2') === 'false'
      ? lambda.Tracing.DISABLED
      : lambda.Tracing.ACTIVE,
    environment: {
      JOB_QUEUE: queue.jobQueueName,
      JOB_DEFINITION: jobDef.jobDefinitionName,
      MEDIA_BUCKET: props.mediaBucket.bucketName,
    },
    logGroup: new logs.LogGroup(stack, 'SubmitTrackLogs', {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    }),
  });
  submitFn.addToRolePolicy(new iam.PolicyStatement({
    actions: ['batch:SubmitJob'],
    resources: [queue.jobQueueArn, jobDef.jobDefinitionArn, `${jobDef.jobDefinitionArn}:*`],
  }));

  const ocrFn = new lambda.Function(stack, 'OcrScoreboard', {
    runtime: lambda.Runtime.PYTHON_3_12,
    handler: 'ocr_scoreboard.handler.handler',
    code: props.code,
    timeout: cdk.Duration.seconds(30),
    memorySize: 512,
    tracing: stack.node.tryGetContext('enableP2') === 'false'
      ? lambda.Tracing.DISABLED
      : lambda.Tracing.ACTIVE,
    environment: {
      JOBS_TABLE: props.jobs.tableName,
      EVENTS_TABLE: props.events.tableName,
      MEDIA_BUCKET: props.mediaBucket.bucketName,
    },
    logGroup: new logs.LogGroup(stack, 'OcrScoreboardLogs', {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    }),
  });
  props.jobs.grantWriteData(ocrFn);
  props.events.grantWriteData(ocrFn);
  props.mediaBucket.grantRead(ocrFn, 'raw/*');
  props.mediaBucket.grantRead(ocrFn, 'frames/*');
  ocrFn.addToRolePolicy(new iam.PolicyStatement({
    actions: ['textract:DetectDocumentText'],
    resources: ['*'],
  }));

  const convertRole = new iam.Role(stack, 'ConvertRole', {
    assumedBy: new iam.ServicePrincipal('mediaconvert.amazonaws.com'),
    description: 'Deep Court MediaConvert reads overlays and writes the rendered mp4',
  });
  props.mediaBucket.grantRead(convertRole, 'raw/*');
  props.mediaBucket.grantRead(convertRole, 'overlays/*');
  props.mediaBucket.grantPut(convertRole, 'rendered/*');

  new mediaconvert.CfnJobTemplate(stack, 'RenderTemplate', {
    name: 'deepcourt-render-template',
    description: '1080p H.264 MP4. 输入和叠加在提交作业时指定。',
    settingsJson: {
      Inputs: [
        {
          TimecodeSource: 'ZEROBASED',
          VideoSelector: {},
          AudioSelectors: {
            'Audio Selector 1': { DefaultSelection: 'DEFAULT' },
          },
        },
      ],
      OutputGroups: [
        {
          Name: 'File Group',
          OutputGroupSettings: {
            Type: 'FILE_GROUP_SETTINGS',
            FileGroupSettings: { Destination: 's3://placeholder/rendered/' },
          },
          Outputs: [
            {
              ContainerSettings: { Container: 'MP4', Mp4Settings: {} },
              VideoDescription: {
                Width: 1920,
                Height: 1080,
                CodecSettings: {
                  Codec: 'H_264',
                  H264Settings: {
                    RateControlMode: 'QVBR',
                    MaxBitrate: 5000000,
                    SceneChangeDetect: 'TRANSITION_DETECTION',
                  },
                },
              },
              AudioDescriptions: [
                {
                  CodecSettings: {
                    Codec: 'AAC',
                    AacSettings: {
                      Bitrate: 96000,
                      CodingMode: 'CODING_MODE_2_0',
                      SampleRate: 48000,
                    },
                  },
                },
              ],
            },
          ],
        },
      ],
    },
  });

  const dashboard = new cloudwatch.Dashboard(stack, 'Overview', {
    dashboardName: 'deepcourt-overview',
  });
  dashboard.addWidgets(
    new cloudwatch.GraphWidget({
      title: '任务吞吐',
      left: [props.analyzeFn.metricInvocations()],
      width: 8,
    }),
    new cloudwatch.GraphWidget({
      title: '分析延迟',
      left: [props.analyzeFn.metricDuration()],
      width: 8,
    }),
    new cloudwatch.GraphWidget({
      title: 'Bedrock 限流',
      left: [
        new cloudwatch.Metric({
          namespace: 'AWS/Bedrock',
          metricName: 'InvocationThrottles',
          statistic: 'Sum',
          period: cdk.Duration.minutes(1),
        }),
      ],
      width: 8,
    }),
  );
  dashboard.addWidgets(new cloudwatch.TextWidget({
    markdown: `Batch 队列 \`${queue.jobQueueName}\`，实例 c7i.48xlarge（192 vCPU）保持开着，上限 256 vCPU。镜像 \`deepcourt-tracker:ready\`。G/VT 配额为 0。作业在同一台机器上写轨迹、记分牌和成片。`,
    width: 24,
    height: 2,
  }));

  const errorAlarm = new cloudwatch.Alarm(stack, 'AnalyzeErrorAlarm', {
    alarmDescription: '分析 Lambda 连续 2 分钟有错误',
    metric: props.analyzeFn.metricErrors({ period: cdk.Duration.minutes(1) }),
    threshold: 0,
    evaluationPeriods: 2,
    comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
  });
  const throttleAlarm = new cloudwatch.Alarm(stack, 'BedrockThrottleAlarm', {
    alarmDescription: 'Bedrock 每分钟限流超过 10 次',
    metric: new cloudwatch.Metric({
      namespace: 'AWS/Bedrock',
      metricName: 'InvocationThrottles',
      statistic: 'Sum',
      period: cdk.Duration.minutes(1),
    }),
    threshold: 10,
    evaluationPeriods: 1,
    comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
  });

  new cdk.CfnOutput(stack, 'TrackerRepoUri', { value: repo.repositoryUri });
  new cdk.CfnOutput(stack, 'TrackJobQueue', { value: queue.jobQueueName });
  new cdk.CfnOutput(stack, 'ConvertRoleArn', { value: convertRole.roleArn });
  new cdk.CfnOutput(stack, 'AnalyzeErrorAlarmName', { value: errorAlarm.alarmName });
  new cdk.CfnOutput(stack, 'BedrockThrottleAlarmName', { value: throttleAlarm.alarmName });
  return { queue, jobDefinition: jobDef, dashboard, jobRole, executionRole };
}

function trackerImage(stack: cdk.Stack, repo: ecr.IRepository, executionRole: iam.IRole): ecs.ContainerImage {
  if (!buildTrackerImageEnabled(stack)) {
    return ecs.ContainerImage.fromEcrRepository(repo, 'ready');
  }
  const asset = new ecrAssets.DockerImageAsset(stack, 'TrackerImage', {
    directory: path.join(__dirname, '../..'),
    file: 'inference/Dockerfile',
    platform: ecrAssets.Platform.LINUX_AMD64,
    exclude: [
      '.git',
      'cdk/node_modules',
      'cdk/cdk.out',
      'cdk/dist',
      'data',
      'pipeline/vision/.venv',
      'pipeline/vision/work',
      'pipeline/vision/models',
      'pipeline/vision/results',
      'web/assets',
      '**/*.mp4',
      '**/*.pt',
      '**/__pycache__',
    ],
  });
  asset.repository.grantPull(executionRole);
  return ecs.ContainerImage.fromDockerImageAsset(asset);
}
