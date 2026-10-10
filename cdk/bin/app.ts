#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { DeepCourtStack } from '../lib/deepcourt-stack.js';

const app = new cdk.App();

// 可部署账号：Workshop 参赛账号 382918437747（profile nba，需挂权限边界）。
// SSO 本队账号 405496568869（profile team04）目前没有 CDK/S3/CloudFront 创建权限。
// 旧共享账号 278477606068 已关闭。
const account = process.env.CDK_DEFAULT_ACCOUNT || '382918437747';
const boundedAccounts = new Set(['382918437747', '278477606068']);

new DeepCourtStack(app, 'Team04DeepCourt', {
  env: {
    account,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  permissionsBoundary: boundedAccounts.has(account)
    ? cdk.PermissionsBoundary.fromName('NBAHackathonParticipantBoundary')
    : undefined,
  description: 'Deep Court P0: S3 upload, AgentCore/Bedrock analysis, CloudFront playback',
  tags: {
    team: '04',
    stage: 'prod',
    project: 'deepcourt',
  },
});
