#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { DeepCourtStack } from '../lib/deepcourt-stack.js';

const app = new cdk.App();

// 主办方共享账号 278477606068 已关闭。栈部署到本队账号。
// 只有回到那个共享账号时才挂 NBAHackathonParticipantBoundary。
const account = process.env.CDK_DEFAULT_ACCOUNT || '405496568869';
const hackathonAccount = '278477606068';

new DeepCourtStack(app, 'Team04DeepCourt', {
  env: {
    account,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  permissionsBoundary: account === hackathonAccount
    ? cdk.PermissionsBoundary.fromName('NBAHackathonParticipantBoundary')
    : undefined,
  description: 'Deep Court P0: S3 upload, AgentCore/Bedrock analysis, CloudFront playback',
  tags: {
    team: '04',
    stage: 'prod',
    project: 'deepcourt',
  },
});
