import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib/core';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as iam from 'aws-cdk-lib/aws-iam';

export const BASKETBALL_TERMINOLOGY_NAME = 'deepcourt-basketball';

const CSV_PATH = path.join(__dirname, '../../data/terminology/basketball-en-zh.csv');

/**
 * 开发者指南要求策略里的术语表 ARN 写成 terminology/名称/LATEST。
 * GetTerminology 返回的 ARN 也是这个形式。
 */
export function basketballTerminologyArn(stack: cdk.Stack): string {
  return stack.formatArn({
    service: 'translate',
    resource: 'terminology',
    resourceName: `${BASKETBALL_TERMINOLOGY_NAME}/LATEST`,
    arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
  });
}

/**
 * aws-cdk-lib/aws-translate 只有 AWS::Translate::ParallelData，没有术语表 L1。
 * 创建和更新都 ImportTerminology，MergeStrategy OVERWRITE。删除时 DeleteTerminology。
 * 自定义资源运行时把 blob 字符串按 UTF-8 编码，SDK 再做 Base64，所以 File 传 CSV 原文。
 */
export function importBasketballTerminology(stack: cdk.Stack): cr.AwsCustomResource {
  const csv = fs.readFileSync(CSV_PATH, 'utf8');
  const importCall: cr.AwsSdkCall = {
    service: 'Translate',
    action: 'importTerminology',
    parameters: {
      Name: BASKETBALL_TERMINOLOGY_NAME,
      MergeStrategy: 'OVERWRITE',
      TerminologyData: {
        File: csv,
        Format: 'CSV',
        Directionality: 'UNI',
      },
    },
    physicalResourceId: cr.PhysicalResourceId.of(BASKETBALL_TERMINOLOGY_NAME),
  };
  const terminologyArn = basketballTerminologyArn(stack);
  return new cr.AwsCustomResource(stack, 'BasketballTerminology', {
    resourceType: 'Custom::TranslateTerminology',
    onCreate: importCall,
    onUpdate: importCall,
    onDelete: {
      service: 'Translate',
      action: 'deleteTerminology',
      parameters: { Name: BASKETBALL_TERMINOLOGY_NAME },
      ignoreErrorCodesMatching: 'ResourceNotFoundException',
    },
    policy: cr.AwsCustomResourcePolicy.fromStatements([
      new iam.PolicyStatement({
        actions: ['translate:ImportTerminology', 'translate:DeleteTerminology'],
        resources: [terminologyArn],
      }),
    ]),
    // 与 cdk.json 的 installLatestAwsSdkDefault=false 一致，用 Lambda 运行时里的 SDK，不在运行时 npm install。
    installLatestAwsSdk: false,
  });
}
