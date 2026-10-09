import * as cdk from 'aws-cdk-lib/core';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as s3 from 'aws-cdk-lib/aws-s3';

const TEXT_MODEL = 'global.anthropic.claude-sonnet-4-6';

export interface P2Auth {
  authorizer: apigw.IAuthorizer;
  userPoolId: string;
  clientId: string;
}

/** 只在开启 P2 时创建用户池。读接口不挂这个授权器。 */
export function addP2Auth(stack: cdk.Stack): P2Auth | undefined {
  if (stack.node.tryGetContext('enableP2') === 'false') return undefined;

  const pool = new cognito.UserPool(stack, 'Judges', {
    userPoolName: 'deepcourt-judges',
    selfSignUpEnabled: false,
    signInAliases: { username: true },
    passwordPolicy: { minLength: 12 },
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });
  const client = pool.addClient('Web', {
    authFlows: { userPassword: true, userSrp: true },
    preventUserExistenceErrors: true,
  });
  const authorizer = new apigw.CognitoUserPoolsAuthorizer(stack, 'JobsAuthorizer', {
    cognitoUserPools: [pool],
  });
  new cdk.CfnOutput(stack, 'UserPoolId', { value: pool.userPoolId });
  return { authorizer, userPoolId: pool.userPoolId, clientId: client.userPoolClientId };
}

/**
 * 解说、密钥、快手回调。不改分析函数的调用关系。
 * POST /jobs 的授权在栈里单独挂上。
 */
export function addP2(
  stack: cdk.Stack,
  props: {
    api: apigw.RestApi;
    mediaBucket: s3.Bucket;
    code: lambda.Code;
    callbackUrl: string;
  },
): void {
  if (stack.node.tryGetContext('enableP2') === 'false') return;

  const feishu = new secretsmanager.Secret(stack, 'FeishuWebhook', {
    secretName: 'deepcourt/feishu-webhook',
    description: '飞书群 webhook。部署后写入真实地址。',
    generateSecretString: {
      secretStringTemplate: JSON.stringify({ webhook: '' }),
      generateStringKey: 'placeholder',
    },
  });
  const github = new secretsmanager.Secret(stack, 'GithubToken', {
    secretName: 'deepcourt/github-token',
    description: 'GitHub webhook token。部署后写入真实值。',
    generateSecretString: {
      secretStringTemplate: JSON.stringify({ token: '' }),
      generateStringKey: 'placeholder',
    },
  });
  const kuaishou = new secretsmanager.Secret(stack, 'KuaishouApp', {
    secretName: 'deepcourt/kuaishou',
    description: '快手 app_id、app_secret 和绑定后的 token。仓库里不放真实值。',
    generateSecretString: {
      secretStringTemplate: JSON.stringify({
        app_id: '',
        app_secret: '',
        refresh_token: '',
        access_token: '',
        open_id: '',
        pending_state: '',
      }),
      generateStringKey: 'placeholder',
    },
  });

  const commentary = fn(stack, 'Commentary', 'commentary.handler.handler', props.code, {
    MEDIA_BUCKET: props.mediaBucket.bucketName,
    TEXT_MODEL,
    FEISHU_SECRET_ARN: feishu.secretArn,
  }, 60, 512);
  props.mediaBucket.grantPut(commentary, 'commentary/*');
  feishu.grantRead(commentary);
  github.grantRead(commentary);
  commentary.addToRolePolicy(new iam.PolicyStatement({
    actions: ['polly:SynthesizeSpeech'],
    resources: ['*'],
  }));
  commentary.addToRolePolicy(new iam.PolicyStatement({
    actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
    resources: [
      'arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6',
      `arn:aws:bedrock:*:${cdk.Aws.ACCOUNT_ID}:inference-profile/${TEXT_MODEL}`,
    ],
  }));

  const oauth = fn(stack, 'KuaishouOauth', 'kuaishou.handler.handler', props.code, {
    KUAISHOU_SECRET_ARN: kuaishou.secretArn,
    CALLBACK_URL: props.callbackUrl,
  }, 20, 256);
  kuaishou.grantRead(oauth);
  kuaishou.grantWrite(oauth);

  const commentaryResource = props.api.root.addResource('commentary');
  commentaryResource.addMethod('POST', new apigw.LambdaIntegration(commentary));
  const oauthRoot = props.api.root.addResource('oauth');
  const ks = oauthRoot.addResource('kuaishou');
  ks.addResource('start').addMethod('GET', new apigw.LambdaIntegration(oauth));
  ks.addResource('callback').addMethod('GET', new apigw.LambdaIntegration(oauth));

  new cdk.CfnOutput(stack, 'KuaishouCallbackUrl', { value: props.callbackUrl });
}

function fn(
  stack: cdk.Stack,
  id: string,
  handler: string,
  code: lambda.Code,
  environment: Record<string, string>,
  timeoutSec: number,
  memory: number,
): lambda.Function {
  return new lambda.Function(stack, id, {
    runtime: lambda.Runtime.PYTHON_3_12,
    handler,
    code,
    timeout: cdk.Duration.seconds(timeoutSec),
    memorySize: memory,
    environment,
    tracing: lambda.Tracing.ACTIVE,
    logGroup: new logs.LogGroup(stack, `${id}Logs`, {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    }),
  });
}
