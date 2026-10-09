import * as cdk from 'aws-cdk-lib/core';
import * as batch from 'aws-cdk-lib/aws-batch';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as mediaconvert from 'aws-cdk-lib/aws-mediaconvert';
import * as s3 from 'aws-cdk-lib/aws-s3';

/**
 * P1 加在同一个栈里，避免再做一层跨栈导出。
 * 上下文 enableP1=false 时整段跳过，GPU 配额不够也不会挡住 P0。
 */
export function addP1(
  stack: cdk.Stack,
  props: {
    mediaBucket: s3.Bucket;
    jobs: dynamodb.Table;
    events: dynamodb.Table;
    analyzeFn: lambda.Function;
    code: lambda.Code;
  },
): void {
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
        volume: ec2.BlockDeviceVolume.ebs(100, {
          volumeType: ec2.EbsDeviceVolumeType.GP3,
          encrypted: true,
        }),
      },
    ],
  });
  const compute = new batch.ManagedEc2EcsComputeEnvironment(stack, 'GpuCompute', {
    vpc,
    vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
    instanceTypes: [ec2.InstanceType.of(ec2.InstanceClass.G4DN, ec2.InstanceSize.XLARGE)],
    useOptimalInstanceClasses: false,
    images: [{ imageType: batch.EcsMachineImageType.ECS_AL2023_NVIDIA }],
    minvCpus: 0,
    maxvCpus: 4,
    launchTemplate,
  });
  const queue = new batch.JobQueue(stack, 'TrackQueue', {
    jobQueueName: 'deepcourt-track',
    computeEnvironments: [{ computeEnvironment: compute, order: 1 }],
  });

  const jobRole = new iam.Role(stack, 'TrackJobRole', {
    assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    description: 'Deep Court Batch tracker reads raw video and writes tracking JSON',
  });
  props.mediaBucket.grantRead(jobRole, 'raw/*');
  props.mediaBucket.grantPut(jobRole, 'tracking/*');
  props.jobs.grantWriteData(jobRole);

  const jobDef = new batch.EcsJobDefinition(stack, 'TrackJob', {
    jobDefinitionName: 'deepcourt-track',
    container: new batch.EcsEc2ContainerDefinition(stack, 'TrackContainer', {
      image: ecs.ContainerImage.fromEcrRepository(repo, 'latest'),
      cpu: 4,
      memory: cdk.Size.mebibytes(8192),
      gpu: 1,
      jobRole,
      command: ['python3', 'track.py'],
      environment: { MEDIA_BUCKET: props.mediaBucket.bucketName },
    }),
    retryAttempts: 1,
  });

  const submitFn = new lambda.Function(stack, 'SubmitTrack', {
    runtime: lambda.Runtime.PYTHON_3_12,
    handler: 'submit_track.handler.handler',
    code: props.code,
    timeout: cdk.Duration.seconds(20),
    memorySize: 256,
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
    description: 'Overlay a PNG on the source and write a 1080p MP4.',
    settingsJson: {
      Inputs: [
        {
          TimecodeSource: 'ZEROBASED',
          FileInput: 's3://placeholder/raw/input.mp4',
          VideoSelector: {},
          AudioSelectors: {
            'Audio Selector 1': { DefaultSelection: 'DEFAULT' },
          },
          MotionImageInserter: {
            Input: 's3://placeholder/overlays/overlay.png',
            InsertionMode: 'PNG',
            StartTime: '00:00:00:00',
            Playback: 'REPEAT',
            Offset: { ImageX: 0, ImageY: 0 },
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
    markdown: `Batch 队列 \`${queue.jobQueueName}\`，空闲时 vCPU 为 0。跟踪作业由 SubmitTrack 提交，不走 P0 的分析函数。`,
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
}
