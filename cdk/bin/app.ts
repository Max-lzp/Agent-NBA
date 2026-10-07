#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { DeepCourtStack } from '../lib/deepcourt-stack.js';

const app = new cdk.App();

new DeepCourtStack(app, 'Team04DeepCourt', {
  env: {
    account: '278477606068',
    region: 'us-east-1',
  },
  // 团队账号只允许创建挂了该权限边界的 IAM 角色。不要 cdk bootstrap。
  permissionsBoundary: cdk.PermissionsBoundary.fromName('NBAHackathonParticipantBoundary'),
  description: 'Deep Court P0: S3 upload, AgentCore/Bedrock analysis, CloudFront playback',
  tags: {
    team: '04',
    stage: 'prod',
    project: 'deepcourt',
  },
});
